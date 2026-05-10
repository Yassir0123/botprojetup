import discord
import os
from discord.ext import commands
import mysql.connector
from mysql.connector import Error
import random
from utils.database import create_db_connection
import datetime
import aiomysql
import time
import urllib
from discord.ui import Button, View, Select
import asyncio

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
    def __init__(self, ctx, card, original_view):
        super().__init__(timeout=None)  # Set timeout to None
        self.ctx = ctx
        self.card = card
        self.original_view = original_view
        self.stat_to_adjust = None
        self.adjustments = {'attack': 0, 'defence': 0, 'speed': 0, 'health': 0}
        self.original_statpoints = card['current_statpoints']
        self.embed_color = self.get_embed_color(card.get('cardrank_id'))
        self.message = None
        self.files = []
        self.initialize_files()
        
        # Initialize select menu with persistent custom_id
        self.add_item(StatSelect())


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
            description = (
                f"Adjusting {self.stat_to_adjust.capitalize() if self.stat_to_adjust else 'No stat selected'}\n\n"
                f"**Card Informations**\n"
                f"{self.card['power_emoji']} Power : {self.card['power_stat']}\n"
                f"{self.card['type_emoji']} Class : {self.card['card_class_name']}\n"
                f"{self.card['class_emoji']} Role : {self.card['type_card']}\n"
                f"{self.card['level_emoji']} Level : {self.card['current_level']}/{self.card['max_level']}\n"
                f"{self.card['xp_emoji']} XP : {self.card['current_xp']}/{self.card['xp_cap']}\n"
                f"{self.card['weapon_emoji']} Weapon : {self.card['weapon_name'] or 'None'}\n\n"
                f"**Combat Statistics**\n"
            )

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
            await interaction.response.edit_message(view=self.original_view)
            self.stop()
        except Exception as e:
            print(f"Error in go back button: {e}")
            await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.blurple)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            confirm_view = ConfirmView()
            await interaction.response.send_message("Are you sure you want to apply these stat changes?", view=confirm_view, ephemeral=True)
            
            await confirm_view.wait()
            
            if confirm_view.value is None:
                await interaction.followup.send("Confirmation timed out. Changes not applied.", ephemeral=True)
            elif confirm_view.value:
                await self.update_database()
                await interaction.followup.send("Stats updated successfully!", ephemeral=True)
                self.stop()
            else:
                await interaction.followup.send("Changes cancelled.", ephemeral=True)
        except Exception as e:
            print(f"Error in confirm button: {e}")
            await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)

    async def update_view(self, interaction):
        try:
            embed = self.create_embed()
            if interaction.response.is_done():
                await interaction.edit_original_message(embed=embed, view=self)
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
            conn = await aiomysql.connect(
                host='localhost',
                user='root',
                password='',
                db='sololevelingboting',
                autocommit=True
            )
            cursor = await conn.cursor()

            try:
                stats_used = sum(self.adjustments.values())
                update_query = """
                UPDATE user_cards
                SET attack_stat = attack_stat + %s,
                    defence_stat = defence_stat + %s,
                    speed_stat = speed_stat + %s,
                    health_stat = health_stat + %s,
                    current_statpoints = %s,
                    used_statpoints = used_statpoints + %s,
                    used_attackpoints = used_attackpoints + %s,
                    used_defencepoints = used_defencepoints + %s,
                    used_speedpoints = used_speedpoints + %s,
                    used_healthpoints = used_healthpoints + %s
                WHERE user_id = %s AND card_id = %s
                """
                await cursor.execute(update_query, (
                    self.adjustments['attack'],
                    self.adjustments['defence'],
                    self.adjustments['speed'],
                    self.adjustments['health'],
                    self.card['current_statpoints'],
                    stats_used,
                    self.adjustments['attack'],
                    self.adjustments['defence'],
                    self.adjustments['speed'],
                    self.adjustments['health'],
                    self.card['user_id'],
                    self.card['card_id']
                ))
            finally:
                await cursor.close()
                conn.close()
        except Exception as e:
            print(f"Error updating database: {e}")
            raise

    async def on_timeout(self):
        try:
            if self.message:
                await self.message.edit(view=None)
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
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.defer()

