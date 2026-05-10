import discord
from discord.ext import commands
import mysql.connector
from mysql.connector import Error
import asyncio
from PIL import Image, ImageDraw, ImageFont
import random
from utils.database import create_db_connection, execute_query
import io
import os
import time
import traceback
class SquadCommands(commands.Cog):
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

    async def gradient_color(self, percent):
        return tuple(int(start + (end - start) * percent) for start, end in zip(await self.gradient_start, await self.gradient_end))

    async def draw_gradient_text(self, draw, text, position, font, width):
        for i, char in enumerate(text):
            percent = i / len(text)
            color = await self.gradient_color(percent)
            bbox = font.getbbox(char)
            char_width = bbox[2] - bbox[0]
            draw.text((position[0], position[1]), char, font=font, fill=color)
            position = (position[0] + char_width, position[1])

    async def get_rank_color(self, cardrank_id):
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
    @commands.command(name="duoinv")
    async def duo_invite(self, ctx, target_user: discord.Member):
        connection =  await create_db_connection()
        if connection is None:
            await ctx.send("Error: Unable to connect to the database.")
            return
        # Get user_id of command user
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        user_squad_id = user_result[0]['squad_id']

        # Get user_id of target user
        target_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        target_result = await execute_query(connection, target_query, (str(target_user.id),))

        if not target_result:
            await ctx.send("The targeted user is not registered in the bot.")
            return

        target_id = target_result[0]['user_id']
        target_squad_id = target_result[0]['squad_id']

        if target_squad_id:
            await ctx.send("The targeted user is already in a squad.")
            return

        # Confirmation message
        confirm_msg = await ctx.send(f"{target_user.mention}, do you want to form a duo squad with {ctx.author.mention}?")
        await confirm_msg.add_reaction('✅')
        await confirm_msg.add_reaction('❌')

        def check(reaction, user):
            return user == target_user and str(reaction.emoji) in ['✅', '❌']

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("Invitation timed out.")
            return
        else:
            if str(reaction.emoji) == '❌':
                await ctx.send("Invitation declined.")
                return

        if not user_squad_id:
            # Create new squad
            create_squad_query = "INSERT INTO squad (user_id, squad_quantiter, squad_type) VALUES (%s, 2, 0)"
            await execute_query(connection, create_squad_query, (user_id,))

            get_squad_id_query = "SELECT squad_id FROM squad WHERE user_id = %s"
            squad_result = await execute_query(connection, get_squad_id_query, (user_id,))
            squad_id = squad_result[0]['squad_id']

            # Update both users' squad_id
            update_user_query = "UPDATE user SET squad_id = %s WHERE user_id IN (%s, %s)"
            await execute_query(connection, update_user_query, (squad_id, user_id, target_id))

            await ctx.send(f"Duo squad created with {ctx.author.mention} and {target_user.mention}!")
        else:
            # Check if user is squad leader
            squad_leader_query = "SELECT user_id FROM squad WHERE squad_id = %s"
            squad_leader_result = await execute_query(connection, squad_leader_query, (user_squad_id,))
            
            if squad_leader_result[0]['user_id'] != user_id:
                await ctx.send("Only the squad leader can invite people.")
                return

            # Check squad size
            squad_size_query = "SELECT squad_quantiter FROM squad WHERE squad_id = %s"
            squad_size_result = await execute_query(connection, squad_size_query, (user_squad_id,))
            
            if squad_size_result[0]['squad_quantiter'] >= 2:
                await ctx.send("The squad is already at maximum capacity.")
                return

            # Add target user to squad
            update_target_query = "UPDATE user SET squad_id = %s WHERE user_id = %s"
            await execute_query(connection, update_target_query, (user_squad_id, target_id))

            # Increase squad size
            update_squad_query = "UPDATE squad SET squad_quantiter = squad_quantiter + 1 WHERE squad_id = %s"
            await execute_query(connection, update_squad_query, (user_squad_id,))

            await ctx.send(f"{target_user.mention} has joined {ctx.author.mention}'s squad!")

        connection.close()

    @commands.command(name="squadinv")
    async def squad_invite(self, ctx, target_user: discord.Member):
        connection = await create_db_connection()
        if connection is None:
            await ctx.send("Error: Unable to connect to the database.")
            return
        # Get user_id of command user
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        user_squad_id = user_result[0]['squad_id']

        # Get user_id of target user
        target_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        target_result = await execute_query(connection, target_query, (str(target_user.id),))

        if not target_result:
            await ctx.send("The targeted user is not registered in the bot.")
            return

        target_id = target_result[0]['user_id']
        target_squad_id = target_result[0]['squad_id']

        if target_squad_id:
            await ctx.send("The targeted user is already in a squad.")
            return

        # Confirmation message
        confirm_msg = await ctx.send(f"{target_user.mention}, do you want to join {ctx.author.mention}'s squad?")
        await confirm_msg.add_reaction('✅')
        await confirm_msg.add_reaction('❌')

        def check(reaction, user):
            return user == target_user and str(reaction.emoji) in ['✅', '❌']

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("Invitation timed out.")
            return
        else:
            if str(reaction.emoji) == '❌':
                await ctx.send("Invitation declined.")
                return

        if not user_squad_id:
            # Create new squad
            create_squad_query = "INSERT INTO squad (user_id, squad_quantiter, squad_type) VALUES (%s, 2, 1)"
            await execute_query(connection, create_squad_query, (user_id,))

            get_squad_id_query = "SELECT squad_id FROM squad WHERE user_id = %s"
            squad_result = await execute_query(connection, get_squad_id_query, (user_id,))
            squad_id = squad_result[0]['squad_id']

            # Update both users' squad_id
            update_user_query = "UPDATE user SET squad_id = %s WHERE user_id IN (%s, %s)"
            await execute_query(connection, update_user_query, (squad_id, user_id, target_id))

            await ctx.send(f"Squad created with {ctx.author.mention} and {target_user.mention}!")
        else:
            # Check if user is squad leader
            squad_leader_query = "SELECT user_id FROM squad WHERE squad_id = %s"
            squad_leader_result = await execute_query(connection, squad_leader_query, (user_squad_id,))
            
            if squad_leader_result[0]['user_id'] != user_id:
                await ctx.send("Only the squad leader can invite people.")
                return

            # Check squad size
            squad_size_query = "SELECT squad_quantiter FROM squad WHERE squad_id = %s"
            squad_size_result = await execute_query(connection, squad_size_query, (user_squad_id,))
            
            if squad_size_result[0]['squad_quantiter'] >= 4:
                await ctx.send("The squad is already at maximum capacity.")
                return

            # Add target user to squad
            update_target_query = "UPDATE user SET squad_id = %s WHERE user_id = %s"
            await execute_query(connection, update_target_query, (user_squad_id, target_id))

            # Increase squad size
            update_squad_query = "UPDATE squad SET squad_quantiter = squad_quantiter + 1 WHERE squad_id = %s"
            await execute_query(connection, update_squad_query, (user_squad_id,))

            await ctx.send(f"{target_user.mention} has joined {ctx.author.mention}'s squad!")

        connection.close()

    @commands.command(name="leavesquad",aliases=['ls'])
    async def leave_squad(self, ctx):
        connection = await create_db_connection()
        if connection is None:
            await ctx.send("Error: Unable to connect to the database.")
            return

        # Get user_id and squad_id
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']

        if not squad_id:
            await ctx.send("You are not in a squad.")
            return

        # Get squad owner and type
        owner_query = "SELECT user_id, squad_type FROM squad WHERE squad_id = %s"
        owner_result = await execute_query(connection, owner_query, (squad_id,))
        owner_id = owner_result[0]['user_id']
        squad_type = owner_result[0]['squad_type']

        if user_id == owner_id:
            # [Previous owner logic remains the same]
            # Check squad size
            size_query = "SELECT squad_quantiter FROM squad WHERE squad_id = %s"
            size_result = await execute_query(connection, size_query, (squad_id,))
            squad_size = size_result[0]['squad_quantiter']

            if squad_size == 1:
                if squad_type == 0:  # Check squad composition for mixed cards
                    # Get user's cards in squad
                    cards_query = """
                    SELECT sc.card_id, c.type_card 
                    FROM squad_cards sc
                    JOIN card c ON sc.card_id = c.card_id
                    WHERE sc.squad_id = %s AND sc.user_id = %s
                    """
                    cards_result = await execute_query(connection, cards_query, (squad_id, user_id))
                    
                    has_fighter = any(card['type_card'] == 'Fighter' for card in cards_result)
                    has_support = any(card['type_card'] == 'Support' for card in cards_result)

                    if has_fighter and has_support:
                        # Handle mixed card composition
                        for card in cards_result:
                            if card['type_card'] == 'Support':
                                # Get support card details
                                support_query = """
                                SELECT c.*, uc.weapon_id, uc.armor_id, uc.shield_id
                                FROM card c
                                JOIN user_cards uc ON c.card_id = uc.card_id
                                WHERE c.card_id = %s AND uc.user_id = %s
                                """
                                support_card_results = await execute_query(connection, support_query, (card['card_id'], user_id))
                                if support_card_results:
                                    support_card = support_card_results[0]
                                    print(f"Support Card Query Result: {support_card}")
                                else:
                                    # Handle the case where no support card is found
                                    print("No support card found for the given card_id")
                                # Get fighter cards
                                fighter_query = """
                                SELECT sc.user_id, sc.card_id
                                FROM squad_cards sc
                                JOIN card c ON sc.card_id = c.card_id
                                WHERE sc.squad_id = %s AND c.type_card = 'Fighter'
                                """
                                fighters = await execute_query(connection, fighter_query, (squad_id,))

                                # Remove support effects from fighters
                                for fighter in fighters:
                                    await self.remove_support_effects(connection, support_card, fighter['user_id'], fighter['card_id'], squad_id)

                # Delete squad cards
                delete_cards_query = "DELETE FROM squad_cards WHERE squad_id = %s"
                await execute_query(connection, delete_cards_query, (squad_id,))

                # Delete squad
                delete_squad_query = "DELETE FROM squad WHERE squad_id = %s"
                await execute_query(connection, delete_squad_query, (squad_id,))

                # Update user
                update_user_query = "UPDATE user SET squad_id = NULL WHERE user_id = %s"
                await execute_query(connection, update_user_query, (user_id,))

                await ctx.send("You have left and disbanded the squad as you were the last member.")
            else:
                await ctx.send("You are the squad leader. You can't leave unless no one is left in the group or if you use 'sl purgesquad'.")
        # This would replace the non-owner section in the leave_squad command
        # This would replace the non-owner section in the leave_squad command
        else:
            print(f"User {user_id} is leaving squad {squad_id}")
            
            # Get user's cards in squad before deletion
            cards_query = """
            SELECT sc.card_id, c.type_card 
            FROM squad_cards sc
            JOIN card c ON sc.card_id = c.card_id
            WHERE sc.squad_id = %s AND sc.user_id = %s
            """
            leaving_cards = await execute_query(connection, cards_query, (squad_id, user_id))
            print(f"Found {len(leaving_cards)} cards leaving the squad")

            # Process support cards first
            for card in leaving_cards:
                if card['type_card'] == 'Support':
                    print(f"Processing leaving support card {card['card_id']}")
                    # Get support card details
                    support_query = """
                    SELECT c.*, uc.weapon_id, uc.armor_id, uc.shield_id
                    FROM card c
                    JOIN user_cards uc ON c.card_id = uc.card_id
                    WHERE c.card_id = %s AND uc.user_id = %s
                    """
                    support_card_results = await execute_query(connection, support_query, (card['card_id'], user_id))
                    if support_card_results:
                        support_card = support_card_results[0]
                        print(f"Support Card Query Result: {support_card}")
                    else:
                        # Handle the case where no support card is found
                        print("No support card found for the given card_id")
                    # Get all fighter cards in squad EXCEPT the ones leaving
                    fighter_query = """
                    SELECT sc.user_id, sc.card_id
                    FROM squad_cards sc
                    JOIN card c ON sc.card_id = c.card_id
                    WHERE sc.squad_id = %s 
                    AND c.type_card = 'Fighter'
                    """
                    fighters = await execute_query(connection, fighter_query, (squad_id,))
                    print(fighters)
                    print(f"Removing support effects from {len(fighters)} remaining fighters")

                    # Remove support effects from all remaining fighters
                    for fighter in fighters:
                        await self.remove_support_effects(connection, support_card, fighter['user_id'], fighter['card_id'], squad_id)
                    
                    # Remove this support card from squad_cards immediately
                    delete_card_query = "DELETE FROM squad_cards WHERE squad_id = %s AND user_id = %s AND card_id = %s"
                    await execute_query(connection, delete_card_query, (squad_id, user_id, card['card_id']))
                    print(f"Removed support card {card['card_id']} from squad_cards")

            # Then process fighter cards
            for card in leaving_cards:
                if card['type_card'] == 'Fighter':
                    print(f"Processing leaving fighter card {card['card_id']}")
                    # Get remaining support cards in squad (excluding the ones that just left)
                    support_query = """
                    SELECT c.*, uc.weapon_id, uc.armor_id, uc.shield_id, sc.user_id
                    FROM squad_cards sc
                    JOIN card c ON sc.card_id = c.card_id
                    JOIN user_cards uc ON sc.card_id = uc.card_id AND sc.user_id = uc.user_id
                    WHERE sc.squad_id = %s 
                    AND c.type_card = 'Support'
                    """
                    support_cards = await execute_query(connection, support_query, (squad_id,))
                    print(support_cards)
                    print(f"Removing effects of {len(support_cards)} remaining support cards")

                    # Remove effects of each remaining support card from the fighter
                    for support in support_cards:
                        await self.remove_support_effects(connection, support, user_id, card['card_id'], squad_id)
                    
                    # Remove this fighter card from squad_cards immediately
                    delete_card_query = "DELETE FROM squad_cards WHERE squad_id = %s AND user_id = %s AND card_id = %s"
                    await execute_query(connection, delete_card_query, (squad_id, user_id, card['card_id']))
                    print(f"Removed fighter card {card['card_id']} from squad_cards")

            # Decrease squad size
            update_squad_query = "UPDATE squad SET squad_quantiter = squad_quantiter - 1 WHERE squad_id = %s"
            await execute_query(connection, update_squad_query, (squad_id,))

            # Update user
            update_user_query = "UPDATE user SET squad_id = NULL WHERE user_id = %s"
            await execute_query(connection, update_user_query, (user_id,))

            await ctx.send("You have left the squad.")

        connection.close()

    async def remove_support_effects(self, connection, support_card, fighter_user_id, fighter_card_id, squad_id):
        # Handle equipment effects
        equipment_types = [
            ('weapon', support_card['weapon_id']),
            ('armor', support_card['armor_id']),
            ('shield', support_card['shield_id'])
        ]

        for eq_type, eq_id in equipment_types:
            if eq_id:
                query = f"""
                SELECT attack_boost, health_boost, speed_boost, defense_boost,
                    attack_percentage, health_percentage, 
                    speed_percentage, defense_percentage
                FROM {eq_type}
                WHERE {eq_type}_id = %s
                """
                eq_details = await execute_query(connection, query, (eq_id,))

                if eq_details:
                    eq_details = eq_details[0]
                    # Remove percentage boosts
                    query = """
                    UPDATE user_cards uc
                    JOIN squad_cards sc ON uc.card_id = sc.card_id
                    SET 
                        uc.attack_stat = uc.attack_stat / (1 + %s/100),
                        uc.health_stat = uc.health_stat / (1 + %s/100),
                        uc.speed_stat = uc.speed_stat / (1 + %s/100),
                        uc.defence_stat = uc.defence_stat / (1 + %s/100)
                    WHERE 
                        sc.squad_id = %s AND 
                        uc.user_id = %s AND 
                        uc.card_id = %s
                    """
                    await execute_query(connection, query, (
                        eq_details.get('attack_percentage', 0),
                        eq_details.get('health_percentage', 0),
                        eq_details.get('speed_percentage', 0),
                        eq_details.get('defense_percentage', 0),
                        squad_id,
                        fighter_user_id,
                        fighter_card_id
                    ))
                    
                    # Remove flat boosts
                    query = """
                    UPDATE user_cards uc
                    JOIN squad_cards sc ON uc.card_id = sc.card_id
                    SET 
                        uc.attack_stat = uc.attack_stat - %s,
                        uc.health_stat = uc.health_stat - %s,
                        uc.speed_stat = uc.speed_stat - %s,
                        uc.defence_stat = uc.defence_stat - %s
                    WHERE 
                        sc.squad_id = %s AND 
                        uc.user_id = %s AND 
                        uc.card_id = %s
                    """
                    await execute_query(connection, query, (
                        eq_details.get('attack_boost', 0),
                        eq_details.get('health_boost', 0),
                        eq_details.get('speed_boost', 0),
                        eq_details.get('defense_boost', 0),
                        squad_id,
                        fighter_user_id,
                        fighter_card_id
                    ))

        # Remove support card's own boosts
        query = """
        UPDATE user_cards uc
        JOIN squad_cards sc ON uc.card_id = sc.card_id
        SET 
            uc.attack_stat = uc.attack_stat / (1 + %s/100),
            uc.health_stat = uc.health_stat / (1 + %s/100),
            uc.speed_stat = uc.speed_stat / (1 + %s/100),
            uc.defence_stat = uc.defence_stat / (1 + %s/100)
        WHERE 
            sc.squad_id = %s AND 
            uc.user_id = %s AND 
            uc.card_id = %s
        """
        await execute_query(connection, query, (
            support_card['attack_percentage'],
            support_card['health_percentage'],
            support_card['speed_percentage'],
            support_card['defence_percentage'],
            squad_id,
            fighter_user_id,
            fighter_card_id
        ))

        query = """
        UPDATE user_cards uc
        JOIN squad_cards sc ON uc.card_id = sc.card_id
        SET 
            uc.attack_stat = uc.attack_stat - %s,
            uc.health_stat = uc.health_stat - %s,
            uc.speed_stat = uc.speed_stat - %s,
            uc.defence_stat = uc.defence_stat - %s
        WHERE 
            sc.squad_id = %s AND 
            uc.user_id = %s AND 
            uc.card_id = %s
        """
        await execute_query(connection, query, (
            support_card['attack'],
            support_card['health'],
            support_card['speed'],
            support_card['defence'],
            squad_id,
            fighter_user_id,
            fighter_card_id
        ))

    @commands.command(name="purgesquad",aliases=['pgs'])
    async def purge_squad(self, ctx):
        print("Starting purge_squad command")
        connection = await create_db_connection()
        print("hi")
        if connection is None:
            await ctx.send("Error: Unable to connect to the database.")
            return

        # Get user_id and squad_id
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))
        print(user_result)
        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']

        if not squad_id:
            await ctx.send("You are not in a squad.")
            return

        # Check if user is squad owner
        owner_query = "SELECT user_id FROM squad WHERE squad_id = %s"
        owner_result = await execute_query(connection, owner_query, (squad_id,))
        
        if owner_result[0]['user_id'] != user_id:
            await ctx.send("Only the squad leader can purge the squad.")
            return

        # Confirmation message
        confirm_msg = await ctx.send("Are you sure you want to purge your squad? This action cannot be undone.")
        await confirm_msg.add_reaction('✅')
        await confirm_msg.add_reaction('❌')

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ['✅', '❌']

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("Purge squad command timed out.")
            return
        else:
            if str(reaction.emoji) == '❌':
                await ctx.send("Purge squad command cancelled.")
                return

        print("Purge confirmed, checking for support cards")
        
        # Get user's cards in squad before deletion
        cards_query = """
        SELECT sc.card_id, c.type_card 
        FROM squad_cards sc
        JOIN card c ON sc.card_id = c.card_id
        WHERE sc.squad_id = %s
        """
        squad_cards = await execute_query(connection, cards_query, (squad_id,))
        print(f"Found {len(squad_cards)} cards in squad")

        # Process support cards first
        support_cards = [card for card in squad_cards if card['type_card'] == 'Support']
        if support_cards:
            print(f"Processing {len(support_cards)} support cards")
            for support_card in support_cards:
                print(f"Processing support card {support_card['card_id']}")
                
                # Get support card details
                support_query = """
                SELECT c.*, uc.weapon_id, uc.armor_id, uc.shield_id
                FROM card c
                JOIN user_cards uc ON c.card_id = uc.card_id
                WHERE c.card_id = %s
                """
                support_details_result = await execute_query(connection, support_query, (support_card['card_id'],))
                if support_details_result:
                    support_details = support_details_result[0]
                # Get all fighter cards
                fighter_query = """
                SELECT sc.user_id, sc.card_id
                FROM squad_cards sc
                JOIN card c ON sc.card_id = c.card_id
                WHERE sc.squad_id = %s AND c.type_card = 'Fighter'
                """
                fighters = await execute_query(connection, fighter_query, (squad_id,))
                print(f"Removing support effects from {len(fighters)} fighters")

                # Remove support effects from all fighters
                for fighter in fighters:
                    await self.remove_support_effects(connection, support_details, fighter['user_id'], fighter['card_id'], squad_id)

        print("Starting squad deletion process")
        
        # Delete squad cards
        delete_cards_query = "DELETE FROM squad_cards WHERE squad_id = %s"
        await execute_query(connection, delete_cards_query, (squad_id,))

        # Delete squad
        delete_squad_query = "DELETE FROM squad WHERE squad_id = %s"
        await execute_query(connection, delete_squad_query, (squad_id,))

        # Update all users in the squad
        update_users_query = "UPDATE user SET squad_id = NULL WHERE squad_id = %s"
        await execute_query(connection, update_users_query, (squad_id,))

        await ctx.send("Squad has been purged and all members have been removed.")

        connection.close()
    @commands.command(name="transfersquadownership",aliases=['tso','ts'])
    async def transfer_squad_ownership(self, ctx, target_user: discord.Member):
        connection = await create_db_connection()
        if connection is None:
            await ctx.send("Error: Unable to connect to the database.")
            return

        # Get user_id and squad_id of command user
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']

        if not squad_id:
            await ctx.send("You are not in a squad.")
            return

        # Get user_id and squad_id of target user
        target_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        target_result = await execute_query(connection, target_query, (str(target_user.id),))

        if not target_result:
            await ctx.send("The targeted user is not registered in the bot.")
            return

        target_id = target_result[0]['user_id']
        target_squad_id = target_result[0]['squad_id']

        # Check if user is squad owner
        owner_query = "SELECT user_id FROM squad WHERE squad_id = %s"
        owner_result = await execute_query(connection, owner_query, (squad_id,))
        
        if owner_result[0]['user_id'] != user_id:
            await ctx.send("Only the squad leader can transfer ownership.")
            return

        if squad_id != target_squad_id:
            await ctx.send("You can't transfer ownership to a user who is not in your squad.")
            return

        # Confirmation message
        confirm_msg = await ctx.send(f"Are you sure you want to transfer squad ownership to {target_user.mention}?")
        await confirm_msg.add_reaction('✅')
        await confirm_msg.add_reaction('❌')

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ['✅', '❌']

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("Transfer ownership command timed out.")
            return
        else:
            if str(reaction.emoji) == '❌':
                await ctx.send("Transfer ownership command cancelled.")
                return

        # Transfer ownership
        transfer_query = "UPDATE squad SET user_id = %s WHERE squad_id = %s"
        await execute_query(connection, transfer_query, (target_id, squad_id))

        await ctx.send(f"Squad ownership has been transferred to {target_user.mention}.")

        connection.close()

    @commands.command(name='addsquadcard', aliases=["asc", "squadadd"])
    async def add_squad_card(self, ctx, *, character_name):
        connection = await create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        # Get user_id and squad_id
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']

        if not squad_id:
            await ctx.send("You are not in a squad.")
            return

        # Look up the card with LIKE operator
        query = "SELECT * FROM card WHERE card_name LIKE %s"
        card_result = await execute_query(connection, query, (f"%{character_name}%",))
        print(card_result)
        if not card_result:
            await ctx.send(f"The card '{character_name}' doesn't exist.")
            connection.close()
            return
        
        if len(card_result) > 1:
            embed = discord.Embed(
                title="Multiple Cards Found",
                description="I found multiple cards matching your search. Please specify which one you'd like to add:",
                color=discord.Color.blue()
            )
            card_list = "\n".join([f"• {card['card_name']}" for card in card_result])
            embed.add_field(name="Matching Cards", value=card_list, inline=False)
            embed.set_footer(text="Please use the command again with the exact name of the card you want to add.")
            await ctx.send(embed=embed)
            return

        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']

        if card_type == "Booster":
            await ctx.send(f"'{character_name}' is a Booster card and cannot be added to your squad.")
            connection.close()
            return

        # Check if user owns the card
        query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
        user_card_result = await execute_query(connection, query, (user_id, card_id))

        if not user_card_result:
            await ctx.send(f"You don't own the card '{character_name}'.")
            connection.close()
            return

        # Check if card is in user's team
        query = "SELECT tc.* FROM team_cards tc JOIN team t ON tc.team_id = t.team_id WHERE t.user_id = %s AND tc.card_id = %s"
        team_card_result = await execute_query(connection, query, (user_id, card_id))

        if team_card_result:
            await ctx.send(f"'{character_name}' is in your team. Remove it from your team first before adding to squad.")
            connection.close()
            return

        # Check if card is already in squad
        query = "SELECT * FROM squad_cards WHERE squad_id = %s AND card_id = %s AND user_id = %s"
        squad_card_result = await execute_query(connection, query, (squad_id, card_id, user_id))

        if squad_card_result:
            await ctx.send(f"'{character_name}' is already in your squad team.")
            connection.close()
            return

        # Get squad type and check card limits
        squad_type_query = "SELECT squad_type FROM squad WHERE squad_id = %s"
        squad_type_result = await execute_query(connection, squad_type_query, (squad_id,))
        squad_type = squad_type_result[0]['squad_type']

        card_count_query = "SELECT COUNT(*) as count FROM squad_cards WHERE squad_id = %s AND user_id = %s"
        card_count_result = await execute_query(connection, card_count_query, (squad_id, user_id))
        card_count = card_count_result[0]['count']

        if squad_type == 0 and card_count >= 2:
            await ctx.send("You are in a duo team and have already added two cards. Remove a card to add a new one.")
            return
        elif squad_type == 1 and card_count >= 1:
            await ctx.send("You are in a squad and have already added one card. Remove the card to add a new one.")
            return

        position_query = "SELECT MAX(position) as max_position FROM squad_cards WHERE squad_id = %s"
        max_position_result = await execute_query(connection, position_query, (squad_id,))
        new_position = (max_position_result[0]['max_position'] or 0) + 1
        
        # Add card to squad
        add_card_query = "INSERT INTO squad_cards (squad_id, user_id, card_id, position) VALUES (%s, %s, %s, %s)"
        await execute_query(connection, add_card_query, (squad_id, user_id, card_id, new_position))

        # When a Fighter card is added
        if card_type == "Fighter":
            # Get all Support cards in the squad (from all users)
            query = """
            SELECT c.card_id, c.type_card, c.attack, c.health, c.speed, c.defence, 
                c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id, sc.user_id
            FROM squad_cards sc
            JOIN user_cards uc ON sc.card_id = uc.card_id AND sc.user_id = uc.user_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE sc.squad_id = %s AND c.type_card = 'Support'
            """
            support_cards = await execute_query(connection, query, (squad_id,))
            print(support_cards)
            for support_card in support_cards:
                # Flat stat boosting
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
                await execute_query(connection, query, (
                    support_card['attack'],
                    support_card['health'],
                    support_card['speed'],
                    support_card['defence'],
                    user_id,
                    card_id
                ))

                # Percentage stat boosting
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
                await execute_query(connection, query, (
                    support_card['attack_percentage'],
                    support_card['health_percentage'],
                    support_card['speed_percentage'],
                    support_card['defence_percentage'],
                    user_id,
                    card_id
                ))

                # Equipment boosting
                equipment_types = [
                    ('weapon', support_card['weapon_id']),
                    ('armor', support_card['armor_id']),
                    ('shield', support_card['shield_id'])
                ]

                for eq_type, eq_id in equipment_types:
                    if eq_id:
                        query = f"""
                        SELECT attack_boost, health_boost, speed_boost, defense_boost,
                            attack_percentage, health_percentage, 
                            speed_percentage, defense_percentage
                        FROM {eq_type}
                        WHERE {eq_type}_id = %s
                        """
                        eq_details = await execute_query(connection, query, (eq_id,))

                        if eq_details:
                            eq_details = eq_details[0]
                            # Flat equipment boosts
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
                            await execute_query(connection, query, (
                                eq_details.get('attack_boost', 0),
                                eq_details.get('health_boost', 0),
                                eq_details.get('speed_boost', 0),
                                eq_details.get('defense_boost', 0),
                                user_id,
                                card_id
                            ))

                            # Percentage equipment boosts
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
                            await execute_query(connection, query, (
                                eq_details.get('attack_percentage', 0),
                                eq_details.get('health_percentage', 0),
                                eq_details.get('speed_percentage', 0),
                                eq_details.get('defense_percentage', 0),
                                user_id,
                                card_id
                            ))

        # When a Support card is added
        if card_type == "Support":
            # Get the stats of the support card
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence, 
                c.attack_percentage, c.health_percentage, 
                c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM card c
            JOIN user_cards uc ON c.card_id = uc.card_id
            WHERE c.card_id = %s AND uc.user_id = %s
            """
            support_card_results = await execute_query(connection, query, (card_id, user_id))
            if support_card_results:
                support_card = support_card_results[0]
                print(f"Support Card Query Result: {support_card}")
            else:
                # Handle the case where no support card is found
                print("No support card found for the given card_id")
            # Get all fighter cards in the squad (from all users)
            query = """
            SELECT DISTINCT sc.card_id, sc.user_id
            FROM squad_cards sc
            JOIN user_cards uc ON sc.card_id = uc.card_id AND sc.user_id = uc.user_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE sc.squad_id = %s AND c.type_card = 'Fighter'
            """
            fighter_cards = await execute_query(connection, query, (squad_id,))

            for fighter in fighter_cards:
                fighter_user_id = fighter['user_id']
                fighter_card_id = fighter['card_id']

                # Update fighter cards with flat boosts
                query = """
                UPDATE user_cards
                SET 
                    attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    speed_stat = speed_stat + %s,
                    defence_stat = defence_stat + %s
                WHERE 
                    user_id = %s AND 
                    card_id = %s
                """
                await execute_query(connection, query, (
                    support_card['attack'],
                    support_card['health'],
                    support_card['speed'],
                    support_card['defence'],
                    fighter_user_id,
                    fighter_card_id
                ))

                # Update fighter cards with percentage boosts
                query = """
                UPDATE user_cards
                SET 
                    attack_stat = attack_stat * (1 + %s/100),
                    health_stat = health_stat * (1 + %s/100),
                    speed_stat = speed_stat * (1 + %s/100),
                    defence_stat = defence_stat * (1 + %s/100)
                WHERE 
                    user_id = %s AND 
                    card_id = %s
                """
                await execute_query(connection, query, (
                    support_card['attack_percentage'],
                    support_card['health_percentage'],
                    support_card['speed_percentage'],
                    support_card['defence_percentage'],
                    fighter_user_id,
                    fighter_card_id
                ))

                # Equipment boosting
                equipment_types = [
                    ('weapon', support_card['weapon_id']),
                    ('armor', support_card['armor_id']),
                    ('shield', support_card['shield_id'])
                ]

                for eq_type, eq_id in equipment_types:
                    if eq_id:
                        query = f"""
                        SELECT attack_boost, health_boost, speed_boost, defense_boost,
                            attack_percentage, health_percentage, 
                            speed_percentage, defense_percentage
                        FROM {eq_type}
                        WHERE {eq_type}_id = %s
                        """
                        eq_details = await execute_query(connection, query, (eq_id,))

                        if eq_details:
                            eq_details = eq_details[0]
                            # Update fighter cards with equipment flat boosts
                            query = """
                            UPDATE user_cards
                            SET 
                                attack_stat = attack_stat + %s,
                                health_stat = health_stat + %s,
                                speed_stat = speed_stat + %s,
                                defence_stat = defence_stat + %s
                            WHERE 
                                user_id = %s AND 
                                card_id = %s
                            """
                            await execute_query(connection, query, (
                                eq_details.get('attack_boost', 0),
                                eq_details.get('health_boost', 0),
                                eq_details.get('speed_boost', 0),
                                eq_details.get('defense_boost', 0),
                                fighter_user_id,
                                fighter_card_id
                            ))

                            # Update fighter cards with equipment percentage boosts
                            query = """
                            UPDATE user_cards
                            SET 
                                attack_stat = attack_stat * (1 + %s/100),
                                health_stat = health_stat * (1 + %s/100),
                                speed_stat = speed_stat * (1 + %s/100),
                                defence_stat = defence_stat * (1 + %s/100)
                            WHERE 
                                user_id = %s AND 
                                card_id = %s"""
                            await execute_query(connection, query, (
                                eq_details.get('attack_percentage', 0),
                                eq_details.get('health_percentage', 0),
                                eq_details.get('speed_percentage', 0),
                                eq_details.get('defense_percentage', 0),
                                fighter_user_id,
                                fighter_card_id
                            ))

        # Calculate total stats for card
        query = """
        SELECT attack_stat, health_stat, speed_stat, defence_stat
        FROM user_cards
        WHERE user_id = %s AND card_id = %s
        """
        stats_result = await execute_query(connection, query, (user_id, card_id))
        if stats_result:
            stats = stats_result[0]
        
        await ctx.send(f"Successfully added {card_result[0]['card_icon']} {card_result[0]['card_name']} to your team")


        connection.close()

    @commands.command(name='removesquadcard', aliases=["rms", "sqrm"])
    async def remove_squad_card(self, ctx, *, character_name):
        connection = await create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_authorid = str(ctx.author.id)
        
        # Get user_id and squad_id
        query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            connection.close()
            return
        
        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']
        
        if not squad_id:
            await ctx.send("You don't have a squad yet.")
            connection.close()
            return
        
        # Look up the card in the card table
        query = "SELECT * FROM card WHERE card_name LIKE %s"
        card_result = await execute_query(connection, query, (f"%{character_name}%",))
        
        if not card_result:
            await ctx.send(f"The card '{character_name}' doesn't exist.")
            connection.close()
            return
        if len(card_result) > 1:
            embed = discord.Embed(
                title="Multiple Cards Found",
                description="I found multiple cards matching your search. Please specify which one you'd like to remove:",
                color=discord.Color.blue()
            )
            card_list = "\n".join([f"• {card['card_name']}" 
                                for card in card_result])
            embed.add_field(name="Matching Cards", value=card_list, inline=False)
            embed.set_footer(text="Please use the command again with the exact name of the card you want to remove.")
            await ctx.send(embed=embed)
            return
        
        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']
        card_stats = card_result[0]
        
        # Check if the card is in the squad and verify ownership
        query = """
        SELECT sc.position, sc.user_id 
        FROM squad_cards sc
        WHERE sc.squad_id = %s AND sc.card_id = %s
        """
        squad_card_result = await execute_query(connection, query, (squad_id, card_id))
        
        if not squad_card_result:
            await ctx.send(f"'{character_name}' is not in the squad team.")
            connection.close()
            return
            
        card_owner_id = squad_card_result[0]['user_id']
        if card_owner_id != user_id:
            await ctx.send(f"You cannot remove this card as it was added by another squad member.")
            connection.close()
            return
        
        removed_position = squad_card_result[0]['position']
        
        # Remove the card from the squad
        query = "DELETE FROM squad_cards WHERE squad_id = %s AND user_id = %s AND card_id = %s"
        await execute_query(connection, query, (squad_id, user_id, card_id))
        
        # Rearrange positions for the same user's cards
        query = """
        UPDATE squad_cards
        SET position = position - 1
        WHERE squad_id = %s 
        AND user_id = %s 
        AND position > %s
        AND position <= 4
        """
        await execute_query(connection, query, (squad_id, user_id, removed_position))
        
        if card_type == "Support":
            # Get the stats of the support card from card table
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence, 
                c.attack_percentage, c.health_percentage, 
                c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM card c
            JOIN user_cards uc ON c.card_id = uc.card_id
            WHERE c.card_id = %s AND uc.user_id = %s
            """
            support_card_results = await execute_query(connection, query, (card_id, user_id))
            if support_card_results:
                support_card = support_card_results[0]
                print(f"Support Card Query Result: {support_card}")
            else:
                # Handle the case where no support card is found
                print("No support card found for the given card_id")
            # Get all fighter cards in the squad for all users
            query = """
            SELECT DISTINCT sc.user_id, uc.card_id
            FROM squad_cards sc
            JOIN user_cards uc ON sc.card_id = uc.card_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE sc.squad_id = %s AND c.type_card = 'Fighter'
            """
            fighter_cards = await execute_query(connection, query, (squad_id,))

            if fighter_cards:
                # Equipment boosting
                equipment_types = [
                    ('weapon', support_card['weapon_id']),
                    ('armor', support_card['armor_id']),
                    ('shield', support_card['shield_id'])
                ]

                for fighter in fighter_cards:
                    fighter_user_id = fighter['user_id']
                    fighter_card_id = fighter['card_id']

                    for eq_type, eq_id in equipment_types:
                        if eq_id:
                            # Query equipment details
                            query = f"""
                            SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                attack_percentage, health_percentage, 
                                speed_percentage, defense_percentage
                            FROM {eq_type}
                            WHERE {eq_type}_id = %s
                            """
                            eq_details = await execute_query(connection, query, (eq_id,))

                            if eq_details:
                                eq_details = eq_details[0]
                                # Remove percentage boosts for this specific fighter
                                query = """
                                UPDATE user_cards uc
                                JOIN squad_cards sc ON uc.card_id = sc.card_id
                                SET 
                                    uc.attack_stat = uc.attack_stat / (1 + %s/100),
                                    uc.health_stat = uc.health_stat / (1 + %s/100),
                                    uc.speed_stat = uc.speed_stat / (1 + %s/100),
                                    uc.defence_stat = uc.defence_stat / (1 + %s/100)
                                WHERE 
                                    sc.squad_id = %s AND 
                                    uc.user_id = %s AND 
                                    uc.card_id = %s
                                """
                                await execute_query(connection, query, (
                                    eq_details.get('attack_percentage', 0),
                                    eq_details.get('health_percentage', 0),
                                    eq_details.get('speed_percentage', 0),
                                    eq_details.get('defense_percentage', 0),
                                    squad_id,
                                    fighter_user_id,
                                    fighter_card_id
                                ))
                                
                                # Remove flat boosts for this specific fighter
                                query = """
                                UPDATE user_cards uc
                                JOIN squad_cards sc ON uc.card_id = sc.card_id
                                SET 
                                    uc.attack_stat = uc.attack_stat - %s,
                                    uc.health_stat = uc.health_stat - %s,
                                    uc.speed_stat = uc.speed_stat - %s,
                                    uc.defence_stat = uc.defence_stat - %s
                                WHERE 
                                    sc.squad_id = %s AND 
                                    uc.user_id = %s AND 
                                    uc.card_id = %s
                                """
                                await execute_query(connection, query, (
                                    eq_details.get('attack_boost', 0),
                                    eq_details.get('health_boost', 0),
                                    eq_details.get('speed_boost', 0),
                                    eq_details.get('defense_boost', 0),
                                    squad_id,
                                    fighter_user_id,
                                    fighter_card_id
                                ))

                    # Remove support card's own boosts for this specific fighter
                    query = """
                    UPDATE user_cards uc
                    JOIN squad_cards sc ON uc.card_id = sc.card_id
                    SET 
                        uc.attack_stat = uc.attack_stat / (1 + %s/100),
                        uc.health_stat = uc.health_stat / (1 + %s/100),
                        uc.speed_stat = uc.speed_stat / (1 + %s/100),
                        uc.defence_stat = uc.defence_stat / (1 + %s/100)
                    WHERE 
                        sc.squad_id = %s AND 
                        uc.user_id = %s AND 
                        uc.card_id = %s
                    """
                    await execute_query(connection, query, (
                        support_card['attack_percentage'],
                        support_card['health_percentage'],
                        support_card['speed_percentage'],
                        support_card['defence_percentage'],
                        squad_id,
                        fighter_user_id,
                        fighter_card_id
                    ))
                    
                    query = """
                    UPDATE user_cards uc
                    JOIN squad_cards sc ON uc.card_id = sc.card_id
                    SET 
                        uc.attack_stat = uc.attack_stat - %s,
                        uc.health_stat = uc.health_stat - %s,
                        uc.speed_stat = uc.speed_stat - %s,
                        uc.defence_stat = uc.defence_stat - %s
                    WHERE 
                        sc.squad_id = %s AND 
                        uc.user_id = %s AND 
                        uc.card_id = %s
                    """
                    await execute_query(connection, query, (
                        support_card['attack'],
                        support_card['health'],
                        support_card['speed'],
                        support_card['defence'],
                        squad_id,
                        fighter_user_id,
                        fighter_card_id
                    ))
        
        elif card_type == "Fighter":
            # Get all Support cards in the squad
            query = """
            SELECT DISTINCT 
                c.card_id, 
                c.type_card, 
                c.attack, 
                c.health, 
                c.speed, 
                c.defence, 
                c.attack_percentage, 
                c.health_percentage, 
                c.speed_percentage, 
                c.defence_percentage,
                uc.weapon_id, 
                uc.armor_id, 
                uc.shield_id, 
                sc.user_id
            FROM squad_cards sc
            JOIN user_cards uc 
                ON sc.card_id = uc.card_id 
                AND sc.user_id = uc.user_id
            JOIN card c 
                ON sc.card_id = c.card_id
            WHERE sc.squad_id = %s 
                AND c.type_card = 'Support'
            GROUP BY 
                c.card_id, 
                c.type_card,
                c.attack,
                c.health,
                c.speed,
                c.defence,
                c.attack_percentage,
                c.health_percentage,
                c.speed_percentage,
                c.defence_percentage,
                uc.weapon_id,
                uc.armor_id,
                uc.shield_id,
                sc.user_id
            """
            support_cards = await execute_query(connection, query, (squad_id,))
            support_cards = await execute_query(connection, query, (squad_id,))
            print(support_cards)
            print("we start the removal")
            # Remove effects of each support card from the fighter being removed
            for support_card in support_cards:
                print("removing")
                support_user_id = support_card['user_id']
                
                # Equipment boosting
                equipment_types = [
                    ('weapon', support_card['weapon_id']),
                    ('armor', support_card['armor_id']),
                    ('shield', support_card['shield_id'])
                ]

                for eq_type, eq_id in equipment_types:
                    if eq_id:
                        print('access equipemnet')
                        query = f"""
                        SELECT attack_boost, health_boost, speed_boost, defense_boost,
                            attack_percentage, health_percentage, 
                            speed_percentage, defense_percentage
                        FROM {eq_type}
                        WHERE {eq_type}_id = %s
                        """
                        eq_details = await execute_query(connection, query, (eq_id,))

                        if eq_details:
                            eq_details = eq_details[0]
                            # Remove percentage boosts
                            query = """
                            UPDATE user_cards uc
                            SET 
                                uc.attack_stat = uc.attack_stat / (1 + %s/100),
                                uc.health_stat = uc.health_stat / (1 + %s/100),
                                uc.speed_stat = uc.speed_stat / (1 + %s/100),
                                uc.defence_stat = uc.defence_stat / (1 + %s/100)
                            WHERE 
                                uc.user_id = %s AND 
                                uc.card_id = %s
                            """
                            await execute_query(connection, query, (
                                eq_details.get('attack_percentage', 0),
                                eq_details.get('health_percentage', 0),
                                eq_details.get('speed_percentage', 0),
                                eq_details.get('defense_percentage', 0),
                                user_id,
                                card_id
                            ))
                            
                            # Remove flat boosts
                            query = """
                            UPDATE user_cards uc
                            SET 
                                uc.attack_stat = uc.attack_stat - %s,
                                uc.health_stat = uc.health_stat - %s,
                                uc.speed_stat = uc.speed_stat - %s,
                                uc.defence_stat = uc.defence_stat - %s
                            WHERE 
                                uc.user_id = %s AND 
                                uc.card_id = %s
                            """
                            await execute_query(connection, query, (
                                eq_details.get('attack_boost', 0),
                                eq_details.get('health_boost', 0),
                                eq_details.get('speed_boost', 0),
                                eq_details.get('defense_boost', 0),
                                user_id,
                                card_id
                            ))
                print("removing stats")
                # Remove support card's own boosts
                query = """
                UPDATE user_cards uc
                SET 
                    uc.attack_stat = uc.attack_stat / (1 + %s/100),
                    uc.health_stat = uc.health_stat / (1 + %s/100),
                    uc.speed_stat = uc.speed_stat / (1 + %s/100),
                    uc.defence_stat = uc.defence_stat / (1 + %s/100)
                WHERE 
                    uc.user_id = %s AND 
                    uc.card_id = %s
                """
                await execute_query(connection, query, (
                    support_card['attack_percentage'],
                    support_card['health_percentage'],
                    support_card['speed_percentage'],
                    support_card['defence_percentage'],
                    user_id,
                    card_id
                ))

                query = """
                UPDATE user_cards uc
                SET 
                    uc.attack_stat = uc.attack_stat - %s,
                    uc.health_stat = uc.health_stat - %s,
                    uc.speed_stat = uc.speed_stat - %s,
                    uc.defence_stat = uc.defence_stat - %s
                WHERE 
                    uc.user_id = %s AND 
                    uc.card_id = %s
                """
                await execute_query(connection, query, (
                    support_card['attack'],
                    support_card['health'],
                    support_card['speed'],
                    support_card['defence'],
                    user_id,
                    card_id
                ))

        # Verify final positions are correct (ensure no gaps)
        query = """
        SELECT position 
        FROM squad_cards 
        WHERE squad_id = %s AND user_id = %s 
        ORDER BY position
        """
        positions = await execute_query(connection, query, (squad_id, user_id))
        
        if positions:
            # Reorder positions to ensure they are sequential (1,2,3,4)
            for index, pos in enumerate(positions, start=1):
                if pos['position'] != index:
                    update_query = """
                    UPDATE squad_cards 
                    SET position = %s 
                    WHERE squad_id = %s 
                    AND user_id = %s 
                    AND position = %s
                    """
                    await execute_query(connection, update_query, (index, squad_id, user_id, pos['position']))

        await ctx.send(f"Successfully removed {card_result[0]['card_icon']} {card_result[0]['card_name']} from your squad team and rearranged positions!")
        connection.close()
    async def prepare_card_image(self, card, size):
        img = card['image']
        if card.get('pseudo_image') and card['pseudo_image'] != "":
            img = card['pseudo_image']
        card_image_path = os.path.join(self.image_path, img)
        if not os.path.exists(card_image_path):
            print(f"Warning: Card image not found at {card_image_path}")
            return None

        with Image.open(card_image_path) as img:
            img = img.convert('RGBA').resize(size, Image.LANCZOS)
            
        rank_color = await self.get_rank_color(card['card_rank'])
        return await self.add_border_and_radius(img, rank_color)

    async def add_border_and_radius(self, image, border_color):
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

    async def draw_gradient_text(self, draw, text, position, font, width):
        for i, char in enumerate(text):
            percent = i / len(text)
            color = await self.gradient_color(percent)
            bbox = font.getbbox(char)
            char_width = bbox[2] - bbox[0]
            draw.text((position[0], position[1]), char, font=font, fill=color)
            position = (position[0] + char_width, position[1])

    async def gradient_color(self, percent):
        return tuple(int(start + (end - start) * percent) for start, end in zip(self.gradient_start,  self.gradient_end))
