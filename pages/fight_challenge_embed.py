import discord
from discord.ext import commands
import random
import asyncio
import aiohttp
import math
import os
import time
import datetime
import traceback
import json
import functools
import uuid
import aiomysql
from discord import ui
from PIL import Image, ImageDraw, ImageFont
import io
import logging
from typing import List, Dict, Any, Optional, Tuple
from utils.database import execute_query_with_retry, get_metrics, get_connection, release_connection
import math

# ---------- Multilanguage helpers (raw, no extra imports) ----------
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

def _msg_already_running(multilanguage: int) -> str:
    if multilanguage == 3:
        return "Vous exécutez déjà cette commande. Veuillez attendre qu’elle se termine."
    elif multilanguage == 1:
        return "أنت تشغِّل هذا الأمر بالفعل. الرجاء الانتظار حتى يكتمل."
    return "You're already running this command. Please wait for it to complete."

def _msg_on_cooldown(multilanguage: int, remaining: float) -> str:
    remaining = int(round(remaining))
    if multilanguage == 3:
        return f"⏰ Commande en recharge. Veuillez attendre {remaining} secondes."
    elif multilanguage == 1:
        return f"⏰ الأمر قيد الانتظار. الرجاء الانتظار {remaining} ثانية."
    return f"⏰ Command on cooldown. Please wait {remaining} seconds."

def _msg_already_in_fight(multilanguage: int) -> str:
    if multilanguage == 3:
        return "Vous êtes déjà en combat. Veuillez terminer votre bataille en cours avant d’en commencer une nouvelle."
    elif multilanguage == 1:
        return "أنت بالفعل في معركة. الرجاء إنهاء المعركة الحالية قبل بدء أخرى."
    return "You are already in a fight. Please complete your current battle before starting a new one."

def _msg_fight_wait(multilanguage: int, m: int, s: int) -> str:
    if multilanguage == 3:
        return f"Vous devez attendre {m} minutes et {s} secondes avant de pouvoir combattre à nouveau."
    elif multilanguage == 1:
        return f"عليك الانتظار {m} دقيقة و {s} ثانية قبل أن تتمكن من القتال مرة أخرى."
    return f"You need to wait {m} minutes and {s} seconds before you can fight again."
# -------------------------------------------------------------------

class CombatCalculator:
    LOG_SCALE = 1000.0      # Every 1,000 DEF = +1 log unit (slow, epic scaling)
    MAX_REDUCTION = 0.9     # Max damage reduction = 90%

    @classmethod
    def calculate_effective_damage(cls, attack_damage: int, defense_stat: int) -> int:
        """New logarithmic defense scaling system."""
        if attack_damage <= 0:
            return 1

        log_val = math.log10(1 + defense_stat / cls.LOG_SCALE)
        reduction = log_val / (1 + log_val)
        reduction = min(reduction, cls.MAX_REDUCTION)

        damage_taken = attack_damage * (1 - reduction)
        return max(1, round(damage_taken))

# Command execution lock registry to prevent duplicate command execution
class CommandLockRegistry:
    def __init__(self):
        self.locks = {}
        self.lock_creation = asyncio.Lock()
   
    async def get_lock(self, user_id, command_name):
        """Get a lock for a specific user and command combination"""
        lock_key = f"{user_id}:{command_name}"
       
        # Use a lock to prevent race conditions when creating new locks
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
            # Get user ID from context
            user_id = ctx.author.id
            multilanguage = await get_user_multilanguage(user_id)
           
            # Use function name if command_name not provided
            cmd_name = command_name or func.__name__
           
            # Get lock for this user and command
            lock = await command_locks.get_lock(user_id, cmd_name)
           
            # Try to acquire the lock with a timeout
            try:
                # Use a timeout to prevent deadlocks
                acquired = await asyncio.wait_for(lock.acquire(), timeout=5.0)
               
                # Generate a unique execution ID for logging
                exec_id = str(uuid.uuid4())[:8]
                print(f"[{exec_id}] Starting command {cmd_name} for user {user_id}")
               
                try:
                    # Execute the command
                    start_time = time.time()
                    result = await func(self, ctx, *args, **kwargs)
                    elapsed = time.time() - start_time
                    print(f"[{exec_id}] Completed command {cmd_name} for user {user_id} in {elapsed:.2f}s")
                    return result
                finally:
                    # Always release the lock
                    lock.release()
                    print(f"[{exec_id}] Released lock for command {cmd_name}, user {user_id}")
           
            except asyncio.TimeoutError:
                # If we couldn't acquire the lock in time, inform the user (translated)
                await ctx.send(_msg_already_running(multilanguage))
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
                    # Get a connection
                    conn = await get_connection()
                    if not conn:
                        if attempt == max_retries - 1:
                            raise Exception("Could not acquire database connection")
                        await asyncio.sleep(1)
                        continue
                   
                    # Begin transaction
                    await conn.begin()
                   
                    # Call the original function with the connection
                    result = await func(*args, conn=conn, **kwargs)
                   
                    # Commit the transaction
                    await conn.commit()
                    return result
                   
                except Exception as e:
                    # Rollback on error
                    if conn:
                        await conn.rollback()
                   
                    if attempt == max_retries - 1:
                        print(f"Transaction failed after {max_retries} attempts: {e}")
                        raise
                   
                    # Wait before retrying
                    await asyncio.sleep(0.5 * (2 ** attempt))  # Exponential backoff
                   
                finally:
                    # Always release the connection
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
            # Get the bucket key based on the cooldown type
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id

            multilanguage = await get_user_multilanguage(ctx.author.id)
               
            # Check if the command is on cooldown
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    # Command is on cooldown (translated)
                    remaining = round(retry_after, 1)
                    await ctx.send(_msg_on_cooldown(multilanguage, remaining))
                    return
            else:
                # Create a new cooldown bucket
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
               
            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator

# Fight Helper Functions
class FightHelpers:
    def __init__(self):
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2
    #  Added comprehensive skill system from dungeon code
    async def get_user_fighters(self, user_id: int) -> Optional[Dict]:
        """Get user data including team and fighters"""
        try:
            query = """
            SELECT u.user_id, u.user_authorid, u.current_team as team_id,
                   c.card_id, c.card_name, uc.image, c.card_rank, uc.power_stat,
                   uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat,
                   uc.current_level, tc.position, uc.current_xp, uc.id_enhancer,
                   uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed,
                   uc.charm_health, uc.attack_count, uc.defence_count, uc.speed_count,
                   uc.health_count, uc.skill_id
            FROM user u
            LEFT JOIN team_cards tc ON u.current_team = tc.team_id
            LEFT JOIN card c ON tc.card_id = c.card_id
            LEFT JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = u.user_id
            WHERE u.user_authorid = %s AND c.type_card = 'Fighter'
            ORDER BY tc.position
            """
            result = await execute_query_with_retry(query, (str(user_id),))
            if not result:
                return None
            user_data = {
                'user_id': result[0]['user_id'],
                'user_authorid': result[0]['user_authorid'],
                'team_id': result[0]['team_id'],
                'fighters': []
            }
            for row in result:
                fighter = {
                    'card_id': row['card_id'],
                    'card_name': row['card_name'],
                    'image': row['image'],
                    'card_rank': row['card_rank'],
                    'power_stat': row['power_stat'],
                    'health_stat': row['health_stat'],
                    'speed_stat': row['speed_stat'],
                    'attack_stat': row['attack_stat'],
                    'defence_stat': row['defence_stat'],
                    'current_level': row['current_level'],
                    'position': row['position'],
                    'current_xp': row['current_xp'],
                    'id_enhancer': row['id_enhancer'],
                    'fight_count': row['fight_count'],
                    'charm_attack': row['charm_attack'],
                    'charm_defence': row['charm_defence'],
                    'charm_speed': row['charm_speed'],
                    'charm_health': row['charm_health'],
                    'attack_count': row['attack_count'],
                    'defence_count': row['defence_count'],
                    'speed_count': row['speed_count'],
                    'health_count': row['health_count'],
                    'skill_id': row['skill_id']
                }
                user_data['fighters'].append(fighter)
            return user_data
        except Exception as e:
            logging.error(f"Error getting user data: {e}")
            return None

    @db_transaction(max_retries=3)
    async def get_user_data(self, user_id: int, conn=None) -> Optional[Dict]:
        """Get complete user data including team and fighters"""
        print("getting user data")
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Get user basic data
            await cursor.execute(
                "SELECT user_id, user_authorid, squad_id FROM user WHERE user_authorid = %s",
                (str(user_id),)
            )
            user_result = await cursor.fetchone()
           
            if not user_result:
                return None

            user_data = user_result
           
            # Get team data
            await cursor.execute(
                "SELECT current_team as team_id FROM user WHERE user_id = %s",
                (user_data['user_id'],)
            )
            team_result = await cursor.fetchone()
           
            if not team_result:
                return None

            user_data['team_id'] = team_result['team_id']
           
            # Get fighter cards
            fighters_query = """
            SELECT c.card_id, c.card_name, uc.image, c.card_rank, uc.power_stat,
                   uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat,
                   uc.current_level, tc.position, uc.current_xp, uc.id_enhancer,
                   uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed,
                   uc.charm_health, uc.attack_count, uc.defence_count, uc.speed_count,
                   uc.health_count, uc.skill_id
            FROM team_cards tc
            JOIN card c ON tc.card_id = c.card_id
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            WHERE tc.team_id = %s AND c.type_card = 'Fighter'
            ORDER BY tc.position
            """
            await cursor.execute(fighters_query, (user_data['user_id'], user_data['team_id']))
            user_data['fighters'] = await cursor.fetchall()
           
            return user_data

    @db_transaction(max_retries=3)
    async def check_fight_status(self, user_id: int, conn=None) -> Tuple[bool, Optional[str]]:
        """Check if user is currently in a fight"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT fight_current FROM user WHERE user_authorid = %s",
                (str(user_id),)
            )
            result = await cursor.fetchone()
            multilanguage = await get_user_multilanguage(user_id)
           
            if result and result['fight_current'] == 1:
                return True, _msg_already_in_fight(multilanguage)
           
            return False, None

    @db_transaction(max_retries=3)
    async def check_fight_timer(self, user_id: int, conn=None) -> Tuple[bool, Optional[str]]:
        """Check fight cooldown timer"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT fight_timer FROM user WHERE user_authorid = %s",
                (str(user_id),)
            )
            result = await cursor.fetchone()
            multilanguage = await get_user_multilanguage(user_id)
           
            if not result or result['fight_timer'] is None:
                # First fight - set timer
                new_timer = datetime.datetime.now()
                await cursor.execute(
                    "UPDATE user SET fight_timer = %s WHERE user_authorid = %s",
                    (new_timer, str(user_id))
                )
                return True, None
           
            fight_timer = result['fight_timer']
            time_elapsed = datetime.datetime.now() - fight_timer
           
            if time_elapsed.total_seconds() < 300:  # 5 minutes
                time_remaining = 300 - int(time_elapsed.total_seconds())
                return False, _msg_fight_wait(multilanguage, time_remaining//60, time_remaining%60)
           
            # Update timer
            new_timer = datetime.datetime.now()
            await cursor.execute(
                "UPDATE user SET fight_timer = %s WHERE user_authorid = %s",
                (new_timer, str(user_id))
            )
            return True, None

    @db_transaction(max_retries=3)
    async def set_fight_status(self, user_id: int, status: int, conn=None):
        """Set user fight status"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "UPDATE user SET fight_current = %s WHERE user_authorid = %s",
                (status, str(user_id))
            )

    @db_transaction(max_retries=3)
    async def get_random_opponents(self, user_id: int, conn=None) -> List[Dict]:
        """Get random opponents with fighter cards"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Get all users except the current one
            await cursor.execute("""
                SELECT u.user_id, u.user_authorid, t.team_id
                FROM user u
                JOIN team t ON u.user_id = t.user_id
                WHERE u.user_authorid != %s
            """, (str(user_id),))
           
            all_users = await cursor.fetchall()
            valid_opponents = []
           
            for opponent in all_users:
                # Check if opponent has fighter cards
                fighters_query = """
                SELECT c.card_id, c.card_name, uc.image, c.card_rank, uc.power_stat,
                       uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat,
                       uc.current_level, tc.position, uc.current_xp, uc.id_enhancer,
                       uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed,
                       uc.charm_health, uc.attack_count, uc.defence_count, uc.speed_count,
                       uc.health_count, uc.skill_id
                FROM team_cards tc
                JOIN card c ON tc.card_id = c.card_id
                JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                ORDER BY tc.position
                """
                await cursor.execute(fighters_query, (opponent['user_id'], opponent['team_id']))
                fighters = await cursor.fetchall()
               
                if fighters:
                    opponent['fighters'] = fighters
                    valid_opponents.append(opponent)
           
            return valid_opponents


    async def get_fight_status(self, user_id: int) -> bool:
        """Check if user is currently in a fight"""
        try:
            query = "SELECT fight_current FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(user_id),))
            if result and result[0]['fight_current'] == 1:
                return True
            return False
        except Exception as e:
            logging.error(f"Error checking fight status: {e}")
            return False

    @db_transaction(max_retries=3)
    async def apply_charm_effects(self, fighters: List[Dict], user_id: int, conn=None) -> List[Dict]:
        """Apply charm effects to fighter cards"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            for fighter in fighters:
                charm_updates = []
               
                if fighter['charm_attack'] == 1 or fighter['charm_attack'] == 9:
                    charm_updates.append("attack_count = attack_count - 1")
                    fighter['attack_stat'] = round(fighter['attack_stat'] * 1.2)

                if fighter['charm_defence'] == 1 or fighter['charm_defence'] == 11:
                    charm_updates.append("defence_count = defence_count - 1")
                    fighter['defence_stat'] = round(fighter['defence_stat'] * 1.2)

                if fighter['charm_speed'] == 1 or fighter['charm_speed'] == 10:
                    charm_updates.append("speed_count = speed_count - 1")
                    fighter['speed_stat'] = round(fighter['speed_stat'] * 1.2)

                if fighter['charm_health'] == 1 or fighter['charm_health'] == 12:
                    charm_updates.append("health_count = health_count - 1")
                    fighter['health_stat'] = round(fighter['health_stat'] * 1.2)
                if fighter['charm_attack'] == 25:
                    charm_updates.append("attack_count = attack_count - 1")
                    fighter['attack_stat'] = round(fighter['attack_stat'] * 1.35)

                if fighter['charm_defence'] == 28:
                    charm_updates.append("defence_count = defence_count - 1")
                    fighter['defence_stat'] = round(fighter['defence_stat'] * 1.35)

                if fighter['charm_speed'] == 26:
                    charm_updates.append("speed_count = speed_count - 1")
                    fighter['speed_stat'] = round(fighter['speed_stat'] * 1.35)

                if fighter['charm_health'] == 27:
                    charm_updates.append("health_count = health_count - 1")
                    fighter['health_stat'] = round(fighter['health_stat'] * 1.35)
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
                    await cursor.execute(update_query, (user_id, fighter['card_id']))

            return fighters

    @db_transaction(max_retries=3)
    async def update_fight_stats(self, user_id: int, won: bool, is_challenge: bool = False, conn=None):
        """Update user fight statistics"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            if won:
                await cursor.execute("""
                    UPDATE user
                    SET win_streak = win_streak + 1,
                        alltimewins = alltimewins + 1,
                        fight_current = 0
                    WHERE user_authorid = %s
                """, (str(user_id),))
            else:
                await cursor.execute("""
                    UPDATE user
                    SET win_streak = 0,
                        fight_current = 0
                    WHERE user_authorid = %s
                """, (str(user_id),))

    @db_transaction(max_retries=3)
    async def check_and_update_quest_progress(self, user_id: int, quest_name: str, conn=None) -> bool:
        """Check and update quest progress"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Get user's internal ID
            await cursor.execute(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(user_id),)
            )
            user_result = await cursor.fetchone()
           
            if not user_result:
                return False
           
            internal_user_id = user_result['user_id']
           
            # Check quest progress
            quest_check_query = """
            SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
            FROM user_quests uq
            JOIN quest q ON uq.quest_id = q.quest_id
            WHERE uq.user_id = %s AND q.quest_name = %s
            """
            await cursor.execute(quest_check_query, (internal_user_id, quest_name))
            quest_result = await cursor.fetchone()
           
            if quest_result and quest_result['completion_status'] == 0:
                new_count = quest_result['current_count'] + 1
               
                # Update quest progress
                await cursor.execute("""
                    UPDATE user_quests
                    SET current_count = %s
                    WHERE userquests_id = %s
                """, (new_count, quest_result['userquests_id']))
               
                # Check if quest is completed
                if new_count >= quest_result['required_count']:
                    await cursor.execute("""
                        UPDATE user_quests
                        SET completion_status = 1
                        WHERE userquests_id = %s
                    """, (quest_result['userquests_id'],))
                   
                    # Add chest reward
                    await cursor.execute(
                        "SELECT inventory_id FROM inventory WHERE user_id = %s",
                        (internal_user_id,)
                    )
                    inventory_result = await cursor.fetchone()
                   
                    if inventory_result:
                        inventory_id = inventory_result['inventory_id']
                       
                        # Check if chest exists
                        await cursor.execute("""
                            SELECT quantiter FROM inventory_chest
                            WHERE inventory_id = %s AND chest_id = 1
                        """, (inventory_id,))
                        chest_result = await cursor.fetchone()
                       
                        if chest_result:
                            await cursor.execute("""
                                UPDATE inventory_chest
                                SET quantiter = quantiter + 1
                                WHERE inventory_id = %s AND chest_id = 1
                            """, (inventory_id,))
                        else:
                            await cursor.execute("""
                                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                                VALUES (%s, 1, 1)
                            """, (inventory_id,))
                   
                    return True  # Quest completed
           
            return False  # Quest not completed

    async def get_user_id(self, user_authorid: int) -> Optional[int]:
        """Get internal user ID from Discord user ID"""
        try:
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(user_authorid),))
            return result[0]['user_id'] if result else None
        except Exception as e:
            logging.error(f"Error getting user ID: {e}")
            return None

#  Added comprehensive skill system from dungeon code
class SkillSystem:
    """Comprehensive skill system for handling all skill categories"""
   
    async def get_user_card_skills(self, user_id, card_id):
        """Get skill information for a specific user card"""
        try:
            skill_query = """
            SELECT uc.skill_id, s.skill_name, s.skill_category_id, sc.category_name
            FROM user_cards uc
            JOIN skill s ON uc.skill_id = s.skill_id
            JOIN skill_category sc ON s.skill_category_id = sc.skill_category_id
            WHERE uc.user_id = %s AND uc.card_id = %s AND uc.skill_id IS NOT NULL
            """
            result = await execute_query_with_retry(skill_query, (user_id, card_id))
            return result[0] if result else None
        except Exception as e:
            logging.error(f"Error getting user card skills: {e}")
            return None

    async def get_skill_details(self, skill_id, skill_category_id):
        """Get detailed skill information based on category"""
        try:
            skill_details = {}
           
            if skill_category_id == 1:  # Buff/Debuff
                query = """
                SELECT attack_stat, health_stat, defense_stat, speed_stat, turn
                FROM skill_buff WHERE skill_id = %s
                """
            elif skill_category_id == 2:  # Paralysis
                query = """
                SELECT turn, chance FROM skill_paralysis WHERE skill_id = %s
                """
            elif skill_category_id == 3:  # Dodge
                query = """
                SELECT turn, chance FROM skill_dodge WHERE skill_id = %s
                """
            elif skill_category_id == 4:  # Flat Damage
                query = """
                SELECT damage, turn FROM skill_flat WHERE skill_id = %s
                """
            elif skill_category_id == 5:  # Percentage Damage
                query = """
                SELECT attack_stat, health_stat, defense_stat, speed_stat, turn
                FROM skill_percent WHERE skill_id = %s
                """
            elif skill_category_id == 6:  # Poison
                query = """
                SELECT percent_damage FROM skill_poison WHERE skill_id = %s
                """
            elif skill_category_id == 7:  # Summon
                query = """
                SELECT attack_stat, health_stat, defense_stat, speed_stat,
                    attack_percentage, health_percentage, defense_percentage, speed_percentage,
                    quantity FROM skill_summon WHERE skill_id = %s
                """
            elif skill_category_id == 8:  # Shield
                query = """
                SELECT shield_amount FROM skill_shield WHERE skill_id = %s
                """
            else:
                return None
               
            result = await execute_query_with_retry(query, (skill_id,))
            return result[0] if result else None
        except Exception as e:
            logging.error(f"Error getting skill details: {e}")
            return None

    async def initialize_card_skills(self, user_id, card):
        """Initialize skill state for a card"""
        if card.get('skill_id'):
            skill_info = await self.get_user_card_skills(user_id, card['card_id'])
            if skill_info:
                skill_details = await self.get_skill_details(skill_info['skill_id'], skill_info['skill_category_id'])
                if skill_details:
                    card['skill_state'] = {
                        'skill_info': skill_info,
                        'skill_details': skill_details,
                        'turn_counter': 0,
                        'active_buffs': {},
                        'active_debuffs': {},
                        'poison_active': False,
                        'shield_amount': 0,
                        'dodge_bonus': 0,
                        'paralysis_chance': 0,
                        'summons': [],
                        'buff_applied': False,
                        'shield_applied': False
                    }

    async def apply_buff_skill(self, card, skill_details, skill_messages):
        """Apply buff/debuff effects to card stats"""
        skill_name = card['skill_state']['skill_info']['skill_name']
       
        if card['skill_state'].get('buff_applied', False):
            return
       
        # Apply stat modifications
        if skill_details['attack_stat'] != 0:
            modifier = skill_details['attack_stat']
            if modifier > 0:
                card['attack_stat'] = int(card['attack_stat'] * (1 + modifier / 100))
                skill_messages.append(f"⚡ **{skill_name}** activated! {card['card_name'][:15]}'s attack increased by {modifier}%!")
            else:
                card['attack_stat'] = int(card['attack_stat'] * (1 + modifier / 100))
                skill_messages.append(f"💀 **{skill_name}** activated! {card['card_name'][:15]}'s attack decreased by {abs(modifier)}%!")
       
        if skill_details['defense_stat'] != 0:
            modifier = skill_details['defense_stat']
            if modifier > 0:
                card['defence_stat'] = int(card['defence_stat'] * (1 + modifier / 100))
                skill_messages.append(f"🛡️ **{skill_name}** activated! {card['card_name'][:15]}'s defense increased by {modifier}%!")
            else:
                print("defense debuff")
                card['defence_stat'] = int(card['defence_stat'] * (1 + modifier / 100))
                skill_messages.append(f"💀 **{skill_name}** activated! {card['card_name'][:15]}'s defense decreased by {abs(modifier)}%!")
       
        if skill_details['speed_stat'] != 0:
            modifier = skill_details['speed_stat']
            if modifier > 0:
                card['speed_stat'] = int(card['speed_stat'] * (1 + modifier / 100))
                skill_messages.append(f"💨 **{skill_name}** activated! {card['card_name'][:15]}'s speed increased by {modifier}%!")
            else:
                card['speed_stat'] = int(card['speed_stat'] * (1 + modifier / 100))
                skill_messages.append(f"💀 **{skill_name}** activated! {card['card_name'][:15]}'s speed decreased by {abs(modifier)}%!")
       
        if skill_details['health_stat'] != 0:
            modifier = skill_details['health_stat']
            if modifier > 0:
                card['health_stat'] = int(card['health_stat'] * (1 + modifier / 100))
                card['current_health'] = card['health_stat']  # Update current health to new max
                skill_messages.append(f"❤️ **{skill_name}** activated! {card['card_name'][:15]}'s health increased by {modifier}%!")
            else:
                card['health_stat'] = int(card['health_stat'] * (1 + modifier / 100))
                card['current_health'] = min(card['current_health'], card['health_stat'])  # Cap current health
                skill_messages.append(f"💀 **{skill_name}** activated! {card['card_name'][:15]}'s health decreased by {abs(modifier)}%!")
       
        card['skill_state']['buff_applied'] = True

    async def apply_shield_skill(self, card, skill_details, skill_messages):
        """Apply shield effects to card"""
        skill_name = card['skill_state']['skill_info']['skill_name']
       
        if card['skill_state'].get('shield_applied', False):
            return
           
        # Calculate shield amount as percentage of health_stat
        shield_percentage = skill_details.get('shield_amount', 0)
        shield_amount = int((card['health_stat'] * shield_percentage) / 100)
       
        card['shield_health'] = shield_amount
        card['max_shield'] = shield_amount
       
        skill_messages.append(f"🛡️ **{skill_name}** activated! {card['card_name'][:15]} gains {shield_amount} shield!")
        card['skill_state']['shield_applied'] = True

    async def handle_defensive_skills(self, defender, attacker, round_log):
        """Handle defensive skills like dodge and paralysis"""
        skill_messages = []
        dodge_success = False
        paralysis_success = False
   
        if 'skill_state' not in defender:
            return skill_messages, dodge_success, paralysis_success
   
        if 'skill_info' not in defender['skill_state']:
            return skill_messages, dodge_success, paralysis_success
           
        skill_info = defender['skill_state']['skill_info']
        skill_details = defender['skill_state']['skill_details']
        skill_name = skill_info['skill_name']
   
        # Handle Dodge skill (category 3)
        if skill_info['skill_category_id'] == 3:
            dodge_chance = skill_details['chance']
            if random.random() * 100 < dodge_chance:
                skill_messages.append(f"🌪️ **{skill_name}** activates! {defender['card_name'][:15]} dodges with {dodge_chance}% chance!")
                dodge_success = True
   
        # Handle Paralysis skill (category 2)
        if skill_info['skill_category_id'] == 2:
            paralysis_chance = skill_details['chance']
            if random.random() * 100 < paralysis_chance:
                skill_messages.append(f"⚡ **{skill_name}** activates! {attacker['card_name'][:15]} is paralyzed ({paralysis_chance}% chance)!")
                paralysis_success = True
   
        return skill_messages, dodge_success, paralysis_success

    async def handle_turn_based_skills(self, card, is_user_turn, round_log):
        """Handle skills that activate based on turn counters"""
        skill_messages = []
        bonus_damage = 0  # Always initialize bonus_damage to ensure we return 2 values
        print("handle_skill")
       
        # <CHANGE> Add safety check for skill_state existence
        if 'skill_state' not in card:
            print("issues")
            return skill_messages, bonus_damage  # Always return 2 values
   
        # <CHANGE> Add safety check for skill_info key to prevent KeyError
        if 'skill_info' not in card['skill_state']:
            print("not in card")
            return skill_messages, bonus_damage
           
        skill_info = card['skill_state']['skill_info']
        skill_details = card['skill_state']['skill_details']
        skill_name = skill_info['skill_name']
        skill_category = skill_info['skill_category_id']
        print(skill_info)
        print(skill_category)
       
        if skill_category not in [1, 4, 5]:
            return skill_messages, bonus_damage
   
        if 'turn' not in skill_details:
            return skill_messages, bonus_damage
   
        # Increment turn counter
        card['skill_state']['turn_counter'] += 1
        current_turn = card['skill_state']['turn_counter']
   
        # Handle different skill types
        if skill_category == 1:  # Buff/Debuff skills
            print("got in")
            await self.apply_buff_skill(card, skill_details, skill_messages)
            card['skill_state']['turn_counter'] = 0  # Reset counter
   
        elif skill_category == 4:  # Flat Damage
            if current_turn >= skill_details['turn']:
                bonus_damage = skill_details['damage']
                skill_messages.append(f"⚔️ **{skill_name}** triggers! Additional {bonus_damage} flat damage!")
                card['skill_state']['turn_counter'] = 0  # Reset counter
   
        elif skill_category == 5:  # Percentage Damage
            if current_turn >= skill_details['turn']:
                # Apply percentage-based attack
                base_attack = card['attack_stat']
                if skill_details['attack_stat'] != 0:
                    bonus_damage = int(base_attack * (skill_details['attack_stat'] / 100))
                    skill_messages.append(f"💥 **{skill_name}** triggers! {skill_details['attack_stat']}% attack bonus ({bonus_damage} damage)!")
                    card['skill_state']['turn_counter'] = 0  # Reset counter
   
        return skill_messages, bonus_damage  # Always return exactly 2 values

    async def handle_poison_skill(self, target_card, poison_skill_details):
        """Poison DoT that NEVER affects shields. Poison starts only after shield breaks."""
        skill_messages = []

        # No active poison? nothing to do
        if not target_card.get('skill_state', {}).get('poison_active', False):
            return skill_messages

        # If shield still up: do NOT damage and do NOT grow stacks yet
        if target_card.get('shield_health', 0) > 0:
            return skill_messages

        # Shield is gone → apply poison to HP
        current_pct = target_card['skill_state'].get('current_poison_percent', 2.0)
        max_pct = poison_skill_details['percent_damage']
        poison_damage = int(target_card['health_stat'] * (current_pct / 100))

        target_card['current_health'] = max(0, target_card['current_health'] - poison_damage)
        skill_messages.append(
            f"☠️ **Poison** deals {poison_damage} damage ({int(current_pct)}%) to {target_card['card_name'][:15]}!"
        )

        # Increase stack AFTER the tick, but cap to max
        if current_pct < max_pct:
            target_card['skill_state']['current_poison_percent'] = min(current_pct + 2.0, max_pct)

        return skill_messages


    async def handle_summon_skills(self, card, user_id, round_log):
        """Handle summon skill activation"""
        skill_messages = []
       
        if 'skill_state' not in card:
            return skill_messages, []
       
        skill_info = card['skill_state']['skill_info']
        skill_details = card['skill_state']['skill_details']
        skill_name = skill_info['skill_name']
       
        # Handle Summon skill (category 7)
        if skill_info['skill_category_id'] == 7:
            if not card['skill_state'].get('summons_created', False):
                summons = []
                quantity = skill_details['quantity']
               
                for i in range(quantity):
                    summon = {
                        'card_name': f"Summon {i+1}",
                        'card_id': f"summon_{card['card_id']}_{i}",
                        'is_summon': True,
                        'parent_card_id': card['card_id'],
                        'image': card.get('image', '')
                    }
                   
                    # Use health_percentage vs health_stat logic
                    if skill_details.get('health_percentage', 0) > 0:
                        summon['health_stat'] = int(card['health_stat'] * (skill_details['health_percentage'] / 100))
                    else:
                        summon['health_stat'] = skill_details.get('health_stat', 100)
                   
                    if skill_details.get('attack_percentage', 0) > 0:
                        summon['attack_stat'] = int(card['attack_stat'] * (skill_details['attack_percentage'] / 100))
                    else:
                        summon['attack_stat'] = skill_details.get('attack_stat', 50)
                   
                    if skill_details.get('defense_percentage', 0) > 0:
                        summon['defence_stat'] = int(card['defence_stat'] * (skill_details['defense_percentage'] / 100))
                    else:
                        summon['defence_stat'] = skill_details.get('defense_stat', 30)
                   
                    if skill_details.get('speed_percentage', 0) > 0:
                        summon['speed_stat'] = int(card['speed_stat'] * (skill_details['speed_percentage'] / 100))
                    else:
                        summon['speed_stat'] = skill_details.get('speed_stat', 40)
                    summon['power_stat'] = summon['attack_stat'] + summon['speed_stat'] +summon['defence_stat'] +summon['health_stat']
                    summon['current_health'] = summon['health_stat']
                    summon['current_level'] = card.get('current_level', 1)
                    summon['card_rank'] = card.get('card_rank', 1)
                    summons.append(summon)
               
                card['skill_state']['summons'] = summons
                card['skill_state']['summons_created'] = True
                skill_messages.append(f"🌟 **{skill_name}** activated! {card['card_name'][:15]} summons {quantity} ally/allies!")
       
        return skill_messages, card['skill_state'].get('summons', [])

    async def initialize_fight_skills(self, user_id, team):
        """Initialize skills for all cards in a team at fight start"""
        skill_messages = []
       
        for card in team:
            await self.initialize_card_skills(user_id, card)
           
            # Apply immediate skills (buffs/shields with turn = 0)
            if 'skill_state' in card:
                skill_info = card['skill_state']['skill_info']
                skill_details = card['skill_state']['skill_details']
               
                # Handle immediate buff skills
                if skill_info['skill_category_id'] == 1 and skill_details.get('turn', 0) == 0:
                    await self.apply_buff_skill(card, skill_details, skill_messages)
               
                # Handle immediate shield skills
                elif skill_info['skill_category_id'] == 8 and skill_details.get('turn', 0) == 0:
                    await self.apply_shield_skill(card, skill_details, skill_messages)
       
        return skill_messages

# Battle Mechanics with Skill Integration
def _tr(multilanguage: int, en: str, ar: str, fr: str) -> str:
    """Return the string in the chosen language. 0=EN, 1=AR, 3=FR."""
    if multilanguage == 1:
        return ar
    if multilanguage == 3:
        return fr
    return en

class BattleMechanics:
    def __init__(self):
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"
        self.skill_system = SkillSystem()

    def calculate_effective_damage(self, attack_damage: int, defense_stat: int) -> int:
        """Calculate effective damage using your original formula with minimum damage of 1"""
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        # Ensure minimum damage of 1
        return max(1, round(effective_damage))

    def calculate_dodge_chance(self, attacker_speed: int, defender_speed: int) -> float:
        """Calculate dodge chance based on speed difference"""
        attacker_speed = max(1, attacker_speed)
        defender_speed = max(1, defender_speed)
        speed_ratio = defender_speed / attacker_speed
        dodge_chance = min(30, max(0, 30 * (1 - math.log(speed_ratio + 1, 2))))
        return dodge_chance
    def calculate_poison_chance(self, attacker_power, target_power):
        """Calculate the chance of poison being applied based on power difference"""
        # If attacker is stronger or equal, 100% chance
        if attacker_power >= target_power:
            return 100
        
        # Calculate power difference (only when attacker is weaker)
        power_diff = target_power - attacker_power
        
        # Base chance is 100%
        chance = 100
        
        # For differences up to 100k: -5% per 10k
        if power_diff <= 100000:
            chance -= (power_diff // 10000) * 5
        else:
            # At 100k: -50%
            chance = 50
            
            # Beyond 100k, calculate additional reduction
            excess_diff = power_diff - 100000
            
            if excess_diff <= 25000:  # 100k-125k
                chance = 45
            elif excess_diff <= 50000:  # 125k-150k
                chance = 40
            elif excess_diff <= 75000:  # 150k-175k
                chance = 35
            else:  # 175k-200k+
                chance = 30  # Minimum 30% chance
        
        return max(30, chance)  # Ensure minimum 30% chance
    async def handle_poison_skill(self, target_card, poison_skill_details):
        """Poison DoT that NEVER affects shields. Poison starts only after shield breaks."""
        skill_messages = []

        # No active poison? nothing to do
        if not target_card.get('skill_state', {}).get('poison_active', False):
            return skill_messages

        # If shield still up: do NOT damage and do NOT grow stacks yet
        if target_card.get('shield_health', 0) > 0:
            return skill_messages

        # Shield is gone → apply poison to HP
        current_pct = target_card['skill_state'].get('current_poison_percent', 2.0)
        max_pct = poison_skill_details['percent_damage']
        poison_damage = int(target_card['health_stat'] * (current_pct / 100))

        target_card['current_health'] = max(0, target_card['current_health'] - poison_damage)
        skill_messages.append(
            f"☠️ **Poison** deals {poison_damage} damage ({int(current_pct)}%) to {target_card['card_name'][:15]}!"
        )

        # Increase stack AFTER the tick, but cap to max
        if current_pct < max_pct:
            target_card['skill_state']['current_poison_percent'] = min(current_pct + 2.0, max_pct)

        return skill_messages
#  Enhanced perform_attack with comprehensive skill integration + multilanguage
    async def perform_attack(
        self,
        attacker: Dict,
        defender: Dict,
        is_user_turn: bool,
        multilanguage: int = 0
    ) -> Tuple[List[str], bool]:
        """Perform an attack with full skill integration (localized messages)
        — Poison never bypasses shields and only starts/ticks after shield breaks.
        """
        battle_log = []

        # Defensive skills (dodge/paralysis) first
        if 'skill_state' in defender:
            skill_messages, skill_dodge_success, paralysis_success = await self.skill_system.handle_defensive_skills(defender, attacker, [])
            battle_log.extend(skill_messages)

            if paralysis_success:
                battle_log.append(_tr(
                    multilanguage,
                    f"⚡ {attacker['card_name'][:15]} is paralyzed and cannot attack!",
                    f"⚡ {attacker['card_name'][:15]} مشلول ولا يستطيع الهجوم!",
                    f"⚡ {attacker['card_name'][:15]} est paralysé et ne peut pas attaquer !"
                ))
                return battle_log, False

            if skill_dodge_success:
                return battle_log, False

        # Attacker turn-based skills
        bonus_damage = 0
        if 'skill_state' in attacker:
            skill_messages, bonus_damage = await self.skill_system.handle_turn_based_skills(attacker, is_user_turn, [])
            battle_log.extend(skill_messages)

            # One-time shield application (category 8)
            if ('skill_info' in attacker.get('skill_state', {}) and
                attacker['skill_state']['skill_info']['skill_category_id'] == 8 and
                not attacker['skill_state'].get('shield_applied', False)):
                shield_messages = []
                await self.skill_system.apply_shield_skill(attacker, attacker['skill_state']['skill_details'], shield_messages)
                battle_log.extend(shield_messages)

        # Normal dodge chance
        dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
        if random.random() * 100 < dodge_chance:
            battle_log.append(_tr(
                multilanguage,
                f"🛡️ {defender['card_name']} dodged the attack!",
                f"🛡️ {defender['card_name']} تفادى الهجوم!",
                f"🛡️ {defender['card_name']} a esquivé l’attaque !"
            ))
            return battle_log, False

        # Damage roll
        base_damage = random.randint(
            int(attacker['attack_stat'] * 0.45),
            int(attacker['attack_stat'] * 0.55)
        )
        base_damage += bonus_damage

        # Critical
        crit_chance = min(10, max(5, attacker['attack_stat'] / 100))
        is_critical = random.random() * 100 < crit_chance
        crit_multiplier = 1.5 if is_critical else 1.0

        effective_damage = self.calculate_effective_damage(
            int(base_damage * crit_multiplier),
            defender['defence_stat']
        )

        # Apply to shield/HP
        shield_message = ""
        actual_hp_damage = 0
        shield_broke_this_hit = False  # <-- track shield break

        if defender.get('shield_health', 0) > 0:
            shield_damage = min(effective_damage, defender['shield_health'])
            defender['shield_health'] -= shield_damage
            remaining_damage = effective_damage - shield_damage

            if defender['shield_health'] <= 0:
                shield_broke_this_hit = True
                defender['current_health'] = max(0, defender['current_health'] - remaining_damage)
                actual_hp_damage = remaining_damage
                shield_message = _tr(
                    multilanguage,
                    f"🛡️ Shield broken! ({shield_damage} shield + {remaining_damage} HP damage)",
                    f"🛡️ الدرع تحطّم! ({shield_damage} درع + {remaining_damage} ضرر نقاط صحة)",
                    f"🛡️ Bouclier brisé ! ({shield_damage} de bouclier + {remaining_damage} PV de dégâts)"
                )
            else:
                actual_hp_damage = 0
                shield_message = _tr(
                    multilanguage,
                    f"🛡️ Shield absorbed {shield_damage} damage!",
                    f"🛡️ الدرع امتصّ {shield_damage} ضررًا!",
                    f"🛡️ Le bouclier a absorbé {shield_damage} dégâts !"
                )
        else:
            defender['current_health'] = max(0, defender['current_health'] - effective_damage)
            actual_hp_damage = effective_damage

        # Build attack message
        emoji = "⚔️" if is_user_turn else "🛡️"
        attacker_name = attacker['card_name'][:20] + '...' if len(attacker['card_name']) > 20 else attacker['card_name']

        if is_critical:
            message = _tr(
                multilanguage,
                f"💥 **CRITICAL HIT!** {emoji} {attacker_name} → {effective_damage} MASSIVE damage (Base: {base_damage})",
                f"💥 **ضربة حرِجة!** {emoji} {attacker_name} → {effective_damage} ضرر هائل (الأساس: {base_damage})",
                f"💥 **COUP CRITIQUE !** {emoji} {attacker_name} → {effective_damage} dégâts MASSIFS (Base : {base_damage})"
            )
        else:
            message = _tr(
                multilanguage,
                f"{emoji} {attacker_name} → {effective_damage} damage (Base: {base_damage})",
                f"{emoji} {attacker_name} → {effective_damage} ضرر (الأساس: {base_damage})",
                f"{emoji} {attacker_name} → {effective_damage} dégâts (Base : {base_damage})"
            )

        if shield_message:
            message += f" | {shield_message}"

        battle_log.append(message)

        # === Poison application/tick (category_id == 6) ===
        if ('skill_state' in attacker and
            'skill_info' in attacker['skill_state'] and
            attacker['skill_state']['skill_info']['skill_category_id'] == 6):

            defender_shield_up = defender.get('shield_health', 0) > 0

            # If shield is up and wasn't broken this hit → do NOT start poison or tick it
            if defender_shield_up and not shield_broke_this_hit:
                pass
            else:
                # <CHANGE> Calculate poison application chance based on power difference
                poison_chance = self.calculate_poison_chance(attacker['power_stat'], defender['power_stat'])
                poison_roll = random.randint(1, 100)
                
                if poison_roll <= poison_chance:
                    # Poison application successful
                    # Shield just broke OR was already absent → ensure poison is active
                    if 'skill_state' not in defender:
                        defender['skill_state'] = {}

                    if not defender['skill_state'].get('poison_active', False):
                        defender['skill_state']['poison_active'] = True
                        defender['skill_state']['current_poison_percent'] = 2.0

                    # Tick poison now (handle_poison_skill itself will ignore if any shield remains)
                    poison_messages = await self.handle_poison_skill(
                        defender,
                        attacker['skill_state']['skill_details']
                    )
                    battle_log.extend(poison_messages)
                else:
                    # Poison application failed
                    battle_log.append(
                        f"☠️ **Poison failed to apply!** ({poison_chance}% chance, rolled {poison_roll})"
                    )

        return battle_log, True



    def create_health_bar(self, percentage: float, is_user: bool, is_challenge: bool = False) -> str:
        """Create a health bar visualization"""
        filled_blocks = min(10, max(0, int(percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_challenge:
            filled_square = self.green_square
        elif is_user:
            filled_square = self.green_square
        else:
            filled_square = self.red_square

        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"
        return f"{health_bar} **({percentage:.1f}%)**"

    def create_card_info(self, card: Dict, is_user: bool, name: str, is_challenge: bool = False, multilanguage: int = 0) -> str:
        """Create formatted card information with skill info (localized labels)"""
        health_percentage = (card['current_health'] / card['health_stat']) * 100
        health_bar = self.create_health_bar(health_percentage, is_user, is_challenge)

        # Localized labels
        hp_label   = _tr(multilanguage, "HP", "نقاط الصحة", "PV")
        pow_label  = _tr(multilanguage, "POW", "القوة", "PUISS")
        atk_label  = _tr(multilanguage, "ATK", "الهجوم", "ATQ")
        def_label  = _tr(multilanguage, "DEF", "الدفاع", "DÉF")
        spd_label  = _tr(multilanguage, "SPD", "السرعة", "VIT")
        shield_lbl = _tr(multilanguage, "Shield", "الدرع", "Bouclier")
        skill_lbl  = _tr(multilanguage, "Skill", "المهارة", "Compétence")

        card_info = (
            f"**{card['card_name']}** (Lv.{card['current_level']})\n"
            f"{health_bar} {card['current_health']}/{card['health_stat']} {hp_label}\n"
            f"🔸 **{pow_label}**: {card['power_stat']}\n"
            f"🔸 **{atk_label}**: {card['attack_stat']}\n"
            f"🔸 **{def_label}**: {card['defence_stat']}\n"
            f"🔸 **{spd_label}**: {card['speed_stat']}\n"
        )

        # Add shield info if present
        if 'shield_health' in card and card['shield_health'] > 0:
            card_info += f"🛡️ **{shield_lbl}**: {card['shield_health']}\n"

        # Add skill info if present
        if 'skill_state' in card and 'skill_info' in card['skill_state']:
            skill_name = card['skill_state']['skill_info']['skill_name']
            card_info += f"✨ **{skill_lbl}**: {skill_name}\n"

        return card_info

    def get_defeat_message(self, defeated_name: str, is_user_turn: bool, multilanguage: int = 0) -> str:
        """Get defeat message (localized)"""
        emoji = "🏆" if is_user_turn else "💀"
        return _tr(
            multilanguage,
            f"{emoji} **{defeated_name} has been defeated!**",
            f"{emoji} **{defeated_name} تمّت هزيمته!**",
            f"{emoji} **{defeated_name} a été vaincu !**"
        )

    def get_new_card_message(self, new_card_name: str, is_user_turn: bool, multilanguage: int = 0) -> str:
        """Get new card entering message (localized)"""
        emoji = "🔄" if is_user_turn else "🆕"
        who_en = "user" if is_user_turn else "opponent"
        who_ar = "المستخدم" if is_user_turn else "الخصم"
        who_fr = "utilisateur" if is_user_turn else "adversaire"
        return _tr(
            multilanguage,
            f"{emoji} New {who_en} card: {new_card_name}",
            f"{emoji} بطاقة {who_ar} الجديدة: {new_card_name}",
            f"{emoji} Nouvelle carte {who_fr} : {new_card_name}"
        )

# XP Rewards System
class XPRewards:
    def __init__(self):
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.gray_square = "<:gray:1295036907595759697>"

    @db_transaction(max_retries=3)
    async def handle_xp_rewards(self, user_id: int, team_id: int, fighter_card_ids: List[int], conn=None) -> List[Dict]:
        """Handle XP and affinity rewards for battle participants"""
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Get user's internal ID
            await cursor.execute("SELECT user_id FROM user WHERE user_authorid = %s", (str(user_id),))
            user_result = await cursor.fetchone()
            if not user_result:
                return []

            internal_user_id = user_result['user_id']

            # Get battle cards
            placeholders = ', '.join(['%s'] * len(fighter_card_ids))
            cards_query = f"""
            SELECT c.card_id, c.card_name, c.card_icon, c.card_rank, c.xp_cap, uc.max_leveled,
                uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat,
                uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                uc.id_enhancer, uc.fight_count, uc.weapon_id, uc.armor_id, uc.shield_id,
                uc.affinity, uc.evo
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            WHERE uc.user_id = %s AND uc.card_id IN ({placeholders}) AND c.type_card = 'Fighter'
            """
            params = [internal_user_id] + fighter_card_ids
            await cursor.execute(cards_query, params)
            battle_cards = await cursor.fetchall()
            if not battle_cards:
                return []

            # Get affinity caps
            affinity_caps = {}
            for card in battle_cards:
                if card['evo'] in [1, 2]:
                    cap_query = """
                    SELECT affinity_cap
                    FROM card_upgrades
                    WHERE card_id = %s AND evo = %s
                    """
                    await cursor.execute(cap_query, (card['card_id'], card['evo']))
                    cap_result = await cursor.fetchone()
                    affinity_caps[card['card_id']] = cap_result['affinity_cap'] if cap_result else 25
                elif card['evo'] == 3:
                    affinity_caps[card['card_id']] = 100  # fixed cap

            xp_rewards = []

            for card in battle_cards:
                at_max_level = card['current_level'] == card['max_leveled']

                # --- XP Reward ---
                xp_reward = 0 if at_max_level else random.randint(20, 100)
                enhancer_active = False

                # --- Affinity Reward ---
                current_affinity = round(card['affinity'], 2)
                if card['evo'] == 1:
                    affinity_reward = round(random.uniform(0.05, 0.07), 2)
                elif card['evo'] == 2:
                    affinity_reward = round(random.uniform(0.02, 0.05), 2)
                elif card['evo'] == 3:
                    affinity_reward = round(random.uniform(0.01, 0.03), 2)
                else:
                    affinity_reward = 0

                # --- Enhancer check ---
                if card.get('id_enhancer'):
                    new_fight_count = card['fight_count'] - 1
                    if not at_max_level:
                        if card.get('id_enhancer') == 17:
                            xp_reward = int(xp_reward * 1.2)  # 20% XP boost
                        else:
                            print("hallow enh")
                            xp_reward = int(xp_reward * 1.35)  # 20% bonus XP only if XP matters
                    affinity_reward = round(affinity_reward * 1.5, 2)  # Always boost affinity
                    enhancer_active = True

                    if new_fight_count <= 0:
                        await cursor.execute(
                            "UPDATE user_cards SET fight_count = 0, id_enhancer = NULL WHERE user_id = %s AND card_id = %s",
                            (internal_user_id, card['card_id'])
                        )
                    else:
                        await cursor.execute(
                            "UPDATE user_cards SET fight_count = %s WHERE user_id = %s AND card_id = %s",
                            (new_fight_count, internal_user_id, card['card_id'])
                        )

                # --- Affinity calc ---
                affinity_cap = affinity_caps.get(card['card_id'], 25)
                affinity_maxed = False
                if current_affinity >= affinity_cap:
                    affinity_reward = 0
                    new_affinity = current_affinity
                    affinity_maxed = True
                elif current_affinity + affinity_reward >= affinity_cap:
                    affinity_reward = round(affinity_cap - current_affinity, 2)
                    new_affinity = affinity_cap
                    affinity_maxed = True
                else:
                    new_affinity = round(current_affinity + affinity_reward, 2)

                # --- Level & XP ---
                new_xp = card['current_xp'] + xp_reward
                new_level = card['current_level']
                level_up = False
                if not at_max_level and new_xp >= card['xp_cap']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True

                # --- Stat increases if level up ---
                stat_increases = {
                    'attack': random.randint(2, 4) if level_up else 0,
                    'health': random.randint(3, 5) if level_up else 0,
                    'defence': random.randint(1, 2) if level_up else 0,
                    'speed': random.randint(2, 4) if level_up else 0
                }

                # --- DB update ---
                if at_max_level:
                    # Only affinity updates at max level
                    await cursor.execute(
                        "UPDATE user_cards SET affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_affinity, internal_user_id, card['card_id'])
                    )
                elif level_up:
                    new_base_stats = {
                        'attack': card['base_attack'] + stat_increases['attack'],
                        'health': card['base_health'] + stat_increases['health'],
                        'defence': card['base_defence'] + stat_increases['defence'],
                        'speed': card['base_speed'] + stat_increases['speed']
                    }
                    new_stats = {
                        'attack': card['attack_stat'] + stat_increases['attack'],
                        'health': card['health_stat'] + stat_increases['health'],
                        'defence': card['defence_stat'] + stat_increases['defence'],
                        'speed': card['speed_stat'] + stat_increases['speed']
                    }
                    update_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        power_stat = attack_stat+health_stat+defence_stat+speed_stat,
                        current_statpoints = current_statpoints + 2,
                        affinity = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await cursor.execute(
                        update_query,
                        (new_level, new_xp,
                        new_base_stats['attack'], new_base_stats['health'],
                        new_base_stats['defence'], new_base_stats['speed'],
                        new_stats['attack'], new_stats['health'],
                        new_stats['defence'], new_stats['speed'],
                        new_affinity, internal_user_id, card['card_id'])
                    )
                else:
                    await cursor.execute(
                        "UPDATE user_cards SET current_xp = %s, affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, new_affinity, internal_user_id, card['card_id'])
                    )

                # --- Collect result ---
                xp_rewards.append({
                    'card_name': card['card_name'],
                    'card_icon': card['card_icon'],
                    'xp_gained': xp_reward,
                    'enhancer_active': enhancer_active,
                    'new_level': new_level,
                    'old_level': card['current_level'],
                    'new_xp': new_xp,
                    'xp_cap': card['xp_cap'],
                    'level_up': level_up,
                    'stat_increases': stat_increases,
                    'max_level_reached': at_max_level or (new_level == card['max_leveled']),
                    'affinity_gained': affinity_reward,
                    'new_affinity': new_affinity,
                    'old_affinity': current_affinity,
                    'affinity_maxed': affinity_maxed,
                    'affinity_cap': affinity_cap
                })

            return xp_rewards

    def create_xp_progress_bar(self, percentage: float, length: int = 10) -> str:
        """Create XP progress bar"""
        filled = int((percentage / 100.0) * length)
        empty = length - filled
        bar = (self.green_square * filled) + (self.gray_square * empty)
        return f"{bar} **{percentage:.1f}%**"


class EmFightCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_id = 818132161534230559
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.image_size = (300, 350)
       
        # Initialize helper classes
        self.fight_helpers = FightHelpers()
        self.battle_mechanics = BattleMechanics()
        self.xp_rewards = XPRewards()

    def get_rank_color(self, cardrank_id):
        """Get color for card rank border"""
        rank_colors = {
            1: (192, 192, 192),  # Silver
            2: (255, 215, 0),    # Gold
            3: (139, 0, 0),      # Bronze
            4: (255, 165, 0),    # Orange
            5: (32, 131, 39),    # Green
            6: (0, 128, 128),    # Teal
            7: (128, 128, 128),  # Gray
            8: (255, 255, 255)   # White
        }
        return rank_colors.get(cardrank_id, (255, 255, 255))

    async def fetch_user_with_retry(self, user_id: str, max_retries: int = 3) -> Optional[discord.User]:
        """Fetch user with retry logic"""
        for attempt in range(max_retries):
            try:
                user = await self.bot.fetch_user(int(user_id))
                return user
            except discord.NotFound:
                return None
            except (discord.HTTPException, discord.Forbidden) as e:
                if attempt == max_retries - 1:
                    logging.error(f"Failed to fetch user {user_id} after {max_retries} attempts: {e}")
                    return None
                await asyncio.sleep(1)
        return None

    async def create_fight_embedd(self, user_card: Dict, opponent_card: Dict, user_name: str, opponent_name: str, is_challenge: bool = False, multilanguage: int = 0) -> Tuple[discord.Embed, discord.File]:
        """Create fight embed with card information"""
        title = _tr(multilanguage, "🥊 Challenge Fight" if is_challenge else "⚔️ PvP Fight",
                    "🥊 قتال التحدي" if is_challenge else "⚔️ قتال PvP",
                    "🥊 Combat de défi" if is_challenge else "⚔️ Combat JcJ")
        embed = discord.Embed(title=title, color=discord.Color.red())
       
        # User card info
        user_info = self.battle_mechanics.create_card_info(user_card, True, user_name, is_challenge, multilanguage=multilanguage)
        embed.add_field(name=_tr(multilanguage, f"👤 {user_name}", f"👤 {user_name}", f"👤 {user_name}"), value=user_info, inline=True)
       
        # Opponent card info
        opponent_info = self.battle_mechanics.create_card_info(opponent_card, False, opponent_name, is_challenge, multilanguage=multilanguage)
        embed.add_field(name=_tr(multilanguage, f"🤖 {opponent_name}", f"🤖 {opponent_name}", f"🤖 {opponent_name}"), value=opponent_info, inline=True)
       
        # Create fight image
        file = await self.create_fight_image(user_card, opponent_card)
        embed.set_image(url="attachment://fight.png")
       
        return embed, file

    async def update_fight_embed(self, embed: discord.Embed, user_card: Dict, opponent_card: Dict, user_name: str, opponent_name: str, battle_log: List[str], multilanguage: int = 0) -> discord.Embed:
        """Update fight embed with current battle state"""
        embed.clear_fields()
       
        # User card info
        user_info = self.battle_mechanics.create_card_info(user_card, True, user_name, multilanguage=multilanguage)
        embed.add_field(name=_tr(multilanguage, f"👤 {user_name}", f"👤 {user_name}", f"👤 {user_name}"), value=user_info, inline=True)
       
        # Opponent card info
        opponent_info = self.battle_mechanics.create_card_info(opponent_card, False, opponent_name, multilanguage=multilanguage)
        embed.add_field(name=_tr(multilanguage, f"🤖 {opponent_name}", f"🤖 {opponent_name}", f"🤖 {opponent_name}"), value=opponent_info, inline=True)
       
        # Battle log (last 5 messages)
        if battle_log:
            log_title = _tr(multilanguage, "📜 Battle Log", "📜 سجل المعركة", "📜 Journal de combat")
            log_text = "\n".join(battle_log[-5:])
            embed.add_field(name=log_title, value=log_text, inline=False)
       
        return embed

    async def create_fight_image(self, user_card: Dict, opponent_card: Dict) -> discord.File:
        """Create fight image with both cards"""
        try:
            # Create base image
            img = Image.new('RGB', (600, 350), color=(50, 50, 50))
           
            # Load and resize card images
            async with aiohttp.ClientSession() as session:
                # User card
                try:
                    async with session.get(user_card['image']) as resp:
                        if resp.status == 200:
                            user_img_data = await resp.read()
                            user_img = Image.open(io.BytesIO(user_img_data)).resize(self.image_size)
                        else:
                            user_img = Image.new('RGB', self.image_size, color=(100, 100, 100))
                except:
                    user_img = Image.new('RGB', self.image_size, color=(100, 100, 100))
               
                # Opponent card
                try:
                    async with session.get(opponent_card['image']) as resp:
                        if resp.status == 200:
                            opp_img_data = await resp.read()
                            opp_img = Image.open(io.BytesIO(opp_img_data)).resize(self.image_size)
                        else:
                            opp_img = Image.new('RGB', self.image_size, color=(100, 100, 100))
                except:
                    opp_img = Image.new('RGB', self.image_size, color=(100, 100, 100))
           
            # Paste images
            img.paste(user_img, (0, 0))
            img.paste(opp_img, (300, 0))
           
            # Add borders
            draw = ImageDraw.Draw(img)
           
            # User card border
            user_color = self.get_rank_color(user_card.get('card_rank', 1))
            draw.rectangle([0, 0, 299, 349], outline=user_color, width=3)
           
            # Opponent card border
            opp_color = self.get_rank_color(opponent_card.get('card_rank', 1))
            draw.rectangle([300, 0, 599, 349], outline=opp_color, width=3)
           
            # Save to bytes
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
           
            return discord.File(img_bytes, filename="fight.png")
           
        except Exception as e:
            logging.error(f"Error creating fight image: {e}")
            # Return a simple placeholder
            img = Image.new('RGB', (600, 350), color=(50, 50, 50))
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            return discord.File(img_bytes, filename="fight.png")

    async def update_fight_imaged(self, user_card: Optional[Dict], opponent_card: Optional[Dict]) -> discord.File:
        """Update fight image when cards change"""
        if not user_card or not opponent_card:
            # Create placeholder for missing cards
            img = Image.new('RGB', (600, 350), color=(50, 50, 50))
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            return discord.File(img_bytes, filename="fight.png")
       
        return await self.create_fight_image(user_card, opponent_card)

    @commands.command(name="fdbstatus", aliases=["fdbs", "fconnections"])
    async def db_status(self, ctx):
        """Display database connection status"""
        multilanguage = await get_user_multilanguage(ctx.author.id)

        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send(_tr(multilanguage,
                               "You don't have permission to use this command.",
                               "ليس لديك الإذن لاستخدام هذا الأمر.",
                               "Vous n’avez pas la permission d’utiliser cette commande."))
            return
       
        try:
            metrics = get_metrics()
           
            embed = discord.Embed(
                title=_tr(multilanguage, "📊 Database Connection Status", "📊 حالة اتصال قاعدة البيانات", "📊 État des connexions à la base"),
                description=_tr(multilanguage, "Current status of database connection pool",
                                "الحالة الحالية لمجمّع اتصالات قاعدة البيانات",
                                "État actuel du pool de connexions"),
                color=discord.Color.blue()
            )
           
            embed.add_field(name=_tr(multilanguage, "Active Connections", "الاتصالات النشطة", "Connexions actives"), value=str(metrics["active_connections"]), inline=True)
            embed.add_field(name=_tr(multilanguage, "Peak Connections", "أعلى عدد اتصالات", "Pic de connexions"), value=str(metrics["peak_connections"]), inline=True)
            embed.add_field(name=_tr(multilanguage, "Connection Errors", "أخطاء الاتصال", "Erreurs de connexion"), value=str(metrics["connection_errors"]), inline=True)
            embed.add_field(name=_tr(multilanguage, "Total Queries", "إجمالي الاستعلامات", "Requêtes totales"), value=str(metrics["total_queries"]), inline=True)
            embed.add_field(name=_tr(multilanguage, "Slow Queries", "الاستعلامات البطيئة", "Requêtes lentes"), value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
            embed.add_field(name=_tr(multilanguage, "Avg Query Time", "متوسط زمن الاستعلام", "Temps moyen requête"), value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
           
            if metrics["last_error"]:
                error_time = metrics["last_error_time"].strftime("%Y-%m-%d %H:%M:%S") if metrics["last_error_time"] else "Unknown"
                embed.add_field(
                    name=_tr(multilanguage, "Last Error", "آخر خطأ", "Dernière erreur"),
                    value=_tr(multilanguage,
                              f"Time: {error_time}\nError: {metrics['last_error'][:100]}...",
                              f"الوقت: {error_time}\nالخطأ: {metrics['last_error'][:100]}...",
                              f"Heure : {error_time}\nErreur : {metrics['last_error'][:100]}..."),
                    inline=False
                )
           
            embed.timestamp = datetime.datetime.utcnow()
            await ctx.send(embed=embed)
                   
        except Exception as e:
            await ctx.send(_tr(multilanguage,
                               f"An error occurred: {str(e)}",
                               f"حدث خطأ: {str(e)}",
                               f"Une erreur s’est produite : {str(e)}"))

    @commands.command(name="fight", aliases=["f", "fg"])
    @command_lock("fight")
    @command_cooldown(rate=1, per=5.0)
    async def fight(self, ctx):
        """Start a PvP fight"""
        multilanguage = await get_user_multilanguage(ctx.author.id)
        try:
            await ctx.send(_tr(multilanguage, "Initiating fight...", "جاري بدء القتال...", "Lancement du combat..."))

            # Get user data
            user_data = await self.fight_helpers.get_user_data(ctx.author.id)
            if not user_data:
                await ctx.send(_tr(multilanguage,
                                   "You need to be registered and have a team to use this command.",
                                   "يجب أن تكون مسجلاً ولديك فريق لاستخدام هذا الأمر.",
                                   "Vous devez être inscrit et avoir une équipe pour utiliser cette commande."))
                return

            if not user_data['fighters']:
                await ctx.send(_tr(multilanguage,
                                   "You need at least one fighter card in your team to proceed with the fight.",
                                   "تحتاج بطاقة مقاتل واحدة على الأقل في فريقك للمتابعة في القتال.",
                                   "Vous avez besoin d’au moins une carte combattant dans votre équipe pour continuer."))
                return

            # Check fight status
            in_fight, fight_message = await self.fight_helpers.check_fight_status(ctx.author.id)
            if in_fight:
                await ctx.send(fight_message)  # already localized upstream
                return

            # Check fight timer
            can_fight, timer_message = await self.fight_helpers.check_fight_timer(ctx.author.id)
            if not can_fight:
                await ctx.send(timer_message)  # already localized upstream
                return

            # Set fight status
            await self.fight_helpers.set_fight_status(ctx.author.id, 1)

            # Get opponents
            opponents = await self.fight_helpers.get_random_opponents(ctx.author.id)
            if not opponents:
                await ctx.send(_tr(multilanguage,
                                   "No suitable opponents found. Please try again later.",
                                   "لم يتم العثور على خصوم مناسبين. الرجاء المحاولة لاحقًا.",
                                   "Aucun adversaire approprié trouvé. Réessayez plus tard."))
                await self.fight_helpers.set_fight_status(ctx.author.id, 0)
                return

            # Select random opponent
            opponent_data = random.choice(opponents)

            # Apply charm effects
            user_data['fighters'] = await self.fight_helpers.apply_charm_effects(user_data['fighters'], user_data['user_id'])
            opponent_data['fighters'] = await self.fight_helpers.apply_charm_effects(opponent_data['fighters'], opponent_data['user_id'])

            # Start the fight
            await self.start_fight(ctx, user_data, opponent_data, is_challenge=False, multilanguage=multilanguage)

        except Exception as e:
            await self.fight_helpers.set_fight_status(ctx.author.id, 0)
            await self.handle_fight_error(ctx, e)

    @commands.command(name="challenge", aliases=["ch", "chl"])
    @command_lock("challenge")
    @command_cooldown(rate=1, per=10.0)
    async def challenge(self, ctx, opponent: discord.Member):
        """Challenge another user to a fight with proper confirmation system"""
        multilanguage = await get_user_multilanguage(ctx.author.id)
        try:
            if ctx.author == opponent:
                await ctx.send(_tr(multilanguage,
                                   "You can't challenge yourself!",
                                   "لا يمكنك تحدي نفسك!",
                                   "Vous ne pouvez pas vous défier vous-même !"))
                return

            # Check if both users have fighters
            user_data = await self.fight_helpers.get_user_fighters(ctx.author.id)
            opponent_data = await self.fight_helpers.get_user_fighters(opponent.id)

            if not user_data or not user_data['fighters']:
                await ctx.send(_tr(multilanguage,
                                   "You don't have any fighters! Use `!team` to set up your team.",
                                   "ليس لديك أي مقاتلين! استخدم `!team` لإعداد فريقك.",
                                   "Vous n’avez aucun combattant ! Utilisez `!team` pour configurer votre équipe."))
                return

            if not opponent_data or not opponent_data['fighters']:
                await ctx.send(_tr(multilanguage,
                                   f"{opponent.name} doesn't have any fighters set up!",
                                   f"{opponent.name} لا يملك أي مقاتلين مُعدّين!",
                                   f"{opponent.name} n’a aucun combattant configuré !"))
                return

            # Check if users are already in fights
            user_fight_status = await self.fight_helpers.get_fight_status(ctx.author.id)
            opponent_fight_status = await self.fight_helpers.get_fight_status(opponent.id)

            if user_fight_status:
                await ctx.send(_tr(multilanguage,
                                   "You're already in a fight!",
                                   "أنت بالفعل في معركة!",
                                   "Vous êtes déjà en combat !"))
                return

            if opponent_fight_status:
                await ctx.send(_tr(multilanguage,
                                   f"{opponent.name} is already in a fight!",
                                   f"{opponent.name} في معركة بالفعل!",
                                   f"{opponent.name} est déjà en combat !"))
                return

            # Create challenge embed
            challenge_embed = discord.Embed(
                title=_tr(multilanguage, "🥊 Challenge Issued!", "🥊 تم إرسال التحدي!", "🥊 Défi lancé !"),
                color=discord.Color.red()
            )
            challenge_embed.add_field(
                name=_tr(multilanguage, "Challenger", "المتحدّي", "Défiant"),
                value=_tr(multilanguage,
                          f"{ctx.author.mention}\n**Team Size:** {len(user_data['fighters'])} fighters",
                          f"{ctx.author.mention}\n**حجم الفريق:** {len(user_data['fighters'])} مقاتل",
                          f"{ctx.author.mention}\n**Taille de l’équipe :** {len(user_data['fighters'])} combattants"),
                inline=True
            )
            challenge_embed.add_field(
                name=_tr(multilanguage, "Challenged", "المتحدّى", "Défié"),
                value=_tr(multilanguage,
                          f"{opponent.mention}\n**Team Size:** {len(opponent_data['fighters'])} fighters",
                          f"{opponent.mention}\n**حجم الفريق:** {len(opponent_data['fighters'])} مقاتل",
                          f"{opponent.mention}\n**Taille de l’équipe :** {len(opponent_data['fighters'])} combattants"),
                inline=True
            )
            challenge_embed.set_footer(
                text=_tr(multilanguage,
                         "You have 60 seconds to respond! ✅ Accept | ❌ Decline",
                         "أمامك 60 ثانية للرد! ✅ قبول | ❌ رفض",
                         "Vous avez 60 secondes pour répondre ! ✅ Accepter | ❌ Refuser")
            )
           
            challenge_msg = await ctx.send(
                content=_tr(multilanguage,
                            f"{opponent.mention}, you've been challenged to a fight!",
                            f"{opponent.mention}، لقد تم تحدّيك لقتال!",
                            f"{opponent.mention}, vous avez été défié pour un combat !"),
                embed=challenge_embed
            )
           
            await challenge_msg.add_reaction("✅")
            await challenge_msg.add_reaction("❌")
           
            def check(reaction, user):
                return user == opponent and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == challenge_msg.id
           
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
               
                if str(reaction.emoji) == "✅":
                    accept_embed = discord.Embed(
                        title=_tr(multilanguage, "🏆 Challenge Accepted!", "🏆 تم قبول التحدي!", "🏆 Défi accepté !"),
                        description=_tr(multilanguage,
                                        f"{opponent.name} has accepted the challenge from {ctx.author.name}!",
                                        f"{opponent.name} قبل التحدي من {ctx.author.name}!",
                                        f"{opponent.name} a accepté le défi de {ctx.author.name} !"),
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=accept_embed)
                   
                    # Apply charm effects
                    user_data['fighters'] = await self.fight_helpers.apply_charm_effects(user_data['fighters'], user_data['user_id'])
                    opponent_data['fighters'] = await self.fight_helpers.apply_charm_effects(opponent_data['fighters'], opponent_data['user_id'])
                   
                    await self.start_fight(ctx, user_data, opponent_data, is_challenge=True, multilanguage=multilanguage)
                else:
                    decline_embed = discord.Embed(
                        title=_tr(multilanguage, "❌ Challenge Declined", "❌ تم رفض التحدي", "❌ Défi refusé"),
                        description=_tr(multilanguage,
                                        f"{opponent.name} has declined the challenge.",
                                        f"{opponent.name} رفض التحدي.",
                                        f"{opponent.name} a refusé le défi."),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=decline_embed)
           
            except asyncio.TimeoutError:
                timeout_embed = discord.Embed(
                    title=_tr(multilanguage, "⏰ Challenge Expired", "⏰ انتهى وقت التحدي", "⏰ Défi expiré"),
                    description=_tr(multilanguage,
                                    f"{opponent.name} didn't respond in time.",
                                    f"{opponent.name} لم يرد في الوقت المحدد.",
                                    f"{opponent.name} n’a pas répondu à temps."),
                    color=discord.Color.dark_grey()
                )
                await ctx.send(embed=timeout_embed)

        except Exception as e:
            await ctx.send(_tr(multilanguage,
                               f"An error occurred while processing the challenge: {str(e)}",
                               f"حدث خطأ أثناء معالجة التحدي: {str(e)}",
                               f"Une erreur s’est produite lors du traitement du défi : {str(e)}"))
            logging.error(f"Challenge error: {e}")

    #  Enhanced start_fight with skill initialization
    async def start_fight(self, ctx, user_data, opponent_data, is_challenge, multilanguage: int = 0):
        """Start the actual fight between two users with skill initialization"""
        try:
            # Sort fighters by position
            user_data['fighters'].sort(key=lambda x: x.get('position', float('inf')))
            opponent_data['fighters'].sort(key=lambda x: x.get('position', float('inf')))
           
            # Get user names
            user = await self.fetch_user_with_retry(user_data['user_authorid'])
            opponent = await self.fetch_user_with_retry(opponent_data['user_authorid'])
           
            user_name = user.name if user else f"Unknown User (ID: {user_data['user_authorid']})"
            opponent_name = opponent.name if opponent else f"Unknown Opponent (ID: {opponent_data['user_authorid']})"

            await ctx.send(_tr(multilanguage,
                               f"Fight starting between {user_name} and {opponent_name}!",
                               f"سيبدأ القتال بين {user_name} و {opponent_name}!",
                               f"Le combat commence entre {user_name} et {opponent_name} !"))
           
            #  Initialize skills for both teams
            user_id = await self.fight_helpers.get_user_id(ctx.author.id)
            opponent_id = await self.fight_helpers.get_user_id(int(opponent_data['user_authorid']))
           
            if user_id:
                skill_messages = await self.battle_mechanics.skill_system.initialize_fight_skills(user_id, user_data['fighters'])
                if skill_messages:
                    print("**User Skills Activated:**\n" + "\n".join(skill_messages))
           
            if opponent_id:
                skill_messages = await self.battle_mechanics.skill_system.initialize_fight_skills(opponent_id, opponent_data['fighters'])
                if skill_messages:
                    print("**Opponent Skills Activated:**\n" + "\n".join(skill_messages))
           
            await asyncio.sleep(1)

            # Track fighter card IDs for XP rewards
            user_fighter_card_ids = [fighter['card_id'] for fighter in user_data['fighters']]

            await self.conduct_fight(ctx, user_data['fighters'], opponent_data['fighters'],
                                     user_name, opponent_name, is_challenge, user_fighter_card_ids, multilanguage)

        except Exception as e:
            await self.handle_fight_error(ctx, e)
        finally:
            await self.fight_helpers.set_fight_status(ctx.author.id, 0)
            if is_challenge:
                opponent_id = int(opponent_data['user_authorid'])
                await self.fight_helpers.set_fight_status(opponent_id, 0)

    #  Enhanced conduct_fight with full skill integration
    async def conduct_fight(self, ctx, user_fighters, opponent_fighters, user_name, opponent_name, is_challenge, fighter_card_ids, multilanguage: int = 0):
        """Conduct the actual battle with comprehensive skill system"""
        try:
            user_team = sorted(user_fighters, key=lambda x: x.get('position', float('inf')))
            opponent_team = sorted(opponent_fighters, key=lambda x: x.get('position', float('inf')))

            if not user_team or not opponent_team:
                await ctx.send(_tr(multilanguage,
                                   "Error: One or both teams are empty. Cannot conduct the fight.",
                                   "خطأ: فريق واحد أو كلا الفريقين فارغ. لا يمكن تنفيذ القتال.",
                                   "Erreur : Une ou deux équipes sont vides. Impossible de poursuivre le combat."))
                return

            # Initialize health
            for fighter in user_team + opponent_team:
                fighter['current_health'] = fighter['health_stat']
           
            # Speed tie resolution
            user_goes_first_in_tie = random.choice([True, False])

            # Create initial fight display
            embed, file = await self.create_fight_embed(user_team[0], opponent_team[0], user_name, opponent_name, is_challenge, multilanguage)
            fight_message = await ctx.send(file=file, embed=embed)
            battle_log = []

            # Handle summon skills before main battle
            user_id = await self.fight_helpers.get_user_id(ctx.author.id)
            opponent_id = await self.fight_helpers.get_user_id(int(opponent_team[0].get('user_authorid', 0))) if 'user_authorid' in opponent_team[0] else None
           
            # Process summons for user team
            if user_id:
                active_summons = []
                for card in user_team:
                    if card['current_health'] > 0 and 'skill_state' in card:
                        summon_messages, summons = await self.battle_mechanics.skill_system.handle_summon_skills(card, user_id, [])
                        if summon_messages:
                            battle_log.extend(summon_messages)
                        if summons:
                            active_summons.extend(summons)
               
                # Add summons to user team (summons fight first)
                if active_summons:
                    user_team = active_summons + user_team

            # Process summons for opponent team
            if opponent_id:
                active_summons = []
                for card in opponent_team:
                    if card['current_health'] > 0 and 'skill_state' in card:
                        summon_messages, summons = await self.battle_mechanics.skill_system.handle_summon_skills(card, opponent_id, [])
                        if summon_messages:
                            battle_log.extend(summon_messages)
                        if summons:
                            active_summons.extend(summons)
               
                # Add summons to opponent team
                if active_summons:
                    opponent_team = active_summons + opponent_team

            # Main battle loop
            while user_team and opponent_team:
                user_fighter = user_team[0]
                opponent_fighter = opponent_team[0]

                # Determine turn order
                if user_fighter['speed_stat'] > opponent_fighter['speed_stat']:
                    fighters = [(user_fighter, True), (opponent_fighter, False)]
                elif user_fighter['speed_stat'] < opponent_fighter['speed_stat']:
                    fighters = [(opponent_fighter, False), (user_fighter, True)]
                else:  # Speed tie
                    if user_goes_first_in_tie:
                        fighters = [(user_fighter, True), (opponent_fighter, False)]
                    else:
                        fighters = [(opponent_fighter, False), (user_fighter, True)]

                # Execute attacks
                for attacker, is_user_turn in fighters:
                    await asyncio.sleep(1)
                    defender = opponent_fighter if is_user_turn else user_fighter

                    # Perform attack with skill integration (localized)
                    attack_log, hit = await self.battle_mechanics.perform_attack(attacker, defender, is_user_turn, multilanguage=multilanguage)
                    battle_log.extend(attack_log)
                   
                    # Update display
                    embed = await self.update_fight_embed(embed, user_fighter, opponent_fighter, user_name, opponent_name, battle_log, multilanguage)
                    await fight_message.edit(embed=embed)

                    # Check for defeat
                    if defender['current_health'] <= 0:
                        battle_log.append(self.battle_mechanics.get_defeat_message(defender['card_name'], is_user_turn, multilanguage))
                       
                        if is_user_turn:
                            opponent_team.pop(0)
                            if opponent_team:
                                battle_log.append(_tr(multilanguage, "🆕 **Next opponent incoming!**", "🆕 **الخصم التالي قادم!**", "🆕 **Adversaire suivant !**"))
                                battle_log.append(self.battle_mechanics.get_new_card_message(opponent_team[0]['card_name'], False, multilanguage))
                                opponent_fighter = opponent_team[0]
                            else:
                                break
                        else:
                            user_team.pop(0)
                            if user_team:
                                battle_log.append(_tr(multilanguage, "🔄 **Next card incoming!**", "🔄 **البطاقة التالية قادمة!**", "🔄 **Carte suivante !**"))
                                battle_log.append(self.battle_mechanics.get_new_card_message(user_team[0]['card_name'], True, multilanguage))
                                user_fighter = user_team[0]
                            else:
                                break

                        # Update image
                        new_file = await self.update_fight_image(user_team[0] if user_team else None,
                                                                 opponent_team[0] if opponent_team else None)
                        await fight_message.remove_attachments(fight_message.attachments)
                        await fight_message.edit(attachments=[new_file], embed=embed)

                    if not user_team or not opponent_team:
                        break

            # Announce winner and handle rewards
            await self.announce_winner(ctx, user_name, opponent_name, user_team, is_challenge, fighter_card_ids, multilanguage)

        except Exception as e:
            await self.handle_fight_error(ctx, e)

    async def announce_winner(self, ctx, user_name, opponent_name, user_team, is_challenge, fighter_card_ids, multilanguage: int = 0):
        """Announce fight winner and handle rewards"""
        if user_team:
            # User won
            await ctx.send(_tr(multilanguage,
                               f"**{user_name} has won the fight!**",
                               f"**{user_name} فاز في القتال!**",
                               f"**{user_name} a gagné le combat !**"))

            # Always update fight stats
            await self.fight_helpers.update_fight_stats(ctx.author.id, True, is_challenge)
            # Update quest progress
            quest_name = "Win 5 Challenges" if is_challenge else "Win 10 Fights"
            quest_completed = await self.fight_helpers.check_and_update_quest_progress(ctx.author.id, quest_name)
           
            if quest_completed:
                await ctx.send(_tr(multilanguage,
                                   "🎉 Congratulations! You've completed a quest!",
                                   "🎉 تهانينا! لقد أكملت مهمة!",
                                   "🎉 Félicitations ! Vous avez terminé une quête !"))
            if not is_challenge:
                # Handle XP rewards
                user_data = await self.fight_helpers.get_user_data(ctx.author.id)
                if user_data:
                    rewards = await self.xp_rewards.handle_xp_rewards(ctx.author.id, user_data['team_id'], fighter_card_ids)
                    await self.send_rewards_embed(ctx, rewards, multilanguage)

        else:
            # User lost
            await ctx.send(_tr(multilanguage,
                               f"**{opponent_name} has won the fight!**",
                               f"**{opponent_name} فاز في القتال!**",
                               f"**{opponent_name} a gagné le combat !**"))
            await ctx.send(_tr(multilanguage,
                               f"{user_name}, you've lost this battle. Better luck next time!",
                               f"{user_name}، لقد خسرت هذه المعركة. حظًا أوفر في المرة القادمة!",
                               f"{user_name}, vous avez perdu cette bataille. Ce sera mieux la prochaine fois !"))

            # Always update fight stats
            await self.fight_helpers.update_fight_stats(ctx.author.id, False, is_challenge)

    async def send_rewards_embed(self, ctx, rewards, multilanguage: int = 0):
        """Send XP rewards embed (shows affinity even when card is max level)"""
        if not rewards:
            return

        total_xp = sum(r.get('xp_gained', 0) for r in rewards)
        total_leveled = sum(1 for r in rewards if r.get('level_up'))

        embed = discord.Embed(
            title=_tr(multilanguage, "🏆 Battle Rewards Summary 🏆", "🏆 ملخص مكافآت المعركة 🏆", "🏆 Récapitulatif des récompenses 🏆"),
            color=discord.Color.gold(),
            description=_tr(multilanguage,
                            f"Total XP Gained: **{total_xp}**\nCards Leveled Up: **{total_leveled}**",
                            f"إجمالي الخبرة المكتسبة: **{total_xp}**\nالبطاقات التي ارتفع مستواها: **{total_leveled}**",
                            f"XP total gagné : **{total_xp}**\nCartes montées de niveau : **{total_leveled}**")
        )

        for reward in rewards:
            name_suffix = _tr(multilanguage, " 🏆 **MAX LEVEL**", " 🏆 **أقصى مستوى**", " 🏆 **NIVEAU MAX**") if reward.get('max_level_reached') else ""
            parts = []

            # If max level, show a short notice but DO NOT skip affinity
            if reward.get('max_level_reached'):
                parts.append(_tr(multilanguage,
                                 "No further XP or stat increases possible.",
                                 "لا مزيد من الخبرة أو زيادات الإحصائيات ممكنة.",
                                 "Plus d’XP ni d’augmentation de stats possibles."))
            else:
                # Safe progress calculation (avoid division by zero)
                xp_cap = reward.get('xp_cap') or 0
                new_xp = reward.get('new_xp', 0)
                xp_percentage = (new_xp / xp_cap) * 100 if xp_cap > 0 else 100
                progress_bar = self.xp_rewards.create_xp_progress_bar(xp_percentage)

                parts.append(
                    _tr(multilanguage,
                        f"{'🔥 Enhanced! ' if reward.get('enhancer_active') else ''}**+{reward.get('xp_gained', 0)} XP**",
                        f"{'🔥 مُعزَّز! ' if reward.get('enhancer_active') else ''}**+{reward.get('xp_gained', 0)} خبرة**",
                        f"{'🔥 Amélioré ! ' if reward.get('enhancer_active') else ''}**+{reward.get('xp_gained', 0)} XP**")
                )
                parts.append(_tr(multilanguage,
                                 f"{progress_bar} **{new_xp}/{xp_cap}**",
                                 f"{progress_bar} **{new_xp}/{xp_cap}**",
                                 f"{progress_bar} **{new_xp}/{xp_cap}**"))

                if reward.get('level_up'):
                    li = _tr(
                        multilanguage,
                        f"✨ **LEVEL UP!** {reward.get('old_level')} → {reward.get('new_level')}\n"
                        f"📈 Stats Increased:\n"
                        f"```diff\n"
                        f"+ ATK: +{reward.get('stat_increases', {}).get('attack', 0)}\n"
                        f"+ HP:  +{reward.get('stat_increases', {}).get('health', 0)}\n"
                        f"+ DEF: +{reward.get('stat_increases', {}).get('defence', 0)}\n"
                        f"+ SPD: +{reward.get('stat_increases', {}).get('speed', 0)}\n"
                        f"```",
                        f"✨ **ارتقى المستوى!** {reward.get('old_level')} → {reward.get('new_level')}\n"
                        f"📈 الإحصائيات ازدادت:\n"
                        f"```diff\n"
                        f"+ ATK: +{reward.get('stat_increases', {}).get('attack', 0)}\n"
                        f"+ HP:  +{reward.get('stat_increases', {}).get('health', 0)}\n"
                        f"+ DEF: +{reward.get('stat_increases', {}).get('defence', 0)}\n"
                        f"+ SPD: +{reward.get('stat_increases', {}).get('speed', 0)}\n"
                        f"```",
                        f"✨ **PASSAGE DE NIVEAU !** {reward.get('old_level')} → {reward.get('new_level')}\n"
                        f"📈 Statistiques augmentées :\n"
                        f"```diff\n"
                        f"+ ATK : +{reward.get('stat_increases', {}).get('attack', 0)}\n"
                        f"+ PV  : +{reward.get('stat_increases', {}).get('health', 0)}\n"
                        f"+ DÉF : +{reward.get('stat_increases', {}).get('defence', 0)}\n"
                        f"+ VIT : +{reward.get('stat_increases', {}).get('speed', 0)}\n"
                        f"```"
                    )
                    parts.append(li)

            # Affinity always appended if there's any change
            if reward.get('affinity_gained', 0) > 0:
                if reward.get('affinity_maxed', False):
                    parts.append(_tr(multilanguage,
                                     f"✨ **Affinity +{reward['affinity_gained']:.2f}** (Total: {reward['new_affinity']:.2f}) 🌟 **MAX AFFINITY REACHED!**",
                                     f"✨ **التآلف +{reward['affinity_gained']:.2f}** (الإجمالي: {reward['new_affinity']:.2f}) 🌟 **تم بلوغ أقصى تآلف!**",
                                     f"✨ **Affinité +{reward['affinity_gained']:.2f}** (Total : {reward['new_affinity']:.2f}) 🌟 **AFFINITÉ MAX !**"))
                else:
                    parts.append(_tr(multilanguage,
                                     f"✨ **Affinity +{reward['affinity_gained']:.2f}** (Total: {reward['new_affinity']:.2f}/{reward.get('affinity_cap', 0):.2f})",
                                     f"✨ **التآلف +{reward['affinity_gained']:.2f}** (الإجمالي: {reward['new_affinity']:.2f}/{reward.get('affinity_cap', 0):.2f})",
                                     f"✨ **Affinité +{reward['affinity_gained']:.2f}** (Total : {reward['new_affinity']:.2f}/{reward.get('affinity_cap', 0):.2f})"))
            # else: (optional show current affinity)

            # Fallback if nothing was added
            card_info = "\n".join(parts) if parts else _tr(multilanguage, "No changes.", "لا تغييرات.", "Aucun changement.")

            embed.add_field(
                name=f"{reward.get('card_icon', '')} {reward.get('card_name', 'Unknown')}{name_suffix}",
                value=card_info,
                inline=False
            )

        embed.set_footer(text=_tr(multilanguage,
                                  "Keep fighting to grow stronger! | Solo Leveling PVP",
                                  "واصل القتال لتصبح أقوى! | Solo Leveling PVP",
                                  "Continuez à vous battre pour devenir plus fort ! | Solo Leveling PVP"))
        await ctx.send(embed=embed)

    def create_composite_image(self, image1_path, image2_path, rank1, rank2, spacing=200):
        """Create composite battle image"""
        try:
            with Image.open(image1_path) as img1, Image.open(image2_path) as img2:
                img1 = img1.resize(self.image_size)
                img2 = img2.resize(self.image_size)

                border_size = 5
                img1_with_border = Image.new('RGBA', (self.image_size[0] + 2*border_size, self.image_size[1] + 2*border_size), self.get_rank_color(rank1))
                img2_with_border = Image.new('RGBA', (self.image_size[0] + 2*border_size, self.image_size[1] + 2*border_size), self.get_rank_color(rank2))

                img1_with_border.paste(img1, (border_size, border_size), img1 if img1.mode == 'RGBA' else None)
                img2_with_border.paste(img2, (border_size, border_size), img2 if img2.mode == 'RGBA' else None)

                total_width = (self.image_size[0] + 2*border_size) * 2 + spacing
                max_height = self.image_size[1] + 2*border_size
                composite = Image.new('RGBA', (total_width, max_height), (0, 0, 0, 0))

                composite.paste(img1_with_border, (0, 0), img1_with_border)
                composite.paste(img2_with_border, (self.image_size[0] + 2*border_size + spacing, 0), img2_with_border)

                # Add VS text
                draw = ImageDraw.Draw(composite)
                try:
                    poppins_font_path = os.path.join(os.path.dirname(__file__), 'Poppins-Regular.ttf')
                    font = ImageFont.truetype(poppins_font_path, 60)
                except:
                    font = ImageFont.load_default()
                   
                vs_text = "VS"
                vs_bbox = draw.textbbox((0, 0), vs_text, font=font)
                vs_width, vs_height = vs_bbox[2] - vs_bbox[0], vs_bbox[3] - vs_bbox[1]
                vs_position = ((total_width - vs_width) // 2, (max_height - vs_height) // 2)
                draw.text(vs_position, vs_text, font=font, fill=(255, 255, 255))

                buffer = io.BytesIO()
                composite.save(buffer, format='PNG')
                buffer.seek(0)
                return buffer
        except Exception as e:
            print(f"Error creating composite image: {e}")
            # Return a blank image as fallback
            blank_image = Image.new('RGBA', self.image_size, (0, 0, 0, 0))
            buffer = io.BytesIO()
            blank_image.save(buffer, format='PNG')
            buffer.seek(0)
            return buffer

    async def create_fight_embed(self, user_card, opponent_card, user_name, opponent_name, is_challenge=False, multilanguage: int = 0):
        """Create initial fight embed"""
        embed = discord.Embed(title=_tr(multilanguage, "⚔️ **Epic PVP Battle** ⚔️", "⚔️ **معركة PvP ملحمية** ⚔️", "⚔️ **Épique combat JcJ** ⚔️"), color=discord.Color.dark_gold())
        embed.set_author(name="Solo Leveling Bot")

        embed.add_field(
            name=_tr(multilanguage, f"🔵 **{user_name}'s Fighter**", f"🔵 **مقاتل {user_name}**", f"🔵 **Combattant de {user_name}**"),
            value=self.battle_mechanics.create_card_info(user_card, True, user_name, is_challenge, multilanguage),
            inline=True
        )
        embed.add_field(
            name=_tr(multilanguage, f"🔴 **{opponent_name}'s Fighter**", f"🔴 **مقاتل {opponent_name}**", f"🔴 **Combattant de {opponent_name}**"),
            value=self.battle_mechanics.create_card_info(opponent_card, False, opponent_name, is_challenge, multilanguage),
            inline=True
        )

        embed.set_footer(text=_tr(multilanguage,
                                  "May the strongest hunter prevail! 💪 | Solo Leveling PVP",
                                  "ليَفُز أقوى الصيادين! 💪 | Solo Leveling PVP",
                                  "Que le plus fort des chasseurs l’emporte ! 💪 | Solo Leveling PVP"))

        file = await self.update_fight_image(user_card, opponent_card)
        embed.set_image(url="attachment://battle_cards.png")

        return embed, file

    async def update_fight_embed(self, embed, user_card, opponent_card, user_name, opponent_name, battle_log, multilanguage: int = 0, is_challenge: bool = False):
        """Update fight embed with current battle state"""
        embed.clear_fields()

        embed.add_field(
            name=_tr(multilanguage, f"🔵 **{user_name}'s Fighter**", f"🔵 **مقاتل {user_name}**", f"🔵 **Combattant de {user_name}**"),
            value=self.battle_mechanics.create_card_info(user_card, True, user_name, is_challenge, multilanguage),
            inline=True
        )
        embed.add_field(
            name=_tr(multilanguage, f"🔴 **{opponent_name}'s Fighter**", f"🔴 **مقاتل {opponent_name}**", f"🔴 **Combattant de {opponent_name}**"),
            value=self.battle_mechanics.create_card_info(opponent_card, False, opponent_name, is_challenge, multilanguage),
            inline=True
        )

        embed.add_field(
            name=_tr(multilanguage, "🗒️ **Battle Log**", "🗒️ **سجل المعركة**", "🗒️ **Journal de combat**"),
            value=f"```css\n{chr(10).join(battle_log[-5:])}\n```",
            inline=False
        )

        return embed

    async def update_fight_image(self, user_card, opponent_card):
        """Update fight image"""
        if not user_card or not opponent_card:
            blank_image = Image.new('RGBA', self.image_size, (0, 0, 0, 0))
            buffer = io.BytesIO()
            blank_image.save(buffer, format='PNG')
            buffer.seek(0)
            return discord.File(buffer, filename="battle_cards.png")

        user_image_path = os.path.join(self.image_path, user_card['image'])
        opponent_image_path = os.path.join(self.image_path, opponent_card['image'])
       
        # Check if images exist
        if not os.path.exists(user_image_path) or not os.path.exists(opponent_image_path):
            blank_image = Image.new('RGBA', self.image_size, (0, 0, 0, 0))
            buffer = io.BytesIO()
            blank_image.save(buffer, format='PNG')
            buffer.seek(0)
            return discord.File(buffer, filename="battle_cards.png")
           
        composite_image = self.create_composite_image(user_image_path, opponent_image_path,
                                                    user_card['card_rank'], opponent_card['card_rank'])
        return discord.File(composite_image, filename="battle_cards.png")

    async def handle_fight_error(self, ctx, error):
        """Handle fight errors and send to support"""
        # Keep support message in English (internal). User-facing message localized.
        error_message = (
            f"🚨 <@818132161534230559> Error in fight system!\n"
            f"```\nError: {str(error)}\n"
            f"Channel: {ctx.channel.name} ({ctx.channel.id})\n"
            f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
            f"Command User: {ctx.author.name} ({ctx.author.id})\n```\n"
            f"Traceback:\n```python\n{traceback.format_exc()[:1500]}```"
        )
       
        try:
            support_channel = self.bot.get_channel(1291676421109973043)
            if support_channel:
                if len(error_message) > 2000:
                    parts = [error_message[i:i+1990] for i in range(0, len(error_message), 1990)]
                    for i, part in enumerate(parts):
                        if i == 0:
                            await support_channel.send(part)
                        else:
                            await support_channel.send(f"```{part}```")
                else:
                    await support_channel.send(error_message)

            multilanguage = await get_user_multilanguage(ctx.author.id)
            await ctx.send(_tr(multilanguage,
                               "An unexpected error occurred. Our support team has been notified and will investigate the issue.",
                               "حدث خطأ غير متوقع. تم إبلاغ فريق الدعم وسيتحقق من المشكلة.",
                               "Une erreur inattendue s’est produite. Notre équipe de support a été notifiée et va enquêter."))
           
        except Exception as send_error:
            print(f"Failed to send error to support channel: {str(send_error)}")
            multilanguage = await get_user_multilanguage(ctx.author.id)
            await ctx.send(_tr(multilanguage,
                               "An unexpected error occurred. Please try again later.",
                               "حدث خطأ غير متوقع. يرجى المحاولة لاحقًا.",
                               "Une erreur inattendue s’est produite. Veuillez réessayer plus tard."))

    @commands.command(name='inventoryallow', hidden=True)
    async def inventory_allow(self, ctx, discord_id: str = None):
        """Add a user to the inventory table (Admin only)"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1076303866854899762","1204879494901407805"):
            return  # Silently ignore if not authorized
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl inventoryallow <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Check if user already has an inventory entry
            check_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            check_result = await execute_query_with_retry(check_query, (user_id,))
           
            if check_result:
                await ctx.send(f"User with Discord ID {discord_id} already has an inventory entry.")
                return
           
            # Insert into inventory table
            insert_query = "INSERT INTO inventory(user_id) VALUES(%s)"
            await execute_query_with_retry(insert_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully added inventory entry for user with Discord ID {discord_id}.")
           
        except Exception as e:
            error_msg = f"Error in inventoryallow command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
   
    @commands.command(name='teamallow', hidden=True)
    async def team_allow(self, ctx, discord_id: str = None):
        """Add a user to the team table (Admin only)"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl teamallow <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Check if user already has a team entry
            check_query = "SELECT current_team as team_id FROM user WHERE user_id = %s"
            check_result = await execute_query_with_retry(check_query, (user_id,))
           
            if check_result:
                await ctx.send(f"User with Discord ID {discord_id} already has a team entry.")
                return
           
            # Insert into team table with the specified background
            insert_query = "INSERT INTO team(user_id, team_bg) VALUES(%s, 'curr.png')"
            await execute_query_with_retry(insert_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully added team entry for user with Discord ID {discord_id}.")
           
        except Exception as e:
            error_msg = f"Error in teamallow command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='adminreset', hidden=True)
    async def admin_reset(self, ctx):
        """Hidden admin command to reset user progress"""
        # Check if the command invoker is the authorized admin
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        try:
            # SQL query to reset all user progress
            query = """
            UPDATE user SET
                fight_current = 0,
                mission_current = 0,
                dungeon_current = 0,
                chapter_count = 0,
                squad_current = 0,
                multi_session = 0,
                fight_timer = NULL,
                squad_timer = NULL,
                dungeon_timer = NULL,
                mission_timer = NULL
            """
           
            # Execute the update query
            await execute_query_with_retry(query)
           
            # Send confirmation message only to admin
            embed = discord.Embed(
                title="Admin Reset Complete",
                description="Successfully reset all user progress metrics.",
                color=discord.Color.green()
            )
           
            embed.add_field(
                name="Reset Fields",
                value="• fight_current\n• mission_current\n• dungeon_current\n• chapter_count\n• fight_timer\n• dungeon_timer\n• mission_timer",
                inline=False
            )
           
            # Add timestamp to embed
           
            await ctx.send(embed=embed, delete_after=30)  # Message deletes after 30 seconds
           
            # Delete the command message if possible
            try:
                await ctx.message.delete()
            except:
                pass
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                log_embed = discord.Embed(
                    title="Admin Reset Command Used",
                    color=discord.Color.red()
                )
                log_embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                log_embed.add_field(name="Action", value="Full User Progress Reset", inline=True)
                log_embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                log_embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=log_embed)
           
        except Exception as e:
            # Only send error message to admin
            if str(ctx.author.id) == "818132161534230559":
                error_msg = f"Error during admin reset: {str(e)}"
                await ctx.send(error_msg, delete_after=10)
           
            print(f"Admin reset error: {traceback.format_exc()}")
    @commands.command(name='statusreset', hidden=True)
    async def status_reset(self, ctx, discord_id: str = None):
        """Reset status tables for a user (Admin only)"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl statusreset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from all the specified status tables
            status_tables = ['sell_status', 'shop_status', 'gamble_status', 'trade_status', 'stat_status']
           
            for table in status_tables:
                delete_query = f"DELETE FROM {table} WHERE user_id = %s"
                await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset status tables for user with Discord ID {discord_id}.")
           
        except Exception as e:
            error_msg = f"Error in statusreset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='marketreset', hidden=True)
    async def marketreset(self, ctx, discord_id: str = None):
        """Reset sell_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl sellreset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from sell_status table
            delete_query = "DELETE FROM market_session WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset market_session for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Market Reset Command Used",
                    color=discord.Color.orange()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="market_session", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in sellreset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='ascendreset', hidden=True)
    async def ascendreset(self, ctx, discord_id: str = None):
        """Reset sell_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl sellreset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from sell_status table
            delete_query = "DELETE FROM ascending_state WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset ascending state for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Sell Reset Command Used",
                    color=discord.Color.orange()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="ascending_state", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in sellreset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='sellreset', hidden=True)
    async def sell_reset(self, ctx, discord_id: str = None):
        """Reset sell_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl sellreset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from sell_status table
            delete_query = "DELETE FROM sell_status WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset sell_status for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Sell Reset Command Used",
                    color=discord.Color.orange()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="sell_status", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in sellreset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='shopreset', hidden=True)
    async def shop_reset(self, ctx, discord_id: str = None):
        """Reset shop_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl shopreset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from shop_status table
            delete_query = "DELETE FROM shop_status WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset shop_status for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Shop Reset Command Used",
                    color=discord.Color.purple()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="shop_status", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in shopreset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='gamblereset', hidden=True)
    async def gamble_reset(self, ctx, discord_id: str = None):
        """Reset gamble_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl gamblereset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from gamble_status table
            delete_query = "DELETE FROM gamble_status WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset gamble_status for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Gamble Reset Command Used",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="gamble_status", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in gamblereset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='tutoreset', hidden=True)
    async def gamble_reset(self, ctx, discord_id: str = None):
        """Reset gamble_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl gamblereset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from gamble_status table
            delete_query = "DELETE FROM quiz_status WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset tutorial_reset for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Tuto Reset Command Used",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="gamble_status", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in gamblereset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='tradereset', hidden=True)
    async def trade_reset(self, ctx, discord_id: str = None):
        """Reset trade_status table for a user (Admin only)"""
        # Check if the command invoker is authorized (ONLY original 4 admins for trade reset)
        if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl tradereset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from trade_status table
            delete_query = "DELETE FROM trade_status WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset trade_status for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Trade Reset Command Used",
                    color=discord.Color.red()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="trade_status", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in tradereset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name='statreset', hidden=True)
    async def stat_reset(self, ctx, discord_id: str = None):
        """Reset stat_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970"]
       
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
       
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970"]:
            if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                return  # Silently ignore if not in correct server
       
        if not discord_id:
            await ctx.send("Please provide a Discord ID: `sl statreset <discord_id>`")
            return
       
        try:
            # First, get the user_id from the user_authorid
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (discord_id,))
           
            if not user_result:
                await ctx.send(f"User with Discord ID {discord_id} not found in the database.")
                return
           
            user_id = user_result[0]['user_id']
           
            # Delete rows from stat_status table
            delete_query = "DELETE FROM stat_status WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
           
            # Send confirmation message
            await ctx.send(f"Successfully reset stat_status for user with Discord ID {discord_id}.")
           
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Stat Reset Command Used",
                    color=discord.Color.teal()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=discord_id, inline=True)
                embed.add_field(name="Reset Table", value="stat_status", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error in statreset command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
async def setup(bot):
    await bot.add_cog(EmFightCommands(bot))