class CardView(discord.ui.View):
    def __init__(self, ctx, cards):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cards = cards
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
        stat_view = StatAdjustView(self.ctx, self.cards[self.current_page], self)
        await interaction.response.edit_message(view=stat_view)

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
            3: discord.Color.from_rgb(120, 0, 164),    # A Rank - Red
            4: discord.Color.from_rgb(0, 0, 255),  # B Rank - Orange
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Yellow
            6: discord.Color.from_rgb(154, 12, 6),    # D Rank - Green
            7: discord.Color.from_rgb(128, 128, 128)   # E Rank - Blue
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
            3: discord.Color.from_rgb(120, 0, 164),    # A Rank - Red
            4: discord.Color.from_rgb(0, 0, 255),  # B Rank - Orange
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Yellow
            6: discord.Color.from_rgb(154, 12, 6),    # D Rank - Green
            7: discord.Color.from_rgb(128, 128, 128)   # E Rank - Blue
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
        self.db_pool = None
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    async def create_db_pool(self):
        try:
            self.db_pool = await aiomysql.create_pool(
                host='localhost',
                user='root',
                password='',
                db='sololevelingboting',
                autocommit=True
            )
        except Exception as e:
            print(f"Error creating database pool: {e}")

    async def execute_query(self, query, params=None):
        if not self.db_pool:
            await self.create_db_pool()
        
        async with self.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                try:
                    await cursor.execute(query, params)
                    result = await cursor.fetchall()
                    return result
                except Exception as e:
                    print(f"Error executing query: {e}")
                    return None
    @commands.command(aliases=["mc", "myc"])
    async def mycards(self, ctx):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            user_id = user_result[0]['user_id']

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
            ORDER BY uc.power_stat DESC
            """
            cards = await self.execute_query(cards_query, (user_id,))
            if not cards:
                await ctx.send("You don't have any cards yet.")
                return

            # Create the view
            view = CardView(ctx, cards)

            # Directly create the embed using the view's create_embed method
            embed = view.create_embed()
            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = view.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")
            img = cards[0]['image']
            if cards[0].get('pseudo_image') and cards[0]['pseudo_image'] != "" :
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
            else:
                message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=None)
        except Exception as e:
            print(f"Error: {e}")
            await ctx.send("An error occurred while processing your request.")
    # The CardView class modifications from the previous artifact remain the same
    @commands.command(aliases=["cv", "cvw"])
    async def cardview(self, ctx, *, card_name):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))

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
            card_results = await self.execute_query(card_query, (user_id, search_pattern, search_pattern, search_pattern))

            if not card_results:
                await ctx.send(f"You don't have any cards matching '{card_name}' in your collection.")
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
                3: discord.Color.from_rgb(120, 0, 164),    # A Rank - Red
                4: discord.Color.from_rgb(0, 0, 255),  # B Rank - Orange
                5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Yellow
                6: discord.Color.from_rgb(154, 12, 6),    # D Rank - Green
                7: discord.Color.from_rgb(128, 128, 128)   # E Rank - Blue
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
                def __init__(self, ctx, card):
                    super().__init__(timeout=180)
                    self.ctx = ctx
                    self.card = card

                    # Disable stats button for Support cards
                    self.stats_button.disabled = card['type_card'] in ['Support','Booster']

                @discord.ui.button(label="Stats", style=discord.ButtonStyle.green)
                async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != self.ctx.author.id:
                        await interaction.response.send_message("You can't use these buttons.", ephemeral=True)
                        return
                    stat_view = StatAdjustView(self.ctx, self.card, self)
                    await interaction.response.edit_message(view=stat_view)
            view = CardView(ctx, card)

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
    @commands.command(aliases=["wv", "wvw"])
    async def weaponview(self, ctx, *, weapon_name):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))
            
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
            weapons = await self.execute_query(weapon_query, (search_pattern,))

            if not weapons:
                await ctx.send(f"No weapons matching '{weapon_name}' found in the database.")
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
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            weapons_query = """
                SELECT w.*, wr.weaponrank_name
                FROM weapon w
                JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                ORDER BY FIELD(wr.weaponrank_name, 'S', 'A', 'B', 'C', 'D', 'E')
            """
            weapons = await self.execute_query(weapons_query)

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
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await self.execute_query(inventory_query, (user_result[0]['user_id'],))
            
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
            weapons = await self.execute_query(weapons_query, (inventory_id,))

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
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))
            
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
            card_results = await self.execute_query(card_query, (search_pattern,))
            
            if not card_results:
                await ctx.send(f"No cards matching '{card_name}' found in the database.")
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
                3: discord.Color.from_rgb(120, 0, 164),    # A Rank - Red
                4: discord.Color.from_rgb(0, 0, 255),  # B Rank - Orange
                5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Yellow
                6: discord.Color.from_rgb(154, 12, 6),    # D Rank - Green
                7: discord.Color.from_rgb(128, 128, 128)   # E Rank - Blue
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
    async def cardlist(self, ctx):
        try:
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await self.execute_query(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            cards_query = """
            SELECT c.*, cr.cardrank_name, cc.card_class_name,cr.cardrank_image,cr.cardrank_id
            FROM card c
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            WHERE c.seeable = 1
            ORDER BY power DESC
            """
            cards = await self.execute_query(cards_query)

            if not cards:
                await ctx.send("There are no cards in the database.")
                return

            view = AllView(ctx, cards)
            
            # Get the image path for the first card
            image_path = view.get_image_path(cards[0]['image'])
            
            # Create initial embed
            embed = view.create_embed()
            files = []
            
            # Handle rank image (thumbnail)
            rank_image_path = self.get_image_path(cards[0]['cardrank_image'])
            if rank_image_path and os.path.exists(rank_image_path):
                rank_file = discord.File(rank_image_path, filename="rank_image.png")
                files.append(rank_file)
                embed.set_thumbnail(url="attachment://rank_image.png")

            # Handle card image (main image)
            card_image_path = self.get_image_path(cards[0]['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")
            if os.path.exists(rank_image_path) and os.path.exists(card_image_path):
                view.message = await ctx.send(embed=embed, files=files, view=view)
            else:
                view.message = await ctx.send(embed=embed, view=view)

            # Wait for the view to timeout or stop
            await view.wait()
            
            # Clean up the view after it's done
            try:
                await view.message.edit(view=None)
            except:
                pass

        except Exception as e:
            print(f"Error in viewallcards: {e}")
            await ctx.send("An error occurred while processing your request.")
            
    
    def is_valid_url(self, url):
        try:
            result = urllib.parse.urlparse(url)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    async def get_valid_card(self, cursor, premium_status=0):
        while True:
            await cursor.execute("""
                SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
                FROM card c
                JOIN card_rank cr ON c.card_rank = cr.cardrank_id
                JOIN card_class cc ON c.card_class_id = cc.card_class_id
                WHERE c.spawn_status = 1
            """)
            cards = await cursor.fetchall()

            # Sort cards by rank
            rank_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
            sorted_cards = sorted(cards, key=lambda x: rank_order[x['cardrank_name']])

            # Determine rarity weights based on premium status
            if premium_status == 2:
                print('2')
                rarity_weights = {
                    'S': 10,    # 0.1%
                    'A': 120,    # 1.2%
                    'B': 500,    # 5%
                    'C': 800,   # 8%
                    'D': 3070,   # 30.7%
                    'E': 5500    # 55%
                }
            elif premium_status == 3:
                print('1')
                rarity_weights = {
                    'S': 100,    # 1%
                    'A': 200,    # 2%
                    'B': 1000,   # 10%
                    'C': 2500,   # 25%
                    'D': 2900,   # 29%
                    'E': 3300    # 33x%
                }
            else:
                print('0 or 1')
                rarity_weights = {
                    'S': 1,     # 0.01%
                    'A': 10,    # 0.1%
                    'B': 200,    # 2%
                    'C': 500,   # 5%
                    'D': 3200,   # 32%
                    'E': 6089    # 60.15%
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

            possible_cards = [card for card in sorted_cards if card['cardrank_name'] == selected_rank]
            if possible_cards:
                drawn_card = random.choice(possible_cards)
                image_path = self.get_image_path(drawn_card['image'])
                if image_path and os.path.exists(image_path):
                    print('pri, ', drawn_card)
                    return drawn_card

    async def get_guaranteed_rank_card(self, cursor, rank_id):
        await cursor.execute("""
            SELECT c.*, cr.cardrank_name, cr.cardrank_id, cr.cardrank_image, cc.card_class_name 
            FROM card c
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            WHERE c.spawn_status = 1 AND cr.cardrank_id = %s
        """, (rank_id,))
        possible_cards = await cursor.fetchall()
        
        if possible_cards:
            drawn_card = random.choice(possible_cards)
            print('drawn', drawn_card)
            return drawn_card
        return None    
    @commands.command(aliases=["ars", "ar"])
    async def arise(self, ctx):
        if not self.db_pool:
            await self.create_db_pool()

        if not self.db_pool:
            await ctx.send("Unable to connect to the database. Please try again.")
            return

        try:
            async with self.db_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    # Get user information including premium status
                    await cursor.execute(
                        """SELECT user_id, quantiter_fragment, fragment_cap, 
                        roll_timer, roll_count, roll_cap, prenium, prenium_date, roll_total
                        FROM user WHERE user_authorid = %s""", 
                        (str(ctx.author.id),)
                    )
                    user_result = await cursor.fetchone()
                    
                    if not user_result:
                        await ctx.send("Error: You need to register to the bot first.")
                        return

                    current_time = datetime.datetime.utcnow()
                    premium_status = user_result.get('prenium', 0)

                    # Check roll timer and count
                    if user_result['roll_timer'] is None:
                        await cursor.execute(
                            "UPDATE user SET roll_timer = %s, roll_count = 1 WHERE user_id = %s",
                            (current_time, user_result['user_id'])
                        )
                    else:
                        roll_timer = user_result['roll_timer']
                        time_difference = current_time - roll_timer
                        hours_difference = time_difference.total_seconds() / 3600

                        if hours_difference < 8 and user_result['roll_count'] >= user_result['roll_cap']:
                            remaining_seconds = (8 * 3600) - time_difference.total_seconds()
                            hours = int(remaining_seconds // 3600)
                            minutes = int((remaining_seconds % 3600) // 60)
                            seconds = int(remaining_seconds % 60)
                            
                            await ctx.send(
                                f"You've reached your roll limit! Time remaining: "
                                f"{hours}hours {minutes}mins {seconds}s"
                            )
                            return
                        elif hours_difference >= 8:
                            await cursor.execute(
                                "UPDATE user SET roll_timer = %s, roll_count = 1 WHERE user_id = %s",
                                (current_time, user_result['user_id'])
                            )
                            user_result['roll_count'] = 0
                        elif user_result['roll_count'] < user_result['roll_cap']:
                            new_count = user_result['roll_count'] + 1
                            await cursor.execute(
                                "UPDATE user SET roll_count = %s WHERE user_id = %s",
                                (new_count, user_result['user_id'])
                            )

                    # Update roll_total
                    new_roll_total = (user_result.get('roll_total', 0) or 0) + 1
                    await cursor.execute(
                        "UPDATE user SET roll_total = %s WHERE user_id = %s",
                        (new_roll_total, user_result['user_id'])
                    )

                    # Check for guaranteed card
                    guaranteed_special = False
                    drawn_card = None

                    roll_thresholds = {
                        0: (250, 3),   # Regular users: 250 rolls for A rank
                        1: (200, 3),   # Premium 1: 200 rolls for A rank
                        2: (150, 3),   # Premium 2: 150 rolls for A rank
                        3: (150, 2),   # Premium 3: 150 rolls for S rank
                    }

                    threshold, rank = roll_thresholds.get(premium_status, (250, 3))
                    if new_roll_total == threshold:
                        drawn_card = await self.get_guaranteed_rank_card(cursor, rank)
                        if drawn_card:
                            guaranteed_special = True
                            await cursor.execute(
                                "UPDATE user SET roll_total = 0 WHERE user_id = %s",
                                (user_result['user_id'],)
                            )
                            new_roll_total = 0

                    if not drawn_card:
                        drawn_card = await self.get_valid_card(cursor, premium_status)

                    if not drawn_card:
                        await ctx.send("Failed to get a valid card. Please try again.")
                        return

                    user_id = user_result['user_id']
                    current_fragments = user_result['quantiter_fragment']
                    fragment_cap = user_result['fragment_cap']

                    # Check if user already has the card
                    await cursor.execute(
                        "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s",
                        (user_id, drawn_card['card_id'])
                    )
                    user_card_result = await cursor.fetchone()

                    description = ""
                    footer_text = ""

                    if not user_card_result:
                        # Add new card to user's collection
                        if drawn_card['card_id'] == 103:
                            await cursor.execute(
                                "UPDATE user SET roll_cap = roll_cap + 1 WHERE user_id = %s", 
                                (user_id,)
                            )
                        if drawn_card['card_id'] == 104:
                            await cursor.execute(
                                "UPDATE user SET fragment_cap = fragment_cap + 100 WHERE user_id = %s", 
                                (user_id,)
                            )

                        await cursor.execute("""
                            INSERT INTO user_cards (user_id, card_id, current_level, current_xp, 
                            current_mana, power_stat, attack_stat, health_stat, speed_stat, name, 
                            defence_stat, attack_emoji, defense_emoji, speed_emoji, health_emoji,
                            weapon_emoji, secondshield_emoji, armor_emoji, xp_emoji, level_emoji,
                            power_emoji, type_emoji, class_emoji) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                            %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            user_id, drawn_card['card_id'], drawn_card['min_level'], 0, 0,
                            drawn_card['power'], drawn_card['attack'], drawn_card['health'], 
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
                            await cursor.execute(
                                "SELECT id_fragment FROM card WHERE card_id = %s",
                                (drawn_card['card_id'],)
                            )
                            fragment_result = await cursor.fetchone()

                            if fragment_result:
                                fragment_id = fragment_result['id_fragment']
                                await cursor.execute(
                                    "SELECT inventory_id FROM inventory WHERE user_id = %s",
                                    (user_id,)
                                )
                                inventory_result = await cursor.fetchone()

                                if inventory_result:
                                    inventory_id = inventory_result['inventory_id']
                                    await cursor.execute(
                                        """SELECT * FROM inventory_frags 
                                        WHERE inventory_id = %s AND frag_id = %s""",
                                        (inventory_id, fragment_id)
                                    )
                                    inv_frag_result = await cursor.fetchone()

                                    if inv_frag_result:
                                        await cursor.execute("""
                                            UPDATE inventory_frags 
                                            SET quantiter = quantiter + 1 
                                            WHERE inventory_id = %s AND frag_id = %s
                                        """, (inventory_id, fragment_id))
                                    else:
                                        await cursor.execute("""
                                            INSERT INTO inventory_frags (inventory_id, frag_id, quantiter)
                                            VALUES (%s, %s, 1)
                                        """, (inventory_id, fragment_id))

                                    await cursor.execute("""
                                        SELECT SUM(quantiter) as total_fragments
                                        FROM inventory_frags
                                        WHERE inventory_id = %s
                                    """, (inventory_id,))
                                    total_fragments_result = await cursor.fetchone()
                                    total_fragments = total_fragments_result['total_fragments']

                                    if total_fragments <= fragment_cap:
                                        await cursor.execute("""
                                            UPDATE user
                                            SET quantiter_fragment = %s
                                            WHERE user_id = %s
                                        """, (total_fragments, user_id))

                                    description = "You already have this card.\n\n"
                                    footer_text = f"You've received an Essence Stone instead! Total fragments: {total_fragments}/{fragment_cap}"

                    # Create embed
                    rank_colors = {
                        2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
                        3: discord.Color.from_rgb(120, 0, 164),  # A Rank - Purple
                        4: discord.Color.from_rgb(0, 0, 255),    # B Rank - Blue
                        5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
                        6: discord.Color.from_rgb(154, 12, 6),   # D Rank - Red
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

                        if guaranteed_special:
                            roll_total_display = 0
                        else:
                            roll_total_display = new_roll_total

                        # Add premium status indicator
                        premium_indicator = ""
                        if premium_status > 0:
                            premium_tiers = {
                                1: "🥉",  # Bronze
                                2: "🥈",  # Silver
                                3: "🥇"   # Gold
                            }
                            premium_indicator = f" {premium_tiers.get(premium_status, '')} "

                        footer_text += (f"{premium_indicator}| Rolls: {user_result['roll_count'] + 1}/"
                                    f"{user_result['roll_cap']} | Total Rolls: {roll_total_display}")

                        if premium_status == 0:
                            print(000)
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

                    embed.set_footer(text=footer_text, icon_url=ctx.author.avatar.url)
                    embed.timestamp = discord.utils.utcnow()

                    # Send the message with all files at once
                    if files:
                        await ctx.send(embed=embed, files=files)
                    else:
                        await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error in arise command: {e}")
            await ctx.send("An error occurred while processing your request. Please try again.")

    async def update_quest_progress(self, cursor, user_id):
        try:
            # Check if the user has any active quests
            await cursor.execute("""
                SELECT uq.*, q.quest_name, q.required_count
                FROM user_quests uq
                JOIN quest q ON uq.quest_id = q.quest_id
                WHERE uq.user_id = %s AND q.quest_name = 'Pull 7 cards' AND uq.completion_status = 0
            """, (user_id,))
            quest_result = await cursor.fetchone()
            
            if quest_result:
                # Increment the current_count
                new_count = quest_result['current_count'] + 1
                await cursor.execute("""
                    UPDATE user_quests
                    SET current_count = %s
                    WHERE userquests_id = %s
                """, (new_count, quest_result['userquests_id']))
                
                # Check if the quest is completed
                if new_count >= quest_result['required_count']:
                    await cursor.execute("""
                        UPDATE user_quests
                        SET completion_status = 1
                        WHERE userquests_id = %s
                    """, (quest_result['userquests_id'],))
                    
                    # Get the user's inventory_id
                    await cursor.execute("""
                        SELECT inventory_id 
                        FROM inventory 
                        WHERE user_id = %s
                    """, (user_id,))
                    inventory_result = await cursor.fetchone()
                    
                    if inventory_result:
                        inventory_id = inventory_result['inventory_id']
                        
                        # Check if the user already has a chest with ID 1
                        await cursor.execute("""
                            SELECT * 
                            FROM inventory_chest 
                            WHERE inventory_id = %s AND chest_id = 1
                        """, (inventory_id,))
                        existing_chest = await cursor.fetchone()
                        
                        if existing_chest:
                            # Update quantity if chest exists
                            await cursor.execute("""
                                UPDATE inventory_chest 
                                SET quantiter = quantiter + 1 
                                WHERE inventory_id = %s AND chest_id = 1
                            """, (inventory_id,))
                        else:
                            # Insert new chest entry if it doesn't exist
                            await cursor.execute("""
                                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                                VALUES (%s, 1, 1)
                            """, (inventory_id,))
                    
                    print(f"User {user_id} completed the 'Pull 7 cards' quest and received a chest!")
        
        except Exception as e:
            print(f"Error updating quest progress: {e}")

async def setup(bot):
    await bot.add_cog(SpawnCommands(bot))