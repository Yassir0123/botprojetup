import discord
import os
from discord.ext import commands
import random
import math
from datetime import datetime, timedelta
import asyncio
import time
import re
from utils.database import execute_query_with_retry, get_metrics
from utils.activity_state import block_during_user_activity, claim_user_status_row
from utils.db_atomic import db_transaction
import functools

MAIN_SHOP_IMAGE = "https://static0.gamerantimages.com/wordpress/wp-content/uploads/2025/05/jinwoo-shop.jpg?q=49&fit=crop&w=825&dpr=2"

# -------------------------
# i18n helper
# 0 = English, 1 = Arabic, 3 = French
# -------------------------
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

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

class PriceModal(discord.ui.Modal, title='Set Your Listing Price'):
    def __init__(self, author_id, session, cog):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.session = session
        self.cog = cog

    price = discord.ui.TextInput(
        label='Total Price (Won)',
        placeholder='Enter the total price for your listing...',
        required=True,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        lang = await get_user_multilanguage(interaction.user.id)
        try:
            total_price = int(self.price.value.strip())
            if total_price <= 0:
                await interaction.response.send_message(
                    _tr(lang, "❌ Price must be greater than 0!", "❌ يجب أن يكون السعر أكبر من 0!", "❌ Le prix doit être supérieur à 0 !"),
                    ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message(
                _tr(lang, "❌ Please enter a valid number for the price!", "❌ الرجاء إدخال رقم صالح للسعر!", "❌ Veuillez saisir un nombre valide pour le prix !"),
                ephemeral=True
            )
            return

        category = self.session.get('category')
        if category != 'fragments':  # Exempt fragments from price validation
            listing = self.session.get('current_listing', [])
            original_total = await self.calculate_original_total_price(listing, category)
           
            if original_total > 0:
                price_ratio = total_price / original_total
                if price_ratio < 0.5:  # Less than 50%
                    await interaction.response.send_message(
                        _tr(
                            lang,
                            f"❌ **Price too low!** Your price ({total_price:,} Won) is more than 50% below the original value ({original_total:,} Won). Minimum allowed: {int(original_total * 0.5):,} Won",
                            f"❌ **السعر منخفض جداً!** سعرك ({total_price:,} وون) أقل بأكثر من 50٪ من القيمة الأصلية ({original_total:,} وون). الحد الأدنى المسموح: {int(original_total * 0.5):,} وون",
                            f"❌ **Prix trop bas !** Votre prix ({total_price:,} Won) est inférieur de plus de 50 % à la valeur d’origine ({original_total:,} Won). Minimum autorisé : {int(original_total * 0.5):,} Won"
                        ),
                        ephemeral=True
                    )
                    return
                elif price_ratio > 2.0:  # More than 200%
                    await interaction.response.send_message(
                        _tr(
                            lang,
                            f"❌ **Price too high!** Your price ({total_price:,} Won) is more than 200% above the original value ({original_total:,} Won). Maximum allowed: {int(original_total * 2.0):,} Won",
                            f"❌ **السعر مرتفع جداً!** سعرك ({total_price:,} وون) يزيد بأكثر من 200٪ عن القيمة الأصلية ({original_total:,} وون). الحد الأقصى المسموح: {int(original_total * 2.0):,} وون",
                            f"❌ **Prix trop élevé !** Votre prix ({total_price:,} Won) dépasse de plus de 200 % la valeur d’origine ({original_total:,} Won). Maximum autorisé : {int(original_total * 2.0):,} Won"
                        ),
                        ephemeral=True
                    )
                    return

        await interaction.response.defer()
       
        # Create the market listing
        success = await self.create_market_listing(self.session, total_price)
        if success:
            user_id = self.session.get('user_id')
            if user_id:
                await self.cog.remove_user_from_market_session(user_id)
            # Remove session
            if self.author_id in self.cog.active_market_sessions:
                del self.cog.active_market_sessions[self.author_id]
            await interaction.edit_original_response(view=None)
            await interaction.followup.send(_tr(
                lang,
                f"✅ **Listing Created Successfully!** Your items are now available for **{total_price:,}** Won in the marketplace!",
                f"✅ **تم إنشاء العرض بنجاح!** عناصرُك متاحة الآن بسعر **{total_price:,}** وون في السوق!",
                f"✅ **Annonce créée avec succès !** Vos objets sont désormais disponibles pour **{total_price:,}** Won sur le marché !"
            ))
        else:
            await interaction.followup.send(_tr(
                lang,
                "❌ Failed to create market listing. Please try again.",
                "❌ فشل إنشاء عرض السوق. يرجى المحاولة مرة أخرى.",
                "❌ Échec de la création de l’annonce. Veuillez réessayer."
            ))

    async def on_timeout(self):
        user_id = self.session.get('user_id')
        if self.cog and user_id:
            await self.cog.remove_user_from_market_session(user_id)
        # Remove session
        if self.cog and self.author_id in self.cog.active_market_sessions:
            del self.cog.active_market_sessions[self.author_id]

    async def calculate_original_total_price(self, listing, category):
        """Calculate the total original price of items in the listing"""
        try:
            total = 0
            table_map = {
                'weapon': 'weapon',
                'booster': 'product',
                'dungeonkey': 'dungeon_key'
            }
           
            if category not in table_map:
                return 0
               
            item_table = table_map[category]
            print(table_map)
            for item in listing:
                item_id = item['id']
                quantity = item['quantity']
                price_result = None
                if item_table == "weapon":
                    price_query = f"SELECT price FROM {item_table} WHERE {item_table}_id = %s"
                    price_result = await execute_query_with_retry(price_query, (item_id,))
                elif item_table == "product":
                    price_query = f"SELECT price FROM {item_table} WHERE {item_table}_id = %s"
                    price_result = await execute_query_with_retry(price_query, (item_id,))
                else:
                    price_query = "SELECT price FROM dungeon_key WHERE dungeonkey_id = %s"
                    price_result = await execute_query_with_retry(price_query, (item_id,))
                if price_result:
                    item_price = price_result[0]['price']
                    total += item_price * quantity
                   
            return total
        except Exception as e:
            print(f"Error calculating original total price: {e}")
            return 0

    async def create_market_listing(self, session, total_price):
        """Create the actual market listing in database"""
        try:
            listing = session.get('current_listing', [])
            category = session.get('category')
            user_id = session.get('user_id')
            inventory_id = session.get('inventory_id')
           
            tax_rate = 0  # Default for fragments
            if category != 'fragments':
                original_total = await self.calculate_original_total_price(listing, category)
                if original_total > 0:
                    price_ratio = total_price / original_total
                    if 0.8 <= price_ratio <= 1.2:  # 80%-120% range
                        tax_rate = 10  # 10% tax
                    else:  # 50%-80% or 120%-200% range
                        tax_rate = 30  # 30% tax
           
            table_map = {
                'weapon': ('market_weapon', 'listing_weapon', 'inventory_weapon', 'weapon_id'),
                'booster': ('market_booster', 'listing_booster', 'inventory_product', 'product_id'),
                'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'inventory_dungeonkey', 'dungeonkey_id'),
                'fragments': ('market_fragments', 'listing_fragments', 'inventory_frags', 'frag_id')
            }

            market_table, listing_table, inventory_table, id_field = table_map[category]
            listing_id_field = 'id_fragment' if category == 'fragments' else id_field

            async with db_transaction() as tx:
                inventory_row = await tx.fetchone(
                    "SELECT inventory_id FROM inventory WHERE inventory_id = %s AND user_id = %s FOR UPDATE",
                    (inventory_id, user_id)
                )
                if not inventory_row:
                    raise RuntimeError("Inventory not found for market listing.")

                for item in listing:
                    current_result = await tx.fetchone(
                        f"""
                        SELECT quantiter
                        FROM {inventory_table}
                        WHERE inventory_id = %s AND {id_field} = %s
                        FOR UPDATE
                        """,
                        (inventory_id, item['id'])
                    )
                    if not current_result or current_result["quantiter"] < item['quantity']:
                        raise RuntimeError("Not enough inventory quantity for market listing.")

                await tx.execute(
                    f"INSERT INTO {market_table} (user_id, total_price, position, tax) VALUES (%s, %s, 0, %s)",
                    (user_id, total_price, tax_rate)
                )
                id_result = await tx.fetchone("SELECT LAST_INSERT_ID() as id_market")
                id_market = id_result["id_market"]

                for item in listing:
                    item_id = item['id']
                    quantity = item['quantity']

                    await tx.execute(
                        f"INSERT INTO {listing_table} (id_market, {listing_id_field}, sell_quantity) VALUES (%s, %s, %s)",
                        (id_market, item_id, quantity)
                    )

                    current_result = await tx.fetchone(
                        f"""
                        SELECT quantiter
                        FROM {inventory_table}
                        WHERE inventory_id = %s AND {id_field} = %s
                        FOR UPDATE
                        """,
                        (inventory_id, item_id)
                    )
                    current_quantity = current_result["quantiter"]

                    if current_quantity == quantity:
                        await tx.execute(
                            f"DELETE FROM {inventory_table} WHERE inventory_id = %s AND {id_field} = %s",
                            (inventory_id, item_id)
                        )
                    else:
                        await tx.execute(
                            f"UPDATE {inventory_table} SET quantiter = quantiter - %s WHERE inventory_id = %s AND {id_field} = %s",
                            (quantity, inventory_id, item_id)
                        )
           
            return True
           
        except Exception as e:
            print(f"Error creating market listing: {e}")
            return False

    async def complete_purchase(self, id_market, category, total_price, buyer_user_id):
        """Complete the market purchase transaction"""
        try:
            table_map = {
                'weapon': 'market_weapon',
                'booster': 'market_booster',
                'dungeonkey': 'market_dungeonkey',
                'fragments': 'market_fragments'
            }
           
            market_table = table_map[category]
           
            # Check if listing still exists and get tax info
            existence_check = f"""
            SELECT user_id, total_price, tax
            FROM {market_table}
            WHERE id_market = %s
            """
            existence_result = await execute_query_with_retry(existence_check, (id_market,))
            if not existence_result:
                # Try to localize to buyer's language
                buyer_authorid = None
                try:
                    r = await execute_query_with_retry("SELECT user_authorid FROM user WHERE user_id = %s", (buyer_user_id,))
                    buyer_authorid = int(r[0]['user_authorid']) if r else None
                except:
                    pass
                lang_buyer = await get_user_multilanguage(buyer_authorid or 0)
                return False, _tr(
                    lang_buyer,
                    "This listing no longer exists. Someone may have already purchased it.",
                    "هذا العرض لم يعد موجوداً. ربما قام شخصٌ ما بشرائه بالفعل.",
                    "Cette annonce n’existe plus. Quelqu’un l’a peut-être déjà achetée."
                )
           
            listing_data = existence_result[0]
            seller_user_id = listing_data["user_id"]
            listed_price = listing_data["total_price"]
            tax_rate = listing_data.get("tax", 0)
           
            # Calculate actual transaction amounts
            if category == 'fragments' or tax_rate == 0:
                buyer_pays = listed_price
                seller_receives = listed_price
            else:
                tax_amount = int(listed_price * (tax_rate / 100))
                buyer_pays = listed_price
                seller_receives = listed_price - tax_amount

            # Get buyer's inventory_id
            inventory_query = """
            SELECT inventory_id
            FROM inventory
            WHERE user_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (buyer_user_id,))
            if not inventory_result:
                lang_buyer = await get_user_multilanguage(buyer_user_id)
                return False, _tr(
                    lang_buyer,
                    "Buyer inventory not found",
                    "لم يتم العثور على جرد المشتري",
                    "Inventaire de l’acheteur introuvable"
                )
               
            buyer_inventory_id = inventory_result[0]["inventory_id"]
           
            # Get listing items
            listing_items = await self.get_listing_items(id_market, category)
            if not listing_items:
                lang_buyer = await get_user_multilanguage(buyer_user_id)
                return False, _tr(
                    lang_buyer,
                    "No items found in listing",
                    "لا توجد عناصر في العرض",
                    "Aucun objet trouvé dans l’annonce"
                )
               
            seller_discord_query = """
            SELECT user_authorid
            FROM user
            WHERE user_id = %s
            """
            seller_discord_result = await execute_query_with_retry(seller_discord_query, (seller_user_id,))
            seller_discord_id = seller_discord_result[0]["user_authorid"] if seller_discord_result else None

            # Transfer currency
            buyer_update = """
            UPDATE user
            SET won_currency = won_currency - %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(buyer_update, (buyer_pays, buyer_user_id))
           
            seller_update = """
            UPDATE user
            SET won_currency = won_currency + %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(seller_update, (seller_receives, seller_user_id))
           
            # Transfer items to buyer
            await self.transfer_items_to_buyer(buyer_inventory_id, listing_items, category)
           
            # Remove listing from market
            await self.remove_market_listing(id_market, category)
           
            # DM seller
            if seller_discord_id:
                try:
                    seller_user = await self.bot.fetch_user(int(seller_discord_id))
                    if seller_user:
                        seller_lang = await get_user_multilanguage(int(seller_discord_id))
                        items_text = ""
                        for item in listing_items:
                            icon = item.get('icon', '❓')
                            items_text += f"{icon} **{item['name']}** x{item['quantity']}\n"
                       
                        embed = discord.Embed(
                            title=_tr(seller_lang, "💰 **Your Listing Was Purchased!**", "💰 **تم شراء عرضك!**", "💰 **Votre annonce a été achetée !**"),
                            description=_tr(
                                seller_lang,
                                "Great news! Someone just bought your marketplace listing!",
                                "أخبار رائعة! لقد اشترى شخص ما عرضك في السوق!",
                                "Bonne nouvelle ! Quelqu’un vient d’acheter votre annonce sur le marché !"
                            ),
                            color=0x27ae60
                        )
                        embed.add_field(name=_tr(seller_lang, "Items Sold", "العناصر المباعة", "Objets vendus"), value=items_text, inline=False)
                        embed.add_field(name=_tr(seller_lang, "Listed Price", "السعر المُعلن", "Prix affiché"), value=f"**{listed_price:,}** Won", inline=True)
                        if category != 'fragments' and tax_rate > 0:
                            tax_amount = listed_price - seller_receives
                            embed.add_field(name=_tr(seller_lang, "Tax Deducted", "الضريبة المخصومة", "Taxe déduite"), value=f"**{tax_amount:,}** Won ({tax_rate}%)", inline=True)
                            embed.add_field(name=_tr(seller_lang, "You Received", "المبلغ المستلم", "Montant reçu"), value=f"**{seller_receives:,}** Won", inline=True)
                        else:
                            embed.add_field(name=_tr(seller_lang, "You Received", "المبلغ المستلم", "Montant reçu"), value=f"**{seller_receives:,}** Won", inline=True)
                        embed.set_footer(text=_tr(seller_lang, "The Won has been added to your balance!", "تم إضافة الوون إلى رصيدك!", "Les Won ont été ajoutés à votre solde !"))
                        await seller_user.send(embed=embed)
                except Exception as e:
                    print(f"Error sending DM to seller: {e}")
            try:
                # Get buyer/seller usernames
                buyer_discord_query = "SELECT user_authorid FROM user WHERE user_id = %s"
                buyer_discord_result = await execute_query_with_retry(buyer_discord_query, (buyer_user_id,))
                buyer_discord_id = buyer_discord_result[0]["user_authorid"] if buyer_discord_result else None
               
                buyer_username = "Unknown Buyer"
                seller_username = "Unknown Seller"
               
                if buyer_discord_id:
                    try:
                        buyer_user = await self.bot.fetch_user(int(buyer_discord_id))
                        buyer_username = buyer_user.display_name if buyer_user else "Unknown Buyer"
                    except Exception:
                        buyer_username = "Unknown Buyer"
               
                if seller_discord_id:
                    try:
                        seller_user_obj = await self.bot.fetch_user(int(seller_discord_id))
                        seller_username = seller_user_obj.display_name if seller_user_obj else "Unknown Seller"
                    except Exception:
                        seller_username = "Unknown Seller"
               
                # Announce in guild (kept in English for global visibility)
                target_guild = self.bot.get_guild(832022150713704479)
                marketplace_channel = None
               
                if target_guild:
                    for channel in target_guild.text_channels:
                        if any(keyword in channel.name.lower() for keyword in ['marketplace', 'market', 'sales', 'transactions']):
                            marketplace_channel = channel
                            break
                    if not marketplace_channel:
                        marketplace_channel = target_guild.system_channel or (target_guild.text_channels[0] if target_guild.text_channels else None)
               
                if marketplace_channel:
                    items_text = ""
                    for item in listing_items:
                        icon = item.get('icon', '❓')
                        items_text += f"{icon} **{item['name']}** x{item['quantity']}\n"
                   
                    marketplace_message = (
                        f"💰 **Marketplace Transaction Completed!**\n"
                        f"**{buyer_username}** purchased from **{seller_username}**\n"
                        f"Price: **{listed_price:,}** Won\n"
                        f"Items: {items_text}\n"
                    )
                   
                    if category != 'fragments' and tax_rate > 0:
                        tax_amount = listed_price - seller_receives
                        marketplace_message += f"Tax Deducted: **{tax_amount:,}** Won ({tax_rate}%)\n"
                        marketplace_message += f"Seller Received: **{seller_receives:,}** Won\n"
                    else:
                        marketplace_message += f"Seller Received: **{seller_receives:,}** Won\n"
                   
                    if len(marketplace_message) > 2000:
                        parts = [marketplace_message[i:i+1990] for i in range(0, len(marketplace_message), 1990)]
                        for i, part in enumerate(parts):
                            if i == 0:
                                await marketplace_channel.send(part)
                            else:
                                await marketplace_channel.send(f"```{part}```")
                    else:
                        await marketplace_channel.send(marketplace_message)
               
            except Exception as channel_error:
                print(f"Failed to send marketplace notification: {str(channel_error)}")
                print(f"Server ID: 832022150713704479")
                print(f"Attempted to notify marketplace transaction")
                pass
           
            lang_buyer = await get_user_multilanguage(buyer_user_id)
            return True, _tr(
                lang_buyer,
                "Purchase completed successfully",
                "تم إتمام عملية الشراء بنجاح",
                "Achat effectué avec succès"
            )
           
        except Exception as e:
            print(f"Error completing purchase: {e}")
            return False, f"Error: {str(e)}"


class MarketCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.active_market_sessions = {}  # Track active market creation sessions
       
    async def add_user_to_market_session(self, user_id):
        """Add user to market_session table"""
        return await claim_user_status_row("market_session", user_id)

    async def remove_user_from_market_session(self, user_id):
        """Remove user from market_session table"""
        try:
            delete_query = "DELETE FROM market_session WHERE user_id = %s"
            await execute_query_with_retry(delete_query, (user_id,))
        except Exception as e:
            print(f"Error removing user from market session: {e}")

    async def check_user_status_conflicts(self, user_id, lang: int):
        """Check if user is in any conflicting status tables (localized messages)"""
        try:
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                return False, _tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء عرض. يُرجى الانتظار حتى يكتمل الإنشاء.",
                    "Vous créez actuellement une annonce. Veuillez attendre la fin de la création."
                )

            trade_query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            trade_result = await execute_query_with_retry(trade_query, (user_id, user_id))
            if trade_result:
                return False, _tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في صفقة نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. عند وجود مشكلة افتح تذكرة دعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai avant d’utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d’assistance."
                )

            sell_query = "SELECT user_id FROM sell_status WHERE user_id = %s"
            sell_result = await execute_query_with_retry(sell_query, (user_id,))
            if sell_result:
                return False, _tr(
                    lang,
                    "You are currently in a sell operation. Please complete or cancel your sell operation first.",
                    "لديك عملية بيع قيد التنفيذ. أكملها أو ألغها أولاً.",
                    "Vous avez une vente en cours. Terminez-la ou annulez-la d’abord."
                )

            ascending_check_query = "SELECT * FROM ascending_state WHERE user_id = %s"
            ascending_result = await execute_query_with_retry(ascending_check_query, (user_id,))
            if ascending_result:
                return False, _tr(
                    lang,
                    "You are already performing an ascension. Please wait for it to complete.",
                    "أنت تقوم بالفعل بعملية ارتقاء. يرجى الانتظار حتى تكتمل.",
                    "Vous êtes déjà en ascension. Veuillez attendre la fin."
                )

            shop_query = "SELECT user_id FROM shop_status WHERE user_id = %s"
            shop_result = await execute_query_with_retry(shop_query, (user_id,))
            if shop_result:
                return False, _tr(
                    lang,
                    "You are currently in a shop operation. Please complete your current purchase first.",
                    "لديك عملية متجر جارية. أكمل عملية الشراء الحالية أولاً.",
                    "Vous avez une opération de boutique en cours. Terminez votre achat en cours d’abord."
                )

            return True, _tr(lang, "No conflicts found", "لا توجد تعارضات", "Aucun conflit trouvé")
        except Exception as e:
            print(f"Error checking user status conflicts: {e}")
            return False, _tr(
                lang,
                "Error checking user status. Please try again.",
                "حدث خطأ أثناء التحقق من حالة المستخدم. يرجى المحاولة مرة أخرى.",
                "Erreur lors de la vérification de l’état de l’utilisateur. Veuillez réessayer."
            )

    async def check_user_status_for_delist(self, user_id, lang: int):
        """Check if user can perform delist operations (localized messages)"""
        try:
            trade_query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            trade_result = await execute_query_with_retry(trade_query, (user_id, user_id))
            if trade_result:
                return False, _tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في صفقة نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. عند وجود مشكلة افتح تذكرة دعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai avant d’utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d’assistance."
                )

            sell_query = "SELECT user_id FROM sell_status WHERE user_id = %s"
            sell_result = await execute_query_with_retry(sell_query, (user_id,))
            if sell_result:
                return False, _tr(
                    lang,
                    "You are currently in a sell operation. Please complete or cancel your sell operation first.",
                    "لديك عملية بيع قيد التنفيذ. أكملها أو ألغها أولاً.",
                    "Vous avez une vente en cours. Terminez-la ou annulez-la d’abord."
                )

            shop_query = "SELECT user_id FROM shop_status WHERE user_id = %s"
            shop_result = await execute_query_with_retry(shop_query, (user_id,))
            if shop_result:
                return False, _tr(
                    lang,
                    "You are currently in a shop operation. Please complete your current purchase first.",
                    "لديك عملية متجر جارية. أكمل عملية الشراء الحالية أولاً.",
                    "Vous avez une opération de boutique en cours. Terminez votre achat en cours d’abord."
                )

            return True, _tr(lang, "No conflicts found", "لا توجد تعارضات", "Aucun conflit trouvé")
        except Exception as e:
            print(f"Error checking user status for delist: {e}")
            return False, _tr(
                lang,
                "Error checking user status. Please try again.",
                "حدث خطأ أثناء التحقق من حالة المستخدم. يرجى المحاولة مرة أخرى.",
                "Erreur lors de la vérification de l’état de l’utilisateur. Veuillez réessayer."
            )

    async def update_user_currency(self, user_id, money_reward, gamble_reward=0):
        try:
            update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (money_reward, user_id))
        except Exception as e:
            print(f"Error updating user currency: {e}")

    async def get_user_info(self, user_discord_id):
        """Get user info from database"""
        try:
            print("usercheck")
            user_query = """
            SELECT user_id, won_currency
            FROM user
            WHERE user_authorid = %s
            """
            result = await execute_query_with_retry(user_query, (str(user_discord_id),))
            print("result:", result[0] if result else None)
            return result[0] if result else None
        except Exception as e:
            print(f"Error getting user info: {e}")
            return None

    async def get_username_from_discord(self, user_authorid):
        """Get username from Discord API using user_authorid"""
        try:
            print("username")
            print(user_authorid)
            if not user_authorid or not str(user_authorid).isdigit():
                print(f"Invalid user_authorid format: {user_authorid}")
                return "Unknown User"
           
            user = self.bot.get_user(int(user_authorid))
            if not user:
                user = await self.bot.fetch_user(int(user_authorid))
            return user.display_name if user else "Unknown User"
        except (ValueError, TypeError) as e:
            print(f"Error converting user_authorid to int: {user_authorid}, error: {e}")
            return "Unknown User"
        except Exception as e:
            print(f"Error getting username from Discord: {e}")
            return "Unknown User"

    async def check_user_in_trade(self, user_id):
        """Check if user is in an active trade"""
        try:
            print("traed check")
            trade_query = """
            SELECT *
            FROM trade_status
            WHERE (initiator_id = %s OR target_id = %s)
            """
            result = await execute_query_with_retry(trade_query, (user_id, user_id))
            return bool(result)
        except Exception as e:
            print(f"Error checking trade status: {e}")
            return False

    async def check_user_in_sell(self, user_id):
        """Check if user is in an active sell operation"""
        try:
            print("sell check")
            sell_query = "SELECT * FROM sell_status WHERE user_id = %s"
            result = await execute_query_with_retry(sell_query, (user_id,))
            return bool(result)
        except Exception as e:
            print(f"Error checking sell status: {e}")
            return False

    async def get_market_listings(self, category, page=0, items_per_page=5):
        """Get market listings for a specific category"""
        try:
            table_map = {
                'weapon': ('market_weapon', 'listing_weapon', 'weapon', 'weapon_id', 'weapon_name', 'weapon_icon'),
                'booster': ('market_booster', 'listing_booster', 'product', 'product_id', 'product_name', 'product_icon'),
                'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'dungeon_key', 'dungeonkey_id', 'dungeonkey_name', 'dungeonkey_icon'),
                'fragments': ('market_fragments', 'listing_fragments', 'fragments', 'id_fragment', 'name_fragment', 'fragments_icon')
            }
           
            if category not in table_map:
                return []
           
            market_table, listing_table, item_table, item_id_col, item_name_col, item_icon_col = table_map[category]
            offset = page * items_per_page
           
            query = f"""
            SELECT m.id_market, m.user_id, m.total_price, u.user_authorid,
                l.{item_id_col} AS item_id, l.sell_quantity,
                i.{item_name_col} AS item_name, i.{item_icon_col} AS item_icon
            FROM {market_table} m
            JOIN user u ON m.user_id = u.user_id
            JOIN {listing_table} l ON m.id_market = l.id_market
            JOIN {item_table} i ON l.{item_id_col} = i.{item_id_col}
            ORDER BY m.id_market ASC
            LIMIT %s OFFSET %s
            """
           
            result = await execute_query_with_retry(query, (items_per_page, offset))
            print(result)

            processed_listings = []
            for row in result:
                print(row)

                if not row["user_authorid"]:
                    print(f"Warning: Empty user_authorid for user_id {row['user_id']}")
                    username = "Unknown User"
                else:
                    username = await self.get_username_from_discord(row["user_authorid"])

                processed_listings.append({
                    'id_market': row["id_market"],
                    'user_id': row["user_id"],
                    'total_price': row["total_price"],
                    'username': username,
                    'item_id': row["item_id"],
                    'sell_quantity': row["sell_quantity"],
                    'item_name': row["item_name"],
                    'item_icon': row["item_icon"]
                })
           
            return processed_listings
           
        except Exception as e:
            print(f"Error getting market listings: {e}")
            return []


    @commands.command(name='market', aliases=["mk", "mkt"])
    async def market_view(self, ctx):
        """View market listings"""
        lang = await get_user_multilanguage(ctx.author.id)
        user_info = await self.get_user_info(ctx.author.id)
        if not user_info:
            await ctx.send(_tr(
                lang,
                "❌ You need to register first! Use `sl register` to get started.",
                "❌ تحتاج إلى التسجيل أولاً! استخدم `sl register` للبدء.",
                "❌ Vous devez d’abord vous inscrire ! Utilisez `sl register` pour commencer."
            ))
            return

        user_id = user_info["user_id"]
        can_proceed, message = await self.check_user_status_for_delist(user_id, lang)
        if not can_proceed:
            await ctx.send(f"⚠️ {message}")
            return

        # Create main market embed
        embed = discord.Embed(
            title=_tr(lang, "🏪 Solo Leveling Marketplace", "🏪 سوق Solo Leveling", "🏪 Marché Solo Leveling"),
            description=_tr(
                lang,
                "🌟 **Welcome to the ultimate trading hub!** 🌟\n\nDiscover rare weapons, powerful boosters, mystical dungeon keys, and precious fragments from fellow hunters. Every listing tells a story of adventure and conquest!",
                "🌟 **مرحباً بك في مركز التداول الأمثل!** 🌟\n\nاكتشف أسلحة نادرة، معززات قوية، مفاتيح زنزانات سحرية، وقطع جوهر ثمينة من صيادين آخرين. كل عرض يحكي قصة مغامرة ونصر!",
                "🌟 **Bienvenue dans le centre d’échange ultime !** 🌟\n\nDécouvrez des armes rares, des boosters puissants, des clés de donjon mystiques et de précieux fragments proposés par d’autres chasseurs. Chaque annonce raconte une histoire d’aventure et de conquête !"
            ),
            color=0x3498db
        )
       
        embed.set_image(url=MAIN_SHOP_IMAGE)
       
        embed.add_field(
            name=_tr(lang, "🛍️ **Available Categories**", "🛍️ **الفئات المتاحة**", "🛍️ **Catégories disponibles**"),
            value=_tr(
                lang,
                "🗡️ **Weapons** - Legendary blades and mystical armaments\n🧪 **Boosters** - Power-enhancing elixirs and consumables\n🗝️ **Dungeon Keys** - Access to hidden realms and treasures\n💎 **Fragments** - Rare crafting materials and essence",
                "🗡️ **الأسلحة** - شفرات أسطورية وتسليحٌ سحري\n🧪 **المعززات** - إكسير ومواد مستهلكة لزيادة القوة\n🗝️ **مفاتيح الزنزانات** - وصول لعوالم خفية وكنوز\n💎 **القطع** - مواد تصنيع نادرة وجوهر",
                "🗡️ **Armes** - Lames légendaires et armes mystiques\n🧪 **Boosters** - Élixirs et consommables amplifiant la puissance\n🗝️ **Clés de donjon** - Accès à des royaumes et trésors cachés\n💎 **Fragments** - Matériaux d’artisanat et essence rares"
            ),
            inline=False
        )
       
        embed.set_footer(text=_tr(
            lang,
            "🎯 Select a category below to explore amazing deals from hunters worldwide!",
            "🎯 اختر فئة أدناه لاستكشاف عروض مذهلة من صيادين حول العالم!",
            "🎯 Sélectionnez une catégorie ci-dessous pour explorer des offres incroyables de chasseurs du monde entier !"
        ))

        # Note: CategorySelect/MarketView classes should be updated separately for i18n if needed.
        select = CategorySelect(lang)
        view = MarketView(select)
        await ctx.send(embed=embed, view=view)

    async def show_category_listings(self, interaction, category, page=0):
        """Show listings for a specific category with pagination"""
        try:
            lang = await get_user_multilanguage(interaction.user.id)
            listings = await self.get_market_listings(category, page)
           
            category_names = {
                'weapon': _tr(lang, '🗡️ Weapons', '🗡️ الأسلحة', '🗡️ Armes'),
                'booster': _tr(lang, '🧪 Boosters', '🧪 المعززات', '🧪 Boosters'),
                'dungeonkey': _tr(lang, '🗝️ Dungeon Keys', '🗝️ مفاتيح الزنزانات', '🗝️ Clés de donjon'),
                'fragments': _tr(lang, '💎 Fragments', '💎 القطع', '💎 Fragments')
            }
           
            category_descriptions = {
                'weapon': _tr(lang, 'Legendary weapons forged by master smiths and blessed by ancient powers',
                                   'أسلحة أسطورية صاغها حدّادون مهرة ومباركة بقوى قديمة',
                                   'Armes légendaires forgées par des maîtres et bénies par d’antiques puissances'),
                'booster': _tr(lang, 'Mystical potions and enhancers to boost your hunting capabilities',
                                    'جرعات سحرية ومقويات لتعزيز قدراتك القتالية',
                                    'Potions mystiques et amplificateurs pour booster vos capacités'),
                'dungeonkey': _tr(lang, 'Keys to unlock hidden dungeons filled with treasures and challenges',
                                       'مفاتيح لفتح زنزانات خفية مليئة بالكنوز والتحديات',
                                       'Des clés pour ouvrir des donjons cachés remplis de trésors et de défis'),
                'fragments': _tr(lang, 'Rare essence fragments used for crafting and enhancement',
                                      'شظايا جوهر نادرة تُستخدم للتصنيع والتعزيز',
                                      'Fragments d’essence rares utilisés pour l’artisanat et l’amélioration')
            }
           
            embed = discord.Embed(
                title=_tr(lang, f"{category_names.get(category, category.title())} Marketplace",
                          f"سوق {category_names.get(category, category.title())}",
                          f"Marché {category_names.get(category, category.title())}"),
                description=f"✨ {category_descriptions.get(category, _tr(lang, 'Browse available listings', 'تصفّح العروض المتاحة', 'Parcourez les annonces disponibles'))} ✨",
                color=0x9b59b6
            )
           
            if listings:
                # Group items by id_market
                grouped = {}
                for listing in listings:
                    mid = listing['id_market']
                    if mid not in grouped:
                        grouped[mid] = {
                            "user_id": listing['user_id'],
                            "username": listing['username'],
                            "total_price": listing['total_price'],
                            "items": []
                        }
                    grouped[mid]["items"].append({
                        "icon": listing['item_icon'],
                        "name": listing['item_name'],
                        "quantity": listing['sell_quantity']
                    })

                category_prefixes = {
                    'weapon': 'a',
                    'booster': 'b',
                    'dungeonkey': 'c',
                    'fragments': 'd'
                }
                prefix = category_prefixes.get(category, 'x')

                listings_text = ""
                for mid, data in grouped.items():
                    price_formatted = f"{data['total_price']:,}"
                    items_text = ", ".join(
                        f"{item['icon']} {item['name']} x{item['quantity']}"
                        for item in data["items"]
                    )
                    listings_text += (
                        f"**#{prefix}{mid}** 👤 {data['username']}\n"
                        + _tr(lang, "Offer", "العرض", "Offre")
                        + f" : {items_text}\n"
                        + _tr(lang, "Cost", "السعر", "Coût")
                        + f" : **{price_formatted}** <:won:1304034228966658048>\n\n"
                    )
               
                embed.add_field(
                    name=_tr(lang, f"📋 **Available Listings** (Page {page + 1})",
                             f"📋 **العروض المتاحة** (الصفحة {page + 1})",
                             f"📋 **Annonces disponibles** (Page {page + 1})"),
                    value=listings_text,
                    inline=False
                )
               
                embed.add_field(
                    name=_tr(lang, "🛒 **How to Purchase**", "🛒 **طريقة الشراء**", "🛒 **Comment acheter**"),
                    value=_tr(
                        lang,
                        f"Use `sl mbuy <listing_id>` to buy a listing\nExample: `sl mbuy {prefix}{listings[0]['id_market']}`",
                        f"استخدم `sl mbuy <listing_id>` لشراء عرض\nمثال: `sl mbuy {prefix}{listings[0]['id_market']}`",
                        f"Utilisez `sl mbuy <listing_id>` pour acheter une annonce\nExemple : `sl mbuy {prefix}{listings[0]['id_market']}`"
                    ),
                    inline=False
                )
            else:
                embed.add_field(
                    name=_tr(lang, "📭 **No Listings Found**", "📭 **لا توجد عروض**", "📭 **Aucune annonce trouvée**"),
                    value=_tr(lang,
                              "No items are currently available in this category. Be the first to create a listing!",
                              "لا توجد عناصر حالياً في هذه الفئة. كن أول من ينشئ عرضاً!",
                              "Aucun objet disponible dans cette catégorie. Soyez le premier à créer une annonce !"),
                    inline=False
                )
           
            embed.set_footer(text=_tr(lang,
                                      "🔄 Use the buttons below to navigate or return to main menu",
                                      "🔄 استخدم الأزرار أدناه للتنقل أو الرجوع للقائمة الرئيسية",
                                      "🔄 Utilisez les boutons ci-dessous pour naviguer ou revenir au menu principal"))
           
            # Create navigation view (unchanged API)
            view = CategoryListingsView(category, page, len(listings) == 5)
            await interaction.edit_original_response(embed=embed, view=view)
           
        except Exception as e:
            print(f"Error showing category listings: {e}")
            lang = await get_user_multilanguage(interaction.user.id)
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title=_tr(lang, "❌ Error", "❌ خطأ", "❌ Erreur"),
                    description=_tr(lang, "Failed to load category listings. Please try again.",
                                    "فشل تحميل عروض الفئة. يرجى المحاولة مرة أخرى.",
                                    "Échec du chargement des annonces de la catégorie. Veuillez réessayer."),
                    color=0xe74c3c
                ),
                view=None
            )


    @commands.command(name='mcreate', aliases=["mcr", "mkr"])
    @block_during_user_activity(get_user_multilanguage)
    async def market_create(self, ctx):
        """Create a new market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        user_info = await self.get_user_info(ctx.author.id)
        if not user_info:
            await ctx.send(_tr(
                lang,
                "❌ You need to register first! Use `sl register` to get started.",
                "❌ تحتاج إلى التسجيل أولاً! استخدم `sl register` للبدء.",
                "❌ Vous devez d’abord vous inscrire ! Utilisez `sl register` pour commencer."
            ))
            return

        user_id = user_info["user_id"]
       
        can_proceed, message = await self.check_user_status_conflicts(user_id, lang)
        if not can_proceed:
            await ctx.send(f"⚠️ {message}")
            return

        if not await self.add_user_to_market_session(user_id):
            await ctx.send(_tr(
                lang,
                "⚠️ You are already creating a market listing.",
                "⚠️ أنت تقوم بإنشاء عرض في السوق بالفعل.",
                "⚠️ Vous créez déjà une annonce sur le marché."
            ))
            return

        # Get user's inventory_id
        try:
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await self.remove_user_from_market_session(user_id)
                await ctx.send(_tr(lang, "❌ You don't have an inventory yet!", "❌ لا تملك جرداً بعد!", "❌ Vous n’avez pas encore d’inventaire !"))
                return
           
            inventory_id = inventory_result[0]["inventory_id"]
        except Exception as e:
            await self.remove_user_from_market_session(user_id)
            await ctx.send(_tr(lang, "❌ Error accessing your inventory.", "❌ خطأ في الوصول إلى جردك.", "❌ Erreur d’accès à votre inventaire."))
            return

        # Create market creation embed
        embed = discord.Embed(
            title=_tr(lang, "🏪 **Create Your Market Listing**", "🏪 **أنشئ عرضك في السوق**", "🏪 **Créez votre annonce sur le marché**"),
            description=_tr(
                lang,
                "🎯 **Ready to become a merchant?** Choose your category and start building your listing!\n\n✨ Transform your inventory into profit by selling to fellow hunters across the realm!",
                "🎯 **جاهز لتصبح تاجراً؟** اختر الفئة وابدأ ببناء عرضك!\n\n✨ حوّل جردك إلى أرباح ببيعه لصيادين آخرين عبر المملكة!",
                "🎯 **Prêt à devenir marchand ?** Choisissez votre catégorie et commencez votre annonce !\n\n✨ Transformez votre inventaire en profit en vendant à d’autres chasseurs !"
            ),
            color=0xe74c3c
        )
       
        embed.add_field(
            name=_tr(lang, "🗂️ **Available Categories**", "🗂️ **الفئات المتاحة**", "🗂️ **Catégories disponibles**"),
            value=_tr(
                lang,
                "🗡️ **Weapons** - Sell your legendary armaments\n🧪 **Boosters** - Share your powerful consumables\n🗝️ **Dungeon Keys** - Trade access to hidden realms\n💎 **Fragments** - Exchange rare crafting materials",
                "🗡️ **الأسلحة** - بِع تجهيزاتك الأسطورية\n🧪 **المعززات** - شارك مستهلكاتك القوية\n🗝️ **مفاتيح الزنزانات** - تداول وصولاً لعوالم خفية\n💎 **القطع** - تبادل مواد تصنيع نادرة",
                "🗡️ **Armes** - Vendez vos armements légendaires\n🧪 **Boosters** - Partagez vos consommables puissants\n🗝️ **Clés de donjon** - Échangez l’accès à des royaumes cachés\n💎 **Fragments** - Échange de matériaux rares"
            ),
            inline=False
        )
       
        embed.set_footer(text=_tr(
            lang,
            "⏰ This session will expire in 2 minutes if no category is selected",
            "⏰ ستنتهي صلاحية هذه الجلسة خلال دقيقتين إذا لم تُحدَّد فئة",
            "⏰ Cette session expirera dans 2 minutes si aucune catégorie n’est sélectionnée"
        ))

        select = MarketCreateCategorySelect(ctx.author.id, user_id, inventory_id)
        view = MarketCreateView(select)
       
        message = await ctx.send(embed=embed, view=view)
       
        # Set timeout for initial category selection (120 seconds)
        await asyncio.sleep(120)
        if ctx.author.id not in self.active_market_sessions:
            await self.remove_user_from_market_session(user_id)
            try:
                await message.edit(view=None)
            except:
                pass

    @commands.command(name='madd')
    @block_during_user_activity(get_user_multilanguage)
    async def market_add_item(self, ctx, *, args):
        """Add item to current market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        if ctx.author.id not in self.active_market_sessions:
            await ctx.send(_tr(lang, "❌ You don't have an active market creation session.", "❌ لا تملك جلسة إنشاء عرض نشطة.", "❌ Vous n’avez pas de session de création d’annonce active."))
            return
           
        session = self.active_market_sessions[ctx.author.id]
        if not session.get('category'):
            await ctx.send(_tr(lang, "❌ Please select a category first.", "❌ يرجى اختيار فئة أولاً.", "❌ Veuillez d’abord sélectionner une catégorie."))
            return
           
        # Parse arguments (item_name, quantity)
        try:
            parts = args.split(',')
            if len(parts) != 2:
                await ctx.send(_tr(lang, "❌ Usage: `sl madd item_name, quantity`", "❌ الاستخدام: `sl madd اسم_العنصر, الكمية`", "❌ Utilisation : `sl madd nom_objet, quantité`"))
                return
               
            item_name = parts[0].strip()
            quantity = int(parts[1].strip())
           
            if quantity <= 0:
                await ctx.send(_tr(lang, "❌ Quantity must be greater than 0.", "❌ يجب أن تكون الكمية أكبر من 0.", "❌ La quantité doit être supérieure à 0."))
                return
               
        except ValueError:
            await ctx.send(_tr(lang, "❌ Invalid quantity. Please enter a number.", "❌ كمية غير صالحة. الرجاء إدخال رقم.", "❌ Quantité invalide. Veuillez entrer un nombre."))
            return
           
        category = session['category']
        inventory_id = session['inventory_id']
       
        # Add item based on category
        if category == 'weapon':
            await self.add_weapon_to_listing(ctx, item_name, quantity, inventory_id, session)
        elif category == 'booster':
            await self.add_booster_to_listing(ctx, item_name, quantity, inventory_id, session)
        elif category == 'dungeonkey':
            await self.add_dungeonkey_to_listing(ctx, item_name, quantity, inventory_id, session)
        elif category == 'fragments':
            await self.add_fragment_to_listing(ctx, item_name, quantity, inventory_id, session)

    @commands.command(name='mremove')
    @block_during_user_activity(get_user_multilanguage)
    async def market_remove_item(self, ctx, *, item_name):
        """Remove item from current market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        if ctx.author.id not in self.active_market_sessions:
            await ctx.send(_tr(lang, "❌ You don't have an active market creation session.", "❌ لا تملك جلسة إنشاء عرض نشطة.", "❌ Vous n’avez pas de session de création d’annonce active."))
            return
           
        session = self.active_market_sessions[ctx.author.id]
        if not session.get('category'):
            await ctx.send(_tr(lang, "❌ Please select a category first.", "❌ يرجى اختيار فئة أولاً.", "❌ Veuillez d’abord sélectionner une catégorie."))
            return
           
        # Remove item from current listing
        listing = session.get('current_listing', [])
        item_name_lower = item_name.lower().strip()
       
        # Find and remove item
        for i, item in enumerate(listing):
            if item['name'].lower() == item_name_lower:
                removed_item = listing.pop(i)
                await ctx.send(_tr(
                    lang,
                    f"✅ Removed **{removed_item['name']}** from your listing.",
                    f"✅ تمت إزالة **{removed_item['name']}** من عرضك.",
                    f"✅ **{removed_item['name']}** a été retiré de votre annonce."
                ))
               
                # Update the listing display
                await self.update_listing_display(ctx, session)
                return
               
        await ctx.send(_tr(
            lang,
            "❌ This item was not found in your current listing.",
            "❌ لم يتم العثور على هذا العنصر في عرضك الحالي.",
            "❌ Cet objet n’a pas été trouvé dans votre annonce actuelle."
        ))

# Helper: translate by user language (0=en, 1=ar, 3=fr)

    async def add_weapon_to_listing(self, ctx, weapon_name, quantity, inventory_id, session):
        """Add weapon to market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Find weapon by name
            weapon_query = """
            SELECT weapon_id, weapon_name, weapon_icon
            FROM weapon
            WHERE weapon_name LIKE %s and tradable = 1
            """
            weapon_result = await execute_query_with_retry(weapon_query, (f"%{weapon_name}%",))
            if not weapon_result:
                await ctx.send(_tr(lang,
                    "❌ This weapon doesn't exist.",
                    "❌ هذا السلاح غير موجود.",
                    "❌ Cette arme n'existe pas."
                ))
                return

            weapon_result = weapon_result[0]
            weapon_id = weapon_result["weapon_id"]
            weapon_name_db = weapon_result["weapon_name"]
            weapon_icon = weapon_result["weapon_icon"]

            # Check if user has this weapon
            inventory_query = """
            SELECT quantiter
            FROM inventory_weapon
            WHERE inventory_id = %s AND weapon_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (inventory_id, weapon_id))

            if not inventory_result:
                await ctx.send(_tr(lang,
                    f"❌ You don't have **{weapon_name_db}** in your inventory.",
                    f"❌ ليس لديك **{weapon_name_db}** في جردك.",
                    f"❌ Vous n'avez pas **{weapon_name_db}** dans votre inventaire."
                ))
                return

            available_quantity = inventory_result[0]['quantiter']
            if available_quantity < quantity:
                await ctx.send(_tr(lang,
                    f"❌ You only have **{available_quantity}** of **{weapon_name_db}**, but you want to sell **{quantity}**.",
                    f"❌ لديك فقط **{available_quantity}** من **{weapon_name_db}**، بينما تريد بيع **{quantity}**.",
                    f"❌ Vous n'avez que **{available_quantity}** de **{weapon_name_db}**, mais vous voulez en vendre **{quantity}**."
                ))
                return

            # Add to current listing
            if 'current_listing' not in session:
                session['current_listing'] = []

            # Check if item already in listing
            for item in session['current_listing']:
                if item['id'] == weapon_id:
                    await ctx.send(_tr(lang,
                        f"❌ **{weapon_name_db}** is already in your listing.",
                        f"❌ **{weapon_name_db}** موجود بالفعل في قائمتك.",
                        f"❌ **{weapon_name_db}** est déjà dans votre annonce."
                    ))
                    return

            session['current_listing'].append({
                'id': weapon_id,
                'name': weapon_name_db,
                'icon': weapon_icon,
                'quantity': quantity
            })

            await ctx.send(_tr(lang,
                f"✅ Added **{weapon_name_db}** x{quantity} to your listing.",
                f"✅ تمت إضافة **{weapon_name_db}** ×{quantity} إلى قائمتك.",
                f"✅ **{weapon_name_db}** ×{quantity} ajouté à votre annonce."
            ))
            await self.update_listing_display(ctx, session)

        except Exception as e:
            await ctx.send(_tr(lang,
                "❌ Error adding weapon to listing.",
                "❌ خطأ أثناء إضافة السلاح إلى القائمة.",
                "❌ Erreur lors de l'ajout de l'arme à l'annonce."
            ))
            print(f"Error adding weapon: {e}")


    async def add_booster_to_listing(self, ctx, product_name, quantity, inventory_id, session):
        """Add booster to market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Find product by name
            product_query = """
            SELECT product_id, product_name, product_icon
            FROM product
            WHERE product_name LIKE %s and tradable = 0
            """
            product_result = await execute_query_with_retry(product_query, (f"%{product_name}%",))

            if not product_result:
                await ctx.send(_tr(lang,
                    "❌ This product doesn't exist.",
                    "❌ هذا المنتج غير موجود.",
                    "❌ Ce produit n'existe pas."
                ))
                return

            product_result = product_result[0]
            product_id = product_result["product_id"]
            product_name_db = product_result["product_name"]
            product_icon = product_result["product_icon"]

            # Check if user has this product
            inventory_query = """
            SELECT quantiter
            FROM inventory_product
            WHERE inventory_id = %s AND product_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (inventory_id, product_id))

            if not inventory_result:
                await ctx.send(_tr(lang,
                    f"❌ You don't have **{product_name_db}** in your inventory.",
                    f"❌ ليس لديك **{product_name_db}** في جردك.",
                    f"❌ Vous n'avez pas **{product_name_db}** dans votre inventaire."
                ))
                return

            available_quantity = inventory_result[0]["quantiter"]
            if available_quantity < quantity:
                await ctx.send(_tr(lang,
                    f"❌ You only have **{available_quantity}** of **{product_name_db}**, but you want to sell **{quantity}**.",
                    f"❌ لديك فقط **{available_quantity}** من **{product_name_db}**، بينما تريد بيع **{quantity}**.",
                    f"❌ Vous n'avez que **{available_quantity}** de **{product_name_db}**, mais vous voulez en vendre **{quantity}**."
                ))
                return

            # Add to current listing
            if 'current_listing' not in session:
                session['current_listing'] = []

            # Check if item already in listing
            for item in session['current_listing']:
                if item['id'] == product_id:
                    await ctx.send(_tr(lang,
                        f"❌ **{product_name_db}** is already in your listing.",
                        f"❌ **{product_name_db}** موجود بالفعل في قائمتك.",
                        f"❌ **{product_name_db}** est déjà dans votre annonce."
                    ))
                    return

            session['current_listing'].append({
                'id': product_id,
                'name': product_name_db,
                'icon': product_icon,
                'quantity': quantity
            })

            await ctx.send(_tr(lang,
                f"✅ Added **{product_name_db}** x{quantity} to your listing.",
                f"✅ تمت إضافة **{product_name_db}** ×{quantity} إلى قائمتك.",
                f"✅ **{product_name_db}** ×{quantity} ajouté à votre annonce."
            ))
            await self.update_listing_display(ctx, session)

        except Exception as e:
            await ctx.send(_tr(lang,
                "❌ Error adding booster to listing.",
                "❌ خطأ أثناء إضافة المعزز إلى القائمة.",
                "❌ Erreur lors de l'ajout du booster à l'annonce."
            ))
            print(f"Error adding booster: {e}")


    async def add_dungeonkey_to_listing(self, ctx, key_name, quantity, inventory_id, session):
        """Add dungeon key to market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Find dungeon key by name
            key_query = """
            SELECT dungeonkey_id, dungeonkey_name, dungeonkey_icon
            FROM dungeon_key
            WHERE dungeonkey_name LIKE %s
            """
            key_result = await execute_query_with_retry(key_query, (f"%{key_name}%",))

            if not key_result:
                await ctx.send(_tr(lang,
                    "❌ This dungeon key doesn't exist.",
                    "❌ مفتاح الزنزانة هذا غير موجود.",
                    "❌ Cette clé de donjon n'existe pas."
                ))
                return

            key_result = key_result[0]
            key_id = key_result["dungeonkey_id"]
            key_name_db = key_result["dungeonkey_name"]
            key_icon = key_result["dungeonkey_icon"]

            # Check if user has this key
            inventory_query = """
            SELECT quantiter
            FROM inventory_dungeonkey
            WHERE inventory_id = %s AND dungeonkey_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (inventory_id, key_id))

            if not inventory_result:
                await ctx.send(_tr(lang,
                    f"❌ You don't have **{key_name_db}** in your inventory.",
                    f"❌ ليس لديك **{key_name_db}** في جردك.",
                    f"❌ Vous n'avez pas **{key_name_db}** dans votre inventaire."
                ))
                return

            available_quantity = inventory_result[0]["quantiter"]
            if available_quantity < quantity:
                await ctx.send(_tr(lang,
                    f"❌ You only have **{available_quantity}** of **{key_name_db}**, but you want to sell **{quantity}**.",
                    f"❌ لديك فقط **{available_quantity}** من **{key_name_db}**، بينما تريد بيع **{quantity}**.",
                    f"❌ Vous n'avez que **{available_quantity}** de **{key_name_db}**, mais vous voulez en vendre **{quantity}**."
                ))
                return

            # Add to current listing
            if 'current_listing' not in session:
                session['current_listing'] = []

            # Check if item already in listing
            for item in session['current_listing']:
                if item['id'] == key_id:
                    await ctx.send(_tr(lang,
                        f"❌ **{key_name_db}** is already in your listing.",
                        f"❌ **{key_name_db}** موجود بالفعل في قائمتك.",
                        f"❌ **{key_name_db}** est déjà dans votre annonce."
                    ))
                    return

            session['current_listing'].append({
                'id': key_id,
                'name': key_name_db,
                'icon': key_icon,
                'quantity': quantity
            })

            await ctx.send(_tr(lang,
                f"✅ Added **{key_name_db}** x{quantity} to your listing.",
                f"✅ تمت إضافة **{key_name_db}** ×{quantity} إلى قائمتك.",
                f"✅ **{key_name_db}** ×{quantity} ajouté à votre annonce."
            ))
            await self.update_listing_display(ctx, session)

        except Exception as e:
            await ctx.send(_tr(lang,
                "❌ Error adding dungeon key to listing.",
                "❌ خطأ أثناء إضافة مفتاح الزنزانة إلى القائمة.",
                "❌ Erreur lors de l'ajout de la clé de donjon à l'annonce."
            ))
            print(f"Error adding dungeon key: {e}")


    async def add_fragment_to_listing(self, ctx, fragment_name, quantity, inventory_id, session):
        """Add fragment to market listing"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Find fragment by name
            fragment_query = """
            SELECT id_fragment, name_fragment, fragments_icon
            FROM fragments
            WHERE name_fragment LIKE %s
            """
            fragment_result = await execute_query_with_retry(fragment_query, (f"%{fragment_name}%",))

            if not fragment_result:
                await ctx.send(_tr(lang,
                    "❌ This essence stone doesn't exist.",
                    "❌ حجر الجوهر هذا غير موجود.",
                    "❌ Cette pierre d'essence n'existe pas."
                ))
                return

            fragment_result = fragment_result[0]
            fragment_id = fragment_result["id_fragment"]
            fragment_name_db = fragment_result["name_fragment"]
            fragment_icon = fragment_result["fragments_icon"]

            # Check if user has this fragment
            inventory_query = """
            SELECT quantiter
            FROM inventory_frags
            WHERE inventory_id = %s AND frag_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (inventory_id, fragment_id))

            if not inventory_result:
                await ctx.send(_tr(lang,
                    f"❌ You don't have **{fragment_name_db}** in your inventory.",
                    f"❌ ليس لديك **{fragment_name_db}** في جردك.",
                    f"❌ Vous n'avez pas **{fragment_name_db}** dans votre inventaire."
                ))
                return

            available_quantity = inventory_result[0]["quantiter"]
            if available_quantity < quantity:
                await ctx.send(_tr(lang,
                    f"❌ You only have **{available_quantity}** of **{fragment_name_db}**, but you want to sell **{quantity}**.",
                    f"❌ لديك فقط **{available_quantity}** من **{fragment_name_db}**، بينما تريد بيع **{quantity}**.",
                    f"❌ Vous n'avez que **{available_quantity}** de **{fragment_name_db}**, mais vous voulez en vendre **{quantity}**."
                ))
                return

            # Add to current listing
            if 'current_listing' not in session:
                session['current_listing'] = []

            # Check if item already in listing
            for item in session['current_listing']:
                if item['id'] == fragment_id:
                    await ctx.send(_tr(lang,
                        f"❌ **{fragment_name_db}** is already in your listing.",
                        f"❌ **{fragment_name_db}** موجود بالفعل في قائمتك.",
                        f"❌ **{fragment_name_db}** est déjà dans votre annonce."
                    ))
                    return

            session['current_listing'].append({
                'id': fragment_id,
                'name': fragment_name_db,
                'icon': fragment_icon,
                'quantity': quantity
            })

            await ctx.send(_tr(lang,
                f"✅ Added **{fragment_name_db}** x{quantity} to your listing.",
                f"✅ تمت إضافة **{fragment_name_db}** ×{quantity} إلى قائمتك.",
                f"✅ **{fragment_name_db}** ×{quantity} ajouté à votre annonce."
            ))
            await self.update_listing_display(ctx, session)

        except Exception as e:
            await ctx.send(_tr(lang,
                "❌ Error adding fragment to listing.",
                "❌ خطأ أثناء إضافة الشظية إلى القائمة.",
                "❌ Erreur lors de l'ajout du fragment à l'annonce."
            ))
            print(f"Error adding fragment: {e}")


    async def update_listing_display(self, ctx, session):
        """Update the listing display embed"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            listing = session.get('current_listing', [])
            category = session.get('category', 'Unknown')

            embed = discord.Embed(
                title=_tr(lang,
                    f"📝 **Creating {category.title()} Listing**",
                    f"📝 **إنشاء قائمة {category.title()}**",
                    f"📝 **Création d'une annonce {category.title()}**"
                ),
                description=_tr(lang,
                    "🎯 **Building your marketplace empire!** Add items to create an irresistible offer for fellow hunters.",
                    "🎯 **نَبني إمبراطوريتك في السوق!** أضف عناصر لصنع عرض لا يُقاوَم لبقية الصيادين.",
                    "🎯 **Bâtissez votre empire du marché !** Ajoutez des objets pour créer une offre irrésistible pour les autres chasseurs."
                ),
                color=0xe74c3c
            )

            if listing:
                listing_text = ""
                total_items = 0
                for item in listing:
                    icon = item.get('icon', '❓')
                    listing_text += f"{icon} **{item['name']}** x{item['quantity']}\n"
                    total_items += item['quantity']
                embed.add_field(
                    name=_tr(lang,
                        f"📦 **Your Listing** ({total_items} items total)",
                        f"📦 **قائمتك** (الإجمالي {total_items} عنصرًا)",
                        f"📦 **Votre annonce** ({total_items} objet(s) au total)"
                    ),
                    value=listing_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name=_tr(lang, "📦 **Your Listing**", "📦 **قائمتك**", "📦 **Votre annonce**"),
                    value=_tr(lang,
                        "*🔍 No items added yet - start building your collection!*",
                        "*🔍 لم تتم إضافة أي عناصر بعد — ابدأ ببناء تشكيلتك!*",
                        "*🔍 Aucun objet ajouté pour le moment — commencez à composer votre lot !*"
                    ),
                    inline=False
                )

            embed.add_field(
                name=_tr(lang, "⚡ **Quick Commands**", "⚡ **أوامر سريعة**", "⚡ **Commandes rapides**"),
                value=_tr(lang,
                    "📥 `sl madd item_name, quantity` - Add item to your listing\n📤 `sl mremove item_name` - Remove item from listing",
                    "📥 `sl madd item_name, quantity` - أضف عنصرًا إلى قائمتك\n📤 `sl mremove item_name` - أزل عنصرًا من القائمة",
                    "📥 `sl madd item_name, quantity` - Ajouter un objet à votre annonce\n📤 `sl mremove item_name` - Retirer un objet de l'annonce"
                ),
                inline=False
            )

            embed.set_footer(text=_tr(lang,
                "💡 Pro tip: Add multiple items to create attractive bundles, then click Confirm!",
                "💡 نصيحة: أضف عناصر متعددة لصنع حِزم جذابة، ثم اضغط Confirm!",
                "💡 Astuce : ajoutez plusieurs objets pour créer des packs attractifs, puis cliquez sur Confirm !"
            ))

            # Create confirm/cancel buttons
            view = MarketListingView(ctx.author.id, session)

            if 'last_message' in session and session['last_message']:
                try:
                    await session['last_message'].edit(embed=embed, view=view)
                    return
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    pass

            # Create new message if edit failed or no previous message
            session['last_message'] = await ctx.send(embed=embed, view=view)

        except Exception as e:
            print(f"Error updating listing display: {e}")


    @commands.command(name='mbuy', aliases=["mb", "mby"])
    @block_during_user_activity(get_user_multilanguage)
    async def buy_listing(self, ctx, listing_id: str):
        """Buy a listing using the new ID format (e.g., a12, b5, c3, d7)"""
        lang = await get_user_multilanguage(ctx.author.id)

        if not listing_id or len(listing_id) < 2:
            await ctx.send(_tr(lang,
                "❌ Invalid listing ID format. Use format like: a12, b5, c3, d7",
                "❌ صيغة معرّف القائمة غير صالحة. استخدم مثل: a12, b5, c3, d7",
                "❌ Format d'identifiant d'annonce invalide. Exemple : a12, b5, c3, d7"
            ))
            return

        # Parse the category prefix and numeric ID
        prefix = listing_id[0].lower()
        try:
            numeric_id = int(listing_id[1:])
        except ValueError:
            await ctx.send(_tr(lang,
                "❌ Invalid listing ID format. Use format like: a12, b5, c3, d7",
                "❌ صيغة معرّف القائمة غير صالحة. استخدم مثل: a12, b5, c3, d7",
                "❌ Format d'identifiant d'annonce invalide. Exemple : a12, b5, c3, d7"
            ))
            return

        # Map prefix to category
        prefix_to_category = {
            'a': 'weapon',
            'b': 'booster',
            'c': 'dungeonkey',
            'd': 'fragments'
        }

        category = prefix_to_category.get(prefix)
        if not category:
            await ctx.send(_tr(lang,
                "❌ Invalid category prefix. Use: a (weapons), b (boosters), c (dungeon keys), d (fragments)",
                "❌ بادئة فئة غير صالحة. استخدم: a (أسلحة)، b (معززات)، c (مفاتيح زنزانة)، d (شظايا)",
                "❌ Préfixe de catégorie invalide. Utilisez : a (armes), b (boosters), c (clés de donjon), d (fragments)"
            ))
            return

        await self.process_market_purchase(ctx, numeric_id, category)


    async def process_market_purchase(self, ctx, id_market, category):
        """Process market purchase for any category"""
        lang = await get_user_multilanguage(ctx.author.id)
        user_info = await self.get_user_info(ctx.author.id)
        if not user_info:
            await ctx.send(_tr(lang,
                "❌ You need to register first! Use `sl register` to get started.",
                "❌ يجب أن تسجّل أولاً! استخدم `sl register` للبدء.",
                "❌ Vous devez d'abord vous inscrire ! Utilisez `sl register` pour commencer."
            ))
            return

        user_id = user_info["user_id"]
        won_currency = user_info["won_currency"]
        try:
            # Get market listing info based on category
            table_map = {
                'weapon': 'market_weapon',
                'booster': 'market_booster',
                'dungeonkey': 'market_dungeonkey',
                'fragments': 'market_fragments'
            }
            market_table = table_map[category]

            # Get listing details
            market_query = f"""
            SELECT user_id, total_price
            FROM {market_table}
            WHERE id_market = %s
            """
            market_result = await execute_query_with_retry(market_query, (id_market,))
            if not market_result:
                await ctx.send(_tr(lang,
                    f"❌ Listing #{id_market} not found.",
                    f"❌ لم يتم العثور على القائمة رقم #{id_market}.",
                    f"❌ Annonce n°{id_market} introuvable."
                ))
                return

            market_result = market_result[0]
            seller_user_id = market_result["user_id"]
            total_price = market_result["total_price"]

            # Can't buy your own listing
            if seller_user_id == user_id:
                await ctx.send(_tr(lang,
                    "❌ You cannot buy your own listing!",
                    "❌ لا يمكنك شراء قائمتك الخاصة!",
                    "❌ Vous ne pouvez pas acheter votre propre annonce !"
                ))
                return

            # Enough currency?
            if won_currency < total_price:
                await ctx.send(_tr(lang,
                    f"❌ You don't have enough Won! You need **{total_price:,}** Won but only have **{won_currency:,}** Won.",
                    f"❌ ليس لديك ما يكفي من الـ Won! تحتاج **{total_price:,}** Won ولكن لديك فقط **{won_currency:,}** Won.",
                    f"❌ Vous n'avez pas assez de Won ! Il vous faut **{total_price:,}** Won mais vous n'avez que **{won_currency:,}** Won."
                ))
                return

            # Load items
            listing_items = await self.get_listing_items(id_market, category)
            if not listing_items:
                await ctx.send(_tr(lang,
                    "❌ This listing has no items.",
                    "❌ هذه القائمة لا تحتوي على أي عناصر.",
                    "❌ Cette annonce ne contient aucun objet."
                ))
                return

            # Confirmation embed
            embed = discord.Embed(
                title=_tr(lang, "🛒 Confirm Purchase", "🛒 تأكيد الشراء", "🛒 Confirmer l'achat"),
                description=_tr(lang,
                    f"Are you sure you want to buy this listing for **{total_price:,}** Won?",
                    f"هل أنت متأكد أنك تريد شراء هذه القائمة مقابل **{total_price:,}** Won؟",
                    f"Êtes-vous sûr de vouloir acheter cette annonce pour **{total_price:,}** Won ?"
                ),
                color=0xf39c12
            )

            items_text = ""
            for item in listing_items:
                icon = item.get('icon', '❓')
                items_text += f"{icon} **{item['name']}** x{item['quantity']}\n"

            embed.add_field(
                name=_tr(lang, "Items You'll Receive", "العناصر التي ستحصل عليها", "Objets reçus"),
                value=items_text, inline=False
            )
            embed.add_field(
                name=_tr(lang, "Total Price", "السعر الإجمالي", "Prix total"),
                value=f"**{total_price:,}** Won", inline=True
            )
            embed.add_field(
                name=_tr(lang, "Your Balance", "رصيدك", "Votre solde"),
                value=f"**{won_currency:,}** Won", inline=True
            )

            # Confirm/cancel view
            view = PurchaseConfirmView(ctx.author.id, id_market, category, total_price, user_id)
            await ctx.send(embed=embed, view=view)

        except Exception as e:
            await ctx.send(_tr(lang,
                "❌ Error processing purchase request.",
                "❌ خطأ أثناء معالجة طلب الشراء.",
                "❌ Erreur lors du traitement de l'achat."
            ))
            print(f"Error in market purchase: {e}")


    async def get_listing_items(self, id_market, category):
        """Get items in a market listing"""
        try:
            if category == 'weapon':
                query = """
                SELECT lw.weapon_id, lw.sell_quantity, w.weapon_name, w.weapon_icon
                FROM listing_weapon lw
                JOIN weapon w ON lw.weapon_id = w.weapon_id
                WHERE lw.id_market = %s
                """
            elif category == 'booster':
                query = """
                SELECT lb.product_id, lb.sell_quantity, p.product_name, p.product_icon
                FROM listing_booster lb
                JOIN product p ON lb.product_id = p.product_id
                WHERE lb.id_market = %s
                """
            elif category == 'dungeonkey':
                query = """
                SELECT ld.dungeonkey_id, ld.sell_quantity, dk.dungeonkey_name, dk.dungeonkey_icon
                FROM listing_dungeonkey ld
                JOIN dungeon_key dk ON ld.dungeonkey_id = dk.dungeonkey_id
                WHERE ld.id_market = %s
                """
            elif category == 'fragments':
                query = """
                SELECT lf.id_fragment, lf.sell_quantity, f.name_fragment, f.fragments_icon
                FROM listing_fragments lf
                JOIN fragments f ON lf.id_fragment = f.id_fragment
                WHERE lf.id_market = %s
                """
            else:
                return []

            result = await execute_query_with_retry(query, (id_market,))

            items = []
            if category == 'fragments':
                for row in result:
                    items.append({
                        'id': row['id_fragment'],
                        'quantity': row['sell_quantity'],
                        'name': row['name_fragment'],
                        'icon': row['fragments_icon']
                    })
            elif category == 'weapon':
                for row in result:
                    items.append({
                        'id': row['weapon_id'],
                        'quantity': row['sell_quantity'],
                        'name': row['weapon_name'],
                        'icon': row['weapon_icon']
                    })
            elif category == 'booster':
                for row in result:
                    items.append({
                        'id': row['product_id'],
                        'quantity': row['sell_quantity'],
                        'name': row['product_name'],
                        'icon': row['product_icon']
                    })
            else:  # dungeonkey
                for row in result:
                    items.append({
                        'id': row['dungeonkey_id'],
                        'quantity': row['sell_quantity'],
                        'name': row['dungeonkey_name'],
                        'icon': row['dungeonkey_icon']
                    })
            return items

        except Exception as e:
            print(f"Error getting listing items: {e}")
            return []


    async def _complete_purchase_transaction(self, id_market, category, buyer_user_id):
        table_map = {
            'weapon': ('market_weapon', 'listing_weapon', 'inventory_weapon', 'weapon_id'),
            'booster': ('market_booster', 'listing_booster', 'inventory_product', 'product_id'),
            'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'inventory_dungeonkey', 'dungeonkey_id'),
            'fragments': ('market_fragments', 'listing_fragments', 'inventory_frags', 'frag_id')
        }
        item_selects = {
            'weapon': """
                SELECT lw.weapon_id AS id, lw.sell_quantity AS quantity, w.weapon_name AS name, w.weapon_icon AS icon
                FROM listing_weapon lw
                JOIN weapon w ON lw.weapon_id = w.weapon_id
                WHERE lw.id_market = %s
                FOR UPDATE
            """,
            'booster': """
                SELECT lb.product_id AS id, lb.sell_quantity AS quantity, p.product_name AS name, p.product_icon AS icon
                FROM listing_booster lb
                JOIN product p ON lb.product_id = p.product_id
                WHERE lb.id_market = %s
                FOR UPDATE
            """,
            'dungeonkey': """
                SELECT ld.dungeonkey_id AS id, ld.sell_quantity AS quantity, dk.dungeonkey_name AS name, dk.dungeonkey_icon AS icon
                FROM listing_dungeonkey ld
                JOIN dungeon_key dk ON ld.dungeonkey_id = dk.dungeonkey_id
                WHERE ld.id_market = %s
                FOR UPDATE
            """,
            'fragments': """
                SELECT lf.id_fragment AS id, lf.sell_quantity AS quantity, f.name_fragment AS name, f.fragments_icon AS icon
                FROM listing_fragments lf
                JOIN fragments f ON lf.id_fragment = f.id_fragment
                WHERE lf.id_market = %s
                FOR UPDATE
            """
        }
        market_table, listing_table, inventory_table, item_id_column = table_map[category]

        async with db_transaction() as tx:
            listing_data = await tx.fetchone(
                f"""
                SELECT user_id, total_price, tax
                FROM {market_table}
                WHERE id_market = %s
                FOR UPDATE
                """,
                (id_market,)
            )
            if not listing_data:
                return None, "This listing no longer exists. Someone may have already purchased it."

            seller_user_id = listing_data["user_id"]
            listed_price = listing_data["total_price"]
            tax_rate = listing_data.get("tax", 0) or 0
            seller_receives = listed_price if category == 'fragments' or tax_rate == 0 else listed_price - int(listed_price * (tax_rate / 100))

            buyer = await tx.fetchone(
                "SELECT won_currency FROM user WHERE user_id = %s FOR UPDATE",
                (buyer_user_id,)
            )
            seller = await tx.fetchone(
                "SELECT user_authorid FROM user WHERE user_id = %s FOR UPDATE",
                (seller_user_id,)
            )
            if not buyer:
                return None, "Buyer not found"
            if buyer["won_currency"] < listed_price:
                return None, "Buyer no longer has enough Won for this listing."

            inventory_result = await tx.fetchone(
                "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                (buyer_user_id,)
            )
            if not inventory_result:
                return None, "Buyer inventory not found"
            buyer_inventory_id = inventory_result["inventory_id"]

            listing_items = await tx.fetchall(item_selects[category], (id_market,))
            if not listing_items:
                return None, "No items found in listing"

            await tx.execute(
                "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s",
                (listed_price, buyer_user_id)
            )
            await tx.execute(
                "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s",
                (seller_receives, seller_user_id)
            )

            for item in listing_items:
                existing = await tx.fetchone(
                    f"""
                    SELECT quantiter
                    FROM {inventory_table}
                    WHERE inventory_id = %s AND {item_id_column} = %s
                    FOR UPDATE
                    """,
                    (buyer_inventory_id, item["id"])
                )
                if existing:
                    await tx.execute(
                        f"UPDATE {inventory_table} SET quantiter = quantiter + %s WHERE inventory_id = %s AND {item_id_column} = %s",
                        (item["quantity"], buyer_inventory_id, item["id"])
                    )
                else:
                    await tx.execute(
                        f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantiter) VALUES (%s, %s, %s)",
                        (buyer_inventory_id, item["id"], item["quantity"])
                    )

            await tx.execute(f"DELETE FROM {listing_table} WHERE id_market = %s", (id_market,))
            await tx.execute(f"DELETE FROM {market_table} WHERE id_market = %s", (id_market,))

            return {
                "seller_user_id": seller_user_id,
                "seller_discord_id": seller["user_authorid"] if seller else None,
                "listed_price": listed_price,
                "seller_receives": seller_receives,
                "tax_rate": tax_rate,
                "listing_items": listing_items,
            }, "Purchase completed successfully"


    async def complete_purchase(self, id_market, category, total_price, buyer_user_id):
        """Complete the market purchase transaction"""
        try:
            purchase_data, message = await self._complete_purchase_transaction(id_market, category, buyer_user_id)
            if not purchase_data:
                return False, message

            seller_user_id = purchase_data["seller_user_id"]
            seller_discord_id = purchase_data["seller_discord_id"]
            listed_price = purchase_data["listed_price"]
            seller_receives = purchase_data["seller_receives"]
            tax_rate = purchase_data["tax_rate"]
            listing_items = purchase_data["listing_items"]

            # DM seller (localized to seller's language)
            if seller_discord_id:
                try:
                    seller_user = await self.bot.fetch_user(int(seller_discord_id))
                    if seller_user:
                        seller_lang = await get_user_multilanguage(int(seller_discord_id))
                        items_text = ""
                        for item in listing_items:
                            icon = item.get('icon', '❓')
                            items_text += f"{icon} **{item['name']}** x{item['quantity']}\n"

                        embed = discord.Embed(
                            title=_tr(seller_lang, "💰 **Your Listing Was Purchased!**", "💰 **تم شراء قائمتك!**", "💰 **Votre annonce a été achetée !**"),
                            description=_tr(seller_lang,
                                "Great news! Someone just bought your marketplace listing!",
                                "أخبار رائعة! قام أحدهم بشراء قائمتك في السوق!",
                                "Bonne nouvelle ! Quelqu'un vient d'acheter votre annonce sur le marché !"
                            ),
                            color=0x27ae60
                        )
                        embed.add_field(name=_tr(seller_lang, "Items Sold", "العناصر المباعة", "Objets vendus"), value=items_text, inline=False)
                        embed.add_field(name=_tr(seller_lang, "Listed Price", "السعر المعروض", "Prix affiché"), value=f"**{listed_price:,}** Won", inline=True)
                        if category != 'fragments' and tax_rate > 0:
                            tax_amount = listed_price - seller_receives
                            embed.add_field(name=_tr(seller_lang, "Tax Deducted", "الضريبة المخصومة", "Taxe déduite"),
                                            value=f"**{tax_amount:,}** Won ({tax_rate}%)", inline=True)
                            embed.add_field(name=_tr(seller_lang, "You Received", "المبلغ المستلم", "Montant reçu"),
                                            value=f"**{seller_receives:,}** Won", inline=True)
                        else:
                            embed.add_field(name=_tr(seller_lang, "You Received", "المبلغ المستلم", "Montant reçu"),
                                            value=f"**{seller_receives:,}** Won", inline=True)
                        embed.set_footer(text=_tr(seller_lang,
                            "The Won has been added to your balance!",
                            "تمت إضافة الـ Won إلى رصيدك!",
                            "Les Won ont été ajoutés à votre solde !"
                        ))
                        await seller_user.send(embed=embed)
                except Exception as e:
                    print(f"Error sending DM to seller: {e}")

            # Channel notification (kept in English for server-wide visibility)
            try:
                buyer_discord_query = "SELECT user_authorid FROM user WHERE user_id = %s"
                buyer_discord_result = await execute_query_with_retry(buyer_discord_query, (buyer_user_id,))
                buyer_discord_id = buyer_discord_result[0]["user_authorid"] if buyer_discord_result else None

                buyer_username = "Unknown Buyer"
                seller_username = "Unknown Seller"

                if buyer_discord_id:
                    try:
                        buyer_user = await self.bot.fetch_user(int(buyer_discord_id))
                        buyer_username = buyer_user.display_name if buyer_user else "Unknown Buyer"
                    except Exception:
                        buyer_username = "Unknown Buyer"

                if seller_discord_id:
                    try:
                        seller_user_obj = await self.bot.fetch_user(int(seller_discord_id))
                        seller_username = seller_user_obj.display_name if seller_user_obj else "Unknown Seller"
                    except Exception:
                        seller_username = "Unknown Seller"

                marketplace_channel = self.bot.get_channel(1417138137267699712)

                if marketplace_channel:
                    items_text = ""
                    for item in listing_items:
                        icon = item.get('icon', '❓')
                        items_text += f"{icon} **{item['name']}** x{item['quantity']}\n"

                    embed = discord.Embed(
                        title="💰 **Marketplace Transaction Completed!**",
                        description=f"**{buyer_username}** purchased from **{seller_username}**",
                        color=0x27ae60
                    )
                    embed.add_field(name="Items Sold", value=items_text, inline=False)
                    embed.add_field(name="Total Price", value=f"**{listed_price:,}** Won", inline=True)

                    if category != 'fragments' and tax_rate > 0:
                        tax_amount = listed_price - seller_receives
                        embed.add_field(name="Tax Deducted", value=f"**{tax_amount:,}** Won ({tax_rate}%)", inline=True)
                        embed.add_field(name="Seller Received", value=f"**{seller_receives:,}** Won", inline=True)
                    else:
                        embed.add_field(name="Seller Received", value=f"**{seller_receives:,}** Won", inline=True)

                    embed.set_footer(text="🛒 Marketplace • Transaction completed successfully!")

                    try:
                        await marketplace_channel.send(embed=embed)
                    except discord.HTTPException as e:
                        if e.code == 50035:
                            simple_message = (
                                f"💰 **Marketplace Sale!**\n"
                                f"**{buyer_username}** bought from **{seller_username}**\n"
                                f"Price: **{listed_price:,}** Won\n"
                                f"Items: {len(listing_items)} item(s)"
                            )
                            await marketplace_channel.send(simple_message)
                        else:
                            raise e
                else:
                    print("Marketplace channel not found or bot doesn't have access")

            except Exception as channel_error:
                print(f"Failed to send marketplace notification: {str(channel_error)}")
                pass

            return True, "Purchase completed successfully"

        except Exception as e:
            print(f"Error completing purchase: {e}")
            return False, f"Error: {str(e)}"


    async def transfer_items_to_buyer(self, buyer_inventory_id, listing_items, category):
        """Transfer items from listing to buyer's inventory"""
        try:
            for item in listing_items:
                item_id = item['id']
                quantity = item['quantity']

                if category == 'weapon':
                    check_query = """
                    SELECT quantiter
                    FROM inventory_weapon
                    WHERE inventory_id = %s AND weapon_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (buyer_inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_weapon
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND weapon_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, buyer_inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (buyer_inventory_id, item_id, quantity))

                elif category == 'booster':
                    check_query = """
                    SELECT quantiter
                    FROM inventory_product
                    WHERE inventory_id = %s AND product_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (buyer_inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_product
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND product_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, buyer_inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_product (inventory_id, product_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (buyer_inventory_id, item_id, quantity))

                elif category == 'dungeonkey':
                    check_query = """
                    SELECT quantiter
                    FROM inventory_dungeonkey
                    WHERE inventory_id = %s AND dungeonkey_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (buyer_inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_dungeonkey
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND dungeonkey_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, buyer_inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_dungeonkey (inventory_id, dungeonkey_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (buyer_inventory_id, item_id, quantity))

                elif category == 'fragments':
                    check_query = """
                    SELECT quantiter
                    FROM inventory_frags
                    WHERE inventory_id = %s AND frag_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (buyer_inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_frags
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND frag_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, buyer_inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_frags (inventory_id, frag_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (buyer_inventory_id, item_id, quantity))

        except Exception as e:
            print(f"Error transferring items: {e}")
            raise


    async def remove_market_listing(self, id_market, category):
        """Remove a market listing and its associated items"""
        try:
            if category == 'weapon':
                await execute_query_with_retry("DELETE FROM listing_weapon WHERE id_market = %s", (id_market,))
                await execute_query_with_retry("DELETE FROM market_weapon WHERE id_market = %s", (id_market,))
            elif category == 'booster':
                await execute_query_with_retry("DELETE FROM listing_booster WHERE id_market = %s", (id_market,))
                await execute_query_with_retry("DELETE FROM market_booster WHERE id_market = %s", (id_market,))
            elif category == 'dungeonkey':
                await execute_query_with_retry("DELETE FROM listing_dungeonkey WHERE id_market = %s", (id_market,))
                await execute_query_with_retry("DELETE FROM market_dungeonkey WHERE id_market = %s", (id_market,))
            elif category == 'fragments':
                await execute_query_with_retry("DELETE FROM listing_fragments WHERE id_market = %s", (id_market,))
                await execute_query_with_retry("DELETE FROM market_fragments WHERE id_market = %s", (id_market,))
        except Exception as e:
            print(f"Error removing market listing: {e}")
            raise


    @commands.command(name='mdelist', aliases=["mdl", "mds"])
    @block_during_user_activity(get_user_multilanguage)
    async def delist_listing(self, ctx, listing_id: str):
        """Delist a listing using the new ID format (e.g., a12, b5, c3, d7)"""
        lang = await get_user_multilanguage(ctx.author.id)

        if not listing_id or len(listing_id) < 2:
            await ctx.send(_tr(lang,
                "❌ Invalid listing ID format. Use format like: a12, b5, c3, d7",
                "❌ تنسيق معرف القائمة غير صالح. استخدم مثل: a12 أو b5 أو c3 أو d7",
                "❌ Format d'identifiant d'annonce invalide. Exemple : a12, b5, c3, d7"
            ))
            return

        # Parse the category prefix and numeric ID
        prefix = listing_id[0].lower()
        try:
            numeric_id = int(listing_id[1:])
        except ValueError:
            await ctx.send(_tr(lang,
                "❌ Invalid listing ID format. Use format like: a12, b5, c3, d7",
                "❌ تنسيق معرف القائمة غير صالح. استخدم مثل: a12 أو b5 أو c3 أو d7",
                "❌ Format d'identifiant d'annonce invalide. Exemple : a12, b5, c3, d7"
            ))
            return

        # Map prefix to category
        prefix_to_category = {
            'a': 'weapon',
            'b': 'booster',
            'c': 'dungeonkey',
            'd': 'fragments'
        }

        category = prefix_to_category.get(prefix)
        if not category:
            await ctx.send(_tr(lang,
                "❌ Invalid category prefix. Use: a (weapons), b (boosters), c (dungeon keys), d (fragments)",
                "❌ بادئة فئة غير صالحة. استخدم: a (أسلحة)، b (معززات)، c (مفاتيح زنزانة)، d (شظايا)",
                "❌ Préfixe de catégorie invalide. Utilisez : a (armes), b (boosters), c (clés de donjon), d (fragments)"
            ))
            return

        await self.process_delist(ctx, numeric_id, category)


    async def _delist_listing_transaction(self, id_market, category, user_id):
        table_map = {
            'weapon': ('market_weapon', 'listing_weapon', 'inventory_weapon', 'weapon_id'),
            'booster': ('market_booster', 'listing_booster', 'inventory_product', 'product_id'),
            'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'inventory_dungeonkey', 'dungeonkey_id'),
            'fragments': ('market_fragments', 'listing_fragments', 'inventory_frags', 'frag_id')
        }
        item_selects = {
            'weapon': "SELECT weapon_id AS id, sell_quantity AS quantity FROM listing_weapon WHERE id_market = %s FOR UPDATE",
            'booster': "SELECT product_id AS id, sell_quantity AS quantity FROM listing_booster WHERE id_market = %s FOR UPDATE",
            'dungeonkey': "SELECT dungeonkey_id AS id, sell_quantity AS quantity FROM listing_dungeonkey WHERE id_market = %s FOR UPDATE",
            'fragments': "SELECT id_fragment AS id, sell_quantity AS quantity FROM listing_fragments WHERE id_market = %s FOR UPDATE"
        }
        market_table, listing_table, inventory_table, item_id_column = table_map[category]

        async with db_transaction() as tx:
            ownership_result = await tx.fetchone(
                f"""
                SELECT user_id, total_price
                FROM {market_table}
                WHERE id_market = %s AND user_id = %s
                FOR UPDATE
                """,
                (id_market, user_id)
            )
            if not ownership_result:
                return False

            listing_items = await tx.fetchall(item_selects[category], (id_market,))
            inventory_result = await tx.fetchone(
                "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                (user_id,)
            )
            if not inventory_result:
                raise RuntimeError("Inventory not found for market delist.")

            inventory_id = inventory_result["inventory_id"]
            for item in listing_items:
                existing = await tx.fetchone(
                    f"""
                    SELECT quantiter
                    FROM {inventory_table}
                    WHERE inventory_id = %s AND {item_id_column} = %s
                    FOR UPDATE
                    """,
                    (inventory_id, item["id"])
                )
                if existing:
                    await tx.execute(
                        f"UPDATE {inventory_table} SET quantiter = quantiter + %s WHERE inventory_id = %s AND {item_id_column} = %s",
                        (item["quantity"], inventory_id, item["id"])
                    )
                else:
                    await tx.execute(
                        f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantiter) VALUES (%s, %s, %s)",
                        (inventory_id, item["id"], item["quantity"])
                    )

            await tx.execute(f"DELETE FROM {listing_table} WHERE id_market = %s", (id_market,))
            await tx.execute(f"DELETE FROM {market_table} WHERE id_market = %s AND user_id = %s", (id_market, user_id))
            return True

    async def process_delist(self, ctx, id_market, category):
        """Process delisting for any category"""
        lang = await get_user_multilanguage(ctx.author.id)

        user_info = await self.get_user_info(ctx.author.id)
        if not user_info:
            await ctx.send(_tr(lang,
                "❌ You need to register first! Use `sl register` to get started.",
                "❌ يجب أن تقوم بالتسجيل أولاً! استخدم `sl register` للبدء.",
                "❌ Vous devez d'abord vous inscrire ! Utilisez `sl register` pour commencer."
            ))
            return

        user_id = user_info["user_id"]

        can_proceed, message = await self.check_user_status_for_delist(user_id,lang)
        if not can_proceed:
            await ctx.send(_tr(lang,
                f"⚠️ {message}",
                f"⚠️ {message}",
                f"⚠️ {message}"
            ))
            return

        try:
            delisted = await self._delist_listing_transaction(id_market, category, user_id)
            if not delisted:
                await ctx.send(_tr(lang,
                    f"Listing #{id_market} was not found or is not yours.",
                    f"Listing #{id_market} was not found or is not yours.",
                    f"Listing #{id_market} was not found or is not yours."
                ))
                return

            await ctx.send(_tr(lang,
                f"Listing #{id_market} has been successfully delisted. Your items have been returned to your inventory.",
                f"Listing #{id_market} has been successfully delisted. Your items have been returned to your inventory.",
                f"Listing #{id_market} has been successfully delisted. Your items have been returned to your inventory."
            ))
            return


        except Exception as e:
            await ctx.send(_tr(lang,
                "❌ Error delisting your item.",
                "❌ حدث خطأ أثناء إزالة قائمتك.",
                "❌ Erreur lors du retrait de votre annonce."
            ))
            print(f"Error in delist: {e}")


    async def return_items_to_inventory(self, inventory_id, listing_items, category):
        """Return items from delisted listing back to inventory (no user-facing text here)"""
        try:
            for item in listing_items:
                item_id = item['id']
                quantity = item['quantity']

                if category == 'weapon':
                    check_query = """
                    SELECT quantiter FROM inventory_weapon
                    WHERE inventory_id = %s AND weapon_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_weapon
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND weapon_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

                elif category == 'booster':
                    check_query = """
                    SELECT quantiter FROM inventory_product
                    WHERE inventory_id = %s AND product_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_product
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND product_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_product (inventory_id, product_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

                elif category == 'dungeonkey':
                    check_query = """
                    SELECT quantiter FROM inventory_dungeonkey
                    WHERE inventory_id = %s AND dungeonkey_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_dungeonkey
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND dungeonkey_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_dungeonkey (inventory_id, dungeonkey_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

                elif category == 'fragments':
                    check_query = """
                    SELECT quantiter FROM inventory_frags
                    WHERE inventory_id = %s AND frag_id = %s
                    """
                    existing = await execute_query_with_retry(check_query, (inventory_id, item_id))

                    if existing:
                        update_query = """
                        UPDATE inventory_frags
                        SET quantiter = quantiter + %s
                        WHERE inventory_id = %s AND frag_id = %s
                        """
                        await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
                    else:
                        insert_query = """
                        INSERT INTO inventory_frags (inventory_id, frag_id, quantiter)
                        VALUES (%s, %s, %s)
                        """
                        await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

        except Exception as e:
            print(f"Error returning items to inventory: {e}")
            raise


    @commands.command(name='mwlist')
    async def list_weapon_listings(self, ctx, page: int = 1):
        """List user's weapon listings"""
        await self.show_user_listings(ctx, 'weapon', page - 1)


    @commands.command(name='mdlist')
    async def list_dungeonkey_listings(self, ctx, page: int = 1):
        """List user's dungeon key listings"""
        await self.show_user_listings(ctx, 'dungeonkey', page - 1)


    @commands.command(name='mflist')
    async def list_fragment_listings(self, ctx, page: int = 1):
        """List user's fragment listings"""
        await self.show_user_listings(ctx, 'fragments', page - 1)


    @commands.command(name='mblist')
    async def list_booster_listings(self, ctx, page: int = 1):
        """List user's booster listings"""
        await self.show_user_listings(ctx, 'booster', page - 1)


    @commands.command(name='mylist', aliases=["myl", "myt"])
    async def my_listings(self, ctx, category: str = None, page: int = 1):
        """
        List user's listings by category: sl mylist weapon/dungeon_key/fragments/booster [page]
        """
        lang = await get_user_multilanguage(ctx.author.id)

        if not category:
            await ctx.send(_tr(lang,
                "❌ Please specify a category: `weapon`, `dungeon_key`, `fragments`, or `booster`\nExample: `sl mylist weapon`",
                "❌ يرجى تحديد فئة: `weapon` أو `dungeon_key` أو `fragments` أو `booster`\nمثال: `sl mylist weapon`",
                "❌ Veuillez indiquer une catégorie : `weapon`, `dungeon_key`, `fragments` ou `booster`\nExemple : `sl mylist weapon`"
            ))
            return

        category_map = {
            'weapon': 'weapon',
            'dungeon_key': 'dungeonkey',
            'fragments': 'fragments',
            'booster': 'booster'
        }

        if category not in category_map:
            await ctx.send(_tr(lang,
                "❌ Invalid category! Use: `weapon`, `dungeon_key`, `fragments`, or `booster`",
                "❌ فئة غير صالحة! استخدم: `weapon` أو `dungeon_key` أو `fragments` أو `booster`",
                "❌ Catégorie invalide ! Utilisez : `weapon`, `dungeon_key`, `fragments` ou `booster`"
            ))
            return

        await self.show_user_listings(ctx, category_map[category], page - 1, items_per_page=5)


    @commands.command(name='mlist', aliases=["mls", "mlt"])
    async def market_listings_by_name(self, ctx, *, item_name: str = None):
        """Search and list all market listings for a specific item: sl mlist item_name [page]"""
        lang = await get_user_multilanguage(ctx.author.id)

        if not item_name:
            await ctx.send(_tr(lang,
                "❌ Please specify an item name!\nExample: `sl mlist Iron Sword`",
                "❌ يرجى تحديد اسم العنصر!\nمثال: `sl mlist Iron Sword`",
                "❌ Veuillez indiquer un nom d'objet !\nExemple : `sl mlist Iron Sword`"
            ))
            return

        # Extract page number if provided at the end
        parts = item_name.split()
        page = 1
        if len(parts) > 1 and parts[-1].isdigit():
            page = int(parts[-1])
            item_name = ' '.join(parts[:-1])

        user_info = await self.get_user_info(ctx.author.id)
        if not user_info:
            await ctx.send(_tr(lang,
                "❌ You need to register first! Use `sl register` to get started.",
                "❌ يجب أن تقوم بالتسجيل أولاً! استخدم `sl register` للبدء.",
                "❌ Vous devez d'abord vous inscrire ! Utilisez `sl register` pour commencer."
            ))
            return

        try:
            # Search for item across all categories
            search_queries = [
                ("weapon", "SELECT weapon_id as item_id, weapon_name as item_name FROM weapon WHERE weapon_name LIKE %s"),
                ("dungeonkey", "SELECT dungeonkey_id as item_id, dungeonkey_name as item_name FROM dungeon_key WHERE dungeonkey_name LIKE %s"),
                ("booster", "SELECT product_id as item_id, product_name as item_name FROM product WHERE product_name LIKE %s"),
                ("fragments", "SELECT id_fragment as item_id, name_fragment as item_name FROM fragments WHERE name_fragment LIKE %s")
            ]

            found_category = None
            found_item = None
            search_term = f"%{item_name}%"

            for category, query in search_queries:
                result = await execute_query_with_retry(query, (search_term,))
                if result:
                    found_category = category
                    found_item = result[0]
                    break

            if not found_item:
                await ctx.send(_tr(lang,
                    f"❌ No item found with name containing: `{item_name}`",
                    f"❌ لم يتم العثور على عنصر يحتوي اسمه على: `{item_name}`",
                    f"❌ Aucun objet trouvé contenant : `{item_name}`"
                ))
                return

            await self.show_item_market_listings(ctx, found_category, found_item['item_id'], found_item['item_name'], page - 1)

        except Exception as e:
            print(f"Error in mlist command: {e}")
            await ctx.send(_tr(lang,
                "❌ An error occurred while searching for the item.",
                "❌ حدث خطأ أثناء البحث عن العنصر.",
                "❌ Une erreur s'est produite lors de la recherche de l'objet."
            ))


    async def show_user_listings(self, ctx, category, page=0, items_per_page=5):
        """Show user's own listings by category with pagination"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            user_info = await self.get_user_info(ctx.author.id)
            if not user_info:
                await ctx.send(_tr(lang,
                    "❌ You need to register first! Use `sl register` to get started.",
                    "❌ يجب أن تقوم بالتسجيل أولاً! استخدم `sl register` للبدء.",
                    "❌ Vous devez d'abord vous inscrire ! Utilisez `sl register` pour commencer."
                ))
                return

            table_map = {
                'weapon': ('market_weapon', 'listing_weapon', 'weapon', 'weapon_id', 'weapon_name', 'weapon_icon'),
                'booster': ('market_booster', 'listing_booster', 'product', 'product_id', 'product_name', 'product_icon'),
                'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'dungeon_key', 'dungeonkey_id', 'dungeonkey_name', 'dungeonkey_icon'),
                'fragments': ('market_fragments', 'listing_fragments', 'fragments', 'id_fragment', 'name_fragment', 'fragments_icon')
            }

            if category not in table_map:
                return

            market_table, listing_table, item_table, item_id_col, item_name_col, item_icon_col = table_map[category]
            offset = page * items_per_page

            query = f"""
            SELECT m.id_market, m.user_id, m.total_price,
                l.{item_id_col} AS item_id, l.sell_quantity,
                i.{item_name_col} AS item_name, i.{item_icon_col} AS item_icon
            FROM {market_table} m
            JOIN {listing_table} l ON m.id_market = l.id_market
            JOIN {item_table} i ON l.{item_id_col} = i.{item_id_col}
            WHERE m.user_id = %s
            ORDER BY m.id_market DESC
            LIMIT %s OFFSET %s
            """

            result = await execute_query_with_retry(query, (user_info['user_id'], items_per_page, offset))

            # Get total count for pagination
            count_query = f"""
            SELECT COUNT(*) as total
            FROM {market_table} m
            JOIN {listing_table} l ON m.id_market = l.id_market
            WHERE m.user_id = %s
            """
            count_result = await execute_query_with_retry(count_query, (user_info['user_id'],))
            total_listings = count_result[0]['total'] if count_result else 0
            total_pages = (total_listings + items_per_page - 1) // items_per_page

            category_icons = {'weapon': '🗡️', 'booster': '🧪', 'dungeonkey': '🗝️', 'fragments': '💎'}
            category_display = {'weapon': 'weapon', 'booster': 'booster', 'dungeonkey': 'dungeon_key', 'fragments': 'fragments'}

            embed = discord.Embed(
                title=_tr(lang,
                    f"{category_icons.get(category, '📦')} Your {category_display[category].title()} Listings",
                    f"{category_icons.get(category, '📦')} قوائمك لـ {category_display[category].title()}",
                    f"{category_icons.get(category, '📦')} Vos annonces {category_display[category].title()}"
                ),
                description=_tr(lang,
                    f"📋 **Your marketplace listings** • Page {page + 1}/{max(1, total_pages)} • {total_listings} total listings",
                    f"📋 **قوائمك في السوق** • الصفحة {page + 1}/{max(1, total_pages)} • إجمالي {total_listings} قائمة",
                    f"📋 **Vos annonces sur le marché** • Page {page + 1}/{max(1, total_pages)} • {total_listings} annonces au total"
                ),
                color=0x2ecc71
            )

            if result:
                category_prefixes = {'weapon': 'a', 'booster': 'b', 'dungeonkey': 'c', 'fragments': 'd'}
                prefix = category_prefixes.get(category, 'x')

                for row in result:
                    embed.add_field(
                        name=f"{prefix}{row['id_market']} • {row['item_icon']} {row['item_name']}",
                        value=_tr(lang,
                            f"💰 **Price:** {row['total_price']:,} coins\n📦 **Quantity:** {row['sell_quantity']}",
                            f"💰 **السعر:** {row['total_price']:,} عملة\n📦 **الكمية:** {row['sell_quantity']}",
                            f"💰 **Prix :** {row['total_price']:,} pièces\n📦 **Quantité :** {row['sell_quantity']}"
                        ),
                        inline=False
                    )
            else:
                embed.add_field(
                    name=_tr(lang, "📭 No Listings Found", "📭 لا توجد قوائم", "📭 Aucune annonce"),
                    value=_tr(lang,
                        f"You don't have any {category_display[category]} listings yet.",
                        f"ليس لديك أي قوائم {category_display[category]} بعد.",
                        f"Vous n'avez pas encore d'annonces {category_display[category]}."
                    ),
                    inline=False
                )

            # Add pagination navigation
            if total_pages > 1:
                nav_text = ""
                if page > 0:
                    nav_text += _tr(lang,
                        f"◀️ Previous: `sl mylist {category_display[category]} {page}`\n",
                        f"◀️ السابق: `sl mylist {category_display[category]} {page}`\n",
                        f"◀️ Précédent : `sl mylist {category_display[category]} {page}`\n"
                    )
                if page + 1 < total_pages:
                    nav_text += _tr(lang,
                        f"▶️ Next: `sl mylist {category_display[category]} {page + 2}`",
                        f"▶️ التالي: `sl mylist {category_display[category]} {page + 2}`",
                        f"▶️ Suivant : `sl mylist {category_display[category]} {page + 2}`"
                    )
                if nav_text:
                    embed.add_field(name=_tr(lang, "📄 Navigation", "📄 التنقل", "📄 Navigation"), value=nav_text, inline=False)

            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error showing user listings: {e}")
            await ctx.send(_tr(lang,
                "❌ An error occurred while fetching your listings.",
                "❌ حدث خطأ أثناء جلب قوائمك.",
                "❌ Une erreur s'est produite lors de la récupération de vos annonces."
            ))


    async def show_item_market_listings(self, ctx, category, item_id, item_name, page=0, items_per_page=5):
        """Show all market listings for a specific item with pagination"""
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            table_map = {
                'weapon': ('market_weapon', 'listing_weapon', 'weapon', 'weapon_id', 'weapon_name', 'weapon_icon'),
                'booster': ('market_booster', 'listing_booster', 'product', 'product_id', 'product_name', 'product_icon'),
                'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'dungeon_key', 'dungeonkey_id', 'dungeonkey_name', 'dungeonkey_icon'),
                'fragments': ('market_fragments', 'listing_fragments', 'fragments', 'id_fragment', 'name_fragment', 'fragments_icon')
            }

            if category not in table_map:
                return

            market_table, listing_table, item_table, item_id_col, item_name_col, item_icon_col = table_map[category]
            offset = page * items_per_page

            query = f"""
            SELECT m.id_market, m.user_id, m.total_price, u.user_authorid,
                l.{item_id_col} AS item_id, l.sell_quantity,
                i.{item_name_col} AS item_name, i.{item_icon_col} AS item_icon
            FROM {market_table} m
            JOIN user u ON m.user_id = u.user_id
            JOIN {listing_table} l ON m.id_market = l.id_market
            JOIN {item_table} i ON l.{item_id_col} = i.{item_id_col}
            WHERE l.{item_id_col} = %s
            ORDER BY m.total_price ASC
            LIMIT %s OFFSET %s
            """

            result = await execute_query_with_retry(query, (item_id, items_per_page, offset))

            # Get total count for pagination
            count_query = f"""
            SELECT COUNT(*) as total
            FROM {market_table} m
            JOIN {listing_table} l ON m.id_market = l.id_market
            WHERE l.{item_id_col} = %s
            """
            count_result = await execute_query_with_retry(count_query, (item_id,))
            total_listings = count_result[0]['total'] if count_result else 0
            total_pages = (total_listings + items_per_page - 1) // items_per_page

            category_icons = {'weapon': '🗡️', 'booster': '🧪', 'dungeonkey': '🗝️', 'fragments': '💎'}

            embed = discord.Embed(
                title=_tr(lang,
                    f"{category_icons.get(category, '📦')} Market Listings for: {item_name}",
                    f"{category_icons.get(category, '📦')} قوائم السوق لـ: {item_name}",
                    f"{category_icons.get(category, '📦')} Annonces pour : {item_name}"
                ),
                description=_tr(lang,
                    f"🏪 **All marketplace listings** • Page {page + 1}/{max(1, total_pages)} • {total_listings} total listings",
                    f"🏪 **كل قوائم السوق** • الصفحة {page + 1}/{max(1, total_pages)} • إجمالي {total_listings} قائمة",
                    f"🏪 **Toutes les annonces du marché** • Page {page + 1}/{max(1, total_pages)} • {total_listings} annonces au total"
                ),
                color=0x3498db
            )

            if result:
                category_prefixes = {'weapon': 'a', 'booster': 'b', 'dungeonkey': 'c', 'fragments': 'd'}
                prefix = category_prefixes.get(category, 'x')

                for row in result:
                    username = await self.get_username_from_discord(row["user_authorid"]) if row["user_authorid"] else _tr(lang, "Unknown User", "مستخدم غير معروف", "Utilisateur inconnu")

                    embed.add_field(
                        name=f"{prefix}{row['id_market']} • {row['item_icon']} {row['item_name']}",
                        value=_tr(lang,
                            f"👤 **Seller:** {username}\n💰 **Price:** {row['total_price']:,} coins\n📦 **Quantity:** {row['sell_quantity']}",
                            f"👤 **البائع:** {username}\n💰 **السعر:** {row['total_price']:,} عملة\n📦 **الكمية:** {row['sell_quantity']}",
                            f"👤 **Vendeur :** {username}\n💰 **Prix :** {row['total_price']:,} pièces\n📦 **Quantité :** {row['sell_quantity']}"
                        ),
                        inline=False
                    )
            else:
                embed.add_field(
                    name=_tr(lang, "📭 No Listings Found", "📭 لا توجد قوائم", "📭 Aucune annonce"),
                    value=_tr(lang,
                        "No one is currently selling this item.",
                        "لا أحد يبيع هذا العنصر حالياً.",
                        "Personne ne vend cet objet pour le moment."
                    ),
                    inline=False
                )

            if total_pages > 1:
                nav_text = ""
                if page > 0:
                    nav_text += _tr(lang,
                        f"◀️ Previous: `sl mlist {item_name} {page}`\n",
                        f"◀️ السابق: `sl mlist {item_name} {page}`\n",
                        f"◀️ Précédent : `sl mlist {item_name} {page}`\n"
                    )
                if page + 1 < total_pages:
                    nav_text += _tr(lang,
                        f"▶️ Next: `sl mlist {item_name} {page + 2}`",
                        f"▶️ التالي: `sl mlist {item_name} {page + 2}`",
                        f"▶️ Suivant : `sl mlist {item_name} {page + 2}`"
                    )
                if nav_text:
                    embed.add_field(name=_tr(lang, "📄 Navigation", "📄 التنقل", "📄 Navigation"), value=nav_text, inline=False)

            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error showing item market listings: {e}")
            await ctx.send(_tr(lang,
                "❌ An error occurred while fetching the listings.",
                "❌ حدث خطأ أثناء جلب القوائم.",
                "❌ Une erreur s'est produite lors de la récupération des annonces."
            ))


    async def show_user_listingsd(self, ctx, category, page=0, items_per_page=5):
        """Show user's own listings for a specific category (alternate view)"""
        lang = await get_user_multilanguage(ctx.author.id)

        user_info = await self.get_user_info(ctx.author.id)
        if not user_info:
            await ctx.send(_tr(lang,
                "❌ You need to register first! Use `sl register` to get started.",
                "❌ يجب أن تقوم بالتسجيل أولاً! استخدم `sl register` للبدء.",
                "❌ Vous devez d'abord vous inscrire ! Utilisez `sl register` pour commencer."
            ))
            return

        user_id = user_info["user_id"]

        try:
            table_map = {
                'weapon': ('market_weapon', 'listing_weapon', 'weapon', 'weapon_id', 'weapon_name', 'weapon_icon'),
                'booster': ('market_booster', 'listing_booster', 'product', 'product_id', 'product_name', 'product_icon'),
                'dungeonkey': ('market_dungeonkey', 'listing_dungeonkey', 'dungeon_key', 'dungeonkey_id', 'dungeonkey_name', 'dungeonkey_icon'),
                'fragments': ('market_fragments', 'listing_fragments', 'fragments', 'id_fragment', 'name_fragment', 'fragments_icon')
            }

            if category not in table_map:
                return

            market_table, listing_table, item_table, item_id_col, item_name_col, item_icon_col = table_map[category]
            offset = page * items_per_page

            query = f"""
            SELECT m.id_market, m.total_price,
                l.{item_id_col} AS item_id, l.sell_quantity,
                i.{item_name_col} AS item_name, i.{item_icon_col} AS item_icon
            FROM {market_table} m
            JOIN {listing_table} l ON m.id_market = l.id_market
            JOIN {item_table} i ON l.{item_id_col} = i.{item_id_col}
            WHERE m.user_id = %s
            ORDER BY m.id_market ASC
            LIMIT %s OFFSET %s
            """

            result = await execute_query_with_retry(query, (user_id, items_per_page, offset))

            category_names = {
                'weapon': _tr(lang, '🗡️ Weapons', '🗡️ أسلحة', '🗡️ Armes'),
                'booster': _tr(lang, '🧪 Boosters', '🧪 معززات', '🧪 Boosters'),
                'dungeonkey': _tr(lang, '🗝️ Dungeon Keys', '🗝️ مفاتيح الزنزانات', '🗝️ Clés de donjon'),
                'fragments': _tr(lang, '💎 Fragments', '💎 شظايا', '💎 Fragments')
            }

            embed = discord.Embed(
                title=_tr(lang,
                    f"📋 Your {category_names.get(category, category.title())} Listings",
                    f"📋 قوائمك لـ {category_names.get(category, category.title())}",
                    f"📋 Vos annonces {category_names.get(category, category.title())}"
                ),
                description=_tr(lang,
                    "🏪 **Your marketplace empire!** Here are all your active listings in this category.",
                    "🏪 **إمبراطوريتك في السوق!** هذه جميع قوائمك النشطة في هذه الفئة.",
                    "🏪 **Votre empire du marché !** Voici toutes vos annonces actives dans cette catégorie."
                ),
                color=0x9b59b6
            )

            if result:
                # Group items by id_market
                grouped = {}
                for row in result:
                    mid = row['id_market']
                    if mid not in grouped:
                        grouped[mid] = {"total_price": row['total_price'], "items": []}
                    grouped[mid]["items"].append({
                        "icon": row['item_icon'],
                        "name": row['item_name'],
                        "quantity": row['sell_quantity']
                    })

                category_prefixes = {'weapon': 'a', 'booster': 'b', 'dungeonkey': 'c', 'fragments': 'd'}
                prefix = category_prefixes.get(category, 'x')

                listings_text = ""
                for mid, data in grouped.items():
                    price_formatted = f"{data['total_price']:,}"
                    items_text = ", ".join(f"{it['icon']} {it['name']} x{it['quantity']}" for it in data["items"])
                    listings_text += _tr(lang,
                        f"**#{prefix}{mid}** - **{price_formatted}** <:won:1304034228966658048>\nItems: {items_text}\n\n",
                        f"**#{prefix}{mid}** - **{price_formatted}** <:won:1304034228966658048>\nالعناصر: {items_text}\n\n",
                        f"**#{prefix}{mid}** - **{price_formatted}** <:won:1304034228966658048>\nObjets : {items_text}\n\n"
                    )

                embed.add_field(
                    name=_tr(lang, f"📦 **Your Active Listings** (Page {page + 1})",
                            f"📦 **قوائمك النشطة** (الصفحة {page + 1})",
                            f"📦 **Vos annonces actives** (Page {page + 1})"),
                    value=listings_text,
                    inline=False
                )

                prefix_map = {'weapon': 'a', 'booster': 'b', 'dungeonkey': 'c', 'fragments': 'd'}
                example = f"{prefix_map.get(category, 'a')}{list(grouped.keys())[0]}"
                embed.add_field(
                    name=_tr(lang, "🗑️ **How to Delist**", "🗑️ **كيفية الإزالة**", "🗑️ **Comment retirer**"),
                    value=_tr(lang,
                        f"Use `sl mdelist <listing_id>` to remove a listing\nExample: `sl mdelist {example}`",
                        f"استخدم `sl mdelist <listing_id>` لإزالة قائمة\nمثال: `sl mdelist {example}`",
                        f"Utilisez `sl mdelist <listing_id>` pour retirer une annonce\nExemple : `sl mdelist {example}`"
                    ),
                    inline=False
                )
            else:
                embed.add_field(
                    name=_tr(lang, "📭 **No Listings Found**", "📭 **لا توجد قوائم**", "📭 **Aucune annonce**"),
                    value=_tr(lang,
                        "You don't have any active listings in this category. Create some listings to start earning!",
                        "ليست لديك أي قوائم نشطة في هذه الفئة. أنشئ بعض القوائم لبدء الكسب!",
                        "Vous n'avez aucune annonce active dans cette catégorie. Créez-en pour commencer à gagner !"
                    ),
                    inline=False
                )

            embed.set_footer(text=_tr(lang,
                "💡 Tip: Use the delist command to remove listings and get your items back",
                "💡 نصيحة: استخدم أمر الإزالة لاسترجاع عناصرِك",
                "💡 Astuce : utilisez la commande de retrait pour récupérer vos objets"
            ))

            has_next_page = len(result) == items_per_page
            if page > 0 or has_next_page:
                view = UserListingsView(category, page, has_next_page, ctx.author.id)
                await ctx.send(embed=embed, view=view)
            else:
                await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error showing user listings: {e}")
            await ctx.send(_tr(lang,
                "❌ Error loading your listings. Please try again.",
                "❌ خطأ أثناء تحميل قوائمك. حاول مرة أخرى.",
                "❌ Erreur lors du chargement de vos annonces. Veuillez réessayer."
            ))


# ====== UI VIEWS (localized user-facing strings) ======

class MarketListingView(discord.ui.View):
    def __init__(self, author_id, session):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.session = session

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your listing.",
                "❌ هذه ليست قائمتك.",
                "❌ Ceci n'est pas votre annonce."
            ), ephemeral=True)
            return

        # Remove session and from market_session table
        cog = interaction.client.get_cog('MarketCommands')
        if cog and self.author_id in cog.active_market_sessions:
            user_id = cog.active_market_sessions[self.author_id].get('user_id')
            if user_id:
                await cog.remove_user_from_market_session(user_id)
            del cog.active_market_sessions[self.author_id]

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=_tr(lang, "❌ **Listing Cancelled**", "❌ **تم إلغاء القائمة**", "❌ **Annonce annulée**"),
                description=_tr(lang,
                    "Your market listing has been cancelled. No items were listed for sale.",
                    "تم إلغاء قائمتك في السوق. لم يتم عرض أي عناصر للبيع.",
                    "Votre annonce a été annulée. Aucun objet n'a été mis en vente."
                ),
                color=0xe74c3c
            ),
            view=None
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your listing.",
                "❌ هذه ليست قائمتك.",
                "❌ Ceci n'est pas votre annonce."
            ), ephemeral=True)
            return

        listing = self.session.get('current_listing', [])
        if not listing:
            await interaction.response.send_message(_tr(lang,
                "❌ You haven't added any items to your listing yet!",
                "❌ لم تُضِف أي عناصر إلى قائمتك بعد!",
                "❌ Vous n'avez encore ajouté aucun objet à votre annonce !"
            ), ephemeral=True)
            return

        cog = interaction.client.get_cog('MarketCommands')
        if not cog:
            await interaction.response.send_message(_tr(lang,
                "❌ Error accessing market system.",
                "❌ خطأ في الوصول إلى نظام السوق.",
                "❌ Erreur d'accès au système du marché."
            ), ephemeral=True)
            return

        verification_result = await self.verify_final_inventory(cog, self.session)
        if not verification_result[0]:
            await interaction.response.send_message(f"❌ {verification_result[1]}", ephemeral=True)
            return

        modal = PriceModal(self.author_id, self.session, cog)
        await interaction.response.send_modal(modal)

    async def verify_final_inventory(self, cog, session):
        """Final verification that user still has all items"""
        try:
            listing = session.get('current_listing', [])
            category = session.get('category')
            inventory_id = session.get('inventory_id')

            for item in listing:
                item_id = item['id']
                required_quantity = item['quantity']

                if category == 'weapon':
                    table = 'inventory_weapon'
                    id_field = 'weapon_id'
                elif category == 'booster':
                    table = 'inventory_product'
                    id_field = 'product_id'
                elif category == 'dungeonkey':
                    table = 'inventory_dungeonkey'
                    id_field = 'dungeonkey_id'
                elif category == 'fragments':
                    table = 'inventory_frags'
                    id_field = 'frag_id'
                else:
                    return False, "Invalid category"

                check_query = f"SELECT quantiter FROM {table} WHERE inventory_id = %s AND {id_field} = %s"
                result = await execute_query_with_retry(check_query, (inventory_id, item_id))

                if not result:
                    return False, f"You no longer have {item['name']} in your inventory"
                available_quantity = result[0]["quantiter"]
                if available_quantity < required_quantity:
                    return False, f"You only have {available_quantity} of {item['name']}, but listing requires {required_quantity}"

            return True, "All items verified"

        except Exception as e:
            print(f"Error in final verification: {e}")
            return False, "Error verifying inventory"


