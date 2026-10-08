import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta
import logging
import time
import asyncio
from utils.database import execute_query_with_retry, get_metrics
from utils.db_atomic import db_advisory_lock, db_transaction
import functools

def _tr(lang, en, ar, fr):
    """Translation function for multilanguage support"""
    if lang == 1:  # Arabic
        return ar
    elif lang == 3:  # French
        return fr
    else:  # Default to English
        return en

async def _get_lang(discord_user_id: int) -> int:
    """Fetch multilanguage flag (0=en,1=ar,3=fr). Fallback to 0."""
    try:
        q = "SELECT multilanguage FROM user WHERE user_authorid = %s"
        res = await execute_query_with_retry(q, (str(discord_user_id),))
        if res and 'multilanguage' in res[0] and res[0]['multilanguage'] is not None:
            return int(res[0]['multilanguage'])
    except Exception:
        pass
    return 0


async def _add_inventory_product(inventory_id: int, product_id: int, quantity: int):
    rows = await execute_query_with_retry(
        """
        SELECT quantiter
        FROM inventory_product
        WHERE inventory_id = %s AND product_id = %s
        FOR UPDATE
        """,
        (inventory_id, product_id)
    )
    if rows:
        await execute_query_with_retry(
            """
            UPDATE inventory_product
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND product_id = %s
            """,
            (quantity, inventory_id, product_id)
        )
    else:
        await execute_query_with_retry(
            """
            INSERT INTO inventory_product (inventory_id, product_id, quantiter)
            VALUES (%s, %s, %s)
            """,
            (inventory_id, product_id, quantity)
        )


async def _add_inventory_chest(inventory_id: int, chest_id: int, quantity: int):
    rows = await execute_query_with_retry(
        """
        SELECT quantiter
        FROM inventory_chest
        WHERE inventory_id = %s AND chest_id = %s
        FOR UPDATE
        """,
        (inventory_id, chest_id)
    )
    if rows:
        await execute_query_with_retry(
            """
            UPDATE inventory_chest
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND chest_id = %s
            """,
            (quantity, inventory_id, chest_id)
        )
    else:
        await execute_query_with_retry(
            """
            INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
            VALUES (%s, %s, %s)
            """,
            (inventory_id, chest_id, quantity)
        )


async def claim_referee_reward_atomic(user_id: int, reroll_quantity: int) -> str:
    async with db_advisory_lock(f"slbot:referee_reward:{user_id}", timeout=5):
        async with db_transaction():
            rows = await execute_query_with_retry(
                """
                SELECT r.referee_claimed_reward, u.roll_total, u.c_dungeon_points
                FROM referrals r
                JOIN user u ON r.referee_id = u.user_id
                WHERE r.referee_id = %s
                FOR UPDATE
                """,
                (user_id,)
            )
            if not rows:
                return "not_found"

            referee_data = rows[0]
            if referee_data.get('referee_claimed_reward', 0) == 1:
                return "claimed"
            if referee_data['roll_total'] < 500 or referee_data['c_dungeon_points'] <= 0:
                return "requirements"

            inventory_rows = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                (user_id,)
            )
            if not inventory_rows:
                return "inventory_missing"

            await _add_inventory_product(inventory_rows[0]['inventory_id'], 2, reroll_quantity)
            await execute_query_with_retry(
                "UPDATE referrals SET referee_claimed_reward = 1 WHERE referee_id = %s",
                (user_id,)
            )
            return "ok"


async def claim_referrer_reward_atomic(referrer_id: int, referee_id: int) -> str:
    lock_key = f"slbot:referrer_reward:{referrer_id}:{referee_id}"
    async with db_advisory_lock(lock_key, timeout=5):
        async with db_transaction():
            rows = await execute_query_with_retry(
                """
                SELECT status, referee_claimed
                FROM referrals
                WHERE referrer_id = %s AND referee_id = %s
                FOR UPDATE
                """,
                (referrer_id, referee_id)
            )
            if not rows:
                return "not_found"
            if rows[0].get('referee_claimed', 0) == 1 or rows[0].get('status') == 'completed':
                return "claimed"

            inventory_rows = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                (referrer_id,)
            )
            if not inventory_rows:
                return "inventory_missing"

            inventory_id = inventory_rows[0]['inventory_id']
            await execute_query_with_retry(
                """
                UPDATE referrals
                SET status = 'completed', referee_claimed = 1, completed_at = %s
                WHERE referrer_id = %s AND referee_id = %s
                """,
                (datetime.now(), referrer_id, referee_id)
            )
            await _add_inventory_product(inventory_id, 2, 2)
            await _add_inventory_chest(inventory_id, 3, 1)
            await execute_query_with_retry(
                "UPDATE user SET won_currency = won_currency + 50000 WHERE user_id = %s",
                (referrer_id,)
            )
            return "ok"


async def confirm_referral_atomic(referrer_id: int, target_id: int) -> str:
    first_id, second_id = sorted([referrer_id, target_id])
    async with db_advisory_lock(f"slbot:referral_confirm:{first_id}:{second_id}", timeout=5):
        async with db_transaction():
            existing_ref = await execute_query_with_retry(
                "SELECT referrer_id FROM referrals WHERE referee_id = %s FOR UPDATE",
                (target_id,)
            )
            if existing_ref:
                return "already_referred"

            target_refs = await execute_query_with_retry(
                "SELECT referee_id FROM referrals WHERE referrer_id = %s FOR UPDATE",
                (target_id,)
            )
            if target_refs:
                return "target_is_referrer"

            await execute_query_with_retry(
                """
                INSERT INTO referrals (referrer_id, referee_id, status, created_at)
                VALUES (%s, %s, 'pending', %s)
                """,
                (referrer_id, target_id, datetime.now())
            )

            active_rows = await execute_query_with_retry(
                """
                SELECT current_refed, max_refed
                FROM activerefs
                WHERE user_id = %s
                FOR UPDATE
                """,
                (referrer_id,)
            )
            if not active_rows:
                return "ok"

            current_refs = active_rows[0]['current_refed']
            max_refs = active_rows[0]['max_refed']
            inventory_rows = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                (referrer_id,)
            )
            inventory_id = inventory_rows[0]['inventory_id'] if inventory_rows else None

            if current_refs < max_refs:
                await execute_query_with_retry(
                    "UPDATE activerefs SET current_refed = current_refed + 1 WHERE user_id = %s",
                    (referrer_id,)
                )
                if inventory_id and (current_refs + 1) % 5 == 0:
                    await _add_inventory_product(inventory_id, 2, 2)
                    await _add_inventory_chest(inventory_id, 3, 1)
            elif inventory_id:
                await _add_inventory_product(inventory_id, 2, 2)
                await execute_query_with_retry(
                    "UPDATE user SET won_currency = won_currency + 50000 WHERE user_id = %s",
                    (referrer_id,)
                )
                await _add_inventory_chest(inventory_id, 3, 1)

            return "ok"

class ReferralCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def calculate_referral_boost(self, current_refs):
        """
        Calculate the referral boost percentage based on number of referrals
        First 3 referrals: 5% each
        Referrals 4-9: 2.5% each
        Maximum total boost: 30% at 9 referrals
        """
        if current_refs <= 0:
            return 0
        
        # Calculate boost from first 3 referrals (5% each)
        first_tier = min(current_refs, 3)
        first_tier_boost = first_tier * 5
        
        # Calculate boost from referrals 4-9 (2.5% each)
        second_tier = max(0, min(current_refs - 3, 6))  # Up to 6 more referrals (4-9)
        second_tier_boost = second_tier * 2.5
        
        # Total boost (capped at 30%)
        total_boost = first_tier_boost + second_tier_boost
        return min(total_boost, 30)  # Cap at 30%

    @commands.command(name="refactivate",aliases=["ra","rfa"])
    async def ref_activate(self, ctx, lang: int = None):
        try:
            if lang is None:
                # Get user's language preference from database
                lang = await _get_lang(ctx.author.id)
            
            # Get user's Discord ID
            discord_id = str(ctx.author.id)
            
            # Check if user exists in database
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(_tr(
                    lang,
                    f"{ctx.author.mention} is not registered in the database. Please register first.",
                    f"{ctx.author.mention} غير مسجل في قاعدة البيانات. يرجى التسجيل أولاً.",
                    f"{ctx.author.mention} n'est pas enregistré dans la base de données. Veuillez vous inscrire d'abord."
                ))
                return
                
            user_id = user_results[0]['user_id']
            
            # Check if user is already in activerefs table
            check_active_query = "SELECT current_refed, max_refed FROM activerefs WHERE user_id = %s"
            active_results = await execute_query_with_retry(check_active_query, (user_id,))
            
            if active_results:
                # User is already active
                current_refs = active_results[0]['current_refed']
                max_refs = active_results[0]['max_refed']
                
                boost_percentage = await self.calculate_referral_boost(current_refs)
                embed = discord.Embed(
                    title=_tr(lang, "🔗 Referral System Already Active", "🔗 نظام الإحالة مُفعَّل بالفعل", "🔗 Le système de parrainage est déjà actif"),
                    description=_tr(lang, "Your referral capabilities are active!", "قدرات الإحالة لديك نشطة!", "Vos capacités de parrainage sont actives !"),
                    color=discord.Color.green()
                )
                embed.add_field(
                    name=_tr(lang, "Current Status", "الحالة الحالية", "Statut actuel"),
                    value=_tr(
                        lang,
                        f"<:blue_dot:1311418123404181585> Current Boost: **+{boost_percentage}%** WON & GWON",
                        f"<:blue_dot:1311418123404181585> التعزيز الحالي: **+{boost_percentage}%** WON و GWON",
                        f"<:blue_dot:1311418123404181585> Bonus actuel : **+{boost_percentage}%** WON & GWON"
                    ),
                    inline=False
                )
                if ctx.author.avatar:
                    embed.set_thumbnail(url=ctx.author.avatar.url)
                await ctx.send(embed=embed)
            else:
                # Insert new record with default values (max_refed = 9)
                insert_query = "INSERT INTO activerefs (user_id, current_refed, max_refed) VALUES (%s, 0, 9)"
                await execute_query_with_retry(insert_query, (user_id,))
                
                embed = discord.Embed(
                    title=_tr(lang, "🎉 Referral System Activated!", "🎉 تم تفعيل نظام الإحالة!", "🎉 Système de parrainage activé !"),
                    description=_tr(lang, "You have successfully activated your referral capabilities!", "لقد فعّلت قدرات الإحالة بنجاح!", "Vous avez activé vos capacités de parrainage avec succès !"),
                    color=discord.Color.gold()
                )
                embed.add_field(
                    name=_tr(lang, "What's Next?", "ما التالي؟", "Et ensuite ?"),
                    value=_tr(
                        lang,
                        "<:blue_dot:1311418123404181585> Use `sl ref @user` to start referring others\n<:blue_dot:1311418123404181585> Earn rewards from each successful referral!",
                        "<:blue_dot:1311418123404181585> استخدم `sl ref @user` لبدء إحالة الآخرين\n<:blue_dot:1311418123404181585> اكسب مكافآت من كل إحالة ناجحة!",
                        "<:blue_dot:1311418123404181585> Utilisez `sl ref @user` pour commencer à parrainer\n<:blue_dot:1311418123404181585> Gagnez des récompenses pour chaque parrainage réussi !"
                    ),
                    inline=False
                )
                if ctx.author.avatar:
                    embed.set_thumbnail(url=ctx.author.avatar.url)
                await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(_tr(lang if lang is not None else 0, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))

    @commands.command(name="ref",aliases=["rf","re"])
    async def refer_user(self, ctx, target: discord.Member = None, lang: int = None):
        try:
            if lang is None:
                lang = await _get_lang(ctx.author.id)
                
            if target is None:
                await ctx.send(_tr(
                    lang,
                    "Please specify a user to refer: `sl ref @user`",
                    "يرجى تحديد مستخدم للإحالة: `sl ref @user`",
                    "Veuillez spécifier un utilisateur à parrainer : `sl ref @user`"
                ))
                return
                
            if target.id == ctx.author.id:
                await ctx.send(_tr(lang, "You cannot refer yourself!", "لا يمكنك إحالة نفسك!", "Vous ne pouvez pas vous parrainer vous-même !"))
                return
            
            # Get referrer's Discord ID and check if they exist
            referrer_discord_id = str(ctx.author.id)
            check_referrer_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            referrer_results = await execute_query_with_retry(check_referrer_query, (referrer_discord_id,))
            
            if not referrer_results:
                await ctx.send(_tr(
                    lang,
                    f"{ctx.author.mention} is not registered in the database. Please register first.",
                    f"{ctx.author.mention} غير مسجل في قاعدة البيانات. يرجى التسجيل أولاً.",
                    f"{ctx.author.mention} n'est pas enregistré dans la base de données. Veuillez vous inscrire d'abord."
                ))
                return
                
            referrer_user_id = referrer_results[0]['user_id']
            
            # Check if referrer is in activerefs table
            check_active_query = "SELECT current_refed, max_refed FROM activerefs WHERE user_id = %s"
            active_results = await execute_query_with_retry(check_active_query, (referrer_user_id,))
            
            if not active_results:
                await ctx.send(_tr(
                    lang,
                    "You need to activate your referral system first using `sl refactivate`!",
                    "تحتاج إلى تفعيل نظام الإحالة الخاص بك أولاً باستخدام `sl refactivate`!",
                    "Vous devez d'abord activer votre système de parrainage avec `sl refactivate` !"
                ))
                return
            
            # Get target's Discord ID and check if they exist
            target_discord_id = str(target.id)
            check_target_query = "SELECT user_id, entry_date FROM user WHERE user_authorid = %s"
            target_results = await execute_query_with_retry(check_target_query, (target_discord_id,))
            
            if not target_results:
                await ctx.send(_tr(
                    lang,
                    f"{target.mention} is not registered in the database.",
                    f"{target.mention} غير مسجل في قاعدة البيانات.",
                    f"{target.mention} n'est pas enregistré dans la base de données."
                ))
                return
                
            target_user_id = target_results[0]['user_id']
            target_entry_date = target_results[0]['entry_date']
            
            # Check if target is already referred by someone
            check_existing_ref_query = "SELECT referrer_id FROM referrals WHERE referee_id = %s"
            existing_ref_result = await execute_query_with_retry(check_existing_ref_query, (target_user_id,))
            
            if existing_ref_result:
                await ctx.send(_tr(
                    lang,
                    f"{target.mention} is already referred by someone else!",
                    f"{target.mention} تمت إحالته بالفعل من قبل شخص آخر!",
                    f"{target.mention} est déjà parrainé par quelqu'un d'autre !"
                ))
                return
            
            # Check if target has referred someone else
            check_target_refs_query = "SELECT referee_id FROM referrals WHERE referrer_id = %s"
            target_refs_result = await execute_query_with_retry(check_target_refs_query, (target_user_id,))
            
            if target_refs_result:
                await ctx.send(_tr(
                    lang,
                    f"{target.mention} has already referred someone and cannot be referred!",
                    f"{target.mention} قام بالفعل بإحالة شخص ما ولا يمكن إحالته!",
                    f"{target.mention} a déjà parrainé quelqu'un et ne peut pas être parrainé !"
                ))
                return
            
            # Check if target's account is older than 7 days
            account_creation = target.created_at.replace(tzinfo=None)
            if (datetime.now() - account_creation).days < 7:
                await ctx.send(_tr(
                    lang,
                    f"{target.mention}'s Discord account must be at least 7 days old to be referred!",
                    f"يجب أن يكون حساب {target.mention} على ديسكورد عمره 7 أيام على الأقل ليتم إحالته!",
                    f"Le compte Discord de {target.mention} doit avoir au moins 7 jours pour être parrainé !"
                ))
                return
            
            # Check if target registered within last 3 days
            if isinstance(target_entry_date, str):
                try:
                    target_entry_date = datetime.strptime(target_entry_date, '%Y-%m-%d')
                except:
                    # Try full datetime
                    target_entry_date = datetime.strptime(target_entry_date.split(' ')[0], '%Y-%m-%d')
            
            days_since_entry = (datetime.now() - target_entry_date).days
            if days_since_entry > 3:
                await ctx.send(_tr(
                    lang,
                    f"{target.mention} must have registered within the last 3 days to be referred!",
                    f"يجب أن يكون {target.mention} قد سجل خلال الأيام الثلاثة الماضية ليتم إحالته!",
                    f"{target.mention} doit s'être inscrit au cours des 3 derniers jours pour être parrainé !"
                ))
                return
            
            # Send confirmation embed to target
            embed = discord.Embed(
                title=_tr(lang, "🔗 Referral Invitation", "🔗 دعوة إحالة", "🔗 Invitation de parrainage"),
                description=_tr(
                    lang,
                    f"Do you wish to become a referred user under **{ctx.author.display_name}**?\n\nThis action can only be done once and is irreversible.",
                    f"هل ترغب في أن تكون مُحالاً تحت **{ctx.author.display_name}**؟\n\nيمكن القيام بهذا الإجراء مرة واحدة فقط وهو غير قابل للتراجع.",
                    f"Souhaitez-vous être parrainé par **{ctx.author.display_name}** ?\n\nCette action ne peut être faite qu'une seule fois et est irréversible."
                ),
                color=discord.Color.gold()
            )
            if target.avatar:
                embed.set_thumbnail(url=target.avatar.url)
            
            view = ReferralConfirmView(referrer_user_id, target_user_id, target, ctx.author)
            await ctx.send(f"{target.mention}", embed=embed, view=view)
            
        except Exception as e:
            await ctx.send(_tr(lang if lang is not None else 0, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))

    @commands.command(name="referralstat", aliases=["rft","rfs"])
    async def referral_status(self, ctx, lang: int = None):
        try:
            if lang is None:
                lang = await _get_lang(ctx.author.id)

            # Get user's Discord ID
            discord_id = str(ctx.author.id)
            
            # Check if user exists in database
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(_tr(
                    lang,
                    f"{ctx.author.mention} is not registered in the database. Please register first.",
                    f"{ctx.author.mention} غير مسجل في قاعدة البيانات. يرجى التسجيل أولاً.",
                    f"{ctx.author.mention} n'est pas enregistré dans la base de données. Veuillez vous inscrire d'abord."
                ))
                return
                
            user_id = user_results[0]['user_id']
            
            # Check if user is a referee (referred by someone else)
            check_referee_query = """
            SELECT r.referrer_id, r.referee_claimed_reward, u.roll_total, u.c_dungeon_points
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referee_id = %s
            """
            referee_result = await execute_query_with_retry(check_referee_query, (user_id,))
            
            # Count total referrals
            total_refs_query = "SELECT COUNT(*) as total FROM referrals WHERE referrer_id = %s"
            total_refs_result = await execute_query_with_retry(total_refs_query, (user_id,))
            total_referrals = total_refs_result[0]['total'] if total_refs_result else 0
            
            # Count pending referrals (users who haven't met requirements)
            pending_refs_query = """
            SELECT COUNT(*) as pending 
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referrer_id = %s AND r.status = 'pending'
            AND (u.roll_total < 500 OR u.c_dungeon_points <= 0)
            """
            pending_refs_result = await execute_query_with_retry(pending_refs_query, (user_id,))
            pending_referrals = pending_refs_result[0]['pending'] if pending_refs_result else 0
            
            # Check if user is in activerefs (can show button)
            check_active_query = "SELECT user_id FROM activerefs WHERE user_id = %s"
            is_active_referrer = await execute_query_with_retry(check_active_query, (user_id,))
            
            # Create main embed
            embed = discord.Embed(
                title=_tr(lang, "🔗 Referral Status", "🔗 حالة الإحالة", "🔗 Statut de parrainage"),
                color=discord.Color.blue()
            )
            if ctx.author.avatar:
                embed.set_thumbnail(url=ctx.author.avatar.url)
            
            # Referral Stats section
            embed.add_field(
                name=_tr(lang, "📊 Referral Stats", "📊 إحصائيات الإحالة", "📊 Statistiques de parrainage"),
                value=_tr(
                    lang,
                    f"<:blue_dot:1311418123404181585> You have referred **{total_referrals}** players\n<:blue_dot:1311418123404181585> You have **{pending_referrals}** pending referrals",
                    f"<:blue_dot:1311418123404181585> لقد أحلت **{total_referrals}** لاعبًا\n<:blue_dot:1311418123404181585> لديك **{pending_referrals}** إحالات قيد الانتظار",
                    f"<:blue_dot:1311418123404181585> Vous avez parrainé **{total_referrals}** joueurs\n<:blue_dot:1311418123404181585> Vous avez **{pending_referrals}** parrainages en attente"
                ),
                inline=False
            )
                        
            embed.add_field(
                name=_tr(lang, "🎁 Rewards", "🎁 المكافآت", "🎁 Récompenses"),
                value=_tr(
                    lang,
                    "<:blue_dot:1311418123404181585> **Per referral:** Earn increased WON & GWON whenever **you** complete a dungeon or mission.\n"
                    " ↳ First 3 referrals: **+5% WON & GWON** per referral\n"
                    " ↳ Beyond that: **+2.5%** per referral\n"
                    "  (Max total boost: **30%** at 9 referrals)\n\n"
                    "<:blue_dot:1311418123404181585> **5th referral:**\n"
                    " ↳ **1x <:mona:1309275864206278748> Monarch Chest**\n"
                    " ↳ **2x <:reroll:1302672409278545940> Rerolls**\n\n"
                    "<:blue_dot:1311418123404181585> **Beyond 9 referrals:**\n"
                    " ↳ **1x <:mona:1309275864206278748> Monarch Chest**\n"
                    " ↳ **2x <:reroll:1302672409278545940> Rerolls**\n"
                    " ↳ **+50,000 <:won:1304034228966658048> WON** per referral",
                    "<:blue_dot:1311418123404181585> **لكل إحالة:** احصل على زيادة في WON و GWON كلما أكملت **أنت** زنزانة أو مهمة.\n"
                    " ↳ أول 3 إحالات: **+5% WON & GWON** لكل إحالة\n"
                    " ↳ بعد ذلك: **+2.5%** لكل إحالة\n"
                    "  (الحد الأقصى للإجمالي: **30%** عند 9 إحالات)\n\n"
                    "<:blue_dot:1311418123404181585> **الإحالة الخامسة:**\n"
                    " ↳ **1x <:mona:1309275864206278748> صندوق المونارك**\n"
                    " ↳ **2x <:reroll:1302672409278545940> إعادة رمية**\n\n"
                    "<:blue_dot:1311418123404181585> **بعد 9 إحالات:**\n"
                    " ↳ **1x <:mona:1309275864206278748> صندوق المونارك**\n"
                    " ↳ **2x <:reroll:1302672409278545940> إعادة رمية**\n"
                    " ↳ **+50,000 <:won:1304034228966658048> وون** لكل إحالة",
                    "<:blue_dot:1311418123404181585> **Par parrainage :** Gagnez plus de WON & GWON à chaque donjon ou mission que **vous** complétez.\n"
                    " ↳ 3 premiers parrainages : **+5% WON & GWON** par parrainage\n"
                    " ↳ Au-delà : **+2,5%** par parrainage\n"
                    "  (Bonus total max : **30%** à 9 parrainages)\n\n"
                    "<:blue_dot:1311418123404181585> **5ᵉ parrainage :**\n"
                    " ↳ **1x <:mona:1309275864206278748> Coffre du Monarque**\n"
                    " ↳ **2x <:reroll:1302672409278545940> Rerolls**\n\n"
                    "<:blue_dot:1311418123404181585> **Au-delà de 9 parrainages :**\n"
                    " ↳ **1x <:mona:1309275864206278748> Coffre du Monarque**\n"
                    " ↳ **2x <:reroll:1302672409278545940> Rerolls**\n"
                    " ↳ **+50 000 <:won:1304034228966658048> WON** par parrainage"
                ),
                inline=False
            )

            # Create view with appropriate buttons
            view = None
            if is_active_referrer:
                view = ReferralStatusView(user_id, ctx.author, lang)
            
            # Check if user is a referee and can claim rewards
            if referee_result:
                referee_data = referee_result[0]
                roll_total = referee_data['roll_total']
                mid_dungeon = referee_data['c_dungeon_points']
                referee_claimed_reward = referee_data.get('referee_claimed_reward', 0)
                
                # Check if requirements are met and reward not claimed
                requirements_met = roll_total >= 500 and mid_dungeon > 0
                
                if requirements_met and referee_claimed_reward == 0:
                    # Add referee status to embed
                    embed.add_field(
                        name=_tr(lang, "🎯 Referee Status", "🎯 حالة المُحال", "🎯 Statut du filleul"),
                        value=_tr(
                            lang,
                            "<:blue_dot:1311418123404181585> You have completed all referral requirements!\n<:blue_dot:1311418123404181585> You can claim your one-time referee reward below.",
                            "<:blue_dot:1311418123404181585> لقد أكملت جميع متطلبات الإحالة!\n<:blue_dot:1311418123404181585> يمكنك المطالبة بمكافأتك مرة واحدة بالأسفل.",
                            "<:blue_dot:1311418123404181585> Vous avez rempli toutes les conditions de parrainage !\n<:blue_dot:1311418123404181585> Vous pouvez réclamer votre récompense de filleul unique ci-dessous."
                        ),
                        inline=False
                    )
                    
                    # Create or modify view to include claim button
                    if view is None:
                        view = RefereeRewardView(user_id, ctx.author, lang)
                    else:
                        view.add_referee_claim_button()
            
            await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            await ctx.send(_tr(lang if lang is not None else 0, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))

