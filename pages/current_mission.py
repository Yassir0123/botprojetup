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
        # Store the original interaction to track the command invoker
        self.original_user = None
        self.message = None
        self.mission_started = False  # Flag to track if mission has started
        self.fight_log_history = []  # Store fight log history
        self.current_user_card = None  # Track current user card
        self.current_monster = None   # Track current monster

    async def on_timeout(self):
        """Handle timeout by resetting mission state and disabling buttons"""
        try:
            # Only handle timeout if the mission hasn't started yet
            if not self.mission_started and self.original_user:
                user_id = await self.bot.get_cog('MissionCommands').get_user_id(self.original_user.id)
                if user_id:
                    # Reset mission state in database
                    reset_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(reset_query, (user_id,))
                    logging.info(f"Mission state reset due to timeout for user {self.original_user.id}")
                
                # Disable all buttons
                for child in self.children:
                    child.disabled = True
                
                # Update the message if it exists
                if self.message:
                    try:
                        timeout_embed = discord.Embed(
                            title="Mission Timed Out",
                            description="The mission has expired due to inactivity.",
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting with the button is the original command user"""
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("Only the user who started this mission can use these buttons!", ephemeral=True)
            return False
        return True
        
    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)
    
    def get_battle_display(self, user_card, monster, action_text="Battle begins!"):
        """Create a minimal battle display that always shows health bars"""
        # Always include these critical elements
        battle_header = f"**{user_card['card_name']} vs {monster['name']}**"
        user_health = self.create_health_bar(user_card)
        monster_health = self.create_health_bar(monster, is_monster=True)
        
        # Combine them with the action text
        display = f"{battle_header}\n{user_health}\n{monster_health}\n{action_text}"
        
        # Ensure we don't exceed Discord's limit
        if len(display) > 1000:
            # If somehow still too long, simplify further
            display = f"{battle_header}\n{user_health}\n{monster_health}"
            
            # If that's still too long (very unlikely), show only the most critical info
            if len(display) > 1000:
                display = f"{battle_header}\nBattle in progress!"
                
        return display
        
    @discord.ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="start_mission")
    async def start_mission(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Set mission_started flag to prevent timeout during battle
        self.mission_started = True
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        user_id = None
        try:
            user_id = await self.bot.get_cog('MissionCommands').get_user_id(interaction.user.id)
            update_mission_query = "UPDATE user SET mission_current = 1 WHERE user_id = %s"
            await execute_query_with_retry(update_mission_query, (user_id,))
        except Exception as e:
            logging.error(f"Error updating mission_current to 1: {e}")

        try:
            # Initial setup and battle preparation
            embed = interaction.message.embeds[0]
            embed.clear_fields()
            files = []
            # Handle rank image
            rank_image_path = self.get_image_path(self.mission_card['cardrank_image'])
            if rank_image_path:
                try:
                    rank_filename = f"rank_{os.path.basename(rank_image_path)}"
                    rank_file = discord.File(rank_image_path, filename=rank_filename)
                    files.append(rank_file)
                    embed.set_thumbnail(url=f"attachment://{rank_filename}")
                except Exception as e:
                    logging.error(f"Error loading rank image: {e}")

            # Handle card image
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

            # Initialize health
            monster['current_health'] = monster['health_stat']
            for card in user_team:
                card['current_health'] = card['health_stat']

            # Set current entities
            self.current_user_card = user_team[0]
            self.current_monster = monster

            # Initial battle display - simple and guaranteed to fit
            battle_display = self.get_battle_display(user_team[0], monster)
            
            # Add the battle field
            embed.add_field(name="⚔️ Mission Battle", value=battle_display, inline=False)
            await interaction.message.edit(attachments=files, embed=embed, view=None)
            await asyncio.sleep(2)

            # Battle parameters
            max_turns = 200
            current_turn = 0
            battle_result = None

            # Main battle loop
            while monster['current_health'] > 0 and user_team and current_turn < max_turns:
                current_turn += 1
                user_card = user_team[0]
                self.current_user_card = user_card

                # User's attack phase
                user_attack = random.randint(int(user_card['attack_stat'] * 0.45), int(user_card['attack_stat'] * 0.55))
                effective_user_damage = self.calculate_damage(user_attack, monster['defence_stat'])
                
                monster['current_health'] = max(0, monster['current_health'] - effective_user_damage)
                self.current_monster = monster

                # Update battle display with user's attack
                battle_display = self.get_battle_display(
                    user_card, 
                    monster, 
                    f"💥 {user_card['card_name']} deals {effective_user_damage} damage to {monster['name']}!"
                )
                
                # Update the embed
                embed.set_field_at(-1, name="⚔️ Mission Battle", value=battle_display, inline=False)
                await interaction.message.edit(embed=embed)
                await asyncio.sleep(3)

                # Check if monster is defeated
                if monster['current_health'] <= 0:
                    battle_result = 'win'
                    break

                # Monster's attack phase
                monster_attack = random.randint(int(monster['min_attack']), int(monster['max_attack']))
                effective_monster_damage = self.calculate_damage(monster_attack, user_card['defence_stat'])
                
                user_card['current_health'] = max(0, user_card['current_health'] - effective_monster_damage)
                self.current_user_card = user_card

                # Update battle display with monster's attack
                battle_display = self.get_battle_display(
                    user_card, 
                    monster, 
                    f"💥 {monster['name']} deals {effective_monster_damage} damage to {user_card['card_name']}!"
                )
                
                # Update the embed
                embed.set_field_at(-1, name="⚔️ Mission Battle", value=battle_display, inline=False)
                await interaction.message.edit(embed=embed)
                await asyncio.sleep(3)

                # Check if user card is defeated
                if user_card['current_health'] <= 0:
                    user_team.pop(0)
                    if not user_team:
                        battle_result = 'lose'
                        break
                    
                    # Update current user card
                    self.current_user_card = user_team[0]
                    
                    # Display next fighter entering battle
                    battle_display = self.get_battle_display(
                        user_team[0], 
                        monster, 
                        "Next fighter enters the battle!"
                    )
                    
                    # Update the embed
                    embed.set_field_at(-1, name="⚔️ Mission Battle", value=battle_display, inline=False)
                    await interaction.message.edit(embed=embed)
                    await asyncio.sleep(3)

            # Mission result processing
            if battle_result == 'win':
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
                            print(days_diff)
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
                def calculate_premium_bonus(premium_level, base_amount):
                    """Calculate bonus based on premium level"""
                    bonus_multipliers = {
                        1: 1.10,  # 10% bonus
                        2: 1.20,  # 20% bonus
                        3: 1.30   # 30% bonus
                    }
                    multiplier = bonus_multipliers.get(premium_level, 1.0)
                    return int(base_amount * multiplier)
                    
                try:
                    # Get user ID
                    user_id = await self.bot.get_cog('MissionCommands').get_user_id(interaction.user.id)
                    if not user_id:
                        logging.error(f"No user ID found for Discord user {interaction.user.id}")
                        raise ValueError("User ID not found")
                    # Check premium status
                    premium_level, is_premium_active = await check_premium_status(self, user_id)
                    
                    # Calculate base money prize
                    money_prize = random.randint(self.mission_card['min_moneycap'], self.mission_card['max_moneycap'])
                    
                    # Check for special card 107
                    card_107_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = 107"
                    card_107_result = await execute_query_with_retry(card_107_query, (user_id,))
                    
                    if card_107_result:
                        money_prize += 300
                    
                    if is_premium_active:
                        money_prize = calculate_premium_bonus(premium_level, money_prize)
                        print("done got")
                    money_prize = await calculate_referral_bonus(self, user_id, money_prize)
                    # Update user currency
                    update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                    rows_affected = await execute_query_with_retry(update_query, (money_prize, user_id))
                    
                    # Update mission streak
                    mission_query = "UPDATE user SET mission_streak = mission_streak + 1 WHERE user_id = %s"
                    await execute_query_with_retry(mission_query, (user_id,))
                    
                    # Reset mission_current
                    reset_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(reset_query, (user_id,))
                    quest_status = await self.bot.get_cog('MissionCommands').update_quest_progress(user_id)
                    if quest_status is not None:
                        if quest_status == 1:
                            print("🎉 Congratulations! You've completed the 'Complete 5 missions' quest!")
                        else:
                            print("📊 Your progress for the 'Complete 5 missions' quest has been updated.")
                    # Update embed with mission results
                    embed.add_field(
                        name="🎉 Mission Complete!", 
                        value=f"Congratulations! You've defeated the enemy and earned {money_prize} <:won:1304034228966658048>!",
                        inline=False
                    )
                    
                    self.mission_completed = True

                except Exception as mission_reward_error:
                    logging.error(f"Mission reward processing error: {mission_reward_error}")
                    logging.error(traceback.format_exc())
                    embed.add_field(
                        name="❌ Mission Reward Error", 
                        value="An error occurred while processing mission rewards. Please contact support.", 
                        inline=False
                    )
                    # Ensure mission_current is reset even if reward processing fails
                    if user_id:
                        reset_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
                        await execute_query_with_retry(reset_query, (user_id,))

            else:
                async def reset_mission_current(self, user_id):
                    """Helper method to reset mission_current to 0"""
                    try:
                        reset_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
                        await execute_query_with_retry(reset_query, (user_id,))
                        logging.info(f"Mission current reset to 0 for user {user_id}")
                    except Exception as reset_error:
                        logging.error(f"Error resetting mission_current: {reset_error}")
                embed.add_field(name="❌ Mission Failed", 
                    value="Your team has been defeated or the battle dragged on too long. Better luck next time!", 
                    inline=False)
                try:
                    user_id = await self.bot.get_cog('MissionCommands').get_user_id(interaction.user.id)
                    await reset_mission_current(self, user_id)
                except Exception as e:
                    logging.error(f"Error updating mission_current after mission failure: {e}")
            # Final message edit and view cleanup
            await interaction.message.edit(embed=embed, view=None)
            self.stop()

        except Exception as overall_error:
            logging.error(f"Unexpected mission logic error: {overall_error}")
            logging.error(traceback.format_exc())
            
            # Send error embed to the user
            error_embed = discord.Embed(
                title="Mission Error",
                description="An unexpected error occurred during the mission. Please try again.",
                color=discord.Color.red()
            )
            await interaction.message.edit(embed=error_embed, view=None, attachments=[])

            # Prepare detailed error message for support
            error_message = (
                f"🚨 <@818132161534230559> Error in Mission system!\n"
                f"```\nError: {str(overall_error)}\n"
                f"Channel: {interaction.channel.name} ({interaction.channel.id})\n"
                f"Server: {interaction.guild.name} ({interaction.guild.id})\n"
                f"Command User: {interaction.user.name} ({interaction.user.id})\n```"
            )

            # Add user data and opponent data in separate messages to avoid length issues
            user_data_message = f"User data:\n```json\n{json.dumps(self.user_fighter_cards, indent=2)[:1900]}```"
            opponent_data_message = f"Opponent data:\n```json\n{json.dumps(self.mission_card, indent=2)[:1900]}```"
            traceback_message = f"Traceback:\n```python\n{traceback.format_exc()[:1900]}```"

            try:
                # Send error to support channel
                support_channel = self.bot.get_channel(1336306672741908561)
                if support_channel:
                    await support_channel.send(error_message)
                    await support_channel.send(user_data_message)
                    await support_channel.send(opponent_data_message)
                    await support_channel.send(traceback_message)

                    # Log that the error was successfully sent
                    logging.info("Error message successfully sent to support channel")
                else:
                    logging.error("Support channel not found")
                    
                # Send follow-up message to user
                try:
                    await interaction.followup.send(
                        "An unexpected error occurred. Our support team has been notified and will investigate the issue.",
                        ephemeral=True
                    )
                except discord.errors.InteractionResponded:
                    await interaction.channel.send(
                        f"{interaction.user.mention} An unexpected error occurred. Our support team has been notified and will investigate the issue."
                    )

            except Exception as send_error:
                logging.error(f"Failed to send error to support channel: {str(send_error)}")
                logging.error(f"Original error message: {error_message}")
                try:
                    await interaction.followup.send(
                        "An unexpected error occurred. Please try again later.",
                        ephemeral=True
                    )
                except:
                    await interaction.channel.send(
                        f"{interaction.user.mention} An unexpected error occurred. Please try again later."
                    )

        finally:
            # Always ensure mission_current is reset to 0 when the mission ends
            if user_id:
                try:
                    reset_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
                    await execute_query_with_retry(reset_query, (user_id,))
                    logging.info(f"Mission current reset to 0 for user {user_id} in finally block")
                except Exception as e:
                    logging.error(f"Error resetting mission_current in finally block: {e}")

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.primary, custom_id="reroll_mission")
    async def reroll_mission(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        new_mission_card = await self.bot.get_cog('MissionCommands').get_random_mission_card(self.mission_card['missioncards_id'])
        if new_mission_card:
            self.mission_card = new_mission_card
            
            # Recreate the embed from scratch
            embed = discord.Embed(
                title=f"🏰 Mission: {self.mission_card['name']}",
                description=self.bot.get_cog('MissionCommands').generate_mission_description(),
                color=discord.Color.blue()
            )

            # Prepare files for sending
            files = []
            # Handle rank image
            rank_image_path = self.get_image_path(self.mission_card['cardrank_image'])
            if rank_image_path:
                try:
                    rank_filename = f"rank_{os.path.basename(rank_image_path)}"
                    rank_file = discord.File(rank_image_path, filename=rank_filename)
                    files.append(rank_file)
                    embed.set_thumbnail(url=f"attachment://{rank_filename}")
                except Exception as e:
                    logging.error(f"Error loading rank image: {e}")

            # Handle card image
            card_image_path = self.get_image_path(self.mission_card['image'])
            if card_image_path:
                try:
                    card_filename = f"card_{os.path.basename(card_image_path)}"
                    card_file = discord.File(card_image_path, filename=card_filename)
                    files.append(card_file)
                    embed.set_image(url=f"attachment://{card_filename}")
                except Exception as e:
                    logging.error(f"Error loading card image: {e}")

            # Recreate fields
            embed.add_field(name="📊 Enemy", value=f"{self.mission_card['name']}", inline=False)
            embed.add_field(name="💰 Money Prize", value=f"{self.mission_card['min_moneycap']} - {self.mission_card['max_moneycap']}", inline=False)
            embed.add_field(name="📜 Your Mission", value=self.bot.get_cog('MissionCommands').generate_mission(), inline=False)
            embed.add_field(name="🕒 Status", value="```diff\n+ Mission in progress...\n```", inline=False)
            
            embed.set_footer(text="'I alone level up.' - Sung Jin-Woo")

            # Disable reroll button to prevent multiple rerolls
            button.disabled = True

            # Edit the message with new files and embed
            await interaction.message.edit(attachments=files, embed=embed, view=self)
        else:
            await interaction.followup.send("No new missions available. Please try again later.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel_mission")
    async def cancel_mission(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            user_id = await self.bot.get_cog('MissionCommands').get_user_id(interaction.user.id)
            update_mission_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_mission_query, (user_id,))
        except Exception as e:
            logging.error(f"Error updating mission_current: {e}")

        await interaction.message.edit(content="Mission canceled.", attachments=[], embed=None, view=None)

    def create_health_bar(self, entity, is_monster=False):
        # Calculate health percentage with extra precision
        health_percentage = max(0, min(100, (entity['current_health'] / entity['health_stat']) * 100))
        filled_blocks = min(10, max(0, int(health_percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_monster:
            # Use red square until health is exactly 0, then switch to gray
            if health_percentage > 0:
                filled_square = self.red_square
            else:
                filled_square = self.gray_square
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

    async def check_mission_timer(self, user_id):
        # Get the current mission timer
        query = "SELECT mission_timer FROM user WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        
        if not result:
            return False, "User not found."
        
        current_time = datetime.now()
        mission_timer = result[0]['mission_timer']
        
        if mission_timer is None:
            # First mission attempt - set timer and allow
            update_query = "UPDATE user SET mission_timer = %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (current_time, user_id))
            return True, None
        
        # Calculate time difference
        time_diff = current_time - mission_timer
        cooldown_seconds = 180  # 3 minutes in seconds
        
        if time_diff.total_seconds() < cooldown_seconds:
            # Still in cooldown
            remaining_seconds = cooldown_seconds - time_diff.total_seconds()
            remaining_minutes = int(remaining_seconds // 60)
            remaining_secs = int(remaining_seconds % 60)
            
            if remaining_minutes > 0:
                time_msg = f"{remaining_minutes} minute(s) and {remaining_secs} second(s)"
            else:
                time_msg = f"{remaining_secs} second(s)"
                
            return False, f"You need to wait {time_msg} before starting another mission."
        
        # Cooldown expired - update timer and allow
        update_query = "UPDATE user SET mission_timer = %s WHERE user_id = %s"
        await execute_query_with_retry(update_query, (current_time, user_id))
        return True, None

    async def update_quest_progress(self, user_id):
        # First, get the quest progress
        quest_query = """
        SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
        FROM user_quests uq
        JOIN quest q ON uq.quest_id = q.quest_id
        WHERE uq.user_id = %s AND q.quest_name = 'Complete 5 missions'
        """
        result = await execute_query_with_retry(quest_query, (user_id,))
        
        if result:
            quest = result[0]
            if quest['completion_status'] == 0:
                new_count = quest['current_count'] + 1
                update_query = """
                UPDATE user_quests
                SET current_count = %s, completion_status = %s
                WHERE userquests_id = %s
                """
                completion_status = 1 if new_count >= quest['required_count'] else 0
                await execute_query_with_retry(update_query, (new_count, completion_status, quest['userquests_id']))
                
                # If quest is completed, update inventory
                if completion_status == 1:
                    # First, get the inventory_id for the user
                    inventory_query = """
                    SELECT inventory_id 
                    FROM inventory 
                    WHERE user_id = %s
                    """
                    inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
                    
                    if inventory_result:
                        inventory_id = inventory_result[0]['inventory_id']
                        chest_id = 1  # The specific chest ID you mentioned
                        
                        # Check if the chest exists in the inventory
                        check_chest_query = """
                        SELECT quantiter
                        FROM inventory_chest 
                        WHERE inventory_id = %s AND chest_id = %s
                        """
                        chest_result = await execute_query_with_retry(check_chest_query, (inventory_id, chest_id))
                        
                        if chest_result:
                            # Update existing chest quantity
                            update_chest_query = """
                            UPDATE inventory_chest 
                            SET quantiter = quantiter + 1 
                            WHERE inventory_id = %s AND chest_id = %s
                            """
                            await execute_query_with_retry(update_chest_query, (inventory_id, chest_id))
                        else:
                            # Insert new chest entry
                            insert_chest_query = """
                            INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                            VALUES (%s, %s, 1)
                            """
                            await execute_query_with_retry(insert_chest_query, (inventory_id, chest_id))
                
                return completion_status
        return None

    async def get_user_id(self, discord_id):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (str(discord_id),))
        return result[0]['user_id'] if result else None

    async def get_team_id(self, user_id):
        query = "SELECT team_id FROM team WHERE user_id = %s"
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

    async def apply_charms(self, fighters):
        for fighter in fighters:
            charm_updates = []
            stat_updates = []

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
                await execute_query_with_retry(update_query, (fighter['user_id'], fighter['card_id']))

        return fighters

    async def get_random_mission_card(self, excluded_id=None):
        # Define rank probabilities with higher chance for lower ranks
        rank_weights = {
            'E': 40,  # Highest probability
            'D': 25,
            'C': 15,
            'B': 10,
            'A': 5,
            'S': 5    # Lowest probability
        }
        
        # Construct query with optional exclusion
        query = "SELECT m.*,c.cardrank_image FROM mission_cards m, card_rank c WHERE m.card_rank = c.cardrank_name  "
        if excluded_id:
            query += " AND m.missioncards_id != %s"
            params = (excluded_id,)
        else:
            params = None
        
        # Execute query
        mission_cards = await execute_query_with_retry(query, params)
        
        # If no cards, return None
        if not mission_cards:
            return None
        
        # Filter cards by their rank
        filtered_cards = [
            card for card in mission_cards 
            if card['card_rank'] in rank_weights
        ]
        
        # Prepare weighted selection
        weights = [rank_weights[card['card_rank']] for card in filtered_cards]
        
        # Use random.choices for weighted selection
        return random.choices(filtered_cards, weights=weights, k=1)[0]

    def generate_mission(self) -> str:
        return "Your task is to clear the dungeon and defeat the boss. Only then will you earn the rewards hidden within."

    def generate_mission_description(self) -> str:
        return "The shadows dance around you as you step into the unknown. Every sound makes you tense, every step feels heavier than the last."

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        full_path = os.path.join(self.image_path, image_name)
        
        # Debug print statements
        print(f"Attempting to load image: {image_name}")
        print(f"Full image path: {full_path}")
        print(f"Image exists: {os.path.exists(full_path)}")
        
        return full_path if os.path.exists(full_path) else None

    @commands.command(name="mission",aliases=["ms", "mis"])
    async def mission(self, ctx):
        user_id = await self.get_user_id(ctx.author.id)
        if not user_id:
            await ctx.send("You need to be registered to use this command.")
            return
            
        # Check if user is in a mission
        fight_current_query = """
        SELECT mission_current, mission_timer
        FROM user
        WHERE user_id = %s
        """
        fight_current_result = await execute_query_with_retry(fight_current_query, (user_id,))

        if fight_current_result and fight_current_result[0]['mission_current'] == 1:
            # Check if the mission state might be stale (more than 10 minutes old)
            mission_timer = fight_current_result[0]['mission_timer']
            current_time = datetime.now()
            
            if mission_timer and (current_time - mission_timer).total_seconds() > 600:  # 10 minutes
                # This is likely a stale state, reset it
                update_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
                await execute_query_with_retry(update_query, (user_id,))
                await ctx.send("Detected and fixed a stale mission state. You can now start a new mission.")
            else:
                # User is actually in a mission
                await ctx.send("You are already doing a mission. Please complete your current battle before starting a new one.")
                return
            # Check mission timer
        can_start, timer_message = await self.check_mission_timer(user_id)
        if not can_start:
            await ctx.send(timer_message)
            return

        team_id = await self.get_team_id(user_id)
        if not team_id:
            await ctx.send("You need to have a team to start a mission.")
            return
        get = await execute_query_with_retry("SELECT mission_current from user where user_id = %s",(user_id,))
        if get[0]['mission_current'] == 0:
            print("current")
            update_mission_query = "UPDATE user SET mission_current = 1 WHERE user_id = %s"
            await execute_query_with_retry(update_mission_query, (user_id,))
        else:
            print("doing")
            await ctx.send("You are already doing a mission. Please complete your current battle before starting a new one.")
            return
        user_fighter_cards = await self.get_user_fighter_cards(team_id, user_id)
        if not user_fighter_cards:
            await ctx.send("You need at least one fighter card in your team to start a mission.")
            update_mission_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_mission_query, (user_id,))
            return

        # Apply charms to the fighter cards
        user_fighter_cards = await self.apply_charms(user_fighter_cards)

        mission_card = await self.get_random_mission_card()
        if not mission_card:
            await ctx.send("No available missions at the moment. Please try again later.")
            update_mission_query = "UPDATE user SET mission_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_mission_query, (user_id,))
        embed = discord.Embed(
            title=f"🏰 Mission: {mission_card['name']}",
            description=self.generate_mission_description(),
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

        # Handle card image (main image)
        card_image_path = self.get_image_path(mission_card['image'])
        if card_image_path:
            try:
                card_filename = f"card_{os.path.basename(card_image_path)}"
                card_file = discord.File(card_image_path, filename=card_filename)
                files.append(card_file)
                embed.set_image(url=f"attachment://{card_filename}")
            except Exception as e:
                logging.error(f"Error loading card image: {e}")
        embed.add_field(name="📊 Enemy", value=f"{mission_card['name']}", inline=False)
        embed.add_field(name="💰 Money Prize", value=f"<:won:1304034228966658048> {mission_card['min_moneycap']} - {mission_card['max_moneycap']}", inline=False)
        embed.add_field(name="📜 Your Mission", value=self.generate_mission(), inline=False)
        embed.add_field(name="🕒 Status", value="```diff\n+ Mission in progress...\n```", inline=False)
        
        # Add information about active charms
        active_charms = []
        for card in user_fighter_cards:
            card_charms = []
            if card['charm_attack'] == 1:
                card_charms.append("Attack")
            if card['charm_defence'] == 1:
                card_charms.append("Defence")
            if card['charm_speed'] == 1:
                card_charms.append("Speed")
            if card['charm_health'] == 1:
                card_charms.append("Health")
            if card_charms:
                active_charms.append(f"{card['card_name']}: {', '.join(card_charms)}")
        
        if active_charms:
            # Limit the length of the active charms field to avoid Discord's 1024 character limit
            charms_text = "\n".join(active_charms)
            if len(charms_text) > 1000:  # Leave some buffer
                charms_text = charms_text[:997] + "..."
            embed.add_field(name="🔮 Active Charms", value=charms_text, inline=False)
        
        embed.set_footer(text="'I alone level up.' - Sung Jin-Woo")

        view = MissionView(self.bot, user_fighter_cards, mission_card)
        view.original_user = ctx.author  # Set the original user
        message = await ctx.send(files=files, embed=embed, view=view)
        view.message = message  # Store the message for timeout handling
        await view.wait()
        
async def setup(bot):
    await bot.add_cog(MissionCommands(bot))