class PurchaseConfirmView(discord.ui.View):
    def __init__(self, author_id, id_market, category, total_price, buyer_user_id):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.id_market = id_market
        self.category = category
        self.total_price = total_price
        self.buyer_user_id = buyer_user_id

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your purchase.",
                "❌ هذه ليست عملية الشراء الخاصة بك.",
                "❌ Cet achat ne vous appartient pas."
            ), ephemeral=True)
            return

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=_tr(lang, "❌ Purchase Cancelled", "❌ تم إلغاء الشراء", "❌ Achat annulé"),
                color=0xe74c3c
            ),
            view=None
        )

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your purchase.",
                "❌ هذه ليست عملية الشراء الخاصة بك.",
                "❌ Cet achat ne vous appartient pas."
            ), ephemeral=True)
            return

        await interaction.response.defer()

        cog = interaction.client.get_cog('MarketCommands')
        if not cog:
            await interaction.followup.send(_tr(lang,
                "❌ Error accessing market system.",
                "❌ خطأ في الوصول إلى نظام السوق.",
                "❌ Erreur d'accès au système du marché."
            ), ephemeral=True)
            return

        success, message = await cog.complete_purchase(self.id_market, self.category, self.total_price, self.buyer_user_id)

        if success:
            embed = discord.Embed(
                title=_tr(lang, "✅ Purchase Successful!", "✅ تم الشراء بنجاح!", "✅ Achat réussi !"),
                description=_tr(lang,
                    f"You have successfully purchased the listing for **{self.total_price:,}** Won!",
                    f"لقد اشتريت القائمة بنجاح مقابل **{self.total_price:,}** Won!",
                    f"Vous avez acheté l'annonce pour **{self.total_price:,}** Won !"
                ),
                color=0x27ae60
            )
            embed.set_footer(text=_tr(lang,
                "Items have been added to your inventory",
                "تمت إضافة العناصر إلى جردك",
                "Les objets ont été ajoutés à votre inventaire"
            ))
        else:
            if "no longer exists" in message:
                await interaction.followup.send(f"❌ {message}", ephemeral=True)
                return
            embed = discord.Embed(
                title=_tr(lang, "❌ Purchase Failed", "❌ فشل الشراء", "❌ Échec de l'achat"),
                description=_tr(lang, f"Error: {message}", f"خطأ: {message}", f"Erreur : {message}"),
                color=0xe74c3c
            )

        await interaction.edit_original_response(embed=embed, view=None)


