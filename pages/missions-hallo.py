import discord
from discord.ext import commands
import random
import json
import logging
import traceback
from datetime import datetime
import asyncio
import os
import time
import functools
import uuid
import aiomysql
from typing import Optional, Union, List, Dict, Any, Tuple
from utils.database import execute_query_with_retry, get_metrics, get_connection, release_connection
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

# Command execution lock registry to prevent duplicate command execution
class CommandLockRegistry:
    def __init__(self):
        self.locks = {}
        self.lock_creation = asyncio.Lock()
    
    async def get_lock(self, user_id, command_name):
        """Get a lock for a specific user and command combination"""
        lock_key = f"{user_id}:{command_name}"
        
        async with self.lock_creation:
            if lock_key not in self.locks:
                self.locks[lock_key] = asyncio.Lock()
        
        return self.locks[lock_key]
    
    async def cleanup_old_locks(self):
        """Periodically clean up unused locks to prevent memory leaks"""
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            async with self.lock_creation:
                if len(self.locks) > 10000:
                    keys_to_remove = list(self.locks.keys())[:5000]
                    for key in keys_to_remove:
                        del self.locks[key]

# Global command lock registry
command_locks = CommandLockRegistry()

# Start the cleanup task
asyncio.create_task(command_locks.cleanup_old_locks())

