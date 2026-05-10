# commands/user.py
import discord
from discord.ext import commands
import random
from datetime import datetime
from utils.database import create_db_connection
from mysql.connector import Error

class UserCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_sets = {
            "spawn_team_upgrade": [
                "https://i.imgur.com/w9HGDs8.png", "https://i.imgur.com/eXDoVtt.png",
                "https://i.imgur.com/BfP3SOb.png", "https://i.imgur.com/0eom9m2.png",
                "https://i.imgur.com/SR5BaxV.png", "https://i.imgur.com/5Zt3z4s.png",
                "https://i.imgur.com/UUYgw06.png", "https://i.imgur.com/dMhOikE.png",
                "https://i.imgur.com/KmTcWGw.png", "https://i.imgur.com/HOJ3lQm.png",
                "https://i.imgur.com/pYO9Hdw.png", "https://i.imgur.com/GosywXi.png"
            ],
            "shop_inventory": [
                "https://i.imgur.com/hgwXqu8.png", "https://i.imgur.com/YY48Fq4.png",
                "https://i.imgur.com/kXgHfbc.png", "https://i.imgur.com/RJytrk0.png",
                "https://i.imgur.com/qS00Lv4.png", "https://i.imgur.com/RYT5LNH.png",
                "https://i.imgur.com/1djoqmW.png", "https://i.imgur.com/vvP4bcz.png",
                "https://i.imgur.com/E5XbGRY.png"
            ],
            "trading": [
                "https://i.imgur.com/ApUiqSp.png", "https://i.imgur.com/FOc88Fc.png",
                "https://i.imgur.com/QI7Mzew.png", "https://i.imgur.com/nyjzkdG.png",
                "https://i.imgur.com/L3mnlYk.png", "https://i.imgur.com/x7Hoe96.png"
            ],
            "dailies_quests": [
                "https://i.imgur.com/NJFNkAq.png", "https://i.imgur.com/UVi08op.png",
                "https://i.imgur.com/266Anne.png"
            ],
            "dungeons_missions": [
                "https://i.imgur.com/qqx1Zqf.png", "https://i.imgur.com/1Zu8XwN.png",
                "https://i.imgur.com/IYNjRCU.png"
            ],
            "fight_challenges": [
                "https://i.imgur.com/UWiuAwM.png", "https://i.imgur.com/RPXvv3M.png",
                "https://i.imgur.com/1556dT1.png"
            ]
        }
        self.default_images = [
            "https://64.media.tumblr.com/26e86a3d6bad8d52abcff52e4a80982a/835b8a36ae6f0bc1-3f/s500x750/46d03db11288f7fb36a96439262fcd4464285d34.jpg",
            "https://i.imgur.com/rGROBpj.jpeg",
            "https://i.imgur.com/MjY6B04.jpeg",
            "https://i.imgur.com/LFbWzgl.jpeg",
            "https://i.imgur.com/tXEx2pf.jpeg",
            "https://i.imgur.com/sCw46hJ.jpeg",
            "https://i.imgur.com/c0Yps3b.jpeg",
            "https://i.imgur.com/npesxBf.jpeg",
            "https://i.imgur.com/IeScbWE.png"
        ]
        self.fast_scroll_sets = ["dungeons_missions", "trading", "fight_challenges"]
    def execute_query(self, connection, query, params=None):
        cursor = connection.cursor(dictionary=True)
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            result = cursor.fetchall()
            connection.commit()
            return result
        except Error as e:
            print(f"Error executing query: {e}")
            return None
        finally:
            cursor.close()

    @commands.command(name='register')
    async def register(self, ctx):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_authorid = str(ctx.author.id)
        
        # Check if user already exists
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = self.execute_query(connection, query, (user_authorid,))
        
        if result:
            embed = discord.Embed(
                title="🚫 Registration Failed",
                description="It seems you've already joined the ranks, Hunter. Use !cards to view your current collection.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://i.imgur.com/X3ViR9W.png")
            await ctx.send(embed=embed)
            return

        # Insert new user
        query = """INSERT INTO user (user_authorid, entry_date, rank, won_currency) 
                   VALUES (%s, %s, %s, %s)"""
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.execute_query(connection, query, (user_authorid, current_date, 1, 20000))

        # Get the new user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = self.execute_query(connection, query, (user_authorid,))
        user_id = result[0]['user_id']

        # Insert into team and inventory tables
        query = "INSERT INTO team (user_id) VALUES (%s)"
        self.execute_query(connection, query, (user_id,))
        
        query = "INSERT INTO inventory (user_id) VALUES (%s)"
        self.execute_query(connection, query, (user_id,))

        # Get cards for the user
        query = """SELECT * FROM card WHERE spawn_status = 1 AND card_rank = 6"""
        cards = self.execute_query(connection, query)

        fighter_cards = [card for card in cards if card['type_card'] == 'Fighter']
        support_cards = [card for card in cards if card['type_card'] == 'Support']

        final_cards = random.sample(fighter_cards, 3) + random.sample(support_cards, 1)

        # Insert cards into user_cards table
        for card in final_cards:
            query = """INSERT INTO user_cards (user_id, card_id, current_level, current_xp, 
                       current_mana, power_stat, attack_stat, health_stat, speed_stat) 
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            self.execute_query(connection, query, (user_id, card['card_id'], card['min_level'], 0, 0,
                                                   card['power'], card['attack'], card['health'], card['speed']))

        # Create embed message
        embed = discord.Embed(
            title="🏆 Welcome to the Hunter's Association!",
            description=(
                "*Congratulations on your registration, brave Hunter!*\n\n"
                "You have taken your first step into a world of adventure, danger, and untold riches. "
                "As a newly registered Hunter, you've been granted a set of powerful starter cards to aid you in your journey."
            ),
            color=discord.Color.gold()
        )

        embed.add_field(
            name="✨ Your Starter Cards",
            value="\n".join([f"**{card['card_name']}** - Rank: E, Type: {card['type_card']}, Power: {card['power']}" for card in final_cards]),
            inline=False
        )

        embed.add_field(
            name="💰 Starting Resources",
            value=f"Cash: 100 gold\nXP Bottles: 0\nCard Fragments: 0",
            inline=False
        )

        embed.add_field(
            name="🔥 Total Power",
            value=f"{sum(card['power'] for card in final_cards)} Hunter Points",
            inline=False
        )

        embed.add_field(
            name="⚔️ Ready for Battle?",
            value=(
                "Your journey to becoming an S-Rank Hunter starts now! Here are some commands to get you started:\n"
                "• `!cards` - View your card collection\n"
                "• `!dungeon` - Start your first dungeon challenge\n"
                "• `!shop` - Visit the Hunter's shop for upgrades\n"
                "• `!daily` - Claim your daily rewards"
            ),
            inline=False
        )

        embed.set_image(url="https://cdn-www.konbini.com/files/2023/01/Une-Solo-Leveling.jpg?width=3840&quality=75&format=webp")
        embed.set_footer(text="May your legend echo through the annals of Hunter history!")
        embed.set_thumbnail(url=ctx.author.avatar.url)

        await ctx.send(embed=embed)

        connection.close()
    @commands.command(name='show')
    async def show(self, ctx):
        current_index = 0
        current_set = "default"
        image_links = self.default_images

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_image(url=image_links[current_index])
        message = await ctx.send(embed=embed)

        async def update_message():
            embed.set_image(url=image_links[current_index])
            
            view = discord.ui.View(timeout=None)
            view.add_item(previous_button)
            view.add_item(next_button)
            
            if current_set == "default":
                if current_index == len(image_links) - 1:
                    view.add_item(select_menu)
                    view.add_item(register_button)
                else:
                    view.add_item(skip_intro_button)
            elif current_set in ["dungeons_missions", "trading"]:
                view.add_item(go_back_button)
            else:
                view.add_item(go_back_button)
            
            await message.edit(embed=embed, view=view)

        async def scroll_callback(interaction, direction):
            nonlocal current_index
            scroll_speed = 1
            if direction == "previous":
                current_index = max(0, current_index - scroll_speed)
            else:
                current_index = min(len(image_links) - 1, current_index + scroll_speed)
            await update_message()
            await interaction.response.defer()

        async def previous_callback(interaction):
            await scroll_callback(interaction, "previous")

        async def next_callback(interaction):
            await scroll_callback(interaction, "next")

        async def skip_intro_callback(interaction):
            nonlocal current_index
            current_index = len(image_links) - 1
            await update_message()
            await interaction.response.defer()

        async def go_back_callback(interaction):
            nonlocal current_set, image_links, current_index
            current_set = "default"
            image_links = self.default_images
            current_index = len(image_links) - 1
            await update_message()
            await interaction.response.defer()

        async def select_callback(interaction):
            nonlocal current_set, image_links, current_index
            selected_option = interaction.data['values'][0]
            current_set = selected_option
            image_links = self.image_sets[selected_option]
            current_index = 0
            await update_message()
            await interaction.response.defer()

        async def register_callback(interaction):
            await interaction.response.send_message("Registration functionality coming soon!", ephemeral=True)

        previous_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="◀️")
        next_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="▶️")
        skip_intro_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Skip Intro")
        go_back_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Go Back")

        previous_button.callback = previous_callback
        next_button.callback = next_callback
        skip_intro_button.callback = skip_intro_callback
        go_back_button.callback = go_back_callback

        select_menu = discord.ui.Select(
            placeholder="Choose an option",
            options=[
                discord.SelectOption(label="Spawn-Team-Upgrade", value="spawn_team_upgrade"),
                discord.SelectOption(label="Shop-Inventory", value="shop_inventory"),
                discord.SelectOption(label="Dungeons-Missions", value="dungeons_missions"),
                discord.SelectOption(label="Trading", value="trading"),
                discord.SelectOption(label="Dailies-Quests", value="dailies_quests"),
                discord.SelectOption(label="Fight-Challenges", value="fight_challenges")
            ]
        )
        select_menu.callback = select_callback

        register_button = discord.ui.Button(style=discord.ButtonStyle.success, label="Register")
        register_button.callback = register_callback

        await update_message()
async def setup(bot):
    await bot.add_cog(UserCommands(bot))