class UserListingsView(discord.ui.View):
    def __init__(self, category, current_page, has_next_page, author_id):
        super().__init__(timeout=300)
        self.category = category
        self.current_page = current_page
        self.has_next_page = has_next_page
        self.author_id = author_id

        if current_page > 0:
            self.add_item(UserListingsPreviousButton(category, current_page, author_id))
        if has_next_page:
            self.add_item(UserListingsNextButton(category, current_page, author_id))


class UserListingsPreviousButton(discord.ui.Button):
    def __init__(self, category, current_page, author_id):
        super().__init__(label="◀️ Previous", style=discord.ButtonStyle.secondary)
        self.category = category
        self.current_page = current_page
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your listing view.",
                "❌ هذه ليست صفحة قوائمك.",
                "❌ Ceci n'est pas votre vue d'annonces."
            ), ephemeral=True)
            return

        await interaction.response.defer()
        cog = interaction.client.get_cog('MarketCommands')
        if cog:
            class MockContext:
                def __init__(self, author, channel):
                    self.author = author
                    self.channel = channel
                async def send(self, *args, **kwargs):
                    return await interaction.edit_original_response(*args, **kwargs)
            mock_ctx = MockContext(interaction.user, interaction.channel)
            await cog.show_user_listings(mock_ctx, self.category, self.current_page - 1)


