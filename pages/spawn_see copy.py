import discord
import os
from discord.ext import commands
import random
from datetime import datetime, timedelta
import asyncio
import time
import logging
import urllib
from collections import deque
from discord.ui import Button, View, Select
import traceback
from utils.database import execute_query_with_retry, get_metrics
import asyncio
from PIL import Image, ImageDraw
import io

# Add this class after your existing View classes
class MultiAriseView(discord.ui.View):
    def __init__(self, ctx, pulled_cards, essence_stones_info):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.pulled_cards = pulled_cards
        self.essence_stones_info = essence_stones_info
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    def create_card_grid_image(self):
        """Create a grid image of all pulled cards with rank borders"""
        try:
            # Card dimensions and grid settings
            card_width, card_height = 150, 200
            border_width = 5
            cols = 5
            
            # Calculate rows needed based on number of cards
            num_cards = len(self.pulled_cards)
            rows = (num_cards + cols - 1) // cols  # Ceiling division
            
            # Grid dimensions
            grid_width = cols * (card_width + border_width * 2) + border_width
            grid_height = rows * (card_height + border_width * 2) + border_width
            
            # Create the main grid image
            grid_image = Image.new('RGBA', (grid_width, grid_height), (0, 0, 0, 0))
            
            # Rank colors for borders
            rank_colors = {
                2: (255, 215, 0),    # S Rank - Gold
                3: (139, 0, 0),      # A Rank - Dark Red
                4: (255, 165, 0),    # B Rank - Orange
                5: (32, 131, 39),    # C Rank - Green
                6: (0, 128, 128),    # D Rank - Teal
                7: (128, 128, 128)   # E Rank - Gray
            }
            
            for i, card in enumerate(self.pulled_cards):
                row = i // cols
                col = i % cols
                
                # Calculate position
                x = col * (card_width + border_width * 2) + border_width
                y = row * (card_height + border_width * 2) + border_width
                
                # Get rank color
                rank_id = card.get('cardrank_id', 7)
                border_color = rank_colors.get(rank_id, (128, 128, 128))
                
                # Create border
                border_image = Image.new('RGB', (card_width + border_width * 2, card_height + border_width * 2), border_color)
                
                # Load and resize card image
                card_image_path = self.get_image_path(card['image'])
                if card_image_path and os.path.exists(card_image_path):
                    try:
                        card_img = Image.open(card_image_path)
                        card_img = card_img.resize((card_width, card_height), Image.Resampling.LANCZOS)
                        
                        # Paste card image onto border
                        border_image.paste(card_img, (border_width, border_width))
                    except Exception as e:
                        print(f"Error loading card image {card_image_path}: {e}")
                        # Create placeholder if image fails to load
                        placeholder = Image.new('RGB', (card_width, card_height), (64, 64, 64))
                        border_image.paste(placeholder, (border_width, border_width))
                else:
                    # Create placeholder if no image
                    placeholder = Image.new('RGB', (card_width, card_height), (64, 64, 64))
                    border_image.paste(placeholder, (border_width, border_width))
                
                # Paste the bordered card onto the grid
                grid_image.paste(border_image, (x, y))
            
            # Save to bytes
            img_bytes = io.BytesIO()
            grid_image.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            
            return img_bytes
            
        except Exception as e:
            print(f"Error creating card grid: {e}")
            return None
class AllViewd(discord.ui.View):
    def __init__(self, ctx, cards):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cards = cards
        self.current_page = 0
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await self.update_message(interaction)

    async def update_message(self, interaction):
        embed = self.create_embed()
        files = []
        
        # Handle rank image (thumbnail)
        card = self.cards[self.current_page]
        rank_image_path = self.get_image_path(card['cardrank_image'])
        if rank_image_path and os.path.exists(rank_image_path):
            rank_file = discord.File(rank_image_path, filename="rank_image.png")
            files.append(rank_file)
            embed.set_thumbnail(url="attachment://rank_image.png")

        # Handle card image (main image)
        card_image_path = self.get_image_path(card['image'])
        if card_image_path and os.path.exists(card_image_path):
            card_file = discord.File(card_image_path, filename="card_image.png")
            files.append(card_file)
            embed.set_image(url="attachment://card_image.png")

        if files:
            await interaction.response.edit_message(embed=embed, attachments=files, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        card = self.cards[self.current_page]
        
        # Define embed color based on card rank
        rank_colors = {
            2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
            3: discord.Color.from_rgb(139, 0, 0),  # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),    # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),   # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }
        equipment_details = []
        if card['weapon_id']:
            equipment_details.append(f"{card['weapon_emoji']} Weapon: {card['weapon_name']}\n")
        if card['armor_id']:
            equipment_details.append(f"{card['armor_emoji']} Armor: {card['armor_name']}\n")
        if card['shield_id']:
            equipment_details.append(f"{card['secondshield_emoji']} Shield: {card['shield_name']}\n")
        
        embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))
        role = ""
        if card['type_card'] == "Fighter":
            role = "Offence"
        else:
            role = card['type_card']
        description = (
            f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
            f"**Card Informations**\n"
            f"<:solopower:1302936147139235882> Power : {card['power_stat']}\n"
            f"<:solotype:1302937991869759539> Class : {card['card_class_name']}\n"
            f"<:solorole:1302938003873726526> Role : {role}\n"
            f"{card['level_emoji']} Level : {card['current_level']}/{card['max_level']}\n"
            f"{card['xp_emoji']} XP : {card['current_xp']}/{card['xp_cap']}\n"
        )
        if equipment_details:
            description += ''.join(equipment_details)
        # Add either Effect description or Combat Statistics based on card type
        if card['type_card'].lower() in ['support', 'booster']:
            description += (
                f"**Effect**\n"
                f"{card['description']}"
            )

        else:
            description += (
                f"**Combat Statistics**\n"
                f"<:solosword:1302911919941746730> Attack : {card['attack_stat']}\n"
                f"<:soloheart:1302912058819477514> Health : {card['health_stat']}\n"
                f"<:solostar:1302912104264761374> Speed : {card['speed_stat']}\n"
                f"<:soloshield:1302911930242961461> Defense : {card['defence_stat']}"
            )

        embed = discord.Embed(
            title=f"{card['card_name']}",
            description=description,
            color=embed_color
        )

        # Set the thumbnail image for the initial card
        if self.current_page == 0:
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                embed.set_thumbnail(url="attachment://rank_image.png")

        sl_quotes = [
            "The true hunter never rests.",
            "Your strength defines your fate.",
            "Another card, another step to greatness.",
            "The shadows whisper, will you answer?"
        ]
        
        footer_text = f"{random.choice(sl_quotes)} • Card {self.current_page+1}/{len(self.cards)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()

        return embed

class WeaponView(discord.ui.View):
    def __init__(self, ctx, weapons):
        super().__init__(timeout=900)
        self.ctx = ctx
        self.weapons = weapons
        self.current_page = 0
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.message = None
        self.loading = False

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.loading or interaction.user.id != self.ctx.author.id:
            await interaction.response.defer()
            return

        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.loading or interaction.user.id != self.ctx.author.id:
            await interaction.response.defer()
            return

        if self.current_page < len(self.weapons) - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def update_message(self, interaction):
        if self.loading:
            await interaction.response.defer()
            return

        self.loading = True
        try:
            embed = self.create_embed()
            weapon = self.weapons[self.current_page]
            
            # Handle image
            files = []
            if weapon.get('weapon_image'):
                weapon_image_path = self.get_image_path(weapon['weapon_image'])
                if weapon_image_path and os.path.exists(weapon_image_path):
                    files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                    embed.set_image(url="attachment://weapon_image.png")

            # Try to update the message
            try:
                if not interaction.response.is_done():
                    await interaction.response.edit_message(embed=embed, attachments=files, view=self)
                else:
                    await interaction.message.edit(embed=embed, attachments=files, view=self)
                self.message = interaction.message
            except (discord.NotFound, discord.HTTPException) as e:
                # If the original edit fails, try again with the stored message
                if self.message:
                    try:
                        # Important: Re-create the file object for the second attempt
                        files = []
                        if weapon.get('weapon_image'):
                            weapon_image_path = self.get_image_path(weapon['weapon_image'])
                            if weapon_image_path and os.path.exists(weapon_image_path):
                                files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                                embed.set_image(url="attachment://weapon_image.png")
                        
                        await self.message.edit(embed=embed, attachments=files, view=self)
                    except Exception as e2:
                        print(f"Error in second edit attempt: {e2}")
                        # Last resort: try one final time without clearing attachments
                        await self.message.edit(embed=embed, view=self)

        except Exception as e:
            print(f"Error in update_message: {e}")
            # Recovery attempt with fresh file
            try:
                weapon = self.weapons[self.current_page]
                files = []
                if weapon.get('weapon_image'):
                    weapon_image_path = self.get_image_path(weapon['weapon_image'])
                    if weapon_image_path and os.path.exists(weapon_image_path):
                        files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                        embed.set_image(url="attachment://weapon_image.png")
                
                if self.message:
                    await self.message.edit(embed=embed, attachments=files, view=self)
            except Exception as e2:
                print(f"Error in recovery attempt: {e2}")
        finally:
            self.loading = False

    def create_embed(self):
        weapon = self.weapons[self.current_page]
        
        embed = discord.Embed(
            title=f"⚔️ {weapon['weapon_name']} ⚔️",
            description=weapon['weapon_description'],
            color=discord.Color.from_rgb(75, 0, 130)
        )

        embed.add_field(name="Rank", value=weapon['weaponrank_name'], inline=False)
        
        # Add boost fields
        boosts = []
        if weapon.get('attack_boost') or weapon.get('attack_percentage'):
            attack = []
            if weapon.get('attack_boost'): attack.append(f"+{weapon['attack_boost']}")
            if weapon.get('attack_percentage'): attack.append(f"+{weapon['attack_percentage']}%")
            boosts.append(("Attack Boost", " | ".join(attack)))
            
        if weapon.get('health_boost') or weapon.get('health_percentage'):
            health = []
            if weapon.get('health_boost'): health.append(f"+{weapon['health_boost']}")
            if weapon.get('health_percentage'): health.append(f"+{weapon['health_percentage']}%")
            boosts.append(("Health Boost", " | ".join(health)))
            
        if weapon.get('speed_boost') or weapon.get('speed_percentage'):
            speed = []
            if weapon.get('speed_boost'): speed.append(f"+{weapon['speed_boost']}")
            if weapon.get('speed_percentage'): speed.append(f"+{weapon['speed_percentage']}%")
            boosts.append(("Speed Boost", " | ".join(speed)))
            
        if weapon.get('defense_boost') or weapon.get('defense_percentage'):
            defense = []
            if weapon.get('defense_boost'): defense.append(f"+{weapon['defense_boost']}")
            if weapon.get('defense_percentage'): defense.append(f"+{weapon['defense_percentage']}%")
            boosts.append(("Defense Boost", " | ".join(defense)))

        for name, value in boosts:
            embed.add_field(name=name, value=value, inline=True)

        if 'price' in weapon:
            embed.add_field(name="Price", value=f"{weapon['price']:,}", inline=True)
        if 'quantiter' in weapon:
            embed.add_field(name="Quantity", value=str(weapon['quantiter']), inline=True)

        sl_quotes = [
            "A weapon is only as strong as its wielder.",
            "Choose your weapon wisely, hunter.",
            "Power lies in both skill and equipment.",
            "Every weapon has its purpose."
        ]
        
        footer_text = f"{random.choice(sl_quotes)} • Weapon {self.current_page+1}/{len(self.weapons)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()
        
        return embed

    async def on_timeout(self):
        try:
            if self.message:
                for item in self.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                await self.message.edit(view=self)
        except Exception as e:
            print(f"Error handling timeout: {e}")

class WeaponDisplay:
    def __init__(self, ctx, weapons):
        self.ctx = ctx
        self.weapons = weapons
        self.current_page = 0
        self.message = None
        self.loading = False
        self.reactions = ['⬅️', '➡️']
        self.last_interaction = time.time()
        self.update_lock = asyncio.Lock()
        self.cached_embeds = {}  # Cache for pre-generated embeds
        
    def create_embed(self, weapon_index):
        # Return cached embed if it exists
        if weapon_index in self.cached_embeds:
            return self.cached_embeds[weapon_index]
            
        weapon = self.weapons[weapon_index]
        
        embed = discord.Embed(
            title=f"⚔️ {weapon['weapon_name']} ⚔️",
            description=weapon['weapon_description'],
            color=discord.Color.from_rgb(75, 0, 130)
        )

        embed.add_field(name="Rank", value=weapon['weaponrank_name'], inline=False)
        
        # Add boost fields
        if weapon.get('attack_boost') or weapon.get('attack_percentage'):
            attack_value = []
            if weapon.get('attack_boost'): attack_value.append(f"+{weapon['attack_boost']}")
            if weapon.get('attack_percentage'): attack_value.append(f"+{weapon['attack_percentage']}%")
            embed.add_field(name="Attack Boost", value=" | ".join(attack_value), inline=True)
            
        if weapon.get('health_boost') or weapon.get('health_percentage'):
            health_value = []
            if weapon.get('health_boost'): health_value.append(f"+{weapon['health_boost']}")
            if weapon.get('health_percentage'): health_value.append(f"+{weapon['health_percentage']}%")
            embed.add_field(name="Health Boost", value=" | ".join(health_value), inline=True)
            
        if weapon.get('speed_boost') or weapon.get('speed_percentage'):
            speed_value = []
            if weapon.get('speed_boost'): speed_value.append(f"+{weapon['speed_boost']}")
            if weapon.get('speed_percentage'): speed_value.append(f"+{weapon['speed_percentage']}%")
            embed.add_field(name="Speed Boost", value=" | ".join(speed_value), inline=True)
            
        if weapon.get('defense_boost') or weapon.get('defense_percentage'):
            defense_value = []
            if weapon.get('defense_boost'): defense_value.append(f"+{weapon['defense_boost']}")
            if weapon.get('defense_percentage'): defense_value.append(f"+{weapon['defense_percentage']}%")
            embed.add_field(name="Defense Boost", value=" | ".join(defense_value), inline=True)

        if 'price' in weapon:
            embed.add_field(name="Price", value=f"{weapon['price']:,}", inline=True)
        if 'quantiter' in weapon:
            embed.add_field(name="Quantity", value=str(weapon['quantiter']), inline=True)

        sl_quotes = [
            "A weapon is only as strong as its wielder.",
            "Choose your weapon wisely, hunter.",
            "Power lies in both skill and equipment.",
            "Every weapon has its purpose."
        ]
        
        footer_text = f"{random.choice(sl_quotes)} • Weapon {weapon_index+1}/{len(self.weapons)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()
        
        if weapon.get('weapon_image'):
            embed.set_image(url=weapon['weapon_image'])
        
        # Cache the embed
        self.cached_embeds[weapon_index] = embed
        return embed

    async def preload_adjacent_embeds(self):
        """Preload embeds for adjacent pages"""
        indices_to_cache = []
        if self.current_page > 0:
            indices_to_cache.append(self.current_page - 1)
        if self.current_page < len(self.weapons) - 1:
            indices_to_cache.append(self.current_page + 1)
            
        for idx in indices_to_cache:
            if idx not in self.cached_embeds:
                self.create_embed(idx)

    async def add_reactions(self):
        for reaction in self.reactions:
            await self.message.add_reaction(reaction)

    async def update_message(self):
        async with self.update_lock:
            if self.loading:
                return

            self.loading = True
            try:
                embed = self.create_embed(self.current_page)
                await self.message.edit(embed=embed)
                
                # Preload adjacent embeds after updating
                await self.preload_adjacent_embeds()
                
                self.last_interaction = time.time()
            except Exception as e:
                print(f"Error in update_message: {e}")
            finally:
                self.loading = False

    async def start(self):
        # Preload first few embeds
        self.create_embed(0)
        if len(self.weapons) > 1:
            self.create_embed(1)
            
        embed = self.cached_embeds[0]
        self.message = await self.ctx.send(embed=embed)
        await self.add_reactions()

        def check(reaction, user):
            return (
                user == self.ctx.author 
                and str(reaction.emoji) in self.reactions 
                and reaction.message.id == self.message.id
            )

        while True:
            try:
                reaction, user = await self.ctx.bot.wait_for('reaction_add', timeout=900.0, check=check)
                
                # Remove the user's reaction
                await reaction.remove(user)

                # Update page based on reaction
                if str(reaction.emoji) == '⬅️' and self.current_page > 0:
                    self.current_page -= 1
                    await self.update_message()
                elif str(reaction.emoji) == '➡️' and self.current_page < len(self.weapons) - 1:
                    self.current_page += 1
                    await self.update_message()

            except asyncio.TimeoutError:
                # Remove all reactions when the view times out
                await self.message.clear_reactions()
                break

class StatSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Attack", value="attack", description="Adjust Attack stat", emoji="⚔️"),
            discord.SelectOption(label="Defence", value="defence", description="Adjust Defence stat", emoji="🛡️"),
            discord.SelectOption(label="Speed", value="speed", description="Adjust Speed stat", emoji="💨"),
            discord.SelectOption(label="Health", value="health", description="Adjust Health stat", emoji="❤️")
        ]
        super().__init__(
            placeholder="Choose a stat to adjust...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="stat_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view: StatAdjustView = self.view
        await view.on_stat_select(interaction, self.values[0])

class StatAdjustView(discord.ui.View):
    def __init__(self, ctx, card, original_view, bot_instance):
        super().__init__(timeout=720)  # Set timeout to 12 minutes
        self.ctx = ctx
        self.card = card
        self.original_view = original_view
        self.bot = bot_instance  # Store the bot instance
        self.stat_to_adjust = None
        self.adjustments = {'attack': 0, 'defence': 0, 'speed': 0, 'health': 0}
        self.original_statpoints = card['current_statpoints']
        self.embed_color = self.get_embed_color(card.get('cardrank_id'))
        self.message = None
        self.files = []
        self.initialize_files()
        # Remove all items first
        self.clear_items()
        
        # Add StatSelect first
        self.add_item(StatSelect())
        
        # Add all other buttons after the select
        self.add_item(self.add_all_button)
        self.add_item(self.increase_button)
        self.add_item(self.decrease_button)
        self.add_item(self.reset_button)
        self.add_item(self.go_back_button)
        self.add_item(self.confirm_button)

    def get_embed_color(self, rank_id):
        rank_colors = {
            2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
            3: discord.Color.from_rgb(139, 0, 0),  # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),    # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),   # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }
        return rank_colors.get(rank_id, discord.Color.from_rgb(75, 0, 130))

    def initialize_files(self):
        try:
            # Correctly access image, prioritizing pseudo image
            img = self.card.get('pseudo_image') or self.card.get('image', '')
            
            image_path = os.path.join(os.path.dirname(__file__), '..', 'images', img)
            print(f"Attempting to load card image: {image_path}")  # Debug print
            
            if img and os.path.exists(image_path):
                self.files.append(discord.File(image_path, filename="card_image.png"))
                self.card_image_url = "attachment://card_image.png"
            else:
                print(f"Card image not found: {img}")  # Debug print
                self.card_image_url = None

            # Rank image handling
            rank_img = self.card.get('cardrank_image', '')
            rank_image_path = os.path.join(os.path.dirname(__file__), '..', 'images', rank_img)
            print(f"Attempting to load rank image: {rank_image_path}")  # Debug print
            
            if rank_img and os.path.exists(rank_image_path):
                self.files.append(discord.File(rank_image_path, filename="rank_image.png"))
                self.rank_image_url = "attachment://rank_image.png"
            else:
                print(f"Rank image not found: {rank_img}")  # Debug print
                self.rank_image_url = None
        except Exception as e:
            print(f"Error initializing files: {e}")
            self.card_image_url = None
            self.rank_image_url = None

    def create_embed(self):
        try:
            equipment_details = []
            if self.card['weapon_id']:
                equipment_details.append(f"{self.card['weapon_emoji']} Weapon: {self.card['weapon_name']}\n")
            if self.card['armor_id']:
                equipment_details.append(f"{self.card['armor_emoji']} Armor: {self.card['armor_name']}\n")
            if self.card['shield_id']:
                equipment_details.append(f"{self.card['secondshield_emoji']} Shield: {self.card['shield_name']}\n")
            role = ""
            if self.card['type_card'] == "Fighter":
                role = "Offence"
            else:
                role = self.card['type_card']
            description = (
                f"Adjusting {self.stat_to_adjust.capitalize() if self.stat_to_adjust else 'No stat selected'}\n\n"
                f"**Card Informations**\n"
                f"{self.card['power_emoji']} Power : {self.card['power_stat']}\n"
                f"{self.card['type_emoji']} Class : {self.card['card_class_name']}\n"
                f"{self.card['class_emoji']} Role : {role}\n"
                f"{self.card['level_emoji']} Level : {self.card['current_level']}/{self.card['max_level']}\n"
                f"{self.card['xp_emoji']} XP : {self.card['current_xp']}/{self.card['xp_cap']}\n"
            )
            if equipment_details:
                description += ''.join(equipment_details)
            description += f"\n**Combat Statistics**\n"
            stats = {
                'attack': (self.card['attack_emoji'], self.card['attack_stat']),
                'health': (self.card['health_emoji'], self.card['health_stat']),
                'speed': (self.card['speed_emoji'], self.card['speed_stat']),
                'defence': (self.card['defense_emoji'], self.card['defence_stat'])
            }

            for stat, (emoji, base_value) in stats.items():
                current_value = base_value + self.adjustments[stat]
                adjustment_text = f" (+{self.adjustments[stat]})" if self.adjustments[stat] > 0 else f" ({self.adjustments[stat]})" if self.adjustments[stat] < 0 else ""
                description += f"{emoji} {stat.capitalize()} : {current_value}{adjustment_text}\n"

            description += f"\nAvailable Stat Points: {self.card['current_statpoints']}"
            name = self.card['card_name']  # Default to card_name
            if self.card['pseudo_name'] != "":
                # If pseudo_name exists, format it with the original card name in brackets
                name = f"{self.card['pseudo_name']} [{self.card['card_name']}]"
            embed = discord.Embed(
                title=name,
                description=description,
                color=self.embed_color
            )

            if self.card_image_url:
                embed.set_image(url=self.card_image_url)
            if self.rank_image_url:
                embed.set_thumbnail(url=self.rank_image_url)

            sl_quotes = [
                "The true hunter never rests.",
                "Your strength defines your fate.",
                "Another step towards greatness.",
                "The shadows whisper, will you answer?"
            ]
            
            embed.set_footer(text=random.choice(sl_quotes))
            embed.timestamp = discord.utils.utcnow()

            return embed
        except Exception as e:
            print(f"Error creating embed: {e}")
            return discord.Embed(title="Error", description="An error occurred while creating the embed.")

    async def on_stat_select(self, interaction, stat):
        try:
            self.stat_to_adjust = stat
            await self.update_view(interaction)
        except Exception as e:
            print(f"Error in stat select: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="Add All", style=discord.ButtonStyle.green)
    async def add_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not self.stat_to_adjust:
                await interaction.response.send_message("Please select a stat first!", ephemeral=True)
                return
            
            if self.card['current_statpoints'] > 0:
                # Add all available stat points to the selected stat
                points_to_add = self.card['current_statpoints']
                self.adjustments[self.stat_to_adjust] += points_to_add
                self.card['current_statpoints'] = 0
                await self.update_view(interaction)
            else:
                await interaction.response.send_message("No available stat points.", ephemeral=True)
        except Exception as e:
            print(f"Error in add all button: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="+", style=discord.ButtonStyle.green)
    async def increase_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not self.stat_to_adjust:
                await interaction.response.send_message("Please select a stat first!", ephemeral=True)
                return
            
            if self.card['current_statpoints'] > 0:
                self.adjustments[self.stat_to_adjust] += 1
                self.card['current_statpoints'] -= 1
                await self.update_view(interaction)
            else:
                await interaction.response.send_message("No available stat points.", ephemeral=True)
        except Exception as e:
            print(f"Error in increase button: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="-", style=discord.ButtonStyle.red)
    async def decrease_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not self.stat_to_adjust:
                await interaction.response.send_message("Please select a stat first!", ephemeral=True)
                return
            
            if self.adjustments[self.stat_to_adjust] > 0:
                self.adjustments[self.stat_to_adjust] -= 1
                self.card['current_statpoints'] += 1
                await self.update_view(interaction)
            else:
                await interaction.response.send_message("Cannot decrease stat further.", ephemeral=True)
        except Exception as e:
            print(f"Error in decrease button: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.gray)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.adjustments = {stat: 0 for stat in self.adjustments}
            self.card['current_statpoints'] = self.original_statpoints
            await self.update_view(interaction)
        except Exception as e:
            print(f"Error in reset button: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="Go Back", style=discord.ButtonStyle.gray)
    async def go_back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Reset stats to original state if there are unconfirmed changes
            if sum(self.adjustments.values()) > 0:
                # Reset adjustments
                self.adjustments = {stat: 0 for stat in self.adjustments}
                # Restore original stat points
                self.card['current_statpoints'] = self.original_statpoints
                
                # Update the view one last time to show the reset state
                embed = self.create_embed()
                await interaction.response.edit_message(embed=embed, view=self.original_view)
            else:
                # If no changes were made, just return to original view
                await interaction.response.edit_message(view=self.original_view)
            
            # Clean up entry in stat_status table
            await self.remove_stat_status_entry()
            self.stop()
        except Exception as e:
            print(f"Error in go back button: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.blurple)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Disable all buttons during confirmation
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            
            # Create confirmation view
            confirm_view = ConfirmView()
            confirm_message = await interaction.followup.send("Are you sure you want to apply these stat changes?", view=confirm_view, ephemeral=True)
            
            # Wait for confirmation
            await confirm_view.wait()
            
            if confirm_view.value is None:
                # Re-enable buttons if confirmation times out
                for item in self.children:
                    item.disabled = False
                await interaction.edit_original_response(view=self)
                await interaction.followup.send("Confirmation timed out. Changes not applied.", ephemeral=True)
            elif confirm_view.value:
                # Store the original stats before updating
                original_stats = {
                    'attack': self.card['attack_stat'],
                    'defence': self.card['defence_stat'],
                    'speed': self.card['speed_stat'],
                    'health': self.card['health_stat'],
                    'power': self.card['power_stat']
                }
                
                # Update the database and get updated stats
                updated_stats = await self.update_database()
                
                # Clean up entry in stat_status table
                await self.remove_stat_status_entry()
                
                # Create a summary of the changes
                stat_changes = []
                for stat in ['attack', 'defence', 'speed', 'health']:
                    if self.adjustments[stat] > 0:
                        old_value = original_stats[stat]
                        new_value = updated_stats[stat]
                        stat_changes.append(f"{stat.capitalize()}: {old_value} → {new_value} (+{self.adjustments[stat]})")
                
                # Add power stat change
                old_power = original_stats['power']
                new_power = updated_stats['power']
                power_change = new_power - old_power
                stat_changes.append(f"Power: {old_power} → {new_power} (+{power_change})")
                
                # Create a success message with stat changes
                success_message = "Stats updated successfully!\n\n**Changes Applied:**\n" + "\n".join(stat_changes)
                await interaction.followup.send(success_message, ephemeral=True)
                
                # Check if the original view is a CardView (for mycards command)
                if isinstance(self.original_view, CardView) and hasattr(self.original_view, 'cards'):
                    # We're in the mycards command
                    self.original_view.cards[self.original_view.current_page] = self.card
                    embed = self.original_view.create_embed()
                    files = []
                    
                    # Handle rank image
                    rank_image_path = self.original_view.get_image_path(self.card.get('cardrank_image'))
                    if rank_image_path and os.path.exists(rank_image_path):
                        rank_file = discord.File(rank_image_path, filename="rank_image.png")
                        files.append(rank_file)
                        embed.set_thumbnail(url="attachment://rank_image.png")
                    
                    # Handle card image
                    img = self.card['image']
                    if self.card.get('pseudo_image') and self.card['pseudo_image'] != "":
                        img = self.card['pseudo_image']
                    card_image_path = self.original_view.get_image_path(img)
                    if card_image_path and os.path.exists(card_image_path):
                        card_file = discord.File(card_image_path, filename="card_image.png")
                        files.append(card_file)
                        embed.set_image(url="attachment://card_image.png")
                    
                    # Update message with original view (with navigation)
                    if files:
                        await interaction.message.edit(embed=embed, attachments=files, view=self.original_view)
                    else:
                        await interaction.message.edit(embed=embed, view=self.original_view)
                else:
                    # We're in the cardview command
                    # Create new single-card view with only stats button
                    class SingleCardView(discord.ui.View):
                        def __init__(self, ctx, card, bot_instance):
                            super().__init__(timeout=180)
                            self.ctx = ctx
                            self.card = card
                            self.bot = bot_instance

                            # Disable stats button for Support cards
                            self.stats_button.disabled = card['type_card'] in ['Support', 'Booster']

                        @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
                        async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            if interaction.user.id != self.ctx.author.id:
                                await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
                                return
                            stat_view = StatAdjustView(self.ctx, self.card, self, self.bot)
                            await interaction.response.edit_message(view=stat_view)
                    
                    single_view = SingleCardView(self.ctx, self.card, self.bot)
                    
                    # Create an updated embed with the latest stats
                    embed = discord.Embed(
                        title=self.card['card_name'] if not self.card.get('pseudo_name') else f"{self.card['pseudo_name']} [{self.card['card_name']}]",
                        description=self.create_description_with_updated_stats(),
                        color=self.embed_color
                    )
                    
                    files = []
                    
                    # Handle rank image
                    rank_image_path = os.path.join(os.path.dirname(__file__), '..', 'images', self.card.get('cardrank_image', ''))
                    if os.path.exists(rank_image_path):
                        rank_file = discord.File(rank_image_path, filename="rank_image.png")
                        files.append(rank_file)
                        embed.set_thumbnail(url="attachment://rank_image.png")
                    
                    # Handle card image
                    img = self.card['image']
                    if self.card.get('pseudo_image') and self.card['pseudo_image'] != "":
                        img = self.card['pseudo_image']
                    card_image_path = os.path.join(os.path.dirname(__file__), '..', 'images', img)
                    if os.path.exists(card_image_path):
                        card_file = discord.File(card_image_path, filename="card_image.png")
                        files.append(card_file)
                        embed.set_image(url="attachment://card_image.png")
                    
                    # Add footer
                    sl_quotes = [
                        "The true hunter never rests.",
                        "Your strength defines your fate.",
                        "Another step towards greatness.",
                        "The shadows whisper, will you answer?"
                    ]
                    embed.set_footer(text=random.choice(sl_quotes))
                    embed.timestamp = discord.utils.utcnow()
                    
                    # Update message with single card view
                    if files:
                        await interaction.message.edit(embed=embed, attachments=files, view=single_view)
                    else:
                        await interaction.message.edit(embed=embed, view=single_view)
            else:
                # Re-enable buttons if user cancels
                for item in self.children:
                    item.disabled = False
                await interaction.edit_original_response(view=self)
                await interaction.followup.send("Changes cancelled.", ephemeral=True)
                # Clean up entry in stat_status table even if changes are cancelled
                await self.remove_stat_status_entry()
        except Exception as e:
            print(f"Error in confirm button: {e}")
            import traceback
            traceback.print_exc()
            # Attempt to clean up the database entry even if there's an error
            try:
                await self.remove_stat_status_entry()
            except:
                pass
            await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)

    async def refresh_card_stats(self, user_id, card_id):
        """
        Refresh a card's stats when equipment or team membership changes
        """
        try:
            # Fetch the latest card data
            card_query = """
            SELECT * FROM user_cards
            WHERE user_id = %s AND card_id = %s
            """
            card_data = await execute_query_with_retry(card_query, (user_id, card_id))
            
            if not card_data:
                return
                
            card = card_data[0]
            
            # Check if card has equipment
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None
            equipment_boosts = None
            
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card_id)
            
            # Check if card is in a team with support cards
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            
            has_support_in_team = False
            support_boosts = None
            
            if team_result:
                team_id = team_result[0]['team_id']
                
                # Check if the card is in the team
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, card_id))
                
                if team_card_result:
                    # Check if there are support cards in the team
                    support_query = """
                    SELECT COUNT(*) as support_count
                    FROM team_cards tc
                    JOIN card c ON tc.card_id = c.card_id
                    WHERE tc.team_id = %s AND c.type_card = 'Support'
                    """
                    support_result = await execute_query_with_retry(support_query, (team_id,))
                    
                    if support_result and support_result[0]['support_count'] > 0:
                        has_support_in_team = True
                        support_boosts = await self.calculate_total_support_boosts(team_id, user_id)
            
            # Calculate true base stats by removing equipment boosts
            true_base_stats = {
                'attack': float(card['base_attack']),
                'health': float(card['base_health']),
                'defence': float(card['base_defence']),
                'speed': float(card['base_speed'])
            }
            
            # Remove equipment boosts if present
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
            
            # Calculate new base stats with equipment boosts re-applied
            new_base_stats = dict(true_base_stats)
            
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
            
            # Round base stats
            for stat in new_base_stats:
                new_base_stats[stat] = int(round(new_base_stats[stat]))
                new_base_stats[stat] = max(0, new_base_stats[stat])
            
            # Calculate new current stats (with all boosts)
            new_current_stats = dict(true_base_stats)
            
            # Apply equipment boosts if present
            if equipment_boosts:
                # Apply flat boosts first
                new_current_stats['attack'] += equipment_boosts['flat_attack']
                new_current_stats['health'] += equipment_boosts['flat_health']
                new_current_stats['defence'] += equipment_boosts['flat_defence']
                new_current_stats['speed'] += equipment_boosts['flat_speed']
                
                # Then apply percentage boosts
                new_current_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                new_current_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                new_current_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                new_current_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
            
            # Apply support boosts if present
            if support_boosts:
                # Apply flat boosts first
                new_current_stats['attack'] += support_boosts['flat_attack']
                new_current_stats['health'] += support_boosts['flat_health']
                new_current_stats['defence'] += support_boosts['flat_defence']
                new_current_stats['speed'] += support_boosts['flat_speed']
                
                # Then apply percentage boosts
                new_current_stats['attack'] *= (1 + support_boosts['pct_attack']/100)
                new_current_stats['health'] *= (1 + support_boosts['pct_health']/100)
                new_current_stats['defence'] *= (1 + support_boosts['pct_defence']/100)
                new_current_stats['speed'] *= (1 + support_boosts['pct_speed']/100)
            
            # Round current stats
            for stat in new_current_stats:
                new_current_stats[stat] = int(round(new_current_stats[stat]))
                new_current_stats[stat] = max(0, new_current_stats[stat])
            
            # Calculate new power stat
            new_power = new_current_stats['attack'] + new_current_stats['health'] + new_current_stats['defence'] + new_current_stats['speed']
            
            # Update the database with the new values
            update_query = """
            UPDATE user_cards 
            SET 
                base_attack = %s,
                base_health = %s,
                base_defence = %s,
                base_speed = %s,
                attack_stat = %s,
                health_stat = %s,
                defence_stat = %s,
                speed_stat = %s,
                power_stat = %s
            WHERE user_id = %s AND card_id = %s
            """
            
            await execute_query_with_retry(update_query, (
                new_base_stats['attack'],
                new_base_stats['health'],
                new_base_stats['defence'],
                new_base_stats['speed'],
                new_current_stats['attack'],
                new_current_stats['health'],
                new_current_stats['defence'],
                new_current_stats['speed'],
                new_power,
                user_id,
                card_id
            ))
            
            return {
                'attack': new_current_stats['attack'],
                'health': new_current_stats['health'],
                'defence': new_current_stats['defence'],
                'speed': new_current_stats['speed'],
                'power': new_power
            }
        except Exception as e:
            print(f"Error refreshing card stats: {e}")
            import traceback
            traceback.print_exc()
            return None

    def create_description_with_updated_stats(self):
        """Create a description with the latest stats for the embed"""
        equipment_details = []
        if self.card['weapon_id']:
            equipment_details.append(f"{self.card['weapon_emoji']} Weapon: {self.card['weapon_name']}\n")
        if self.card['armor_id']:
            equipment_details.append(f"{self.card['armor_emoji']} Armor: {self.card['armor_name']}\n")
        if self.card['shield_id']:
            equipment_details.append(f"{self.card['secondshield_emoji']} Shield: {self.card['shield_name']}\n")
        
        role = "Offence" if self.card['type_card'] == "Fighter" else self.card['type_card']
        
        description = (
            f"A {self.card['cardrank_name']}-Rank card, Summoner!\n\n"
            f"**Card Informations**\n"
            f"{self.card['power_emoji']} Power : {self.card['power_stat']}\n"
            f"{self.card['type_emoji']} Class : {self.card['card_class_name']}\n"
            f"{self.card['class_emoji']} Role : {role}\n"
            f"{self.card['level_emoji']} Level : {self.card['current_level']}/{self.card['max_level']}\n"
            f"{self.card['xp_emoji']} XP : {self.card['current_xp']}/{self.card['xp_cap']}\n"
        )
        
        if equipment_details:
            description += ''.join(equipment_details)
        
        description += (
            f"\n**Combat Statistics**\n"
            f"{self.card['attack_emoji']} Attack : {self.card['attack_stat']}\n"
            f"{self.card['health_emoji']} Health : {self.card['health_stat']}\n"
            f"{self.card['speed_emoji']} Speed : {self.card['speed_stat']}\n"
            f"{self.card['defense_emoji']} Defense : {self.card['defence_stat']}\n\n"
            f"Available Stat Points: {self.card['current_statpoints']}"
        )
        
        return description


    async def update_view(self, interaction):
        try:
            embed = self.create_embed()
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"Error updating view: {e}")
            try:
                await interaction.followup.send(embed=embed, view=self)
            except:
                pass

    async def update_database(self):
        try:
            stats_used = sum(self.adjustments.values())
            
            # Fetch the latest card data to ensure we're working with current values
            card_query = """
            SELECT * FROM user_cards
            WHERE user_id = %s AND card_id = %s
            """
            latest_card = await execute_query_with_retry(card_query, (self.card['user_id'], self.card['card_id']))
            
            if not latest_card:
                raise Exception("Card not found in database")
            
            # Update our local card data with the latest from the database
            for key, value in latest_card[0].items():
                if key in self.card:
                    self.card[key] = value
            
            # Check if card has equipment
            has_equipment = self.card['weapon_id'] is not None or self.card['armor_id'] is not None or self.card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(self.card['user_id'], self.card['card_id'])

            # Check if card is in a team with support cards
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (self.card['user_id'],))
            
            has_support_in_team = False
            team_id = None
            support_boosts = None
            
            if team_result:
                team_id = team_result[0]['team_id']
                
                # Check if the card is in the team
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, self.card['card_id']))
                
                if team_card_result:
                    # Check if there are support cards in the team
                    support_query = """
                    SELECT COUNT(*) as support_count
                    FROM team_cards tc
                    JOIN card c ON tc.card_id = c.card_id
                    WHERE tc.team_id = %s AND c.type_card = 'Support'
                    """
                    support_result = await execute_query_with_retry(support_query, (team_id,))
                    
                    if support_result and support_result[0]['support_count'] > 0:
                        has_support_in_team = True
                        support_boosts = await self.calculate_total_support_boosts(team_id, self.card['user_id'])

            # Store original stats for comparison
            original_stats = {
                'attack': self.card['attack_stat'],
                'health': self.card['health_stat'],
                'defence': self.card['defence_stat'],
                'speed': self.card['speed_stat'],
                'power': self.card['power_stat'],
                'base_attack': self.card['base_attack'],
                'base_health': self.card['base_health'],
                'base_defence': self.card['base_defence'],
                'base_speed': self.card['base_speed']
            }

            # Log the current state for debugging
            print(f"Card {self.card['card_id']} before update:")
            print(f"  Base stats: ATK={original_stats['base_attack']}, DEF={original_stats['base_defence']}, SPD={original_stats['base_speed']}, HP={original_stats['base_health']}")
            print(f"  Current stats: ATK={original_stats['attack']}, DEF={original_stats['defence']}, SPD={original_stats['speed']}, HP={original_stats['health']}")
            print(f"  Has equipment: {has_equipment}, Has support: {has_support_in_team}")
            print(f"  Adjustments: {self.adjustments}")

            # STEP 1: Calculate true base stats by removing equipment boosts
            true_base_stats = {
                'attack': float(self.card['base_attack']),
                'health': float(self.card['base_health']),
                'defence': float(self.card['base_defence']),
                'speed': float(self.card['base_speed'])
            }
            
            # Remove equipment boosts if present to get true base stats
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
            
            # STEP 2: Apply stat adjustments to true base stats
            new_true_base_stats = {
                'attack': max(0, true_base_stats['attack'] + self.adjustments['attack']),
                'health': max(0, true_base_stats['health'] + self.adjustments['health']),
                'defence': max(0, true_base_stats['defence'] + self.adjustments['defence']),
                'speed': max(0, true_base_stats['speed'] + self.adjustments['speed'])
            }
            
            # Round values to integers
            for stat in new_true_base_stats:
                new_true_base_stats[stat] = int(round(new_true_base_stats[stat]))
            
            # STEP 3: Calculate new base stats with equipment boosts re-applied
            new_base_stats = dict(new_true_base_stats)
            
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
            
            # Round base stats
            for stat in new_base_stats:
                new_base_stats[stat] = int(round(new_base_stats[stat]))
                new_base_stats[stat] = max(0, new_base_stats[stat])
            
            # STEP 4: Calculate new current stats (with all boosts)
            new_current_stats = dict(new_true_base_stats)
            
            # Apply equipment boosts if present
            if equipment_boosts:
                # Apply flat boosts first
                new_current_stats['attack'] += equipment_boosts['flat_attack']
                new_current_stats['health'] += equipment_boosts['flat_health']
                new_current_stats['defence'] += equipment_boosts['flat_defence']
                new_current_stats['speed'] += equipment_boosts['flat_speed']
                
                # Then apply percentage boosts
                new_current_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                new_current_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                new_current_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                new_current_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
            
            # Apply support boosts if present
            if support_boosts:
                # Apply flat boosts first
                new_current_stats['attack'] += support_boosts['flat_attack']
                new_current_stats['health'] += support_boosts['flat_health']
                new_current_stats['defence'] += support_boosts['flat_defence']
                new_current_stats['speed'] += support_boosts['flat_speed']
                
                # Then apply percentage boosts
                new_current_stats['attack'] *= (1 + support_boosts['pct_attack']/100)
                new_current_stats['health'] *= (1 + support_boosts['pct_health']/100)
                new_current_stats['defence'] *= (1 + support_boosts['pct_defence']/100)
                new_current_stats['speed'] *= (1 + support_boosts['pct_speed']/100)
            
            # Round current stats
            for stat in new_current_stats:
                new_current_stats[stat] = int(round(new_current_stats[stat]))
                new_current_stats[stat] = max(0, new_current_stats[stat])
            
            # Calculate new power stat
            new_power = new_current_stats['attack'] + new_current_stats['health'] + new_current_stats['defence'] + new_current_stats['speed']
            
            # STEP 5: Update the database with all new values
            update_query = """
            UPDATE user_cards 
            SET 
                -- Base stats (with equipment boosts)
                base_attack = %s,
                base_health = %s,
                base_defence = %s,
                base_speed = %s,
                
                -- Current stats (with all boosts)
                attack_stat = %s,
                health_stat = %s,
                defence_stat = %s,
                speed_stat = %s,
                power_stat = %s,
                
                -- Stat points tracking
                current_statpoints = current_statpoints - %s,
                used_statpoints = used_statpoints + %s,
                used_attackpoints = used_attackpoints + %s,
                used_defencepoints = used_defencepoints + %s,
                used_speedpoints = used_speedpoints + %s,
                used_healthpoints = used_healthpoints + %s
            WHERE user_id = %s AND card_id = %s
            """
            
            await execute_query_with_retry(update_query, (
                new_base_stats['attack'],
                new_base_stats['health'],
                new_base_stats['defence'],
                new_base_stats['speed'],
                
                new_current_stats['attack'],
                new_current_stats['health'],
                new_current_stats['defence'],
                new_current_stats['speed'],
                new_power,
                
                stats_used,
                stats_used,
                self.adjustments['attack'],
                self.adjustments['defence'],
                self.adjustments['speed'],
                self.adjustments['health'],
                
                self.card['user_id'],
                self.card['card_id']
            ))
            
            # Fetch updated stats from database to ensure accuracy
            updated_stats_query = """
            SELECT attack_stat, defence_stat, speed_stat, health_stat, power_stat, 
                base_attack, base_defence, base_speed, base_health,
                current_statpoints, used_statpoints, used_attackpoints, 
                used_defencepoints, used_speedpoints, used_healthpoints
            FROM user_cards
            WHERE user_id = %s AND card_id = %s
            """
            updated_stats = await execute_query_with_retry(updated_stats_query, (self.card['user_id'], self.card['card_id']))
            
            if updated_stats:
                # Update card stats in memory
                self.card['attack_stat'] = updated_stats[0]['attack_stat']
                self.card['defence_stat'] = updated_stats[0]['defence_stat']
                self.card['speed_stat'] = updated_stats[0]['speed_stat']
                self.card['health_stat'] = updated_stats[0]['health_stat']
                self.card['power_stat'] = updated_stats[0]['power_stat']
                self.card['base_attack'] = updated_stats[0]['base_attack']
                self.card['base_defence'] = updated_stats[0]['base_defence']
                self.card['base_speed'] = updated_stats[0]['base_speed']
                self.card['base_health'] = updated_stats[0]['base_health']
                self.card['current_statpoints'] = updated_stats[0]['current_statpoints']
                self.card['used_statpoints'] = updated_stats[0]['used_statpoints']
                self.card['used_attackpoints'] = updated_stats[0]['used_attackpoints']
                self.card['used_defencepoints'] = updated_stats[0]['used_defencepoints']
                self.card['used_speedpoints'] = updated_stats[0]['used_speedpoints']
                self.card['used_healthpoints'] = updated_stats[0]['used_healthpoints']
            
            # Log the updated state for debugging
            print(f"Card {self.card['card_id']} after update:")
            print(f"  Base stats: ATK={self.card['base_attack']}, DEF={self.card['base_defence']}, SPD={self.card['base_speed']}, HP={self.card['base_health']}")
            print(f"  Current stats: ATK={self.card['attack_stat']}, DEF={self.card['defence_stat']}, SPD={self.card['speed_stat']}, HP={self.card['health_stat']}")
            
            # Return the updated stats for display
            return {
                'attack': self.card['attack_stat'],
                'health': self.card['health_stat'],
                'defence': self.card['defence_stat'],
                'speed': self.card['speed_stat'],
                'power': self.card['power_stat']
            }
        except Exception as e:
            print(f"Error updating database: {e}")
            import traceback
            traceback.print_exc()
            raise

    # Helper methods needed for the improved update_database function
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

        # Check each equipment type - all from the weapon table
        equipment_ids = [
            equipment['weapon_id'],
            equipment['armor_id'],
            equipment['shield_id']
        ]

        for eq_id in equipment_ids:
            if eq_id:
                # Query equipment details from the weapon table
                query = """
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
            
            # Add equipment boosts - all from weapon table
            equipment_ids = [
                support['weapon_id'],
                support['armor_id'],
                support['shield_id']
            ]
            
            for eq_id in equipment_ids:
                if eq_id:
                    # Query equipment details from the weapon table
                    query = """
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


    # Fixed helper methods to use the weapon table for all equipment types

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


    @commands.command(name="addcard")
    @commands.has_permissions(administrator=True)
    async def add_card(self, ctx, *, args):
        """
        Admin command to add a card to a user's collection.
        Usage: sl addcard <card_name>,<discord_id>
        """
        try:
            # Parse arguments
            if ',' not in args:
                await ctx.send("❌ Invalid format. Use: sl addcard <card_name>,<discord_id>")
                return
                
            card_name, discord_id = args.split(',', 1)
            card_name = card_name.strip()
            discord_id = discord_id.strip()
            
            # Validate discord_id is a number
            try:
                discord_id = int(discord_id)
            except ValueError:
                await ctx.send("❌ Discord ID must be a number.")
                return
            
            # Check if card exists in database
            card_query = """
                SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
                FROM card c
                JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                JOIN card_class cc ON c.card_class_id = cc.card_class_id
                WHERE c.card_name LIKE %s
            """
            card_results = await execute_query_with_retry(card_query, (f"%{card_name}%",))
            
            if not card_results:
                await ctx.send(f"❌ Card '{card_name}' not found in the database.")
                return
                
            if len(card_results) > 1:
                # Multiple cards found, list them
                card_names = [f"• {card['card_name']}" for card in card_results]
                await ctx.send(f"⚠️ Multiple cards found matching your description. Please be more specific:\\n{chr(10).join(card_names)}")
                return
                
            # Get the card
            card = card_results[0]
            
            # Get user_id from discord_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(discord_id),))
            
            if not user_result:
                await ctx.send(f"❌ User with Discord ID {discord_id} not found in the database.")
                return
                
            user_id = user_result[0]['user_id']
            
            # Check if user already has the card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, card['card_id']))
            
            if user_card_result:
                await ctx.send(f"⚠️ User already has the card '{card['card_name']}'.")
                return
                
            # Add card to user's collection (similar to arise command)
            await execute_query_with_retry("""
                INSERT INTO user_cards (user_id, card_id, current_level, current_xp, 
                current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name, 
                defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                power_emoji, type_emoji, class_emoji) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, card['card_id'], card['min_level'], 0, 0,
                card['power'], card['attack'], card['health'], 
                card['speed'], card['defence'], card['attack'], card['health'], 
                card['speed'], card['card_name'], card['defence'],
                '<:solosword:1302911919941746730>', '<:soloshield:1302911930242961461>',
                '<:solostar:1302912104264761374>', '<:soloheart:1302912058819477514>',
                '<:soloweapon:1302961287747342356>', '<:solosecondshield:1302959829413007380>',
                '<:armor64:1311994697794850897>', '<:soloxp:1302960018747822080>',
                '<:sololevel:1302959449220321350>', '<:solopower:1302936147139235882>',
                '<:solorole:1302938003873726526>', '<:solotype:1302937991869759539>'
            ))
            
            # Handle special cards with additional effects
            if card['card_id'] == 103:
                await execute_query_with_retry(
                    "UPDATE user SET roll_cap = roll_cap + 1 WHERE user_id = %s", 
                    (user_id,)
                )
            if card['card_id'] == 104:
                await execute_query_with_retry(
                    "UPDATE user SET fragment_cap = fragment_cap + 100 WHERE user_id = %s", 
                    (user_id,)
                )
                
            await ctx.send(f"✅ Successfully added card '{card['card_name']}' to user with Discord ID {discord_id}.")
            
        except Exception as e:
            logging.error(f"Error in addcard command: {str(e)}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    async def remove_stat_status_entry(self):
        """Remove the entry from stat_status table when done with adjustments"""
        try:
            delete_query = "DELETE FROM stat_status WHERE user_id = %s AND card_id = %s"
            await execute_query_with_retry(delete_query, (self.card['user_id'], self.card['card_id']))
        except Exception as e:
            print(f"Error removing stat_status entry: {e}")
            raise

    async def on_timeout(self):
        try:
            if self.message:
                await self.message.edit(view=None)
            # Clean up entry in stat_status table if view times out
            await self.remove_stat_status_entry()
        except:
            pass
        self.stop()

        
class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.value = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        # Disable buttons after selection
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        # Disable buttons after selection
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class CardView(discord.ui.View):
    def __init__(self, ctx, cards, bot_instance):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cards = cards
        self.bot = bot_instance  # Store the bot instance
        self.current_page = 0
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await self.update_message(interaction)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        
        # Check if card type is Support or Booster
        current_card = self.cards[self.current_page]
        if current_card['type_card'] in ['Support', 'Booster']:
            await interaction.response.send_message("Support and Booster cards don't have adjustable stats.", ephemeral=True)
            return
        
        # Acknowledge the interaction immediately to prevent timeout
        await interaction.response.defer(ephemeral=False)
        
        try:
            # Get the current card's user_id and card_id
            user_id = current_card['user_id']
            card_id = current_card['card_id']
            
            # Check if there's an active stat adjustment session for this card
            # Check if there's an entry in stat_status
            query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            result = await execute_query_with_retry(query, (user_id, card_id))
        
            # IMPORTANT: Fetch the latest card data from the database
            card_query = """
            SELECT uc.*, 
                c.card_name, c.image, c.xp_cap, c.max_level, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id, 
                cc.card_class_name, 
                w.weapon_id, w.weapon_name, w.weapon_rank,
                a.weapon_id as armor_id, a.weapon_name as armor_name, a.weapon_rank as armor_rank,
                s.weapon_id as shield_id, s.weapon_name as shield_name, s.weapon_rank as shield_rank,
                uc.pseudo_name, uc.pseudo_image
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            WHERE uc.user_id = %s AND uc.card_id = %s
            """
            updated_card = await execute_query_with_retry(card_query, (user_id, card_id))
        
            # Update the current card in the cards list with the latest data
            if updated_card:
                self.cards[self.current_page] = updated_card[0]
                current_card = updated_card[0]
            
            if result:
                # There's already an active session
                await interaction.followup.send("You already have an active stat adjustment session for this card. Please complete or cancel that session first.", ephemeral=True)
                return
            
            # No active session, create one and show the stat adjustment view
            # Insert new entry into stat_status
            insert_query = "INSERT INTO stat_status (user_id, card_id) VALUES (%s, %s)"
            await execute_query_with_retry(insert_query, (user_id, card_id))
        
            # Create and show the stat adjustment view with the updated card data
            stat_view = StatAdjustView(self.ctx, current_card, self, self.bot)
            await interaction.edit_original_response(view=stat_view)
        
        except Exception as e:
            print(f"Error in stats button: {e}")
            await interaction.followup.send(f"An error occurred while processing your request: {e}", ephemeral=True)

    async def update_message(self, interaction):
        embed = self.create_embed()
        files = []
        
        # Handle rank image (thumbnail)
        rank_image_path = self.get_image_path(self.cards[self.current_page].get('cardrank_image'))
        if rank_image_path and os.path.exists(rank_image_path):
            rank_file = discord.File(rank_image_path, filename="rank_image.png")
            files.append(rank_file)
            embed.set_thumbnail(url="attachment://rank_image.png")
        img = self.cards[self.current_page]['image']
        if self.cards[self.current_page].get('pseudo_image') and self.cards[self.current_page]['pseudo_image'] != "" :
            img = self.cards[self.current_page]['pseudo_image']
        # Handle card image
        card_image_path = self.get_image_path(img)
        if card_image_path and os.path.exists(card_image_path):
            card_file = discord.File(card_image_path, filename="card_image.png")
            files.append(card_file)
            embed.set_image(url="attachment://card_image.png")

        # Modify stats button visibility
        self.stats_button.disabled = self.cards[self.current_page]['type_card'] in ['Support','Booster']

        if files:
            await interaction.response.edit_message(embed=embed, attachments=files, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        card = self.cards[self.current_page]
        display_name = card['pseudo_name'] if card.get('pseudo_name') else card['card_name']
        
        # Define embed color based on card rank
        rank_colors = {
            2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
            3: discord.Color.from_rgb(139, 0, 0),  # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),    # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),   # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }
        
        embed_color = rank_colors.get(card.get('cardrank_id'), discord.Color.from_rgb(75, 0, 130))
        # Check if the card is a Support type
        equipment_details = []
        if card['weapon_id']:
            equipment_details.append(f"{card['weapon_emoji']} Weapon: {card['weapon_name']}\n")
        if card['armor_id']:
            equipment_details.append(f"{card['armor_emoji']} Armor: {card['armor_name']}\n")
        if card['shield_id']:
            equipment_details.append(f"{card['secondshield_emoji']} Shield: {card['shield_name']}\n")
        
        # Add equipment section if there are any items

        role = ""
        if card['type_card'] == "Fighter":
            role = "Offence"
        else:
            role = card['type_card']
        # Handle different card types
        if card['type_card'].lower() in ['support', 'booster']:
            description = (
                f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
                f"Card Informations\n"
                f"{card['power_emoji']} Power : {card['power_stat']}\n"
                f"{card['type_emoji']} Class : {card['card_class_name']}\n"
                f"{card['class_emoji']} Role : {card['type_card']}\n"
            )
            if equipment_details:
                description += ''.join(equipment_details)
            description += f"\n**Effect**\n{card['description']}"
        else:
            description = (
                f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
                f"**Card Informations**\n"
                f"{card['power_emoji']} Power : {card['power_stat']}\n"
                f"{card['type_emoji']} Class : {card['card_class_name']}\n"
                f"{card['class_emoji']} Role : {role}\n"
                f"{card['level_emoji']} Level : {card['current_level']}/{card['max_level']}\n"
                f"{card['xp_emoji']} XP : {card['current_xp']}/{card['xp_cap']}\n"
            )
            if equipment_details:
                description += ''.join(equipment_details)
            description += (
                f"\n**Combat Statistics**\n"
                f"{card['attack_emoji']} Attack : {card['attack_stat']}\n"
                f"{card['health_emoji']} Health : {card['health_stat']}\n"
                f"{card['speed_emoji']} Speed : {card['speed_stat']}\n"
                f"{card['defense_emoji']} Defense : {card['defence_stat']}\n\n"
                f"Available Stat Points: {card['current_statpoints']}"
            )
        name = card['card_name']  # Default to card_name
        if card['pseudo_name'] is not None and card['pseudo_name'] != '':
            # If pseudo_name exists, format it with the original card name in brackets
            name = f"{card['pseudo_name']} [{card['card_name']}]"
        embed = discord.Embed(
            title=name,
            description=description,
            color=embed_color
        )

        # Set the thumbnail image for the current card
        rank_image_path = self.get_image_path(card['cardrank_image'])
        if rank_image_path and os.path.exists(rank_image_path):
            rank_file = discord.File(rank_image_path, filename="rank_image.png")
            embed.set_thumbnail(url="attachment://rank_image.png")

        sl_quotes = [
            "The true hunter never rests.",
            "Your strength defines your fate.",
            "Another step towards greatness.",
            "The shadows whisper, will you answer?"
        ]
        
        footer_text = f"{random.choice(sl_quotes)} • Card {self.current_page+1}/{len(self.cards)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()

        return embed

class AllView(discord.ui.View):
    def __init__(self, ctx, cards):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cards = cards
        self.current_page = 0
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
            return
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await self.update_message(interaction)

    async def update_message(self, interaction):
        embed = self.create_embed()
        files = []
        
        # Handle rank image (thumbnail)
        card = self.cards[self.current_page]
        rank_image_path = self.get_image_path(card['cardrank_image'])
        if rank_image_path and os.path.exists(rank_image_path):
            rank_file = discord.File(rank_image_path, filename="rank_image.png")
            files.append(rank_file)
            embed.set_thumbnail(url="attachment://rank_image.png")

        # Handle card image (main image)
        card_image_path = self.get_image_path(card['image'])
        if card_image_path and os.path.exists(card_image_path):
            card_file = discord.File(card_image_path, filename="card_image.png")
            files.append(card_file)
            embed.set_image(url="attachment://card_image.png")

        if files:
            await interaction.response.edit_message(embed=embed, attachments=files, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        card = self.cards[self.current_page]
        
        # Define embed color based on card rank
        rank_colors = {
            2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
            3: discord.Color.from_rgb(139, 0, 0),  # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),    # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),   # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }
        
        embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))
        role = ""
        if card['type_card'] == "Fighter":
            role = "Offence"
        else:
            role = card['type_card']
        description = (
            f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
            f"**Card Informations**\n"
            f"<:solopower:1302936147139235882> Power : {card['power']}\n"
            f"<:solotype:1302937991869759539> Class : {card['card_class_name']}\n"
            f"<:solorole:1302938003873726526> Role : {role}\n\n"
        )

        # Add either Effect description or Combat Statistics based on card type
        if card['type_card'].lower() in ['support', 'booster']:
            description += (
                f"**Effect**\n"
                f"{card['description']}"
            )

        else:
            description += (
                f"**Combat Statistics**\n"
                f"<:solosword:1302911919941746730> Attack : {card['attack']}\n"
                f"<:soloheart:1302912058819477514> Health : {card['health']}\n"
                f"<:solostar:1302912104264761374> Speed : {card['speed']}\n"
                f"<:soloshield:1302911930242961461> Defense : {card['defence']}"
            )

        embed = discord.Embed(
            title=f"{card['card_name']}",
            description=description,
            color=embed_color
        )

        # Set the thumbnail image for the initial card
        if self.current_page == 0:
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                embed.set_thumbnail(url="attachment://rank_image.png")

        sl_quotes = [
            "The true hunter never rests.",
            "Your strength defines your fate.",
            "Another card, another step to greatness.",
            "The shadows whisper, will you answer?"
        ]
        
        footer_text = f"{random.choice(sl_quotes)} • Card {self.current_page+1}/{len(self.cards)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()

        return embed

class SpawnCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_cooldowns = {}  # Store user cooldowns
        self.COOLDOWN_TIME = 2  # Cooldown time in seconds
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.multiarise_sessions = set()  # Track users in multiarise sessions
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
        
    @commands.command(name="shealth", aliases=["sdb"])
    async def db_health(self, ctx):
        """Display detailed database health metrics"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send("You don't have permission to use this command.")
            return
        
        try:
            # Get metrics from the imported function
            metrics = get_metrics()
            
            # Create health status embed
            embed = discord.Embed(
                title="🏥 Database Health Check",
                color=discord.Color.green() if metrics.get("avg_query_time", 0) < 100 and metrics.get("utilization", 0) < 80 else discord.Color.orange()
            )
            
            # Add basic health metrics
            embed.add_field(name="Response Time", value=f"{metrics.get('avg_query_time', 0):.2f}ms", inline=True)
            embed.add_field(name="Pool Utilization", value=f"{metrics.get('utilization', 0):.1f}%", inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics.get('connection_errors', 0)), inline=True)
            
            # Add connection pool status
            embed.add_field(
                name="Connection Pool",
                value=f"Active: {metrics.get('active_connections', 0)}/{metrics.get('max_connections', 0)} ({metrics.get('free_connections', 0)} free)",
                inline=False
            )
            
            # Add query performance metrics
            embed.add_field(
                name="Query Performance",
                value=f"Avg: {metrics.get('avg_query_time', 0):.2f}ms | Min: {metrics.get('min_query_time', 0):.2f}ms | Max: {metrics.get('max_query_time', 0):.2f}ms",
                inline=False
            )
            
            # Add recommendations based on metrics
            recommendations = []
            if metrics.get('utilization', 0) > 80:
                recommendations.append("⚠️ High connection pool utilization. Consider increasing maxsize.")
            if metrics.get('avg_query_time', 0) > 100:
                recommendations.append("⚠️ Slow database response time. Check server load.")
            if metrics.get('connection_errors', 0) > 0:
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
                
            embed.timestamp = datetime.utcnow()
            await ctx.send(embed=embed)
            
        except Exception as e:
            error_msg = f"Error checking database health: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=["mc", "myc"])
    async def mycards(self, ctx, filter_type: str = None, filter_value: str = None):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            user_id = user_result[0]['user_id']

            # Base query for user cards
            base_query = """
            SELECT uc.*, 
                c.card_name, c.image, c.xp_cap, c.max_level, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id, 
                cc.card_class_name, 
                w.weapon_id, w.weapon_name, w.weapon_rank,
                a.weapon_id as armor_id, a.weapon_name as armor_name, a.weapon_rank as armor_rank,
                s.weapon_id as shield_id, s.weapon_name as shield_name, s.weapon_rank as shield_rank,
                uc.pseudo_name, uc.pseudo_image
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            WHERE uc.user_id = %s
            """

            # Define rank mappings
            rank_map = {
                's': 2, 'a': 3, 'b': 4, 
                'c': 5, 'd': 6, 'e': 7
            }

            # Handle filtering
            filter_condition = ""
            order_by = "ORDER BY uc.power_stat DESC"  # Default ordering
            query_params = [user_id]

            if filter_type:
                filter_type_lower = filter_type.lower()
                
                # Handle rank filtering
                if filter_type_lower == "rank" and filter_value:
                    rank_value = rank_map.get(filter_value.lower())
                    if not rank_value:
                        await ctx.send("❌ Invalid rank. Please use: S, A, B, C, D, or E")
                        return
                    filter_condition = "AND c.card_rank = %s"
                    query_params.append(rank_value)
                    order_by = "ORDER BY uc.power_stat DESC"
                
                # Handle stat-based ordering
                elif filter_type_lower in ["power", "attack", "health", "speed", "defense", "defence"]:
                    if filter_type_lower == "power":
                        order_by = "ORDER BY uc.power_stat DESC"
                    elif filter_type_lower == "attack":
                        order_by = "ORDER BY uc.attack_stat DESC"
                    elif filter_type_lower == "health":
                        order_by = "ORDER BY uc.health_stat DESC"
                    elif filter_type_lower == "speed":
                        order_by = "ORDER BY uc.speed_stat DESC"
                    elif filter_type_lower in ["defense", "defence"]:
                        order_by = "ORDER BY uc.defence_stat DESC"
                
                # Handle card type filtering
                elif filter_type_lower in ["offence", "offense", "fighter"]:
                    filter_condition = "AND c.type_card = 'Fighter'"
                    order_by = "ORDER BY uc.power_stat DESC"
                elif filter_type_lower == "support":
                    filter_condition = "AND c.type_card = 'Support'"
                    order_by = "ORDER BY uc.power_stat DESC"
                elif filter_type_lower == "booster":
                    filter_condition = "AND c.type_card = 'Booster'"
                    order_by = "ORDER BY uc.power_stat DESC"
                
                # Handle rank filtering without "rank" keyword (direct rank input)
                elif filter_type_lower in rank_map:
                    rank_value = rank_map[filter_type_lower]
                    filter_condition = "AND c.card_rank = %s"
                    query_params.append(rank_value)
                    order_by = "ORDER BY uc.power_stat DESC"
                
                else:
                    await ctx.send("❌ Invalid filter. Available filters:\n"
                                "• **Ranks**: `s`, `a`, `b`, `c`, `d`, `e` or `rank s/a/b/c/d/e`\n"
                                "• **Stats**: `power`, `attack`, `health`, `speed`, `defense`\n"
                                "• **Types**: `offence`, `support`, `booster`")
                    return

            # Construct final query
            final_query = f"{base_query} {filter_condition} {order_by}"
            cards = await execute_query_with_retry(final_query, query_params)

            if not cards:
                filter_desc = ""
                if filter_type:
                    if filter_type.lower() == "rank" and filter_value:
                        filter_desc = f"Rank {filter_value.upper()} "
                    elif filter_type.lower() in rank_map:
                        filter_desc = f"Rank {filter_type.upper()} "
                    elif filter_type.lower() in ["offence", "offense", "fighter"]:
                        filter_desc = "Offence "
                    elif filter_type.lower() == "support":
                        filter_desc = "Support "
                    elif filter_type.lower() == "booster":
                        filter_desc = "Booster "
                    else:
                        filter_desc = f"{filter_type.title()} "
                
                await ctx.send(f"You don't have any {filter_desc}cards.")
                return

            # Create the view with bot instance
            view = CardView(ctx, cards, self.bot)

            # Directly create the embed using the view's create_embed method
            embed = view.create_embed()
            
            # Add filter information to embed title if filtering is applied
            if filter_type:
                if filter_type.lower() == "rank" and filter_value:
                    embed.title = f"Rank {filter_value.upper()} - {embed.title}"
                elif filter_type.lower() in rank_map:
                    embed.title = f"Rank {filter_type.upper()} - {embed.title}"
                elif filter_type.lower() in ["offence", "offense", "fighter"]:
                    embed.title = f"Offence Cards - {embed.title}"
                elif filter_type.lower() == "support":
                    embed.title = f"Support Cards - {embed.title}"
                elif filter_type.lower() == "booster":
                    embed.title = f"Booster Cards - {embed.title}"
                elif filter_type.lower() in ["power", "attack", "health", "speed", "defense", "defence"]:
                    embed.title = f"Sorted by {filter_type.title()} - {embed.title}"
            
            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = view.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")
            
            img = cards[0]['image']
            if cards[0].get('pseudo_image') and cards[0]['pseudo_image'] != "":
                img = cards[0]['pseudo_image']
            
            # Handle card image
            card_image_path = view.get_image_path(img)
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            # Send the message with embed, files, and view
            if files:
                message = await ctx.send(embed=embed, files=files, view=view)
                view.message = message
            else:
                message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=None)
            
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")
    
    @commands.command(aliases=["cv", "cvw"])
    async def cardview(self, ctx, *, card_name):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            user_id = user_result[0]['user_id']

            # Modified query to use LIKE for partial matches
            card_query = """
            SELECT uc.*, 
                c.card_name, c.image, c.xp_cap, c.max_level, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id, 
                cc.card_class_name, 
                w.weapon_id, w.weapon_name, w.weapon_rank,
                a.weapon_id as armor_id, a.weapon_name as armor_name, a.weapon_rank as armor_rank,
                s.weapon_id as shield_id, s.weapon_name as shield_name, s.weapon_rank as shield_rank,
                uc.pseudo_name, uc.pseudo_image
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            WHERE uc.user_id = %s AND (uc.name LIKE %s OR uc.pseudo_name LIKE %s OR c.card_name LIKE %s)
            ORDER BY FIELD(cr.cardrank_name, 'S', 'A', 'B', 'C', 'D','E')
            """
            search_pattern = f"%{card_name}%"
            card_results = await execute_query_with_retry(card_query, (user_id, search_pattern, search_pattern, search_pattern))

            if not card_results:
                await ctx.send(f"You don't have any cards matching your description.")
                return

            if len(card_results) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to view:",
                    color=discord.Color.blue()
                )
                # List all matching cards
                card_list = "\n".join([f"• {card['pseudo_name'] if card.get('pseudo_name') else card['card_name']}" 
                                    for card in card_results])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to view.")
                
                await ctx.send(embed=embed)
                return

            # If only one card is found, proceed with the display logic
            card = card_results[0]
            display_name = card['pseudo_name'] if card.get('pseudo_name') else card['card_name']
            display_image = card['pseudo_image'] if card.get('pseudo_image') else card['image']

            rank_colors = {
            2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
            3: discord.Color.from_rgb(139, 0, 0),  # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),    # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),   # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
            }
            
            embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))

            # Equipment section (for both Support and Fighter cards)
            equipment_details = []
            if card['weapon_id']:
                equipment_details.append(f"{card['weapon_emoji']} Weapon: {card['weapon_name']}\n")
            if card['armor_id']:
                equipment_details.append(f"{card['armor_emoji']} Armor: {card['armor_name']}\n")
            if card['shield_id']:
                equipment_details.append(f"{card['secondshield_emoji']} Shield: {card['shield_name']}\n")
            
            # Add equipment section if there are any items

            role = ""
            if card['type_card'] == "Fighter":
                role = "Offence"
            else:
                role = card['type_card']
            # Handle different card types
            if card['type_card'].lower() in ['support', 'booster']:
                description = (
                    f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
                    f"**Card Informations**\n"
                    f"{card['power_emoji']} Power : {card['power_stat']}\n"
                    f"{card['type_emoji']} Class : {card['card_class_name']}\n"
                    f"{card['class_emoji']} Role : {card['type_card']}\n"
                )
                if equipment_details:
                    description += ''.join(equipment_details)
                description += f"\n**Effect**\n{card['description']}"
            else:
                description = (
                    f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
                    f"**Card Informations**\n"
                    f"{card['power_emoji']} Power : {card['power_stat']}\n"
                    f"{card['type_emoji']} Class : {card['card_class_name']}\n"
                    f"{card['class_emoji']} Role : {role}\n"
                    f"{card['level_emoji']} Level : {card['current_level']}/{card['max_level']}\n"
                    f"{card['xp_emoji']} XP : {card['current_xp']}/{card['xp_cap']}\n"
                )
                if equipment_details:
                    description += ''.join(equipment_details)
                description += (
                    f"\n**Combat Statistics**\n"
                    f"{card['attack_emoji']} Attack : {card['attack_stat']}\n"
                    f"{card['health_emoji']} Health : {card['health_stat']}\n"
                    f"{card['speed_emoji']} Speed : {card['speed_stat']}\n"
                    f"{card['defense_emoji']} Defense : {card['defence_stat']}\n\n"
                    f"Available Stat Points: {card['current_statpoints']}"
                )
            name = card['card_name']  # Default to card_name
            if card['pseudo_name'] is not None and card['pseudo_name'] != '':
                # If pseudo_name exists, format it with the original card name in brackets
                name = f"{card['pseudo_name']} [{card['card_name']}]"
            embed = discord.Embed(
                title=name,
                description=description,
                color=embed_color
            )

            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")
            img = card['image']
            if card.get('pseudo_image') and card['pseudo_image'] != "":
                img = card['pseudo_image']
            # Handle card image (main image)
            card_image_path = self.get_image_path(img)
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            sl_quotes = [
                "The true hunter never rests.",
                "Your strength defines your fate.",
                "Another card, another step to greatness.",
                "The shadows whisper, will you answer?"
            ]
            
            footer_text = random.choice(sl_quotes)
            embed.set_footer(text=footer_text)
            embed.timestamp = discord.utils.utcnow()

            # Create a view with a Stats button
            class CardView(discord.ui.View):
                def __init__(self, ctx, card, bot_instance):
                    super().__init__(timeout=180)
                    self.ctx = ctx
                    self.card = card
                    self.bot = bot_instance

                    # Disable stats button for Support cards
                    self.stats_button.disabled = card['type_card'] in ['Support','Booster']

                @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
                async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != self.ctx.author.id:
                        await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
                        return
                    
                    # Check if there's already an entry in stat_status table
                    stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
                    stat_status_result = await execute_query_with_retry(stat_status_query, (self.card['user_id'], self.card['card_id']))
                    
                    if stat_status_result:
                        # If entry exists, show a message and don't allow adjustment
                        await interaction.response.send_message(
                            "This card is currently being adjusted. Please wait until the process is completed.", 
                            ephemeral=True
                        )
                    else:
                        # Fetch the latest card data from the database
                        card_query = """
                        SELECT uc.*, 
                            c.card_name, c.image, c.xp_cap, c.max_level, c.type_card, c.description,
                            cr.cardrank_name, cr.cardrank_image, cr.cardrank_id, 
                            cc.card_class_name, 
                            w.weapon_id, w.weapon_name, w.weapon_rank,
                            a.weapon_id as armor_id, a.weapon_name as armor_name, a.weapon_rank as armor_rank,
                            s.weapon_id as shield_id, s.weapon_name as shield_name, s.weapon_rank as shield_rank,
                            uc.pseudo_name, uc.pseudo_image
                        FROM user_cards uc
                        JOIN card c ON uc.card_id = c.card_id
                        JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                        JOIN card_class cc ON c.card_class_id = cc.card_class_id
                        LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
                        LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
                        LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
                        WHERE uc.user_id = %s AND uc.card_id = %s
                        """
                        updated_card_result = await execute_query_with_retry(card_query, (self.card['user_id'], self.card['card_id']))
                        
                        if updated_card_result:
                            # Update the card with the latest data
                            self.card = updated_card_result[0]
                        
                        # If no entry exists, insert one and allow adjustment
                        insert_query = "INSERT INTO stat_status (user_id, card_id) VALUES (%s, %s)"
                        await execute_query_with_retry(insert_query, (self.card['user_id'], self.card['card_id']))
                        
                        # Create the stat adjust view with the updated card data
                        stat_view = StatAdjustView(self.ctx, self.card, self, self.bot)
                        await interaction.response.edit_message(view=stat_view)
            
            view = CardView(ctx, card, self.bot)

            # Send the message with all files at once
            if files:
                message = await ctx.send(embed=embed, files=files, view=view)
            else:
                message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=None)

        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @commands.command(aliases=["wv", "wvw"])
    async def weaponview(self, ctx, *, weapon_name):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            # Query to find weapons with partial name matches
            weapon_query = """
                SELECT w.*, wr.weaponrank_name
                FROM weapon w
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                WHERE w.weapon_name LIKE %s
            """
            search_pattern = f"%{weapon_name}%"
            weapons = await execute_query_with_retry(weapon_query, (search_pattern,))

            if not weapons:
                await ctx.send(f"No weapons found matching your description.")
                return

            if len(weapons) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title="Multiple Weapons Found",
                    description="I found multiple weapons matching your search. Please specify which one you'd like to view:",
                    color=discord.Color.blue()
                )

                # List all matching weapons
                weapon_list = "\n".join([f"• {weapon['weapon_name']}" for weapon in weapons])
                embed.add_field(name="Matching Weapons", value=weapon_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the weapon you want to view.")
                
                await ctx.send(embed=embed)
                return

            # If only one weapon is found, create the embed
            weapon = weapons[0]
            
            embed = discord.Embed(
                title=f"⚔️ {weapon['weapon_name']} ⚔️",
                description=weapon['weapon_description'],
                color=discord.Color.from_rgb(75, 0, 130)
            )

            embed.add_field(name="Rank", value=weapon['weaponrank_name'], inline=False)
            
            # Add boost fields
            if weapon.get('attack_boost') or weapon.get('attack_percentage'):
                attack_value = []
                if weapon.get('attack_boost'):
                    attack_value.append(f"+{weapon['attack_boost']}")
                if weapon.get('attack_percentage'):
                    attack_value.append(f"+{weapon['attack_percentage']}%")
                embed.add_field(name="Attack Boost", value=" | ".join(attack_value), inline=True)
            
            if weapon.get('health_boost') or weapon.get('health_percentage'):
                health_value = []
                if weapon.get('health_boost'):
                    health_value.append(f"+{weapon['health_boost']}")
                if weapon.get('health_percentage'):
                    health_value.append(f"+{weapon['health_percentage']}%")
                embed.add_field(name="Health Boost", value=" | ".join(health_value), inline=True)
                
            if weapon.get('speed_boost') or weapon.get('speed_percentage'):
                speed_value = []
                if weapon.get('speed_boost'):
                    speed_value.append(f"+{weapon['speed_boost']}")
                if weapon.get('speed_percentage'):
                    speed_value.append(f"+{weapon['speed_percentage']}%")
                embed.add_field(name="Speed Boost", value=" | ".join(speed_value), inline=True)
                
            if weapon.get('defense_boost') or weapon.get('defense_percentage'):
                defense_value = []
                if weapon.get('defense_boost'):
                    defense_value.append(f"+{weapon['defense_boost']}")
                if weapon.get('defense_percentage'):
                    defense_value.append(f"+{weapon['defense_percentage']}%")
                embed.add_field(name="Defense Boost", value=" | ".join(defense_value), inline=True)

            # Add price if available
            if 'price' in weapon:
                embed.add_field(name="Price", value=f"{weapon['price']:,}", inline=True)

            # Add quantity if available
            if 'quantiter' in weapon:
                embed.add_field(name="Quantity", value=str(weapon['quantiter']), inline=True)

            sl_quotes = [
                "A weapon is only as strong as its wielder.",
                "Choose your weapon wisely, hunter.",
                "Power lies in both skill and equipment.",
                "Every weapon has its purpose."
            ]
            
            embed.set_footer(text=f"{random.choice(sl_quotes)} • Weapon Details")
            embed.timestamp = discord.utils.utcnow()

            files = []
            
            # Handle weapon image
            if weapon.get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapon['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            # Send the message with all files
            await ctx.send(embed=embed, files=files)
                
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")

    @commands.command(aliases=["wl", "wlt"])
    async def weaponlist(self, ctx):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            weapons_query = """
                SELECT w.*, wr.weaponrank_name
                FROM weapon w
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                ORDER BY FIELD(wr.weaponrank_name, 'S', 'A', 'B', 'C', 'D', 'E')
            """
            weapons = await execute_query_with_retry(weapons_query)

            if not weapons:
                await ctx.send("No weapons found in the database.")
                return

            view = WeaponView(ctx, weapons)
            embed = view.create_embed()
            files = []
            
            if weapons[0].get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapons[0]['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            # Store the message reference in the view
            view.message = await ctx.send(embed=embed, files=files, view=view)
                
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")

    @commands.command(aliases=["mw", "myw"])
    async def myweapons(self, ctx):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_result[0]['user_id'],))
            
            if not inventory_result:
                await ctx.send("Error: You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            weapons_query = """
                SELECT w.*, wr.weaponrank_name, iw.quantiter
                FROM inventory_weapon iw
                JOIN weapon w ON iw.weapon_id = w.weapon_id
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                WHERE iw.inventory_id = %s
                ORDER BY FIELD(wr.weaponrank_name, 'S', 'A', 'B', 'C', 'D', 'E')
            """
            weapons = await execute_query_with_retry(weapons_query, (inventory_id,))

            if not weapons:
                await ctx.send("You don't have any weapons in your inventory.")
                return

            view = WeaponView(ctx, weapons)
            embed = view.create_embed()
            files = []
            
            if weapons[0].get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapons[0]['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            # Store the message reference in the view
            view.message = await ctx.send(embed=embed, files=files, view=view)
                
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")

    @commands.command(aliases=["ci", "cif"])
    async def cardinfo(self, ctx, *, card_name):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            # Modified query to use LIKE for partial matches
            card_query = """
                SELECT c.*, cr.cardrank_name, cc.card_class_name, cr.cardrank_image, cr.cardrank_id
                FROM card c
                JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                JOIN card_class cc ON c.card_class_id = cc.card_class_id
                WHERE c.card_name LIKE %s AND c.seeable = 1
            """
            search_pattern = f"%{card_name}%"
            card_results = await execute_query_with_retry(card_query, (search_pattern,))
            
            if not card_results:
                await ctx.send(f"No cards found matching your description.")
                return

            if len(card_results) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to view:",
                    color=discord.Color.blue()
                )

                # List all matching cards
                card_list = "\n".join([f"• {card['card_name']}" for card in card_results])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to view.")
                
                await ctx.send(embed=embed)
                return

            # If only one card is found, proceed with the original display logic
            card = card_results[0]

            rank_colors = {
                2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
                3: discord.Color.from_rgb(139, 0, 0),  # A Rank - Purple
                4: discord.Color.from_rgb(255, 165, 0),    # B Rank - Blue
                5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
                6: discord.Color.from_rgb(0, 128, 128),   # D Rank - Red
                7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
            }
            
            embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))
            role = ""
            if card['type_card'] == "Fighter":
                role = "Offence"
            else:
                role = card['type_card']
            # Base description with card information
            description = (
                f"A {card['cardrank_name']}-Rank card, Summoner!\n\n"
                f"**Card Informations**\n"
                f"<:solopower:1302936147139235882> Power : {card['power']}\n"
                f"<:solotype:1302937991869759539> Class : {card['card_class_name']}\n"
                f"<:solorole:1302938003873726526> Role : {role}\n\n"
            )

            # Add either Effect description or Combat Statistics based on card type
            if card['type_card'].lower() in ['support', 'booster']:
                description += (
                    f"**Effect**\n"
                    f"{card['description']}"
                )

            else:
                description += (
                    f"**Combat Statistics**\n"
                    f"<:solosword:1302911919941746730> Attack : {card['attack']}\n"
                    f"<:soloheart:1302912058819477514> Health : {card['health']}\n"
                    f"<:solostar:1302912104264761374> Speed : {card['speed']}\n"
                    f"<:soloshield:1302911930242961461> Defense : {card['defence']}"
                )

            embed = discord.Embed(
                title=f"{card['card_name']}",
                description=description,
                color=embed_color
            )

            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Handle card image (main image)
            card_image_path = self.get_image_path(card['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            sl_quotes = [
                "The true hunter never rests.",
                "Your strength defines your fate.",
                "Another card, another step to greatness.",
                "The shadows whisper, will you answer?"
            ]
            
            footer_text = random.choice(sl_quotes)
            embed.set_footer(text=footer_text)
            embed.timestamp = discord.utils.utcnow()

            # Send the message with all files at once
            if files:
                await ctx.send(embed=embed, files=files)
            else:
                await ctx.send(embed=embed)
                
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")

    @commands.command(aliases=["cl", "clt"])
    async def cardlist(self, ctx, filter_type: str = None, filter_value: str = None):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return 
                await ctx.send("Error: You need to register to the bot first.")
                return

            # Define rank mappings
            rank_map = {
                's': 2, 'a': 3, 'b': 4, 
                'c': 5, 'd': 6, 'e': 7
            }

            cards_query = """
            SELECT c.*, cr.cardrank_name, cc.card_class_name, cr.cardrank_image, cr.cardrank_id
            FROM card c
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            WHERE c.seeable = 1
            {}
            ORDER BY power DESC
            """

            query_params = []
            filter_condition = ""

            if filter_type and filter_type.lower() in ['support', 'fighter', 'booster']:
                filter_condition = "AND c.type_card = %s"
                query_params.append(filter_type.capitalize())
            elif filter_type and filter_type.lower() == "rank" and filter_value:
                rank_value = rank_map.get(filter_value.lower())
                if not rank_value:
                    await ctx.send("Invalid rank. Please use: S, A, B, C, D, or E")
                    return
                filter_condition = "AND c.card_rank = %s"
                query_params.append(rank_value)
            elif filter_type:
                await ctx.send("Invalid filter. Use: Support, Fighter, Booster, or 'rank [S/A/B/C/D/E]'")
                return

            cards_query = cards_query.format(filter_condition)
            cards = await execute_query_with_retry(cards_query, tuple(query_params) if query_params else None)

            if not cards:
                filter_desc = ''
                if filter_type == 'rank' and filter_value:
                    filter_desc = f"Rank {filter_value.upper()} "
                elif filter_type:
                    filter_desc = f"{filter_type} "
                await ctx.send(f"No {filter_desc}cards found.")
                return

            view = AllView(ctx, cards)
            embed = view.create_embed()
            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = view.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Handle card image (main image)
            card_image_path = view.get_image_path(cards[0]['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            if files:
                view.message = await ctx.send(embed=embed, files=files, view=view)
            else:
                view.message = await ctx.send(embed=embed, view=view)

            await view.wait()
            
            try:
                await view.message.edit(view=None)
            except:
                pass

        except Exception as e:
            print(f"Error in cardlist: {e}")
            await ctx.send("An error occurred while processing your request.")
            
    
    def is_valid_url(self, url):
        try:
            result = urllib.parse.urlparse(url)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False

    async def get_valid_card(self, premium_status=0):
        # Query to get all cards with spawn_status = 1
        query = """
            SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
            FROM card c
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            WHERE c.spawn_status = 1
        """
        cards = await execute_query_with_retry(query)

        # Sort cards by rank
        rank_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
        sorted_cards = sorted(cards, key=lambda x: rank_order[x['cardrank_name']])

        # Determine rarity weights based on premium status
        if premium_status == 2:
            print('Premium 2')
            rarity_weights = {
                'S': 50,    # 0.5% (Hyper Rare) - 60% increase
                'A': 170,   # 1.7% (Super Rare) - 50% increase
                'B': 895,   # 8.95% (Rare) - 17% increase
                'C': 1085,   # 10.85% (Uncommon) - 13% increase
                'D': 3800,  # 38.0% (Common) - 5% decrease
                'E': 4000   # 40.0% (Very Common) - slight decrease
            }
        elif premium_status == 3:
            print('Premium 3')
            rarity_weights = {
                'S': 100,   # 1.0% (Hyper Rare) - 140% increase from base
                'A': 200,   # 2.0% (Super Rare) - 100% increase from base
                'B': 1000,   # 10.0% (Rare) - 33% increase from base
                'C': 1100,   # 11.0% (Uncommon) - 27% increase from base
                'D': 3600,  # 36.0% (Common) - 10% decrease from base
                'E': 4000   # 40.0% (Very Common) - slight decrease from base
            }
        else:
            print('Standard (0 or 1)')
            rarity_weights = {
                'S': 10,    # 0.1% (Hyper Rare)
                'A': 100,   # 1% (Super Rare)
                'B': 640,  # 6.4% (Rare)
                'C': 750,  # 7.5% (Uncommon)
                'D': 4000,  # 40.0% (Common)
                'E': 4500   # 45.0% (Very Common)
            }

        total_weight = sum(rarity_weights.values())
        rand = random.randint(1, total_weight)
        current_sum = 0
        selected_rank = None

        for rank, weight in rarity_weights.items():
            current_sum += weight
            if rand <= current_sum:
                selected_rank = rank
                break

        # Special handling for S-rank cards using drop_luck
        if selected_rank == 'S':
            # Get all S-rank cards with their drop_luck values
            s_rank_query = """
                SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
                FROM card c
                JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                JOIN card_class cc ON c.card_class_id = cc.card_class_id
                WHERE c.spawn_status = 1 AND c.card_rank = 2
                ORDER BY c.drop_luck ASC
            """
            s_rank_cards = await execute_query_with_retry(s_rank_query)
            
            if s_rank_cards:
                # Create weighted selection based on drop_luck
                # Convert drop_luck percentages to weights (inverse relationship - lower drop_luck = rarer = lower weight)
                # We'll use (6.0 - drop_luck) * 1000 to create appropriate weights
                # This makes Bellion (0.1%) have weight ~5900 and Laura (5.0%) have weight ~1000
                
                card_weights = []
                total_s_weight = 0
                
                for card in s_rank_cards:
                    # Convert drop_luck to weight (inverse relationship)
                    # Higher drop_luck = more common = higher weight for selection
                    weight = int(card['drop_luck'] * 1000)  # Convert percentage to integer weight
                    card_weights.append((card, weight))
                    total_s_weight += weight
                
                # Select S-rank card based on drop_luck weights
                s_rand = random.randint(1, total_s_weight)
                s_current_sum = 0
                
                for card, weight in card_weights:
                    s_current_sum += weight
                    if s_rand <= s_current_sum:
                        drawn_card = card
                        break
                else:
                    # Fallback to first card if something goes wrong
                    drawn_card = s_rank_cards[0]
                
                # Verify image exists
                image_path = self.get_image_path(drawn_card['image'])
                if image_path and os.path.exists(image_path):
                    print(f'S-rank card drawn: {drawn_card["card_name"]} (drop_luck: {drawn_card["drop_luck"]}%)')
                    return drawn_card
                else:
                    # If image doesn't exist, try another S-rank card
                    for card in s_rank_cards:
                        alt_image_path = self.get_image_path(card['image'])
                        if alt_image_path and os.path.exists(alt_image_path):
                            print(f'S-rank fallback card: {card["card_name"]} (drop_luck: {card["drop_luck"]}%)')
                            return card
        
        # For non-S ranks, use the original random selection
        possible_cards = [card for card in sorted_cards if card['cardrank_name'] == selected_rank]
        if possible_cards:
            drawn_card = random.choice(possible_cards)
            image_path = self.get_image_path(drawn_card['image'])
            if image_path and os.path.exists(image_path):
                print(f'Non-S rank card drawn: {drawn_card["card_name"]} ({selected_rank} rank)')
                return drawn_card
        
        # If we couldn't find a valid card with the selected rank, try again with any card
        return random.choice(sorted_cards) if sorted_cards else None

    async def get_guaranteed_rank_card(self, rank_id):
        # Special handling for guaranteed S-rank cards (rank_id = 2)
        if rank_id == 2:
            # Use the same drop_luck system for guaranteed S-rank cards
            s_rank_query = """
                SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
                FROM card c
                JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                JOIN card_class cc ON c.card_class_id = cc.card_class_id
                WHERE c.spawn_status = 1 AND c.card_rank = 2
                ORDER BY c.drop_luck ASC
            """
            s_rank_cards = await execute_query_with_retry(s_rank_query)
            
            if s_rank_cards:
                # Create weighted selection based on drop_luck
                card_weights = []
                total_s_weight = 0
                
                for card in s_rank_cards:
                    # Convert drop_luck to weight (higher drop_luck = more common = higher weight)
                    weight = int(card['drop_luck'] * 1000)
                    card_weights.append((card, weight))
                    total_s_weight += weight
                
                # Select S-rank card based on drop_luck weights
                s_rand = random.randint(1, total_s_weight)
                s_current_sum = 0
                
                for card, weight in card_weights:
                    s_current_sum += weight
                    if s_rand <= s_current_sum:
                        drawn_card = card
                        break
                else:
                    drawn_card = s_rank_cards[0]
                
                print(f'Guaranteed S-rank card: {drawn_card["card_name"]} (drop_luck: {drawn_card["drop_luck"]}%)')
                return drawn_card
        
        # For non-S rank guaranteed cards, use original logic
        query = """
            SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
            FROM card c
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            WHERE c.spawn_status = 1 AND cr.cardrank_id = %s
        """
        possible_cards = await execute_query_with_retry(query, (rank_id,))
        
        if possible_cards:
            drawn_card = random.choice(possible_cards)
            print(f'Guaranteed non-S rank card: {drawn_card["card_name"]}')
            return drawn_card
        return None

    @commands.command(aliases=["ars", "ar"])
    async def arise(self, ctx):
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "duel")
        if on_cooldown:
          await ctx.send(f"⏰ Command on cooldown. Please wait {time_left} seconds.")
          return

        try:
            if ctx.author.id in self.multiarise_sessions:
                await ctx.send("⚠️ You cannot use arise while multiarise is in progress. Please wait for it to complete.")
                return

            # Get user information including premium status
            user_query = """SELECT user_id, quantiter_fragment, fragment_cap, 
                    roll_timer, roll_count, roll_cap, prenium, prenium_date, roll_total, joined_server,
                    first_joined, second_joined, third_joined
                    FROM user WHERE user_authorid = %s"""
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
                
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return
            
            user_id = user_result[0]['user_id']
            premium_status = user_result[0]['prenium']
            premium_date = user_result[0]['prenium_date']
            first_joined = user_result[0]['first_joined'] or 0
            second_joined = user_result[0]['second_joined'] or 0
            third_joined = user_result[0]['third_joined'] or 0

            # Check premium expiration (30 days)
            current_time = datetime.utcnow()
            if premium_status > 0 and premium_date:
                time_difference = current_time - premium_date
                days_since_premium = time_difference.days
                
                if days_since_premium >= 30:
                    # Premium has expired, reduce roll_cap based on previous premium level
                    roll_cap_reduction = 0
                    if premium_status == 1:
                        roll_cap_reduction = 1
                    elif premium_status == 2:
                        roll_cap_reduction = 2
                    elif premium_status == 3:
                        roll_cap_reduction = 3
                    
                    # Reset premium status and reduce roll_cap
                    await execute_query_with_retry(
                        "UPDATE user SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s WHERE user_authorid = %s",
                        (roll_cap_reduction, str(ctx.author.id))
                    )
                    
                    error_embed = discord.Embed(
                        title="⏰ Premium Subscription Expired",
                        description=f"Your Premium {premium_status} subscription has expired after 30 days.\n\n"
                                f"Your roll cap has been reduced by {roll_cap_reduction}.",
                        color=discord.Color.orange()
                    )
                    error_embed.add_field(
                        name="Subscription Date", 
                        value=premium_date.strftime("%Y-%m-%d"), 
                        inline=True
                    )
                    error_embed.add_field(
                        name="Days Elapsed", 
                        value=f"{days_since_premium} days", 
                        inline=True
                    )
                    await ctx.send(embed=error_embed)
                    
                    # Update local premium_status for the rest of the command
                    premium_status = 0
                else:
                    # Premium is valid, check for join bonuses
                    if premium_status == 1 and first_joined == 0:
                        await execute_query_with_retry(
                            "UPDATE user SET first_joined = 1, roll_cap = roll_cap + 1 WHERE user_authorid = %s",
                            (str(ctx.author.id),)
                        )
                    elif premium_status == 2 and second_joined == 0:
                        await execute_query_with_retry(
                            "UPDATE user SET second_joined = 1, roll_cap = roll_cap + 2 WHERE user_authorid = %s",
                            (str(ctx.author.id),)
                        )
                    elif premium_status == 3 and third_joined == 0:
                        await execute_query_with_retry(
                            "UPDATE user SET third_joined = 1, roll_cap = roll_cap + 3 WHERE user_authorid = %s",
                            (str(ctx.author.id),)
                        )

            # Check if user is in support server (ID: 832022150713704479)
            is_in_support_server = False
            try:
                support_guild = self.bot.get_guild(832022150713704479)
                if support_guild:
                    # First try get_member (works if member is cached)
                    member = support_guild.get_member(ctx.author.id)
                    
                    # If that fails, try to fetch the member directly
                    if not member:
                        try:
                            member = await support_guild.fetch_member(ctx.author.id)
                        except discord.NotFound:
                            # Member truly not in the server
                            member = None
                        except Exception as e:
                            print(f"Error fetching member: {e}")
                            # Default to checking if the command was run in the support server
                            member = ctx.guild.id == 832022150713704479 if ctx.guild else None
                    
                    is_in_support_server = member is not None
                    
            except Exception as e:
                print(f"Error checking server membership: {e}")
                # As a fallback, check if the command was run in the support server
                is_in_support_server = ctx.guild and ctx.guild.id == 832022150713704479

            # Using a transaction for joined_server updates to avoid race conditions
            joined_server = user_result[0].get('joined_server', 0)
            if is_in_support_server and joined_server == 0:
                # Increase roll_cap by 2 and mark as joined
                await execute_query_with_retry(
                    "UPDATE user SET roll_cap = roll_cap + 2, joined_server = 1 WHERE user_id = %s",
                    (user_id,)
                )
            elif not is_in_support_server and joined_server == 1:
                # Decrease roll_cap by 2 and mark as not joined
                await execute_query_with_retry(
                    "UPDATE user SET roll_cap = roll_cap - 2, joined_server = 0 WHERE user_id = %s",
                    (user_id,)
                )

            # Fetch the latest user data after potential roll_cap changes
            latest_user_query = """SELECT roll_timer, roll_count, roll_cap, roll_total, prenium
                                FROM user WHERE user_id = %s FOR UPDATE"""
            latest_user = await execute_query_with_retry(latest_user_query, (user_id,))
            
            if not latest_user:
                await ctx.send("Error retrieving your data. Please try again.")
                return
                
            current_time = datetime.utcnow()
            roll_timer = latest_user[0]['roll_timer']
            roll_count = latest_user[0]['roll_count']
            roll_cap = latest_user[0]['roll_cap']
            roll_total = latest_user[0]['roll_total'] or 0
            premium_status = latest_user[0]['prenium']

            # Handle roll timing and count in a single transaction to prevent race conditions
            if roll_timer is None:
                # Initialize timer and set count to 1
                await execute_query_with_retry(
                    "UPDATE user SET roll_timer = %s, roll_count = 1 WHERE user_id = %s",
                    (current_time, user_id)
                )
                roll_count = 1
            else:
                time_difference = current_time - roll_timer
                hours_difference = time_difference.total_seconds() / 3600

                if hours_difference < 4 and roll_count >= roll_cap:
                    remaining_seconds = (4 * 3600) - time_difference.total_seconds()
                    hours = int(remaining_seconds // 3600)
                    minutes = int((remaining_seconds % 3600) // 60)
                    seconds = int(remaining_seconds % 60)
                    
                    embed = discord.Embed(
                        title="⏰ Roll Limit Reached",
                        description=f"You've reached your daily roll limit!\n\nTime until reset: **{hours}h {minutes}m {seconds}s**",
                        color=discord.Color.red()
                    )
                        
                    # Add fields with information about voting and joining the support server
                    embed.add_field(
                        name="🗳️ Vote for Extra Rolls",
                        value="Vote for the bot to get an extra reroll daily! Use `sl vote` to get your voting link.",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="🌟 Join Our Support Server",
                        value=f"Join our support server to get +2 arise rolls (e.g., 11 → 13)!\n[Click here to join](https://discord.gg/rJHgZsVAK8)",
                        inline=False
                    )
                    
                    if ctx.author.avatar:
                        embed.set_thumbnail(url=ctx.author.avatar.url)
                    
                    embed.set_footer(text="Try again after the cooldown expires")
                    embed.timestamp = discord.utils.utcnow()
                    
                    await ctx.send(embed=embed)
                    return
                
                # Use a single atomic database operation to avoid race conditions
                if hours_difference >= 4:
                    # Reset timer and count
                    await execute_query_with_retry(
                        "UPDATE user SET roll_timer = %s, roll_count = 1 WHERE user_id = %s",
                        (current_time, user_id)
                    )
                    roll_count = 1
                else:
                    # Increment roll count
                    await execute_query_with_retry(
                        "UPDATE user SET roll_count = roll_count + 1 WHERE user_id = %s",
                        (user_id,)
                    )
                    roll_count += 1

            # Update roll_total in a single atomic operation
            new_roll_total = roll_total + 1
            await execute_query_with_retry(
                "UPDATE user SET roll_total = %s WHERE user_id = %s",
                (new_roll_total, user_id)
            )

            guaranteed_special = False
            drawn_card = None

            roll_thresholds = {
                0: (250, 3),   # Regular users: 250 rolls for A rank
                1: (200, 3),   # Premium 1: 200 rolls for A rank
                2: (150, 3),   # Premium 2: 150 rolls for A rank
                3: (150, 2),   # Premium 3: 150 rolls for S rank
            }

            threshold, rank = roll_thresholds.get(premium_status, (250, 3))
            if new_roll_total % threshold == 0 and new_roll_total > 0:
                drawn_card = await self.get_guaranteed_rank_card(rank)
                if drawn_card:
                    guaranteed_special = True

            if not drawn_card:
                drawn_card = await self.get_valid_card(premium_status)
            if not drawn_card:
                await ctx.send("Failed to get a valid card. Please try again.")
                return
            
            # Get the latest fragment information
            latest_fragment_query = "SELECT quantiter_fragment, fragment_cap FROM user WHERE user_id = %s"
            latest_fragment_result = await execute_query_with_retry(latest_fragment_query, (user_id,))
            
            if latest_fragment_result:
                current_fragments = latest_fragment_result[0]['quantiter_fragment']
                fragment_cap = latest_fragment_result[0]['fragment_cap']
            else:
                current_fragments = user_result[0]['quantiter_fragment']
                fragment_cap = user_result[0]['fragment_cap']

            # Check if user already has the card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, drawn_card['card_id']))

            description = ""
            footer_text = ""

            if not user_card_result:
                # Add new card to user's collection
                if drawn_card['card_id'] == 103:
                    await execute_query_with_retry(
                        "UPDATE user SET roll_cap = roll_cap + 1 WHERE user_id = %s", 
                        (user_id,)
                    )
                if drawn_card['card_id'] == 104:
                    await execute_query_with_retry(
                        "UPDATE user SET fragment_cap = fragment_cap + 100 WHERE user_id = %s", 
                        (user_id,)
                    )

                await execute_query_with_retry("""
                    INSERT INTO user_cards (user_id, card_id, current_level, current_xp, 
                    current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name, 
                    defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                    weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                    power_emoji, type_emoji, class_emoji) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                    %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id, drawn_card['card_id'], drawn_card['min_level'], 0, 0,
                    drawn_card['power'], drawn_card['attack'], drawn_card['health'], 
                    drawn_card['speed'], drawn_card['defence'], drawn_card['attack'], drawn_card['health'], 
                    drawn_card['speed'], drawn_card['card_name'], drawn_card['defence'],
                    '<:solosword:1302911919941746730>', '<:soloshield:1302911930242961461>',
                    '<:solostar:1302912104264761374>', '<:soloheart:1302912058819477514>',
                    '<:soloweapon:1302961287747342356>', '<:solosecondshield:1302959829413007380>',
                    '<:armor64:1311994697794850897>', '<:soloxp:1302960018747822080>',
                    '<:sololevel:1302959449220321350>', '<:solopower:1302936147139235882>',
                    '<:solorole:1302938003873726526>', '<:solotype:1302937991869759539>'
                ))
                description = "🎉 Congratulations! This card has been added to your collection!\n\n"
            else:
                if current_fragments >= fragment_cap:
                    description = "You have reached your stones limit. Delete some if you wish to collect more!\n\n"
                    footer_text = "Essence Stones limit reached!"
                else:
                    # Handle fragment addition
                    fragment_query = "SELECT id_fragment FROM card WHERE card_id = %s"
                    fragment_result = await execute_query_with_retry(fragment_query, (drawn_card['card_id'],))

                    if fragment_result:
                        fragment_id = fragment_result[0]['id_fragment']
                        inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
                        inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

                        if inventory_result:
                            inventory_id = inventory_result[0]['inventory_id']
                            inv_frag_query = """SELECT * FROM inventory_frags 
                                WHERE inventory_id = %s AND frag_id = %s"""
                            inv_frag_result = await execute_query_with_retry(inv_frag_query, (inventory_id, fragment_id))

                            if inv_frag_result:
                                await execute_query_with_retry("""
                                    UPDATE inventory_frags 
                                    SET quantiter = quantiter + 1 
                                    WHERE inventory_id = %s AND frag_id = %s
                                """, (inventory_id, fragment_id))
                            else:
                                await execute_query_with_retry("""
                                    INSERT INTO inventory_frags (inventory_id, frag_id, quantiter)
                                    VALUES (%s, %s, 1)
                                """, (inventory_id, fragment_id))

                            total_fragments_query = """
                                SELECT SUM(quantiter) as total_fragments
                                FROM inventory_frags
                                WHERE inventory_id = %s
                            """
                            total_fragments_result = await execute_query_with_retry(total_fragments_query, (inventory_id,))
                            total_fragments = total_fragments_result[0]['total_fragments']

                            if total_fragments <= fragment_cap:
                                await execute_query_with_retry("""
                                    UPDATE user
                                    SET quantiter_fragment = %s
                                    WHERE user_id = %s
                                """, (total_fragments, user_id))

                            description = "You already have this card.\n\n"
                            footer_text = f"You've received an Essence Stone instead! Total fragments: {total_fragments}/{fragment_cap}"

            # Create embed
            rank_colors = {
                2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
                3: discord.Color.from_rgb(139, 0, 0),    # A Rank - Purple
                4: discord.Color.from_rgb(255, 165, 0),  # B Rank - Blue
                5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
                6: discord.Color.from_rgb(0, 128, 128),  # D Rank - Red
                7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
            }
            embed_color = rank_colors.get(drawn_card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))

            # Set role based on card type
            role = "Offence" if drawn_card['type_card'] == "Fighter" else drawn_card['type_card']

            # Create description with emojis
            description += (
                f"**Card Information**\n"
                f"<:solopower:1302936147139235882> Power: {drawn_card['power']}\n"
                f"<:solotype:1302937991869759539> Class: {drawn_card['card_class_name']}\n"
                f"<:solorole:1302938003873726526> Role: {role}\n\n"
            )

            # Modify the stats display based on card type
            if drawn_card['type_card'].lower() in ['support', 'booster']:
                description += (
                    f"**Effect**\n"
                    f"{drawn_card['description']}"
                )
            else:
                description += (
                    f"**Combat Statistics**\n"
                    f"<:solosword:1302911919941746730> Attack: {drawn_card['attack']}\n"
                    f"<:soloheart:1302912058819477514> Health: {drawn_card['health']}\n"
                    f"<:solostar:1302912104264761374> Speed: {drawn_card['speed']}\n"
                    f"<:soloshield:1302911930242961461> Defense: {drawn_card['defence']}"
                )

            embed = discord.Embed(
                title=f"{drawn_card['card_name']}",
                description=description,
                color=embed_color
            )

            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = self.get_image_path(drawn_card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Handle card image (main image)
            card_image_path = self.get_image_path(drawn_card['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")
                
            if guaranteed_special:
                footer_text = "🌟 Special guaranteed card obtained! Roll counter reset!"
            else:
                if not footer_text:
                    sl_quotes = [
                        "The true hunter never rests.",
                        "Your strength defines your fate.",
                        "Another card, another step to greatness.",
                        "The shadows whisper, will you answer?"
                    ]
                    footer_text = random.choice(sl_quotes)

                # Fetch the latest roll count after all operations
                latest_rolls_query = "SELECT roll_count, roll_cap, roll_total FROM user WHERE user_id = %s"
                latest_rolls = await execute_query_with_retry(latest_rolls_query, (user_id,))
                
                if latest_rolls:
                    latest_roll_count = latest_rolls[0]['roll_count']
                    latest_roll_cap = latest_rolls[0]['roll_cap']
                    latest_roll_total = latest_rolls[0]['roll_total'] or 0
                else:
                    latest_roll_count = roll_count
                    latest_roll_cap = roll_cap
                    latest_roll_total = new_roll_total
                    
                roll_total_display = 0 if guaranteed_special else latest_roll_total

                # Add premium status indicator
                premium_indicator = ""
                if premium_status > 0:
                    premium_tiers = {
                        1: "🥉",  # Bronze
                        2: "🥈",  # Silver
                        3: "🥇"   # Gold
                    }
                    premium_indicator = f" {premium_tiers.get(premium_status, '')} "

                footer_text += (f"{premium_indicator}| Rolls: {latest_roll_count}/"
                            f"{latest_roll_cap} | Total Rolls: {roll_total_display}")

                # Add remaining rolls information based on premium status
                if premium_status == 0:
                    remaining_rolls = 250 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | A-Rank in {remaining_rolls} rolls"
                elif premium_status == 1:
                    remaining_rolls = 200 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | A-Rank in {remaining_rolls} rolls"
                elif premium_status == 2:
                    remaining_rolls = 150 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | A-Rank in {remaining_rolls} rolls"
                elif premium_status == 3:
                    remaining_rolls = 150 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | S-Rank in {remaining_rolls} rolls"

            if ctx.author.avatar:
                embed.set_footer(text=footer_text, icon_url=ctx.author.avatar.url)
            else:
                embed.set_footer(text=footer_text) 
            embed.timestamp = discord.utils.utcnow()
            
            await self.update_quest_progress(user_id)
            await self.check_and_update_referral_status(user_id)
            
            # Send the message with all files at once
            if files:
                await ctx.send(embed=embed, files=files)
            else:
                await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error in arise command: {e}")
            await ctx.send("An error occurred while processing your request. Please try again.")

    @commands.command(aliases=["mp", "mpa"])
    async def multiarise(self, ctx):
        """Pull multiple cards at once up to roll cap - Premium 2+ only"""
        try:
            # Check if user is already in a multiarise session
            if ctx.author.id in self.multiarise_sessions:
                await ctx.send("You're already in a multiarise session. Please wait for it to complete.")
                return
                
            # Add user to session
            self.multiarise_sessions.add(ctx.author.id)
            
            # STEP 1: Quick initial check for premium status only
            quick_check_query = """SELECT prenium, prenium_date, first_joined, second_joined, third_joined 
                                FROM user WHERE user_authorid = %s"""
            quick_result = await execute_query_with_retry(quick_check_query, (str(ctx.author.id),))
            
            if not quick_result:
                await ctx.send("❌ You need to register to the bot first.")
                self.multiarise_sessions.discard(ctx.author.id)
                return
            
            premium_status = quick_result[0]['prenium']
            premium_date = quick_result[0]['prenium_date']
            first_joined = quick_result[0]['first_joined'] or 0
            second_joined = quick_result[0]['second_joined'] or 0
            third_joined = quick_result[0]['third_joined'] or 0
            
            # Check premium expiration (30 days)
            current_time = datetime.utcnow()
            if premium_status > 0 and premium_date:
                time_difference = current_time - premium_date
                days_since_premium = time_difference.days
                
                if days_since_premium >= 30:
                    # Premium has expired, reduce roll_cap based on previous premium level
                    roll_cap_reduction = 0
                    if premium_status == 1:
                        roll_cap_reduction = 1
                    elif premium_status == 2:
                        roll_cap_reduction = 2
                    elif premium_status == 3:
                        roll_cap_reduction = 3
                    
                    # Reset premium status and reduce roll_cap
                    await execute_query_with_retry(
                        "UPDATE user SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s WHERE user_authorid = %s",
                        (roll_cap_reduction, str(ctx.author.id))
                    )
                    
                    error_embed = discord.Embed(
                        title="⏰ Premium Subscription Expired",
                        description=f"Your Premium {premium_status} subscription has expired after 30 days.\n\n"
                                f"Your roll cap has been reduced by {roll_cap_reduction}.\n\n"
                                "Please renew your subscription to continue using Multi Arise.",
                        color=discord.Color.orange()
                    )
                    error_embed.add_field(
                        name="Subscription Date", 
                        value=premium_date.strftime("%Y-%m-%d"), 
                        inline=True
                    )
                    error_embed.add_field(
                        name="Days Elapsed", 
                        value=f"{days_since_premium} days", 
                        inline=True
                    )
                    await ctx.send(embed=error_embed)
                    self.multiarise_sessions.discard(ctx.author.id)
                    return
                else:
                    # Premium is valid, check for join bonuses
                    if premium_status == 1 and first_joined == 0:
                        await execute_query_with_retry(
                            "UPDATE user SET first_joined = 1, roll_cap = roll_cap + 1 WHERE user_authorid = %s",
                            (str(ctx.author.id),)
                        )
                    elif premium_status == 2 and second_joined == 0:
                        await execute_query_with_retry(
                            "UPDATE user SET second_joined = 1, roll_cap = roll_cap + 2 WHERE user_authorid = %s",
                            (str(ctx.author.id),)
                        )
                    elif premium_status == 3 and third_joined == 0:
                        await execute_query_with_retry(
                            "UPDATE user SET third_joined = 1, roll_cap = roll_cap + 3 WHERE user_authorid = %s",
                            (str(ctx.author.id),)
                        )
            
            # PREMIUM CHECK: Only allow premium 2 and 3 users
            if premium_status < 2:
                error_embed = discord.Embed(
                    title="🔒 Premium Feature",
                    description="Multi Arise is a premium feature available only to **Premium 2** and **Premium 3** subscribers.\n\n"
                            "Upgrade your subscription to access this feature!",
                    color=discord.Color.red()
                )
                error_embed.add_field(
                    name="Current Status", 
                    value=f"Premium {premium_status}" if premium_status > 0 else "Free User", 
                    inline=True
                )
                error_embed.add_field(
                    name="Required", 
                    value="Premium 2 or Premium 3", 
                    inline=True
                )
                await ctx.send(embed=error_embed)
                self.multiarise_sessions.discard(ctx.author.id)
                return
            
            # STEP 2: SHOW LOADING GIF IMMEDIATELY after premium check
            loading_embed = discord.Embed(
                title="🎴 Multi Arise Starting...",
                description="⏳ Initializing your card pull session...",
                color=discord.Color.blue()
            )
            
            # Load the loading gif immediately
            loading_gif_path = self.get_image_path("load.gif")
            files = []
            if loading_gif_path and os.path.exists(loading_gif_path):
                loading_file = discord.File(loading_gif_path, filename="loading.gif")
                files.append(loading_file)
                loading_embed.set_image(url="attachment://loading.gif")
            
            # Send loading message FIRST - before any heavy processing
            if files:
                message = await ctx.send(embed=loading_embed, files=files)
            else:
                message = await ctx.send(embed=loading_embed)
            
            # STEP 3: OPTIMIZE - Get all necessary user data in a single query
            try:
                # Fetch all user data in one optimized query
                user_query = """
                    SELECT u.user_id, u.quantiter_fragment, u.fragment_cap, 
                        u.roll_timer, u.roll_count, u.roll_cap, u.roll_total, u.joined_server,
                        i.inventory_id
                    FROM user u
                    LEFT JOIN inventory i ON u.user_id = i.user_id
                    WHERE u.user_authorid = %s
                """
                user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
                    
                if not user_result:
                    error_embed = discord.Embed(
                        title="❌ Error",
                        description="Error retrieving your data. Please try again.",
                        color=discord.Color.red()
                    )
                    await message.edit(embed=error_embed, attachments=[])
                    return
                    
                user_id = user_result[0]['user_id']
                inventory_id = user_result[0]['inventory_id']
                current_fragments = user_result[0]['quantiter_fragment'] or 0
                fragment_cap = user_result[0]['fragment_cap'] or 0
                current_time = datetime.utcnow()
                roll_timer = user_result[0]['roll_timer']
                roll_count = user_result[0]['roll_count']
                roll_cap = user_result[0]['roll_cap']
                roll_total = user_result[0]['roll_total'] or 0
                
                # Calculate how many cards can be pulled - OPTIMIZED
                if roll_timer is None:
                    available_rolls = roll_cap
                    cards_to_pull = roll_cap
                else:
                    time_difference = current_time - roll_timer
                    hours_difference = time_difference.total_seconds() / 3600
                    
                    if hours_difference >= 4:
                        available_rolls = roll_cap
                        cards_to_pull = roll_cap
                    else:
                        available_rolls = max(0, roll_cap - roll_count)
                        cards_to_pull = available_rolls
                
                # Check if user has any rolls available
                if available_rolls == 0:
                    if roll_timer:
                        time_difference = current_time - roll_timer
                        remaining_seconds = (4 * 3600) - time_difference.total_seconds()
                        hours = int(remaining_seconds // 3600)
                        minutes = int((remaining_seconds % 3600) // 60)
                        seconds = int(remaining_seconds % 60)
                        
                        error_embed = discord.Embed(
                            title="⏰ Roll Limit Reached",
                            description=f"You've reached your daily roll limit!\n\nTime until reset: **{hours}h {minutes}m {seconds}s**",
                            color=discord.Color.red()
                        )
                        await message.edit(embed=error_embed, attachments=[])
                        return
                
                # Define guaranteed card thresholds based on premium status
                if premium_status == 0:
                    threshold = 250  # Free: 250 pulls for A rank
                    guaranteed_rank = 3  # A rank
                    guaranteed_card_text = "A rank card"
                elif premium_status == 1:
                    threshold = 200  # Premium 1: 200 pulls for A rank
                    guaranteed_rank = 3  # A rank
                    guaranteed_card_text = "A rank card"
                elif premium_status == 2:
                    threshold = 150  # Premium 2: 150 pulls for A rank
                    guaranteed_rank = 3  # A rank
                    guaranteed_card_text = "A rank card"
                elif premium_status == 3:
                    threshold = 150  # Premium 3: 150 pulls for S rank
                    guaranteed_rank = 2  # S rank
                    guaranteed_card_text = "S rank card"
                
                # Calculate guaranteed card info for display
                pulls_until_guaranteed = threshold - roll_total
                
                # Update loading message with card count and guaranteed card info
                loading_embed.title = "🎴 Multi Arise in Progress..."
                loading_embed.description = f"⏳ Pulling {cards_to_pull} cards for you!"
                loading_embed.add_field(
                    name="🎯 Guaranteed Card Progress",
                    value=f"Pulls until guaranteed {guaranteed_card_text}: **{pulls_until_guaranteed}/{threshold}**",
                    inline=False
                )
                try:
                    await message.edit(embed=loading_embed)
                except:
                    pass
                
                # STEP 4: OPTIMIZE - Pull cards with minimal database operations
                pulled_cards = []
                essence_stones_info = {}
                new_cards_count = 0
                essence_stones_capped = False
                essence_stones_message = ""
                fragment_updates = {}  # Track fragment updates to batch them
                guaranteed_card_found = False  # Track if we found a guaranteed card during pulls
                fragments_given = 0  # Track how many fragments we've given during this session
                
                # Check if already at fragment cap before starting
                if current_fragments >= fragment_cap:
                    essence_stones_capped = True
                    essence_stones_message = "Essence stones limit already reached!"
                
                # OPTIMIZATION: Pre-fetch user's existing cards to avoid repeated queries
                existing_cards_query = "SELECT card_id FROM user_cards WHERE user_id = %s"
                existing_cards_result = await execute_query_with_retry(existing_cards_query, (user_id,))
                existing_card_ids = {row['card_id'] for row in existing_cards_result} if existing_cards_result else set()
                
                # Track the current roll total for guaranteed card calculation
                current_roll_total = roll_total
                
                # Pull all cards at once
                for i in range(cards_to_pull):
                    # Update progress only occasionally to reduce message edits
                    if i == 0 or i % 5 == 0 or i == cards_to_pull - 1:
                        progress_embed = discord.Embed(
                            title="🎴 Multi Arise in Progress...",
                            description=f"⏳ Pulling card {i + 1}/{cards_to_pull}...",
                            color=discord.Color.blue()
                        )
                        if loading_gif_path and os.path.exists(loading_gif_path):
                            progress_embed.set_image(url="attachment://loading.gif")
                        
                        # Add guaranteed card info to progress updates
                        pulls_until_guaranteed = max(0, threshold - current_roll_total)
                        progress_embed.add_field(
                            name="🎯 Guaranteed Card Progress",
                            value=f"Pulls until guaranteed {guaranteed_card_text}: **{pulls_until_guaranteed}/{threshold}**",
                            inline=False
                        )
                        
                        try:
                            await message.edit(embed=progress_embed)
                        except:
                            pass
                    
                    # Check for guaranteed cards
                    # Check for guaranteed cards
                    guaranteed_special = False
                    drawn_card = None
                    current_roll_total += 1  # Increment for this pull

                    if current_roll_total % threshold == 0 and current_roll_total > 0:
                        drawn_card = await self.get_guaranteed_rank_card(guaranteed_rank)
                        if drawn_card:
                            guaranteed_special = True
                            guaranteed_card_found = True

                    if not drawn_card:
                        drawn_card = await self.get_valid_card(premium_status)

                    if drawn_card:
                        pulled_cards.append(drawn_card)
                        
                        # If this is a guaranteed card, mark it for special display
                        if guaranteed_special:
                            drawn_card['guaranteed'] = True
                            # Get card rank name
                            card_rank_query = "SELECT cardrank_name FROM cardrank WHERE cardrank_id = %s"
                            card_rank_result = await execute_query_with_retry(card_rank_query, (drawn_card['card_rank'],))
                            if card_rank_result:
                                drawn_card['card_rank_name'] = card_rank_result[0]['cardrank_name']
                        
                        # Check if user already has the card
                        if drawn_card['card_id'] not in existing_card_ids:
                            # Add new card
                            new_cards_count += 1
                            existing_card_ids.add(drawn_card['card_id'])  # Update our local cache
                            
                            # Handle special cards
                            if drawn_card['card_id'] == 103:
                                await execute_query_with_retry(
                                    "UPDATE user SET roll_cap = roll_cap + 1 WHERE user_id = %s", 
                                    (user_id,)
                                )
                            if drawn_card['card_id'] == 104:
                                await execute_query_with_retry(
                                    "UPDATE user SET fragment_cap = fragment_cap + 100 WHERE user_id = %s", 
                                    (user_id,)
                                )
                                fragment_cap += 100  # Update local variable

                            await execute_query_with_retry("""
                                INSERT INTO user_cards (user_id, card_id, current_level, current_xp, 
                                current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name, 
                                defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                                weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                                power_emoji, type_emoji, class_emoji) 
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                                %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                user_id, drawn_card['card_id'], drawn_card['min_level'], 0, 0,
                                drawn_card['power'], drawn_card['attack'], drawn_card['health'], 
                                drawn_card['speed'], drawn_card['defence'], drawn_card['attack'], drawn_card['health'], 
                                drawn_card['speed'], drawn_card['card_name'], drawn_card['defence'],
                                '<:solosword:1302911919941746730>', '<:soloshield:1302911930242961461>',
                                '<:solostar:1302912104264761374>', '<:soloheart:1302912058819477514>',
                                '<:soloweapon:1302961287747342356>', '<:solosecondshield:1302959829413007380>',
                                '<:armor64:1311994697794850897>', '<:soloxp:1302960018747822080>',
                                '<:sololevel:1302959449220321350>', '<:solopower:1302936147139235882>',
                                '<:solorole:1302938003873726526>', '<:solotype:1302937991869759539>'
                            ))
                        else:
                            # Handle essence stones for duplicate cards with fragment cap checking
                            if not essence_stones_capped:
                                fragment_query = """
                                    SELECT c.id_fragment, f.name_fragment, f.fragments_icon 
                                    FROM card c 
                                    JOIN fragments f ON c.id_fragment = f.id_fragment 
                                    WHERE c.card_id = %s
                                """
                                fragment_result = await execute_query_with_retry(fragment_query, (drawn_card['card_id'],))

                                if fragment_result:
                                    fragment_id = fragment_result[0]['id_fragment']
                                    fragment_name = fragment_result[0]['name_fragment']
                                    fragment_icon = fragment_result[0]['fragments_icon']
                                    
                                    # FIXED: Check if we can give this fragment without exceeding cap
                                    if current_fragments + fragments_given < fragment_cap:
                                        # Add to essence stones info with icon
                                        if fragment_name not in essence_stones_info:
                                            essence_stones_info[fragment_name] = {
                                                'quantity': 0,
                                                'icon': fragment_icon,
                                                'id': fragment_id
                                            }
                                        essence_stones_info[fragment_name]['quantity'] += 1
                                        fragments_given += 1  # Track fragments given in this session
                                        
                                        # Track fragment updates for batch processing
                                        if fragment_id not in fragment_updates:
                                            fragment_updates[fragment_id] = 0
                                        fragment_updates[fragment_id] += 1
                                        
                                        # Check if we've now reached the cap
                                        if current_fragments + fragments_given >= fragment_cap:
                                            essence_stones_capped = True
                                            essence_stones_message = f"Essence stones limit reached at {fragment_cap}! No more fragments given for remaining duplicates."
                                    else:
                                        # Fragment cap would be exceeded, stop giving fragments
                                        if not essence_stones_capped:
                                            essence_stones_capped = True
                                            essence_stones_message = f"Essence stones limit reached at {fragment_cap}! No more fragments given for remaining duplicates."
                
                # STEP 5: OPTIMIZE - Batch update fragments in inventory
                if fragment_updates and inventory_id:
                    # Process all fragment updates in batch
                    for fragment_id, quantity in fragment_updates.items():
                        # Check if fragment already exists in inventory
                        check_query = "SELECT quantiter FROM inventory_frags WHERE inventory_id = %s AND frag_id = %s"
                        check_result = await execute_query_with_retry(check_query, (inventory_id, fragment_id))
                        
                        if check_result:
                            # Update existing fragment
                            await execute_query_with_retry(
                                "UPDATE inventory_frags SET quantiter = quantiter + %s WHERE inventory_id = %s AND frag_id = %s",
                                (quantity, inventory_id, fragment_id)
                            )
                        else:
                            # Insert new fragment
                            await execute_query_with_retry(
                                "INSERT INTO inventory_frags (inventory_id, frag_id, quantiter) VALUES (%s, %s, %s)",
                                (inventory_id, fragment_id, quantity)
                            )
                
                # Show final processing message
                final_loading_embed = discord.Embed(
                    title="🎴 Multi Arise in Progress...",
                    description=f"✅ Pulled {cards_to_pull} cards!\n⏳ Creating results display...",
                    color=discord.Color.blue()
                )
                if loading_gif_path and os.path.exists(loading_gif_path):
                    final_loading_embed.set_image(url="attachment://loading.gif")
                
                try:
                    await message.edit(embed=final_loading_embed)
                except:
                    pass

                # Update roll counts and timer
                if roll_timer is None or (current_time - roll_timer).total_seconds() / 3600 >= 4:
                    await execute_query_with_retry(
                        "UPDATE user SET roll_timer = %s, roll_count = %s, roll_total = %s WHERE user_id = %s",
                        (current_time, cards_to_pull, current_roll_total, user_id)
                    )
                    new_roll_count = cards_to_pull
                else:
                    new_roll_count = roll_count + cards_to_pull
                    await execute_query_with_retry(
                        "UPDATE user SET roll_count = %s, roll_total = %s WHERE user_id = %s",
                        (new_roll_count, current_roll_total, user_id)
                    )

                # FIXED: Update fragment count correctly - ensure it never goes negative
                if fragment_updates:
                    # Calculate the new fragment total
                    new_fragment_total = current_fragments + fragments_given
                    # Ensure it doesn't exceed the cap and isn't negative
                    new_fragment_total = max(0, min(new_fragment_total, fragment_cap))
                    
                    # Update the user's fragment count with the correct total
                    await execute_query_with_retry(
                        "UPDATE user SET quantiter_fragment = %s WHERE user_id = %s",
                        (new_fragment_total, user_id)
                    )
                    
                    # Update our local variable for display
                    current_fragments = new_fragment_total

                # Update quest progress in batch
                for _ in range(cards_to_pull):
                    await self.update_quest_progress(user_id)
                    await self.check_and_update_referral_status(user_id)

                # Create results embed
                results_embed = discord.Embed(
                    title=f"🎴 Multi Arise Results - {cards_to_pull} Cards Pulled!",
                    color=discord.Color.green()
                )
                
                # Add guaranteed card info if one was found during pulls
                if guaranteed_card_found:
                    for card in pulled_cards:
                        if card.get('guaranteed'):
                            card_name = card['card_name']
                            card_rank_name = card.get('card_rank_name', 'Unknown')
                            
                            if premium_status == 0 or premium_status == 1 or premium_status == 2:
                                results_embed.add_field(
                                    name="🎯 Guaranteed Card Found!",
                                    value=f"You pulled your guaranteed A rank card: **{card_name}**!",
                                    inline=False
                                )
                            elif premium_status == 3:
                                results_embed.add_field(
                                    name="🎯 Guaranteed Card Found!",
                                    value=f"You pulled your guaranteed S rank card: **{card_name}**!",
                                    inline=False
                                )
                            break
                
                # Add essence stones info with proper icons and cap message
                if essence_stones_info:
                    essence_text = ""
                    for fragment_name, info in essence_stones_info.items():
                        quantity = info['quantity']
                        icon = info['icon'] if info['icon'] else '<:fragment:1234567890>'
                        essence_text += f"x{quantity} {icon} {fragment_name} Essence Stones\n"
                    
                    if essence_text:
                        results_embed.add_field(
                            name="🔮 Essence Stones Received",
                            value=essence_text,
                            inline=False
                        )
                        
                        # Add fragment count info
                        results_embed.add_field(
                            name="Fragment Count", 
                            value=f"{current_fragments}/{fragment_cap}", 
                            inline=True
                        )
                
                # Add essence stones cap message if applicable
                if essence_stones_capped and essence_stones_message:
                    results_embed.add_field(
                        name="⚠️ Fragment Cap Notice",
                        value=essence_stones_message,
                        inline=False
                    )
                elif current_fragments >= fragment_cap and not essence_stones_info:
                    results_embed.add_field(
                        name="⚠️ Fragment Cap Notice",
                        value="Essence stones limit reached! No fragments given for duplicate cards.",
                        inline=False
                    )
                
                # Add new cards count
                if new_cards_count > 0:
                    results_embed.add_field(
                        name="✨ New Cards",
                        value=f"{new_cards_count} new cards added to your collection!",
                        inline=False
                    )
                
                # Create and attach card grid image
                view = MultiAriseView(ctx, pulled_cards, essence_stones_info)
                grid_image_bytes = view.create_card_grid_image()
                
                files = []
                if grid_image_bytes:
                    grid_file = discord.File(grid_image_bytes, filename="multiarise_results.png")
                    files.append(grid_file)
                    results_embed.set_image(url="attachment://multiarise_results.png")
                
                # Add footer with roll info and guaranteed card info
                premium_indicator = ""
                if premium_status > 0:
                    premium_tiers = {1: "🥉", 2: "🥈", 3: "🥇"}
                    premium_indicator = f" {premium_tiers.get(premium_status, '')} "
                
                # Base footer text
                roll_total_display = current_roll_total
                footer_text = f"{premium_indicator}Rolls: {new_roll_count}/{roll_cap} | Total Rolls: {roll_total_display}"
                
                # Add guaranteed card info to footer
                if premium_status == 0:
                    remaining_rolls = 250 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | A-Rank in {remaining_rolls} rolls"
                elif premium_status == 1:
                    remaining_rolls = 200 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | A-Rank in {remaining_rolls} rolls"
                elif premium_status == 2:
                    remaining_rolls = 150 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | A-Rank in {remaining_rolls} rolls"
                elif premium_status == 3:
                    remaining_rolls = 150 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += f" | S-Rank in {remaining_rolls} rolls"
                
                results_embed.set_footer(text=footer_text)
                results_embed.timestamp = discord.utils.utcnow()
                
                # Edit message with final results
                if files:
                    await message.edit(embed=results_embed, attachments=files)
                else:
                    await message.edit(embed=results_embed)
                    
            finally:
                # Always remove user from session when done
                self.multiarise_sessions.discard(ctx.author.id)
                
        except Exception as e:
            print(f"Error in multiarise command: {e}")
            self.multiarise_sessions.discard(ctx.author.id)
            await ctx.send(f"An error occurred while processing your multiarise request. Please try again.")

    @commands.command(name="mpreset")
    async def multiarise_reset(self, ctx, user_id: int = None):
        """Reset a user's multiarise session (Owner only)"""
        if ctx.author.id != 818132161534230559:
            await ctx.send("You don't have permission to use this command.")
            return
        
        target_user_id = user_id if user_id else ctx.author.id
        
        if target_user_id in self.multiarise_sessions:
            self.multiarise_sessions.discard(target_user_id)
            await ctx.send(f"✅ Multiarise session reset for user {target_user_id}")
        else:
            await ctx.send(f"❌ User {target_user_id} is not in a multiarise session.")
    @commands.command(name="givepremium")
    async def give_premium(self, ctx, *, args):
        """
        Owner-only command to give premium status to a user.
        Usage: sl givepremium <discord_id>,<premium_level>
        """
        try:
            # Check if the command invoker is the owner
            if ctx.author.id != 818132161534230559:
                await ctx.send("❌ You don't have permission to use this command.")
                return
            
            # Parse arguments
            if ',' not in args:
                await ctx.send("❌ Invalid format. Use: `sl givepremium <discord_id>,<premium_level>`")
                return
                
            discord_id, premium_level = args.split(',', 1)
            discord_id = discord_id.strip()
            premium_level = premium_level.strip()
            
            # Validate discord_id is a number
            try:
                discord_id = int(discord_id)
            except ValueError:
                await ctx.send("❌ Discord ID must be a number.")
                return
            
            # Validate premium_level is a number
            try:
                premium_level = int(premium_level)
            except ValueError:
                await ctx.send("❌ Premium level must be a number.")
                return
            
            # Validate premium level range
            if premium_level < 0 or premium_level > 3:
                await ctx.send("❌ Premium level must be between 0 and 3.")
                return
            
            # Check if user exists in database
            user_query = "SELECT user_id, prenium, prenium_date FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(discord_id),))
            
            if not user_result:
                await ctx.send(f"❌ User with Discord ID {discord_id} not found in the database.")
                return
            
            user_id = user_result[0]['user_id']
            current_premium = user_result[0]['prenium']
            current_premium_date = user_result[0]['prenium_date']
            current_time = datetime.utcnow()
            
            # Get user's display name for better UX
            try:
                target_user = await self.bot.fetch_user(discord_id)
                user_display_name = target_user.display_name
            except:
                user_display_name = f"User {discord_id}"
            
            # Case 1: User has no premium (prenium = 0)
            if current_premium == 0:
                # Set premium date to current time if premium_level > 0, otherwise null
                premium_date_value = current_time if premium_level > 0 else None
                
                await execute_query_with_retry(
                    "UPDATE user SET prenium = %s, prenium_date = %s WHERE user_id = %s",
                    (premium_level, premium_date_value, user_id)
                )
                
                if premium_level == 0:
                    success_message = f"✅ Successfully reset premium status for {user_display_name} to Free User."
                else:
                    success_message = f"✅ Successfully granted Premium {premium_level} to {user_display_name}!"
                
                embed = discord.Embed(
                    title="Premium Status Updated",
                    description=success_message,
                    color=discord.Color.green()
                )
                embed.add_field(name="User", value=user_display_name, inline=True)
                embed.add_field(name="New Premium Level", value=f"Premium {premium_level}" if premium_level > 0 else "Free User", inline=True)
                if premium_level > 0:
                    embed.add_field(name="Subscription Date", value=current_time.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
                
                await ctx.send(embed=embed)
                return
            
            # Case 2: User already has premium (prenium > 0)
            if current_premium_date:
                time_difference = current_time - current_premium_date
                days_since_premium = time_difference.days
                
                # Check if user is still within 30 days subscription
                if 1 <= days_since_premium < 30:
                    # User is still within subscription period - ask for confirmation
                    confirm_embed = discord.Embed(
                        title="⚠️ Active Subscription Detected",
                        description=f"{user_display_name} still has an active Premium {current_premium} subscription.\n\n"
                                f"**Subscription Details:**\n"
                                f"• Current Level: Premium {current_premium}\n"
                                f"• Subscription Date: {current_premium_date.strftime('%Y-%m-%d')}\n"
                                f"• Days Elapsed: {days_since_premium}/30 days\n"
                                f"• Days Remaining: {30 - days_since_premium} days\n\n"
                                f"Are you sure you want to update to Premium {premium_level}?",
                        color=discord.Color.orange()
                    )
                    
                    # Create confirmation view
                    class PremiumConfirmView(discord.ui.View):
                        def __init__(self):
                            super().__init__(timeout=60)
                            self.value = None

                        @discord.ui.button(label="Confirm Update", style=discord.ButtonStyle.green)
                        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                            if interaction.user.id != ctx.author.id:
                                await interaction.response.send_message("Only the command invoker can use this button.", ephemeral=True)
                                return
                            self.value = True
                            for item in self.children:
                                item.disabled = True
                            await interaction.response.edit_message(view=self)
                            self.stop()

                        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
                        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                            if interaction.user.id != ctx.author.id:
                                await interaction.response.send_message("Only the command invoker can use this button.", ephemeral=True)
                                return
                            self.value = False
                            for item in self.children:
                                item.disabled = True
                            await interaction.response.edit_message(view=self)
                            self.stop()
                    
                    confirm_view = PremiumConfirmView()
                    message = await ctx.send(embed=confirm_embed, view=confirm_view)
                    
                    # Wait for confirmation
                    await confirm_view.wait()
                    
                    if confirm_view.value is None:
                        await message.edit(content="❌ Premium update timed out.", embed=None, view=None)
                        return
                    elif not confirm_view.value:
                        await message.edit(content="❌ Premium update cancelled.", embed=None, view=None)
                        return
                    
                    # User confirmed - update premium
                    premium_date_value = current_time if premium_level > 0 else None
                    
                    await execute_query_with_retry(
                        "UPDATE user SET prenium = %s, prenium_date = %s WHERE user_id = %s",
                        (premium_level, premium_date_value, user_id)
                    )
                    
                    if premium_level == 0:
                        success_message = f"✅ Successfully reset premium status for {user_display_name} to Free User."
                    else:
                        success_message = f"✅ Successfully updated {user_display_name} to Premium {premium_level}!"
                    
                    success_embed = discord.Embed(
                        title="Premium Status Updated",
                        description=success_message,
                        color=discord.Color.green()
                    )
                    success_embed.add_field(name="User", value=user_display_name, inline=True)
                    success_embed.add_field(name="Previous Level", value=f"Premium {current_premium}", inline=True)
                    success_embed.add_field(name="New Level", value=f"Premium {premium_level}" if premium_level > 0 else "Free User", inline=True)
                    if premium_level > 0:
                        success_embed.add_field(name="New Subscription Date", value=current_time.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
                    
                    await message.edit(embed=success_embed, view=None)
                    return
                
                else:
                    # User is outside 30 days (expired or <1 day bug) - auto update
                    premium_date_value = current_time if premium_level > 0 else None
                    
                    await execute_query_with_retry(
                        "UPDATE user SET prenium = %s, prenium_date = %s WHERE user_id = %s",
                        (premium_level, premium_date_value, user_id)
                    )
                    
                    if premium_level == 0:
                        success_message = f"✅ Successfully reset premium status for {user_display_name} to Free User."
                        reason = "Subscription expired" if days_since_premium >= 30 else "Subscription date anomaly detected"
                    else:
                        success_message = f"✅ Successfully granted Premium {premium_level} to {user_display_name}!"
                        reason = "Previous subscription expired" if days_since_premium >= 30 else "Previous subscription date anomaly detected"
                    
                    embed = discord.Embed(
                        title="Premium Status Updated",
                        description=success_message,
                        color=discord.Color.green()
                    )
                    embed.add_field(name="User", value=user_display_name, inline=True)
                    embed.add_field(name="Previous Level", value=f"Premium {current_premium}", inline=True)
                    embed.add_field(name="New Level", value=f"Premium {premium_level}" if premium_level > 0 else "Free User", inline=True)
                    embed.add_field(name="Reason", value=reason, inline=True)
                    embed.add_field(name="Days Since Last Subscription", value=f"{days_since_premium} days", inline=True)
                    if premium_level > 0:
                        embed.add_field(name="New Subscription Date", value=current_time.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
                    
                    await ctx.send(embed=embed)
                    return
            
            else:
                # User has premium but no date (data inconsistency) - treat as expired
                premium_date_value = current_time if premium_level > 0 else None
                
                await execute_query_with_retry(
                    "UPDATE user SET prenium = %s, prenium_date = %s WHERE user_id = %s",
                    (premium_level, premium_date_value, user_id)
                )
                
                if premium_level == 0:
                    success_message = f"✅ Successfully reset premium status for {user_display_name} to Free User."
                else:
                    success_message = f"✅ Successfully granted Premium {premium_level} to {user_display_name}!"
                
                embed = discord.Embed(
                    title="Premium Status Updated",
                    description=success_message,
                    color=discord.Color.green()
                )
                embed.add_field(name="User", value=user_display_name, inline=True)
                embed.add_field(name="Previous Level", value=f"Premium {current_premium}", inline=True)
                embed.add_field(name="New Level", value=f"Premium {premium_level}" if premium_level > 0 else "Free User", inline=True)
                embed.add_field(name="Note", value="Fixed missing subscription date", inline=True)
                if premium_level > 0:
                    embed.add_field(name="New Subscription Date", value=current_time.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            print(f"Error in givepremium command: {e}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    async def update_quest_progress(self, user_id):
        try:
            # Check if the user has any active quests
            quest_query = """
                SELECT uq.*, q.quest_name, q.required_count
                FROM user_quests uq
                JOIN quest q ON uq.quest_id = q.quest_id
                WHERE uq.user_id = %s AND q.quest_name = 'Pull 7 cards' AND uq.completion_status = 0
            """
            quest_result = await execute_query_with_retry(quest_query, (user_id,))
            
            if quest_result:
                # Increment the current_count
                new_count = quest_result[0]['current_count'] + 1
                await execute_query_with_retry("""
                    UPDATE user_quests
                    SET current_count = %s
                    WHERE userquests_id = %s
                """, (new_count, quest_result[0]['userquests_id']))
                
                # Check if the quest is completed
                if new_count >= quest_result[0]['required_count']:
                    await execute_query_with_retry("""
                        UPDATE user_quests
                        SET completion_status = 1
                        WHERE userquests_id = %s
                    """, (quest_result[0]['userquests_id'],))
                    
                    # Get the user's inventory_id
                    inventory_query = """
                        SELECT inventory_id 
                        FROM inventory 
                        WHERE user_id = %s
                    """
                    inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
                    
                    if inventory_result:
                        inventory_id = inventory_result[0]['inventory_id']
                        
                        # Check if the user already has a chest with ID 1
                        chest_query = """
                            SELECT * 
                            FROM inventory_chest 
                            WHERE inventory_id = %s AND chest_id = 1
                        """
                        existing_chest = await execute_query_with_retry(chest_query, (inventory_id,))
                        
                        if existing_chest:
                            # Update quantity if chest exists
                            await execute_query_with_retry("""
                                UPDATE inventory_chest 
                                SET quantiter = quantiter + 1 
                                WHERE inventory_id = %s AND chest_id = 1
                            """, (inventory_id,))
                        else:
                            # Insert new chest entry if it doesn't exist
                            await execute_query_with_retry("""
                                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                                VALUES (%s, 1, 1)
                            """, (inventory_id,))
                    
                    print(f"User {user_id} completed the 'Pull 7 cards' quest and received a chest!")
        
        except Exception as e:
            print(f"Error updating quest progress: {e}")
    @commands.command(aliases=["uc"])
    async def usercards(self, ctx, discord_id: str):
        try:
            # Check if the command invoker is registered
            invoker_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            invoker_result = await execute_query_with_retry(invoker_query, (str(ctx.author.id),))

            if not invoker_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            # Check if the target user exists in the database
            target_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            target_result = await execute_query_with_retry(target_query, (discord_id,))

            if not target_result:
                await ctx.send(f"Error: User with Discord ID {discord_id} not found in the database.")
                return

            target_user_id = target_result[0]['user_id']
            target_username = await self.get_user_name_by_discord_id(int(ctx.author.id))

            # Get all cards for the target user
            cards_query = """
            SELECT uc.*, 
                c.card_name, c.image, c.xp_cap, c.max_level, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id, 
                cc.card_class_name, 
                w.weapon_id, w.weapon_name, w.weapon_rank,
                a.weapon_id as armor_id, a.weapon_name as armor_name, a.weapon_rank as armor_rank,
                s.weapon_id as shield_id, s.weapon_name as shield_name, s.weapon_rank as shield_rank,
                uc.pseudo_name, uc.pseudo_image
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            WHERE uc.user_id = %s
            ORDER BY power_stat DESC
            """
            cards = await execute_query_with_retry(cards_query, (target_user_id,))

            if not cards:
                await ctx.send(f"User {target_username} doesn't have any cards.")
                return

            # Create a view similar to AllView for pagination
            view = AllViewd(ctx, cards)
            embed = view.create_embed()
            
            # Add title to show whose cards are being viewed
            embed.title = f"{target_username}'s {embed.title}"
            
            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = view.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Handle card image (main image)
            card_image_path = view.get_image_path(cards[0]['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            # Send the message with all files
            if files:
                view.message = await ctx.send(embed=embed, files=files, view=view)
            else:
                view.message = await ctx.send(embed=embed, view=view)

            await view.wait()
            
            try:
                await view.message.edit(view=None)
            except:
                pass

        except Exception as e:
            print(f"Error in usercards command: {e}")
            await ctx.send("An error occurred while processing your request.")
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
    @commands.command(name="resetcard")
    @commands.has_permissions(administrator=True)
    async def reset_card(self, ctx, user: discord.Member, *, card_name):
        """
        Admin command to reset a card's stats to default values.
        Usage: sl resetcard @user card_name
        """
        try:
            if ctx.author.id != 818132161534230559:
                    await ctx.send("You don't have permission to use this command.")
                    return 
            # Get the Discord ID from the mentioned user
            discord_id = user.id
            
            # Check if user exists in database
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(discord_id),))
            
            if not user_result:
                await ctx.send(f"❌ User {user.mention} not found in the database.")
                return
                
            user_id = user_result[0]['user_id']
            
            # Check if card exists in database
            card_query = """
                SELECT c.card_id, c.card_name, c.attack, c.defence, c.speed, c.health, c.power
                FROM card c
                WHERE c.card_name LIKE %s
            """
            card_results = await execute_query_with_retry(card_query, (f"%{card_name}%",))
            
            if not card_results:
                await ctx.send(f"❌ Card '{card_name}' not found in the database.")
                return
                
            if len(card_results) > 1:
                # Multiple cards found, list them
                card_names = [f"• {card['card_name']}" for card in card_results]
                await ctx.send(f"⚠️ Multiple cards found matching your description. Please be more specific:\\n{chr(10).join(card_names)}")
                return
                
            # Get the card
            card = card_results[0]
            card_id = card['card_id']
            
            # Check if user has the card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, card_id))
            
            if not user_card_result:
                await ctx.send(f"❌ User {user.mention} does not have the card '{card['card_name']}'.")
                return
            
            # Create confirmation embed
            embed = discord.Embed(
                title="⚠️ Reset Card Confirmation",
                description=f"Are you sure you want to reset the card '{card['card_name']}' for user {user.mention}?\\n\\n"
                           f"This will reset all stats to default values and remove all stat points.",
                color=discord.Color.red()
            )
            
            # Create confirmation buttons
            confirm_view = ConfirmView()
            message = await ctx.send(embed=embed, view=confirm_view)
            
            # Wait for confirmation
            await confirm_view.wait()
            
            if confirm_view.value is None:
                await message.edit(content="❌ Reset card operation timed out.", embed=None, view=None)
                return
            elif not confirm_view.value:
                await message.edit(content="❌ Reset card operation cancelled.", embed=None, view=None)
                return
            
            # First update: Reset all stats to default values from card table
            update_query1 = """
                UPDATE user_cards u, card c 
                SET u.attack_stat = c.attack, 
                    u.defence_stat = c.defence, 
                    u.speed_stat = c.speed, 
                    u.health_stat = c.health,
                    u.power_stat = c.power, 
                    u.current_xp = 0, 
                    u.current_level = 1, 
                    u.current_statpoints = 0, 
                    u.used_attackpoints = 0, 
                    u.used_healthpoints = 0, 
                    u.used_defencepoints = 0, 
                    u.used_speedpoints = 0 
                WHERE u.user_id = %s 
                AND c.card_id = %s 
                AND u.card_id = c.card_id
            """
            await execute_query_with_retry(update_query1, (user_id, card_id))
            
            # Second update: Set base stats equal to current stats
            update_query2 = """
                UPDATE user_cards 
                SET base_attack = attack_stat, 
                    base_speed = speed_stat, 
                    base_health = health_stat, 
                    base_defence = defence_stat 
                WHERE user_id = %s 
                AND card_id = %s
            """
            await execute_query_with_retry(update_query2, (user_id, card_id))
            
            # Log the action
            logging.info(f"Admin {ctx.author.id} reset card {card_id} ({card['card_name']}) for user {user_id} ({discord_id})")
            
            # Send success message
            success_embed = discord.Embed(
                title="✅ Card Reset Successfully",
                description=f"Card '{card['card_name']}' has been reset to default values for user {user.mention}.\\n\\n"
                           f"**Default Stats:**\\n"
                           f"Attack: {card['attack']}\\n"
                           f"Defence: {card['defence']}\\n"
                           f"Speed: {card['speed']}\\n"
                           f"Health: {card['health']}\\n"
                           f"Power: {card['power']}",
                color=discord.Color.green()
            )
            await message.edit(embed=success_embed, view=None)
            
        except Exception as e:
            logging.error(f"Error in resetcard command: {str(e)}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    @commands.command(name="addcard")
    @commands.has_permissions(administrator=True)
    async def add_card(self, ctx, *, args):
        """
        Admin command to add a card to a user's collection.
        Usage: sl addcard <card_name>,<discord_id>
        """
        try:
            if ctx.author.id != 818132161534230559:
                    await ctx.send("You don't have permission to use this command.")
                    return 
            # Parse arguments
            if ',' not in args:
                await ctx.send("❌ Invalid format. Use: sl addcard <card_name>,<discord_id>")
                return
                
            card_name, discord_id = args.split(',', 1)
            card_name = card_name.strip()
            discord_id = discord_id.strip()
            
            # Validate discord_id is a number
            try:
                discord_id = int(discord_id)
            except ValueError:
                await ctx.send("❌ Discord ID must be a number.")
                return
            
            # Check if card exists in database
            card_query = """
                SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
                FROM card c
                JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                JOIN card_class cc ON c.card_class_id = cc.card_class_id
                WHERE c.card_name LIKE %s
            """
            card_results = await execute_query_with_retry(card_query, (f"%{card_name}%",))
            
            if not card_results:
                await ctx.send(f"❌ Card '{card_name}' not found in the database.")
                return
                
            if len(card_results) > 1:
                # Multiple cards found, list them
                card_names = [f"• {card['card_name']}" for card in card_results]
                await ctx.send(f"⚠️ Multiple cards found matching your description. Please be more specific:\\n{chr(10).join(card_names)}")
                return
                
            # Get the card
            card = card_results[0]
            
            # Get user_id from discord_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(discord_id),))
            
            if not user_result:
                await ctx.send(f"❌ User with Discord ID {discord_id} not found in the database.")
                return
                
            user_id = user_result[0]['user_id']
            
            # Check if user already has the card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, card['card_id']))
            
            if user_card_result:
                await ctx.send(f"⚠️ User already has the card '{card['card_name']}'.")
                return
                
            # Add card to user's collection (similar to arise command)
            await execute_query_with_retry("""
                INSERT INTO user_cards (user_id, card_id, current_level, current_xp, 
                current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name, 
                defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                power_emoji, type_emoji, class_emoji) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, card['card_id'], card['min_level'], 0, 0,
                card['power'], card['attack'], card['health'], 
                card['speed'], card['defence'], card['attack'], card['health'], 
                card['speed'], card['card_name'], card['defence'],
                '<:solosword:1302911919941746730>', '<:soloshield:1302911930242961461>',
                '<:solostar:1302912104264761374>', '<:soloheart:1302912058819477514>',
                '<:soloweapon:1302961287747342356>', '<:solosecondshield:1302959829413007380>',
                '<:armor64:1311994697794850897>', '<:soloxp:1302960018747822080>',
                '<:sololevel:1302959449220321350>', '<:solopower:1302936147139235882>',
                '<:solorole:1302938003873726526>', '<:solotype:1302937991869759539>'
            ))
            
            # Handle special cards with additional effects
            if card['card_id'] == 103:
                await execute_query_with_retry(
                    "UPDATE user SET roll_cap = roll_cap + 1 WHERE user_id = %s", 
                    (user_id,)
                )
            if card['card_id'] == 104:
                await execute_query_with_retry(
                    "UPDATE user SET fragment_cap = fragment_cap + 100 WHERE user_id = %s", 
                    (user_id,)
                )
                
            await ctx.send(f"✅ Successfully added card '{card['card_name']}' to user with Discord ID {discord_id}.")
            
        except Exception as e:
            logging.error(f"Error in addcard command: {str(e)}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
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
    @commands.command(name="resetcard")
    @commands.has_permissions(administrator=True)
    async def reset_card(self, ctx, user: discord.Member, *, card_name):
        """
        Admin command to reset a card's stats to default values.
        Usage: sl resetcard @user card_name
        """
        try:
            # Get the Discord ID from the mentioned user
            discord_id = user.id
            
            # Check if user exists in database
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(discord_id),))
            
            if not user_result:
                await ctx.send(f"❌ User {user.mention} not found in the database.")
                return
                
            user_id = user_result[0]['user_id']
            
            # Check if card exists in database
            card_query = """
                SELECT c.card_id, c.card_name, c.attack, c.defence, c.speed, c.health, c.power
                FROM card c
                WHERE c.card_name LIKE %s
            """
            card_results = await execute_query_with_retry(card_query, (f"%{card_name}%",))
            
            if not card_results:
                await ctx.send(f"❌ Card '{card_name}' not found in the database.")
                return
                
            if len(card_results) > 1:
                # Multiple cards found, list them
                card_names = [f"• {card['card_name']}" for card in card_results]
                await ctx.send(f"⚠️ Multiple cards found matching your description. Please be more specific:\\n{chr(10).join(card_names)}")
                return
                
            # Get the card
            card = card_results[0]
            card_id = card['card_id']
            
            # Check if user has the card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, card_id))
            
            if not user_card_result:
                await ctx.send(f"❌ User {user.mention} does not have the card '{card['card_name']}'.")
                return
            
            # Create confirmation embed
            embed = discord.Embed(
                title="⚠️ Reset Card Confirmation",
                description=f"Are you sure you want to reset the card '{card['card_name']}' for user {user.mention}?\\n\\n"
                           f"This will reset all stats to default values and remove all stat points.",
                color=discord.Color.red()
            )
            
            # Create confirmation buttons
            confirm_view = ConfirmView()
            message = await ctx.send(embed=embed, view=confirm_view)
            
            # Wait for confirmation
            await confirm_view.wait()
            
            if confirm_view.value is None:
                await message.edit(content="❌ Reset card operation timed out.", embed=None, view=None)
                return
            elif not confirm_view.value:
                await message.edit(content="❌ Reset card operation cancelled.", embed=None, view=None)
                return
            
            # First update: Reset all stats to default values from card table
            update_query1 = """
                UPDATE user_cards u, card c 
                SET u.attack_stat = c.attack, 
                    u.defence_stat = c.defence, 
                    u.speed_stat = c.speed, 
                    u.health_stat = c.health,
                    u.power_stat = c.power, 
                    u.current_xp = 0, 
                    u.current_level = 1, 
                    u.current_statpoints = 0, 
                    u.used_attackpoints = 0, 
                    u.used_healthpoints = 0, 
                    u.used_defencepoints = 0, 
                    u.used_speedpoints = 0 
                WHERE u.user_id = %s 
                AND c.card_id = %s 
                AND u.card_id = c.card_id
            """
            await execute_query_with_retry(update_query1, (user_id, card_id))
            
            # Second update: Set base stats equal to current stats
            update_query2 = """
                UPDATE user_cards 
                SET base_attack = attack_stat, 
                    base_speed = speed_stat, 
                    base_health = health_stat, 
                    base_defence = defence_stat 
                WHERE user_id = %s 
                AND card_id = %s
            """
            await execute_query_with_retry(update_query2, (user_id, card_id))
            
            # Log the action
            logging.info(f"Admin {ctx.author.id} reset card {card_id} ({card['card_name']}) for user {user_id} ({discord_id})")
            
            # Send success message
            success_embed = discord.Embed(
                title="✅ Card Reset Successfully",
                description=f"Card '{card['card_name']}' has been reset to default values for user {user.mention}.\\n\\n"
                           f"**Default Stats:**\\n"
                           f"Attack: {card['attack']}\\n"
                           f"Defence: {card['defence']}\\n"
                           f"Speed: {card['speed']}\\n"
                           f"Health: {card['health']}\\n"
                           f"Power: {card['power']}",
                color=discord.Color.green()
            )
            await message.edit(embed=success_embed, view=None)
            
        except Exception as e:
            logging.error(f"Error in resetcard command: {str(e)}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    async def check_and_update_referral_status(self, user_id):
        """
        Helper function to check if user is a referee and update their referral status if requirements are met.
        
        Args:
            user_id: The user_id to check
        
        Returns:
            bool: True if status was updated, False otherwise
        """
        try:
            # Check if user is a referee with pending status
            referee_check_query = """
            SELECT r.referrer_id, r.referee_id, r.status, u.roll_total, u.c_dungeon_points
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referee_id = %s AND r.status = 'pending'
            """
            referee_result = await execute_query_with_retry(referee_check_query, (user_id,))
            
            if not referee_result:
                # User is not a pending referee
                return False
            
            referee_data = referee_result[0]
            roll_total = referee_data['roll_total'] or 0
            mid_dungeon_points = referee_data['c_dungeon_points'] or 0
            
            # Check if requirements are met (500 rolls + 1 mid dungeon)
            if roll_total >= 500 and mid_dungeon_points > 0:
                # Update status to completed
                update_query = """
                UPDATE referrals 
                SET status = 'completed', completed_at = %s
                WHERE referee_id = %s AND status = 'pending'
                """
                await execute_query_with_retry(update_query, (datetime.now(), user_id))
                
                print(f"Updated referral status to completed for user_id: {user_id}")
                return True
            
            return False
            
        except Exception as e:
            print(f"Error in check_and_update_referral_status: {e}")
            return False
async def setup(bot):
    await bot.add_cog(SpawnCommands(bot))