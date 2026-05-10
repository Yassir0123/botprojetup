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
from PIL import Image, ImageDraw, ImageFont
import io

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
# =========================================================
# Localization helper
# EN = 0 (default), AR = 1, FR = 3
# =========================================================
def _tr(lang, en: str, ar: str, fr: str) -> str:
    """
    Robust translator: accepts int or str ('1'/'3').
    1 => Arabic, 3 => French, else => English.
    """
    try:
        code = int(lang) if lang is not None else 0
    except (TypeError, ValueError):
        code = 0
    if code == 1:
        return ar
    if code == 3:
        return fr
    return en



# =========================================================
# Multi-draw (Arise) Grid Preview
# =========================================================
class MultiAriseView(discord.ui.View):
    def __init__(self, ctx, pulled_cards, essence_stones_info, multilanguage: int = 0, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.lang = multilanguage
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
            # (Keep mapping aligned with your DB's cardrank_id)
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
                card_image_path = self.get_image_path(card.get('image'))
                if card_image_path and os.path.exists(card_image_path):
                    try:
                        card_img = Image.open(card_image_path).convert("RGBA")
                        card_img = card_img.resize((card_width, card_height), Image.Resampling.LANCZOS)
                        border_image.paste(card_img, (border_width, border_width))
                    except Exception as e:
                        logging.warning(f"Error loading card image {card_image_path}: {e}")
                        placeholder = Image.new('RGB', (card_width, card_height), (64, 64, 64))
                        border_image.paste(placeholder, (border_width, border_width))
                else:
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
            logging.error(f"Error creating card grid: {e}")
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


# =========================================================
# Weapon Carousel (Buttons View)
# =========================================================
import os
import random
import logging
import discord

def _tr(lang, en, ar, fr):
    if lang == 1:
        return ar
    elif lang == 2:
        return fr
    else:
        return en

class WeaponView(discord.ui.View):
    def __init__(self, ctx, weapons, multilanguage: int, timeout: int = 900):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.lang = multilanguage
        self.weapons = weapons
        self.current_page = 0
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.message = None
        self.loading = False

        # Localize labels of decorator-created buttons
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "Back":
                    child.label = _tr(self.lang, "Back", "رجوع", "Précédent")
                elif child.label == "Next":
                    child.label = _tr(self.lang, "Next", "التالي", "Suivant")

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

            # Handle image file attachment
            files = []
            if weapon.get('weapon_image'):
                weapon_image_path = self.get_image_path(weapon['weapon_image'])
                if weapon_image_path and os.path.exists(weapon_image_path):
                    files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                    embed.set_image(url="attachment://weapon_image.png")

            # Try to update
            try:
                if not interaction.response.is_done():
                    await interaction.response.edit_message(embed=embed, attachments=files, view=self)
                else:
                    await interaction.message.edit(embed=embed, attachments=files, view=self)
                self.message = interaction.message
            except (discord.NotFound, discord.HTTPException):
                # Recovery attempt using stored message
                if self.message:
                    files = []
                    if weapon.get('weapon_image'):
                        weapon_image_path = self.get_image_path(weapon['weapon_image'])
                        if weapon_image_path and os.path.exists(weapon_image_path):
                            files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                            embed.set_image(url="attachment://weapon_image.png")
                    await self.message.edit(embed=embed, attachments=files, view=self)

        except Exception as e:
            logging.error(f"Error in update_message: {e}")
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
                logging.error(f"Error in recovery attempt: {e2}")
        finally:
            self.loading = False

    def create_embed(self):
        weapon = self.weapons[self.current_page]
        embed = discord.Embed(
            title=f"⚔️ {weapon.get('weapon_name','')} ⚔️",
            description=weapon.get('weapon_description',''),
            color=discord.Color.from_rgb(75, 0, 130)
        )

        embed.add_field(name=_tr(self.lang, "Type", "النوع", "Type"),
                        value=weapon.get('weaponrank_name',''), inline=False)

        # Boost groups
        is_halloween = weapon.get('w.halloween', 0) != 0

        def fmt_pair(label_en, label_ar, label_fr, boost_key, pct_key,
                    min_boost_key=None, max_boost_key=None, min_pct_key=None, max_pct_key=None):
            parts = []
            if is_halloween:
                # Flat range
                if min_boost_key and max_boost_key:
                    min_val = weapon.get(min_boost_key) or 0
                    max_val = weapon.get(max_boost_key) or 0
                    if (min_val != 0 or max_val != 0):
                        parts.append(f"+{min_val}-{max_val}")
                # Percent range
                if min_pct_key and max_pct_key:
                    min_pct = weapon.get(min_pct_key) or 0
                    max_pct = weapon.get(max_pct_key) or 0
                    if (min_pct != 0 or max_pct != 0):
                        parts.append(f"+{min_pct}-{max_pct}%")
            else:
                if weapon.get(boost_key):
                    parts.append(f"+{weapon[boost_key]}")
                if weapon.get(pct_key):
                    parts.append(f"+{weapon[pct_key]}%")

            if parts:
                embed.add_field(name=_tr(self.lang, label_en, label_ar, label_fr),
                                value=" | ".join(parts), inline=True)

        fmt_pair("Attack Boost",  "تعزيز الهجوم",  "Bonus d'attaque",  
                 "attack_boost",  "attack_percentage",
                 "min_attackboost", "max_attackboost", "min_attackpercentage", "max_attackpercentage")
        fmt_pair("Health Boost",  "تعزيز الصحة",   "Bonus de santé",    
                 "health_boost",  "health_percentage",
                 "min_healthboost", "max_healthboost", "min_healthpercentage", "max_healthpercentage")
        fmt_pair("Speed Boost",   "تعزيز السرعة",  "Bonus de vitesse",  
                 "speed_boost",   "speed_percentage",
                 "min_speedboost", "max_speedboost", "min_speedpercentage", "max_speedpercentage")
        fmt_pair("Defense Boost", "تعزيز الدفاع",  "Bonus de défense",  
                 "defense_boost", "defense_percentage",
                 "min_defenseboost", "max_defenseboost", "min_defensepercentage", "max_defensepercentage")

        if is_halloween:
            if 'cprice' in weapon and weapon['cprice'] > 0:
                embed.add_field(name=_tr(self.lang, "Candy Price", "سعر الحلوى", "Prix en bonbons"), 
                                value=f"{weapon['cprice']:,}", inline=True)
        else:
            if 'price' in weapon and weapon['price'] > 0:
                embed.add_field(name=_tr(self.lang, "Price", "السعر", "Prix"), 
                                value=f"{weapon['price']:,}", inline=True)
        
        if 'quantiter' in weapon:
            embed.add_field(name=_tr(self.lang, "Quantity", "الكمية", "Quantité"), 
                            value=str(weapon['quantiter']), inline=True)

        quotes = [
            _tr(self.lang, "A weapon is only as strong as its wielder.", "السلاح بقوة حامله فقط.", "Une arme n’est forte que par son porteur."),
            _tr(self.lang, "Choose your weapon wisely, hunter.", "اختر سلاحك بحكمة، أيها الصياد.", "Choisis ton arme avec sagesse, chasseur."),
            _tr(self.lang, "Power lies in both skill and equipment.", "القوة في المهارة والمعدات معًا.", "La puissance vient du talent et de l’équipement."),
            _tr(self.lang, "Every weapon has its purpose.", "لكل سلاح غايته.", "Chaque arme a son but."),
        ]
        footer_text = f"{random.choice(quotes)} • " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f" {self.current_page+1}/{len(self.weapons)}"
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
            logging.error(f"Error handling timeout: {e}")

class HalloweenWeaponView(discord.ui.View):
    def __init__(self, ctx, weapons, multilanguage: int, timeout: int = 900):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.lang = multilanguage
        self.weapons = weapons
        self.current_page = 0
        self.message = None
        self.loading = False
        # NEW: image path like WeaponView
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

        # Localize labels of decorator-created buttons
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "Back":
                    child.label = _tr(self.lang, "Back", "رجوع", "Précédent")
                elif child.label == "Next":
                    child.label = _tr(self.lang, "Next", "التالي", "Suivant")

    # NEW: helper like WeaponView
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

            # NEW: image handling like WeaponView
            files = []
            if weapon.get('weapon_image'):
                weapon_image_path = self.get_image_path(weapon['weapon_image'])
                if weapon_image_path and os.path.exists(weapon_image_path):
                    files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                    embed.set_image(url="attachment://weapon_image.png")

            # Try to update
            try:
                if not interaction.response.is_done():
                    await interaction.response.edit_message(embed=embed, attachments=files, view=self)
                else:
                    await interaction.message.edit(embed=embed, attachments=files, view=self)
                self.message = interaction.message
            except (discord.NotFound, discord.HTTPException):
                # Recovery attempt using stored message
                if self.message:
                    files = []
                    if weapon.get('weapon_image'):
                        weapon_image_path = self.get_image_path(weapon['weapon_image'])
                        if weapon_image_path and os.path.exists(weapon_image_path):
                            files.append(discord.File(weapon_image_path, filename="weapon_image.png"))
                            embed.set_image(url="attachment://weapon_image.png")
                    await self.message.edit(embed=embed, attachments=files, view=self)

        except Exception as e:
            logging.error(f"Error in update_message: {e}")
            try:
                # Recovery without interaction
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
                logging.error(f"Error in recovery attempt: {e2}")
        finally:
            self.loading = False

    def create_embed(self):
        weapon = self.weapons[self.current_page]
        embed = discord.Embed(
            title=f"🎃 {weapon.get('weapon_surname','')} 🎃",
            description=_tr(self.lang, "Halloween Weapon", "سلاح هالوين", "Arme Halloween"),
            color=discord.Color.from_rgb(255, 140, 0)  # Orange color for Halloween
        )

        embed.add_field(name=_tr(self.lang, "Type", "النوع", "Type"),
                        value=weapon.get('weaponrank_name',''), inline=False)

        def fmt_pair(label_en, label_ar, label_fr, boost_key, pct_key):
            parts = []
            if weapon.get(boost_key):
                parts.append(f"+{weapon[boost_key]}")
            if weapon.get(pct_key):
                parts.append(f"+{weapon[pct_key]}%")
            if parts:
                embed.add_field(name=_tr(self.lang, label_en, label_ar, label_fr), value=" | ".join(parts), inline=True)

        fmt_pair("Attack Boost",  "تعزيز الهجوم",  "Bonus d'attaque",  "attack_boost",  "attack_percentage")
        fmt_pair("Health Boost",  "تعزيز الصحة",   "Bonus de santé",    "health_boost",  "health_percentage")
        fmt_pair("Speed Boost",   "تعزيز السرعة",  "Bonus de vitesse",  "speed_boost",   "speed_percentage")
        fmt_pair("Defense Boost", "تعزيز الدفاع",  "Bonus de défense",  "defense_boost", "defense_percentage")

        if 'quantiter' in weapon:
            status = _tr(self.lang, "In Use", "قيد الاستخدام", "En cours d'utilisation") if weapon['quantiter'] == 0 else _tr(self.lang, "Available", "متاح", "Disponible")
            embed.add_field(name=_tr(self.lang, "Status", "الحالة", "Statut"), value=status, inline=True)

        quotes = [
            _tr(self.lang, "A cursed weapon from the Halloween realm.", "سلاح ملعون من عالم الهالوين.", "Une arme maudite du royaume d'Halloween."),
            _tr(self.lang, "Forged in the shadows of the night.", "مصنوع في ظلال الليل.", "Forgé dans les ombres de la nuit."),
            _tr(self.lang, "Power from beyond the veil.", "قوة من وراء الحجاب.", "Pouvoir d'au-delà du voile."),
        ]
        footer_text = f"{random.choice(quotes)} • " + _tr(self.lang, "Halloween Weapon", "سلاح هالوين", "Arme Halloween") + f" {self.current_page+1}/{len(self.weapons)}"
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
            logging.error(f"Error handling timeout: {e}")



# =========================================================
# Weapon Carousel (Reactions version)
# =========================================================
class WeaponDisplay:
    def __init__(self, ctx, weapons, multilanguage: int):
        self.ctx = ctx
        self.lang = multilanguage
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
            title=f"⚔️ {weapon.get('weapon_name','')} ⚔️",
            description=weapon.get('weapon_description',''),
            color=discord.Color.from_rgb(75, 0, 130)
        )

        embed.add_field(name=_tr(self.lang, "Rank", "الرتبة", "Rang"),
                        value=weapon.get('weaponrank_name',''), inline=False)

        def add_boost(label_en, label_ar, label_fr, boost_key, pct_key):
            vals = []
            if weapon.get(boost_key): vals.append(f"+{weapon[boost_key]}")
            if weapon.get(pct_key):  vals.append(f"+{weapon[pct_key]}%")
            if vals:
                embed.add_field(name=_tr(self.lang, label_en, label_ar, label_fr), value=" | ".join(vals), inline=True)

        add_boost("Attack Boost",  "تعزيز الهجوم",  "Bonus d’attaque",  "attack_boost",  "attack_percentage")
        add_boost("Health Boost",  "تعزيز الصحة",   "Bonus de santé",    "health_boost",  "health_percentage")
        add_boost("Speed Boost",   "تعزيز السرعة",  "Bonus de vitesse",  "speed_boost",   "speed_percentage")
        add_boost("Defense Boost", "تعزيز الدفاع",  "Bonus de défense",  "defense_boost", "defense_percentage")

        if 'price' in weapon:
            embed.add_field(name=_tr(self.lang, "Price", "السعر", "Prix"),
                            value=f"{weapon['price']:,}", inline=True)
        if 'quantiter' in weapon:
            embed.add_field(name=_tr(self.lang, "Quantity", "الكمية", "Quantité"),
                            value=str(weapon['quantiter']), inline=True)

        quotes = [
            _tr(self.lang, "A weapon is only as strong as its wielder.", "السلاح بقوة حامله فقط.", "Une arme n’est forte que par son porteur."),
            _tr(self.lang, "Choose your weapon wisely, hunter.", "اختر سلاحك بحكمة، أيها الصياد.", "Choisis ton arme avec sagesse, chasseur."),
            _tr(self.lang, "Power lies in both skill and equipment.", "القوة في المهارة والمعدات معًا.", "La puissance vient du talent et de l’équipement."),
            _tr(self.lang, "Every weapon has its purpose.", "لكل سلاح غايته.", "Chaque arme a son but."),
        ]
        footer_text = f"{random.choice(quotes)} • " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f" {weapon_index+1}/{len(self.weapons)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()

        # If you store absolute URLs in DB, keep as-is; if using local files,
        # prefer the WeaponView that attaches files.
        if weapon.get('weapon_image') and isinstance(weapon['weapon_image'], str) and weapon['weapon_image'].startswith(("http://", "https://")):
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
                logging.error(f"Error in update_message: {e}")
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
                try:
                    await reaction.remove(user)
                except discord.Forbidden:
                    # Missing manage_messages: ignore removing
                    pass

                # Update page based on reaction
                if str(reaction.emoji) == '⬅️' and self.current_page > 0:
                    self.current_page -= 1
                    await self.update_message()
                elif str(reaction.emoji) == '➡️' and self.current_page < len(self.weapons) - 1:
                    self.current_page += 1
                    await self.update_message()

            except asyncio.TimeoutError:
                # Clear reactions when the carousel times out
                try:
                    await self.message.clear_reactions()
                except discord.Forbidden:
                    pass
                break