class UserListingsNextButton(discord.ui.Button):
    def __init__(self, category, current_page, author_id):
        super().__init__(label="Next ▶️", style=discord.ButtonStyle.secondary)
        self.category = category
        self.current_page = current_page
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your listing view.",
                "❌ هذه ليست صفحة قوائمك.",
                "❌ Ceci n'est pas votre vue d'annonces."
            ), ephemeral=True)
            return

        await interaction.response.defer()
        cog = interaction.client.get_cog('MarketCommands')
        if cog:
            class MockContext:
                def __init__(self, author, channel):
                    self.author = author
                    self.channel = channel
                async def send(self, *args, **kwargs):
                    return await interaction.edit_original_response(*args, **kwargs)
            mock_ctx = MockContext(interaction.user, interaction.channel)
            await cog.show_user_listings(mock_ctx, self.category, self.current_page + 1)


class CategoryListingsView(discord.ui.View):
    def __init__(self, category, current_page, has_next_page):
        super().__init__(timeout=300)
        self.category = category
        self.current_page = current_page
        self.has_next_page = has_next_page

        if current_page > 0:
            self.add_item(PreviousPageButton(category, current_page))
        if has_next_page:
            self.add_item(NextPageButton(category, current_page))
        self.add_item(BackToMainButton())


