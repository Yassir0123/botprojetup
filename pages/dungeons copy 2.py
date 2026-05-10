import discord
from discord.ext import commands
import random
from datetime import datetime
import time
import asyncio
import math
from typing import List, Dict, Union
import json
from enum import Enum
import logging
import os
import traceback
from utils.database import execute_query_with_retry, get_metrics
import functools

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

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

# Constants
DUNGEON_GATE_IMAGE = "https://staticg.sportskeeda.com/editor/2024/03/078be-17113304718546-1920.jpg"
# Constants
E_DUNGEON_IMAGE = "https://i.imgur.com/nLFDb29.jpeg"
C_DUNGEON_IMAGE = "https://i.imgur.com/5WfEsHQ.png"
S_DUNGEON_IMAGE = "https://i.imgur.com/XiqKiv3.png"
A_DUNGEON_IMAGE = "https://i.imgur.com/MFNSo3U.jpeg"
B_DUNGEON_IMAGE = "https://i.imgur.com/xG9ChWZ.jpeg"
D_DUNGEON_IMAGE = "https://i.imgur.com/yUhVAOt.jpeg"

class DungeonRank(Enum):
    E_RANK = ("E-Rank", "🟢", E_DUNGEON_IMAGE, "E", "E-Rank Dungeon", 0x4CAF50, "🗡️", "🏺", "💎", 1, "Beginner hunters start here. Goblins and weak monsters.")
    D_RANK = ("D-Rank", "🟡", D_DUNGEON_IMAGE, "D", "D-Rank Dungeon", 0xFFC107, "⚔️", "🏺🏺", "💎💎", 4, "Slightly stronger foes await the determined.")
    C_RANK = ("C-Rank", "🟠", C_DUNGEON_IMAGE, "C", "C-Rank Dungeon", 0xFF9800, "⚔️", "🏺🏺", "💎💎", 2, "Mid-tier challenges for experienced hunters.")
    B_RANK = ("B-Rank", "🔴", B_DUNGEON_IMAGE, "B", "B-Rank Dungeon", 0xF44336, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 5, "High-level threats require skill and strategy.")
    A_RANK = ("A-Rank", "🟣", A_DUNGEON_IMAGE, "A", "A-Rank Dungeon", 0x9C27B0, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 6, "Elite dungeons for the most capable hunters.")
    S_RANK = ("S-Rank", "⚫", S_DUNGEON_IMAGE, "S", "S-Rank Dungeon", 0x000000, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 3, "Legendary challenges that test the limits of power.")
# Create a Run Away button class
class RunAwayButton(discord.ui.Button):
    def __init__(self, dungeon_commands, ctx):
        super().__init__(style=discord.ButtonStyle.danger, label="Run Away", emoji="🏃")
        self.dungeon_commands = dungeon_commands
        self.ctx = ctx
        
    async def callback(self, interaction: discord.Interaction):
        # Only allow the original user to use the button
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
    
        # First acknowledge the interaction to prevent timeout
        await interaction.response.defer()
    
        # Set a flag on the dungeon_commands to indicate this dungeon has been fled
        self.dungeon_commands.fled_dungeons.add(self.ctx.author.id)
    
        # Disable the button to prevent multiple clicks
        self.disabled = True
        try:
            await interaction.message.edit(view=self.view)
        except:
            pass
    
        # Send a new message about fleeing
        await self.ctx.send("🏃‍♂️ **You have fled from the dungeon!**")
    
        # Handle the run away logic
        await self.dungeon_commands.handle_run_away(self.ctx, interaction.message)
        self.dungeon_commands.dungeons_in_progress.remove(self.ctx.author.id)
        # Delete the original dungeon message completely
        try:
            await interaction.message.delete()
        except:
            # If we can't delete, try to at least remove all content
            try:
                await interaction.message.edit(content="", embed=None, view=None, attachments=[])
            except:
                pass

# Create a view that contains the Run Away button
class DungeonView(discord.ui.View):
    def __init__(self, dungeon_commands, ctx):
        super().__init__(timeout=None)  # No timeout for dungeon buttons
        self.add_item(RunAwayButton(dungeon_commands, ctx))

class DungeonCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.blue_square = "<:blueshield:1385260867280703499>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"
        self.empty_square = "⬜"
        self.dungeons_in_progress = set()  # Track active dungeons
        self.fled_dungeons = set()  # Track dungeons that have been fled
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def calculate_dodge_chance(self, attacker_speed, defender_speed):
        """
        Calculate dodge chance based on relative speed difference.
        
        Args:
            attacker_speed (int): Speed stat of the attacker
            defender_speed (int): Speed stat of the defender
        
        Returns:
            float: Percentage chance of dodging between 0-30%
        """
        # Prevent division by zero and ensure positive speeds
        attacker_speed = max(1, attacker_speed)
        defender_speed = max(1, defender_speed)
        
        # Relative speed difference calculation
        speed_ratio = defender_speed / attacker_speed
        
        # Logarithmic scaling to create a balanced dodge chance
        # Ensures dodge chance is always non-negative and caps at 30%
        dodge_chance = min(30, max(0, 30 * (1 - math.log(speed_ratio + 1, 2))))
        
        return dodge_chance

    def calculate_critical_chance(self, attack_stat):
        """
        Calculate critical hit chance based on attack stat.
        
        Args:
            attack_stat (int): Attack statistic of the attacker
        
        Returns:
            float: Percentage chance of critical hit between 5-10%
        """
        return min(10, max(5, attack_stat / 100))

    def handle_dodge_attempt(self, attacker, defender):
        """
        Determine if an attack is successfully dodged.
        
        Args:
            attacker (dict): Attacker's card details
            defender (dict): Defender's card details
        
        Returns:
            tuple: (bool indicating dodge success, dodge message)
        """
        dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
        dodge_roll = random.random() * 100
        
        if dodge_roll < dodge_chance:
            dodge_message = f"🛡️ {defender['card_name']} swiftly dodged the attack!"
            return True, dodge_message
        
        return False, ""

    def handle_critical_attack(self, base_damage, attack_stat):
        """
        Determine if an attack is a critical hit.
        
        Args:
            base_damage (int): Calculated base damage
            attack_stat (int): Attacker's attack statistic
        
        Returns:
            tuple: (bool indicating critical hit, adjusted damage, critical message)
        """
        crit_chance = self.calculate_critical_chance(attack_stat)
        crit_roll = random.random() * 100
        
        if crit_roll < crit_chance:
            crit_multiplier = 1.5
            crit_message = f"💥 **CRITICAL HIT!** Damage amplified by {int((crit_multiplier - 1) * 100)}%!"
            return True, int(base_damage * crit_multiplier), crit_message
        
        return False, base_damage, ""

    # Modify the existing perform_combat_round method signature and add shield handling:

    # Modified perform_combat_round method - replace the shield handling section:
    # Fix 2: Modified perform_combat_round to properly handle shield
    def perform_combat_round(self, attacker, defender, is_user_turn, damage_multiplier=1.0):
        """
        Comprehensive combat round handling with dodge, critical mechanics, and boss special effects.
        """
        # Handle C-Rank boss enhanced dodge chance
        if hasattr(defender, 'dodge_bonus') and not is_user_turn:
            # Add bonus dodge chance for C-Rank boss
            base_dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
            enhanced_dodge_chance = base_dodge_chance + defender['dodge_bonus']
            dodge_roll = random.random() * 100
            
            if dodge_roll < enhanced_dodge_chance:
                dodge_message = f"🌪️ {defender['card_name']} swiftly dodged the attack with enhanced agility!"
                return [dodge_message]
        else:
            # Normal dodge check
            is_dodged, dodge_message = self.handle_dodge_attempt(attacker, defender)
            if is_dodged:
                return [dodge_message]
        
        # Base damage calculation with variance
        base_damage = random.randint(
            int(attacker['attack_stat'] * 0.45), 
            int(attacker['attack_stat'] * 0.55)
        )
        
        # Apply damage multiplier for special attacks
        base_damage = int(base_damage * damage_multiplier)
        
        # Critical hit check
        is_critical, final_damage, crit_message = self.handle_critical_attack(base_damage, attacker['attack_stat'])
        
        # Effective damage calculation
        effective_damage = self.calculate_effective_damage(final_damage, defender['defence_stat'])
            
            # Handle S-Rank boss shield - FIXED LOGIC
        # Handle S-Rank boss shield - FIXED LOGIC
        # Handle S-Rank boss shield - PERSISTENT DISPLAY
        shield_message = ""
        actual_hp_damage = 0

        # Check if this is user attacking S-Rank boss with shield
        if is_user_turn and 'shield_health' in defender and 'max_shield' in defender:
            if defender['shield_health'] > 0:
                # Shield is active, handle shield damage
                shield_damage = min(effective_damage, defender['shield_health'])
                defender['shield_health'] -= shield_damage
                remaining_damage = effective_damage - shield_damage
                
                if defender['shield_health'] <= 0:
                    # Shield broken, apply remaining damage to HP
                    defender['current_health'] = max(0, defender['current_health'] - remaining_damage)
                    actual_hp_damage = remaining_damage
                    shield_message = f"🛡️ Shield broken! ({shield_damage} shield + {remaining_damage} HP damage)"
                else:
                    # Shield absorbed all damage
                    actual_hp_damage = 0
                    shield_message = f"🛡️ Shield absorbed {shield_damage} damage!"
            else:
                # Shield already broken, normal HP damage
                defender['current_health'] = max(0, defender['current_health'] - effective_damage)
                actual_hp_damage = effective_damage
                shield_message = f"🛡️ Shield already broken - direct HP damage!"
        else:
            # Normal damage (no shield or monster attacking user)
            defender['current_health'] = max(0, defender['current_health'] - effective_damage)
            actual_hp_damage = effective_damage
        
        # Prepare attack messages
        messages = []
        if is_critical and crit_message:
            messages.append(crit_message)
        
        if shield_message:
            messages.append(shield_message)
        
        attack_message = self.get_attack_message(
            attacker['card_name'], 
            effective_damage if not shield_message else actual_hp_damage, 
            base_damage, 
            is_user_turn, 
            is_critical
        )
        messages.append(attack_message)
        
        return messages

    async def check_and_update_referral_status(self, user_id):
        """
        Helper function to check if user is a referee and update their referral status if requirements are met.
        
        Args:
            user_id: The user_id to check
        
        Returns:
            bool: True if status was updated, False otherwise
        """
        try:
            # Check if user is a referee with pending status
            referee_check_query = """
            SELECT r.referrer_id, r.referee_id, r.status, u.roll_total, u.c_dungeon_points
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referee_id = %s AND r.status = 'pending'
            """
            referee_result = await execute_query_with_retry(referee_check_query, (user_id,))
            
            if not referee_result:
                # User is not a pending referee
                return False
            
            referee_data = referee_result[0]
            roll_total = referee_data['roll_total'] or 0
            mid_dungeon_points = referee_data['c_dungeon_points'] or 0
            
            # Check if requirements are met (500 rolls + 1 mid dungeon)
            if roll_total >= 500 and mid_dungeon_points > 0:
                # Update status to completed
                update_query = """
                UPDATE referrals 
                SET status = 'completed', completed_at = %s
                WHERE referee_id = %s AND status = 'pending'
                """
                await execute_query_with_retry(update_query, (datetime.now(), user_id))
                
                print(f"Updated referral status to completed for user_id: {user_id}")
                return True
            
            return False
            
        except Exception as e:
            print(f"Error in check_and_update_referral_status: {e}")
            return False
    async def calculate_referral_bonus(self, user_id, base_amount):
        """
        Calculate referral bonus based on user's referrals
        Returns the modified amount with referral bonus applied
        """
        try:
            # Check if user is in activerefs table
            check_active_query = "SELECT user_id FROM activerefs WHERE user_id = %s"
            is_active_referrer = await execute_query_with_retry(check_active_query, (user_id,))
            
            if not is_active_referrer:
                # User is not an active referrer, return original amount
                return base_amount
                
            # Count total referrals
            count_refs_query = "SELECT COUNT(*) as total FROM referrals WHERE referrer_id = %s"
            refs_result = await execute_query_with_retry(count_refs_query, (user_id,))
            total_refs = refs_result[0]['total'] if refs_result else 0
            
            # Calculate percentage bonus based on referral count
            perc = 0
            if total_refs <= 3:
                # 5% per referral for first 3
                perc = total_refs * 5
            elif total_refs <= 9:
                # 15% base (for first 3) + 2.5% per additional referral
                additional_refs = total_refs - 3
                perc = 15 + (additional_refs * 2.5)
            else:
                # Cap at 30% (9 referrals)
                perc = 30
                
            # Apply percentage bonus
            bonus_amount = int(base_amount * (perc / 100))
            return base_amount + bonus_amount
            
        except Exception as e:
            logging.error(f"Error calculating referral bonus: {e}")
            # In case of error, return original amount
            return base_amount          
    def get_attack_message(self, attacker_name, effective_damage, base_damage, is_user_turn, is_critical):
        # Limit name length to prevent overflow
        attacker_name = attacker_name[:20] + '...' if len(attacker_name) > 20 else attacker_name
        
        # Emoji and base formatting
        emoji = "⚔️" if is_user_turn else "🛡️"
        
        # Critical hit formatting
        if is_critical:
            return f"💥 **CRITICAL HIT!** {emoji} {attacker_name} → {effective_damage} MASSIVE damage (Base: {base_damage})"
        else:
            return f"{emoji} {attacker_name} → {effective_damage} damage (Base: {base_damage})"

    async def get_user_id(self, discord_id):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (str(discord_id),))
        return result[0]['user_id'] if result else None

    async def get_inventory_id(self, user_id):
        query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['inventory_id'] if result else None

    async def get_team_id(self, user_id):
        query = "SELECT team_id FROM team WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['team_id'] if result else None

    async def get_solo_dungeons(self):
        query = "SELECT * FROM dungeon"
        return await execute_query_with_retry(query)

