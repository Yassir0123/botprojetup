import discord
from discord.ext import commands
import random
import time
from datetime import datetime, timedelta
import asyncio
import math
from typing import Optional
from utils.database import execute_query_with_retry, get_metrics, db_transaction, command_lock
import functools
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
# Localization helpers
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

async def _get_lang(discord_user_id: int) -> int:
    try:
        return await get_user_multilanguage(int(discord_user_id))
    except Exception:
        return 0
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
                    lang = await _get_lang(ctx.author.id)
                    await ctx.send(_tr(lang, f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                                       f"⏰ الأمر في فترة تهدئة. الرجاء الانتظار {remaining} ثوانٍ.",
                                       f"⏰ Commande en temps de recharge. Veuillez patienter {remaining} secondes."))
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

    @db_transaction(max_retries=3)
    async def check_gamble_status(self, user_id, conn=None):
        """
        Check if a user is already in a gambling session
        Returns (bool): True if user is already gambling, False otherwise
        """
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM gamble_status WHERE user_id = %s", (user_id,))
            result = await cursor.fetchone()
            return bool(result)

    @db_transaction(max_retries=3)
    async def add_to_gamble_status(self, user_id, conn=None):
        """
        Add user to gamble_status table to indicate they're in a gambling session
        """
        async with conn.cursor() as cursor:
            await cursor.execute("INSERT INTO gamble_status (user_id) VALUES (%s)", (user_id,))

    @db_transaction(max_retries=3)
    async def remove_from_gamble_status(self, user_id, conn=None):
        """
        Remove user from gamble_status table when gambling session is complete
        """
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM gamble_status WHERE user_id = %s", (user_id,))

    @db_transaction(max_retries=3)
    async def get_user_data(self, user_authorid, conn=None):
        """Get user data from database"""
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT user_id, gamble_currency FROM `user` WHERE user_authorid = %s", 
                (user_authorid,)
            )
            result = await cursor.fetchone()
            if result:
                return {
                    'user_id': result[0],
                    'gamble_currency': result[1]
                }
            return None

    @db_transaction(max_retries=3)
    async def update_user_currency(self, user_authorid, new_balance, conn=None):
        """Update user's gambling currency"""
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE `user` SET gamble_currency = %s WHERE user_authorid = %s",
                (new_balance, user_authorid)
            )

    #@commands.command(name='slot', aliases=["slt","slots"])
    async def slot_command(self, ctx, bet: int = 1):
        """
        Slot machine command with betting functionality.
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "slot")
        if on_cooldown:
            lang = await _get_lang(ctx.author.id)
            await ctx.send(_tr(lang, f"⏰ Command on cooldown. Please wait {time_left} seconds.",
                               f"⏰ الأمر في فترة تهدئة. الرجاء الانتظار {time_left} ثوانٍ.",
                               f"⏰ Commande en temps de recharge. Veuillez patienter {time_left} secondes."))
            return

        try:
            # Fetch user data
            user_data = await self.get_user_data(str(ctx.author.id))

            if not user_data:
                lang = await _get_lang(ctx.author.id)
                await ctx.send(_tr(lang, "User not found in the database.",
                                   "المستخدم غير موجود في قاعدة البيانات.",
                                   "Utilisateur introuvable dans la base de données."))
                return

            user_id = user_data['user_id']
            
            # Check if user is already gambling
            if await self.check_gamble_status(user_id):
                lang = await _get_lang(ctx.author.id)
                await ctx.send(_tr(lang, "⏰ You are already in a gambling session. Please wait until it completes.",
                                   "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                                   "⏰ Vous êtes déjà en session de jeu. Veuillez attendre qu'elle se termine."))
                return
                
            # Add user to gamble_status
            await self.add_to_gamble_status(user_id)
            
            try:
                current_balance = user_data['gamble_currency']
                lang = await _get_lang(ctx.author.id)
                if bet < 0:
                    await ctx.send(_tr(lang, "❌ Invalid betting amount. Must be greater than 0.",
                                       "❌ مبلغ الرهان غير صالح. يجب أن يكون أكبر من 0.",
                                       "❌ Montant de mise invalide. Doit être supérieur à 0."))
                    return
                # Validate bet amount
                if bet > current_balance:
                    await ctx.send(_tr(lang, f"❌ Insufficient funds! Your current balance is {current_balance} currency.",
                                       f"❌ أموال غير كافية! رصيدك الحالي هو {current_balance}.",
                                       f"❌ Fonds insuffisants ! Votre solde actuel est de {current_balance}."))
                    return
                
                # Deduct bet amount immediately
                new_balance = current_balance - bet
                await self.update_user_currency(str(ctx.author.id), new_balance)
                
                # Create embed and display initial slot design
                embed = discord.Embed(
                    title="<:Slot_machine:1314187511706161152> Slot Machine <:Slot_machine:1314187511706161152>",
                    color=discord.Color.gold()
                )
                embed.description = _tr(lang, "Spinning the reels...", "يتم تدوير البكرات...", "Les rouleaux tournent...")

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
                    name=_tr(lang, "Slot Reels", "بكرات السلوت", "Rouleaux"),
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
                    name=_tr(lang, "Slot Reels", "بكرات السلوت", "Rouleaux"),
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
                    name=_tr(lang, "Slot Reels", "بكرات السلوت", "Rouleaux"),
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
                    await self.update_user_currency(str(ctx.author.id), new_balance)

                    # Winning embed details
                    embed.add_field(
                        name=_tr(lang, "🎉 Congratulations!", "🎉 مبروك!", "🎉 Félicitations !"), 
                        value=_tr(lang, f"You won {winnings} <:gamble:1314341368176574564> GWon!",
                                  f"ربحت {winnings} <:gamble:1314341368176574564> جي-وون!",
                                  f"Vous avez gagné {winnings} <:gamble:1314341368176574564> GWon !"),
                        inline=False
                    )
                    embed.color = discord.Color.green()
                else:
                    # No need to update balance as bet was already deducted
                    embed.add_field(
                        name=_tr(lang, "😢 Better Luck Next Time", "😢 حظاً أوفر في المرة القادمة", "😢 Bonne chance la prochaine fois"), 
                        value=_tr(lang, f"You lost {bet} <:gamble:1314341368176574564> GWon",
                                  f"لقد خسرت {bet} <:gamble:1314341368176574564> جي-وون",
                                  f"Vous avez perdu {bet} <:gamble:1314341368176574564> GWon"),
                        inline=False
                    )
                    embed.color = discord.Color.red()

                # Show current balance
                embed.add_field(
                    name=_tr(lang, "💰 Balance", "💰 الرصيد", "💰 Solde"), 
                    value=_tr(lang, f"{new_balance} currency", f"{new_balance} عملة", f"{new_balance} monnaie"), 
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
            lang = await _get_lang(ctx.author.id)
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}",
                               f"حدث خطأ: {str(e)}",
                               f"Une erreur s'est produite : {str(e)}"))

    @db_transaction(max_retries=3)
    async def get_two_user_data(self, initiator_id, target_id, conn=None):
        """Get data for two users"""
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT user_id, user_authorid, gamble_currency FROM user WHERE user_authorid IN (%s, %s)",
                (initiator_id, target_id)
            )
            results = await cursor.fetchall()
            users_data = []
            for result in results:
                users_data.append({
                    'user_id': result[0],
                    'user_authorid': result[1],
                    'gamble_currency': result[2]
                })
            return users_data

    @db_transaction(max_retries=3)
    async def deduct_bet_from_users(self, initiator_id, target_id, amount, conn=None):
        """Deduct bet amount from both users"""
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE user SET gamble_currency = gamble_currency - %s WHERE user_id IN (%s, %s)",
                (amount, initiator_id, target_id)
            )

    @db_transaction(max_retries=3)
    async def award_winner(self, winner_id, amount, conn=None):
        """Award winnings to the winner"""
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s",
                (amount, winner_id)
            )

   # @commands.command(name="rr",aliases=['russianroulette','russian'])
    @command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def russian_roulette(self, ctx, target: Optional[discord.Member] = None, amount: int = 1):
        """
        Initiate a Russian Roulette gambling game with another user
        
        :param ctx: Command context
        :param target: The user to challenge
        :param amount: The betting amount (defaults to 1)
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "rr")
        if on_cooldown:
            lang = await _get_lang(ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=_tr(lang, f"⏰ Command on cooldown. Please wait {time_left} seconds.",
                                f"⏰ الأمر في فترة تهدئة. الرجاء الانتظار {time_left} ثوانٍ.",
                                f"⏰ Commande en temps de recharge. Veuillez patienter {time_left} secondes."),
                color=discord.Color.red()
            ))
            return

        GUN_EMOTE = "<:gunemote:1318294271735890052>"
        lang = await _get_lang(ctx.author.id)

        if not target:
            await ctx.send(embed=discord.Embed(
                description=_tr(lang, "❌ You must specify a target to challenge.",
                                "❌ يجب تحديد شخص للتحدي.",
                                "❌ Vous devez spécifier une cible à défier."),
                color=discord.Color.red()
            ))
            return

        if target == ctx.author:
            await ctx.send(embed=discord.Embed(
                description=_tr(lang, "❌ You cannot challenge yourself!",
                                "❌ لا يمكنك تحدي نفسك!",
                                "❌ Vous ne pouvez pas vous défier vous-même !"),
                color=discord.Color.red()
            ))
            return

        if amount <= 0:
            await ctx.send(embed=discord.Embed(
                description=_tr(lang, "❌ Invalid betting amount. Must be greater than 0.",
                                "❌ مبلغ الرهان غير صالح. يجب أن يكون أكبر من 0.",
                                "❌ Montant de mise invalide. Doit être supérieur à 0."),
                color=discord.Color.red()
            ))
            return

        try:
            # Get both users' data
            users_data = await self.get_two_user_data(str(ctx.author.id), str(target.id))
            
            if len(users_data) != 2:
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang, "❌ One or both users are not registered.",
                                    "❌ مستخدم واحد أو كلاهما غير مسجل.",
                                    "❌ Un ou les deux utilisateurs ne sont pas enregistrés."),
                    color=discord.Color.red()
                ))
                return
            
            # Find initiator and target data
            initiator_info = next((u for u in users_data if u['user_authorid'] == str(ctx.author.id)), None)
            target_info = next((u for u in users_data if u['user_authorid'] == str(target.id)), None)
            
            if not initiator_info or not target_info:
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang, "❌ User data not found.",
                                    "❌ لم يتم العثور على بيانات المستخدم.",
                                    "❌ Données utilisateur introuvables."),
                    color=discord.Color.red()
                ))
                return
            
            initiator_user_id = initiator_info['user_id']
            target_user_id = target_info['user_id']
            
            # Check if either user is already gambling
            if await self.check_gamble_status(initiator_user_id):
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang, "⏰ You are already in a gambling session. Please wait until it completes.",
                                    "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                                    "⏰ Vous êtes déjà en session de jeu. Veuillez attendre qu'elle se termine."),
                    color=discord.Color.red()
                ))
                return
                
            if await self.check_gamble_status(target_user_id):
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang, f"⏰ {target.mention} is already in a gambling session. Please wait until it completes.",
                                    f"⏰ {target.mention} في جلسة مقامرة بالفعل. يرجى الانتظار حتى تنتهي.",
                                    f"⏰ {target.mention} est déjà en session de jeu. Veuillez attendre qu'elle se termine."),
                    color=discord.Color.red()
                ))
                return
            
            # Add both users to gamble_status
            await self.add_to_gamble_status(initiator_user_id)
            await self.add_to_gamble_status(target_user_id)
            
            try:
                if amount < 0:
                    await ctx.send(_tr(lang, "❌ You can't bet with a negative amount",
                                       "❌ لا يمكنك المراهنة بمبلغ سلبي",
                                       "❌ Vous ne pouvez pas miser un montant négatif"))
                    return
                if initiator_info['gamble_currency'] < amount:
                    await ctx.send(embed=discord.Embed(
                        description=_tr(lang, f"❌ {ctx.author.mention} does not have enough gambling currency.",
                                        f"❌ {ctx.author.mention} لا يمتلك رصيد مقامرة كافياً.",
                                        f"❌ {ctx.author.mention} n'a pas assez de monnaie de jeu."),
                        color=discord.Color.red()
                    ))
                    return
                
                if target_info['gamble_currency'] < amount:
                    await ctx.send(embed=discord.Embed(
                        description=_tr(lang, f"❌ {target.mention} does not have enough gambling currency.",
                                        f"❌ {target.mention} لا يمتلك رصيد مقامرة كافياً.",
                                        f"❌ {target.mention} n'a pas assez de monnaie de jeu."),
                        color=discord.Color.red()
                    ))
                    return
                
                # Challenge embed
                challenge_embed = discord.Embed(
                    title=_tr(lang, "🔫 RUSSIAN ROULETTE CHALLENGE 🔫",
                              "🔫 تحدي الروليت الروسي 🔫",
                              "🔫 DÉFI ROULETTE RUSSE 🔫"),
                    description=_tr(
                        lang,
                        f"{ctx.author.mention} challenges {target.mention} to Russian Roulette!\nBetting Amount: {amount} gambling currency\n\n**{target.mention}**, do you accept the challenge?\n\nReact with ✅ to accept or ❌ to decline.",
                        f"{ctx.author.mention} يتحدى {target.mention} في الروليت الروسي!\nقيمة الرهان: {amount} من عملة المقامرة\n\n**{target.mention}**، هل تقبل التحدي؟\n\nتفاعل بـ ✅ للقبول أو ❌ للرفض.",
                        f"{ctx.author.mention} défie {target.mention} à la roulette russe !\nMise : {amount} monnaie de jeu\n\n**{target.mention}**, acceptes-tu le défi ?\n\nRéagis avec ✅ pour accepter ou ❌ pour refuser."
                    ),
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
                            description=_tr(lang, f"{target.mention} declined the Russian Roulette challenge.",
                                            f"{target.mention} رفض تحدي الروليت الروسي.",
                                            f"{target.mention} a refusé le défi de la roulette russe."),
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=decline_embed)
                        return
                except asyncio.TimeoutError:
                    timeout_embed = discord.Embed(
                        description=_tr(lang, "Challenge timed out.", "انتهى وقت التحدي.", "Le défi a expiré."),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=timeout_embed)
                    return

                # Deduct betting amount from both players immediately after challenge acceptance
                await self.deduct_bet_from_users(initiator_info['user_id'], target_info['user_id'], amount)
                
                # Update local info
                initiator_info['gamble_currency'] -= amount
                target_info['gamble_currency'] -= amount
                
                game_embed = discord.Embed(
                    title=_tr(lang, "🔫 RUSSIAN ROULETTE BEGINS 🔫",
                              "🔫 تبدأ الروليت الروسي 🔫",
                              "🔫 LA ROULETTE RUSSE COMMENCE 🔫"),
                    description=_tr(
                        lang,
                        f"**Participants:**\n{ctx.author.mention} vs {target.mention}\n\n**PREPARE YOURSELVES!**\nThe game begins in 3 seconds...",
                        f"**المشاركون:**\n{ctx.author.mention} ضد {target.mention}\n\n**استعدوا!**\nستبدأ اللعبة خلال 3 ثوانٍ...",
                        f"**Participants :**\n{ctx.author.mention} vs {target.mention}\n\n**PRÉPAREZ-VOUS !**\nLa partie commence dans 3 secondes..."
                    ),
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
                        title=_tr(lang, "🔫 RUSSIAN ROULETTE IN PROGRESS 🔫",
                                  "🔫 الروليت الروسي قيد التنفيذ 🔫",
                                  "🔫 ROULETTE RUSSE EN COURS 🔫"),
                        description=_tr(
                            lang,
                            f"**Current Player:** {current_player.mention}\n**REACT TO THE GUN EMOTE!**\nYou have 10 seconds to react...",
                            f"**اللاعب الحالي:** {current_player.mention}\n**تفاعل بإيموجي المسدس!**\nلديك 10 ثوانٍ للتفاعل...",
                            f"**Joueur actuel :** {current_player.mention}\n**RÉAGIS À L'ÉMOJI PISTOLET !**\nTu as 10 secondes pour réagir..."
                        ),
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
                            await self.award_winner(winner_info['user_id'], amount * 2)
                            
                            result_embed = discord.Embed(
                                title=_tr(lang, "🏆 RUSSIAN ROULETTE RESULT 🏆",
                                          "🏆 نتيجة الروليت الروسي 🏆",
                                          "🏆 RÉSULTAT ROULETTE RUSSE 🏆"),
                                description=_tr(
                                    lang,
                                    f"**Loser:** {loser.mention}! (Shot by the gun)\n**Winner:** {winner.mention}!\n\n{winner.mention}'s New Balance: {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\n{loser.mention}'s New Balance: {loser_info['gamble_currency']} <:gamble:1314341368176574564>",
                                    f"**الخاسر:** {loser.mention}! (أصابته الطلقة)\n**الفائز:** {winner.mention}!\n\nرصيد {winner.mention} الجديد: {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\nرصيد {loser.mention} الجديد: {loser_info['gamble_currency']} <:gamble:1314341368176574564>",
                                    f"**Perdant :** {loser.mention} ! (Touché par la balle)\n**Gagnant :** {winner.mention} !\n\nNouveau solde de {winner.mention} : {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\nNouveau solde de {loser.mention} : {loser_info['gamble_currency']} <:gamble:1314341368176574564>"
                                ),
                                color=discord.Color.gold()
                            )
                            await game_message.clear_reactions()
                            await game_message.edit(embed=result_embed)
                            break
                        else:
                            await game_message.clear_reactions()
                            game_embed = discord.Embed(
                                title=_tr(lang, "🔫 RUSSIAN ROULETTE CONTINUES 🔫",
                                          "🔫 تستمر الروليت الروسي 🔫",
                                          "🔫 LA ROULETTE RUSSE CONTINUE 🔫"),
                                description=_tr(
                                    lang,
                                    f"**{current_player.mention}** survived!\nNow it's **{other_player.mention}**'s turn.",
                                    f"**{current_player.mention}** نجا!\nحان دور **{other_player.mention}**.",
                                    f"**{current_player.mention}** a survécu !\nC'est maintenant le tour de **{other_player.mention}**."
                                ),
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
                        await self.award_winner(winner_info['user_id'], amount * 2)
                        
                        result_embed = discord.Embed(
                            title=_tr(lang, "🏆 RUSSIAN ROULETTE RESULT 🏆",
                                      "🏆 نتيجة الروليت الروسي 🏆",
                                      "🏆 RÉSULTAT ROULETTE RUSSE 🏆"),
                            description=_tr(
                                lang,
                                f"**Loser:** {loser.mention}! (Failed to react in time)\n**Winner:** {winner.mention}!\n\n{winner.mention}'s New Balance: {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\n{loser.mention}'s New Balance: {loser_info['gamble_currency']} <:gamble:1314341368176574564>",
                                f"**الخاسر:** {loser.mention}! (فشل في التفاعل في الوقت المحدد)\n**الفائز:** {winner.mention}!\n\nرصيد {winner.mention} الجديد: {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\nرصيد {loser.mention} الجديد: {loser_info['gamble_currency']} <:gamble:1314341368176574564>",
                                f"**Perdant :** {loser.mention} ! (N'a pas réagi à temps)\n**Gagnant :** {winner.mention} !\n\nNouveau solde de {winner.mention} : {winner_info['gamble_currency'] + (amount * 2)} <:gamble:1314341368176574564>\nNouveau solde de {loser.mention} : {loser_info['gamble_currency']} <:gamble:1314341368176574564>"
                            ),
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
            lang = await _get_lang(ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=_tr(lang, f"❌ An error occurred: {str(e)}",
                                f"❌ حدث خطأ: {str(e)}",
                                f"❌ Une erreur s'est produite : {str(e)}"),
                color=discord.Color.red()
            ))

    @commands.command(name='coinflip', aliases=['cf','coin'])
    @command_lock("coinflip_command")  # Add command lock to prevent duplicate execution
    @command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def coinflip_command(self, ctx, *args):
        """
        Coinflip gambling command with simplified user interaction.
        """
     #   on_cooldown, time_left = self.check_cooldown(ctx.author.id, "coinflip")
     #   if on_cooldown:
      #      await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
      #      return

        try:
            user_data = await self.get_user_data(str(ctx.author.id))

            if not user_data:
                lang = await _get_lang(ctx.author.id)
                await ctx.send(_tr(lang, "User not found in the database.",
                                   "المستخدم غير موجود في قاعدة البيانات.",
                                   "Utilisateur introuvable dans la base de données."))
                return

            user_id = user_data['user_id']
            current_balance = user_data['gamble_currency']
            
            # Check if user is already gambling
            if await self.check_gamble_status(user_id):
                lang = await _get_lang(ctx.author.id)
                await ctx.send(_tr(lang, "⏰ You are already in a gambling session. Please wait until it completes.",
                                   "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                                   "⏰ Vous êtes déjà en session de jeu. Veuillez attendre qu'elle se termine."))
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

                lang = await _get_lang(ctx.author.id)

                if bet > current_balance:
                    await ctx.send(_tr(lang, f"❌ Insufficient funds! Your current balance is {current_balance} currency.",
                                       f"❌ أموال غير كافية! رصيدك الحالي هو {current_balance}.",
                                       f"❌ Fonds insuffisants ! Votre solde actuel est de {current_balance}."))
                    return
                if bet < 0:
                    await ctx.send(_tr(lang, "❌ Invalid betting amount. Must be greater than 0.",
                                       "❌ مبلغ الرهان غير صالح. يجب أن يكون أكبر من 0.",
                                       "❌ Montant de mise invalide. Doit être supérieur à 0."))
                    return
                if not side:
                    side = random.choice(['heads', 'tails'])

                # Deduct bet amount immediately
                current_balance -= bet
                await self.update_user_currency(str(ctx.author.id), current_balance)

                flip_embed = discord.Embed(
                    title=_tr(lang, "🪙 Coinflip in Progress 🪙",
                              "🪙 قرعة العملة جارية 🪙",
                              "🪙 Pile ou Face en cours 🪙"),
                    description=_tr(lang, "Flipping the coin...", "جاري قلب العملة...", "On lance la pièce..."),
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
                    result_title = _tr(lang, "🎉 You Won!", "🎉 لقد فزت!", "🎉 Tu as gagné !")
                    result_desc = _tr(lang, f"Congratulations! You won {winnings} currency!",
                                      f"تهانينا! ربحت {winnings} عملة!",
                                      f"Félicitations ! Tu as gagné {winnings} monnaie !")
                    
                    # Update database with winnings
                    await self.update_user_currency(str(ctx.author.id), new_balance)
                else:
                    # Loss - bet amount already deducted
                    winnings = 0
                    new_balance = current_balance
                    result_color = discord.Color.red()
                    result_title = _tr(lang, "😢 Better Luck Next Time", "😢 حظاً أوفر في المرة القادمة", "😢 Bonne chance la prochaine fois")
                    result_desc = _tr(lang, f"You lost {bet} currency.", f"لقد خسرت {bet} عملة.", f"Tu as perdu {bet} monnaie.")

                result_embed = discord.Embed(
                    title=result_title,
                    description=result_desc,
                    color=result_color
                )
                result_embed.add_field(
                    name=_tr(lang, "Coin Result", "نتيجة القرعة", "Résultat"), 
                    value=_tr(lang,
                              f"Your side: {side.capitalize()}\nComputer's side: {computer_side.capitalize()}",
                              f"اختيارك: {side.capitalize()}\nاختيار الكمبيوتر: {computer_side.capitalize()}",
                              f"Ton côté : {side.capitalize()}\nCôté de l'ordinateur : {computer_side.capitalize()}"), 
                    inline=False
                )
                result_embed.add_field(
                    name=_tr(lang, "💰 New Balance", "💰 الرصيد الجديد", "💰 Nouveau solde"), 
                    value=_tr(lang, f"**GWon** : {new_balance} <:gamble:1314341368176574564>",
                              f"**جي-وون** : {new_balance} <:gamble:1314341368176574564>",
                              f"**GWon** : {new_balance} <:gamble:1314341368176574564>"), 
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
            lang = await _get_lang(ctx.author.id)
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}",
                               f"حدث خطأ: {str(e)}",
                               f"Une erreur s'est produite : {str(e)}"))

    @db_transaction(max_retries=3)
    async def get_user_premium_data(self, user_authorid, conn=None):
        """Get user data including premium status"""
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT user_id, gamble_currency, won_currency, prenium, prenium_date, roll_cap 
                FROM `user` 
                WHERE user_authorid = %s
            """, (user_authorid,))
            result = await cursor.fetchone()
            if result:
                return {
                    'user_id': result[0],
                    'gamble_currency': result[1],
                    'won_currency': result[2],
                    'prenium': result[3],
                    'prenium_date': result[4],
                    'roll_cap': result[5]
                }
            return None

    @db_transaction(max_retries=3)
    async def update_premium_status(self, user_authorid, new_roll_cap, conn=None):
        """Update user's premium status when expired"""
        async with conn.cursor() as cursor:
            await cursor.execute("""
                UPDATE `user` 
                SET prenium = 0, prenium_date = NULL, roll_cap = %s 
                WHERE user_authorid = %s
            """, (new_roll_cap, user_authorid))

    @db_transaction(max_retries=3)
    async def convert_currency(self, user_authorid, convertible_amount, won_converted, conn=None):
        """Convert gambling currency to won currency"""
        async with conn.cursor() as cursor:
            await cursor.execute("""
                UPDATE `user` 
                SET gamble_currency = gamble_currency - %s, won_currency = won_currency + %s 
                WHERE user_authorid = %s
            """, (convertible_amount, won_converted, user_authorid))

    #@commands.command(name='convert', aliases=["cvt","conv"])
    async def convert_command(self, ctx, amount: str = None):
        """
        Convert gamble currency to won currency.
        Base conversion rate: 50,000 gamble currency = 1,000 won currency
        Premium users get better rates based on their tier.
        """
        lang = await _get_lang(ctx.author.id)
        if amount is None:
            await ctx.send(_tr(lang,
                               "❌ Please specify an amount to convert or use 'all'. \nUsage: `sl convert <amount>` or `sl convert all`",
                               "❌ الرجاء تحديد مبلغ للتحويل أو استخدام 'all'. \nالاستخدام: `sl convert <amount>` أو `sl convert all`",
                               "❌ Veuillez indiquer un montant à convertir ou utiliser 'all'. \nUtilisation : `sl convert <amount>` ou `sl convert all`"))
            return

        try:
            # Fetch user data including premium status
            user_data = await self.get_user_premium_data(str(ctx.author.id))

            if not user_data:
                await ctx.send(_tr(lang, "User not found in the database.",
                                   "المستخدم غير موجود في قاعدة البيانات.",
                                   "Utilisateur introuvable dans la base de données."))
                return

            user_id = user_data['user_id']
            current_gamble = user_data['gamble_currency']
            current_won = user_data['won_currency']
            premium_status = user_data['prenium']
            premium_date = user_data['prenium_date']
            roll_cap = user_data['roll_cap']
            
            # Check if user is already gambling
            if await self.check_gamble_status(user_id):
                await ctx.send(_tr(lang, "⏰ You are already in a gambling session. Please wait until it completes.",
                                   "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                                   "⏰ Vous êtes déjà en session de jeu. Veuillez attendre qu'elle se termine."))
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
                        await self.update_premium_status(str(ctx.author.id), new_roll_cap)
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
                        await ctx.send(_tr(lang, f"❌ You need at least {min_conversion:,} <:gamble:1314341368176574564> GWon to convert.",
                                           f"❌ تحتاج إلى {min_conversion:,} <:gamble:1314341368176574564> جي-وون على الأقل للتحويل.",
                                           f"❌ Vous avez besoin d'au moins {min_conversion:,} <:gamble:1314341368176574564> GWon pour convertir."))
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
                        await ctx.send(_tr(lang, "❌ Invalid amount. Please use a number or 'all'.",
                                           "❌ مبلغ غير صالح. يرجى استخدام رقم أو 'all'.",
                                           "❌ Montant invalide. Veuillez utiliser un nombre ou 'all'."))
                        return

                    # Check minimum conversion requirement
                    if parsed_amount < min_conversion:
                        await ctx.send(_tr(lang, f"❌ You need at least {min_conversion:,} <:gamble:1314341368176574564> GWon to convert.",
                                           f"❌ تحتاج إلى {min_conversion:,} <:gamble:1314341368176574564> جي-وون على الأقل للتحويل.",
                                           f"❌ Vous avez besoin d'au moins {min_conversion:,} <:gamble:1314341368176574564> GWon pour convertir."))
                        return

                    # Ensure user has enough gamble currency
                    if parsed_amount > current_gamble:
                        await ctx.send(_tr(lang, f"❌ Insufficient funds! Your current balance is {current_gamble:,} <:gamble:1314341368176574564> GWon.",
                                           f"❌ أموال غير كافية! رصيدك الحالي {current_gamble:,} <:gamble:1314341368176574564> جي-وون.",
                                           f"❌ Fonds insuffisants ! Votre solde actuel est de {current_gamble:,} <:gamble:1314341368176574564> GWon."))
                        return

                    # Calculate convertible amount
                    convertible_amount = (parsed_amount // min_conversion) * min_conversion
                    won_converted = convertible_amount // conversion_rate
                    remaining_gamble = current_gamble - convertible_amount

                # Update currency values
                await self.convert_currency(str(ctx.author.id), convertible_amount, won_converted)

                # Create embed to show conversion result
                embed = discord.Embed(
                    title=_tr(lang, "💱 Currency Conversion", "💱 تحويل العملة", "💱 Conversion de devise"),
                    color=discord.Color.blue()
                )
                
                status_text = ""
                if premium_expired:
                    status_text = _tr(lang, "\n⚠️ Your premium has expired and has been reset.",
                                      "\n⚠️ انتهت صلاحية حسابك المميز وتمت إعادة تعيينه.",
                                      "\n⚠️ Votre premium a expiré et a été réinitialisé.")
                elif premium_status > 0:
                    status_text = _tr(lang, f"\n✨ Premium Tier {premium_status} conversion rate applied!",
                                      f"\n✨ تم تطبيق معدل تحويل الفئة المميزة {premium_status}!",
                                      f"\n✨ Taux de conversion Premium palier {premium_status} appliqué !")

                embed.add_field(
                    name=_tr(lang, "Conversion Details", "تفاصيل التحويل", "Détails de conversion"),
                    value=(
                        _tr(lang, "Converted", "المحوّل", "Converti") + f": {convertible_amount:,} <:gamble:1314341368176574564> GWon\n"
                        + _tr(lang, "Received", "المستلم", "Reçu") + f": {won_converted:,} <:won:1304034228966658048> Won\n"
                        + _tr(lang, "Remaining GWon", "GWon المتبقي", "GWon restant") + f": {remaining_gamble:,} <:gamble:1314341368176574564> GWon"
                        + status_text
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
            lang = await _get_lang(ctx.author.id)
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}",
                               f"حدث خطأ: {str(e)}",
                               f"Une erreur s'est produite : {str(e)}"))

    @commands.command(name='duel', aliases=["dll","du"])
    @command_lock("duel")  # Add command lock to prevent duplicate execution
    @command_cooldown(1, 3.0, commands.BucketType.user)  # 1 use every 3 seconds per user
    async def duel_command(self, ctx, user: discord.Member = None, bet: int = 1):
        """
        Duel gambling command where two users can bet against each other.
        Bet amount is deducted upfront from both players.
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "duel")
        lang = await _get_lang(ctx.author.id)
        if on_cooldown:
            await ctx.send(_tr(lang, f"⏰ Command on cooldown. Please wait {time_left} seconds.",
                               f"⏰ الأمر في فترة تهدئة. الرجاء الانتظار {time_left} ثوانٍ.",
                               f"⏰ Commande en temps de recharge. Veuillez patienter {time_left} secondes."))
            return
            
        try:
            # Validate second user
            if not user or user == ctx.author:
                await ctx.send(_tr(lang, "Please mention a different user to duel with!",
                                   "يرجى منشن مستخدم آخر للمبارزة!",
                                   "Veuillez mentionner un autre utilisateur pour le duel !"))
                return

            # Get both users' data
            users_data = await self.get_two_user_data(str(ctx.author.id), str(user.id))
            
            if len(users_data) != 2:
                await ctx.send(_tr(lang, "One or both users are not registered in the database.",
                                   "مستخدم واحد أو كلاهما غير مسجل في قاعدة البيانات.",
                                   "Un ou les deux utilisateurs ne sont pas enregistrés dans la base."))
                return
            
            # Find initiator and target data
            initiator_data = next((u for u in users_data if u['user_authorid'] == str(ctx.author.id)), None)
            target_data = next((u for u in users_data if u['user_authorid'] == str(user.id)), None)

            initiator_user_id = initiator_data['user_id']
            target_user_id = target_data['user_id']
            initiator_balance = initiator_data['gamble_currency']
            target_balance = target_data['gamble_currency']
            
            # Check if either user is already gambling
            if await self.check_gamble_status(initiator_user_id):
                await ctx.send(_tr(lang, "⏰ You are already in a gambling session. Please wait until it completes.",
                                   "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                                   "⏰ Vous êtes déjà en session de jeu. Veuillez attendre qu'elle se termine."))
                return
                
            if await self.check_gamble_status(target_user_id):
                await ctx.send(_tr(lang, f"⏰ {user.mention} is already in a gambling session. Please wait until it completes.",
                                   f"⏰ {user.mention} في جلسة مقامرة بالفعل. يرجى الانتظار حتى تنتهي.",
                                   f"⏰ {user.mention} est déjà en session de jeu. Veuillez attendre qu'elle se termine."))
                return
                
            # Add both users to gamble_status
            await self.add_to_gamble_status(initiator_user_id)
            await self.add_to_gamble_status(target_user_id)
            
            try:
                if bet < 0:
                    await ctx.send(_tr(lang, "❌ Invalid betting amount. Must be greater than 0.",
                                       "❌ مبلغ الرهان غير صالح. يجب أن يكون أكبر من 0.",
                                       "❌ Montant de mise invalide. Doit être supérieur à 0."))
                    return
                # Validate bet amounts
                if bet > initiator_balance:
                    await ctx.send(_tr(lang, f"{ctx.author.mention}, you don't have enough currency to make this duel!",
                                       f"{ctx.author.mention}، لا تملك رصيداً كافياً لإجراء هذه المبارزة!",
                                       f"{ctx.author.mention}, vous n'avez pas assez de monnaie pour ce duel !"))
                    return
                
                if bet > target_balance:
                    await ctx.send(_tr(lang, f"{user.mention} doesn't have enough currency to accept this duel!",
                                       f"{user.mention} لا يملك رصيداً كافياً لقبول هذه المبارزة!",
                                       f"{user.mention} n'a pas assez de monnaie pour accepter ce duel !"))
                    return

                # Create invite embed
                invite_embed = discord.Embed(
                    title=_tr(lang, "🎲 Duel Challenge 🎲", "🎲 تحدي المبارزة 🎲", "🎲 Défi Duel 🎲"),
                    description=_tr(lang,
                                    f"{ctx.author.mention} challenges {user.mention} to a duel for {bet} currency!",
                                    f"{ctx.author.mention} يتحدى {user.mention} في مبارزة مقابل {bet} عملة!",
                                    f"{ctx.author.mention} défie {user.mention} pour un duel à {bet} monnaie !"),
                    color=discord.Color.gold()
                )
                invite_embed.add_field(
                    name=_tr(lang, "Instructions", "التعليمات", "Instructions"), 
                    value=_tr(lang, "React with ✅ to accept or ❌ to decline",
                              "تفاعل بـ ✅ للقبول أو ❌ للرفض",
                              "Réagis avec ✅ pour accepter ou ❌ pour refuser"),
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
                            title=_tr(lang, "🚫 Duel Declined", "🚫 تم رفض المبارزة", "🚫 Duel refusé"),
                            description=_tr(lang, f"{user.mention} declined the duel!",
                                            f"{user.mention} رفض المبارزة!",
                                            f"{user.mention} a refusé le duel !"),
                            color=discord.Color.red()
                        )
                        await invite_message.edit(embed=decline_embed)
                        return

                    # If user accepts - deduct bets from both players immediately
                    await self.deduct_bet_from_users(initiator_user_id, target_user_id, bet)

                    # Update local balance variables
                    initiator_balance -= bet
                    target_balance -= bet

                    toss_embed = discord.Embed(
                        title=_tr(lang, "🎲 Duel in Progress", "🎲 المبارزة جارية", "🎲 Duel en cours"),
                        description=_tr(lang, "Flipping the coin...", "جاري قلب العملة...", "On lance la pièce..."),
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
                        winner_id = initiator_user_id
                    else:
                        new_initiator_balance = initiator_balance
                        new_target_balance = target_balance + (bet * 2)
                        winner_id = target_user_id

                    # Update winner's balance in database
                    await self.award_winner(winner_id, bet * 2)

                    # Result embed
                    result_embed = discord.Embed(
                        title=_tr(lang, "🎉 Duel Complete", "🎉 اكتملت المبارزة", "🎉 Duel terminé"),
                        description=_tr(lang, f"{winner.mention} won the duel!",
                                        f"{winner.mention} فاز في المبارزة!",
                                        f"{winner.mention} a remporté le duel !"),
                        color=discord.Color.green()
                    )
                    result_embed.add_field(
                        name=_tr(lang, "Winnings", "المكافأة", "Gain"), 
                        value=_tr(lang, f"{winner.mention} wins {bet * 2} currency",
                                  f"{winner.mention} يفوز بـ {bet * 2} عملة",
                                  f"{winner.mention} gagne {bet * 2} monnaie"),
                        inline=False
                    )
                    result_embed.add_field(
                        name=_tr(lang, "New Balances", "الأرصدة الجديدة", "Nouveaux soldes"), 
                        value=f"{ctx.author.mention}: {new_initiator_balance} <:gamble:1314341368176574564> GWon\n{user.mention}: {new_target_balance} <:gamble:1314341368176574564> GWon",
                        inline=False
                    )

                    await invite_message.edit(embed=result_embed)

                except asyncio.TimeoutError:
                    timeout_embed = discord.Embed(
                        title=_tr(lang, "⏰ Duel Expired", "⏰ انتهت المهلة للمبارزة", "⏰ Duel expiré"),
                        description=_tr(lang, "The duel challenge was not accepted in time.",
                                        "لم يتم قبول تحدي المبارزة في الوقت المحدد.",
                                        "Le défi de duel n'a pas été accepté à temps."),
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
            lang = await _get_lang(ctx.author.id)
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}",
                               f"حدث خطأ: {str(e)}",
                               f"Une erreur s'est produite : {str(e)}"))

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