class PreviousPageButton(discord.ui.Button):
    def __init__(self, category, current_page):
        super().__init__(label="◀️ Previous", style=discord.ButtonStyle.secondary)
        self.category = category
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        cog = interaction.client.get_cog('MarketCommands')
        if cog:
            await cog.show_category_listings(interaction, self.category, self.current_page - 1)


class NextPageButton(discord.ui.Button):
    def __init__(self, category, current_page):
        super().__init__(label="Next ▶️", style=discord.ButtonStyle.secondary)
        self.category = category
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        cog = interaction.client.get_cog('MarketCommands')
        if cog:
            await cog.show_category_listings(interaction, self.category, self.current_page + 1)


class BackToMainButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🏠 Back to Main", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        lang = await get_user_multilanguage(interaction.user.id)
        await interaction.response.defer()

        embed = discord.Embed(
            title=_tr(lang,
                "🏪 Solo Leveling Marketplace",
                "🏪 سوق Solo Leveling",
                "🏪 Marché Solo Leveling"
            ),
            description=_tr(lang,
                "🌟 **Welcome to the ultimate trading hub!** 🌟\n\nDiscover rare weapons, powerful boosters, mystical dungeon keys, and precious fragments from fellow hunters. Every listing tells a story of adventure and conquest!",
                "🌟 **مرحباً بك في مركز التداول الأروع!** 🌟\n\nاكتشف أسلحة نادرة، معززات قوية، ومفاتيح زنزانات غامضة، وشظايا ثمينة من الصيادين الآخرين. كل قائمة تحكي قصة مغامرة ونصر!",
                "🌟 **Bienvenue dans le hub d'échange ultime !** 🌟\n\nDécouvrez des armes rares, des boosters puissants, des clés de donjon mystiques et des fragments précieux. Chaque annonce raconte une histoire d'aventure et de conquête !"
            ),
            color=0x3498db
        )

        embed.set_image(url=MAIN_SHOP_IMAGE)

        embed.add_field(
            name=_tr(lang, "🛍️ **Available Categories**", "🛍️ **الفئات المتاحة**", "🛍️ **Catégories disponibles**"),
            value=_tr(lang,
                "🗡️ **Weapons** - Legendary blades and mystical armaments\n🧪 **Boosters** - Power-enhancing elixirs and consumables\n🗝️ **Dungeon Keys** - Access to hidden realms and treasures\n💎 **Fragments** - Rare crafting materials and essence",
                "🗡️ **أسلحة** - سيوف أسطورية وتسليح غامض\n🧪 **معززات** - إكسير ومستهلكات تعزز القوة\n🗝️ **مفاتيح زنزانات** - وصول إلى عوالم وخزائن خفية\n💎 **شظايا** - مواد صنع نادرة وخلاصة",
                "🗡️ **Armes** - Lames légendaires et armements mystiques\n🧪 **Boosters** - Élixirs et consommables renforçant la puissance\n🗝️ **Clés de donjon** - Accès à des royaumes et trésors cachés\n💎 **Fragments** - Matériaux d'artisanat rares et essences"
            ),
            inline=False
        )

        embed.set_footer(text=_tr(lang,
            "🎯 Select a category below to explore amazing deals from hunters worldwide!",
            "🎯 اختر فئة أدناه لاستكشاف عروض مذهلة من الصيادين حول العالم!",
            "🎯 Sélectionnez une catégorie ci-dessous pour explorer des offres incroyables du monde entier !"
        ))

        select = CategorySelect(lang)
        view = MarketView(select)
        await interaction.edit_original_response(embed=embed, view=view)


