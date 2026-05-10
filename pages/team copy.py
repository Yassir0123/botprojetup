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
# Create a command cooldown decorator
class PaginatedCardCollectionView(discord.ui.View):
    def __init__(self, ctx, results, user_name, user, card_type=None):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.results = results
        self.user_name = user_name
        self.user = user
        self.card_type = card_type
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = math.ceil(len(results) / self.items_per_page)
        self.message = None
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def update_message(self, interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        type_title = f" {self.card_type.capitalize()}" if self.card_type else ""
        
        embed = discord.Embed(
            title=f"{self.user_name}'s{type_title} Card Collection",
            description=f"Showing {len(self.results)} cards (Page {self.current_page + 1}/{self.total_pages})",
            color=self.user.color
        )
        
        for i, card in enumerate(self.results[start_idx:end_idx], start=start_idx + 1):
            # Determine card name display
            display_name = card['pseudo_name'] if card.get('pseudo_name') else card['name']
            
            # Format the stat values with commas for thousands
            power = f"{card['power_stat']:,}"
            
            # Different display for different card types
            if card['type_card'] == 'Fighter':
                atk = f"{card['attack_stat']:,}"
                def_stat = f"{card['defence_stat']:,}"
                spd = f"{card['speed_stat']:,}"
                hp = f"{card['health_stat']:,}"
                
                stats = f"ATK: **{atk}** | DEF: **{def_stat}** | SPD: **{spd}** | HP: **{hp}**"
            else:
                stats = f"Type: **{card['type_card']}**"
            
            embed.add_field(
                name=f"{i}. {display_name}",
                value=(
                    f"Power: **{power}** | Level: **{card['current_level']}/{card['max_level']}**\n"
                    f"{stats}\n"
                    f"Rank: **{card['cardrank_name']}**"
                ),
                inline=False
            )
        
        embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text=f"ID: {self.user.id} • Use ◀ ▶ to navigate")
        embed.timestamp = discord.utils.utcnow()
        
        return embed

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)
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
class PaginatedStatsView(discord.ui.View):
    def __init__(self, ctx, results, stat_name, stat_field):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.results = results
        self.stat_name = stat_name
        self.stat_field = stat_field
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = math.ceil(len(results) / self.items_per_page)
        self.message = None
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def update_message(self, interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        embed = discord.Embed(
            title=f"Top {self.stat_name} Cards",
            description=f"Showing cards with highest {self.stat_name.lower()} stats",
            color=discord.Color.gold()
        )
        
        for i, card in enumerate(self.results[start_idx:end_idx], start=start_idx + 1):
            # Determine card name display
            display_name = card.get('pseudo_name') if card.get('pseudo_name') else card.get('card_name', card.get('name', 'Unknown Card'))
        
            # Format the stat value with commas for thousands
            stat_value = f"{card[self.stat_field]:,}" if self.stat_field in card else "N/A"
        
            # Debug info to console
            print(f"Card keys: {card.keys()}")
            print(f"Looking for stat field: {self.stat_field}")
        
            embed.add_field(
                name=f"{i}. {display_name}",
                value=f"Owner: **{card.get('user_name', 'Unknown')}**\n{self.stat_name}: **{stat_value}**\nRank: **{card.get('cardrank_name', 'Unknown')}**",
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Use ◀ ▶ to navigate")
        embed.timestamp = discord.utils.utcnow()
        
        return embed

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

class PaginatedAllStatsView(discord.ui.View):
    def __init__(self, ctx, results):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.results = results
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = math.ceil(len(results) / self.items_per_page)
        self.message = None
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def update_message(self, interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        embed = discord.Embed(
            title="Top Combined Stats Cards",
            description="Showing cards with highest combined stats (ATK + DEF + SPD + HP)",
            color=discord.Color.gold()
        )
        
        for i, card in enumerate(self.results[start_idx:end_idx], start=start_idx + 1):
            # Determine card name display
            display_name = card.get('pseudo_name') if card.get('pseudo_name') else card.get('name', 'Unknown Card')
            
            # Format the stat values with commas for thousands
            total_stats = f"{card.get('total_stats', 'N/A'):,}"
            atk = f"{card.get('attack_stat', 'N/A'):,}"
            def_stat = f"{card.get('defence_stat', 'N/A'):,}"
            spd = f"{card.get('speed_stat', 'N/A'):,}"
            hp = f"{card.get('health_stat', 'N/A'):,}"
            
            embed.add_field(
                name=f"{i}. {display_name}",
                value=(
                    f"Owner: **{card.get('user_name', 'Unknown')}**\n"
                    f"Total: **{total_stats}**\n"
                    f"discordid: **{card.get('user_authorid','Unknown')}"
                    f"ATK: **{atk}** | DEF: **{def_stat}** | SPD: **{spd}** | HP: **{hp}**\n"
                    f"Rank: **{card.get('cardrank_name', 'Unknown')}**"
                ),
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Use ◀ ▶ to navigate")
        embed.timestamp = discord.utils.utcnow()
        
        return embed

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

class PaginatedCardStatsView(discord.ui.View):
    def __init__(self, ctx, results, card_name):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.results = results
        self.card_name = card_name
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = math.ceil(len(results) / self.items_per_page)
        self.message = None
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def update_message(self, interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        embed = discord.Embed(
            title=f"{self.card_name} Stats",
            description=f"Showing all {self.card_name} cards across users",
            color=discord.Color.gold()
        )
        
        for i, card in enumerate(self.results[start_idx:end_idx], start=start_idx + 1):
            # Determine card name display
            display_name = card.get('pseudo_name') if card.get('pseudo_name') else card.get('name', 'Unknown Card')
            
            # Format the stat values with commas for thousands
            power = f"{card.get('power_stat', 'N/A'):,}"
            atk = f"{card.get('attack_stat', 'N/A'):,}"
            def_stat = f"{card.get('defence_stat', 'N/A'):,}"
            spd = f"{card.get('speed_stat', 'N/A'):,}"
            hp = f"{card.get('health_stat', 'N/A'):,}"
            
            embed.add_field(
                name=f"{i}. {display_name}",
                value=(
                    f"Owner: **{card.get('user_name', 'Unknown')}**\n"
                    f"Power: **{power}** | Level: **{card.get('current_level', 'N/A')}/{card.get('max_level', 'N/A')}**\n"
                    f"discordid: **{card.get('user_authorid','Unknown')}"
                    f"ATK: **{atk}** | DEF: **{def_stat}** | SPD: **{spd}** | HP: **{hp}**\n"
                    f"Rank: **{card.get('cardrank_name', 'Unknown')}**"
                ),
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Use ◀ ▶ to navigate")
        embed.timestamp = discord.utils.utcnow()
        
        return embed

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

class PaginatedRankView(discord.ui.View):
    def __init__(self, ctx, results, rank):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.results = results
        self.rank = rank
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = math.ceil(len(results) / self.items_per_page)
        self.message = None
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
            
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def update_message(self, interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        embed = discord.Embed(
            title=f"Top {self.rank}-Rank Cards",
            description=f"Showing the most powerful {self.rank}-rank cards",
            color=discord.Color.gold()
        )
        
        for i, card in enumerate(self.results[start_idx:end_idx], start=start_idx + 1):
            # Determine card name display
            display_name = card['pseudo_name'] if card.get('pseudo_name') else card['name']
            
            # Format the stat values with commas for thousands
            power = f"{card['power_stat']:,}"
            atk = f"{card['attack_stat']:,}"
            def_stat = f"{card['defence_stat']:,}"
            spd = f"{card['speed_stat']:,}"
            hp = f"{card['health_stat']:,}"
            
            embed.add_field(
                name=f"{i}. {display_name}",
                value=(
                    f"Owner: **{card['user_name']}**\n"
                    f"discordid: **{card.get('user_authorid','Unknown')}"
                    f"Power: **{power}**\n"
                    f"ATK: **{atk}** | DEF: **{def_stat}** | SPD: **{spd}** | HP: **{hp}**"
                ),
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Use ◀ ▶ to navigate")
        embed.timestamp = discord.utils.utcnow()
        
        return embed

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)
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

    @commands.command(name='team',aliases=["tv", "tvw"])
    async def team(self, ctx):
        start_time = time.time()

        try:
            user_authorid = str(ctx.author.id)
            
            query = """
            SELECT u.user_id,uc.pseudo_image, t.team_id, t.team_bg, c.card_id, c.card_name, c.image, uc.current_level, c.card_rank, c.type_card
            FROM user u
            JOIN team t ON u.user_id = t.user_id
            LEFT JOIN team_cards tc ON t.team_id = tc.team_id
            LEFT JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = u.user_id
            LEFT JOIN card c ON uc.card_id = c.card_id
            WHERE u.user_authorid = %s
            ORDER BY tc.position  -- Ensure cards are in the correct order
            """
            result = await execute_query_with_retry(query, (user_authorid,))

            if not result or 'user_id' not in result[0]:
                await ctx.send("Error: You need to register and create a team first.")
                return

            team_bg = result[0]['team_bg']
            team_cards = [r for r in result if r['card_id'] is not None]

            if not team_cards:
                await ctx.send("Your team is empty.")
                return

            background_path = os.path.join(self.image_path, team_bg)
            if not os.path.exists(background_path):
                await ctx.send(f"Error: Background image not found at {background_path}")
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

                    # Only show level for non-Support cards
                    if card['type_card'] != 'Support':
                        level_text = f"Lv.{card['current_level']}"
                        level_bbox = self.poppins_font.getbbox(level_text)
                        level_width = level_bbox[2] - level_bbox[0]
                        level_position = (text_positions[i][0] + (300 - level_width) // 2, text_positions[i][1] + 90)
                        self.draw_gradient_text(draw, level_text, level_position, self.poppins_font, level_width)

                username = await self.bot.fetch_user(int(str(ctx.author.id)))
                title_text = f"{username}'s Team"
                title_bbox = self.title_font.getbbox(title_text)
                title_width = title_bbox[2] - title_bbox[0]
                title_position = ((1600 - title_width) // 2, 20)  # Centered at the top
                self.draw_gradient_text(draw, title_text, title_position, self.title_font, title_width)

                buffer = io.BytesIO()
                background.convert('RGB').save(buffer, format='JPEG', quality=85, optimize=True)
                buffer.seek(0)

            file_size = buffer.getbuffer().nbytes
            print(f"Generated image file size: {file_size} bytes")

            if file_size == 0:
                await ctx.send("Error: Generated image is empty.")
                return

            message_content = f"{ctx.author.mention} Here's your team:"
            await ctx.send(content=message_content, file=discord.File(buffer, filename="team_view.jpg"))
            total_time = time.time() - start_time
            print(f"Total time: {total_time:.2f}s")

        except Exception as e:
            error_msg = f"An error occurred while generating the team image: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await ctx.send(f"An error occurred. Please try again later or contact support.")

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
            
            for  eq_id in equipment_types:
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
        support_card = support_card[0]
        if not support_card:
            return None
        
        # Initialize boosts
        flat_attack = support_card['attack'] or 0
        flat_health = support_card['health'] or 0
        flat_speed = support_card['speed'] or 0
        flat_defence = support_card['defence'] or 0
        
        pct_attack = support_card['attack_percentage'] or 0
        pct_health = support_card['health_percentage'] or 0
        pct_speed = support_card['speed_percentage'] or 0
        pct_defence = support_card['defence_percentage'] or 0
        
        # Add equipment boosts
        equipment_types = [
            support_card['weapon_id'],
            support_card['armor_id'],
            support_card['shield_id']
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

    @commands.command(name='add',aliases=["ad", "tad"])
    async def add(self, ctx, *, character_name):

        user_authorid = str(ctx.author.id)
        
        # Get user_id
        query = "SELECT user_id,squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            return
        
        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']
        # Get team_id
        query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = await execute_query_with_retry(query, (user_id,))
        
        if not team_result:
            await ctx.send("You don't have a team yet.")
            return
        
        team_id = team_result[0]['team_id']
        
        # Look up the card in the card table
        query = "SELECT * FROM card WHERE card_name LIKE %s"
        card_result = await execute_query_with_retry(query, (f"%{character_name}%",))
        
        if not card_result:
            await ctx.send(f"The card '{character_name}' doesn't exist.")
            return
        if len(card_result) > 1:
            # Create an embed to show multiple matches
            embed = discord.Embed(
                title="Multiple Cards Found",
                description="I found multiple cards matching your search. Please specify which one you'd like to view:",
                color=discord.Color.blue()
            )

            # List all matching cards
            card_list = "\n".join([f"• {card['card_name']}" 
                                for card in card_result])
            embed.add_field(name="Matching Cards", value=card_list, inline=False)
            embed.set_footer(text="Please use the command again with the exact name of the card you want to view.")
            []
            await ctx.send(embed=embed)
            return
        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']
        
        if card_type == "Booster":
            await ctx.send(f"'{character_name}' is a Booster card and cannot be added to your team.")
            return

        # Check if the user owns the card
        query = "SELECT * FROM user_cards WHERE user_id = %s and card_id = %s"
        user_card_result = await execute_query_with_retry(query, (user_id, card_id))
        
        if not user_card_result:
            await ctx.send(f"You don't own the card '{character_name}'.")
            return

        # Check if the team already has 4 cards
        query = "SELECT COUNT(*) as card_count FROM team_cards WHERE team_id = %s"
        count_result = await execute_query_with_retry(query, (team_id,))
        
        if count_result[0]['card_count'] >= 4:
            await ctx.send("Your team already has the maximum of 4 cards.")
            return

        # Check if the card is in the team
        query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
        team_card_result = await execute_query_with_retry(query, (team_id, card_id))
        
        if team_card_result:
            await ctx.send(f"'{character_name}' is already in your team.")
            return
        query = "SELECT * FROM squad_cards WHERE squad_id = %s AND card_id = %s AND user_id = %s"
        squad_card_result = await execute_query_with_retry(query, (squad_id, card_id, user_id))
        
        if squad_card_result:
            await ctx.send(f"'{character_name}' is in your squad team, type sl rms {character_name} to remove the card.")
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
        
        await ctx.send(f"Successfully added {card_result[0]['card_icon']} {card_result[0]['card_name']} to your team at position {new_position}!")

    @commands.command(name='remove',aliases=["rm", "trm"])
    async def remove(self, ctx, *, character_name):

        user_authorid = str(ctx.author.id)
        
        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            return
        
        user_id = user_result[0]['user_id']
        
        # Get team_id
        query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = await execute_query_with_retry(query, (user_id,))
        
        if not team_result:
            await ctx.send("You don't have a team yet.")
            return
        
        team_id = team_result[0]['team_id']
        
        # Look up the card in the card table
        query = "SELECT * FROM card WHERE card_name LIKE %s"
        card_result = await execute_query_with_retry(query, (f"%{character_name}%",))
        
        if not card_result:
            await ctx.send(f"The card '{character_name}' doesn't exist.")
            return
        if len(card_result) > 1:
            # Create an embed to show multiple matches
            embed = discord.Embed(
                title="Multiple Cards Found",
                description="I found multiple cards matching your search. Please specify which one you'd like to view:",
                color=discord.Color.blue()
            )

            # List all matching cards
            card_list = "\n".join([f"• {card['card_name']}" 
                                for card in card_result])
            embed.add_field(name="Matching Cards", value=card_list, inline=False)
            embed.set_footer(text="Please use the command again with the exact name of the card you want to view.")
            []
            await ctx.send(embed=embed)
            return
        
        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']
        
        # Check if the card is in the team
        query = "SELECT position FROM team_cards WHERE team_id = %s AND card_id = %s"
        team_card_result = await execute_query_with_retry(query, (team_id, card_id))
        
        if not team_card_result:
            await ctx.send(f"'{character_name}' is not in your team.")
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
        
        await ctx.send(f"Successfully removed {card_result[0]['card_icon']} {card_result[0]['card_name']} from your team and rearranged positions!")

    @commands.command(name='swap',aliases=["sw", "tsw"])
    async def swap(self, ctx, *, card_names):

        # Split the card names
        try:
            first_card, second_card = card_names.rsplit(',', 1)
            first_card = first_card.strip()
            second_card = second_card.strip()
        except ValueError:
            await ctx.send("Please provide two card names separated by a comma.")
            return

        user_authorid = str(ctx.author.id)
        
        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query_with_retry(query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            return
        
        user_id = user_result[0]['user_id']
        
        # Get team_id
        query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = await execute_query_with_retry(query, (user_id,))
        
        if not team_result:
            await ctx.send("You don't have a team yet.")
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
                await ctx.send(f"No card found matching '{card_name}'.")
                return
            else:
                # If exactly one match, add to card_ids
                card_ids.append(card_result[0]['card_id'])

        # If any cards have multiple matches, create a detailed embed
        if multiple_match_cards:
            embed = discord.Embed(
                title="Multiple Cards Found",
                description="Some of your card names are ambiguous:",
                color=discord.Color.blue()
            )

            for match_info in multiple_match_cards:
                # Create a field for each ambiguous card name
                card_list = "\n".join([f"• {card['name']}" for card in match_info['matches']])
                embed.add_field(
                    name=f"Matches for '{match_info['search_name']}'", 
                    value=card_list, 
                    inline=False
                )
            
            embed.set_footer(text="Please use the exact card name when swapping.")
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
            await ctx.send("Both cards need to be in your team to swap their positions.")
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
        
        await ctx.send(f"Successfully swapped the positions of '{first_card}' and '{second_card}' in your team!")
    @commands.command(name="teamdbstats")
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

    async def get_user_name_by_discord_id(self, discord_id):
        """
        Get a user's name from Discord using their Discord ID
        
        Args:
            discord_id (int): The Discord ID of the user
            
        Returns:
            str: The user's name or "Unknown User" if not found
        """
        try:
            user = await self.bot.fetch_user(int(discord_id))
            return user.display_name  # Return the display name as a string
        except Exception as e:
            print(f"Error getting user name by Discord ID: {e}")
            return "Unknown User"

    @commands.command(aliases=["topatk"])
    async def topattack(self, ctx):
        """Display users with the highest attack stat cards"""
        try:
            # Query to get top attack cards with user info
            query = """
            SELECT uc.attack_stat, uc.name, uc.pseudo_name, u.user_authorid, c.card_name, 
                cr.cardrank_name, cr.cardrank_image, c.image, uc.pseudo_image,u.user_authorid
            FROM user_cards uc
            JOIN user u ON uc.user_id = u.user_id
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            WHERE c.type_card = 'Fighter'
            ORDER BY uc.attack_stat DESC
            LIMIT 100
            """
            results = await execute_query_with_retry(query)
            
            if not results:
                await ctx.send("No cards found.")
                return
            
            if results:
                print(f"First result keys: {results[0].keys()}")
                print(f"First result: {results[0]}")
            
            # Process results to include Discord usernames
            for result in results:
                discord_id = result['user_authorid']
                result['user_name'] = await self.get_user_name_by_discord_id(discord_id)
            
            # Create paginated view
            view = PaginatedStatsView(ctx, results, "Attack", "attack_stat")
            embed = view.create_embed()
            
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            
        except Exception as e:
            print(f"Error in topattack command: {e}")
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=["topdef"])
    async def topdefense(self, ctx):
        """Display users with the highest defense stat cards"""
        try:
            # Query to get top defense cards with user info
            query = """
            SELECT uc.defence_stat, uc.name, uc.pseudo_name, u.user_authorid, c.card_name, 
                cr.cardrank_name, cr.cardrank_image, c.image, uc.pseudo_image,u.user_authorid
            FROM user_cards uc
            JOIN user u ON uc.user_id = u.user_id
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            WHERE c.type_card = 'Fighter'
            ORDER BY uc.defence_stat DESC
            LIMIT 100
            """
            results = await execute_query_with_retry(query)
            
            if not results:
                await ctx.send("No cards found.")
                return
            
            if results:
                print(f"First result keys: {results[0].keys()}")
                print(f"First result: {results[0]}")
            
            # Process results to include Discord usernames
            for result in results:
                discord_id = result['user_authorid']
                result['user_name'] = await self.get_user_name_by_discord_id(discord_id)
            
            # Create paginated view
            view = PaginatedStatsView(ctx, results, "Defense", "defence_stat")
            embed = view.create_embed()
            
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            
        except Exception as e:
            print(f"Error in topdefense command: {e}")
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=["topspd"])
    async def topspeed(self, ctx):
        """Display users with the highest speed stat cards"""
        try:
            # Query to get top speed cards with user info
            query = """
            SELECT uc.speed_stat, uc.name, uc.pseudo_name, u.user_authorid, c.card_name, 
                cr.cardrank_name, cr.cardrank_image, c.image, uc.pseudo_image,u.user_authorid
            FROM user_cards uc
            JOIN user u ON uc.user_id = u.user_id
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            WHERE c.type_card = 'Fighter'
            ORDER BY uc.speed_stat DESC
            LIMIT 100
            """
            results = await execute_query_with_retry(query)
            
            if not results:
                await ctx.send("No cards found.")
                return
            
            if results:
                print(f"First result keys: {results[0].keys()}")
                print(f"First result: {results[0]}")
            
            # Process results to include Discord usernames
            for result in results:
                discord_id = result['user_authorid']
                result['user_name'] = await self.get_user_name_by_discord_id(discord_id)
            
            # Create paginated view
            view = PaginatedStatsView(ctx, results, "Speed", "speed_stat")
            embed = view.create_embed()
            
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            
        except Exception as e:
            print(f"Error in topspeed command: {e}")
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=["tophp"])
    async def tophealth(self, ctx):
        """Display users with the highest health stat cards"""
        try:
            # Query to get top health cards with user info
            query = """
            SELECT uc.health_stat, uc.name, uc.pseudo_name, u.user_authorid, c.card_name, 
                cr.cardrank_name, cr.cardrank_image, c.image, uc.pseudo_image,u.user_authorid
            FROM user_cards uc
            JOIN user u ON uc.user_id = u.user_id
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            WHERE c.type_card = 'Fighter'
            ORDER BY uc.health_stat DESC
            LIMIT 100
            """
            results = await execute_query_with_retry(query)
            
            if not results:
                await ctx.send("No cards found.")
                return
            
            if results:
                print(f"First result keys: {results[0].keys()}")
                print(f"First result: {results[0]}")
            
            # Process results to include Discord usernames
            for result in results:
                discord_id = result['user_authorid']
                result['user_name'] = await self.get_user_name_by_discord_id(discord_id)
            
            # Create paginated view
            view = PaginatedStatsView(ctx, results, "Health", "health_stat")
            embed = view.create_embed()
            
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            
        except Exception as e:
            print(f"Error in tophealth command: {e}")
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=["topall"])
    async def topstats(self, ctx):
        """Display users with the highest combined stats cards"""
        try:
            # Query to get top combined stats cards with user info
            query = """
            SELECT 
                (uc.attack_stat + uc.defence_stat + uc.speed_stat + uc.health_stat) as total_stats,
                uc.attack_stat, uc.defence_stat, uc.speed_stat, uc.health_stat,
                uc.name, uc.pseudo_name, u.user_authorid, c.card_name,u.user_authorid, 
                cr.cardrank_name, cr.cardrank_image, c.image, uc.pseudo_image
            FROM user_cards uc
            JOIN user u ON uc.user_id = u.user_id
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            WHERE c.type_card = 'Fighter'
            ORDER BY total_stats DESC
            LIMIT 100
            """
            results = await execute_query_with_retry(query)
            
            if not results:
                await ctx.send("No cards found.")
                return
            
            if results:
                print(f"First result keys: {results[0].keys()}")
                print(f"First result: {results[0]}")
            
            # Process results to include Discord usernames
            for result in results:
                discord_id = result['user_authorid']
                result['user_name'] = await self.get_user_name_by_discord_id(discord_id)
            
            # Create paginated view
            view = PaginatedAllStatsView(ctx, results)
            embed = view.create_embed()
            
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            
        except Exception as e:
            print(f"Error in topstats command: {e}")
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=["toppwr"])
    async def toppower(self, ctx):
        """Display users with the highest power stat cards"""
        try:
            # Query to get top power cards with user info
            query = """
            SELECT uc.power_stat, uc.name, uc.pseudo_name, u.user_authorid, c.card_name, 
                cr.cardrank_name, cr.cardrank_image, c.image, uc.pseudo_image,u.user_authorid,
            FROM user_cards uc
            JOIN user u ON uc.user_id = u.user_id
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            ORDER BY uc.power_stat DESC
            LIMIT 100
            """
            results = await execute_query_with_retry(query)
            
            if not results:
                await ctx.send("No cards found.")
                return
            
            if results:
                print(f"First result keys: {results[0].keys()}")
                print(f"First result: {results[0]}")
            
            # Process results to include Discord usernames
            for result in results:
                discord_id = result['user_authorid']
                result['user_name'] = await self.get_user_name_by_discord_id(discord_id)
            
            # Create paginated view
            view = PaginatedStatsView(ctx, results, "Power", "power_stat")
            embed = view.create_embed()
            
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            
        except Exception as e:
            print(f"Error in toppower command: {e}")
            await ctx.send(f"An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(TeamCommands(bot))
