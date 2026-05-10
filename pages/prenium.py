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

    async def get_user_lang(self, user_id):
        """Get user's language preference"""
        user_lang_query = "SELECT multilanguage FROM user WHERE user_authorid = %s"
        user_lang_result = await execute_query_with_retry(user_lang_query, (str(user_id),))
        return user_lang_result[0]['multilanguage'] if user_lang_result else 0

    def get_text(self, lang, en_text, ar_text, fr_text):
        """Get text based on language preference"""
        if lang == 1:  # Arabic
            return ar_text
        elif lang == 3:  # French
            return fr_text
        else:  # English (default)
            return en_text

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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=self.get_text(lang, 
                        "I found multiple cards matching your search. Please specify which one you'd like to edit:",
                        "وجدت عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد تعديلها:",
                        "J'ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez spécifier celle que vous souhaitez modifier :"),
                    color=discord.Color.blue()
                )
                
                card_list = "\n".join([
                    f"• {card.get('pseudo_name') or card['name']}" 
                    for card in card_results
                ])
                embed.add_field(
                    name=self.get_text(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"), 
                    value=card_list, 
                    inline=False
                )
                embed.set_footer(text=self.get_text(lang, 
                    "Please use the command again with the exact card name.",
                    "يرجى استخدام الأمر مرة أخرى مع اسم البطاقة الدقيق.",
                    "Veuillez utiliser la commande à nouveau avec le nom exact de la carte."))
                
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Multiple Anime Cards Found", "تم العثور على عدة بطاقات أنمي", "Plusieurs cartes d'anime trouvées"),
                    description=self.get_text(lang,
                        "I found multiple anime cards matching your search. Please specify which one you'd like to edit:",
                        "وجدت عدة بطاقات أنمي تطابق بحثك. يرجى تحديد البطاقة التي تريد تعديلها:",
                        "J'ai trouvé plusieurs cartes d'anime correspondant à votre recherche. Veuillez spécifier celle que vous souhaitez modifier :"),
                    color=discord.Color.blue()
                )
                
                card_list = "\n".join([f"• {card['animecard_name']}" for card in animecard_results])
                embed.add_field(
                    name=self.get_text(lang, "Matching Anime Cards", "بطاقات الأنمي المطابقة", "Cartes d'anime correspondantes"),
                    value=card_list,
                    inline=False
                )
                embed.set_footer(text=self.get_text(lang,
                    "Please use the command again with the exact anime card name.",
                    "يرجى استخدام الأمر مرة أخرى مع اسم بطاقة الأنمي الدقيق.",
                    "Veuillez utiliser la commande à nouveau avec le nom exact de la carte d'anime."))
                
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Missing Arguments", "معاملات مفقودة", "Arguments manquants"),
                    description=self.get_text(lang,
                        "Please provide the card name and new name. Usage: `sl nedit <card name>, <new name>`",
                        "يرجى تقديم اسم البطاقة والاسم الجديد. الاستخدام: `sl nedit <اسم البطاقة>, <الاسم الجديد>`",
                        "Veuillez fournir le nom de la carte et le nouveau nom. Usage : `sl nedit <nom de la carte>, <nouveau nom>`"),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            # Check if both card name and new name are provided
            if len(parts) < 2:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Incorrect Format", "تنسيق غير صحيح", "Format incorrect"),
                    description=self.get_text(lang,
                        "Please use the format: `sl nedit <card name>, <new name>`\nEnsure you have a comma separating the card name and new name.",
                        "يرجى استخدام التنسيق: `sl nedit <اسم البطاقة>, <الاسم الجديد>`\nتأكد من وجود فاصلة تفصل بين اسم البطاقة والاسم الجديد.",
                        "Veuillez utiliser le format : `sl nedit <nom de la carte>, <nouveau nom>`\nAssurez-vous d'avoir une virgule séparant le nom de la carte et le nouveau nom."),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Registration Required", "التسجيل مطلوب", "Inscription requise"),
                    description=self.get_text(lang,
                        "You need to register first. Use `sl register`.",
                        "تحتاج للتسجيل أولاً. استخدم `sl register`.",
                        "Vous devez d'abord vous inscrire. Utilisez `sl register`."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_data = user_data_list[0]

            # Check premium/tier eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Access Denied", "تم رفض الوصول", "Accès refusé"),
                    description=self.get_text(lang,
                        "You need to be a premium member or in a tier to use this command. Use `sl patreon` to learn more.",
                        "تحتاج لأن تكون عضواً مميزاً أو في مستوى لاستخدام هذا الأمر. استخدم `sl patreon` لمعرفة المزيد.",
                        "Vous devez être membre premium ou dans un niveau pour utiliser cette commande. Utilisez `sl patreon` pour en savoir plus."),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Card Not Found", "البطاقة غير موجودة", "Carte non trouvée"),
                    description=self.get_text(lang,
                        "No card matching this name found in your inventory.",
                        "لم يتم العثور على بطاقة بهذا الاسم في مخزونك.",
                        "Aucune carte correspondant à ce nom trouvée dans votre inventaire."),
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

            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Card Name Updated", "تم تحديث اسم البطاقة", "Nom de carte mis à jour"),
                description=self.get_text(lang,
                    f"Card name successfully set to '{new_name}'.",
                    f"تم تعيين اسم البطاقة بنجاح إلى '{new_name}'.",
                    f"Nom de la carte défini avec succès sur '{new_name}'."),
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in sl nedit: {e}")
            traceback.print_exc()
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Error", "خطأ", "Erreur"),
                description=self.get_text(lang,
                    "An unexpected error occurred.",
                    "حدث خطأ غير متوقع.",
                    "Une erreur inattendue s'est produite."),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Registration Required", "التسجيل مطلوب", "Inscription requise"),
                    description=self.get_text(lang,
                        "You need to register first. Use `sl register`.",
                        "تحتاج للتسجيل أولاً. استخدم `sl register`.",
                        "Vous devez d'abord vous inscrire. Utilisez `sl register`."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_data = user_data_list[0]

            # Check premium/tier eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Access Denied", "تم رفض الوصول", "Accès refusé"),
                    description=self.get_text(lang,
                        "You need to be a premium member or in a tier to use this command. Use `sl patreon` to learn more.",
                        "تحتاج لأن تكون عضواً مميزاً أو في مستوى لاستخدام هذا الأمر. استخدم `sl patreon` لمعرفة المزيد.",
                        "Vous devez être membre premium ou dans un niveau pour utiliser cette commande. Utilisez `sl patreon` pour en savoir plus."),
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Check if card name is provided
            if not card_name:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Missing Card Name", "اسم البطاقة مفقود", "Nom de carte manquant"),
                    description=self.get_text(lang,
                        "Please provide the card name. Usage: `sl nremove <card name>`",
                        "يرجى تقديم اسم البطاقة. الاستخدام: `sl nremove <اسم البطاقة>`",
                        "Veuillez fournir le nom de la carte. Usage : `sl nremove <nom de la carte>`"),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Card Not Found", "البطاقة غير موجودة", "Carte non trouvée"),
                    description=self.get_text(lang,
                        "No card matching this name found in your inventory.",
                        "لم يتم العثور على بطاقة بهذا الاسم في مخزونك.",
                        "Aucune carte correspondant à ce nom trouvée dans votre inventaire."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Get the specific card
            card = card_results[0]

            # Check if card already has no pseudo name
            if card.get('pseudo_name') is None:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "No Name to Remove", "لا يوجد اسم للإزالة", "Aucun nom à supprimer"),
                    description=self.get_text(lang,
                        "This card does not have a custom name to remove.",
                        "هذه البطاقة لا تحتوي على اسم مخصص للإزالة.",
                        "Cette carte n'a pas de nom personnalisé à supprimer."),
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

            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Card Name Removed", "تم إزالة اسم البطاقة", "Nom de carte supprimé"),
                description=self.get_text(lang,
                    "Custom card name has been successfully removed.",
                    "تم إزالة الاسم المخصص للبطاقة بنجاح.",
                    "Le nom personnalisé de la carte a été supprimé avec succès."),
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in sl nremove: {e}")
            traceback.print_exc()
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Error", "خطأ", "Erreur"),
                description=self.get_text(lang,
                    "An unexpected error occurred.",
                    "حدث خطأ غير متوقع.",
                    "Une erreur inattendue s'est produite."),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Registration Required", "التسجيل مطلوب", "Inscription requise"),
                    description=self.get_text(lang,
                        "You need to register first to use the store. Use `sl register`.",
                        "تحتاج للتسجيل أولاً لاستخدام المتجر. استخدم `sl register`.",
                        "Vous devez d'abord vous inscrire pour utiliser la boutique. Utilisez `sl register`."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            user_data = user_data_list[0]
            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Premium Access Required", "الوصول المميز مطلوب", "Accès premium requis"),
                    description=self.get_text(lang,
                        "You need to be a premium member to access the store. Use `sl patreon` to learn about tiers.",
                        "تحتاج لأن تكون عضواً مميزاً للوصول إلى المتجر. استخدم `sl patreon` لمعرفة المزيد عن المستويات.",
                        "Vous devez être membre premium pour accéder à la boutique. Utilisez `sl patreon` pour en savoir plus sur les niveaux."),
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

                        # Updated premium expired embed
                        lang = await self.get_user_lang(ctx.author.id)
                        embed = discord.Embed(
                            title=self.get_text(lang, "Premium Expired", "انتهت صلاحية العضوية المميزة", "Premium expiré"),
                            description=self.get_text(lang,
                                "Your premium status has expired. Renew to continue using premium features.",
                                "انتهت صلاحية عضويتك المميزة. جدد للاستمرار في استخدام الميزات المميزة.",
                                "Votre statut premium a expiré. Renouvelez pour continuer à utiliser les fonctionnalités premium."),
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
            # Updated initial store embed
            lang = await self.get_user_lang(ctx.author.id)
            initial_embed = discord.Embed(
                title=self.get_text(lang, "🌟 Premium Anime Card Store 🌟", "🌟 متجر بطاقات الأنمي المميز 🌟", "🌟 Boutique de cartes d'anime premium 🌟"),
                description=self.get_text(lang,
                    "Explore exclusive anime cards across different series!\n\n**How to Use:**\n• Select an anime from the dropdown below\n• Browse through unique collectible cards\n• Each card represents a special moment from the anime",
                    "استكشف بطاقات الأنمي الحصرية عبر سلاسل مختلفة!\n\n**كيفية الاستخدام:**\n• اختر أنمي من القائمة المنسدلة أدناه\n• تصفح البطاقات الفريدة القابلة للتحصيل\n• كل بطاقة تمثل لحظة خاصة من الأنمي",
                    "Explorez des cartes d'anime exclusives à travers différentes séries !\n\n**Comment utiliser :**\n• Sélectionnez un anime dans la liste déroulante ci-dessous\n• Parcourez des cartes de collection uniques\n• Chaque carte représente un moment spécial de l'anime"),
                color=0x2C3E50  # Dark blue-gray color
            )
            initial_embed.set_thumbnail(url=ctx.author.avatar.url)  # If you have a store icon
            initial_embed.set_footer(text=self.get_text(lang, "Select an anime to begin your collection journey!", "اختر أنمي لبدء رحلة مجموعتك!", "Sélectionnez un anime pour commencer votre voyage de collection !"))

            await ctx.send(embed=initial_embed, view=view)

        except Exception as e:
            print(f"Error in sl store: {e}")
            traceback.print_exc()
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Error", "خطأ", "Erreur"),
                description=self.get_text(lang,
                    "An unexpected error occurred.",
                    "حدث خطأ غير متوقع.",
                    "Une erreur inattendue s'est produite."),
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
                # Updated invalid purchase command embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Invalid Purchase Command", "أمر شراء غير صالح", "Commande d'achat invalide"),
                    description=self.get_text(lang,
                        "Usage: `sl purchase <card_name> [quantity]`\n\nExample: `sl purchase Naruto` or `sl purchase Naruto 3`",
                        "الاستخدام: `sl purchase <اسم البطاقة> [الكمية]`\n\nمثال: `sl purchase Naruto` أو `sl purchase Naruto 3`",
                        "Usage : `sl purchase <nom de la carte> [quantité]`\n\nExemple : `sl purchase Naruto` ou `sl purchase Naruto 3`"),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Registration Required", "التسجيل مطلوب", "Inscription requise"),
                    description=self.get_text(lang,
                        "You need to register first to purchase cards. Use `sl register`.",
                        "تحتاج للتسجيل أولاً لشراء البطاقات. استخدم `sl register`.",
                        "Vous devez d'abord vous inscrire pour acheter des cartes. Utilisez `sl register`."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            user_data = user_data_list[0]
            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Premium Access Required", "الوصول المميز مطلوب", "Accès premium requis"),
                    description=self.get_text(lang,
                        "You need to be a premium member to purchase cards. Use `sl patreon` to learn about tiers.",
                        "تحتاج لأن تكون عضواً مميزاً لشراء البطاقات. استخدم `sl patreon` لمعرفة المزيد عن المستويات.",
                        "Vous devez être membre premium pour acheter des cartes. Utilisez `sl patreon` pour en savoir plus sur les niveaux."),
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

                        # Updated premium expired embed
                        lang = await self.get_user_lang(ctx.author.id)
                        embed = discord.Embed(
                            title=self.get_text(lang, "Premium Expired", "انتهت صلاحية العضوية المميزة", "Premium expiré"),
                            description=self.get_text(lang,
                                "Your premium status has expired. Renew to continue using premium features.",
                                "انتهت صلاحية عضويتك المميزة. جدد للاستمرار في استخدام الميزات المميزة.",
                                "Votre statut premium a expiré. Renouvelez pour continuer à utiliser les fonctionnalités premium."),
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            # Search for cards matching the name
            card_query = """
            SELECT animecard_id, animecard_name, price, image, description, description, ardescription, frdescription, anime_name
            FROM animecard ac
            JOIN anime a ON ac.anime_id = a.anime_id
            WHERE ac.animecard_name LIKE %s
            """
            search_term = f'%{card_name}%'
            card_results = await execute_query_with_retry(card_query, (search_term,))

            # Handle multiple or no card matches
            if not card_results:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Card Not Found", "البطاقة غير موجودة", "Carte non trouvée"),
                    description=self.get_text(lang,
                        "No cards found matching this name.",
                        "لم يتم العثور على بطاقات بهذا الاسم.",
                        "Aucune carte correspondant à ce nom trouvée."),
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
                # Updated insufficient funds embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Insufficient Funds", "أموال غير كافية", "Fonds insuffisants"),
                    description=self.get_text(lang,
                        f"You need {total_price:,} won to purchase {quantity} {card['animecard_name']} card(s), but you only have {user_data['won_currency']:,} won.",
                        f"تحتاج إلى {total_price:,} ون لشراء {quantity} بطاقة {card['animecard_name']}, ولكن لديك فقط {user_data['won_currency']:,} ون.",
                        f"Vous avez besoin de {total_price:,} won pour acheter {quantity} carte(s) {card['animecard_name']}, mais vous n'avez que {user_data['won_currency']:,} won."),
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
            # Updated purchase confirmation embed
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(title=self.get_text(lang, "🛒 Purchase Confirmation", "🛒 تأكيد الشراء", "🛒 Confirmation d'achat"), color=discord.Color.gold())
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            embed.add_field(name=self.get_text(lang, "Card", "البطاقة", "Carte"), value=f"**{card['animecard_name']}**", inline=False)
            embed.add_field(name=self.get_text(lang, "Anime", "الأنمي", "Anime"), value=f"**{card['anime_name']}**", inline=False)
            embed.add_field(name=self.get_text(lang, "Quantity", "الكمية", "Quantité"), value=f"**{quantity}**", inline=True)
            embed.add_field(name=self.get_text(lang, "Price per Card", "السعر لكل بطاقة", "Prix par carte"), value=f"**{card['price']:,} won**", inline=True)
            embed.add_field(name=self.get_text(lang, "Total Price", "السعر الإجمالي", "Prix total"), value=f"**{total_price:,} won**", inline=True)
            embed.add_field(name=self.get_text(lang, "Your Balance", "رصيدك", "Votre solde"), value=f"**{user_data['won_currency']:,} won**", inline=True)
            embed.add_field(name=self.get_text(lang, "Remaining Balance", "الرصيد المتبقي", "Solde restant"), value=f"**{user_data['won_currency'] - total_price:,} won**", inline=True)
            
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
            # Updated purchase error embed
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Purchase Error", "خطأ في الشراء", "Erreur d'achat"),
                description=self.get_text(lang,
                    "Type `sl store` to check the right name of the image",
                    "اكتب `sl store` للتحقق من الاسم الصحيح للصورة",
                    "Tapez `sl store` pour vérifier le bon nom de l'image"),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Registration Required", "التسجيل مطلوب", "Inscription requise"),
                    description=self.get_text(lang,
                        "You need to register first to view inventory. Use `sl register`.",
                        "تحتاج للتسجيل أولاً لعرض المخزون. استخدم `sl register`.",
                        "Vous devez d'abord vous inscrire pour voir l'inventaire. Utilisez `sl register`."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            user_data  = user_data_list[0]
            # Check premium eligibility
            if user_data['prenium'] == 0 and user_data['firsttier'] == 0 and user_data['secondtier'] == 0:
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Premium Access Required", "الوصول المميز مطلوب", "Accès premium requis"),
                    description=self.get_text(lang,
                        "You need to be a premium member to view inventory. Use `sl patreon` to learn about tiers.",
                        "تحتاج لأن تكون عضواً مميزاً لعرض المخزون. استخدم `sl patreon` لمعرفة المزيد عن المستويات.",
                        "Vous devez être membre premium pour voir l'inventaire. Utilisez `sl patreon` pour en savoir plus sur les niveaux."),
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

                        # Updated premium expired embed
                        lang = await self.get_user_lang(ctx.author.id)
                        embed = discord.Embed(
                            title=self.get_text(lang, "Premium Expired", "انتهت صلاحية العضوية المميزة", "Premium expiré"),
                            description=self.get_text(lang,
                                "Your premium status has expired. Renew to continue using premium features.",
                                "انتهت صلاحية عضويتك المميزة. جدد للاستمرار في استخدام الميزات المميزة.",
                                "Votre statut premium a expiré. Renouvelez pour continuer à utiliser les fonctionnalités premium."),
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
                # Updated empty inventory embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Empty Inventory", "المخزون فارغ", "Inventaire vide"),
                    description=self.get_text(lang,
                        "You haven't purchased any anime cards yet.",
                        "لم تقم بشراء أي بطاقات أنمي بعد.",
                        "Vous n'avez encore acheté aucune carte d'anime."),
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
                # Updated empty inventory embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Empty Inventory", "المخزون فارغ", "Inventaire vide"),
                    description=self.get_text(lang,
                        "You haven't purchased any anime cards yet.",
                        "لم تقم بشراء أي بطاقات أنمي بعد.",
                        "Vous n'avez encore acheté aucune carte d'anime."),
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
            # Updated inventory cards embed
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Inventory Cards", "بطاقات المخزون", "Cartes d'inventaire"),
                description=self.get_text(lang, "Your collected anime cards", "بطاقات الأنمي التي جمعتها", "Vos cartes d'anime collectées"),
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://inventory_cards.png")
            embed.set_footer(text=self.get_text(lang, f"Page 1 of {(len(cards) // 6) + 1} | Total Cards: {len(cards)}", f"صفحة 1 من {(len(cards) // 6) + 1} | إجمالي البطاقات: {len(cards)}", f"Page 1 sur {(len(cards) // 6) + 1} | Total de cartes : {len(cards)}"))

            # Send message with view
            await ctx.send(embed=embed, view=view, file=file)

        except Exception as e:
            logger.error(f"Error in sl_inventory_cards: {e}")
            traceback.print_exc()
            # Updated inventory error embed
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Inventory Error", "خطأ في المخزون", "Erreur d'inventaire"),
                description=self.get_text(lang,
                    "An unexpected error occurred while fetching your inventory.",
                    "حدث خطأ غير متوقع أثناء جلب مخزونك.",
                    "Une erreur inattendue s'est produite lors de la récupération de votre inventaire."),
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
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Registration Required", "التسجيل مطلوب", "Inscription requise"),
                    description=self.get_text(lang,
                        "You need to register first to use the market. Use sl register.",
                        "تحتاج للتسجيل أولاً لاستخدام السوق. استخدم sl register.",
                        "Vous devez d'abord vous inscrire pour utiliser le marché. Utilisez sl register."),
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_data = user_data_list[0]
            
            # Check premium eligibility - Requires prenium = 3
            if user_data['prenium'] != 3:
                # Updated premium level required embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Premium Level 3 Required", "مستوى العضوية المميزة 3 مطلوب", "Niveau premium 3 requis"),
                    description=self.get_text(lang,
                        "The market is only available for Premium Level 3 members.",
                        "السوق متاح فقط لأعضاء المستوى المميز 3.",
                        "Le marché est uniquement disponible pour les membres Premium de niveau 3."),
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

                        # Updated premium expired embed
                        lang = await self.get_user_lang(ctx.author.id)
                        embed = discord.Embed(
                            title=self.get_text(lang, "Premium Expired", "انتهت صلاحية العضوية المميزة", "Premium expiré"),
                            description=self.get_text(lang,
                                "Your premium status has expired. Renew to continue using the market.",
                                "انتهت صلاحية عضويتك المميزة. جدد للاستمرار في استخدام السوق.",
                                "Votre statut premium a expiré. Renouvelez pour continuer à utiliser le marché."),
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

            if not action:
                # Updated market help embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "🎴 Premium Card Market", "🎴 سوق بطاقات الأنمي المميز", "🎴 Marché de cartes premium"),
                    description=(
                        self.get_text(lang, "Available commands:", "الأوامر المتاحة:", "Commandes disponibles :") + "\n"
                        "`sl market view` - " + self.get_text(lang, "Browse current listings", "تصفح القوائم الحالية", "Parcourir les annonces actuelles") + "\n"
                        "`sl market list <card_name> <price>` - " + self.get_text(lang, "List a card for sale", "إدراج بطاقة للبيع", "Lister une carte à vendre") + "\n"
                        "`sl market bid <listing_id> <amount>` - " + self.get_text(lang, "Place a bid on a card", "تقديم عرض على بطاقة", "Placer une enchère sur une carte") + "\n"
                        "`sl market cancel <listing_id>` - " + self.get_text(lang, "Cancel your listing", "إلغاء قائمتك", "Annuler votre annonce")
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
                    # Updated no active listings embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Market Listings", "قوائم السوق", "Annonces du marché"),
                        description=self.get_text(lang, "No active listings found.", "لم يتم العثور على قوائم نشطة.", "Aucune annonce active trouvée."),
                        color=discord.Color.blue()
                    )
                    await ctx.send(embed=embed)
                    return

                # Updated market listings embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "🎴 Current Market Listings", "🎴 قوائم السوق الحالية", "🎴 Annonces actuelles du marché"),
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
                    # Updated invalid list format embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Invalid Format", "تنسيق غير صحيح", "Format invalide"),
                        description=self.get_text(lang, "Usage: sl market list <card_name> <price>", "الاستخدام: sl market list <اسم البطاقة> <السعر>", "Usage : sl market list <nom de la carte> <prix>"),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                card_name = ' '.join(args[:-1])
                try:
                    price = int(args[-1])
                except ValueError:
                    # Updated invalid price embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Invalid Price", "سعر غير صالح", "Prix invalide"),
                        description=self.get_text(lang, "Price must be a number.", "يجب أن يكون السعر رقمًا.", "Le prix doit être un nombre."),
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
                    # Updated card not found embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Card Not Found", "البطاقة غير موجودة", "Carte non trouvée"),
                        description=self.get_text(lang, "You don't own this card.", "أنت لا تملك هذه البطاقة.", "Vous ne possédez pas cette carte."),
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
                    # Updated already listed embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Already Listed", "مدرج بالفعل", "Déjà listé"),
                        description=self.get_text(lang, "This card is already listed on the market.", "هذه البطاقة مدرجة بالفعل في السوق.", "Cette carte est déjà listée sur le marché."),
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

                # Updated listing created embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "🎉 Listing Created!", "🎉 تم إنشاء القائمة!", "🎉 Annonce créée !"),
                    description=(
                        self.get_text(lang, f"Listed {card['animecard_name']} for {price:,} won\nListing will expire in 24 hours.", f"تم إدراج {card['animecard_name']} بسعر {price:,} ون\nستنتهي صلاحية القائمة خلال 24 ساعة.", f"Annonce de {card['animecard_name']} pour {price:,} won\nL'annonce expirera dans 24 heures.")
                    ),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)

            elif action.lower() == 'bid':
                if len(args) != 2:
                    # Updated invalid bid format embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Invalid Format", "تنسيق غير صحيح", "Format invalide"),
                        description=self.get_text(lang, "Usage: sl market bid <listing_id> <amount>", "الاستخدام: sl market bid <معرف القائمة> <المبلغ>", "Usage : sl market bid <id_annonce> <montant>"),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                try:
                    listing_id = int(args[0])
                    bid_amount = int(args[1])
                except ValueError:
                    # Updated invalid input embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Invalid Input", "إدخال غير صالح", "Entrée invalide"),
                        description=self.get_text(lang, "Listing ID and bid amount must be numbers.", "يجب أن يكون معرف القائمة ومبلغ العرض أرقامًا.", "L'ID de l'annonce et le montant de l'enchère doivent être des nombres."),
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
                    # Updated listing not found embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Listing Not Found", "القائمة غير موجودة", "Annonce non trouvée"),
                        description=self.get_text(lang, "This listing doesn't exist or has expired.", "هذه القائمة غير موجودة أو انتهت صلاحيتها.", "Cette annonce n'existe pas ou a expiré."),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                listing = listing[0]

                # Check if bid is high enough
                min_bid = max(listing['price'], listing['highest_bid'] * 1.1)
                if bid_amount < min_bid:
                    # Updated bid too low embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Bid Too Low", "العرض منخفض جدًا", "Enchère trop basse"),
                        description=self.get_text(lang, f"Minimum bid required: {int(min_bid):,} won", f"الحد الأدنى للعرض المطلوب: {int(min_bid):,} ون", f"Enchère minimale requise : {int(min_bid):,} won"),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                # Check if user has enough currency
                if user_data['won_currency'] < bid_amount:
                    # Updated insufficient funds embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Insufficient Funds", "أموال غير كافية", "Fonds insuffisants"),
                        description=self.get_text(lang, "You don't have enough won for this bid.", "ليس لديك ما يكفي من الون لهذا العرض.", "Vous n'avez pas assez de won pour cette enchère."),
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

                # Updated bid placed embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "🎉 Bid Placed!", "🎉 تم تقديم العرض!", "🎉 Enchère placée !"),
                    description=(
                        self.get_text(lang, f"You bid {bid_amount:,} won on {listing['animecard_name']}\nYou'll be notified if you're outbid or win the auction.", f"لقد قدمت عرضًا بقيمة {bid_amount:,} ون على {listing['animecard_name']}\nسيتم إعلامك إذا تم تجاوز عرضك أو فزت بالمزاد.", f"Vous avez enchéri {bid_amount:,} won sur {listing['animecard_name']}\nVous serez informé si vous êtes surenchéri ou si vous gagnez l'enchère.")
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
                            # Updated outbid notification embed
                            lang = await self.get_user_lang(ctx.author.id)
                            outbid_embed = discord.Embed(
                                title=self.get_text(lang, "You've Been Outbid!", "تم تجاوز عرضك!", "Vous avez été surenchéri !"),
                                description=(
                                    self.get_text(lang, f"Someone has outbid you on {listing['animecard_name']}\nNew bid: {bid_amount:,} won", f"لقد تجاوز شخص ما عرضك على {listing['animecard_name']}\nالعرض الجديد: {bid_amount:,} ون", f"Quelqu'un a enchéri plus haut que vous sur {listing['animecard_name']}\nNouvelle enchère : {bid_amount:,} won")
                                ),
                                color=discord.Color.orange()
                            )
                            await previous_bidder_user.send(embed=outbid_embed)
                    except:
                        pass  # Ignore if DM fails

            elif action.lower() == 'cancel':
                if not args:
                    # Updated invalid cancel format embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Invalid Format", "تنسيق غير صحيح", "Format invalide"),
                        description=self.get_text(lang, "Usage: sl market cancel <listing_id>", "الاستخدام: sl market cancel <معرف القائمة>", "Usage : sl market cancel <id_annonce>"),
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                try:
                    listing_id = int(args[0])
                except ValueError:
                    # Updated invalid input embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Invalid Input", "إدخال غير صالح", "Entrée invalide"),
                        description=self.get_text(lang, "Listing ID must be a number.", "يجب أن يكون معرف القائمة رقمًا.", "L'ID de l'annonce doit être un nombre."),
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
                    # Updated listing not found embed
                    lang = await self.get_user_lang(ctx.author.id)
                    embed = discord.Embed(
                        title=self.get_text(lang, "Listing Not Found", "القائمة غير موجودة", "Annonce non trouvée"),
                        description=self.get_text(lang, "This listing doesn't exist or isn't yours to cancel.", "هذه القائمة غير موجودة أو ليست لك للإلغاء.", "Cette annonce n'existe pas ou n'est pas la vôtre à annuler."),
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

                # Updated listing cancelled embed
                lang = await self.get_user_lang(ctx.author.id)
                embed = discord.Embed(
                    title=self.get_text(lang, "Listing Cancelled", "تم إلغاء القائمة", "Annonce annulée"),
                    description=self.get_text(lang, f"Your listing for {listing[0]['animecard_name']} has been cancelled.", f"تم إلغاء قائمتك لـ {listing[0]['animecard_name']}.", f"Votre annonce pour {listing[0]['animecard_name']} a été annulée."),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in market command: {e}")
            traceback.print_exc()
            # Updated market error embed
            lang = await self.get_user_lang(ctx.author.id)
            embed = discord.Embed(
                title=self.get_text(lang, "Market Error", "خطأ في السوق", "Erreur du marché"),
                description=self.get_text(lang,
                    "An unexpected error occurred.",
                    "حدث خطأ غير متوقع.",
                    "Une erreur inattendue s'est produite."),
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
            # Updated card details embed
            lang = await self.cog.get_user_lang(interaction.user.id)
            embed = discord.Embed(
                title=self.cog.get_text(lang, f"📦 Card Details: {card['animecard_name']}", f"تفاصيل البطاقة: {card['animecard_name']}", f"Détails de la carte : {card['animecard_name']}"),
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
            # Updated inventory cards embed
            lang = await self.cog.get_user_lang(interaction.user.id)
            embed = discord.Embed(
                title=self.cog.get_text(lang, "📦 Inventory Cards", "📦 بطاقات المخزون", "📦 Cartes d'inventaire"),
                description=self.cog.get_text(lang, "Your collected anime cards", "بطاقات الأنمي التي جمعتها", "Vos cartes d'anime collectées"),
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://inventory_cards.png")
            embed.set_footer(text=self.cog.get_text(lang, f"Page {self.current_page + 1} of {(len(self.cards) // 6) + 1} | Total Cards: {self.total_cards}", f"صفحة {self.current_page + 1} من {(len(self.cards) // 6) + 1} | إجمالي البطاقات: {self.total_cards}", f"Page {self.current_page + 1} sur {(len(self.cards) // 6) + 1} | Total de cartes : {self.total_cards}"))

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
            # Updated purchase successful embed
            lang = await self.cog.get_user_lang(interaction.user.id)
            embed = discord.Embed(
                title=self.cog.get_text(lang, "🎉 Purchase Successful!", "🎉 تم الشراء بنجاح!", "🎉 Achat réussi !"),
                description=self.cog.get_text(lang, f"You've purchased {self.quantity} {self.card['animecard_name']} card(s).", f"لقد اشتريت {self.quantity} بطاقة {self.card['animecard_name']}.", f"Vous avez acheté {self.quantity} carte(s) {self.card['animecard_name']}."),
                color=discord.Color.green()
            )
            embed.add_field(name=self.cog.get_text(lang, "Total Spent", "إجمالي المبلغ المنفق", "Total dépensé"), value=f"{self.total_price:,} won", inline=False)
            embed.add_field(name=self.cog.get_text(lang, "Remaining Balance", "الرصيد المتبقي", "Solde restant"), value=f"{self.user_currency - self.total_price:,} won", inline=False)

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
            # Updated purchase error embed
            lang = await self.cog.get_user_lang(interaction.user.id)
            embed = discord.Embed(
                title=self.cog.get_text(lang, "Purchase Error", "خطأ في الشراء", "Erreur d'achat"),
                description=self.cog.get_text(lang,
                    "An unexpected error occurred during purchase confirmation.",
                    "حدث خطأ غير متوقع أثناء تأكيد الشراء.",
                    "Une erreur inattendue s'est produite lors de la confirmation de l'achat."),
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Updated purchase cancelled embed
        lang = await self.cog.get_user_lang(interaction.user.id)
        embed = discord.Embed(
            title=self.cog.get_text(lang, "Purchase Cancelled", "تم إلغاء الشراء", "Achat annulé"),
            description=self.cog.get_text(lang, "Your card purchase has been cancelled.", "تم إلغاء شراء بطاقتك.", "Votre achat de carte a été annulé."),
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None, attachments=[])
class AnimeStoreView(discord.ui.View):
    def __init__(self, ctx, cog, anime_list):
        super().__init__(timeout=180)  # Add timeout to prevent memory leaks
        self.ctx = ctx
        self.cog = cog
        self.anime_list = anime_list
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'storeimages')
        self.page = 0  # Start on the first page
        self.page_size = 25
        self.message = None  # Store message reference for editing

        self.update_dropdown()

    def update_dropdown(self):
        """Update the select list based on the current page."""
        self.clear_items()
        
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
        self.add_item(select)
        
        if len(self.anime_list) > self.page_size:
            self.add_pagination_buttons()

    def add_pagination_buttons(self):
        """Add pagination buttons to navigate the select list."""
        max_page = (len(self.anime_list) - 1) // self.page_size
        
        prev_button = discord.ui.Button(
            label="<", 
            style=discord.ButtonStyle.primary,
            disabled=(self.page == 0)
        )
        prev_button.callback = self.show_previous_page
        self.add_item(prev_button)

        next_button = discord.ui.Button(
            label=">", 
            style=discord.ButtonStyle.primary,
            disabled=(self.page >= max_page)
        )
        next_button.callback = self.show_next_page
        self.add_item(next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ensure only the user who initiated the command can interact
        return interaction.user.id == self.ctx.author.id

    async def anime_select_callback(self, interaction: discord.Interaction):
        """Handle anime selection."""
        try:
            await interaction.response.defer()
            
            selected_anime_id = int(interaction.data['values'][0])

            # Fetch first position card for the selected anime
            card_query = """
            SELECT animecard_name, image, price, position, description, ardescription, frdescription
            FROM animecard 
            WHERE anime_id = %s AND position = 1
            """
            anime_card = await execute_query_with_retry(card_query, (selected_anime_id,))
            
            if not anime_card:
                await interaction.followup.send("No cards found for this anime.", ephemeral=True)
                return
                
            anime_card = anime_card[0]

            # Fetch user's language preference
            user_lang_query = "SELECT multilanguage as lang FROM user WHERE user_authorid = %s"
            user_lang_result = await execute_query_with_retry(user_lang_query, (str(self.ctx.author.id),))
            lang = user_lang_result[0]['lang'] if user_lang_result else 0 # Default to English

            # Get the appropriate description based on language
            if lang == 1:
                card_desc = anime_card.get('ardescription', anime_card.get('description', ''))
            elif lang == 3:
                card_desc = anime_card.get('frdescription', anime_card.get('description', ''))
            else:
                card_desc = anime_card.get('description', '')

            card_description = (
                f"**Card Name:** {anime_card['animecard_name']}\n"
                f"**Price:** {anime_card['price']} <:won:1304034228966658048>\n"
                f"**Description:** {card_desc}"
            )

            anime_name = next(
                anime['anime_name'] for anime in self.anime_list 
                if anime['anime_id'] == selected_anime_id
            )

            lang = await self.cog.get_user_lang(interaction.user.id)
            embed = discord.Embed(
                title=self.cog.get_text(lang, f"🎴 {anime_name} Collectible Card", f"🎴 بطاقة الأنمي القابلة للتحصيل {anime_name}", f"🎴 Carte à collectionner {anime_name}"),
                description=card_description,
                color=0x3498DB
            )

            files = []
            anime_card_path = self.cog.validate_image_path(anime_card['image'])
            if anime_card_path:
                try:
                    file = discord.File(anime_card_path, filename="anime_image.png")
                    files.append(file)
                    embed.set_image(url="attachment://anime_image.png")
                except Exception as file_error:
                    logger.error(f"Error creating file for image: {file_error}")

            view = AnimeCardNavigationView(
                self.ctx, 
                self.cog, 
                selected_anime_id, 
                self.anime_list
            )

            if files:
                await interaction.edit_original_response(embed=embed, view=view, attachments=files)
            else:
                await interaction.edit_original_response(embed=embed, view=view, attachments=[])

        except Exception as e:
            logger.error(f"Error in anime_select_callback: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send("An error occurred while processing your selection.", ephemeral=True)
            except:
                pass

    async def show_previous_page(self, interaction: discord.Interaction):
        """Show the previous page of the anime list."""
        try:
            await interaction.response.defer()
            
            if self.page > 0:
                self.page -= 1
                self.update_dropdown()

                await interaction.edit_original_response(view=self)
        except Exception as e:
            logger.error(f"Error in show_previous_page: {e}")

    async def show_next_page(self, interaction: discord.Interaction):
        """Show the next page of the anime list."""
        try:
            await interaction.response.defer()
            
            max_page = (len(self.anime_list) - 1) // self.page_size
            if self.page < max_page:
                self.page += 1
                self.update_dropdown()

                await interaction.edit_original_response(view=self)
        except Exception as e:
            logger.error(f"Error in show_next_page: {e}")

class AnimeCardNavigationView(discord.ui.View):
    def __init__(self, ctx, cog, anime_id, anime_list):
        super().__init__(timeout=180)  # Add timeout
        self.ctx = ctx
        self.cog = cog
        self.anime_id = anime_id
        self.anime_list = anime_list
        self.current_position = 1
        self.max_position = 3

        back_button = discord.ui.Button(
            label="🔙 Back", 
            style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_anime_list
        self.add_item(back_button)

        self.prev_button = discord.ui.Button(
            label="◀️ Previous", 
            style=discord.ButtonStyle.primary,
            disabled=True
        )
        self.prev_button.callback = self.show_previous_card
        self.add_item(self.prev_button)

        self.next_button = discord.ui.Button(
            label="Next ▶️", 
            style=discord.ButtonStyle.primary
        )
        self.next_button.callback = self.show_next_card
        self.add_item(self.next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    async def back_to_anime_list(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            
            anime_query = "SELECT anime_id, anime_name FROM anime"
            anime_list = await execute_query_with_retry(anime_query)

            view = AnimeStoreView(self.ctx, self.cog, anime_list)
            
            lang = await self.cog.get_user_lang(interaction.user.id)
            initial_embed = discord.Embed(
                title=self.cog.get_text(lang, "🌟 Premium Anime Card Store 🌟", "🌟 متجر بطاقات الأنمي المميز 🌟", "🌟 Boutique de cartes d'anime premium 🌟"),
                description=self.cog.get_text(lang,
                    "Explore exclusive anime cards across different series!\n\n**How to Use:**\n• Select an anime from the dropdown below\n• Browse through unique collectible cards\n• Each card represents a special moment from the anime",
                    "استكشف بطاقات الأنمي الحصرية عبر سلاسل مختلفة!\n\n**كيفية الاستخدام:**\n• اختر أنمي من القائمة المنسدلة أدناه\n• تصفح البطاقات الفريدة القابلة للتحصيل\n• كل بطاقة تمثل لحظة خاصة من الأنمي",
                    "Explorez des cartes d'anime exclusives à travers différentes séries !\n\n**Comment utiliser :**\n• Sélectionnez un anime dans la liste déroulante ci-dessous\n• Parcourez des cartes de collection uniques\n• Chaque carte représente un moment spécial de l'anime"),
                color=0x2C3E50
            )
            initial_embed.set_thumbnail(url=self.ctx.author.avatar.url)
            
            await interaction.edit_original_response(embed=initial_embed, view=view, attachments=[])

        except Exception as e:
            logger.error(f"Error in back_to_anime_list: {e}")
            traceback.print_exc()

    async def show_previous_card(self, interaction: discord.Interaction):
        await self.navigate_cards(interaction, -1)

    async def show_next_card(self, interaction: discord.Interaction):
        await self.navigate_cards(interaction, 1)

    async def navigate_cards(self, interaction: discord.Interaction, direction):
        try:
            await interaction.response.defer()
            
            new_position = self.current_position + direction
            
            if new_position < 1 or new_position > self.max_position:
                lang = await self.cog.get_user_lang(interaction.user.id)
                await interaction.followup.send(
                    self.cog.get_text(lang, "No more cards available.", "لا مزيد من البطاقات المتاحة.", "Plus de cartes disponibles."), 
                    ephemeral=True
                )
                return

            # Fetch card at new position
            card_query = """
            SELECT animecard_name, image, price, position, description, ardescription, frdescription
            FROM animecard 
            WHERE anime_id = %s AND position = %s
            """
            anime_card = await execute_query_with_retry(card_query, (self.anime_id, new_position))
            
            if not anime_card:
                await interaction.followup.send("Card not found.", ephemeral=True)
                return
                
            anime_card = anime_card[0]

            # Update current position
            self.current_position = new_position

            # Update button states
            self.prev_button.disabled = (self.current_position == 1)
            self.next_button.disabled = (self.current_position == self.max_position)

            # Fetch user's language preference
            user_lang_query = "SELECT multilanguage as lang FROM user WHERE user_authorid = %s"
            user_lang_result = await execute_query_with_retry(user_lang_query, (str(self.ctx.author.id),))
            lang = user_lang_result[0]['lang'] if user_lang_result else 0

            if lang == 1:
                card_desc = anime_card.get('ardescription', anime_card.get('description', ''))
            elif lang == 3:
                card_desc = anime_card.get('frdescription', anime_card.get('description', ''))
            else:
                card_desc = anime_card.get('description', '')

            card_description = (
                f"**Card Name:** {anime_card['animecard_name']}\n"
                f"**Price:** {anime_card['price']} <:won:1304034228966658048>\n"
                f"**Description:** {card_desc}"
            )

            anime_name = next(
                anime['anime_name'] for anime in self.anime_list 
                if anime['anime_id'] == self.anime_id
            )

            embed = discord.Embed(
                title=f"🎴 {anime_name} Collectible Card",
                description=card_description,
                color=0x3498DB
            )

            files = []
            anime_card_path = self.cog.validate_image_path(anime_card['image'])
            if anime_card_path:
                try:
                    file = discord.File(anime_card_path, filename="anime_image.png")
                    files.append(file)
                    embed.set_image(url="attachment://anime_image.png")
                except Exception as file_error:
                    logger.error(f"Error creating file: {file_error}")

            if files:
                await interaction.edit_original_response(embed=embed, view=self, attachments=files)
            else:
                await interaction.edit_original_response(embed=embed, view=self, attachments=[])

        except Exception as e:
            logger.error(f"Error in navigate_cards: {e}")
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(PreniumCommands(bot))