class CategorySelect(discord.ui.Select):
    def __init__(self, lang: int = 0):
        # Localized placeholder
        placeholder = _tr(
            lang,
            "🎯 Choose your adventure - Select a category...",
            "🎯 اختر مغامرتك — اختر فئة...",
            "🎯 Choisissez votre aventure — Sélectionnez une catégorie..."
        )

        # Localized options
        if lang == 1:  # Arabic
            options = [
                discord.SelectOption(label="🗡️ أسلحة", value="weapon", description="تصفح أسلحة أسطورية وتسليح غامض"),
                discord.SelectOption(label="🧪 معززات", value="booster", description="اكتشف عناصر تعزيز قوية"),
                discord.SelectOption(label="🗝️ مفاتيح الزنزانات", value="dungeonkey", description="اعثر على مفاتيح لفتح عوالم خفية"),
                discord.SelectOption(label="💎 شظايا", value="fragments", description="تاجر بمواد صنع نادرة")
            ]
        elif lang == 3:  # French
            options = [
                discord.SelectOption(label="🗡️ Armes", value="weapon", description="Parcourez des armes légendaires et mystiques"),
                discord.SelectOption(label="🧪 Boosters", value="booster", description="Découvrez des objets de renforcement puissants"),
                discord.SelectOption(label="🗝️ Clés de donjon", value="dungeonkey", description="Trouvez des clés pour déverrouiller des royaumes cachés"),
                discord.SelectOption(label="💎 Fragments", value="fragments", description="Échangez des matériaux d'artisanat rares")
            ]
        else:  # English (default)
            options = [
                discord.SelectOption(label="🗡️ Weapons", value="weapon", description="Browse legendary weapons and armaments"),
                discord.SelectOption(label="🧪 Boosters", value="booster", description="Discover powerful enhancement items"),
                discord.SelectOption(label="🗝️ Dungeon Keys", value="dungeonkey", description="Find keys to unlock hidden realms"),
                discord.SelectOption(label="💎 Fragments", value="fragments", description="Trade rare crafting materials")
            ]

        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        lang = await get_user_multilanguage(interaction.user.id)
        try:
            await interaction.response.defer()
            category = self.values[0]

            cog = interaction.client.get_cog('MarketCommands')
            if cog:
                await cog.show_category_listings(interaction, category)
        except Exception as e:
            print(f"Error in category select callback: {e}")
            try:
                await interaction.followup.send(_tr(
                    lang,
                    "❌ An error occurred while loading the category. Please try again.",
                    "❌ حدث خطأ أثناء تحميل الفئة. يرجى المحاولة مرة أخرى.",
                    "❌ Une erreur s'est produite lors du chargement de la catégorie. Veuillez réessayer."
                ), ephemeral=True)
            except:
                pass



