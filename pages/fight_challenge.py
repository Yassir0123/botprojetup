import discord
from discord.ext import commands
import mysql.connector
from mysql.connector import Error
import random
import asyncio
from discord.errors import NotFound
from PIL import Image, ImageDraw
from easy_pil import Font, Editor, Canvas
import io
import os
import time
import aiomysql

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
class FightCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.font = Font.poppins(size=60, variant="bold")
        self.processed_images = {}  # Cache for processed images
        self.db_pool = None
        
    async def create_db_pool(self):
        try:
            self.db_pool = await aiomysql.create_pool(
                host='localhost',
                user='root',
                password='',
                db='sololevelingbot',
                autocommit=True
            )
        except Exception as e:
            print(f"Error creating database pool: {e}")

    async def execute_query(self, query, params=None):
        if not self.db_pool:
            await self.create_db_pool()
        
        start_time = time.time()
        async with self.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                try:
                    await cursor.execute(query, params)
                    result = await cursor.fetchall()
                    print(f"Query execution time: {time.time() - start_time:.2f} seconds")
                    return result
                except Error as e:
                    print(f"Error executing query: {e}")
                    return None
    def calculate_effective_damage(self, attack_damage, defense_stat):
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        return round(effective_damage)
    async def get_user_data(self, user_id):
        user_query = "SELECT user_id, user_authorid FROM user WHERE user_authorid = %s"
        user_result = await self.execute_query(user_query, (str(user_id),))
        
        if not user_result:
            return None

        user_data = user_result[0]
        
        team_query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = await self.execute_query(team_query, (user_data['user_id'],))
        
        if not team_result:
            return None

        user_data['team_id'] = team_result[0]['team_id']

        fighters_query = """
        SELECT c.card_name, c.image, uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, uc.current_level
        FROM team_cards tc
        JOIN card c ON tc.card_id = c.card_id
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        """
        user_data['fighters'] = await self.execute_query(fighters_query, (user_data['user_id'], user_data['team_id']))
        return user_data
    @commands.command(name="fight")
    async def fight(self, ctx):
        start_time = time.time()
        await ctx.send("Initiating fight...")
        
        if not self.db_pool:
            await self.create_db_pool()

        if not self.db_pool:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        try:
            user_data = await self.get_user_data(ctx.author.id)
            if not user_data:
                await ctx.send("You need to be registered and have a team to use this command.")
                return

            if not user_data['fighters']:
                await ctx.send("You need at least one fighter card in your team to proceed with the fight.")
                return

            # Get all users except the one who initiated the fight
            all_users_query = """
            SELECT u.user_id, u.user_authorid, t.team_id
            FROM user u
            JOIN team t ON u.user_id = t.user_id
            WHERE u.user_id NOT IN (1, 2, 3) AND u.user_id != %s
            """
            all_users = await self.execute_query(all_users_query, (user_data['user_id'],))


            # Filter users with fighter cards in their team
            valid_opponents = []
            for opponent in all_users:
                fighters_query = """
                SELECT c.card_name, c.image, uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, uc.current_level
                FROM team_cards tc
                JOIN card c ON tc.card_id = c.card_id
                JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                """
                fighters = await self.execute_query(fighters_query, (opponent['user_id'], opponent['team_id']))
                if fighters:
                    opponent['fighters'] = fighters
                    valid_opponents.append(opponent)

            if not valid_opponents:
                await ctx.send("No suitable opponents found. Please try again later.")
                return

            # Randomly select an opponent
            opponent_data = random.choice(valid_opponents)

            # Get fight background
            fight_bg_query = "SELECT fight_bg FROM fight LIMIT 1"
            fight_bg_result = await self.execute_query(fight_bg_query)
            fight_bg = fight_bg_result[0]['fight_bg'] if fight_bg_result else 'default_fight_bg.jpg'
            
            print(f"Fight preparation time: {time.time() - start_time:.2f} seconds")
            
            # Start the fight
            await self.start_fight(ctx, user_data, opponent_data, fight_bg)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
            print(f"Error in fight command: {e}")

    async def fetch_user_with_retry(self, user_id, max_retries=MAX_RETRIES, delay=RETRY_DELAY):
        for attempt in range(max_retries):
            try:
                return await self.bot.fetch_user(int(user_id))
            except NotFound as e:
                if attempt == max_retries - 1:  # Last attempt
                    print(f"Failed to fetch user {user_id} after {max_retries} attempts: {e}")
                    return None
                print(f"Attempt {attempt + 1} failed to fetch user {user_id}. Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
        return None

    async def start_fight(self, ctx, user_data, opponent_data, fight_bg):
        try:
            print(f"Starting fight with user_data: {user_data}")
            print(f"Opponent data: {opponent_data}")

            # Fetch users with retry
            user = await self.fetch_user_with_retry(user_data['user_authorid'])
            opponent = await self.fetch_user_with_retry(opponent_data['user_authorid'])

            print(f"Fetched user: {user}")
            print(f"Fetched opponent: {opponent}")

            # Set names, defaulting to "Unknown" if fetch fails
            user_name = user.name if user else f"Unknown User (ID: {user_data['user_authorid']})"
            opponent_name = opponent.name if opponent else f"Unknown Opponent (ID: {opponent_data['user_authorid']})"

            print(f"User name: {user_name}")
            print(f"Opponent name: {opponent_name}")

            # Proceed with the fight even if one or both users couldn't be fetched
            await ctx.send(f"Fight starting between {user_name} and {opponent_name}!")
            #await asyncio.sleep(1)  # 1-second pause before the fight starts

            await self.conduct_fight(ctx, user_data['fighters'], opponent_data['fighters'], user_name, opponent_name, fight_bg)

        except Exception as e:
            error_message = f"An unexpected error occurred: {str(e)}"
            print(error_message)
            print(f"User data: {user_data}")
            print(f"Opponent data: {opponent_data}")
            await ctx.send(f"An unexpected error occurred. Please try again later or contact an administrator.")

        finally:
            print("Fight sequence completed.")
    @commands.command(name="challenge")
    async def challenge(self, ctx, opponent: discord.Member):
        if not self.db_pool:
            await self.create_db_pool()

        if not self.db_pool:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return
        if opponent.id == ctx.author.id:
            await ctx.send(f"If you want to challenge yourself, might aswell get some missions done")
            return
        user_data = await self.get_user_data(ctx.author.id)
        if not user_data:
            await ctx.send("You need to be registered and have a team to use this command.")
            return

        if not user_data['fighters']:
            await ctx.send("You need at least one fighter card in your team to proceed with the challenge.")
            return
        opponent_data = await self.get_user_data(opponent.id)
        if not opponent_data:
            await ctx.send(f"{opponent.name} is not registered or doesn't have a team.")
            return

        if not opponent_data['fighters']:
            await ctx.send(f"{opponent.name} doesn't have any fighter cards in their team.")
            return

        # Send challenge message and add reactions
        challenge_msg = await ctx.send(f"{opponent.mention}, you've been challenged to a fight by {ctx.author.name}! React with ✅ to accept or ❌ to decline.")
        await challenge_msg.add_reaction("✅")
        await challenge_msg.add_reaction("❌")

        def check(reaction, user):
            return user == opponent and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == challenge_msg.id

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            if str(reaction.emoji) == "✅":
                await ctx.send(f"{opponent.name} has accepted the challenge! The fight will begin shortly.")
                
                # Get fight background
                fight_bg_query = "SELECT fight_bg FROM fight LIMIT 1"
                fight_bg_result = await self.execute_query(fight_bg_query)
                fight_bg = fight_bg_result[0]['fight_bg'] if fight_bg_result else 'default_fight_bg.jpg'
                
                await self.start_fight(ctx, user_data, opponent_data, fight_bg)
            else:
                await ctx.send(f"{opponent.name} has declined the challenge. The fight won't happen.")
        except asyncio.TimeoutError:
            await ctx.send(f"{opponent.name} didn't respond in time. The challenge has expired.")
    async def conduct_fight(self, ctx, user_fighters, opponent_fighters, user_name, opponent_name, fight_bg):
        user_team = list(user_fighters)
        opponent_team = list(opponent_fighters)

        user_card = user_team[0]
        opponent_card = opponent_team[0]

        user_card['current_health'] = user_card['health_stat']
        opponent_card['current_health'] = opponent_card['health_stat']

        config = {
            'line_x': 1280,
            'line_color': "white",
            'line_width': 5,
            'additional_image_size': (200, 300),
            'user_additional_start_y': 800,
            'opponent_additional_start_y': 800,
            'additional_image_spacing': 20
        }

        # Create the initial fight image
        fight_image = await self.create_fight_image(fight_bg, user_team, opponent_team, config)
        fight_message = await ctx.send(file=discord.File(fight_image, filename="fight.png"))
        damage_message = await ctx.send("The fight is about to begin!")

        while user_team and opponent_team:
            await asyncio.sleep(1)

            # User attacks
            base_user_damage = random.randint(10, user_card['attack_stat'])
            effective_user_damage = self.calculate_effective_damage(base_user_damage, opponent_card['defence_stat'])
            opponent_card['current_health'] -= effective_user_damage
            opponent_card['current_health'] = max(0, opponent_card['current_health'])

            # Update the fight image and damage message
            fight_image = await self.create_fight_image(fight_bg, user_team, opponent_team, config)
            await fight_message.edit(attachments=[discord.File(fight_image, filename="fight.png")])
            await damage_message.edit(content=f"Damage dealt: {user_card['card_name']} → {effective_user_damage} (Base: {base_user_damage})")

            if opponent_card['current_health'] <= 0:
                opponent_team.pop(0)
                if opponent_team:
                    opponent_card = opponent_team[0]
                    opponent_card['current_health'] = opponent_card['health_stat']
                    fight_image = await self.create_fight_image(fight_bg, user_team, opponent_team, config)
                    await fight_message.edit(attachments=[discord.File(fight_image, filename="fight.png")])
                    await damage_message.edit(content="New opponent has entered the fight!")
                else:
                    break
                continue

            await asyncio.sleep(1)

            # Opponent attacks
            base_opponent_damage = random.randint(10, opponent_card['attack_stat'])
            effective_opponent_damage = self.calculate_effective_damage(base_opponent_damage, user_card['defence_stat'])
            user_card['current_health'] -= effective_opponent_damage
            user_card['current_health'] = max(0, user_card['current_health'])

            # Update the fight image and damage message
            fight_image = await self.create_fight_image(fight_bg, user_team, opponent_team, config)
            await fight_message.edit(attachments=[discord.File(fight_image, filename="fight.png")])
            await damage_message.edit(content=f"Damage dealt: {opponent_card['card_name']} → {effective_opponent_damage} (Base: {base_opponent_damage})")

            if user_card['current_health'] <= 0:
                await ctx.send(f"**{user_card['card_name']} has been defeated!**")
                user_team.pop(0)
                if user_team:
                    user_card = user_team[0]
                    user_card['current_health'] = user_card['health_stat']
                    fight_image = await self.create_fight_image(fight_bg, user_team, opponent_team, config)
                    await fight_message.edit(attachments=[discord.File(fight_image, filename="fight.png")])
                    await damage_message.edit(content="New fighter has entered the fight!")
                else:
                    break
                continue

        if user_team:
            await ctx.send(f"**{user_name} has won the fight!**")
            user_data = await self.get_user_data(ctx.author.id)
            await self.handle_xp_rewards(ctx, user_data['user_id'], user_data['team_id'])
        else:
            await ctx.send(f"**{opponent_name} has won the fight!**")
            await ctx.send(f"{user_name}, you've lost this battle. Better luck next time!")


    async def create_fight_image(self, fight_bg, user_team, opponent_team, config):
        background = Editor(os.path.join(self.image_path, fight_bg))

        # Draw vertical line using PIL's ImageDraw
        line_x = config.get('line_x', 1280)  # Default to middle of a 2560px wide image
        line_color = config.get('line_color', "white")
        line_width = config.get('line_width', 5)
        draw = ImageDraw.Draw(background.image)
        draw.line((line_x, 0, line_x, background.image.height), fill=line_color, width=line_width)

        # Process and paste main fighter images
        user_image = self.process_card_image(user_team[0]['image'], size=(730, 1050))
        opponent_image = self.process_card_image(opponent_team[0]['image'], size=(730, 1050))

        background.paste(user_image, (90, 90))
        background.paste(opponent_image, (1700, 90))

        # Process and paste additional fighter images
        additional_image_size = config.get('additional_image_size', (200, 300))
        user_start_y = config.get('user_additional_start_y', 800)
        opponent_start_y = config.get('opponent_additional_start_y', 800)
        spacing = config.get('additional_image_spacing', 20)

        for i, card in enumerate(user_team[1:], start=1):
            additional_image = self.process_card_image(card['image'], size=additional_image_size)
            x = 90 + (additional_image_size[0] + spacing) * (i - 1)
            background.paste(additional_image, (x, user_start_y))

        for i, card in enumerate(opponent_team[1:], start=1):
            additional_image = self.process_card_image(card['image'], size=additional_image_size)
            x = 1700 + (additional_image_size[0] + spacing) * (i - 1)
            background.paste(additional_image, (x, opponent_start_y))

        # Add text and health bars for main fighters
        user_card = user_team[0]
        opponent_card = opponent_team[0]

        background.text((90, 1300), f"(Lv.{user_card['current_level']}) {user_card['card_name']}", font=self.font, color="white")
        background.text((1670, 1300), f"(Lv.{opponent_card['current_level']}) {opponent_card['card_name']}", font=self.font, color="white")

        user_health_percentage = (user_card['current_health'] / user_card['health_stat']) * 100
        opponent_health_percentage = (opponent_card['current_health'] / opponent_card['health_stat']) * 100

        background.bar(
            (120, 1200),
            max_width=650,
            height=40,
            percentage=user_health_percentage,
            fill="#FF56B2",
            radius=20,
        )
        background.bar(
            (1740, 1200),
            max_width=650,
            height=40,
            percentage=opponent_health_percentage,
            fill="#FF56B2",
            radius=20,
        )

        image_bytes = io.BytesIO()
        background.image.save(image_bytes, format='PNG')
        image_bytes.seek(0)
        return image_bytes

    def process_card_image(self, image_filename, size=(730, 1050)):
        card_image_path = os.path.join(self.image_path, image_filename)
        with Image.open(card_image_path) as img:
            img = img.convert('RGBA').resize(size)
            
            # Create a rectangular mask instead of an elliptical one
            mask = Image.new('L', img.size, 0)
            draw = ImageDraw.Draw(mask)
            draw.rectangle([0, 0, img.size[0], img.size[1]], fill=255)
            
            # Apply rounded corners to the rectangular mask
            radius = 20  # You can adjust this value to change the roundness of the corners
            circle = Image.new('L', (radius * 2, radius * 2), 0)
            circle_draw = ImageDraw.Draw(circle)
            circle_draw.ellipse((0, 0, radius * 2, radius * 2), fill=255)
            alpha = Image.new('L', img.size, 255)
            w, h = img.size
            alpha.paste(circle.crop((0, 0, radius, radius)), (0, 0))
            alpha.paste(circle.crop((0, radius, radius, radius * 2)), (0, h - radius))
            alpha.paste(circle.crop((radius, 0, radius * 2, radius)), (w - radius, 0))
            alpha.paste(circle.crop((radius, radius, radius * 2, radius * 2)), (w - radius, h - radius))
            
            # Combine the rectangular mask with rounded corners
            mask = Image.composite(mask, alpha, alpha)
            
            img.putalpha(mask)
        return img

    async def handle_xp_rewards(self, ctx, user_id, team_id):
        # Get the user's team cards
        team_cards_query = """
        SELECT tc.*, c.card_name, c.card_rank, c.xp_cap, c.max_level, uc.current_level, uc.current_xp,
            uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        JOIN card c ON tc.card_id = c.card_id
        WHERE tc.team_id = %s
        """
        team_cards = await self.execute_query(team_cards_query, (user_id, team_id))

        if not team_cards:
            await ctx.send("No cards found in your team. Unable to process XP rewards.")
            return

        xp_rewards = []

        for card in team_cards:
            xp_reward = random.randint(20, 100)

            new_xp = card['current_xp'] + xp_reward
            new_level = card['current_level']
            
            # Handle level up
            level_up = False
            if new_xp >= card['xp_cap']:
                if new_level < card['max_level']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True
                else:
                    new_xp = card['xp_cap']

            # Generate stat increases if leveled up
            attack_increase = random.randint(10, 15) if level_up else 0
            health_increase = random.randint(10, 15) if level_up else 0
            defence_increase = random.randint(10, 15) if level_up else 0
            speed_increase = random.randint(10, 15) if level_up else 0

            new_attack = card['attack_stat'] + attack_increase
            new_health = card['health_stat'] + health_increase
            new_defence = card['defence_stat'] + defence_increase
            new_speed = card['speed_stat'] + speed_increase

            # Update user_cards table
            update_query = """
            UPDATE user_cards
            SET current_level = %s, current_xp = %s,
                attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s
            WHERE user_id = %s AND card_id = %s
            """
            await self.execute_query(update_query, (new_level, new_xp, new_attack, new_health, 
                                                    new_defence, new_speed, user_id, card['card_id']))

            xp_rewards.append({
                'card_name': card['card_name'],
                'xp_gained': xp_reward,
                'new_level': new_level,
                'new_xp': new_xp,
                'level_up': level_up,
                'attack_increase': attack_increase,
                'health_increase': health_increase,
                'defence_increase': defence_increase,
                'speed_increase': speed_increase
            })

        # Display XP rewards to the user
        if xp_rewards:
            xp_message = "Congratulations! Your cards have gained XP:\n"
            for reward in xp_rewards:
                xp_message += f"- {reward['card_name']}: +{reward['xp_gained']} XP (Now Level {reward['new_level']} with {reward['new_xp']} XP)\n"
                if reward['level_up']:
                    xp_message += f"  Level Up! Stats increased: ATK +{reward['attack_increase']}, HP +{reward['health_increase']}, DEF +{reward['defence_increase']}, SPD +{reward['speed_increase']}\n"
            
            await ctx.send(xp_message)
        else:
            await ctx.send("No XP rewards were calculated. This may be due to an error. Please contact an administrator.")

async def setup(bot):
    await bot.add_cog(FightCommands(bot))