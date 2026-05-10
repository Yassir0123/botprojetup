import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta
import aiomysql
import time
import asyncio
from typing import List, Dict, Union
from enum import Enum
from utils.database import execute_query_with_retry, get_metrics
import functools

# ---------------------------
# Simple translation helpers
# ---------------------------
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    """Return a localized string based on lang (0=en, 1=ar, 3=fr)."""
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en  # default EN

async def _get_lang(user_or_id) -> int:
    """Fetch user's language (0=en, 1=ar, 3=fr)."""
    try:
        uid = user_or_id.id if hasattr(user_or_id, "id") else int(user_or_id)
        return await get_user_multilanguage(uid)
    except Exception:
        return 0
async def get_user_multilanguage(user_authorid: int) -> int:
    """
    Fetch multilanguage flag for a Discord user. Fallback to 0 (English).
    0 = English, 1 = Arabic, 3 = French
    """
    try:
        q = "SELECT multilanguage FROM user WHERE user_authorid = %s"
        res = await execute_query_with_retry(q, (str(user_authorid),))
        if res and 'multilanguage' in res[0] and res[0]['multilanguage'] is not None:
            return int(res[0]['multilanguage'])
    except Exception as e:
        # Do not log per your request (no logging changes); silently fall back
        pass
    return 0
# Create a command cooldown decorator
def command_cooldown(rate=1, per=3.0, type=commands.BucketType.user):
    """
    A robust cooldown decorator that prevents command spam
    """
    cooldown = commands.Cooldown(rate, per)
    cooldowns = {}
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            # Get the bucket key based on the cooldown type
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id
                
            # Check if the command is on cooldown
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    remaining = round(retry_after, 1)
                    await ctx.send(_tr(
                        await _get_lang(ctx.author),
                        f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                        f"⏰ الأمر على فترة انتظار. الرجاء الانتظار {remaining} ثانية.",
                        f"⏰ Commande en recharge. Veuillez patienter {remaining} secondes."
                    ))
                    return
            else:
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
                
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator

class QuestStatus(Enum):
    INCOMPLETE = 0
    COMPLETE = 1

class DifficultyType(Enum):
    NORMAL = "Normal"
    HIGH = "High"
    DANGER = "Danger"
    DISASTER = "Disaster"

class QuestCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rewards = {
            "Normal": ["Magic Core", "Healing Crystal", "Mana Stone"],
            "High": ["Greater Magic Core", "Monster Core", "Ancient Rune"],
            "Danger": ["Demon Core", "Dragon's Essence", "Soul Fragment"],
            "Disaster": ["Black Heart", "Sovereign's Breath", "S-Grade Magic Core"]
        }
        
        self.difficulty_colors = {
            "Normal": discord.Color.from_rgb(67, 181, 129),
            "High": discord.Color.from_rgb(250, 168, 26),
            "Danger": discord.Color.from_rgb(240, 71, 71),
            "Disaster": discord.Color.from_rgb(138, 43, 226)
        }
        
        self.status_indicators = {0: "⚡ ACTIVE", 1: "✨ CLEARED"}
        self.REQUIRED_QUEST_COUNT = 5

    @commands.command(name="quest")
    async def quest(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        discord_id = str(ctx.author.id)
        try:
            user_data = await execute_query_with_retry(
                "SELECT user_id, quest_status, quest_daily FROM user WHERE user_authorid = %s",
                (discord_id,)
            )
            if not user_data:
                await ctx.send(_tr(
                    lang,
                    "You haven't registered yet. Please register before using the quest system.",
                    "لم تقم بالتسجيل بعد. الرجاء التسجيل قبل استخدام نظام المهام.",
                    "Vous ne vous êtes pas encore inscrit. Veuillez vous inscrire avant d'utiliser le système de quêtes."
                ))
                return

            user_id = user_data[0]['user_id']
            quest_status = user_data[0]['quest_status']
            quest_daily = user_data[0]['quest_daily']

            current_time = datetime.now()
            should_reset = False
            if quest_daily is None:
                should_reset = True
                reset_reason = "first_time"
            elif (current_time - quest_daily) >= timedelta(hours=24):
                should_reset = True
                reset_reason = "daily_reset"
            
            if should_reset:
                await self.reset_quests(user_id, current_time)
                if reset_reason == "first_time":
                    await ctx.send(_tr(
                        lang,
                        "New daily quests have appeared, brave adventurer!",
                        "ظهرت مهام يومية جديدة أيها المغامر الشجاع!",
                        "De nouvelles quêtes quotidiennes sont apparues, brave aventurier !"
                    ))
                else:
                    await ctx.send(_tr(
                        lang,
                        "Your quests have been refreshed for a new day!",
                        "تم تحديث مهامك ليوم جديد!",
                        "Vos quêtes ont été actualisées pour une nouvelle journée !"
                    ))
                
                user_data = await execute_query_with_retry(
                    "SELECT quest_status, quest_daily FROM user WHERE user_id = %s",
                    (user_id,)
                )
                quest_status = user_data[0]['quest_status']
                quest_daily = user_data[0]['quest_daily']

            incomplete_query = """
                SELECT COUNT(*) as incomplete_count 
                FROM user_quests 
                WHERE user_id = %s AND completion_status = %s
            """
            incomplete_result = await execute_query_with_retry(
                incomplete_query, (user_id, QuestStatus.INCOMPLETE.value)
            )
            incomplete_count = incomplete_result[0]['incomplete_count']
            all_quests_completed = incomplete_count == 0
            
            total_quests_query = "SELECT COUNT(*) as total_quests FROM user_quests WHERE user_id = %s"
            total_quests_result = await execute_query_with_retry(total_quests_query, (user_id,))
            total_quests = total_quests_result[0]['total_quests']
            
            if total_quests == 0 and quest_status != QuestStatus.COMPLETE.value:
                await self.reset_quests(user_id, current_time)
                await ctx.send(_tr(
                    lang,
                    "New quests have been generated for you!",
                    "تم إنشاء مهام جديدة لك!",
                    "De nouvelles quêtes ont été générées pour vous !"
                ))
                
                incomplete_result = await execute_query_with_retry(
                    incomplete_query, (user_id, QuestStatus.INCOMPLETE.value)
                )
                incomplete_count = incomplete_result[0]['incomplete_count']
                all_quests_completed = incomplete_count == 0

            if all_quests_completed and total_quests > 0:
                if quest_status != QuestStatus.COMPLETE.value:
                    await self.distribute_quest_rewards(user_id, ctx)
                    return

            if quest_status == QuestStatus.COMPLETE.value:
                next_reset = quest_daily + timedelta(days=1)
                time_left = next_reset - current_time

                embed = discord.Embed(
                    title=_tr(lang, "Daily Quests Completed", "تم إكمال المهام اليومية", "Quêtes quotidiennes terminées"),
                    description=_tr(
                        lang,
                        "You have already completed all your quests for today!",
                        "لقد أكملت جميع مهامك لليوم!",
                        "Vous avez déjà terminé toutes vos quêtes pour aujourd'hui !"
                    ),
                    color=discord.Color.green()
                )
                
                hours, remainder = divmod(int(time_left.total_seconds()), 3600)
                minutes = remainder // 60
                embed.set_footer(text=_tr(
                    lang,
                    f"Next quest reset in {hours}h {minutes}m",
                    f"إعادة تعيين المهام بعد {hours}س {minutes}د",
                    f"Prochaine réinitialisation des quêtes dans {hours}h {minutes}m"
                ))
                
                await ctx.send(embed=embed)
                return

            await self.display_quests(ctx, user_id)
            await self.validate_quest_count(user_id)

        except Exception as e:
            print(f"Error in quest command: {e}")
            await ctx.send(_tr(
                lang,
                "A mystical force prevents me from retrieving your quests. Try again later.",
                "قوة غامضة تمنعني من جلب مهامك. حاول لاحقًا.",
                "Une force mystique m'empêche de récupérer vos quêtes. Réessayez plus tard."
            ))

    async def distribute_quest_rewards(self, user_id: int, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s",
                (user_id,)
            )
            if not inventory:
                await ctx.send(_tr(
                    lang,
                    "Could not find your inventory, brave adventurer.",
                    "تعذر العثور على جردك، أيها المغامر الشجاع.",
                    "Impossible de trouver votre inventaire, brave aventurier."
                ))
                return

            inventory_id = inventory[0]['inventory_id']
            shadow_chest_product_ids = [2, 3]  # example

            for product_id in shadow_chest_product_ids:
                existing_product = await execute_query_with_retry(
                    "SELECT 1 FROM inventory_product WHERE inventory_id = %s AND product_id = %s",
                    (inventory_id, product_id)
                )
                if existing_product:
                    await execute_query_with_retry(
                        "UPDATE inventory_product SET quantiter = quantiter + 1 WHERE inventory_id = %s AND product_id = %s",
                        (inventory_id, product_id)
                    )
                else:
                    await execute_query_with_retry(
                        "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, %s, 1)",
                        (inventory_id, product_id)
                    )

            await execute_query_with_retry(
                "UPDATE user SET quest_status = %s WHERE user_id = %s",
                (QuestStatus.COMPLETE.value, user_id)
            )
            await execute_query_with_retry(
                "UPDATE user_quests SET completion_status = %s WHERE user_id = %s",
                (QuestStatus.COMPLETE.value, user_id)
            )

            embed = discord.Embed(
                title=_tr(lang, "Daily Quests Completed!", "تم إكمال المهام اليومية!", "Quêtes quotidiennes terminées !"),
                description=_tr(
                    lang,
                    "You've successfully completed all your daily quests!",
                    "لقد أكملت جميع مهامك اليومية بنجاح!",
                    "Vous avez terminé toutes vos quêtes quotidiennes avec succès !"
                ),
                color=discord.Color.gold()
            )
            embed.add_field(
                name=_tr(lang, "Rewards", "المكافآت", "Récompenses"),
                value="<:reroll:1302672409278545940> reroll\n<:statreset:1302627469177651232> stat reset",
                inline=False
            )
            embed.set_footer(text=_tr(
                lang,
                "Rewards have been added to your inventory!",
                "تمت إضافة المكافآت إلى جردك!",
                "Les récompenses ont été ajoutées à votre inventaire !"
            ))

            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error distributing rewards: {e}")
            await ctx.send(_tr(
                lang,
                "An error occurred while distributing rewards.",
                "حدث خطأ أثناء توزيع المكافآت.",
                "Une erreur s'est produite lors de la distribution des récompenses."
            ))

    async def display_quests(self, ctx, user_id: int):
        lang = await get_user_multilanguage(ctx.author.id)
        print(f"Displaying quests for User ID: {user_id}")

        # Fetch quests with ALL localized name/description columns
        quests = await execute_query_with_retry(
            """
            SELECT 
                uq.quest_id, 
                q.quest_name,
                q.quest_arname,
                q.quest_frname,
                q.quest_description,
                q.quest_ardescription,
                q.quest_frdescription,
                q.product_icon,
                uq.current_count, 
                q.required_count, 
                uq.completion_status
            FROM user_quests uq 
            JOIN quest q ON uq.quest_id = q.quest_id 
            WHERE uq.user_id = %s
            """,
            (user_id,)
        )

        print("Quests Found:")
        for quest in quests:
            print(quest)

        if not quests:
            embed = discord.Embed(
                title=_tr(lang, "No Quests Available", "لا توجد مهام متاحة", "Aucune quête disponible"),
                description=_tr(
                    lang,
                    "No active quests found. Generating new quests...",
                    "لم يتم العثور على مهام نشطة. يتم إنشاء مهام جديدة...",
                    "Aucune quête active trouvée. Génération de nouvelles quêtes..."
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            
            current_time = datetime.now()
            await self.reset_quests(user_id, current_time)
            
            quests = await execute_query_with_retry(
                """
                SELECT 
                    uq.quest_id, 
                    q.quest_name,
                    q.quest_arname,
                    q.quest_frname,
                    q.quest_description,
                    q.quest_ardescription,
                    q.quest_frdescription,
                    q.product_icon,
                    uq.current_count, 
                    q.required_count, 
                    uq.completion_status
                FROM user_quests uq 
                JOIN quest q ON uq.quest_id = q.quest_id 
                WHERE uq.user_id = %s
                """,
                (user_id,)
            )
            
            if not quests:
                await ctx.send(_tr(
                    lang,
                    "Failed to generate new quests. Please try again later.",
                    "فشل إنشاء مهام جديدة. الرجاء المحاولة لاحقًا.",
                    "Échec de la génération de nouvelles quêtes. Veuillez réessayer plus tard."
                ))
                return

        for quest in quests:
            if quest['current_count'] < 0:
                print(f"Fixing negative quest count for quest_id {quest['quest_id']}")
                await execute_query_with_retry(
                    "UPDATE user_quests SET current_count = 0 WHERE user_id = %s AND quest_id = %s",
                    (user_id, quest['quest_id'])
                )
                quest['current_count'] = 0

        embed = discord.Embed(
            title=_tr(lang, "Active Quests", "المهام النشطة", "Quêtes actives"),
            description=_tr(
                lang,
                "Complete your daily quests to unlock treasure chests, a <:reroll:1302672409278545940> reroll, and a <:statreset:1302627469177651232> stat reset!",
                "أكمل مهامك اليومية لفتح صناديق الكنز، وإعادة تدوير <:reroll:1302672409278545940>، وإعادة تعيين الإحصائيات <:statreset:1302627469177651232>!",
                "Terminez vos quêtes quotidiennes pour obtenir des coffres, un <:reroll:1302672409278545940> reroll et un <:statreset:1302627469177651232> reset de stats !"
            ),
            color=discord.Color.from_rgb(47, 49, 54)
        )

        for quest in quests:
            # Localized NAME (lang: 0=en, 1=ar, 3=fr)
            if lang == 1 and quest.get('quest_arname'):
                qname = quest['quest_arname']
            elif lang == 3 and quest.get('quest_frname'):
                qname = quest['quest_frname']
            else:
                qname = quest.get('quest_name') or ""

            # Localized DESCRIPTION
            if lang == 1 and quest.get('quest_ardescription'):
                qdesc = quest['quest_ardescription']
            elif lang == 3 and quest.get('quest_frdescription'):
                qdesc = quest['quest_frdescription']
            else:
                qdesc = quest.get('quest_description') or ""

            status = _tr(
                lang,
                "Completed <:bluecheck:1314355807034282054>" if quest['completion_status'] == QuestStatus.COMPLETE.value else "In Progress",
                "مكتملة <:bluecheck:1314355807034282054>" if quest['completion_status'] == QuestStatus.COMPLETE.value else "قيد التقدم",
                "Terminée <:bluecheck:1314355807034282054>" if quest['completion_status'] == QuestStatus.COMPLETE.value else "En cours"
            )
            
            quest_text = (
                f"<:blue_dot:1311418123404181585> " + _tr(lang, "Status", "الحالة", "Statut") + f": {status}\n"
                f"<:blue_dot:1311418123404181585> " + _tr(lang, "Reward", "المكافأة", "Récompense") + ": 1 x <:shadowchest:1303850149122015324> Shadow Chest"
                + (f"\n<:blue_dot:1311418123404181585> " + _tr(lang, "Description", "الوصف", "Description") + f": {qdesc}" if qdesc else "")
            )
            
            embed.add_field(
                name=f"{quest.get('product_icon', '❓')} {qname} - {quest['current_count']}/{quest['required_count']}",
                value=quest_text,
                inline=False
            )

        if ctx.author.avatar:
            embed.set_thumbnail(url=ctx.author.avatar.url)

        user_data = await execute_query_with_retry("SELECT quest_daily FROM user WHERE user_id = %s", (user_id,))
        user_data = user_data[0]
        next_reset = user_data['quest_daily'] + timedelta(days=1)
        time_left = next_reset - datetime.now()
        hours, remainder = divmod(int(time_left.total_seconds()), 3600)
        minutes = remainder // 60

        embed.set_footer(text=_tr(
            lang,
            f"The quests will refresh in {hours}hours {minutes}minutes",
            f"سيتم تحديث المهام خلال {hours} ساعة و {minutes} دقيقة",
            f"Les quêtes se réinitialiseront dans {hours} heures {minutes} minutes"
        ))

        await ctx.send(embed=embed)

    def create_progress_bar(self, current: int, total: int, length: int = 15) -> str:
        filled = int(length * current / total)
        empty = length - filled
        return f"[{'━' * filled}{'═' * empty}]"

    async def reset_quests(self, user_id: int, current_time: datetime):
        try:
            print(f"Resetting quests for user {user_id} at {current_time}")
            
            await execute_query_with_retry(
                """
                UPDATE user 
                SET quest_status = %s, 
                    quest_daily = %s 
                WHERE user_id = %s
                """,
                (QuestStatus.INCOMPLETE.value, current_time, user_id)
            )

            await execute_query_with_retry("DELETE FROM user_quests WHERE user_id = %s", (user_id,))

            all_quests = await execute_query_with_retry("SELECT quest_id, required_count FROM quest")
            
            quest_12_exists = False
            quest_12_data = None
            for quest in all_quests:
                if quest['quest_id'] == 12:
                    quest_12_exists = True
                    quest_12_data = quest
                    break
            
            selected_quest_ids = set()
            selected_quests = []
            if quest_12_exists:
                selected_quest_ids.add(12)
                selected_quests.append(quest_12_data)
                print(f"Added required quest_id 12 to user {user_id}'s quests")
            
            other_quests = [q for q in all_quests if q['quest_id'] != 12]
            random.shuffle(other_quests)
            
            remaining_slots = self.REQUIRED_QUEST_COUNT - len(selected_quests)
            for quest in other_quests:
                quest_id = quest['quest_id']
                if quest_id not in selected_quest_ids and len(selected_quests) < self.REQUIRED_QUEST_COUNT:
                    selected_quest_ids.add(quest_id)
                    selected_quests.append(quest)
                if len(selected_quests) >= self.REQUIRED_QUEST_COUNT:
                    break
            
            print(f"Selected {len(selected_quests)} unique quests for user {user_id}")
            
            for quest in selected_quests:
                await execute_query_with_retry(
                    """
                    INSERT INTO user_quests 
                    (user_id, quest_id, current_count, completion_status) 
                    VALUES (%s, %s, 0, %s)
                    """,
                    (user_id, quest['quest_id'], QuestStatus.INCOMPLETE.value)
                )
                
            await self.validate_quest_count(user_id)
                
        except Exception as e:
            print(f"Error resetting quests: {e}")
    
    async def validate_quest_count(self, user_id: int):
        try:
            count_query = "SELECT COUNT(*) as quest_count FROM user_quests WHERE user_id = %s"
            count_result = await execute_query_with_retry(count_query, (user_id,))
            current_count = count_result[0]['quest_count']
            
            print(f"Validating quest count for user {user_id}: Current count = {current_count}, Required count = {self.REQUIRED_QUEST_COUNT}")
            
            if current_count == self.REQUIRED_QUEST_COUNT:
                print(f"User {user_id} has the correct number of quests ({current_count})")
                return
                
            if current_count > 10:
                print(f"User {user_id} has too many quests ({current_count}). Resetting all quests.")
                await self.reset_quests(user_id, datetime.now())
                return
                
            if current_count < self.REQUIRED_QUEST_COUNT:
                print(f"User {user_id} has too few quests ({current_count}). Adding more quests.")
                
                current_quests_query = "SELECT quest_id FROM user_quests WHERE user_id = %s"
                current_quests_result = await execute_query_with_retry(current_quests_query, (user_id,))
                current_quest_ids = {q['quest_id'] for q in current_quests_result}
                
                all_quests_query = """
                    SELECT quest_id, required_count 
                    FROM quest 
                    WHERE quest_id NOT IN (
                        SELECT quest_id 
                        FROM user_quests 
                        WHERE user_id = %s
                    )
                """
                available_quests = await execute_query_with_retry(all_quests_query, (user_id,))
                random.shuffle(available_quests)
                
                quests_to_add = self.REQUIRED_QUEST_COUNT - current_count
                for i in range(min(quests_to_add, len(available_quests))):
                    quest = available_quests[i]
                    await execute_query_with_retry(
                        """
                        INSERT INTO user_quests 
                        (user_id, quest_id, current_count, completion_status) 
                        VALUES (%s, %s, 0, %s)
                        """,
                        (user_id, quest['quest_id'], QuestStatus.INCOMPLETE.value)
                    )
                    print(f"Added quest {quest['quest_id']} to user {user_id}")
                
                count_result = await execute_query_with_retry(count_query, (user_id,))
                updated_count = count_result[0]['quest_count']
                
                if updated_count < self.REQUIRED_QUEST_COUNT:
                    print(f"Still need {self.REQUIRED_QUEST_COUNT - updated_count} more quests but no unique quests available.")
            
            if self.REQUIRED_QUEST_COUNT < current_count <= 10:
                print(f"User {user_id} has too many quests ({current_count}). Removing excess quests.")
                
                current_quests_query = """
                    SELECT userquests_id, quest_id 
                    FROM user_quests 
                    WHERE user_id = %s 
                    ORDER BY 
                        CASE WHEN quest_id = 12 THEN 0 ELSE 1 END, 
                        userquests_id
                """
                current_quests = await execute_query_with_retry(current_quests_query, (user_id,))
                
                quests_to_remove = current_count - self.REQUIRED_QUEST_COUNT
                for i in range(quests_to_remove):
                    quest_to_remove = current_quests[-(i+1)]
                    await execute_query_with_retry(
                        "DELETE FROM user_quests WHERE userquests_id = %s",
                        (quest_to_remove['userquests_id'],)
                    )
                    print(f"Removed quest {quest_to_remove['quest_id']} (ID: {quest_to_remove['userquests_id']}) from user {user_id}")
            
            count_result = await execute_query_with_retry(count_query, (user_id,))
            final_count = count_result[0]['quest_count']
            print(f"Final quest count for user {user_id}: {final_count}")
            
        except Exception as e:
            print(f"Error validating quest count: {e}")
            
    def format_time_left(self, time_left: timedelta) -> str:
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours:02d}h {minutes:02d}m"
        
    @commands.command(name="questdbstats")
    @commands.has_permissions(administrator=True)
    async def db_stats(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        metrics = get_metrics()
        
        embed = discord.Embed(
            title=_tr(lang, "Database Connection Metrics", "مقاييس اتصال قاعدة البيانات", "Métriques de connexion à la base de données"),
            color=discord.Color.blue()
        )
        
        embed.add_field(name=_tr(lang, "Active Connections", "الاتصالات النشطة", "Connexions actives"), value=str(metrics["active_connections"]), inline=True)
        embed.add_field(name=_tr(lang, "Peak Connections", "أعلى عدد اتصالات", "Pic de connexions"), value=str(metrics["peak_connections"]), inline=True)
        embed.add_field(name=_tr(lang, "Connection Errors", "أخطاء الاتصال", "Erreurs de connexion"), value=str(metrics["connection_errors"]), inline=True)
        
        embed.add_field(name=_tr(lang, "Total Queries", "إجمالي الاستعلامات", "Requêtes totales"), value=str(metrics["total_queries"]), inline=True)
        embed.add_field(name=_tr(lang, "Slow Queries", "الاستعلامات البطيئة", "Requêtes lentes"), value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
        embed.add_field(name=_tr(lang, "Avg Query Time", "متوسط زمن الاستعلام", "Temps moyen des requêtes"), value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
        
        if metrics["last_error"]:
            embed.add_field(
                name=_tr(lang, "Last Error", "آخر خطأ", "Dernière erreur"),
                value=f"{metrics['last_error']}\n{_tr(lang, 'Time', 'الوقت', 'Heure')}: {metrics['last_error_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
            
        await ctx.send(embed=embed)

    @commands.command(name="fixquests")
    @commands.has_permissions(administrator=True)
    async def fix_quests(self, ctx, user_id: int = None):
        lang = await get_user_multilanguage(ctx.author.id)
        if user_id is None:
            user_id = ctx.author.id
            
        try:
            user_check = await execute_query_with_retry("SELECT user_id FROM user WHERE user_id = %s", (user_id,))
            if not user_check:
                await ctx.send(_tr(
                    lang,
                    f"Error: User ID {user_id} not found.",
                    f"خطأ: لم يتم العثور على معرف المستخدم {user_id}.",
                    f"Erreur : ID utilisateur {user_id} introuvable."
                ))
                return
                
            await self.validate_quest_count(user_id)
            
            count_query = "SELECT COUNT(*) as quest_count FROM user_quests WHERE user_id = %s"
            count_result = await execute_query_with_retry(count_query, (user_id,))
            current_count = count_result[0]['quest_count']
            
            await ctx.send(_tr(
                lang,
                f"Quest validation complete for user ID {user_id}. Current quest count: {current_count}",
                f"اكتمل التحقق من المهام لمعرف المستخدم {user_id}. عدد المهام الحالي: {current_count}",
                f"Validation des quêtes terminée pour l'ID utilisateur {user_id}. Nombre de quêtes actuel : {current_count}"
            ))
            
        except Exception as e:
            print(f"Error in fix_quests command: {e}")
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))

async def setup(bot):
    await bot.add_cog(QuestCommands(bot))