class MarketView(discord.ui.View):
    def __init__(self, select):
        super().__init__(timeout=300)
        self.add_item(select)


class MarketCreateCategorySelect(discord.ui.Select):
    def __init__(self, author_id, user_id, inventory_id):
        self.author_id = author_id
        self.user_id = user_id
        self.inventory_id = inventory_id

        options = [
            discord.SelectOption(label="🗡️ Weapons", value="weapon", description="List your legendary weapons for sale"),
            discord.SelectOption(label="🧪 Boosters", value="booster", description="Sell your enhancement consumables"),
            discord.SelectOption(label="🗝️ Dungeon Keys", value="dungeonkey", description="Trade access to hidden dungeons"),
            discord.SelectOption(label="💎 Fragments", value="fragments", description="Exchange rare crafting materials")
        ]
        super().__init__(placeholder="🎯 Choose your specialty - What will you sell?", options=options)

    async def callback(self, interaction: discord.Interaction):
        lang = await get_user_multilanguage(interaction.user.id)
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(_tr(lang,
                "❌ This is not your market creation session.",
                "❌ هذه ليست جلسة إنشاء السوق الخاصة بك.",
                "❌ Ceci n'est pas votre session de création de marché."
            ), ephemeral=True)
            return

        await interaction.response.defer()
        category = self.values[0]

        cog = interaction.client.get_cog('MarketCommands')
        if cog:
            cog.active_market_sessions[self.author_id] = {
                'category': category,
                'user_id': self.user_id,
                'inventory_id': self.inventory_id,
                'current_listing': [],
                'start_time': time.time()
            }

            embed = discord.Embed(
                title=_tr(lang,
                    f"📝 **Creating {category.title()} Listing**",
                    f"📝 **إنشاء قائمة {category.title()}**",
                    f"📝 **Création d'une annonce {category.title()}**"
                ),
                description=_tr(lang,
                    "🎯 **Building your marketplace empire!** Add items to create an irresistible offer for fellow hunters.",
                    "🎯 **نَبني إمبراطوريتك في السوق!** أضف عناصر لابتكار عرض لا يُقاوَم للصيادين الآخرين.",
                    "🎯 **Bâtissez votre empire du marché !** Ajoutez des objets pour créer une offre irrésistible."
                ),
                color=0xe74c3c
            )

            embed.add_field(
                name=_tr(lang, "⚡ **Quick Commands**", "⚡ **أوامر سريعة**", "⚡ **Commandes rapides**"),
                value=_tr(lang,
                    "📥 `sl madd item_name, quantity` - Add item to your listing\n📤 `sl mremove item_name` - Remove item from listing",
                    "📥 `sl madd item_name, quantity` - أضف عنصراً إلى قائمتك\n📤 `sl mremove item_name` - أزل عنصراً من القائمة",
                    "📥 `sl madd item_name, quantity` - Ajouter un objet à votre annonce\n📤 `sl mremove item_name` - Retirer un objet de l'annonce"
                ),
                inline=False
            )

            embed.add_field(
                name=_tr(lang, "📦 **Your Listing**", "📦 **قائمتك**", "📦 **Votre annonce**"),
                value=_tr(lang,
                    "*🔍 No items added yet - start building your collection!*",
                    "*🔍 لم تتم إضافة أي عناصر بعد — ابدأ ببناء تشكيلتك !*",
                    "*🔍 Aucun objet ajouté pour le moment — commencez à composer votre lot !*"
                ),
                inline=False
            )

            embed.set_footer(text=_tr(lang,
                "⏰ Session expires in 5 minutes | Add items then click Confirm to proceed",
                "⏰ تنتهي الجلسة خلال 5 دقائق | أضف العناصر ثم اضغط Confirm للمتابعة",
                "⏰ La session expire dans 5 minutes | Ajoutez des objets puis cliquez sur Confirm pour continuer"
            ))

            view = MarketListingView(self.author_id, cog.active_market_sessions[self.author_id])
            message = await interaction.edit_original_response(embed=embed, view=view)

            cog.active_market_sessions[self.author_id]['last_message'] = message

            await asyncio.sleep(300)
            if self.author_id in cog.active_market_sessions:
                await cog.remove_user_from_market_session(self.user_id)
                del cog.active_market_sessions[self.author_id]
                try:
                    await interaction.edit_original_response(view=None)
                    await interaction.followup.send(_tr(lang,
                        "⏰ Market creation session expired.",
                        "⏰ انتهت جلسة إنشاء السوق.",
                        "⏰ La session de création du marché a expiré."
                    ), ephemeral=True)
                except:
                    pass


class MarketCreateView(discord.ui.View):
    def __init__(self, select):
        super().__init__(timeout=120)
        self.add_item(select)


async def setup(bot):
    await bot.add_cog(MarketCommands(bot))
