import discord
from discord.ext import commands
import aiohttp
from datetime import datetime, timedelta
import asyncio
import traceback
import os
from typing import Dict, Any, Optional, Tuple, List
from enum import Enum
from collections import deque
from utils.database import execute_query_with_retry, get_metrics
from utils.db_atomic import db_advisory_lock, db_transaction
import functools
from aiohttp import web
import hmac
import hashlib
from config import TOPGG_WEBHOOK_SECRET as CFG_WEBHOOK_SECRET
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

class VoteCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Get API token from environment variable or use a default for development
        self.topgg_api_token = os.getenv("TOPGG_API_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJib3QiOiJ0cnVlIiwiaWQiOiIxMjMxMjk4MzI5NTQ4MjkyMTk3IiwiaWF0IjoiMTc0MzgwMjAyNiJ9.f6CO-3v5I0upRoVi1Bw5E0PZ7ughvFw9RJQakueX04k")
        self.topgg_vote_url = "https://top.gg/bot/1231298329548292197/vote"
        self.topgg_webhook_secret = os.getenv("TOPGG_WEBHOOK_SECRET", CFG_WEBHOOK_SECRET)  # Set this in your environment
       
        # Set for tracking users who have been reminded
        self.reminded_users = set()
       
        # Set for tracking users currently being processed (prevent duplicate processing)
        self.processing_votes = set()
       
        # Start background task for periodic health checks
        self.health_check_task = asyncio.create_task(self.periodic_health_check())
       
        # Start webhook server
        self.webhook_task = None
    async def cog_load(self):
        """Called when the cog is loaded. This is an async context where we can safely start tasks."""
        # Start the reminder task
        self.reminder_task = asyncio.create_task(self.check_vote_reminders())
       
        # Start webhook server
        self.webhook_task = asyncio.create_task(self.start_webhook_server())
       
    def cog_unload(self):
        # Cancel the reminder task when the cog is unloaded
        if hasattr(self, 'reminder_task') and self.reminder_task:
            self.reminder_task.cancel()
       
        # Cancel the health check task
        if hasattr(self, 'health_check_task') and self.health_check_task:
            self.health_check_task.cancel()
           
        # Cancel webhook server
        if hasattr(self, 'webhook_task') and self.webhook_task:
            self.webhook_task.cancel()
           
    async def start_webhook_server(self):
        """Start the webhook server to receive vote notifications from top.gg"""
        try:
            app = web.Application()
            app.router.add_post('/topgg-webhook', self.handle_topgg_webhook)
           
            runner = web.AppRunner(app)
            await runner.setup()
           
            # Use port from environment or default to 8080
            port = int(os.getenv("WEBHOOK_PORT", "5555"))
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
           
            print(f"Top.gg webhook server started on port {port}")
           
            # Keep the server running
            while True:
                await asyncio.sleep(3600)
               
        except asyncio.CancelledError:
            print("Webhook server stopped")
        except Exception as e:
            print(f"Error starting webhook server: {e}")
            print(traceback.format_exc())
   
    async def handle_topgg_webhook(self, request):
        """Handle incoming webhook from top.gg when a user votes"""
        try:
            # Verify the webhook signature if secret is set
            if self.topgg_webhook_secret:
                auth_header = request.headers.get('Authorization', '')
                if not self.verify_webhook_signature(auth_header):
                    print("Invalid webhook signature")
                    return web.Response(status=401, text="Unauthorized")
           
            # Parse the webhook data
            data = await request.json()
            user_id = int(data.get('user', 0))
            bot_id = int(data.get('bot', 0))
            vote_type = data.get('type', 'upvote')
           
            # Verify it's for our bot
            if bot_id != 1231298329548292197:
                return web.Response(status=200, text="OK")
           
            # Only process upvotes
            if vote_type != 'upvote':
                return web.Response(status=200, text="OK")
           
            # <CHANGE> Prevent duplicate processing for the same user
            if user_id in self.processing_votes:
                print(f"Vote already being processed for user {user_id}, skipping duplicate")
                return web.Response(status=200, text="OK")
           
            # Add to processing set
            self.processing_votes.add(user_id)
           
            try:
                # Process the vote asynchronously
                asyncio.create_task(self.process_vote_reward(user_id))
            finally:
                # Remove from processing set after a delay to prevent rapid duplicates
                asyncio.create_task(self.remove_from_processing(user_id, delay=5))
           
            return web.Response(status=200, text="OK")
           
        except Exception as e:
            print(f"Error handling webhook: {e}")
            print(traceback.format_exc())
            return web.Response(status=500, text="Internal Server Error")
   
    async def remove_from_processing(self, user_id: int, delay: int = 5):
        """Remove user from processing set after a delay"""
        await asyncio.sleep(delay)
        if user_id in self.processing_votes:
            self.processing_votes.discard(user_id)
    
    def verify_webhook_signature(self, auth_header: str) -> bool:
        secret = (self.topgg_webhook_secret or "").strip()
        received = (auth_header or "").strip()
        return hmac.compare_digest(received, secret)

    async def process_vote_reward_atomic(self, user_id: int):
        current_time = datetime.now()
        quest_completed = False
        already_claimed_until = None

        async with db_advisory_lock(f"slbot:vote:{user_id}", timeout=5):
            async with db_transaction():
                user_rows = await execute_query_with_retry(
                    """
                    SELECT user_id, votecount, votestreak, vote_timer
                    FROM user
                    WHERE user_authorid = %s
                    FOR UPDATE
                    """,
                    (str(user_id),)
                )
                if not user_rows:
                    print(f"User {user_id} not found in database, cannot process vote")
                    return None

                user_data = user_rows[0]
                db_user_id = user_data['user_id']
                vote_timer = user_data.get('vote_timer')

                if vote_timer:
                    time_since_vote = current_time - vote_timer
                    if time_since_vote.total_seconds() < 43200:
                        already_claimed_until = vote_timer + timedelta(hours=12)
                        return {
                            "already_claimed_until": already_claimed_until,
                            "votecount": user_data['votecount'],
                            "votestreak": user_data['votestreak'],
                            "quest_completed": False,
                        }

                votecount = user_data['votecount'] + 1
                if vote_timer is None:
                    votestreak = 1
                else:
                    time_diff = current_time - vote_timer
                    votestreak = 1 if time_diff.total_seconds() > 86400 else user_data['votestreak'] + 1

                await execute_query_with_retry(
                    """
                    UPDATE user
                    SET votecount = %s,
                        votestreak = %s,
                        vote_timer = %s,
                        candy_currency = COALESCE(candy_currency, 0) + %s
                    WHERE user_id = %s
                    """,
                    (votecount, votestreak, current_time, 20, db_user_id)
                )

                inventory_rows = await execute_query_with_retry(
                    "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                    (db_user_id,)
                )
                if not inventory_rows:
                    print(f"Could not find inventory for user {user_id}")
                    return None

                inventory_id = inventory_rows[0]['inventory_id']
                product_rows = await execute_query_with_retry(
                    """
                    SELECT quantiter
                    FROM inventory_product
                    WHERE inventory_id = %s AND product_id = 2
                    FOR UPDATE
                    """,
                    (inventory_id,)
                )
                if product_rows:
                    await execute_query_with_retry(
                        """
                        UPDATE inventory_product
                        SET quantiter = quantiter + 1
                        WHERE inventory_id = %s AND product_id = 2
                        """,
                        (inventory_id,)
                    )
                else:
                    await execute_query_with_retry(
                        """
                        INSERT INTO inventory_product (inventory_id, product_id, quantiter)
                        VALUES (%s, 2, 1)
                        """,
                        (inventory_id,)
                    )

                quest_rows = await execute_query_with_retry(
                    """
                    SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
                    FROM user_quests uq
                    JOIN quest q ON uq.quest_id = q.quest_id
                    WHERE uq.user_id = %s AND q.quest_name = 'Vote 1 time'
                    FOR UPDATE
                    """,
                    (db_user_id,)
                )
                if quest_rows and quest_rows[0]['completion_status'] == 0:
                    quest = quest_rows[0]
                    new_count = quest['current_count'] + 1
                    completion_status = 1 if new_count >= quest['required_count'] else 0
                    await execute_query_with_retry(
                        """
                        UPDATE user_quests
                        SET current_count = %s, completion_status = %s
                        WHERE userquests_id = %s
                        """,
                        (new_count, completion_status, quest['userquests_id'])
                    )
                    if completion_status == 1:
                        quest_completed = True
                        chest_rows = await execute_query_with_retry(
                            """
                            SELECT quantiter
                            FROM inventory_chest
                            WHERE inventory_id = %s AND chest_id = 1
                            FOR UPDATE
                            """,
                            (inventory_id,)
                        )
                        if chest_rows:
                            await execute_query_with_retry(
                                """
                                UPDATE inventory_chest
                                SET quantiter = quantiter + 1
                                WHERE inventory_id = %s AND chest_id = 1
                                """,
                                (inventory_id,)
                            )
                        else:
                            await execute_query_with_retry(
                                """
                                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                                VALUES (%s, 1, 1)
                                """,
                                (inventory_id,)
                            )

        return {
            "already_claimed_until": already_claimed_until,
            "votecount": votecount,
            "votestreak": votestreak,
            "quest_completed": quest_completed,
        }

   
    async def process_vote_reward(self, user_id: int):
        """Process vote rewards and send DM to user"""
        try:
            reward_result = await self.process_vote_reward_atomic(user_id)
            if not reward_result:
                return

            user = await self.bot.fetch_user(user_id)
            already_claimed_until = reward_result["already_claimed_until"]
            if already_claimed_until:
                already_claimed_embed = discord.Embed(
                    title="⏰ Rewards Already Claimed",
                    description="You've already claimed your vote rewards! Please wait before voting again.",
                    color=discord.Color.orange()
                )
                time_until_next = already_claimed_until - datetime.now()
                hours_until_next = max(0, time_until_next.total_seconds() / 3600)
                already_claimed_embed.add_field(
                    name="Next Vote Available",
                    value=f"You can vote again in **{hours_until_next:.1f} hours**.",
                    inline=False
                )
                await user.send(embed=already_claimed_embed)
                return

            votecount = reward_result["votecount"]
            votestreak = reward_result["votestreak"]
            quest_completed = reward_result["quest_completed"]

            success_embed = discord.Embed(
                title="🎉 Vote Successful!",
                description="Thank you for supporting Solo Leveling Bot!",
                color=discord.Color.green()
            )
            success_embed.add_field(
                name="Rewards Received",
                value=f"• 1x <:reroll:1302672409278545940>\n• +20 <:candy:1426599572012269578>\n• Vote count: **{votecount}**\n• Vote streak: **{votestreak}** 🔥",
                inline=False
            )
            if quest_completed:
                success_embed.add_field(
                    name="🎯 Quest Completed!",
                    value="You've completed the **'Vote 1 time'** quest and received your rewards!",
                    inline=False
                )
            if votestreak >= 7:
                success_embed.add_field(
                    name="🔥 Streak Bonus!",
                    value=f"Amazing! You've voted **{votestreak}** days in a row! Keep it up!",
                    inline=False
                )
            success_embed.add_field(
                name="Next Vote",
                value="You can vote again in **12 hours**!",
                inline=False
            )
            if user.avatar:
                success_embed.set_thumbnail(url=user.avatar.url)
            success_embed.set_footer(text="Thank you for your support! ❤️")
            success_embed.timestamp = datetime.now()
            await user.send(embed=success_embed)
            print(f"Successfully processed vote and sent DM to user {user_id}")
            return

            # Get user from database
            query = "SELECT user_id, votecount, votestreak, vote_timer FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(user_id),))
           
            if not result or len(result) == 0:
                print(f"User {user_id} not found in database, cannot process vote")
                return
           
            user_data = result[0]
            db_user_id = user_data['user_id']
            vote_timer = user_data.get('vote_timer')
           
            # <CHANGE> Check if user already claimed rewards recently (within 12 hours)
            if vote_timer:
                time_since_vote = datetime.now() - vote_timer
                if time_since_vote.total_seconds() < 43200:  # 12 hours
                    # User already claimed rewards, send message
                    try:
                        user = await self.bot.fetch_user(user_id)
                        already_claimed_embed = discord.Embed(
                            title="⏰ Rewards Already Claimed",
                            description="You've already claimed your vote rewards! Please wait before voting again.",
                            color=discord.Color.orange()
                        )
                       
                        next_vote_time = vote_timer + timedelta(hours=12)
                        time_until_next = next_vote_time - datetime.now()
                        hours_until_next = max(0, time_until_next.total_seconds() / 3600)
                       
                        already_claimed_embed.add_field(
                            name="Next Vote Available",
                            value=f"You can vote again in **{hours_until_next:.1f} hours**.",
                            inline=False
                        )
                       
                        await user.send(embed=already_claimed_embed)
                    except Exception as e:
                        print(f"Could not send already claimed message to user {user_id}: {e}")
                    return
           
            # Update vote count, streak, and timer
            current_time = datetime.now()
            votecount = user_data['votecount'] + 1
            votestreak = user_data['votestreak']
           
            # <CHANGE> Handle vote streak logic
            if vote_timer is None:
                # First vote ever
                votestreak = 1
            else:
                # Check if it's been more than a day
                time_diff = current_time - vote_timer
                if time_diff.total_seconds() > 86400:  # More than 24 hours (1 day)
                    votestreak = 1  # Reset streak
                else:
                    votestreak += 1  # Increase streak
           
            # Prepare transaction queries
            transaction_queries = [
                (
                    """
                    UPDATE user
                    SET votecount = %s, votestreak = %s, vote_timer = %s, candy_currency = COALESCE(candy_currency, 0) + %s
                    WHERE user_id = %s
                    """,
                    (votecount, votestreak, current_time,20,db_user_id)
                )
            ]
           
            # Get inventory ID
            get_inventory_id_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_id_result = await execute_query_with_retry(get_inventory_id_query, (db_user_id,))
           
            if not inventory_id_result:
                print(f"Could not find inventory for user {user_id}")
                return
           
            inventory_id = inventory_id_result[0]['inventory_id']
           
            # <CHANGE> Check if user has product_id 2 (reroll) in inventory
            inventory_query = """
            SELECT quantiter
            FROM inventory_product
            WHERE inventory_id = %s AND product_id = 2
            """
            inventory_result = await execute_query_with_retry(inventory_query, (inventory_id,))
           
            if inventory_result:
                # User already has the product, increase quantity
                current_quantity = inventory_result[0]['quantiter']
               
                transaction_queries.append((
                    """
                    UPDATE inventory_product
                    SET quantiter = %s
                    WHERE inventory_id = %s AND product_id = 2
                    """,
                    (current_quantity + 1, inventory_id)
                ))
            else:
                # User doesn't have the product, add it
                transaction_queries.append((
                    """
                    INSERT INTO inventory_product (inventory_id, product_id, quantiter)
                    VALUES (%s, 2, 1)
                    """,
                    (inventory_id,)
                ))
           
            # Execute transaction
            success = await self.execute_transaction(transaction_queries)
           
            if not success:
                print(f"Failed to process vote rewards for user {user_id}")
                return
           
            # <CHANGE> Check and update quest progress for "Vote 1 time" quest
            quest_completed = False
            quest_status = await self.update_quest_progress(db_user_id)
            if quest_status is not None and quest_status == 1:
                quest_completed = True
           
            # <CHANGE> Send DM with confirmation and rewards
            try:
                user = await self.bot.fetch_user(user_id)
               
                success_embed = discord.Embed(
                    title="🎉 Vote Successful!",
                    description="Thank you for supporting Solo Leveling Bot!",
                    color=discord.Color.green()
                )
               
                success_embed.add_field(
                    name="Rewards Received",
                    value=f"• 1x <:reroll:1302672409278545940>\n• +20 <:candy:1426599572012269578>\n• Vote count: **{votecount}**\n• Vote streak: **{votestreak}** 🔥",
                    inline=False
                )
               
                # Add quest completion message if applicable
                if quest_completed:
                    success_embed.add_field(
                        name="🎯 Quest Completed!",
                        value="You've completed the **'Vote 1 time'** quest and received your rewards!",
                        inline=False
                    )
               
                # Add streak bonus message if applicable
                if votestreak >= 7:
                    success_embed.add_field(
                        name="🔥 Streak Bonus!",
                        value=f"Amazing! You've voted **{votestreak}** days in a row! Keep it up!",
                        inline=False
                    )
               
                success_embed.add_field(
                    name="Next Vote",
                    value="You can vote again in **12 hours**!",
                    inline=False
                )
               
                if user.avatar:
                    success_embed.set_thumbnail(url=user.avatar.url)
               
                success_embed.set_footer(text="Thank you for your support! ❤️")
                success_embed.timestamp = datetime.now()
               
                await user.send(embed=success_embed)
                print(f"Successfully processed vote and sent DM to user {user_id}")
               
            except discord.Forbidden:
                print(f"Could not send DM to user {user_id} - DMs disabled")
            except Exception as e:
                print(f"Error sending DM to user {user_id}: {e}")
               
        except Exception as e:
            print(f"Error processing vote reward for user {user_id}: {e}")
            print(traceback.format_exc())
           
    async def check_vote_reminders(self):
        """Background task to check for users who need vote reminders"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # Get users who have voted and might need reminders
                current_time = datetime.now()
               
                # Query for users who:
                # 1. Have voted before (vote_timer is not NULL)
                # 2. Their vote cooldown has expired or will expire soon (vote_timer + 12 hours <= current_time + 10 minutes)
                # 3. Either have voteremind=1 OR haven't been reminded yet and need the one-time reminder
                # 4. Haven't received a reminder in the last 12 hours (last_reminder_time is NULL OR last_reminder_time + 12 hours <= current_time)
                query = """
                SELECT user_id, user_authorid, vote_timer, voteremind, last_reminder_time
                FROM user
                WHERE vote_timer IS NOT NULL
                AND DATE_ADD(vote_timer, INTERVAL 12 HOUR) <= DATE_ADD(%s, INTERVAL 10 MINUTE)
                AND (last_reminder_time IS NULL OR DATE_ADD(last_reminder_time, INTERVAL 12 HOUR) <= %s)
                """
               
                users_to_check = await execute_query_with_retry(query, (current_time, current_time))
               
                if users_to_check:
                    for user in users_to_check:
                        user_id = user['user_id']
                        author_id = user['user_authorid']
                        vote_timer = user['vote_timer']
                        voteremind = user.get('voteremind', 0)
                        last_reminder_time = user.get('last_reminder_time')
                       
                        # Skip if already reminded for one-time reminders
                        if not voteremind and int(author_id) in self.reminded_users:
                            continue
                       
                        # Check if they've already voted again
                        has_voted = await self.check_user_vote(int(author_id))
                        if has_voted:
                            continue
                           
                        # Calculate time since last vote
                        time_since_vote = current_time - vote_timer
                       
                        # For one-time reminders: only send if it's been between 12 and 12.5 hours
                        if not voteremind:
                            if 12 * 3600 <= time_since_vote.total_seconds() <= 12.5 * 3600:
                                await self.send_vote_reminder(int(author_id), user_id)
                                self.reminded_users.add(int(author_id))
                        # For opt-in reminders: send if it's been at least 12 hours
                        elif voteremind and time_since_vote.total_seconds() >= 12 * 3600:
                            await self.send_vote_reminder(int(author_id), user_id)
               
            except Exception as e:
                print(f"Error in vote reminder task: {e}")
                print(traceback.format_exc())
               
            # Check every 5 minutes
            await asyncio.sleep(300)
           
    async def send_vote_reminder(self, user_id, db_user_id):
        """Send a DM reminder to a user that they can vote again"""
        try:
            # Get the user object
            user = await self.bot.fetch_user(user_id)
            if not user:
                return
               
            # Create the reminder embed
            reminder_embed = discord.Embed(
                title="🗳️ Vote Reminder",
                description=f"Hey {user.name}! You can vote for Solo Leveling Bot again!\n\n"
                           f"[Click here to vote]({self.topgg_vote_url})",
                color=discord.Color.blue()
            )
           
            reminder_embed.add_field(
                name="Rewards",
                value="• +1 to your vote count\n• Vote streak bonuses\n• 1x <:reroll:1302672409278545940> per vote",
                inline=False
            )
           
            reminder_embed.set_footer(text="Use 'sl voteremind' to toggle vote reminders or 'sl disablevoteremind' to disable them")
           
            # Send the DM
            await user.send(embed=reminder_embed)
            print(f"Sent vote reminder to {user.name} ({user_id})")
           
            # Update the last_reminder_time in the database
            current_time = datetime.now()
            update_query = """
            UPDATE user
            SET last_reminder_time = %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_query, (current_time, db_user_id))
           
        except discord.Forbidden:
            # User has DMs disabled
            print(f"Could not send vote reminder to user {user_id} - DMs disabled")
        except Exception as e:
            print(f"Error sending vote reminder to {user_id}: {e}")

    @commands.command(name="voteremind", aliases=["vm"])
    async def voteremind(self, ctx):
        """Toggle vote reminders for when you can vote again"""
        try:
            # Check if user exists
            query = "SELECT user_id, voteremind FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
           
            # Check if user exists
            if not result or len(result) == 0:
                await ctx.send("❌ You need to create an account first. Use `sl register` to create your account.")
                return
               
            # User exists, get their data
            user_data = result[0]
            user_id = user_data['user_id']
            current_setting = user_data.get('voteremind', 0)
           
            # Toggle the setting
            new_setting = 0 if current_setting else 1
           
            # Update the database
            update_query = "UPDATE user SET voteremind = %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (new_setting, user_id))
           
            # Create response embed
            if new_setting:
                embed = discord.Embed(
                    title="🔔 Vote Reminders Enabled",
                    description="You will now receive a DM reminder when you can vote again.",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="How it works",
                    value="The bot will send you a DM when your 12-hour vote cooldown expires.",
                    inline=False
                )
            else:
                embed = discord.Embed(
                    title="🔕 Vote Reminders Disabled",
                    description="You will no longer receive regular DM reminders when you can vote again.",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="One-time reminder",
                    value="You'll still receive a one-time reminder 12 hours after voting.",
                    inline=False
                )
               
            if ctx.author.avatar:
                embed.set_thumbnail(url=ctx.author.avatar.url)
               
            await ctx.send(embed=embed)
           
        except Exception as e:
            print(f"Error in voteremind command: {e}")
            print(traceback.format_exc())
            await ctx.send(f"❌ An error occurred: {str(e)}")
   
    @commands.command(name="disablevoteremind", aliases=["dvr"])
    async def disablevoteremind(self, ctx):
        """Disable vote reminders completely"""
        try:
            # Check if user exists
            query = "SELECT user_id, voteremind FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
           
            # Check if user exists
            if not result or len(result) == 0:
                await ctx.send("❌ You need to create an account first. Use `sl register` to create your account.")
                return
               
            # User exists, get their data
            user_data = result[0]
            user_id = user_data['user_id']
           
            # Set voteremind to 0 (disabled)
            update_query = "UPDATE user SET voteremind = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_query, (user_id,))
           
            # Remove from reminded users set if they were in it
            if ctx.author.id in self.reminded_users:
                self.reminded_users.remove(ctx.author.id)
           
            # Create response embed
            embed = discord.Embed(
                title="🔕 Vote Reminders Disabled",
                description="You will no longer receive any vote reminders.",
                color=discord.Color.red()
            )
           
            embed.add_field(
                name="Note",
                value="This disables both regular reminders and the one-time reminder after voting.",
                inline=False
            )
           
            embed.add_field(
                name="Re-enable",
                value="Use `sl voteremind` if you want to enable reminders again in the future.",
                inline=False
            )
               
            if ctx.author.avatar:
                embed.set_thumbnail(url=ctx.author.avatar.url)
               
            await ctx.send(embed=embed)
           
        except Exception as e:
            print(f"Error in disablevoteremind command: {e}")
            print(traceback.format_exc())
            await ctx.send(f"❌ An error occurred: {str(e)}")
       
    async def periodic_health_check(self):
        """Periodically check database health"""
        try:
            while True:
                # Wait for 1 hour between checks
                await asyncio.sleep(3600)
               
                # Perform a simple health check query
                try:
                    start_time = datetime.now()
                    await execute_query_with_retry("SELECT 1")
                    end_time = datetime.now()
                    duration = (end_time - start_time).total_seconds() * 1000  # Convert to ms
                    print(f"Health check query completed in {duration:.2f}ms")
                except Exception as e:
                    print(f"Periodic health check failed: {e}")
        except asyncio.CancelledError:
            # Task was cancelled, clean up
            pass
        except Exception as e:
            print(f"Error in periodic health check: {e}")
   
    async def execute_transaction(self, queries_and_params, max_retries=3):
        """Execute multiple queries in a transaction with retry logic"""
        for attempt in range(max_retries):
            try:
                # Start transaction
                await execute_query_with_retry("START TRANSACTION")
               
                # Execute all queries
                for query, params in queries_and_params:
                    await execute_query_with_retry(query, params)
               
                # Commit transaction
                await execute_query_with_retry("COMMIT")
                return True
            except Exception as e:
                # Rollback on error
                try:
                    await execute_query_with_retry("ROLLBACK")
                except Exception as rollback_error:
                    print(f"Error during rollback: {rollback_error}")
               
                print(f"Transaction attempt {attempt+1} failed: {e}")
                if attempt == max_retries - 1:  # Last attempt
                    print(f"Transaction failed after {max_retries} attempts: {e}")
                    return False
               
                # Exponential backoff
                await asyncio.sleep(1 * (2 ** attempt))
       
        return False
   
    async def check_user_vote(self, user_id: int) -> bool:
        """Check if a user has voted on top.gg"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": self.topgg_api_token}
                async with session.get(f"https://top.gg/api/bots/1231298329548292197/check?userId={user_id}", headers=headers) as resp:
                    success = resp.status == 200
                    metrics = get_metrics()
                    metrics["api_call_count"] = metrics.get("api_call_count", 0) + 1
                   
                    if not success:
                        metrics["api_errors"] = metrics.get("api_errors", 0) + 1
                        print(f"Top.gg API error: Status {resp.status}, Response: {await resp.text()}")
                        return False
                   
                    data = await resp.json()
                    return data.get("voted", 0) == 1
        except Exception as e:
            metrics = get_metrics()
            metrics["api_errors"] = metrics.get("api_errors", 0) + 1
            print(f"Error checking vote status: {e}")
            return False
   
    async def update_quest_progress(self, user_id):
        """Update the user's quest progress for voting"""
        try:
            # First, get the quest progress
            quest_query = """
            SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
            FROM user_quests uq
            JOIN quest q ON uq.quest_id = q.quest_id
            WHERE uq.user_id = %s AND q.quest_name = 'Vote 1 time'
            """
            result = await execute_query_with_retry(quest_query, (user_id,))
           
            if result:
                quest = result[0]
                if quest['completion_status'] == 0:
                    new_count = quest['current_count'] + 1
                    completion_status = 1 if new_count >= quest['required_count'] else 0
                   
                    # Update quest progress
                    update_query = """
                    UPDATE user_quests
                    SET current_count = %s, completion_status = %s
                    WHERE userquests_id = %s
                    """
                   
                    # If quest is completed, update inventory in a transaction
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
                            chest_id = 1  # The specific chest ID
                           
                            # Prepare transaction queries
                            transaction_queries = [
                                (update_query, (new_count, completion_status, quest['userquests_id']))
                            ]
                           
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
                                transaction_queries.append((update_chest_query, (inventory_id, chest_id)))
                            else:
                                # Insert new chest entry
                                insert_chest_query = """
                                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                                VALUES (%s, %s, 1)
                                """
                                transaction_queries.append((insert_chest_query, (inventory_id, chest_id)))
                           
                            # Execute the transaction
                            success = await self.execute_transaction(transaction_queries)
                            if success:
                                return completion_status
                            else:
                                print(f"Failed to update quest progress and inventory for user {user_id}")
                                return None
                    else:
                        # Just update the quest progress
                        await execute_query_with_retry(update_query, (new_count, completion_status, quest['userquests_id']))
                        return completion_status
            return None
        except Exception as e:
            print(f"Error updating quest progress: {e}")
            return None
   
    @commands.command(name="votesyshealth", aliases=["vsh"])
    @command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def vote_system_health(self, ctx):
        """Display vote system health metrics"""
        # Check if the command invoker is authorized
        if not ctx.author.guild_permissions.administrator and str(ctx.author.id) != "818132161534230559":
            await ctx.send("You don't have permission to use this command.")
            return
       
        try:
            # Test database responsiveness
            start_time = datetime.now()
            test_result = await execute_query_with_retry("SELECT 1 as test")
            end_time = datetime.now()
            response_time = (end_time - start_time).total_seconds() * 1000  # ms
           
            # Get metrics from the metrics service
            metrics = get_metrics()
           
            # Get health status and color
            status = "HEALTHY"
            color = discord.Color.green()
           
            if metrics.get("connection_errors", 0) > 10 or metrics.get("api_errors", 0) > 5:
                status = "CRITICAL"
                color = discord.Color.red()
            elif metrics.get("connection_errors", 0) > 5 or metrics.get("slow_queries", 0) > 20 or metrics.get("api_errors", 0) > 2:
                status = "WARNING"
                color = discord.Color.orange()
               
            # Create health status embed
            embed = discord.Embed(
                title="🗳️ Vote System Health",
                description=f"Current status: **{status}**",
                color=color
            )
           
            # Add basic health metrics
            embed.add_field(name="Response Time", value=f"{response_time:.2f}ms", inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics.get("connection_errors", 0)), inline=True)
            embed.add_field(name="Active Connections", value=str(metrics.get("active_connections", 0)), inline=True)
           
            # Add query performance metrics
            embed.add_field(
                name="Query Performance",
                value=f"Total: {metrics.get('total_queries', 0)}\nSlow: {metrics.get('slow_queries', 0)}\nAvg: {metrics.get('avg_query_time', 0):.2f}ms",
                inline=True
            )
           
            # Add API metrics
            embed.add_field(
                name="Top.gg API",
                value=f"Calls: {metrics.get('api_call_count', 0)}\nErrors: {metrics.get('api_errors', 0)}",
                inline=True
            )
           
            # Add last error if any
            if metrics.get("last_error"):
                error_time = metrics.get("last_error_time", "Unknown")
                if isinstance(error_time, datetime):
                    error_time = error_time.strftime("%Y-%m-%d %H:%M:%S")
               
                embed.add_field(
                    name="Last Error",
                    value=f"Time: {error_time}\nError: {metrics.get('last_error', 'Unknown')[:100]}...",
                    inline=False
                )
           
            embed.set_footer(text=f"Last metrics reset: {metrics.get('last_reset_time', datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}")
            embed.timestamp = datetime.now()
            await ctx.send(embed=embed)
           
        except Exception as e:
            error_msg = f"Error checking vote system health: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")
   
    # <CHANGE> Simplified vote command - only shows voting information
    @commands.command(name="vote", aliases=["v"])
    async def vote(self, ctx):
        """Vote for the bot on top.gg and receive rewards"""
        try:
            # Check if user exists in database
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
           
            # Check if user exists
            if not result or len(result) == 0:
                await ctx.send("❌ You need to create an account first before voting. Use `sl register` to create your account.")
                return
           
            # Create voting information embed
            vote_embed = discord.Embed(
                title="🗳️ Vote for Solo Leveling Bot!",
                description=f"Support us by voting on top.gg and receive rewards!\n\n"
                           f"[Click here to vote]({self.topgg_vote_url})",
                color=discord.Color.blue()
            )
           
            vote_embed.add_field(
                name="Rewards",
                value="• +1 to your vote count\n• Vote streak bonuses\n• 1x <:reroll:1302672409278545940> per vote",
                inline=False
            )
           
            vote_embed.add_field(
                name="How it works",
                value="1. Click the link above to vote\n2. Your rewards will be sent to your DMs automatically!\n3. Vote every 12 hours to maintain your streak!",
                inline=False
            )
           
            vote_embed.set_footer(text="Rewards are automatically sent to your DMs after voting!")
           
            if ctx.guild and ctx.guild.icon:
                vote_embed.set_thumbnail(url=ctx.guild.icon.url)
           
            await ctx.send(embed=vote_embed)
       
        except Exception as e:
            print(f"Error in vote command: {e}")
            print(traceback.format_exc())
            await ctx.send(f"❌ An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(VoteCommands(bot))
