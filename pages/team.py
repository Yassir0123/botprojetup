import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import aiomysql
import io
import os
import time
import traceback
from datetime import datetime
import asyncio
import math
import concurrent.futures
import logging
from utils.database import execute_query_with_retry, get_metrics
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
# ---------- i18n helpers ----------
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    """Translate a short UI string based on lang (0=en, 1=ar, 3=fr)."""
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

def _team_title(lang: int, username: str) -> str:
    """Localized team title."""
    return f"{username}'s Team"

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
                    lang = await get_user_multilanguage(ctx.author.id)
                    await ctx.send(_tr(
                        lang,
                        f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                        f"⏰ الأمر في فترة تهدئة. يرجى الانتظار {remaining} ثانية.",
                        f"⏰ Commande en cooldown. Veuillez patienter {remaining} secondes."
                    ))
                    return
            else:
                # Create a new cooldown bucket
                cooldowns[bucket_key] = commands.Cooldown(rate, per)

            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator
class TeamViewButton(discord.ui.Button):
    """Button to view a specific team"""
    def __init__(self, team_id: int, user_id: int, user_authorid: int, cog, lang: int):
        super().__init__(label=f"Team #{team_id}", style=discord.ButtonStyle.primary)
        self.team_id = team_id
        self.user_id = user_id
        self.user_authorid = user_authorid
        self.cog = cog
        self.lang = lang

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message(
                _tr(
                    self.lang,
                    "You can only use buttons for your own teams.",
                    "يمكنك فقط استخدام الأزرار لفريقك الخاص.",
                    "Vous ne pouvez utiliser les boutons que pour vos propres équipes."
                ),
                ephemeral=True
            )
            return

        await interaction.response.defer()
        
        try:
            # Fetch team data
            query = """
            SELECT u.user_id, t.team_id, t.team_bg, c.card_id, c.card_name, uc.image, uc.current_level, c.card_rank, c.type_card, uc.pseudo_image
            FROM user u
            JOIN team t ON u.user_id = t.user_id
            LEFT JOIN team_cards tc ON t.team_id = tc.team_id
            LEFT JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = u.user_id
            LEFT JOIN card c ON uc.card_id = c.card_id
            WHERE u.user_id = %s AND t.team_id = %s
            ORDER BY tc.position
            """
            result = await execute_query_with_retry(query, (self.user_id, self.team_id))

            if not result or 'user_id' not in result[0]:
                await interaction.followup.send(_tr(
                    self.lang,
                    "Error: Team not found.",
                    "خطأ: لم يتم العثور على الفريق.",
                    "Erreur : équipe introuvable."
                ))
                return

            team_bg = result[0]['team_bg']
            team_cards = [r for r in result if r['card_id'] is not None]

            if not team_cards:
                await interaction.followup.send(_tr(
                    self.lang,
                    "Your team is empty.",
                    "فريقك فارغ.",
                    "Votre équipe est vide."
                ))
                return

            # Generate team image
            background_path = os.path.join(self.cog.image_path, team_bg)
            if not os.path.exists(background_path):
                await interaction.followup.send(_tr(
                    self.lang,
                    f"Error: Background image not found.",
                    f"خطأ: لم يتم العثور على صورة الخلفية.",
                    f"Erreur : image d'arrière-plan introuvable."
                ))
                return

            with Image.open(background_path).convert('RGBA').resize((1600, 900)) as background:
                draw = ImageDraw.Draw(background)

                image_positions = [(0, 150), (399, 150), (800, 150), (1205, 150)]
                card_size = (380, 580)
                text_positions = [(50, 750), (450, 750), (850, 750), (1250, 750)]

                for i, card in enumerate(team_cards[:4]):
                    card_image = self.cog.prepare_card_image(card, card_size)
                    if card_image:
                        background.paste(card_image, image_positions[i], card_image)

                    name_text = card['card_name']
                    name_bbox = self.cog.font_small.getbbox(name_text)
                    name_width = name_bbox[2] - name_bbox[0]
                    name_position = (text_positions[i][0] + (300 - name_width) // 2, text_positions[i][1])
                    self.cog.draw_gradient_text(draw, name_text, name_position, self.cog.font_small, name_width)

                    if card['type_card'] != 'Support':
                        level_text = f"Lv.{card['current_level']}"
                        level_bbox = self.cog.poppins_font.getbbox(level_text)
                        level_width = level_bbox[2] - level_bbox[0]
                        level_position = (text_positions[i][0] + (300 - level_width) // 2, text_positions[i][1] + 90)
                        self.cog.draw_gradient_text(draw, level_text, level_position, self.cog.poppins_font, level_width)

                user_obj = await self.cog.bot.fetch_user(self.user_authorid)
                username = getattr(user_obj, "display_name", str(user_obj))
                title_text = f"{username}'s Team"
                title_bbox = self.cog.title_font.getbbox(title_text)
                title_width = title_bbox[2] - title_bbox[0]
                title_position = ((1600 - title_width) // 2, 20)
                self.cog.draw_gradient_text(draw, title_text, title_position, self.cog.title_font, title_width)

                buffer = io.BytesIO()
                background.convert('RGB').save(buffer, format='JPEG', quality=85, optimize=True)
                buffer.seek(0)

            message_content = f"{interaction.user.mention} " + _tr(self.lang, "Here's your team:", "إليك فريقك:", "Voici votre équipe :")
            await interaction.followup.send(content=message_content, file=discord.File(buffer, filename="team_view.jpg"))

        except Exception as e:
            error_msg = f"An error occurred: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await interaction.followup.send(_tr(
                self.lang,
                "An error occurred. Please try again later.",
                "حدث خطأ. يُرجى المحاولة لاحقًا.",
                "Une erreur s'est produite. Veuillez réessayer plus tard."
            ))


class TeamViewButtonView(discord.ui.View):
    """View containing team buttons"""
    def __init__(self, teams: list, user_id: int, user_authorid: int, cog, lang: int, timeout=180):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.user_authorid = user_authorid
        self.cog = cog
        self.lang = lang

        for team in teams:
            team_id = team['team_id']
            card_count = team['card_count']
            button = TeamViewButton(team_id, user_id, user_authorid, cog, lang)
            button.disabled = card_count == 0
            self.add_item(button)

class TeamCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        custom_font_path = os.path.join(os.path.dirname(__file__), 'SoloLevelDemo.otf')
        poppins_font_path = os.path.join(os.path.dirname(__file__), 'Poppins-Regular.ttf')  # Adjust path as needed
        dashhorizon_font_path = os.path.join(os.path.dirname(__file__), 'Dashhorizon-eZ5wg.otf')  # Adjust path as needed
        self.font_large = ImageFont.truetype(custom_font_path, 120)  # Adjust size as needed
        self.font_small = ImageFont.truetype(custom_font_path, 90)  # Adjust size as needed
        self.title_font = ImageFont.truetype(dashhorizon_font_path, 70)  # Poppins font with size 20
        self.poppins_font = ImageFont.truetype(poppins_font_path, 45)  # Poppins font with size 20
        self.gradient_start = (0, 0, 0)  # Light Sky Blue
        self.gradient_end = (0, 0, 0)          # Black

    async def execute_query_single(self, query, params=None):
        """Execute a query and return a single result"""
        result = await execute_query_with_retry(query, params)
        if result and len(result) > 0:
            return result[0]
        return None

    def gradient_color(self, percent):
        return tuple(int(start + (end - start) * percent) for start, end in zip(self.gradient_start, self.gradient_end))

    def draw_gradient_text(self, draw, text, position, font, width):
        for i, char in enumerate(text):
            percent = i / len(text)
            color = self.gradient_color(percent)
            bbox = font.getbbox(char)
            char_width = bbox[2] - bbox[0]
            draw.text((position[0], position[1]), char, font=font, fill=color)
            position = (position[0] + char_width, position[1])

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

    @commands.command(name='myteams', aliases=["mts", "teams"])
    @command_cooldown(rate=1, per=3.0)
    async def myteams(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        user_authorid = str(ctx.author.id)

        try:
            # Get user_id
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(query, (user_authorid,))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire au bot."
                ), ephemeral=True)
                return

            user_id = user_result[0]['user_id']

            # Get all teams for the user
            query = "SELECT team_id FROM team WHERE user_id = %s ORDER BY team_id ASC"
            teams_result = await execute_query_with_retry(query, (user_id,))

            if not teams_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have any teams yet.",
                    "ليس لديك أي فرق بعد.",
                    "Vous n'avez pas encore d'équipes."
                ), ephemeral=True)
                return

            # Get user info for thumbnail
            user_obj = await self.bot.fetch_user(int(user_authorid))
            username = getattr(user_obj, "display_name", str(user_obj))
            user_avatar = user_obj.display_avatar.url if user_obj.display_avatar else None

            # Create embed with user thumbnail
            embed = discord.Embed(
                title=_tr(lang, f"{username}'s Teams", f"فرق {username}", f"Équipes de {username}"),
                description=_tr(lang, "Click a button to view team details", "انقر على زر لعرض تفاصيل الفريق", "Cliquez sur un bouton pour voir les détails de l'équipe"),
                color=discord.Color.blurple()
            )

            if user_avatar:
                embed.set_thumbnail(url=user_avatar)

            # Build team list with power stats
            team_info_list = []
            for team in teams_result:
                team_id = team['team_id']

                # Get total power stat for this team
                query = """
                SELECT COALESCE(SUM(uc.power_stat), 0) as total_power
                FROM team_cards tc
                JOIN user_cards uc ON tc.card_id = uc.card_id
                WHERE tc.team_id = %s AND uc.user_id = %s
                """
                power_result = await execute_query_with_retry(query, (team_id, user_id))
                total_power = power_result[0]['total_power'] if power_result else 0

                # Get card count
                query = "SELECT COUNT(*) as card_count FROM team_cards WHERE team_id = %s"
                card_count_result = await execute_query_with_retry(query, (team_id,))
                card_count = card_count_result[0]['card_count'] if card_count_result else 0

                team_info_list.append({
                    'team_id': team_id,
                    'power': total_power,
                    'cards': card_count
                })

            # Add team info to embed
            team_list_text = ""
            for team_info in team_info_list:
                team_list_text += f"**Team #{team_info['team_id']}** - Power: {team_info['power']:,} | Cards: {team_info['cards']}/4\n"

            embed.add_field(
                name=_tr(lang, "Your Teams", "فريقك", "Vos équipes"),
                value=team_list_text if team_list_text else _tr(lang, "No teams found", "لم يتم العثور على فرق", "Aucune équipe trouvée"),
                inline=False
            )

            # Create buttons for each team
            class TeamView(discord.ui.View):
                def __init__(self, teams, user_id, user_authorid, cog):
                    super().__init__(timeout=300)
                    self.teams = teams
                    self.user_id = user_id
                    self.user_authorid = user_authorid
                    self.cog = cog

                    for team_info in teams:
                        team_id = team_info['team_id']
                        card_count = team_info['cards']
                        # Disable button if team has no cards
                        button = discord.ui.Button(
                            label=f"Team #{team_id}",
                            custom_id=f"team_{team_id}",
                            disabled=(card_count == 0)
                        )
                        button.callback = self.team_button_callback
                        self.add_item(button)

                async def team_button_callback(self, interaction: discord.Interaction):
                    # Only allow the user who triggered the command
                    if interaction.user.id != int(self.user_authorid):
                        await interaction.response.send_message(
                            _tr(lang, "You can only view your own teams.", "يمكنك فقط عرض فريقك الخاص.", "Vous ne pouvez voir que vos propres équipes."),
                            ephemeral=True
                        )
                        return

                    team_id = int(interaction.data['custom_id'].split('_')[1])
                    await self.cog.show_team_view(interaction, team_id, self.user_id, lang)

            view = TeamView(team_info_list, user_id, user_authorid, self)
            await ctx.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            error_msg = f"An error occurred: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await ctx.send(_tr(
                lang,
                "An error occurred. Please try again later.",
                "حدث خطأ. يرجى المحاولة لاحقاً.",
                "Une erreur s'est produite. Veuillez réessayer plus tard."
            ), ephemeral=True)

    @commands.command(name='cteam', aliases=["ct", "createteam"])
    @command_cooldown(rate=1, per=5.0)
    async def cteam(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        user_authorid = str(ctx.author.id)

        try:
            # Get user_id, team_count, and team_cap
            query = "SELECT user_id, team_count, team_cap FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(query, (user_authorid,))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire au bot."
                ), ephemeral=True)
                return

            user_id = user_result[0]['user_id']
            team_count = user_result[0]['team_count']
            team_cap = user_result[0]['team_cap']

            # Check if user has reached team cap
            if team_count >= team_cap:
                await ctx.send(_tr(
                    lang,
                    f"You have reached your team limit ({team_cap} teams). You cannot create more teams.",
                    f"لقد وصلت إلى حد الفريق ({team_cap} فرق). لا يمكنك إنشاء المزيد من الفرق.",
                    f"Vous avez atteint votre limite d'équipes ({team_cap} équipes). Vous ne pouvez pas créer plus d'équipes."
                ), ephemeral=True)
                return

            # Insert new team
            query = "INSERT INTO team (user_id,team_bg) VALUES (%s,%s)"
            await execute_query_with_retry(query, (user_id,"curr.png"))

            # Increment team_count
            query = "UPDATE user SET team_count = team_count + 1 WHERE user_id = %s"
            await execute_query_with_retry(query, (user_id,))

            # Get the new team_id
            query = "SELECT team_id FROM team WHERE user_id = %s ORDER BY team_id DESC LIMIT 1"
            new_team_result = await execute_query_with_retry(query, (user_id,))
            new_team_id = new_team_result[0]['team_id'] if new_team_result else None

            # Create success embed
            embed = discord.Embed(
                title=_tr(lang, "Team Created", "تم إنشاء الفريق", "Équipe créée"),
                description=_tr(lang, f"Your new team has been created successfully!", f"تم إنشاء فريقك الجديد بنجاح!", f"Votre nouvelle équipe a été créée avec succès !"),
                color=discord.Color.green()
            )
            embed.add_field(
                name=_tr(lang, "Team ID", "معرف الفريق", "ID de l'équipe"),
                value=f"**#{new_team_id}**",
                inline=True
            )
            embed.add_field(
                name=_tr(lang, "Teams Created", "الفرق المنشأة", "Équipes créées"),
                value=f"**{team_count + 1}/{team_cap}**",
                inline=True
            )

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            error_msg = f"An error occurred: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await ctx.send(_tr(
                lang,
                "An error occurred. Please try again later.",
                "حدث خطأ. يرجى المحاولة لاحقاً.",
                "Une erreur s'est produite. Veuillez réessayer plus tard."
            ), ephemeral=True)

    @commands.command(name='steam', aliases=["sts", "setteam"])
    @command_cooldown(rate=1, per=3.0)
    async def steam(self, ctx, team_id: int):
        lang = await get_user_multilanguage(ctx.author.id)
        user_authorid = str(ctx.author.id)

        try:
            # Get user_id
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(query, (user_authorid,))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire au bot."
                ), ephemeral=True)
                return

            user_id = user_result[0]['user_id']

            # Check if user owns this team
            query = "SELECT team_id FROM team WHERE user_id = %s AND team_id = %s"
            team_result = await execute_query_with_retry(query, (user_id, team_id))

            if not team_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't own team #{team_id}.",
                    f"أنت لا تملك الفريق #{team_id}.",
                    f"Vous ne possédez pas l'équipe #{team_id}."
                ), ephemeral=True)
                return

            # Update current_team
            query = "UPDATE user SET current_team = %s WHERE user_id = %s"
            await execute_query_with_retry(query, (team_id, user_id))

            # Create success embed
            embed = discord.Embed(
                title=_tr(lang, "Current Team Updated", "تم تحديث الفريق الحالي", "Équipe actuelle mise à jour"),
                description=_tr(lang, f"Your current team is now Team #{team_id}", f"فريقك الحالي هو الآن الفريق #{team_id}", f"Votre équipe actuelle est maintenant l'équipe #{team_id}"),
                color=discord.Color.green()
            )

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            error_msg = f"An error occurred: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await ctx.send(_tr(
                lang,
                "An error occurred. Please try again later.",
                "حدث خطأ. يرجى المحاولة لاحقاً.",
                "Une erreur s'est produite. Veuillez réessayer plus tard."
            ), ephemeral=True)

    @commands.command(name='team', aliases=["tv", "tvw"])
    @command_cooldown(rate=1, per=3.0)
    async def team(self, ctx, team_id: int = None):
        start_time = time.time()
        lang = await get_user_multilanguage(ctx.author.id)
        user_authorid = str(ctx.author.id)

        try:
            # Get user_id and current_team
            query = "SELECT user_id, current_team FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(query, (user_authorid,))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire au bot."
                ))
                return

            user_id = user_result[0]['user_id']
            current_team = user_result[0]['current_team']

            # If no team_id specified, use current_team
            if team_id is None:
                team_id = current_team
                if team_id is None:
                    await ctx.send(_tr(
                        lang,
                        "You don't have a current team set. Use `sl steam <team_id>` to set one.",
                        "ليس لديك فريق حالي محدد. استخدم `sl steam <team_id>` لتعيين واحد.",
                        "Vous n'avez pas d'équipe actuelle définie. Utilisez `sl steam <team_id>` pour en définir une."
                    ))
                    return

            # If team_id is specified, verify user owns it
            if team_id is not None:
                query = "SELECT team_id FROM team WHERE user_id = %s AND team_id = %s"
                team_check = await execute_query_with_retry(query, (user_id, team_id))
                if not team_check:
                    await ctx.send(_tr(
                        lang,
                        f"You don't own team #{team_id}.",
                        f"أنت لا تملك الفريق #{team_id}.",
                        f"Vous ne possédez pas l'équipe #{team_id}."
                    ))
                    return

            # Get team data
            query = """
            SELECT u.user_id, uc.pseudo_image, t.team_id, t.team_bg, c.card_id, c.card_name, uc.image, uc.current_level, c.card_rank, c.type_card
            FROM user u
            JOIN team t ON u.user_id = t.user_id
            LEFT JOIN team_cards tc ON t.team_id = tc.team_id
            LEFT JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = u.user_id
            LEFT JOIN card c ON uc.card_id = c.card_id
            WHERE u.user_id = %s AND t.team_id = %s
            ORDER BY tc.position
            """
            result = await execute_query_with_retry(query, (user_id, team_id))

            if not result or 'user_id' not in result[0]:
                await ctx.send(_tr(
                    lang,
                    "Error: Team not found.",
                    "خطأ: لم يتم العثور على الفريق.",
                    "Erreur : équipe non trouvée."
                ))
                return

            team_bg = result[0]['team_bg']
            team_cards = [r for r in result if r['card_id'] is not None]

            if not team_cards:
                await ctx.send(_tr(
                    lang,
                    "Your team is empty.",
                    "فريقك فارغ.",
                    "Votre équipe est vide."
                ))
                return

            background_path = os.path.join(self.image_path, team_bg)
            if not os.path.exists(background_path):
                await ctx.send(_tr(
                    lang,
                    f"Error: Background image not found.",
                    f"خطأ: لم يتم العثور على صورة الخلفية.",
                    f"Erreur : image d'arrière-plan introuvable."
                ))
                return

            with Image.open(background_path).convert('RGBA').resize((1600, 900)) as background:
                draw = ImageDraw.Draw(background)

                image_positions = [(0, 150), (399, 150), (800, 150), (1205, 150)]
                card_size = (380, 580)
                text_positions = [(50, 750), (450, 750), (850, 750), (1250, 750)]

                for i, card in enumerate(team_cards[:4]):
                    card_image = self.prepare_card_image(card, card_size)
                    if card_image:
                        background.paste(card_image, image_positions[i], card_image)

                    name_text = card['card_name']
                    name_bbox = self.font_small.getbbox(name_text)
                    name_width = name_bbox[2] - name_bbox[0]
                    name_position = (text_positions[i][0] + (300 - name_width) // 2, text_positions[i][1])
                    self.draw_gradient_text(draw, name_text, name_position, self.font_small, name_width)

                    if card['type_card'] != 'Support':
                        level_text = f"Lv.{card['current_level']}"
                        level_bbox = self.poppins_font.getbbox(level_text)
                        level_width = level_bbox[2] - level_bbox[0]
                        level_position = (text_positions[i][0] + (300 - level_width) // 2, text_positions[i][1] + 90)
                        self.draw_gradient_text(draw, level_text, level_position, self.poppins_font, level_width)

                user_obj = await self.bot.fetch_user(int(user_authorid))
                username = getattr(user_obj, "display_name", str(user_obj))
                title_text = _team_title(lang, username)
                title_bbox = self.title_font.getbbox(title_text)
                title_width = title_bbox[2] - title_bbox[0]
                title_position = ((1600 - title_width) // 2, 20)
                self.draw_gradient_text(draw, title_text, title_position, self.title_font, title_width)

                buffer = io.BytesIO()
                background.convert('RGB').save(buffer, format='JPEG', quality=85, optimize=True)
                buffer.seek(0)

            file_size = buffer.getbuffer().nbytes
            print(f"Generated image file size: {file_size} bytes")

            if file_size == 0:
                await ctx.send(_tr(
                    lang,
                    "Error: Generated image is empty.",
                    "خطأ: الصورة المُنشأة فارغة.",
                    "Erreur : l'image générée est vide."
                ))
                return

            message_content = f"{ctx.author.mention} " + _tr(lang, "Here's your team:", "إليك فريقك:", "Voici votre équipe :")
            await ctx.send(content=message_content, file=discord.File(buffer, filename="team_view.jpg"))
            total_time = time.time() - start_time
            print(f"Total time: {total_time:.2f}s")

        except Exception as e:
            error_msg = f"An error occurred while generating the team image: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await ctx.send(_tr(
                lang,
                "An error occurred. Please try again later or contact support.",
                "حدث خطأ. يُرجى المحاولة لاحقًا أو الاتصال بالدعم.",
                "Une erreur s'est produite. Veuillez réessayer plus tard ou contacter le support."
            ))

    async def show_team_view(self, interaction: discord.Interaction, team_id: int, user_id: int, lang: int):
        """Display team view for a specific team_id"""
        try:
            user_authorid = str(interaction.user.id)

            query = """
            SELECT u.user_id, uc.pseudo_image, t.team_id, t.team_bg, c.card_id, c.card_name, uc.image, uc.current_level, c.card_rank, c.type_card
            FROM user u
            JOIN team t ON u.user_id = t.user_id
            LEFT JOIN team_cards tc ON t.team_id = tc.team_id
            LEFT JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = u.user_id
            LEFT JOIN card c ON uc.card_id = c.card_id
            WHERE u.user_id = %s AND t.team_id = %s
            ORDER BY tc.position
            """
            result = await execute_query_with_retry(query, (user_id, team_id))

            if not result or 'user_id' not in result[0]:
                await interaction.response.send_message(_tr(
                    lang,
                    "Error: Team not found.",
                    "خطأ: لم يتم العثور على الفريق.",
                    "Erreur : équipe non trouvée."
                ), ephemeral=True)
                return

            team_bg = result[0]['team_bg']
            team_cards = [r for r in result if r['card_id'] is not None]

            if not team_cards:
                await interaction.response.send_message(_tr(
                    lang,
                    "This team is empty.",
                    "هذا الفريق فارغ.",
                    "Cette équipe est vide."
                ), ephemeral=True)
                return

            background_path = os.path.join(self.image_path, team_bg)
            if not os.path.exists(background_path):
                await interaction.response.send_message(_tr(
                    lang,
                    "Error: Background image not found.",
                    "خطأ: لم يتم العثور على صورة الخلفية.",
                    "Erreur : image d'arrière-plan introuvable."
                ), ephemeral=True)
                return

            with Image.open(background_path).convert('RGBA').resize((1600, 900)) as background:
                draw = ImageDraw.Draw(background)

                image_positions = [(0, 150), (399, 150), (800, 150), (1205, 150)]
                card_size = (380, 580)
                text_positions = [(50, 750), (450, 750), (850, 750), (1250, 750)]

                for i, card in enumerate(team_cards[:4]):
                    card_image = self.prepare_card_image(card, card_size)
                    if card_image:
                        background.paste(card_image, image_positions[i], card_image)

                    name_text = card['card_name']
                    name_bbox = self.font_small.getbbox(name_text)
                    name_width = name_bbox[2] - name_bbox[0]
                    name_position = (text_positions[i][0] + (300 - name_width) // 2, text_positions[i][1])
                    self.draw_gradient_text(draw, name_text, name_position, self.font_small, name_width)

                    if card['type_card'] != 'Support':
                        level_text = f"Lv.{card['current_level']}"
                        level_bbox = self.poppins_font.getbbox(level_text)
                        level_width = level_bbox[2] - level_bbox[0]
                        level_position = (text_positions[i][0] + (300 - level_width) // 2, text_positions[i][1] + 90)
                        self.draw_gradient_text(draw, level_text, level_position, self.poppins_font, level_width)

                user_obj = await self.bot.fetch_user(int(user_authorid))
                username = getattr(user_obj, "display_name", str(user_obj))
                title_text = _team_title(lang, username)
                title_bbox = self.title_font.getbbox(title_text)
                title_width = title_bbox[2] - title_bbox[0]
                title_position = ((1600 - title_width) // 2, 20)
                self.draw_gradient_text(draw, title_text, title_position, self.title_font, title_width)

                buffer = io.BytesIO()
                background.convert('RGB').save(buffer, format='JPEG', quality=85, optimize=True)
                buffer.seek(0)

            await interaction.response.send_message(
                content=_tr(lang, "Here's your team:", "إليك فريقك:", "Voici votre équipe :"),
                file=discord.File(buffer, filename="team_view.jpg"),
                ephemeral=True
            )

        except Exception as e:
            error_msg = f"An error occurred: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await interaction.response.send_message(_tr(
                lang,
                "An error occurred. Please try again later.",
                "حدث خطأ. يرجى المحاولة لاحقاً.",
                "Une erreur s'est produite. Veuillez réessayer plus tard."
            ), ephemeral=True)

    def prepare_card_image(self, card, size):
        img = card['image']
        if card.get('pseudo_image') and card['pseudo_image'] != "":
            img = card['pseudo_image']
        card_image_path = os.path.join(self.image_path, img)
        if not os.path.exists(card_image_path):
            print(f"Warning: Card image not found at {card_image_path}")
            return None

        with Image.open(card_image_path) as img:
            img = img.convert('RGBA').resize(size, Image.LANCZOS)

        rank_color = self.get_rank_color(card['card_rank'])
        return self.add_border_and_radius(img, rank_color)

    def add_border_and_radius(self, image, border_color):
        border_width = 5
        new_size = (image.width + 2 * border_width, image.height + 2 * border_width)
        bordered_image = Image.new('RGBA', new_size, (255, 255, 255, 0))

        mask = Image.new('L', new_size, 0)
        draw = ImageDraw.Draw(mask)
        radius = 70
        draw.rounded_rectangle([0, 0, new_size[0], new_size[1]], radius, fill=255)

        border_draw = ImageDraw.Draw(bordered_image)
        border_draw.rounded_rectangle([0, 0, new_size[0] - 1, new_size[1] - 1], radius,
                                      outline=border_color, fill=(0, 0, 0, 0), width=border_width)

        bordered_image.paste(image, (border_width, border_width), image.split()[3])

        result = Image.new('RGBA', new_size, (0, 0, 0, 0))
        result.paste(bordered_image, (0, 0), mask)

        return result
    def draw_gradient_text(self, draw, text, position, font, width):
        for i, char in enumerate(text):
            percent = i / len(text)
            color = self.gradient_color(percent)
            bbox = font.getbbox(char)
            char_width = bbox[2] - bbox[0]
            draw.text((position[0], position[1]), char, font=font, fill=color)
            position = (position[0] + char_width, position[1])

    def gradient_color(self, percent):
        return tuple(int(start + (end - start) * percent) for start, end in zip(self.gradient_start, self.gradient_end))

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
        # <CHANGE> Fetch equipment stats considering Halloween variants for weapon, armor, and shield
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

            # Get user_cards data to fetch Halloween IDs
            user_cards_query = """
            SELECT weapon_id, armor_id, shield_id, 
                id_weaponhalloween, id_armorhalloween, id_shieldhalloween
            FROM user_cards
            WHERE user_id = %s AND card_id = %s
            """
            user_card_data = await execute_query_with_retry(user_cards_query, (user_id, support['card_id']))
            
            if not user_card_data:
                continue
                
            user_card_data = user_card_data[0]

            # Define equipment types with their corresponding IDs and Halloween IDs
            equipment_list = [
                {
                    'eq_id': user_card_data['weapon_id'],
                    'halloween_id': user_card_data.get('id_weaponhalloween', 0) or 0,
                    'rank': 1
                },
                {
                    'eq_id': user_card_data['armor_id'],
                    'halloween_id': user_card_data.get('id_armorhalloween', 0) or 0,
                    'rank': 3
                },
                {
                    'eq_id': user_card_data['shield_id'],
                    'halloween_id': user_card_data.get('id_shieldhalloween', 0) or 0,
                    'rank': 2
                }
            ]

            for equipment in equipment_list:
                eq_id = equipment['eq_id']
                halloween_id = equipment['halloween_id']
                
                if not eq_id:
                    continue

                # Check if equipment is Halloween
                queryd = "SELECT halloween FROM weapon WHERE weapon_id = %s"
                eq_hallo = await execute_query_with_retry(queryd, (eq_id,))
                
                if not eq_hallo:
                    continue
                    
                eq_hallo = eq_hallo[0]
                eq_details = None

                if eq_hallo["halloween"] == 0:
                    # Regular weapon - fetch from weapon table
                    query = """
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage, 
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details = await execute_query_with_retry(query, (eq_id,))
                else:
                    # Halloween weapon - fetch from weapon_halloween using the Halloween ID
                    if halloween_id > 0:
                        query = """
                        SELECT attack_boost, health_boost, speed_boost, defense_boost,
                            attack_percentage, health_percentage, 
                            speed_percentage, defense_percentage
                        FROM weapon_halloween
                        WHERE id_weaponhalloween = %s
                        """
                        eq_details = await execute_query_with_retry(query, (halloween_id,))
                
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

    async def calculate_support_card_boosts(self, user_id, card_id):
        """Calculate the total boosts from a single support card and its equipment"""
        # Get support card details
        query = """
        SELECT c.card_id, c.attack, c.health, c.speed, c.defence, 
            c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
            uc.weapon_id, uc.armor_id, uc.shield_id
        FROM card c
        JOIN user_cards uc ON c.card_id = uc.card_id
        WHERE c.card_id = %s AND uc.user_id = %s
        """
        support_card = await execute_query_with_retry(query, (card_id, user_id))
        
        if not support_card:
            return None
            
        support_card = support_card[0]

        # Initialize boosts
        flat_attack = support_card['attack'] or 0
        flat_health = support_card['health'] or 0
        flat_speed = support_card['speed'] or 0
        flat_defence = support_card['defence'] or 0

        pct_attack = support_card['attack_percentage'] or 0
        pct_health = support_card['health_percentage'] or 0
        pct_speed = support_card['speed_percentage'] or 0
        pct_defence = support_card['defence_percentage'] or 0

        # Get user_cards data to fetch Halloween IDs
        user_cards_query = """
        SELECT weapon_id, armor_id, shield_id, 
            id_weaponhalloween, id_armorhalloween, id_shieldhalloween
        FROM user_cards
        WHERE user_id = %s AND card_id = %s
        """
        user_card_data = await execute_query_with_retry(user_cards_query, (user_id, card_id))

        if not user_card_data:
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

        user_card_data = user_card_data[0]

        # Define equipment types with their corresponding IDs and Halloween IDs
        equipment_list = [
            {
                'eq_id': user_card_data['weapon_id'],
                'halloween_id': user_card_data.get('id_weaponhalloween', 0) or 0,
                'rank': 1
            },
            {
                'eq_id': user_card_data['armor_id'],
                'halloween_id': user_card_data.get('id_armorhalloween', 0) or 0,
                'rank': 3
            },
            {
                'eq_id': user_card_data['shield_id'],
                'halloween_id': user_card_data.get('id_shieldhalloween', 0) or 0,
                'rank': 2
            }
        ]

        for equipment in equipment_list:
            eq_id = equipment['eq_id']
            halloween_id = equipment['halloween_id']
            
            if not eq_id:
                continue

            # Check if equipment is Halloween
            queryd = "SELECT halloween FROM weapon WHERE weapon_id = %s"
            eq_hallo = await execute_query_with_retry(queryd, (eq_id,))
            
            if not eq_hallo:
                continue
                
            eq_hallo = eq_hallo[0]
            eq_details = None

            if eq_hallo["halloween"] == 0:
                # Regular weapon - fetch from weapon table
                query = """
                SELECT attack_boost, health_boost, speed_boost, defense_boost,
                    attack_percentage, health_percentage, 
                    speed_percentage, defense_percentage
                FROM weapon
                WHERE weapon_id = %s
                """
                eq_details = await execute_query_with_retry(query, (eq_id,))
            else:
                # Halloween weapon - fetch from weapon_halloween using the Halloween ID
                if halloween_id > 0:
                    query = """
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage, 
                        speed_percentage, defense_percentage
                    FROM weapon_halloween
                    WHERE id_weaponhalloween = %s
                    """
                    eq_details = await execute_query_with_retry(query, (halloween_id,))
            
            if eq_details:
                eq_details = eq_details[0]
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

    @commands.command(name='add', aliases=["ad", "tad"])
    async def add(self, ctx, *, character_name):
        lang = await get_user_multilanguage(ctx.author.id)

        user_authorid = str(ctx.author.id)

        # Get user_id
        query = "SELECT user_id,squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(query, (user_authorid,))

        if not user_result:
            await ctx.send(_tr(
                lang,
                "Error: You need to register to the bot first.",
                "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                "Erreur : vous devez d'abord vous inscrire au bot."
            ))
            return

        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']
        # Get team_id
        query = "SELECT current_team as team_id FROM user WHERE user_id = %s"
        team_result = await execute_query_with_retry(query, (user_id,))

        if not team_result:
            await ctx.send(_tr(
                lang,
                "You don't have a team yet.",
                "ليس لديك فريق بعد.",
                "Vous n'avez pas encore d'équipe."
            ))
            return

        team_id = team_result[0]['team_id']

        # Look up the card in the card table
        query = "SELECT * FROM card WHERE card_name LIKE %s"
        card_result = await execute_query_with_retry(query, (f"%{character_name}%",))

        if not card_result:
            await ctx.send(_tr(
                lang,
                "This card doesn't exist.",
                "هذه البطاقة غير موجودة.",
                "Cette carte n'existe pas."
            ))
            return
        if len(card_result) > 1:
            # Create an embed to show multiple matches
            embed = discord.Embed(
                title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                description=_tr(
                    lang,
                    "I found multiple cards matching your search. Please specify which one you'd like to view:",
                    "وجدت عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد عرضها:",
                    "Plusieurs cartes correspondent à votre recherche. Veuillez préciser laquelle vous souhaitez voir :"
                ),
                color=discord.Color.blue()
            )

            # List all matching cards
            card_list = "\n".join([f"• {card['card_name']}" for card in card_result])
            embed.add_field(
                name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                value=card_list,
                inline=False
            )
            embed.set_footer(text=_tr(
                lang,
                "Please use the command again with the exact name of the card you want to view.",
                "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للبطاقة التي تريد عرضها.",
                "Veuillez réutiliser la commande avec le nom exact de la carte à afficher."
            ))
            await ctx.send(embed=embed)
            return
        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']

        if card_type == "Booster":
            await ctx.send(_tr(
                lang,
                f"'{character_name}' is a Booster card and cannot be added to your team.",
                f"‏'{character_name}' هي بطاقة معزز ولا يمكن إضافتها إلى فريقك.",
                f"« {character_name} » est une carte Booster et ne peut pas être ajoutée à votre équipe."
            ))
            return

        stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
        stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card_id))

        if stat_status_result:
            await ctx.send(_tr(
                lang,
                "This card is currently being adjusted. Please wait until the process is completed.",
                "هذه البطاقة قيد التعديل حالياً. يرجى الانتظار حتى اكتمال العملية.",
                "Cette carte est en cours d'ajustement. Veuillez patienter jusqu'à la fin du processus."
            ))
            return
        # Check if the user owns the card
        query = "SELECT * FROM user_cards WHERE user_id = %s and card_id = %s"
        user_card_result = await execute_query_with_retry(query, (user_id, card_id))

        if not user_card_result:
            await ctx.send(_tr(
                lang,
                f"You don't own the card '{character_name}'.",
                f"أنت لا تملك البطاقة '{character_name}'.",
                f"Vous ne possédez pas la carte « {character_name} »."
            ))
            return

        # Check if the team already has 4 cards
        query = "SELECT COUNT(*) as card_count FROM team_cards WHERE team_id = %s"
        count_result = await execute_query_with_retry(query, (team_id,))

        if count_result[0]['card_count'] >= 4:
            await ctx.send(_tr(
                lang,
                "Your team already has the maximum of 4 cards.",
                "فريقك يحتوي بالفعل على الحد الأقصى وهو 4 بطاقات.",
                "Votre équipe a déjà le maximum de 4 cartes."
            ))
            return

        # Check if the card is in the team
        # 1) Is the card already in THIS team?
        in_this_team_q = "SELECT 1 FROM team_cards WHERE team_id = %s AND card_id = %s LIMIT 1"
        if await execute_query_with_retry(in_this_team_q, (team_id, card_id)):
            await ctx.send(_tr(
                lang,
                f"'{character_name}' is already in your team.",
                f"‏'{character_name}' موجودة بالفعل في فريقك.",
                f"« {character_name} » est déjà dans votre équipe."
            ))
            return

        # 2) Is the card in ANY OTHER team of THIS user?
        #    Prefer 'team' table; fall back to 'teams'. If neither exists, we can only check the current team (done above).
        other_team_row = None
        for team_table in ("team", "teams"):
            try:
                q = f"""
                    SELECT t.team_id
                    FROM team_cards tc
                    JOIN {team_table} t ON t.team_id = tc.team_id
                    WHERE t.user_id = %s AND tc.card_id = %s
                    LIMIT 1
                """
                rows = await execute_query_with_retry(q, (user_id, card_id))
                if rows:
                    other_team_row = rows[0]
                break
            except Exception:
                continue

        if other_team_row:
            if other_team_row['team_id'] != team_id:
                await ctx.send(_tr(
                    lang,
                    f"'{character_name}' is already assigned to your other team (ID: {other_team_row['team_id']}). Remove it there first.",
                    f"‏'{character_name}' مُضافة بالفعل إلى فريقك الآخر (المعرف: {other_team_row['team_id']}). يرجى إزالتها أولاً.",
                    f"« {character_name} » est déjà affectée à votre autre équipe (ID : {other_team_row['team_id']}). Retirez-la d'abord."
                ))
                return
            else:
                # (Should have been caught by 'in_this_team' above, but keep defensive)
                await ctx.send(_tr(
                    lang,
                    f"'{character_name}' is already in your team.",
                    f"‏'{character_name}' موجودة بالفعل في فريقك.",
                    f"« {character_name} » est déjà dans votre équipe."
                ))
                return
        query = "SELECT * FROM squad_cards WHERE squad_id = %s AND card_id = %s AND user_id = %s"
        squad_card_result = await execute_query_with_retry(query, (squad_id, card_id, user_id))

        if squad_card_result:
            await ctx.send(_tr(
                lang,
                f"'{character_name}' is in your squad team, type sl rms {character_name} to remove the card.",
                f"‏'{character_name}' موجودة في فريق السرب الخاص بك، اكتب `sl rms {character_name}` لإزالة البطاقة.",
                f"« {character_name} » est dans votre escouade. Tapez `sl rms {character_name}` pour retirer la carte."
            ))
            return
        # Get the maximum position for the current team
        query = "SELECT MAX(position) as max_position FROM team_cards WHERE team_id = %s"
        max_position_result = await execute_query_with_retry(query, (team_id,))
        new_position = (max_position_result[0]['max_position'] or 0) + 1

        # Add the card to the team with the correct position
        query = "INSERT INTO team_cards (team_id, card_id, position) VALUES (%s, %s, %s)"
        await execute_query_with_retry(query, (team_id, card_id, new_position))

        # When adding a Fighter card
        if card_type == "Fighter":
            # Reset the fighter card to base stats
            query = """
            UPDATE user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            SET 
                uc.attack_stat = uc.base_attack,
                uc.health_stat = uc.base_health,
                uc.speed_stat = uc.base_speed,
                uc.defence_stat = uc.base_defence
            WHERE 
                uc.user_id = %s AND 
                uc.card_id = %s
            """
            await execute_query_with_retry(query, (user_id, card_id))

            # Calculate total boosts from all support cards
            total_boosts = await self.calculate_total_support_boosts(team_id, user_id)

            # Apply these boosts to the new fighter card
            await self.apply_support_boosts_to_single_offense(user_id, card_id, total_boosts)
            query = """
            UPDATE user_cards
            SET power_stat = attack_stat + health_stat + speed_stat + defence_stat
            WHERE user_id = %s AND card_id = %s
            """
            await execute_query_with_retry(query, (user_id, card_id))
        # When adding a Support card
        elif card_type == "Support":
            # Get all fighter cards in the team
            query = """
            SELECT uc.card_id
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Fighter'
            """
            fighter_cards = await execute_query_with_retry(query, (team_id, user_id))

            if fighter_cards:
                # Reset all fighter cards to base stats
                await self.reset_offense_cards_to_base(team_id, user_id)

                # Calculate total boosts from all support cards (including the new one)
                total_boosts = await self.calculate_total_support_boosts(team_id, user_id)

                # Apply these boosts to all fighter cards
                await self.apply_support_boosts_to_offense(team_id, user_id, total_boosts)
                query = """
                UPDATE user_cards uc
                JOIN team_cards tc ON uc.card_id = tc.card_id
                JOIN card c ON uc.card_id = c.card_id
                SET uc.power_stat = uc.attack_stat + uc.health_stat + uc.speed_stat + uc.defence_stat
                WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Fighter'
                """
                await execute_query_with_retry(query, (team_id, user_id))
        await ctx.send(_tr(
            lang,
            f"Successfully added {card_result[0]['card_icon']} {card_result[0]['card_name']} to your team at position {new_position}!",
            f"تمت إضافة {card_result[0]['card_icon']} {card_result[0]['card_name']} إلى فريقك في الموضع {new_position} بنجاح!",
            f"Ajout réussi de {card_result[0]['card_icon']} {card_result[0]['card_name']} à votre équipe en position {new_position} !"
        ))

    @commands.command(name='remove', aliases=["rm", "trm"])
    async def remove(self, ctx, *, character_name):
        lang = await get_user_multilanguage(ctx.author.id)

        user_authorid = str(ctx.author.id)

        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(query, (user_authorid,))

        if not user_result:
            await ctx.send(_tr(
                lang,
                "Error: You need to register to the bot first.",
                "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                "Erreur : vous devez d'abord vous inscrire au bot."
            ))
            return

        user_id = user_result[0]['user_id']

        # Get team_id
        query = "SELECT current_team as team_id FROM user WHERE user_id = %s"
        team_result = await execute_query_with_retry(query, (user_id,))

        if not team_result:
            await ctx.send(_tr(
                lang,
                "You don't have a team yet.",
                "ليس لديك فريق بعد.",
                "Vous n'avez pas encore d'équipe."
            ))
            return

        team_id = team_result[0]['team_id']

        # Look up the card in the card table
        query = "SELECT * FROM card WHERE card_name LIKE %s"
        card_result = await execute_query_with_retry(query, (f"%{character_name}%",))

        if not card_result:
            await ctx.send(_tr(
                lang,
                "This card doesn't exist.",
                "هذه البطاقة غير موجودة.",
                "Cette carte n'existe pas."
            ))
            return
        if len(card_result) > 1:
            # Create an embed to show multiple matches
            embed = discord.Embed(
                title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                description=_tr(
                    lang,
                    "I found multiple cards matching your search. Please specify which one you'd like to view:",
                    "وجدت عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد عرضها:",
                    "Plusieurs cartes correspondent à votre recherche. Veuillez préciser laquelle vous souhaitez voir :"
                ),
                color=discord.Color.blue()
            )

            # List all matching cards
            card_list = "\n".join([f"• {card['card_name']}" for card in card_result])
            embed.add_field(
                name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                value=card_list,
                inline=False
            )
            embed.set_footer(text=_tr(
                lang,
                "Please use the command again with the exact name of the card you want to view.",
                "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للبطاقة التي تريد عرضها.",
                "Veuillez réutiliser la commande avec le nom exact de la carte à afficher."
            ))
            await ctx.send(embed=embed)
            return

        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']
        stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
        stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card_id))

        if stat_status_result:
            await ctx.send(_tr(
                lang,
                "This card is currently being adjusted. Please wait until the process is completed.",
                "هذه البطاقة قيد التعديل حالياً. يرجى الانتظار حتى اكتمال العملية.",
                "Cette carte est en cours d'ajustement. Veuillez patienter jusqu'à la fin du processus."
            ))
            return
        # Check if the card is in the team
        query = "SELECT position FROM team_cards WHERE team_id = %s AND card_id = %s"
        team_card_result = await execute_query_with_retry(query, (team_id, card_id))

        if not team_card_result:
            await ctx.send(_tr(
                lang,
                f"'{character_name}' is not in your team.",
                f"‏'{character_name}' ليست ضمن فريقك.",
                f"« {character_name} » n'est pas dans votre équipe."
            ))
            return

        removed_position = team_card_result[0]['position']

        # Remove the card from the team
        query = "DELETE FROM team_cards WHERE team_id = %s AND card_id = %s"
        await execute_query_with_retry(query, (team_id, card_id))

        # Rearrange positions
        query = """
        UPDATE team_cards
        SET position = position - 1
        WHERE team_id = %s AND position > %s
        """
        await execute_query_with_retry(query, (team_id, removed_position))

        # When removing a Support card
        if card_type == "Support":
            # Reset all fighter cards to base stats
            await self.reset_offense_cards_to_base(team_id, user_id)

            # Calculate total boosts from remaining support cards (excluding the removed one)
            total_boosts = await self.calculate_total_support_boosts(team_id, user_id, card_id)

            # Apply these boosts to all fighter cards
            await self.apply_support_boosts_to_offense(team_id, user_id, total_boosts)
            query = """
            UPDATE user_cards uc
            JOIN team_cards tc ON uc.card_id = tc.card_id
            JOIN card c ON uc.card_id = c.card_id
            SET uc.power_stat = uc.attack_stat + uc.health_stat + uc.speed_stat + uc.defence_stat
            WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Fighter'
            """
            await execute_query_with_retry(query, (team_id, user_id))
        # When removing a Fighter card
        elif card_type == "Fighter":
            # Reset the fighter card to base stats
            query = """
            UPDATE user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            SET 
                uc.attack_stat = uc.base_attack,
                uc.health_stat = uc.base_health,
                uc.speed_stat = uc.base_speed,
                uc.defence_stat = uc.base_defence
            WHERE 
                uc.user_id = %s AND 
                uc.card_id = %s
            """
            await execute_query_with_retry(query, (user_id, card_id))
            query = """
            UPDATE user_cards
            SET power_stat = attack_stat + health_stat + speed_stat + defence_stat
            WHERE user_id = %s AND card_id = %s
            """
            await execute_query_with_retry(query, (user_id, card_id))
        await ctx.send(_tr(
            lang,
            f"Successfully removed {card_result[0]['card_icon']} {card_result[0]['card_name']} from your team and rearranged positions!",
            f"تمت إزالة {card_result[0]['card_icon']} {card_result[0]['card_name']} من فريقك وإعادة ترتيب المواقع بنجاح!",
            f"Suppression réussie de {card_result[0]['card_icon']} {card_result[0]['card_name']} de votre équipe et réorganisation des positions !"
        ))

    @commands.command(name='swap', aliases=["sw", "tsw"])
    async def swap(self, ctx, *, card_names):
        lang = await get_user_multilanguage(ctx.author.id)

        # Split the card names
        try:
            first_card, second_card = card_names.rsplit(',', 1)
            first_card = first_card.strip()
            second_card = second_card.strip()
        except ValueError:
            await ctx.send(_tr(
                lang,
                "Please provide two card names separated by a comma.",
                "يرجى توفير اسمي بطاقتين مفصولين بفاصلة.",
                "Veuillez fournir deux noms de cartes séparés par une virgule."
            ))
            return

        user_authorid = str(ctx.author.id)

        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(query, (user_authorid,))

        if not user_result:
            await ctx.send(_tr(
                lang,
                "Error: You need to register to the bot first.",
                "خطأ: تحتاج إلى التسجيل في البوت أولاً.",
                "Erreur : vous devez d'abord vous inscrire au bot."
            ))
            return

        user_id = user_result[0]['user_id']

        # Get team_id
        query = "SELECT current_team as team_id FROM user WHERE user_id = %s"
        team_result = await execute_query_with_retry(query, (user_id,))

        if not team_result:
            await ctx.send(_tr(
                lang,
                "You don't have a team yet.",
                "ليس لديك فريق بعد.",
                "Vous n'avez pas encore d'équipe."
            ))
            return

        team_id = team_result[0]['team_id']

        # Separate checks for each card to provide more specific multiple match information
        multiple_match_cards = []
        card_ids = []

        for card_name in [first_card, second_card]:
            # Modify the card search query to use LIKE for more flexible matching
            query = "SELECT card_id, name FROM user_cards WHERE name LIKE %s AND user_id = %s"
            card_result = await execute_query_with_retry(query, (f'%{card_name}%', user_id))

            if len(card_result) > 1:
                # If multiple cards match, add to list of problematic cards
                multiple_match_cards.append({
                    'search_name': card_name,
                    'matches': card_result
                })
            elif len(card_result) == 0:
                await ctx.send(_tr(
                    lang,
                    "One of the card names doesn't exist.",
                    "أحد اسمي البطاقتين غير موجود.",
                    "L'un des noms de carte n'existe pas."
                ))
                return
            else:
                # If exactly one match, add to card_ids
                card_ids.append(card_result[0]['card_id'])

        # If any cards have multiple matches, create a detailed embed
        if multiple_match_cards:
            embed = discord.Embed(
                title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                description=_tr(lang, "Some of your card names are ambiguous:", "بعض أسماء بطاقاتك غير واضحة:", "Certains des noms de vos cartes sont ambigus :"),
                color=discord.Color.blue()
            )

            for match_info in multiple_match_cards:
                card_list = "\n".join([f"• {card['name']}" for card in match_info['matches']])
                embed.add_field(
                    name=_tr(lang, f"Matches for '{match_info['search_name']}'", f"نتائج لـ '{match_info['search_name']}'", f"Correspondances pour « {match_info['search_name']} »"),
                    value=card_list,
                    inline=False
                )

            embed.set_footer(text=_tr(lang, "Please use the exact card name when swapping.", "يرجى استخدام الاسم الدقيق للبطاقة عند الاستبدال.", "Veuillez utiliser le nom exact de la carte lors de l'échange."))
            await ctx.send(embed=embed)
            return

        # Check if both cards are in the team
        query = """
        SELECT card_id, position
        FROM team_cards
        WHERE team_id = %s AND card_id IN (%s, %s)
        """
        team_cards = await execute_query_with_retry(query, (team_id, card_ids[0], card_ids[1]))

        if len(team_cards) != 2:
            await ctx.send(_tr(
                lang,
                "Both cards need to be in your team to swap their positions.",
                "يجب أن تكون البطاقتان ضمن فريقك لتبديل مواقعهما.",
                "Les deux cartes doivent être dans votre équipe pour échanger leurs positions."
            ))
            return

        # Swap positions
        positions = {card['card_id']: card['position'] for card in team_cards}

        query = """
        UPDATE team_cards
        SET position = CASE
            WHEN card_id = %s THEN %s
            WHEN card_id = %s THEN %s
        END
        WHERE team_id = %s AND card_id IN (%s, %s)
        """
        await execute_query_with_retry(query, (
            card_ids[0], positions[card_ids[1]],
            card_ids[1], positions[card_ids[0]],
            team_id, card_ids[0], card_ids[1]
        ))

        await ctx.send(_tr(
            lang,
            f"Successfully swapped the positions of '{first_card}' and '{second_card}' in your team!",
            f"تم تبديل موضعي '{first_card}' و '{second_card}' في فريقك بنجاح!",
            f"Échange réussi des positions de « {first_card} » et « {second_card} » dans votre équipe !"
        ))
async def setup(bot):
    await bot.add_cog(TeamCommands(bot))

