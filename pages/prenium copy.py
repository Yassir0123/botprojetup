import discord
from discord.ext import commands
from discord.ui import Select, View
import os
import datetime
import traceback
import logging
from PIL import Image, ImageDraw, ImageFont
import os
import asyncio
import io
import traceback
from utils.database import execute_query_with_retry

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='premium_store.log'
)
logger = logging.getLogger('PremiumStore')

class PreniumCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'storeimages')
        logger.info(f"PreniumCommands initialized. Image path: {self.image_path}")

    def validate_image_path(self, image_name):
        """
        Validate and log details about the image path.
        Returns the full path or None if invalid.
        """
        if not image_name:
            logger.warning("No image name provided")
            return None
        
        full_path = os.path.join(self.image_path, image_name)
        
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

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)
    DEFAULT_EMOTES = {
        'attack': '<:solosword:1302911919941746730>',
        'defense': '<:soloshield:1302911930242961461>',
        'health': '<:soloheart:1302912058819477514>',
        'shield': '<:soloshield:1302911930242961461>',
        'power': '<:solopower:1302936147139235882>',
        'type': '<:solotype:1302937991869759539>',
        'secondshield': '<:solosecondshield:1302959829413007380>',
        'class': '<:solorole:1302938003873726526>',
        'level': '<:sololevel:1302959449220321350>',
        'xp': '<:soloxp:1302960018747822080>',
        'armor': '<:armor64:1311994697794850897>',
        'weapon': '<:soloweapon:1302961287747342356>',
        'speed': '<:solostar:1302912104264761374>'
    }

    @commands.command("iedit", aliases=["ied","ie"])
    async def inventory_edit(self, ctx, *, args):
        try:
            # Check if user is registered
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            user_id = user_result[0]['user_id']

            # Split arguments
            args_split = args.split(',')
            if len(args_split) < 2:
                await ctx.send("Error: Please provide card name and anime card name.")
                return

            card_name = args_split[0].strip()
            anime_card_name = args_split[1].strip()

            # First, check if the user has the card in user_cards
            card_check_query = """
            SELECT * FROM user_cards 
            WHERE user_id = %s AND (name LIKE %s OR pseudo_name LIKE %s)
            """
            card_search_pattern = f"%{card_name}%"
            card_results = await execute_query_with_retry(card_check_query, (
                user_id, 
                card_search_pattern, 
                card_search_pattern
            ))

            # Handle multiple matches for the card
            if len(card_results) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to edit:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([
                    f"• {card.get('pseudo_name') or card['name']}" 
                    for card in card_results
                ])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                
                await ctx.send(embed=embed)
                return

            # If no card matches found
            if not card_results:
                await ctx.send(f"You don't have a card matching this name in your collection.")
                return

            # Get the specific card
            user_card = card_results[0]

            # Check if the card already has a pseudo image
            if user_card.get('pseudo_image'):
                await ctx.send(f"This card already has a pseudo image. Please remove the existing pseudo image before adding a new one.")
                return

            # Get inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("Error: No inventory found for this user.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Search for anime card in inventory_animecard with LIKE match
            animecard_query = """
            SELECT * FROM inventory_animecard 
            WHERE inventory_id = %s AND animecard_name LIKE %s
            """
            anime_search_pattern = f"%{anime_card_name}%"
            animecard_results = await execute_query_with_retry(animecard_query, (inventory_id, anime_search_pattern))

            # Handle multiple matches for anime card
            if len(animecard_results) > 1:
                embed = discord.Embed(
                    title="Multiple Anime Cards Found",
                    description="I found multiple anime cards matching your search. Please specify which one you'd like to edit:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['animecard_name']}" for card in animecard_results])
                embed.add_field(name="Matching Anime Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact anime card name.")
                
                await ctx.send(embed=embed)
                return

            # If no matches found for anime card
            if not animecard_results:
                await ctx.send(f"You don't have an anime card matching this name in your inventory.")
                return

            # Get the image from the matched anime card
            anime_card = animecard_results[0]
            anime_card_image = anime_card['image']
            anime_card_quantiter = anime_card['quantiter']
            aname = anime_card['animecard_name']
            print(anime_card_quantiter)

            # Update user_cards pseudo_image for the specific card
            update_query = """
            UPDATE user_cards 
            SET pseudo_image = %s 
            WHERE user_id = %s AND name = %s
            """
            await execute_query_with_retry(update_query, (
                anime_card_image, 
                user_id, 
                user_card['name']
            ))

            # Handle inventory_animecard quantiter
            if anime_card_quantiter == 1:
                # Delete the row if quantiter is 1
                delete_query = """
                DELETE FROM inventory_animecard 
                WHERE inventory_id = %s AND animecard_name = %s AND image = %s
                """
                await execute_query_with_retry(delete_query, (inventory_id, aname, anime_card_image))
            else:
                # Decrement quantiter
                update_quantiter_query = """
                UPDATE inventory_animecard 
                SET quantiter = quantiter - 1 
                WHERE inventory_id = %s AND animecard_name = %s AND image = %s
                """
                await execute_query_with_retry(update_quantiter_query, (inventory_id, aname, anime_card_image))

            await ctx.send(f"Successfully updated image for card '{card_name}'.")

        except Exception as e:
            print(f"Error in inventory_edit: {e}")
            await ctx.send("An error occurred while processing your request.")

    @commands.command("imremove", aliases=["imr","ire"])
    async def inventory_remove(self, ctx, *, card_name):
        try:
            # Check if user is registered
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return

            user_id = user_result[0]['user_id']

            # Find the current pseudo_image before removing
            find_image_query = """
            SELECT pseudo_image 
            FROM user_cards 
            WHERE user_id = %s AND (name LIKE %s OR pseudo_name LIKE %s)
            """
            search_pattern = f"%{card_name}%"
            image_result = await execute_query_with_retry(find_image_query, (user_id, search_pattern, search_pattern))

            if not image_result or not image_result[0]['pseudo_image']:
                await ctx.send(f"No custom image found for card '{card_name}'.")
                return

            pseudo_image = image_result[0]['pseudo_image']

            # Find the animecard_name from the animecard table
            animecard_query = """
            SELECT *
            FROM animecard 
            WHERE image = %s
            """
            animecard_result = await execute_query_with_retry(animecard_query, (pseudo_image,))

            if not animecard_result:
                await ctx.send("Could not find the corresponding anime card.")
                return

            animecard_name = animecard_result[0]['animecard_name']
            animecard_id = animecard_result[0]['animecard_id']

            # Get inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("Error: No inventory found for this user.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check if the anime card exists in inventory_animecard
            inventory_animecard_query = """
            SELECT * FROM inventory_animecard 
            WHERE inventory_id = %s AND animecard_name = %s AND image = %s
            """
            inventory_animecard_result = await execute_query_with_retry(
                inventory_animecard_query, 
                (inventory_id, animecard_name, pseudo_image)
            )

            if inventory_animecard_result:
                # Increment quantiter if exists
                update_quantiter_query = """
                UPDATE inventory_animecard 
                SET quantiter = quantiter + 1 
                WHERE inventory_id = %s AND animecard_name = %s AND image = %s
                """
                await execute_query_with_retry(
                    update_quantiter_query, 
                    (inventory_id, animecard_name, pseudo_image)
                )
            else:
                # Insert new row if not exists
                insert_query = """
                INSERT INTO inventory_animecard 
                (inventory_id, animecard_id, animecard_name, image, quantiter) 
                VALUES (%s, %s, %s, %s, 1)
                """
                await execute_query_with_retry(
                    insert_query, 
                    (inventory_id, animecard_id ,animecard_name, pseudo_image)
                )

            # Rsemove the pseudo_image from user_cards
            update_query = """
            UPDATE user_cards 
            SET pseudo_image = '' 
            WHERE user_id = %s AND (name LIKE %s OR pseudo_name LIKE %s)
            """
            await execute_query_with_retry(update_query, (
                user_id, 
                search_pattern, 
                search_pattern
            ))

            await ctx.send(f"Successfully removed custom image for card '{card_name}'.")

        except Exception as e:
            print(f"Error in inventory_remove: {e}")
            await ctx.send("An error occurred while processing your request.")
    @commands.command(name='nedit', aliases=["ned","ne"])
    async def sl_nedit(self, ctx, *, args=None):
        try:
            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # Check if arguments are provided
            if not args:
                embed = discord.Embed(
                    title="Missing Arguments",
                    description="Please provide the card name and new name. Usage: `sl nedit <card name>, <new name>`",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            # Check if both card name and new name are provided
            if len(parts) < 2:
                embed = discord.Embed(
                    title="Incorrect Format",
                    description="Please use the format: `sl nedit <card name>, <new name>`\nEnsure you have a comma separating the card name and new name.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            card_name, new_name = parts[0], parts[1]

            # First, check user eligibility
            query = """
            SELECT user_id, prenium, firsttier, secondtier 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(query, (discord_id,))

            # Check if user is registered
            if not user_data_list:
                embed = discord.Embed(
                    title="Registration Required",
                    description="You need to register first. Use `sl register`.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_data = user_data_list[0]

            # Check premium/tier eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                embed = discord.Embed(
                    title="Access Denied",
                    description="You need to be a premium member or in a tier to use this command. Use `sl patreon` to learn more.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Search for the card in user_cards
            search_query = """
            SELECT * FROM user_cards 
            WHERE user_id = %s AND name LIKE %s
            """
            card_results = await execute_query_with_retry(search_query, (user_data['user_id'], f'%{card_name}%'))

            # Handle multiple card matches
            if len(card_results) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to edit:",
                    color=discord.Color.blue()
                )
                
                card_list = "\n".join([f"• {card['name']} (from {card.get('anime_name', 'Unknown Anime')})" 
                                        for card in card_results])
                
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to edit.")
                
                await ctx.send(embed=embed)
                return

            # No cards found
            if not card_results:
                embed = discord.Embed(
                    title="Card Not Found",
                    description=f"No card matching this name found in your inventory.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Get the specific card
            card = card_results[0]

            # Update the pseudo name in the database
            update_query = """
            UPDATE user_cards 
            SET pseudo_name = %s 
            WHERE id_ligneusercards = %s
            """
            await execute_query_with_retry(update_query, (new_name, card['id_ligneusercards']))

            embed = discord.Embed(
                title="Card Name Updated",
                description=f"Card name successfully set to '{new_name}'.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in sl nedit: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Error",
                description="An unexpected error occurred.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    @commands.command(name='nremove', aliases=["nrm","nr"])
    async def sl_nremove(self, ctx, *, card_name=None):
        try:
            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # First, check user eligibility (similar to nedit)
            query = """
            SELECT user_id, prenium, firsttier, secondtier 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(query, (discord_id,))

            # Check if user is registered
            if not user_data_list:
                embed = discord.Embed(
                    title="Registration Required",
                    description="You need to register first. Use `sl register`.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_data = user_data_list[0]

            # Check premium/tier eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                embed = discord.Embed(
                    title="Access Denied",
                    description="You need to be a premium member or in a tier to use this command. Use `sl patreon` to learn more.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Check if card name is provided
            if not card_name:
                embed = discord.Embed(
                    title="Missing Card Name",
                    description="Please provide the card name. Usage: `sl nremove <card name>`",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Search for the card in user_cards
            search_query = """
            SELECT * FROM user_cards 
            WHERE user_id = %s AND name LIKE %s
            """
            card_results = await execute_query_with_retry(search_query, (user_data['user_id'], f'%{card_name}%'))

            # Handle multiple card matches
            if len(card_results) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to remove the name from:",
                    color=discord.Color.blue()
                )
                
                card_list = "\n".join([f"• {card['name']} (from {card.get('name', 'Unknown card')})" 
                                        for card in card_results])
                
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card.")
                
                await ctx.send(embed=embed)
                return

            # No cards found
            if not card_results:
                embed = discord.Embed(
                    title="Card Not Found",
                    description=f"No card matching this name found in your inventory.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Get the specific card
            card = card_results[0]

            # Check if card already has no pseudo name
            if card.get('pseudo_name') is None:
                embed = discord.Embed(
                    title="No Name to Remove",
                    description="This card does not have a custom name to remove.",
                    color=discord.Color.yellow()
                )
                await ctx.send(embed=embed)
                return

            # Update query to remove pseudo name
            update_query = """
            UPDATE user_cards 
            SET pseudo_name = NULL 
            WHERE id_ligneusercards = %s
            """
            await execute_query_with_retry(update_query, (card['id_ligneusercards'],))

            embed = discord.Embed(
                title="Card Name Removed",
                description="Custom card name has been successfully removed.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in sl nremove: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Error",
                description="An unexpected error occurred.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    @commands.command(name='emedit', aliases=["eme","ee"])
    async def sl_emedit_card(self, ctx, card_name: str = None, emote_type: str = None, emote: str = None):
        try:
            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # Validate user and premium status
            query = """
            SELECT user_id, prenium, firsttier, secondtier, prenium_date 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(query, (discord_id,))

            # Check if user is registered
            if not user_data_list:
                return await ctx.send("You need to register first. Use `sl register`.")

            user_data = user_data_list[0]

            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                return await ctx.send("You need to be a premium member to use this feature. Use `sl patreon` to learn about tiers.")

            # Input validation checks
            if not card_name:
                return await ctx.send("Please specify a card name. Usage: `sl emedit <card_name> <emote_type> <emote>`")
            
            if not emote_type:
                return await ctx.send("Please specify the emote type. Usage: `sl emedit <card_name> <emote_type> <emote>`\n"
                                    "Emote types: attack, defense, speed, health, power, role, class, shield, level, xp, armor, weapon")

            
            emote_type = emote_type.lower()
            # Mapping for some alternative type names
            emote_type_map = {
                'defense': 'defense', 
                'shield': 'secondshield',
                'role': 'class',  # When 'role' is used, it maps to 'type_emoji'
                'class': 'type'  # When 'class' is used, it maps to 'class_emoji'
            }
            emote_type = emote_type_map.get(emote_type, emote_type)

            # Define valid emote types from the user_cards table
            valid_emote_types = [
                'attack_emoji', 'speed_emoji', 'health_emoji', 'defense_emoji', 
                'power_emoji', 'type_emoji', 'class_emoji', 'secondshield_emoji', 
                'level_emoji', 'xp_emoji', 'armor_emoji', 'weapon_emoji'
            ]
            # Convert user-friendly type to database column
            emote_column = f"{emote_type}_emoji"
            if emote_column not in valid_emote_types:
                return await ctx.send(f"Invalid emote type. Valid types are: {', '.join(type.replace('_emoji','') for type in valid_emote_types)}")

            if not emote:
                return await ctx.send("Please specify an emote to set.")

            # Check if card exists for the user
            card_query = """
            SELECT * FROM user_cards 
            WHERE user_id = %s AND name LIKE %s
            """
            card_results = await execute_query_with_retry(card_query, (user_data['user_id'], f"%{card_name}%"))

            if not card_results:
                return await ctx.send(f"No card found matching this name in your collection.")

            if len(card_results) > 1:
                # Multiple matches scenario
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to edit:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']} (from {card.get('name', 'Unknown')})" for card in card_results])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to edit.")
                return await ctx.send(embed=embed)

            # Single card found
            card = card_results[0]

            # Extract emote code if it's a Discord emote
            if emote.startswith('<:') and emote.endswith('>'):
                emote_code = emote
            else:
                return await ctx.send("Please provide a valid Discord emote.")

            # Update query
            update_query = f"""
            UPDATE user_cards 
            SET {emote_column} = %s 
            WHERE id_ligneusercards = %s
            """
            await execute_query_with_retry(update_query, (emote_code, card['id_ligneusercards']))

            await ctx.send(f"Successfully updated {emote_type} emote for {card['name']} to {emote_code}")

        except Exception as e:
            print(f"Error in sl emedit: {e}")
            traceback.print_exc()
            await ctx.send("An unexpected error occurred.")

    @commands.command(name='creset', aliases=["cre","cr"])
    async def sl_remote_card(self, ctx, card_name: str = None, emote_type: str = None):
        try:
            discord_id = str(ctx.author.id)

            # User and premium validation
            query = """
            SELECT user_id, prenium, firsttier, secondtier, prenium_date 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(query, (discord_id,))

            if not user_data_list:
                return await ctx.send("You need to register first. Use `sl register`.")

            user_data = user_data_list[0]

            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                return await ctx.send("You need to be a premium member to use this feature. Use `sl patreon` to learn about tiers.")

            # Input validation
            if not card_name:
                return await ctx.send("Please specify a card name. Usage: `sl remote <card_name> [emote_type/all]`")

            # Find the card
            card_query = """
            SELECT * FROM user_cards 
            WHERE user_id = %s AND name LIKE %s
            """
            card_results = await execute_query_with_retry(card_query, (user_data['user_id'], f"%{card_name}%"))

            if not card_results:
                return await ctx.send(f"No card found matchingthis name in your collection.")

            if len(card_results) > 1:
                # Multiple matches scenario
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to reset:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']} (from {card.get('anime_name', 'Unknown')})" for card in card_results])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to reset.")
                return await ctx.send(embed=embed)

            # Single card found
            card = card_results[0]

            # If no emote type specified, show available reset options
            if not emote_type:
                reset_options = [
                    'attack', 'defense', 'speed', 'health', 'power', 
                    'role', 'class', 'secondshield', 'level', 'xp', 
                    'armor', 'weapon', 'all'
                ]
                return await ctx.send(f"Available reset types for {card['name']}: {', '.join(reset_options)}")

            emote_type = emote_type.lower()
            
            # Mapping for alternative type names
            emote_type_map = {
                'defense': 'defense', 
                'shield': 'secondshield',
                'role': 'class',
                'class': 'type' # When 'class' is used, it maps to 'class_emoji'
            }
            emote_type = emote_type_map.get(emote_type, emote_type)

            # Reset for specific card and all its emotes
            if emote_type == 'all':
                # Prepare update query for all emoji columns
                update_query = """
                UPDATE user_cards 
                SET 
                    attack_emoji = %s, 
                    speed_emoji = %s, 
                    health_emoji = %s, 
                    defense_emoji = %s, 
                    power_emoji = %s, 
                    type_emoji = %s, 
                    class_emoji = %s, 
                    secondshield_emoji = %s, 
                    level_emoji = %s, 
                    xp_emoji = %s, 
                    armor_emoji = %s, 
                    weapon_emoji = %s
                WHERE id_ligneusercards = %s
                """
                
                # Prepare parameters with default emotes
# When preparing parameters for all emote reset
                params = (
                    self.DEFAULT_EMOTES.get('attack', ''), 
                    self.DEFAULT_EMOTES.get('speed', ''), # Changed 'speed' to 'move' or another appropriate key
                    self.DEFAULT_EMOTES.get('health', ''), 
                    self.DEFAULT_EMOTES.get('defense', ''), 
                    self.DEFAULT_EMOTES.get('power', ''), 
                    self.DEFAULT_EMOTES.get('type', ''), 
                    self.DEFAULT_EMOTES.get('class', ''), 
                    self.DEFAULT_EMOTES.get('secondshield', ''), 
                    self.DEFAULT_EMOTES.get('level', ''), 
                    self.DEFAULT_EMOTES.get('xp', ''), 
                    self.DEFAULT_EMOTES.get('armor', ''), 
                    self.DEFAULT_EMOTES.get('weapon', ''),
                    card['id_ligneusercards']
                )
                
                await execute_query_with_retry(update_query, params)
                return await ctx.send(f"Reset all emotes for {card['name']} to default.")

            # Define valid emote types from the user_cards table
            valid_emote_types = [
                'attack_emoji', 'speed_emoji', 'health_emoji', 'defense_emoji', 
                'power_emoji', 'type_emoji', 'class_emoji', 'secondshield_emoji', 
                'level_emoji', 'xp_emoji', 'armor_emoji', 'weapon_emoji'
            ]

            # Convert user-friendly type to database column
            emote_column = f"{emote_type}_emoji"
            if emote_column not in valid_emote_types:
                return await ctx.send(f"Invalid emote type. Valid types are: {', '.join(type.replace('_emoji','') for type in valid_emote_types)}")

            # Get default emote
            default_emote = self.DEFAULT_EMOTES.get(emote_type)
            if not default_emote:
                return await ctx.send("Could not find a default emote for this type.")

            # Update query for specific emote type
            update_query = f"""
            UPDATE user_cards 
            SET {emote_column} = %s 
            WHERE id_ligneusercards = %s
            """
            await execute_query_with_retry(update_query, (default_emote, card['id_ligneusercards']))

            await ctx.send(f"Successfully reset {emote_type} emote for {card['name']} to default.")

        except Exception as e:
            print(f"Error in sl remote: {e}")
            traceback.print_exc()
            await ctx.send("An unexpected error occurred.")
    @commands.command(name='store', aliases=["str","st"])
    async def sl_store(self, ctx):
        try:
            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # Query to fetch user details
            query = """
            SELECT user_id, prenium, firsttier, secondtier, prenium_date 
            FROM user 
            WHERE user_authorid = %s
            """
            #await execute_query_with_retry(query, (discord_id,))
            user_data_list  = await execute_query_with_retry(query, (discord_id,))
            print('user : ',user_data_list)
            # Check if user is registered
            if not user_data_list:
                embed = discord.Embed(
                    title="Registration Required",
                    description="You need to register first to use the store. Use `sl register`.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            user_data = user_data_list[0]
            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                embed = discord.Embed(
                    title="Premium Access Required",
                    description="You need to be a premium member to access the store. Use `sl patreon` to learn about tiers.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Check premium status with date
            if user_data['prenium'] != 0:
                current_date = datetime.datetime.now()
                premium_date = user_data['prenium_date']
                
                if premium_date:
                    days_difference = (current_date - premium_date).days
                    if days_difference > 30:
                        # Update premium status
                        update_query = """
                        UPDATE user
                        SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (user_data['user_id'],user_data['prenium']))

                        embed = discord.Embed(
                            title="Premium Expired",
                            description="Your premium status has expired. Renew to continue using premium features.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            # Fetch anime list
            anime_query = "SELECT anime_id, anime_name FROM anime"
            anime_list = await execute_query_with_retry(anime_query)

            # Create view for anime selection
            view = AnimeStoreView(ctx, self, anime_list)

            # Initial embed with anime selection
            # In the initial method
            initial_embed = discord.Embed(
                title="🌟 Premium Anime Card Store 🌟",
                description="Explore exclusive anime cards across different series!\n\n**How to Use:**\n• Select an anime from the dropdown below\n• Browse through unique collectible cards\n• Each card represents a special moment from the anime",
                color=0x2C3E50  # Dark blue-gray color
            )
            initial_embed.set_thumbnail(url=ctx.author.avatar.url)  # If you have a store icon
            initial_embed.set_footer(text="Select an anime to begin your collection journey!")

            await ctx.send(embed=initial_embed, view=view)

        except Exception as e:
            print(f"Error in sl store: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Error",
                description="An unexpected error occurred.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    @commands.command(name='purchase', aliases=["pur","pr"])
    async def sl_purchase(self, ctx, *args):
        """
        Purchase anime cards from the store
        Usage: sl purchase <card_name> [quantity]
        """
        try:
            # Check if arguments are provided
            if not args:
                embed = discord.Embed(
                    title="Invalid Purchase Command",
                    description="Usage: `sl purchase <card_name> [quantity]`\n\nExample: `sl purchase Naruto` or `sl purchase Naruto 3`",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Separate card name and quantity
            if args[-1].isdigit():
                card_name = ' '.join(args[:-1])
                quantity = int(args[-1])
            else:
                card_name = ' '.join(args)
                quantity = 1

            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # Query to fetch user details
            user_query = """
            SELECT user_id, prenium, firsttier, secondtier, prenium_date, won_currency 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(user_query, (discord_id,))
            
            # Check if user is registered
            if not user_data_list:
                embed = discord.Embed(
                    title="Registration Required",
                    description="You need to register first to purchase cards. Use `sl register`.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            user_data = user_data_list[0]
            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                embed = discord.Embed(
                    title="Premium Access Required",
                    description="You need to be a premium member to purchase cards. Use `sl patreon` to learn about tiers.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return
            
            # Check premium status with date
            if user_data['prenium'] != 0:
                current_date = datetime.datetime.now()
                premium_date = user_data['prenium_date']
                
                if premium_date:
                    days_difference = (current_date - premium_date).days
                    if days_difference > 30:
                        # Update premium status
                        update_query = """
                        UPDATE user 
                        SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (user_data['user_id'],))

                        embed = discord.Embed(
                            title="Premium Expired",
                            description="Your premium status has expired. Renew to continue using premium features.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            # Search for cards matching the name
            card_query = """
            SELECT animecard_id, animecard_name, price, image, description, anime_name
            FROM animecard ac
            JOIN anime a ON ac.anime_id = a.anime_id
            WHERE ac.animecard_name LIKE %s
            """
            search_term = f'%{card_name}%'
            card_results = await execute_query_with_retry(card_query, (search_term,))

            # Handle multiple or no card matches
            if not card_results:
                embed = discord.Embed(
                    title="Card Not Found",
                    description=f"No cards found matching this name.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            if len(card_results) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to purchase:",
                    color=discord.Color.blue()
                )

                # List all matching cards
                card_list = "\n".join([f"• {card['animecard_name']} (from {card['anime_name']})" 
                                        for card in card_results])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to purchase.")
                
                await ctx.send(embed=embed)
                return

            # Get the single matched card
            card = card_results[0]
            total_price = card['price'] * quantity

            # Check if user has enough currency
            if user_data['won_currency'] < total_price:
                embed = discord.Embed(
                    title="Insufficient Funds",
                    description=f"You need {total_price:,} won to purchase {quantity} {card['animecard_name']} card(s), but you only have {user_data['won_currency']:,} won.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Create confirmation view
            view = PurchaseConfirmationView(
                ctx, 
                self, 
                card, 
                quantity, 
                total_price, 
                user_data['user_id'], 
                user_data['won_currency']
            )

            # Create confirmation embed
            embed = discord.Embed(title="🛒 Purchase Confirmation", color=discord.Color.gold())
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            embed.add_field(name="Card", value=f"**{card['animecard_name']}**", inline=False)
            embed.add_field(name="Anime", value=f"**{card['anime_name']}**", inline=False)
            embed.add_field(name="Quantity", value=f"**{quantity}**", inline=True)
            embed.add_field(name="Price per Card", value=f"**{card['price']:,} won**", inline=True)
            embed.add_field(name="Total Price", value=f"**{total_price:,} won**", inline=True)
            embed.add_field(name="Your Balance", value=f"**{user_data['won_currency']:,} won**", inline=True)
            embed.add_field(name="Remaining Balance", value=f"**{user_data['won_currency'] - total_price:,} won**", inline=True)
            
            # Validate image path for additional context
            image_path = self.validate_image_path(card['image'])
            if image_path:
                file = discord.File(image_path, filename="card_image.png")
                embed.set_image(url="attachment://card_image.png")
                await ctx.send(embed=embed, view=view, file=file)
            else:
                await ctx.send(embed=embed, view=view)

        except Exception as e:
            logger.error(f"Error in sl_purchase: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Purchase Error",
                description="Type `sl store` to check the right name of the image",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    @commands.command(name='invcards', aliases=["inc","icd"])
    async def sl_inventory_cards(self, ctx):
        try:
            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # Query to fetch user details
            user_query = """
            SELECT user_id, prenium, firsttier, secondtier, prenium_date 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(user_query, (discord_id,))

            # Check if user is registered
            if not user_data_list:
                embed = discord.Embed(
                    title="Registration Required",
                    description="You need to register first to view inventory. Use `sl register`.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            user_data  = user_data_list[0]
            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                embed = discord.Embed(
                    title="Premium Access Required",
                    description="You need to be a premium member to view inventory. Use `sl patreon` to learn about tiers.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Check premium status with date
            if user_data['prenium'] != 0:
                current_date = datetime.datetime.now()
                premium_date = user_data['prenium_date']
                
                if premium_date:
                    days_difference = (current_date - premium_date).days
                    if days_difference > 30:
                        # Update premium status
                        update_query = """
                        UPDATE user 
                        SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s  
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (user_data['user_id'],))

                        embed = discord.Embed(
                            title="Premium Expired",
                            description="Your premium status has expired. Renew to continue using premium features.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            # Find user's inventory
            inventory_query = """
            SELECT inventory_id FROM inventory 
            WHERE user_id = %s
            """
            inventory = await execute_query_with_retry(inventory_query, (user_data['user_id'],))

            if not inventory:
                embed = discord.Embed(
                    title="Empty Inventory",
                    description="You haven't purchased any anime cards yet.",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
                return
            inventory = inventory[0]
            # Fetch inventory cards with anime name
            cards_query = """
            SELECT 
                ia.animecard_id, 
                ia.animecard_name, 
                ia.quantiter, 
                ia.image,
                a.anime_name
            FROM inventory_animecard ia
            JOIN animecard ac ON ia.animecard_id = ac.animecard_id
            JOIN anime a ON ac.anime_id = a.anime_id
            WHERE ia.inventory_id = %s
            ORDER BY ia.animecard_name ASC
            """
            cards = await execute_query_with_retry(cards_query, (inventory['inventory_id'],))

            if not cards:
                embed = discord.Embed(
                    title="Empty Inventory",
                    description="You haven't purchased any anime cards yet.",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
                return

            # Create initial view and composite image
            view = InventoryCardsView(ctx, self, cards, len(cards))
            
            # Create composite image
            composite_image = await view.create_composite_image(cards[:6])
            
            # Prepare file
            img_byte_arr = io.BytesIO()
            composite_image.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            file = discord.File(img_byte_arr, filename="inventory_cards.png")
            # Create embed
            embed = discord.Embed(
                title="Inventory Cards",
                description="Your collected anime cards",
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://inventory_cards.png")
            embed.set_footer(text=f"Page 1 of {(len(cards) // 6) + 1} | Total Cards: {len(cards)}")

            # Send message with view
            await ctx.send(embed=embed, view=view, file=file)

        except Exception as e:
            logger.error(f"Error in sl_inventory_cards: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Inventory Error",
                description="An unexpected error occurred while fetching your inventory.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    #@commands.command(name='market')
    async def sl_market(self, ctx, action: str = None, *args):
        """
        Premium market system for trading cards
        Usage: 
        sl market view - View current listings
        sl market list <card_name> <price> - List a card for sale
        sl market bid <listing_id> <amount> - Place a bid on a card
        sl market cancel <listing_id> - Cancel your listing
        """
        try:
            # Get user's Discord ID
            discord_id = str(ctx.author.id)

            # Query to fetch user details
            user_query = """
            SELECT user_id, prenium, firsttier, secondtier, prenium_date, won_currency 
            FROM user 
            WHERE user_authorid = %s
            """
            user_data_list = await execute_query_with_retry(user_query, (discord_id,))

            # Check if user is registered
            if not user_data_list:
                embed = discord.Embed(
                    title="Registration Required",
                    description="You need to register first to use the market. Use sl register.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_data = user_data_list[0]
            
            # Check premium eligibility - Requires prenium = 3
            if user_data['prenium'] != 3:
                embed = discord.Embed(
                    title="Premium Level 3 Required",
                    description="The market is only available for Premium Level 3 members.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Check premium status with date
            if user_data['prenium'] != 0:
                current_date = datetime.datetime.now()
                premium_date = user_data['prenium_date']
                
                if premium_date:
                    days_difference = (current_date - premium_date).days
                    if days_difference > 30:
                        # Update premium status
                        update_query = """
                        UPDATE user 
                        SET prenium = 0, prenium_date = NULL, roll_cap = roll_cap - %s 
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (user_data['prenium'], user_data['user_id']))

                        embed = discord.Embed(
                            title="Premium Expired",
                            description="Your premium status has expired. Renew to continue using the market.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            if not action:
                embed = discord.Embed(
                    title="🎴 Premium Card Market",
                    description=(
                        "Available commands:\n"
                        "`sl market view` - Browse current listings\n"
                        "`sl market list <card_name> <price>` - List a card for sale\n"
                        "`sl market bid <listing_id> <amount>` - Place a bid on a card\n"
                        "`sl market cancel <listing_id>` - Cancel your listing"
                    ),
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
                return

            if action.lower() == 'view':
                # Fetch active listings
                listings_query = """
                SELECT m.listing_id, m.seller_id, m.card_id, m.price, m.highest_bid,
                    m.highest_bidder_id, m.end_time, i.animecard_name,
                    u.user_authorid as seller_discord_id
                FROM market_listings m
                JOIN inventory_animecard i ON m.card_id = i.id_inventoryanimecard
                JOIN user u ON m.seller_id = u.user_id
                WHERE m.active = 1 AND m.end_time > NOW()
                ORDER BY m.end_time ASC
                LIMIT 10
                """
                listings = await execute_query_with_retry(listings_query)

                if not listings:
                    embed = discord.Embed(
                        title="Market Listings",
                        description="No active listings found.",
                        color=discord.Color.blue()
                    )
                    await ctx.send(embed=embed)
                    return

                embed = discord.Embed(
                    title="🎴 Current Market Listings",
                    color=discord.Color.blue()
                )

                for listing in listings:
                    seller = await self.bot.fetch_user(int(listing['seller_discord_id']))
                    time_left = listing['end_time'] - datetime.datetime.now()
                    hours_left = int(time_left.total_seconds() / 3600)

                    value = (
                        f"Seller: {seller.name}\n"
                        f"Price: {listing['price']:,} won\n"
                        f"Current Bid: {listing['highest_bid']:,} won\n"
                        f"Time Left: {hours_left} hours\n"
                        f"ID: {listing['listing_id']}"
                    )
                    embed.add_field(
                        name=f"📦 {listing['animecard_name']}", 
                        value=value, 
                        inline=False
                    )

                await ctx.send(embed=embed)

            elif action.lower() == 'list':
                if len(args) < 2:
                    embed = discord.Embed(
                        title="Invalid Format",
                        description="Usage: sl market list <card_name> <price>",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                card_name = ' '.join(args[:-1])
                try:
                    price = int(args[-1])
                except ValueError:
                    embed = discord.Embed(
                        title="Invalid Price",
                        description="Price must be a number.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Check if user has the card
                inventory_query = """
                SELECT id_inventoryanimecard, animecard_name, quantiter
                FROM inventory_animecard ia
                JOIN inventory i ON ia.inventory_id = i.inventory_id
                WHERE i.user_id = %s AND ia.animecard_name LIKE %s
                """
                card = await execute_query_with_retry(
                    inventory_query, 
                    (user_data['user_id'], f"%{card_name}%")
                )

                if not card:
                    embed = discord.Embed(
                        title="Card Not Found",
                        description="You don't own this card.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                card = card[0]
                
                # Check if card is already listed
                check_query = """
                SELECT listing_id FROM market_listings
                WHERE card_id = %s AND active = 1
                """
                existing_listing = await execute_query_with_retry(
                    check_query, 
                    (card['id_inventoryanimecard'],)
                )

                if existing_listing:
                    embed = discord.Embed(
                        title="Already Listed",
                        description="This card is already listed on the market.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Create listing
                insert_query = """
                INSERT INTO market_listings 
                (seller_id, card_id, price, end_time, active)
                VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL 24 HOUR), 1)
                """
                await execute_query_with_retry(
                    insert_query,
                    (user_data['user_id'], card['id_inventoryanimecard'], price)
                )

                embed = discord.Embed(
                    title="🎉 Listing Created!",
                    description=(
                        f"Listed {card['animecard_name']} for {price:,} won\n"
                        "Listing will expire in 24 hours."
                    ),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)

            elif action.lower() == 'bid':
                if len(args) != 2:
                    embed = discord.Embed(
                        title="Invalid Format",
                        description="Usage: sl market bid <listing_id> <amount>",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                try:
                    listing_id = int(args[0])
                    bid_amount = int(args[1])
                except ValueError:
                    embed = discord.Embed(
                        title="Invalid Input",
                        description="Listing ID and bid amount must be numbers.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Check if listing exists and is active
                listing_query = """
                SELECT m.*, i.animecard_name, u.user_authorid as seller_discord_id
                FROM market_listings m
                JOIN inventory_animecard i ON m.card_id = i.id_inventoryanimecard
                JOIN user u ON m.seller_id = u.user_id
                WHERE m.listing_id = %s AND m.active = 1 AND m.end_time > NOW()
                """
                listing = await execute_query_with_retry(listing_query, (listing_id,))

                if not listing:
                    embed = discord.Embed(
                        title="Listing Not Found",
                        description="This listing doesn't exist or has expired.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                listing = listing[0]

                # Check if bid is high enough
                min_bid = max(listing['price'], listing['highest_bid'] * 1.1)
                if bid_amount < min_bid:
                    embed = discord.Embed(
                        title="Bid Too Low",
                        description=f"Minimum bid required: {int(min_bid):,} won",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Check if user has enough currency
                if user_data['won_currency'] < bid_amount:
                    embed = discord.Embed(
                        title="Insufficient Funds",
                        description="You don't have enough won for this bid.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Update listing with new bid
                update_query = """
                UPDATE market_listings
                SET highest_bid = %s, highest_bidder_id = %s
                WHERE listing_id = %s
                """
                await execute_query_with_retry(
                    update_query,
                    (bid_amount, user_data['user_id'], listing_id)
                )

                embed = discord.Embed(
                    title="🎉 Bid Placed!",
                    description=(
                        f"You bid {bid_amount:,} won on {listing['animecard_name']}\n"
                        "You'll be notified if you're outbid or win the auction."
                    ),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)

                # Notify previous highest bidder if exists
                if listing['highest_bidder_id']:
                    try:
                        previous_bidder_query = """
                        SELECT user_authorid FROM user
                        WHERE user_id = %s
                        """
                        previous_bidder = await execute_query_with_retry(
                            previous_bidder_query,
                            (listing['highest_bidder_id'],)
                        )
                        if previous_bidder:
                            previous_bidder_user = await self.bot.fetch_user(
                                int(previous_bidder[0]['user_authorid'])
                            )
                            outbid_embed = discord.Embed(
                                title="You've Been Outbid!",
                                description=(
                                    f"Someone has outbid you on {listing['animecard_name']}\n"
                                    f"New bid: {bid_amount:,} won"
                                ),
                                color=discord.Color.orange()
                            )
                            await previous_bidder_user.send(embed=outbid_embed)
                    except:
                        pass  # Ignore if DM fails

            elif action.lower() == 'cancel':
                if not args:
                    embed = discord.Embed(
                        title="Invalid Format",
                        description="Usage: sl market cancel <listing_id>",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                try:
                    listing_id = int(args[0])
                except ValueError:
                    embed = discord.Embed(
                        title="Invalid Input",
                        description="Listing ID must be a number.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Check if listing exists and belongs to user
                listing_query = """
                SELECT m.*, i.animecard_name
                FROM market_listings m
                JOIN inventory_animecard i ON m.card_id = i.id_inventoryanimecard
                WHERE m.listing_id = %s AND m.seller_id = %s AND m.active = 1
                """
                listing = await execute_query_with_retry(
                    listing_query,
                    (listing_id, user_data['user_id'])
                )

                if not listing:
                    embed = discord.Embed(
                        title="Listing Not Found",
                        description="This listing doesn't exist or isn't yours to cancel.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Cancel listing
                cancel_query = """
                UPDATE market_listings
                SET active = 0
                WHERE listing_id = %s
                """
                await execute_query_with_retry(cancel_query, (listing_id,))

                embed = discord.Embed(
                    title="Listing Cancelled",
                    description=f"Your listing for {listing[0]['animecard_name']} has been cancelled.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in market command: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Market Error",
                description="An unexpected error occurred.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

class InventoryCardsView(discord.ui.View):
    def __init__(self, ctx, cog, cards, total_cards):
        super().__init__()
        self.ctx = ctx
        self.cog = cog
        self.cards = cards
        self.total_cards = total_cards
        self.current_page = 0
        
        # Populate card select dropdown
        options = [
            discord.SelectOption(label=card['animecard_name'], value=str(idx)) 
            for idx, card in enumerate(cards[:6])
        ]
        
        # Card select dropdown
        self.select = discord.ui.Select(
            placeholder="Select a specific card",
            options=options
        )
        self.select.callback = self.show_specific_card
        self.add_item(self.select)

        # Navigation buttons
        prev_button = discord.ui.Button(label="◀️ Previous", style=discord.ButtonStyle.secondary)
        next_button = discord.ui.Button(label="Next ▶️", style=discord.ButtonStyle.secondary)
        
        prev_button.callback = self.show_previous_page
        next_button.callback = self.show_next_page
        
        self.add_item(prev_button)
        self.add_item(next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    async def show_specific_card(self, interaction: discord.Interaction):
        try:
            # Get the selected card index
            selected_index = int(interaction.data['values'][0])
            card = self.cards[selected_index]

            # Create detailed card embed
            embed = discord.Embed(
                title=f"📦 Card Details: {card['animecard_name']}",
                description=(
                    f"**Anime:** {card['anime_name']}\n"
                    f"**Quantity:** {card['quantiter']}"
                ),
                color=discord.Color.blue()
            )

            # Add card image
            card_image_path = self.cog.validate_image_path(card['image'])
            if card_image_path:
                file = discord.File(card_image_path, filename="card_image.png")
                embed.set_image(url="attachment://card_image.png")
                await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in show_specific_card: {e}")
            await interaction.response.send_message("An error occurred.", ephemeral=True)

    async def show_previous_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    async def show_next_page(self, interaction: discord.Interaction):
        if (self.current_page + 1) * 6 < len(self.cards):
            self.current_page += 1
            await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        try:
            # Calculate the slice of cards for current page
            start_idx = self.current_page * 6
            end_idx = start_idx + 6
            page_cards = self.cards[start_idx:end_idx]

            # Create composite image
            composite_image = await self.create_composite_image(page_cards)
            
            # Update select dropdown options
            self.select.options = [
                discord.SelectOption(label=card['animecard_name'], value=str(start_idx + idx)) 
                for idx, card in enumerate(page_cards)
            ]

            # Prepare file
            img_byte_arr = io.BytesIO()
            composite_image.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            file = discord.File(img_byte_arr, filename="inventory_cards.png")

            # Create embed
            embed = discord.Embed(
                title="📦 Inventory Cards",
                description="Your collected anime cards",
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://inventory_cards.png")
            embed.set_footer(text=f"Page {self.current_page + 1} of {(len(self.cards) // 6) + 1} | Total Cards: {self.total_cards}")

            # Update message
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])

        except Exception as e:
            logger.error(f"Error in update_message: {e}")
            await interaction.response.send_message("An error occurred.", ephemeral=True)

    async def create_composite_image(self, cards):
        """
        Create a visually enhanced composite image of inventory cards
        
        :param cards: List of card dictionaries to display
        :return: PIL Image object
        """
            # Base image dimensions and card sizing
        WIDTH = 1200
        HEIGHT = 800
        CARD_WIDTH = 250  # Reduced from 300
        CARD_HEIGHT = 350  # Reduced from 400
        PADDING = 40  # Increased padding
        BORDER_WIDTH = 15  # Border width
        
        # Create base image with transparent background
        base_image = Image.new('RGBA', (WIDTH, HEIGHT), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(base_image)
        
        # Prepare font
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'Poppins-Regular.ttf')
            font_quantity = ImageFont.truetype(font_path, 32)  # Increased size from 24 to 32
        except Exception:
            font_quantity = ImageFont.load_default()
        
        # Card positioning with centered and spread out layout
        positions = [
            # Top row
            (PADDING, PADDING),  # Left
            (WIDTH // 2 - CARD_WIDTH // 2, PADDING),  # Center
            (WIDTH - CARD_WIDTH - PADDING, PADDING),  # Right
            # Bottom row
            (PADDING, CARD_HEIGHT + PADDING * 2),  # Left
            (WIDTH // 2 - CARD_WIDTH // 2, CARD_HEIGHT + PADDING * 2),  # Center
            (WIDTH - CARD_WIDTH - PADDING, CARD_HEIGHT + PADDING * 2)  # Right
        ]
        
        # Border color - light white with slight transparency
        BORDER_COLOR = (255, 255, 255, 180)
        
        # Add cards to composite
        for idx, card in enumerate(cards):
            if idx >= 6:
                break
            
            # Get card image
            card_image_path = self.cog.validate_image_path(card['image'])
            
            if card_image_path:
                try:
                    # Open and resize card image
                    card_img = Image.open(card_image_path)
                    card_img = card_img.resize((CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)
                    
                    # Create a slightly larger white-bordered surface
                    bordered_surface = Image.new('RGBA', 
                        (CARD_WIDTH + BORDER_WIDTH * 2, CARD_HEIGHT + BORDER_WIDTH * 2), 
                        color=(0, 0, 0, 0)
                    )
                    draw_border = ImageDraw.Draw(bordered_surface)
                    
                    # Draw multiple layers of soft white border
                    for i in range(BORDER_WIDTH):
                        alpha = int(180 * (1 - i/BORDER_WIDTH))
                        border_color = (*BORDER_COLOR[:3], alpha)
                        draw_border.rectangle(
                            [i, i, CARD_WIDTH + BORDER_WIDTH*2 - i, CARD_HEIGHT + BORDER_WIDTH*2 - i], 
                            outline=border_color
                        )
                    
                    # Paste card onto bordered surface
                    bordered_surface.paste(card_img, (BORDER_WIDTH, BORDER_WIDTH))
                    
                    # Paste bordered card onto base image
                    base_image.paste(
                        bordered_surface, 
                        (positions[idx][0] - BORDER_WIDTH, positions[idx][1] - BORDER_WIDTH), 
                        bordered_surface
                    )
                    
                    # Add quantity text with shadow effect
                    quantity_text = f"x{card['quantiter']}"
                    
                    # Shadow first
                    draw.text(
                        (positions[idx][0] + CARD_WIDTH - draw.textlength(quantity_text, font=font_quantity) - 20, 
                        positions[idx][1] + CARD_HEIGHT - 45), 
                        quantity_text, 
                        font=font_quantity, 
                        fill=(0, 0, 0, 128)
                    )
                    
                    # Actual text
                    draw.text(
                        (positions[idx][0] + CARD_WIDTH - draw.textlength(quantity_text, font=font_quantity) - 18, 
                        positions[idx][1] + CARD_HEIGHT - 46), 
                        quantity_text, 
                        font=font_quantity, 
                        fill=(255, 255, 255)
                    )

                except Exception as e:
                    logger.error(f"Error processing card image: {e}")
        
        return base_image

class PurchaseConfirmationView(discord.ui.View):
    def __init__(self, ctx, cog, card, quantity, total_price, user_id, user_currency):
        super().__init__()
        self.ctx = ctx
        self.cog = cog
        self.card = card
        self.quantity = quantity
        self.total_price = total_price
        self.user_id = user_id
        self.user_currency = user_currency

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Find or create user's inventory
            inventory_query = """
            SELECT inventory_id FROM inventory 
            WHERE user_id = %s
            """
            inventory = await execute_query_with_retry(inventory_query, (self.user_id,))
            inventory = inventory[0] if inventory else None

            if not inventory:
                # Create new inventory if not exists
                create_inventory_query = """
                INSERT INTO inventory (user_id) VALUES (%s)
                """
                await execute_query_with_retry(create_inventory_query, (self.user_id,))
                
                # Fetch the new inventory ID
                inventory = await execute_query_with_retry(inventory_query, (self.user_id,))
                inventory = inventory[0]

            inventory_id = inventory['inventory_id']
            
            # Check if card already exists in inventory
            check_card_query = """
            SELECT id_inventoryanimecard, quantiter
            FROM inventory_animecard 
            WHERE inventory_id = %s AND animecard_id = %s
            """
            existing_card = await execute_query_with_retry(check_card_query, (inventory_id, self.card['animecard_id']))
            
            # Update or insert inventory card
            if existing_card:
                # Card exists, update quantity
                existing_card = existing_card[0]
                update_query = """
                UPDATE inventory_animecard 
                SET quantiter = quantiter + %s 
                WHERE id_inventoryanimecard = %s
                """
                await execute_query_with_retry(update_query, (self.quantity, existing_card['id_inventoryanimecard']))
            else:
                # Insert new card to inventory
                insert_query = """
                INSERT INTO inventory_animecard 
                (inventory_id, animecard_id, animecard_name, quantiter, image) 
                VALUES (%s, %s, %s, %s, %s)
                """
                await execute_query_with_retry(insert_query, (
                    inventory_id, 
                    self.card['animecard_id'], 
                    self.card['animecard_name'], 
                    self.quantity, 
                    self.card['image']
                ))

            # Update user's won currency
            update_currency_query = """
            UPDATE user 
            SET won_currency = won_currency - %s 
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_currency_query, (self.total_price, self.user_id))

            # Create success embed
            embed = discord.Embed(
                title="🎉 Purchase Successful!",
                description=f"You've purchased {self.quantity} {self.card['animecard_name']} card(s).",
                color=discord.Color.green()
            )
            embed.add_field(name="Total Spent", value=f"{self.total_price:,} won", inline=False)
            embed.add_field(name="Remaining Balance", value=f"{self.user_currency - self.total_price:,} won", inline=False)

            # Validate and add image if possible
            image_path = self.cog.validate_image_path(self.card['image'])
            if image_path:
                file = discord.File(image_path, filename="purchased_card.png")
                embed.set_image(url="attachment://purchased_card.png")
                await interaction.response.edit_message(embed=embed, view=None, attachments=[file])
            else:
                await interaction.response.edit_message(embed=embed, view=None)

        except Exception as e:
            logger.error(f"Purchase confirmation error: {e}")
            traceback.print_exc()
            embed = discord.Embed(
                title="Purchase Error",
                description="An unexpected error occurred during purchase confirmation.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Purchase Cancelled",
            description="Your card purchase has been cancelled.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None, attachments=[])
class AnimeStoreView(discord.ui.View):
    def __init__(self, ctx, cog, anime_list):
        super().__init__()
        self.ctx = ctx
        self.cog = cog
        self.anime_list = anime_list
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'storeimages')
        self.page = 0  # Start on the first page
        self.page_size = 25

        self.update_dropdown()

        # Add pagination buttons only if the content exceeds 25 rows
        if len(anime_list) > self.page_size:
            self.add_pagination_buttons()

    def update_dropdown(self):
        """Update the select list based on the current page."""
        start_idx = self.page * self.page_size
        end_idx = start_idx + self.page_size

        select = discord.ui.Select(
            placeholder="Select an Anime",
            options=[
                discord.SelectOption(
                    label=anime['anime_name'], 
                    value=str(anime['anime_id'])
                ) for anime in self.anime_list[start_idx:end_idx]
            ]
        )
        select.callback = self.anime_select_callback
        self.clear_items()  # Clear existing components
        self.add_item(select)

    def add_pagination_buttons(self):
        """Add pagination buttons to navigate the select list."""
        # Previous page button
        prev_button = discord.ui.Button(label="<", style=discord.ButtonStyle.primary)
        prev_button.callback = self.show_previous_page
        self.add_item(prev_button)

        # Next page button
        next_button = discord.ui.Button(label=">", style=discord.ButtonStyle.primary)
        next_button.callback = self.show_next_page
        self.add_item(next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ensure only the user who initiated the command can interact
        return interaction.user.id == self.ctx.author.id

    async def anime_select_callback(self, interaction: discord.Interaction):
        """Handle anime selection."""
        try:
            # Ensure database connection
            selected_anime_id = int(interaction.data['values'][0])

            # Fetch first position card for the selected anime
            card_query = """
            SELECT animecard_name, image, price, position, description
            FROM animecard 
            WHERE anime_id = %s AND position = 1
            """
            anime_card = await execute_query_with_retry(card_query, (selected_anime_id,))
            anime_card = anime_card[0]

            # Find anime name
            anime_name = next(
                anime['anime_name'] for anime in self.anime_list 
                if anime['anime_id'] == selected_anime_id
            )

            # Create detailed description for the card
            card_description = (
                f"**Card Name:** {anime_card['animecard_name']}\n"
                f"**Price:** {anime_card['price']} <:won:1304034228966658048>\n"
                f"**Description:** {anime_card['description']}"
            )

            # Create embed for anime card
            embed = discord.Embed(
                title=f"🎴 {anime_name} Collectible Card",
                description=card_description,
                color=0x3498DB  # Bright blue color
            )

            # Prepare files for image
            file = None

            # Enhanced image handling with logging
            anime_card_path = self.cog.validate_image_path(anime_card['image'])
            if anime_card_path:
                try:
                    file = discord.File(anime_card_path, filename="anime_image.png")
                    embed.set_image(url="attachment://anime_image.png")
                except Exception as file_error:
                    pass

            # Create navigation view
            view = AnimeCardNavigationView(
                self.ctx, 
                self.cog, 
                selected_anime_id, 
                self.anime_list
            )

            # Respond to interaction
            if file:
                await interaction.response.edit_message(embed=embed, view=view, attachments=[file])
            else:
                await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            await interaction.response.send_message(
                "An error occurred while processing your selection.", 
                ephemeral=True
            )

    async def show_previous_page(self, interaction: discord.Interaction):
        """Show the previous page of the anime list."""
        if self.page > 0:
            self.page -= 1
            self.update_dropdown()
            self.add_pagination_buttons()  # Re-add buttons

            # Send updated view
            await interaction.response.edit_message(view=self)

    async def show_next_page(self, interaction: discord.Interaction):
        """Show the next page of the anime list."""
        max_page = (len(self.anime_list) - 1) // self.page_size
        if self.page < max_page:
            self.page += 1
            self.update_dropdown()
            self.add_pagination_buttons()  # Re-add buttons

            # Send updated view
            await interaction.response.edit_message(view=self)

class AnimeCardNavigationView(discord.ui.View):
    def __init__(self, ctx, cog, anime_id, anime_list):
        super().__init__()
        self.ctx = ctx
        self.cog = cog
        self.anime_id = anime_id
        self.anime_list = anime_list
        self.current_position = 1
        self.max_position = 3  # Assuming 3 cards per anime

        # Back to anime selection button
        back_button = discord.ui.Button(
            label="🔙 Back", 
            style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_anime_list
        self.add_item(back_button)

        # Previous card button
        self.prev_button = discord.ui.Button(
            label="◀️ Previous", 
            style=discord.ButtonStyle.primary,
            disabled=True  # Start disabled since we're on the first card
        )
        self.prev_button.callback = self.show_previous_card
        self.add_item(self.prev_button)

        # Next card button
        self.next_button = discord.ui.Button(
            label="Next ▶️", 
            style=discord.ButtonStyle.primary
        )
        self.next_button.callback = self.show_next_card
        self.add_item(self.next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ensure only the user who initiated the command can interact
        return interaction.user.id == self.ctx.author.id

    async def back_to_anime_list(self, interaction: discord.Interaction):
        try:
            # Fetch anime list again to ensure fresh data
            anime_query = "SELECT anime_id, anime_name FROM anime"
            anime_list = await execute_query_with_retry(anime_query)

            # Recreate anime selection view
            view = AnimeStoreView(self.ctx, self.cog, anime_list)
            
            initial_embed = discord.Embed(
                title="🌟 Premium Anime Card Store 🌟",
                description="Explore exclusive anime cards across different series!\n\n**How to Use:**\n• Select an anime from the dropdown below\n• Browse through unique collectible cards\n• Each card represents a special moment from the anime",
                color=0x2C3E50  # Dark blue-gray color
            )
            initial_embed.set_thumbnail(url = self.ctx.author.avatar.url)
            # Use empty list for attachments
            await interaction.response.edit_message(embed=initial_embed, view=view, attachments=[])

        except Exception as e:
            print(f"Error in back to anime list: {e}")
            await interaction.response.send_message(
                "An error occurred.", 
                ephemeral=True
            )

    async def show_previous_card(self, interaction: discord.Interaction):
        await self.navigate_cards(interaction, -1)

    async def show_next_card(self, interaction: discord.Interaction):
        await self.navigate_cards(interaction, 1)

    async def navigate_cards(self, interaction: discord.Interaction, direction):
        try:
            # Check if navigation is valid
            if self.current_position + direction < 1 or self.current_position + direction > self.max_position:
                await interaction.response.send_message(
                    "No more cards available.", 
                    ephemeral=True
                )
                return

            # Calculate new position
            self.current_position += direction

            # Fetch card for the current position
            card_query = """
            SELECT animecard_name, image, price, position, description
            FROM animecard 
            WHERE anime_id = %s AND position = %s
            """
            anime_card = await execute_query_with_retry(card_query, (self.anime_id, self.current_position))
            anime_card = anime_card[0]

            # Find anime name
            anime_name = next(
                anime['anime_name'] for anime in self.anime_list 
                if anime['anime_id'] == self.anime_id
            )

            # Update button states
            self.prev_button.disabled = self.current_position == 1
            self.next_button.disabled = self.current_position == self.max_position

            # Create detailed description for the card
            card_description = (
                f"**Card Name:** {anime_card['animecard_name']}\n"
                f"**Price:** {anime_card['price']} <:won:1304034228966658048>\n"
                f"**Description:** {anime_card['description']}"
            )

            # Create embed for anime card
            embed = discord.Embed(
                title=f"🎴 {anime_name} (Card {self.current_position}/{self.max_position})",
                description=card_description,
                color=discord.Color.blue()
            )

            # Prepare files for image
            files = []

            # Enhanced image handling with logging
            anime_card_path = self.cog.validate_image_path(anime_card['image'])
            if anime_card_path:
                try:
                    anime_file = discord.File(anime_card_path, filename="anime_image.png")
                    files.append(anime_file)
                    embed.set_image(url="attachment://anime_image.png")
                    logger.info(f"Image successfully added for {anime_name}, Position {self.current_position}")
                except Exception as file_error:
                    logger.error(f"Failed to add image file: {file_error}")
            else:
                logger.warning(f"No valid image path for {anime_name}, Position {self.current_position}")

            # Respond to interaction
            if files:
                await interaction.response.edit_message(embed=embed, view=self, attachments=files)
            else:
                logger.warning(f"No files for {anime_name}, Position {self.current_position}, sending embed without attachments")
                await interaction.response.edit_message(embed=embed, view=self)

        except Exception as e:
            logger.error(f"Error in card navigation: {e}")
            await interaction.response.send_message(
                "An error occurred while navigating cards.", 
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(PreniumCommands(bot))