class StatSelect(discord.ui.Select):

    def __init__(self, lang: int = 0):
        self.lang = lang
        # labels/descriptions localized; values remain stable for logic
        options = [
            discord.SelectOption(
                label=_tr(lang, "Attack", "هجوم", "Attaque"),
                value="attack",
                description=_tr(lang, "Adjust Attack stat", "تعديل قيمة الهجوم", "Ajuster l’Attaque"),
                emoji="⚔️",
            ),
            discord.SelectOption(
                label=_tr(lang, "Defence", "دفاع", "Défense"),
                value="defence",
                description=_tr(lang, "Adjust Defence stat", "تعديل قيمة الدفاع", "Ajuster la Défense"),
                emoji="🛡️",
            ),
            discord.SelectOption(
                label=_tr(lang, "Speed", "سرعة", "Vitesse"),
                value="speed",
                description=_tr(lang, "Adjust Speed stat", "تعديل قيمة السرعة", "Ajuster la Vitesse"),
                emoji="💨",
            ),
            discord.SelectOption(
                label=_tr(lang, "Health", "صحة", "Santé"),
                value="health",
                description=_tr(lang, "Adjust Health stat", "تعديل قيمة الصحة", "Ajuster la Santé"),
                emoji="❤️",
            ),
        ]
        super().__init__(
            placeholder=_tr(lang, "Choose a stat to adjust...", "اختر سمة للتعديل...", "Choisissez une statistique à ajuster…"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="stat_select",
        )

    async def callback(self, interaction: discord.Interaction):
        view: StatAdjustView = self.view  # type: ignore
        await view.on_stat_select(interaction, self.values[0])

class StatAdjustView(discord.ui.View):
    def __init__(self, ctx, card, original_view, bot_instance, lang: int = 0):
        super().__init__(timeout=720)  # 12 minutes
        self.ctx = ctx
        self.card = card
        self.original_view = original_view
        self.bot = bot_instance
        self.stat_to_adjust = None
        self.adjustments = {'attack': 0, 'defence': 0, 'speed': 0, 'health': 0}
        self.original_statpoints = card['current_statpoints']
        self.embed_color = self.get_embed_color(card.get('cardrank_id'))
        self.message = None
        self.files = []
        self.lang = lang

        self.initialize_files()

        # Re-order controls
        self.clear_items()

        # Add the localized stat selector
        self.add_item(StatSelect(self.lang))

        # Add the buttons in desired order
        self.add_item(self.add_all_button)
        self.add_item(self.increase_button)
        self.add_item(self.decrease_button)
        self.add_item(self.reset_button)
        self.add_item(self.go_back_button)
        self.add_item(self.confirm_button)

        # Localize button labels
        self.add_all_button.label = _tr(lang, "Add All", "إضافة الكل", "Tout ajouter")
        self.increase_button.label = "+"
        self.decrease_button.label = "-"
        self.reset_button.label = _tr(lang, "Reset", "إعادة ضبط", "Réinitialiser")
        self.go_back_button.label = _tr(lang, "Go Back", "رجوع", "Retour")
        self.confirm_button.label = _tr(lang, "Confirm", "تأكيد", "Confirmer")

        self.original_individual_points = {
            'attack': card['current_attackpoints'],
            'health': card['current_healthpoints'],
            'speed': card['current_speedpoints'],
            'defence': card['current_defencepoints'],
        }

        self.used_from_general = {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}
        self.used_from_individual = {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}
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
            weapon_display_name = self.card['weapon_name']
            if self.card.get('w.halloween') == 1 and self.card.get('weapon_surname'):
                weapon_display_name = self.card['weapon_surname']
            equipment_details = []
            if self.card['weapon_id']:
                weapon_display_name = self.card['weapon_name']
                if self.card.get('w.halloween') == 1 and self.card.get('weapon_surname'):
                    weapon_display_name = self.card['weapon_surname']
                equipment_details.append(f"{self.card['weapon_emoji']} " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f": {weapon_display_name}\n")
            
            # <CHANGE> Check if armor is Halloween and use surname
            if self.card['armor_id']:
                armor_display_name = self.card['armor_name']
                if self.card.get('armor_halloween') == 1 and self.card.get('armor_surname'):
                    armor_display_name = self.card['armor_surname']
                equipment_details.append(f"{self.card['armor_emoji']} " + _tr(self.lang, "Armor", "درع", "Armure") + f": {armor_display_name}\n")
            
            # <CHANGE> Check if shield is Halloween and use surname
            if self.card['shield_id']:
                shield_display_name = self.card['shield_name']
                if self.card.get('shield_halloween') == 1 and self.card.get('shield_surname'):
                    shield_display_name = self.card['shield_surname']
                equipment_details.append(f"{self.card['secondshield_emoji']} " + _tr(self.lang, "Shield", "ترس", "Bouclier") + f": {shield_display_name}\n")

            role = "Offence" if self.card['type_card'] == "Fighter" else self.card['type_card']
            role = _tr(self.lang, role, "هجوم" if role == "Offence" else role, "Offensif" if role == "Offence" else role)

            stat_label = _tr(self.lang, "Adjusting", "جارِ التعديل", "Ajustement")
            no_stat = _tr(self.lang, "No stat selected", "لا توجد سمة محددة", "Aucune statistique sélectionnée")

            ci = _tr(self.lang, "Card Informations", "معلومات البطاقة", "Informations de la carte")
            power = _tr(self.lang, "Power", "القوة", "Puissance")
            cls = _tr(self.lang, "Class", "الفئة", "Classe")
            role_lbl = _tr(self.lang, "Role", "الدور", "Rôle")
            level = _tr(self.lang, "Level", "المستوى", "Niveau")
            xp = _tr(self.lang, "XP", "خبرة", "XP")
            skill_lbl = _tr(self.lang, "Skill", "مهارة", "Compétence")
            affinity_lbl = _tr(self.lang, "Affinity", "الأُلفة", "Affinité")
            combat_stats = _tr(self.lang, "Combat Statistics", "إحصائيات القتال", "Statistiques de combat")

            description = (
                f"**📊 {stat_label}:** {self.stat_to_adjust.capitalize() if self.stat_to_adjust else no_stat}\n\n"
                f"**{ci}**\n"
                f"{self.card['power_emoji']} {power} : {self.card['power_stat']}\n"
                f"{self.card['type_emoji']} {cls} : {self.card['card_class_name']}\n"
                f"{self.card['class_emoji']} {role_lbl} : {role}\n"
                f"{self.card['level_emoji']} {level} : {self.card['current_level']}/{self.card['max_leveled']}\n"
                f"{self.card['xp_emoji']} {xp} : {self.card['current_xp']}/{self.card['xp_cap']}\n"
            )
            if self.card['skill_name']:
                description += f"🌟 **{skill_lbl}:** {self.card['skill_name']}\n"

            if self.card['evo'] in (1, 2):
                description += f"⚡ **{affinity_lbl}:** {self.card['affinity']}%/{self.card['affinity_cap']}%\n"
            else:
                description += f"⚡ **{affinity_lbl}:** {self.card['affinity']}/100%\n"

            if equipment_details:
                description += ''.join(equipment_details)

            description += f"\n**{combat_stats}**\n"

            # Localized stat names
            atk_lbl = _tr(self.lang, "Attack", "هجوم", "Attaque")
            hp_lbl = _tr(self.lang, "Health", "صحة", "Santé")
            spd_lbl = _tr(self.lang, "Speed", "سرعة", "Vitesse")
            def_lbl = _tr(self.lang, "Defence", "دفاع", "Défense")

            stats = {
                'attack': (self.card['attack_emoji'], self.card['attack_stat'], atk_lbl),
                'health': (self.card['health_emoji'], self.card['health_stat'], hp_lbl),
                'speed': (self.card['speed_emoji'], self.card['speed_stat'], spd_lbl),
                'defence': (self.card['defense_emoji'], self.card['defence_stat'], def_lbl),
            }

            for stat, (emoji, base_value, localized_name) in stats.items():
                current_value = base_value + self.adjustments[stat]
                adj = self.adjustments[stat]
                adjustment_text = f" (+{adj})" if adj > 0 else (f" ({adj})" if adj < 0 else "")
                description += f"{emoji} {localized_name} : {current_value}{adjustment_text}\n"

            # Available points section
            ap_title = _tr(self.lang, "Available Points", "النقاط المتاحة", "Points disponibles")
            lvl_pts = _tr(self.lang, "Level Points", "نقاط المستوى", "Points de niveau")
            atk_pts = _tr(self.lang, "Attack Points", "نقاط الهجوم", "Points d’attaque")
            hp_pts  = _tr(self.lang, "Health Points", "نقاط الصحة", "Points de santé")
            spd_pts = _tr(self.lang, "Speed Points", "نقاط السرعة", "Points de vitesse")
            def_pts = _tr(self.lang, "Defence Points", "نقاط الدفاع", "Points de défense")

            description += f"\n\n{ap_title}\n"
            description += f"{lvl_pts}: {self.card['current_statpoints']}\n"
            description += f"{atk_pts}: {self.card['current_attackpoints']}\n"
            description += f"{hp_pts}: {self.card['current_healthpoints']}\n"
            description += f"{spd_pts}: {self.card['current_speedpoints']}\n"
            description += f"{def_pts}: {self.card['current_defencepoints']}\n"

            name = self.card['card_name']
            if self.card['pseudo_name'] != "":
                name = f"{self.card['pseudo_name']} [{self.card['card_name']}]"

            embed = discord.Embed(title=name, description=description, color=self.embed_color)

            if self.card_image_url:
                embed.set_image(url=self.card_image_url)
            if self.rank_image_url:
                embed.set_thumbnail(url=self.rank_image_url)

            quotes_en = [
                "The true hunter never rests.",
                "Your strength defines your fate.",
                "Another step towards greatness.",
                "The shadows whisper, will you answer?",
            ]
            quotes_ar = [
                "الصيّاد الحقيقي لا يرتاح.",
                "قوتك تحدد مصيرك.",
                "خطوة أخرى نحو العظمة.",
                "الظلال تهمس، هل ستجيب؟",
            ]
            quotes_fr = [
                "Le véritable chasseur ne se repose jamais.",
                "Ta force définit ton destin.",
                "Un pas de plus vers la grandeur.",
                "Les ombres murmurent, répondras-tu ?",
            ]
            quotes = quotes_en if self.lang == 0 else (quotes_ar if self.lang == 1 else quotes_fr)
            embed.set_footer(text=random.choice(quotes))
            embed.timestamp = discord.utils.utcnow()
            return embed
        except Exception as e:
            print(f"Error creating embed: {e}")
            return discord.Embed(
                title=_tr(self.lang, "Error", "خطأ", "Erreur"),
                description=_tr(self.lang, "An error occurred while creating the embed.", "حدث خطأ أثناء إنشاء التضمين.", "Une erreur s’est produite lors de la création de l’embed."),
            )

    async def on_stat_select(self, interaction, stat):
        try:
            self.stat_to_adjust = stat
            await self.update_view(interaction)
        except Exception as e:
            print(f"Error in stat select: {e}")
            await interaction.response.send_message(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

    @discord.ui.button(label="Add All", style=discord.ButtonStyle.green)
    async def add_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                return
            if not self.stat_to_adjust:
                await interaction.response.send_message(_tr(self.lang, "Please select a stat first!", "يرجى اختيار سمة أولًا!", "Veuillez d’abord choisir une statistique !"), ephemeral=True)
                return

            points_added = 0
            if self.card['current_statpoints'] > 0:
                general_points = self.card['current_statpoints']
                self.adjustments[self.stat_to_adjust] += general_points
                self.used_from_general[self.stat_to_adjust] += general_points
                self.card['current_statpoints'] = 0
                points_added += general_points

            individual_key = f'current_{self.stat_to_adjust}points'
            if self.card[individual_key] > 0:
                individual_points = self.card[individual_key]
                self.adjustments[self.stat_to_adjust] += individual_points
                self.used_from_individual[self.stat_to_adjust] += individual_points
                self.card[individual_key] = 0
                points_added += individual_points

            if points_added > 0:
                await self.update_view(interaction)
            else:
                await interaction.response.send_message(_tr(self.lang, "No available stat points.", "لا توجد نقاط متاحة.", "Aucun point disponible."), ephemeral=True)
        except Exception as e:
            print(f"Error in add all button: {e}")
            await interaction.response.send_message(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

    @discord.ui.button(label="+", style=discord.ButtonStyle.green)
    async def increase_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                return
            if not self.stat_to_adjust:
                await interaction.response.send_message(_tr(self.lang, "Please select a stat first!", "يرجى اختيار سمة أولًا!", "Veuillez d’abord choisir une statistique !"), ephemeral=True)
                return

            if self.card['current_statpoints'] > 0:
                self.adjustments[self.stat_to_adjust] += 1
                self.card['current_statpoints'] -= 1
                self.used_from_general[self.stat_to_adjust] += 1
                await self.update_view(interaction)
            elif self.card[f'current_{self.stat_to_adjust}points'] > 0:
                self.adjustments[self.stat_to_adjust] += 1
                self.card[f'current_{self.stat_to_adjust}points'] -= 1
                self.used_from_individual[self.stat_to_adjust] += 1
                await self.update_view(interaction)
            else:
                await interaction.response.send_message(_tr(self.lang, "No available stat points.", "لا توجد نقاط متاحة.", "Aucun point disponible."), ephemeral=True)
        except Exception as e:
            print(f"Error in increase button: {e}")
            await interaction.response.send_message(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

    @discord.ui.button(label="-", style=discord.ButtonStyle.red)
    async def decrease_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                return
            if not self.stat_to_adjust:
                await interaction.response.send_message(_tr(self.lang, "Please select a stat first!", "يرجى اختيار سمة أولًا!", "Veuillez d’abord choisir une statistique !"), ephemeral=True)
                return

            if self.adjustments[self.stat_to_adjust] > 0:
                if self.used_from_individual[self.stat_to_adjust] > 0:
                    self.adjustments[self.stat_to_adjust] -= 1
                    self.card[f'current_{self.stat_to_adjust}points'] += 1
                    self.used_from_individual[self.stat_to_adjust] -= 1
                elif self.used_from_general[self.stat_to_adjust] > 0:
                    self.adjustments[self.stat_to_adjust] -= 1
                    self.card['current_statpoints'] += 1
                    self.used_from_general[self.stat_to_adjust] -= 1

                await self.update_view(interaction)
            else:
                await interaction.response.send_message(_tr(self.lang, "Cannot decrease stat further.", "لا يمكن تقليل السمة أكثر.", "Impossible de diminuer davantage."), ephemeral=True)
        except Exception as e:
            print(f"Error in decrease button: {e}")
            await interaction.response.send_message(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.gray)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                return

            self.adjustments = {stat: 0 for stat in self.adjustments}
            self.card['current_statpoints'] = self.original_statpoints
            self.card['current_attackpoints'] = self.original_individual_points['attack']
            self.card['current_healthpoints'] = self.original_individual_points['health']
            self.card['current_speedpoints'] = self.original_individual_points['speed']
            self.card['current_defencepoints'] = self.original_individual_points['defence']
            self.used_from_general = {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}
            self.used_from_individual = {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}
            await self.update_view(interaction)
        except Exception as e:
            print(f"Error in reset button: {e}")
            await interaction.response.send_message(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

    @discord.ui.button(label="Go Back", style=discord.ButtonStyle.gray)
    async def go_back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                return

            if sum(self.adjustments.values()) > 0:
                self.adjustments = {stat: 0 for stat in self.adjustments}
                self.card['current_statpoints'] = self.original_statpoints
                self.card['current_attackpoints'] = self.original_individual_points['attack']
                self.card['current_healthpoints'] = self.original_individual_points['health']
                self.card['current_speedpoints'] = self.original_individual_points['speed']
                self.card['current_defencepoints'] = self.original_individual_points['defence']
                self.used_from_general = {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}
                self.used_from_individual = {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}

            embed = self.create_simple_embed()
            await interaction.response.edit_message(embed=embed, view=self.original_view)
            await self.remove_stat_status_entry()
            self.stop()
        except Exception as e:
            print(f"Error in go back button: {e}")
            await interaction.response.send_message(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.blurple)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                return

            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)

            confirm_view = ConfirmView()
            confirm_message = await interaction.followup.send(
                _tr(self.lang, "Are you sure you want to apply these stat changes?", "هل أنت متأكد من تطبيق تغييرات السمات هذه؟", "Confirmer l’application de ces changements ?"),
                view=confirm_view,
                ephemeral=True,
            )
            await confirm_view.wait()

            if confirm_view.value is None:
                for item in self.children:
                    item.disabled = False
                await interaction.edit_original_response(view=self)
                await interaction.followup.send(_tr(self.lang, "Confirmation timed out. Changes not applied.", "انتهى وقت التأكيد. لم يتم تطبيق التغييرات.", "Confirmation expirée. Changements non appliqués."), ephemeral=True)
            elif confirm_view.value:
                original_stats = {
                    'attack': self.card['attack_stat'],
                    'defence': self.card['defence_stat'],
                    'speed': self.card['speed_stat'],
                    'health': self.card['health_stat'],
                    'power': self.card['power_stat'],
                }

                updated_stats = await self.update_database(interaction)
                if updated_stats is None:
                    await self.remove_stat_status_entry()
                    return
                await self.remove_stat_status_entry()

                stat_changes = []
                for stat in ['attack', 'defence', 'speed', 'health']:
                    if self.adjustments[stat] > 0:
                        old_value = original_stats[stat]
                        new_value = updated_stats[stat]
                        stat_changes.append(f"{_tr(self.lang, stat.capitalize(), 'هجوم' if stat=='attack' else 'دفاع' if stat=='defence' else 'سرعة' if stat=='speed' else 'صحة', 'Attaque' if stat=='attack' else 'Défense' if stat=='defence' else 'Vitesse' if stat=='speed' else 'Santé')}: {old_value} → {new_value} (+{self.adjustments[stat]})")

                old_power = original_stats['power']
                new_power = updated_stats['power']
                power_lbl = _tr(self.lang, "Power", "القوة", "Puissance")
                stat_changes.append(f"{power_lbl}: {old_power} → {new_power} (+{new_power - old_power})")

                success = _tr(self.lang, "Stats updated successfully!", "تم تحديث الإحصائيات بنجاح!", "Statistiques mises à jour avec succès !")
                changes_applied = _tr(self.lang, "**Changes Applied:**", "**التغييرات المطبقة:**", "**Changements appliqués :**")
                await interaction.followup.send(success + "\n\n" + changes_applied + "\n" + "\n".join(stat_changes), ephemeral=True)

                # If original view is CardView, refresh it (labels stay as-is)
                if isinstance(self.original_view, CardView) and hasattr(self.original_view, 'cards'):
                    self.original_view.cards[self.original_view.current_page] = self.card
                    embed = self.original_view.create_embed()
                    files = []
                    rank_image_path = self.original_view.get_image_path(self.card.get('cardrank_image'))
                    if rank_image_path and os.path.exists(rank_image_path):
                        rank_file = discord.File(rank_image_path, filename="rank_image.png")
                        files.append(rank_file)
                        embed.set_thumbnail(url="attachment://rank_image.png")

                    img = self.card['image']
                    if self.card.get('pseudo_image') and self.card['pseudo_image'] != "":
                        img = self.card['pseudo_image']
                    card_image_path = self.original_view.get_image_path(img)
                    if card_image_path and os.path.exists(card_image_path):
                        card_file = discord.File(card_image_path, filename="card_image.png")
                        files.append(card_file)
                        embed.set_image(url="attachment://card_image.png")

                    new_view = CardView(self.ctx, self.original_view.cards, self.bot,self.lang)
                    new_view.current_page = self.original_view.current_page

                    if files:
                        await interaction.message.edit(embed=embed, attachments=files, view=new_view)
                    else:
                        await interaction.message.edit(embed=embed, view=new_view)
                else:
                    # Localized SingleCardView
                    class SingleCardView(discord.ui.View):
                        def __init__(self, ctx, card, bot_instance, lang: int = 0):
                            super().__init__(timeout=180)
                            self.ctx = ctx
                            self.card = card
                            self.bot = bot_instance
                            self.lang = lang
                            # Button label localization (decorated below)
                            self.stats_button.label = _tr(lang, "Stats", "إحصائيات", "Stats")
                            # Disable for support/booster
                            self.stats_button.disabled = card['type_card'] in ['Support', 'Booster']
                        @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
                        async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            if interaction.user.id != self.ctx.author.id:
                                await interaction.response.send_message(_tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                                return

                            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
                            stat_status_result = await execute_query_with_retry(stat_status_query, (self.card['user_id'], self.card['card_id']))

                            if stat_status_result:
                                await interaction.response.send_message(
                                    _tr(self.lang, "This card is currently being adjusted. Please wait until the process is completed.", "تجري حاليًا عملية تعديل هذه البطاقة. يرجى الانتظار حتى تكتمل العملية.", "Cette carte est en cours d’ajustement. Veuillez patienter jusqu’à la fin."),
                                    ephemeral=True,
                                )
                                return

                            card_query = """
                    SELECT uc.*,
                        c.card_name, c.xp_cap, uc.max_leveled, c.type_card, c.description,
                        cr.cardrank_name, cr.cardrank_image, cr.cardrank_id,
                        cc.card_class_name,
                        w.weapon_id, w.weapon_name, w.weapon_rank, w.halloween,
                        wh.weapon_surname, wh.id_weaponhalloween,
                        a.weapon_id AS armor_id, a.weapon_name AS armor_name, a.weapon_rank AS armor_rank, a.halloween AS armor_halloween,
                        ah.weapon_surname AS armor_surname, ah.id_weaponhalloween AS armor_id_weaponhalloween,
                        s.weapon_id AS shield_id, s.weapon_name AS shield_name, s.weapon_rank AS shield_rank, s.halloween AS shield_halloween,
                        sh.weapon_surname AS shield_surname, sh.id_weaponhalloween AS shield_id_weaponhalloween,
                        uc.pseudo_name, uc.pseudo_image, uc.skill_id, sk.skill_name,
                        uc.affinity, cu.affinity_cap
                    FROM user_cards uc
                    JOIN card c ON uc.card_id = c.card_id
                    JOIN card_rank cr ON uc.card_rank = cr.cardrank_id
                    JOIN card_class cc ON c.card_class_id = cc.card_class_id
                    LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
                    LEFT JOIN weapon_halloween wh ON uc.id_weaponhalloween = wh.id_weaponhalloween
                    LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
                    LEFT JOIN weapon_halloween ah ON uc.id_armorhalloween = ah.id_weaponhalloween
                    LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
                    LEFT JOIN weapon_halloween sh ON uc.id_shieldhalloween = sh.id_weaponhalloween
                    LEFT JOIN skill sk ON uc.skill_id = sk.skill_id AND uc.skill_id <> 0
                    LEFT JOIN card_upgrades cu ON cu.card_id = uc.card_id AND cu.evo = uc.evo
                    WHERE uc.user_id = %s AND uc.card_id = %s
                            """
                            updated_card_result = await execute_query_with_retry(card_query, (self.card['user_id'], self.card['card_id']))
                            if updated_card_result:
                                self.card = updated_card_result[0]

                            stat_status_result = await execute_query_with_retry(stat_status_query, (self.card['user_id'], self.card['card_id']))
                            if stat_status_result:
                                await interaction.response.send_message(
                                    _tr(self.lang, "This card is currently being adjusted. Please wait until the process is completed.", "تجري حاليًا عملية تعديل هذه البطاقة. يرجى الانتظار حتى تكتمل العملية.", "Cette carte est en cours d’ajustement. Veuillez patienter jusqu’à la fin."),
                                    ephemeral=True,
                                )
                                return

                            insert_query = "INSERT INTO stat_status (user_id, card_id) VALUES (%s, %s)"
                            await execute_query_with_retry(insert_query, (self.card['user_id'], self.card['card_id']))

                            stat_view = StatAdjustView(self.ctx, self.card, self, self.bot, lang=self.lang)
                            await interaction.response.edit_message(view=stat_view)

                    single_view = SingleCardView(self.ctx, self.card, self.bot, lang=self.lang)
                    total_available_points = (
                        self.card['current_statpoints']
                        + self.card['current_attackpoints']
                        + self.card['current_healthpoints']
                        + self.card['current_speedpoints']
                        + self.card['current_defencepoints']
                    )

                    # Rebuild localized single-card embed
                    equipment_details = []
                    if self.card['weapon_id']:
                        weapon_display_name = self.card['weapon_name']
                        if self.card.get('w.halloween') == 1 and self.card.get('weapon_surname'):
                            weapon_display_name = self.card['weapon_surname']
                        equipment_details.append(f"{self.card['weapon_emoji']} " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f": {weapon_display_name}\n")
                    
                    # <CHANGE> Check if armor is Halloween and use surname
                    if self.card['armor_id']:
                        armor_display_name = self.card['armor_name']
                        if self.card.get('armor_halloween') == 1 and self.card.get('armor_surname'):
                            armor_display_name = self.card['armor_surname']
                        equipment_details.append(f"{self.card['armor_emoji']} " + _tr(self.lang, "Armor", "درع", "Armure") + f": {armor_display_name}\n")
                    
                    # <CHANGE> Check if shield is Halloween and use surname
                    if self.card['shield_id']:
                        shield_display_name = self.card['shield_name']
                        if self.card.get('shield_halloween') == 1 and self.card.get('shield_surname'):
                            shield_display_name = self.card['shield_surname']
                        equipment_details.append(f"{self.card['secondshield_emoji']} " + _tr(self.lang, "Shield", "ترس", "Bouclier") + f": {shield_display_name}\n")

                    role = "Offence" if self.card['type_card'] == "Fighter" else self.card['type_card']
                    role_loc = _tr(self.lang, role, "هجوم" if role == "Offence" else role, "Offensif" if role == "Offence" else role)

                    rank_line = _tr(self.lang, "A {rank}-Rank card, Summoner!", "بطاقة رتبة {rank}، أَيُّها المُستدعي!", "Une carte de rang {rank}, Invocateur !").format(rank=self.card['cardrank_name'])
                    ci = _tr(self.lang, "Card Informations", "معلومات البطاقة", "Informations de la carte")
                    power = _tr(self.lang, "Power", "القوة", "Puissance")
                    cls = _tr(self.lang, "Class", "الفئة", "Classe")
                    role_lbl = _tr(self.lang, "Role", "الدور", "Rôle")
                    level = _tr(self.lang, "Level", "المستوى", "Niveau")
                    xp = _tr(self.lang, "XP", "خبرة", "XP")
                    skill_lbl = _tr(self.lang, "Skill", "مهارة", "Compétence")
                    affinity_lbl = _tr(self.lang, "Affinity", "الأُلفة", "Affinité")
                    effect_lbl = _tr(self.lang, "Effect", "التأثير", "Effet")
                    combat_stats = _tr(self.lang, "Combat Statistics", "إحصائيات القتال", "Statistiques de combat")
                    atk_lbl = _tr(self.lang, "Attack", "هجوم", "Attaque")
                    hp_lbl  = _tr(self.lang, "Health", "صحة", "Santé")
                    spd_lbl = _tr(self.lang, "Speed", "سرعة", "Vitesse")
                    def_lbl = _tr(self.lang, "Defence", "دفاع", "Défense")
                    asp_lbl = _tr(self.lang, "Available Stat Points", "نقاط السمات المتاحة", "Points de stats disponibles")

                    if self.card['type_card'].lower() in ['support', 'booster']:
                        description = (
                            f"{rank_line}\n\n"
                            f"**{ci}**\n"
                            f"{self.card['power_emoji']} {power} : {self.card['power_stat']}\n"
                            f"{self.card['type_emoji']} {cls} : {self.card['card_class_name']}\n"
                            f"{self.card['class_emoji']} {role_lbl} : {self.card['type_card']}\n"
                        )
                        if equipment_details:
                            description += ''.join(equipment_details)
                        description += f"\n**{effect_lbl}**\n{self.card['description']}"
                    else:
                        description = (
                            f"{rank_line}\n\n"
                            f"**{ci}**\n"
                            f"{self.card['power_emoji']} {power} : {self.card['power_stat']}\n"
                            f"{self.card['type_emoji']} {cls} : {self.card['card_class_name']}\n"
                            f"{self.card['class_emoji']} {role_lbl} : {role_loc}\n"
                            f"{self.card['level_emoji']} {level} : {self.card['current_level']}/{self.card['max_leveled']}\n"
                            f"{self.card['xp_emoji']} {xp} : {self.card['current_xp']}/{self.card['xp_cap']}\n"
                        )
                        if self.card['skill_name']:
                            description += f"🌟 **{skill_lbl}:** {self.card['skill_name']}\n"
                        if self.card['evo'] in (1, 2):
                            description += f"⚡ **{affinity_lbl}:** {self.card['affinity']}%/{self.card['affinity_cap']}%\n"
                        else:
                            description += f"⚡ **{affinity_lbl}:** {self.card['affinity']}/100%\n"
                        if equipment_details:
                            description += ''.join(equipment_details)
                        description += (
                            f"\n**{combat_stats}**\n"
                            f"{self.card['attack_emoji']} {atk_lbl} : {self.card['attack_stat']}\n"
                            f"{self.card['health_emoji']} {hp_lbl} : {self.card['health_stat']}\n"
                            f"{self.card['speed_emoji']} {spd_lbl} : {self.card['speed_stat']}\n"
                            f"{self.card['defense_emoji']} {def_lbl} : {self.card['defence_stat']}\n\n"
                            f"{asp_lbl}: {total_available_points}"
                        )

                    name = self.card['card_name']
                    if self.card.get('pseudo_name') and self.card['pseudo_name'] != "":
                        name = f"{self.card['pseudo_name']} [{self.card['card_name']}]"

                    embed = discord.Embed(title=name, description=description, color=self.embed_color)

                    files = []
                    rank_image_path = os.path.join(os.path.dirname(__file__), '..', 'images', self.card.get('cardrank_image', ''))
                    if os.path.exists(rank_image_path):
                        rank_file = discord.File(rank_image_path, filename="rank_image.png")
                        files.append(rank_file)
                        embed.set_thumbnail(url="attachment://rank_image.png")

                    img = self.card['image']
                    if self.card.get('pseudo_image') and self.card['pseudo_image'] != "":
                        img = self.card['pseudo_image']
                    card_image_path = os.path.join(os.path.dirname(__file__), '..', 'images', img)
                    if os.path.exists(card_image_path):
                        card_file = discord.File(card_image_path, filename="card_image.png")
                        files.append(card_file)
                        embed.set_image(url="attachment://card_image.png")

                    quotes_en = [
                        "The true hunter never rests.",
                        "Your strength defines your fate.",
                        "Another step towards greatness.",
                        "The shadows whisper, will you answer?",
                    ]
                    quotes_ar = [
                        "الصيّاد الحقيقي لا يرتاح.",
                        "قوتك تحدد مصيرك.",
                        "خطوة أخرى نحو العظمة.",
                        "الظلال تهمس، هل ستجيب؟",
                    ]
                    quotes_fr = [
                        "Le véritable chasseur ne se repose jamais.",
                        "Ta force définit ton destin.",
                        "Un pas de plus vers la grandeur.",
                        "Les ombres murmurent, répondras-tu ?",
                    ]
                    quotes = quotes_en if self.lang == 0 else (quotes_ar if self.lang == 1 else quotes_fr)
                    embed.set_footer(text=random.choice(quotes))
                    embed.timestamp = discord.utils.utcnow()

                    if files:
                        await interaction.message.edit(embed=embed, attachments=files, view=single_view)
                    else:
                        await interaction.message.edit(embed=embed, view=single_view)
            else:
                for item in self.children:
                    item.disabled = False
                await interaction.edit_original_response(view=self)
                await interaction.followup.send(_tr(self.lang, "Changes cancelled.", "تم إلغاء التغييرات.", "Changements annulés."), ephemeral=True)
                await self.remove_stat_status_entry()
        except Exception as e:
            print(f"Error in confirm button: {e}")
            import traceback
            traceback.print_exc()
            try:
                await self.remove_stat_status_entry()
            except:
                pass
            await interaction.followup.send(_tr(self.lang, "An error occurred. Please try again.", "حدث خطأ. حاول مجددًا.", "Une erreur s’est produite. Réessayez."), ephemeral=True)

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
        """Create a localized description with the latest stats for the embed"""
        equipment_details = []
            # <CHANGE> Check if weapon is Halloween and use surname
        if self.card['weapon_id']:
            weapon_display_name = self.card['weapon_name']
            if self.card.get('halloween') == 1 and self.card.get('weapon_surname'):
                weapon_display_name = self.card['weapon_surname']
            equipment_details.append(f"{self.card['weapon_emoji']} " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f": {weapon_display_name}\n")
        
        # <CHANGE> Check if armor is Halloween and use surname
        if self.card['armor_id']:
            armor_display_name = self.card['armor_name']
            if self.card.get('armor_halloween') == 1 and self.card.get('armor_surname'):
                armor_display_name = self.card['armor_surname']
            equipment_details.append(f"{self.card['armor_emoji']} " + _tr(self.lang, "Armor", "درع", "Armure") + f": {armor_display_name}\n")
        
        # <CHANGE> Check if shield is Halloween and use surname
        if self.card['shield_id']:
            shield_display_name = self.card['shield_name']
            if self.card.get('shield_halloween') == 1 and self.card.get('shield_surname'):
                shield_display_name = self.card['shield_surname']
            equipment_details.append(f"{self.card['secondshield_emoji']} " + _tr(self.lang, "Shield", "ترس", "Bouclier") + f": {shield_display_name}\n")

        role = "Offence" if self.card['type_card'] == "Fighter" else self.card['type_card']
        role_loc = _tr(self.lang, role, "هجوم" if role == "Offence" else role, "Offensif" if role == "Offence" else role)

        description = (
            f"{_tr(self.lang, 'A {r}-Rank card, Summoner!', 'بطاقة رتبة {r}، أيها المُستدعي!', 'Une carte de rang {r}, Invocateur !').format(r=self.card['cardrank_name'])}\n\n"
            f"**{_tr(self.lang, 'Card Informations', 'معلومات البطاقة', 'Informations de la carte')}**\n"
            f"{self.card['power_emoji']} {_tr(self.lang, 'Power', 'القوة', 'Puissance')} : {self.card['power_stat']}\n"
            f"{self.card['type_emoji']} {_tr(self.lang, 'Class', 'الفئة', 'Classe')} : {self.card['card_class_name']}\n"
            f"{self.card['class_emoji']} {_tr(self.lang, 'Role', 'الدور', 'Rôle')} : {role_loc}\n"
            f"{self.card['level_emoji']} {_tr(self.lang, 'Level', 'المستوى', 'Niveau')} : {self.card['current_level']}/{self.card['max_leveled']}\n"
            f"{self.card['xp_emoji']} {_tr(self.lang, 'XP', 'خبرة', 'XP')} : {self.card['current_xp']}/{self.card['xp_cap']}\n"
        )

        if equipment_details:
            description += ''.join(equipment_details)

        description += (
            f"\n**{_tr(self.lang, 'Combat Statistics', 'إحصائيات القتال', 'Statistiques de combat')}**\n"
            f"{self.card['attack_emoji']} {_tr(self.lang, 'Attack', 'هجوم', 'Attaque')} : {self.card['attack_stat']}\n"
            f"{self.card['health_emoji']} {_tr(self.lang, 'Health', 'صحة', 'Santé')} : {self.card['health_stat']}\n"
            f"{self.card['speed_emoji']} {_tr(self.lang, 'Speed', 'سرعة', 'Vitesse')} : {self.card['speed_stat']}\n"
            f"{self.card['defense_emoji']} {_tr(self.lang, 'Defense', 'دفاع', 'Défense')} : {self.card['defence_stat']}\n\n"
        )

        # Enhanced available points display
        description += f"\n{_tr(self.lang, 'Available Points', 'النقاط المتاحة', 'Points disponibles')}\n"
        description += f"{_tr(self.lang, 'Level Points', 'نقاط المستوى', 'Points de niveau')}: {self.card['current_statpoints']}\n"
        description += f"{_tr(self.lang, 'Attack Points', 'نقاط الهجوم', 'Points d’attaque')}: {self.card['current_attackpoints']}\n"
        description += f"{_tr(self.lang, 'Health Points', 'نقاط الصحة', 'Points de santé')}: {self.card['current_healthpoints']}\n"
        description += f"{_tr(self.lang, 'Speed Points', 'نقاط السرعة', 'Points de vitesse')}: {self.card['current_speedpoints']}\n"
        description += f"{_tr(self.lang, 'Defence Points', 'نقاط الدفاع', 'Points de défense')}: {self.card['current_defencepoints']}\n"

        return description



    def create_simple_embed(self):
        """Create embed with only total available stat points (for going back)"""
        try:
            equipment_details = []
            # <CHANGE> Check if weapon is Halloween and use surname
            if self.card['weapon_id']:
                weapon_display_name = self.card['weapon_name']
                if self.card.get('w.halloween') == 1 and self.card.get('weapon_surname'):
                    weapon_display_name = self.card['weapon_surname']
                equipment_details.append(f"{self.card['weapon_emoji']} " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f": {weapon_display_name}\n")
            
            # <CHANGE> Check if armor is Halloween and use surname
            if self.card['armor_id']:
                armor_display_name = self.card['armor_name']
                if self.card.get('armor_halloween') == 1 and self.card.get('armor_surname'):
                    armor_display_name = self.card['armor_surname']
                equipment_details.append(f"{self.card['armor_emoji']} " + _tr(self.lang, "Armor", "درع", "Armure") + f": {armor_display_name}\n")
            
            # <CHANGE> Check if shield is Halloween and use surname
            if self.card['shield_id']:
                shield_display_name = self.card['shield_name']
                if self.card.get('shield_halloween') == 1 and self.card.get('shield_surname'):
                    shield_display_name = self.card['shield_surname']
                equipment_details.append(f"{self.card['secondshield_emoji']} " + _tr(self.lang, "Shield", "ترس", "Bouclier") + f": {shield_display_name}\n")

            role = "Offence" if self.card['type_card'] == "Fighter" else self.card['type_card']
            role_loc = _tr(self.lang, role, "هجوم" if role == "Offence" else role, "Offensif" if role == "Offence" else role)

            if self.card['type_card'].lower() in ['support', 'booster']:
                description = (
                    f"{_tr(self.lang, 'A {r}-Rank card, Summoner!', 'بطاقة رتبة {r}، أيها المُستدعي!', 'Une carte de rang {r}, Invocateur !').format(r=self.card['cardrank_name'])}\n\n"
                    f"**{_tr(self.lang, 'Card Informations', 'معلومات البطاقة', 'Informations de la carte')}**\n"
                    f"{self.card['power_emoji']} {_tr(self.lang, 'Power', 'القوة', 'Puissance')} : {self.card['power_stat']}\n"
                    f"{self.card['type_emoji']} {_tr(self.lang, 'Class', 'الفئة', 'Classe')} : {self.card['card_class_name']}\n"
                    f"{self.card['class_emoji']} {_tr(self.lang, 'Role', 'الدور', 'Rôle')} : {self.card['type_card']}\n"
                )
                if equipment_details:
                    description += ''.join(equipment_details)
                description += f"\n**{_tr(self.lang, 'Effect', 'التأثير', 'Effet')}**\n{self.card['description']}"
            else:
                description = (
                    f"{_tr(self.lang, 'A {r}-Rank card, Summoner!', 'بطاقة رتبة {ر}، أيها المُستدعي!', 'Une carte de rang {r}, Invocateur !').format(r=self.card['cardrank_name'])}\n\n"
                    f"**{_tr(self.lang, 'Card Informations', 'معلومات البطاقة', 'Informations de la carte')}**\n"
                    f"{self.card['power_emoji']} {_tr(self.lang, 'Power', 'القوة', 'Puissance')} : {self.card['power_stat']}\n"
                    f"{self.card['type_emoji']} {_tr(self.lang, 'Class', 'الفئة', 'Classe')} : {self.card['card_class_name']}\n"
                    f"{self.card['class_emoji']} {_tr(self.lang, 'Role', 'الدور', 'Rôle')} : {role_loc}\n"
                    f"{self.card['level_emoji']} {_tr(self.lang, 'Level', 'المستوى', 'Niveau')} : {self.card['current_level']}/{self.card['max_leveled']}\n"
                    f"{self.card['xp_emoji']} {_tr(self.lang, 'XP', 'خبرة', 'XP')} : {self.card['current_xp']}/{self.card['xp_cap']}\n"
                )
                if self.card['skill_name']:
                    description += f"🌟 **{_tr(self.lang, 'Skill', 'مهارة', 'Compétence')}:** {self.card['skill_name']}\n"
                if self.card['evo'] in (1, 2):
                    description += f"⚡ **{_tr(self.lang, 'Affinity', 'الأُلفة', 'Affinité')}:** {self.card['affinity']}%/{self.card['affinity_cap']}%\n"
                else:
                    description += f"⚡ **{_tr(self.lang, 'Affinity', 'الأُلفة', 'Affinité')}:** {self.card['affinity']}/100%\n"

                if equipment_details:
                    description += ''.join(equipment_details)

                description += (
                    f"\n**{_tr(self.lang, 'Combat Statistics', 'إحصائيات القتال', 'Statistiques de combat')}**\n"
                    f"{self.card['attack_emoji']} {_tr(self.lang, 'Attack', 'هجوم', 'Attaque')} : {self.card['attack_stat']}\n"
                    f"{self.card['health_emoji']} {_tr(self.lang, 'Health', 'صحة', 'Santé')} : {self.card['health_stat']}\n"
                    f"{self.card['speed_emoji']} {_tr(self.lang, 'Speed', 'سرعة', 'Vitesse')} : {self.card['speed_stat']}\n"
                    f"{self.card['defense_emoji']} {_tr(self.lang, 'Defense', 'دفاع', 'Défense')} : {self.card['defence_stat']}\n\n"
                )

                total_available_points = (
                    self.card['current_statpoints'] + self.card['current_attackpoints'] +
                    self.card['current_healthpoints'] + self.card['current_speedpoints'] +
                    self.card['current_defencepoints']
                )
                description += f"{_tr(self.lang, 'Available Stat Points', 'نقاط السمات المتاحة', 'Points de stats disponibles')}: {total_available_points}"

            name = self.card['card_name']
            if self.card['pseudo_name'] != "":
                name = f"{self.card['pseudo_name']} [{self.card['card_name']}]"

            embed = discord.Embed(title=name, description=description, color=self.embed_color)

            if self.card_image_url:
                embed.set_image(url=self.card_image_url)
            if self.rank_image_url:
                embed.set_thumbnail(url=self.rank_image_url)

            sl_quotes_en = [
                "The true hunter never rests.",
                "Your strength defines your fate.",
                "Another step towards greatness.",
                "The shadows whisper, will you answer?"
            ]
            sl_quotes_ar = [
                "الصيّاد الحقيقي لا يرتاح.",
                "قوتك تحدد مصيرك.",
                "خطوة أخرى نحو العظمة.",
                "الظلال تهمس، هل ستجيب؟"
            ]
            sl_quotes_fr = [
                "Le véritable chasseur ne se repose jamais.",
                "Ta force définit ton destin.",
                "Un pas de plus vers la grandeur.",
                "Les ombres murmurent, répondras-tu ?"
            ]
            quotes = sl_quotes_en if self.lang == 0 else (sl_quotes_ar if self.lang == 1 else sl_quotes_fr)
            embed.set_footer(text=random.choice(quotes))
            embed.timestamp = discord.utils.utcnow()
            return embed
        except Exception as e:
            print(f"Error creating simple embed: {e}")
            return discord.Embed(
                title=_tr(self.lang, "Error", "خطأ", "Erreur"),
                description=_tr(self.lang, "An error occurred while creating the embed.", "حدث خطأ أثناء إنشاء التضمين.", "Une erreur s’est produite lors de la création de l’embed.")
            )


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


    async def update_database(self, interaction: discord.Interaction):
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
            # STEP 1: Calculate true base stats by removing equipment boosts
            true_base_stats = {
                'attack': float(self.card['base_attack']),
                'health': float(self.card['base_health']),
                'defence': float(self.card['base_defence']),
                'speed': float(self.card['base_speed'])
            }
                    
            # Remove equipment boosts if present to get true base stats
            if equipment_boosts:
                # <CHANGE> Fixed order: first divide by percentage, then subtract flat
                for stat in ['attack', 'health', 'defence', 'speed']:
                    # Remove percentage boost first
                    if equipment_boosts[f'pct_{stat}'] != 0:
                        true_base_stats[stat] = true_base_stats[stat] / (1 + equipment_boosts[f'pct_{stat}']/100)
                    # Then remove flat boost
                    true_base_stats[stat] -= equipment_boosts[f'flat_{stat}']
                    # Ensure no negative values from rounding errors
                    true_base_stats[stat] = max(0, true_base_stats[stat])
                    
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
                # <CHANGE> Apply in correct order: flat first, then percentage
                for stat in ['attack', 'health', 'defence', 'speed']:
                    # Apply flat boosts first
                    new_base_stats[stat] += equipment_boosts[f'flat_{stat}']
                    # Then apply percentage boosts
                    if equipment_boosts[f'pct_{stat}'] != 0:
                        new_base_stats[stat] *= (1 + equipment_boosts[f'pct_{stat}']/100)
                    
            # Round base stats and ensure no negatives
            for stat in new_base_stats:
                new_base_stats[stat] = int(round(new_base_stats[stat]))
                new_base_stats[stat] = max(0, new_base_stats[stat])
                    
            # STEP 4: Calculate new current stats (with all boosts)
            new_current_stats = dict(new_true_base_stats)
                    
            # Apply equipment boosts if present
            if equipment_boosts:
                for stat in ['attack', 'health', 'defence', 'speed']:
                    # Apply flat boosts first
                    new_current_stats[stat] += equipment_boosts[f'flat_{stat}']
                    # Then apply percentage boosts
                    if equipment_boosts[f'pct_{stat}'] != 0:
                        new_current_stats[stat] *= (1 + equipment_boosts[f'pct_{stat}']/100)
                    
            # Apply support boosts if present
            if support_boosts:
                for stat in ['attack', 'health', 'defence', 'speed']:
                    # Apply flat boosts first
                    new_current_stats[stat] += support_boosts[f'flat_{stat}']
                    # Then apply percentage boosts
                    if support_boosts[f'pct_{stat}'] != 0:
                        new_current_stats[stat] *= (1 + support_boosts[f'pct_{stat}']/100)
                    
            # Round current stats and ensure no negatives
            for stat in new_current_stats:
                new_current_stats[stat] = int(round(new_current_stats[stat]))
                new_current_stats[stat] = max(0, new_current_stats[stat])
                    
            # Calculate new power stat
            new_power = new_current_stats['attack'] + new_current_stats['health'] + new_current_stats['defence'] + new_current_stats['speed']
                    
            # Calculate the new individual stat point values
            new_attack_points = self.original_individual_points['attack'] - self.used_from_individual['attack']
            new_health_points = self.original_individual_points['health'] - self.used_from_individual['health']
            new_speed_points = self.original_individual_points['speed'] - self.used_from_individual['speed']
            new_defence_points = self.original_individual_points['defence'] - self.used_from_individual['defence']
                    
            # Calculate new general stat points
            new_general_points = self.original_statpoints - sum(self.used_from_general.values())
        
            # VALIDATION: Check if stat adjustments would exceed caps
            validation_errors = []
            for stat_name, adjustment_amount in self.adjustments.items():
                if adjustment_amount > 0:
                    stat_cap_map = {
                        'attack': ('used_attackpoints', 'current_cap_attack',
                                _tr(self.lang, 'attack', 'الهجوم', 'attaque')),
                        'health': ('used_healthpoints', 'current_cap_health',
                                _tr(self.lang, 'health', 'الصحة', 'santé')),
                        'speed':  ('used_speedpoints',  'current_cap_speed',
                                _tr(self.lang, 'speed', 'السرعة', 'vitesse')),
                        'defence':('used_defencepoints','current_cap_defence',
                                _tr(self.lang, 'defence', 'الدفاع', 'défense')),
                    }
                    if stat_name in stat_cap_map:
                        used_column, cap_column, stat_loc = stat_cap_map[stat_name]
                        current_used = self.card.get(used_column, 0)
                        current_cap = self.card.get(cap_column, 0)
                        if current_used + adjustment_amount > current_cap:
                            remaining_capacity = current_cap - current_used
                            validation_errors.append(
                                _tr(
                                    self.lang,
                                    f"Cannot apply +{adjustment_amount} to {stat_loc}. This would exceed the stat cap limit.\n"
                                    f"Current {stat_loc}: {current_used}/{current_cap}\n"
                                    f"You can only apply {remaining_capacity} more point(s) to this stat.",
                                    f"لا يمكن تطبيق +{adjustment_amount} على {stat_loc}. هذا سيتجاوز الحد الأقصى للسمة.\n"
                                    f"{stat_loc} الحالي: {current_used}/{current_cap}\n"
                                    f"يمكنك تطبيق {remaining_capacity} نقطة إضافية فقط لهذه السمة.",
                                    f"Impossible d'appliquer +{adjustment_amount} à {stat_loc}. Cela dépasserait la limite de cette statistique.\n"
                                    f"{stat_loc} actuel : {current_used}/{current_cap}\n"
                                    f"Vous pouvez seulement ajouter {remaining_capacity} point(s) de plus à cette statistique."
                                )
                            )

            if validation_errors:
                error_message = "\n\n".join(validation_errors)
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ { _tr(self.lang, 'Error', 'خطأ', 'Erreur') }: {error_message}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ { _tr(self.lang, 'Error', 'خطأ', 'Erreur') }: {error_message}", ephemeral=True)
                return
                    
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
                current_statpoints = %s,
                level_attackpoints = level_attackpoints + %s,
                level_healthpoints = level_healthpoints + %s,
                level_speedpoints = level_speedpoints + %s,
                level_defencepoints = level_defencepoints + %s,
                used_statpoints = used_statpoints + %s,
                used_attackpoints = used_attackpoints + %s,
                used_defencepoints = used_defencepoints + %s,
                used_speedpoints = used_speedpoints + %s,
                used_healthpoints = used_healthpoints + %s,
                            
                -- Individual stat points (set to new values)
                current_attackpoints = %s,
                current_healthpoints = %s,
                current_speedpoints = %s,
                current_defencepoints = %s
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
                            
                new_general_points,  # Set current_statpoints to new value
                self.used_from_general['attack'],    # Only general points used for attack
                self.used_from_general['health'],    # Only general points used for health
                self.used_from_general['speed'],     # Only general points used for speed
                self.used_from_general['defence'],   # Only general points used for defence
                stats_used,  # Total stats used for used_statpoints
                self.adjustments['attack'],
                self.adjustments['defence'],
                self.adjustments['speed'],
                self.adjustments['health'],
                            
                new_attack_points,   # Set individual points to new values
                new_health_points,
                new_speed_points,
                new_defence_points,
                            
                self.card['user_id'],
                self.card['card_id']
            ))
                    
            # Update card data in memory with the new values
            self.card['attack_stat'] = new_current_stats['attack']
            self.card['defence_stat'] = new_current_stats['defence']
            self.card['speed_stat'] = new_current_stats['speed']
            self.card['health_stat'] = new_current_stats['health']
            self.card['power_stat'] = new_power
            self.card['base_attack'] = new_base_stats['attack']
            self.card['base_defence'] = new_base_stats['defence']
            self.card['base_speed'] = new_base_stats['speed']
            self.card['base_health'] = new_base_stats['health']
            self.card['current_statpoints'] = new_general_points
            self.card['current_attackpoints'] = new_attack_points
            self.card['current_healthpoints'] = new_health_points
            self.card['current_speedpoints'] = new_speed_points
            self.card['current_defencepoints'] = new_defence_points
                    
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
    def __init__(self, lang: int = 0):
        super().__init__(timeout=30)
        self.lang = lang
        self.value = None
        # Localize button labels after the view is constructed
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.style == discord.ButtonStyle.green:
                    item.label = _tr(lang, "Yes", "نعم", "Oui")
                elif item.style == discord.ButtonStyle.red:
                    item.label = _tr(lang, "No", "لا", "Non")

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        # Disable buttons after selection
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        # Disable buttons after selection
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()



class CardView(discord.ui.View):
    def __init__(self, ctx, cards, bot_instance, lang:int):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cards = cards
        self.bot = bot_instance  # Store the bot instance
        self.current_page = 0
        self.lang = lang
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # localize label on first render (discord copies label from decorator)
        button.label = _tr(self.lang, "Back", "رجوع", "Précédent")

        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                _tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."),
                ephemeral=True
            )
            return
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.label = _tr(self.lang, "Next", "التالي", "Suivant")

        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                _tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."),
                ephemeral=True
            )
            return
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await self.update_message(interaction)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.label = _tr(self.lang, "Stats", "الإحصائيات", "Stats")

        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                _tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."),
                ephemeral=True
            )
            return

        # Check if card type is Support or Booster
        current_card = self.cards[self.current_page]
        if current_card['type_card'] in ['Support', 'Booster']:
            await interaction.response.send_message(
                _tr(self.lang,
                    "Support and Booster cards don't have adjustable stats.",
                    "بطاقات الدعم والمعزِّز ليس لها نقاط يمكن تعديلها.",
                    "Les cartes Support et Booster n’ont pas de statistiques ajustables."
                ),
                ephemeral=True
            )
            return

        # Acknowledge the interaction immediately to prevent timeout
        await interaction.response.defer(ephemeral=False)

        try:
            # Get the current card's user_id and card_id
            user_id = current_card['user_id']
            card_id = current_card['card_id']

            # Check if there's an active stat adjustment session for this card
            query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            result = await execute_query_with_retry(query, (user_id, card_id))
            if result:
                # There's already an active session
                await interaction.followup.send(
                    _tr(self.lang,
                        "You already have an active stat adjustment session for this card. Please complete or cancel that session first.",
                        "لديك جلسة تعديل إحصائيات نشطة لهذه البطاقة. الرجاء إنهاؤها أو إلغاؤها أولاً.",
                        "Vous avez déjà une session de modification des stats active pour cette carte. Veuillez d’abord la terminer ou l’annuler."
                    ),
                    ephemeral=True
                )
                return

            # IMPORTANT: Fetch the latest card data from the database
            card_query = """
            SELECT uc.*,
                c.card_name, c.xp_cap, uc.max_leveled, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id,
                cc.card_class_name,
                w.weapon_id, w.weapon_name, w.weapon_rank, w.halloween,
                wh.weapon_surname, wh.id_weaponhalloween,
                a.weapon_id AS armor_id, a.weapon_name AS armor_name, a.weapon_rank AS armor_rank,
                s.weapon_id AS shield_id, s.weapon_name AS shield_name, s.weapon_rank AS shield_rank,
                uc.pseudo_name, uc.pseudo_image, uc.skill_id, sk.skill_name,
                uc.affinity, cu.affinity_cap
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON uc.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon_halloween wh ON uc.id_weaponhalloween = wh.id_weaponhalloween
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN skill sk ON uc.skill_id = sk.skill_id AND uc.skill_id <> 0
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            LEFT JOIN card_upgrades cu ON cu.card_id = uc.card_id AND cu.evo = uc.evo
            WHERE uc.user_id = %s AND uc.card_id = %s
            """
            updated_card = await execute_query_with_retry(card_query, (user_id, card_id))

            # Update the current card in the cards list with the latest data
            if updated_card:
                self.cards[self.current_page] = updated_card[0]
                current_card = updated_card[0]

            # Double-check that no session was created between our checks
            result = await execute_query_with_retry(query, (user_id, card_id))
            if result:
                await interaction.followup.send(
                    _tr(self.lang,
                        "Another stat adjustment session was just started for this card.",
                        "تم بدء جلسة تعديل إحصائيات أخرى لهذه البطاقة للتو.",
                        "Une autre session de modification des stats vient d’être lancée pour cette carte."
                    ),
                    ephemeral=True
                )
                return

            # No active session, create one and show the stat adjustment view
            insert_query = "INSERT INTO stat_status (user_id, card_id) VALUES (%s, %s)"
            await execute_query_with_retry(insert_query, (user_id, card_id))

            # Create and show the stat adjustment view with the updated card data
            stat_view = StatAdjustView(self.ctx, current_card, self, self.bot,self.lang)
            await interaction.edit_original_response(view=stat_view)

        except Exception as e:
            await interaction.followup.send(
                _tr(self.lang,
                    f"An error occurred while processing your request: {e}",
                    f"حدث خطأ أثناء معالجة طلبك: {e}",
                    f"Une erreur s’est produite lors du traitement de votre demande : {e}"
                ),
                ephemeral=True
            )

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
        if self.cards[self.current_page].get('pseudo_image') and self.cards[self.current_page]['pseudo_image'] != "":
            img = self.cards[self.current_page]['pseudo_image']

        # Handle card image
        card_image_path = self.get_image_path(img)
        if card_image_path and os.path.exists(card_image_path):
            card_file = discord.File(card_image_path, filename="card_image.png")
            files.append(card_file)
            embed.set_image(url="attachment://card_image.png")

        # Modify stats button visibility
        self.stats_button.disabled = self.cards[self.current_page]['type_card'] in ['Support','Booster']

        # localize button labels each time we edit the view
        self.back_button.label  = _tr(self.lang, "Back", "رجوع", "Précédent")
        self.next_button.label  = _tr(self.lang, "Next", "التالي", "Suivant")
        self.stats_button.label = _tr(self.lang, "Stats", "الإحصائيات", "Stats")

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
            3: discord.Color.from_rgb(139, 0, 0),    # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),  # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),  # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }

        embed_color = rank_colors.get(card.get('cardrank_id'), discord.Color.from_rgb(75, 0, 130))

        # Equipment details
        equipment_details = []
            
        # <CHANGE> Check if weapon is Halloween and use surname
        if card['weapon_id']:
            weapon_display_name = card['weapon_name']
            if card.get('w.halloween') == 1 and card.get('weapon_surname'):
                weapon_display_name = card['weapon_surname']
            equipment_details.append(f"{card['weapon_emoji']} " + _tr(self.lang, "Weapon", "سلاح", "Arme") + f": {weapon_display_name}\n")
        
        # <CHANGE> Check if armor is Halloween and use surname
        if card['armor_id']:
            armor_display_name = card['armor_name']
            if card.get('armor_halloween') == 1 and card.get('armor_surname'):
                armor_display_name = card['armor_surname']
            equipment_details.append(f"{card['armor_emoji']} " + _tr(self.lang, "Armor", "درع", "Armure") + f": {armor_display_name}\n")
        
        # <CHANGE> Check if shield is Halloween and use surname
        if card['shield_id']:
            shield_display_name = card['shield_name']
            if card.get('shield_halloween') == 1 and card.get('shield_surname'):
                shield_display_name = card['shield_surname']
            equipment_details.append(f"{card['secondshield_emoji']} " + _tr(self.lang, "Shield", "ترس", "Bouclier") + f": {shield_display_name}\n")

        total_available_points = (card['current_statpoints'] + card['current_attackpoints'] +
                                  card['current_healthpoints'] + card['current_speedpoints'] +
                                  card['current_defencepoints'])

        role = "Offence" if card['type_card'] == "Fighter" else card['type_card']
        role_localized = _tr(self.lang, role, "هجوم" if role == "Offence" else role, "Offensif" if role == "Offence" else role)

        # Header
        header = _tr(
            self.lang,
            f"A {card['cardrank_name']}-Rank card, Summoner!\n\n",
            f"بطاقة رتبة {card['cardrank_name']}، أيتها/أيها المستدعي!\n\n",
            f"Carte de rang {card['cardrank_name']}, Invocateur !\n\n"
        )

        # Sections / labels
        lbl_card_info   = _tr(self.lang, "**Card Information**", "**معلومات البطاقة**", "**Informations de la carte**")
        lbl_power       = _tr(self.lang, "Power", "القوة", "Puissance")
        lbl_class       = _tr(self.lang, "Class", "الفئة", "Classe")
        lbl_role        = _tr(self.lang, "Role", "الدور", "Rôle")
        lbl_level       = _tr(self.lang, "Level", "المستوى", "Niveau")
        lbl_xp          = _tr(self.lang, "XP", "الخبرة (XP)", "XP")
        lbl_effect      = _tr(self.lang, "**Effect**", "**التأثير**", "**Effet**")
        lbl_combat      = _tr(self.lang, "**Combat Statistics**", "**إحصائيات القتال**", "**Statistiques de combat**")
        lbl_attack      = _tr(self.lang, "Attack", "الهجوم", "Attaque")
        lbl_health      = _tr(self.lang, "Health", "الصحة", "Santé")
        lbl_speed       = _tr(self.lang, "Speed", "السرعة", "Vitesse")
        lbl_defence     = _tr(self.lang, "Defense", "الدفاع", "Défense")
        lbl_avail_pts   = _tr(self.lang, "Available Stat Points", "نقاط الإحصائيات المتاحة", "Points de stats disponibles")

        # Build description
        if card['type_card'].lower() in ['support', 'booster']:
            description = (
                header +
                f"{lbl_card_info}\n"
                f"{card['power_emoji']} {lbl_power} : {card['power_stat']}\n"
                f"{card['type_emoji']} {lbl_class} : {card['card_class_name']}\n"
                f"{card['class_emoji']} {lbl_role} : {card['type_card']}\n"
            )
            if equipment_details:
                description += ''.join(equipment_details)
            description += f"\n{lbl_effect}\n{card['description']}"
        else:
            description = (
                header +
                f"{lbl_card_info}\n"
                f"{card['power_emoji']} {lbl_power} : {card['power_stat']}\n"
                f"{card['type_emoji']} {lbl_class} : {card['card_class_name']}\n"
                f"{card['class_emoji']} {lbl_role} : {role_localized}\n"
                f"{card['level_emoji']} {lbl_level} : {card['current_level']}/{card['max_leveled']}\n"
                f"{card['xp_emoji']} {lbl_xp} : {card['current_xp']}/{card['xp_cap']}\n"
            )
            if card['skill_name']:
                description += f"🌟 **" + _tr(self.lang, "Skill", "المهارة", "Compétence") + f":** {card['skill_name']}\n"

            # Affinity
            aff_label = _tr(self.lang, "Affinity", "الألفة", "Affinité")
            if card['evo'] == 1:
                description += f"⚡ **{aff_label}:** {card['affinity']}%/{card['affinity_cap']}%\n"
            elif card['evo'] == 2:
                description += f"⚡ **{aff_label}:** {card['affinity']}%/{card['affinity_cap']}%\n"
            else:
                description += f"⚡ **{aff_label}:** {card['affinity']}/100%\n"

            if equipment_details:
                description += ''.join(equipment_details)

            description += (
                f"\n{lbl_combat}\n"
                f"{card['attack_emoji']} {lbl_attack} : {card['attack_stat']}\n"
                f"{card['health_emoji']} {lbl_health} : {card['health_stat']}\n"
                f"{card['speed_emoji']} {lbl_speed} : {card['speed_stat']}\n"
                f"{card['defense_emoji']} {lbl_defence} : {card['defence_stat']}\n\n"
            )
            description += f"{lbl_avail_pts}: {total_available_points}"

        # Title
        name = card['card_name']  # Default to card_name
        if card.get('pseudo_name') is not None and card['pseudo_name'] != '':
            name = f"{card['pseudo_name']} [{card['card_name']}]"

        embed = discord.Embed(
            title=name,
            description=description,
            color=embed_color
        )

        # Set the thumbnail image for the current card (we attach file on update_message)
        rank_image_path = self.get_image_path(card.get('cardrank_image'))
        if rank_image_path and os.path.exists(rank_image_path):
            # We set the URL in update_message when we attach the file. Here, keep logic minimal.
            pass

        # Localized footer quotes
        sl_quotes_en = [
            "The true hunter never rests.",
            "Your strength defines your fate.",
            "Another step towards greatness.",
            "The shadows whisper, will you answer?"
        ]
        sl_quotes_ar = [
            "الصياد الحقيقي لا يرتاح.",
            "قوتك تحدد مصيرك.",
            "خطوة أخرى نحو العظمة.",
            "الظلال تهمس… هل ستجيب؟"
        ]
        sl_quotes_fr = [
            "Le véritable chasseur ne se repose jamais.",
            "Ta force décide de ton destin.",
            "Un pas de plus vers la grandeur.",
            "Les ombres murmurent… répondrez-vous ?"
        ]
        quotes = sl_quotes_en if self.lang == 0 else (sl_quotes_ar if self.lang == 1 else sl_quotes_fr)

        footer_card = _tr(self.lang, "Card", "بطاقة", "Carte")
        footer_text = f"{random.choice(quotes)} • {footer_card} {self.current_page+1}/{len(self.cards)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()

        return embed


