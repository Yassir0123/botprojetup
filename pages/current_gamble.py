import discord
from discord.ext import commands
import random
import time
from datetime import datetime, timedelta
import asyncio
import math
from typing import Optional
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
class RealisticSlotMachine:
    SYMBOLS = [
        {'name': 'Cherry', 'value': 0.5, 'frequency': 40, 'emoji': '🍒'},
        {'name': 'Lemon', 'value': 1, 'frequency': 25, 'emoji': '🍋'},
        {'name': 'Orange', 'value': 2, 'frequency': 15, 'emoji': '🍊'},
        {'name': 'Plum', 'value': 3, 'frequency': 10, 'emoji': '🍇'},
        {'name': 'Bell', 'value': 5, 'frequency': 7, 'emoji': '🔔'},
        {'name': 'BAR', 'value': 10, 'frequency': 2, 'emoji': '💰'},
        {'name': 'Seven', 'value': 25, 'frequency': 1, 'emoji': '💎'}
    ]

    @classmethod
    def generate_weighted_symbols(cls):
        """Generate a list of symbols based on their frequency."""
        weighted_symbols = []
        for symbol in cls.SYMBOLS:
            weighted_symbols.extend([symbol] * symbol['frequency'])
        return weighted_symbols

    @classmethod
    def spin(cls):
        """
        Generate a spin with weighted probabilities.
        More realistic slot machine spin with weighted symbol selection.
        """
        weighted_symbols = cls.generate_weighted_symbols()
        return [random.choice(weighted_symbols) for _ in range(3)]

    @classmethod
    def check_win(cls, spin, bet):
        """
        Check win conditions more realistically:
        - Multiple win lines possible
        - Different payouts based on symbol rarity
        - Includes combinations and matching symbols
        - Ensures integer winnings
        """
        winnings = 0

        # Check for matching symbols
        if spin[0]['name'] == spin[1]['name'] == spin[2]['name']:
            # Full line match - highest payout
            winnings = math.floor(bet * spin[0]['value'] * 10)
        
        # Check for two-symbol combinations
        elif spin[0]['name'] == spin[1]['name'] or spin[1]['name'] == spin[2]['name']:
            # Partial match - smaller payout
            winnings = math.floor(bet * spin[1]['value'] * 2)
        
        # Bonus for specific rare combinations
        if any(sym['name'] == 'Seven' for sym in spin):
            winnings += math.floor(bet * 5)

        return winnings

class GamblingCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.slot_machine = RealisticSlotMachine()
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

    #@commands.command(name='slot', aliases=["slt","slots"])
    async def slot_command(self, ctx, bet: int = 1):
        """
        Slot machine command with betting functionality.
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "slot")
        if on_cooldown:
            await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
            return

        try:
            # Fetch user data
            query = "SELECT user_id, gamble_currency FROM `user` WHERE user_authorid = %s"
            user_data = await execute_query_with_retry(query, (str(ctx.author.id),))

            if not user_data:
                await ctx.send("User not found in the database.")
                return

            user_data = user_data[0]
            user_id = user_data['user_id']
            
            # Check if user is already gambling
            if await self.check_gamble_status(user_id):
                await ctx.send("⏰ You are already in a gambling session. Please wait until it completes.")
                return
                
            # Add user to gamble_status
            await self.add_to_gamble_status(user_id)
            
            try:
                current_balance = user_data['gamble_currency']
                if bet < 0:
                    await ctx.send(f"❌ Invalid betting amount. Must be greater than 0.")
                    return
                # Validate bet amount
                if bet > current_balance:
                    await ctx.send(f"❌ Insufficient funds! Your current balance is {current_balance} currency.")
                    return
                
                # Deduct bet amount immediately
                new_balance = current_balance - bet
                update_query = """
                UPDATE `user` 
                SET gamble_currency = %s 
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(update_query, (new_balance, str(ctx.author.id)))
                
                # Create embed and display initial slot design
                embed = discord.Embed(
                    title="<:Slot_machine:1314187511706161152> Slot Machine <:Slot_machine:1314187511706161152>",
                    color=discord.Color.gold()
                )
                embed.description = "Spinning the reels..."

                # Generate initial spins for animation
                first_spin = self.slot_machine.spin()
                second_spin = self.slot_machine.spin()
                final_spin = self.slot_machine.spin()

                # Convert spins to emoji display
                slot_display = (
                    f"{first_spin[0]['emoji']} | {first_spin[1]['emoji']} | {first_spin[2]['emoji']}\n\n"
                    f"{second_spin[0]['emoji']} | {second_spin[1]['emoji']} | {second_spin[2]['emoji']}\n\n"
                    f"{final_spin[0]['emoji']} | {final_spin[1]['emoji']} | {final_spin[2]['emoji']}"
                )
                
                embed.add_field(
                    name="Slot Reels",
                    value=slot_display,
                    inline=False
                )

                message = await ctx.send(embed=embed)
                await asyncio.sleep(2)

                # Animate the slot machine
                new_first_spin = self.slot_machine.spin()
                new_second_spin = self.slot_machine.spin()
                slot_display = (
                    f"{new_first_spin[0]['emoji']} | {new_first_spin[1]['emoji']} | {new_first_spin[2]['emoji']}\n\n"
                    f"{second_spin[0]['emoji']} | {second_spin[1]['emoji']} | {second_spin[2]['emoji']}\n\n"
                    f"{final_spin[0]['emoji']} | {final_spin[1]['emoji']} | {final_spin[2]['emoji']}"
                )
                embed.set_field_at(
                    0,
                    name="Slot Reels",
                    value=slot_display,
                    inline=False
                )
                await message.edit(embed=embed)
                await asyncio.sleep(2)

                # Final spin display
                slot_display = (
                    f"{new_first_spin[0]['emoji']} | {new_first_spin[1]['emoji']} | {new_first_spin[2]['emoji']}\n\n"
                    f"{new_second_spin[0]['emoji']} | {new_second_spin[1]['emoji']} | {new_second_spin[2]['emoji']}\n\n"
                    f"{final_spin[0]['emoji']} | {final_spin[1]['emoji']} | {final_spin[2]['emoji']}"
                )
                embed.set_field_at(
                    0,
                    name="Slot Reels",
                    value=slot_display,
                    inline=False
                )
                await message.edit(embed=embed)
                await asyncio.sleep(2)

                # Determine the result
                winnings = self.slot_machine.check_win(final_spin, bet)

                # Handle the result
                if winnings > 0:
                    # Add winnings to balance (bet amount was already deducted)
                    new_balance += winnings
                    
                    # Update database with winnings
                    update_query = """
                    UPDATE `user` 
                    SET gamble_currency = %s 
                    WHERE user_authorid = %s
                    """
                    await execute_query_with_retry(update_query, (new_balance, str(ctx.author.id)))

                    # Winning embed details
                    embed.add_field(
                        name="🎉 Congratulations!", 
                        value=f"You won {winnings} <:gamble:1314341368176574564> GWon!",
                        inline=False
                    )
                    embed.color = discord.Color.green()
                else:
                    # No need to update balance as bet was already deducted
                    embed.add_field(
                        name="😢 Better Luck Next Time", 
                        value=f"You lost {bet} <:gamble:1314341368176574564> GWon",
                        inline=False
                    )
                    embed.color = discord.Color.red()

                # Show current balance
                embed.add_field(
                    name="💰 Balance", 
                    value=f"{new_balance} currency", 
                    inline=False
                )

                await message.edit(embed=embed)
            
            finally:
                # Always remove user from gamble_status when done
                await self.remove_from_gamble_status(user_id)

        except Exception as e:
            # Make sure to remove from gamble_status even if an error occurs
            try:
                if user_data and 'user_id' in user_data:
                    await self.remove_from_gamble_status(user_data['user_id'])
            except:
                pass
            await ctx.send(f"An error occurred: {str(e)}")

    #@commands.command(name="rr",aliases=['russianroulette','russian'])
    #@command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def russian_roulette(self, ctx, target: Optional[discord.Member] = None, amount: int = 1):
        """
        Initiate a Russian Roulette gambling game with another user
        
        :param ctx: Command context
        :param target: The user to challenge
        :param amount: The betting amount (defaults to 1)
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "rr")
        if on_cooldown:
            await ctx.send(embed=discord.Embed(
                description=f"⏰ Command on cooldown. Please wait {time_left} seconds.",
                color=discord.Color.red()
            ))
            return

        GUN_EMOTE = "<:gunemote:1318294271735890052>"

        if not target:
            await ctx.send(embed=discord.Embed(
                description="❌ You must specify a target to challenge.",
                color=discord.Color.red()
            ))
            return

        if target == ctx.author:
            await ctx.send(embed=discord.Embed(
                description="❌ You cannot challenge yourself!",
                color=discord.Color.red()
            ))
            return

        if amount <= 0:
            await ctx.send(embed=discord.Embed(
                description="❌ Invalid betting amount. Must be greater than 0.",
                color=discord.Color.red()
            ))
            return

        try:
            # Fetch initiator's info
            initiator_query = "SELECT user_id, user_authorid, gamble_currency FROM user WHERE user_authorid = %s"
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
            target_query = "SELECT user_id, user_authorid, gamble_currency FROM user WHERE user_authorid = %s"
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
                if amount < 0:
                    await ctx.send(f"❌ You can't bet with a negative amount")
                    return
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
                    title="🔫 RUSSIAN ROULETTE CHALLENGE 🔫",
                    description=f"{ctx.author.mention} challenges {target.mention} to Russian Roulette!\n" \
                            f"Betting Amount: {amount} gambling currency\n\n" \
                            f"**{target.mention}**, do you accept the challenge?\n\n" \
                            f"React with ✅ to accept or ❌ to decline.",
                    color=discord.Color.orange()
                )
                
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
                            description=f"{target.mention} declined the Russian Roulette challenge.",
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

                # Deduct betting amount from both players immediately after challenge acceptance
                deduct_query = "UPDATE user SET gamble_currency = gamble_currency - %s WHERE user_id = %s"
                await execute_query_with_retry(deduct_query, (amount, initiator_info['user_id']))
                await execute_query_with_retry(deduct_query, (amount, target_info['user_id']))
                
                # Update local info
                initiator_info['gamble_currency'] -= amount
                target_info['gamble_currency'] -= amount
                
                game_embed = discord.Embed(
                    title="🔫 RUSSIAN ROULETTE BEGINS 🔫",
                    description=f"**Participants:**\n" \
                            f"{ctx.author.mention} vs {target.mention}\n\n" \
                            f"**PREPARE YOURSELVES!**\n" \
                            f"The game begins in 3 seconds...",
                    color=discord.Color.dark_red()
                )
                
                game_message = await ctx.send(embed=game_embed)
                await asyncio.sleep(3)
                
                current_player = ctx.author
                current_player_info = initiator_info
                current_player_id = initiator_user_id
                other_player = target
                other_player_info = target_info
                other_player_id = target_user_id
                
                while True:
                    game_embed = discord.Embed(
                        title="🔫 RUSSIAN ROULETTE IN PROGRESS 🔫",
                        description=f"**Current Player:** {current_player.mention}\n" \
                                f"**REACT TO THE GUN EMOTE!**\n" \
                                f"You have 10 seconds to react...",
                        color=discord.Color.dark_red()
                    )
                    await game_message.edit(embed=game_embed)
                    await game_message.add_reaction(GUN_EMOTE)
                    
                    def check_gun_reaction(reaction, user):
                        return (user == current_player and 
                                str(reaction.emoji) == GUN_EMOTE and 
                                reaction.message.id == game_message.id)
                    
                    try:
                        await self.bot.wait_for('reaction_add', timeout=10.0, check=check_gun_reaction)
                        
                        if random.random() < 0.5:
                            winner = other_player
                            loser = current_player
                            winner_info = other_player_info
                            loser_info = current_player_info
                            
                            # Give winner double the bet amount (they get their bet back plus opponent's bet)
                            update_winner_query = """
                            UPDATE user 
                            SET gamble_currency = gamble_currency + %s 
                            WHERE user_id = %s
                            """
                            await execute_query_with_retry(update_winner_query, (amount * 2, winner_info['user_id']))
                            
                            result_embed = discord.Embed(
                                title="🏆 RUSSIAN ROULETTE RESULT 🏆",
                                description=f"**Loser:** {loser.mention}! (Shot by the gun)\n" \
                                        f"**Winner:** {winner.mention}!\n\n" \
                                        f"{winner.mention}'s New Balance: {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\n" \
                                        f"{loser.mention}'s New Balance: {loser_info['gamble_currency']} <:gamble:1314341368176574564>",
                                color=discord.Color.gold()
                            )
                            await game_message.clear_reactions()
                            await game_message.edit(embed=result_embed)
                            break
                        else:
                            await game_message.clear_reactions()
                            game_embed = discord.Embed(
                                title="🔫 RUSSIAN ROULETTE CONTINUES 🔫",
                                description=f"**{current_player.mention}** survived!\n" \
                                        f"Now it's **{other_player.mention}**'s turn.",
                                color=discord.Color.dark_red()
                            )
                            await game_message.edit(embed=game_embed)
                            await asyncio.sleep(2)
                            
                            current_player, other_player = other_player, current_player
                            current_player_info, other_player_info = other_player_info, current_player_info
                            current_player_id, other_player_id = other_player_id, current_player_id
                    
                    except asyncio.TimeoutError:
                        winner = other_player
                        loser = current_player
                        winner_info = other_player_info
                        loser_info = current_player_info
                        
                        # Give winner double the bet amount
                        update_winner_query = """
                        UPDATE user 
                        SET gamble_currency = gamble_currency + %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_winner_query, (amount * 2, winner_info['user_id']))
                        
                        result_embed = discord.Embed(
                            title="🏆 RUSSIAN ROULETTE RESULT 🏆",
                            description=f"**Loser:** {loser.mention}! (Failed to react in time)\n" \
                                    f"**Winner:** {winner.mention}!\n\n" \
                                    f"{winner.mention}'s New Balance: {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\n" \
                                    f"{loser.mention}'s New Balance: {loser_info['gamble_currency']} <:gamble:1314341368176574564>",
                            color=discord.Color.gold()
                        )
                        await game_message.clear_reactions()
                        await game_message.edit(embed=result_embed)
                        break
            
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

    @commands.command(name='coinflip', aliases=['cf','coin'])
    @command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def coinflip_command(self, ctx, *args):
        """
        Coinflip gambling command with simplified user interaction.
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "coinflip")
        if on_cooldown:
            await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
            return

        try:
            query = "SELECT user_id, gamble_currency FROM `user` WHERE user_authorid = %s"
            user_data = await execute_query_with_retry(query, (str(ctx.author.id),))

            if not user_data:
                await ctx.send("User not found in the database.")
                return

            user_data = user_data[0]
            user_id = user_data['user_id']
            current_balance = user_data['gamble_currency']
            
            # Check if user is already gambling
            if await self.check_gamble_status(user_id):
                await ctx.send("⏰ You are already in a gambling session. Please wait until it completes.")
                return
                
            # Add user to gamble_status
            await self.add_to_gamble_status(user_id)
            
            try:
                bet = 1
                side = None

                for arg in args:
                    try:
                        bet = int(arg)
                    except ValueError:
                        if arg.lower() in ['heads', 'tails']:
                            side = arg.lower()

                if bet > current_balance:
                    await ctx.send(f"❌ Insufficient funds! Your current balance is {current_balance} currency.")
                    return
                if bet < 0:
                    await ctx.send(f"❌ Invalid betting amount. Must be greater than 0.")
                    return
                if not side:
                    side = random.choice(['heads', 'tails'])

                # Deduct bet amount immediately
                deduct_query = """
                UPDATE `user` 
                SET gamble_currency = gamble_currency - %s 
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(deduct_query, (bet, str(ctx.author.id)))
                current_balance -= bet

                flip_embed = discord.Embed(
                    title="🪙 Coinflip in Progress 🪙",
                    description="Flipping the coin...",
                    color=discord.Color.gold()
                )
                flip_message = await ctx.send(embed=flip_embed)

                await asyncio.sleep(2)

                computer_side = random.choice(['heads', 'tails'])

                if computer_side == side:
                    # Win - give double the bet amount
                    winnings = bet * 2
                    new_balance = current_balance + winnings
                    result_color = discord.Color.green()
                    result_title = "🎉 You Won!"
                    result_desc = f"Congratulations! You won {winnings} currency!"
                    
                    # Update database with winnings
                    update_query = """
                    UPDATE `user` 
                    SET gamble_currency = gamble_currency + %s 
                    WHERE user_authorid = %s
                    """
                    await execute_query_with_retry(update_query, (winnings, str(ctx.author.id)))
                else:
                    # Loss - bet amount already deducted
                    winnings = 0
                    new_balance = current_balance
                    result_color = discord.Color.red()
                    result_title = "😢 Better Luck Next Time"
                    result_desc = f"You lost {bet} currency."

                result_embed = discord.Embed(
                    title=result_title,
                    description=result_desc,
                    color=result_color
                )
                result_embed.add_field(
                    name="Coin Result", 
                    value=f"Your side: {side.capitalize()}\nComputer's side: {computer_side.capitalize()}", 
                    inline=False
                )
                result_embed.add_field(
                    name="💰 New Balance", 
                    value=f"**GWon** : {new_balance} <:gamble:1314341368176574564>", 
                    inline=False
                )

                await flip_message.edit(embed=result_embed)
            
            finally:
                # Always remove user from gamble_status when done
                await self.remove_from_gamble_status(user_id)

        except Exception as e:
            # Make sure to remove from gamble_status even if an error occurs
            try:
                if user_data and 'user_id' in user_data:
                    await self.remove_from_gamble_status(user_data['user_id'])
            except:
                pass
            await ctx.send(f"An error occurred: {str(e)}")

    #@commands.command(name='convert', aliases=["cvt","conv"])
    async def convert_command(self, ctx, amount: str = None):
        """
        Convert gamble currency to won currency.
        Base conversion rate: 50,000 gamble currency = 1,000 won currency
        Premium users get better rates based on their tier.
        """
        if amount is None:
            await ctx.send("❌ Please specify an amount to convert or use 'all'. \nUsage: `sl convert <amount>` or `sl convert all`")
            return

        try:
            # Fetch user data including premium status
            query = """
            SELECT user_id, gamble_currency, won_currency, prenium, prenium_date, roll_cap 
            FROM `user` 
            WHERE user_authorid = %s
            """
            user_data = await execute_query_with_retry(query, (str(ctx.author.id),))

            if not user_data:
                await ctx.send("User not found in the database.")
                return

            user_data = user_data[0]
            user_id = user_data['user_id']
            current_gamble = user_data['gamble_currency']
            current_won = user_data['won_currency']
            premium_status = user_data['prenium']
            premium_date = user_data['prenium_date']
            roll_cap = user_data['roll_cap']
            
            # Check if user is already gambling
            if await self.check_gamble_status(user_id):
                await ctx.send("⏰ You are already in a gambling session. Please wait until it completes.")
                return
                
            # Add user to gamble_status
            await self.add_to_gamble_status(user_id)
            
            try:
                # Check premium status and date
                premium_expired = False
                if premium_status != 0 and premium_date:
                    current_date = datetime.now()
                    days_difference = (current_date - premium_date).days
                    
                    if days_difference >= 30:
                        premium_expired = True
                        # Adjust roll_cap based on premium tier before resetting
                        roll_cap_reduction = min(premium_status, roll_cap)  # Ensure we don't reduce below 0
                        new_roll_cap = roll_cap - roll_cap_reduction
                        
                        # Update user's premium status and roll_cap
                        update_premium_query = """
                        UPDATE `user` 
                        SET prenium = 0, 
                            prenium_date = NULL, 
                            roll_cap = %s 
                        WHERE user_authorid = %s
                        """
                        await execute_query_with_retry(update_premium_query, (new_roll_cap, str(ctx.author.id)))
                        premium_status = 0

                # Set conversion rate based on premium status
                if premium_status == 0 or premium_expired:
                    conversion_rate = 15  # 50,000 gamble = 1,000 won
                else:
                    # Premium conversion rates
                    premium_rates = {
                        1: 500,  # 35,000 gamble = 1,000 won
                        2: 10,  # 15,000 gamble = 1,000 won
                        3: 7    # 5,000 gamble = 1,000 won
                    }
                    conversion_rate = premium_rates.get(premium_status, 50)

                min_conversion = conversion_rate * 1000  # Minimum conversion amount

                # Handle 'all' scenario
                if amount.lower() == 'all':
                    convertible_amount = (current_gamble // min_conversion) * min_conversion
                    
                    if convertible_amount < min_conversion:
                        await ctx.send(f"❌ You need at least {min_conversion:,} <:gamble:1314341368176574564> GWon to convert.")
                        return
                    
                    won_converted = convertible_amount // conversion_rate
                    remaining_gamble = current_gamble - convertible_amount
                else:
                    # Parse amount and handle k/K suffix
                    try:
                        if amount.lower().endswith('k'):
                            parsed_amount = int(float(amount[:-1]) * 1000)
                        else:
                            parsed_amount = int(amount)
                    except ValueError:
                        await ctx.send("❌ Invalid amount. Please use a number or 'all'.")
                        return

                    # Check minimum conversion requirement
                    if parsed_amount < min_conversion:
                        await ctx.send(f"❌ You need at least {min_conversion:,} <:gamble:1314341368176574564> GWon to convert.")
                        return

                    # Ensure user has enough gamble currency
                    if parsed_amount > current_gamble:
                        await ctx.send(f"❌ Insufficient funds! Your current balance is {current_gamble:,} <:gamble:1314341368176574564> GWon.")
                        return

                    # Calculate convertible amount
                    convertible_amount = (parsed_amount // min_conversion) * min_conversion
                    won_converted = convertible_amount // conversion_rate
                    remaining_gamble = current_gamble - convertible_amount

                # Update currency values
                update_query = """
                UPDATE `user` 
                SET gamble_currency = %s, won_currency = %s 
                WHERE user_authorid = %s
                """
                await execute_query_with_retry(update_query, (
                    remaining_gamble, 
                    current_won + won_converted, 
                    str(ctx.author.id)
                ))

                # Create embed to show conversion result
                embed = discord.Embed(
                    title="💱 Currency Conversion",
                    color=discord.Color.blue()
                )
                
                status_text = ""
                if premium_expired:
                    status_text = "\n⚠️ Your premium has expired and has been reset."
                elif premium_status > 0:
                    status_text = f"\n✨ Premium Tier {premium_status} conversion rate applied!"

                embed.add_field(
                    name="Conversion Details",
                    value=(
                        f"Converted: {convertible_amount:,} <:gamble:1314341368176574564> GWon\n"
                        f"Received: {won_converted:,} <:won:1304034228966658048> Won\n"
                        f"Remaining GWon: {remaining_gamble:,} <:gamble:1314341368176574564> GWon"
                        f"{status_text}"
                    ),
                    inline=False
                )
                
                await ctx.send(embed=embed)
            
            finally:
                # Always remove user from gamble_status when done
                await self.remove_from_gamble_status(user_id)

        except Exception as e:
            # Make sure to remove from gamble_status even if an error occurs
            try:
                if user_data and 'user_id' in user_data:
                    await self.remove_from_gamble_status(user_data['user_id'])
            except:
                pass
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name='duel', aliases=["dll","du"])
    @command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def duel_command(self, ctx, user: discord.Member = None, bet: int = 1):
        """
        Duel gambling command where two users can bet against each other.
        Bet amount is deducted upfront from both players.
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "duel")
        if on_cooldown:
            await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
            return
            
        try:
            # Validate second user
            if not user or user == ctx.author:
                await ctx.send("Please mention a different user to duel with!")
                return

            # Fetch first user (initiator) data
            query = "SELECT user_id, gamble_currency FROM `user` WHERE user_authorid = %s"
            initiator_data = await execute_query_with_retry(query, (str(ctx.author.id),))
            
            if not initiator_data:
                await ctx.send("You are not registered in the database.")
                return
            
            # Fetch second user data
            target_data = await execute_query_with_retry(query, (str(user.id),))
            
            if not target_data:
                await ctx.send(f"{user.mention} is not registered in the database.")
                return

            initiator_user_id = initiator_data[0]['user_id']
            target_user_id = target_data[0]['user_id']
            initiator_balance = initiator_data[0]['gamble_currency']
            target_balance = target_data[0]['gamble_currency']
            
            # Check if either user is already gambling
            if await self.check_gamble_status(initiator_user_id):
                await ctx.send("⏰ You are already in a gambling session. Please wait until it completes.")
                return
                
            if await self.check_gamble_status(target_user_id):
                await ctx.send(f"⏰ {user.mention} is already in a gambling session. Please wait until it completes.")
                return
                
            # Add both users to gamble_status
            await self.add_to_gamble_status(initiator_user_id)
            await self.add_to_gamble_status(target_user_id)
            
            try:
                if bet < 0:
                    await ctx.send(f"❌ Invalid betting amount. Must be greater than 0.")
                    return
                # Validate bet amounts
                if bet > initiator_balance:
                    await ctx.send(f"{ctx.author.mention}, you don't have enough currency to make this duel!")
                    return
                
                if bet > target_balance:
                    await ctx.send(f"{user.mention} doesn't have enough currency to accept this duel!")
                    return

                # Create invite embed
                invite_embed = discord.Embed(
                    title="🎲 Duel Challenge 🎲",
                    description=f"{ctx.author.mention} challenges {user.mention} to a duel for {bet} currency!",
                    color=discord.Color.gold()
                )
                invite_embed.add_field(
                    name="Instructions", 
                    value="React with ✅ to accept or ❌ to decline",
                    inline=False
                )

                # Send invite message
                invite_message = await ctx.send(embed=invite_embed)
                
                # Add reactions
                await invite_message.add_reaction("✅")
                await invite_message.add_reaction("❌")

                def check(reaction, react_user):
                    return (
                        react_user == user and  # Only the challenged user can respond
                        reaction.message.id == invite_message.id and 
                        str(reaction.emoji) in ["✅", "❌"]
                    )

                try:
                    # Wait for reaction
                    reaction, react_user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)

                    # If user declines
                    if str(reaction.emoji) == "❌":
                        decline_embed = discord.Embed(
                            title="🚫 Duel Declined",
                            description=f"{user.mention} declined the duel!",
                            color=discord.Color.red()
                        )
                        await invite_message.edit(embed=decline_embed)
                        return

                    # If user accepts - deduct bets from both players immediately
                    update_query = """
                    UPDATE `user` 
                    SET gamble_currency = gamble_currency - %s 
                    WHERE user_authorid = %s
                    """
                    await execute_query_with_retry(update_query, (bet, str(ctx.author.id)))
                    await execute_query_with_retry(update_query, (bet, str(user.id)))

                    # Update local balance variables
                    initiator_balance -= bet
                    target_balance -= bet

                    toss_embed = discord.Embed(
                        title="🎲 Duel in Progress",
                        description="Flipping the coin...",
                        color=discord.Color.gold()
                    )
                    await invite_message.edit(embed=toss_embed)
                    await asyncio.sleep(2)

                    # True 50/50 chance - flip a coin using 0 or 1
                    winner = ctx.author if random.randint(0, 1) == 0 else user
                    loser = user if winner == ctx.author else ctx.author

                    # Update balances based on who won
                    if winner == ctx.author:
                        new_initiator_balance = initiator_balance + (bet * 2)
                        new_target_balance = target_balance
                    else:
                        new_initiator_balance = initiator_balance
                        new_target_balance = target_balance + (bet * 2)

                    # Update winner's balance in database
                    update_winner_query = """
                    UPDATE `user` 
                    SET gamble_currency = gamble_currency + %s 
                    WHERE user_authorid = %s
                    """
                    await execute_query_with_retry(update_winner_query, (bet * 2, str(winner.id)))

                    # Result embed
                    result_embed = discord.Embed(
                        title="🎉 Duel Complete",
                        description=f"{winner.mention} won the duel!",
                        color=discord.Color.green()
                    )
                    result_embed.add_field(
                        name="Winnings", 
                        value=f"{winner.mention} wins {bet * 2} currency",
                        inline=False
                    )
                    result_embed.add_field(
                        name="New Balances", 
                        value=f"{ctx.author.mention}: {new_initiator_balance} <:gamble:1314341368176574564> GWon\n{user.mention}: {new_target_balance} <:gamble:1314341368176574564> GWon",
                        inline=False
                    )

                    await invite_message.edit(embed=result_embed)

                except asyncio.TimeoutError:
                    timeout_embed = discord.Embed(
                        title="⏰ Duel Expired",
                        description="The duel challenge was not accepted in time.",
                        color=discord.Color.red()
                    )
                    await invite_message.edit(embed=timeout_embed)
            
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
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="gamblestats")
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

async def setup(bot):
    await bot.add_cog(GamblingCommands(bot))