# Command execution lock decorator
def command_lock(command_name=None):
    """
    Decorator to prevent multiple executions of the same command by the same user
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            user_id = ctx.author.id
            cmd_name = command_name or func.__name__
            lock = await command_locks.get_lock(user_id, cmd_name)
            lang = await _get_lang(ctx.author)
            try:
                acquired = await asyncio.wait_for(lock.acquire(), timeout=5.0)
                exec_id = str(uuid.uuid4())[:8]
                print(f"[{exec_id}] Starting command {cmd_name} for user {user_id}")
                try:
                    start_time = time.time()
                    result = await func(self, ctx, *args, **kwargs)
                    elapsed = time.time() - start_time
                    print(f"[{exec_id}] Completed command {cmd_name} for user {user_id} in {elapsed:.2f}s")
                    return result
                finally:
                    lock.release()
                    print(f"[{exec_id}] Released lock for command {cmd_name}, user {user_id}")
            except asyncio.TimeoutError:
                await ctx.send(_tr(
                    lang,
                    "You're already running this command. Please wait for it to complete.",
                    "أنت تستخدم هذا الأمر بالفعل. يرجى الانتظار حتى يكتمل.",
                    "Vous exécutez déjà cette commande. Veuillez attendre qu'elle se termine."
                ))
                print(f"Command {cmd_name} for user {user_id} rejected (already running)")
                return None
        return wrapper
    return decorator

# Transaction decorator with retry logic
def db_transaction(max_retries=3):
    """
    Decorator to wrap a function in a database transaction with retry logic
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                conn = None
                try:
                    conn = await get_connection()
                    if not conn:
                        if attempt == max_retries - 1:
                            raise Exception("Could not acquire database connection")
                        await asyncio.sleep(1)
                        continue
                    await conn.begin()
                    result = await func(*args, conn=conn, **kwargs)
                    await conn.commit()
                    return result
                except Exception as e:
                    if conn:
                        await conn.rollback()
                    if attempt == max_retries - 1:
                        print(f"Transaction failed after {max_retries} attempts: {e}")
                        raise
                    await asyncio.sleep(0.5 * (2 ** attempt))
                finally:
                    if conn:
                        await release_connection(conn)
        return wrapper
    return decorator

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
            lang = await _get_lang(ctx.author)
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    remaining = round(retry_after, 1)
                    await ctx.send(_tr(
                        lang,
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

class MissionView(discord.ui.View):
    def __init__(self, bot, user_fighter_cards, mission_card):
        super().__init__(timeout=60)  # 60 second timeout only for initial button press
        self.bot = bot
        self.user_fighter_cards = user_fighter_cards
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.mission_card = mission_card
        self.mission_completed = False
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"
        self.original_user = None
        self.message = None
        self.mission_started = False
        self.fight_log_history = []
        self.current_user_card = None
        self.current_monster = None
        self.canceled = False

    async def on_timeout(self):
        """Handle timeout by resetting mission state and disabling buttons"""
        try:
            if getattr(self, "canceled", False):
                return
            if not self.mission_started and self.original_user:
                user_id = await self.bot.get_cog('MissionCommands').get_user_id(self.original_user.id)
                lang = await _get_lang(self.original_user)
                if user_id:
                    await self.reset_mission_state(user_id)
                    logging.info(f"Mission state reset due to timeout for user {self.original_user.id}")
                for child in self.children:
                    child.disabled = True
                if self.message:
                    try:
                        timeout_embed = discord.Embed(
                            title=_tr(lang, "Mission Timed Out", "انتهى وقت المهمة", "Mission expirée"),
                            description=_tr(
                                lang,
                                "The mission has expired due to inactivity.",
                                "انتهت المهمة بسبب عدم النشاط.",
                                "La mission a expiré en raison de l'inactivité."
                            ),
                            color=discord.Color.red()
                        )
                        await self.message.edit(embed=timeout_embed, view=self, attachments=[])
                    except discord.errors.NotFound:
                        logging.info(f"Message not found when handling timeout for user {self.original_user.id}")
                    except Exception as e:
                        logging.error(f"Error updating message on timeout: {e}")
        except Exception as e:
            logging.error(f"Error in on_timeout: {e}")
            logging.error(traceback.format_exc())

    @db_transaction(max_retries=3)
    async def reset_mission_state(self, user_id, conn=None):
        """Reset mission state in database with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("UPDATE user SET mission_current = 0 WHERE user_id = %s", (user_id,))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting with the button is the original command user"""
        lang = await _get_lang(interaction.user)
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(_tr(
                lang,
                "Only the user who started this mission can use these buttons!",
                "فقط المستخدم الذي بدأ هذه المهمة يمكنه استخدام هذه الأزرار!",
                "Seul l'utilisateur qui a lancé cette mission peut utiliser ces boutons !"
            ), ephemeral=True)
            return False
        return True
        
    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)
    
    def get_battle_display(self, user_card, monster, action_text="Battle begins!"):
        """Create a minimal battle display that always shows health bars"""
        battle_header = f"**{user_card['card_name']} vs {monster['name']}**"
        user_health = self.create_health_bar(user_card)
        monster_health = self.create_health_bar(monster, is_monster=True)
        display = f"{battle_header}\n{user_health}\n{monster_health}\n{action_text}"
        if len(display) > 1000:
            display = f"{battle_header}\n{user_health}\n{monster_health}"
            if len(display) > 1000:
                display = f"{battle_header}\nBattle in progress!"
        return display
        
    @discord.ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="start_mission")
    async def start_mission(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.mission_started = True
        lang = await _get_lang(interaction.user)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        user_id = None
        try:
            user_id = await self.bot.get_cog('MissionCommands').get_user_id(interaction.user.id)
            await self.update_mission_current(user_id, 1)
        except Exception as e:
            logging.error(f"Error updating mission_current to 1: {e}")

        try:
            embed = interaction.message.embeds[0]
            embed.clear_fields()
            files = []
            rank_image_path = self.get_image_path(self.mission_card['cardrank_image'])
            if rank_image_path:
                try:
                    rank_filename = f"rank_{os.path.basename(rank_image_path)}"
                    rank_file = discord.File(rank_image_path, filename=rank_filename)
                    files.append(rank_file)
                    embed.set_thumbnail(url=f"attachment://{rank_filename}")
                except Exception as e:
                    logging.error(f"Error loading rank image: {e}")

            card_image_path = self.get_image_path(self.mission_card['image'])
            if card_image_path:
                try:
                    card_filename = f"card_{os.path.basename(card_image_path)}"
                    card_file = discord.File(card_image_path, filename=card_filename)
                    files.append(card_file)
                    embed.set_image(url=f"attachment://{card_filename}")
                except Exception as e:
                    logging.error(f"Error loading card image: {e}")
            user_team = sorted(self.user_fighter_cards, key=lambda x: x['position'])
            monster = self.mission_card.copy()

            monster['current_health'] = monster['health_stat']
            for card in user_team:
                card['current_health'] = card['health_stat']

            self.current_user_card = user_team[0]
            self.current_monster = monster

            battle_display = self.get_battle_display(user_team[0], monster, _tr(
                lang, "Battle begins!", "المعركة تبدأ!", "La bataille commence !"
            ))
            embed.add_field(name=_tr(lang, "⚔️ Mission Battle", "⚔️ معركة المهمة", "⚔️ Combat de mission"),
                            value=battle_display, inline=False)
            await interaction.message.edit(attachments=files, embed=embed, view=None)
            await asyncio.sleep(2)

            max_turns = 200
            current_turn = 0
            battle_result = None

            while monster['current_health'] > 0 and user_team and current_turn < max_turns:
                current_turn += 1
                user_card = user_team[0]
                self.current_user_card = user_card

                user_attack = random.randint(int(user_card['attack_stat'] * 0.45), int(user_card['attack_stat'] * 0.55))
                effective_user_damage = self.calculate_damage(user_attack, monster['defence_stat'])
                monster['current_health'] = max(0, monster['current_health'] - effective_user_damage)
                self.current_monster = monster

                battle_display = self.get_battle_display(
                    user_card, 
                    monster, 
                    _tr(lang,
                        f"💥 {user_card['card_name']} deals {effective_user_damage} damage to {monster['name']}!",
                        f"💥 {user_card['card_name']} يُلحق {effective_user_damage} ضررًا بـ {monster['name']}!",
                        f"💥 {user_card['card_name']} inflige {effective_user_damage} dégâts à {monster['name']} !"
                    )
                )
                embed.set_field_at(-1, name=_tr(lang, "⚔️ Mission Battle", "⚔️ معركة المهمة", "⚔️ Combat de mission"),
                                   value=battle_display, inline=False)
                await interaction.message.edit(embed=embed)
                await asyncio.sleep(3)

                if monster['current_health'] <= 0:
                    battle_result = 'win'
                    break

                monster_attack = random.randint(int(monster['min_attack']), int(monster['max_attack']))
                effective_monster_damage = self.calculate_damage(monster_attack, user_card['defence_stat'])
                user_card['current_health'] = max(0, user_card['current_health'] - effective_monster_damage)
                self.current_user_card = user_card

                battle_display = self.get_battle_display(
                    user_card, 
                    monster, 
                    _tr(lang,
                        f"💥 {monster['name']} deals {effective_monster_damage} damage to {user_card['card_name']}!",
                        f"💥 {monster['name']} يُلحق {effective_monster_damage} ضررًا بـ {user_card['card_name']}!",
                        f"💥 {monster['name']} inflige {effective_monster_damage} dégâts à {user_card['card_name']} !"
                    )
                )
                embed.set_field_at(-1, name=_tr(lang, "⚔️ Mission Battle", "⚔️ معركة المهمة", "⚔️ Combat de mission"),
                                   value=battle_display, inline=False)
                await interaction.message.edit(embed=embed)
                await asyncio.sleep(3)

                if user_card['current_health'] <= 0:
                    user_team.pop(0)
                    if not user_team:
                        battle_result = 'lose'
                        break
                    self.current_user_card = user_team[0]
                    battle_display = self.get_battle_display(
                        user_team[0], 
                        monster, 
                        _tr(lang, "Next fighter enters the battle!", "يدخل المقاتل التالي المعركة!", "Le combattant suivant entre dans la bataille !")
                    )
                    embed.set_field_at(-1, name=_tr(lang, "⚔️ Mission Battle", "⚔️ معركة المهمة", "⚔️ Combat de mission"),
                                       value=battle_display, inline=False)
                    await interaction.message.edit(embed=embed)
                    await asyncio.sleep(3)

            if battle_result == 'win':
                try:
                    reward_result,candy_result = await self.process_mission_victory(user_id)
                    if reward_result:
                        money_prize = reward_result
                        candy_prize = candy_result

                        # Build reward message dynamically based on candy availability
                        if candy_prize > 0:
                            reward_message_en = f"Congratulations! You've defeated the enemy and earned {money_prize} <:won:1304034228966658048> and {candy_prize} <:candy:1426599572012269578>!"
                            reward_message_ar = f"تهانينا! لقد هزمت العدو وربحت {money_prize} <:won:1304034228966658048> و{candy_prize} <:candy:1426599572012269578>!"
                            reward_message_fr = f"Félicitations ! Vous avez vaincu l'ennemi et gagné {money_prize} <:won:1304034228966658048> ainsi que {candy_prize} <:candy:1426599572012269578>!"
                        else:
                            reward_message_en = f"Congratulations! You've defeated the enemy and earned {money_prize} <:won:1304034228966658048>!"
                            reward_message_ar = f"تهانينا! لقد هزمت العدو وربحت {money_prize} <:won:1304034228966658048>!"
                            reward_message_fr = f"Félicitations ! Vous avez vaincu l'ennemi et gagné {money_prize} <:won:1304034228966658048>!"

                        embed.add_field(
                            name=_tr(lang, "🎉 Mission Complete!", "🎉 اكتملت المهمة!", "🎉 Mission terminée !"),
                            value=_tr(lang, reward_message_en, reward_message_ar, reward_message_fr),
                            inline=False
                        )

                        self.mission_completed = True
                    else:
                        raise Exception("Failed to process mission rewards")

                except Exception as mission_reward_error:
                    logging.error(f"Mission reward processing error: {mission_reward_error}")
                    logging.error(traceback.format_exc())
                    embed.add_field(
                        name=_tr(lang, "❌ Mission Reward Error", "❌ خطأ في مكافأة المهمة", "❌ Erreur de récompense de mission"),
                        value=_tr(lang,
                                  "An error occurred while processing mission rewards. Please contact support.",
                                  "حدث خطأ أثناء معالجة مكافآت المهمة. يرجى الاتصال بالدعم.",
                                  "Une erreur s'est produite lors du traitement des récompenses de mission. Veuillez contacter le support."),
                        inline=False
                    )
                    if user_id:
                        await self.reset_mission_state(user_id)
            else:
                embed.add_field(
                    name=_tr(lang, "❌ Mission Failed", "❌ فشلت المهمة", "❌ Échec de la mission"),
                    value=_tr(lang,
                              "Your team has been defeated or the battle dragged on too long. Better luck next time!",
                              "تم هزيمة فريقك أو طال أمد المعركة كثيرًا. حظًا أوفر في المرة القادمة!",
                              "Votre équipe a été vaincue ou le combat a trop duré. Bonne chance la prochaine fois !"),
                    inline=False
                )
                try:
                    await self.reset_mission_state(user_id)
                except Exception as e:
                    logging.error(f"Error updating mission_current after mission failure: {e}")
            
            await interaction.message.edit(embed=embed, view=None)
            self.stop()

        except Exception as overall_error:
            logging.error(f"Unexpected mission logic error: {overall_error}")
            logging.error(traceback.format_exc())
            error_embed = discord.Embed(
                title=_tr(lang, "Mission Error", "خطأ في المهمة", "Erreur de mission"),
                description=_tr(lang, "An unexpected error occurred during the mission. Please try again.",
                                "حدث خطأ غير متوقع أثناء المهمة. يرجى المحاولة مرة أخرى.",
                                "Une erreur inattendue s'est produite pendant la mission. Veuillez réessayer."),
                color=discord.Color.red()
            )
            await interaction.message.edit(embed=error_embed, view=None, attachments=[])
            await self.send_error_to_support(interaction, overall_error)

        finally:
            if user_id:
                try:
                    await self.reset_mission_state(user_id)
                    logging.info(f"Mission current reset to 0 for user {user_id} in finally block")
                except Exception as e:
                    logging.error(f"Error resetting mission_current in finally block: {e}")

    @db_transaction(max_retries=3)
    async def update_mission_current(self, user_id, status, conn=None):
        """Update mission current status with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("UPDATE user SET mission_current = %s WHERE user_id = %s", (status, user_id))

    @db_transaction(max_retries=3)
    async def process_mission_victory(self, user_id, conn=None):
        """Process mission victory rewards with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            premium_level, is_premium_active = await self.check_premium_status(user_id, cursor)
            money_prize = random.randint(self.mission_card['min_moneycap'], self.mission_card['max_moneycap'])
            candy_prize = random.randint(self.mission_card['min_candycap'], self.mission_card['max_candycap'])
            await cursor.execute("SELECT 1 FROM user_cards WHERE user_id = %s AND card_id = 107", (user_id,))
            card_107_result = await cursor.fetchall()
            if card_107_result:
                money_prize += 300
            if is_premium_active:
                money_prize = self.calculate_premium_bonus(premium_level, money_prize)
            money_prize = await self.calculate_referral_bonus(user_id, money_prize, cursor)
            await cursor.execute("UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s", (money_prize, user_id))
            await cursor.execute("UPDATE user SET candy_currency = candy_currency + %s WHERE user_id = %s", (candy_prize, user_id))
            await cursor.execute("UPDATE user SET mission_streak = mission_streak + 1 WHERE user_id = %s", (user_id,))
            await cursor.execute("UPDATE user SET mission_current = 0 WHERE user_id = %s", (user_id,))
            quest_status = await self.bot.get_cog('MissionCommands').update_quest_progress(user_id)
            if quest_status is not None:
                if quest_status == 1:
                    print("🎉 Congratulations! You've completed the 'Complete 5 missions' quest!")
                else:
                    print("📊 Your progress for the 'Complete 5 missions' quest has been updated.")
            return money_prize, candy_prize

    async def check_premium_status(self, user_id, cursor):
        """Helper function to check and update premium status"""
        try:
            await cursor.execute(
                "SELECT prenium, prenium_date, roll_cap FROM user WHERE user_id = %s",
                (user_id,)
            )
            result = await cursor.fetchone()
            if not result:
                return 0, False
            premium = result['prenium']
            premium_date = result['prenium_date']
            if premium == 0:
                return 0, False
            if premium_date:
                current_date = datetime.now()
                days_diff = (current_date - premium_date).days
                if days_diff > 30:
                    roll_cap_reduction = min(premium, 3)
                    await cursor.execute(
                        "UPDATE user SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s WHERE user_id = %s",
                        (roll_cap_reduction, user_id)
                    )
                    return 0, False
                return premium, True
            return 0, False
        except Exception as e:
            logging.error(f"Error checking premium status: {e}")
            return 0, False

    async def calculate_referral_bonus(self, user_id, base_amount, cursor):
        """Calculate referral bonus based on user's referrals"""
        try:
            await cursor.execute("SELECT user_id FROM activerefs WHERE user_id = %s", (user_id,))
            is_active_referrer = await cursor.fetchall()
            if not is_active_referrer:
                return base_amount
            await cursor.execute("SELECT COUNT(*) as total FROM referrals WHERE referrer_id = %s", (user_id,))
            refs_result = await cursor.fetchone()
            total_refs = refs_result['total'] if refs_result else 0
            perc = 0
            if total_refs <= 3:
                perc = total_refs * 5
            elif total_refs <= 9:
                additional_refs = total_refs - 3
                perc = 15 + (additional_refs * 2.5)
            else:
                perc = 30
            bonus_amount = int(base_amount * (perc / 100))
            return base_amount + bonus_amount
        except Exception as e:
            logging.error(f"Error calculating referral bonus: {e}")
            return base_amount

    def calculate_premium_bonus(self, premium_level, base_amount):
        """Calculate bonus based on premium level"""
        bonus_multipliers = {1: 1.10, 2: 1.20, 3: 1.30}
        multiplier = bonus_multipliers.get(premium_level, 1.0)
        return int(base_amount * multiplier)

    async def send_error_to_support(self, interaction, error):
        """Send error information to support channel"""
        error_message = (
            f"🚨 <@818132161534230559> Error in Mission system!\n"
            f"```\nError: {str(error)}\n"
            f"Channel: {interaction.channel.name} ({interaction.channel.id})\n"
            f"Server: {interaction.guild.name} ({interaction.guild.id})\n"
            f"Command User: {interaction.user.name} ({interaction.user.id})\n```"
        )
        user_data_message = f"User data:\n```json\n{json.dumps(self.user_fighter_cards, indent=2)[:1900]}```"
        opponent_data_message = f"Opponent data:\n```json\n{json.dumps(self.mission_card, indent=2)[:1900]}```"
        traceback_message = f"Traceback:\n```python\n{traceback.format_exc()[:1900]}```"

        try:
            support_channel = self.bot.get_channel(1336306672741908561)
            if support_channel:
                await support_channel.send(error_message)
                await support_channel.send(user_data_message)
                await support_channel.send(opponent_data_message)
                await support_channel.send(traceback_message)
                logging.info("Error message successfully sent to support channel")
            else:
                logging.error("Support channel not found")
            try:
                await interaction.followup.send(
                    _tr(await _get_lang(interaction.user),
                        "An unexpected error occurred. Our support team has been notified and will investigate the issue.",
                        "حدث خطأ غير متوقع. تم إخطار فريق الدعم لدينا وسيحقق في المشكلة.",
                        "Une erreur inattendue s'est produite. Notre équipe d'assistance a été informée et enquêtera sur le problème."
                    ),
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.channel.send(
                    f"{interaction.user.mention} " + _tr(await _get_lang(interaction.user),
                        "An unexpected error occurred. Our support team has been notified and will investigate the issue.",
                        "حدث خطأ غير متوقع. تم إخطار فريق الدعم لدينا وسيحقق في المشكلة.",
                        "Une erreur inattendue s'est produite. Notre équipe d'assistance a été informée et enquêtera sur le problème."
                    )
                )
        except Exception as send_error:
            logging.error(f"Failed to send error to support channel: {str(send_error)}")
            logging.error(f"Original error message: {error_message}")
            try:
                await interaction.followup.send(
                    _tr(await _get_lang(interaction.user),
                        "An unexpected error occurred. Please try again later.",
                        "حدث خطأ غير متوقع. يرجى المحاولة لاحقًا.",
                        "Une erreur inattendue s'est produite. Veuillez réessayer plus tard."
                    ),
                    ephemeral=True
                )
            except:
                await interaction.channel.send(
                    f"{interaction.user.mention} " + _tr(await _get_lang(interaction.user),
                        "An unexpected error occurred. Please try again later.",
                        "حدث خطأ غير متوقع. يرجى المحاولة لاحقًا.",
                        "Une erreur inattendue s'est produite. Veuillez réessayer plus tard."
                    )
                )

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.primary, custom_id="reroll_mission")
    async def reroll_mission(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        lang = await _get_lang(interaction.user)
        new_mission_card = await self.bot.get_cog('MissionCommands').get_random_mission_card(self.mission_card['missioncards_id'])
        if new_mission_card:
            self.mission_card = new_mission_card
            embed = discord.Embed(
                title=_tr(lang, f"🏰 Mission: {self.mission_card['name']}",
                          f"🏰 المهمة: {self.mission_card['name']}",
                          f"🏰 Mission : {self.mission_card['name']}"),
                description=self.bot.get_cog('MissionCommands').generate_mission_description(lang),
                color=discord.Color.blue()
            )
            files = []
            rank_image_path = self.get_image_path(self.mission_card['cardrank_image'])
            if rank_image_path:
                try:
                    rank_filename = f"rank_{os.path.basename(rank_image_path)}"
                    rank_file = discord.File(rank_image_path, filename=rank_filename)
                    files.append(rank_file)
                    embed.set_thumbnail(url=f"attachment://{rank_filename}")
                except Exception as e:
                    logging.error(f"Error loading rank image: {e}")

            card_image_path = self.get_image_path(self.mission_card['image'])
            if card_image_path:
                try:
                    card_filename = f"card_{os.path.basename(card_image_path)}"
                    card_file = discord.File(card_image_path, filename=card_filename)
                    files.append(card_file)
                    embed.set_image(url=f"attachment://{card_filename}")
                except Exception as e:
                    logging.error(f"Error loading card image: {e}")

            embed.add_field(name=_tr(lang, "📊 Enemy", "📊 العدو", "📊 Ennemi"), value=f"{self.mission_card['name']}", inline=False)
            embed.add_field(name=_tr(lang, "💰 Money Prize", "💰 الجائزة المالية", "💰 Récompense"),
                            value=f"{self.mission_card['min_moneycap']} - {self.mission_card['max_moneycap']}", inline=False)
            embed.add_field(name=_tr(lang, "📜 Your Mission", "📜 مهمتك", "📜 Votre mission"),
                            value=self.bot.get_cog('MissionCommands').generate_mission(lang), inline=False)
            embed.add_field(name=_tr(lang, "🕒 Status", "🕒 الحالة", "🕒 Statut"),
                            value=_tr(lang, "```diff\n+ Mission in progress...\n```",
                                      "```diff\n+ المهمة قيد التنفيذ...\n```",
                                      "```diff\n+ Mission en cours...\n```"), inline=False)
            embed.set_footer(text=_tr(lang, "'I alone level up.' - Sung Jin-Woo",
                                      "'أنا وحدي أرتقي.' - سونغ جين-وو",
                                      "'Je monte de niveau seul.' - Sung Jin-Woo"))

            button.disabled = True
            await interaction.message.edit(attachments=files, embed=embed, view=self)
        else:
            await interaction.followup.send(_tr(
                lang,
                "No new missions available. Please try again later.",
                "لا توجد مهام جديدة متاحة. يرجى المحاولة لاحقًا.",
                "Aucune nouvelle mission disponible. Veuillez réessayer plus tard."
            ), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel_mission")
    async def cancel_mission(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        lang = await _get_lang(interaction.user)
        try:
            user_id = await self.bot.get_cog('MissionCommands').get_user_id(interaction.user.id)
            await self.reset_mission_state(user_id)
        except Exception as e:
            logging.error(f"Error updating mission_current: {e}")

        # NEW: mark canceled, disable buttons, and stop the view (kills timeout)
        self.canceled = True
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(
                content=_tr(lang, "Mission canceled.", "تم إلغاء المهمة.", "Mission annulée."),
                attachments=[], embed=None, view=None
            )
        except Exception:
            pass
        # NEW: ensure the view stops so on_timeout won't trigger later
        self.stop()

    def create_health_bar(self, entity, is_monster=False):
        health_percentage = max(0, min(100, (entity['current_health'] / entity['health_stat']) * 100))
        filled_blocks = min(10, max(0, int(health_percentage / 10)))
        empty_blocks = 10 - filled_blocks
        if is_monster:
            filled_square = self.red_square if health_percentage > 0 else self.gray_square
        else:
            filled_square = self.green_square
        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"
        name = entity['name'] if is_monster else entity['card_name']
        return f"{name}: {health_bar} **({health_percentage:.1f}%)** ({max(0, entity['current_health'])}/{entity['health_stat']} HP)"

    def calculate_damage(self, attacker_attack, defender_defense):
        k = 100
        effective_damage = attacker_attack * (1 - (defender_defense / (defender_defense + k)))
        return round(effective_damage)

class MissionCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds

    @db_transaction(max_retries=3)
    async def check_mission_timer(self, user_id, conn=None):
        """Check mission timer with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT mission_timer FROM user WHERE user_id = %s", (user_id,))
            result = await cursor.fetchone()
            if not result:
                return False, "User not found."
            current_time = datetime.now()
            mission_timer = result['mission_timer']
            if mission_timer is None:
                await cursor.execute("UPDATE user SET mission_timer = %s WHERE user_id = %s", (current_time, user_id))
                return True, None
            time_diff = current_time - mission_timer
            cooldown_seconds = 180  # 3 minutes
            if time_diff.total_seconds() < cooldown_seconds:
                remaining_seconds = int(cooldown_seconds - time_diff.total_seconds())
                remaining_minutes = remaining_seconds // 60
                remaining_secs = remaining_seconds % 60
                lang = 0
                # We don't have ctx here; leave English or compute later in caller if needed
                if remaining_minutes > 0:
                    time_msg_en = f"{remaining_minutes} minute(s) and {remaining_secs} second(s)"
                    time_msg_ar = f"{remaining_minutes} دقيقة و {remaining_secs} ثانية"
                    time_msg_fr = f"{remaining_minutes} minute(s) et {remaining_secs} seconde(s)"
                else:
                    time_msg_en = f"{remaining_secs} second(s)"
                    time_msg_ar = f"{remaining_secs} ثانية"
                    time_msg_fr = f"{remaining_secs} seconde(s)"
                return False, _tr(lang, f"You need to wait {time_msg_en} before starting another mission.",
                                  f"تحتاج إلى الانتظار {time_msg_ar} قبل بدء مهمة أخرى.",
                                  f"Vous devez attendre {time_msg_fr} avant de commencer une autre mission.")
            await cursor.execute("UPDATE user SET mission_timer = %s WHERE user_id = %s", (current_time, user_id))
            return True, None

    @db_transaction(max_retries=3)
    async def update_quest_progress(self, user_id, conn=None):
        """Update quest progress with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            quest_query = """
            SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
            FROM user_quests uq
            JOIN quest q ON uq.quest_id = q.quest_id
            WHERE uq.user_id = %s AND q.quest_name = 'Complete 5 missions'
            """
            await cursor.execute(quest_query, (user_id,))
            result = await cursor.fetchone()
            if result:
                quest = result
                if quest['completion_status'] == 0:
                    new_count = quest['current_count'] + 1
                    update_query = """
                    UPDATE user_quests
                    SET current_count = %s, completion_status = %s
                    WHERE userquests_id = %s
                    """
                    completion_status = 1 if new_count >= quest['required_count'] else 0
                    await cursor.execute(update_query, (new_count, completion_status, quest['userquests_id']))
                    if completion_status == 1:
                        await cursor.execute("SELECT inventory_id FROM inventory WHERE user_id = %s", (user_id,))
                        inventory_result = await cursor.fetchone()
                        if inventory_result:
                            inventory_id = inventory_result['inventory_id']
                            chest_id = 1
                            await cursor.execute(
                                "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s",
                                (inventory_id, chest_id)
                            )
                            chest_result = await cursor.fetchone()
                            if chest_result:
                                await cursor.execute(
                                    "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = %s",
                                    (inventory_id, chest_id)
                                )
                            else:
                                await cursor.execute(
                                    "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, 1)",
                                    (inventory_id, chest_id)
                                )
                    return completion_status
            return None

    async def get_user_id(self, discord_id):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (str(discord_id),))
        return result[0]['user_id'] if result else None

    async def get_team_id(self, user_id):
        query = "SELECT current_team as team_id FROM user WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['team_id'] if result else None

    async def get_user_fighter_cards(self, team_id, user_id):
        query = """
        SELECT c.card_name, uc.power_stat, uc.health_stat, uc.attack_stat, uc.speed_stat, uc.defence_stat, 
               uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
               uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
               uc.fight_count, uc.id_enhancer, tc.position, uc.card_id, uc.user_id
        FROM team_cards tc
        JOIN card c ON tc.card_id = c.card_id
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        ORDER BY tc.position
        """
        return await execute_query_with_retry(query, (user_id, team_id))

    @db_transaction(max_retries=3)
    async def apply_charms(self, fighters, conn=None):
        """Apply charms to fighter cards with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            for fighter in fighters:
                charm_updates = []
                if fighter['charm_attack'] == 1:
                    charm_updates.append("attack_count = attack_count - 1")
                    fighter['attack_stat'] = round(fighter['attack_stat'] * 1.2)
                if fighter['charm_defence'] == 1:
                    charm_updates.append("defence_count = defence_count - 1")
                    fighter['defence_stat'] = round(fighter['defence_stat'] * 1.2)
                if fighter['charm_speed'] == 1:
                    charm_updates.append("speed_count = speed_count - 1")
                    fighter['speed_stat'] = round(fighter['speed_stat'] * 1.2)
                if fighter['charm_health'] == 1:
                    charm_updates.append("health_count = health_count - 1")
                    fighter['health_stat'] = round(fighter['health_stat'] * 1.2)
                if charm_updates:
                    update_query = f"""
                    UPDATE user_cards
                    SET {', '.join(charm_updates)},
                        charm_attack = CASE WHEN attack_count = 0 THEN 0 ELSE charm_attack END,
                        charm_defence = CASE WHEN defence_count = 0 THEN 0 ELSE charm_defence END,
                        charm_speed = CASE WHEN speed_count = 0 THEN 0 ELSE charm_speed END,
                        charm_health = CASE WHEN health_count = 0 THEN 0 ELSE charm_health END
                    WHERE user_id = %s AND card_id = %s
                    """
                    await cursor.execute(update_query, (fighter['user_id'], fighter['card_id']))
        return fighters

    async def get_random_mission_card(self, excluded_id=None):
        # Weighted by rank (more likely lower ranks)
        rank_weights = {'E': 40, 'D': 25, 'C': 15, 'B': 10, 'A': 5, 'S': 5}
        query = "SELECT m.*, c.cardrank_image FROM mission_cards m, card_rank c WHERE m.card_rank = c.cardrank_name"
        params = None
        if excluded_id:
            query += " AND m.missioncards_id != %s"
            params = (excluded_id,)
        mission_cards = await execute_query_with_retry(query, params)
        if not mission_cards:
            return None
        filtered_cards = [card for card in mission_cards if card['card_rank'] in rank_weights]
        weights = [rank_weights[card['card_rank']] for card in filtered_cards]
        return random.choices(filtered_cards, weights=weights, k=1)[0]

    def generate_mission(self, lang: int = 0) -> str:
        return _tr(lang,
                   "Your task is to clear the dungeon and defeat the boss. Only then will you earn the rewards hidden within.",
                   "مهمتك هي تطهير الزنزانة وهزيمة الزعيم. عندها فقط ستحصل على المكافآت المخفية بداخلها.",
                   "Votre tâche est de nettoyer le donjon et de vaincre le boss. Ce n'est qu'alors que vous gagnerez les récompenses cachées.")

    def generate_mission_description(self, lang: int = 0) -> str:
        return _tr(lang,
                   "The shadows dance around you as you step into the unknown. Every sound makes you tense, every step feels heavier than the last.",
                   "تتراقص الظلال من حولك عندما تخطو نحو المجهول. كل صوت يجعلك متوتراً، وكل خطوة أثقل من التي قبلها.",
                   "Les ombres dansent autour de vous alors que vous entrez dans l'inconnu. Chaque son vous tend, chaque pas paraît plus lourd que le précédent.")

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        full_path = os.path.join(self.image_path, image_name)
        print(f"Attempting to load image: {image_name}")
        print(f"Full image path: {full_path}")
        print(f"Image exists: {os.path.exists(full_path)}")
        return full_path if os.path.exists(full_path) else None

    @db_transaction(max_retries=3)
    async def check_and_update_mission_state(self, user_id, conn=None):
        """Check and update mission state with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT mission_current, mission_timer FROM user WHERE user_id = %s",
                (user_id,)
            )
            result = await cursor.fetchone()
            if result and result['mission_current'] == 1:
                mission_timer = result['mission_timer']
                current_time = datetime.now()
                if mission_timer and (current_time - mission_timer).total_seconds() > 600:
                    await cursor.execute("UPDATE user SET mission_current = 0 WHERE user_id = %s", (user_id,))
                    return False, "Detected and fixed a stale mission state. You can now start a new mission."
                else:
                    return True, "You are already doing a mission. Please complete your current battle before starting a new one."
            return False, None

    @commands.command(name="mission", aliases=["ms", "mis"])
    @command_lock("mission")
    async def mission(self, ctx):
        lang = await _get_lang(ctx.author)
        user_id = await self.get_user_id(ctx.author.id)
        if not user_id:
            await ctx.send(_tr(lang,
                               "You need to be registered to use this command.",
                               "تحتاج إلى التسجيل لاستخدام هذا الأمر.",
                               "Vous devez être enregistré pour utiliser cette commande."))
            return
        is_in_mission, mission_message = await self.check_and_update_mission_state(user_id)
        if is_in_mission:
            await ctx.send(_tr(lang,
                               mission_message,
                               "أنت تقوم بمهمة بالفعل. يرجى إكمال معركتك الحالية قبل بدء مهمة جديدة.",
                               "Vous êtes déjà en mission. Terminez votre combat en cours avant d'en commencer une nouvelle."))
            return
        elif mission_message:
            await ctx.send(_tr(lang,
                               mission_message,
                               "تم اكتشاف حالة مهمة قديمة وتم إصلاحها. يمكنك الآن بدء مهمة جديدة.",
                               "Un état de mission obsolète a été détecté et corrigé. Vous pouvez maintenant commencer une nouvelle mission."))
        can_start, timer_message = await self.check_mission_timer(user_id)
        if not can_start:
            await ctx.send(_tr(lang,
                               timer_message or "You need to wait a bit before starting another mission.",
                               "تحتاج إلى الانتظار قليلاً قبل بدء مهمة أخرى.",
                               "Vous devez attendre un peu avant de lancer une autre mission."))
            return
        team_id = await self.get_team_id(user_id)
        if not team_id:
            await ctx.send(_tr(lang,
                               "You need to have a team to start a mission.",
                               "تحتاج إلى وجود فريق لبدء مهمة.",
                               "Vous devez avoir une équipe pour commencer une mission."))
            return
        await self.set_mission_active(user_id)
        user_fighter_cards = await self.get_user_fighter_cards(team_id, user_id)
        if not user_fighter_cards:
            await ctx.send(_tr(lang,
                               "You need at least one fighter card in your team to start a mission.",
                               "تحتاج إلى بطاقة مقاتل واحدة على الأقل في فريقك لبدء مهمة.",
                               "Vous avez besoin d'au moins une carte combattant dans votre équipe pour commencer une mission."))
            await self.reset_mission_state(user_id)
            return
        user_fighter_cards = await self.apply_charms(user_fighter_cards)
        mission_card = await self.get_random_mission_card()
        if not mission_card:
            await ctx.send(_tr(lang,
                               "No available missions at the moment. Please try again later.",
                               "لا توجد مهام متاحة في الوقت الحالي. يرجى المحاولة لاحقًا.",
                               "Aucune mission disponible pour le moment. Veuillez réessayer plus tard."))
            await self.reset_mission_state(user_id)
            return

        embed = discord.Embed(
            title=_tr(lang, f"🏰 Mission: {mission_card['name']}",
                      f"🏰 المهمة: {mission_card['name']}",
                      f"🏰 Mission : {mission_card['name']}"),
            description=self.generate_mission_description(lang),
            color=discord.Color.blue()
        )

        files = []
        rank_image_path = self.get_image_path(mission_card['cardrank_image'])
        if rank_image_path:
            try:
                rank_filename = f"rank_{os.path.basename(rank_image_path)}"
                rank_file = discord.File(rank_image_path, filename=rank_filename)
                files.append(rank_file)
                embed.set_thumbnail(url=f"attachment://{rank_filename}")
            except Exception as e:
                logging.error(f"Error loading rank image: {e}")

        card_image_path = self.get_image_path(mission_card['image'])
        if card_image_path:
            try:
                card_filename = f"card_{os.path.basename(card_image_path)}"
                card_file = discord.File(card_image_path, filename=card_filename)
                files.append(card_file)
                embed.set_image(url=f"attachment://{card_filename}")
            except Exception as e:
                logging.error(f"Error loading card image: {e}")
        
        embed.add_field(name=_tr(lang, "📊 Enemy", "📊 العدو", "📊 Ennemi"), value=f"{mission_card['name']}", inline=False)
        embed.add_field(name=_tr(lang, "💰 Money Prize", "💰 الجائزة المالية", "💰 Récompense"),
                        value=f"<:won:1304034228966658048> {mission_card['min_moneycap']} - {mission_card['max_moneycap']}", inline=False)
        embed.add_field(name=_tr(lang, "📜 Your Mission", "📜 مهمتك", "📜 Votre mission"),
                        value=self.generate_mission(lang), inline=False)
        embed.add_field(name=_tr(lang, "🕒 Status", "🕒 الحالة", "🕒 Statut"),
                        value=_tr(lang, "```diff\n+ Mission in progress...\n```",
                                  "```diff\n+ المهمة قيد التنفيذ...\n```",
                                  "```diff\n+ Mission en cours...\n```"), inline=False)
        
        active_charms = []
        for card in user_fighter_cards:
            card_charms = []
            if card['charm_attack'] == 1:
                card_charms.append(_tr(lang, "Attack", "الهجوم", "Attaque"))
            if card['charm_defence'] == 1:
                card_charms.append(_tr(lang, "Defence", "الدفاع", "Défense"))
            if card['charm_speed'] == 1:
                card_charms.append(_tr(lang, "Speed", "السرعة", "Vitesse"))
            if card['charm_health'] == 1:
                card_charms.append(_tr(lang, "Health", "الصحة", "Santé"))
            if card_charms:
                active_charms.append(f"{card['card_name']}: {', '.join(card_charms)}")
        
        if active_charms:
            charms_text = "\n".join(active_charms)
            if len(charms_text) > 1000:
                charms_text = charms_text[:997] + "..."
            embed.add_field(name=_tr(lang, "🔮 Active Charms", "🔮 التعويذات النشطة", "🔮 Charms actifs"),
                            value=charms_text, inline=False)
        
        embed.set_footer(text=_tr(lang,
                                  "'I alone level up.' - Sung Jin-Woo",
                                  "'أنا وحدي أرتقي.' - سونغ جين-وو",
                                  "'Je monte de niveau seul.' - Sung Jin-Woo"))

        view = MissionView(self.bot, user_fighter_cards, mission_card)
        view.original_user = ctx.author
        message = await ctx.send(files=files, embed=embed, view=view)
        view.message = message
        await view.wait()

    @db_transaction(max_retries=3)
    async def set_mission_active(self, user_id, conn=None):
        """Set mission state to active with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("UPDATE user SET mission_current = 1 WHERE user_id = %s", (user_id,))

    @db_transaction(max_retries=3)
    async def reset_mission_state(self, user_id, conn=None):
        """Reset mission state with transaction safety"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("UPDATE user SET mission_current = 0 WHERE user_id = %s", (user_id,))
        
async def setup(bot):
    await bot.add_cog(MissionCommands(bot))

