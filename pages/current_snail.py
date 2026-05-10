import discord
from discord.ext import commands
import random
import asyncio
from typing import Optional
import time
from datetime import datetime
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

class SnailRace(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_cooldowns = {}  # Store user cooldowns
        self.COOLDOWN_TIME = 10  # Cooldown time in seconds
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds

    def check_cooldown(self, user_id, command_name):
        """
        Check if a user is on cooldown for a specific command
        Returns (bool, float): (is_on_cooldown, time_remaining)
        """
        current_time = time.time()
        cooldown_key = f"{user_id}_{command_name}"
        
        if cooldown_key in self.user_cooldowns:
            last_use = self.user_cooldowns[cooldown_key]
            time_passed = current_time - last_use
            
            if time_passed < self.COOLDOWN_TIME:
                return True, round(self.COOLDOWN_TIME - time_passed, 1)
        
        self.user_cooldowns[cooldown_key] = current_time
        return False, 0

    async def check_gamble_status(self, user_id):
        """
        Check if a user is already in a gambling session
        Returns (bool): True if user is already gambling, False otherwise
        """
        query = "SELECT * FROM gamble_status WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        
        return bool(result)

    async def add_to_gamble_status(self, user_id):
        """
        Add user to gamble_status table to indicate they're in a gambling session
        """
        query = "INSERT INTO gamble_status (user_id) VALUES (%s)"
        await execute_query_with_retry(query, (user_id,))

    async def remove_from_gamble_status(self, user_id):
        """
        Remove user from gamble_status table when gambling session is complete
        """
        query = "DELETE FROM gamble_status WHERE user_id = %s"
        await execute_query_with_retry(query, (user_id,))

    @commands.command(name="snailstats")
    @commands.has_permissions(administrator=True)
    async def gamble_stats(self, ctx):
        """Display database connection statistics for the gambling system"""
        metrics = get_metrics()
        
        embed = discord.Embed(
            title="Gambling System Database Metrics",
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

    @commands.command(name="race",aliases=["sr","rcs"])
    async def snail_course(self, ctx, target: Optional[discord.Member] = None, amount: int = 1):
        """
        Initiate a snail race gambling game with another user
        
        :param ctx: Command context
        :param target: The user to challenge
        :param amount: The betting amount (defaults to 1)
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "race")
        if on_cooldown:
            await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
            return

        # Emojis for snails
        GREEN_SNAIL = "<:greensail:1318274540962320398>"
        RED_SNAIL = "<:redsnail:1318274530816561202>"
        TRACK_MARKER = "🔹"
        FINISH_LINE = "🏁"

        # Check if a target is specified
        if not target:
            await ctx.send(embed=discord.Embed(
                description="❌ You must specify a target to challenge.",
                color=discord.Color.red()
            ))
            return

        # Prevent self-challenge
        if target == ctx.author:
            await ctx.send(embed=discord.Embed(
                description="❌ You cannot challenge yourself!",
                color=discord.Color.red()
            ))
            return

        # Check if amount is valid
        if amount <= 0:
            await ctx.send(embed=discord.Embed(
                description="❌ Invalid betting amount. Must be greater than 0.",
                color=discord.Color.red()
            ))
            return

        try:
            # Fetch initiator's info
            initiator_query = """
            SELECT user_id, user_authorid, gamble_currency 
            FROM user 
            WHERE user_authorid = %s
            """
            initiator_result = await execute_query_with_retry(initiator_query, (str(ctx.author.id),))
            
            if not initiator_result:
                await ctx.send(embed=discord.Embed(
                    description="❌ You are not registered. Please register first.",
                    color=discord.Color.red()
                ))
                return
            
            initiator_info = initiator_result[0]
            initiator_user_id = initiator_info['user_id']
            
            # Fetch target's info
            target_query = """
            SELECT user_id, user_authorid, gamble_currency 
            FROM user 
            WHERE user_authorid = %s
            """
            target_result = await execute_query_with_retry(target_query, (str(target.id),))
            
            if not target_result:
                await ctx.send(embed=discord.Embed(
                    description="❌ The target is not registered. They must register first.",
                    color=discord.Color.red()
                ))
                return
            
            target_info = target_result[0]
            target_user_id = target_info['user_id']
            
            # Check if either user is already gambling
            if await self.check_gamble_status(initiator_user_id):
                await ctx.send(embed=discord.Embed(
                    description="⏰ You are already in a gambling session. Please wait until it completes.",
                    color=discord.Color.red()
                ))
                return
                
            if await self.check_gamble_status(target_user_id):
                await ctx.send(embed=discord.Embed(
                    description=f"⏰ {target.mention} is already in a gambling session. Please wait until it completes.",
                    color=discord.Color.red()
                ))
                return
            
            # Add both users to gamble_status
            await self.add_to_gamble_status(initiator_user_id)
            await self.add_to_gamble_status(target_user_id)
            
            try:
                # Check if both users have enough gambling currency
                if initiator_info['gamble_currency'] < amount:
                    await ctx.send(embed=discord.Embed(
                        description=f"❌ {ctx.author.mention} does not have enough gambling currency.",
                        color=discord.Color.red()
                    ))
                    return
                
                if target_info['gamble_currency'] < amount:
                    await ctx.send(embed=discord.Embed(
                        description=f"❌ {target.mention} does not have enough gambling currency.",
                        color=discord.Color.red()
                    ))
                    return
                
                # Challenge embed
                challenge_embed = discord.Embed(
                    title="🏁 Snail Race Challenge 🏁",
                    description=f"{ctx.author.mention} challenges {target.mention} to a snail race!\n" \
                               f"Betting Amount: {amount} gambling currency\n\n" \
                               f"**{target.mention}**, do you accept the challenge?\n\n" \
                               f"React with ✅ to accept or ❌ to decline.",
                    color=discord.Color.orange()
                )
                
                # Send the challenge message
                challenge_message = await ctx.send(embed=challenge_embed)
                await challenge_message.add_reaction("✅")
                await challenge_message.add_reaction("❌")
                
                def check(reaction, user):
                    return (user == target and str(reaction.emoji) in ["✅", "❌"] and 
                            reaction.message.id == challenge_message.id)
                
                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
                    
                    if str(reaction.emoji) == "❌":
                        decline_embed = discord.Embed(
                            description=f"{target.mention} declined the snail race challenge.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=decline_embed)
                        return
                except asyncio.TimeoutError:
                    timeout_embed = discord.Embed(
                        description="Challenge timed out.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=timeout_embed)
                    return

                # Deduct bet amount from both players immediately after acceptance
                deduct_query = """
                UPDATE user 
                SET gamble_currency = gamble_currency - %s 
                WHERE user_id = %s
                """
                await execute_query_with_retry(deduct_query, (amount, initiator_info['user_id']))
                await execute_query_with_retry(deduct_query, (amount, target_info['user_id']))
                
                # Update local currency values
                initiator_info['gamble_currency'] -= amount
                target_info['gamble_currency'] -= amount
                
                # Prepare race tracks
                def create_track(snail, position):
                    track = [TRACK_MARKER] * 20
                    if position < 20:
                        track[position] = snail
                    return ''.join(track)
                
                # Initial race embed with both snails at start
                race_embed = discord.Embed(
                    title="🐌 SNAIL RACE STARTING SOON 🐌",
                    description=f"**Racers:**\n" \
                               f"{ctx.author.mention}: {create_track(GREEN_SNAIL, 0)}\n" \
                               f"{target.mention}: {create_track(RED_SNAIL, 0)}\n\n" \
                               f"{FINISH_LINE} Finish Line\n\n" \
                               f"🚦 Race begins in 3 seconds...",
                    color=discord.Color.green()
                )
                
                race_message = await ctx.send(embed=race_embed)
                
                # Countdown and race start
                await asyncio.sleep(3)
                
                # Race Logic
                initiator_position = 0
                target_position = 0
                round_number = 1
                
                while initiator_position < 20 and target_position < 20:
                    # Move snails
                    initiator_move = random.randint(1, 3)
                    target_move = random.randint(1, 3)
                    
                    initiator_position = min(initiator_position + initiator_move, 20)
                    target_position = min(target_position + target_move, 20)
                    
                    # Update race tracks
                    race_update_embed = discord.Embed(
                        title=f"🐌 SNAIL RACE - Round {round_number} 🐌",
                        description=f"**Racers:**\n" \
                                   f"{ctx.author.mention}: {create_track(GREEN_SNAIL, initiator_position)}\n" \
                                   f"{target.mention}: {create_track(RED_SNAIL, target_position)}\n\n" \
                                   f"{FINISH_LINE} Finish Line\n\n" \
                                   f"**Last Move:**\n" \
                                   f"{ctx.author.mention}: {initiator_move} steps\n" \
                                   f"{target.mention}: {target_move} steps",
                        color=discord.Color.green()
                    )
                    
                    await race_message.edit(embed=race_update_embed)
                    await asyncio.sleep(2)
                    
                    round_number += 1
                
                # Determine winner
                if initiator_position >= 20 and target_position >= 20:
                    # Tie - return both players' bets
                    update_query = """
                    UPDATE user 
                    SET gamble_currency = gamble_currency + %s 
                    WHERE user_id = %s
                    """
                    await execute_query_with_retry(update_query, (amount, initiator_info['user_id']))
                    await execute_query_with_retry(update_query, (amount, target_info['user_id']))
                    
                    tie_embed = discord.Embed(
                        description="🤝 It's a tie! All bets returned.",
                        color=discord.Color.yellow()
                    )
                    await ctx.send(embed=tie_embed)
                    return
                
                if initiator_position >= 20:
                    winner = ctx.author
                    winner_info = initiator_info
                else:
                    winner = target
                    winner_info = target_info
                
                # Update winner's balance (2x the bet amount since they already paid their bet)
                update_query = """
                UPDATE user 
                SET gamble_currency = gamble_currency + %s 
                WHERE user_id = %s
                """
                await execute_query_with_retry(update_query, (amount * 2, winner_info['user_id']))
                
                # Final result embed
                result_embed = discord.Embed(
                    title="🏆 Race Result 🏆",
                    description=f"**Winner:** {winner.mention}!\n" \
                               f"They win {amount * 2} gambling currency!\n\n" \
                               f"**Race Completed in {round_number - 1} Rounds**",
                    color=discord.Color.gold()
                )
                
                await ctx.send(embed=result_embed)
            
            finally:
                # Always remove both users from gamble_status when done
                await self.remove_from_gamble_status(initiator_user_id)
                await self.remove_from_gamble_status(target_user_id)
        
        except Exception as e:
            # Make sure to remove from gamble_status even if an error occurs
            try:
                if 'initiator_user_id' in locals():
                    await self.remove_from_gamble_status(initiator_user_id)
                if 'target_user_id' in locals():
                    await self.remove_from_gamble_status(target_user_id)
            except:
                pass
            await ctx.send(embed=discord.Embed(
                description=f"❌ An error occurred: {str(e)}",
                color=discord.Color.red()
            ))

    #@commands.command(name="standoff")
    async def standoff(self, ctx, target: Optional[discord.Member] = None, amount: int = 1):
        """
        Initiate a standoff gambling game with another user
        
        :param ctx: Command context
        :param target: The user to challenge
        :param amount: The betting amount (defaults to 1)
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "standoff")
        if on_cooldown:
            await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
            return

        # Emojis
        GUN_EMOTE = "<:gunemote:1318294271735890052>"
        TROLL_FACE = "<:Trollface:1318294261279625227>"

        # Check if a target is specified
        if not target:
            await ctx.send(embed=discord.Embed(
                description="❌ You must specify a target to challenge.",
                color=discord.Color.red()
            ))
            return

        # Prevent self-challenge
        if target == ctx.author:
            await ctx.send(embed=discord.Embed(
                description="❌ You cannot challenge yourself!",
                color=discord.Color.red()
            ))
            return

        # Check if amount is valid
        if amount <= 0:
            await ctx.send(embed=discord.Embed(
                description="❌ Invalid betting amount. Must be greater than 0.",
                color=discord.Color.red()
            ))
            return

        try:
            # Fetch initiator's info
            initiator_query = """
            SELECT user_id, user_authorid, gamble_currency 
            FROM user 
            WHERE user_authorid = %s
            """
            initiator_result = await execute_query_with_retry(initiator_query, (str(ctx.author.id),))
            
            if not initiator_result:
                await ctx.send(embed=discord.Embed(
                    description="❌ You are not registered. Please register first.",
                    color=discord.Color.red()
                ))
                return
            
            initiator_info = initiator_result[0]
            initiator_user_id = initiator_info['user_id']
            
            # Fetch target's info
            target_query = """
            SELECT user_id, user_authorid, gamble_currency 
            FROM user 
            WHERE user_authorid = %s
            """
            target_result = await execute_query_with_retry(target_query, (str(target.id),))
            
            if not target_result:
                await ctx.send(embed=discord.Embed(
                    description="❌ The target is not registered. They must register first.",
                    color=discord.Color.red()
                ))
                return
            
            target_info = target_result[0]
            target_user_id = target_info['user_id']
            
            # Check if either user is already gambling
            if await self.check_gamble_status(initiator_user_id):
                await ctx.send(embed=discord.Embed(
                    description="⏰ You are already in a gambling session. Please wait until it completes.",
                    color=discord.Color.red()
                ))
                return
                
            if await self.check_gamble_status(target_user_id):
                await ctx.send(embed=discord.Embed(
                    description=f"⏰ {target.mention} is already in a gambling session. Please wait until it completes.",
                    color=discord.Color.red()
                ))
                return
            
            # Add both users to gamble_status
            await self.add_to_gamble_status(initiator_user_id)
            await self.add_to_gamble_status(target_user_id)
            
            try:
                # Check if both users have enough gambling currency
                if initiator_info['gamble_currency'] < amount:
                    await ctx.send(embed=discord.Embed(
                        description=f"❌ {ctx.author.mention} does not have enough gambling currency.",
                        color=discord.Color.red()
                    ))
                    return
                
                if target_info['gamble_currency'] < amount:
                    await ctx.send(embed=discord.Embed(
                        description=f"❌ {target.mention} does not have enough gambling currency.",
                        color=discord.Color.red()
                    ))
                    return
                
                # Challenge embed
                challenge_embed = discord.Embed(
                    title="🔫 STANDOFF CHALLENGE 🔫",
                    description=f"{ctx.author.mention} challenges {target.mention} to a standoff!\n" \
                               f"Betting Amount: {amount} gambling currency\n\n" \
                               f"**{target.mention}**, do you accept the challenge?\n\n" \
                               f"React with ✅ to accept or ❌ to decline.",
                    color=discord.Color.orange()
                )
                
                # Send the challenge message
                challenge_message = await ctx.send(embed=challenge_embed)
                await challenge_message.add_reaction("✅")
                await challenge_message.add_reaction("❌")
                
                def check_challenge(reaction, user):
                    return (user == target and str(reaction.emoji) in ["✅", "❌"] and 
                            reaction.message.id == challenge_message.id)
                
                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check_challenge)
                    
                    if str(reaction.emoji) == "❌":
                        decline_embed = discord.Embed(
                            description=f"{target.mention} declined the standoff challenge.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=decline_embed)
                        return
                except asyncio.TimeoutError:
                    timeout_embed = discord.Embed(
                        description="Challenge timed out.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=timeout_embed)
                    return

                # Deduct bet amount from both players immediately after acceptance
                deduct_query = """
                UPDATE user 
                SET gamble_currency = gamble_currency - %s 
                WHERE user_id = %s
                """
                await execute_query_with_retry(deduct_query, (amount, initiator_info['user_id']))
                await execute_query_with_retry(deduct_query, (amount, target_info['user_id']))
                
                # Update local currency values
                initiator_info['gamble_currency'] -= amount
                target_info['gamble_currency'] -= amount
                
                # Standoff preparation embed
                standoff_embed = discord.Embed(
                    title="🔫 STANDOFF INITIATED 🔫",
                    description=f"**Participants:**\n" \
                               f"{ctx.author.mention} vs {target.mention}\n\n" \
                               f"**PREPARE YOURSELVES!**\n" \
                               f"Only these two can react. Choose wisely...\n\n" \
                               f"Waiting for standoff to begin...",
                    color=discord.Color.dark_red()
                )
                
                # Send standoff message
                standoff_message = await ctx.send(embed=standoff_embed)
                
                # Random duration between 3-10 seconds
                wait_duration = random.uniform(3, 10)
                await asyncio.sleep(wait_duration)
                
                # Randomly choose the outcome type
                outcome_type = random.choice(['troll', 'gun'])
                
                # Reaction embed
                reaction_embed = discord.Embed(
                    title="🔫 STANDOFF IN PROGRESS 🔫",
                    description=f"**Participants:**\n" \
                               f"{ctx.author.mention} vs {target.mention}\n\n" \
                               f"**REACT NOW!**\n" \
                               f"Be the first to react!",
                    color=discord.Color.dark_red()
                )
                
                await standoff_message.edit(embed=reaction_embed)
                
                # Add relevant reaction based on outcome type
                reaction_emoji = TROLL_FACE if outcome_type == 'troll' else GUN_EMOTE
                await standoff_message.add_reaction(reaction_emoji)
                
                # Different check functions based on outcome type
                if outcome_type == 'troll':
                    def check_reaction(reaction, user):
                        return (user in [ctx.author, target] and 
                                str(reaction.emoji) == TROLL_FACE and 
                                reaction.message.id == standoff_message.id)
                    
                    try:
                        # Wait for Troll Face reaction
                        reaction, loser = await self.bot.wait_for('reaction_add', timeout=4.0, check=check_reaction)
                        winner = ctx.author if loser == target else target
                        winner_info = initiator_info if winner == ctx.author else target_info
                        
                        # Update winner's balance with double the bet amount
                        update_winner_query = """
                        UPDATE user 
                        SET gamble_currency = gamble_currency + %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_winner_query, (amount * 2, winner_info['user_id']))
                        
                        # Result embed
                        result_embed = discord.Embed(
                            title="🏆 STANDOFF RESULT 🏆",
                            description=f"**Winner:** {winner.mention}!\n" \
                                       f"They win {amount * 2} gambling currency!",
                            color=discord.Color.gold()
                        )
                        await standoff_message.clear_reactions()
                        await standoff_message.edit(embed=result_embed)
                    
                    except asyncio.TimeoutError:
                        # No one touched Troll Face, add Gun Emote
                        await standoff_message.add_reaction(GUN_EMOTE)
                        
                        def check_gun_reaction(reaction, user):
                            return (user in [ctx.author, target] and 
                                    str(reaction.emoji) == GUN_EMOTE and 
                                    reaction.message.id == standoff_message.id)
                        
                        try:
                            # Wait for Gun Emote reaction
                            gun_reaction, winner = await self.bot.wait_for('reaction_add', timeout=4.0, check=check_gun_reaction)
                            winner_info = initiator_info if winner == ctx.author else target_info
                            
                            # Update winner's balance with double the bet amount
                            update_winner_query = """
                            UPDATE user 
                            SET gamble_currency = gamble_currency + %s 
                            WHERE user_id = %s
                            """
                            await execute_query_with_retry(update_winner_query, (amount * 2, winner_info['user_id']))
                            
                            # Result embed
                            result_embed = discord.Embed(
                                title="🏆 STANDOFF RESULT 🏆",
                                description=f"**Winner:** {winner.mention}!\n" \
                                           f"They win {amount * 2} gambling currency!",
                                color=discord.Color.gold()
                            )
                            await standoff_message.clear_reactions()
                            await standoff_message.edit(embed=result_embed)
                        
                        except asyncio.TimeoutError:
                            # No one reacted to Gun Emote, return bets to both players
                            return_query = """
                            UPDATE user 
                            SET gamble_currency = gamble_currency + %s 
                            WHERE user_id = %s
                            """
                            await execute_query_with_retry(return_query, (amount, initiator_info['user_id']))
                            await execute_query_with_retry(return_query, (amount, target_info['user_id']))
                            
                            tie_embed = discord.Embed(
                                description="🤝 No one reacted! It's a tie. All bets returned.",
                                color=discord.Color.yellow()
                            )
                            await standoff_message.clear_reactions()
                            await standoff_message.edit(embed=tie_embed)
                
                else:  # Gun scenario
                    def check_gun_reaction(reaction, user):
                        return (user in [ctx.author, target] and 
                                str(reaction.emoji) == GUN_EMOTE and 
                                reaction.message.id == standoff_message.id)
                    
                    try:
                        # Wait for Gun Emote reaction
                        gun_reaction, winner = await self.bot.wait_for('reaction_add', timeout=4.0, check=check_gun_reaction)
                        winner_info = initiator_info if winner == ctx.author else target_info
                        
                        # Update winner's balance with double the bet amount
                        update_winner_query = """
                        UPDATE user 
                        SET gamble_currency = gamble_currency + %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_winner_query, (amount * 2, winner_info['user_id']))
                        
                        # Result embed
                        result_embed = discord.Embed(
                            title="🏆 STANDOFF RESULT 🏆",
                            description=f"**Winner:** {winner.mention}!\n" \
                                       f"They win {amount * 2} gambling currency!",
                            color=discord.Color.gold()
                        )
                        await standoff_message.clear_reactions()
                        await standoff_message.edit(embed=result_embed)
                    
                    except asyncio.TimeoutError:
                        # No one reacted to Gun Emote, return bets to both players
                        return_query = """
                        UPDATE user 
                        SET gamble_currency = gamble_currency + %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(return_query, (amount, initiator_info['user_id']))
                        await execute_query_with_retry(return_query, (amount, target_info['user_id']))
                        
                        tie_embed = discord.Embed(
                            description="🤝 No one reacted! It's a tie. All bets returned.",
                            color=discord.Color.yellow()
                        )
                        await standoff_message.clear_reactions()
                        await standoff_message.edit(embed=tie_embed)
            
            finally:
                # Always remove both users from gamble_status when done
                await self.remove_from_gamble_status(initiator_user_id)
                await self.remove_from_gamble_status(target_user_id)
        
        except Exception as e:
            # Make sure to remove from gamble_status even if an error occurs
            try:
                if 'initiator_user_id' in locals():
                    await self.remove_from_gamble_status(initiator_user_id)
                if 'target_user_id' in locals():
                    await self.remove_from_gamble_status(target_user_id)
            except:
                pass
            await ctx.send(embed=discord.Embed(
                description=f"❌ An error occurred: {str(e)}",
                color=discord.Color.red()
            ))

async def setup(bot):
    await bot.add_cog(SnailRace(bot))