# In DungeonCommands class, inside the dungeon command method

    @commands.command(name="dungeon",aliases=["dg", "dgn"])
    async def dungeon(self, ctx):
        print("passed through")

        user_id = await self.get_user_id(ctx.author.id)
        print(user_id)
        if not user_id:
            await ctx.send("You need to be registered to use this command.")
            return
        
        # First check if user is in the in-memory tracking
        if ctx.author.id in self.dungeons_in_progress:
            await ctx.send("Please finish your current dungeon exploration before starting a new one.")
            return
        
        # Then check database state
        check_dungeon_status_query = "SELECT dungeon_current FROM user WHERE user_id = %s"
        dungeon_status_result = await execute_query_with_retry(check_dungeon_status_query, (user_id,))
        print(dungeon_status_result)
        
        if dungeon_status_result[0]['dungeon_current'] == 1:
            # User is marked as in a dungeon in the database but not in memory
            # This is a stale state - reset it and inform the user
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            logging.info(f"Fixed stale dungeon state for user {ctx.author.id}")
            await ctx.send("Your previous dungeon state was reset. You can now enter a new dungeon.")
            
        # Add user to in-progress set AFTER checking for stale state
        self.dungeons_in_progress.add(ctx.author.id)
        
        # Check cooldown
        query = "SELECT dungeon_timer FROM user WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        current_time = datetime.now()
        if result[0]['dungeon_timer'] is not None:
            last_dungeon_time = result[0]['dungeon_timer']
            time_diff = current_time - last_dungeon_time
            cooldown_minutes = 10
            
            if time_diff.total_seconds() < cooldown_minutes * 60:
                remaining_seconds = (cooldown_minutes * 60) - time_diff.total_seconds()
                remaining_minutes = int(remaining_seconds // 60)
                remaining_secs = int(remaining_seconds % 60)
                
                # Remove from in-progress before returning
                self.dungeons_in_progress.remove(ctx.author.id)
                
                if remaining_minutes > 0:
                    await ctx.send(f"⏳ You must wait {remaining_minutes}m {remaining_secs}s before entering another dungeon!")
                else:
                    await ctx.send(f"⏳ You must wait {remaining_secs}s before entering another dungeon!")
                return
        
        # Update dungeon timer
        update_timer_query = "UPDATE user SET dungeon_timer = %s WHERE user_id = %s"
        await execute_query_with_retry(update_timer_query, (current_time, user_id))
        
        # Double-check dungeon status again before proceeding
        check_dungeon_status_query = "SELECT dungeon_current FROM user WHERE user_id = %s"
        dungeon_status_result = await execute_query_with_retry(check_dungeon_status_query, (user_id,))
        if dungeon_status_result[0]['dungeon_current'] == 1:
            # Remove from in-progress before returning
            self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send("Please finish your current dungeon exploration before starting a new one.")
            return
            
        inventory_id = await self.get_inventory_id(user_id)
        team_id = await self.get_team_id(user_id)

        dungeon_cards_query = """
        SELECT dc.*, c.card_name
        FROM dungeon_cards dc
        JOIN card c ON dc.card_id = c.card_id
        WHERE dc.dungeon_id = %s
        ORDER BY dc.round
        """

        user_cards_query = """
        SELECT c.card_id, c.card_name, c.image, c.card_rank, c.xp_cap, c.max_level,
            uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, 
            uc.current_level, uc.current_xp, uc.id_enhancer, uc.fight_count, 
            uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
            uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count, tc.position
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        JOIN card c ON uc.card_id = c.card_id
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        ORDER BY tc.position
        """

        if not inventory_id or not team_id:
            # Remove from in-progress before returning
            self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send("You need to have an inventory and a team to enter a dungeon.")
            return

        all_dungeons_from_db = await self.get_solo_dungeons()
        if not all_dungeons_from_db:
            self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send("No dungeons available at the moment.")
            return

        # FIX: Reorder dungeons based on DungeonRank enum's desired sequence
        ordered_dungeons = []
        dungeon_map = {d['dungeon_id']: d for d in all_dungeons_from_db} # Map for quick lookup

        for rank_enum_member in DungeonRank:
            # rank_enum_member.value[9] holds the dungeon_id for that rank
            dungeon_id_for_rank = rank_enum_member.value[9] 
            if dungeon_id_for_rank in dungeon_map:
                ordered_dungeons.append(dungeon_map[dungeon_id_for_rank])
            else:
                logging.warning(f"Dungeon with ID {dungeon_id_for_rank} (for {rank_enum_member.value[0]}) not found in database.")

        # Use ordered_dungeons for all subsequent operations
        dungeons_to_display = ordered_dungeons
        if not dungeons_to_display:
            self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send("No dungeons available after ordering. Please check dungeon configurations.")
            return

        current_page = 0
        
        async def update_embed():
            # Get the specific dungeon data from the reordered list
            dungeon_data_from_db = dungeons_to_display[current_page]
            
            # Find the corresponding DungeonRank enum member using the dungeon_id from the DB data
            rank_enum = None
            for dr in DungeonRank:
                if dr.value[9] == dungeon_data_from_db['dungeon_id']:
                    rank_enum = dr
                    break
            if not rank_enum:
                logging.error(f"Could not find DungeonRank enum for dungeon_id: {dungeon_data_from_db['dungeon_id']}")
                return None # Or handle this error appropriately
            
            embed = discord.Embed(
                title=f"🏰 {dungeon_data_from_db['dungeon_name']}", # Dynamic name from DB
                color=discord.Color(rank_enum.value[5]) # From Enum
            )
            embed.set_image(url=rank_enum.value[2]) # From Enum
            
            # Add dungeon stats (all from Enum or helper methods using Enum)
            embed.add_field(name="📊 Rank", value=f"[{rank_enum.value[3]}]", inline=True)
            embed.add_field(name="☠️ Danger", value=self.format_danger_level(rank_enum), inline=True)
            embed.add_field(name="💰 Rewards", value=self.format_reward_level(rank_enum), inline=True)
            
            # Add more detailed information (all from Enum or helper methods using Enum)
            embed.add_field(name="🗺️ Environment", value=self.generate_environment_description(rank_enum), inline=False)
            embed.add_field(name="👹 Monsters", value=self.generate_monster_description(rank_enum), inline=False)
            embed.add_field(name="💎 Treasures", value=self.generate_treasure_description(rank_enum), inline=False)
            #embed.add_field(name="⚡ Power Suggestion", value=self.get_power_suggestion(rank_enum), inline=False)
            
            embed.set_footer(text=f"Gate {current_page + 1}/{len(dungeons_to_display)} | ⬅️ Previous | ➡️ Next | 🚪 Enter")
            return embed

        message = await ctx.send(embed=await update_embed())
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")
        await message.add_reaction("🚪")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️", "🚪"]

        while True:
            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
            except asyncio.TimeoutError:
                self.dungeons_in_progress.remove(ctx.author.id)
                update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                await ctx.send("The gates have sealed shut. Your hesitation has cost you this opportunity, hunter.")
                return

            if str(reaction.emoji) == "⬅️":
                current_page = (current_page - 1) % len(dungeons_to_display)
                await message.edit(embed=await update_embed())
            elif str(reaction.emoji) == "➡️":
                current_page = (current_page + 1) % len(dungeons_to_display)
                await message.edit(embed=await update_embed())
            elif str(reaction.emoji) == "🚪":
                check_dungeon_status_query = "SELECT dungeon_current FROM user WHERE user_id = %s"
                dungeon_status_result = await execute_query_with_retry(check_dungeon_status_query, (user_id,))
                if dungeon_status_result[0]['dungeon_current'] == 1:
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("Please finish your current dungeon exploration before starting a new one.")
                    return
                
                selected_dungeon = dungeons_to_display[current_page] # This is the DB row from the ordered list
                
                # Find the corresponding DungeonRank enum member using the dungeon_id from the DB data
                selected_rank_enum = None
                for dr in DungeonRank:
                    if dr.value[9] == selected_dungeon['dungeon_id']:
                        selected_rank_enum = dr
                        break
                if not selected_rank_enum:
                    await ctx.send("Error: Could not find matching dungeon rank. Please contact an administrator.")
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    self.dungeons_in_progress.remove(ctx.author.id)
                    return
                
                update_dungeon_current_query = "UPDATE user SET dungeon_current = 1 WHERE user_id = %s"
                await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                
                # Check for dungeon key (uses selected_dungeon from DB)
                key_query = """
                SELECT idk.quantiter 
                FROM inventory_dungeonkey idk
                JOIN dungeon d ON idk.dungeonkey_id = d.dungeonkey_id
                WHERE idk.inventory_id = %s AND d.dungeon_id = %s
                """
                key_result = await execute_query_with_retry(key_query, (inventory_id, selected_dungeon['dungeon_id']))
                if not key_result:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("You don't have the required key for this dungeon.")
                    return                
                key_quantity = key_result[0]['quantiter']

                # Check for fighter card (unchanged)
                fighter_query = """
                SELECT COUNT(*) as fighter_count
                FROM team_cards tc
                JOIN user_cards uc ON tc.card_id = uc.card_id
                JOIN card c ON uc.card_id = c.card_id
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                """
                fighter_result = await execute_query_with_retry(fighter_query, (team_id,))
                
                if fighter_result[0]['fighter_count'] == 0:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("Your team needs at least one Fighter card to enter the dungeon.")
                    return
                
                # Update or delete the dungeon key (uses selected_dungeon from DB)
                if key_quantity == 1:
                    delete_key_query = "DELETE FROM inventory_dungeonkey WHERE inventory_id = %s AND dungeonkey_id = %s"
                    await execute_query_with_retry(delete_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))
                else:
                    update_key_query = "UPDATE inventory_dungeonkey SET quantiter = quantiter - 1 WHERE inventory_id = %s AND dungeonkey_id = %s"
                    await execute_query_with_retry(update_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))

                # Get dungeon cards (uses selected_dungeon from DB)
                dungeon_cards = await execute_query_with_retry(dungeon_cards_query, (selected_dungeon['dungeon_id'],))
                print(dungeon_cards)
                if not dungeon_cards:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send(f"🚫 This dungeon ({selected_dungeon['dungeon_name']}) has no monsters configured! Please contact an administrator or ensure the `dungeon_cards` table is populated for this dungeon.")
                    return

                # Get user's fighter cards (unchanged)
                user_fighter_cards = await execute_query_with_retry(user_cards_query, (user_id, team_id))
                print(user_fighter_cards)
                if not user_fighter_cards:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("🚫 You need at least one 'Fighter' card in your team to enter a dungeon. Please add one to your team.")
                    return
                
                # Apply charm effects (unchanged)
                user_fighter_cards = await self.apply_charm_effects(user_id, user_fighter_cards)

                # Pass both the enum and the database data to start_dungeon
                await self.start_dungeon(ctx, selected_rank_enum, user_fighter_cards, dungeon_cards, selected_dungeon)
                return

            await message.remove_reaction(reaction, user)

    def format_dungeon_description(self, difficulty: DungeonRank) -> str:
        descriptions = {
            DungeonRank.E_RANK: "A beginner's trial, filled with the weakest of monsters. Ideal for new adventurers.",
            DungeonRank.D_RANK: "A step up from E-Rank, introducing slightly tougher challenges and more cunning foes.",
            DungeonRank.C_RANK: "A true test for seasoned hunters, where mid-tier monsters and complex traps await.",
            DungeonRank.B_RANK: "Only for the brave and well-prepared. Expect formidable enemies and strategic combat.",
            DungeonRank.A_RANK: "An elite challenge, pushing even the most skilled hunters to their limits against powerful adversaries.",
            DungeonRank.S_RANK: "The pinnacle of danger. Only legends dare to enter, facing mythical beasts and overwhelming power."
        }
        return descriptions[difficulty]

    def format_danger_level(self, rank_enum: DungeonRank) -> str:
        danger = ':star:' * min(rank_enum.value[9], 3) + '⚪' * (3 - min(rank_enum.value[9], 3))
        return danger

    def format_reward_level(self, rank_enum: DungeonRank) -> str:
        rewards = '💎' * min(rank_enum.value[9], 3) + '⚪' * (3 - min(rank_enum.value[9], 3))
        return rewards
    def generate_environment_description(self, rank_enum: DungeonRank) -> str:
        environments = {
            DungeonRank.E_RANK: "A misty forest with ancient trees and hidden paths. Goblin traps and crude fortifications dot the landscape.",
            DungeonRank.D_RANK: "Darker woods with more dangerous creatures lurking in the shadows.",
            DungeonRank.C_RANK: "A dark, cavernous space filled with giant webs. Abandoned equipment of unfortunate adventurers litters the ground.",
            DungeonRank.B_RANK: "Frozen caverns where the cold bites as much as the monsters within.",
            DungeonRank.A_RANK: "A colossal gate surrounded by glowing runes and pulsating with energy. The area hums with mana.",
            DungeonRank.S_RANK: "The ultimate challenge where reality itself seems to bend under immense magical pressure."
        }
        return environments[rank_enum]

    def manage_combat_log(self, battle_log, max_entries=5):
        """
        Manages the battle log by keeping only the most recent entries.
        Similar to the attack-button file's battle log system.
        """
        # Keep only the last max_entries
        if len(battle_log) > max_entries:
            return battle_log[-max_entries:]
        return battle_log

    def create_battle_log_field(self, battle_log, current_round):
        """
        Creates a single field for the battle log display
        """
        if not battle_log:
            return ("🗒️ **Battle Log**", "```css\nBattle starting...\n```", False)
        
        # Get the last 5 entries
        recent_log = self.manage_combat_log(battle_log, 5)
        log_text = '\n'.join(recent_log)
        
        # Ensure it doesn't exceed Discord's field limit
        if len(log_text) > 1000:
            log_text = log_text[-997] + "..."
        
        return ("🗒️ **Battle Log**", f"```css\n{log_text}\n```", False)
    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    async def update_embed_fields(self, embed, dungeon_message, round_log, current_round):
        """
        Updates the embed with the managed battle log
        """
        try:
            # Clear existing fields
            embed.clear_fields()
            
            # Create the battle log field
            field_name, field_value, field_inline = self.create_battle_log_field(round_log, current_round)
            embed.add_field(name=field_name, value=field_value, inline=field_inline)
            
            # Update the message
            await dungeon_message.edit(embed=embed)
        except Exception as e:
            logging.error(f"Error updating embed fields: {str(e)}")
            raise

    def generate_monster_description(self, rank_enum: DungeonRank) -> str:
        monsters = {
            DungeonRank.E_RANK: "Goblins, Hobgoblins",
            DungeonRank.D_RANK: "Stronger Goblins, Orc Scouts",
            DungeonRank.C_RANK: "Insects, Lizard Squad, Giant Spiders", 
            DungeonRank.B_RANK: "Ice Elementals, Frost Giants",
            DungeonRank.A_RANK: "Jackals, Strike Team B, High-Orcs",
            DungeonRank.S_RANK: "Ancient Dragons, Demon Lords, Mythical Beasts"
        }
        return monsters[rank_enum]

    def generate_treasure_description(self, rank_enum: DungeonRank) -> str:
        treasures = {
            DungeonRank.E_RANK: "Shadow Chests with basic rewards.",
            DungeonRank.D_RANK: "Multiple Shadow Chests with chance for Hunter Chest.",
            DungeonRank.C_RANK: "Hunter Chests and Shadow Chests with rare Monarch Chest chance.",
            DungeonRank.B_RANK: "Multiple Hunter and Shadow Chests with good Monarch Chest chance.",
            DungeonRank.A_RANK: "High chance for Monarch Chests and rare Random Capsules.",
            DungeonRank.S_RANK: "Guaranteed Monarch Chests, Random Capsules, and legendary jackpot chance."
        }
        return treasures[rank_enum]
    def get_power_suggestion(self, rank_enum: DungeonRank) -> str:
        power_suggestions = {
            DungeonRank.E_RANK: "💪 **Recommended Power:** 500-1,000",
            DungeonRank.D_RANK: "💪 **Recommended Power:** 1,000-2,000", 
            DungeonRank.C_RANK: "💪 **Recommended Power:** 2,000-4,000",
            DungeonRank.B_RANK: "💪 **Recommended Power:** 4,000-7,000",
            DungeonRank.A_RANK: "💪 **Recommended Power:** 7,000-12,000",
            DungeonRank.S_RANK: "💪 **Recommended Power:** 12,000+"
        }
        return power_suggestions[rank_enum]
    async def start_dungeon(self, ctx, rank_enum: DungeonRank, user_fighter_cards, dungeon_cards, dungeon_data):
        # Extract card IDs for the fighters that will participate in the dungeon
        user_card_ids = [card['card_id'] for card in user_fighter_cards]

        embed = discord.Embed(
            title=f"🏰 {dungeon_data['dungeon_name']}",
            description=f"{dungeon_data['dungeon_description']}\n\n{rank_enum.value[10]}",
            color=discord.Color(rank_enum.value[5])
        )

        embed.set_image(url=rank_enum.value[2])

        embed.add_field(name="📊 Rank", value=f"\n[{rank_enum.value[3]}]\n", inline=True)

        danger_level = '█' * rank_enum.value[9] + '░' * (6 - rank_enum.value[9])
        
        embed.add_field(name="☠️ Danger Level", value=f"\n{danger_level}\n", inline=True)

        rewards = '🌟' * min(rank_enum.value[9], 3)
        embed.add_field(name="💰 Rewards", value=f"\n{rewards}\n", inline=True)

        embed.add_field(name="📜 Your Mission", value=self.generate_mission(), inline=False)
        embed.add_field(name="🕒 Status", value="```diff\n+ Dungeon exploration in progress...\n```", inline=False)

        embed.set_footer(text="'I alone level up.' - Sung Jin-Woo")

        # Send the initial dungeon message without the Run Away button
        dungeon_message = await ctx.send(embed=embed)
        
        await asyncio.sleep(3)
        
        # FIX: Pass the correct parameters to dungeon_exploration
        # The method expects: ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards, user_card_ids
        await self.dungeon_exploration(ctx, dungeon_message, rank_enum, user_fighter_cards, dungeon_cards, user_card_ids)

    def truncate_log(self, log_entries: List[str], max_length: int = 1000) -> List[str]:
        """
        Truncate log entries to prevent Discord embed field length overflow.
        
        Args:
            log_entries (List[str]): List of log entries to truncate
            max_length (int): Maximum total length of log entries
        
        Returns:
            List[str]: Truncated log entries
        """
        truncated_log = []
        current_length = 0

        for entry in reversed(log_entries):
            entry_length = len(entry)
            
            # If adding this entry would exceed max_length, stop adding entries
            if current_length + entry_length > max_length:
                # Add an ellipsis to indicate truncation
                if not truncated_log or truncated_log[-1] != "...":
                    truncated_log.append(" ")
                break
            
            # Add entry to the beginning of the list
            truncated_log.insert(0, entry)
            current_length += entry_length

        return truncated_log

    def calculate_effective_damage(self, attack_damage: int, defense_stat: int) -> int:
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        return round(effective_damage)

    def create_health_bar(self, entity, is_monster=False, damage_taken=None):
        # Ensure health_stat is greater than 0 to avoid division by zero
        health_stat = max(1, entity['health_stat'])
        current_health = max(0, entity['current_health'])
        
        health_percentage = min(100, (current_health / health_stat) * 100)
        filled_blocks = min(10, max(0, int(health_percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_monster:
            filled_square = self.red_square if health_percentage > 0 else self.gray_square
        else:
            filled_square = self.green_square

        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"
        name = entity.get('card_name', 'Unknown')
        
        # Truncate name if too long to prevent layout issues
        if len(name) > 15:
            name = name[:12] + "..."
        
        # Add damage taken display if provided
        damage_text = f" (-{damage_taken})" if damage_taken else ""
        base_display = f"{name}: {health_bar} **({health_percentage:.1f}%){damage_text}** ({max(0, current_health)}/{health_stat} HP)"
        
        # ALWAYS show shield if the entity has shield attributes (even if shield is 0)
        if 'shield_health' in entity and 'max_shield' in entity:
            shield_display = self.create_shield_bar(entity['shield_health'], entity['max_shield'])
            return f"{base_display}\n{shield_display}"
        
        return base_display

    def create_shield_bar(self, current_shield, max_shield):
        """Create a shield bar for S-Rank boss using simple text"""
        if max_shield == 0:
            return "🛡️ Shield: ERROR (0/0)"
        
        shield_percentage = min(100, (current_shield / max_shield) * 100)
        filled_blocks = min(10, max(0, int(shield_percentage / 10)))
        empty_blocks = 10 - filled_blocks
        
        # Use simple characters for shield bar
        shield_bar = f"{'█' * filled_blocks}{'░' * empty_blocks}"
        
        # Compact shield display
        return f"🛡️ Shield: {shield_bar} {current_shield}/{max_shield} ({shield_percentage:.0f}%)"
    # Complete modified combat loop for the dungeon_exploration method
    # Replace the existing combat section with this complete implementation

    async def dungeon_exploration(self, ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards, user_card_ids=None):
        try:
            logging.info(f"Starting dungeon exploration for user {ctx.author.id} with difficulty {difficulty.value[0]}")
            
            # Add user to dungeons_in_progress at the start (but check if already added)
            if ctx.author.id not in self.dungeons_in_progress:
                self.dungeons_in_progress.add(ctx.author.id)
            
            dungeon_rounds = {}
            for card in dungeon_cards:
                round_num = card['round']
                if round_num not in dungeon_rounds:
                    dungeon_rounds[round_num] = []
                dungeon_rounds[round_num].append(card)
            
            total_rounds = len(dungeon_rounds)
            embed = dungeon_message.embeds[0]
            embed.clear_fields()
            original_user_team = user_fighter_cards.copy()
            
            # Now add the Run Away button after the initial dungeon info is shown
            view = DungeonView(self, ctx)
            embed.set_footer(text="Use the Run Away button to escape the dungeon")
            
            # Add the view to the message
            try:
                dungeon_message = await dungeon_message.edit(embed=embed, view=view)
            except discord.errors.NotFound:
                # If message was deleted, send a new one
                dungeon_message = await ctx.send(embed=embed, view=view)
            
            # Track if evasion pass was used
            evasion_pass_used = False
            rounds_to_play = list(range(1, total_rounds + 1))  # Create list of rounds to play
            
            # Continue with the rest of the exploration logic...
            round_index = 0
            while round_index < len(rounds_to_play):
                current_round = rounds_to_play[round_index]
                
                # Check if the user has fled the dungeon
                if ctx.author.id in self.fled_dungeons:
                    logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                    # Remove from fled_dungeons to clean up
                    self.fled_dungeons.remove(ctx.author.id)
                    return
                
                # Reset user team health at the start of each round
                user_team = []
                for card in original_user_team:
                    new_card = card.copy()
                    new_card['current_health'] = new_card['health_stat']
                    user_team.append(new_card)
                
                round_description = f"Round {current_round} of the {difficulty.value[4]}"
                round_log = [round_description]  # Reset log for each round
                embed.clear_fields()  # Clear any previous fields
                embed.add_field(name=f"⚔️ Round {current_round}", value=round_description, inline=False)
                
                # Check if message still exists before editing
                try:
                    await dungeon_message.edit(embed=embed)
                except discord.errors.NotFound:
                    # Message was deleted (likely due to fleeing)
                    logging.info(f"Dungeon message for user {ctx.author.id} not found, likely fled")
                    return
                
                await asyncio.sleep(1)
                round_monsters = dungeon_rounds[current_round].copy()
                fight_number = 1
                
                while round_monsters and user_team:
                    # Check if the user has fled the dungeon
                    if ctx.author.id in self.fled_dungeons:
                        logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                        # Remove from fled_dungeons to clean up
                        self.fled_dungeons.remove(ctx.author.id)
                        return
                    
                    # Break out of the loop if no monsters or user team left
                    if not round_monsters or not user_team:
                        break
                    
                    # Reset fight number control
                    current_fight_complete = False
                    monster = round_monsters[0]
                    user_card = user_team[0]
                    
                    if 'current_health' not in monster:
                        monster['current_health'] = monster['health_stat']
                    
                    if current_round == 4 and difficulty.value[9] == 3 and monster['card_id'] == 19:
                        if 'shield_health' not in monster:
                            shield_amount = int(monster['health_stat'] * 0.5)
                            monster['shield_health'] = shield_amount
                            monster['max_shield'] = shield_amount
                            print(f"DEBUG: Combat shield init - {monster['shield_health']}/{monster['max_shield']}")
                            # Add a message to the round log
                            round_log.append("🛡️ **Kargalgan's shield activates!** A powerful barrier surrounds the boss!")
                    
                    # Initialize turn counters for special abilities
                    user_turn_count = 0
                    monster_turn_count = 0
                    
                    # Apply boss special effects when combat starts
                    if current_round == 4:  # Boss round
                        boss_special_messages = await self.apply_boss_special_effects(monster, difficulty.value[9], current_round, round_log)
                        if boss_special_messages:
                            round_log.extend(boss_special_messages)
                    
                    fight_number = 1
                    
                    # Initial fight setup - only add fight header to battle log
                    round_log.append(f"\n**Fight {fight_number}: {user_card['card_name']} vs {monster['card_name']}**")
                    
                    try:
                        # Update embed with battle log and health bars separately
                        await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster)
                        
                        # Improved image loading
                        files = []
                        card_image_path = self.get_image_path(monster['image'])
                        if card_image_path and os.path.exists(card_image_path):
                            try:
                                card_filename = f"card_{os.path.basename(card_image_path)}"
                                card_file = discord.File(card_image_path, filename=card_filename)
                                files.append(card_file)
                                
                                embed.set_image(url=f"attachment://{card_filename}")
                                await dungeon_message.edit(embed=embed, attachments=files)
                            except discord.errors.NotFound:
                                # Message was deleted (likely due to fleeing)
                                logging.info(f"Dungeon message for user {ctx.author.id} not found, likely fled")
                                return
                            except Exception as e:
                                logging.error(f"Error loading card image: {e}")
                                try:
                                    await dungeon_message.edit(embed=embed)
                                except discord.errors.NotFound:
                                    return
                        else:
                            try:
                                await dungeon_message.edit(embed=embed)
                            except discord.errors.NotFound:
                                return
                    except discord.errors.NotFound:
                        # Message was deleted (likely due to fleeing)
                        logging.info(f"Dungeon message for user {ctx.author.id} not found, likely fled")
                        return
                    
                    await asyncio.sleep(1)
                    
                    # MODIFIED COMBAT LOOP WITH BOSS MECHANICS
                    while not current_fight_complete and monster['current_health'] > 0 and user_card['current_health'] > 0:
                        # Check if the user has fled the dungeon
                        if ctx.author.id in self.fled_dungeons:
                            logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                            # Remove from fled_dungeons to clean up
                            self.fled_dungeons.remove(ctx.author.id)
                            return
                        
                        # Battle timer
                        await asyncio.sleep(0.7)
                        
                        user_turn_count += 1
                        
                        # Handle user turn effects (D-Rank drain, B-Rank freeze)
                        user_effect_messages, card_defeated_or_skip = await self.handle_user_turn_effects(
                            user_card, monster, difficulty.value[9], current_round, user_turn_count, round_log
                        )
                        
                        if user_effect_messages:
                            round_log.extend(user_effect_messages)
                            # Update display with managed log and health bars
                            try:
                                await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster)
                                await dungeon_message.edit(embed=embed)
                            except discord.errors.NotFound:
                                return
                            await asyncio.sleep(0)
                        
                        # Check if user card was defeated by special effects (D-Rank drain)
                        if card_defeated_or_skip and user_card['current_health'] <= 0:
                            user_team.pop(0)
                            current_fight_complete = True
                            fight_number += 1
                            break
                        
                        # Check if turn should be skipped (B-Rank freeze) - only skip if card is alive
                        if card_defeated_or_skip and user_card['current_health'] > 0:
                            # Skip user turn, go directly to monster turn
                            pass
                        else:
                            # Only proceed with normal attack if card is alive and turn isn't skipped
                            if user_card['current_health'] > 0:
                                # Normal user attack
                                user_attack_messages = self.perform_combat_round(
                                    user_card, 
                                    monster, 
                                    is_user_turn=True
                                )
                                
                                # Add attack messages to log (NO health bars)
                                round_log.extend(user_attack_messages)
                                
                                # Check for boss health threshold effects after user attack
                                threshold_messages = self.handle_boss_health_threshold(monster, difficulty.value[9], current_round, round_log)
                                if threshold_messages:
                                    round_log.extend(threshold_messages)
                        
                        # Update display with managed log size and health bars
                        try:
                            await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster)
                            await dungeon_message.edit(embed=embed)
                        except discord.errors.NotFound:
                            return
                        
                        await asyncio.sleep(0.7)
                        
                        # Check if monster is defeated after user attack
                        if monster['current_health'] <= 0:
                            round_monsters.pop(0)
                            current_fight_complete = True
                            
                            if user_card['current_health'] <= 0:
                                user_team.pop(0)
                            
                            fight_number += 1
                            break
                        
                        if monster['current_health'] > 0:
                            monster_turn_count += 1
                            
                            # Handle boss turn effects (A-Rank White Flare, S-Rank Hymn of Blazing Fire)
                            boss_turn_messages, damage_multiplier = await self.handle_boss_turn_effects(
                                monster, difficulty.value[9], current_round, monster_turn_count, round_log
                            )
                            
                            if boss_turn_messages:
                                round_log.extend(boss_turn_messages)
                                # Update display with managed log and health bars
                                try:
                                    await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster)
                                    await dungeon_message.edit(embed=embed)
                                except discord.errors.NotFound:
                                    return
                                await asyncio.sleep(1)
                            
                            # Monster attack with potential damage multiplier
                            monster_attack_messages = self.perform_combat_round(
                                monster, 
                                user_card, 
                                is_user_turn=False,
                                damage_multiplier=damage_multiplier
                            )
                            
                            # Add attack messages to log (NO health bars)
                            round_log.extend(monster_attack_messages)
                            
                            # Check for boss health threshold effects after monster attack
                            threshold_messages = self.handle_boss_health_threshold(monster, difficulty.value[9], current_round, round_log)
                            if threshold_messages:
                                round_log.extend(threshold_messages)
                            
                            # Update display with managed log size and health bars
                            try:
                                await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster)
                                await dungeon_message.edit(embed=embed)
                            except discord.errors.NotFound:
                                return
                            
                            await asyncio.sleep(1)
                            
                            if user_card['current_health'] <= 0:
                                user_team.pop(0)
                                current_fight_complete = True
                                fight_number += 1
                                break
                
                # Round completion or game end logic
                if not round_monsters and current_round < total_rounds:
                    # Check if the user has fled the dungeon
                    if ctx.author.id in self.fled_dungeons:
                        logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                        self.fled_dungeons.remove(ctx.author.id)
                        return
                    
                    # Check for dungeon skip after round 1 (only if not already used)
                    if current_round == 1 and not evasion_pass_used:
                        should_skip = await self.handle_dungeon_skip_check(ctx, current_round)
                        if should_skip:
                            evasion_pass_used = True
                            round_log.append("⚡ **Evasion Pass activated!** Skipping directly to the boss round!")
                            
                            # Update embed with skip message
                            embed.clear_fields()
                            embed.add_field(
                                name="⚡ Evasion Pass Activated",
                                value="Skipping directly to the boss battle...",
                                inline=False
                            )
                            
                            try:
                                await dungeon_message.edit(embed=embed, attachments=[])
                            except discord.errors.NotFound:
                                return
                            
                            await asyncio.sleep(2)
                            
                            # Modify the rounds list to skip directly to round 4 (boss)
                            rounds_to_play = [4]  # Only play the boss round
                            round_index = -1  # Will be incremented to 0 at the end of this iteration
                            round_index += 1
                            continue
                    
                    # CLEAR ALL PREVIOUS FIELDS COMPLETELY
                    embed.clear_fields()
                    
                    # Add only a simple transition message
                    embed.add_field(
                        name=f"🚪 Round {current_round} Complete", 
                        value=f"Moving to Round {current_round + 1}...", 
                        inline=False
                    )
                    
                    # Clear previous image
                    embed.set_image(url=difficulty.value[2])
                    
                    # Update with cleared embed
                    try:
                        await dungeon_message.edit(embed=embed, attachments=[])
                    except discord.errors.NotFound:
                        return
                    
                    await asyncio.sleep(2)
                    
                    # IMPORTANT: Clear the embed again before starting next round
                    embed.clear_fields()
                    
                elif not round_monsters and current_round == total_rounds:
                    # Dungeon completed successfully - disable Run Away button
                    try:
                        # Check if the message has a view before trying to access it
                        if hasattr(dungeon_message, 'view') and dungeon_message.view:
                            for item in dungeon_message.view.children:
                                item.disabled = True
                            await dungeon_message.edit(view=dungeon_message.view)
                    except Exception as e:
                        logging.error(f"Error disabling Run Away button: {str(e)}")
                        # Continue with victory handling even if button disabling fails
                    
                    await self.handle_dungeon_victory(ctx, dungeon_message, embed, difficulty, user_card_ids)
                    return
                    
                elif not user_team:
                    # User team defeated - disable Run Away button
                    embed.set_image(url=difficulty.value[2])  # Clear any existing image
                    
                    try:
                        # Check if the message has a view before trying to access it
                        if hasattr(dungeon_message, 'view') and dungeon_message.view:
                            for item in dungeon_message.view.children:
                                item.disabled = True
                            await dungeon_message.edit(view=dungeon_message.view)
                    except Exception as e:
                        logging.error(f"Error disabling Run Away button: {str(e)}")
                        # Continue with defeat handling even if button disabling fails
                    
                    await self.handle_dungeon_defeat(ctx, dungeon_message, embed, difficulty)
                    
                    # Optional: If handle_dungeon_defeat method doesn't fully reset the message
                    try:
                        await dungeon_message.edit(embed=embed, attachments=[])
                    except discord.errors.NotFound:
                        # Message was deleted (likely due to fleeing)
                        pass
                    return
                
                # Move to next round
                round_index += 1
            
            # If we get here, make sure to clean up
            if ctx.author.id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(ctx.author.id)
            
            # Clean up fled_dungeons if needed
            if ctx.author.id in self.fled_dungeons:
                self.fled_dungeons.remove(ctx.author.id)
            
            # Ensure dungeon state is reset in database
            try:
                user_id = await self.get_user_id(ctx.author.id)
                if user_id:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            except Exception as cleanup_error:
                logging.error(f"Error during final cleanup: {str(cleanup_error)}")
                
        except Exception as e:
            logging.error(f"Critical error in dungeon exploration: {str(e)}")
            logging.error(traceback.format_exc())  # Added full traceback for more detailed error logging
            
            # Make sure to reset dungeon state on any error
            try:
                user_id = await self.get_user_id(ctx.author.id)
                if user_id:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    update_dungeon_timer_query = "UPDATE user SET dungeon_timer = NULL WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_timer_query, (user_id,))
            except Exception as reset_error:
                logging.error(f"Failed to reset dungeon state: {str(reset_error)}")
            
            # Remove from dungeons_in_progress
            if ctx.author.id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(ctx.author.id)
            
            # Clean up fled_dungeons if needed
            if ctx.author.id in self.fled_dungeons:
                self.fled_dungeons.remove(ctx.author.id)
            
            error_message = (
                f"🚨 <@818132161534230559> Error in Dungeon system!\n"
                f"```\nError: {str(e)}\n"
                f"Channel: {ctx.channel.name} ({ctx.channel.id})\n"
                f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
                f"Command User: {ctx.author.name} ({ctx.author.id})\n"
                f"User data: {json.dumps(user_fighter_cards, indent=2)}\n"
                f"Opponent data: {json.dumps(dungeon_cards, indent=2)}```\n"
                f"Traceback:\n```python\n{traceback.format_exc()}```"
            )
            
            try:
                # Send error to support channel
                support_channel = ctx.bot.get_channel(1336306672741908561)
                print("support")
                print(support_channel)
                if support_channel:
                    # Split message if it's too long
                    if len(error_message) > 2000:
                        print("message sent")
                        parts = [error_message[i:i+1990] for i in range(0, len(error_message), 1990)]
                        for i, part in enumerate(parts):
                            if i == 0:  # First part includes the ping
                                await support_channel.send(part)
                            else:
                                await support_channel.send(f"```{part}```")
                    else:
                        await support_channel.send(error_message)
                
                # Send generic message to user
                await ctx.send("An unexpected error occurred. Our support team has been notified and will investigate the issue.")
            
            except Exception as send_error:
                # If error channel sending fails, log to console
                print(f"Failed to send error to support channel: {str(send_error)}")
                print(error_message)
                # Still send generic message to user
                await ctx.send("An unexpected error occurred. Please try again later.")

    async def update_embed_with_health_bars(self, embed, dungeon_message, round_log, current_round, user_card, monster):
        """
        Updates the embed with the managed battle log and current health bars separately
        """
        try:
            # Clear existing fields
            embed.clear_fields()
            
            # Create the battle log field (attacks only)
            field_name, field_value, field_inline = self.create_battle_log_field(round_log, current_round)
            embed.add_field(name=field_name, value=field_value, inline=field_inline)
            
            # Create health status field (separate from battle log)
            health_status = f"**{user_card['card_name'][:15]}**\n{self.create_health_bar(user_card, is_monster=False)}\n\n"
            health_status += f"**{monster['card_name'][:15]}**\n{self.create_health_bar(monster, is_monster=True)}"
            
            embed.add_field(
                name="⚔️ **Combat Status**", 
                value=health_status, 
                inline=False
            )
            
        except Exception as e:
            logging.error(f"Error updating embed with health bars: {str(e)}")
            raise

    async def handle_dungeon_victory(self, ctx, dungeon_message, embed, difficulty, user_card_ids=None):
        try:
            user_id = await self.get_user_id(ctx.author.id)
            
            # Reset dungeon state immediately to avoid getting stuck
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            
            # Remove from in-memory tracking
            if ctx.author.id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(ctx.author.id)
            
            # Continue with the rest of the victory logic
            embed.clear_fields()  # Clear existing fields for a clean slate
            embed.add_field(name="🎉 Dungeon Cleared!", 
                        value="Congratulations! You've defeated the boss and cleared the dungeon!", 
                        inline=False)
            embed.set_image(url=difficulty.value[2])
            
            # Remove the Run Away button completely
            try:
                await dungeon_message.edit(embed=embed, view=None, attachments=[])
            except discord.errors.NotFound:
                # Message was deleted, create a new one
                dungeon_message = await ctx.send(embed=embed)
            except Exception as edit_error:
                logging.error(f"Error editing dungeon message: {str(edit_error)}")
                # Try to send a new message if editing fails
                try:
                    dungeon_message = await ctx.send(embed=embed)
                except:
                    pass
            
            inventory_id = await self.get_inventory_id(user_id)
            quest_message = await self.check_and_update_quest(user_id)
            
            if quest_message:
                await ctx.send(quest_message)
                
            # Process rewards
            await self.treasure_room(ctx, inventory_id, difficulty.value[9], f"{difficulty.value[0].lower()} gate", user_card_ids)
            await self.handle_rare_weapon_drop(ctx, difficulty.value[9])
        except Exception as e:
            logging.error(f"Error handling dungeon rewards: {str(e)}")
            traceback.print_exc()  # Print full traceback for debugging
            
            # Attempt to reset dungeon state even if other operations fail
            try:
                if user_id:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                
                if ctx.author.id in self.dungeons_in_progress:
                    self.dungeons_in_progress.remove(ctx.author.id)
            except:
                pass
            
            # Send a message to the user about the error
            await ctx.send("There was an error processing your dungeon rewards. Please contact an administrator.")

    async def handle_dungeon_defeat(self, ctx, dungeon_message, embed, difficulty):
        try:
            user_id = await self.get_user_id(ctx.author.id)
            
            # Reset dungeon state immediately to avoid getting stuck
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            
            # Remove from in-memory tracking
            if ctx.author.id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(ctx.author.id)
            
            # Clear any existing fields
            embed.clear_fields()
            
            # Add defeat field
            embed.add_field(name="❌ Dungeon Failed",
                            value=f"{ctx.author.mention} Your team has been defeated. Better luck next time!",
                            inline=False)
            
            # Calculate money loss
            money_loss = self.calculate_money_loss(difficulty)
            await self.update_user_currency(user_id, -money_loss, 1, 1)
            embed.add_field(name="💸 Currency Lost", 
                            value=f"You lost {money_loss} <:won:1304034228966658048> for failing the dungeon.", 
                            inline=False)
            
            # Clear the image 
            embed.set_image(url=difficulty.value[2])
            
            # Remove the Run Away button completely
            try:
                await dungeon_message.edit(embed=embed, view=None, attachments=[])
            except discord.errors.NotFound:
                # Message was deleted, create a new one
                await ctx.send(embed=embed)
            except Exception as edit_error:
                logging.error(f"Error editing dungeon message: {str(edit_error)}")
                # Try to send a new message if editing fails
                try:
                    await ctx.send(embed=embed)
                except:
                    pass
            
        except Exception as e:
            logging.error(f"Error handling dungeon failure: {str(e)}")
            traceback.print_exc()  # Print full traceback for debugging
            
            # Attempt to reset dungeon state even if other operations fail
            try:
                if user_id:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                
                if ctx.author.id in self.dungeons_in_progress:
                    self.dungeons_in_progress.remove(ctx.author.id)
            except:
                pass
            
            # Send a message to the user about the error
            await ctx.send("There was an error processing your dungeon defeat. Please contact an administrator.")

    async def handle_run_away(self, ctx, dungeon_message):
        try:
            user_id = await self.get_user_id(ctx.author.id)
            inventory_id = await self.get_inventory_id(user_id)

            # Check if the user has a return crystal (product_id = 13)
            check_crystal_query = """
            SELECT quantiter FROM inventory_product
            WHERE inventory_id = %s AND product_id = 13
            """
            crystal_result = await execute_query_with_retry(check_crystal_query, (inventory_id,))

            if not crystal_result or crystal_result[0]['quantiter'] <= 0:
                await ctx.send("You don't have a return crystal to escape the dungeon.")
                # Reset dungeon state anyway to prevent getting stuck
                update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                self.dungeons_in_progress.remove(ctx.author.id)
                return False
            
            # Use up one return crystal
            if crystal_result[0]['quantiter'] == 1:
                # Delete the crystal if it's the last one
                delete_crystal_query = """
                DELETE FROM inventory_product
                WHERE inventory_id = %s AND product_id = 13
                """
                await execute_query_with_retry(delete_crystal_query, (inventory_id,))
            else:
                # Reduce the quantity by 1
                update_crystal_query = """
                UPDATE inventory_product
                SET quantiter = quantiter - 1
                WHERE inventory_id = %s AND product_id = 13
                """
                await execute_query_with_retry(update_crystal_query, (inventory_id,))
            
            # Reset dungeon state
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            self.dungeons_in_progress.remove(ctx.author.id)

            # Send a clear message that the dungeon has been escaped
            await ctx.send("You used a return crystal to escape the dungeon. Your adventure has ended, but you'll still need to wait for the cooldown before entering another dungeon.")
            
            return True
        except Exception as e:
            logging.error(f"Error handling run away: {str(e)}")
            traceback.print_exc()
            
            # Ensure dungeon state is reset even if there's an error
            try:
                if user_id:
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    self.dungeons_in_progress.remove(ctx.author.id)
            except:
                pass
            
            await ctx.send("There was an error processing your escape. Please contact an administrator.")
            return False

    def calculate_money_loss(self, rank_enum):
        loss_amounts = {
            DungeonRank.E_RANK: 100,
            DungeonRank.D_RANK: 200,
            DungeonRank.C_RANK: 500,
            DungeonRank.B_RANK: 700,
            DungeonRank.A_RANK: 1000,
            DungeonRank.S_RANK: 1500
        }
        return loss_amounts.get(rank_enum, 100)
    async def check_premium_status(self, user_id):
        """Helper function to check and update premium status"""
        try:
            # Get premium info
            premium_query = """
            SELECT prenium, prenium_date, roll_cap 
            FROM user 
            WHERE user_id = %s
            """
            result = await execute_query_with_retry(premium_query, (user_id,))
            if not result:
                return 0, False  # No premium

            premium = result[0]['prenium']
            premium_date = result[0]['prenium_date']
            roll_cap = result[0]['roll_cap']

            if premium == 0:
                return 0, False  # No premium

            if premium_date:
                current_date = datetime.now()
                days_diff = (current_date - premium_date).days
                
                if days_diff > 30:
                    # Reset premium and adjust roll_cap based on premium level
                    roll_cap_reduction = min(premium, 3)  # Reduce by premium level (max 3)
                    update_query = """
                    UPDATE user 
                    SET prenium = 0, 
                        prenium_date = NULL, 
                        roll_cap = roll_cap - %s 
                    WHERE user_id = %s
                    """
                    await execute_query_with_retry(update_query, (roll_cap_reduction, user_id))
                    return 0, False  # Premium expired
                
                return premium, True  # Active premium
            
            return 0, False  # No premium date set
        except Exception as e:
            logging.error(f"Error checking premium status: {e}")
            return 0, False

    async def check_and_update_quest(self, user_id):
        # Check if the user has the "Conquer 1 dungeon" quest
        check_quest_query = """
        SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count, q.quest_id
        FROM user_quests uq
        JOIN quest q ON uq.quest_id = q.quest_id
        WHERE uq.user_id = %s AND q.quest_name = 'Conquer 1 dungeon'
        """
        quest_result = await execute_query_with_retry(check_quest_query, (user_id,))

        if quest_result:
            quest = quest_result[0]
            if quest['completion_status'] == 0:
                new_count = quest['current_count'] + 1
                update_query = """
                UPDATE user_quests
                SET current_count = %s, completion_status = %s
                WHERE userquests_id = %s
                """
                completion_status = 1 if new_count >= quest['required_count'] else 0
                await execute_query_with_retry(update_query, (new_count, completion_status, quest['userquests_id']))

                if completion_status == 1:
                    inventory_id = await self.get_inventory_id(user_id)
                    
                    # Update or insert into inventory_chesttable
                    check_chest_query = """
                    SELECT * FROM inventory_chest
                    WHERE inventory_id = %s AND chest_id = 1
                    """
                    chest_result = await execute_query_with_retry(check_chest_query, (inventory_id,))
                    
                    if chest_result:
                        # If chest exists, update quantity
                        update_chest_query = """
                        UPDATE inventory_chest
                        SET quantiter = quantiter + 1 
                        WHERE inventory_id = %s AND chest_id = 1
                        """
                        await execute_query_with_retry(update_chest_query, (inventory_id,))
                    else:
                        # If chest doesn't exist, insert new row
                        insert_chest_query = """
                        INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                        VALUES (%s, 1, 1)
                        """
                        await execute_query_with_retry(insert_chest_query, (inventory_id,))
                    
                    return f"Quest 'Conquer 1 dungeon' completed! You have received a reward."
                else:
                    return f"Quest 'Conquer 1 dungeon' progress: {new_count}/{quest['required_count']}"

        return None

    def generate_round_description(self, round_number):
        descriptions = [
            "The air grows thicker as you delve deeper into the dungeon.",
            "Shadows dance on the walls, hinting at the dangers that lie ahead.",
            "The echoes of your footsteps are drowned out by distant, ominous sounds.",
            "You feel a chill run down your spine as you prepare for the next challenge.",
            "The very stones beneath your feet seem to pulse with an ancient, dark energy."
        ]
        return random.choice(descriptions)

    def generate_dungeon_name(self, difficulty: DungeonRank) -> str:
        return f"{difficulty.value[0]} Dungeon of Shadows"

    def generate_dungeon_description(self, difficulty: DungeonRank) -> str:
        return "The shadows dance around you as you step into the unknown. Every sound makes you tense, every step feels heavier than the last."

    def generate_mission(self) -> str:
        return "Your objective is to complete the mission and take down the boss. Only by succeeding will you claim the rewards awaiting you."

    def generate_stage_description(self, stage: str) -> str:
        return f"You've arrived at the {stage}. Prepare yourself for what's to come."

    async def treasure_room(self, ctx, inventory_id, dungeon_rank, dungeon_name, user_card_ids=None):
        # Get chest rewards based on dungeon rank
        rewards = {}
        
        # Get user_id from inventory_id
        user_query = """
        SELECT user_id 
        FROM inventory 
        WHERE inventory_id = %s
        """
        user_result = await execute_query_with_retry(user_query, (inventory_id,))
        user_id = user_result[0]['user_id']
        premium_level, is_premium_active = await self.check_premium_status(user_id)
        
        def calculate_premium_bonus(premium_level, base_amount):
            """Calculate bonus based on premium level"""
            bonus_multipliers = {
                1: 1.10,  # 10% bonus
                2: 1.20,  # 20% bonus
                3: 1.30   # 30% bonus
            }
            multiplier = bonus_multipliers.get(premium_level, 1.0)
            return int(base_amount * multiplier)
        
        # Reward logic based on dungeon rank
        if dungeon_rank == 1:  # E-Rank
            # 1 shadow chest
            rewards[1] = {'chest_id': 1, 'quantity': 1}
            # 10% chance for hunter chest
            if random.random() < 0.1:
                rewards[2] = {'chest_id': 2, 'quantity': 1}
            # Update low dungeon points
            update_points_query = "UPDATE user SET e_dungeon_points = e_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            
        elif dungeon_rank == 4:  # D-Rank
            # 1-3 shadow chests
            shadow_count = random.randint(1, 3)
            rewards[1] = {'chest_id': 1, 'quantity': shadow_count}
            # 60% chance for hunter chest
            if random.random() < 0.6:
                rewards[2] = {'chest_id': 2, 'quantity': 1}
            # Update low dungeon points
            update_points_query = "UPDATE user SET d_dungeon_points = d_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            
        elif dungeon_rank == 2:  # C-Rank
            # 1 hunter chest + 1-2 shadow chests
            rewards[2] = {'chest_id': 2, 'quantity': 1}
            shadow_count = random.randint(1, 2)
            rewards[1] = {'chest_id': 1, 'quantity': shadow_count}
            # 5% chance for monarch chest
            if random.random() < 0.05:
                rewards[3] = {'chest_id': 3, 'quantity': 1}
            # Update medium dungeon points
            update_points_query = "UPDATE user SET c_dungeon_points = c_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            await self.check_and_update_referral_status(user_id)
            
        elif dungeon_rank == 5:  # B-Rank
            # 1-2 hunter chests + 2-3 shadow chests
            hunter_count = random.randint(1, 2)
            shadow_count = random.randint(2, 3)
            rewards[2] = {'chest_id': 2, 'quantity': hunter_count}
            rewards[1] = {'chest_id': 1, 'quantity': shadow_count}
            # 40% chance for monarch chest
            if random.random() < 0.4:
                rewards[3] = {'chest_id': 3, 'quantity': 1}
            # Update medium dungeon points
            update_points_query = "UPDATE user SET b_dungeon_points = b_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            await self.check_and_update_referral_status(user_id)
            
        elif dungeon_rank == 6:  # A-Rank
            # 80% chance for monarch chest
            hunter_count = random.randint(2, 3)
            rewards[2] = {'chest_id': 2, 'quantity': hunter_count}
            if random.random() < 0.8:
                rewards[3] = {'chest_id': 3, 'quantity': 1}
            # 1-2 random capsules
            capsule_count = random.randint(1, 2)
            capsule_id = random.choice([4, 5, 6, 7])  # Random capsule type
            await self.add_product_to_inventory(inventory_id, capsule_id, capsule_count)
            # Update high dungeon points
            update_points_query = "UPDATE user SET a_dungeon_points = a_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            
        elif dungeon_rank == 3:  # S-Rank
            jackpot_roll = random.random()
            if jackpot_roll < 0.005:  # 0.5% chance for jackpot
                # Case 2: Jackpot
                capsule_count = random.randint(10, 20)
                capsule_id = random.choice([4, 5, 6, 7])
                await self.add_product_to_inventory(inventory_id, capsule_id, capsule_count)
            else:
                # Case 1: Normal rewards
                # 1-2 monarch chests
                monarch_count = random.randint(1, 2)
                rewards[3] = {'chest_id': 3, 'quantity': monarch_count}
                # 1-3 random capsules
                capsule_count = random.randint(1, 3)
                capsule_id = random.choice([4, 5, 6, 7])
                await self.add_product_to_inventory(inventory_id, capsule_id, capsule_count)
            # Update high dungeon points
            update_points_query = "UPDATE user SET s_dungeon_points = s_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
        
        # Update inventory_chest for chest rewards
        for chest_entry in rewards.values():
            # Check if chest already exists in inventory
            check_query = "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            existing_chest = await execute_query_with_retry(check_query, (inventory_id, chest_entry['chest_id']))
            
            if existing_chest:
                # Update quantity if chest exists
                update_query = "UPDATE inventory_chest SET quantiter = quantiter + %s WHERE inventory_id = %s AND chest_id = %s"
                await execute_query_with_retry(update_query, (chest_entry['quantity'], inventory_id, chest_entry['chest_id']))
            else:
                # Insert new chest entry
                insert_query = "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, chest_entry['chest_id'], chest_entry['quantity']))
        
        # Calculate money reward from dungeon_moneycap
        money_query = "SELECT dungeon_moneycap FROM dungeon WHERE dungeon_id = %s"
        money_result = await execute_query_with_retry(money_query, (dungeon_rank,))
        money_cap = money_result[0]["dungeon_moneycap"]
        money_reward = await self.calculate_money_reward(ctx, money_cap, 0)
        gamble_reward = await self.calculate_money_reward(ctx, money_cap, 1)
        
        if is_premium_active:
            money_reward = calculate_premium_bonus(premium_level, money_reward)
            gamble_reward = calculate_premium_bonus(premium_level, gamble_reward)
        
        # Handle guild contribution and update user currency
        final_money_reward = await self.handle_guild_contribution(ctx, user_id, money_reward)
        await self.update_user_currency(user_id, gamble_reward, 0, 1)
        final_gamble_reward = gamble_reward
        
        # Handle XP rewards
        team_id = await self.get_team_id(user_id)
        await self.handle_xp_rewards(ctx, user_id, team_id, dungeon_rank, final_money_reward, final_gamble_reward, rewards, user_card_ids)
        
        return rewards
    async def handle_xp_rewards(self, ctx, user_id, team_id, dungeon_id, money_reward, gamble_reward, rewards, fighter_card_ids=None):
        # Get the user's team cards
        if fighter_card_ids:
            # Get the user's cards that participated in the dungeon
            placeholders = ', '.join(['%s'] * len(fighter_card_ids))
            team_cards_query = f"""
            SELECT c.card_id, c.card_name, c.type_card, c.card_icon, c.card_rank, c.xp_cap, c.max_level, 
                uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat, 
                uc.speed_stat, uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, 
                uc.charm_speed, uc.charm_health, uc.current_statpoints,
                uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            WHERE uc.user_id = %s AND uc.card_id IN ({placeholders}) AND c.type_card='Fighter'
            """
            params = [user_id] + fighter_card_ids
            team_cards = await execute_query_with_retry(team_cards_query, params)
        else:
            # Fallback to original method if no card IDs provided
            team_cards_query = """
            SELECT tc.*, c.card_name, c.type_card, c.card_icon, c.card_rank, c.xp_cap, c.max_level, 
                uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat, 
                uc.speed_stat, uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, 
                uc.charm_speed, uc.charm_health, uc.current_statpoints,
                uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            JOIN card c ON tc.card_id = c.card_id
            WHERE tc.team_id = %s AND c.type_card='Fighter'
            """
            team_cards = await execute_query_with_retry(team_cards_query, (user_id, team_id))
        
        # Check for support cards in the team
        support_query = """
        SELECT COUNT(*) as support_count
        FROM team_cards tc
        JOIN card c ON tc.card_id = c.card_id
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        WHERE tc.team_id = %s AND c.type_card = 'Support'
        """
        support_result = await execute_query_with_retry(support_query, (user_id, team_id))
        has_support_cards = support_result[0]['support_count'] > 0 if support_result else False

        # Get support card boosts if there are any support cards
        support_boosts = None
        if has_support_cards:
            support_boosts = await self.calculate_total_support_boosts(team_id, user_id)
        
        xp_rewards = []
        total_xp_gained = 0
        total_cards_leveled = 0
        
        for card in team_cards:
            # Skip XP and stat increases for max-level cards
            if card['current_level'] == card['max_level']:
                xp_rewards.append({
                    'card_name': card['card_name'],
                    'xp_gained': 0,
                    'new_level': card['current_level'],
                    'new_xp': 0,
                    'level_up': False,
                    'enhancer_active': False,
                    'old_level': card['current_level'],
                    'xp_cap': card['xp_cap'],
                    'attack_increase': 0,
                    'health_increase': 0,
                    'defence_increase': 0,
                    'speed_increase': 0,
                    'card_icon': card['card_icon'],
                    'new_attack': card['attack_stat'],
                    'new_defence': card['defence_stat'],
                    'new_health': card['health_stat'],
                    'new_speed': card['speed_stat'],
                    'max_level_reached': True
                })
                continue

            xp_reward = random.randint(20, 100)
                        
            if dungeon_id == 1:  # Dungeon 1 expects card rank 7
                if card['card_rank'] == 7:
                    xp_reward = xp_reward
                elif card['card_rank'] < 7:
                    xp_reward = xp_reward // 2
                else:
                    xp_reward = xp_reward * 2

            elif dungeon_id == 2:  # Dungeon 2 expects card rank 5
                if card['card_rank'] == 5:
                    xp_reward = xp_reward
                elif card['card_rank'] < 5:
                    xp_reward = xp_reward // 2
                else:
                    xp_reward = xp_reward * 2

            elif dungeon_id == 3:  # Dungeon 3 expects card rank 2
                if card['card_rank'] == 2:
                    xp_reward = xp_reward
                elif card['card_rank'] < 2:
                    xp_reward = xp_reward // 2
                else:
                    xp_reward = xp_reward * 2

            elif dungeon_id == 4:  # Dungeon 4 expects card rank 6
                if card['card_rank'] == 6:
                    xp_reward = xp_reward
                elif card['card_rank'] < 6:
                    xp_reward = xp_reward // 2
                else:
                    xp_reward = xp_reward * 2

            elif dungeon_id == 5:  # Dungeon 5 expects card rank 4
                if card['card_rank'] == 4:
                    xp_reward = xp_reward
                elif card['card_rank'] < 4:
                    xp_reward = xp_reward // 2
                else:
                    xp_reward = xp_reward * 2

            elif dungeon_id == 6:  # Dungeon 6 expects card rank 3
                if card['card_rank'] == 3:
                    xp_reward = xp_reward
                elif card['card_rank'] < 3:
                    xp_reward = xp_reward // 2
                else:
                    xp_reward = xp_reward * 2
            enhancer_active = False
            if card['id_enhancer'] is not None and card['id_enhancer'] != 0:
                enhancer_active = True
                xp_reward = int(xp_reward * 1.2)  # 20% XP boost
                new_fight_count = card['fight_count'] - 1
                if new_fight_count <= 0:
                    update_enhancer_query = """
                    UPDATE user_cards
                    SET fight_count = 0, id_enhancer = NULL
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(update_enhancer_query, (user_id, card['card_id']))
                else:
                    update_enhancer_query = """
                    UPDATE user_cards
                    SET fight_count = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(update_enhancer_query, (new_fight_count, user_id, card['card_id']))
                    
            total_xp_gained += xp_reward
            new_xp = card['current_xp'] + xp_reward
            new_level = card['current_level']
            
            # Handle level up
            level_up = False
            if new_xp >= card['xp_cap']:
                if new_level < card['max_level']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True
                    total_cards_leveled += 1
                else:
                    new_xp = 0  # XP stays at 0 if at level cap

            # Generate stat increases if leveled up
            attack_increase = random.randint(2, 4) if level_up else 0
            health_increase = random.randint(3, 5) if level_up else 0
            defence_increase = random.randint(1, 2) if level_up else 0
            speed_increase = random.randint(2, 4) if level_up else 0
            stat_increase = 2 if level_up else 0

            # Check if card has equipment
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card['card_id'])

            # SCENARIO HANDLING:
            # 1. No support in team and no weapon
            if not has_support_cards and not has_equipment:
                # Apply stat buff directly to both base stats and current stats
                new_base_stats = {
                    'attack': card['base_attack'] + attack_increase,
                    'health': card['base_health'] + health_increase,
                    'defence': card['base_defence'] + defence_increase,
                    'speed': card['base_speed'] + speed_increase
                }
                
                new_stats = {
                    'attack': card['attack_stat'] + attack_increase,
                    'health': card['health_stat'] + health_increase,
                    'defence': card['defence_stat'] + defence_increase,
                    'speed': card['speed_stat'] + speed_increase
                }
                
                # Update database
                if level_up:
                    update_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        power_stat = attack_stat+health_stat+defence_stat+speed_stat,
                        current_statpoints = current_statpoints + 2
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_query,
                        (new_level, new_xp, 
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_stats['attack'], new_stats['health'], new_stats['defence'], new_stats['speed'],
                        user_id, card['card_id'])
                    )
                else:
                    # Just update XP if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, user_id, card['card_id'])
                    )
            
            # 2. Support cards but no equipment
            elif has_support_cards and not has_equipment:
                # Reset to base stats first
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE user_id = %s AND card_id = %s
                """
                await execute_query_with_retry(reset_query, (user_id, card['card_id']))
                
                # Apply stat buff to both base stats and current stats
                new_base_stats = {
                    'attack': card['base_attack'] + attack_increase,
                    'health': card['base_health'] + health_increase,
                    'defence': card['base_defence'] + defence_increase,
                    'speed': card['base_speed'] + speed_increase
                }
                
                # Update base stats and level info
                if level_up:
                    update_base_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        current_statpoints = current_statpoints + 2
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_base_query,
                        (new_level, new_xp, 
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        user_id, card['card_id'])
                    )
                else:
                    # Just update XP if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, user_id, card['card_id'])
                    )
                
                # Now apply support boosts to current stats
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                
                # Get the updated stats after support boosts
                updated_stats_query = """
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                """
                updated_stats = await execute_query_with_retry(updated_stats_query, (user_id, card['card_id']))
                
                new_stats = {
                    'attack': updated_stats[0]['attack_stat'],
                    'health': updated_stats[0]['health_stat'],
                    'defence': updated_stats[0]['defence_stat'],
                    'speed': updated_stats[0]['speed_stat']
                }
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE user_id = %s AND card_id = %s",
                    (user_id, card['card_id'])
                )
            
            # 3. No support cards but has equipment
            elif not has_support_cards and has_equipment:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + attack_increase,
                    'health': true_base_stats['health'] + health_increase,
                    'defence': true_base_stats['defence'] + defence_increase,
                    'speed': true_base_stats['speed'] + speed_increase
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Update base stats and current stats with the buff
                if level_up:
                    update_base_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        current_statpoints = current_statpoints + 2
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_base_query,
                        (new_level, new_xp, 
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        user_id, card['card_id'])
                    )
                else:
                    # Just update XP if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, user_id, card['card_id'])
                    )
                
                # Get the updated stats
                updated_stats_query = """
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                """
                updated_stats = await execute_query_with_retry(updated_stats_query, (user_id, card['card_id']))
                
                new_stats = {
                    'attack': updated_stats[0]['attack_stat'],
                    'health': updated_stats[0]['health_stat'],
                    'defence': updated_stats[0]['defence_stat'],
                    'speed': updated_stats[0]['speed_stat']
                }
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE user_id = %s AND card_id = %s",
                    (user_id, card['card_id'])
                )
            
            # 4. Has both support cards and equipment
            else:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + attack_increase,
                    'health': true_base_stats['health'] + health_increase,
                    'defence': true_base_stats['defence'] + defence_increase,
                    'speed': true_base_stats['speed'] + speed_increase
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Reset current stats to true base stats + stat increases (without equipment boosts)
                reset_query = """
                UPDATE user_cards
                SET attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s
                WHERE user_id = %s AND card_id = %s
                """
                await execute_query_with_retry(
                    reset_query,
                    (new_true_base_stats['attack'], new_true_base_stats['health'], 
                    new_true_base_stats['defence'], new_true_base_stats['speed'],
                    user_id, card['card_id'])
                )
                
                # Update base stats and level info
                if level_up:
                    update_base_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        current_statpoints = current_statpoints + 2
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_base_query,
                        (new_level, new_xp, 
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        user_id, card['card_id'])
                    )
                else:
                    # Just update XP if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, user_id, card['card_id'])
                    )
                
                # Apply equipment boosts to current stats
                await self.apply_equipment_boosts(user_id, card['card_id'], equipment_boosts)
                
                # Then apply support boosts
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                
                # Get the updated stats after all boosts
                updated_stats_query = """
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                """
                updated_stats = await execute_query_with_retry(updated_stats_query, (user_id, card['card_id']))
                
                new_stats = {
                    'attack': updated_stats[0]['attack_stat'],
                    'health': updated_stats[0]['health_stat'],
                    'defence': updated_stats[0]['defence_stat'],
                    'speed': updated_stats[0]['speed_stat']
                }
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE user_id = %s AND card_id = %s",
                    (user_id, card['card_id'])
                )

            xp_rewards.append({
                'card_name': card['card_name'],
                'xp_gained': xp_reward,
                'new_level': new_level,
                'new_xp': new_xp,
                'enhancer_active': enhancer_active,
                'level_up': level_up,
                'old_level': card['current_level'],
                'xp_cap': card['xp_cap'],
                'attack_increase': attack_increase,
                'health_increase': health_increase,
                'defence_increase': defence_increase,
                'speed_increase': speed_increase,
                'card_icon': card['card_icon'],
                'new_attack': new_stats['attack'],
                'new_defence': new_stats['defence'],
                'new_health': new_stats['health'],
                'new_speed': new_stats['speed'],
                'max_level_reached': new_level == card['max_level']
            })
        
        await self.send_rewards_embed(ctx, xp_rewards, total_xp_gained, total_cards_leveled, money_reward, gamble_reward, rewards)

    # Helper methods needed for the improved handle_xp_rewards function


    async def calculate_equipment_boosts(self, user_id, card_id):
        """Calculate the boosts provided by a card's equipment"""
        try:
            # Get the card's equipment
            equipment_query = """
                SELECT weapon_id, armor_id, shield_id
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
            """
            equipment = await execute_query_with_retry(equipment_query, (user_id, card_id))

            if not equipment:
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                }

            equipment = equipment[0]  # Now safe to access first element

            # Initialize boosts
            boosts = {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }

            # Check each equipment type
            equipment_types = [
                equipment.get('weapon_id'),
                equipment.get('armor_id'),
                equipment.get('shield_id')
            ]

            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage, 
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details = await execute_query_with_retry(query, (eq_id,))
                    
                    if eq_details:
                        eq_details = eq_details[0]
                        # Add equipment flat boosts
                        boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                        boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                        boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                        boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                        
                        # Add equipment percentage boosts
                        boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                        boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                        boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                        boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

            return boosts
        except Exception as e:
            print(f"Error in calculate_equipment_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }

    # Fix for the calculate_support_card_boosts function
    async def calculate_support_card_boosts(self, user_id, card_id):
        """Calculate the total boosts from a single support card and its equipment"""
        try:
            # Get support card details
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence, 
                c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM card c
            JOIN user_cards uc ON c.card_id = uc.card_id
            WHERE c.card_id = %s AND uc.user_id = %s
            """
            support_card_result = await execute_query_with_retry(query, (card_id, user_id))
            
            if not support_card_result:
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_speed': 0, 'pct_defence': 0
                }
                
            support_card = support_card_result[0]  # Now safe to access first element
            
            # Initialize boosts
            flat_attack = support_card.get('attack', 0) or 0
            flat_health = support_card.get('health', 0) or 0
            flat_speed = support_card.get('speed', 0) or 0
            flat_defence = support_card.get('defence', 0) or 0
            
            pct_attack = support_card.get('attack_percentage', 0) or 0
            pct_health = support_card.get('health_percentage', 0) or 0
            pct_speed = support_card.get('speed_percentage', 0) or 0
            pct_defence = support_card.get('defence_percentage', 0) or 0
            
            # Add equipment boosts
            equipment_types = [
                support_card.get('weapon_id'),
                support_card.get('armor_id'),
                support_card.get('shield_id')
            ]
            
            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage, 
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details_result = await execute_query_with_retry(query, (eq_id,))
                    
                    if eq_details_result:
                        eq_details = eq_details_result[0]
                        # Add equipment flat boosts
                        flat_attack += eq_details.get('attack_boost', 0) or 0
                        flat_health += eq_details.get('health_boost', 0) or 0
                        flat_speed += eq_details.get('speed_boost', 0) or 0
                        flat_defence += eq_details.get('defense_boost', 0) or 0
                        
                        # Add equipment percentage boosts
                        pct_attack += eq_details.get('attack_percentage', 0) or 0
                        pct_health += eq_details.get('health_percentage', 0) or 0
                        pct_speed += eq_details.get('speed_percentage', 0) or 0
                        pct_defence += eq_details.get('defense_percentage', 0) or 0
            
            return {
                'flat_attack': flat_attack,
                'flat_health': flat_health,
                'flat_speed': flat_speed,
                'flat_defence': flat_defence,
                'pct_attack': pct_attack,
                'pct_health': pct_health,
                'pct_speed': pct_speed,
                'pct_defence': pct_defence
            }
        except Exception as e:
            print(f"Error in calculate_support_card_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_speed': 0, 'pct_defence': 0
            }
    async def add_product_to_inventory(self, inventory_id, product_id, quantity):
        """Helper method to add products (like capsules) to inventory"""
        # Check if product already exists in inventory
        check_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
        existing_product = await execute_query_with_retry(check_query, (inventory_id, product_id))
        
        if existing_product:
            # Update quantity if product exists
            update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = %s"
            await execute_query_with_retry(update_query, (quantity, inventory_id, product_id))
        else:
            # Insert new product entry
            insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, %s, %s)"
            await execute_query_with_retry(insert_query, (inventory_id, product_id, quantity))
    async def apply_equipment_boosts(self, user_id, card_id, equipment_boosts):
        """Apply equipment boosts to a card's stats"""
        if not equipment_boosts:
            return
        
        # Apply flat boosts first
        flat_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat + %s,
            health_stat = health_stat + %s,
            defence_stat = defence_stat + %s,
            speed_stat = speed_stat + %s
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            flat_query,
            (equipment_boosts['flat_attack'], equipment_boosts['flat_health'], 
            equipment_boosts['flat_defence'], equipment_boosts['flat_speed'],
            user_id, card_id)
        )
        
        # Then apply percentage boosts
        pct_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat * (1 + %s/100),
            health_stat = health_stat * (1 + %s/100),
            defence_stat = defence_stat * (1 + %s/100),
            speed_stat = speed_stat * (1 + %s/100)
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            pct_query,
            (equipment_boosts['pct_attack'], equipment_boosts['pct_health'], 
            equipment_boosts['pct_defence'], equipment_boosts['pct_speed'],
            user_id, card_id)
        )

    async def calculate_total_support_boosts(self, team_id, user_id):
        """
        Calculate the total flat and percentage boosts from all support cards in the team
        """
        try:
            # Get all support cards in the team
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence, 
                c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Support'
            """
            support_cards = await execute_query_with_retry(query, (team_id, user_id))
            
            # Check if support_cards is None or empty
            if not support_cards:
                # Return default values if no support cards found
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                }
            
            # Initialize total boosts
            total_flat_attack = 0
            total_flat_health = 0
            total_flat_speed = 0
            total_flat_defence = 0
            
            total_pct_attack = 0
            total_pct_health = 0
            total_pct_speed = 0
            total_pct_defence = 0
            
            # Calculate total boosts from all support cards and their equipment
            for support in support_cards:
                # Add support card flat boosts (with null checks)
                total_flat_attack += support.get('attack', 0) or 0
                total_flat_health += support.get('health', 0) or 0
                total_flat_speed += support.get('speed', 0) or 0
                total_flat_defence += support.get('defence', 0) or 0
                
                # Add support card percentage boosts (with null checks)
                total_pct_attack += support.get('attack_percentage', 0) or 0
                total_pct_health += support.get('health_percentage', 0) or 0
                total_pct_speed += support.get('speed_percentage', 0) or 0
                total_pct_defence += support.get('defence_percentage', 0) or 0
                
                # Add equipment boosts - FIX: Don't try to unpack equipment IDs
                equipment_ids = [
                    support.get('weapon_id'),
                    support.get('armor_id'),
                    support.get('shield_id')
                ]
                
                for eq_id in equipment_ids:
                    if eq_id:
                        try:
                            # Query equipment details
                            query = """
                            SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                attack_percentage, health_percentage, 
                                speed_percentage, defense_percentage
                            FROM weapon
                            WHERE weapon_id = %s
                            """
                            eq_details = await execute_query_with_retry(query, (eq_id,))
                            
                            if eq_details and eq_details[0]:
                                eq_details = eq_details[0]
                                # Add equipment flat boosts
                                total_flat_attack += eq_details.get('attack_boost', 0) or 0
                                total_flat_health += eq_details.get('health_boost', 0) or 0
                                total_flat_speed += eq_details.get('speed_boost', 0) or 0
                                total_flat_defence += eq_details.get('defense_boost', 0) or 0
                                
                                # Add equipment percentage boosts
                                total_pct_attack += eq_details.get('attack_percentage', 0) or 0
                                total_pct_health += eq_details.get('health_percentage', 0) or 0
                                total_pct_speed += eq_details.get('speed_percentage', 0) or 0
                                total_pct_defence += eq_details.get('defense_percentage', 0) or 0
                        except Exception as e:
                            print(f"Error querying equipment with ID {eq_id}: {e}")
                            # Continue with the next equipment
                            continue
            
            return {
                'flat_attack': total_flat_attack,
                'flat_health': total_flat_health,
                'flat_speed': total_flat_speed,
                'flat_defence': total_flat_defence,
                'pct_attack': total_pct_attack,
                'pct_health': total_pct_health,
                'pct_speed': total_pct_speed,
                'pct_defence': total_pct_defence
            }
        except Exception as e:
            print(f"Error in calculate_total_support_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }
    async def apply_support_boosts_to_single_offense(self, user_id, card_id, support_boosts):
        """Apply the given support boosts to a single offense card"""
        if not support_boosts:
            return
            
        # Apply flat boosts first
        flat_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat + %s,
            health_stat = health_stat + %s,
            defence_stat = defence_stat + %s,
            speed_stat = speed_stat + %s
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            flat_query,
            (support_boosts['flat_attack'], support_boosts['flat_health'], 
            support_boosts['flat_defence'], support_boosts['flat_speed'],
            user_id, card_id)
        )
        
        # Then apply percentage boosts
        pct_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat * (1 + %s/100),
            health_stat = health_stat * (1 + %s/100),
            defence_stat = defence_stat * (1 + %s/100),
            speed_stat = speed_stat * (1 + %s/100)
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            pct_query,
            (support_boosts['pct_attack'], support_boosts['pct_health'], 
            support_boosts['pct_defence'], support_boosts['pct_speed'],
            user_id, card_id)
        )

    async def calculate_money_reward(self, ctx, money_cap, case):
        user_id = await self.get_user_id(ctx.author.id)
        # Check if user has card_id 108
        check_card_query = """
        SELECT card_id FROM user_cards 
        WHERE user_id = %s AND card_id = 106
        """
        card_result = await execute_query_with_retry(check_card_query, (user_id,))
        if case == 0:
            min_reward = int(money_cap * 0.2)
            # Calculate base money prize
            money_prize = random.randint(min_reward, money_cap)
            
            # Add bonus if user has card 108
            if card_result:
                print("lol")
                money_prize += 200
            else:
                print("no")
            
            return money_prize
        else:
            money_prize = random.randint(50, money_cap)
            return money_prize

    async def send_rewards_embed(self, ctx, xp_rewards, total_xp_gained, total_cards_leveled, money_reward, gamble_reward, rewards):
        reward_message = "You've obtained the following chests: "
        for chest_info in rewards.values():
            chest_name_query = "SELECT chest_icon FROM chest WHERE chest_id = %s"
            chest_name_result = await execute_query_with_retry(chest_name_query, (chest_info['chest_id'],))
            chest_name = chest_name_result[0]["chest_icon"]
            reward_message += f"{chest_info['quantity']}x {chest_name}"
        embed = discord.Embed(
            title="🏆 Battle Rewards Summary 🏆",
            color=discord.Color.gold(),
            description=f"{reward_message}\nWon Received: **{money_reward}** <:won:1304034228966658048>\nGWon Received : **{gamble_reward}** <:gamble:1314341368176574564>\nTotal XP Gained: **{total_xp_gained}**\nCards Leveled Up: **{total_cards_leveled}**"
        )

        # Add a field for each card's rewards
        for reward in xp_rewards:
            # Skip XP progress bar and details for max-level cards
            if reward.get('max_level_reached', False):
                embed.add_field(
                    name=f"{reward['card_icon']} {reward['card_name']}: 🏆 **MAX LEVEL**",
                    value="No further XP or stat increases possible.",
                    inline=False
                )
                continue

            # Create the XP progress bar
            xp_percentage = (reward['new_xp'] / reward['xp_cap']) * 100
            progress_bar = self.create_xp_progress_bar(xp_percentage)
            
            # Format the card's rewards information
            card_info = (
                f"{'🔥 Enhanced! ' if reward['enhancer_active'] else ''}"
                f"{progress_bar} **{reward['new_xp']}/{reward['xp_cap']}**\n"
            )

            if reward['level_up']:
                level_up_info = (
                    f"✨ **LEVEL UP!** {reward['old_level']} → {reward['new_level']}\n"
                    f"📈 Stats Increased:\n"
                    f"```diff\n"
                    f"+ ATK: +{reward['attack_increase']}\n"
                    f"+ HP:  +{reward['health_increase']}\n"
                    f"+ DEF: +{reward['defence_increase']}\n"
                    f"+ SPD: +{reward['speed_increase']}\n"
                    f"```"
                )
                card_info += level_up_info

            embed.add_field(
                name=f"{reward['card_icon']} {reward['card_name']}: **+{reward['xp_gained']}**",
                value=card_info,
                inline=False
            )

        embed.set_footer(text="Keep fighting to grow stronger! | Solo Leveling PVP")
        await ctx.send(embed=embed)

    def create_xp_progress_bar(self, percentage, length=10):
        filled = int((percentage / 100.0) * length)
        empty = length - filled
        
        # Use custom emotes for the progress bar
        bar = (self.green_square * filled) + (self.gray_square * empty)
        return f"{bar} **{percentage:.1f}%**"

    async def handle_guild_contribution(self, ctx, user_id, money_reward):
        # Check if user is in a guild
        guild_query = "SELECT guild_id FROM user WHERE user_id = %s"
        guild_result = await execute_query_with_retry(guild_query, (user_id,))

        if guild_result and guild_result[0]['guild_id']:
            guild_id = guild_result[0]['guild_id']
            guild_contribution = int(money_reward * 0.05)  # 5% guild contribution
            final_reward = money_reward - guild_contribution

            # Update guild fund
            guild_update_query = "UPDATE guild SET fund_cash = fund_cash + %s WHERE guild_id = %s"
            await execute_query_with_retry(guild_update_query, (guild_contribution, guild_id))

            await ctx.send(f"Your guild received a contribution of {guild_contribution} <:won:1304034228966658048>.")
        else:
            final_reward = money_reward

        # Update user currency
        await self.update_user_currency(user_id, final_reward, 0, 0)

        return final_reward

    async def update_user_currency(self, user_id, money_reward, losswin, case):
        if losswin == 0:
            if case == 0:
                money_reward=await self.calculate_referral_bonus(user_id,money_reward)
                update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                await execute_query_with_retry(update_query, (money_reward, user_id))
            else:
                money_reward=await self.calculate_referral_bonus(user_id,money_reward)
                update_query = "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s"
                await execute_query_with_retry(update_query, (money_reward, user_id))
        else:
            # First get current won_currency value
            check_query = "SELECT won_currency FROM user WHERE user_id = %s"
            current_balance = await execute_query_with_retry(check_query, (user_id,))
            current_balance = current_balance[0]['won_currency']
            # If current balance is 0, no update needed
            if current_balance == 0:
                return
            
            # Calculate potential new balance
            # Note: money_reward is already negative in the elif case
            new_balance = current_balance + money_reward
            
            if new_balance < 0:
                # If would go negative, set to 0
                update_query = "UPDATE user SET won_currency = 0 WHERE user_id = %s"
                await execute_query_with_retry(update_query, (user_id,))
            else:
                # Safe to subtract
                money_reward=await self.calculate_referral_bonus(user_id,money_reward)
                update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                await execute_query_with_retry(update_query, (money_reward, user_id))

    async def get_dungeon_items(self, dungeon_id):
        dungeon_items = {
            'orbs': [],
            'fragments': [],
            'weapons': [],
            'xp_boost': [],
            'stat_boosts': [],
            'special_items': []
        }

        # Fetch orbs
        orb_query = """
        SELECT o.orb_id as id, o.orb_name as name
        FROM dungeon_orb do
        JOIN leveling_orbs o ON do.orb_id = o.orb_id
        WHERE do.dungeon_id = %s
        """
        dungeon_items['orbs'] = await execute_query_with_retry(orb_query, (dungeon_id,))

        # Fetch fragments
        fragment_query = """
        SELECT f.id_fragment as id, f.name_fragment as name
        FROM dungeon_fragments df
        JOIN fragments f ON df.frag_id = f.id_fragment
        WHERE df.dungeon_id = %s
        """
        dungeon_items['fragments'] = await execute_query_with_retry(fragment_query, (dungeon_id,))

        # Fetch weapons
        weapon_query = """
        SELECT w.weapon_id as id, w.weapon_name as name
        FROM dungeon_weapon dw
        JOIN weapon w ON dw.weapon_id = w.weapon_id
        WHERE dw.dungeon_id = %s
        """
        dungeon_items['weapons'] = await execute_query_with_retry(weapon_query, (dungeon_id,))

        # Fetch products (xp_boost, stat_boosts, special_items)
        product_query = """
        SELECT p.product_id as id, p.product_name as name, 
               p.xp_boost, p.attack_boost, p.health_boost, p.speed_boost
        FROM dungeon_product dp
        JOIN product p ON dp.product_id = p.product_id
        WHERE dp.dungeon_id = %s
        """
        products = await execute_query_with_retry(product_query, (dungeon_id,))

        for product in products:
            if product['xp_boost'] != 0 and product['attack_boost'] == 0 and product['health_boost'] == 0 and product['speed_boost'] == 0:
                dungeon_items['xp_boost'].append(product)
            elif product['xp_boost'] == 0 and (product['attack_boost'] != 0 or product['health_boost'] != 0 or product['speed_boost'] != 0):
                dungeon_items['stat_boosts'].append(product)
            else:
                dungeon_items['special_items'].append(product)

        return dungeon_items

    async def update_inventory(self, inventory_id, item_id, quantity, item_type):
        if item_type in ['xp_boost', 'stat_boosts', 'special_items']:
            table = 'inventory_product'
            id_column = 'product_id'
        elif item_type == 'orbs':
            table = 'inventory_orbs'
            id_column = 'orb_id'
        elif item_type == 'weapons':
            table = 'inventory_weapon'
            id_column = 'weapon_id'
        elif item_type == 'fragments':
            table = 'inventory_frags'
            id_column = 'frag_id'
        else:
            print(f"Unknown item type: {item_type}")
            return

        # Check if item exists in inventory
        check_query = f"""
        SELECT quantiter FROM {table}
        WHERE inventory_id = %s AND {id_column} = %s
        """
        result = await execute_query_with_retry(check_query, (inventory_id, item_id))

        if result:
            # Update existing item
            update_query = f"""
            UPDATE {table}
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND {id_column} = %s
            """
            await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
        else:
            # Insert new item
            insert_query = f"""
            INSERT INTO {table} (inventory_id, {id_column}, quantiter)
            VALUES (%s, %s, %s)
            """
            await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

    async def apply_charm_effects(self, user_id, team_cards):
        for card in team_cards:
            charm_updates = []
            stat_updates = []

            if card['charm_attack'] == 1:
                charm_updates.append("attack_count = CASE WHEN attack_count > 1 THEN attack_count - 1 ELSE 0 END")
                charm_updates.append("charm_attack = CASE WHEN attack_count > 1 THEN 1 ELSE 0 END")
                card['attack_stat'] = round(card['attack_stat'] * 1.2)

            if card['charm_defence'] == 1:
                charm_updates.append("defence_count = CASE WHEN defence_count > 1 THEN defence_count - 1 ELSE 0 END")
                charm_updates.append("charm_defence = CASE WHEN defence_count > 1 THEN 1 ELSE 0 END")
                card['defence_stat'] = round(card['defence_stat'] * 1.2)

            if card['charm_speed'] == 1:
                charm_updates.append("speed_count = CASE WHEN speed_count > 1 THEN speed_count - 1 ELSE 0 END")
                charm_updates.append("charm_speed = CASE WHEN speed_count > 1 THEN 1 ELSE 0 END")
                card['speed_stat'] = round(card['speed_stat'] * 1.2)

            if card['charm_health'] == 1:
                charm_updates.append("health_count = CASE WHEN health_count > 1 THEN health_count - 1 ELSE 0 END")
                charm_updates.append("charm_health = CASE WHEN health_count > 1 THEN 1 ELSE 0 END")
                card['health_stat'] = round(card['health_stat'] * 1.2)

            if charm_updates:
                update_query = "UPDATE user_cards SET "
                update_query += ", ".join(charm_updates)
                update_query += " WHERE user_id = %s AND card_id = %s"

                await execute_query_with_retry(update_query, (user_id, card['card_id']))

        return team_cards
        
    @commands.command(name="dungeonreset", hidden=True)
    async def dungeon_reset(self, ctx, discord_id: int):
        """
        Admin command to reset a user's dungeon state.
        Usage: sl dungeonreset <discord_id>
        """
        try:
            # Check if the command invoker is authorized
            authorized_users = ["818132161534230559", "1076303866854899762", "1204879494901407805", "1045687344277246022", "411676136037416970"]
            
            if str(ctx.author.id) not in authorized_users:
                return  # Silently ignore if not authorized
            
            # Check if restricted users are in the correct server
            if str(ctx.author.id) in ["1045687344277246022", "411676136037416970"]:
                if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                    return  # Silently ignore if not in correct server
            
            # Check if the user exists in the database
            user_id = await self.get_user_id(discord_id)
            
            if not user_id:
                await ctx.send(f"❌ User with Discord ID {discord_id} not found in the database.")
                return
                        
            # Check if user is in dungeons_in_progress before trying to remove
            if discord_id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(discord_id)
                        
            # Reset dungeon state in database
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                        
            # Also remove from fled_dungeons if present
            if discord_id in self.fled_dungeons:
                self.fled_dungeons.remove(discord_id)
                        
            await ctx.send(f"✅ Successfully reset dungeon state for user with Discord ID {discord_id}.")
            
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Dungeon Reset Command Used",
                    color=discord.Color.dark_red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=str(discord_id), inline=True)
                embed.add_field(name="Action", value="Dungeon State Reset", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await log_channel.send(embed=embed)
                    
        except Exception as e:
            logging.error(f"Error in dungeonreset command: {str(e)}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
            
    @commands.command(name="dungeondbstats")
    @commands.has_permissions(administrator=True)
    async def trade_db_stats(self, ctx):
        """Display database connection statistics for trading system"""
        metrics = get_metrics()
        
        embed = discord.Embed(
            title="Trading System Database Metrics",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
        embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
        embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
        
        embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
        embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
        embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
        
        if metrics["last_error"]:
            embed.add_field(
                name="Last Error", 
                value=f"{metrics['last_error']}\nTime: {metrics['last_error_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
            
        await ctx.send(embed=embed)
    # Add these methods to the DungeonCommands class

    def create_shield_bar(self, current_shield, max_shield):
        """Create a shield bar for S-Rank boss"""
        if max_shield == 0:
            return "🛡️ **ERROR** (0/0)"
        
        shield_percentage = min(100, (current_shield / max_shield) * 100)
        filled_blocks = min(10, max(0, int(shield_percentage / 10)))
        empty_blocks = 10 - filled_blocks
        
        # Use blue squares for shield with proper spacing
        shield_bar = f"{self.blue_square * filled_blocks}{self.gray_square * empty_blocks}"
        
        # Keep shield text compact to avoid layout issues
        return f"🛡️{max(0, current_shield)}/{max_shield}[**{shield_percentage:.0f}%**]"
    # Fix 1: Properly initialize shield in apply_boss_special_effects
    async def apply_boss_special_effects(self, monster, dungeon_id, current_round, round_log):
        """Apply special effects when encountering round 4 bosses"""
        special_messages = []
        
        if current_round == 4:  # Boss round
            if dungeon_id == 2 and monster['card_id'] == 63:  # C-Rank Buryura
                # Apply speed boost and dodge chance boost (one-time at combat start)
                if not hasattr(monster, 'buryura_buffed'):
                    monster['speed_stat'] = int(monster['speed_stat'] * 1.2)
                    monster['dodge_bonus'] = 30  # 30% additional dodge chance
                    monster['buryura_buffed'] = True
                    special_messages.append("🌪️ **Buryura activates its agility enhancement!** Speed increased by 20% and dodge chance increased by 30%!")
            
            elif dungeon_id == 3 and monster['card_id'] == 19:  # S-Rank Kargalgan - FIXED
                print("non")
                # Initialize shield if not already done
                if not hasattr(monster, 'shield_health'):
                    shield_amount = int(monster['health_stat'] * 0.5)  # 50% of max HP as shield
                    monster['shield_health'] = shield_amount
                    monster['max_shield'] = shield_amount
                    special_messages.append("🛡️ **Hymn of Protection!**")
                    print(f"DEBUG: Shield initialized - {monster['shield_health']}/{monster['max_shield']}")  # Debug line
        
        return special_messages
    async def handle_boss_turn_effects(self, monster, dungeon_id, current_round, monster_turn_count, round_log):
        """Handle special effects that trigger on boss turns"""
        special_messages = []
        
        if current_round == 4:  # Boss round
            if dungeon_id == 6 and monster['card_id'] == 127:  # A-Rank boss
                # White Flare attack every 2 boss rounds (3rd, 6th, etc.)
                if monster_turn_count % 3 == 0:
                    # Check if health is <= 20% for combined effect
                    health_percentage = (monster['current_health'] / monster['health_stat']) * 100
                    if health_percentage <= 20 and not hasattr(monster, 'soldiers_redemption_applied'):
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.2)
                        monster['soldiers_redemption_applied'] = True
                        special_messages.append("⚔️ **Soldier's Redemption activated!** Attack power increased by 20%!")
                    
                    # Apply White Flare attack (1.7x damage)
                    special_messages.append("🔥 **White Flare!** The boss unleashes a devastating attack!")
                    return special_messages, 1.7  # Return damage multiplier
            
            elif dungeon_id == 3 and monster['card_id'] == 19:  # S-Rank Kargalgan
                # Hymn of Blazing Fire every 2 boss rounds (after shield is broken)
                if not hasattr(monster, 'shield_health') or monster['shield_health'] <= 0:
                    if monster_turn_count % 3 == 0:
                        special_messages.append("🔥 **Hymn of Blazing Fire!**")
                        return special_messages, 0.7  # 70% of attack stat
        
        return special_messages, 1.0  # Normal damage multiplier

    async def handle_user_turn_effects(self, user_card, monster, dungeon_id, current_round, user_turn_count, round_log):
        """Handle special effects that trigger on user turns"""
        special_messages = []
        card_defeated_or_skip = False
        
        if current_round == 4:  # Boss round
            if dungeon_id == 4 and monster['card_id'] == 65:  # D-Rank boss Kasaka
                # User loses 5% health before attacking
                health_loss = int(user_card['health_stat'] * 0.05)
                user_card['current_health'] = max(0, user_card['current_health'] - health_loss)
                special_messages.append(f"💀 Toxic aura drains {user_card['card_name'][:15]} for {health_loss} damage!")
                
                # Check if user card is defeated by the drain
                if user_card['current_health'] <= 0:
                    special_messages.append(f"💀 {user_card['card_name'][:15]} defeated by toxic aura!")
                    card_defeated_or_skip = True
                else:
                    # Card survives, continue with normal attack
                    card_defeated_or_skip = False
                    
            elif dungeon_id == 5 and monster['card_id'] == 54:  # B-Rank boss
                # Freeze effect - skip turn every 5 user rounds
                if user_turn_count % 5 == 0:
                    special_messages.append(f"❄️ {user_card['card_name'][:15]} is frozen and cannot act!")
                    card_defeated_or_skip = True  # Skip turn due to freeze
                else:
                    card_defeated_or_skip = False
            else:
                card_defeated_or_skip = False
        else:
            card_defeated_or_skip = False
        
        # Always return a tuple
        return special_messages, card_defeated_or_skip
        

    def handle_boss_health_threshold(self, monster, dungeon_id, current_round, round_log):
        """Handle special effects when boss health drops below 20%"""
        special_messages = []
        
        if current_round == 4:  # Boss round
            health_percentage = (monster['current_health'] / monster['health_stat']) * 100
            
            if health_percentage <= 20:
                if dungeon_id == 1 and monster['card_id'] == 116:  # E-Rank Hobgoblin
                    if not hasattr(monster, 'last_attempt_activated'):
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.2)
                        monster['last_attempt_activated'] = True
                        special_messages.append("⚡ **Hobgoblin activates LAST ATTEMPT!** Attack power increased by 20%!")
                
                elif dungeon_id == 6 and monster['card_id'] == 127:  # A-Rank boss
                    if not hasattr(monster, 'soldiers_redemption_applied'):
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.2)
                        monster['soldiers_redemption_applied'] = True
                        special_messages.append("⚔️ **Soldier's Redemption activated!** Attack power increased by 20%!")
                
                elif dungeon_id == 3 and monster['card_id'] == 19:  # S-Rank Kargalgan
                    if not hasattr(monster, 'hymn_strengthening_applied'):
                        monster['speed_stat'] = int(monster['speed_stat'] * 1.3)
                        monster['defence_stat'] = int(monster['defence_stat'] * 1.3)
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.3)
                        monster['hymn_strengthening_applied'] = True
                        special_messages.append("💪 **Hymn of Strengthening!**")
        
        return special_messages

    def handle_shield_damage(self, monster, damage):
        """Handle damage to S-Rank boss shield"""
        if hasattr(monster, 'shield_health') and monster['shield_health'] > 0:
            shield_damage = min(damage, monster['shield_health'])
            monster['shield_health'] -= shield_damage
            remaining_damage = damage - shield_damage
            
            # Store damage taken for display purposes
            monster['shield_damage_taken'] = shield_damage
            
            if monster['shield_health'] <= 0:
                # Shield is broken, apply remaining damage to HP
                monster['current_health'] = max(0, monster['current_health'] - remaining_damage)
                return remaining_damage, "🛡️ **Shield Broken!** Kargalgan's protection has been shattered!"
            else:
                # Shield absorbed all damage, no HP damage
                return 0, f"🛡️ Shield absorbed {shield_damage} damage!"
        
        # No shield or shield already broken
        return damage, ""
    async def handle_rare_weapon_drop(self, ctx, dungeon_rank):
        """Handle rare weapon drops for B, A, and S rank dungeons"""
        try:
            user_id = await self.get_user_id(ctx.author.id)
            inventory_id = await self.get_inventory_id(user_id)
            
            weapon_id = None
            drop_chance = 0.001  # 1% chance
            print("i'm here")
            # Determine weapon based on dungeon rank
            if dungeon_rank == 5:  # B-Rank
                weapon_id = 75
            elif dungeon_rank == 6:  # A-Rank  
                weapon_id = 74
            elif dungeon_rank == 3:  # S-Rank
                weapon_id = 73
            
            if weapon_id and random.random() < drop_chance:
                # Check if weapon already exists in inventory
                check_weapon_query = """
                SELECT quantiter FROM inventory_weapon 
                WHERE inventory_id = %s AND weapon_id = %s
                """
                weapon_result = await execute_query_with_retry(check_weapon_query, (inventory_id, weapon_id))
                
                if weapon_result:
                    # Update existing weapon quantity
                    update_weapon_query = """
                    UPDATE inventory_weapon 
                    SET quantiter = quantiter + 1 
                    WHERE inventory_id = %s AND weapon_id = %s
                    """
                    await execute_query_with_retry(update_weapon_query, (inventory_id, weapon_id))
                else:
                    # Insert new weapon
                    insert_weapon_query = """
                    INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter) 
                    VALUES (%s, %s, 1)
                    """
                    await execute_query_with_retry(insert_weapon_query, (inventory_id, weapon_id))
                
                # Get weapon name for notification
                weapon_name_query = "SELECT weapon_name FROM weapon WHERE weapon_id = %s"
                weapon_name_result = await execute_query_with_retry(weapon_name_query, (weapon_id,))
                weapon_name = weapon_name_result[0]['weapon_name'] if weapon_name_result else "Rare Weapon"
                
                await ctx.send(f"🎉 **LEGENDARY DROP!** You obtained a rare weapon: **{weapon_name}**!")
                
        except Exception as e:
            print(f"Error in handle_rare_weapon_drop: {e}")
    async def handle_dungeon_skip_check(self, ctx, current_round):
        """Check and handle dungeon skip after round 1 completion"""
        try:
            user_id = await self.get_user_id(ctx.author.id)
            
            # Check dungeon_skip status
            skip_query = "SELECT dungeon_skip, dungeon_rounds FROM user WHERE user_id = %s"
            skip_result = await execute_query_with_retry(skip_query, (user_id,))
            
            if skip_result:
                dungeon_skip = skip_result[0]['dungeon_skip']
                dungeon_round = skip_result[0]['dungeon_rounds']
                
                if dungeon_skip == 1:
                    new_dungeon_round = dungeon_round - 1
                    
                    if new_dungeon_round <= 0:
                        # Reset both dungeon_skip and dungeon_round to 0
                        update_query = """
                        UPDATE user 
                        SET dungeon_skip = 0, dungeon_rounds = 0 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (user_id,))
                    else:
                        # Just decrease dungeon_round
                        update_query = """
                        UPDATE user 
                        SET dungeon_rounds = %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (new_dungeon_round, user_id,))
                    
                    return True  # Skip to boss round
            
            return False  # Continue normal progression
            
        except Exception as e:
            print(f"Error in handle_dungeon_skip_check: {e}")
            return False

async def setup(bot):
    await bot.add_cog(DungeonCommands(bot))