#    @commands.command(name="squadteamview")
    async def squad_team_view(self, ctx):
        connection = await create_db_connection()
        if connection is None:
            await ctx.send("Error: Unable to connect to the database.")
            return

        # Get user_id and squad_id
        user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
        user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']
        squad_id = user_result[0]['squad_id']

        if not squad_id:
            await ctx.send("You are not in a squad.")
            return

        # Get squad cards
        squad_cards_query = """
        SELECT sc.user_id, sc.card_id, u.user_authorid, c.card_name, c.image,
               uc.current_level, uc.current_xp, c.xp_cap, w.weapon_name,
               uc.power_stat, uc.attack_stat, uc.health_stat, uc.speed_stat
        FROM squad_cards sc
        JOIN user u ON sc.user_id = u.user_id
        JOIN card c ON sc.card_id = c.card_id
        JOIN user_cards uc ON sc.user_id = uc.user_id AND sc.card_id = uc.card_id
        LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
        WHERE sc.squad_id = %s
        """
        squad_cards_result = await execute_query(connection, squad_cards_query, (squad_id,))

        if not squad_cards_result:
            await ctx.send("Your squad team is empty.")
            return

        embeds = []
        for card in squad_cards_result:
            embed = discord.Embed(title=f"{card['card_name']}", color=0x00ff00)
            embed.add_field(name="Level", value=card['current_level'], inline=True)
            embed.add_field(name="XP", value=f"{card['current_xp']}/{card['xp_cap']}", inline=False)
            embed.add_field(name="Weapon", value=card['weapon_name'] or "None", inline=False)
            embed.add_field(name="Power", value=card['power_stat'], inline=False)
            embed.add_field(name="Attack", value=card['attack_stat'], inline=False)
            embed.add_field(name="Health", value=card['health_stat'], inline=False)
            embed.add_field(name="Speed", value=card['speed_stat'], inline=False)
            embed.set_image(url=card['image'])
            owner = await self.bot.fetch_user(int(card['user_authorid']))
            embed.add_field(name="Owner", value=owner.name, inline=False)
            embed.set_thumbnail(url=owner.avatar.url)
            embeds.append(embed)

        current_page = 0
        message = await ctx.send(embed=embeds[current_page])

        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️"]

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60, check=check)

                if str(reaction.emoji) == "➡️" and current_page < len(embeds) - 1:
                    current_page += 1
                    await message.edit(embed=embeds[current_page])
                elif str(reaction.emoji) == "⬅️" and current_page > 0:
                    current_page -= 1
                    await message.edit(embed=embeds[current_page])

                await message.remove_reaction(reaction, user)

            except asyncio.TimeoutError:
                await message.clear_reactions()
                break

        connection.close()
    @commands.command(name='squadteamview', aliases=["stv"])
    async def squad_team_view(self, ctx):
        start_time = time.time()

        try:
            connection = await create_db_connection()
            if not connection:
                await ctx.send("Unable to connect to the database. Please try again later.")
                return

            # Get user_id and squad_id
            user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query(connection, user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return

            user_id = user_result[0]['user_id']
            squad_id = user_result[0]['squad_id']

            if not squad_id:
                await ctx.send("You are not in a squad.")
                return

            # Get squad cards with all necessary information
            squad_cards_query = """
            SELECT sc.user_id, sc.card_id, u.user_authorid, c.card_name, c.image,
                uc.current_level, uc.pseudo_image, c.card_rank, c.type_card,
                uc.power_stat, uc.attack_stat, uc.health_stat, uc.speed_stat
            FROM squad_cards sc
            JOIN user u ON sc.user_id = u.user_id
            JOIN card c ON sc.card_id = c.card_id
            JOIN user_cards uc ON sc.user_id = uc.user_id AND sc.card_id = uc.card_id
            WHERE sc.squad_id = %s
            ORDER BY sc.position
            """
            squad_cards = await execute_query(connection, squad_cards_query, (squad_id,))
            connection.close()

            if not squad_cards:
                await ctx.send("Your squad team is empty.")
                return

            # Use a default background or you could add a squad_bg column to the squad table
            background_path = os.path.join(self.image_path, "curr.png")
            if not os.path.exists(background_path):
                await ctx.send("Error: Background image not found.")
                return

            with Image.open(background_path).convert('RGBA').resize((1600, 900)) as background:
                draw = ImageDraw.Draw(background)

                # Same positioning as team command
                image_positions = [(0, 150), (399, 150), (800, 150), (1205, 150)]
                card_size = (380, 580)
                text_positions = [(50, 750), (450, 750), (850, 750), (1250, 750)]

                for i, card in enumerate(squad_cards[:4]):
                    card_image = await self.prepare_card_image(card, card_size)
                    if card_image:
                        background.paste(card_image, image_positions[i], card_image)
                    
                    # Draw card name
                    name_text = card['card_name']
                    name_bbox = self.font_small.getbbox(name_text)
                    name_width = name_bbox[2] - name_bbox[0]
                    name_position = (text_positions[i][0] + (300 - name_width) // 2, text_positions[i][1])
                    await self.draw_gradient_text(draw, name_text, name_position, self.font_small, name_width)

                    # Show level for non-Support cards
                    if card['type_card'] != 'Support':
                        level_text = f"Lv.{card['current_level']}"
                        level_bbox = self.poppins_font.getbbox(level_text)
                        level_width = level_bbox[2] - level_bbox[0]
                        level_position = (text_positions[i][0] + (300 - level_width) // 2, text_positions[i][1] + 90)
                        await self.draw_gradient_text(draw, level_text, level_position, self.poppins_font, level_width)

                # Draw title
                username = await self.bot.fetch_user(int(str(ctx.author.id)))
                title_text = f"{username}'s Squad"
                title_bbox = self.title_font.getbbox(title_text)
                title_width = title_bbox[2] - title_bbox[0]
                title_position = ((1600 - title_width) // 2, 20)  # Centered at the top
                await self.draw_gradient_text(draw, title_text, title_position, self.title_font, title_width)

                # Save and send image
                buffer = io.BytesIO()
                background.convert('RGB').save(buffer, format='JPEG', quality=85, optimize=True)
                buffer.seek(0)

                file_size = buffer.getbuffer().nbytes
                print(f"Generated squad image file size: {file_size} bytes")

                if file_size == 0:
                    await ctx.send("Error: Generated image is empty.")
                    return

                message_content = f"{ctx.author.mention} Here's your squad team:"
                await ctx.send(content=message_content, file=discord.File(buffer, filename="squad_view.jpg"))
                total_time = time.time() - start_time
                print(f"Total time: {total_time:.2f}s")

        except Exception as e:
            error_msg = f"An error occurred while generating the squad team image: {str(e)}\n"
            error_msg += "".join(traceback.format_tb(e.__traceback__))
            print(error_msg)
            await ctx.send("An error occurred. Please try again later or contact support.")
async def setup(bot):
    await bot.add_cog(SquadCommands(bot))