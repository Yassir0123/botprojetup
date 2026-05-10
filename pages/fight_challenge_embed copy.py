import discord
from discord.ext import commands
import random
import asyncio
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
from typing import List, Dict, Any, Optional, Tuple
from utils.database import execute_query_with_retry, get_metrics, get_connection, release_connection

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
                # If we couldn't acquire the lock in time, inform the user
                await ctx.send(f"You're already running this command. Please wait for it to complete.")
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
                
            # Check if the command is on cooldown
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    # Command is on cooldown
                    remaining = round(retry_after, 1)
                    await ctx.send(f"⏰ Command on cooldown. Please wait {remaining} seconds.")
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

    @db_transaction(max_retries=3)
    async def get_user_data(self, user_id: int, conn=None) -> Optional[Dict]:
        """Get complete user data including team and fighters"""
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
                "SELECT team_id FROM team WHERE user_id = %s",
                (user_data['user_id'],)
            )
            team_result = await cursor.fetchone()
            
            if not team_result:
                return None

            user_data['team_id'] = team_result['team_id']
            
            # Get fighter cards
            fighters_query = """
            SELECT c.card_id, c.card_name, c.image, c.card_rank, uc.power_stat, 
                   uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, 
                   uc.current_level, tc.position, uc.current_xp, uc.id_enhancer, 
                   uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed, 
                   uc.charm_health, uc.attack_count, uc.defence_count, uc.speed_count, 
                   uc.health_count
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
            
            if result and result['fight_current'] == 1:
                return True, "You are already in a fight. Please complete your current battle before starting a new one."
            
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
                return False, f"You need to wait {time_remaining//60} minutes and {time_remaining%60} seconds before you can fight again."
            
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
                SELECT c.card_id, c.card_name, c.image, c.card_rank, uc.power_stat, 
                       uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, 
                       uc.current_level, tc.position, uc.current_xp, uc.id_enhancer, 
                       uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed, 
                       uc.charm_health, uc.attack_count, uc.defence_count, uc.speed_count, 
                       uc.health_count
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

    @db_transaction(max_retries=3)
    async def apply_charm_effects(self, fighters: List[Dict], user_id: int, conn=None) -> List[Dict]:
        """Apply charm effects to fighter cards"""
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

# Battle Mechanics
class BattleMechanics:
    def __init__(self):
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"

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

    def perform_attack(self, attacker: Dict, defender: Dict, is_user_turn: bool) -> Tuple[List[str], bool]:
        """Perform an attack and return battle log and if attack hit"""
        # Calculate dodge chance
        dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
        
        # Check for dodge
        if random.random() * 100 < dodge_chance:
            return [f"🛡️ {defender['card_name']} dodged the attack!"], False
        
        # Calculate damage
        base_damage = random.randint(
            int(attacker['attack_stat'] * 0.45), 
            int(attacker['attack_stat'] * 0.55)
        )
        
        # Critical hit check
        crit_chance = min(10, max(5, attacker['attack_stat'] / 100))
        is_critical = random.random() * 100 < crit_chance
        crit_multiplier = 1.5 if is_critical else 1.0
        
        # Apply damage using your original formula
        effective_damage = self.calculate_effective_damage(
            int(base_damage * crit_multiplier), 
            defender['defence_stat']
        )
        
        defender['current_health'] -= effective_damage
        defender['current_health'] = max(0, defender['current_health'])
        
        # Create attack message
        emoji = "⚔️" if is_user_turn else "🛡️"
        attacker_name = attacker['card_name'][:20] + '...' if len(attacker['card_name']) > 20 else attacker['card_name']
        
        if is_critical:
            message = f"💥 **CRITICAL HIT!** {emoji} {attacker_name} → {effective_damage} MASSIVE damage (Base: {base_damage})"
        else:
            message = f"{emoji} {attacker_name} → {effective_damage} damage (Base: {base_damage})"
        
        return [message], True

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

    def create_card_info(self, card: Dict, is_user: bool, name: str, is_challenge: bool = False) -> str:
        """Create formatted card information"""
        health_percentage = (card['current_health'] / card['health_stat']) * 100
        health_bar = self.create_health_bar(health_percentage, is_user, is_challenge)

        return (
            f"**{card['card_name']}** (Lv.{card['current_level']})\n"
            f"{health_bar} {card['current_health']}/{card['health_stat']} HP\n"
            f"🔸 **POW**: {card['power_stat']}\n"
            f"🔸 **ATK**: {card['attack_stat']}\n"
            f"🔸 **DEF**: {card['defence_stat']}\n"
            f"🔸 **SPD**: {card['speed_stat']}\n"
        )

    def get_defeat_message(self, defeated_name: str, is_user_turn: bool) -> str:
        """Get defeat message"""
        emoji = "🏆" if is_user_turn else "💀"
        return f"{emoji} **{defeated_name} has been defeated!**"

    def get_new_card_message(self, new_card_name: str, is_user_turn: bool) -> str:
        """Get new card entering message"""
        emoji = "🔄" if is_user_turn else "🆕"
        return f"{emoji} New {'user' if is_user_turn else 'opponent'} card: {new_card_name}"

# XP Rewards System
class XPRewards:
    def __init__(self):
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.gray_square = "<:gray:1295036907595759697>"

    @db_transaction(max_retries=3)
    async def handle_xp_rewards(self, user_id: int, team_id: int, fighter_card_ids: List[int], conn=None) -> List[Dict]:
        """Handle XP rewards for battle participants"""
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
            SELECT c.card_id, c.card_name, c.card_icon, c.card_rank, c.xp_cap, c.max_level, 
                uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat,
                uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                uc.id_enhancer, uc.fight_count, uc.weapon_id, uc.armor_id, uc.shield_id
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            WHERE uc.user_id = %s AND uc.card_id IN ({placeholders}) AND c.type_card = 'Fighter'
            """
            
            params = [internal_user_id] + fighter_card_ids
            await cursor.execute(cards_query, params)
            battle_cards = await cursor.fetchall()

            if not battle_cards:
                return []

            xp_rewards = []
            
            for card in battle_cards:
                # Skip max level cards
                if card['current_level'] == card['max_level']:
                    xp_rewards.append({
                        'card_name': card['card_name'],
                        'card_icon': card['card_icon'],
                        'xp_gained': 0,
                        'enhancer_active': False,
                        'new_level': card['current_level'],
                        'old_level': card['current_level'],
                        'new_xp': card['current_xp'],
                        'xp_cap': card['xp_cap'],
                        'level_up': False,
                        'stat_increases': {'attack': 0, 'health': 0, 'defence': 0, 'speed': 0},
                        'max_level_reached': True
                    })
                    continue

                # Calculate XP reward
                xp_reward = random.randint(20, 100)
                enhancer_active = False
                
                # Check for enhancer
                if card.get('id_enhancer'):
                    new_fight_count = card['fight_count'] - 1
                    xp_reward = int(xp_reward * 1.2)  # 20% bonus
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

                # Calculate new level and XP
                new_xp = card['current_xp'] + xp_reward
                new_level = card['current_level']
                level_up = False
                
                if new_xp >= card['xp_cap'] and new_level < card['max_level']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True

                # Generate stat increases
                stat_increases = {
                    'attack': random.randint(2, 4) if level_up else 0,
                    'health': random.randint(3, 5) if level_up else 0,
                    'defence': random.randint(1, 2) if level_up else 0,
                    'speed': random.randint(2, 4) if level_up else 0
                }

                # Update database
                if level_up:
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
                        current_statpoints = current_statpoints + 2
                    WHERE user_id = %s AND card_id = %s
                    """
                    await cursor.execute(
                        update_query,
                        (new_level, new_xp, 
                        new_base_stats['attack'], new_base_stats['health'], 
                        new_base_stats['defence'], new_base_stats['speed'],
                        new_stats['attack'], new_stats['health'], 
                        new_stats['defence'], new_stats['speed'],
                        internal_user_id, card['card_id'])
                    )
                else:
                    await cursor.execute(
                        "UPDATE user_cards SET current_xp = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, internal_user_id, card['card_id'])
                    )

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
                    'max_level_reached': new_level == card['max_level']
                })

            return xp_rewards

    def create_xp_progress_bar(self, percentage: float, length: int = 10) -> str:
        """Create XP progress bar"""
        filled = int((percentage / 100.0) * length)
        empty = length - filled
        
        bar = (self.green_square * filled) + (self.gray_square * empty)
        return f"{bar} **{percentage:.1f}%**"

# Attack Button UI Component
class AttackButton(ui.View):
    def __init__(self, ctx, user_fighter, opponent_fighter, user_name, opponent_name, battle_log, is_user_turn):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.user_fighter = user_fighter
        self.opponent_fighter = opponent_fighter
        self.user_name = user_name
        self.opponent_name = opponent_name
        self.battle_log = battle_log
        self.is_user_turn = is_user_turn
        self.attack_pressed = False
        
        self.attack_button.disabled = not is_user_turn

    @ui.button(label="Attack", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def attack_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This is not your battle!", ephemeral=True)
            return
            
        self.attack_pressed = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self.battle_log.append("⏰ Time's up! Attack automatically triggered.")
        self.attack_pressed = True
        self.stop()

# Main Fight Commands Cog
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

    @commands.command(name="fdbstatus", aliases=["fdbs", "fconnections"])
    async def db_status(self, ctx):
        """Display database connection status"""
        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send("You don't have permission to use this command.")
            return
        
        try:
            metrics = get_metrics()
            
            embed = discord.Embed(
                title="📊 Database Connection Status",
                description="Current status of database connection pool",
                color=discord.Color.blue()
            )
            
            embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
            embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
            embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
            embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
            embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
            
            if metrics["last_error"]:
                error_time = metrics["last_error_time"].strftime("%Y-%m-%d %H:%M:%S") if metrics["last_error_time"] else "Unknown"
                embed.add_field(
                    name="Last Error",
                    value=f"Time: {error_time}\nError: {metrics['last_error'][:100]}...",
                    inline=False
                )
            
            embed.timestamp = datetime.datetime.utcnow()
            await ctx.send(embed=embed)
                    
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="fight", aliases=["f", "fg"])
    @command_lock("fight")
    @command_cooldown(rate=1, per=5.0)
    async def fight(self, ctx):
        """Start a PvP fight"""
        try:
            await ctx.send("Initiating fight...")

            # Get user data
            user_data = await self.fight_helpers.get_user_data(ctx.author.id)
            if not user_data:
                await ctx.send("You need to be registered and have a team to use this command.")
                return

            if not user_data['fighters']:
                await ctx.send("You need at least one fighter card in your team to proceed with the fight.")
                return

            # Check fight status
            in_fight, fight_message = await self.fight_helpers.check_fight_status(ctx.author.id)
            if in_fight:
                await ctx.send(fight_message)
                return

            # Check fight timer
            can_fight, timer_message = await self.fight_helpers.check_fight_timer(ctx.author.id)
            if not can_fight:
                await ctx.send(timer_message)
                return

            # Set fight status
            await self.fight_helpers.set_fight_status(ctx.author.id, 1)

            # Get opponents
            opponents = await self.fight_helpers.get_random_opponents(ctx.author.id)
            if not opponents:
                await ctx.send("No suitable opponents found. Please try again later.")
                await self.fight_helpers.set_fight_status(ctx.author.id, 0)
                return

            # Select random opponent
            opponent_data = random.choice(opponents)

            # Apply charm effects
            user_data['fighters'] = await self.fight_helpers.apply_charm_effects(user_data['fighters'], user_data['user_id'])
            opponent_data['fighters'] = await self.fight_helpers.apply_charm_effects(opponent_data['fighters'], opponent_data['user_id'])

            # Start the fight
            await self.start_fight(ctx, user_data, opponent_data, is_challenge=False)

        except Exception as e:
            await self.fight_helpers.set_fight_status(ctx.author.id, 0)
            await self.handle_fight_error(ctx, e)

    @commands.command(name="challenge", aliases=["ch", "chl"])
    @command_lock("challenge")
    @command_cooldown(rate=1, per=10.0)
    async def challenge(self, ctx, opponent: discord.Member):
        """Challenge another user to a fight"""
        if opponent.id == ctx.author.id:
            await ctx.send("You can't challenge yourself! Find a worthy opponent.")
            return
        
        # Get user data
        user_data = await self.fight_helpers.get_user_data(ctx.author.id)
        if not user_data:
            await ctx.send("You need to be registered and have a team to use this command.")
            return
        
        if not user_data['fighters']:
            await ctx.send("You need at least one fighter card in your team to proceed with the challenge.")
            return
        
        # Get opponent data
        opponent_data = await self.fight_helpers.get_user_data(opponent.id)
        if not opponent_data:
            await ctx.send(f"{opponent.name} is not registered or doesn't have a team.")
            return
        
        if not opponent_data['fighters']:
            await ctx.send(f"{opponent.name} doesn't have any fighter cards in their team.")
            return
        
        # Create challenge embed
        challenge_embed = discord.Embed(
            title="🥊 Challenge Issued!",
            color=discord.Color.red()
        )
        challenge_embed.add_field(
            name="Challenger", 
            value=f"{ctx.author.mention}\n**Team Size:** {len(user_data['fighters'])} fighters", 
            inline=True
        )
        challenge_embed.add_field(
            name="Challenged", 
            value=f"{opponent.mention}\n**Team Size:** {len(opponent_data['fighters'])} fighters", 
            inline=True
        )
        challenge_embed.set_footer(
            text="You have 60 seconds to respond! ✅ Accept | ❌ Decline"
        )
        
        challenge_msg = await ctx.send(
            content=f"{opponent.mention}, you've been challenged to a fight!",
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
                    title="🏆 Challenge Accepted!",
                    description=f"{opponent.name} has accepted the challenge from {ctx.author.name}!",
                    color=discord.Color.green()
                )
                await ctx.send(embed=accept_embed)
                
                # Apply charm effects
                user_data['fighters'] = await self.fight_helpers.apply_charm_effects(user_data['fighters'], user_data['user_id'])
                opponent_data['fighters'] = await self.fight_helpers.apply_charm_effects(opponent_data['fighters'], opponent_data['user_id'])
                
                await self.start_fight(ctx, user_data, opponent_data, is_challenge=True)
            else:
                decline_embed = discord.Embed(
                    title="❌ Challenge Declined",
                    description=f"{opponent.name} has declined the challenge.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=decline_embed)
        
        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="⏰ Challenge Expired",
                description=f"{opponent.name} didn't respond in time.",
                color=discord.Color.dark_grey()
            )
            await ctx.send(embed=timeout_embed)

    async def fetch_user_with_retry(self, user_id, max_retries=3, delay=2):
        """Fetch user with retry logic"""
        for attempt in range(max_retries):
            try:
                return await self.bot.fetch_user(int(user_id))
            except discord.NotFound as e:
                if attempt == max_retries - 1:
                    print(f"Failed to fetch user {user_id} after {max_retries} attempts: {e}")
                    return None
                print(f"Attempt {attempt + 1} failed to fetch user {user_id}. Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
        return None

    async def start_fight(self, ctx, user_data, opponent_data, is_challenge):
        """Start the actual fight between two users"""
        try:
            # Sort fighters by position
            user_data['fighters'].sort(key=lambda x: x.get('position', float('inf')))
            opponent_data['fighters'].sort(key=lambda x: x.get('position', float('inf')))
            
            # Get user names
            user = await self.fetch_user_with_retry(user_data['user_authorid'])
            opponent = await self.fetch_user_with_retry(opponent_data['user_authorid'])
            
            user_name = user.name if user else f"Unknown User (ID: {user_data['user_authorid']})"
            opponent_name = opponent.name if opponent else f"Unknown Opponent (ID: {opponent_data['user_authorid']})"

            await ctx.send(f"Fight starting between {user_name} and {opponent_name}!")
            await asyncio.sleep(1)

            # Track fighter card IDs for XP rewards
            user_fighter_card_ids = [fighter['card_id'] for fighter in user_data['fighters']]

            await self.conduct_fight(ctx, user_data['fighters'], opponent_data['fighters'], 
                                   user_name, opponent_name, is_challenge, user_fighter_card_ids)

        except Exception as e:
            await self.handle_fight_error(ctx, e)
        finally:
            await self.fight_helpers.set_fight_status(ctx.author.id, 0)

    async def conduct_fight(self, ctx, user_fighters, opponent_fighters, user_name, opponent_name, is_challenge, fighter_card_ids):
        """Conduct the actual battle"""
        try:
            user_team = sorted(user_fighters, key=lambda x: x.get('position', float('inf')))
            opponent_team = sorted(opponent_fighters, key=lambda x: x.get('position', float('inf')))

            if not user_team or not opponent_team:
                await ctx.send("Error: One or both teams are empty. Cannot conduct the fight.")
                return

            # Initialize health
            for fighter in user_team + opponent_team:
                fighter['current_health'] = fighter['health_stat']
            
            # Speed tie resolution
            user_goes_first_in_tie = random.choice([True, False])

            # Create initial fight display
            embed, file = await self.create_fight_embed(user_team[0], opponent_team[0], user_name, opponent_name, is_challenge)
            fight_message = await ctx.send(file=file, embed=embed)
            battle_log = []

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

                    # Perform attack
                    attack_log, hit = self.battle_mechanics.perform_attack(attacker, defender, is_user_turn)
                    battle_log.extend(attack_log)
                    
                    # Update display
                    embed = await self.update_fight_embed(embed, user_fighter, opponent_fighter, user_name, opponent_name, battle_log)
                    await fight_message.edit(embed=embed)

                    # Check for defeat
                    if defender['current_health'] <= 0:
                        battle_log.append(self.battle_mechanics.get_defeat_message(defender['card_name'], is_user_turn))
                        
                        if is_user_turn:
                            opponent_team.pop(0)
                            if opponent_team:
                                battle_log.append("🆕 **Next opponent incoming!**")
                                battle_log.append(self.battle_mechanics.get_new_card_message(opponent_team[0]['card_name'], False))
                                opponent_fighter = opponent_team[0]
                            else:
                                break
                        else:
                            user_team.pop(0)
                            if user_team:
                                battle_log.append("🔄 **Next card incoming!**")
                                battle_log.append(self.battle_mechanics.get_new_card_message(user_team[0]['card_name'], True))
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
            await self.announce_winner(ctx, user_name, opponent_name, user_team, is_challenge, fighter_card_ids)

        except Exception as e:
            await self.handle_fight_error(ctx, e)

    async def announce_winner(self, ctx, user_name, opponent_name, user_team, is_challenge, fighter_card_ids):
        """Announce fight winner and handle rewards"""
        if user_team:
            # User won
            await ctx.send(f"**{user_name} has won the fight!**")
            
            # Update fight stats
            await self.fight_helpers.update_fight_stats(ctx.author.id, True, is_challenge)
            
            # Update quest progress
            quest_name = "Win 5 Challenges" if is_challenge else "Win 10 Fights"
            quest_completed = await self.fight_helpers.check_and_update_quest_progress(ctx.author.id, quest_name)
            
            if quest_completed:
                await ctx.send("🎉 Congratulations! You've completed a quest!")
            
            # Handle XP rewards
            user_data = await self.fight_helpers.get_user_data(ctx.author.id)
            if user_data:
                rewards = await self.xp_rewards.handle_xp_rewards(ctx.author.id, user_data['team_id'], fighter_card_ids)
                await self.send_rewards_embed(ctx, rewards)
        else:
            # User lost
            await ctx.send(f"**{opponent_name} has won the fight!**")
            await ctx.send(f"{user_name}, you've lost this battle. Better luck next time!")
            await self.fight_helpers.update_fight_stats(ctx.author.id, False, is_challenge)

    async def send_rewards_embed(self, ctx, rewards):
        """Send XP rewards embed"""
        if not rewards:
            return
            
        total_xp = sum(r['xp_gained'] for r in rewards)
        total_leveled = sum(1 for r in rewards if r['level_up'])
        
        embed = discord.Embed(
            title="🏆 Battle Rewards Summary 🏆",
            color=discord.Color.gold(),
            description=f"Total XP Gained: **{total_xp}**\nCards Leveled Up: **{total_leveled}**"
        )

        for reward in rewards:
            if reward['max_level_reached']:
                embed.add_field(
                    name=f"{reward['card_icon']} {reward['card_name']}: 🏆 **MAX LEVEL**",
                    value="No further XP or stat increases possible.",
                    inline=False
                )
                continue

            xp_percentage = (reward['new_xp'] / reward['xp_cap']) * 100
            progress_bar = self.xp_rewards.create_xp_progress_bar(xp_percentage)
            
            card_info = (
                f"{'🔥 Enhanced! ' if reward['enhancer_active'] else ''}"
                f"**+{reward['xp_gained']} XP**\n"
                f"{progress_bar} **{reward['new_xp']}/{reward['xp_cap']}**\n"
            )

            if reward['level_up']:
                level_up_info = (
                    f"✨ **LEVEL UP!** {reward['old_level']} → {reward['new_level']}\n"
                    f"📈 Stats Increased:\n"
                    f"```diff\n"
                    f"+ ATK: +{reward['stat_increases']['attack']}\n"
                    f"+ HP:  +{reward['stat_increases']['health']}\n"
                    f"+ DEF: +{reward['stat_increases']['defence']}\n"
                    f"+ SPD: +{reward['stat_increases']['speed']}\n"
                    f"```"
                )
                card_info += level_up_info

            embed.add_field(
                name=f"{reward['card_icon']} {reward['card_name']}",
                value=card_info,
                inline=False
            )

        embed.set_footer(text="Keep fighting to grow stronger! | Solo Leveling PVP")
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

    async def create_fight_embed(self, user_card, opponent_card, user_name, opponent_name, is_challenge=False):
        """Create initial fight embed"""
        embed = discord.Embed(title="⚔️ **Epic PVP Battle** ⚔️", color=discord.Color.dark_gold())
        embed.set_author(name="Solo Leveling Bot")

        embed.add_field(
            name=f"🔵 **{user_name}'s Fighter**",
            value=self.battle_mechanics.create_card_info(user_card, True, user_name, is_challenge),
            inline=True
        )
        embed.add_field(
            name=f"🔴 **{opponent_name}'s Fighter**",
            value=self.battle_mechanics.create_card_info(opponent_card, False, opponent_name, is_challenge),
            inline=True
        )

        embed.set_footer(text="May the strongest hunter prevail! 💪 | Solo Leveling PVP")

        file = await self.update_fight_image(user_card, opponent_card)
        embed.set_image(url="attachment://battle_cards.png")

        return embed, file

    async def update_fight_embed(self, embed, user_card, opponent_card, user_name, opponent_name, battle_log, is_challenge=False):
        """Update fight embed with current battle state"""
        embed.clear_fields()

        embed.add_field(
            name=f"🔵 **{user_name}'s Fighter**",
            value=self.battle_mechanics.create_card_info(user_card, True, user_name, is_challenge),
            inline=True
        )
        embed.add_field(
            name=f"🔴 **{opponent_name}'s Fighter**",
            value=self.battle_mechanics.create_card_info(opponent_card, False, opponent_name, is_challenge),
            inline=True
        )

        embed.add_field(
            name="🗒️ **Battle Log**",
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
        error_message = (
            f"🚨 <@818132161534230559> Error in fight system!\n"
            f"```\nError: {str(error)}\n"
            f"Channel: {ctx.channel.name} ({ctx.channel.id})\n"
            f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
            f"Command User: {ctx.author.name} ({ctx.author.id})\n```\n"
            f"Traceback:\n```python\n{traceback.format_exc()[:1500]}```"
        )
        
        try:
            support_channel = self.bot.get_channel(1336306672741908561)
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
            
            await ctx.send("An unexpected error occurred. Our support team has been notified and will investigate the issue.")
            
        except Exception as send_error:
            print(f"Failed to send error to support channel: {str(send_error)}")
            await ctx.send("An unexpected error occurred. Please try again later.")
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
            check_query = "SELECT team_id FROM team WHERE user_id = %s"
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
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970"]
        
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
        
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970"]:
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
            embed.timestamp = datetime.datetime.utcnow()
            
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
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.utcnow()
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
    @commands.command(name='sellreset', hidden=True)
    async def sell_reset(self, ctx, discord_id: str = None):
        """Reset sell_status table for a user (Admin only)"""
        # Check if the command invoker is authorized
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970"]
        
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
        
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970"]:
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
                    color=discord.Color.orange(),
                    timestamp=datetime.datetime.utcnow()
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
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970"]
        
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
        
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970"]:
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
                    color=discord.Color.purple(),
                    timestamp=datetime.datetime.utcnow()
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
        authorized_users = ["818132161534230559", "1384189515199021127", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970"]
        
        if str(ctx.author.id) not in authorized_users:
            return  # Silently ignore if not authorized
        
        # Check if restricted users are in the correct server
        if str(ctx.author.id) in ["1045687344277246022", "411676136037416970"]:
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
                    color=discord.Color.gold(),
                    timestamp=datetime.datetime.utcnow()
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
                    color=discord.Color.teal(),
                    timestamp=datetime.datetime.utcnow()
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
