import discord
from discord.ext import commands
from discord.ui import Select, View
import copy
from typing import List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont
import os
import io
import asyncio
import random
import traceback
import logging
from datetime import datetime
import json
import time
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

# Configure logging
logger = logging.getLogger('adventure_commands')
logger.setLevel(logging.DEBUG)

# Create file handler
fh = logging.FileHandler('adventure_commands.log')
fh.setLevel(logging.DEBUG)

# Create console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(fh)
logger.addHandler(ch)

class ChapterSelect(Select):
    def __init__(self, arcs: List[Dict], current_arc_id: int, lang: int):
        options = [
            discord.SelectOption(
                label=f"{_tr(lang, 'Arc', 'القوس', 'Arc')} {arc['arc_name']}", 
                value=str(arc['arc_id']),
                description=_tr(lang, f"View chapters for {arc['arc_name']}", f"عرض فصول {arc['arc_name']}", f"Voir les chapitres de {arc['arc_name']}"),
                default=arc['arc_id'] == current_arc_id
            ) for arc in arcs
        ]
        super().__init__(placeholder=_tr(lang, "Select an arc to view its chapters...", "اختر قوسًا لعرض فصوله...", "Sélectionnez un arc pour voir ses chapitres..."), options=options)
        self.lang = lang

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.view.show_arc_chapters(interaction, int(self.values[0]))

