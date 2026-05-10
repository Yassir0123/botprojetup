import discord
from discord.ext import commands
import random
import asyncio
import math
from discord.errors import NotFound
import os
import time
import datetime
from discord import ui
import os
from PIL import Image, ImageDraw, ImageFont
import io
import traceback
import json
from PIL import Image, ImageDraw, ImageFont
import io
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
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
# Attack Button UI Component
class AttackButton(ui.View):
    def __init__(self, conduct_fight_instance, ctx, user_fighter, opponent_fighter, user_name, opponent_name, battle_log, is_user_turn):
        super().__init__(timeout=60)  # 60 second timeout
        self.conduct_fight_instance = conduct_fight_instance
        self.ctx = ctx
        self.user_fighter = user_fighter
        self.opponent_fighter = opponent_fighter
        self.user_name = user_name
        self.opponent_name = opponent_name
        self.battle_log = battle_log
        self.is_user_turn = is_user_turn
        self.attack_pressed = False
        
        # Disable button if it's not user's turn
        self.attack_button.disabled = not is_user_turn

    @ui.button(label="Attack", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def attack_button(self, interaction: discord.Interaction, button: ui.Button):
        # Verify the user who clicked is the one in the fight
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This is not your battle!", ephemeral=True)
            return
            
        # Mark button as pressed to continue the fight flow
        self.attack_pressed = True
        
        # Disable the button after it's pressed
        button.disabled = True
        
        # Acknowledge the interaction
        await interaction.response.edit_message(view=self)
        
        # Stop the view to continue with the fight
        self.stop()

    async def on_timeout(self):
        # If the button times out, add a message to the battle log
        self.battle_log.append("⏰ Time's up! Attack automatically triggered.")
        self.attack_pressed = True
        self.stop()
class EmFightCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_id = 818132161534230559  # Your specified user ID
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"  # Green emote for user
        self.red_square = "<:redc:1295036894417125550>"  # Red emote for opponent
        self.gray_square = "<:gray:1295036907595759697>"
        self.empty_square = "⬜"
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.image_size = (300, 350)  # Customizable image size (width, height)
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds
        
    def calculate_effective_damage(self, attack_damage, defense_stat):
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        return round(effective_damage)

    async def get_user_data_with_retry(self, user_id, max_retries=3):
        for attempt in range(max_retries):
            try:
                user_data = await self.get_user_data(user_id)
                if user_data:
                    return user_data
                
                # If we got None, wait and retry
                retry_delay = self.connection_retry_delay * (2 ** attempt)
                print(f"Get user data attempt {attempt+1} failed. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            except Exception as e:
                print(f"Get user data retry attempt {attempt+1} failed with error: {e}")
                if attempt == max_retries - 1:  # Last attempt
                    return None
                await asyncio.sleep(self.connection_retry_delay * (2 ** attempt))
        
        return None

    async def get_user_data(self, user_id):
        user_query = "SELECT user_id, user_authorid, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(user_query, (str(user_id),))
        
        if not user_result:
            return None

        user_data = user_result[0]
        
        team_query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = await execute_query_with_retry(team_query, (user_data['user_id'],))
        
        if not team_result:
            return None

        user_data['team_id'] = team_result[0]['team_id']
 
        fighters_query = """
        SELECT c.card_id, c.card_name, c.image, c.card_rank, uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, uc.current_level, tc.position,
            uc.current_xp, uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
            uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count
        FROM team_cards tc
        JOIN card c ON tc.card_id = c.card_id
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        ORDER BY tc.position
        """
        user_data['fighters'] = await execute_query_with_retry(fighters_query, (user_data['user_id'], user_data['team_id']))
        return user_data

    def get_rank_color(self, cardrank_id):
        rank_colors = {
            1: (192, 192, 192),  # Silver
            2: (255, 215, 0),    # Gold
            3: (139, 0, 0),     # Bronze
            4: (255, 165, 0),      # Green
            5: (32, 131, 39),      # Blue
            6: (0, 128, 128),    # Purple
            7: (128, 128, 128),      # Red
            8: (255, 255, 255)   # White
        }
        return rank_colors.get(cardrank_id, (255, 255, 255))  # Default to white if rank not found

    @commands.command(name="fdbstatus", aliases=["fdbs", "fconnections"])
    async def db_status(self, ctx):
        """Display information about the current database connection status"""
        # Check if the command invoker is authorized (optional security)
        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send("You don't have permission to use this command.")
            return
        
        try:
            metrics = get_metrics()
            
            # Create an embed for the database status
            embed = discord.Embed(
                title="📊 Database Connection Status",
                description="Current status of database connection pool",
                color=discord.Color.blue()
            )
            
            # Add metrics statistics
            embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
            embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
            
            embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
            embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
            embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
            
            # Add last error if any
            if metrics["last_error"]:
                error_time = metrics["last_error_time"].strftime("%Y-%m-%d %H:%M:%S") if metrics["last_error_time"] else "Unknown"
                embed.add_field(
                    name="Last Error",
                    value=f"Time: {error_time}\nError: {metrics['last_error'][:100]}...",
                    inline=False
                )
            
            # Add timestamp
            embed.timestamp = datetime.datetime.utcnow()
            
            await ctx.send(embed=embed)
                    
        except Exception as e:
            error_msg = f"Error retrieving database status: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")

    # Add a new command to monitor connection health
    @commands.command(name="dbhealth", aliases=["dbh"])
    async def db_health(self, ctx):
        """Display detailed database health metrics"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send("You don't have permission to use this command.")
            return
        
        try:
            metrics = get_metrics()
            
            # Test database responsiveness
            start_time = time.time()
            test_result = await execute_query_with_retry("SELECT 1 as test")
            response_time = (time.time() - start_time) * 1000  # ms
            
            # Create health status embed
            embed = discord.Embed(
                title="🏥 Database Health Check",
                color=discord.Color.green() if response_time < 100 and metrics["active_connections"] < 80 else discord.Color.orange()
            )
            
            # Add basic health metrics
            embed.add_field(name="Response Time", value=f"{response_time:.2f}ms", inline=True)
            embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
            
            # Add query performance metrics
            embed.add_field(
                name="Query Performance",
                value=f"Avg: {metrics['avg_query_time']:.2f}ms",
                inline=False
            )
            
            # Add recommendations based on metrics
            recommendations = []
            if metrics["active_connections"] > 80:
                recommendations.append("⚠️ High connection pool utilization. Consider increasing maxsize.")
            if response_time > 100:
                recommendations.append("⚠️ Slow database response time. Check server load.")
            if metrics["connection_errors"] > 0:
                recommendations.append("⚠️ Connection errors detected. Check network stability.")
                
            if recommendations:
                embed.add_field(
                    name="Recommendations",
                    value="\n".join(recommendations),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Status",
                    value="✅ Database connection pool is healthy",
                    inline=False
                )
                
            embed.timestamp = datetime.datetime.utcnow()
            await ctx.send(embed=embed)
            
        except Exception as e:
            error_msg = f"Error checking database health: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="fight", aliases=["f", "fg"])
    async def fight(self, ctx):
        start_time = time.time()
        await ctx.send("Initiating fight...")

        try:
            # Use the retry version for important queries
            user_data = await self.get_user_data_with_retry(ctx.author.id)
            if not user_data:
                await ctx.send("You need to be registered and have a team to use this command.")
                return

            if not user_data['fighters']:
                await ctx.send("You need at least one fighter card in your team to proceed with the fight.")
                return

            # Check fight_current status with retry
            fight_current_query = """
            SELECT fight_current
            FROM user
            WHERE user_authorid = %s
            """
            fight_current_result = await execute_query_with_retry(fight_current_query, (user_data['user_authorid'],))

            if fight_current_result and fight_current_result[0]['fight_current'] == 1:
                await ctx.send("You are already in a fight. Please complete your current battle before starting a new one.")
                return

            # Get fight timer information separately
            fight_timer_query = """
            SELECT fight_timer
            FROM user
            WHERE user_authorid = %s
            """
            fight_timer_result = await execute_query_with_retry(fight_timer_query, (user_data['user_authorid'],))

            # fight_timer_query = """
            # SELECT fight_timer, fight_current
            # FROM user
            # WHERE user_authorid = %s
            # """
            # fight_timer_result = await execute_query_with_retry(fight_timer_query, (user_data['user_authorid'],))

            # if fight_current_result and fight_current_result[0]['fight_current'] == 1:
            #     # Check if this is a stale state by comparing with the current time
            #     time_elapsed = datetime.datetime.now() - fight_timer_result[0]['fight_timer'] if fight_timer_result and fight_timer_result[0]['fight_timer'] else datetime.timedelta(hours=1)
                
            #     # If it's been more than 30 minutes, this is likely a stale state
            #     if time_elapsed.total_seconds() > 1800:  # 30 minutes
            #         # Reset the stale state
            #         reset_fight_current_query = """
            #         UPDATE user
            #         SET fight_current = 0
            #         WHERE user_authorid = %s
            #         """
            #         await execute_query_with_retry(reset_fight_current_query, (user_data['user_authorid'],))
            #         # Continue with the fight
            #     else:
            #         await ctx.send("You are already in a fight. Please complete your current battle before starting a new one.")
            #         return

            # Update fight_current to 1 before starting the fight
            update_fight_current_query = """
            UPDATE user
            SET fight_current = 1
            WHERE user_authorid = %s
            """
            await execute_query_with_retry(update_fight_current_query, (user_data['user_authorid'],))

            

            if not fight_timer_result or fight_timer_result[0]['fight_timer'] is None:
                # Update fight timer for the user
                new_fight_timer = datetime.datetime.now()
                update_timer_query = """
                UPDATE user
                SET fight_timer = %s
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(update_timer_query, (new_fight_timer, user_data['user_authorid']))
            else:
                fight_timer = fight_timer_result[0]['fight_timer']
                time_elapsed = datetime.datetime.now() - fight_timer
                if time_elapsed.total_seconds() < 300:  # 5 minutes
                    time_remaining = 300 - int(time_elapsed.total_seconds())
                    await ctx.send(f"You need to wait {time_remaining//60} minutes and {time_remaining%60} seconds before you can fight again.")
                    # Reset fight_current to 0
                    reset_fight_current_query = """
                    UPDATE user
                    SET fight_current = 0
                    WHERE user_authorid = %s
                    """
                    await execute_query_with_retry(reset_fight_current_query, (user_data['user_authorid'],))
                    return

                # Update fight timer for the user
                new_fight_timer = datetime.datetime.now()
                update_timer_query = """
                UPDATE user
                SET fight_timer = %s
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(update_timer_query, (new_fight_timer, user_data['user_authorid']))

            # Get all users except the one who initiated the fight
            all_users_query = """
            SELECT u.user_id, u.user_authorid, t.team_id
            FROM user u
            JOIN team t ON u.user_id = t.user_id
            WHERE u.user_id != %s
            """
            all_users = await execute_query_with_retry(all_users_query, (user_data['user_id'],))

            # Filter users with fighter cards in their team
            valid_opponents = []
            for opponent in all_users:
                fighters_query = """
                SELECT c.card_id, c.card_name, c.image, c.card_rank, uc.power_stat, uc.health_stat, uc.speed_stat, 
                    uc.attack_stat, uc.defence_stat, uc.current_level, tc.position,
                    uc.current_xp, uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, 
                    uc.charm_speed, uc.charm_health,
                    uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count
                FROM team_cards tc
                JOIN card c ON tc.card_id = c.card_id
                JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                ORDER BY tc.position
                """
                fighters = await execute_query_with_retry(fighters_query, (opponent['user_id'], opponent['team_id']))
                if fighters:
                    opponent['fighters'] = fighters
                    valid_opponents.append(opponent)

            if not valid_opponents:
                await ctx.send("No suitable opponents found. Please try again later.")
                # Reset fight_current to 0
                reset_fight_current_query = """
                UPDATE user
                SET fight_current = 0
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(reset_fight_current_query, (user_data['user_authorid'],))
                return

            # Randomly select an opponent
            opponent_data = random.choice(valid_opponents)

            print(f"Fight preparation time: {time.time() - start_time:.2f} seconds")
            user_data['fighters'] = await self.apply_charm_effects(user_data['fighters'], user_data['user_id'])
            opponent_data['fighters'] = await self.apply_charm_effects(opponent_data['fighters'], opponent_data['user_id'])

            # Start the fight
            await self.start_fight(ctx, user_data, opponent_data, is_challenge=False, is_squad=False)

        except Exception as e:
            # Reset fight_current to 0 in case of any error
            reset_fight_current_query = """
            UPDATE user
            SET fight_current = 0
            WHERE user_authorid = %s
            """
            await execute_query_with_retry(reset_fight_current_query, (str(ctx.author.id),))
            
            await ctx.send(f"An error occurred: {str(e)}")
            print(f"Error in fight command: {e}")
            traceback.print_exc()

    async def fetch_user_with_retry(self, user_id, max_retries=MAX_RETRIES, delay=RETRY_DELAY):
        for attempt in range(max_retries):
            try:
                return await self.bot.fetch_user(int(user_id))
            except NotFound as e:
                if attempt == max_retries - 1:  # Last attempt
                    print(f"Failed to fetch user {user_id} after {max_retries} attempts: {e}")
                    return None
                print(f"Attempt {attempt + 1} failed to fetch user {user_id}. Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
        return None

    @commands.command(name='dualfind', hidden=True)
    async def dual_find_shared_servers(self, ctx, first_user_id: str = None, second_user_id: str = None):
        """Find shared servers between two specified users (Admin only)"""
        # Check if the command invoker is the authorized admin
        if str(ctx.author.id) != "818132161534230559":
            return  # Silently ignore if not authorized
        
        try:
            # Check if both IDs were provided
            if not first_user_id or not second_user_id:
                await ctx.send("Please provide both user IDs: `sl dualfind first_id second_id`")
                return

            # Verify if users exist in database
            users_data = {}
            for user_id in [first_user_id, second_user_id]:
                query = "SELECT * FROM user WHERE user_authorid = %s"
                result = await execute_query_with_retry(query, (user_id,))
                
                if not result:
                    await ctx.send(f"User {user_id} not found in database!")
                    return
                users_data[user_id] = result[0]
            # Get list of all servers the bot is in
            bot_guilds = self.bot.guilds
            
            # Find servers where both users are present
            shared_servers = []
            for guild in bot_guilds:
                try:
                    # Try to fetch both members
                    member1 = await guild.fetch_member(int(first_user_id))
                    member2 = await guild.fetch_member(int(second_user_id))
                    
                    # If both members are found in this guild
                    if member1 and member2:
                        # Create an invite link if possible
                        invite_link = "No invite link available"
                        channels_to_try = [guild.system_channel] + guild.text_channels
                        
                        for channel in channels_to_try:
                            if channel and channel.permissions_for(guild.me).create_instant_invite:
                                try:
                                    invite = await channel.create_invite(max_age=300)  # 5 minute invite
                                    invite_link = invite.url
                                    break
                                except:
                                    continue
                        
                        shared_servers.append({
                            'name': guild.name,
                            'id': guild.id,
                            'member_count': guild.member_count,
                            'invite': invite_link,
                            'member1_name': member1.name,
                            'member2_name': member2.name
                        })
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    print(f"No permission to fetch member in guild {guild.name}")
                    continue
                except Exception as e:
                    print(f"Error checking guild {guild.name}: {e}")
                    continue

            if not shared_servers:
                await ctx.send(f"No shared servers found between users {first_user_id} and {second_user_id}")
                return

            # Create an embed to display the results
            embed = discord.Embed(
                title=f"Shared Servers Between Users",
                description=f"Found {len(shared_servers)} shared servers",
                color=discord.Color.blue()
            )
            
            # Add user info
            embed.add_field(
                name="Users",
                value=f"User 1: {shared_servers[0]['member1_name']} ({first_user_id})\n"
                    f"User 2: {shared_servers[0]['member2_name']} ({second_user_id})",
                inline=False
            )
            
            # Add server info
            for server in shared_servers:
                embed.add_field(
                    name=f"{server['name']} ({server['member_count']} members)",
                    value=f"Server ID: {server['id']}\nInvite: {server['invite']}",
                    inline=False
                )

            # Add timestamp
            embed.timestamp = datetime.datetime.utcnow()
            
            await ctx.send(embed=embed)

        except Exception as e:
            error_msg = f"Error in dual_find: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name='find')
    async def find_shared_servers(self, ctx, user_authorid: str = None):
        """Find shared servers between a specified user and the bot"""
        try:
            # Check if the command invoker is the authorized admin
            if str(ctx.author.id) != "818132161534230559":
                return  # Silently ignore if not authorized
            if not user_authorid:
                user_authorid = str(ctx.author.id)

            # Verify if user exists in database with retry logic
            retries = 0
            while retries < MAX_RETRIES:
                try:
                    query = "SELECT * FROM user WHERE user_authorid = %s"
                    result = await execute_query_with_retry(query, (user_authorid,))
                    
                    if result:
                        break
                    
                    retries += 1
                    if retries < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY)
                except Exception as e:
                    print(f"Database retry {retries + 1} failed: {e}")
                    retries += 1
                    if retries < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY)

            if not result:
                await ctx.send("User not found in database! Please make sure the user has interacted with the bot at least once.")
                return

            # Get the bot's ID
            bot_id = str(self.bot.user.id)
            
            # Get list of all servers the bot is in
            bot_guilds = self.bot.guilds
            
            # Find the target user in each guild
            shared_servers = []
            for guild in bot_guilds:
                try:
                    member = await guild.fetch_member(int(user_authorid))
                    if member:
                        # Create an invite link if possible
                        invite_link = "No invite link available"
                        # Try system channel first
                        channels_to_try = [guild.system_channel] + guild.text_channels
                        
                        for channel in channels_to_try:
                            if channel and channel.permissions_for(guild.me).create_instant_invite:
                                try:
                                    invite = await channel.create_invite(max_age=300)  # 5 minute invite
                                    invite_link = invite.url
                                    break
                                except:
                                    continue
                        
                        shared_servers.append({
                            'name': guild.name,
                            'id': guild.id,
                            'member_count': guild.member_count,
                            'invite': invite_link
                        })
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    print(f"No permission to fetch member in guild {guild.name}")
                    continue
                except Exception as e:
                    print(f"Error checking guild {guild.name}: {e}")
                    continue

            if not shared_servers:
                await ctx.send(f"No shared servers found for user ID: {user_authorid}")
                return

            # Create an embed to display the results
            embed = discord.Embed(
                title=f"Shared Servers",
                description=f"Found {len(shared_servers)} shared servers",
                color=discord.Color.blue()
            )
            
            for server in shared_servers:
                embed.add_field(
                    name=f"{server['name']} ({server['member_count']} members)",
                    value=f"Server ID: {server['id']}\nInvite: {server['invite']}",
                    inline=False
                )

            await ctx.send(embed=embed)

        except Exception as e:
            error_msg = f"Error in find_shared_servers: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred while processing your request. Error: {str(e)}")
    @commands.command(name='statusreset', hidden=True)
    async def status_reset(self, ctx, discord_id: str = None):
        """Reset status tables for a user (Admin only)"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1076303866854899762", "1204879494901407805"):
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
        if str(ctx.author.id) not in ("818132161534230559", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
        
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
                
        except Exception as e:
            # Only send error message to admin
            if str(ctx.author.id) == "818132161534230559":
                error_msg = f"Error during admin reset: {str(e)}"
                await ctx.send(error_msg, delete_after=10)
                print(f"Admin reset error: {traceback.format_exc()}")

    @commands.command(name="challenge", aliases=["ch", "chl"])
    async def challenge(self, ctx, opponent: discord.Member):
        # Self-challenge prevention
        if opponent.id == ctx.author.id:
            await ctx.send("You can't challenge yourself! Find a worthy opponent.")
            return
        
        # User data validation (existing logic)
        user_data = await self.get_user_data_with_retry(ctx.author.id)
        if not user_data:
            await ctx.send("You need to be registered and have a team to use this command.")
            return
        
        if not user_data['fighters']:
            await ctx.send("You need at least one fighter card in your team to proceed with the challenge.")
            return
        
        # Opponent data validation
        opponent_data = await self.get_user_data_with_retry(opponent.id)
        if not opponent_data:
            await ctx.send(f"{opponent.name} is not registered or doesn't have a team.")
            return
        
        if not opponent_data['fighters']:
            await ctx.send(f"{opponent.name} doesn't have any fighter cards in their team.")
            return
        
        # Create an embed for the challenge
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
        
        # Send challenge message with embed
        challenge_msg = await ctx.send(
            content=f"{opponent.mention}, you've been challenged to a fight!",
            embed=challenge_embed
        )
        
        # Add reaction buttons
        await challenge_msg.add_reaction("✅")
        await challenge_msg.add_reaction("❌")
        
        # Reaction check logic (existing)
        def check(reaction, user):
            return user == opponent and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == challenge_msg.id
        
        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            
            if str(reaction.emoji) == "✅":
                # Accept embed
                accept_embed = discord.Embed(
                    title="🏆 Challenge Accepted!",
                    description=f"{opponent.name} has accepted the challenge from {ctx.author.name}!",
                    color=discord.Color.green()
                )
                await ctx.send(embed=accept_embed)
                
                # Apply charm effects (existing logic)
                user_data['fighters'] = await self.apply_charm_effects(user_data['fighters'], user_data['user_id'])
                opponent_data['fighters'] = await self.apply_charm_effects(opponent_data['fighters'], opponent_data['user_id'])
                
                # Start the fight
                await self.start_fight(ctx, user_data, opponent_data, is_challenge=True, is_squad=False)
            else:
                # Decline embed
                decline_embed = discord.Embed(
                    title="❌ Challenge Declined",
                    description=f"{opponent.name} has declined the challenge.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=decline_embed)
        
        except asyncio.TimeoutError:
            # Timeout embed
            timeout_embed = discord.Embed(
                title="⏰ Challenge Expired",
                description=f"{opponent.name} didn't respond in time.",
                color=discord.Color.dark_grey()
            )
            await ctx.send(embed=timeout_embed)

    #@commands.command(name="squadbattle", aliases=["sb", "sbattle"])
    async def squadbattle(self, ctx):
        try:
            # Modified query to handle NULL values from GROUP_CONCAT
            squad_check_query = """
            SELECT 
                s.squad_id, 
                s.squad_type,
                COALESCE(GROUP_CONCAT(u.squad_current), '') as member_currents,
                COALESCE(GROUP_CONCAT(
                    CASE WHEN u.squad_timer IS NULL 
                        THEN 'None' 
                        ELSE u.squad_timer 
                    END
                ), '') as member_timers,
                COALESCE(GROUP_CONCAT(u.user_authorid), '') as member_ids
            FROM user u
            LEFT JOIN squad s ON u.squad_id = s.squad_id
            WHERE u.user_authorid = %s OR u.squad_id = (
                SELECT squad_id FROM user WHERE user_authorid = %s
            )
            GROUP BY s.squad_id
            """
            squad_result = await execute_query_with_retry(squad_check_query, (str(ctx.author.id), str(ctx.author.id)))
            
            if not squad_result or squad_result[0]['squad_id'] is None:
                await ctx.send("You need to be in a squad to use this command!")
                return
                
            squad_id = squad_result[0]['squad_id']
            squad_type = squad_result[0]['squad_type']
            
            # Safe parsing of member data with empty string handling
            member_currents = [int(x) for x in squad_result[0]['member_currents'].split(',') if x]
            member_timers = []
            member_ids = [x for x in squad_result[0]['member_ids'].split(',') if x]
            
            # Safely parse the timers
            timer_strings = [x for x in squad_result[0]['member_timers'].split(',') if x]
            for timer_str in timer_strings:
                if timer_str == 'None':
                    member_timers.append(None)
                else:
                    try:
                        member_timers.append(datetime.datetime.fromisoformat(timer_str))
                    except ValueError:
                        member_timers.append(None)
            
            # Verify we have valid member data
            if not member_ids:
                await ctx.send("No squad members found. Please try again later.")
                return
                
            # Check if any squad member is in a fight
            if member_currents and 1 in member_currents:
                member_in_fight = member_ids[member_currents.index(1)]
                await ctx.send(f"Your Squad is already in a fight. Please wait for their battle to complete.")
                return
                
            # Check cooldown for all members
            current_time = datetime.datetime.now()
            for timer, member_id in zip(member_timers, member_ids):
                if timer is not None:
                    time_elapsed = current_time - timer
                    if time_elapsed.total_seconds() < 300:  # 5 minutes
                        time_remaining = 300 - int(time_elapsed.total_seconds())
                        await ctx.send(f"Your Squad needs to wait {time_remaining//60} minutes and {time_remaining%60} seconds before the squad can fight again.")
                        return
                        
            # Check squad team
            squad_team_query = """
            SELECT sc.*, c.type_card, c.card_name
            FROM squad_cards sc
            JOIN card c ON sc.card_id = c.card_id
            WHERE sc.squad_id = %s AND c.type_card = 'Fighter'
            ORDER BY sc.position
            """
            squad_team = await execute_query_with_retry(squad_team_query, (squad_id,))
            
            if not squad_team:
                await ctx.send("Your squad needs to set up a team with at least one fighter card first!")
                return
                
            # Get fighter cards count
            fighter_count = len(squad_team)
            
            # Get opponents based on squad type
            required_opponents = 2 if squad_type == 0 else 4
            opponents = await self.get_squad_opponents(ctx.author.id, required_opponents)
            
            if len(opponents) < required_opponents:
                await ctx.send(f"Not enough opponents found. Need {required_opponents} opponents with fighter cards.")
                return
                
            # Create opponent team
            opponent_team = await self.create_opponent_team(opponents)
            
            # Update fight status for ALL squad members
            update_fight_query = """
            UPDATE user 
            SET squad_current = 1, squad_timer = %s
            WHERE squad_id = %s
            """
            await execute_query_with_retry(update_fight_query, (current_time, squad_id))
            
            # Start squad battle
            await self.start_fight(
                ctx, 
                {'fighters': await self.get_squad_fighters(squad_team), 'user_authorid': ctx.author.id},
                {'fighters': opponent_team, 'user_authorid': 'SQUAD_OPPONENT'},
                is_challenge=False,
                is_squad=True
            )
                                
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
            print(f"Error in squadbattle command: {e}")
            traceback.print_exc()

    async def get_squad_opponents(self, user_id, required_count):
        # Get random opponents with fighter cards in their team
        opponents_query = """
        SELECT DISTINCT u.user_id, u.user_authorid, t.team_id
        FROM user u
        JOIN team t ON u.user_id = t.user_id
        JOIN team_cards tc ON t.team_id = tc.team_id
        JOIN card c ON tc.card_id = c.card_id
        WHERE u.user_authorid != %s 
        AND c.type_card = 'Fighter'
        ORDER BY RAND()
        LIMIT %s
        """
        return await execute_query_with_retry(opponents_query, (str(user_id), required_count))

    async def create_opponent_team(self, opponents):
        opponent_team = []
        position = 0
        
        for opponent in opponents:
            # Get fighter cards for each opponent
            fighter_query = """
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
            fighters = await execute_query_with_retry(fighter_query, (opponent['user_id'], opponent['team_id']))
            
            for fighter in fighters:
                fighter['position'] = position
                opponent_team.append(fighter)
                position += 1
                
        # Randomly select fighters if we have too many
        if len(opponent_team) > 4:
            random.shuffle(opponent_team)
            opponent_team = opponent_team[:4]
            # Reassign positions
            for i, fighter in enumerate(opponent_team):
                fighter['position'] = i
                
        return opponent_team

    async def get_squad_fighters(self, squad_team):
        fighters = []
        for card in squad_team:
            # Get fighter card data from user_cards
            fighter_query = """
            SELECT c.card_id, c.card_name, c.image, c.card_rank, uc.power_stat,
                uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat,
                uc.current_level, %s as position, uc.current_xp, uc.id_enhancer,
                uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed,
                uc.charm_health, uc.attack_count, uc.defence_count, uc.speed_count,
                uc.health_count
            FROM card c
            JOIN user_cards uc ON c.card_id = uc.card_id
            WHERE c.card_id = %s AND uc.user_id = %s
            """
            fighter = await execute_query_with_retry(fighter_query, (card['position'], card['card_id'], card['user_id']))
            if fighter:
                fighters.append(fighter[0])
                
        return fighters

    async def handle_xp_reward(self, ctx, user_id, team_id, is_squad=False):
        if is_squad:
            # Get squad cards and their respective users with all necessary fields
            squad_query = """
            SELECT 
                sc.squadcards_id,
                sc.squad_id,
                sc.card_id,
                sc.user_id,
                sc.position,
                uc.power_stat,
                uc.health_stat,
                uc.speed_stat,
                uc.attack_stat,
                uc.defence_stat,
                uc.current_level,
                uc.current_xp,
                uc.id_enhancer,
                uc.fight_count,
                uc.charm_attack,
                uc.charm_defence,
                uc.charm_speed,
                uc.charm_health,
                uc.attack_count,
                uc.defence_count,
                uc.speed_count,
                uc.health_count,
                c.card_name,
                c.card_rank,
                c.xp_cap,
                c.card_icon,
                c.max_level,
                c.image
            FROM squad_cards sc
            JOIN user_cards uc ON sc.card_id = uc.card_id AND sc.user_id = uc.user_id
            JOIN card c ON sc.card_id = c.card_id
            WHERE sc.squad_id = %s 
            AND c.type_card = 'Fighter'
            ORDER BY sc.position
            """
            
            try:
                squad_cards = await execute_query_with_retry(squad_query, (team_id,))
                print("cards, ",squad_cards)
                if not squad_cards:
                    print(f"No squad cards found for squad_id: {team_id}")
                    return
                    
                print(f"Found {len(squad_cards)} squad cards")
                
                xp_rewards = []
                total_xp_gained = 0
                total_cards_leveled = 0
                
                for card in squad_cards:
                    try:
                        # Calculate and apply rewards for each card
                        reward_data = await self.calculate_card_rewards(card)
                        print(f"Processed rewards for card {card['card_name']}: {reward_data}")
                        
                        xp_rewards.append(reward_data)
                        total_xp_gained += reward_data['xp_gained']
                        if reward_data['level_up']:
                            total_cards_leveled += 1
                            
                    except Exception as card_error:
                        print(f"Error processing card {card.get('card_name', 'Unknown')}: {str(card_error)}")
                        continue
                
                # Send squad rewards embed
                if xp_rewards:
                    await self.send_rewards_embed(ctx, xp_rewards, total_xp_gained, total_cards_leveled)
                else:
                    await ctx.send("No rewards could be processed for the squad battle.")
                    
            except Exception as e:
                print(f"Error in squad rewards processing: {str(e)}")
                traceback.print_exc()
                await ctx.send("An error occurred while processing squad rewards.")
                
        else:
            # Original non-squad reward logic
            await self.handle_xp_rewards(ctx, user_id, team_id)

    async def calculate_card_rewards(self, card):
        """Calculate rewards for a single card after battle."""
        # Calculate base XP reward
        xp_reward = random.randint(10, 30)
        
        # Check enhancer status
        enhancer_active = bool(card['id_enhancer'])
        if enhancer_active:
            xp_reward = int(xp_reward * 1.2)
            
            # Update enhancer usage
            new_fight_count = card['fight_count'] - 1
            if new_fight_count <= 0:
                await execute_query_with_retry(
                    "UPDATE user_cards SET fight_count = 0, id_enhancer = NULL WHERE user_id = %s AND card_id = %s",
                    (card['user_id'], card['card_id'])
                )
            else:
                await execute_query_with_retry(
                    "UPDATE user_cards SET fight_count = %s WHERE user_id = %s AND card_id = %s",
                    (new_fight_count, card['user_id'], card['card_id'])
                )
        
        # Calculate new level and XP
        new_xp = card['current_xp'] + xp_reward
        new_level = card['current_level']
        level_up = False
        
        if new_xp >= card['xp_cap'] and new_level < card['max_level']:
            new_level += 1
            new_xp -= card['xp_cap']
            level_up = True
        
        # Generate stat increases for level up
        stat_increases = {
            'attack': random.randint(2, 4) if level_up else 0,
            'health': random.randint(3, 5) if level_up else 0,
            'defence': random.randint(1, 2) if level_up else 0,
            'speed': random.randint(2, 4) if level_up else 0
        }
        
        # Update card stats in database
        if level_up:
            update_query = """
            UPDATE user_cards
            SET current_level = %s,
                current_xp = %s,
                base_attack = base_attack + %s,
                base_health = base_health + %s,
                base_defence = base_defence + %s,
                base_speed = base_speed+ %s,
                attack_stat = attack_stat + %s,
                health_stat = health_stat + %s,
                defence_stat = defence_stat + %s,
                speed_stat = speed_stat + %s
            WHERE user_id = %s AND card_id = %s
            """
            await execute_query_with_retry(
                update_query,
                (new_level, new_xp,
                stat_increases['attack'],
                stat_increases['health'],
                stat_increases['defence'],
                stat_increases['speed'],
                stat_increases['attack'],
                stat_increases['health'],
                stat_increases['defence'],
                stat_increases['speed'],
                card['user_id'], card['card_id'])
            )
        else:
            # Just update XP if no level up
            await execute_query_with_retry(
                "UPDATE user_cards SET current_xp = %s WHERE user_id = %s AND card_id = %s",
                (new_xp, card['user_id'], card['card_id'])
            )
        
        return {
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
            'max_level_reached': new_level >= card['max_level']
        }

    async def send_squad_rewards_embed(self, ctx, xp_rewards, total_xp_gained, total_cards_leveled):
        embed = discord.Embed(
            title="🏆 Squad Battle Rewards Summary 🏆",
            color=discord.Color.gold(),
            description=f"Total Squad XP Gained: *{total_xp_gained}\nSquad Cards Leveled Up: *{total_cards_leveled}**"
        )
        
        for reward in xp_rewards:
            if reward['max_level_reached']:
                embed.add_field(
                    name=f"{reward['card_icon']} {reward['card_name']}: 🏆 *MAX LEVEL*",
                    value="No further XP or stat increases possible.",
                    inline=False
                )
                continue
                
            progress_bar = self.create_xp_progress_bar((reward['new_xp'] / reward['xp_cap']) * 100)
            
            value = (
                f"{'🔥 Enhanced! ' if reward['enhancer_active'] else ''}"
                f"*+{reward['xp_gained']} XP*\n"
                f"{progress_bar} *{reward['new_xp']}/{reward['xp_cap']}*\n"
            )
            
            if reward['level_up']:
                value += (
                    f"✨ *LEVEL UP!* {reward['old_level']} → {reward['new_level']}\n"
                    f"📈 Stats Increased:\n"
                    f"diff\n"
                    f"+ ATK: +{reward['stat_increases']['attack']}\n"
                    f"+ HP:  +{reward['stat_increases']['health']}\n"
                    f"+ DEF: +{reward['stat_increases']['defence']}\n"
                    f"+ SPD: +{reward['stat_increases']['speed']}\n"
                    f""
                )
                
            embed.add_field(
                name=f"{reward['card_icon']} {reward['card_name']}",
                value=value,
                inline=False
            )
            
        embed.set_footer(text="Squad victory brings greater rewards! | Solo Leveling Squad Battle")
        await ctx.send(embed=embed)

    async def start_fight(self, ctx, user_data, opponent_data, is_challenge, is_squad):
        try:
            # Sort fighters based on position
            user_data['fighters'].sort(key=lambda x: x.get('position', float('inf')))
            opponent_data['fighters'].sort(key=lambda x: x.get('position', float('inf')))
            
            # Track fighter card IDs that participate in the battle
            user_fighter_card_ids = [fighter['card_id'] for fighter in user_data['fighters']]
            
            # Modified user fetching logic to handle squad battles
            user_name = None
            opponent_name = None
            
            if is_squad:
                user = await self.fetch_user_with_retry(user_data['user_authorid'])
                user_name = user.name if user else "Unknown User"
                opponent_name = "Squad Opponent"  # Default name for squad battles
            else:
                # Regular battle user fetching
                user = await self.fetch_user_with_retry(user_data['user_authorid'])
                opponent = await self.fetch_user_with_retry(opponent_data['user_authorid'])
                
                user_name = user.name if user else f"Unknown User (ID: {user_data['user_authorid']})"
                opponent_name = opponent.name if opponent else f"Unknown Opponent (ID: {opponent_data['user_authorid']})"

            # Proceed with the fight
            if is_squad:
                await ctx.send(f"Squad battle starting between {user_name}'s squad and the opponent squad!")
            else:
                await ctx.send(f"Fight starting between {user_name} and {opponent_name}!")
                
            await asyncio.sleep(1)  # 1-second pause before the fight starts

            await self.conduct_fight(ctx, user_data['fighters'], opponent_data['fighters'], user_name, opponent_name, is_challenge, is_squad, user_fighter_card_ids)

        except Exception as e:
            # Create detailed error message
            error_message = (
                f"🚨 <@818132161534230559> Error in fight system!\n"
                f"```\nError: {str(e)}\n"
                f"Channel: {ctx.channel.name} ({ctx.channel.id})\n"
                f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
                f"Command User: {ctx.author.name} ({ctx.author.id})\n"
                f"User data: {json.dumps(user_data, indent=2)}\n"
                f"Opponent data: {json.dumps(opponent_data, indent=2)}```\n"
                f"Traceback:\n```python\n{traceback.format_exc()}```"
            )
            
            try:
                # Send error to support channel
                support_channel = ctx.bot.get_channel(1336306672741908561)
                if support_channel:
                    # Split message if it's too long
                    if len(error_message) > 2000:
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
                print(f"Failed to send error to support channel: {str(send_error)}")
                print(error_message)
                await ctx.send("An unexpected error occurred. Please try again later.")
        finally:
            # Always reset fight_current at the end, regardless of success or failure
            try:
                reset_fight_current_query = """
                UPDATE user
                SET fight_current = 0
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(reset_fight_current_query, (str(ctx.author.id),))
            except Exception as reset_error:
                print(f"Error resetting fight_current: {reset_error}")

    async def check_and_update_quest_progress(self, ctx, user_id, quest_name, is_challenge):
        # Check if the user has the quest
        quest_check_query = """
        SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
        FROM user_quests uq
        JOIN quest q ON uq.quest_id = q.quest_id
        WHERE uq.user_id = %s AND q.quest_name = %s
        """
        quest_result = await execute_query_with_retry(quest_check_query, (user_id, quest_name))
        
        if quest_result:
            quest = quest_result[0]
            if quest['completion_status'] == 0:
                new_count = quest['current_count'] + 1
                update_query = """
                UPDATE user_quests
                SET current_count = %s
                WHERE userquests_id = %s
                """
                await execute_query_with_retry(update_query, (new_count, quest['userquests_id']))
                
                if new_count >= quest['required_count']:
                    # Quest completion logic
                    complete_query = """
                    UPDATE user_quests
                    SET completion_status = 1
                    WHERE userquests_id = %s
                    """
                    await execute_query_with_retry(complete_query, (quest['userquests_id'],))
                    
                    # Get user's inventory_id
                    inventory_query = """
                    SELECT inventory_id 
                    FROM inventory 
                    WHERE user_id = %s
                    """
                    inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
                    
                    if inventory_result:
                        inventory_id = inventory_result[0]['inventory_id']
                        
                        # Check if chest already exists in inventory_chest
                        chest_check_query = """
                        SELECT quantiter 
                        FROM inventory_chest 
                        WHERE inventory_id = %s AND chest_id = 1
                        """
                        chest_result = await execute_query_with_retry(chest_check_query, (inventory_id,))
                        
                        if chest_result:
                            # Update existing chest quantity
                            update_chest_query = """
                            UPDATE inventory_chest
                            SET quantiter = quantiter + 1
                            WHERE inventory_id = %s AND chest_id = 1
                            """
                            await execute_query_with_retry(update_chest_query, (inventory_id,))
                        else:
                            # Insert new chest entry
                            insert_chest_query = """
                            INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                            VALUES (%s, 1, 1)
                            """
                            await execute_query_with_retry(insert_chest_query, (inventory_id,))
                    
                    # Send quest completion message
                else:
                    print(f"📊 Quest progress: {quest_name} - {new_count}/{quest['required_count']}")

    async def conduct_fight(self, ctx, user_fighters, opponent_fighters, user_name, opponent_name, is_challenge, is_squad, fighter_card_ids):
        try:
            def safe_sort_key(x):
                return x.get('position', float('inf'))

            user_team = sorted(user_fighters, key=safe_sort_key)
            opponent_team = sorted(opponent_fighters, key=safe_sort_key)

            if not user_team or not opponent_team:
                await ctx.send("Error: One or both teams are empty. Cannot conduct the fight.")
                reset_stats_query = """
                UPDATE user
                SET fight_current = 0
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(reset_stats_query, (ctx.author.id,))
                return

            # Initialize current_health and determine speed tie resolution
            for fighter in user_team + opponent_team:
                fighter['current_health'] = fighter['health_stat']
            
            # Set a consistent speed tie resolution for the entire fight
            user_goes_first_in_tie = random.choice([True, False])

            embed, file = await self.create_fight_embed(user_team[0], opponent_team[0], user_name, opponent_name, is_challenge=False)
            fight_message = await ctx.send(file=file, embed=embed)
            battle_log = []

            while user_team and opponent_team:
                user_fighter = user_team[0]
                opponent_fighter = opponent_team[0]

                # Modified speed comparison with tie resolution
                if user_fighter['speed_stat'] > opponent_fighter['speed_stat']:
                    fighters = [(user_fighter, True), (opponent_fighter, False)]
                elif user_fighter['speed_stat'] < opponent_fighter['speed_stat']:
                    fighters = [(opponent_fighter, False), (user_fighter, True)]
                else:  # Speed tie
                    if user_goes_first_in_tie:
                        fighters = [(user_fighter, True), (opponent_fighter, False)]
                    else:
                        fighters = [(opponent_fighter, False), (user_fighter, True)]

                for attacker, is_user_turn in fighters:
                    await asyncio.sleep(1)

                    defender = opponent_fighter if is_user_turn else user_fighter

                    battle_log.extend(await self.perform_attack(attacker, defender, is_user_turn))
                    embed = await self.update_fight_embed(embed, user_fighter, opponent_fighter, user_name, opponent_name, battle_log)
                    await fight_message.edit(embed=embed)

                    if defender['current_health'] <= 0:
                        battle_log.append(self.get_defeat_message(defender['card_name'], is_user_turn))
                        if is_user_turn:
                            opponent_team.pop(0)
                            if opponent_team:
                                battle_log.append("🆕 **Next opponent incoming!**")
                                battle_log.append(self.get_new_card_message(opponent_team[0]['card_name'], is_user_turn=False))
                                opponent_fighter = opponent_team[0]
                            else:
                                break
                        else:
                            user_team.pop(0)
                            if user_team:
                                battle_log.append("🔄 **Next card incoming!**")
                                battle_log.append(self.get_new_card_message(user_team[0]['card_name'], is_user_turn=True))
                                user_fighter = user_team[0]
                            else:
                                break

                        new_file = await self.update_fight_image(user_team[0] if user_team else None, opponent_team[0] if opponent_team else None)
                        await fight_message.remove_attachments(fight_message.attachments)
                        await fight_message.edit(attachments=[new_file], embed=embed)

                    if not user_team or not opponent_team:
                        break

            await self.announce_winner(ctx, user_name, opponent_name, user_team, is_challenge, is_squad, fighter_card_ids)

        except Exception as e:
            error_message = (
                f"🚨 <@818132161534230559> Error in fight system!\n"
                f"```\nError: {str(e)}\n"
                f"Channel: {ctx.channel.name} ({ctx.channel.id})\n"
                f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
                f"Command User: {ctx.author.name} ({ctx.author.id})\n"
                f"User data: {json.dumps(user_fighters, indent=2)}\n"
                f"Opponent data: {json.dumps(opponent_fighters, indent=2)}```\n"
                f"Traceback:\n```python\n{traceback.format_exc()}```"
            )
            
            try:
                reset_stats_query = """
                UPDATE user
                SET fight_current = 0
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(reset_stats_query, (ctx.author.id,))
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
        finally:
            # Always reset fight_current at the end, regardless of success or failure
            try:
                reset_fight_current_query = """
                UPDATE user
                SET fight_current = 0
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(reset_fight_current_query, (str(ctx.author.id),))
            except Exception as reset_error:
                print(f"Error resetting fight_current: {reset_error}")

    def calculate_dodge_chance(self, attacker_speed, defender_speed):
        # Prevent division by zero and ensure positive speeds
        attacker_speed = max(1, attacker_speed)
        defender_speed = max(1, defender_speed)
        
        # Relative speed difference calculation
        speed_ratio = defender_speed / attacker_speed
        
        # Logarithmic scaling to create a balanced dodge chance
        # Ensures dodge chance is always non-negative and caps at 30%
        dodge_chance = min(30, max(0, 30 * (1 - math.log(speed_ratio + 1, 2))))
        
        return dodge_chance

    async def perform_attack(self, attacker, defender, is_user_turn):
        # Calculate dodge chance
        dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
        
        # Random dodge check
        dodge_roll = random.random() * 100
        if dodge_roll < dodge_chance:
            # Successful dodge
            return [f"🛡️ {defender['card_name']} dodged the attack!"]
        
        # Base damage calculation
        base_damage = random.randint(
            int(attacker['attack_stat'] * 0.45), 
            int(attacker['attack_stat'] * 0.55)
        )
        
        # Critical hit calculation
        # Base crit chance: 5-10% depending on attack stat
        crit_chance = min(10, max(5, attacker['attack_stat'] / 100))
        crit_roll = random.random() * 100
        is_critical = crit_roll < crit_chance
        
        # Critical hit multiplier
        crit_multiplier = 1.5 if is_critical else 1.0
        
        # Damage calculation with critical hit
        effective_damage = self.calculate_effective_damage(
            int(base_damage * crit_multiplier), 
            defender['defence_stat']
        )
        
        # Reduce defender's health
        defender['current_health'] -= effective_damage
        defender['current_health'] = max(0, defender['current_health'])
        
        # Prepare attack message
        attack_message = self.get_attack_message(
            attacker['card_name'], 
            effective_damage, 
            base_damage, 
            is_user_turn, 
            is_critical
        )
        
        return [attack_message]

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

    def get_defeat_message(self, defeated_name, is_user_turn):
        emoji = "🏆" if is_user_turn else "💀"
        return f"{emoji} **{defeated_name} has been defeated!**"

    def get_new_card_message(self, new_card_name, is_user_turn):
        emoji = "🔄" if is_user_turn else "🆕"
        return f"{emoji} New {'user' if is_user_turn else 'opponent'} card: {new_card_name}"

    async def announce_winner(self, ctx, user_name, opponent_name, user_team, is_challenge, is_squad, fighter_card_ids):
        # Reset fight_current immediately to avoid stale states
        reset_fight_current_query = """
        UPDATE user
        SET fight_current = 0
        WHERE user_authorid = %s
        """
        await execute_query_with_retry(reset_fight_current_query, (str(ctx.author.id),))
        
        if user_team:
            # User won the fight
            if is_squad:
                await ctx.send(f"**{user_name}'s squad has won the fight!**")
            else:
                await ctx.send(f"**{user_name} has won the fight!**")
            
            user_data = await self.get_user_data_with_retry(ctx.author.id)

            if is_challenge:
                # Handle challenge win
                await self.check_and_update_quest_progress(ctx, user_data['user_id'], "Win 5 Challenges", is_challenge)
            else:
                if is_squad:
                    # Get all users in the squad
                    get_squad_members_query = """
                    SELECT user_id
                    FROM user
                    WHERE squad_id = %s
                    """
                    squad_members = await execute_query_with_retry(get_squad_members_query, (user_data['squad_id'],))
                    
                    # Update stats for all squad members
                    update_squad_stats_query = """
                    UPDATE user 
                    SET squad_streak = squad_streak + 1,
                        alltime_squad = alltime_squad + 1,
                        squad_current = 0
                    WHERE user_id = %s
                    """
                    
                    # Apply updates to all squad members
                    for member in squad_members:
                        await execute_query_with_retry(update_squad_stats_query, (member['user_id'],))
                    
                    # Handle XP rewards for squad
                    await self.handle_xp_reward(ctx, user_data['user_id'], user_data['squad_id'], is_squad)
                else:
                    # Handle individual fight win
                    update_stats_query = """
                    UPDATE user 
                    SET win_streak = win_streak + 1,
                        alltimewins = alltimewins + 1,
                        fight_current = 0
                    WHERE user_id = %s
                    """
                    await execute_query_with_retry(update_stats_query, (user_data['user_id'],))
                    await self.handle_xp_rewards(ctx, user_data['user_id'], user_data['team_id'], fighter_card_ids)
                
                await self.check_and_update_quest_progress(ctx, user_data['user_id'], "Win 10 Fights", is_challenge)
        else:
            # User lost the fight
            await ctx.send(f"**{opponent_name} has won the fight!**")
            await ctx.send(f"{user_name}, you've lost this battle. Better luck next time!")
            
            if is_squad:
                # Reset squad streak and squad_current for all squad members
                user_data = await self.get_user_data_with_retry(ctx.author.id)
                get_squad_members_query = """
                SELECT user_id
                FROM user
                WHERE squad_id = %s
                """
                squad_members = await execute_query_with_retry(get_squad_members_query, (user_data['squad_id'],))
                
                reset_squad_stats_query = """
                UPDATE user
                SET squad_streak = 0,
                    squad_current = 0
                WHERE user_id = %s
                """
                
                for member in squad_members:
                    await execute_query_with_retry(reset_squad_stats_query, (member['user_id'],))
            else:
                # Reset individual fight stats
                reset_stats_query = """
                UPDATE user
                SET win_streak = 0,
                    fight_current = 0
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(reset_stats_query, (ctx.author.id,))

    async def handle_fight_error(self, ctx, error, user_fighters, opponent_fighters):
        error_message = f"An unexpected error occurred in conduct_fight: {str(error)}"
        print(error_message)
        print(f"User fighters: {user_fighters}")
        print(f"Opponent fighters: {opponent_fighters}")
        print("Traceback:")
        traceback.print_exc()
        await ctx.send(f"An unexpected error occurred during the fight. Please try again later or contact an administrator. Error details: {str(error)}")

    def create_composite_image(self, image1_path, image2_path, rank1, rank2, spacing=200):
        with Image.open(image1_path) as img1, Image.open(image2_path) as img2:
            # Resize images to the specified size
            img1 = img1.resize(self.image_size)
            img2 = img2.resize(self.image_size)

            # Create new images with borders
            border_size = 5
            img1_with_border = Image.new('RGBA', (self.image_size[0] + 2*border_size, self.image_size[1] + 2*border_size), self.get_rank_color(rank1))
            img2_with_border = Image.new('RGBA', (self.image_size[0] + 2*border_size, self.image_size[1] + 2*border_size), self.get_rank_color(rank2))

            img1_with_border.paste(img1, (border_size, border_size), img1 if img1.mode == 'RGBA' else None)
            img2_with_border.paste(img2, (border_size, border_size), img2 if img2.mode == 'RGBA' else None)

            # Create a new image with the combined width and height
            total_width = (self.image_size[0] + 2*border_size) * 2 + spacing
            max_height = self.image_size[1] + 2*border_size
            composite = Image.new('RGBA', (total_width, max_height), (0, 0, 0, 0))  # Transparent background

            # Paste the images with spacing
            composite.paste(img1_with_border, (0, 0), img1_with_border)
            composite.paste(img2_with_border, (self.image_size[0] + 2*border_size + spacing, 0), img2_with_border)

            # Add "VS" text
            draw = ImageDraw.Draw(composite)
            poppins_font_path = os.path.join(os.path.dirname(__file__), 'Poppins-Regular.ttf')  # Adjust path as needed
            font = ImageFont.truetype(poppins_font_path, 60)  # Adjust font and size as needed
            vs_text = "VS"
            vs_bbox = draw.textbbox((0, 0), vs_text, font=font)
            vs_width, vs_height = vs_bbox[2] - vs_bbox[0], vs_bbox[3] - vs_bbox[1]
            vs_position = ((total_width - vs_width) // 2, (max_height - vs_height) // 2)
            draw.text(vs_position, vs_text, font=font, fill=(255, 255, 255))  # White color

            # Save the composite image to a bytes buffer
            buffer = io.BytesIO()
            composite.save(buffer, format='PNG')
            buffer.seek(0)

            return buffer

    async def create_fight_embed(self, user_card, opponent_card, user_name, opponent_name, is_challenge=False):
        embed = discord.Embed(title="⚔️ **Epic PVP Battle** ⚔️", color=discord.Color.dark_gold())
        embed.set_author(name="Solo Leveling Bot", icon_url="https://example.com/solo_leveling_icon.png")

        embed.add_field(
            name=f"🔵 **{user_name}'s Fighter**",
            value=self.create_card_info(user_card, is_user=True, name=user_name, is_challenge=is_challenge),
            inline=True
        )
        embed.add_field(
            name=f"🔴 **{opponent_name}'s Fighter**",
            value=self.create_card_info(opponent_card, is_user=False, name=opponent_name, is_challenge=is_challenge),
            inline=True
        )

        embed.set_thumbnail(url="https://example.com/battle_icon.png")
        embed.set_footer(text="May the strongest hunter prevail! 💪 | Solo Leveling PVP")

        file = await self.update_fight_image(user_card, opponent_card)
        embed.set_image(url="attachment://battle_cards.png")

        return embed, file

    async def update_fight_embed(self, embed, user_card, opponent_card, user_name, opponent_name, battle_log, is_challenge=False):
        embed.clear_fields()

        embed.add_field(
            name=f"🔵 **{user_name}'s Fighter**",
            value=self.create_card_info(user_card, is_user=True, name=user_name, is_challenge=is_challenge),
            inline=True
        )
        embed.add_field(
            name=f"🔴 **{opponent_name}'s Fighter**",
            value=self.create_card_info(opponent_card, is_user=False, name=opponent_name, is_challenge=is_challenge),
            inline=True
        )

        embed.add_field(
            name="🗒️ **Battle Log**",
            value=f"```css\n{'\n'.join(battle_log[-5:])}\n```",
            inline=False
        )

        return embed

    async def handle_fight_errord(self, ctx, error, user_fighters, opponent_fighters):
        # Support channel ID and owner ID
        SUPPORT_CHANNEL_ID = 1336306672741908561
        OWNER_ID = 818132161534230559
        
        try:
            # Get the support channel
            support_channel = self.bot.get_channel(SUPPORT_CHANNEL_ID)
            
            if support_channel:
                # Create detailed error message
                error_embed = discord.Embed(
                    title="⚠️ Fight Error Report",
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.utcnow()
                )
                
                error_embed.add_field(
                    name="Error Type",
                    value=f"```python\n{type(error).__name__}```",
                    inline=False
                )
                
                error_embed.add_field(
                    name="Error Message",
                    value=f"```python\n{str(error)}```",
                    inline=False
                )
                
                error_embed.add_field(
                    name="Server",
                    value=f"{ctx.guild.name} ({ctx.guild.id})",
                    inline=True
                )
                
                error_embed.add_field(
                    name="Channel",
                    value=f"{ctx.channel.name} ({ctx.channel.id})",
                    inline=True
                )
                
                error_embed.add_field(
                    name="User",
                    value=f"{ctx.author} ({ctx.author.id})",
                    inline=True
                )
                
                # Add fighter data if available
                if user_fighters and opponent_fighters:
                    fighter_data = f"User Fighters: {len(user_fighters)}\nOpponent Fighters: {len(opponent_fighters)}"
                    error_embed.add_field(
                        name="Fight Data",
                        value=f"```{fighter_data}```",
                        inline=False
                    )
                
                # Get traceback information
                tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
                if len(tb) > 1000:
                    tb = tb[:997] + "..."
                
                error_embed.add_field(
                    name="Traceback",
                    value=f"```python\n{tb}```",
                    inline=False
                )
                
                # Send error report and ping owner
                await support_channel.send(
                    content=f"<@{OWNER_ID}> New error reported!",
                    embed=error_embed
                )
                
                # Send a generic message to the user in the original channel
                user_message = "An unexpected error occurred during the fight. The development team has been notified."
                if ctx.message:
                    try:
                        await ctx.send(user_message)
                    except:
                        pass  # If we can't send to the original channel, just continue
                        
        except Exception as e:
            # If error handling itself fails, print to console as last resort
            print(f"Error in error handler: {str(e)}")
            traceback.print_exc()

    async def update_fight_image(self, user_card, opponent_card):
        if not user_card or not opponent_card:
            # Create a blank image if one of the cards is missing
            blank_image = Image.new('RGBA', self.image_size, (0, 0, 0, 0))
            buffer = io.BytesIO()
            blank_image.save(buffer, format='PNG')
            buffer.seek(0)
            return discord.File(buffer, filename="battle_cards.png")

        user_image_path = os.path.join(self.image_path, user_card['image'])
        opponent_image_path = os.path.join(self.image_path, opponent_card['image'])
        composite_image = self.create_composite_image(user_image_path, opponent_image_path, user_card['card_rank'], opponent_card['card_rank'])

        return discord.File(composite_image, filename="battle_cards.png")

    def create_card_info(self, card, is_user, name, is_challenge=False):
        # Health bar and card details
        health_percentage = (card['current_health'] / card['health_stat']) * 100
        health_bar = self.create_health_bar(health_percentage, is_user, is_challenge)

        # Return formatted card information
        return (
            f"**{card['card_name']}** (Lv.{card['current_level']})\n"
            f"{health_bar} {card['current_health']}/{card['health_stat']} HP\n"
            f"🔸 **POW**: {card['power_stat']}\n"
            f"🔸 **ATK**: {card['attack_stat']}\n"
            f"🔸 **DEF**: {card['defence_stat']}\n"
            f"🔸 **SPD**: {card['speed_stat']}\n"
        )

    def create_health_bar(self, percentage, is_user, is_challenge=False):
        # Calculate the number of filled and empty blocks for the health bar
        filled_blocks = min(10, max(0, int(percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_challenge:
            filled_square = self.green_square
        elif is_user:
            filled_square = self.green_square
        else:
            filled_square = self.red_square

        # Create the health bar string with filled and empty blocks
        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"

        # Return the health bar string with percentage
        return f"{health_bar} **({percentage:.1f}%)**"

    async def handle_xp_rewards(self, ctx, user_id, team_id, fighter_card_ids):
        # Get fighter cards that participated in the battle
        placeholders = ', '.join(['%s'] * len(fighter_card_ids))
        cards_query = f"""
        SELECT c.card_id, c.card_name, c.card_icon, c.card_rank, c.xp_cap, c.max_level, 
            uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat,
            uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
            uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
            uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
            uc.weapon_id, uc.armor_id, uc.shield_id
        FROM user_cards uc
        JOIN card c ON uc.card_id = c.card_id
        WHERE uc.user_id = %s AND uc.card_id IN ({placeholders}) AND c.type_card = 'Fighter'
        """
        
        # Create parameter list with user_id and all fighter card IDs
        params = [user_id] + fighter_card_ids
        
        # Execute the query with all parameters
        battle_cards = await execute_query_with_retry(cards_query, params)

        if not battle_cards:
            await ctx.send("No cards found that participated in the battle. Unable to process XP rewards.")
            return

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

        for card in battle_cards:
            # Skip XP and stat increases for max-level cards
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
                    'stat_increases': {
                        'attack': 0,
                        'health': 0,
                        'defence': 0,
                        'speed': 0
                    },
                    'new_stats': {
                        'attack': card['attack_stat'],
                        'health': card['health_stat'],
                        'defence': card['defence_stat'],
                        'speed': card['speed_stat']
                    },
                    'max_level_reached': True
                })
                continue

            # Calculate XP reward
            xp_reward = random.randint(20, 100)
            total_xp_gained += xp_reward

            # Check for enhancer and apply bonus
            enhancer_active = False
            if card.get('id_enhancer'):
                new_fight_count = card['fight_count'] - 1
                xp_reward = int(xp_reward * 1.2)  # 20% bonus
                enhancer_active = True
                
                if new_fight_count <= 0:
                    await execute_query_with_retry(
                        "UPDATE user_cards SET fight_count = 0, id_enhancer = NULL WHERE user_id = %s AND card_id = %s",
                        (user_id, card['card_id'])
                    )
                else:
                    await execute_query_with_retry(
                        "UPDATE user_cards SET fight_count = %s WHERE user_id = %s AND card_id = %s",
                        (new_fight_count, user_id, card['card_id'])
                    )

            # Calculate new level and XP
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
                    new_xp = card['xp_cap']

            # Generate stat increases if leveled up
            stat_increases = {
                'attack': random.randint(2, 4) if level_up else 0,
                'health': random.randint(3, 5) if level_up else 0,
                'defence': random.randint(1, 2) if level_up else 0,
                'speed': random.randint(2, 4) if level_up else 0
            }

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
                    'attack': card['base_attack'] + stat_increases['attack'],
                    'health': card['base_health'] + stat_increases['health'],
                    'defence': card['base_defence'] + stat_increases['defence'],
                    'speed': card['base_speed'] + stat_increases['speed']
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
                    'attack': true_base_stats['attack'] + stat_increases['attack'],
                    'health': true_base_stats['health'] + stat_increases['health'],
                    'defence': true_base_stats['defence'] + stat_increases['defence'],
                    'speed': true_base_stats['speed'] + stat_increases['speed']
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
                    'attack': true_base_stats['attack'] + stat_increases['attack'],
                    'health': true_base_stats['health'] + stat_increases['health'],
                    'defence': true_base_stats['defence'] + stat_increases['defence'],
                    'speed': true_base_stats['speed'] + stat_increases['speed']
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

            # Add reward data to the list
            xp_rewards.append({
                'card_name': card['card_name'],
                'xp_gained': xp_reward,
                'enhancer_active': enhancer_active,
                'new_level': new_level,
                'old_level': card['current_level'],
                'new_xp': new_xp,
                'xp_cap': card['xp_cap'],
                'level_up': level_up,
                'stat_increases': stat_increases,
                'new_stats': new_stats,
                'card_icon': card['card_icon'],
                'max_level_reached': new_level == card['max_level']
            })

        # Create and send the rewards embed
        await self.send_rewards_embed(ctx, xp_rewards, total_xp_gained, total_cards_leveled)

    # Helper methods needed for the improved handle_xp_rewards function
    async def calculate_total_support_boosts(self, team_id, user_id, exclude_card_id=None):
        """
        Calculate the total flat and percentage boosts from all support cards in the team
        Optionally exclude a specific card (useful when removing a support)
        """
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
        params = (team_id, user_id)
        
        if exclude_card_id:
            query += " AND c.card_id != %s"
            params += (exclude_card_id,)
            
        support_cards = await execute_query_with_retry(query, params)
        
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
            # Add support card flat boosts
            total_flat_attack += support['attack'] or 0
            total_flat_health += support['health'] or 0
            total_flat_speed += support['speed'] or 0
            total_flat_defence += support['defence'] or 0
            
            # Add support card percentage boosts
            total_pct_attack += support['attack_percentage'] or 0
            total_pct_health += support['health_percentage'] or 0
            total_pct_speed += support['speed_percentage'] or 0
            total_pct_defence += support['defence_percentage'] or 0
            
            # Add equipment boosts
            equipment_types = [
                support['weapon_id'],
                support['armor_id'],
                support['shield_id']
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
                        total_flat_attack += eq_details.get('attack_boost', 0) or 0
                        total_flat_health += eq_details.get('health_boost', 0) or 0
                        total_flat_speed += eq_details.get('speed_boost', 0) or 0
                        total_flat_defence += eq_details.get('defense_boost', 0) or 0
                        
                        # Add equipment percentage boosts
                        total_pct_attack += eq_details.get('attack_percentage', 0) or 0
                        total_pct_health += eq_details.get('health_percentage', 0) or 0
                        total_pct_speed += eq_details.get('speed_percentage', 0) or 0
                        total_pct_defence += eq_details.get('defense_percentage', 0) or 0
        
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
    
    async def reset_offense_cards_to_base(self, team_id, user_id):
        """Reset all offense cards in the team to their base stats"""
        query = """
        UPDATE user_cards uc
        JOIN team_cards tc ON uc.card_id = tc.card_id
        JOIN card c ON uc.card_id = c.card_id
        SET 
            uc.attack_stat = uc.base_attack,
            uc.health_stat = uc.base_health,
            uc.speed_stat = uc.base_speed,
            uc.defence_stat = uc.base_defence
        WHERE 
            tc.team_id = %s AND 
            uc.user_id = %s AND 
            c.type_card = 'Fighter'
        """
        await execute_query_with_retry(query, (team_id, user_id))
    
    async def apply_support_boosts_to_offense(self, team_id, user_id, boosts):
        """Apply the given boosts to all offense cards in the team"""
        # Apply flat boosts first
        query = """
        UPDATE user_cards uc
        JOIN team_cards tc ON uc.card_id = tc.card_id
        JOIN card c ON uc.card_id = c.card_id
        SET 
            uc.attack_stat = uc.attack_stat + %s,
            uc.health_stat = uc.health_stat + %s,
            uc.speed_stat = uc.speed_stat + %s,
            uc.defence_stat = uc.defence_stat + %s
        WHERE 
            tc.team_id = %s AND 
            uc.user_id = %s AND 
            c.type_card = 'Fighter'
        """
        await execute_query_with_retry(query, (
            boosts['flat_attack'],
            boosts['flat_health'],
            boosts['flat_speed'],
            boosts['flat_defence'],
            team_id,
            user_id
        ))
        
        # Then apply percentage boosts
        query = """
        UPDATE user_cards uc
        JOIN team_cards tc ON uc.card_id = tc.card_id
        JOIN card c ON uc.card_id = c.card_id
        SET 
            uc.attack_stat = uc.attack_stat * (1 + %s/100),
            uc.health_stat = uc.health_stat * (1 + %s/100),
            uc.speed_stat = uc.speed_stat * (1 + %s/100),
            uc.defence_stat = uc.defence_stat * (1 + %s/100)
        WHERE 
            tc.team_id = %s AND 
            uc.user_id = %s AND 
            c.type_card = 'Fighter'
        """
        await execute_query_with_retry(query, (
            boosts['pct_attack'],
            boosts['pct_health'],
            boosts['pct_speed'],
            boosts['pct_defence'],
            team_id,
            user_id
        ))
    
    async def apply_support_boosts_to_single_offense(self, user_id, card_id, boosts):
        """Apply the given boosts to a single offense card"""
        # Apply flat boosts first
        query = """
        UPDATE user_cards uc
        SET 
            uc.attack_stat = uc.attack_stat + %s,
            uc.health_stat = uc.health_stat + %s,
            uc.speed_stat = uc.speed_stat + %s,
            uc.defence_stat = uc.defence_stat + %s
        WHERE 
            uc.user_id = %s AND 
            uc.card_id = %s
        """
        await execute_query_with_retry(query, (
            boosts['flat_attack'],
            boosts['flat_health'],
            boosts['flat_speed'],
            boosts['flat_defence'],
            user_id,
            card_id
        ))
        
        # Then apply percentage boosts
        query = """
        UPDATE user_cards uc
        SET 
            uc.attack_stat = uc.attack_stat * (1 + %s/100),
            uc.health_stat = uc.health_stat * (1 + %s/100),
            uc.speed_stat = uc.speed_stat * (1 + %s/100),
            uc.defence_stat = uc.defence_stat * (1 + %s/100)
        WHERE 
            uc.user_id = %s AND 
            uc.card_id = %s
        """
        await execute_query_with_retry(query, (
            boosts['pct_attack'],
            boosts['pct_health'],
            boosts['pct_speed'],
            boosts['pct_defence'],
            user_id,
            card_id
        ))
    

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

    async def remove_equipment_boosts(self, user_id, card_id, equipment_boosts):
        """Remove equipment boosts from a card's stats"""
        if not equipment_boosts:
            return
        
        # Get current stats
        stats_query = """
        SELECT attack_stat, health_stat, defence_stat, speed_stat
        FROM user_cards
        WHERE user_id = %s AND card_id = %s
        """
        stats = await execute_query_with_retry(stats_query, (user_id, card_id))
        
        if not stats:
            return
        
        stats = stats[0]
        
        # Remove percentage boosts first (in reverse order of application)
        new_attack = stats['attack_stat'] / (1 + equipment_boosts['pct_attack']/100)
        new_health = stats['health_stat'] / (1 + equipment_boosts['pct_health']/100)
        new_defence = stats['defence_stat'] / (1 + equipment_boosts['pct_defence']/100)
        new_speed = stats['speed_stat'] / (1 + equipment_boosts['pct_speed']/100)
        
        # Then remove flat boosts
        new_attack -= equipment_boosts['flat_attack']
        new_health -= equipment_boosts['flat_health']
        new_defence -= equipment_boosts['flat_defence']
        new_speed -= equipment_boosts['flat_speed']
        
        # Update stats
        update_query = """
        UPDATE user_cards
        SET attack_stat = %s,
            health_stat = %s,
            defence_stat = %s,
            speed_stat = %s
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            update_query,
            (new_attack, new_health, new_defence, new_speed, user_id, card_id)
        )

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
    async def send_rewards_embed(self, ctx, xp_rewards, total_xp_gained, total_cards_leveled):
        embed = discord.Embed(
            title="🏆 Battle Rewards Summary 🏆",
            color=discord.Color.gold(),
            description=f"Total XP Gained: **{total_xp_gained}**\nCards Leveled Up: **{total_cards_leveled}**"
        )

        # Add a field for each card's rewards
        for reward in xp_rewards:
            # Skip max level cards from displaying in a different way
            if reward['max_level_reached']:
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

    def create_xp_progress_bar(self, percentage, length=10):
        filled = int((percentage / 100.0) * length)
        empty = length - filled
        
        # Use custom emotes for the progress bar
        bar = (self.green_square * filled) + (self.gray_square * empty)
        return f"{bar} **{percentage:.1f}%**"

    async def apply_charm_effects(self, fighters, user_id):
        for fighter in fighters:
            charm_updates = []
            stat_updates = []
            print("hey charms")
            print("current fighter in check :", fighter)
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
                await execute_query_with_retry(update_query, (user_id, fighter['card_id']))

        return fighters

    # Simplified alternative with server links
async def setup(bot):
    await bot.add_cog(EmFightCommands(bot))
