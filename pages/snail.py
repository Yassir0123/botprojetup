import discord
from discord.ext import commands
from typing import Optional
import asyncio
import random
import functools
from utils.database import db_transaction,command_lock, execute_query_with_retry
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
                    await ctx.send(_tr(lang,
                        f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                        f"⏰ الأمر في فترة تهدئة. الرجاء الانتظار {remaining} ثانية.",
                        f"⏰ Commande en recharge. Veuillez patienter {remaining} secondes."
                    ))
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
        self.cooldowns = {}  # Dictionary to store cooldowns for users

    def check_cooldown(self, user_id, command_name, cooldown_duration=60):
        """
        Checks if a user is on cooldown for a specific command.
        
        :param user_id: The ID of the user.
        :param command_name: The name of the command.
        :param cooldown_duration: The cooldown duration in seconds (default: 60).
        :return: A tuple containing a boolean indicating whether the command is on cooldown and the time left in seconds.
        """
        if user_id in self.cooldowns and command_name in self.cooldowns[user_id]:
            time_elapsed = discord.utils.utcnow() - self.cooldowns[user_id][command_name]
            time_left = cooldown_duration - time_elapsed.total_seconds()
            if time_left > 0:
                return True, round(time_left)
        return False, 0

    def set_cooldown(self, user_id, command_name):
        """
        Sets a cooldown for a user for a specific command.
        
        :param user_id: The ID of the user.
        :param command_name: The name of the command.
        """
        if user_id not in self.cooldowns:
            self.cooldowns[user_id] = {}
        self.cooldowns[user_id][command_name] = discord.utils.utcnow()

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
    async def get_two_user_data(self, user_authorid1: str, user_authorid2: str, conn=None):
        """
        Retrieves data for two users from the database.
        
        :param user_authorid1: The author ID of the first user.
        :param user_authorid2: The author ID of the second user.
        :return: A list containing the data for both users.
        """
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT user_id, user_authorid, gamble_currency FROM user WHERE user_authorid IN (%s, %s)", 
                (user_authorid1, user_authorid2)
            )
            users_data = await cursor.fetchall()
            
        # Convert the list of tuples to a list of dictionaries
        users_list = []
        for user in users_data:
            user_dict = {
                'user_id': user[0],
                'user_authorid': user[1],
                'gamble_currency': user[2]
            }
            users_list.append(user_dict)
        
        return users_list

    @db_transaction(max_retries=3)
    async def deduct_bet_from_users(self, user_id1: int, user_id2: int, amount: int, conn=None):
        """
        Deducts the bet amount from both users' gambling currency.
        
        :param user_id1: The ID of the first user.
        :param user_id2: The ID of the second user.
        :param amount: The amount to deduct.
        """
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE user SET gamble_currency = gamble_currency - %s WHERE user_id IN (%s, %s)", 
                (amount, user_id1, user_id2)
            )

    @db_transaction(max_retries=3)
    async def award_winner(self, user_id: int, amount: int, conn=None):
        """
        Awards the winner with the specified amount of gambling currency.
        
        :param user_id: The ID of the winner.
        :param amount: The amount to award.
        """
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s", 
                (amount, user_id)
            )

    @db_transaction(max_retries=3)
    async def return_bets_to_users(self, user_id1: int, user_id2: int, amount: int, conn=None):
        """
        Returns the bets to both users in case of a tie.
        
        :param user_id1: The ID of the first user.
        :param user_id2: The ID of the second user.
        :param amount: The amount to return.
        """
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id IN (%s, %s)", 
                (amount, user_id1, user_id2)
            )

    @commands.command(name="race", aliases=["sr", "rcs"])
    @command_lock("snail_course")
    @command_cooldown(1, 5.0, commands.BucketType.user)  # 1 use every 5 seconds per user
    async def snail_course(self, ctx, target: Optional[discord.Member] = None, amount: int = 1):
        """
        Initiate a snail race gambling game with another user
        
        :param ctx: Command context
        :param target: The user to challenge
        :param amount: The betting amount (defaults to 1)
        """
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "race")
        if on_cooldown:
            lang = await _get_lang(ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=_tr(lang,
                    f"⏰ Command on cooldown. Please wait {time_left} seconds.",
                    f"⏰ الأمر في فترة تهدئة. الرجاء الانتظار {time_left} ثانية.",
                    f"⏰ Commande en recharge. Veuillez patienter {time_left} secondes."
                ),
                color=discord.Color.red()
            ))
            return

        # Set cooldown for this user
        self.set_cooldown(ctx.author.id, "race")

        # Emojis for snails
        GREEN_SNAIL = "<:greensail:1318274540962320398>"
        RED_SNAIL = "<:redsnail:1318274530816561202>"
        TRACK_MARKER = "🔹"
        FINISH_LINE = "🏁"

        # Check if a target is specified
        if not target:
            lang = await _get_lang(ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=_tr(lang,
                    "❌ You must specify a target to challenge.",
                    "❌ يجب أن تحدد شخصاً لتتحداه.",
                    "❌ Vous devez spécifier une cible à défier."
                ),
                color=discord.Color.red()
            ))
            return

        # Prevent self-challenge
        if target == ctx.author:
            lang = await _get_lang(ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=_tr(lang,
                    "❌ You cannot challenge yourself!",
                    "❌ لا يمكنك تحدي نفسك!",
                    "❌ Vous ne pouvez pas vous défier vous-même !"
                ),
                color=discord.Color.red()
            ))
            return

        # Check if amount is valid
        if amount <= 0:
            lang = await _get_lang(ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=_tr(lang,
                    "❌ Invalid betting amount. Must be greater than 0.",
                    "❌ مبلغ الرهان غير صالح. يجب أن يكون أكبر من 0.",
                    "❌ Montant de mise invalide. Doit être supérieur à 0."
                ),
                color=discord.Color.red()
            ))
            return

        try:
            # Get both users' data
            users_data = await self.get_two_user_data(str(ctx.author.id), str(target.id))
            
            if len(users_data) != 2:
                lang = await _get_lang(ctx.author.id)
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang,
                        "❌ One or both users are not registered.",
                        "❌ مستخدم واحد أو كلاهما غير مسجل.",
                        "❌ Un ou les deux utilisateurs ne sont pas enregistrés."
                    ),
                    color=discord.Color.red()
                ))
                return
            
            # Find initiator and target data
            initiator_info = next((u for u in users_data if u['user_authorid'] == str(ctx.author.id)), None)
            target_info = next((u for u in users_data if u['user_authorid'] == str(target.id)), None)
            
            if not initiator_info or not target_info:
                lang = await _get_lang(ctx.author.id)
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang,
                        "❌ User data not found.",
                        "❌ لم يتم العثور على بيانات المستخدم.",
                        "❌ Données utilisateur introuvables."
                    ),
                    color=discord.Color.red()
                ))
                return
            
            initiator_user_id = initiator_info['user_id']
            target_user_id = target_info['user_id']
            
            # Check if either user is already gambling using database
            if await self.check_gamble_status(initiator_user_id):
                lang = await _get_lang(ctx.author.id)
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang,
                        "⏰ You are already in a gambling session. Please wait until it completes.",
                        "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                        "⏰ Vous êtes déjà en session de jeu. Veuillez attendre qu'elle se termine."
                    ),
                    color=discord.Color.red()
                ))
                return
            
            if await self.check_gamble_status(target_user_id):
                lang = await _get_lang(ctx.author.id)
                await ctx.send(embed=discord.Embed(
                    description=_tr(lang,
                        f"⏰ {target.mention} is already in a gambling session. Please wait until it completes.",
                        f"⏰ {target.mention} في جلسة مقامرة بالفعل. يرجى الانتظار حتى تنتهي.",
                        f"⏰ {target.mention} est déjà en session de jeu. Veuillez attendre qu'elle se termine."
                    ),
                    color=discord.Color.red()
                ))
                return
            
            # Add both users to gamble_status table
            await self.add_to_gamble_status(initiator_user_id)
            await self.add_to_gamble_status(target_user_id)
            
            try:
                # Check if both users have enough gambling currency
                if initiator_info['gamble_currency'] < amount:
                    lang = await _get_lang(ctx.author.id)
                    await ctx.send(embed=discord.Embed(
                        description=_tr(lang,
                            f"❌ {ctx.author.mention} does not have enough gambling currency.",
                            f"❌ {ctx.author.mention} لا يمتلك رصيد مقامرة كافياً.",
                            f"❌ {ctx.author.mention} n'a pas assez de monnaie de jeu."
                        ),
                        color=discord.Color.red()
                    ))
                    return
                
                if target_info['gamble_currency'] < amount:
                    lang = await _get_lang(ctx.author.id)
                    await ctx.send(embed=discord.Embed(
                        description=_tr(lang,
                            f"❌ {target.mention} does not have enough gambling currency.",
                            f"❌ {target.mention} لا يمتلك رصيد مقامرة كافياً.",
                            f"❌ {target.mention} n'a pas assez de monnaie de jeu."
                        ),
                        color=discord.Color.red()
                    ))
                    return
                
                # Challenge embed
                lang = await _get_lang(ctx.author.id)
                challenge_embed = discord.Embed(
                    title=_tr(lang, "🏁 Snail Race Challenge 🏁", "🏁 تحدي سباق القواقع 🏁", "🏁 Défi Course d'Escargots 🏁"),
                    description=_tr(
                        lang,
                        f"{ctx.author.mention} challenges {target.mention} to a snail race!\nBetting Amount: {amount} <:gamble:1314341368176574564> GWon\n\n**{target.mention}**, do you accept the challenge?\n\nReact with ✅ to accept or ❌ to decline.",
                        f"{ctx.author.mention} يتحدى {target.mention} في سباق القواقع!\nقيمة الرهان: {amount} <:gamble:1314341368176574564> جي-وون\n\n**{target.mention}**، هل تقبل التحدي؟\n\nتفاعل بـ ✅ للقبول أو ❌ للرفض.",
                        f"{ctx.author.mention} défie {target.mention} à une course d'escargots !\nMise : {amount} <:gamble:1314341368176574564> GWon\n\n**{target.mention}**, acceptes-tu le défi ?\n\nRéagis avec ✅ pour accepter ou ❌ pour refuser."
                    ),
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
                        lang = await _get_lang(ctx.author.id)
                        decline_embed = discord.Embed(
                            description=_tr(lang,
                                f"{target.mention} declined the snail race challenge.",
                                f"{target.mention} رفض تحدي سباق القواقع.",
                                f"{target.mention} a refusé le défi de la course d'escargots."
                            ),
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=decline_embed)
                        return
                except asyncio.TimeoutError:
                    lang = await _get_lang(ctx.author.id)
                    timeout_embed = discord.Embed(
                        description=_tr(lang,
                            "Challenge timed out.",
                            "انتهى وقت التحدي.",
                            "Le défi a expiré."
                        ),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=timeout_embed)
                    return

                # Deduct bet amount from both players immediately after acceptance
                await self.deduct_bet_from_users(initiator_info['user_id'], target_info['user_id'], amount)
                
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
                lang = await _get_lang(ctx.author.id)
                race_embed = discord.Embed(
                    title=_tr(lang, "🐌 SNAIL RACE STARTING SOON 🐌", "🐌 سباق القواقع سيبدأ قريباً 🐌", "🐌 COURSE D'ESCARGOTS DÉMARRE BIENTÔT 🐌"),
                    description=_tr(
                        lang,
                        f"**Racers:**\n{ctx.author.mention}: {create_track(GREEN_SNAIL, 0)}\n{target.mention}: {create_track(RED_SNAIL, 0)}\n\n{FINISH_LINE} Finish Line\n\n🚦 Race begins in 3 seconds...",
                        f"**المتسابقون:**\n{ctx.author.mention}: {create_track(GREEN_SNAIL, 0)}\n{target.mention}: {create_track(RED_SNAIL, 0)}\n\n{FINISH_LINE} خط النهاية\n\n🚦 يبدأ السباق خلال 3 ثوانٍ...",
                        f"**Coureurs :**\n{ctx.author.mention} : {create_track(GREEN_SNAIL, 0)}\n{target.mention} : {create_track(RED_SNAIL, 0)}\n\n{FINISH_LINE} Ligne d'arrivée\n\n🚦 La course commence dans 3 secondes..."
                    ),
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
                    # Move snails with weighted random movement (more realistic)
                    initiator_move = random.choices([1, 2, 3], weights=[40, 35, 25])[0]
                    target_move = random.choices([1, 2, 3], weights=[40, 35, 25])[0]
                    
                    initiator_position = min(initiator_position + initiator_move, 20)
                    target_position = min(target_position + target_move, 20)
                    
                    # Create progress bars for better visualization
                    initiator_progress = "█" * (initiator_position // 2) + "░" * (10 - (initiator_position // 2))
                    target_progress = "█" * (target_position // 2) + "░" * (10 - (target_position // 2))
                    
                    # Update race tracks
                    lang = await _get_lang(ctx.author.id)
                    race_update_embed = discord.Embed(
                        title=_tr(lang, f"🐌 SNAIL RACE - Round {round_number} 🐌",
                                  f"🐌 سباق القواقع - الجولة {round_number} 🐌",
                                  f"🐌 COURSE D'ESCARGOTS - Manche {round_number} 🐌"),
                        description=_tr(
                            lang,
                            f"**Race Track:**\n{ctx.author.mention}: {create_track(GREEN_SNAIL, initiator_position)}\nProgress: [{initiator_progress}] {initiator_position}/20\n\n{target.mention}: {create_track(RED_SNAIL, target_position)}\nProgress: [{target_progress}] {target_position}/20\n\n{FINISH_LINE} Finish Line\n\n**Last Move:**\n{GREEN_SNAIL} {ctx.author.display_name}: {initiator_move} steps\n{RED_SNAIL} {target.display_name}: {target_move} steps",
                            f"**مسار السباق:**\n{ctx.author.mention}: {create_track(GREEN_SNAIL, initiator_position)}\nالتقدم: [{initiator_progress}] {initiator_position}/20\n\n{target.mention}: {create_track(RED_SNAIL, target_position)}\nالتقدم: [{target_progress}] {target_position}/20\n\n{FINISH_LINE} خط النهاية\n\n**آخر حركة:**\n{GREEN_SNAIL} {ctx.author.display_name}: {initiator_move} خطوات\n{RED_SNAIL} {target.display_name}: {target_move} خطوات",
                            f"**Piste de course :**\n{ctx.author.mention} : {create_track(GREEN_SNAIL, initiator_position)}\nProgression : [{initiator_progress}] {initiator_position}/20\n\n{target.mention} : {create_track(RED_SNAIL, target_position)}\nProgression : [{target_progress}] {target_position}/20\n\n{FINISH_LINE} Ligne d'arrivée\n\n**Dernier mouvement :**\n{GREEN_SNAIL} {ctx.author.display_name} : {initiator_move} pas\n{RED_SNAIL} {target.display_name} : {target_move} pas"
                        ),
                        color=discord.Color.green()
                    )
                    
                    await race_message.edit(embed=race_update_embed)
                    await asyncio.sleep(2.5)  # Slightly longer delay for better viewing
                    
                    round_number += 1
                
                # Determine winner
                if initiator_position >= 20 and target_position >= 20:
                    # Tie - return both players' bets
                    await self.return_bets_to_users(initiator_info['user_id'], target_info['user_id'], amount)
                    
                    lang = await _get_lang(ctx.author.id)
                    tie_embed = discord.Embed(
                        title=_tr(lang, "🤝 It's a Tie! 🤝", "🤝 إنها تعادل! 🤝", "🤝 Match nul ! 🤝"),
                        description=_tr(
                            lang,
                            f"Both snails crossed the finish line simultaneously!\nAll bets have been returned to both players.\n\n**Final Positions:**\n{GREEN_SNAIL} {ctx.author.display_name}: 20/20\n{RED_SNAIL} {target.display_name}: 20/20\n\nRace completed in {round_number - 1} rounds!",
                            f"كِلا القوقعَين وصلا إلى خط النهاية في نفس اللحظة!\nتمت إعادة جميع الرهانات لكلا اللاعبين.\n\n**المراكز النهائية:**\n{GREEN_SNAIL} {ctx.author.display_name}: 20/20\n{RED_SNAIL} {target.display_name}: 20/20\n\nاكتمل السباق في {round_number - 1} جولة!",
                            f"Les deux escargots ont franchi la ligne d'arrivée simultanément !\nToutes les mises ont été restituées aux deux joueurs.\n\n**Positions finales :**\n{GREEN_SNAIL} {ctx.author.display_name} : 20/20\n{RED_SNAIL} {target.display_name} : 20/20\n\nCourse terminée en {round_number - 1} manches !"
                        ),
                        color=discord.Color.yellow()
                    )
                    await ctx.send(embed=tie_embed)
                    return
                
                # Determine the winner
                if initiator_position >= 20:
                    winner = ctx.author
                    winner_info = initiator_info
                    winner_snail = GREEN_SNAIL
                    loser = target
                    loser_snail = RED_SNAIL
                    loser_position = target_position
                else:
                    winner = target
                    winner_info = target_info
                    winner_snail = RED_SNAIL
                    loser = ctx.author
                    loser_snail = GREEN_SNAIL
                    loser_position = initiator_position
                
                # Update winner's balance (2x the bet amount since they already paid their bet)
                await self.award_winner(winner_info['user_id'], amount * 2)
                
                # Final result embed with more details
                lang = await _get_lang(ctx.author.id)
                result_embed = discord.Embed(
                    title=_tr(lang, "🏆 SNAIL RACE RESULTS 🏆", "🏆 نتائج سباق القواقع 🏆", "🏆 RÉSULTATS DE LA COURSE D'ESCARGOTS 🏆"),
                    description=_tr(
                        lang,
                        f"**🥇 Winner:** {winner.mention} {winner_snail}\n**🥈 Runner-up:** {loser.mention} {loser_snail}\n\n**Prize:** {amount * 2} <:gamble:1314341368176574564> GWon\n**Race Duration:** {round_number - 1} rounds\n**Final Gap:** {20 - loser_position} steps\n\nCongratulations to {winner.mention}! 🎉",
                        f"**🥇 الفائز:** {winner.mention} {winner_snail}\n**🥈 الوصيف:** {loser.mention} {loser_snail}\n\n**الجائزة:** {amount * 2} <:gamble:1314341368176574564> جي-وون\n**مدة السباق:** {round_number - 1} جولات\n**الفارق النهائي:** {20 - loser_position} خطوات\n\nتهانينا لـ {winner.mention}! 🎉",
                        f"**🥇 Gagnant :** {winner.mention} {winner_snail}\n**🥈 Second :** {loser.mention} {loser_snail}\n\n**Prix :** {amount * 2} <:gamble:1314341368176574564> GWon\n**Durée de la course :** {round_number - 1} manches\n**Écart final :** {20 - loser_position} pas\n\nFélicitations à {winner.mention} ! 🎉"
                    ),
                    color=discord.Color.gold()
                )
                
                result_embed.add_field(
                    name=_tr(lang, "💰 Updated Balances", "💰 الأرصدة المحدّثة", "💰 Soldes mis à jour"),
                    value=(
                        f"{ctx.author.mention}: {initiator_info['gamble_currency'] + (amount * 2 if winner == ctx.author else 0)} <:gamble:1314341368176574564>\n"
                        f"{target.mention}: {target_info['gamble_currency'] + (amount * 2 if winner == target else 0)} <:gamble:1314341368176574564>"
                    ),
                    inline=False
                )
                
                await ctx.send(embed=result_embed)
            
            finally:
                # Always remove both users from gamble_status table when done
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
                description=_tr(lang,
                    f"❌ An error occurred: {str(e)}",
                    f"❌ حدث خطأ: {str(e)}",
                    f"❌ Une erreur s'est produite : {str(e)}"
                ),
                color=discord.Color.red()
            ))

async def setup(bot):
    await bot.add_cog(SnailRace(bot))
