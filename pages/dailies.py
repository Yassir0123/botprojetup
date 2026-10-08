import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta
import asyncio
import time
import functools
from typing import List, Dict, Union
from utils.database import execute_query_with_retry, get_metrics
from utils.db_atomic import advisory_lock_for_context, db_transaction

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
async def _get_lang_from_discord_id(discord_id: int) -> int:
    """Fetch user's language (0=en, 1=ar, 3=fr) using their Discord ID."""
    try:
        return await get_user_multilanguage(int(discord_id))
    except Exception:
        return 0

async def _get_lang_from_user_id(user_id: int) -> int:
    """Fetch user's language using internal user_id."""
    try:
        # Look up their discord id then call the other helper
        res = await execute_query_with_retry("SELECT user_authorid FROM user WHERE user_id = %s", (user_id,))
        if res:
            return await _get_lang_from_discord_id(int(res[0]['user_authorid']))
    except Exception:
        pass
    return 0

# Create a command cooldown decorator
def command_cooldown(rate=1, per=3.0, type=commands.BucketType.user):
    """
    A robust cooldown decorator that prevents command spam, with basic localization.
    """
    cooldown = commands.Cooldown(rate, per)
    cooldowns = {}

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            # Determine language early for messages
            lang = await _get_lang_from_discord_id(ctx.author.id)

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
                        lang,
                        f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                        f"⏰ الأمر في فترة تهدئة. يرجى الانتظار {remaining} ثانية.",
                        f"⏰ Commande en temps de recharge. Veuillez attendre {remaining} secondes."
                    ))
                    return
            else:
                cooldowns[bucket_key] = commands.Cooldown(rate, per)

            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator

class DailyCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.BOOSTED_SERVER_ID = 832022150713704479  # The specific server ID to check for boosters

    async def get_user_id(self, discord_id):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (str(discord_id),))
        return result[0]['user_id'] if result else None

    async def get_inventory_id(self, user_id):
        query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['inventory_id'] if result else None

    async def get_user_info(self, user_id):
        query = """
            SELECT date_daily, daily_streak, prenium, prenium_date, roll_cap 
            FROM user 
            WHERE user_id = %s
        """
        result = await execute_query_with_retry(query, (user_id,))
        return result[0] if result else None

    async def check_and_update_prenium(self, user_id, user_info):
        if user_info['prenium'] != 0 and user_info['prenium_date']:
            current_time = datetime.now()
            prenium_end_date = user_info['prenium_date'] + timedelta(days=30)

            if current_time >= prenium_end_date:
                # Update roll cap based on previous prenium level
                roll_cap_reduction = user_info['prenium']
                update_roll_query = """
                    UPDATE user 
                    SET roll_cap = roll_cap - %s 
                    WHERE user_id = %s
                """
                await execute_query_with_retry(update_roll_query, (roll_cap_reduction, user_id))

                # Reset prenium status
                reset_query = """
                    UPDATE user 
                    SET prenium = 0, prenium_date = NULL 
                    WHERE user_id = %s
                """
                await execute_query_with_retry(reset_query, (user_id,))
                return 0

        return user_info['prenium']

    async def update_daily_info(self, user_id, new_date, new_streak):
        query = "UPDATE user SET date_daily = %s, daily_streak = %s WHERE user_id = %s"
        await execute_query_with_retry(query, (new_date, new_streak, user_id))

    def get_won(self, streak):
        if streak <= 3:
            return random.randint(500, 1000)
        elif streak <= 6:
            return random.randint(1000, 5000)
        else:
            return random.randint(5000, 10000)

    def get_gwon(self, streak):
        if streak <= 3:
            return random.randint(100, 250)
        elif streak <= 6:
            return random.randint(250, 500)
        else:
            return random.randint(500, 1000)

    def get_chest_rewards(self, streak, prenium_level):
        chests = []
        if prenium_level == 3:
            # Modified probabilities for prenium 3 for both <= 3 and <= 6
            if streak <= 3:
                rand = random.random()
                if rand < 0.7:  # 70% shadow
                    chests.append((1, 1))
                elif rand < 0.9:  # 20% hunter
                    chests.append((2, 1))
                else:  # 10% monarch
                    chests.append((3, 1))
            elif streak <= 6:
                rand = random.random()
                if rand < 0.3:  # 30% shadow
                    chests.append((1, 1))
                elif rand < 0.8:  # 50% hunter
                    chests.append((2, 1))
                elif rand < 0.9:  # 10% both
                    chests.append((1, 1))
                    chests.append((2, 1))
                else:  # 10% monarch
                    chests.append((3, 1))
            else:  # streak 7
                chests.append((3, 1))
                rand = random.random()
                if rand < 0.4:
                    pass
                elif rand < 0.6:
                    chests.append((2, random.randint(1, 2)))
                elif rand < 0.8:
                    chests.append((1, random.randint(1, 4)))
                else:
                    chests.append((2, 1))
                    chests.append((1, random.randint(1, 3)))

        elif prenium_level == 2:
            # Modified probabilities for prenium 2 only for <= 6
            if streak <= 3:
                chest_id = 1 if random.random() < 0.9 else 2
                chests.append((chest_id, 1))
            elif streak <= 6:
                rand = random.random()
                if rand < 0.3:  # 30% shadow
                    chests.append((1, 1))
                elif rand < 0.8:  # 50% hunter
                    chests.append((2, 1))
                elif rand < 0.9:  # 10% both
                    chests.append((1, 1))
                    chests.append((2, 1))
                else:  # 10% monarch
                    chests.append((3, 1))
            else:  # streak 7
                chests.extend(self._get_streak_seven_rewards())

        else:  # prenium 0 or 1 - original logic
            if streak <= 3:
                chest_id = 1 if random.random() < 0.9 else 2
                chests.append((chest_id, 1))
            elif streak <= 6:
                rand = random.random()
                if rand < 0.3:
                    chests.append((1, 1))
                elif rand < 0.8:
                    chests.append((2, 1))
                else:
                    chests.append((1, 1))
                    chests.append((2, 1))
            else:
                chests.extend(self._get_streak_seven_rewards())

        return chests

    def _get_streak_seven_rewards(self):
        chests = [(3, 1)]  # Guaranteed monarch chest
        rand = random.random()
        if rand < 0.4:
            pass
        elif rand < 0.6:
            chests.append((2, random.randint(1, 2)))
        elif rand < 0.8:
            chests.append((1, random.randint(1, 4)))
        else:
            chests.append((2, 1))
            chests.append((1, random.randint(1, 3)))
        return chests

    async def add_prenium_chest(self, inventory_id, prenium_level):
        chest_id = prenium_level + 3  # 4 for prenium 1, 5 for prenium 2, 6 for prenium 3
        query = "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
        result = await execute_query_with_retry(query, (inventory_id, chest_id))

        if result:
            new_quantity = result[0]['quantiter'] + 1
            update_query = """
                UPDATE inventory_chest 
                SET quantiter = %s 
                WHERE inventory_id = %s AND chest_id = %s
            """
            await execute_query_with_retry(update_query, (new_quantity, inventory_id, chest_id))
        else:
            insert_query = """
                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                VALUES (%s, %s, %s)
            """
            await execute_query_with_retry(insert_query, (inventory_id, chest_id, 1))

    async def add_chest_to_inventory(self, inventory_id, chest_id, quantity):
        query = "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
        result = await execute_query_with_retry(query, (inventory_id, chest_id))

        if result:
            new_quantity = result[0]['quantiter'] + quantity
            update_query = """
                UPDATE inventory_chest 
                SET quantiter = %s 
                WHERE inventory_id = %s AND chest_id = %s
            """
            await execute_query_with_retry(update_query, (new_quantity, inventory_id, chest_id))
        else:
            insert_query = """
                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                VALUES (%s, %s, %s)
            """
            await execute_query_with_retry(insert_query, (inventory_id, chest_id, quantity))

    async def check_bonus_card(self, user_id):
        query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = 108"
        result = await execute_query_with_retry(query, (user_id,))
        return bool(result)

    async def update_currency(self, user_id, amount, gamount):
        query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
        await execute_query_with_retry(query, (amount, user_id))
        query = "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s"
        await execute_query_with_retry(query, (gamount, user_id))

    async def check_server_booster(self, user_id):
        """Check if the user is boosting the specified server"""
        try:
            # Get the Discord user ID from the database
            query = "SELECT user_authorid FROM user WHERE user_id = %s"
            result = await execute_query_with_retry(query, (user_id,))
            if not result:
                return False

            discord_id = int(result[0]['user_authorid'])

            # Get the server object
            server = self.bot.get_guild(self.BOOSTED_SERVER_ID)
            if not server:
                return False

            # Get the member object
            member = server.get_member(discord_id)
            if not member:
                try:
                    member = await server.fetch_member(discord_id)
                except discord.NotFound:
                    return False
                except Exception as e:
                    print(f"Error fetching member: {e}")
                    return False

            # Check if the member is boosting the server
            return member.premium_since is not None
        except Exception as e:
            print(f"Error checking server booster status: {e}")
            return False

    async def add_roll_reset_to_inventory(self, inventory_id):
        """Add a roll reset to the user's inventory"""
        try:
            # Check if the user already has roll resets in inventory
            query = """
                SELECT quantiter 
                FROM inventory_product 
                WHERE inventory_id = %s AND product_id = 2
            """
            result = await execute_query_with_retry(query, (inventory_id,))

            if result:
                # Update existing quantity
                update_query = """
                    UPDATE inventory_product 
                    SET quantiter = quantiter + 3 
                    WHERE inventory_id = %s AND product_id = 2
                """
                await execute_query_with_retry(update_query, (inventory_id,))
            else:
                # Insert new entry
                insert_query = """
                    INSERT INTO inventory_product (inventory_id, product_id, quantiter) 
                    VALUES (%s, 2, 3)
                """
                await execute_query_with_retry(insert_query, (inventory_id,))

            return True
        except Exception as e:
            print(f"Error adding roll reset to inventory: {e}")
            return False

    @commands.command(name="daily", aliases=["dl", "dy"])
    @advisory_lock_for_context("slbot:daily")
    async def daily(self, ctx):
        try:
            lang = await _get_lang_from_discord_id(ctx.author.id)

            user_id = await self.get_user_id(ctx.author.id)
            if not user_id:
                await ctx.reply(_tr(
                    lang,
                    "You must be registered in the System to receive daily rewards.",
                    "يجب أن تكون مسجلاً في النظام لتستلم المكافآت اليومية.",
                    "Vous devez être inscrit au Système pour recevoir les récompenses quotidiennes."
                ))
                return

            inventory_id = await self.get_inventory_id(user_id)
            user_info = await self.get_user_info(user_id)

            # Check and update prenium status
            current_prenium = await self.check_and_update_prenium(user_id, user_info)

            current_time = datetime.now()
            last_daily = user_info['date_daily']
            streak = user_info['daily_streak']

            if last_daily:
                time_diff = timedelta(hours=24) - (current_time - last_daily)
                if time_diff > timedelta():
                    if time_diff >= timedelta(hours=23):
                        await ctx.reply(_tr(
                            lang,
                            "System on cooldown. Try again next day.",
                            "النظام في فترة تهدئة. جرّب غدًا.",
                            "Système en recharge. Réessayez demain."
                        ))
                    elif time_diff.seconds // 3600 > 0:
                        hours = time_diff.seconds // 3600
                        await ctx.reply(_tr(
                            lang,
                            f"System on cooldown. {hours} hour{'s' if hours > 1 else ''} left.",
                            f"النظام في فترة تهدئة. تبقى {hours} ساعة.",
                            f"Système en recharge. Il reste {hours} heure{'s' if hours > 1 else ''}."
                        ))
                    elif time_diff.seconds // 60 > 0:
                        minutes = time_diff.seconds // 60
                        await ctx.reply(_tr(
                            lang,
                            f"System on cooldown. {minutes} minute{'s' if minutes > 1 else ''} left.",
                            f"النظام في فترة تهدئة. تبقى {minutes} دقيقة.",
                            f"Système en recharge. Il reste {minutes} minute{'s' if minutes > 1 else ''}."
                        ))
                    else:
                        seconds = time_diff.seconds
                        await ctx.reply(_tr(
                            lang,
                            f"System on cooldown. {seconds} second{'s' if seconds > 1 else ''} left.",
                            f"النظام في فترة تهدئة. تبقى {seconds} ثانية.",
                            f"Système en recharge. Il reste {seconds} seconde{'s' if seconds > 1 else ''}."
                        ))
                    return

            new_streak = streak + 1 if last_daily and current_time - last_daily < timedelta(hours=48) else 1

            # Get rewards
            won = self.get_won(new_streak)
            gwon = self.get_gwon(new_streak)
            chest_rewards = self.get_chest_rewards(new_streak, current_prenium)

            # Check for bonus card
            has_bonus = await self.check_bonus_card(user_id)
            if has_bonus:
                won += 500

            # Check if user is a server booster
            is_booster = await self.check_server_booster(user_id)
            booster_bonus = False

            if is_booster:
                # Add bonus currency
                won += 10000
                gwon += 5000
                booster_bonus = True

            async with db_transaction():
                await self.update_daily_info(user_id, current_time, new_streak)

                if is_booster:
                    # Add roll reset to inventory
                    await self.add_roll_reset_to_inventory(inventory_id)

                # Add regular chests to inventory
                for chest_id, quantity in chest_rewards:
                    await self.add_chest_to_inventory(inventory_id, chest_id, quantity)

                # Add prenium chest if applicable
                if current_prenium > 0:
                    await self.add_prenium_chest(inventory_id, current_prenium)

                await self.update_currency(user_id, won, gwon)

                if new_streak == 7:
                    await self.update_daily_info(user_id, current_time, 0)

            # Create embed
            embed = discord.Embed(color=0x1a1a1a)
            embed.set_author(
                name=_tr(lang, "Daily System Rewards", "مكافآت النظام اليومية", "Récompenses quotidiennes du Système"),
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            embed.description = _tr(
                lang,
                "The System has processed your daily login.\nRewards have been added to your inventory.",
                "قام النظام بمعالجة تسجيل دخولك اليومي.\nتمت إضافة المكافآت إلى جردك.",
                "Le Système a traité votre connexion quotidienne.\nLes récompenses ont été ajoutées à votre inventaire."
            )

            progress_filled = "█" * min(new_streak, 7)
            progress_empty = "░" * (7 - min(new_streak, 7))
            embed.add_field(
                name=_tr(lang, "📊 Arise Sequence", "📊 تسلسل النهوض", "📊 Séquence d'éveil"),
                value=f"<:arrowpurple:1309253795909468160> `{progress_filled}{progress_empty}` " +
                      _tr(lang, f"Day {new_streak}", f"اليوم {new_streak}", f"Jour {new_streak}"),
                inline=False
            )

            # Get chest information for display
            chest_query = "SELECT * FROM chest WHERE chest_id = %s"
            chest_display = []
            for chest_id, quantity in chest_rewards:
                chest_info = await execute_query_with_retry(chest_query, (chest_id,))
                chest_display.append(f"{quantity}x {chest_info[0]['chest_icon']}")

            # Add prenium chest to display if applicable
            if current_prenium > 0:
                prenium_chest_info = await execute_query_with_retry(chest_query, (current_prenium + 3,))
                chest_display.append(f"{prenium_chest_info[0]['chest_name']} : 1x {prenium_chest_info[0]['chest_icon']}")

            rewards_display = [
                ("<:arrowpurple:1309253795909468160> " + _tr(lang, "Won Obtained:", "الون المكتسب:", "Won obtenu :"),
                 f"{won:,} <:won:1304034228966658048>"),
                ("<:arrowpurple:1309253795909468160> " + _tr(lang, "GWon Obtained:", "الجون المكتسب:", "GWon obtenu :"),
                 f"{gwon:,} <:gamble:1314341368176574564>"),
                ("<:arrowpurple:1309253795909468160> " + _tr(lang, "Chests Obtained:", "الصناديق المكتسبة:", "Coffres obtenus :"),
                 "\n".join(chest_display) if chest_display else _tr(lang, "None", "لا شيء", "Aucun"))
            ]

            # Add booster bonus display if applicable
            if booster_bonus:
                rewards_display.append((
                    "<:arrowpurple:1309253795909468160> " + _tr(lang, "Server Booster Bonus:", "مكافأة داعم الخادم:", "Bonus de booster de serveur :"),
                    _tr(lang, "3x <:reroll:1302672409278545940> Roll Reset", "٣× <:reroll:1302672409278545940> إعادة رمية", "3× <:reroll:1302672409278545940> Réinitialisation de tirage")
                ))

            embed.add_field(
                name="<:reward:1309253302667710596> " + _tr(lang, "Rewards", "المكافآت", "Récompenses"),
                value="\n".join([f"{label} {val}" for label, val in rewards_display]),
                inline=False
            )

            if new_streak < 3:
                next_milestone = _tr(lang, "Reach day 4 for improved rewards", "وصل إلى اليوم الرابع لتحسين المكافآت", "Atteignez le jour 4 pour de meilleures récompenses")
            elif new_streak < 6:
                next_milestone = _tr(lang, "Reach day 7 for Monarch chest", "وصل إلى اليوم السابع لصندوق الملك", "Atteignez le jour 7 pour un coffre Monarque")
            else:
                next_milestone = _tr(lang, "Maximum rewards achieved", "تم تحقيق أقصى المكافآت", "Récompenses maximales atteintes")

            footer_text = _tr(lang, "Next Milestone:", "الهدف التالي:", "Prochaine étape :") + f" {next_milestone}"
            if booster_bonus:
                footer_text += " | " + _tr(lang, "Server Booster Bonus Applied", "تم تطبيق مكافأة داعم الخادم", "Bonus de booster de serveur appliqué")

            embed.set_footer(text=footer_text)

            await ctx.reply(embed=embed)

        except Exception as e:
            print(f"Error in daily command: {e}")
            lang = await _get_lang_from_discord_id(ctx.author.id)
            await ctx.reply(_tr(
                lang,
                "A System error has occurred. Please try again later.",
                "حدث خطأ في النظام. يرجى المحاولة لاحقًا.",
                "Une erreur du Système s'est produite. Veuillez réessayer plus tard."
            ))

    @commands.command(name="dailydbstats")
    @commands.has_permissions(administrator=True)
    async def db_stats(self, ctx):
        """Display database connection statistics (localized fields)"""
        metrics = get_metrics()
        lang = await _get_lang_from_discord_id(ctx.author.id)

        embed = discord.Embed(
            title=_tr(lang, "Database Connection Metrics", "مقاييس اتصال قاعدة البيانات", "Métriques de connexion à la base de données"),
            color=discord.Color.blue()
        )

        embed.add_field(name=_tr(lang, "Active Connections", "الاتصالات النشطة", "Connexions actives"),
                        value=str(metrics["active_connections"]), inline=True)
        embed.add_field(name=_tr(lang, "Peak Connections", "أقصى عدد اتصالات", "Pic de connexions"),
                        value=str(metrics["peak_connections"]), inline=True)
        embed.add_field(name=_tr(lang, "Connection Errors", "أخطاء الاتصال", "Erreurs de connexion"),
                        value=str(metrics["connection_errors"]), inline=True)

        embed.add_field(name=_tr(lang, "Total Queries", "إجمالي الاستعلامات", "Requêtes totales"),
                        value=str(metrics["total_queries"]), inline=True)
        embed.add_field(name=_tr(lang, "Slow Queries", "استعلامات بطيئة", "Requêtes lentes"),
                        value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
        embed.add_field(name=_tr(lang, "Avg Query Time", "متوسط زمن الاستعلام", "Temps moyen de requête"),
                        value=f"{metrics['avg_query_time']:.2f}ms", inline=True)

        if metrics["last_error"]:
            embed.add_field(
                name=_tr(lang, "Last Error", "آخر خطأ", "Dernière erreur"),
                value=f"{metrics['last_error']}\n"
                      + _tr(lang, "Time", "الوقت", "Heure")
                      + f": {metrics['last_error_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(DailyCommands(bot))