class RefereeRewardView(discord.ui.View):
    def __init__(self, user_id, original_user, lang: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.original_user = original_user
        self.lang = lang

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                _tr(self.lang, "You cannot claim someone else's rewards.", "لا يمكنك المطالبة بمكافآت شخص آخر.", "Vous ne pouvez pas réclamer la récompense de quelqu’un d’autre."),
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Claim Referee Reward", style=discord.ButtonStyle.success, emoji="🎁")
    async def claim_referee_reward(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            claim_status = await claim_referee_reward_atomic(self.user_id, 10)
            if claim_status != "ok":
                messages = {
                    "not_found": "Error: Referral record not found.",
                    "requirements": "Error: You haven't completed the referral requirements yet.",
                    "claimed": "Error: You have already claimed your referee reward.",
                    "inventory_missing": "Error: Inventory not found.",
                }
                await interaction.response.send_message(messages.get(claim_status, "Error: Unable to claim reward."), ephemeral=True)
                return

            embed = discord.Embed(
                title=_tr(self.lang, "🎉 Referee Reward Claimed!", "🎉 تم استلام مكافأة المُحال!", "🎉 Récompense de filleul réclamée !"),
                description=_tr(self.lang, "You have successfully claimed your one-time referee completion reward!", "لقد طالبت بنجاح بمكافأتك لمرة واحدة لإكمال الإحالة!", "Vous avez réclamé avec succès votre récompense unique de filleul !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(self.lang, "Rewards Received", "المكافآت المستلمة", "Récompenses reçues"),
                value=_tr(self.lang, "<:blue_dot:1311418123404181585> +10 <:reroll:1302672409278545940> Reset Rolls", "<:blue_dot:1311418123404181585> +10 <:reroll:1302672409278545940> إعادة رمية", "<:blue_dot:1311418123404181585> +10 <:reroll:1302672409278545940> Rerolls"),
                inline=False
            )
            button.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)
            return

            # Create success embed
            embed = discord.Embed(
                title=_tr(self.lang, "🎉 Referee Reward Claimed!", "🎉 تم استلام مكافأة المُحال!", "🎉 Récompense de filleul réclamée !"),
                description=_tr(self.lang, "You have successfully claimed your one-time referee completion reward!", "لقد طالبت بنجاح بمكافأتك لمرة واحدة لإكمال الإحالة!", "Vous avez réclamé avec succès votre récompense unique de filleul !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(self.lang, "Rewards Received", "المكافآت المستلمة", "Récompenses reçues"),
                value=_tr(self.lang, "<:blue_dot:1311418123404181585> +10 <:reroll:1302672409278545940> Reset Rolls", "<:blue_dot:1311418123404181585> +10 <:reroll:1302672409278545940> إعادة رمية", "<:blue_dot:1311418123404181585> +10 <:reroll:1302672409278545940> Rerolls"),
                inline=False
            )
            embed.add_field(
                name=_tr(self.lang, "Note", "ملاحظة", "Note"),
                value=_tr(self.lang, "<:blue_dot:1311418123404181585> This is a one-time reward for completing referral requirements", "<:blue_dot:1311418123404181585> هذه مكافأة لمرة واحدة لإكمال متطلبات الإحالة", "<:blue_dot:1311418123404181585> Il s’agit d’une récompense unique pour avoir rempli les conditions de parrainage"),
                inline=False
            )
            
            # Disable the button
            button.disabled = True
            
            await interaction.response.edit_message(embed=embed, view=self)
            
        except Exception as e:
            await interaction.response.send_message(_tr(self.lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

class ReferralStatusView(discord.ui.View):
    def __init__(self, user_id, original_user, lang: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.original_user = original_user
        self.lang = lang
        self.has_referee_claim_button = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                _tr(self.lang, "You cannot interact with someone else's referral status.", "لا يمكنك التفاعل مع حالة إحالة شخص آخر.", "Vous ne pouvez pas interagir avec le statut de parrainage de quelqu’un d’autre."),
                ephemeral=True
            )
            return False
        return True

    def add_referee_claim_button(self):
        """Add referee claim button to existing view"""
        if not self.has_referee_claim_button:
            claim_button = discord.ui.Button(
                label=_tr(self.lang, "Claim Referee Reward", "استلام مكافأة المُحال", "Réclamer la récompense de filleul"),
                style=discord.ButtonStyle.success,
                emoji="🎁"
            )
            claim_button.callback = self.claim_referee_reward
            self.add_item(claim_button)
            self.has_referee_claim_button = True

    async def claim_referee_reward(self, interaction: discord.Interaction):
        """Handle referee reward claiming"""
        try:
            claim_status = await claim_referee_reward_atomic(self.user_id, 5)
            if claim_status != "ok":
                messages = {
                    "not_found": "Error: Referral record not found.",
                    "requirements": "Error: You haven't completed the referral requirements yet.",
                    "claimed": "Error: You have already claimed your referee reward.",
                    "inventory_missing": "Error: Inventory not found.",
                }
                await interaction.response.send_message(messages.get(claim_status, "Error: Unable to claim reward."), ephemeral=True)
                return

            embed = discord.Embed(
                title=_tr(self.lang, "🎉 Referee Reward Claimed!", "🎉 تم استلام مكافأة المُحال!", "🎉 Récompense de filleul réclamée !"),
                description=_tr(self.lang, "You have successfully claimed your one-time referee completion reward!", "لقد طالبت بنجاح بمكافأتك لمرة واحدة لإكمال الإحالة!", "Vous avez réclamé avec succès votre récompense unique de filleul !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(self.lang, "Rewards Received", "المكافآت المستلمة", "Récompenses reçues"),
                value=_tr(self.lang, "<:blue_dot:1311418123404181585> +5 <:reroll:1302672409278545940> Reset Rolls", "<:blue_dot:1311418123404181585> +5 <:reroll:1302672409278545940> إعادة رمية", "<:blue_dot:1311418123404181585> +5 <:reroll:1302672409278545940> Rerolls"),
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

            # Create success embed
            embed = discord.Embed(
                title=_tr(self.lang, "🎉 Referee Reward Claimed!", "🎉 تم استلام مكافأة المُحال!", "🎉 Récompense de filleul réclamée !"),
                description=_tr(self.lang, "You have successfully claimed your one-time referee completion reward!", "لقد طالبت بنجاح بمكافأتك لمرة واحدة لإكمال الإحالة!", "Vous avez réclamé avec succès votre récompense unique de filleul !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(self.lang, "Rewards Received", "المكافآت المستلمة", "Récompenses reçues"),
                value=_tr(self.lang, "<:blue_dot:1311418123404181585> +5 <:reroll:1302672409278545940> Reset Rolls", "<:blue_dot:1311418123404181585> +5 <:reroll:1302672409278545940> إعادة رمية", "<:blue_dot:1311418123404181585> +5 <:reroll:1302672409278545940> Rerolls"),
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(_tr(self.lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

    @discord.ui.button(label="View Referred Users", style=discord.ButtonStyle.primary, emoji="👥")
    async def view_referred_users(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Get all referred users with their progress, ordered by referral ID (creation order)
            query = """
            SELECT u.user_authorid, u.roll_total, u.c_dungeon_points, r.status, r.referee_claimed, r.id,
                   (SELECT COUNT(*) FROM referrals WHERE referrer_id = %s AND id <= r.id) as position
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referrer_id = %s
            ORDER BY r.id ASC
            """
            referred_users = await execute_query_with_retry(query, (self.user_id, self.user_id))
            
            if not referred_users:
                await interaction.response.send_message(_tr(self.lang, "You haven't referred anyone yet!", "لم تقم بإحالة أي شخص بعد!", "Vous n’avez encore parrainé personne !"), ephemeral=True)
                return
            
            embed = discord.Embed(
                title=_tr(self.lang, "👥 Your Referred Users", "👥 المستخدمون الذين أحلتهم", "👥 Vos filleuls"),
                description=_tr(self.lang, "Select a user from the dropdown to view detailed information.", "اختر مستخدمًا من القائمة المنسدلة لعرض التفاصيل.", "Sélectionnez un utilisateur dans la liste pour voir les détails."),
                color=discord.Color.blue()
            )
            
            view = ReferralDetailView(self.user_id, self.original_user, referred_users, self.lang)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(_tr(self.lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

class ReferralDetailView(discord.ui.View):
    def __init__(self, user_id, original_user, referred_users, lang: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.original_user = original_user
        self.referred_users = referred_users
        self.current_view = "list"
        self.selected_user_index = None
        self.lang = lang
        
        # Add dropdown
        self.user_select = UserDropdown(self.referred_users, self, self.lang)
        self.add_item(self.user_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                _tr(self.lang, "You cannot interact with someone else's referral status.", "لا يمكنك التفاعل مع حالة إحالة شخص آخر.", "Vous ne pouvez pas interagir avec le statut de parrainage de quelqu’un d’autre."),
                ephemeral=True
            )
            return False
        return True

    async def show_user_detail(self, interaction, user_index):
        try:
            user_data = self.referred_users[user_index]
            discord_id = user_data['user_authorid']
            roll_total = user_data['roll_total']
            mid_dungeon = user_data['c_dungeon_points']
            status = user_data['status']
            referee_claimed = user_data.get('referee_claimed', 0)
            referral_id = user_data['id']
            position = user_data['position']
            
            # Try to get Discord user object
            try:
                discord_user = interaction.client.get_user(int(discord_id))
                display_name = discord_user.display_name if discord_user else f"User {discord_id}"
                avatar_url = discord_user.avatar.url if discord_user and discord_user.avatar else None
            except:
                display_name = f"User {discord_id}"
                avatar_url = None
            
            # Check if requirements are met
            requirements_met = roll_total >= 500 and mid_dungeon > 0
            
            # Check if this user is within the first 9 referrals
            is_within_first_nine = position <= 9
            
            # Determine status
            if requirements_met:
                if status == 'pending':
                    if is_within_first_nine:
                        status_text = _tr(self.lang, "✅ **Complete** (No rewards for first 9 referrals)", "✅ **مكتمل** (لا مكافآت لأول 9 إحالات)", "✅ **Terminé** (pas de récompense pour les 9 premiers parrainages)")
                        status_color = discord.Color.blue()
                    else:
                        status_text = _tr(self.lang, "✅ **Complete** (Ready to claim rewards!)", "✅ **مكتمل** (جاهز لاستلام المكافآت!)", "✅ **Terminé** (Prêt à réclamer les récompenses !)")
                        status_color = discord.Color.gold()
                else:
                    status_text = _tr(self.lang, "✅ **Complete**", "✅ **مكتمل**", "✅ **Terminé**")
                    status_color = discord.Color.green()
            else:
                status_text = _tr(self.lang, "⏳ **Pending**", "⏳ **قيد الانتظار**", "⏳ **En attente**")
                status_color = discord.Color.orange()
            
            embed = discord.Embed(
                title=_tr(self.lang, f"📊 {display_name}'s Progress", f"📊 تقدم {display_name}", f"📊 Progrès de {display_name}"),
                description=_tr(self.lang, f"Detailed information about this referred user.\n**Referral Position:** #{position}", f"معلومات مفصلة عن هذا المُحال.\n**ترتيب الإحالة:** #{position}", f"Informations détaillées sur ce filleul.\n**Position de parrainage :** #{position}"),
                color=status_color
            )
            
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            
            embed.add_field(
                name=_tr(self.lang, "📈 Current Status", "📈 الحالة الحالية", "📈 Statut actuel"),
                value=status_text,
                inline=False
            )
            
            embed.add_field(
                name=_tr(self.lang, "🎲 Rolling Progress", "🎲 تقدم الرميات", "🎲 Progression des lancers"),
                value=_tr(self.lang,
                         f"<:blue_dot:1311418123404181585> **Current Rolls:** {roll_total:,}/500\n<:blue_dot:1311418123404181585> **Progress:** {min(100, (roll_total/500)*100):.1f}%",
                         f"<:blue_dot:1311418123404181585> **الرميات الحالية:** {roll_total:,}/500\n<:blue_dot:1311418123404181585> **التقدم:** {min(100, (roll_total/500)*100):.1f}%",
                         f"<:blue_dot:1311418123404181585> **Lancers actuels :** {roll_total:,}/500\n<:blue_dot:1311418123404181585> **Progression :** {min(100, (roll_total/500)*100):.1f}%"),
                inline=True
            )
            
            embed.add_field(
                name=_tr(self.lang, "🏰 Dungeon Progress", "🏰 تقدم الزنزانات", "🏰 Progression des donjons"),
                value=_tr(self.lang,
                         f"<:blue_dot:1311418123404181585> **Mid Dungeons:** {mid_dungeon}/1\n<:blue_dot:1311418123404181585> **Status:** {'✅ Complete' if mid_dungeon > 0 else '❌ Incomplete'}",
                         f"<:blue_dot:1311418123404181585> **زنزانات متوسطة:** {mid_dungeon}/1\n<:blue_dot:1311418123404181585> **الحالة:** {'✅ مكتمل' if mid_dungeon > 0 else '❌ غير مكتمل'}",
                         f"<:blue_dot:1311418123404181585> **Donjons Moyens :** {mid_dungeon}/1\n<:blue_dot:1311418123404181585> **Statut :** {'✅ Terminé' if mid_dungeon > 0 else '❌ Incomplet'}"),
                inline=True
            )
            
            # Add referral position info
            embed.add_field(
                name=_tr(self.lang, "🏆 Referral Info", "🏆 معلومات الإحالة", "🏆 Infos de parrainage"),
                value=_tr(self.lang,
                          f"<:blue_dot:1311418123404181585> **Position:** #{position}\n<:blue_dot:1311418123404181585> **Referral ID:** {referral_id}\n<:blue_dot:1311418123404181585> **Reward Eligible:** {'❌ No (First 9)' if is_within_first_nine else '✅ Yes'}",
                          f"<:blue_dot:1311418123404181585> **الترتيب:** #{position}\n<:blue_dot:1311418123404181585> **معرّف الإحالة:** {referral_id}\n<:blue_dot:1311418123404181585> **مؤهل للمكافأة:** {'❌ لا (أول 9)' if is_within_first_nine else '✅ نعم'}",
                          f"<:blue_dot:1311418123404181585> **Position :** #{position}\n<:blue_dot:1311418123404181585> **ID de parrainage :** {referral_id}\n<:blue_dot:1311418123404181585> **Éligible à la récompense :** {'❌ Non (9 premiers)' if is_within_first_nine else '✅ Oui'}"),
                inline=False
            )
            
            # Create view with go back button and claim button if applicable
            detail_view = UserDetailView(self.user_id, self.original_user, self.referred_users, user_index, self.lang)
            
            # Check if user just completed, hasn't been claimed yet, and is NOT within first 9
            if (requirements_met and status == 'pending' and referee_claimed == 0 and not is_within_first_nine):
                detail_view.add_claim_button()
            
            self.current_view = "detail"
            self.selected_user_index = user_index
            
            await interaction.response.edit_message(embed=embed, view=detail_view)
            
        except Exception as e:
            await interaction.response.send_message(_tr(self.lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

    async def show_user_list(self, interaction):
        try:
            embed = discord.Embed(
                title=_tr(self.lang, "👥 Your Referred Users", "👥 المستخدمون الذين أحلتهم", "👥 Vos filleuls"),
                description=_tr(self.lang, "Select a user from the dropdown to view detailed information.", "اختر مستخدمًا من القائمة المنسدلة لعرض التفاصيل.", "Sélectionnez un utilisateur dans la liste pour voir les détails."),
                color=discord.Color.blue()
            )
            
            # Recreate the view with dropdown
            new_view = ReferralDetailView(self.user_id, self.original_user, self.referred_users, self.lang)
            self.current_view = "list"
            
            await interaction.response.edit_message(embed=embed, view=new_view)
            
        except Exception as e:
            await interaction.response.send_message(_tr(self.lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

class UserDropdown(discord.ui.Select):
    def __init__(self, referred_users, parent_view, lang: int):
        self.referred_users = referred_users
        self.parent_view = parent_view
        self.lang = lang
        
        options = []
        for i, user_data in enumerate(referred_users):
            discord_id = user_data['user_authorid']
            roll_total = user_data['roll_total']
            mid_dungeon = user_data['c_dungeon_points']
            position = user_data['position']
            
            # Try to get Discord user object for display name
            try:
                discord_user = parent_view.original_user.guild.get_member(int(discord_id))
                if discord_user:
                    display_name = discord_user.display_name
                else:
                    display_name = f"User {discord_id}"
            except:
                display_name = f"User {discord_id}"
            
            # Check if requirements are met
            requirements_met = roll_total >= 500 and mid_dungeon > 0
            status_emoji = "✅" if requirements_met else "⏳"
            
            # Add position indicator
            position_text = f"#{position}"
            
            options.append(discord.SelectOption(
                label=f"{position_text} {display_name}",
                value=str(i),
                description=_tr(self.lang, f"{status_emoji} Rolls: {roll_total}/500 | Mid Dungeons: {mid_dungeon}/1",
                                f"{status_emoji} الرميات: {roll_total}/500 | الزنزانات المتوسطة: {mid_dungeon}/1",
                                f"{status_emoji} Lancers : {roll_total}/500 | Donjons moyens : {mid_dungeon}/1"),
                emoji=status_emoji
            ))
        
        super().__init__(
            placeholder=_tr(self.lang, "Select a referred user to view details...", "اختر مستخدمًا مُحالًا لعرض التفاصيل...", "Sélectionnez un filleul pour voir les détails..."),
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        await self.parent_view.show_user_detail(interaction, selected_index)

class UserDetailView(discord.ui.View):
    def __init__(self, user_id, original_user, referred_users, user_index, lang: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.original_user = original_user
        self.referred_users = referred_users
        self.user_index = user_index
        self.has_claim_button = False
        self.lang = lang

        # Go back button (localized)
        self.add_item(discord.ui.Button(label=_tr(self.lang, "Go Back", "عودة", "Retour"), style=discord.ButtonStyle.secondary, emoji="⬅️", custom_id="go_back"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                _tr(self.lang, "You cannot interact with someone else's referral status.", "لا يمكنك التفاعل مع حالة إحالة شخص آخر.", "Vous ne pouvez pas interagir avec le statut de parrainage de quelqu’un d’autre."),
                ephemeral=True
            )
            return False
        return True

    def add_claim_button(self):
        if not self.has_claim_button:
            claim_button = discord.ui.Button(
                label=_tr(self.lang, "Claim Reward", "استلام المكافأة", "Réclamer la récompense"),
                style=discord.ButtonStyle.success,
                emoji="🎁"
            )
            claim_button.callback = self.claim_reward
            self.add_item(claim_button)
            self.has_claim_button = True

    @discord.ui.button(label="Go Back", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Go back to user list
        embed = discord.Embed(
            title=_tr(self.lang, "👥 Your Referred Users", "👥 المستخدمون الذين أحلتهم", "👥 Vos filleuls"),
            description=_tr(self.lang, "Select a user from the dropdown to view detailed information.", "اختر مستخدمًا من القائمة المنسدلة لعرض التفاصيل.", "Sélectionnez un utilisateur dans la liste pour voir les détails."),
            color=discord.Color.blue()
        )
        
        # Recreate the view with dropdown
        new_view = ReferralDetailView(self.user_id, self.original_user, self.referred_users, self.lang)
        await interaction.response.edit_message(embed=embed, view=new_view)

    async def claim_reward(self, interaction: discord.Interaction):
        try:
            user_data = self.referred_users[self.user_index]
            position = user_data['position']
            
            # Double-check that this user is not within the first 9 referrals
            if position <= 9:
                await interaction.response.send_message(_tr(self.lang, "Error: No rewards available for the first 9 referrals.", "خطأ: لا توجد مكافآت لأول 9 إحالات.", "Erreur : aucune récompense pour les 9 premiers parrainages."), ephemeral=True)
                return
            
            # Get referee_id from referrals table
            referee_query = """
            SELECT r.referee_id 
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referrer_id = %s AND u.user_authorid = %s
            """
            referee_result = await execute_query_with_retry(referee_query, (self.user_id, user_data['user_authorid']))
            
            if not referee_result:
                await interaction.response.send_message(_tr(self.lang, "Error: Could not find referral record.", "خطأ: تعذر العثور على سجل الإحالة.", "Erreur : impossible de trouver l’enregistrement de parrainage."), ephemeral=True)
                return
                
            referee_id = referee_result[0]['referee_id']

            claim_status = await claim_referrer_reward_atomic(self.user_id, referee_id)
            if claim_status != "ok":
                messages = {
                    "not_found": "Error: Could not find referral record.",
                    "claimed": "Error: These referral rewards have already been claimed.",
                    "inventory_missing": "Error: Inventory not found.",
                }
                await interaction.response.send_message(messages.get(claim_status, "Error: Unable to claim reward."), ephemeral=True)
                return

            embed = discord.Embed(
                title=_tr(self.lang, "🎉 Referral Rewards Claimed!", "🎉 تم استلام مكافآت الإحالة!", "🎉 Récompenses de parrainage réclamées !"),
                description=_tr(self.lang, "You have successfully claimed your referral completion rewards!", "لقد استلمت مكافآت إكمال الإحالة بنجاح!", "Vous avez réclamé vos récompenses de parrainage avec succès !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(self.lang, "Rewards Received", "المكافآت المستلمة", "Récompenses reçues"),
                value=_tr(self.lang,
                         "<:blue_dot:1311418123404181585> +1 <:mona:1309275864206278748> Monarch Chest\n<:blue_dot:1311418123404181585> +2 <:reroll:1302672409278545940> Reset Rolls\n<:blue_dot:1311418123404181585> +50,000 Won Currency",
                         "<:blue_dot:1311418123404181585> +1 <:mona:1309275864206278748> صندوق المونارك\n<:blue_dot:1311418123404181585> +2 <:reroll:1302672409278545940> إعادة رمية\n<:blue_dot:1311418123404181585> +50,000 وون",
                         "<:blue_dot:1311418123404181585> +1 <:mona:1309275864206278748> Coffre du Monarque\n<:blue_dot:1311418123404181585> +2 <:reroll:1302672409278545940> Rerolls\n<:blue_dot:1311418123404181585> +50 000 Won"),
                inline=False
            )
            new_view = UserDetailView(self.user_id, self.original_user, self.referred_users, self.user_index, self.lang)
            await interaction.response.edit_message(embed=embed, view=new_view)
            return
            
            # Create success embed
            embed = discord.Embed(
                title=_tr(self.lang, "🎉 Referral Rewards Claimed!", "🎉 تم استلام مكافآت الإحالة!", "🎉 Récompenses de parrainage réclamées !"),
                description=_tr(self.lang, "You have successfully claimed your referral completion rewards!", "لقد استلمت مكافآت إكمال الإحالة بنجاح!", "Vous avez réclamé vos récompenses de parrainage avec succès !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(self.lang, "Rewards Received", "المكافآت المستلمة", "Récompenses reçues"),
                value=_tr(self.lang,
                         "<:blue_dot:1311418123404181585> +1 <:mona:1309275864206278748> Monarch Chest\n<:blue_dot:1311418123404181585> +2 <:reroll:1302672409278545940> Reset Rolls\n<:blue_dot:1311418123404181585> +50,000 Won Currency",
                         "<:blue_dot:1311418123404181585> +1 <:mona:1309275864206278748> صندوق المونارك\n<:blue_dot:1311418123404181585> +2 <:reroll:1302672409278545940> إعادة رمية\n<:blue_dot:1311418123404181585> +50,000 وون",
                         "<:blue_dot:1311418123404181585> +1 <:mona:1309275864206278748> Coffre du Monarque\n<:blue_dot:1311418123404181585> +2 <:reroll:1302672409278545940> Rerolls\n<:blue_dot:1311418123404181585> +50 000 Won"),
                inline=False
            )
            
            # Remove claim button and update view
            new_view = UserDetailView(self.user_id, self.original_user, self.referred_users, self.user_index, self.lang)
            
            await interaction.response.edit_message(embed=embed, view=new_view)
            
        except Exception as e:
            await interaction.response.send_message(_tr(self.lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

class ReferralConfirmView(discord.ui.View):
    def __init__(self, referrer_user_id, target_user_id, target_user, referrer_user):
        super().__init__(timeout=300)
        self.referrer_user_id = referrer_user_id
        self.target_user_id = target_user_id
        self.target_user = target_user
        self.referrer_user = referrer_user

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_user.id:
            lang = await _get_lang(interaction.user.id)
            await interaction.response.send_message(_tr(lang, "Only the mentioned user can respond to this referral invitation.", "فقط المستخدم المذكور يمكنه الرد على دعوة الإحالة هذه.", "Seul l’utilisateur mentionné peut répondre à cette invitation de parrainage."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_referral(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            lang = await _get_lang(interaction.user.id)

            confirm_status = await confirm_referral_atomic(self.referrer_user_id, self.target_user_id)
            if confirm_status != "ok":
                for item in self.children:
                    item.disabled = True
                if confirm_status == "already_referred":
                    description = _tr(lang, "You are already referred by someone else and cannot accept another referral.", "أنت مُحال بالفعل من قبل شخص آخر ولا يمكنك قبول إحالة أخرى.", "Vous êtes déjà parrainé par quelqu’un d’autre et ne pouvez pas accepter un autre parrainage.")
                elif confirm_status == "target_is_referrer":
                    description = _tr(lang, "You have already referred someone and cannot be referred by others.", "لقد قمت بالفعل بإحالة شخص ما ولا يمكنك أن تكون مُحالاً.", "Vous avez déjà parrainé quelqu’un et ne pouvez pas être parrainé.")
                else:
                    description = "Referral could not be confirmed."

                embed = discord.Embed(
                    title=_tr(lang, "❌ Referral Not Allowed", "❌ الإحالة غير مسموح بها", "❌ Parrainage non autorisé"),
                    description=description,
                    color=discord.Color.red()
                )
                await interaction.response.edit_message(embed=embed, view=self)
                return

            for item in self.children:
                item.disabled = True

            embed = discord.Embed(
                title=_tr(lang, "🎉 Referral Confirmed!", "🎉 تم تأكيد الإحالة!", "🎉 Parrainage confirmé !"),
                description=_tr(
                    lang,
                    f"You have successfully been referred by **{self.referrer_user.display_name}**!",
                    f"لقد تم إحالتك بنجاح بواسطة **{self.referrer_user.display_name}**!",
                    f"Vous avez été parrainé avec succès par **{self.referrer_user.display_name}** !"
                ),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(lang, "What's Next?", "ما التالي؟", "Et ensuite ?"),
                value=_tr(
                    lang,
                    "<:blue_dot:1311418123404181585> Complete 500 total rolls\n<:blue_dot:1311418123404181585> Clear at least 1 mid-level dungeon\n<:blue_dot:1311418123404181585> Once completed, your referrer will earn passive rewards from your activities!",
                    "<:blue_dot:1311418123404181585> أكمل 500 رمية إجمالية\n<:blue_dot:1311418123404181585> أتمم على الأقل زنزانة واحدة متوسطة\n<:blue_dot:1311418123404181585> عند اكتمالها، سيحصل المُحيل على مكافآت سلبية من نشاطاتك!",
                    "<:blue_dot:1311418123404181585> Atteignez 500 lancers au total\n<:blue_dot:1311418123404181585> Terminez au moins 1 donjon moyen\n<:blue_dot:1311418123404181585> Une fois fait, votre parrain gagnera des récompenses passives grâce à vos activités !"
                ),
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return

            # Disable all buttons
            for item in self.children:
                item.disabled = True
            
            embed = discord.Embed(
                title=_tr(lang, "🎉 Referral Confirmed!", "🎉 تم تأكيد الإحالة!", "🎉 Parrainage confirmé !"),
                description=_tr(
                    lang,
                    f"You have successfully been referred by **{self.referrer_user.display_name}**!",
                    f"لقد تم إحالتك بنجاح بواسطة **{self.referrer_user.display_name}**!",
                    f"Vous avez été parrainé avec succès par **{self.referrer_user.display_name}** !"
                ),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(lang, "What's Next?", "ما التالي؟", "Et ensuite ?"),
                value=_tr(
                    lang,
                    "<:blue_dot:1311418123404181585> Complete 500 total rolls\n<:blue_dot:1311418123404181585> Clear at least 1 mid-level dungeon\n<:blue_dot:1311418123404181585> Once completed, your referrer will earn passive rewards from your activities!",
                    "<:blue_dot:1311418123404181585> أكمل 500 رمية إجمالية\n<:blue_dot:1311418123404181585> أتمم على الأقل زنزانة واحدة متوسطة\n<:blue_dot:1311418123404181585> عند اكتمالها، سيحصل المُحيل على مكافآت سلبية من نشاطاتك!",
                    "<:blue_dot:1311418123404181585> Atteignez 500 lancers au total\n<:blue_dot:1311418123404181585> Terminez au moins 1 donjon moyen\n<:blue_dot:1311418123404181585> Une fois fait, votre parrain gagnera des récompenses passives grâce à vos activités !"
                ),
                inline=False
            )
            
            await interaction.response.edit_message(embed=embed, view=self)
            
        except Exception as e:
            lang = await _get_lang(interaction.user.id)
            await interaction.response.send_message(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_referral(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await _get_lang(interaction.user.id)
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        embed = discord.Embed(
            title=_tr(lang, "❌ Referral Cancelled", "❌ تم إلغاء الإحالة", "❌ Parrainage annulé"),
            description=_tr(lang, "The referral invitation has been cancelled.", "تم إلغاء دعوة الإحالة.", "L’invitation de parrainage a été annulée."),
            color=discord.Color.red()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

async def setup(bot):
    await bot.add_cog(ReferralCommands(bot))