class AllView(discord.ui.View):
    def __init__(self, ctx, cards, lang: int):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cards = cards
        self.current_page = 0
        self.lang = lang
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # localize label (discord keeps decorator label until we change it)
        button.label = _tr(self.lang, "Back", "رجوع", "Précédent")

        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                _tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."),
                ephemeral=True
            )
            return
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.label = _tr(self.lang, "Next", "التالي", "Suivant")

        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                _tr(self.lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."),
                ephemeral=True
            )
            return
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await self.update_message(interaction)

    async def update_message(self, interaction):
        embed = self.create_embed()
        files = []

        # localize button labels every time we edit the view
        self.back_button.label = _tr(self.lang, "Back", "رجوع", "Précédent")
        self.next_button.label = _tr(self.lang, "Next", "التالي", "Suivant")

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
            3: discord.Color.from_rgb(139, 0, 0),    # A Rank - Purple
            4: discord.Color.from_rgb(255, 165, 0),  # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(0, 128, 128),  # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }
        embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))

        # localized labels
        lbl_header      = _tr(self.lang, "A {rank}-Rank card, Summoner!", "بطاقة رتبة {rank}، أيتها/أيها المستدعي!", "Carte de rang {rank}, Invocateur !")
        lbl_card_info   = _tr(self.lang, "**Card Information**", "**معلومات البطاقة**", "**Informations de la carte**")
        lbl_power       = _tr(self.lang, "Power", "القوة", "Puissance")
        lbl_class       = _tr(self.lang, "Class", "الفئة", "Classe")
        lbl_role        = _tr(self.lang, "Role", "الدور", "Rôle")
        lbl_effect      = _tr(self.lang, "**Effect**", "**التأثير**", "**Effet**")
        lbl_combat      = _tr(self.lang, "**Combat Statistics**", "**إحصائيات القتال**", "**Statistiques de combat**")
        lbl_attack      = _tr(self.lang, "Attack", "الهجوم", "Attaque")
        lbl_health      = _tr(self.lang, "Health", "الصحة", "Santé")
        lbl_speed       = _tr(self.lang, "Speed", "السرعة", "Vitesse")
        lbl_defence     = _tr(self.lang, "Defense", "الدفاع", "Défense")
        lbl_weapon      = _tr(self.lang, "Weapon", "سلاح", "Arme")
        lbl_armor       = _tr(self.lang, "Armor", "درع", "Armure")
        lbl_shield      = _tr(self.lang, "Shield", "ترس", "Bouclier")

        role = "Offence" if card['type_card'] == "Fighter" else card['type_card']
        role_localized = _tr(self.lang, role, "هجوم" if role == "Offence" else role, "Offensif" if role == "Offence" else role)

        description = (
            f"{lbl_header.format(rank=card['cardrank_name'])}\n\n"
            f"{lbl_card_info}\n"
            f"<:solopower:1302936147139235882> {lbl_power} : {card['power']}\n"
            f"<:solotype:1302937991869759539> {lbl_class} : {card['card_class_name']}\n"
            f"<:solorole:1302938003873726526> {lbl_role} : {role_localized}\n\n"
        )

        # equipment lines (if present)
        equipment_lines = []
        if card.get('weapon_id'):
            equipment_lines.append(f"{card.get('weapon_emoji','')} {lbl_weapon}: {card.get('weapon_name','')}\n")
        if card.get('armor_id'):
            equipment_lines.append(f"{card.get('armor_emoji','')} {lbl_armor}: {card.get('armor_name','')}\n")
        if card.get('shield_id'):
            equipment_lines.append(f"{card.get('secondshield_emoji','')} {lbl_shield}: {card.get('shield_name','')}\n")

        # Effect vs Combat stats
        if card['type_card'].lower() in ['support', 'booster']:
            description += f"{lbl_effect}\n{card['description']}"
            if equipment_lines:
                description += "\n" + "".join(equipment_lines)
        else:
            description += (
                f"{lbl_combat}\n"
                f"<:solosword:1302911919941746730> {lbl_attack} : {card['attack']}\n"
                f"<:soloheart:1302912058819477514> {lbl_health} : {card['health']}\n"
                f"<:solostar:1302912104264761374> {lbl_speed} : {card['speed']}\n"
                f"<:soloshield:1302911930242961461> {lbl_defence} : {card['defence']}"
            )
            if equipment_lines:
                description += "\n\n" + "".join(equipment_lines)

        embed = discord.Embed(
            title=f"{card['card_name']}",
            description=description,
            color=embed_color
        )

        # Set the thumbnail image for the initial card (kept from your original logic)
        if self.current_page == 0:
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                embed.set_thumbnail(url="attachment://rank_image.png")

        # localized footer quotes
        quotes_en = [
            "The true hunter never rests.",
            "Your strength defines your fate.",
            "Another card, another step to greatness.",
            "The shadows whisper, will you answer?"
        ]
        quotes_ar = [
            "الصياد الحقيقي لا يرتاح.",
            "قوتك تحدد مصيرك.",
            "بطاقة أخرى، خطوة أخرى نحو العظمة.",
            "الظلال تهمس… هل ستجيب؟"
        ]
        quotes_fr = [
            "Le véritable chasseur ne se repose jamais.",
            "Ta force décide de ton destin.",
            "Une autre carte, un pas de plus vers la grandeur.",
            "Les ombres murmurent… répondrez-vous ?"
        ]
        quotes = quotes_en if self.lang == 0 else (quotes_ar if self.lang == 1 else quotes_fr)

        footer_card = _tr(self.lang, "Card", "بطاقة", "Carte")
        footer_text = f"{random.choice(quotes)} • {footer_card} {self.current_page+1}/{len(self.cards)}"
        embed.set_footer(text=footer_text)
        embed.timestamp = discord.utils.utcnow()

        return embed