class AdventureView(View):
    def __init__(self, cog, ctx, user_id: int, arcs: List[Dict], chapters: List[Dict], user_chapters: List[Dict], lang: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.user_id = user_id
        self.arcs = arcs
        self.chapters = chapters
        self.user_chapters = user_chapters
        self.current_arc_id = arcs[0]['arc_id'] if arcs else None
        self.lang = lang
        self.add_item(ChapterSelect(arcs, self.current_arc_id, self.lang))

    def create_arc_embed(self, arc_id: int) -> discord.Embed:
        selected_arc = next((arc for arc in self.arcs if arc['arc_id'] == arc_id), None)
        if not selected_arc:
            return None

        arc_chapters = [ch for ch in self.chapters if ch['arc_id'] == arc_id]
        arc_chapters.sort(key=lambda x: x['chapter_position'])

        user_arc_stars = sum(
            uch['total_stars'] for uch in self.user_chapters 
            if any(ch['chapter_id'] == uch['chapter_id'] and ch['arc_id'] == arc_id for ch in self.chapters)
        )

        embed = discord.Embed(
            title=f"{_tr(self.lang, 'Arc', 'القوس', 'Arc')}: {selected_arc['arc_name']}",
            description=f"{selected_arc['arc_description']}\n{_tr(self.lang, 'Progress', 'التقدّم', 'Progression')}: {user_arc_stars}/{selected_arc['arc_stars']} <:reward:1309253302667710596>",
            color=discord.Color.from_rgb(47, 49, 54)
        )

        if self.ctx.author.avatar:
            embed.set_thumbnail(url=self.ctx.author.avatar.url)

        for chapter in arc_chapters:
            user_chapter = next(
                (uch for uch in self.user_chapters if uch['chapter_id'] == chapter['chapter_id']), 
                None
            )

            stars_text = f"<:blue_dot:1311418123404181585> {_tr(self.lang, 'Stars', 'النجوم', 'Étoiles')}: "
            if user_chapter:
                stars_text += "<:reward:1309253302667710596>" * user_chapter['total_stars']
                stars_text += "<:Black_star:1330204429281923144>" * (3 - user_chapter['total_stars'])
            else:
                stars_text += "<:Black_star:1330204429281923144>" * 3

            chest = {
                1: "<:shadowchest:1303850149122015324>",
                2: "<:hunterchest:1303850110412783626>",
                3: "<:mona:1309275864206278748>"
            }.get(chapter['chapter_chest'], "")

            chapter_text = (
                f"`sl chapter info {chapter['chapter_id']}` {_tr(self.lang, 'to view description', 'لعرض الوصف', 'pour voir la description')}\n"
                f"`sl chapter start {chapter['chapter_id']}` {_tr(self.lang, 'to begin fight', 'لبدء القتال', 'pour commencer le combat')}\n"
                f"{stars_text}\n"
                f"<:blue_dot:1311418123404181585> {_tr(self.lang, 'Reward', 'المكافأة', 'Récompense')}: {chest}"
            )

            embed.add_field(
                name=f"{_tr(self.lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter['chapter_position']}: {chapter['chapter_name']}",
                value=chapter_text,
                inline=False
            )

        return embed

    async def show_arc_chapters(self, interaction: discord.Interaction, arc_id: int):
        self.current_arc_id = arc_id
        embed = self.create_arc_embed(arc_id)
        if embed:
            await interaction.message.edit(embed=embed, view=self)

class AdventureCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"
        self.image_size = (300, 350)
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds

    @commands.command(name="adventuredbstats")
    @commands.has_permissions(administrator=True)
    async def db_stats(self, ctx):
        """Display database connection statistics"""
        metrics = get_metrics()
        
        embed = discord.Embed(
            title="Database Connection Metrics",
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

    @commands.command(name="adventure", aliases=["adv","advr"])
    async def sl_adventure(self, ctx):
        try:
            lang = await _get_lang(ctx.author.id)
            # Check if user exists
            user_data = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(ctx.author.id),)
            )

            if not user_data:
                await ctx.send(_tr(lang, "You don't have a profile yet! Create one first.", "ليس لديك ملف بعد! أنشئ واحدًا أولاً.", "Vous n’avez pas encore de profil ! Créez-en un d’abord."))
                return

            user_id = user_data[0]['user_id']

            # Fetch all required data
            arcs = await execute_query_with_retry(
                "SELECT * FROM arc ORDER BY arc_position"
            )

            chapters = await execute_query_with_retry(
                "SELECT * FROM chapter ORDER BY arc_id, chapter_position"
            )

            user_chapters = await execute_query_with_retry(
                "SELECT * FROM user_chapters WHERE user_id = %s",
                (user_id,)
            )

            if not arcs:
                await ctx.send(_tr(lang, "No adventure content available!", "لا توجد محتويات مغامرة متاحة!", "Aucun contenu d’aventure disponible !"))
                return

            # Create view with initial arc display
            view = AdventureView(self, ctx, user_id, arcs, chapters, user_chapters, lang)
            initial_embed = view.create_arc_embed(arcs[0]['arc_id'])
            await ctx.send(embed=initial_embed, view=view)

        except Exception as e:
            logger.error(f"Error in sl_adventure: {e}")
            print(f"Error in sl_adventure: {e}")
            await ctx.send(_tr(0, "An error occurred while fetching adventure data!", "حدث خطأ أثناء جلب بيانات المغامرة!", "Une erreur s’est produite lors de la récupération des données d’aventure !"))

    @commands.group(name="chapter")
    async def chapter(self, ctx):
        """Base command for chapter-related operations"""
        lang = await _get_lang(ctx.author.id)
        logger.info(f"Chapter command invoked by {ctx.author.id}")
        if ctx.invoked_subcommand is None:
            logger.debug("No subcommand specified, showing help")
            embed = discord.Embed(
                title=_tr(lang, "Chapter Command Help", "مساعدة أوامر الفصول", "Aide des commandes Chapitre"),
                description=_tr(lang, "Available chapter commands:", "أوامر الفصول المتاحة:", "Commandes de chapitre disponibles :"),
                color=discord.Color.from_rgb(47, 49, 54)
            )
            embed.add_field(
                name=f"`sl chapter info <chapter_id>`",
                value=_tr(lang, "View detailed information about a specific chapter and its cards.", "اعرض معلومات مفصلة حول فصل محدد وبطاقاته.", "Voir des informations détaillées sur un chapitre et ses cartes."),
                inline=False
            )
            await ctx.send(embed=embed)

    @chapter.command(name="info")
    async def chapter_info(self, ctx, chapter_id: int = None):
        """View detailed information about a specific chapter and its cards."""
        lang = await _get_lang(ctx.author.id)
        logger.info(f"Chapter info command invoked by {ctx.author.id} for chapter {chapter_id}")

        if chapter_id is None:
            logger.warning("No chapter ID provided")
            await ctx.send(_tr(lang, "Please provide a chapter ID! Usage: `sl chapter info <chapter_id>`", "يرجى تقديم معرف الفصل! الاستخدام: `sl chapter info <chapter_id>`", "Veuillez fournir un ID de chapitre ! Utilisation : `sl chapter info <chapter_id>`"))
            return

        try:
            # Check if user exists
            user_data = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(ctx.author.id),)
            )

            if not user_data:
                await ctx.send(_tr(lang, "You don't have a profile yet! Create one first.", "ليس لديك ملف بعد! أنشئ واحدًا أولاً.", "Vous n’avez pas encore de profil ! Créez-en un d’abord."))
                return

            user_id = user_data[0]['user_id']

            # Get chapter information with cards and fragments
            chapter_data = await execute_query_with_retry(
                """
                SELECT c.*, a.arc_name 
                FROM chapter c
                JOIN arc a ON c.arc_id = a.arc_id
                WHERE c.chapter_id = %s
                """,
                (chapter_id,)
            )

            if not chapter_data:
                await ctx.send(_tr(lang, "Chapter not found!", "الفصل غير موجود!", "Chapitre introuvable !"))
                return

            chapter = chapter_data[0]

            # Updated query to include fragment information
            chapter_cards = await execute_query_with_retry(
                """
                SELECT cc.*, c.card_name, cc.round,
                    cc.health_stat, cc.attack_stat, cc.speed_stat,
                    cc.defence_stat, cc.power_stat,
                    f.fragments_icon, f.name_fragment
                FROM chapter_cards cc
                JOIN card c ON cc.card_id = c.card_id
                JOIN fragments f ON c.id_fragment = f.id_fragment
                WHERE cc.chapter_id = %s
                ORDER BY cc.round, cc.card_id
                """,
                (chapter_id,)
            )
            # Create embed
            # For chapter_description: keep as is if lang == 0, else use chapter_ardescription for 1, chapter_frdescription for 3
            if lang == 1:
                chapter_desc = chapter.get('chapter_ardescription') or chapter.get('chapter_description', '')
            elif lang == 3:
                chapter_desc = chapter.get('chapter_frdescription') or chapter.get('chapter_description', '')
            else:
                chapter_desc = chapter.get('chapter_description', '')

            embed = discord.Embed(
                title=f"{chapter['arc_name']} - {chapter['chapter_name']}",
                description=chapter_desc,
                color=discord.Color.from_rgb(47, 49, 54)
            )

            if ctx.author.avatar:
                embed.set_thumbnail(url=ctx.author.avatar.url)

            # Group cards by round
            cards_by_round = {}
            for card in chapter_cards:
                round_num = card['round']
                if round_num not in cards_by_round:
                    cards_by_round[round_num] = []
                cards_by_round[round_num].append(card)

            # Add card information to embed with fragment icons
            for round_num in sorted(cards_by_round.keys()):
                round_cards = cards_by_round[round_num]
                round_text = []
                
                for card in round_cards:
                    stats_text = (
                        f"**<:soloheart:1302912058819477514> {_tr(lang, 'HP', 'الصحة', 'PV')}: **{card['health_stat']} "
                        f"**<:soloweapon:1302961287747342356> {_tr(lang, 'ATK', 'الهجوم', 'ATQ')}:  **{card['attack_stat']} "
                        f"**<:solosecondshield:1302959829413007380> {_tr(lang, 'DEF', 'الدفاع', 'DEF')}: **{card['defence_stat']}  "
                        f"**<:pngclipartemojirunningsneakersaf:1330240246377480242> {_tr(lang, 'SPD', 'السرعة', 'VIT')}: **{card['speed_stat']}"
                    )
                    # Add fragment icon before card name
                    fragment_icon = card['fragments_icon']
                    round_text.append(f"{fragment_icon} **{card['card_name']}**\n{stats_text}")
                
                embed.add_field(
                    name=f"{_tr(lang, 'Round', 'الجولة', 'Manche')} {round_num}",
                    value="\n\n".join(round_text),
                    inline=False
                )

            # Create and attach cards image
            if chapter_cards:
                try:
                    base_image = await self.create_cards_image(chapter_cards)
                    if base_image:
                        img_byte_arr = io.BytesIO()
                        base_image.save(img_byte_arr, format='PNG')
                        img_byte_arr.seek(0)
                        file = discord.File(fp=img_byte_arr, filename="chapter_cards.png")
                        embed.set_image(url="attachment://chapter_cards.png")
                        await ctx.send(embed=embed, file=file)
                    else:
                        await ctx.send(embed=embed)
                except Exception as e:
                    logger.error(f"Error creating cards image: {e}")
                    await ctx.send(embed=embed)
            else:
                await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Database error in chapter info command: {e}")
            await ctx.send(_tr(lang, "An error occurred while fetching chapter information!", "حدث خطأ أثناء جلب معلومات الفصل!", "Une erreur s’est produite lors de la récupération des informations du chapitre !"))

    async def create_cards_image(self, cards: List[Dict]) -> Optional[Image.Image]:
        """Create composite image of chapter cards with dynamic height"""
        logger.debug(f"Creating cards image for {len(cards)} cards")
        try:
            # Base image dimensions and card sizing
            CARD_WIDTH = 250
            CARD_HEIGHT = 350
            PADDING = 40
            BORDER_WIDTH = 15
            
            # Calculate required width and height based on number of cards
            cards_per_row = 3
            num_cards = len(set(card['card_id'] for card in cards))  # Count unique cards
            num_rows = (num_cards + cards_per_row - 1) // cards_per_row  # Ceiling division
            
            WIDTH = cards_per_row * (CARD_WIDTH + PADDING * 2) + PADDING
            HEIGHT = num_rows * (CARD_HEIGHT + PADDING * 2) + PADDING
            
            logger.debug("Creating base image")
            base_image = Image.new('RGBA', (WIDTH, HEIGHT), color=(0, 0, 0, 0))
            draw = ImageDraw.Draw(base_image)
            
            try:
                font_path = os.path.join(os.path.dirname(__file__), 'Poppins-Regular.ttf')
                font_quantity = ImageFont.truetype(font_path, 32)
            except Exception as e:
                logger.warning(f"Failed to load custom font: {e}")
                font_quantity = ImageFont.load_default()
            
            # Process cards
            processed_cards = {}
            for card in cards:
                if card['card_id'] in processed_cards:
                    processed_cards[card['card_id']]['quantity'] += 1
                else:
                    processed_cards[card['card_id']] = {
                        'image': card['image'],
                        'quantity': 1
                    }
            
            # Calculate positions dynamically
            positions = []
            for row in range(num_rows):
                for col in range(cards_per_row):
                    x = PADDING + col * (CARD_WIDTH + PADDING * 2)
                    y = PADDING + row * (CARD_HEIGHT + PADDING * 2)
                    positions.append((x, y))
            
            BORDER_COLOR = (255, 255, 255, 180)
            
            # Add cards to composite
            idx = 0
            for card_id, card_info in processed_cards.items():
                if idx >= len(positions):
                    break
                    
                card_image_path = self.validate_image_path(card_info['image'])
                
                if not card_image_path:
                    continue
                
                try:
                    with Image.open(card_image_path) as card_img:
                        card_img = card_img.convert('RGBA')
                        card_img = card_img.resize((CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)
                        
                        bordered_surface = Image.new('RGBA', 
                            (CARD_WIDTH + BORDER_WIDTH * 2, CARD_HEIGHT + BORDER_WIDTH * 2), 
                            color=(0, 0, 0, 0)
                        )
                        draw_border = ImageDraw.Draw(bordered_surface)
                        
                        for i in range(BORDER_WIDTH):
                            alpha = int(180 * (1 - i/BORDER_WIDTH))
                            border_color = (*BORDER_COLOR[:3], alpha)
                            draw_border.rectangle(
                                [i, i, CARD_WIDTH + BORDER_WIDTH*2 - i, CARD_HEIGHT + BORDER_WIDTH*2 - i], 
                                outline=border_color
                            )
                        
                        bordered_surface.paste(card_img, (BORDER_WIDTH, BORDER_WIDTH))
                        base_image.paste(
                            bordered_surface, 
                            (positions[idx][0] - BORDER_WIDTH, positions[idx][1] - BORDER_WIDTH), 
                            bordered_surface
                        )
                        
                        if card_info['quantity'] > 1:
                            quantity_text = f"x{card_info['quantity']}"
                            draw.text(
                                (positions[idx][0] + CARD_WIDTH - draw.textlength(quantity_text, font=font_quantity) - 20, 
                                positions[idx][1] + CARD_HEIGHT - 45), 
                                quantity_text, 
                                font=font_quantity, 
                                fill=(0, 0, 0, 128)
                            )
                            draw.text(
                                (positions[idx][0] + CARD_WIDTH - draw.textlength(quantity_text, font=font_quantity) - 18, 
                                positions[idx][1] + CARD_HEIGHT - 46), 
                                quantity_text, 
                                font=font_quantity, 
                                fill=(255, 255, 255)
                            )
                        
                        idx += 1
                except Exception as e:
                    logger.error(f"Error processing card {card_id}: {e}")
                    continue
            
            return base_image
                
        except Exception as e:
            logger.error(f"Error in create_cards_image: {e}", exc_info=True)
            return None


    def validate_image_path(self, image_name):
        """
        Validate and log details about the image path.
        Returns the full path or None if invalid.
        """
        if not image_name:
            logger.warning("No image name provided")
            return None
        image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        full_path = os.path.join(image_path, image_name)
        
        if not os.path.exists(full_path):
            logger.error(f"Image not found: {full_path}")
            return None
        
        if not os.path.isfile(full_path):
            logger.error(f"Path is not a file: {full_path}")
            return None
        
        try:
            # Check file size and readability
            file_size = os.path.getsize(full_path)
            logger.info(f"Image found: {full_path}, Size: {file_size} bytes")
            
            return full_path
        except Exception as e:
            logger.error(f"Error checking image file: {full_path}. Error: {e}")
            return None

    @chapter.command(name="start")
    async def chapter_start(self, ctx, chapter_id: int = None):
        """Start a chapter battle with the specified chapter ID."""
        lang = await _get_lang(ctx.author.id)

        if chapter_id is None:
            await ctx.send(_tr(lang, "Please provide a chapter ID! Usage: `sl chapter start <chapter_id>`",
                               "يرجى تقديم معرف الفصل! الاستخدام: `sl chapter start <chapter_id>`",
                               "Veuillez fournir un ID de chapitre ! Utilisation : `sl chapter start <chapter_id>`"))
            return

        user_id = None
        try:
            # Get user data
            user_data = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(ctx.author.id),)
            )

            if not user_data:
                await ctx.send(_tr(lang, "You don't have a profile yet! Create one first.",
                                   "ليس لديك ملف بعد! أنشئ واحدًا أولاً.",
                                   "Vous n’avez pas encore de profil ! Créez-en un d’abord."))
                return

            user_id = user_data[0]['user_id']

            # Check if user is already in a chapter battle
            chapter_count_check = await execute_query_with_retry(
                "SELECT chapter_count FROM user WHERE user_id = %s",
                (user_id,)
            )

            if chapter_count_check[0]['chapter_count'] > 0:
                embed = discord.Embed(
                    title=_tr(lang, "❌ Chapter Battle Error", "❌ خطأ معركة الفصل", "❌ Erreur de combat de chapitre"),
                    description=_tr(lang, "You are already in a chapter battle! Please finish your current battle first.",
                                    "أنت بالفعل في معركة فصل! يرجى إنهاء معركتك الحالية أولاً.",
                                    "Vous êtes déjà dans un combat de chapitre ! Terminez d’abord votre combat en cours."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            arc_data = await execute_query_with_retry(
                "SELECT c.arc_id, a.arc_stars FROM chapter c JOIN arc a ON c.arc_id = a.arc_id WHERE c.chapter_id = %s",
                (chapter_id,)
            )

            if not arc_data:
                await ctx.send(_tr(lang, "Invalid chapter ID!", "معرّف الفصل غير صالح!", "ID de chapitre invalide !"))
                return

            # Skip previous chapter check for Chapter 1
            if chapter_id > 1:
                # Check if previous chapter is completed
                previous_chapter = await execute_query_with_retry(
                    "SELECT total_stars FROM user_chapters WHERE user_id = %s AND chapter_id = %s",
                    (user_id, chapter_id - 1)
                )

                if not previous_chapter:
                    embed = discord.Embed(
                        title=_tr(lang, "❌ Chapter Access Denied", "❌ رفض الوصول إلى الفصل", "❌ Accès au chapitre refusé"),
                        description=_tr(lang, f"You need to complete Chapter {chapter_id - 1} first!",
                                        f"تحتاج إلى إكمال الفصل {chapter_id - 1} أولاً!",
                                        f"Vous devez d’abord terminer le Chapitre {chapter_id - 1} !"),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                arc_id = arc_data[0]['arc_id']
                # Check arc requirements if not in Arc 1
                if arc_id > 1:
                    # Get chapter's arc information
                    arc_idd = await execute_query_with_retry(
                        "SELECT arc_id FROM chapter WHERE chapter_id = %s",
                        (chapter_id,)
                    )
                    arc_iddd = arc_idd[0]['arc_id']
                    arc_iddb = await execute_query_with_retry(
                        "SELECT arc_stars FROM arc WHERE arc_id = %s",
                        (arc_iddd - 1,)
                    )
                    arc_iddd = arc_iddb[0]['arc_stars']
                    required_arc_stars = arc_iddd
                    # Get total stars from previous arc
                    total_arc_stars = await execute_query_with_retry(
                        """
                        SELECT SUM(uc.total_stars) as stars
                        FROM user_chapters uc
                        JOIN chapter c ON uc.chapter_id = c.chapter_id
                        WHERE uc.user_id = %s AND c.arc_id = %s
                        """,
                        (user_id, arc_id - 1)
                    )

                    current_stars = total_arc_stars[0]['stars'] or 0
                    newarc = arc_id - 1
                    req = required_arc_stars - 2
                    print(current_stars)
                    print("req : ", req)
                    if current_stars < req:
                        embed = discord.Embed(
                            title=_tr(lang, "❌ Arc Access Denied", "❌ رفض الوصول إلى القوس", "❌ Accès à l’arc refusé"),
                            description=_tr(lang,
                                            f"You need {req - current_stars} more stars from the previous arc to access this chapter!",
                                            f"تحتاج إلى {req - current_stars} نجوم إضافية من القوس السابق للوصول إلى هذا الفصل!",
                                            f"Il vous faut {req - current_stars} étoiles de plus de l’arc précédent pour accéder à ce chapitre !"),
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            # Update user's chapter count
            await execute_query_with_retry(
                "UPDATE user SET chapter_count = 1 WHERE user_id = %s",
                (user_id,)
            )

            # Get team data
            team_data = await execute_query_with_retry(
                "SELECT t.current_team as team_id FROM user t WHERE t.user_id = %s",
                (user_id,)
            )

            if not team_data:
                await ctx.send(_tr(lang, "You need to create a team first!",
                                   "تحتاج إلى إنشاء فريق أولاً!",
                                   "Vous devez d’abord créer une équipe !"))
                await execute_query_with_retry(
                    "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                    (user_id,)
                )
                return

            team_id = team_data[0]['team_id']
 
            # Get user's fighter cards with charm data
            user_cards_query = """
            SELECT c.card_id, c.card_name, uc.image, c.card_rank, c.xp_cap, uc.max_leveled,
                uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, 
                uc.current_level, uc.current_xp, uc.id_enhancer, uc.fight_count, 
                uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
                uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count, tc.position
            FROM team_cards tc
            JOIN card c ON tc.card_id = c.card_id
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            WHERE tc.team_id = %s AND c.type_card = 'Fighter'
            ORDER BY tc.position
            """
            user_fighters = await execute_query_with_retry(user_cards_query, (user_id, team_id))

            if not user_fighters:
                await ctx.send(_tr(lang, "You need at least one Fighter card in your team to start a chapter!",
                                   "تحتاج إلى بطاقة مقاتل واحدة على الأقل في فريقك لبدء فصل!",
                                   "Vous avez besoin d’au moins une carte Combattant dans votre équipe pour commencer un chapitre !"))
                await execute_query_with_retry(
                    "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                    (user_id,)
                )
                return

            # Store original stats before applying charm effects
            original_fighters = copy.deepcopy(user_fighters)
            
            # Apply charm effects temporarily for battle
            user_fighters = await self.apply_charm_effects(user_fighters, user_id)

            # Get chapter cards and star requirements
            chapter_data = await execute_query_with_retry(
                """
                SELECT c.*, cc.card_id, cc.health_stat, cc.attack_stat, cc.defence_stat, 
                    cc.speed_stat, cc.power_stat, cc.round,
                    ch.chapter_firststar, ch.chapter_secondstar, ch.chapter_thirdstar
                FROM chapter_cards cc
                JOIN card c ON cc.card_id = c.card_id
                JOIN chapter ch ON cc.chapter_id = ch.chapter_id
                WHERE cc.chapter_id = %s
                ORDER BY cc.round, cc.power_stat
                """,
                (chapter_id,)
            )

            if not chapter_data:
                await ctx.send(_tr(lang, "Invalid chapter ID or no enemies found in this chapter!",
                                   "معرّف فصل غير صالح أو لم يتم العثور على أعداء في هذا الفصل!",
                                   "ID de chapitre invalide ou aucun ennemi trouvé dans ce chapitre !"))
                await execute_query_with_retry(
                    "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                    (user_id,)
                )
                return

            # Extract star requirements
            first_star_time = chapter_data[0]['chapter_firststar']
            second_star_time = chapter_data[0]['chapter_secondstar']
            third_star_time = chapter_data[0]['chapter_thirdstar']

            # Group enemies by round
            chapter_rounds = {}
            for card in chapter_data:
                round_num = card['round']
                if round_num not in chapter_rounds:
                    chapter_rounds[round_num] = []
                chapter_card = {
                    'card_id': card['card_id'],
                    'card_name': card['card_name'],
                    'image': card['image'],
                    'card_rank': card['card_rank'],
                    'health_stat': card['health_stat'],
                    'attack_stat': card['attack_stat'],
                    'defence_stat': card['defence_stat'],
                    'speed_stat': card['speed_stat'],
                    'power_stat': card['power_stat'],
                    'round': card['round'],
                    'current_health': card['health_stat']
                }
                chapter_rounds[round_num].append(chapter_card)

            total_rounds = len(chapter_rounds)

            # Create initial "Loading" embed
            initial_embed = discord.Embed(
                title=f"⚔️ **{_tr(lang, 'Chapter Battle Loading', 'جارٍ تحميل معركة الفصل', 'Chargement du combat de chapitre')}** ⚔️",
                description=_tr(lang, "Preparing for battle...", "جارٍ التحضير للمعركة...", "Préparation du combat..."),
                color=discord.Color.dark_gold()
            )
            battle_message = await ctx.send(embed=initial_embed)
            await asyncio.sleep(3)

            # Initialize battle state
            user_team = []
            for card in user_fighters:
                new_card = card.copy()
                new_card['current_health'] = new_card['health_stat']
                user_team.append(new_card)

            battle_log = []

            # Start the timer
            start_time = asyncio.get_event_loop().time()

            # Main battle loop for each round
            for current_round in range(1, total_rounds + 1):
                round_enemies = chapter_rounds[current_round].copy()
                
                # Initial round setup
                current_user_card = user_team[0]
                current_enemy = round_enemies[0]

                # Create/update embed with first fighters of the round
                embed, file = await self.create_fight_embed(
                    current_user_card, 
                    current_enemy, 
                    ctx.author.name, 
                    f"{_tr(lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter_id} {_tr(lang, 'Enemy', 'العدو', 'Ennemi')}", 
                    is_challenge=False
                )

                battle_log = [f"\n**{_tr(lang, 'Round', 'الجولة', 'Manche')} {current_round} {_tr(lang, 'of Chapter', 'من الفصل', 'du Chapitre')} {chapter_id}**",
                            f"\n**{_tr(lang, 'Fight', 'المعركة', 'Combat')} 1: {current_user_card['card_name']} vs {current_enemy['card_name']}**"]
                
                embed = await self.update_fight_embed(
                    embed, current_user_card, current_enemy,
                    ctx.author.name, f"{_tr(lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter_id} {_tr(lang, 'Enemy', 'العدو', 'Ennemi')}", battle_log[-5:]
                )

                await battle_message.remove_attachments(battle_message.attachments)
                await battle_message.edit(attachments=[file], embed=embed)

                fight_number = 1
                
                # Combat loop for current round
                while round_enemies and user_team:
                    enemy = round_enemies[0]
                    user_card = user_team[0]

                    # Combat turn loop
                    while enemy['current_health'] > 0 and user_card['current_health'] > 0:
                        # Player attack
                        user_damage = random.randint(
                            int(user_card['attack_stat'] * 0.45),
                            int(user_card['attack_stat'] * 0.55)
                        )
                        effective_user_damage = self.calculate_effective_damage(user_damage, enemy['defence_stat'])
                        enemy['current_health'] = max(0, enemy['current_health'] - effective_user_damage)
                        
                        battle_log.append(_tr(lang,
                                              f"🔵 {user_card['card_name']} deals {effective_user_damage} damage!",
                                              f"🔵 {user_card['card_name']} يسبّب {effective_user_damage} ضررًا!",
                                              f"🔵 {user_card['card_name']} inflige {effective_user_damage} dégâts !"))
                        embed = await self.update_fight_embed(
                            embed, user_card, enemy, ctx.author.name,
                            f"{_tr(lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter_id} {_tr(lang, 'Enemy', 'العدو', 'Ennemi')}", battle_log[-5:]
                        )
                        await battle_message.edit(embed=embed)
                        await asyncio.sleep(3)

                        if enemy['current_health'] > 0:
                            # Enemy attack
                            enemy_damage = random.randint(
                                int(enemy['attack_stat'] * 0.45),
                                int(enemy['attack_stat'] * 0.55)
                            )
                            effective_enemy_damage = self.calculate_effective_damage(enemy_damage, user_card['defence_stat'])
                            user_card['current_health'] = max(0, user_card['current_health'] - effective_enemy_damage)
                            
                            battle_log.append(_tr(lang,
                                                  f"🔴 {enemy['card_name']} deals {effective_enemy_damage} damage!",
                                                  f"🔴 {enemy['card_name']} يسبّب {effective_enemy_damage} ضررًا!",
                                                  f"🔴 {enemy['card_name']} inflige {effective_enemy_damage} dégâts !"))
                            embed = await self.update_fight_embed(
                                embed, user_card, enemy, ctx.author.name,
                                f"{_tr(lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter_id} {_tr(lang, 'Enemy', 'العدو', 'Ennemi')}", battle_log[-5:]
                            )
                            await battle_message.edit(embed=embed)
                            await asyncio.sleep(2)

                    # Handle fight outcome
                    if enemy['current_health'] <= 0:
                        battle_log.append(_tr(lang,
                                              f"💥 **{enemy['card_name']} has been defeated!**",
                                              f"💥 **{enemy['card_name']} قد هُزِم!**",
                                              f"💥 **{enemy['card_name']} a été vaincu !**"))
                        round_enemies.pop(0)
                        
                        if round_enemies:
                            fight_number += 1
                            battle_log.append(_tr(lang, "🆕 **Next enemy approaching!**",
                                                  "🆕 **العدو التالي يقترب!**",
                                                  "🆕 **Nouvel ennemi en approche !**"))
                            battle_log.append(_tr(lang,
                                                  f"\n**Fight {fight_number}: {user_card['card_name']} vs {round_enemies[0]['card_name']}**",
                                                  f"\n**المعركة {fight_number}: {user_card['card_name']} ضد {round_enemies[0]['card_name']}**",
                                                  f"\n**Combat {fight_number} : {user_card['card_name']} vs {round_enemies[0]['card_name']}**"))
                            
                            new_file = await self.update_fight_image(user_card, round_enemies[0])
                            embed = await self.update_fight_embed(
                                embed, user_card, round_enemies[0],
                                ctx.author.name, f"{_tr(lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter_id} {_tr(lang, 'Enemy', 'العدو', 'Ennemi')}", battle_log[-5:]
                            )
                            await battle_message.remove_attachments(battle_message.attachments)
                            await battle_message.edit(attachments=[new_file], embed=embed)
                    else:
                        battle_log.append(_tr(lang,
                                              f"💔 **{user_card['card_name']} has been defeated!**",
                                              f"💔 **{user_card['card_name']} قد هُزِم!**",
                                              f"💔 **{user_card['card_name']} a été vaincu !**"))
                        user_team.pop(0)
                        
                        if user_team:
                            battle_log.append(_tr(lang, "🔄 **Next fighter entering!**",
                                                  "🔄 **المقاتل التالي يدخل!**",
                                                  "🔄 **Prochain combattant entre en scène !**"))
                            battle_log.append(_tr(lang,
                                                  f"\n**Fight {fight_number}: {user_team[0]['card_name']} vs {enemy['card_name']}**",
                                                  f"\n**المعركة {fight_number}: {user_team[0]['card_name']} ضد {enemy['card_name']}**",
                                                  f"\n**Combat {fight_number} : {user_team[0]['card_name']} vs {enemy['card_name']}**"))
                            
                            new_file = await self.update_fight_image(user_team[0], enemy)
                            embed = await self.update_fight_embed(
                                embed, user_team[0], enemy,
                                ctx.author.name, f"{_tr(lang, 'Chapter', 'الفصل', 'Chapitre')} {chapter_id} {_tr(lang, 'Enemy', 'العدو', 'Ennemi')}", battle_log[-5:]
                            )
                            await battle_message.remove_attachments(battle_message.attachments)
                            await battle_message.edit(attachments=[new_file], embed=embed)

                    # Check end conditions
                    if not user_team:
                        # Stop timer for loss
                        end_time = asyncio.get_event_loop().time()
                        total_time = round(end_time - start_time)
                        
                        embed.add_field(
                            name=_tr(lang, "❌ Chapter Failed", "❌ فشل الفصل", "❌ Chapitre échoué"),
                            value=_tr(lang,
                                      f"{ctx.author.mention} Your team has been defeated after {total_time} seconds. Better luck next time!",
                                      f"{ctx.author.mention} لقد هُزم فريقك بعد {total_time} ثانية. حظًا أوفر في المرة القادمة!",
                                      f"{ctx.author.mention} Votre équipe a été vaincue après {total_time} secondes. Ce sera pour la prochaine fois !"),
                            inline=False
                        )
                        
                        # Update charm counts without applying stat boosts
                        await self.update_charm_counts(user_id, original_fighters)
                        
                        await execute_query_with_retry(
                            "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                            (user_id,)
                        )
                        await battle_message.edit(embed=embed)
                        return
                    elif not round_enemies:
                        if current_round == total_rounds:
                            # Stop timer for win
                            end_time = asyncio.get_event_loop().time()
                            total_time = round(end_time - start_time)
                            stars_earned = 1  # Default 1 star
                            if total_time <= third_star_time:
                                stars_earned = 3
                            elif total_time <= second_star_time:
                                stars_earned = 2

                            # Check existing progress
                            existing_progress = await execute_query_with_retry(
                                "SELECT total_stars FROM user_chapters WHERE user_id = %s AND chapter_id = %s",
                                (user_id, chapter_id)
                            )

                            if existing_progress:
                                current_stars = existing_progress[0]['total_stars']
                                if stars_earned > current_stars:
                                    await execute_query_with_retry(
                                        "UPDATE user_chapters SET total_stars = %s WHERE user_id = %s AND chapter_id = %s",
                                        (stars_earned, user_id, chapter_id)
                                    )
                            else:
                                await execute_query_with_retry(
                                    "INSERT INTO user_chapters (user_id, chapter_id, total_stars) VALUES (%s, %s, %s)",
                                    (user_id, chapter_id, stars_earned)
                                )

                            # Update charm counts without applying stat boosts
                            await self.update_charm_counts(user_id, original_fighters)

                            # Create star display
                            stars_display = "<:reward:1309253302667710596>" * stars_earned

                            embed.add_field(
                                name=_tr(lang, "🎉 Chapter Complete!", "🎉 اكتمل الفصل!", "🎉 Chapitre terminé !"),
                                value=_tr(lang,
                                          f"Congratulations {ctx.author.mention}! You've completed Chapter {chapter_id} in {total_time} seconds!\nStars Earned: {stars_display}",
                                          f"تهانينا {ctx.author.mention}! لقد أكملت الفصل {chapter_id} في {total_time} ثانية!\nالنجوم المكتسبة: {stars_display}",
                                          f"Félicitations {ctx.author.mention} ! Vous avez terminé le Chapitre {chapter_id} en {total_time} secondes !\nÉtoiles obtenues : {stars_display}"),
                                inline=False
                            )
                            await battle_message.edit(embed=embed)
                            
                            # Reset chapter count
                            await execute_query_with_retry(
                                "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                                (user_id,)
                            )
                            if not existing_progress:
                                # Extract card IDs from original_fighters
                                fighter_card_ids = [fighter['card_id'] for fighter in original_fighters]
                                await self.handle_chapter_rewards(ctx, user_id, team_id, chapter_id, fighter_card_ids)
                            return
                        else:
                            battle_log.append(_tr(lang, "✨ **Round Complete!** Preparing for next round...",
                                                  "✨ **انتهت الجولة!** يتم التحضير للجولة التالية...",
                                                  "✨ **Manche terminée !** Préparation de la manche suivante..."))
                            await asyncio.sleep(2)
                            break

        except Exception as e:
            # Make sure to reset chapter count on error
            if user_id:
                await execute_query_with_retry(
                    "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                    (user_id,)
                )
                
            error_details = {
                'error_type': type(e).__name__,
                'error_message': str(e),
                'chapter_id': chapter_id,
                'user_id': ctx.author.id,
                'timestamp': datetime.now().isoformat()
            }
            
            print("Chapter battle error:", json.dumps(error_details))
            print("Traceback:", traceback.format_exc())
            
            # User-friendly error message
            error_msg = discord.Embed(
                title=_tr(lang, "❌ Chapter Battle Error", "❌ خطأ معركة الفصل", "❌ Erreur de combat de chapitre"),
                description=_tr(lang,
                                f"An error occurred during the chapter battle:\n```{type(e).__name__}: {str(e)}```\nThe error has been logged for investigation.",
                                f"حدث خطأ أثناء معركة الفصل:\n```{type(e).__name__}: {str(e)}```\nتم تسجيل الخطأ للمراجعة.",
                                f"Une erreur s’est produite pendant le combat de chapitre :\n```{type(e).__name__} : {str(e)}```\nL’erreur a été consignée pour investigation."),
                color=discord.Color.red()
            )
            await ctx.send(embed=error_msg)

            # Prepare error message for support channel
            error_message = (
                f"🚨 Error in chapter system!\n"
                f"```\nError: {str(e)}\n"
                f"Channel: {ctx.channel.name} ({ctx.channel.id})\n"
                f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
                f"Command User: {ctx.author.name} ({ctx.author.id})```\n"
                f"Traceback:\n```python\n{traceback.format_exc()}```"
            )
            
            try:
                # Get the support channel and send error message
                support_channel = self.bot.get_channel(1336306672741908561)
                if support_channel:
                    # First message with ping
                    await support_channel.send("<@818132161534230559>")
                    
                    # Split and send error message
                    if len(error_message) > 2000:
                        parts = [error_message[i:i+1990] for i in range(0, len(error_message), 1990)]
                        for part in parts:
                            await support_channel.send(f"```{part}```")
                    else:
                        await support_channel.send(error_message)
                else:
                    print("Could not find support channel")
            except Exception as send_error:
                print(f"Failed to send error to support channel: {str(send_error)}")
                print(error_message)
            
            # Clean up battle state
            try:
                if user_id:  # Only if user_id was defined before error
                    await execute_query_with_retry(
                        "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                        (user_id,)
                    )
            except Exception as cleanup_error:
                print("Cleanup error:", str(cleanup_error))


    async def apply_charm_effects(self, fighters, user_id):
        """Apply charm effects temporarily without updating the database."""
        modified_fighters = copy.deepcopy(fighters)
        
        for fighter in modified_fighters:
            if fighter['charm_attack'] == 1 or fighter['charm_attack'] == 9:
                fighter['attack_stat'] = round(fighter['attack_stat'] * 1.2)

            if fighter['charm_defence'] == 1 or fighter['charm_defence'] == 11:
                fighter['defence_stat'] = round(fighter['defence_stat'] * 1.2)

            if fighter['charm_speed'] == 1 or fighter['charm_speed'] == 10:
                fighter['speed_stat'] = round(fighter['speed_stat'] * 1.2)

            if fighter['charm_health'] == 1 or fighter['charm_health'] == 12:
                fighter['health_stat'] = round(fighter['health_stat'] * 1.2)
                
            if fighter['charm_attack'] == 25:
                fighter['attack_stat'] = round(fighter['attack_stat'] * 1.35)

            if fighter['charm_defence'] == 28:
                fighter['defence_stat'] = round(fighter['defence_stat'] * 1.35)

            if fighter['charm_speed'] == 26:
                fighter['speed_stat'] = round(fighter['speed_stat'] * 1.35)

            if fighter['charm_health'] == 27:
                fighter['health_stat'] = round(fighter['health_stat'] * 1.35)

        
        return modified_fighters

    async def update_charm_counts(self, user_id, fighters):
        """Update only the charm counts and remove charms if count reaches 0."""
        for fighter in fighters:
            updates = []
            charm_resets = []
            
            if fighter['charm_attack'] == 1:
                updates.append("attack_count = attack_count - 1")
                charm_resets.append("charm_attack = CASE WHEN attack_count <= 1 THEN 0 ELSE charm_attack END")
                
            if fighter['charm_defence'] == 1:
                updates.append("defence_count = defence_count - 1")
                charm_resets.append("charm_defence = CASE WHEN defence_count <= 1 THEN 0 ELSE charm_defence END")
                
            if fighter['charm_speed'] == 1:
                updates.append("speed_count = speed_count - 1")
                charm_resets.append("charm_speed = CASE WHEN speed_count <= 1 THEN 0 ELSE charm_speed END")
                
            if fighter['charm_health'] == 1:
                updates.append("health_count = health_count - 1")
                charm_resets.append("charm_health = CASE WHEN health_count <= 1 THEN 0 ELSE charm_health END")
            
            if updates:
                update_query = f"""
                UPDATE user_cards
                SET {', '.join(updates)},
                    {', '.join(charm_resets)}
                WHERE user_id = %s AND card_id = %s
                """
                await execute_query_with_retry(update_query, (user_id, fighter['card_id']))

    async def cleanup_battle_state(self, user_id):
        """Clean up battle state after an error"""
        try:
            await execute_query_with_retry(
                "UPDATE user SET chapter_count = 0 WHERE user_authorid = %s",
                (str(user_id),)
            )
        except Exception as e:
            logger.error(f"Error in cleanup_battle_state: {e}")

    async def create_fight_embed(self, user_card, opponent_card, user_name, opponent_name, is_challenge=False):
        lang = await _get_lang(user_card.get("user_authorid") if isinstance(user_card, dict) else None)

        embed = discord.Embed(
            title=_tr(lang, "⚔️ **Chapter Battle** ⚔️", "⚔️ **معركة الفصل** ⚔️", "⚔️ **Combat de Chapitre** ⚔️"),
            color=discord.Color.dark_gold()
        )
        
        embed.add_field(
            name=_tr(lang, f"🔵 **{user_name}'s Fighter**", f"🔵 **مقاتل {user_name}**", f"🔵 **Combattant de {user_name}**"),
            value=self.create_card_info(user_card, is_user=True, name=user_name),
            inline=True
        )
        
        embed.add_field(
            name=f"🔴 **{opponent_name}**",
            value=self.create_card_info(opponent_card, is_user=False, name=opponent_name),
            inline=True
        )

        file = await self.update_fight_image(user_card, opponent_card)
        embed.set_image(url="attachment://battle_cards.png")

        return embed, file

    async def update_fight_embed(self, embed, user_card, opponent_card, user_name, opponent_name, battle_log):
        lang = await _get_lang(user_card.get("user_authorid") if isinstance(user_card, dict) else None)

        embed.clear_fields()

        embed.add_field(
            name=_tr(lang, f"🔵 **{user_name}'s Fighter**", f"🔵 **مقاتل {user_name}**", f"🔵 **Combattant de {user_name}**"),
            value=self.create_card_info(user_card, is_user=True, name=user_name),
            inline=True
        )
        
        embed.add_field(
            name=f"🔴 **{opponent_name}**",
            value=self.create_card_info(opponent_card, is_user=False, name=opponent_name),
            inline=True
        )

        log_text = "\n".join(battle_log)

        if log_text:
            embed.add_field(
                name=_tr(lang, "🗒️ **Battle Log**", "🗒️ **سجل المعركة**", "🗒️ **Journal de Combat**"),
                value=f"```css\n{log_text}\n```",
                inline=False
            )

        return embed


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

    def create_card_info(self, card, is_user, name):
        lang = getattr(self, "lang", 0)

        health_percentage = (card['current_health'] / card['health_stat']) * 100
        health_bar = ""
        filled = int(health_percentage / 10)
        
        if is_user:
            health_bar = self.green_square * filled + self.gray_square * (10 - filled)
        else:
            health_bar = self.red_square * filled + self.gray_square * (10 - filled)

        return (
            f"**{card['card_name']}**\n"
            f"{health_bar} {card['current_health']}/{card['health_stat']} {_tr(lang, 'HP', 'نقطة حياة', 'PV')}\n"
            f"🔸 **{_tr(lang, 'POW', 'قوة', 'PUISS')}**: {card['power_stat']}\n"
            f"🔸 **{_tr(lang, 'ATK', 'هجوم', 'ATQ')}**: {card['attack_stat']}\n"
            f"🔸 **{_tr(lang, 'DEF', 'دفاع', 'DEF')}**: {card['defence_stat']}\n"
            f"🔸 **{_tr(lang, 'SPD', 'سرعة', 'VIT')}**: {card['speed_stat']}\n"
        )

    def manage_combat_log(self, round_log, current_round):
        """
        Manages the combat log by splitting it into multiple fields if needed.
        Each field has a maximum of 1024 characters.
        """
        lang = getattr(self, "lang", 0)

        fields = []
        current_field = []
        current_length = 0
        
        for line in round_log:
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > 1000:  # Using 1000 to be safe
                # Create a new field with accumulated lines
                field_value = "\n".join(current_field)
                fields.append((f"⚔️ {_tr(lang, 'Round', 'الجولة', 'Manche')} {current_round}", field_value, False))
                
                # Start a new field
                current_field = [line]
                current_length = line_length
            else:
                current_field.append(line)
                current_length += line_length
        
        # Add the last field if it has content
        if current_field:
            field_value = "\n".join(current_field)
            fields.append((f"⚔️ {_tr(lang, 'Round', 'الجولة', 'Manche')} {current_round}", field_value, False))
        
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
        lang = getattr(self, "lang", 0)

        health_percentage = max(0, min(100, (entity['current_health'] / entity['health_stat']) * 100))
        filled_blocks = min(10, max(0, int(health_percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_monster:
            filled_square = self.red_square if health_percentage > 0 else self.gray_square
        else:
            filled_square = self.green_square

        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"
        name = entity.get('card_name', 'Unknown')
        
        # Add damage taken display if provided
        damage_text = ""
        return f"{name}: {health_bar} **({health_percentage:.1f}%){damage_text}** ({max(0, entity['current_health'])}/{entity['health_stat']} {_tr(lang, 'HP', 'نقطة حياة', 'PV')})"

    async def handle_chapter_rewards(self, ctx, user_id, team_id, chapter_id, fighter_card_ids=None):
        """Handle all rewards after completing a chapter"""
        print(f"Processing chapter rewards for user_id: {user_id}, team_id: {team_id}, chapter_id: {chapter_id}")
        
        try:
            lang = await _get_lang(ctx.author.id)

            # Get chapter chest info
            chapter_chest_data = await execute_query_with_retry(
                "SELECT chapter_chest FROM chapter WHERE chapter_id = %s",
                (chapter_id,)
            )

            if not chapter_chest_data:
                print(f"No chest data found for chapter_id: {chapter_id}")
                await ctx.send(_tr(lang, "Error retrieving chapter data!", "حدث خطأ أثناء جلب بيانات الفصل!", "Erreur lors de la récupération des données du chapitre !"))
                return

            chapter_chest = chapter_chest_data[0]['chapter_chest']
            print(f"Retrieved chest type {chapter_chest} for chapter {chapter_id}")

            # Process chest rewards
            chest_mappings = {
                1: {"id": 1, "name": _tr(lang, "Shadow Chest", "صندوق الظل", "Coffre de l'Ombre"), "icon": "<:shadowchest:1303850149122015324>"},
                2: {"id": 2, "name": _tr(lang, "Hunter Chest", "صندوق الصياد", "Coffre du Chasseur"), "icon": "<:hunterchest:1303850110412783626>"},
                3: {"id": 3, "name": _tr(lang, "Monarch Chest", "صندوق الملك", "Coffre du Monarque"), "icon": "<:mona:1309275864206278748>"}
            }

            # Get inventory ID
            inventory_result = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s",
                (user_id,)
            )
            
            if not inventory_result:
                print(f"No inventory found for user_id: {user_id}")
                await ctx.send(_tr(lang, "Error: Inventory not found!", "خطأ: لم يتم العثور على الجرد!", "Erreur : inventaire introuvable !"))
                return

            inventory_id = inventory_result[0]['inventory_id']
            chest_info = chest_mappings.get(chapter_chest)

            if chest_info:
                print(f"Processing {chest_info['name']} reward for user_id: {user_id}")
                
                # Check if user already has this chest type
                existing_chest = await execute_query_with_retry(
                    "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s",
                    (inventory_id, chest_info['id'])
                )
                print("exist: ", existing_chest)

                try:
                    if existing_chest:
                        print(f"Updating existing chest quantity for inventory_id: {inventory_id}")
                        await execute_query_with_retry(
                            "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = %s",
                            (inventory_id, chest_info['id'])
                        )
                    else:
                        print(f"Adding new chest for inventory_id: {inventory_id}")
                        await execute_query_with_retry(
                            "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, 1)",
                            (inventory_id, chest_info['id'])
                        )
                except Exception as e:
                    logging.error(f"Error processing chest reward: {str(e)}")
                    raise

            # Handle XP rewards and team advancement
            print(f"Processing XP rewards for user_id: {user_id}")
            xp_rewards, total_xp_gained, total_cards_leveled = await self.handle_xp_rewards(ctx, user_id, team_id, fighter_card_ids)
            print(f"XP rewards processed - Total XP: {total_xp_gained}, Cards Leveled: {total_cards_leveled}")

            # Reset chapter count after successful completion
            try:
                await execute_query_with_retry(
                    "UPDATE user SET chapter_count = 0 WHERE user_id = %s",
                    (user_id,)
                )
                print(f"Reset chapter count for user_id: {user_id}")
            except Exception as e:
                print(f"Error resetting chapter count: {str(e)}")
                raise

            # Send rewards embed
            await self.send_chapter_rewards_embed(ctx, xp_rewards, total_xp_gained, total_cards_leveled, chest_info)
            logging.info(f"Rewards successfully sent to user_id: {user_id}")

        except Exception as e:
            logging.error(f"Error handling chapter rewards: {str(e)}")
            logging.error(traceback.format_exc())
            await ctx.send(_tr(0, "An error occurred while processing rewards!", "حدث خطأ أثناء معالجة المكافآت!", "Une erreur s'est produite lors du traitement des récompenses !"))

    async def handle_xp_rewards(self, ctx, user_id, team_id, fighter_card_ids=None):
        """Handle XP distribution for team cards with proper equipment and support card handling"""
        print(f"Processing XP rewards for user_id: {user_id}, team_id: {team_id}")
        
        try:
            lang = await _get_lang(ctx.author.id)

            # Get the user's cards that participated in the battle
            if fighter_card_ids:
                # Convert list of IDs to comma-separated string for SQL query
                card_ids_str = ', '.join(str(card_id) for card_id in fighter_card_ids)
                
                # Modified query to include base stats and equipment info
                cards_query = f"""
                SELECT c.card_name, c.card_icon, c.card_rank,c.xp_cap, uc.max_leveled,
                    uc.current_level, uc.current_xp,
                    uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat, uc.power_stat,
                    uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                    uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
                    uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count, uc.card_id,
                    uc.weapon_id, uc.armor_id, uc.shield_id
                FROM user_cards uc
                JOIN card c ON uc.card_id = c.card_id
                WHERE uc.user_id = %s AND uc.card_id IN ({card_ids_str})
                ORDER BY FIELD(uc.card_id, {card_ids_str})
                """
                
                team_cards = await execute_query_with_retry(cards_query, (user_id,))
            else:
                # Fallback to original method if no fighter_card_ids provided
                team_cards_query = """
                SELECT tc.*, c.card_name, c.card_icon, c.card_rank, c.xp_cap, uc.max_leveled,
                    uc.current_level, uc.current_xp,
                    uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat, uc.power_stat,
                    uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                    uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
                    uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count, uc.card_id,
                    uc.weapon_id, uc.armor_id, uc.shield_id
                FROM team_cards tc
                JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
                JOIN card c ON tc.card_id = c.card_id
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                ORDER BY tc.position
                """
                
                team_cards = await execute_query_with_retry(team_cards_query, (user_id, team_id))
            
            print('Retrieved team cards:', team_cards)  # Debug print
            
            if not team_cards:
                print(f"No cards found for user_id: {user_id}, team_id: {team_id}")
                await ctx.send(_tr(lang, "No cards found in your team. Unable to process XP rewards.", "لم يتم العثور على بطاقات في فريقك. لا يمكن معالجة نقاط الخبرة.", "Aucune carte trouvée dans votre équipe. Impossible de traiter l’XP."))
                return [], 0, 0

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
                print(f"Processing XP for card: {card['card_name']}, current level: {card['current_level']}")
                
                # Handle max level cards
                if card['current_level'] >= card['max_leveled']:
                    print(f"Card {card['card_name']} is already at max level")
                    xp_rewards.append(self.create_max_level_card_reward(card))
                    continue

                # Calculate base XP reward
                xp_reward = random.randint(20, 100)
                total_xp_gained += xp_reward

                # Process enhancer bonus if active
                enhancer_active = False
                if card.get('id_enhancer'):
                    enhancer_active = True
                    if card.get('id_enhancer') == 17:
                        xp_reward = int(xp_reward * 1.2)  # 20% XP boost
                    else:
                        print("hallow enh")
                        xp_reward = int(xp_reward * 1.35)  # 20% bonus XP only if XP matters
                    new_fight_count = card.get('fight_count', 0) - 1
                    
                    try:
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
                    except Exception as e:
                        print(f"Error updating enhancer status: {str(e)}")
                        raise

                # Calculate new XP and level
                new_xp = card['current_xp'] + xp_reward
                new_level = card['current_level']
                level_up = False
                stat_increases = {'attack': 0, 'health': 0, 'defence': 0, 'speed': 0}

                # Handle level up
                if new_xp >= card['xp_cap']:
                    if new_level < card['max_leveled']:
                        new_level += 1
                        new_xp = new_xp - card['xp_cap']
                        level_up = True
                        total_cards_leveled += 1
                        stat_increases = {
                            'attack': random.randint(2, 4),
                            'health': random.randint(3, 5),
                            'defence': random.randint(1, 2),
                            'speed': random.randint(2, 4)
                        }
                    else:
                        new_xp = card['xp_cap']

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

                # Record rewards for this card
                xp_rewards.append({
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
                    'new_stats': new_stats,
                    'max_level_reached': new_level >= card['max_leveled']
                })

                print(f"Card {card['card_name']} processed - New Level: {new_level}, XP Gained: {xp_reward}")

            print(f"XP reward processing complete - Total XP: {total_xp_gained}, Cards Leveled: {total_cards_leveled}")
            return xp_rewards, total_xp_gained, total_cards_leveled

        except Exception as e:
            logging.error(f"Error in handle_xp_rewards: {str(e)}")


            logging.error(traceback.format_exc())
            raise

    # Helper methods needed for the improved handle_xp_rewards function

    async def calculate_equipment_boosts(self, user_id, card_id):
        """Calculate the boosts provided by a card's equipment"""
        # Get the card's equipment
        equipment_query = """
        SELECT weapon_id, armor_id, shield_id
        FROM user_cards
        WHERE user_id = %s AND card_id = %s
        """
        equipment = await execute_query_with_retry(equipment_query, (user_id, card_id))
        
        if not equipment:
            return None
        
        equipment = equipment[0]
        
        # Initialize boosts
        boosts = {
            'flat_attack': 0,
            'flat_health': 0,
            'flat_defence': 0,
            'flat_speed': 0,
            'pct_attack': 0,
            'pct_health': 0,
            'pct_defence': 0,
            'pct_speed': 0
        }
        
        # Check each equipment type
        equipment_types = [
            equipment['weapon_id'],
            equipment['armor_id'],
            equipment['shield_id']
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

    def create_max_level_card_reward(self, card):
        """Create reward object for max level card"""
        return {
            'card_name': card['card_name'],
            'card_icon': card['card_icon'],
            'xp_gained': 0,
            'enhancer_active': False,
            'new_level': card['current_level'],
            'old_level': card['current_level'],
            'new_xp': card['current_xp'],
            'xp_cap': card['xp_cap'],
            'level_up': False,
            'stat_increases': {'attack': 0, 'health': 0, 'defence': 0, 'speed': 0},
            'new_stats': {
                'attack': card['attack_stat'],
                'health': card['health_stat'],
                'defence': card['defence_stat'],
                'speed': card['speed_stat']
            },
            'max_level_reached': True
        }

    async def send_chapter_rewards_embed(self, ctx, xp_rewards, total_xp_gained, total_cards_leveled, chest_info):
        """Send embed with all chapter rewards including XP and chest"""
        logging.info(f"Creating rewards embed for user: {ctx.author.id}")
        
        try:
            lang = await _get_lang(ctx.author.id)

            embed = discord.Embed(
                title=_tr(lang, "🏆 Chapter Completion Rewards 🏆", "🏆 مكافآت إكمال الفصل 🏆", "🏆 Récompenses de Fin de Chapitre 🏆"),
                color=discord.Color.gold(),
                description=(
                    f"{_tr(lang, 'Total XP Gained', 'إجمالي الخبرة المكتسبة', 'XP total gagné')}: **{total_xp_gained}**\n"
                    f"{_tr(lang, 'Cards Leveled Up', 'البطاقات التي ارتفع مستواها', 'Cartes montées de niveau')}: **{total_cards_leveled}** \n "
                    f"{_tr(lang, 'Chests Received', 'الصناديق المستلمة', 'Coffres reçus')} : 1x {chest_info['icon']}"
                )
            )

            # Process each card's rewards
            for reward in xp_rewards:
                field_value = self.format_card_reward(reward)
                field_name = f"{reward['card_icon']} {reward['card_name']}"
                
                if reward['max_level_reached'] and reward['old_level'] >= reward['new_level']:
                    field_name += f": 🏆 **{_tr(lang, 'MAX LEVEL', 'أقصى مستوى', 'NIVEAU MAX')}**"
                elif reward['level_up']:
                    field_name += f": ✨ {_tr(lang, 'Level', 'المستوى', 'Niveau')} {reward['old_level']} → {reward['new_level']}"

                embed.add_field(
                    name=field_name,
                    value=field_value,
                    inline=False
                )

            embed.set_footer(text=_tr(lang, "Keep fighting to grow stronger! | Solo Leveling PVP",
                                      "واصل القتال لتصبح أقوى! | Solo Leveling PVP",
                                      "Continue de te battre pour devenir plus fort ! | Solo Leveling PVP"))
            await ctx.send(embed=embed)
            logging.info("Rewards embed sent successfully")
            
        except Exception as e:
            logging.error(f"Error creating rewards embed: {str(e)}")
            logging.error(traceback.format_exc())
            await ctx.send(_tr(0, "An error occurred while displaying rewards!",
                               "حدث خطأ أثناء عرض المكافآت!",
                               "Une erreur s'est produite lors de l'affichage des récompenses !"))

    def create_max_level_card_reward(self, card):
        """Create reward object for max level card"""
        return {
            'card_name': card['card_name'],
            'card_icon': card['card_icon'],
            'xp_gained': 0,
            'enhancer_active': False,
            'new_level': card['current_level'],
            'old_level': card['current_level'],
            'new_xp': card['current_xp'],
            'xp_cap': card['xp_cap'],
            'level_up': False,
            'stat_increases': {'attack': 0, 'health': 0, 'defence': 0, 'speed': 0},
            'new_stats': {
                'attack': card['attack_stat'],
                'health': card['health_stat'],
                'defence': card['defence_stat'],
                'speed': card['speed_stat']
            },
            'max_level_reached': True
        }

    def format_card_reward(self, reward):
        """Format the reward information for a single card"""
        lang = getattr(self, "lang", 0)

        if reward['max_level_reached'] and reward['old_level'] >= reward['new_level']:
            return _tr(lang, "No further XP or stat increases possible.",
                       "لا يمكن كسب خبرة إضافية أو زيادة في الإحصائيات.",
                       "Aucun gain d’XP ou d’augmentation de stats supplémentaire possible.")

        # Create XP progress bar
        xp_percentage = min((reward['new_xp'] / reward['xp_cap']) * 100, 100)
        progress_bar = self.create_xp_progress_bar(xp_percentage)
        
        reward_text = []
        if reward['enhancer_active']:
            reward_text.append(_tr(lang, "🔥 Enhanced!", "🔥 مُعزَّز!", "🔥 Amélioré !"))
        reward_text.append(f"**+{reward['xp_gained']} XP**")
        reward_text.append(f"{progress_bar} **{reward['new_xp']}/{reward['xp_cap']}**")

        if reward['level_up']:
            reward_text.append(_tr(lang, "📈 Stats Increased:", "📈 زيادة الإحصائيات:", "📈 Statistiques augmentées :"))
            reward_text.append("```diff")
            reward_text.append(f"+ {_tr(lang, 'ATK', 'هجوم', 'ATQ')}: +{reward['stat_increases']['attack']}")
            reward_text.append(f"+ {_tr(lang, 'HP', 'نقطة حياة', 'PV')}:  +{reward['stat_increases']['health']}")
            reward_text.append(f"+ {_tr(lang, 'DEF', 'دفاع', 'DEF')}: +{reward['stat_increases']['defence']}")
            reward_text.append(f"+ {_tr(lang, 'SPD', 'سرعة', 'VIT')}: +{reward['stat_increases']['speed']}")
            reward_text.append("```")

        return "\n".join(reward_text)

    def create_xp_progress_bar(self, percentage):
        """Create a visual progress bar for XP with exactly 10 blocks"""
        # Calculate filled blocks (0-10)
        filled = min(round(percentage / 10), 10)  # Round to nearest whole number
        empty = 10 - filled  # Ensure total is always 10
        
        # Create progress bar with exactly 10 blocks
        progress_bar = (self.green_square * filled) + (self.gray_square * empty)
        
        return progress_bar

async def setup(bot):
    logger.info("Setting up AdventureCommands cog")
    await bot.add_cog(AdventureCommands(bot))