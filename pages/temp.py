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
    @commands.command()
    async def viewthisweapon(self, ctx, *, weapon_name):
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

    @commands.command()
    async def viewallweapons(self, ctx):
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

    @commands.command()
    async def viewmyweapons(self, ctx):
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