class SpawnCommands(commands.Cog):
    def __init__(self, bot, lang : int = 0):
        self.bot = bot
        self.lang = lang
        self.user_cooldowns = {}  # Store user cooldowns
        self.COOLDOWN_TIME = 2    # Cooldown time in seconds
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
    @commands.command(aliases=["mc", "myc"])
    async def mycards(self, ctx, filter_type: str = None, filter_value: str = None):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            # Optional: show what we actually got
            print("lang(raw):", self.lang)

            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(lang,
                                "Error: You need to register to the bot first.",
                                "خطأ: يجب أن تسجل في البوت أولاً.",
                                "Erreur : vous devez d'abord vous inscrire au bot."))
                return

            user_id = user_result[0]['user_id']

            # Base query for user cards
            # <CHANGE> Added LEFT JOIN for weapon_halloween to fetch custom surnames
            base_query = """
            SELECT uc.*,
                c.card_name, c.xp_cap, uc.max_leveled, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id,
                cc.card_class_name,
                w.weapon_id, w.weapon_name, w.weapon_rank, w.halloween,
                wh.weapon_surname, wh.id_weaponhalloween,
                a.weapon_id AS armor_id, a.weapon_name AS armor_name, a.weapon_rank AS armor_rank, a.halloween AS armor_halloween,
                ah.weapon_surname AS armor_surname, ah.id_weaponhalloween AS armor_id_weaponhalloween,
                s.weapon_id AS shield_id, s.weapon_name AS shield_name, s.weapon_rank AS shield_rank, s.halloween AS shield_halloween,
                sh.weapon_surname AS shield_surname, sh.id_weaponhalloween AS shield_id_weaponhalloween,
                uc.pseudo_name, uc.pseudo_image, uc.skill_id,
                sk.skill_name,
                cu.affinity_cap
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON uc.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon_halloween wh ON uc.id_weaponhalloween = wh.id_weaponhalloween
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN weapon_halloween ah ON uc.id_armorhalloween = ah.id_weaponhalloween
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            LEFT JOIN weapon_halloween sh ON uc.id_shieldhalloween = sh.id_weaponhalloween
            LEFT JOIN skill sk ON uc.skill_id = sk.skill_id AND uc.skill_id <> 0
            LEFT JOIN card_upgrades cu ON cu.card_id = uc.card_id AND cu.evo = uc.evo
            WHERE uc.user_id = %s
            """

            # Define rank mappings
            rank_map = {'s': 2, 'a': 3, 'b': 4, 'c': 5, 'd': 6, 'e': 7}

            # Handle filtering
            filter_condition = ""
            order_by = "ORDER BY uc.power_stat DESC"  # Default ordering
            query_params = [user_id]

            if filter_type:
                filter_type_lower = filter_type.lower()

                # rank with value
                if filter_type_lower == "rank" and filter_value:
                    rank_value = rank_map.get(filter_value.lower())
                    if not rank_value:
                        await ctx.send(_tr(lang,
                            "❌ Invalid rank. Please use: S, A, B, C, D, or E",
                            "❌ رتبة غير صالحة. الرجاء استخدام: S أو A أو B أو C أو D أو E",
                            "❌ Rang invalide. Veuillez utiliser : S, A, B, C, D ou E"))
                        return
                    filter_condition = "AND uc.card_rank = %s"
                    query_params.append(rank_value)
                    order_by = "ORDER BY uc.power_stat DESC"

                # stat sort
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

                # type filter
                elif filter_type_lower in ["offence", "offense", "fighter"]:
                    filter_condition = "AND c.type_card = 'Fighter'"
                    order_by = "ORDER BY uc.power_stat DESC"
                elif filter_type_lower == "support":
                    filter_condition = "AND c.type_card = 'Support'"
                    order_by = "ORDER BY uc.power_stat DESC"
                elif filter_type_lower == "booster":
                    filter_condition = "AND c.type_card = 'Booster'"
                    order_by = "ORDER BY uc.power_stat DESC"

                # direct rank input (s/a/b/c/d/e)
                elif filter_type_lower in rank_map:
                    rank_value = rank_map[filter_type_lower]
                    filter_condition = "AND uc.card_rank = %s"
                    query_params.append(rank_value)
                    order_by = "ORDER BY uc.power_stat DESC"

                else:
                    await ctx.send(_tr(lang,
                        "❌ Invalid filter. Available filters:\n"
                        "• Ranks: `s`, `a`, `b`, `c`, `d`, `e` or `rank s/a/b/c/d/e`\n"
                        "• Stats: `power`, `attack`, `health`, `speed`, `defense`\n"
                        "• Types: `offence`, `support`, `booster`",
                        "❌ فلتر غير صالح. المرشحات المتاحة:\n"
                        "• الرتب: `s`, `a`, `b`, `c`, `d`, `e` أو `rank s/a/b/c/d/e`\n"
                        "• الإحصائيات: `power`, `attack`, `health`, `speed`, `defense`\n"
                        "• الأنواع: `offence`, `support`, `booster`",
                        "❌ Filtre invalide. Filtres disponibles :\n"
                        "• Rangs : `s`, `a`, `b`, `c`, `d`, `e` ou `rank s/a/b/c/d/e`\n"
                        "• Stats : `power`, `attack`, `health`, `speed`, `defense`\n"
                        "• Types : `offence`, `support`, `booster`"))
                    return

            # Run query
            final_query = f"{base_query} {filter_condition} {order_by}"
            cards = await execute_query_with_retry(final_query, query_params)

            if not cards:
                # friendly filter description
                filter_desc = ""
                if filter_type:
                    if filter_type.lower() == "rank" and filter_value:
                        filter_desc = _tr(lang, f"Rank {filter_value.upper()} ",
                                        f"رتبة {filter_value.upper()} ",
                                        f"Rang {filter_value.upper()} ")
                    elif filter_type.lower() in rank_map:
                        filter_desc = _tr(lang, f"Rank {filter_type.upper()} ",
                                        f"رتبة {filter_type.upper()} ",
                                        f"Rang {filter_type.upper()} ")
                    elif filter_type.lower() in ["offence", "offense", "fighter"]:
                        filter_desc = _tr(lang, "Offence ", "هجومية ", "Offensive ")
                    elif filter_type.lower() == "support":
                        filter_desc = _tr(lang, "Support ", "دعم ", "Support ")
                    elif filter_type.lower() == "booster":
                        filter_desc = _tr(lang, "Booster ", "معزز ", "Booster ")
                    else:
                        filter_desc = f"{filter_type.title()} "

                await ctx.send(_tr(lang,
                                f"You don't have any {filter_desc}cards.",
                                f"ليس لديك أي بطاقات {filter_desc}.",
                                f"Vous n'avez aucune carte {filter_desc}."))
                return

            # View + embed
            view = CardView(ctx, cards, self.bot,lang)
            embed = view.create_embed()

            # Title decorations
            if filter_type:
                if filter_type.lower() == "rank" and filter_value:
                    embed.title = _tr(lang,
                        f"Rank {filter_value.upper()} - {embed.title}",
                        f"الرتبة {filter_value.upper()} - {embed.title}",
                        f"Rang {filter_value.upper()} - {embed.title}")
                elif filter_type.lower() in rank_map:
                    embed.title = _tr(lang,
                        f"Rank {filter_type.upper()} - {embed.title}",
                        f"الرتبة {filter_type.upper()} - {embed.title}",
                        f"Rang {filter_type.upper()} - {embed.title}")
                elif filter_type.lower() in ["offence", "offense", "fighter"]:
                    embed.title = _tr(lang,
                        f"Offence Cards - {embed.title}",
                        f"بطاقات هجومية - {embed.title}",
                        f"Cartes offensives - {embed.title}")
                elif filter_type.lower() == "support":
                    embed.title = _tr(lang,
                        f"Support Cards - {embed.title}",
                        f"بطاقات الدعم - {embed.title}",
                        f"Cartes de support - {embed.title}")
                elif filter_type.lower() == "booster":
                    embed.title = _tr(lang,
                        f"Booster Cards - {embed.title}",
                        f"بطاقات المعزز - {embed.title}",
                        f"Cartes booster - {embed.title}")
                elif filter_type.lower() in ["power", "attack", "health", "speed", "defense", "defence"]:
                    label = filter_type.title() if filter_type.lower() != "defence" else "Defense"
                    embed.title = _tr(lang,
                        f"Sorted by {label} - {embed.title}",
                        f"مرتبة حسب {label} - {embed.title}",
                        f"Trié par {label} - {embed.title}")

            files = []

            # Rank image (thumbnail)
            rank_image_path = view.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Card image (prefer pseudo)
            img = cards[0]['image']
            if cards[0].get('pseudo_image') and cards[0]['pseudo_image'] != "":
                img = cards[0]['pseudo_image']

            card_image_path = view.get_image_path(img)
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            # Send
            if files:
                message = await ctx.send(embed=embed, files=files, view=view)
                view.message = message
            else:
                message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=None)

        except Exception as e:
            print(f"Error: {e}")
            lang = await get_user_multilanguage(ctx.author.id)
            await ctx.send(_tr(lang,
                            "An error occurred while processing your request.",
                            "حدث خطأ أثناء معالجة طلبك.",
                            "Une erreur s'est produite lors du traitement de votre demande."))

    @commands.command(aliases=["cv", "cvw"])
    async def cardview(self, ctx, *, card_name):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(lang, "Error: You need to register to the bot first.",
                                   "خطأ: يجب أن تسجل في البوت أولاً.",
                                   "Erreur : vous devez d'abord vous inscrire au bot."))
                return

            user_id = user_result[0]['user_id']

            # Modified query to use LIKE for partial matches
            card_query = """
            SELECT uc.*,
                c.card_name, c.xp_cap, uc.max_leveled, c.type_card, c.description,
                cr.cardrank_name, cr.cardrank_image, cr.cardrank_id,
                cc.card_class_name,
                w.weapon_id, w.weapon_name, w.weapon_rank, w.halloween,
                wh.weapon_surname, wh.id_weaponhalloween,
                a.weapon_id AS armor_id, a.weapon_name AS armor_name, a.weapon_rank AS armor_rank, a.halloween AS armor_halloween,
                ah.weapon_surname AS armor_surname, ah.id_weaponhalloween AS armor_id_weaponhalloween,
                s.weapon_id AS shield_id, s.weapon_name AS shield_name, s.weapon_rank AS shield_rank, s.halloween AS shield_halloween,
                sh.weapon_surname AS shield_surname, sh.id_weaponhalloween AS shield_id_weaponhalloween,
                uc.pseudo_name, uc.pseudo_image, uc.skill_id,
                sk.skill_name,
                cu.affinity_cap
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            JOIN card_rank cr ON uc.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            LEFT JOIN weapon_halloween wh ON uc.id_weaponhalloween = wh.id_weaponhalloween
            LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
            LEFT JOIN weapon_halloween ah ON uc.id_armorhalloween = ah.id_weaponhalloween
            LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
            LEFT JOIN weapon_halloween sh ON uc.id_shieldhalloween = sh.id_weaponhalloween
            LEFT JOIN skill sk ON uc.skill_id = sk.skill_id AND uc.skill_id <> 0
            LEFT JOIN card_upgrades cu ON cu.card_id = uc.card_id AND cu.evo = uc.evo
            WHERE uc.user_id = %s
  AND (uc.name LIKE %s OR uc.pseudo_name LIKE %s OR c.card_name LIKE %s)
ORDER BY FIELD(cr.cardrank_name, 'S', 'A', 'B', 'C', 'D','E')
            """
            search_pattern = f"%{card_name}%"
            card_results = await execute_query_with_retry(card_query, (user_id, search_pattern, search_pattern, search_pattern))
            print(card_results)
            if not card_results:
                await ctx.send(_tr(lang,
                                   "You don't have any cards matching your description.",
                                   "ليس لديك أي بطاقات تطابق هذا الوصف.",
                                   "Vous n'avez aucune carte correspondant à votre description."))
                return

            if len(card_results) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(lang,
                                    "I found multiple cards matching your search. Please specify which one you'd like to view:",
                                    "وجدت عدة بطاقات تطابق بحثك. من فضلك حدّد أي بطاقة تريد عرضها:",
                                    "Plusieurs cartes correspondent à votre recherche. Veuillez préciser celle à afficher :"),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['pseudo_name'] if card.get('pseudo_name') else card['card_name']}"
                                       for card in card_results])
                embed.add_field(name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                                value=card_list, inline=False)
                embed.set_footer(text=_tr(lang,
                                          "Please use the command again with the exact name of the card you want to view.",
                                          "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للبطاقة التي تريد عرضها.",
                                          "Veuillez réutiliser la commande avec le nom exact de la carte à afficher."))
                await ctx.send(embed=embed)
                return

            # Single card
            card = card_results[0]
            display_name = card['pseudo_name'] if card.get('pseudo_name') else card['card_name']
            display_image = card['pseudo_image'] if card.get('pseudo_image') else card['image']

            # skill name (if any)
            skill_name = None
            if card['skill_id'] and card['skill_id'] != 0:
                skill_query = "SELECT skill_name FROM skill WHERE skill_id = %s"
                skill_result = await execute_query_with_retry(skill_query, (card['skill_id'],))
                if skill_result:
                    skill_name = skill_result[0]['skill_name']

            rank_colors = {
                2: discord.Color.from_rgb(255, 215, 0),  # S
                3: discord.Color.from_rgb(139, 0, 0),    # A
                4: discord.Color.from_rgb(255, 165, 0),  # B
                5: discord.Color.from_rgb(32, 131, 39),  # C
                6: discord.Color.from_rgb(0, 128, 128),  # D
                7: discord.Color.from_rgb(128, 128, 128) # E
            }
            embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))

            # Equipment section
            # Equipment section
            # Equipment section
            equipment_details = []
            if card['weapon_id']:
                # <CHANGE> Check if weapon is Halloween and use surname if available
                weapon_display_name = card['weapon_name']
                if card.get('w.halloween') == 1 and card.get('weapon_surname'):
                    weapon_display_name = card['weapon_surname']
                equipment_details.append(f"{card['weapon_emoji']} " + _tr(lang, "Weapon", "سلاح", "Arme") + f": {weapon_display_name}\n")

            if card['armor_id']:
                # <CHANGE> Check if armor is Halloween and use surname if available
                armor_display_name = card['armor_name']
                if card.get('armor_halloween') == 1 and card.get('armor_surname'):
                    armor_display_name = card['armor_surname']
                equipment_details.append(f"{card['armor_emoji']} " + _tr(lang, "Armor", "درع", "Armure") + f": {armor_display_name}\n")

            if card['shield_id']:
                # <CHANGE> Check if shield is Halloween and use surname if available
                shield_display_name = card['shield_name']
                if card.get('shield_halloween') == 1 and card.get('shield_surname'):
                    shield_display_name = card['shield_surname']
                equipment_details.append(f"{card['secondshield_emoji']} " + _tr(lang, "Shield", "ترس", "Bouclier") + f": {shield_display_name}\n")
            total_available_points = (card['current_statpoints'] + card['current_attackpoints'] +
                                      card['current_healthpoints'] + card['current_speedpoints'] +
                                      card['current_defencepoints'])
            role = "Offence" if card['type_card'] == "Fighter" else card['type_card']
            role_localized = _tr(lang, role, "هجومية" if role == "Offence" else role, "Offensive" if role == "Offence" else role)

            if card['type_card'].lower() in ['support', 'booster']:
                description = (
                    _tr(lang, f"A {card['cardrank_name']}-Rank card, Summoner!",
                                f"بطاقة رتبة {card['cardrank_name']}, أيتها/أيها المستدعي!",
                                f"Carte de rang {card['cardrank_name']}, Invocateur !")
                    + "\n\n"
                    + _tr(lang, "**Card Information**", "**معلومات البطاقة**", "**Informations de la carte**") + "\n"
                    + f"{card['power_emoji']} " + _tr(lang, "Power", "القوة", "Puissance") + f" : {card['power_stat']}\n"
                    + f"{card['type_emoji']} " + _tr(lang, "Class", "الفئة", "Classe") + f" : {card['card_class_name']}\n"
                    + f"{card['class_emoji']} " + _tr(lang, "Role", "الدور", "Rôle") + f" : {card['type_card']}\n"
                )
                if equipment_details:
                    description += ''.join(equipment_details)
                description += "\n" + _tr(lang, "**Effect**", "**التأثير**", "**Effet**") + f"\n{card['description']}"
            else:
                description = (
                    _tr(lang, f"A {card['cardrank_name']}-Rank card, Summoner!",
                                f"بطاقة رتبة {card['cardrank_name']}, أيتها/أيها المستدعي!",
                                f"Carte de rang {card['cardrank_name']}, Invocateur !")
                    + "\n\n"
                    + _tr(lang, "**Card Information**", "**معلومات البطاقة**", "**Informations de la carte**") + "\n"
                    + f"{card['power_emoji']} " + _tr(lang, "Power", "القوة", "Puissance") + f" : {card['power_stat']}\n"
                    + f"{card['type_emoji']} " + _tr(lang, "Class", "الفئة", "Classe") + f" : {card['card_class_name']}\n"
                    + f"{card['class_emoji']} " + _tr(lang, "Role", "الدور", "Rôle") + f" : {role_localized}\n"
                    + f"{card['level_emoji']} " + _tr(lang, "Level", "المستوى", "Niveau") + f" : {card['current_level']}/{card['max_leveled']}\n"
                    + f"{card['xp_emoji']} XP : {card['current_xp']}/{card['xp_cap']}\n"
                )
                if skill_name:
                    description += "🌟 " + _tr(lang, "**Skill:**", "**المهارة:**", "**Compétence :**") + f" {skill_name}\n"
                if card['evo'] == 1:
                    description += "⚡ " + _tr(lang, "**Affinity:**", "**الأُلْفَة:**", "**Affinité :**") + f" {card['affinity']}%/{card['affinity_cap']}%\n"
                elif card['evo'] == 2:
                    description += "⚡ " + _tr(lang, "**Affinity:**", "**الأُلْفَة:**", "**Affinité :**") + f" {card['affinity']}%/{card['affinity_cap']}%\n"
                else:
                    description += "⚡ " + _tr(lang, "**Affinity:**", "**الأُلْفَة:**", "**Affinité :**") + f" {card['affinity']}/100%\n"
                if equipment_details:
                    description += ''.join(equipment_details)
                description += (
                    "\n" + _tr(lang, "**Combat Statistics**", "**إحصائيات القتال**", "**Statistiques de combat**") + "\n"
                    + f"{card['attack_emoji']} " + _tr(lang, "Attack", "الهجوم", "Attaque") + f" : {card['attack_stat']}\n"
                    + f"{card['health_emoji']} " + _tr(lang, "Health", "الصحة", "Santé") + f" : {card['health_stat']}\n"
                    + f"{card['speed_emoji']} " + _tr(lang, "Speed", "السرعة", "Vitesse") + f" : {card['speed_stat']}\n"
                    + f"{card['defense_emoji']} " + _tr(lang, "Defense", "الدفاع", "Défense") + f" : {card['defence_stat']}\n\n"
                )
                description += _tr(lang, "Available Stat Points:", "نقاط الإحصائيات المتاحة:", "Points de stat disponibles :") + f" {total_available_points}"

            name = card['card_name']
            if card['pseudo_name'] is not None and card['pseudo_name'] != '':
                name = f"{card['pseudo_name']} [{card['card_name']}]"

            embed = discord.Embed(title=name, description=description, color=embed_color)

            files = []
            # rank thumb
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")
            # main image
            img = card['image']
            if card.get('pseudo_image') and card['pseudo_image'] != "":
                img = card['pseudo_image']
            card_image_path = self.get_image_path(img)
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            sl_quotes = {
                0: [
                    "The true hunter never rests.",
                    "Your strength defines your fate.",
                    "Another card, another step to greatness.",
                    "The shadows whisper, will you answer?"
                ],
                1: [
                    "الصياد الحقيقي لا يرتاح.",
                    "قوتك تحدد مصيرك.",
                    "بطاقة أخرى، خطوة أخرى نحو العظمة.",
                    "الظلال تهمس… هل ستجيب؟"
                ],
                3: [
                    "Le véritable chasseur ne se repose jamais.",
                    "Ta force décide de ton destin.",
                    "Une autre carte, un pas de plus vers la grandeur.",
                    "Les ombres murmurent… répondrez-vous ?"
                ]
            }
            quotes = sl_quotes.get(lang, sl_quotes[0])
            embed.set_footer(text=random.choice(quotes))
            embed.timestamp = discord.utils.utcnow()

            # inner view with localized button text
            class CardView(discord.ui.View):
                def __init__(self, ctx, card, bot_instance, lang: int):
                    super().__init__(timeout=180)
                    self.ctx = ctx
                    self.card = card
                    self.bot = bot_instance
                    self.lang = lang
                    # will be set after creation to apply label
                    self.stats_button.disabled = card['type_card'] in ['Support','Booster']
                    self.stats_button.label = _tr(lang, "Stats", "الإحصائيات", "Stats")
                @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
                async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != self.ctx.author.id:
                        await interaction.response.send_message(
                            _tr(lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."),
                            ephemeral=True
                        )
                        return

                    stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
                    stat_status_result = await execute_query_with_retry(stat_status_query, (self.card['user_id'], self.card['card_id']))
                    if stat_status_result:
                        await interaction.response.send_message(
                            _tr(lang,
                                "This card is currently being adjusted. Please wait until the process is completed.",
                                "يتم تعديل هذه البطاقة حالياً. الرجاء الانتظار حتى اكتمال العملية.",
                                "Cette carte est en cours de modification. Veuillez attendre la fin du processus."),
                            ephemeral=True
                        )
                        return

                    # fetch latest
                    card_query = """
                    SELECT uc.*,
                        c.card_name, c.xp_cap, uc.max_leveled, c.type_card, c.description,
                        cr.cardrank_name, cr.cardrank_image, cr.cardrank_id,
                        cc.card_class_name,
                        w.weapon_id, w.weapon_name, w.weapon_rank, w.halloween,
                        wh.weapon_surname, wh.id_weaponhalloween,
                        a.weapon_id AS armor_id, a.weapon_name AS armor_name, a.weapon_rank AS armor_rank, a.halloween AS armor_halloween,
                        ah.weapon_surname AS armor_surname, ah.id_weaponhalloween AS armor_id_weaponhalloween,
                        s.weapon_id AS shield_id, s.weapon_name AS shield_name, s.weapon_rank AS shield_rank, s.halloween AS shield_halloween,
                        sh.weapon_surname AS shield_surname, sh.id_weaponhalloween AS shield_id_weaponhalloween,
                        uc.pseudo_name, uc.pseudo_image, uc.skill_id, sk.skill_name,
                        uc.affinity, cu.affinity_cap
                    FROM user_cards uc
                    JOIN card c ON uc.card_id = c.card_id
                    JOIN card_rank cr ON uc.card_rank = cr.cardrank_id
                    JOIN card_class cc ON c.card_class_id = cc.card_class_id
                    LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
                    LEFT JOIN weapon_halloween wh ON uc.id_weaponhalloween = wh.id_weaponhalloween
                    LEFT JOIN weapon a ON uc.armor_id = a.weapon_id
                    LEFT JOIN weapon_halloween ah ON uc.id_armorhalloween = ah.id_weaponhalloween
                    LEFT JOIN weapon s ON uc.shield_id = s.weapon_id
                    LEFT JOIN weapon_halloween sh ON uc.id_shieldhalloween = sh.id_weaponhalloween
                    LEFT JOIN skill sk ON uc.skill_id = sk.skill_id AND uc.skill_id <> 0
                    LEFT JOIN card_upgrades cu ON cu.card_id = uc.card_id AND cu.evo = uc.evo
                    WHERE uc.user_id = %s AND uc.card_id = %s
                    """
                    updated_card_result = await execute_query_with_retry(card_query, (self.card['user_id'], self.card['card_id']))
                    if updated_card_result:
                        self.card = updated_card_result[0]

                    # double-check lock
                    stat_status_result = await execute_query_with_retry(stat_status_query, (self.card['user_id'], self.card['card_id']))
                    if stat_status_result:
                        await interaction.response.send_message(
                            _tr(lang,
                                "This card is currently being adjusted. Please wait until the process is completed.",
                                "يتم تعديل هذه البطاقة حالياً. الرجاء الانتظار حتى اكتمال العملية.",
                                "Cette carte est en cours de modification. Veuillez attendre la fin du processus."),
                            ephemeral=True
                        )
                        return

                    # lock and open stat view
                    insert_query = "INSERT INTO stat_status (user_id, card_id) VALUES (%s, %s)"
                    await execute_query_with_retry(insert_query, (self.card['user_id'], self.card['card_id']))

                    stat_view = StatAdjustView(self.ctx, self.card, self, self.bot,lang)
                    await interaction.response.edit_message(view=stat_view)

            view = CardView(ctx, card, self.bot, lang)

            if files:
                message = await ctx.send(embed=embed, files=files, view=view)
            else:
                message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=None)

        except Exception as e:
            print(f"Error: {e}")
            await ctx.send(_tr(lang, "An error occurred while processing your request.",
                               "حدث خطأ أثناء معالجة طلبك.",
                               "Une erreur s'est produite lors du traitement de votre demande."))

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    @commands.command(aliases=["wv", "wvw"])
    async def weaponview(self, ctx, *, weapon_name):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(lang, "Error: You need to register to the bot first.",
                                "خطأ: يجب أن تسجل في البوت أولاً.",
                                "Erreur : vous devez d'abord vous inscrire au bot."))
                return

            weapon_query = """
                SELECT w.*, wr.weaponrank_name
                FROM weapon w
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                WHERE w.weapon_name LIKE %s
            """
            search_pattern = f"%{weapon_name}%"
            weapons = await execute_query_with_retry(weapon_query, (search_pattern,))

            if not weapons:
                await ctx.send(_tr(lang,
                                "No weapons found matching your description.",
                                "لم يتم العثور على أسلحة مطابقة لوصفك.",
                                "Aucune arme ne correspond à votre description."))
                return

            if len(weapons) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Weapons Found", "تم العثور على عدة أسلحة", "Plusieurs armes trouvées"),
                    description=_tr(lang,
                                    "I found multiple weapons matching your search. Please specify which one you'd like to view:",
                                    "وجدت عدة أسلحة تطابق بحثك. من فضلك حدّد أي سلاح تريد عرضه:",
                                    "Plusieurs armes correspondent à votre recherche. Veuillez préciser laquelle afficher :"),
                    color=discord.Color.blue()
                )
                weapon_list = "\n".join([f"• {weapon['weapon_name']}" for weapon in weapons])
                embed.add_field(name=_tr(lang, "Matching Weapons", "الأسلحة المطابقة", "Armes correspondantes"),
                                value=weapon_list, inline=False)
                embed.set_footer(text=_tr(lang,
                                        "Please use the command again with the exact name of the weapon you want to view.",
                                        "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للسلاح الذي تريد عرضه.",
                                        "Veuillez réutiliser la commande avec le nom exact de l'arme à afficher."))
                await ctx.send(embed=embed)
                return

            weapon = weapons[0]
            embed = discord.Embed(
                title=f"⚔️ {weapon['weapon_name']} ⚔️",
                description=weapon['weapon_description'],
                color=discord.Color.from_rgb(75, 0, 130)
            )

            embed.add_field(name=_tr(lang, "Type", "النوع", "Type"),
                            value=weapon['weaponrank_name'], inline=False)

            is_halloween = weapon.get('halloween', 0) != 0

            def add_boost(name_en, name_ar, name_fr, flat_key, pct_key,
                        min_flat_key=None, max_flat_key=None, min_pct_key=None, max_pct_key=None):
                vals = []
                if is_halloween:
                    # Flat range
                    if min_flat_key and max_flat_key:
                        min_val = weapon.get(min_flat_key) or 0
                        max_val = weapon.get(max_flat_key) or 0
                        if (min_val != 0 or max_val != 0):
                            vals.append(f"+{min_val}-{max_val}")
                    # Percent range
                    if min_pct_key and max_pct_key:
                        min_pct = weapon.get(min_pct_key) or 0
                        max_pct = weapon.get(max_pct_key) or 0
                        if (min_pct != 0 or max_pct != 0):
                            vals.append(f"+{min_pct}-{max_pct}%")
                else:
                    if weapon.get(flat_key):
                        vals.append(f"+{weapon[flat_key]}")
                    if weapon.get(pct_key):
                        vals.append(f"+{weapon[pct_key]}%")

                if vals:
                    embed.add_field(name=_tr(lang, name_en, name_ar, name_fr),
                                    value=" | ".join(vals), inline=True)


            add_boost("Attack Boost", "تعزيز الهجوم", "Bonus d'attaque", 
                    "attack_boost", "attack_percentage",
                    "min_attackboost", "max_attackboost", "min_attackpercentage", "max_attackpercentage")
            add_boost("Health Boost", "تعزيز الصحة", "Bonus de santé", 
                    "health_boost", "health_percentage",
                    "min_healthboost", "max_healthboost", "min_healthpercentage", "max_healthpercentage")
            add_boost("Speed Boost", "تعزيز السرعة", "Bonus de vitesse", 
                    "speed_boost", "speed_percentage",
                    "min_speedboost", "max_speedboost", "min_speedpercentage", "max_speedpercentage")
            add_boost("Defense Boost", "تعزيز الدفاع", "Bonus de défense", 
                    "defense_boost", "defense_percentage",
                    "min_defenseboost", "max_defenseboost", "min_defensepercentage", "max_defensepercentage")

            if is_halloween:
                if 'cprice' in weapon and weapon['cprice'] > 0:
                    embed.add_field(name=_tr(lang, "Candy Price", "سعر الحلوى", "Prix en bonbons"),
                                    value=f"{weapon['cprice']:,}", inline=True)
            else:
                if 'price' in weapon and weapon['price'] > 0:
                    embed.add_field(name=_tr(lang, "Price", "السعر", "Prix"),
                                    value=f"{weapon['price']:,}", inline=True)
            
            if 'quantiter' in weapon:
                embed.add_field(name=_tr(lang, "Quantity", "الكمية", "Quantité"),
                                value=str(weapon['quantiter']), inline=True)

            sl_quotes = {
                0: [
                    "A weapon is only as strong as its wielder.",
                    "Choose your weapon wisely, hunter.",
                    "Power lies in both skill and equipment.",
                    "Every weapon has its purpose."
                ],
                1: [
                    "السلاح لا يقوى إلا بصاحبه.",
                    "اختر سلاحك بحكمة، أيها الصياد.",
                    "القوة في المهارة والمعدات معاً.",
                    "لكل سلاحٍ غايته."
                ],
                2: [
                    "Une arme n'est forte que par son porteur.",
                    "Choisissez votre arme avec sagesse, chasseur.",
                    "La puissance réside dans l'habileté et l'équipement.",
                    "Chaque arme a sa raison d'être."
                ]
            }
            quotes = sl_quotes.get(lang, sl_quotes[0])
            embed.set_footer(text=f"{random.choice(quotes)} • " + _tr(lang, "Weapon Details", "تفاصيل السلاح", "Détails de l'arme"))
            embed.timestamp = discord.utils.utcnow()

            files = []
            if weapon.get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapon['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            await ctx.send(embed=embed, files=files)

        except Exception as e:
            print(f"Error: {e}")
            await ctx.send(_tr(lang, "An error occurred while processing your request.",
                            "حدث خطأ أثناء معالجة طلبك.",
                            "Une erreur s'est produite lors du traitement de votre demande."))

    @commands.command(aliases=["wl", "wlt"])
    async def weaponlist(self, ctx):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(lang, "Error: You need to register to the bot first.",
                                "خطأ: يجب أن تسجل في البوت أولاً.",
                                "Erreur : vous devez d'abord vous inscrire au bot."))
                return

            weapons_query = """
                SELECT w.*, wr.weaponrank_name
                FROM weapon w
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                ORDER BY FIELD(wr.weaponrank_name, 'S', 'A', 'B', 'C', 'D', 'E')
            """
            weapons = await execute_query_with_retry(weapons_query)

            if not weapons:
                await ctx.send(_tr(lang, "No weapons found in the database.",
                                "لم يتم العثور على أسلحة في قاعدة البيانات.",
                                "Aucune arme trouvée dans la base de données."))
                return

            view = WeaponView(ctx, weapons, lang)  # your existing view
            embed = view.create_embed()
            files = []

            if weapons[0].get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapons[0]['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            view.message = await ctx.send(embed=embed, files=files, view=view)

        except Exception as e:
            print(f"Error: {e}")
            await ctx.send(_tr(lang, "An error occurred while processing your request.",
                            "حدث خطأ أثناء معالجة طلبك.",
                            "Une erreur s'est produite lors du traitement de votre demande."))

    @commands.command(aliases=["mw", "myw"])
    async def myweapons(self, ctx):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(lang, "Error: You need to register to the bot first.",
                                "خطأ: يجب أن تسجل في البوت أولاً.",
                                "Erreur : vous devez d'abord vous inscrire au bot."))
                return

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_result[0]['user_id'],))

            if not inventory_result:
                await ctx.send(_tr(lang, "Error: You don't have an inventory.",
                                "خطأ: ليس لديك مخزون.",
                                "Erreur : vous n'avez pas d'inventaire."))
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
                await ctx.send(_tr(lang, "You don't have any weapons in your inventory.",
                                "ليس لديك أي أسلحة في مخزونك.",
                                "Vous n'avez aucune arme dans votre inventaire."))
                return

            view = WeaponView(ctx, weapons, lang)  # your existing view
            embed = view.create_embed()
            files = []

            if weapons[0].get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapons[0]['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            view.message = await ctx.send(embed=embed, files=files, view=view)

        except Exception as e:
            print(f"Error: {e}")
            await ctx.send(_tr(lang, "An error occurred while processing your request.",
                            "حدث خطأ أثناء معالجة طلبك.",
                            "Une erreur s'est produite lors du traitement de votre demande."))

    @commands.command(name="myhweapons",aliases=["mhw", "myhw"])
    async def myhweapons(self, ctx):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(lang, "Error: You need to register to the bot first.",
                                "خطأ: يجب أن تسجل في البوت أولاً.",
                                "Erreur : vous devez d'abord vous inscrire au bot."))
                return

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_result[0]['user_id'],))

            if not inventory_result:
                await ctx.send(_tr(lang, "Error: You don't have an inventory.",
                                "خطأ: ليس لديك مخزون.",
                                "Erreur : vous n'avez pas d'inventaire."))
                return

            inventory_id = inventory_result[0]['inventory_id']

            weapons_query = """
                SELECT wh.*, wr.weaponrank_name, w.weapon_rank, w.weapon_image
                FROM weapon_halloween wh
                JOIN weapon w ON wh.weapon_id = w.weapon_id
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                WHERE wh.inventory_id = %s
                ORDER BY wh.id_weaponhalloween ASC
            """
            weapons = await execute_query_with_retry(weapons_query, (inventory_id,))

            if not weapons:
                await ctx.send(_tr(lang, "You don't have any Halloween weapons in your inventory.",
                                "ليس لديك أي أسلحة هالوين في مخزونك.",
                                "Vous n'avez aucune arme Halloween dans votre inventaire."))
                return

            view = HalloweenWeaponView(ctx, weapons, lang)
            embed = view.create_embed()
            files = []

            if weapons[0].get('weapon_image'):
                weapon_image_path = os.path.join(self.image_path, weapons[0]['weapon_image'])
                if os.path.exists(weapon_image_path):
                    weapon_file = discord.File(weapon_image_path, filename="weapon_image.png")
                    files.append(weapon_file)
                    embed.set_image(url="attachment://weapon_image.png")

            view.message = await ctx.send(embed=embed, files=files, view=view)
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send(_tr(lang, "An error occurred while processing your request.",
                            "حدث خطأ أثناء معالجة طلبك.",
                            "Une erreur s'est produite lors du traitement de votre demande."))

    @commands.command(name="setlang")
    async def setlang(self, ctx, *, langv: str):
        try:
            # normalize user input
            s = str(langv).strip().lower()

            # accepted aliases → language codes
            alias_to_code = {
                "0": 0, "en": 0, "english": 0,
                "1": 1, "ar": 1, "arabic": 1,
                "3": 3, "fr": 3, "french": 3,
            }
            # resolve code
            if s not in alias_to_code:
                await ctx.send(
                    "Error: Please specify language as en/english, ar/arabic, or fr/french (or 0/1/3)."
                )
                return
            code = alias_to_code[s]

            # fetch user
            user_query = "SELECT user_id, multilanguage FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send("Error: You need to register with the bot first.")
                return

            current = user_result[0].get("multilanguage", 0)
            if int(current) == code:
                names = {0: "English", 1: "Arabic", 3: "French"}
                await ctx.send(f"This is already your current language setting ({names[code]}).")
                return

            # update language
            await execute_query_with_retry(
                "UPDATE user SET multilanguage = %s WHERE user_authorid = %s",
                (code, str(ctx.author.id))
            )

            names = {0: "English", 1: "Arabic", 3: "French"}
            await ctx.send(f"Language successfully updated to {names[code]}.")
        except Exception as e:
            print(f"Error in setlang command: {e}")
            await ctx.send("An error occurred while updating your language setting. Please try again later.")


    @commands.command(aliases=["ci", "cif"])
    async def cardinfo(self, ctx, *, card_name):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
           
            if not user_result:
                await ctx.send(_tr(lang,
                                   "Error: You need to register to the bot first.",
                                   "خطأ: يجب أن تسجل في البوت أولاً.",
                                   "Erreur : vous devez d'abord vous inscrire au bot."))
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
                await ctx.send(_tr(lang,
                                   "No cards found matching your description.",
                                   "لم يتم العثور على بطاقات مطابقة لوصفك.",
                                   "Aucune carte ne correspond à votre description."))
                return

            if len(card_results) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(lang,
                                    "I found multiple cards matching your search. Please specify which one you'd like to view:",
                                    "وجدت عدة بطاقات تطابق بحثك. من فضلك حدّد أي بطاقة تريد عرضها:",
                                    "Plusieurs cartes correspondent à votre recherche. Veuillez préciser celle à afficher :"),
                    color=discord.Color.blue()
                )

                card_list = "\n".join([f"• {card['card_name']}" for card in card_results])
                embed.add_field(name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                                value=card_list, inline=False)
                embed.set_footer(text=_tr(lang,
                                          "Please use the command again with the exact name of the card you want to view.",
                                          "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للبطاقة التي تريد عرضها.",
                                          "Veuillez réutiliser la commande avec le nom exact de la carte à afficher."))
               
                await ctx.send(embed=embed)
                return

            # Single card
            card = card_results[0]

            rank_colors = {
                2: discord.Color.from_rgb(255, 215, 0),  # S
                3: discord.Color.from_rgb(139, 0, 0),    # A
                4: discord.Color.from_rgb(255, 165, 0),  # B
                5: discord.Color.from_rgb(32, 131, 39),  # C
                6: discord.Color.from_rgb(0, 128, 128),  # D
                7: discord.Color.from_rgb(128, 128, 128) # E
            }
           
            embed_color = rank_colors.get(card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))
            role = "Offence" if card['type_card'] == "Fighter" else card['type_card']
            role_loc = _tr(lang, role, "هجومية" if role == "Offence" else role, "Offensive" if role == "Offence" else role)

            description = (
                _tr(lang, f"A {card['cardrank_name']}-Rank card, Summoner!",
                            f"بطاقة رتبة {card['cardrank_name']}, أيتها/أيها المستدعي!",
                            f"Carte de rang {card['cardrank_name']}, Invocateur !")
                + "\n\n"
                + _tr(lang, "**Card Information**", "**معلومات البطاقة**", "**Informations de la carte**") + "\n"
                + f"<:solopower:1302936147139235882> " + _tr(lang, "Power", "القوة", "Puissance") + f" : {card['power']}\n"
                + f"<:solotype:1302937991869759539> " + _tr(lang, "Class", "الفئة", "Classe") + f" : {card['card_class_name']}\n"
                + f"<:solorole:1302938003873726526> " + _tr(lang, "Role", "الدور", "Rôle") + f" : {role_loc}\n\n"
            )

            if card['type_card'].lower() in ['support', 'booster']:
                description += (
                    _tr(lang, "**Effect**", "**التأثير**", "**Effet**") + "\n"
                    + f"{card['description']}"
                )
            else:
                description += (
                    _tr(lang, "**Combat Statistics**", "**إحصائيات القتال**", "**Statistiques de combat**") + "\n"
                    + f"<:solosword:1302911919941746730> " + _tr(lang, "Attack", "الهجوم", "Attaque") + f" : {card['attack']}\n"
                    + f"<:soloheart:1302912058819477514> " + _tr(lang, "Health", "الصحة", "Santé") + f" : {card['health']}\n"
                    + f"<:solostar:1302912104264761374> " + _tr(lang, "Speed", "السرعة", "Vitesse") + f" : {card['speed']}\n"
                    + f"<:soloshield:1302911930242961461> " + _tr(lang, "Defense", "الدفاع", "Défense") + f" : {card['defence']}"
                )

            embed = discord.Embed(
                title=f"{card['card_name']}",
                description=description,
                color=embed_color
            )

            files = []
           
            # Rank image (thumbnail)
            rank_image_path = self.get_image_path(card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Card image (main image)
            card_image_path = self.get_image_path(card['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            sl_quotes = {
                0: [
                    "The true hunter never rests.",
                    "Your strength defines your fate.",
                    "Another card, another step to greatness.",
                    "The shadows whisper, will you answer?"
                ],
                1: [
                    "الصياد الحقيقي لا يرتاح.",
                    "قوتك تحدد مصيرك.",
                    "بطاقة أخرى، خطوة أخرى نحو العظمة.",
                    "الظلال تهمس… هل ستجيب؟"
                ],
                3: [
                    "Le véritable chasseur ne se repose jamais.",
                    "Ta force décide de ton destin.",
                    "Une autre carte, un pas de plus vers la grandeur.",
                    "Les ombres murmurent… répondrez-vous ?"
                ]
            }
            embed.set_footer(text=random.choice(sl_quotes.get(lang, sl_quotes[0])))
            embed.timestamp = discord.utils.utcnow()

            if files:
                await ctx.send(embed=embed, files=files)
            else:
                await ctx.send(embed=embed)
               
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send(_tr(lang,
                               "An error occurred while processing your request.",
                               "حدث خطأ أثناء معالجة طلبك.",
                               "Une erreur s'est produite lors du traitement de votre demande."))

    @commands.command(aliases=["cl", "clt"])
    async def cardlist(self, ctx, filter_type: str = None, filter_value: str = None):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(self._tr(lang,
                                   "Error: You need to register to the bot first.",
                                   "خطأ: يجب أن تسجل في البوت أولاً.",
                                   "Erreur : vous devez d'abord vous inscrire au bot."))
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
                    await ctx.send(self._tr(lang,
                                       "Invalid rank. Please use: S, A, B, C, D, or E",
                                       "رتبة غير صالحة. الرجاء استخدام: S أو A أو B أو C أو D أو E",
                                       "Rang invalide. Veuillez utiliser : S, A, B, C, D ou E"))
                    return
                filter_condition = "AND c.card_rank = %s"
                query_params.append(rank_value)
            elif filter_type:
                await ctx.send(self._tr(lang,
                                   "Invalid filter. Use: Support, Fighter, Booster, or 'rank [S/A/B/C/D/E]'",
                                   "فلتر غير صالح. استخدم: Support أو Fighter أو Booster أو 'rank [S/A/B/C/D/E]'",
                                   "Filtre invalide. Utilisez : Support, Fighter, Booster, ou 'rank [S/A/B/C/D/E]'"))
                return

            cards_query = cards_query.format(filter_condition)
            cards = await execute_query_with_retry(cards_query, tuple(query_params) if query_params else None)

            if not cards:
                filter_desc = ''
                if filter_type == 'rank' and filter_value:
                    filter_desc = self._tr(lang, f"Rank {filter_value.upper()} ", f"رتبة {filter_value.upper()} ", f"Rang {filter_value.upper()} ")
                elif filter_type:
                    filter_desc = f"{filter_type} "
                await ctx.send(self._tr(lang, f"No {filter_desc}cards found.",
                                   f"لم يتم العثور على بطاقات {filter_desc}.",
                                   f"Aucune carte {filter_desc}trouvée."))
                return

            view = AllView(ctx, cards,lang)
            embed = view.create_embed()
            files = []
           
            # Rank thumb
            rank_image_path = view.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Card image
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
            await ctx.send(self._tr(lang,
                               "An error occurred while processing your request.",
                               "حدث خطأ أثناء معالجة طلبك.",
                               "Une erreur s'est produite lors du traitement de votre demande."))
           
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
            rarity_weights = {
                'S': 50,
                'A': 170,
                'B': 895,
                'C': 1085,
                'D': 3800,
                'E': 4000
            }
        elif premium_status == 3:
            rarity_weights = {
                'S': 100,
                'A': 200,
                'B': 1000,
                'C': 1100,
                'D': 3600,
                'E': 4000
            }
        else:
            rarity_weights = {
                'S': 10,
                'A': 100,
                'B': 640,
                'C': 750,
                'D': 4000,
                'E': 4500
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
                card_weights = []
                total_s_weight = 0
               
                for card in s_rank_cards:
                    weight = int(card['drop_luck'] * 1000)  # higher drop_luck = more common
                    card_weights.append((card, weight))
                    total_s_weight += weight
               
                s_rand = random.randint(1, total_s_weight)
                s_current_sum = 0
                drawn_card = None
                for card, weight in card_weights:
                    s_current_sum += weight
                    if s_rand <= s_current_sum:
                        drawn_card = card
                        break
                if drawn_card is None:
                    drawn_card = s_rank_cards[0]
               
                # Verify image exists
                image_path = self.get_image_path(drawn_card['image'])
                if image_path and os.path.exists(image_path):
                    return drawn_card
                else:
                    for card in s_rank_cards:
                        alt_image_path = self.get_image_path(card['image'])
                        if alt_image_path and os.path.exists(alt_image_path):
                            return card
       
        # For non-S ranks, use the original random selection
        possible_cards = [card for card in sorted_cards if card['cardrank_name'] == selected_rank]
        if possible_cards:
            drawn_card = random.choice(possible_cards)
            image_path = self.get_image_path(drawn_card['image'])
            if image_path and os.path.exists(image_path):
                return drawn_card
       
        return random.choice(sorted_cards) if sorted_cards else None

    async def get_guaranteed_rank_card(self, rank_id):
        # Special handling for guaranteed S-rank cards (rank_id = 2)
        if rank_id == 2:
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
                card_weights = []
                total_s_weight = 0
               
                for card in s_rank_cards:
                    weight = int(card['drop_luck'] * 1000)
                    card_weights.append((card, weight))
                    total_s_weight += weight
               
                s_rand = random.randint(1, total_s_weight)
                s_current_sum = 0
                drawn_card = None
                for card, weight in card_weights:
                    s_current_sum += weight
                    if s_rand <= s_current_sum:
                        drawn_card = card
                        break
                if drawn_card is None:
                    drawn_card = s_rank_cards[0]
               
                return drawn_card
       
        # For non-S rank guaranteed cards
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
            return drawn_card
        return None

    @commands.command(aliases=["ars", "ar"])
    async def arise(self, ctx):
        on_cooldown, time_left = self.check_cooldown(ctx.author.id, "duel")
        lang = await get_user_multilanguage(ctx.author.id)
        if on_cooldown:
            await ctx.send(_tr(lang,
                               f"⏰ Command on cooldown. Please wait {time_left} seconds.",
                               f"⏰ الأمر على فترات التهدئة. يرجى الانتظار {time_left} ثانية.",
                               f"⏰ Commande en recharge. Veuillez attendre {time_left} secondes."))
            return

        try:
            user_queried = """SELECT multi_session from user where user_authorid=%s"""
            user_resulted = await execute_query_with_retry(user_queried, (str(ctx.author.id),))
            if user_resulted and user_resulted[0]['multi_session'] == 1:
                await ctx.send(_tr(lang,
                                   "⚠️ You cannot use arise while multiarise is in progress. Please wait for it to complete.",
                                   "⚠️ لا يمكنك استخدام arise بينما multiarise قيد التقدم. يرجى الانتظار حتى ينتهي.",
                                   "⚠️ Vous ne pouvez pas utiliser arise pendant qu'un multiarise est en cours. Merci d'attendre la fin."))
                return

            # Get user info incl. premium
            user_query = """SELECT user_id, quantiter_fragment, fragment_cap,
                    roll_timer, roll_count, roll_cap, prenium, prenium_date, roll_total, joined_server,
                    first_joined, second_joined, third_joined
                    FROM user WHERE user_authorid = %s"""
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
               
            if not user_result:
                await ctx.send(_tr(lang,
                                   "Error: You need to register to the bot first.",
                                   "خطأ: يجب أن تسجل في البوت أولاً.",
                                   "Erreur : vous devez d'abord vous inscrire au bot."))
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
                    roll_cap_reduction = 0
                    if premium_status == 1:
                        roll_cap_reduction = 1
                    elif premium_status == 2:
                        roll_cap_reduction = 2
                    elif premium_status == 3:
                        roll_cap_reduction = 3
                   
                    await execute_query_with_retry(
                        "UPDATE user SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s WHERE user_authorid = %s",
                        (roll_cap_reduction, str(ctx.author.id))
                    )
                   
                    error_embed = discord.Embed(
                        title=_tr(lang, "⏰ Premium Subscription Expired", "⏰ انتهى اشتراك بريميوم", "⏰ Abonnement Premium expiré"),
                        description=_tr(lang,
                                        f"Your Premium {premium_status} subscription has expired after 30 days.\n\nYour roll cap has been reduced by {roll_cap_reduction}.",
                                        f"انتهى اشتراك بريميوم {premium_status} بعد 30 يوماً.\n\nتم تقليل حد الرميات بمقدار {roll_cap_reduction}.",
                                        f"Votre abonnement Premium {premium_status} a expiré après 30 jours.\n\nVotre limite de tirages a été réduite de {roll_cap_reduction}."),
                        color=discord.Color.orange()
                    )
                    error_embed.add_field(name=_tr(lang, "Subscription Date", "تاريخ الاشتراك", "Date d'abonnement"),
                                          value=premium_date.strftime("%Y-%m-%d"), inline=True)
                    error_embed.add_field(name=_tr(lang, "Days Elapsed", "الأيام المنقضية", "Jours écoulés"),
                                          value=_tr(lang, f"{days_since_premium} days", f"{days_since_premium} يومًا", f"{days_since_premium} jours"),
                                          inline=True)
                    await ctx.send(embed=error_embed)
                   
                    premium_status = 0
                else:
                    # Join bonuses
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
                    member = support_guild.get_member(ctx.author.id)
                    if not member:
                        try:
                            member = await support_guild.fetch_member(ctx.author.id)
                        except discord.NotFound:
                            member = None
                        except Exception as e:
                            print(f"Error fetching member: {e}")
                            member = ctx.guild.id == 832022150713704479 if ctx.guild else None
                    is_in_support_server = member is not None
                   
            except Exception as e:
                print(f"Error checking server membership: {e}")
                is_in_support_server = ctx.guild and ctx.guild.id == 832022150713704479

            # Update joined_server/roll_cap atomically-ish
            joined_server = user_result[0].get('joined_server', 0)
            if is_in_support_server and joined_server == 0:
                await execute_query_with_retry(
                    "UPDATE user SET roll_cap = roll_cap + 2, joined_server = 1 WHERE user_id = %s",
                    (user_id,)
                )
            elif not is_in_support_server and joined_server == 1:
                await execute_query_with_retry(
                    "UPDATE user SET roll_cap = roll_cap - 2, joined_server = 0 WHERE user_id = %s",
                    (user_id,)
                )

            # Fetch latest user after possible changes
            latest_user_query = """SELECT roll_timer, roll_count, roll_cap, roll_total, prenium
                                FROM user WHERE user_id = %s FOR UPDATE"""
            latest_user = await execute_query_with_retry(latest_user_query, (user_id,))
           
            if not latest_user:
                await ctx.send(_tr(lang,
                                   "Error retrieving your data. Please try again.",
                                   "خطأ في جلب بياناتك. يرجى المحاولة مرة أخرى.",
                                   "Erreur lors de la récupération de vos données. Veuillez réessayer."))
                return
               
            current_time = datetime.utcnow()
            roll_timer = latest_user[0]['roll_timer']
            roll_count = latest_user[0]['roll_count']
            roll_cap = latest_user[0]['roll_cap']
            roll_total = latest_user[0]['roll_total'] or 0
            premium_status = latest_user[0]['prenium']

            # Handle roll timing and count
            if roll_timer is None:
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
                        title=_tr(lang, "⏰ Roll Limit Reached", "⏰ تم بلوغ حد الرميات", "⏰ Limite de tirages atteinte"),
                        description=_tr(lang,
                                        "You've reached your daily roll limit!",
                                        "لقد بلغت الحد اليومي من الرميات!",
                                        "Vous avez atteint votre limite quotidienne de tirages!")
                                    + "\n\n"
                                    + _tr(lang, "Time until reset:", "الوقت حتى إعادة التعيين:", "Temps avant réinitialisation:")
                                    + f" **{hours}h {minutes}m {seconds}s**",
                        color=discord.Color.red()
                    )
                       
                    embed.add_field(
                        name=_tr(lang, "🗳️ Vote for Extra Rolls", "🗳️ صوّت للحصول على رميات إضافية", "🗳️ Votez pour des tirages bonus"),
                        value=_tr(lang,
                                  "Vote for the bot to get an extra reroll daily! Use `sl vote` to get your voting link.",
                                  "صوّت للبوت لتحصل على إعادة رمية إضافية يوميًا! استخدم `sl vote` للحصول على رابط التصويت.",
                                  "Votez pour le bot pour obtenir un reroll quotidien supplémentaire ! Utilisez `sl vote` pour obtenir votre lien de vote."),
                        inline=False
                    )
                   
                    embed.add_field(
                        name=_tr(lang, "🌟 Join Our Support Server", "🌟 انضم إلى سيرفر الدعم", "🌟 Rejoignez notre serveur support"),
                        value=_tr(lang,
                                  "Join our support server to get +2 arise rolls (e.g., 11 → 13)!",
                                  "انضم إلى سيرفر الدعم لتحصل على +2 رميات arise (مثال: 11 → 13)!",
                                  "Rejoignez notre serveur support pour obtenir +2 tirages arise (ex. : 11 → 13)!")
                              + "\n[Click here to join](https://discord.gg/rJHgZsVAK8)",
                        inline=False
                    )
                   
                    if ctx.author.avatar:
                        embed.set_thumbnail(url=ctx.author.avatar.url)
                   
                    embed.set_footer(text=_tr(lang,
                                              "Try again after the cooldown expires",
                                              "حاول مرة أخرى بعد انتهاء التهدئة",
                                              "Réessayez après la fin du délai de recharge"))
                    embed.timestamp = discord.utils.utcnow()
                   
                    await ctx.send(embed=embed)
                    return
               
                if hours_difference >= 4:
                    await execute_query_with_retry(
                        "UPDATE user SET roll_timer = %s, roll_count = 1 WHERE user_id = %s",
                        (current_time, user_id)
                    )
                    roll_count = 1
                else:
                    await execute_query_with_retry(
                        "UPDATE user SET roll_count = roll_count + 1 WHERE user_id = %s",
                        (user_id,)
                    )
                    roll_count += 1

            # Update roll_total
            new_roll_total = roll_total + 1
            await execute_query_with_retry(
                "UPDATE user SET roll_total = %s WHERE user_id = %s",
                (new_roll_total, user_id)
            )

            guaranteed_special = False
            drawn_card = None

            roll_thresholds = {
                0: (250, 3),   # Regular -> A rank
                1: (200, 3),   # Premium 1 -> A
                2: (150, 3),   # Premium 2 -> A
                3: (150, 2),   # Premium 3 -> S
            }

            threshold, rank = roll_thresholds.get(premium_status, (250, 3))
            if new_roll_total % threshold == 0 and new_roll_total > 0:
                drawn_card = await self.get_guaranteed_rank_card(rank)
                if drawn_card:
                    guaranteed_special = True

            if not drawn_card:
                drawn_card = await self.get_valid_card(premium_status)
            if not drawn_card:
                await ctx.send(_tr(lang,
                                   "Failed to get a valid card. Please try again.",
                                   "فشل في الحصول على بطاقة صالحة. يرجى المحاولة مرة أخرى.",
                                   "Impossible d'obtenir une carte valide. Veuillez réessayer."))
                return
           
            # Latest fragment info
            latest_fragment_query = "SELECT quantiter_fragment, fragment_cap FROM user WHERE user_id = %s"
            latest_fragment_result = await execute_query_with_retry(latest_fragment_query, (user_id,))
           
            if latest_fragment_result:
                current_fragments = latest_fragment_result[0]['quantiter_fragment']
                fragment_cap = latest_fragment_result[0]['fragment_cap']
            else:
                current_fragments = user_result[0]['quantiter_fragment']
                fragment_cap = user_result[0]['fragment_cap']

            # Does user already have the card?
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, drawn_card['card_id']))

            description = ""
            footer_text = ""

            if not user_card_result:
                # New card bonus hooks
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
                    INSERT INTO user_cards (card_rank, max_leveled,image,evo,current_cap_attack,current_cap_speed,current_cap_health,current_cap_defence,user_id, card_id, current_level, current_xp,
                    current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name,
                    defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                    weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                    power_emoji, type_emoji, class_emoji)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s)
                """, (  
                    drawn_card['card_rank'],50,drawn_card['image'],1,30000,10000,30000,5000,user_id, drawn_card['card_id'], drawn_card['min_level'], 0, 0,
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
                description = _tr(lang,
                                  "🎉 Congratulations! This card has been added to your collection!\n\n",
                                  "🎉 مبروك! تمت إضافة هذه البطاقة إلى مجموعتك!\n\n",
                                  "🎉 Félicitations ! Cette carte a été ajoutée à votre collection !\n\n")
            else:
                if current_fragments >= fragment_cap:
                    description = _tr(lang,
                                      "You have reached your stones limit. Delete some if you wish to collect more!\n\n",
                                      "لقد وصلت إلى حد الأحجار. احذف بعضها إذا رغبت في جمع المزيد!\n\n",
                                      "Vous avez atteint votre limite de pierres. Supprimez-en si vous souhaitez en collecter davantage !\n\n")
                    footer_text = _tr(lang,
                                      "Essence Stones limit reached!",
                                      "تم بلوغ حد أحجار الجوهر!",
                                      "Limite de Pierres d'Essence atteinte !")
                else:
                    # add fragment instead
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

                            description = _tr(lang,
                                              "You already have this card.\n\n",
                                              "لديك هذه البطاقة بالفعل.\n\n",
                                              "Vous avez déjà cette carte.\n\n")
                            footer_text = _tr(lang,
                                              f"You've received an Essence Stone instead! Total fragments: {total_fragments}/{fragment_cap}",
                                              f"لقد حصلت على حجر جوهر بدلاً من ذلك! إجمالي الشظايا: {total_fragments}/{fragment_cap}",
                                              f"Vous avez reçu une Pierre d'Essence à la place ! Fragments totaux : {total_fragments}/{fragment_cap}")

            # Build embed
            rank_colors = {
                2: discord.Color.from_rgb(255, 215, 0),  # S
                3: discord.Color.from_rgb(139, 0, 0),    # A
                4: discord.Color.from_rgb(255, 165, 0),  # B
                5: discord.Color.from_rgb(32, 131, 39),  # C
                6: discord.Color.from_rgb(0, 128, 128),  # D
                7: discord.Color.from_rgb(128, 128, 128) # E
            }
            embed_color = rank_colors.get(drawn_card['cardrank_id'], discord.Color.from_rgb(75, 0, 130))

            role = "Offence" if drawn_card['type_card'] == "Fighter" else drawn_card['type_card']
            role_loc = _tr(lang, role, "هجومية" if role == "Offence" else role, "Offensive" if role == "Offence" else role)

            description += (
                _tr(lang, "**Card Information**", "**معلومات البطاقة**", "**Informations de la carte**") + "\n"
                + f"<:solopower:1302936147139235882> " + _tr(lang, "Power", "القوة", "Puissance") + f": {drawn_card['power']}\n"
                + f"<:solotype:1302937991869759539> " + _tr(lang, "Class", "الفئة", "Classe") + f": {drawn_card['card_class_name']}\n"
                + f"<:solorole:1302938003873726526> " + _tr(lang, "Role", "الدور", "Rôle") + f": {role_loc}\n\n"
            )

            if drawn_card['type_card'].lower() in ['support', 'booster']:
                description += (
                    _tr(lang, "**Effect**", "**التأثير**", "**Effet**") + "\n"
                    + f"{drawn_card['description']}"
                )
            else:
                description += (
                    _tr(lang, "**Combat Statistics**", "**إحصائيات القتال**", "**Statistiques de combat**") + "\n"
                    + f"<:solosword:1302911919941746730> " + _tr(lang, "Attack", "الهجوم", "Attaque") + f": {drawn_card['attack']}\n"
                    + f"<:soloheart:1302912058819477514> " + _tr(lang, "Health", "الصحة", "Santé") + f": {drawn_card['health']}\n"
                    + f"<:solostar:1302912104264761374> " + _tr(lang, "Speed", "السرعة", "Vitesse") + f": {drawn_card['speed']}\n"
                    + f"<:soloshield:1302911930242961461> " + _tr(lang, "Defense", "الدفاع", "Défense") + f": {drawn_card['defence']}"
                )

            embed = discord.Embed(
                title=f"{drawn_card['card_name']}",
                description=description,
                color=embed_color
            )

            files = []
           
            # Rank thumb
            rank_image_path = self.get_image_path(drawn_card['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Card image
            card_image_path = self.get_image_path(drawn_card['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")
               
            if guaranteed_special:
                footer_text = _tr(lang,
                                  "🌟 Special guaranteed card obtained! Roll counter reset!",
                                  "🌟 تم الحصول على بطاقة مضمونة خاصة! تمت إعادة تعيين عداد الرميات!",
                                  "🌟 Carte garantie spéciale obtenue ! Compteur de tirages réinitialisé !")
            else:
                if not footer_text:
                    sl_quotes = {
                        0: [
                            "The true hunter never rests.",
                            "Your strength defines your fate.",
                            "Another card, another step to greatness.",
                            "The shadows whisper, will you answer?"
                        ],
                        1: [
                            "الصياد الحقيقي لا يرتاح.",
                            "قوتك تحدد مصيرك.",
                            "بطاقة أخرى، خطوة أخرى نحو العظمة.",
                            "الظلال تهمس… هل ستجيب؟"
                        ],
                        3: [
                            "Le véritable chasseur ne se repose jamais.",
                            "Ta force décide de ton destin.",
                            "Une autre carte, un pas de plus vers la grandeur.",
                            "Les ombres murmurent… répondrez-vous ?"
                        ]
                    }
                    footer_text = random.choice(sl_quotes.get(lang, sl_quotes[0]))

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

                premium_indicator = ""
                if premium_status > 0:
                    premium_tiers = {1: "🥉", 2: "🥈", 3: "🥇"}
                    premium_indicator = f" {premium_tiers.get(premium_status, '')} "

                footer_text += (f"{premium_indicator}| "
                                + _tr(lang, "Rolls", "الرميات", "Tirages")
                                + f": {latest_roll_count}/{latest_roll_cap} | "
                                + _tr(lang, "Total Rolls", "إجمالي الرميات", "Total tirages")
                                + f": {roll_total_display}")

                # Remaining rolls hint
                if premium_status == 0:
                    remaining = 250 - roll_total_display
                    if remaining > 0:
                        footer_text += " | " + _tr(lang, f"A-Rank in {remaining} rolls",
                                                   f"رتبة A خلال {remaining} رمية",
                                                   f"Rang A dans {remaining} tirages")
                elif premium_status == 1:
                    remaining = 200 - roll_total_display
                    if remaining > 0:
                        footer_text += " | " + _tr(lang, f"A-Rank in {remaining} rolls",
                                                   f"رتبة A خلال {remaining} رمية",
                                                   f"Rang A dans {remaining} tirages")
                elif premium_status == 2:
                    remaining = 150 - roll_total_display
                    if remaining > 0:
                        footer_text += " | " + _tr(lang, f"A-Rank in {remaining} rolls",
                                                   f"رتبة A خلال {remaining} رمية",
                                                   f"Rang A dans {remaining} tirages")
                elif premium_status == 3:
                    remaining = 150 - roll_total_display
                    if remaining > 0:
                        footer_text += " | " + _tr(lang, f"S-Rank in {remaining} rolls",
                                                   f"رتبة S خلال {remaining} رمية",
                                                   f"Rang S dans {remaining} tirages")

            if ctx.author.avatar:
                embed.set_footer(text=footer_text, icon_url=ctx.author.avatar.url)
            else:
                embed.set_footer(text=footer_text)
            embed.timestamp = discord.utils.utcnow()
           
            await self.update_quest_progress(user_id)
            await self.check_and_update_referral_status(user_id)
           
            if files:
                await ctx.send(embed=embed, files=files)
            else:
                await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error in arise command: {e}")
            await ctx.send(_tr(lang,
                               "An error occurred while processing your request. Please try again.",
                               "حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى.",
                               "Une erreur s'est produite lors du traitement de votre demande. Veuillez réessayer."))


    @commands.command(aliases=["mp", "mpa"])
    async def multiarise(self, ctx):
        """Pull multiple cards at once up to roll cap - Premium 2+ only"""
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            
            session_check_query = "SELECT multi_session FROM user WHERE user_authorid = %s"
            session_result = await execute_query_with_retry(session_check_query, (str(ctx.author.id),))
            
            if session_result and session_result[0]['multi_session'] == 1:
                await ctx.send(_tr(lang,
                                   "You're already in a multiarise session. Please wait for it to complete.",
                                   "أنت بالفعل في جلسة سحب متعددة. يرجى الانتظار حتى تنتهي.",
                                   "Vous êtes déjà dans une session multiarise. Veuillez attendre sa fin."))
                return
               
            # STEP 1: Quick initial check for premium status only
            quick_check_query = """SELECT prenium, prenium_date, first_joined, second_joined, third_joined
                                FROM user WHERE user_authorid = %s"""
            quick_result = await execute_query_with_retry(quick_check_query, (str(ctx.author.id),))
           
            if not quick_result:
                await ctx.send(_tr(lang,
                                   "❌ You need to register to the bot first.",
                                   "❌ يجب أن تسجّل في البوت أولاً.",
                                   "❌ Vous devez d'abord vous inscrire au bot."))
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
                        title=_tr(lang, "⏰ Premium Subscription Expired", "⏰ انتهى اشتراك بريميوم", "⏰ Abonnement Premium expiré"),
                        description=_tr(lang,
                                        f"Your Premium {premium_status} subscription has expired after 30 days.\n\nYour roll cap has been reduced by {roll_cap_reduction}.\n\nPlease renew your subscription to continue using Multi Arise.",
                                        f"انتهى اشتراك بريميوم {premium_status} بعد 30 يوماً.\n\nتم تقليل حد الرميات بمقدار {roll_cap_reduction}.\n\nيرجى تجديد اشتراكك لمتابعة استخدام ميزة السحب المتعدد.",
                                        f"Votre abonnement Premium {premium_status} a expiré après 30 jours.\n\nVotre limite de tirages a été réduite de {roll_cap_reduction}.\n\nVeuillez le renouveler pour continuer à utiliser Multi Arise."),
                        color=discord.Color.orange()
                    )
                    error_embed.add_field(
                        name=_tr(lang, "Subscription Date", "تاريخ الاشتراك", "Date d'abonnement"),
                        value=premium_date.strftime("%Y-%m-%d"),
                        inline=True
                    )
                    error_embed.add_field(
                        name=_tr(lang, "Days Elapsed", "الأيام المنقضية", "Jours écoulés"),
                        value=_tr(lang, f"{days_since_premium} days", f"{days_since_premium} يومًا", f"{days_since_premium} jours"),
                        inline=True
                    )
                    await ctx.send(embed=error_embed)
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
                    title=_tr(lang, "🔒 Premium Feature", "🔒 ميزة بريميوم", "🔒 Fonction Premium"),
                    description=_tr(lang,
                                    "Multi Arise is a premium feature available only to **Premium 2** and **Premium 3** subscribers.\n\nUpgrade your subscription to access this feature!",
                                    "السحب المتعدد ميزة بريميوم متاحة فقط لمشتركي **بريميوم 2** و **بريميوم 3**.\n\nقم بالترقية للوصول إلى هذه الميزة!",
                                    "Multi Arise est une fonctionnalité premium réservée aux abonnés **Premium 2** et **Premium 3**.\n\nPassez à l’offre supérieure pour y accéder !"),
                    color=discord.Color.red()
                )
                error_embed.add_field(
                    name=_tr(lang, "Current Status", "الحالة الحالية", "Statut actuel"),
                    value=_tr(lang, f"Premium {premium_status}" if premium_status > 0 else "Free User",
                              f"بريميوم {premium_status}" if premium_status > 0 else "مستخدم مجاني",
                              f"Premium {premium_status}" if premium_status > 0 else "Utilisateur gratuit"),
                    inline=True
                )
                error_embed.add_field(
                    name=_tr(lang, "Required", "المطلوب", "Requis"),
                    value=_tr(lang, "Premium 2 or Premium 3", "بريميوم 2 أو بريميوم 3", "Premium 2 ou Premium 3"),
                    inline=True
                )
                await ctx.send(embed=error_embed)
                return
           
            await execute_query_with_retry(
                "UPDATE user SET multi_session = 1 WHERE user_authorid = %s",
                (str(ctx.author.id),)
            )
           
            # STEP 2: SHOW LOADING GIF IMMEDIATELY after premium check
            loading_embed = discord.Embed(
                title=_tr(lang, "🎴 Multi Arise Starting...", "🎴 بدء السحب المتعدد...", "🎴 Démarrage de Multi Arise..."),
                description=_tr(lang, "⏳ Initializing your card pull session...", "⏳ جارٍ تهيئة جلسة السحب...", "⏳ Initialisation de votre session de tirages..."),
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
                        title=_tr(lang, "❌ Error", "❌ خطأ", "❌ Erreur"),
                        description=_tr(lang, "Error retrieving your data. Please try again.",
                                        "حدث خطأ أثناء جلب بياناتك. يرجى المحاولة مرة أخرى.",
                                        "Erreur lors de la récupération de vos données. Veuillez réessayer."),
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
               
                # Calculate how many cards can be pulled
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
               
                # No rolls available
                if available_rolls == 0:
                    if roll_timer:
                        time_difference = current_time - roll_timer
                        remaining_seconds = (4 * 3600) - time_difference.total_seconds()
                        hours = int(remaining_seconds // 3600)
                        minutes = int((remaining_seconds % 3600) // 60)
                        seconds = int(remaining_seconds % 60)
                       
                        error_embed = discord.Embed(
                            title=_tr(lang, "⏰ Roll Limit Reached", "⏰ تم بلوغ حد الرميات", "⏰ Limite de tirages atteinte"),
                            description=_tr(lang,
                                            "You've reached your daily roll limit!",
                                            "لقد بلغت الحد اليومي من الرميات!",
                                            "Vous avez atteint votre limite quotidienne de tirages!")
                                        + "\n\n"
                                        + _tr(lang, "Time until reset:", "الوقت حتى إعادة التعيين:", "Temps avant réinitialisation:")
                                        + f" **{hours}h {minutes}m {seconds}s**",
                            color=discord.Color.red()
                        )
                        await message.edit(embed=error_embed, attachments=[])
                        return
               
                # Guaranteed thresholds
                if premium_status == 0:
                    threshold = 250; guaranteed_rank = 3; guaranteed_card_text = _tr(lang, "A rank card", "بطاقة رتبة A", "Carte de rang A")
                elif premium_status == 1:
                    threshold = 200; guaranteed_rank = 3; guaranteed_card_text = _tr(lang, "A rank card", "بطاقة رتبة A", "Carte de rang A")
                elif premium_status == 2:
                    threshold = 150; guaranteed_rank = 3; guaranteed_card_text = _tr(lang, "A rank card", "بطاقة رتبة A", "Carte de rang A")
                else:
                    threshold = 150; guaranteed_rank = 2; guaranteed_card_text = _tr(lang, "S rank card", "بطاقة رتبة S", "Carte de rang S")
               
                # Update loading message
                pulls_until_guaranteed = threshold - roll_total
                loading_embed.title = _tr(lang, "🎴 Multi Arise in Progress...", "🎴 السحب المتعدد قيد التقدم...", "🎴 Multi Arise en cours...")
                loading_embed.description = _tr(lang, f"⏳ Pulling {cards_to_pull} cards for you!",
                                               f"⏳ جارٍ سحب {cards_to_pull} بطاقة لك!",
                                               f"⏳ Tirage de {cards_to_pull} cartes pour vous !")
                loading_embed.add_field(
                    name=_tr(lang, "🎯 Guaranteed Card Progress", "🎯 تقدّم البطاقة المضمونة", "🎯 Progression de la carte garantie"),
                    value=_tr(lang,
                              f"Pulls until guaranteed {guaranteed_card_text}: **{pulls_until_guaranteed}/{threshold}**",
                              f"عدد السحبات حتى {guaranteed_card_text} المضمونة: **{pulls_until_guaranteed}/{threshold}**",
                              f"Tirages avant {guaranteed_card_text} garantie : **{pulls_until_guaranteed}/{threshold}**"),
                    inline=False
                )
                try:
                    await message.edit(embed=loading_embed)
                except:
                    pass
               
                # Pull cards
                pulled_cards = []
                essence_stones_info = {}
                new_cards_count = 0
                essence_stones_capped = False
                essence_stones_message = ""
                fragment_updates = {}
                guaranteed_card_found = False
                fragments_given = 0
               
                # Cap pre-check
                if current_fragments >= fragment_cap:
                    essence_stones_capped = True
                    essence_stones_message = _tr(lang,
                                                 "Essence stones limit already reached!",
                                                 "تم بلوغ حد أحجار الجوهر بالفعل!",
                                                 "Limite de Pierres d'Essence déjà atteinte !")
               
                # Cache user's existing cards
                existing_cards_query = "SELECT card_id FROM user_cards WHERE user_id = %s"
                existing_cards_result = await execute_query_with_retry(existing_cards_query, (user_id,))
                existing_card_ids = {row['card_id'] for row in existing_cards_result} if existing_cards_result else set()
               
                # Track current roll total for guarantees
                current_roll_total = roll_total
               
                for i in range(cards_to_pull):
                    # Occasional progress updates
                    if i == 0 or i % 5 == 0 or i == cards_to_pull - 1:
                        progress_embed = discord.Embed(
                            title=_tr(lang, "🎴 Multi Arise in Progress...", "🎴 السحب المتعدد قيد التقدم...", "🎴 Multi Arise en cours..."),
                            description=_tr(lang,
                                            f"⏳ Pulling card {i + 1}/{cards_to_pull}...",
                                            f"⏳ يتم سحب البطاقة {i + 1}/{cards_to_pull}...",
                                            f"⏳ Tirage de la carte {i + 1}/{cards_to_pull}..."),
                            color=discord.Color.blue()
                        )
                        if loading_gif_path and os.path.exists(loading_gif_path):
                            progress_embed.set_image(url="attachment://loading.gif")
                        pulls_until_guaranteed = max(0, threshold - current_roll_total)
                        progress_embed.add_field(
                            name=_tr(lang, "🎯 Guaranteed Card Progress", "🎯 تقدّم البطاقة المضمونة", "🎯 Progression de la carte garantie"),
                            value=_tr(lang,
                                      f"Pulls until guaranteed {guaranteed_card_text}: **{pulls_until_guaranteed}/{threshold}**",
                                      f"عدد السحبات حتى {guaranteed_card_text} المضمونة: **{pulls_until_guaranteed}/{threshold}**",
                                      f"Tirages avant {guaranteed_card_text} garantie : **{pulls_until_guaranteed}/{threshold}**"),
                            inline=False
                        )
                        try:
                            await message.edit(embed=progress_embed)
                        except:
                            pass
                   
                    guaranteed_special = False
                    drawn_card = None
                    current_roll_total += 1

                    if current_roll_total % threshold == 0 and current_roll_total > 0:
                        drawn_card = await self.get_guaranteed_rank_card(guaranteed_rank)
                        if drawn_card:
                            guaranteed_special = True
                            guaranteed_card_found = True

                    if not drawn_card:
                        drawn_card = await self.get_valid_card(premium_status)

                    if drawn_card:
                        pulled_cards.append(drawn_card)
                       
                        if guaranteed_special:
                            drawn_card['guaranteed'] = True
                            # (Optional) If you actually have this table/column name, otherwise remove:
                            try:
                                card_rank_query = "SELECT cardrank_name FROM cardrank WHERE cardrank_id = %s"
                                card_rank_result = await execute_query_with_retry(card_rank_query, (drawn_card['card_rank'],))
                                if card_rank_result:
                                    drawn_card['card_rank_name'] = card_rank_result[0]['cardrank_name']
                            except:
                                pass
                       
                        # New card or duplicate?
                        if drawn_card['card_id'] not in existing_card_ids:
                            new_cards_count += 1
                            existing_card_ids.add(drawn_card['card_id'])
                           
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
                                fragment_cap += 100

                            await execute_query_with_retry("""
                                INSERT INTO user_cards (card_rank, max_leveled,image,evo,current_cap_attack,current_cap_speed,current_cap_health,current_cap_defence,user_id, card_id, current_level, current_xp,
                                current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name,
                                defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                                weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                                power_emoji, type_emoji, class_emoji)  
                                VALUES (%s, %s, %s,%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                drawn_card['card_rank'],50,drawn_card['image'],1,30000,10000,30000,5000,user_id, drawn_card['card_id'], drawn_card['min_level'], 0, 0,
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
                            # Duplicate → essence stones (respect cap)
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
                                   
                                    if current_fragments + fragments_given < fragment_cap:
                                        if fragment_name not in essence_stones_info:
                                            essence_stones_info[fragment_name] = {
                                                'quantity': 0,
                                                'icon': fragment_icon,
                                                'id': fragment_id
                                            }
                                        essence_stones_info[fragment_name]['quantity'] += 1
                                        fragments_given += 1
                                       
                                        if fragment_id not in fragment_updates:
                                            fragment_updates[fragment_id] = 0
                                        fragment_updates[fragment_id] += 1
                                       
                                        if current_fragments + fragments_given >= fragment_cap:
                                            essence_stones_capped = True
                                            essence_stones_message = _tr(lang,
                                                                         f"Essence stones limit reached at {fragment_cap}! No more fragments given for remaining duplicates.",
                                                                         f"تم بلوغ حد أحجار الجوهر {fragment_cap}! لن تُمنح شظايا إضافية للبطاقات المكررة المتبقية.",
                                                                         f"Limite de Pierres d'Essence atteinte à {fragment_cap} ! Plus de fragments pour les doublons restants.")
                                    else:
                                        if not essence_stones_capped:
                                            essence_stones_capped = True
                                            essence_stones_message = _tr(lang,
                                                                         f"Essence stones limit reached at {fragment_cap}! No more fragments given for remaining duplicates.",
                                                                         f"تم بلوغ حد أحجار الجوهر {fragment_cap}! لن تُمنح شظايا إضافية للبطاقات المكررة المتبقية.",
                                                                         f"Limite de Pierres d'Essence atteinte à {fragment_cap} ! Plus de fragments pour les doublons restants.")
               
                # STEP 5: Batch update fragments
                if fragment_updates and inventory_id:
                    for fragment_id, quantity in fragment_updates.items():
                        check_query = "SELECT quantiter FROM inventory_frags WHERE inventory_id = %s AND frag_id = %s"
                        check_result = await execute_query_with_retry(check_query, (inventory_id, fragment_id))
                        if check_result:
                            await execute_query_with_retry(
                                "UPDATE inventory_frags SET quantiter = quantiter + %s WHERE inventory_id = %s AND frag_id = %s",
                                (quantity, inventory_id, fragment_id)
                            )
                        else:
                            await execute_query_with_retry(
                                "INSERT INTO inventory_frags (inventory_id, frag_id, quantiter) VALUES (%s, %s, %s)",
                                (inventory_id, fragment_id, quantity)
                            )
               
                # Final processing message
                final_loading_embed = discord.Embed(
                    title=_tr(lang, "🎴 Multi Arise in Progress...", "🎴 السحب المتعدد قيد التقدم...", "🎴 Multi Arise en cours..."),
                    description=_tr(lang, f"✅ Pulled {cards_to_pull} cards!\n⏳ Creating results display...",
                                    f"✅ تم سحب {cards_to_pull} بطاقة!\n⏳ جارٍ إنشاء عرض النتائج...",
                                    f"✅ {cards_to_pull} cartes tirées !\n⏳ Création de l'affichage des résultats..."),
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

                # Update fragment count safely
                if fragment_updates:
                    new_fragment_total = current_fragments + fragments_given
                    new_fragment_total = max(0, min(new_fragment_total, fragment_cap))
                    await execute_query_with_retry(
                        "UPDATE user SET quantiter_fragment = %s WHERE user_id = %s",
                        (new_fragment_total, user_id)
                    )
                    current_fragments = new_fragment_total

                # Update quest progress
                for _ in range(cards_to_pull):
                    await self.update_quest_progress(user_id)
                    await self.check_and_update_referral_status(user_id)

                # Build results embed
                results_embed = discord.Embed(
                    title=_tr(lang,
                              f"🎴 Multi Arise Results - {cards_to_pull} Cards Pulled!",
                              f"🎴 نتائج السحب المتعدد - تم سحب {cards_to_pull} بطاقة!",
                              f"🎴 Résultats Multi Arise - {cards_to_pull} cartes tirées !"),
                    color=discord.Color.green()
                )
               
                # Guaranteed card info
                if guaranteed_card_found:
                    for card in pulled_cards:
                        if card.get('guaranteed'):
                            card_name = card['card_name']
                            if premium_status in (0, 1, 2):
                                results_embed.add_field(
                                    name=_tr(lang, "🎯 Guaranteed Card Found!", "🎯 تم العثور على بطاقة مضمونة!", "🎯 Carte garantie trouvée !"),
                                    value=_tr(lang,
                                              f"You pulled your guaranteed A rank card: **{card_name}**!",
                                              f"لقد سحبت بطاقتك المضمونة من رتبة A: **{card_name}**!",
                                              f"Vous avez tiré votre carte garantie de rang A : **{card_name}** !"),
                                    inline=False
                                )
                            else:
                                results_embed.add_field(
                                    name=_tr(lang, "🎯 Guaranteed Card Found!", "🎯 تم العثور على بطاقة مضمونة!", "🎯 Carte garantie trouvée !"),
                                    value=_tr(lang,
                                              f"You pulled your guaranteed S rank card: **{card_name}**!",
                                              f"لقد سحبت بطاقتك المضمونة من رتبة S: **{card_name}**!",
                                              f"Vous avez tiré votre carte garantie de rang S : **{card_name}** !"),
                                    inline=False
                                )
                            break
               
                # Essence stones section
                if essence_stones_info:
                    essence_text = ""
                    for fragment_name, info in essence_stones_info.items():
                        quantity = info['quantity']
                        icon = info['icon'] if info['icon'] else '<:fragment:1234567890>'
                        essence_text += _tr(lang,
                                            f"x{quantity} {icon} {fragment_name} Essence Stones\n",
                                            f"x{quantity} {icon} أحجار جوهر {fragment_name}\n",
                                            f"x{quantity} {icon} Pierres d'Essence {fragment_name}\n")
                    if essence_text:
                        results_embed.add_field(
                            name=_tr(lang, "🔮 Essence Stones Received", "🔮 الأحجار الجوهرية المستلمة", "🔮 Pierres d'Essence reçues"),
                            value=essence_text,
                            inline=False
                        )
                        results_embed.add_field(
                            name=_tr(lang, "Fragment Count", "عدد الشظايا", "Compte des fragments"),
                            value=f"{current_fragments}/{fragment_cap}",
                            inline=True
                        )
               
                # Cap notice
                if essence_stones_capped and essence_stones_message:
                    results_embed.add_field(
                        name=_tr(lang, "⚠️ Fragment Cap Notice", "⚠️ إشعار حد الشظايا", "⚠️ Avis de limite de fragments"),
                        value=essence_stones_message,
                        inline=False
                    )
                elif current_fragments >= fragment_cap and not essence_stones_info:
                    results_embed.add_field(
                        name=_tr(lang, "⚠️ Fragment Cap Notice", "⚠️ إشعار حد الشظايا", "⚠️ Avis de limite de fragments"),
                        value=_tr(lang,
                                  "Essence stones limit reached! No fragments given for duplicate cards.",
                                  "تم بلوغ حد أحجار الجوهر! لن تُمنح شظايا للبطاقات المكررة.",
                                  "Limite de Pierres d'Essence atteinte ! Aucun fragment donné pour les doublons."),
                        inline=False
                    )
               
                # New cards count
                if new_cards_count > 0:
                    results_embed.add_field(
                        name=_tr(lang, "✨ New Cards", "✨ بطاقات جديدة", "✨ Nouvelles cartes"),
                        value=_tr(lang,
                                  f"{new_cards_count} new cards added to your collection!",
                                  f"تمت إضافة {new_cards_count} بطاقة جديدة إلى مجموعتك!",
                                  f"{new_cards_count} nouvelles cartes ajoutées à votre collection !"),
                        inline=False
                    )
               
                # Grid image (if your MultiAriseView supports it)
                view = MultiAriseView(ctx, pulled_cards, essence_stones_info)
                grid_image_bytes = view.create_card_grid_image()
               
                files = []
                if grid_image_bytes:
                    grid_file = discord.File(grid_image_bytes, filename="multiarise_results.png")
                    files.append(grid_file)
                    results_embed.set_image(url="attachment://multiarise_results.png")
               
                # Footer with roll info and guaranteed progress
                premium_indicator = ""
                if premium_status > 0:
                    premium_tiers = {1: "🥉", 2: "🥈", 3: "🥇"}
                    premium_indicator = f" {premium_tiers.get(premium_status, '')} "
               
                roll_total_display = current_roll_total
                footer_text = (f"{premium_indicator}"
                               + _tr(lang, "Rolls", "الرميات", "Tirages")
                               + f": {new_roll_count}/{roll_cap} | "
                               + _tr(lang, "Total Rolls", "إجمالي الرميات", "Total tirages")
                               + f": {roll_total_display}")
               
                if premium_status == 0:
                    remaining_rolls = 250 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += " | " + _tr(lang, f"A-Rank in {remaining_rolls} rolls",
                                                   f"رتبة A خلال {remaining_rolls} رمية",
                                                   f"Rang A dans {remaining_rolls} tirages")
                elif premium_status == 1:
                    remaining_rolls = 200 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += " | " + _tr(lang, f"A-Rank in {remaining_rolls} rolls",
                                                   f"رتبة A خلال {remaining_rolls} رمية",
                                                   f"Rang A dans {remaining_rolls} tirages")
                elif premium_status == 2:
                    remaining_rolls = 150 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += " | " + _tr(lang, f"A-Rank in {remaining_rolls} rolls",
                                                   f"رتبة A خلال {remaining_rolls} رمية",
                                                   f"Rang A dans {remaining_rolls} tirages")
                elif premium_status == 3:
                    remaining_rolls = 150 - roll_total_display
                    if remaining_rolls > 0:
                        footer_text += " | " + _tr(lang, f"S-Rank in {remaining_rolls} rolls",
                                                   f"رتبة S خلال {remaining_rolls} رمية",
                                                   f"Rang S dans {remaining_rolls} tirages")

                results_embed.set_footer(text=footer_text)
                results_embed.timestamp = discord.utils.utcnow()
               
                # Edit message with final results
                if files:
                    await message.edit(embed=results_embed, attachments=files)
                else:
                    await message.edit(embed=results_embed)
                   
            finally:
                await execute_query_with_retry(
                    "UPDATE user SET multi_session = 0 WHERE user_authorid = %s",
                    (str(ctx.author.id),)
                )
               
        except Exception as e:
            print(f"Error in multiarise command: {e}")
            try:
                # Get user_id from user_authorid
                user_id_query = "SELECT user_id FROM user WHERE user_authorid = %s"
                user_id_result = await execute_query_with_retry(user_id_query, (str(ctx.author.id),))
                
                if user_id_result:
                    error_user_id = user_id_result[0]['user_id']
                    
                    # Reset multi_session to 0
                    await execute_query_with_retry(
                        "UPDATE user SET multi_session = 0 WHERE user_id = %s",
                        (error_user_id,)
                    )
                    
                    # Get inventory_id
                    inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
                    inventory_result = await execute_query_with_retry(inventory_query, (error_user_id,))
                    
                    if inventory_result:
                        error_inventory_id = inventory_result[0]['inventory_id']
                        
                        # Check if product_id=2 exists in inventory_product
                        product_check_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = 2"
                        product_result = await execute_query_with_retry(product_check_query, (error_inventory_id,))
                        
                        if product_result:
                            # Update existing row
                            await execute_query_with_retry(
                                "UPDATE inventory_product SET quantiter = quantiter + 1 WHERE inventory_id = %s AND product_id = 2",
                                (error_inventory_id,)
                            )
                        else:
                            # Insert new row
                            await execute_query_with_retry(
                                "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, 2, 1)",
                                (error_inventory_id,)
                            )
            except Exception as compensation_error:
                print(f"Error during compensation logic: {compensation_error}")
            
            await ctx.send(_tr(lang,
                               "An error occurred while processing your multiarise request. Please try again.",
                               "حدث خطأ أثناء معالجة طلب السحب المتعدد. يرجى المحاولة مرة أخرى.",
                               "Une erreur s'est produite lors du traitement de votre demande multiarise. Veuillez réessayer."))


    @commands.command(name="mpreset")
    async def multiarise_reset(self, ctx, user_id: int = None):
        """Reset a user's multiarise session (Owner only)"""
        if ctx.author.id != 818132161534230559:
            await ctx.send("You don't have permission to use this command.")
            return

        target_user_id = user_id if user_id else ctx.author.id

        if target_user_id in self.multiarise_sessions:
            # Remove from in-memory session
            self.multiarise_sessions.discard(target_user_id)

            # Update the database to reset multi_session
            update_query = "UPDATE user SET multi_session = 0 WHERE user_authorid = %s"
            await execute_query_with_retry(update_query, (str(target_user_id),))

            await ctx.send(f"✅ Multiarise session reset for user {target_user_id}")
        else:
            # Update the database to reset multi_session
            target_user_id = user_id if user_id else ctx.author.id
            update_query = "UPDATE user SET multi_session = 0 WHERE user_authorid = %s"
            await execute_query_with_retry(update_query, (str(target_user_id),))
            await ctx.send(f"❌ User {target_user_id} is not in a multiarise session BUT the multi session was reset regardless.")

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
                c.card_name, c.xp_cap, uc.max_leveled, c.type_card, c.description,
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
                INSERT INTO user_cards (current_cap_attack,current_cap_speed,current_cap_health,current_cap_defence,user_id, card_id, current_level, current_xp,
                current_mana, power_stat, base_attack, base_health, base_speed, base_defence, attack_stat, health_stat, speed_stat, name,
                defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                power_emoji, type_emoji, class_emoji)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
            """, (
                30000,10000,30000,5000,user_id, card['card_id'], card['min_level'], 0, 0,
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
