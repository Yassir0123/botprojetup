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
EASY_DUNGEON_IMAGE = "https://i.imgur.com/nLFDb29.jpeg"
MEDIUM_DUNGEON_IMAGE = "https://i.imgur.com/5WfEsHQ.png"
HARD_DUNGEON_IMAGE = "https://i.imgur.com/XiqKiv3.png"

class DungeonDifficulty(Enum):
    EASY = ("Easy", "🟢", EASY_DUNGEON_IMAGE, "E-D", "The Forest Goblin Gate", 0x4CAF50, "🗡️", "🏺", "💎", 1)
    MEDIUM = ("Medium", "🟡", MEDIUM_DUNGEON_IMAGE, "C-B", "The Giant Spider's Nest Gate", 0xFFC107, "⚔️", "🏺🏺", "💎💎", 2)
    HARD = ("Hard", "🔴", HARD_DUNGEON_IMAGE, "A-S", "The Hunters Guild Gate", 0xF44336, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 3)

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

    def perform_combat_round(self, attacker, defender, is_user_turn):
        """
        Comprehensive combat round handling with dodge and critical mechanics.
        
        Args:
            attacker (dict): Attacking card's details
            defender (dict): Defending card's details
            is_user_turn (bool): Whether it's the user's turn to attack
        
        Returns:
            list: Combat round messages
        """
        # Dodge check
        is_dodged, dodge_message = self.handle_dodge_attempt(attacker, defender)
        if is_dodged:
            return [dodge_message]
        
        # Base damage calculation with variance
        base_damage = random.randint(
            int(attacker['attack_stat'] * 0.45), 
            int(attacker['attack_stat'] * 0.55)
        )
        
        # Critical hit check
        is_critical, final_damage, crit_message = self.handle_critical_attack(base_damage, attacker['attack_stat'])
        
        # Effective damage calculation
        effective_damage = self.calculate_effective_damage(final_damage, defender['defence_stat'])
        
        # Reduce defender's health
        defender['current_health'] = max(0, defender['current_health'] - effective_damage)
        
        # Prepare attack messages
        messages = []
        if is_critical and crit_message:
            messages.append(crit_message)
        
        attack_message = self.get_attack_message(
            attacker['card_name'], 
            effective_damage, 
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
            SELECT r.referrer_id, r.referee_id, r.status, u.roll_total, u.mid_dungeon_points
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
            mid_dungeon_points = referee_data['mid_dungeon_points'] or 0
            
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
        query = "SELECT * FROM dungeon WHERE dungeon_rank = 1"
        return await execute_query_with_retry(query)

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

        if not inventory_id or not team_id:
            # Remove from in-progress before returning
            self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send("You need to have an inventory and a team to enter a dungeon.")
            return

        dungeons = await self.get_solo_dungeons()
        if not dungeons:
            # Remove from in-progress before returning
            self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send("No dungeons available at the moment.")
            return

        current_page = 0
        
        async def update_embed():
            difficulty = list(DungeonDifficulty)[current_page]
            embed = discord.Embed(
                title=f"{difficulty.value[4]}",
                description=self.format_dungeon_description(difficulty),
                color=discord.Color(difficulty.value[5])
            )
            embed.set_image(url=difficulty.value[2])
            
            # Add dungeon stats
            embed.add_field(name="📊 Rank", value=f"[{difficulty.value[3]}]", inline=True)
            embed.add_field(name="☠️ Danger", value=self.format_danger_level(difficulty), inline=True)
            embed.add_field(name="💰 Rewards", value=self.format_reward_level(difficulty), inline=True)
            
            # Add more detailed information
            embed.add_field(name="🗺️ Environment", value=self.generate_environment_description(difficulty), inline=False)
            embed.add_field(name="👹 Monsters", value=self.generate_monster_description(difficulty), inline=False)
            embed.add_field(name="💎 Treasures", value=self.generate_treasure_description(difficulty), inline=False)
            
            embed.set_footer(text=f"Gate {current_page + 1}/3 | ⬅️ Previous | ➡️ Next | 🚪 Enter")
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
                # Remove from in-progress before returning
                self.dungeons_in_progress.remove(ctx.author.id)
                update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                await ctx.send("The gates have sealed shut. Your hesitation has cost you this opportunity, hunter.")
                return

            if str(reaction.emoji) == "⬅️":
                current_page = (current_page - 1) % 3
                await message.edit(embed=await update_embed())
            elif str(reaction.emoji) == "➡️":
                current_page = (current_page + 1) % 3
                await message.edit(embed=await update_embed())
            elif str(reaction.emoji) == "🚪":
                # Double-check dungeon status again before proceeding
                check_dungeon_status_query = "SELECT dungeon_current FROM user WHERE user_id = %s"
                dungeon_status_result = await execute_query_with_retry(check_dungeon_status_query, (user_id,))
                if dungeon_status_result[0]['dungeon_current'] == 1:
                    # Remove from in-progress before returning
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("Please finish your current dungeon exploration before starting a new one.")
                    return
                
                selected_dungeon = dungeons[current_page]
                dungeon_id = selected_dungeon['dungeon_id']
                
                # Set dungeon_current to 1 AFTER all checks pass
                update_dungeon_current_query = "UPDATE user SET dungeon_current = 1 WHERE user_id = %s"
                await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                
                # Check for dungeon key
                key_query = """
                SELECT idk.quantiter 
                FROM inventory_dungeonkey idk
                JOIN dungeon d ON idk.dungeonkey_id = d.dungeonkey_id
                WHERE idk.inventory_id = %s AND d.dungeon_id = %s
                """
                key_result = await execute_query_with_retry(key_query, (inventory_id, dungeon_id))
                if not key_result:
                    # Reset dungeon state if no key
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    # Remove from in-progress before returning
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("You don't have the required key for this dungeon.")
                    return                
                key_quantity = key_result[0]['quantiter']

                # Check for fighter card
                fighter_query = """
                SELECT COUNT(*) as fighter_count
                FROM team_cards tc
                JOIN user_cards uc ON tc.card_id = uc.card_id
                JOIN card c ON uc.card_id = c.card_id
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                """
                fighter_result = await execute_query_with_retry(fighter_query, (team_id,))
                
                if fighter_result[0]['fighter_count'] == 0:
                    # Reset dungeon state if no fighter cards
                    update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                    # Remove from in-progress before returning
                    self.dungeons_in_progress.remove(ctx.author.id)
                    await ctx.send("Your team needs at least one Fighter card to enter the dungeon.")
                    return
                
                # Update or delete the dungeon key
                if key_quantity == 1:
                    delete_key_query = "DELETE FROM inventory_dungeonkey WHERE inventory_id = %s AND dungeonkey_id = %s"
                    await execute_query_with_retry(delete_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))
                else:
                    update_key_query = "UPDATE inventory_dungeonkey SET quantiter = quantiter - 1 WHERE inventory_id = %s AND dungeonkey_id = %s"
                    await execute_query_with_retry(update_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))

                # Get dungeon cards
                dungeon_cards_query = """
                SELECT dc.*, c.card_name
                FROM dungeon_cards dc
                JOIN card c ON dc.card_id = c.card_id
                WHERE dc.dungeon_id = %s
                ORDER BY dc.round
                """
                dungeon_cards = await execute_query_with_retry(dungeon_cards_query, (dungeon_id,))

                # Get user's fighter cards - FIXED SQL QUERY
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
                user_fighter_cards = await execute_query_with_retry(user_cards_query, (user_id, team_id))
                
                # Apply charm effects
                user_fighter_cards = await self.apply_charm_effects(user_id, user_fighter_cards)

                selected_difficulty = list(DungeonDifficulty)[current_page]
                
                await self.start_dungeon(ctx, selected_difficulty, user_fighter_cards, dungeon_cards)
                return

            await message.remove_reaction(reaction, user)

    def format_dungeon_description(self, difficulty: DungeonDifficulty) -> str:
        descriptions = {
            DungeonDifficulty.EASY: "A shadowy forest teeming with mischievous goblins. The air is thick with an eerie calm, but danger lurks behind every tree.",
            DungeonDifficulty.MEDIUM: "A vast network of sticky webs, home to giant spiders. The chittering echoes in the darkness, testing the mettle of all who enter.",
            DungeonDifficulty.HARD: "The Hunter Guild's Gate, a massive portal exuding overwhelming mana. Behind it lies a battlefield of high-rank monsters and treasures unknown. Only the strongest hunters dare to challenge it."
        }
        return descriptions[difficulty]

    def format_danger_level(self, difficulty: DungeonDifficulty) -> str:
        danger = ':star:' * difficulty.value[9] + '⚪' * (3 - difficulty.value[9])
        return danger

    def format_reward_level(self, difficulty: DungeonDifficulty) -> str:
        rewards = '💎' * difficulty.value[9] + '⚪' * (3 - difficulty.value[9])
        return rewards

    def generate_environment_description(self, difficulty: DungeonDifficulty) -> str:
        environments = {
            DungeonDifficulty.EASY: "A misty forest with ancient trees and hidden paths. Goblin traps and crude fortifications dot the landscape.",
            DungeonDifficulty.MEDIUM: "A dark, cavernous space filled with giant webs. Abandoned equipment of unfortunate adventurers litters the ground.",
            DungeonDifficulty.HARD: "A colossal gate surrounded by glowing runes and pulsating with energy. The area hums with mana, and the faint roar of monsters echoes from within."
        }
        return environments[difficulty]

    def manage_combat_log(self, round_log, current_round):
        """
        Manages the combat log by splitting it into multiple fields if needed.
        Each field has a maximum of 1024 characters.
        """
        fields = []
        current_field = []
        current_length = 0
        
        for line in round_log:
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > 1000:  # Using 1000 to be safe
                # Create a new field with accumulated lines
                field_value = "\n".join(current_field)
                fields.append((f"⚔️ Round {current_round}", field_value, False))
                
                # Start a new field
                current_field = [line]
                current_length = line_length
            else:
                current_field.append(line)
                current_length += line_length
        
        # Add the last field if it has content
        if current_field:
            field_value = "\n".join(current_field)
            fields.append((f"⚔️ Round {current_round}", field_value, False))
        
        return fields

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    async def update_embed_fields(self, embed, dungeon_message, round_log, current_round):
        """
        Updates the embed with managed combat log fields
        """
        try:
            # Clear existing fields
            embed.clear_fields()
            
            # Get managed fields
            fields = self.manage_combat_log(round_log, current_round)
            
            # Add all fields to embed
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
            
            # Update the message
            await dungeon_message.edit(embed=embed)
        except Exception as e:
            logging.error(f"Error updating embed fields: {str(e)}")
            raise

    def generate_monster_description(self, difficulty: DungeonDifficulty) -> str:
        monsters = {
            DungeonDifficulty.EASY: "Goblins, Hobgoblin",
            DungeonDifficulty.MEDIUM: "Insects, Lizard Squad, Giant Spiders",
            DungeonDifficulty.HARD: "Jackals, Strike Team B, High-Orcss"
        }
        return monsters[difficulty]

    def generate_treasure_description(self, difficulty: DungeonDifficulty) -> str:
        treasures = {
            DungeonDifficulty.EASY: "Abundant Shadow Chests scattered throughout the dungeon.",
            DungeonDifficulty.MEDIUM: "A mix of Hunter Chests and Shadow Chests, offering greater rewards.",
            DungeonDifficulty.HARD: "An array of Monarch Chests, Hunter Chests, and Shadow Chests, brimming with rare treasures."
        }
        return treasures[difficulty]

    async def start_dungeon(self, ctx, difficulty: DungeonDifficulty, user_fighter_cards, dungeon_cards):
        # Extract card IDs for the fighters that will participate in the dungeon
        user_card_ids = [card['card_id'] for card in user_fighter_cards]

        dungeon_name = self.generate_dungeon_name(difficulty)
        embed = discord.Embed(
            title=f"🏰 {dungeon_name}",
            description=self.generate_dungeon_description(difficulty),
            color=discord.Color(difficulty.value[5])
        )

        embed.set_image(url=difficulty.value[2])

        embed.add_field(name="📊 Rank", value=f"\n[{difficulty.value[3]}]\n", inline=True)

        danger_level = '█' * (list(DungeonDifficulty).index(difficulty) + 1) + '░' * (3 - (list(DungeonDifficulty).index(difficulty) + 1))
        
        embed.add_field(name="☠️ Danger Level", value=f"\n{danger_level}\n", inline=True)

        rewards = '🌟' * (list(DungeonDifficulty).index(difficulty) + 1)
        embed.add_field(name="💰 Rewards", value=f"\n{rewards}\n", inline=True)

        embed.add_field(name="📜 Your Mission", value=self.generate_mission(), inline=False)
        embed.add_field(name="🕒 Status", value="```diff\n+ Dungeon exploration in progress...\n```", inline=False)

        embed.set_footer(text="'I alone level up.' - Sung Jin-Woo")

        # Send the initial dungeon message without the Run Away button
        dungeon_message = await ctx.send(embed=embed)
        
        await asyncio.sleep(3)
        await self.dungeon_exploration(ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards, user_card_ids)

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
        
        # Add damage taken display if provided
        damage_text = f" (-{damage_taken})" if damage_taken else ""
        
        return f"{name}: {health_bar} **({health_percentage:.1f}%){damage_text}** ({max(0, current_health)}/{health_stat} HP)"

    async def dungeon_exploration(self, ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards, user_card_ids=None):
        try:
            logging.info(f"Starting dungeon exploration for user {ctx.author.id} with difficulty {difficulty.value[0]}")
            
            # Add user to dungeons_in_progress at the start
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
            dungeon_message = await dungeon_message.edit(embed=embed, view=view)

            for current_round in range(1, total_rounds + 1):
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
                round_log = [round_description]
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
                fight_number = 1  # Reset fight number for each round

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

                    fight_header = f"\n**Fight {fight_number}: {user_card['card_name']} vs {monster['card_name']}**"
                    player_health = self.create_health_bar(user_card, is_monster=False)
                    monster_health = self.create_health_bar(monster, is_monster=True)
                    damage_line = "Damage dealt: -"

                    fight_status = [fight_header, player_health, monster_health, damage_line]
                    round_log = self.truncate_log(round_log + fight_status)

                    try:
                        embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                        
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

                    while not current_fight_complete and monster['current_health'] > 0 and user_card['current_health'] > 0:
                        # Check if the user has fled the dungeon
                        if ctx.author.id in self.fled_dungeons:
                            logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                            # Remove from fled_dungeons to clean up
                            self.fled_dungeons.remove(ctx.author.id)
                            return
                            
                        # Battle timer
                        await asyncio.sleep(1)
                        
                        # User attack
                        user_attack_messages = self.perform_combat_round(
                            user_card, 
                            monster, 
                            is_user_turn=True
                        )
                        
                        fight_status = [
                            self.create_health_bar(user_card, is_monster=False),
                            self.create_health_bar(monster, is_monster=True, damage_taken=monster.get('damage_taken', 0)),
                        ] + user_attack_messages

                        round_log = self.truncate_log(round_log[:-4] + fight_status)
                        
                        try:
                            embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                            await dungeon_message.edit(embed=embed)
                        except discord.errors.NotFound:
                            # Message was deleted (likely due to fleeing)
                            return
                            
                        await asyncio.sleep(1)

                        # Check if monster is defeated after user attack
                        if monster['current_health'] <= 0:
                            # Remove the defeated monster
                            round_monsters.pop(0)
                            current_fight_complete = True
                            
                            # If user card is defeated, remove it from team
                            if user_card['current_health'] <= 0:
                                user_team.pop(0)
                            
                            # Increment fight number only when a fight is complete
                            fight_number += 1
                            break

                        if monster['current_health'] > 0:
                            # Monster attack
                            monster_attack_messages = self.perform_combat_round(
                                monster, 
                                user_card, 
                                is_user_turn=False
                            )
                            
                            fight_status = [
                                self.create_health_bar(user_card, is_monster=False, damage_taken=user_card.get('damage_taken', 0)),
                                self.create_health_bar(monster, is_monster=True),
                            ] + monster_attack_messages

                            round_log = self.truncate_log(round_log[:-4] + fight_status)
                            
                            try:
                                embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                                await dungeon_message.edit(embed=embed)
                            except discord.errors.NotFound:
                                # Message was deleted (likely due to fleeing)
                                return
                                
                            await asyncio.sleep(1)

                            # Check if user card is defeated after monster attack
                            if user_card['current_health'] <= 0:
                                user_team.pop(0)
                                current_fight_complete = True
                                # Increment fight number only when a fight is complete
                                fight_number += 1
                                break

                    # Round completion or game end logic
                    if not round_monsters and current_round < total_rounds:
                        # Check if the user has fled the dungeon
                        if ctx.author.id in self.fled_dungeons:
                            logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                            # Remove from fled_dungeons to clean up
                            self.fled_dungeons.remove(ctx.author.id)
                            return
                            
                        # Clear the previous round's log completely
                        round_log = [f"\n**Round Complete!** Moving to the next round..."]
                        
                        # Clear previous image 
                        embed.set_image(url=difficulty.value[2])  # Round transition image
                        
                        # Update the field with the fresh, minimal log
                        try:
                            embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                            await dungeon_message.edit(embed=embed, attachments=[])  # Explicitly clear files
                        except discord.errors.NotFound:
                            # Message was deleted (likely due to fleeing)
                            return
                        
                        await asyncio.sleep(2)
                    elif not round_monsters and current_round == total_rounds:
                        # Disable the Run Away button when dungeon is complete
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
                        # When defeat occurs, clear image and ensure clean embed
                        embed.set_image(url=difficulty.value[2])  # Clear any existing image
                        
                        # Disable the Run Away button when dungeon is failed
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

    def calculate_money_loss(self, difficulty):
        if difficulty == DungeonDifficulty.EASY:
            return 200
        elif difficulty == DungeonDifficulty.MEDIUM:
            return 500
        elif difficulty == DungeonDifficulty.HARD:
            return 1000
        else:
            return 200  # Default to easy dungeon loss if difficulty is unknown

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

    def generate_dungeon_name(self, difficulty: DungeonDifficulty) -> str:
        return f"{difficulty.value[0]} Dungeon of Shadows"

    def generate_dungeon_description(self, difficulty: DungeonDifficulty) -> str:
        return "The shadows dance around you as you step into the unknown. Every sound makes you tense, every step feels heavier than the last."

    def generate_mission(self) -> str:
        return "Your objective is to complete the mission and take down the boss. Only by succeeding will you claim the rewards awaiting you."

    def generate_stage_description(self, stage: str) -> str:
        return f"You've arrived at the {stage}. Prepare yourself for what's to come."

    async def treasure_room(self, ctx, inventory_id, dungeon_id, dungeon_name, user_card_ids=None):
        # Get chest rewards based on dungeon difficulty
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
            
        if dungeon_name.lower() == "easy gate":
            # Shadow chest (chest_id 1)
            shadow_chest_count = random.randint(1, 3)
            rewards[1] = {
                'chest_id': 1,
                'quantity': shadow_chest_count
            }
            # Update low dungeon points
            update_points_query = """
            UPDATE user 
            SET low_dungeon_points = low_dungeon_points + 1 
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_points_query, (user_id,))
        
        elif dungeon_name.lower() == "medium gate":
            # Hunter chest (chest_id 2)
            chest_type_roll = random.random()
            
            if chest_type_roll < 0.9:
                # 90% chance: 1-2 hunter chests
                hunter_chest_count = random.randint(1, 2)
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': hunter_chest_count
                }
            else:
                # 10% chance: 1 hunter chest and 1-3 shadow chests
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': 1
                }
                shadow_chest_count = random.randint(1, 3)
                rewards[1] = {
                    'chest_id': 1,
                    'quantity': shadow_chest_count
                }
            # Update medium dungeon points
            update_points_query = """
            UPDATE user 
            SET mid_dungeon_points = mid_dungeon_points + 1 
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_points_query, (user_id,))
            await self.check_and_update_referral_status(user_id)
        elif dungeon_name.lower() == "hard gate":
            # Monarch chest (chest_id 3)
            chest_type_roll = random.random()
            
            # Always get monarch chest
            rewards[3] = {
                'chest_id': 3,
                'quantity': 1
            }
            
            # Additional chest possibilities
            if chest_type_roll < 0.7:  # High probability of only monarch chest
                pass
            elif chest_type_roll < 0.8:  # Monarch + Hunter
                hunter_chest_count = random.randint(1, 2)
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': hunter_chest_count
                }
            elif chest_type_roll < 0.9:  # Monarch + Shadow
                shadow_chest_count = random.randint(1, 4)
                rewards[1] = {
                    'chest_id': 1,
                    'quantity': shadow_chest_count
                }
            else:  # Monarch + Hunter + Shadow
                hunter_chest_count = random.randint(1, 2)
                shadow_chest_count = random.randint(1, 3)
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': hunter_chest_count
                }
                rewards[1] = {
                    'chest_id': 1,
                    'quantity': shadow_chest_count
                }
            # Update high dungeon points
            update_points_query = """
            UPDATE user 
            SET high_dungeon_points = high_dungeon_points + 1 
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_points_query, (user_id,))
        
        # Update inventory_chest
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
        
        # Calculate money reward
        money_query = "SELECT dungeon_moneycap FROM dungeon WHERE dungeon_id = %s"
        money_result = await execute_query_with_retry(money_query, (dungeon_id,))
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
        print('final gamble', final_gamble_reward)
        # Handle XP rewards
        team_id = await self.get_team_id(user_id)
        await self.handle_xp_rewards(ctx, user_id, team_id, dungeon_id, final_money_reward, final_gamble_reward, rewards, user_card_ids)
        
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
            
            # Adjust XP based on dungeon and card rank
            if dungeon_id == 1:  # Easy dungeon
                if card['card_rank'] in [5, 6]:
                    xp_reward = xp_reward
                else:
                    xp_reward = xp_reward // 2
            elif dungeon_id == 2:  # Medium dungeon
                if card['card_rank'] in [3, 4]:
                    xp_reward = xp_reward
                elif card['card_rank'] in [1, 2]:
                    xp_reward = xp_reward // 2
                else:  # Rank 5 or 6
                    xp_reward = xp_reward * 2
            elif dungeon_id == 3:  # Hard dungeon
                if card['card_rank'] in [1, 2]:
                    xp_reward = xp_reward
                else:  # Rank 3, 4, 5, or 6
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
            # Calculate base money prize
            money_prize = random.randint(1000, money_cap)
            
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
        
    @commands.command(name="dungeonreset")
    @commands.has_permissions(administrator=True)
    async def dungeon_reset(self, ctx, discord_id: int):
        """
        Admin command to reset a user's dungeon state.
        Usage: sl dungeonreset <discord_id>
        """
        try:
            # Check if the user exists in the database
            user_id = await self.get_user_id(discord_id)
            if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127","1076303866854899762", "1204879494901407805"):
                return  # Silently ignore if not authorized
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

async def setup(bot):
    await bot.add_cog(DungeonCommands(bot))