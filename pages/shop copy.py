import discord
from discord.ext import commands
from discord import ButtonStyle, SelectOption
from typing import List, Dict, Union, Tuple
from enum import Enum
import time
import asyncio
from datetime import datetime, timedelta
from utils.database import execute_query_with_retry, get_metrics

# =========================
# Multilanguage helpers
# =========================
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

async def get_user_multilanguage(user_authorid: int) -> int:
    """Fetch user's language code from DB (0=en, 1=ar, 3=fr)."""
    rows = await execute_query_with_retry(
        "SELECT multilanguage FROM user WHERE user_authorid = %s",
        (str(user_authorid),)
    )
    if rows and rows[0].get("multilanguage") is not None:
        return int(rows[0]["multilanguage"])
    return 0

# Alias as requested
async def getlang(user_authorid: int) -> int:
    return await get_user_multilanguage(user_authorid)

# Constants
MAIN_SHOP_IMAGE = "https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/01/solo-leveling-anime-to-premiere-this-week-on-crunchyroll.jpg"
WEAPONS_SHOP_IMAGE = "https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/02/solo-leveling-6-strongest-weapons-of-sung-jinwoo.jpg"
BOOSTERS_SHOP_IMAGE = "https://static0.gamerantimages.com/wordpress/wp-content/uploads/2024/03/orb-of-avarice.jpg"
KEYS_SHOP_IMAGE = "https://i.imgur.com/TN4CdVv.png"  # Replace with actual image URL
PICKAXES_SHOP_IMAGE = "https://i.imgur.com/ariM9iv.png"  # Replace with an actual image URL
SKILLS_SHOP_IMAGE = "https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/01/solo-leveling-anime-to-premiere-this-week-on-crunchyroll.jpg"

# Update ItemType enum
class ItemType(Enum):
    WEAPON = "Weapon"
    BOOSTER = "Booster"
    KEY = "Key"
    PICKAXE = "Pickaxe"
    SKILL = "Skill"

# Modify ShopCategoryView
class ShopCategorySelect(discord.ui.Select):
    def __init__(self, cog, original_user, lang: int):
        self.cog = cog
        self.original_user = original_user
        self.lang = lang
       
        options = [
            SelectOption(label=_tr(lang, "Weapons", "الأسلحة", "Armes"), value="WEAPON", emoji="⚔️"),
            SelectOption(label=_tr(lang, "Boosters", "المعززات", "Boosters"), value="BOOSTER", emoji="🔮"),
            SelectOption(label=_tr(lang, "Dungeon Keys", "مفاتيح الأبراج", "Clés de Donjon"), value="KEY", emoji="🗝️"),
            SelectOption(label=_tr(lang, "Pickaxes", "المعاول", "Pioches"), value="PICKAXE", emoji="⛏️"),
            SelectOption(label=_tr(lang, "Skills", "المهارات", "Compétences"), value="SKILL", emoji="🎯")
        ]
       
        super().__init__(
            placeholder=_tr(lang, "Choose a Shop Category", "اختر فئة المتجر", "Choisissez une catégorie de boutique"),
            min_values=1,
            max_values=1,
            options=options
        )
   
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                _tr(self.lang,
                    "You cannot interact with someone else's shop menu.",
                    "لا يمكنك التفاعل مع قائمة متجر شخص آخر.",
                    "Vous ne pouvez pas interagir avec le menu de boutique de quelqu'un d'autre."
                ),
                ephemeral=True
            )
            return
           
        selected_category = ItemType[self.values[0]]
        await self.cog.show_category(interaction, selected_category)

class ShopCategoryView(discord.ui.View):
    def __init__(self, cog, original_user, lang: int):
        super().__init__(timeout=180.0)
        self.original_user = original_user
        self.add_item(ShopCategorySelect(cog, original_user, lang))

class ShopPaginationView(discord.ui.View):
    def __init__(self, cog, category: ItemType, original_user, lang: int):
        super().__init__(timeout=180.0)
        self.cog = cog
        self.category = category
        self.current_page = 1
        self.original_user = original_user
        self.items = []  # Store items to avoid repeated database calls
        self.lang = lang

        self.next_button = discord.ui.Button(
            label=_tr(lang, "Next", "التالي", "Suivant"),
            style=ButtonStyle.primary,
            emoji="▶️"
        )
        self.next_button.callback = self.next_page

        self.prev_button = discord.ui.Button(
            label=_tr(lang, "Previous", "السابق", "Précédent"),
            style=ButtonStyle.primary,
            emoji="◀️"
        )
        self.prev_button.callback = self.previous_page

        self.back_button = discord.ui.Button(
            label=_tr(lang, "Back to Categories", "العودة إلى الفئات", "Retour aux catégories"),
            style=ButtonStyle.secondary,
            emoji="🏠"
        )
        self.back_button.callback = self.go_back_to_categories

        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                _tr(self.lang,
                    "You cannot interact with someone else's shop menu.",
                    "لا يمكنك التفاعل مع قائمة متجر شخص آخر.",
                    "Vous ne pouvez pas interagir avec le menu de boutique de quelqu'un d'autre."
                ),
                ephemeral=True
            )
            return False
        return True

    async def update_button_states(self, total_items: int):
        items_per_page = 9 if self.category == ItemType.SKILL else self.cog.items_per_page
        total_pages = -(-total_items // items_per_page)
        self.prev_button.disabled = (self.current_page <= 1)
        self.next_button.disabled = (self.current_page >= total_pages)
        self.back_button.disabled = False

    async def fetch_items(self):
        self.items = await self.cog.get_category_items(self.category)
        return self.items

    async def go_back_to_categories(self, interaction: discord.Interaction):
        try:
            view = ShopCategoryView(self.cog, self.original_user, self.lang)
            embed = discord.Embed(
                title=_tr(self.lang, "🏯 Hunter's Association Shop", "🏯 متجر جمعية الصيادين", "🏯 Boutique de l'Association des Chasseurs"),
                description=_tr(self.lang,
                                "Welcome, brave hunter! What would you like to purchase today?",
                                "مرحباً أيها الصياد الشجاع! ماذا ترغب في شراءه اليوم؟",
                                "Bienvenue, chasseur courageux ! Que souhaitez-vous acheter aujourd'hui ?"),
                color=discord.Color.dark_purple()
            )
            embed.set_image(url=MAIN_SHOP_IMAGE)
            embed.add_field(name="⚔️ " + _tr(self.lang, "Weapons", "الأسلحة", "Armes"), value=_tr(self.lang, "Powerful weapons to slay monsters", "أسلحة قوية لقتل الوحوش", "Des armes puissantes pour terrasser les monstres"), inline=False)
            embed.add_field(name="🔮 " + _tr(self.lang, "Boosters", "المعززات", "Boosters"), value=_tr(self.lang, "Magical items to enhance your abilities", "عناصر سحرية لتعزيز قدراتك", "Objets magiques pour améliorer vos capacités"), inline=False)
            embed.add_field(name="🗝️ " + _tr(self.lang, "Dungeon Keys", "مفاتيح الأبراج", "Clés de Donjon"), value=_tr(self.lang, "Keys to access special dungeons", "مفاتيح للوصول إلى الأبراج الخاصة", "Des clés pour accéder à des donjons spéciaux"), inline=False)
            embed.add_field(name="⛏️ " + _tr(self.lang, "Pickaxes", "المعاول", "Pioches"), value=_tr(self.lang, "Tools to enhance your mining capabilities", "أدوات لتعزيز قدرات التعدين لديك", "Des outils pour améliorer vos capacités de minage"), inline=False)
            embed.add_field(name="📜 " + _tr(self.lang, "Skills", "المهارات", "Compétences"), value=_tr(self.lang, "Unlock powerful abilities for your class", "افتح قدرات قوية لفئتك", "Débloquez des compétences puissantes pour votre classe"), inline=False)
            embed.set_footer(text=_tr(self.lang,
                                      "Choose a category to browse. Your equipment may determine your fate in the dungeons!",
                                      "اختر فئة لتصفحها. قد يحدد تجهيزك مصيرك في الأبراج!",
                                      "Choisissez une catégorie à parcourir. Votre équipement peut déterminer votre destin dans les donjons !"))

            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"Go back to categories error: {e}")

    async def previous_page(self, interaction: discord.Interaction):
        try:
            # Ensure we have items
            if not self.items:
                await self.fetch_items()
           
            if self.current_page <= 1:
                await interaction.response.send_message(_tr(self.lang, "You're already on the first page!", "أنت بالفعل في الصفحة الأولى!", "Vous êtes déjà sur la première page !"), ephemeral=True)
                return
           
            self.current_page -= 1
           
            # Get premium level
            user_result = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(interaction.user.id),))
           
            premium_level = 0
            if user_result:
                premium_level = await self.cog.check_premium_status(user_result[0]['user_id'])
           
            await self.update_button_states(len(self.items))
           
            # Create embed for new page
            embed = self.cog.create_category_embed(self.category, self.items, self.current_page, premium_level, self.lang)
           
            # Update message
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"Previous page error: {e}")

    async def next_page(self, interaction: discord.Interaction):
        try:
            # Ensure we have items
            if not self.items:
                await self.fetch_items()
           
            items_per_page = 9 if self.category == ItemType.SKILL else self.cog.items_per_page
            total_pages = -(-len(self.items) // items_per_page)
           
            if self.current_page >= total_pages:
                await interaction.response.send_message(_tr(self.lang, "You're already on the last page!", "أنت بالفعل في الصفحة الأخيرة!", "Vous êtes déjà sur la dernière page !"), ephemeral=True)
                return
           
            self.current_page += 1
           
            # Get premium level
            user_result = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(interaction.user.id),))
           
            premium_level = 0
            if user_result:
                premium_level = await self.cog.check_premium_status(user_result[0]['user_id'])
           
            await self.update_button_states(len(self.items))
           
            # Create embed for new page
            embed = self.cog.create_category_embed(self.category, self.items, self.current_page, premium_level, self.lang)
           
            # Update message
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"Next page error: {e}")

class EnhancedConfirmView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.value = None
        self.user_id = user_id
        self.interaction = None
        self.original_user = None  # Store the original user
        self.ctx = None  # Store context for error messages
        self.cog = None  # Store cog reference
        self.final_price = None  # Store the final price
        self.is_gamble = False  # Flag to indicate if this is a gamble purchase

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting with the button is the original command user"""
        lang = await getlang(interaction.user.id)
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(_tr(lang,
                "Only the user who initiated this purchase can use these buttons!",
                "فقط المستخدم الذي بدأ عملية الشراء يمكنه استخدام هذه الأزرار!",
                "Seul l'utilisateur ayant initié cet achat peut utiliser ces boutons !"
            ), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Purchase", style=ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.interaction = interaction
        lang = await getlang(interaction.user.id)
       
        # Check if user is in a trade
        if self.cog:
            # 1. First check if user is in trade_status table
            trade_query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            trade_result = await execute_query_with_retry(trade_query, (self.user_id, self.user_id))
           
            if trade_result:
                await interaction.response.send_message(_tr(lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في صفقة نشطة، تأكد من إنهائها أو انتظار انتهاء التهدئة لاستخدام هذا الأمر، إذا واجهت أي مشكلة افتح تذكرة في خادم الدعم",
                    "Vous êtes actuellement dans un échange actif, terminez-le ou attendez la fin du délai de recharge pour utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d'assistance."
                ))
                return
            if trade_result:
                await interaction.response.send_message(_tr(lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في صفقة نشطة، تأكد من إنهائها أو انتظار انتهاء التهدئة لاستخدام هذا الأمر، إذا واجهت أي مشكلة افتح تذكرة في خادم الدعم",
                    "Vous êtes actuellement dans un échange actif, terminez-le ou attendez la fin du délai de recharge pour utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d'assistance."
                ))
                return
            # 2. Get the user's current currency (most recent)
            if self.is_gamble:
                currency_query = """
                SELECT gamble_currency FROM user WHERE user_id = %s
                """
            else:
                currency_query = """
                SELECT won_currency FROM user WHERE user_id = %s
                """
            currency_result = await execute_query_with_retry(currency_query, (self.user_id,))
            print(currency_result)
            if not currency_result:
                await interaction.response.send_message(_tr(lang,
                    "Error: Could not retrieve your currency information.",
                    "خطأ: تعذر استرجاع معلومات عملتك.",
                    "Erreur : Impossible de récupérer les informations de votre monnaie."
                ))
                return
           
            if self.is_gamble:
                current_currency = currency_result[0]['gamble_currency']
            else:
                current_currency = currency_result[0]['won_currency']
           
            # 3. Compare with final price
            if current_currency < self.final_price:
                if self.is_gamble:
                    await interaction.response.send_message(_tr(lang,
                        f"You don't have enough gwon. You need {self.final_price:,} gwon but only have {current_currency:,} gwon.",
                        f"ليست لديك كمية كافية من الـ GWon. تحتاج إلى {self.final_price:,} GWon ولكن لديك فقط {current_currency:,} GWon.",
                        f"Vous n'avez pas assez de GWon. Il vous faut {self.final_price:,} GWon mais vous n'en avez que {current_currency:,}."
                    ))
                else:
                    await interaction.response.send_message(_tr(lang,
                        f"You don't have enough won. You need {self.final_price:,} won but only have {current_currency:,} won.",
                        f"ليست لديك كمية كافية من الـ Won. تحتاج إلى {self.final_price:,} Won ولكن لديك فقط {current_currency:,} Won.",
                        f"Vous n'avez pas assez de Won. Il vous faut {self.final_price:,} Won mais vous n'en avez que {current_currency:,}."
                    ))
                return
       
        # If all checks pass, proceed with purchase
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=ButtonStyle.red, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.interaction = interaction
        self.value = False
        self.stop()

class ShopCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.items_per_page = 6
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds

    async def check_premium_status(self, user_id):
        """Check premium status and handle expiration"""
        query = """
        SELECT prenium, prenium_date
        FROM user
        WHERE user_id = %s
        """
        result = await execute_query_with_retry(query, (user_id,))
       
        if not result or result[0]['prenium'] == 0:
            return 0
           
        premium_level = result[0]['prenium']
        premium_date = result[0]['prenium_date']
       
        if premium_date:
            current_date = datetime.now()
            days_difference = (current_date - premium_date).days
           
            if days_difference > 30:
                # Reset premium status
                update_query = """
                UPDATE user
                SET prenium = 0, prenium_date = NULL
                WHERE user_id = %s
                """
                await execute_query_with_retry(update_query, (user_id,))
                return 0
               
        return premium_level

    def calculate_discounted_price(self, weapon, premium_level):
        """Calculate discounted price based on premium level and weapon price"""
        original_price = weapon['price']
       
        # No discount for non-premium users
        if premium_level == 0:
            return original_price, False
           
        discount = 0
       
        # Premium Level 1: Gets discount on weapons priced 5k-12k
        if premium_level == 1:
            if 5000 <= original_price <= 12000:
                discount = 0.10
       
        # Premium Level 2: Gets discount on weapons priced 5k-120k
        elif premium_level == 2:
            if 5000 <= original_price <= 120000:
                discount = 0.10
       
        # Premium Level 3: Gets 30% discount on all weapons
        elif premium_level == 3:
            discount = 0.30  # Changed from 0.10 to 0.30
               
        if discount > 0:
            discounted_price = int(original_price * (1 - discount))
            return discounted_price, True
       
        return original_price, False

    async def get_category_items(self, category: ItemType):
        if category == ItemType.WEAPON:
            query = """
            SELECT w.*, wr.weaponrank_name, w.weapon_icon,
                   GROUP_CONCAT(cc.card_class_name ORDER BY cc.card_class_name) as class_names,
                   GROUP_CONCAT(cc.card_class_id ORDER BY cc.card_class_id) as class_ids,
                   COUNT(wc.card_class_id) as class_count
            FROM weapon w
            JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
            JOIN weapon_class wc ON w.weapon_id = wc.weapon_id
            JOIN card_class cc ON wc.card_class_id = cc.card_class_id
            WHERE w.price > 0
            GROUP BY w.weapon_id
            ORDER BY class_count ASC, w.weapon_id DESC
            """
        elif category == ItemType.BOOSTER:
            # Modified to only show items with buyable=1
            query = "SELECT *, product_icon FROM product WHERE buyable = 1 ORDER BY price ASC"
        elif category == ItemType.KEY:  # ItemType.KEY
            query = """
            SELECT dk.*
            FROM dungeon_key dk
            ORDER BY price
            """
        elif category == ItemType.SKILL:
            query = """
            SELECT s.*, cc.card_class_name
            FROM skill s
            JOIN card_class cc ON s.card_class = cc.card_class_id
            WHERE s.shop_status = 1 AND s.price > 0
            ORDER BY s.card_class, s.skill_id
            """
        else:
            query = """
            SELECT p.*,
                MIN(p.min_value) as min_value,
                MAX(p.max_value) as max_value
            FROM pickaxe p
            GROUP BY p.id_pickaxe
            ORDER BY p.price DESC
            """
        return await execute_query_with_retry(query)

    def create_category_embed(self, category: ItemType, items: List[Dict[str, Union[int, str]]], page: int, premium_level: int = 0, lang: int = 0) -> discord.Embed:

        if category == ItemType.WEAPON:
            return self.create_weapon_embed(items, page, premium_level, lang)
        elif category == ItemType.SKILL:
            return self.create_skills_embed(items, page, premium_level, lang)
           
        # Original logic for other categories remains unchanged
        start = (page - 1) * self.items_per_page
        end = start + self.items_per_page
        page_items = items[start:end]

        if category == ItemType.PICKAXE:
            title = _tr(lang, "⛏️ Pickaxes Shop", "⛏️ متجر المعاول", "⛏️ Boutique des Pioches")
            image_url = PICKAXES_SHOP_IMAGE
        elif category == ItemType.BOOSTER:
            title = _tr(lang, "🔮 Boosters & Enhancers Shop", "🔮 متجر المعززات والمقويات", "🔮 Boutique des Boosters & Améliorations")
            image_url = BOOSTERS_SHOP_IMAGE
        else:  # ItemType.KEY
            title = _tr(lang, "🗝️ Dungeon Keys Shop", "🗝️ متجر مفاتيح الأبراج", "🗝️ Boutique des Clés de Donjon")
            image_url = KEYS_SHOP_IMAGE

        embed = discord.Embed(title=title, color=discord.Color.gold())
        embed.set_image(url=image_url)
        print("cat: ",lang)
        for item in page_items:
            name, value = self.format_item(category, item, lang)
            embed.add_field(name=name, value=value, inline=False)

        total_pages = -(-len(items) // self.items_per_page)
       
        # Add premium status to footer if applicable
        footer_text = _tr(lang, f"Page {page}/{total_pages}", f"الصفحة {page}/{total_pages}", f"Page {page}/{total_pages}")
        if premium_level > 0:
            premium_tiers = {1: _tr(lang,"National Rank","رتبة وطنية","Rang National"),
                             2: _tr(lang,"Ruler","الحاكم","Souverain"),
                             3: _tr(lang,"Monarch","العاهل","Monarque")}
            footer_text += _tr(lang, f" | {premium_tiers[premium_level]} Premium Member", f" | عضو بريميوم {premium_tiers[premium_level]}", f" | Membre Premium {premium_tiers[premium_level]}")
           
        embed.set_footer(text=footer_text)
        return embed

    def create_weapon_embed(self, items: List[Dict[str, Union[int, str]]], page: int, premium_level: int = 0, lang: int = 0) -> discord.Embed:
        start = (page - 1) * self.items_per_page
        end = start + self.items_per_page
        page_items = items[start:end]

        embed = discord.Embed(title=_tr(lang, "⚔️ Weapons Shop", "⚔️ متجر الأسلحة", "⚔️ Boutique des Armes"), color=discord.Color.gold())
        embed.set_image(url=WEAPONS_SHOP_IMAGE)
        print("weponshop")
        current_classes = None

        for item in page_items:
            class_names = item['class_names'].split(',')
            class_count = item['class_count']
           
            # Add class header if it's different from the current one
            class_display = None
            if class_count == 1:
                class_display = class_names[0]
            else:
                class_display = " | ".join(class_names)

            if class_display != current_classes:
                current_classes = class_display
                embed.add_field(name=_tr(lang, f"📋 {class_display}", f"📋 {class_display}", f"📋 {class_display}"), value="─" * 20, inline=False)

            # Calculate discounted price if applicable
            original_price = item['price']
            discounted_price, has_discount = self.calculate_discounted_price(
                item, premium_level
            )

            # Format weapon stats
            stats = []
            if item['attack_boost'] > 0:
                stats.append(_tr(lang, f"Attack+{item['attack_boost']}", f"هجوم+{item['attack_boost']}", f"Attaque+{item['attack_boost']}"))
            if item['attack_percentage'] > 0:
                stats.append(_tr(lang, f"Attack+{item['attack_percentage']}%", f"هجوم+{item['attack_percentage']}%", f"Attaque+{item['attack_percentage']}%"))
            if item['health_boost'] > 0:
                stats.append(_tr(lang, f"Health+{item['health_boost']}", f"صحة+{item['health_boost']}", f"Santé+{item['health_boost']}"))
            if item['health_percentage'] > 0:
                stats.append(_tr(lang, f"Speed+{item['speed_boost']}", f"سرعة+{item['speed_boost']}", f"Vitesse+{item['speed_boost']}"))
            if item['speed_percentage'] > 0:
                stats.append(_tr(lang, f"Speed+{item['speed_percentage']}%", f"سرعة+{item['speed_percentage']}%", f"Vitesse+{item['speed_percentage']}%"))
            if item['defense_boost'] > 0:
                stats.append(_tr(lang, f"Defense+{item['defense_boost']}", f"دفاع+{item['defense_boost']}", f"Défense+{item['defense_boost']}"))
            if item['defense_percentage'] > 0:
                stats.append(_tr(lang, f"Defense+{item['defense_percentage']}%", f"دفاع+{item['defense_percentage']}%", f"Défense+{item['defense_percentage']}%"))

            # Add weapon icon and format price display
            icon_prefix = item.get('weapon_icon', '') if item.get('weapon_icon') else ''
            name = f"{icon_prefix} | {item['weapon_name']} ({item['weaponrank_name']})"
           
            # Format price display with discount if applicable
            if has_discount:
                value = _tr(
                    lang,
                    f"Price: ~~{original_price:,}~~ {discounted_price:,} 💰\n{' | '.join(stats)}",
                    f"السعر: ~~{original_price:,}~~ {discounted_price:,} 💰\n{' | '.join(stats)}",
                    f"Prix : ~~{original_price:,}~~ {discounted_price:,} 💰\n{' | '.join(stats)}"
                )
            else:
                value = _tr(
                    lang,
                    f"Price: {original_price:,} 💰\n{' | '.join(stats)}",
                    f"السعر: {original_price:,} 💰\n{' | '.join(stats)}",
                    f"Prix : {original_price:,} 💰\n{' | '.join(stats)}"
                )
           
            embed.add_field(name=name, value=value, inline=False)

        total_pages = -(-len(items) // self.items_per_page)
        print("done")        
        # Add premium status to footer if applicable
        footer_text = _tr(lang, f"Page {page}/{total_pages}", f"الصفحة {page}/{total_pages}", f"Page {page}/{total_pages}")
        if premium_level > 0:
            premium_tiers = {1: _tr(lang,"National Rank", "الرتبة الوطنية", "Rang National"),
                             2: _tr(lang,"Ruler","الحاكم","Souverain"),
                             3: _tr(lang,"Monarch","العاهل","Monarque")}
            footer_text += _tr(lang, f" | {premium_tiers[premium_level]} Premium Member", f" | عضو بريميوم {premium_tiers[premium_level]}", f" | Membre Premium {premium_tiers[premium_level]}")
           
        embed.set_footer(text=footer_text)
        return embed

    def create_skills_embed(self, items: List[Dict[str, Union[int, str]]], page: int, premium_level: int = 0, lang: int = 0) -> discord.Embed:
        skills_per_page = 9
        start = (page - 1) * skills_per_page
        end = start + skills_per_page
        page_items = items[start:end]

        embed = discord.Embed(title=_tr(lang, "🎯 Skills Shop", "🎯 متجر المهارات", "🎯 Boutique des Compétences"), color=discord.Color.blue())
        embed.set_image(url=SKILLS_SHOP_IMAGE)
       
        current_class = None
       
        # Card class mapping
        class_names = {
            1: _tr(lang, "Fighter", "مقاتل", "Combattant"),
            3: _tr(lang, "Mage", "ساحر", "Mage"),
            4: _tr(lang, "Ranger", "حارس", "Rôdeur"),
            5: _tr(lang, "Tank", "دبابة", "Tank"),
            6: _tr(lang, "Assassin", "قاتل", "Assassin")
        }

        class_emotes = {
            1: "⚔️",   # Fighter
            3: "🔮",   # Mage
            4: "🏹",   # Ranger
            5: "🛡️",   # Tank
            6: "🗡️"    # Assassin
        }

        for item in page_items:
            card_class = item['card_class']
            class_name = class_names.get(card_class, _tr(lang, "Unknown", "غير معروف", "Inconnu"))
           
            # Add class header if it's different from the current one
            if class_name != current_class:
                current_class = class_name
                embed.add_field(name=_tr(lang, f"📋 {class_name} Skills", f"📋 مهارات {class_name}", f"📋 Compétences {class_name}"), value="─" * 20, inline=False)

            # Calculate discounted price if applicable
            original_price = item['price']

            # Format skill display with class emote
            skill_name = item['skill_name']
            class_emote = class_emotes.get(card_class, "❓")

            price_display = _tr(lang, f"Price: {original_price:,} 💰", f"السعر: {original_price:,} 💰", f"Prix : {original_price:,} 💰")
           
            # Include skill description in the embed (skills table not specified for AR/FR; keep as is)
            skill_description = (
                item.get('ardescription', item.get('description', _tr(lang, 'No description available.', 'لا يوجد وصف متاح.', 'Pas de description disponible.'))) if lang == 1
                else item.get('frdescription', item.get('description', _tr(lang, 'No description available.', 'لا يوجد وصف متاح.', 'Pas de description disponible.'))) if lang == 3
                else item.get('description', _tr(lang, 'No description available.', 'لا يوجد وصف متاح.', 'Pas de description disponible.'))
            )
           
            embed.add_field(
                name=f"{class_emote} {skill_name} | {price_display}",
                value=skill_description,
                inline=False
            )

        total_pages = -(-len(items) // skills_per_page)
       
        # Add premium status to footer if applicable
        footer_text = _tr(lang, f"Page {page}/{total_pages}", f"الصفحة {page}/{total_pages}", f"Page {page}/{total_pages}")
        if premium_level > 0:
            premium_tiers = {1: _tr(lang,"National Rank", "الرتبة الوطنية", "Rang National"),
                             2: _tr(lang,"Ruler","الحاكم","Souverain"),
                             3: _tr(lang,"Monarch","العاهل","Monarque")}
            footer_text += _tr(lang, f" | {premium_tiers[premium_level]} Premium Member", f" | عضو بريميوم {premium_tiers[premium_level]}", f" | Membre Premium {premium_tiers[premium_level]}")
           
        embed.set_footer(text=footer_text)
        return embed

    def format_item(self, category: ItemType, item: Dict[str, Union[int, str]], lang: int) -> Tuple[str, str]:
        """
        Formats a single item's display for non-weapon/non-skill categories with multilanguage description fields.
        - If lang=0 -> use default description fields already present.
        - If lang=1 -> use *_ardescription fields where available.
        - If lang=3 -> use *_frdescription fields where available.
        """
        # Common price label
        def price_label(amount: int) -> str:
            return _tr(lang, f"Price: {amount:,} 💰", f"السعر: {amount:,} 💰", f"Prix : {amount:,} 💰")

        if category == ItemType.BOOSTER:
            # product table
            name = f"{item.get('product_icon','')} {item['product_name']}"
            if lang == 1 and item.get('product_ardescription'):
                print("arab")
                desc = item['product_ardescription']
            elif lang == 3 and item.get('product_frdescription'):
                print("fr")
                desc = item['product_frdescription']
            else:
                print("eng")
                desc = item.get('product_description', '')
            value = f"{price_label(item['price'])}\n{desc}"
            return name, value

        if category == ItemType.KEY:
            # dungeon_key table
            name = f"🗝️ {item['dungeonkey_name']}"
            if lang == 1 and item.get('dungeonkey_ardescription'):
                desc = item['dungeonkey_ardescription']
            elif lang == 3 and item.get('dungeonkey_frdescription'):
                desc = item['dungeonkey_frdescription']
            else:
                desc = item.get('dungeonkey_description', '')
            value = f"{price_label(item['price'])}\n{desc}"
            return name, value

        # PICKAXE
        name = f"⛏️ {item['name_pickaxe']}"
        if lang == 1 and item.get('pickaxe_ardescription'):
            desc = item['pickaxe_ardescription']
        elif lang == 3 and item.get('pickaxe_frdescription'):
            desc = item['pickaxe_frdescription']
        else:
            desc = item.get('pickaxe_description', '')
        stats_line = _tr(
            lang,
            f"Power: {item.get('min_value', item.get('min_value', 0))} - {item.get('max_value', item.get('max_value', 0))}",
            f"القوة: {item.get('min_value', 0)} - {item.get('max_value', 0)}",
            f"Puissance : {item.get('min_value', 0)} - {item.get('max_value', 0)}"
        )
        value = f"{price_label(item['price'])}\n{stats_line}\n{desc}"
        return name, value

    @commands.command(name="shopstats")
    @commands.has_permissions(administrator=True)
    async def shop_stats(self, ctx):
        """Display database connection statistics for the shop system"""
        metrics = get_metrics()
       
        embed = discord.Embed(
            title="Shop System Database Metrics",
            color=discord.Color.blue()
        )
       
        embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
        embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
        embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
       
        embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
        embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
        embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
       
        if metrics["last_error"]:
            embed.add_field(
                name="Last Error",
                value=f"{metrics['last_error']}\nTime: {metrics['last_error_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
           
        await ctx.send(embed=embed)


    @commands.command(name="buy", aliases=["by", "bu"])
    async def buy(self, ctx, *, args):
        lang = await getlang(ctx.author.id)

        if not args:
            await ctx.send(_tr(lang,
                "Usage: sl buy <item_name> <quantity>",
                "الاستخدام: sl buy <اسم_العنصر> <الكمية>",
                "Utilisation : sl buy <nom_objet> <quantité>"
            ))
            return

        args = args.split()
        item_name = " ".join(args) if len(args) == 1 else " ".join(args[:-1])

        try:
            quantity = 1 if len(args) == 1 else int(args[-1])
            if quantity <= 0:
                await ctx.send(_tr(lang,
                    "Quantity must be a positive number.",
                    "يجب أن تكون الكمية رقماً موجباً.",
                    "La quantité doit être un nombre positif."
                ))
                return
        except ValueError:
            item_name = " ".join(args)
            quantity = 1

        try:
            # Get user details and balance
            user_result = await execute_query_with_retry(
                "SELECT user_id, won_currency FROM user WHERE user_authorid = %s",
                (str(ctx.author.id),)
            )
            if not user_result:
                await ctx.send(_tr(lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: يجب عليك التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire sur le bot."
                ))
                return

            user_id = user_result[0]['user_id']
            user_currency = user_result[0]['won_currency']

            # Block if in trade
            trade_result = await execute_query_with_retry(
                """
                SELECT id_tradestatus FROM trade_status
                WHERE initiator_id = %s OR target_id = %s
                """,
                (user_id, user_id)
            )
            if trade_result:
                await ctx.send(_tr(lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في صفقة نشطة، تأكد من إنهائها أو انتظار انتهاء التهدئة لاستخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge pour utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d'assistance."
                ))
                return

            premium_level = await self.check_premium_status(user_id)

            # Lookup item across tables
            item_details = None
            item_type = None

            # Weapons (with rank_in_class calc)
            weapon_result = await execute_query_with_retry(
                """
                WITH WeaponRanks AS (
                    SELECT
                        w.*,
                        wr.weaponrank_name,
                        GROUP_CONCAT(DISTINCT cc.card_class_name ORDER BY cc.card_class_name) as class_names,
                        COUNT(DISTINCT cc.card_class_id) as class_count,
                        ROW_NUMBER() OVER (
                            PARTITION BY GROUP_CONCAT(DISTINCT cc.card_class_name ORDER BY cc.card_class_name)
                            ORDER BY w.price DESC
                        ) as rank_in_class
                    FROM weapon w
                    JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                    JOIN weapon_class wc ON w.weapon_id = wc.weapon_id
                    JOIN card_class cc ON wc.card_class_id = cc.card_class_id
                    WHERE w.price > 0
                    GROUP BY w.weapon_id
                )
                SELECT * FROM WeaponRanks WHERE weapon_name = %s
                """,
                (item_name,)
            )
            if weapon_result:
                item_details = weapon_result[0]
                item_type = 'weapon'
            else:
                # Try other categories
                for table, name_column, typed in [
                    ('dungeon_key', 'dungeonkey_name', 'dungeonkey'),
                    ('product',     'product_name',     'product'),
                    ('pickaxe',     'pickaxe_name',     'pickaxe'),
                    ('skill',       'skill_name',       'skill')
                ]:
                    res = await execute_query_with_retry(
                        f"SELECT * FROM {table} WHERE {name_column} = %s",
                        (item_name,)
                    )
                    if res:
                        # product must be buyable
                        if table == 'product' and res[0].get('buyable') == 0:
                            await ctx.send(_tr(lang,
                                "Error : this item is not listed for sale",
                                "خطأ: هذا العنصر غير معروض للبيع.",
                                "Erreur : cet objet n'est pas en vente."
                            ))
                            return
                        item_details = res[0]
                        item_type = typed
                        break

            if not item_details:
                await ctx.send(_tr(lang,
                    "Error: The specified item does not exist.",
                    "خطأ: العنصر المحدد غير موجود.",
                    "Erreur : l'objet spécifié n'existe pas."
                ))
                return

            # Price / discount (weapons only)
            base_price = item_details['price']
            discount_applied = False
            if item_type == 'weapon' and premium_level > 0:
                base_price, discount_applied = self.calculate_discounted_price(item_details, premium_level)

            final_price = base_price * quantity
            if user_currency < final_price:
                await ctx.send(_tr(lang,
                    f"You don't have enough won. You need {final_price:,} won.",
                    f"ليس لديك ما يكفي من الون. تحتاج إلى {final_price:,} Won.",
                    f"Vous n'avez pas assez de Won. Il vous faut {final_price:,} Won."
                ))
                return

            # Ensure no pending purchase or ascension
            pending = await execute_query_with_retry(
                "SELECT id FROM shop_status WHERE user_id = %s",
                (user_id,)
            )
            if pending:
                await ctx.send(_tr(lang,
                    "⚠️ You already have a pending purchase. Please complete or cancel your previous purchase before buying this item again.",
                    "⚠️ لديك عملية شراء معلّقة بالفعل. يرجى إكمال أو إلغاء العملية السابقة قبل الشراء مرة أخرى.",
                    "⚠️ Vous avez déjà un achat en attente. Veuillez terminer ou annuler l'achat précédent avant d'acheter à nouveau."
                ))
                return

            asc = await execute_query_with_retry(
                "SELECT * FROM ascending_state WHERE user_id = %s",
                (user_id,)
            )
            if asc:
                await ctx.send(_tr(lang,
                    "⏳ You are already performing an ascension. Please wait for it to complete.",
                    "⏳ أنت تقوم بعملية ترقّي حالياً. يرجى الانتظار حتى تنتهي.",
                    "⏳ Vous effectuez déjà une ascension. Veuillez attendre qu'elle se termine."
                ))
                return

            # Insert pending
            await execute_query_with_retry("INSERT INTO shop_status (user_id) VALUES (%s)", (user_id,))

            # Build confirmation embed
            embed = discord.Embed(
                title=_tr(lang, "🛒 Purchase Confirmation", "🛒 تأكيد الشراء", "🛒 Confirmation d'achat"),
                color=discord.Color.gold()
            )
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            embed.add_field(name=_tr(lang, "Item", "العنصر", "Objet"), value=f"**{item_name}**", inline=False)
            embed.add_field(name=_tr(lang, "Quantity", "الكمية", "Quantité"), value=f"**{quantity}**", inline=True)

            if item_type == 'weapon':
                embed.add_field(name=_tr(lang, "Class", "الفئة", "Classe"), value=f"**{item_details['class_names']}**", inline=True)

            # Price per item
            if item_type == 'weapon' and discount_applied:
                original_price = item_details['price']
                embed.add_field(
                    name=_tr(lang, "Price per Item", "السعر لكل قطعة", "Prix unitaire"),
                    value=_tr(lang,
                        f"**~~{original_price:,}~~ {base_price:,} won (Premium Discount)**",
                        f"**~~{original_price:,}~~ {base_price:,} Won (خصم بريميوم)**",
                        f"**~~{original_price:,}~~ {base_price:,} Won (Remise Premium)**"
                    ),
                    inline=True
                )
            else:
                embed.add_field(
                    name=_tr(lang, "Price per Item", "السعر لكل قطعة", "Prix unitaire"),
                    value=f"**{base_price:,} won**", inline=True
                )

            embed.add_field(name=_tr(lang, "Total Price", "السعر الإجمالي", "Prix total"), value=f"**{final_price:,} won**", inline=True)
            embed.add_field(name=_tr(lang, "Your Balance", "رصيدك", "Votre solde"), value=f"**{user_currency:,} won**", inline=True)
            embed.add_field(name=_tr(lang, "Remaining Balance", "الرصيد المتبقي", "Solde restant"), value=f"**{user_currency - final_price:,} won**", inline=True)

            # Item-specific extras (with multilingual descriptions)
            if item_type == 'weapon':
                stats = []
                if item_details.get('attack_boost', 0) > 0:
                    stats.append(_tr(lang, f"Attack +{item_details['attack_boost']}", f"هجوم +{item_details['attack_boost']}", f"Attaque +{item_details['attack_boost']}"))
                if item_details.get('health_boost', 0) > 0:
                    stats.append(_tr(lang, f"Health +{item_details['health_boost']}", f"صحة +{item_details['health_boost']}", f"Santé +{item_details['health_boost']}"))
                if item_details.get('speed_boost', 0) > 0:
                    stats.append(_tr(lang, f"Speed +{item_details['speed_boost']}", f"سرعة +{item_details['speed_boost']}", f"Vitesse +{item_details['speed_boost']}"))
                if item_details.get('defense_boost', 0) > 0:
                    stats.append(_tr(lang, f"Power +{item_details['defense_boost']}", f"قوة +{item_details['defense_boost']}", f"Puissance +{item_details['defense_boost']}"))
                embed.add_field(
                    name=_tr(lang, "Stats", "الإحصائيات", "Statistiques"),
                    value=("**" + " | ".join(stats) + "**") if stats else _tr(lang, "No stat boosts", "لا توجد زيادات", "Aucun bonus de stats"),
                    inline=False
                )

            elif item_type == 'product':
                if item_details.get('xp_boost', 0) > 0:
                    embed.add_field(name=_tr(lang, "XP Boost", "زيادة الخبرة", "Bonus d'XP"), value=f"**+{item_details['xp_boost']}**", inline=True)
                # language-aware product description
                if lang == 1 and item_details.get('product_ardescription'):
                    pdesc = item_details['product_ardescription']
                elif lang == 3 and item_details.get('product_frdescription'):
                    pdesc = item_details['product_frdescription']
                else:
                    pdesc = item_details.get('product_description', '')
                embed.add_field(name=_tr(lang, "Description", "الوصف", "Description"), value=f"*{pdesc}*", inline=False)

            elif item_type == 'dungeonkey' or item_type == 'dungeon_key':
                # unify name
                dname = item_details.get('dungeonkey_name', '')
                embed.add_field(name=_tr(lang, "Dungeon Rank", "رتبة الدنجن", "Rang du donjon"), value=f"**{dname}**", inline=True)
                # description (unused in confirmation but could be added if desired)

            elif item_type == 'pickaxe':
                minv = item_details.get('min_value', item_details.get('min_value', 0))
                maxv = item_details.get('max_value', item_details.get('max_value', 0))
                # language-aware description
                if lang == 1 and item_details.get('pickaxe_ardescription'):
                    pdesc = item_details['pickaxe_ardescription']
                elif lang == 3 and item_details.get('pickaxe_frdescription'):
                    pdesc = item_details['pickaxe_frdescription']
                else:
                    pdesc = item_details.get('pickaxe_description', '')
                embed.add_field(
                    name=_tr(lang, "Pickaxe Stats", "إحصائيات المعول", "Stats de la pioche"),
                    value=_tr(lang,
                        f"**Min Value: {minv} <:won:1304034228966658048>\nMax Value: {maxv} <:won:1304034228966658048>**\n{pdesc}",
                        f"**أقل قيمة: {minv} <:won:1304034228966658048>\nأعلى قيمة: {maxv} <:won:1304034228966658048>**\n{pdesc}",
                        f"**Valeur min : {minv} <:won:1304034228966658048>\nValeur max : {maxv} <:won:1304034228966658048>**\n{pdesc}"
                    ),
                    inline=False
                )

            elif item_type == 'skill':
                sdesc = (
                    item_details.get('ardescription', item_details.get('description', _tr(lang, "No description available.", "لا يوجد وصف متاح.", "Pas de description disponible."))) if lang == 1
                    else item_details.get('frdescription', item_details.get('description', _tr(lang, "No description available.", "لا يوجد وصف متاح.", "Pas de description disponible."))) if lang == 3
                    else item_details.get('description', _tr(lang, "No description available.", "لا يوجد وصف متاح.", "Pas de description disponible."))
                )
                embed.add_field(name=_tr(lang, "Skill Description", "وصف المهارة", "Description de la compétence"), value=f"*{sdesc}*", inline=False)

            if premium_level > 0:
                tiers = {1: _tr(lang, "National Ranker", "رتبة وطنية", "Rang National"),
                         2: _tr(lang, "Ruler", "الحاكم", "Souverain"),
                         3: _tr(lang, "Monarch", "العاهل", "Monarque")}
                embed.set_footer(text=f"{tiers[premium_level]} | " + _tr(lang, "Please confirm your purchase within 60 seconds.", "يرجى تأكيد الشراء خلال 60 ثانية.", "Veuillez confirmer votre achat dans les 60 secondes."))
            else:
                embed.set_footer(text=_tr(lang, "Please confirm your purchase within 60 seconds.", "يرجى تأكيد الشراء خلال 60 ثانية.", "Veuillez confirmer votre achat dans les 60 secondes."))

            # Confirmation view
            view = EnhancedConfirmView(user_id)
            view.original_user = ctx.author
            view.ctx = ctx
            view.cog = self
            view.final_price = final_price

            msg = await ctx.send(embed=embed, view=view)
            await view.wait()
            await msg.delete()

            # Remove pending (always)
            await execute_query_with_retry("DELETE FROM shop_status WHERE user_id = %s", (user_id,))

            if view.value is None:
                await ctx.send(_tr(lang,
                    "Purchase timed out.",
                    "انتهى وقت الشراء.",
                    "Temps d'achat écoulé."
                ))
                return
            elif not view.value:
                await ctx.send(_tr(lang,
                    "Purchase cancelled.",
                    "تم إلغاء الشراء.",
                    "Achat annulé."
                ))
                return

            # Re-read balance and charge
            won_result = await execute_query_with_retry("SELECT won_currency FROM user WHERE user_id = %s", (user_id,))
            user_currency = won_result[0]['won_currency']
            new_currency = user_currency - final_price
            await execute_query_with_retry("UPDATE user SET won_currency = %s WHERE user_id = %s", (new_currency, user_id))

            # Inventory update
            inv_res = await execute_query_with_retry("SELECT inventory_id FROM inventory WHERE user_id = %s", (user_id,))
            if not inv_res:
                await ctx.send(_tr(lang,
                    "Error: You don't have an inventory.",
                    "خطأ: ليس لديك مخزون.",
                    "Erreur : vous n'avez pas d'inventaire."
                ))
                return

            inventory_id = inv_res[0]['inventory_id']
            table_map = {
                'weapon': 'inventory_weapon',
                'product': 'inventory_product',
                'dungeonkey': 'inventory_dungeonkey',
                'pickaxe': 'inventory_pickaxe',
                'skill': 'inventory_skills'
            }
            inventory_table = table_map[item_type]
            item_id_column = 'id_pickaxe' if item_type == 'pickaxe' else f"{item_type}_id"

            existing_item = await execute_query_with_retry(
                f"SELECT * FROM {inventory_table} WHERE inventory_id = %s AND {item_id_column} = %s",
                (inventory_id, item_details[item_id_column])
            )
            if existing_item:
                # column name can be quantiter/quantity depending on table — use 'quantiter' if present else 'quantity'
                qty_col = 'quantiter' if 'quantiter' in existing_item[0] else 'quantity'
                new_qty = existing_item[0][qty_col] + quantity
                await execute_query_with_retry(
                    f"UPDATE {inventory_table} SET {qty_col} = %s WHERE inventory_id = %s AND {item_id_column} = %s",
                    (new_qty, inventory_id, item_details[item_id_column])
                )
            else:
                # default to quantiter where schema uses it
                # Try insert with quantiter first; fallback to quantity
                try:
                    await execute_query_with_retry(
                        f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantiter) VALUES (%s, %s, %s)",
                        (inventory_id, item_details[item_id_column], quantity)
                    )
                except Exception:
                    await execute_query_with_retry(
                        f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantity) VALUES (%s, %s, %s)",
                        (inventory_id, item_details[item_id_column], quantity)
                    )

            # Success embed
            success = discord.Embed(
                title=_tr(lang, "🎉 Purchase Successful!", "🎉 تمت عملية الشراء بنجاح!", "🎉 Achat réussi !"),
                color=discord.Color.green()
            )
            success.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            success.add_field(name=_tr(lang, "Item", "العنصر", "Objet"), value=item_name, inline=True)
            success.add_field(name=_tr(lang, "Quantity", "الكمية", "Quantité"), value=str(quantity), inline=True)
            if item_type == 'weapon' and discount_applied:
                success.add_field(
                    name=_tr(lang, "Total Price", "السعر الإجمالي", "Prix total"),
                    value=_tr(lang,
                        f"~~{item_details['price'] * quantity:,}~~ {final_price:,} won (Premium Discount Applied)",
                        f"~~{item_details['price'] * quantity:,}~~ {final_price:,} Won (تم تطبيق خصم بريميوم)",
                        f"~~{item_details['price'] * quantity:,}~~ {final_price:,} Won (Remise Premium appliquée)"
                    ),
                    inline=True
                )
            else:
                success.add_field(name=_tr(lang, "Total Price", "السعر الإجمالي", "Prix total"), value=f"{final_price:,} won", inline=True)
            success.add_field(name=_tr(lang, "New Balance", "الرصيد الجديد", "Nouveau solde"), value=f"{new_currency:,} won", inline=True)
            success.set_footer(text=_tr(lang, "Thank you for your purchase!", "شكراً لعملية الشراء!", "Merci pour votre achat !"))

            if view.interaction:
                await view.interaction.response.send_message(embed=success)
            else:
                await ctx.send(embed=success)

            # Quest progress (unchanged text keys)
            user_quests = await execute_query_with_retry("""
                SELECT uq.*, q.quest_name, q.required_count
                FROM user_quests uq
                JOIN quest q ON uq.quest_id = q.quest_id
                WHERE uq.user_id = %s AND q.quest_name = 'Buy 1 shop item' AND uq.completion_status = 0
            """, (user_id,))
            if user_quests:
                quest = user_quests[0]
                new_count = quest['current_count'] + 1
                await execute_query_with_retry("""
                    UPDATE user_quests
                    SET current_count = %s,
                        completion_status = CASE WHEN %s >= %s THEN 1 ELSE 0 END
                    WHERE userquests_id = %s
                """, (new_count, new_count, quest['required_count'], quest['userquests_id']))

                if new_count >= quest['required_count']:
                    inv2 = await execute_query_with_retry("SELECT inventory_id FROM inventory WHERE user_id = %s", (user_id,))
                    if inv2:
                        inv_id = inv2[0]['inventory_id']
                        chest = await execute_query_with_retry(
                            "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = 1",
                            (inv_id,)
                        )
                        if chest:
                            await execute_query_with_retry(
                                "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = 1",
                                (inv_id,)
                            )
                        else:
                            await execute_query_with_retry(
                                "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, 1, 1)",
                                (inv_id,)
                            )

        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {e}", f"حدث خطأ: {e}", f"Une erreur s'est produite : {e}"))

    @commands.command(name="gbuy", aliases=["gby", "gbu"])
    async def gbuy(self, ctx, *, args):
        lang = await getlang(ctx.author.id)

        if not args:
            await ctx.send(_tr(lang,
                "Usage: sl gbuy <item_name> <quantity>",
                "الاستخدام: sl gbuy <اسم_العنصر> <الكمية>",
                "Utilisation : sl gbuy <nom_objet> <quantité>"
            ))
            return
        args = args.split()
        item_name = " ".join(args) if len(args) == 1 else " ".join(args[:-1])

        try:
            quantity = 1 if len(args) == 1 else int(args[-1])
            if quantity <= 0:
                await ctx.send(_tr(lang,
                    "Quantity must be a positive number.",
                    "يجب أن تكون الكمية رقماً موجباً.",
                    "La quantité doit être un nombre positif."
                ))
                return
        except ValueError:
            item_name = " ".join(args)
            quantity = 1

        try:
            # User and gwon
            user_result = await execute_query_with_retry(
                "SELECT user_id, gamble_currency FROM user WHERE user_authorid = %s",
                (str(ctx.author.id),)
            )
            if not user_result:
                await ctx.send(_tr(lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: يجب عليك التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire sur le bot."
                ))
                return

            user_id = user_result[0]['user_id']
            user_currency = user_result[0]['gamble_currency']

            # Must not be in gambling session
            gamble_result = await execute_query_with_retry("SELECT * FROM gamble_status WHERE user_id = %s", (user_id,))
            if gamble_result:
                await ctx.send(_tr(lang,
                    "⏰ You are already in a gambling session. Please wait until it completes.",
                    "⏰ أنت بالفعل في جلسة مقامرة. يرجى الانتظار حتى تنتهي.",
                    "⏰ Vous êtes déjà dans une session de jeu. Veuillez attendre qu'elle se termine."
                ))
                return

            # Block if in trade
            trade_result = await execute_query_with_retry(
                """
                SELECT id_tradestatus FROM trade_status
                WHERE initiator_id = %s OR target_id = %s
                """,
                (user_id, user_id)
            )
            if trade_result:
                await ctx.send(_tr(lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في صفقة نشطة، تأكد من إنهائها أو انتظار انتهاء التهدئة لاستخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge pour utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d'assistance."
                ))
                return

            premium_level = await self.check_premium_status(user_id)

            # Lookup item (same as buy)
            item_details = None
            item_type = None

            weapon_result = await execute_query_with_retry(
                """
                WITH WeaponRanks AS (
                    SELECT
                        w.*,
                        wr.weaponrank_name,
                        GROUP_CONCAT(DISTINCT cc.card_class_name ORDER BY cc.card_class_name) as class_names,
                        COUNT(DISTINCT cc.card_class_id) as class_count,
                        ROW_NUMBER() OVER (
                            PARTITION BY GROUP_CONCAT(DISTINCT cc.card_class_name ORDER BY cc.card_class_name)
                            ORDER BY w.price DESC
                        ) as rank_in_class
                    FROM weapon w
                    JOIN weapon_rank wr ON w.weapon_rank = wr.weaponrank_id
                    JOIN weapon_class wc ON w.weapon_id = wc.weapon_id
                    JOIN card_class cc ON wc.card_class_id = cc.card_class_id
                    WHERE w.price > 0
                    GROUP BY w.weapon_id
                )
                SELECT * FROM WeaponRanks WHERE weapon_name = %s
                """,
                (item_name,)
            )
            if weapon_result:
                item_details = weapon_result[0]
                item_type = 'weapon'
            else:
                for table, name_column, typed in [
                    ('dungeon_key', 'dungeonkey_name', 'dungeonkey'),
                    ('product',     'product_name',     'product'),
                    ('pickaxe',     'pickaxe_name',     'pickaxe'),
                    ('skill',       'skill_name',       'skill')
                ]:
                    res = await execute_query_with_retry(
                        f"SELECT * FROM {table} WHERE {name_column} = %s",
                        (item_name,)
                    )
                    if res:
                        item_details = res[0]
                        item_type = typed
                        break

            if not item_details:
                await ctx.send(_tr(lang,
                    "Error: The specified item does not exist.",
                    "خطأ: العنصر المحدد غير موجود.",
                    "Erreur : l'objet spécifié n'existe pas."
                ))
                return

            # Must have gprice > 0
            if not item_details.get('gprice') or item_details['gprice'] == 0:
                await ctx.send(_tr(lang,
                    "This item cannot be purchased with gwon.",
                    "لا يمكن شراء هذا العنصر بـ GWon.",
                    "Cet objet ne peut pas être acheté avec des GWon."
                ))
                return

            base_price = item_details['gprice']
            final_price = base_price * quantity
            if user_currency < final_price:
                await ctx.send(_tr(lang,
                    f"You don't have enough gwon. You need {final_price:,} gwon but only have {user_currency:,} gwon.",
                    f"ليس لديك ما يكفي من GWon. تحتاج إلى {final_price:,} GWon لكن لديك {user_currency:,} GWon فقط.",
                    f"Vous n'avez pas assez de GWon. Il vous faut {final_price:,} GWon mais vous n'en avez que {user_currency:,}."
                ))
                return

            # Pending purchase?
            pending = await execute_query_with_retry("SELECT id FROM shop_status WHERE user_id = %s", (user_id,))
            if pending:
                await ctx.send(_tr(lang,
                    "⚠️ You already have a pending purchase. Please complete or cancel your previous purchase before buying this item again.",
                    "⚠️ لديك عملية شراء معلّقة بالفعل. يرجى إكمال أو إلغاء العملية السابقة قبل الشراء مرة أخرى.",
                    "⚠️ Vous avez déjà un achat en attente. Veuillez terminer ou annuler l'achat précédent avant d'acheter à nouveau."
                ))
                return

            await execute_query_with_retry("INSERT INTO shop_status (user_id) VALUES (%s)", (user_id,))
            await execute_query_with_retry("INSERT INTO gamble_status (user_id) VALUES (%s)", (user_id,))

            # Confirmation embed
            embed = discord.Embed(
                title=_tr(lang, "🛒 Purchase Confirmation (Gwon)", "🛒 تأكيد الشراء (GWon)", "🛒 Confirmation d'achat (GWon)"),
                color=discord.Color.gold()
            )
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            embed.add_field(name=_tr(lang, "Item", "العنصر", "Objet"), value=f"**{item_name}**", inline=False)
            embed.add_field(name=_tr(lang, "Quantity", "الكمية", "Quantité"), value=f"**{quantity}**", inline=True)
            if item_type == 'weapon':
                embed.add_field(name=_tr(lang, "Class", "الفئة", "Classe"), value=f"**{item_details['class_names']}**", inline=True)

            embed.add_field(name=_tr(lang, "Price per Item", "السعر لكل قطعة", "Prix unitaire"), value=f"**{base_price:,} <:gamble:1314341368176574564>**", inline=True)
            embed.add_field(name=_tr(lang, "Total Price", "السعر الإجمالي", "Prix total"), value=f"**{final_price:,} <:gamble:1314341368176574564>**", inline=True)
            embed.add_field(name=_tr(lang, "Your Balance", "رصيدك", "Votre solde"), value=f"**{user_currency:,} <:gamble:1314341368176574564>**", inline=True)
            embed.add_field(name=_tr(lang, "Remaining Balance", "الرصيد المتبقي", "Solde restant"), value=f"**{user_currency - final_price:,} <:gamble:1314341368176574564>**", inline=True)

            # Item extras (use multilingual descs where applicable)
            if item_type == 'weapon':
                stats = []
                if item_details.get('attack_boost', 0) > 0:
                    stats.append(_tr(lang, f"Attack +{item_details['attack_boost']}", f"هجوم +{item_details['attack_boost']}", f"Attaque +{item_details['attack_boost']}"))
                if item_details.get('health_boost', 0) > 0:
                    stats.append(_tr(lang, f"Health +{item_details['health_boost']}", f"صحة +{item_details['health_boost']}", f"Santé +{item_details['health_boost']}"))
                if item_details.get('speed_boost', 0) > 0:
                    stats.append(_tr(lang, f"Speed +{item_details['speed_boost']}", f"سرعة +{item_details['speed_boost']}", f"Vitesse +{item_details['speed_boost']}"))
                if item_details.get('defense_boost', 0) > 0:
                    stats.append(_tr(lang, f"Power +{item_details['defense_boost']}", f"قوة +{item_details['defense_boost']}", f"Puissance +{item_details['defense_boost']}"))
                embed.add_field(name=_tr(lang, "Stats", "الإحصائيات", "Statistiques"),
                                value=("**" + " | ".join(stats) + "**") if stats else _tr(lang, "No stat boosts", "لا توجد زيادات", "Aucun bonus de stats"),
                                inline=False)
            elif item_type == 'product':
                if item_details.get('xp_boost', 0) > 0:
                    embed.add_field(name=_tr(lang, "XP Boost", "زيادة الخبرة", "Bonus d'XP"), value=f"**+{item_details['xp_boost']}**", inline=True)
                if lang == 1 and item_details.get('product_ardescription'):
                    pdesc = item_details['product_ardescription']
                elif lang == 3 and item_details.get('product_frdescription'):
                    pdesc = item_details['product_frdescription']
                else:
                    pdesc = item_details.get('product_description', '')
                embed.add_field(name=_tr(lang, "Description", "الوصف", "Description"), value=f"*{pdesc}*", inline=False)
            elif item_type == 'dungeonkey' or item_type == 'dungeon_key':
                dname = item_details.get('dungeonkey_name', '')
                embed.add_field(name=_tr(lang, "Dungeon Rank", "رتبة الدنجن", "Rang du donjon"), value=f"**{dname}**", inline=True)
            elif item_type == 'pickaxe':
                minv = item_details.get('min_value', 0)
                maxv = item_details.get('max_value', 0)
                if lang == 1 and item_details.get('pickaxe_ardescription'):
                    pdesc = item_details['pickaxe_ardescription']
                elif lang == 3 and item_details.get('pickaxe_frdescription'):
                    pdesc = item_details['pickaxe_frdescription']
                else:
                    pdesc = item_details.get('pickaxe_description', '')
                embed.add_field(
                    name=_tr(lang, "Pickaxe Stats", "إحصائيات المعول", "Stats de la pioche"),
                    value=_tr(lang,
                        f"**Min Value: {minv} <:won:1304034228966658048>\nMax Value: {maxv} <:won:1304034228966658048>**\n{pdesc}",
                        f"**أقل قيمة: {minv} <:won:1304034228966658048>\nأعلى قيمة: {maxv} <:won:1304034228966658048>**\n{pdesc}",
                        f"**Valeur min : {minv} <:won:1304034228966658048>\nValeur max : {maxv} <:won:1304034228966658048>**\n{pdesc}"
                    ),
                    inline=False
                )
            elif item_type == 'skill':
                sdesc = (
                    item_details.get('ardescription', item_details.get('description', _tr(lang, "No description available.", "لا يوجد وصف متاح.", "Pas de description disponible."))) if lang == 1
                    else item_details.get('frdescription', item_details.get('description', _tr(lang, "No description available.", "لا يوجد وصف متاح.", "Pas de description disponible."))) if lang == 3
                    else item_details.get('description', _tr(lang, "No description available.", "لا يوجد وصف متاح.", "Pas de description disponible."))
                )
                embed.add_field(name=_tr(lang, "Skill Description", "وصف المهارة", "Description de la compétence"), value=f"*{sdesc}*", inline=False)

            embed.set_footer(text=_tr(lang,
                "Please confirm your purchase within 60 seconds.",
                "يرجى تأكيد الشراء خلال 60 ثانية.",
                "Veuillez confirmer votre achat dans les 60 secondes."
            ))

            view = EnhancedConfirmView(user_id)
            view.original_user = ctx.author
            view.ctx = ctx
            view.cog = self
            view.final_price = final_price
            view.is_gamble = True
            msg = await ctx.send(embed=embed, view=view)

            await view.wait()
            await msg.delete()

            # Always clean up
            await execute_query_with_retry("DELETE FROM shop_status WHERE user_id = %s", (user_id,))
            await execute_query_with_retry("DELETE FROM gamble_status WHERE user_id = %s", (user_id,))

            if view.value is None:
                await ctx.send(_tr(lang, "Purchase timed out.", "انتهى وقت الشراء.", "Temps d'achat écoulé."))
                return
            elif not view.value:
                await ctx.send(_tr(lang, "Purchase cancelled.", "تم إلغاء الشراء.", "Achat annulé."))
                return

            # Charge gwon
            gwon_result = await execute_query_with_retry("SELECT gamble_currency FROM user WHERE user_id = %s", (user_id,))
            user_currency = gwon_result[0]['gamble_currency']
            new_currency = user_currency - final_price
            await execute_query_with_retry("UPDATE user SET gamble_currency = %s WHERE user_id = %s", (new_currency, user_id))

            # Inventory update
            inv_res = await execute_query_with_retry("SELECT inventory_id FROM inventory WHERE user_id = %s", (user_id,))
            if not inv_res:
                await ctx.send(_tr(lang,
                    "Error: You don't have an inventory.",
                    "خطأ: ليس لديك مخزون.",
                    "Erreur : vous n'avez pas d'inventaire."
                ))
                return

            inventory_id = inv_res[0]['inventory_id']
            table_map = {
                'weapon': 'inventory_weapon',
                'product': 'inventory_product',
                'dungeonkey': 'inventory_dungeonkey',
                'pickaxe': 'inventory_pickaxe',
                'skill': 'inventory_skills'
            }
            inventory_table = table_map[item_type]
            item_id_column = 'id_pickaxe' if item_type == 'pickaxe' else f"{item_type}_id"

            existing_item = await execute_query_with_retry(
                f"SELECT * FROM {inventory_table} WHERE inventory_id = %s AND {item_id_column} = %s",
                (inventory_id, item_details[item_id_column])
            )
            if existing_item:
                qty_col = 'quantiter' if 'quantiter' in existing_item[0] else 'quantity'
                new_qty = existing_item[0][qty_col] + quantity
                await execute_query_with_retry(
                    f"UPDATE {inventory_table} SET {qty_col} = %s WHERE inventory_id = %s AND {item_id_column} = %s",
                    (new_qty, inventory_id, item_details[item_id_column])
                )
            else:
                try:
                    await execute_query_with_retry(
                        f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantiter) VALUES (%s, %s, %s)",
                        (inventory_id, item_details[item_id_column], quantity)
                    )
                except Exception:
                    await execute_query_with_retry(
                        f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantity) VALUES (%s, %s, %s)",
                        (inventory_id, item_details[item_id_column], quantity)
                    )

            # Success embed
            success = discord.Embed(
                title=_tr(lang, "🎉 Purchase Successful! (Gwon)", "🎉 تمت عملية الشراء بنجاح! (GWon)", "🎉 Achat réussi ! (GWon)"),
                color=discord.Color.green()
            )
            success.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            success.add_field(name=_tr(lang, "Item", "العنصر", "Objet"), value=item_name, inline=True)
            success.add_field(name=_tr(lang, "Quantity", "الكمية", "Quantité"), value=str(quantity), inline=True)
            success.add_field(name=_tr(lang, "Total Price", "السعر الإجمالي", "Prix total"), value=f"{final_price:,} <:gamble:1314341368176574564>", inline=True)
            success.add_field(name=_tr(lang, "New Balance", "الرصيد الجديد", "Nouveau solde"), value=f"{new_currency:,} <:gamble:1314341368176574564>", inline=True)
            success.set_footer(text=_tr(lang, "Thank you for your purchase!", "شكراً لعملية الشراء!", "Merci pour votre achat !"))

            if view.interaction:
                await view.interaction.response.send_message(embed=success)
            else:
                await ctx.send(embed=success)

            # Quest progress (same as buy)
            user_quests = await execute_query_with_retry("""
                SELECT uq.*, q.quest_name, q.required_count
                FROM user_quests uq
                JOIN quest q ON uq.quest_id = q.quest_id
                WHERE uq.user_id = %s AND q.quest_name = 'Buy 1 shop item' AND uq.completion_status = 0
            """, (user_id,))
            if user_quests:
                quest = user_quests[0]
                new_count = quest['current_count'] + 1
                await execute_query_with_retry("""
                    UPDATE user_quests
                    SET current_count = %s,
                        completion_status = CASE WHEN %s >= %s THEN 1 ELSE 0 END
                    WHERE userquests_id = %s
                """, (new_count, new_count, quest['required_count'], quest['userquests_id']))

                if new_count >= quest['required_count']:
                    inv2 = await execute_query_with_retry("SELECT inventory_id FROM inventory WHERE user_id = %s", (user_id,))
                    if inv2:
                        inv_id = inv2[0]['inventory_id']
                        chest = await execute_query_with_retry(
                            "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = 1",
                            (inv_id,)
                        )
                        if chest:
                            await execute_query_with_retry(
                                "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = 1",
                                (inv_id,)
                            )
                        else:
                            await execute_query_with_retry(
                                "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, 1, 1)",
                                (inv_id,)
                            )

        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {e}", f"حدث خطأ: {e}", f"Une erreur s'est produite : {e}"))

    @commands.command(name="shop", aliases=["sh", "sp"])
    async def shop(self, ctx):
        lang = await getlang(ctx.author.id)
        try:
            user_id = ctx.author.id

            query = "SELECT * FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(query, (str(user_id),))

            if not user_result:
                await ctx.send(_tr(lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: يجب عليك التسجيل في البوت أولاً.",
                    "Erreur : vous devez d'abord vous inscrire sur le bot."
                ))
                return

            premium_level = await self.check_premium_status(user_result[0]['user_id'])

            view = ShopCategoryView(self, ctx.author, lang)  # pass lang
            embed = discord.Embed(
                title=_tr(lang, "🏯 Hunter's Association Shop", "🏯 متجر جمعية الصيادين", "🏯 Boutique de l'Association des Chasseurs"),
                description=_tr(lang,
                    "Welcome, brave hunter! What would you like to purchase today?",
                    "مرحباً أيها الصياد الشجاع! ماذا ترغب في شراءه اليوم؟",
                    "Bienvenue, chasseur courageux ! Que souhaitez-vous acheter aujourd'hui ?"
                ),
                color=discord.Color.dark_purple()
            )
            embed.set_image(url=MAIN_SHOP_IMAGE)
            embed.add_field(name="⚔️ " + _tr(lang, "Weapons", "الأسلحة", "Armes"),
                            value=_tr(lang, "Powerful weapons to slay monsters", "أسلحة قوية لقتل الوحوش", "Des armes puissantes pour terrasser les monstres"),
                            inline=False)
            embed.add_field(name="🔮 " + _tr(lang, "Boosters", "المعززات", "Boosters"),
                            value=_tr(lang, "Magical items to enhance your abilities", "عناصر سحرية لتعزيز قدراتك", "Objets magiques pour améliorer vos capacités"),
                            inline=False)
            embed.add_field(name="🗝️ " + _tr(lang, "Dungeon Keys", "مفاتيح الأبراج", "Clés de Donjon"),
                            value=_tr(lang, "Keys to access special dungeons", "مفاتيح للوصول إلى الأبراج الخاصة", "Des clés pour accéder à des donjons spéciaux"),
                            inline=False)
            embed.add_field(name="⛏️ " + _tr(lang, "Pickaxes", "المعاول", "Pioches"),
                            value=_tr(lang, "Tools to enhance your mining capabilities", "أدوات لتعزيز قدرات التعدين لديك", "Des outils pour améliorer vos capacités de minage"),
                            inline=False)
            embed.add_field(name="📜 " + _tr(lang, "Skills", "المهارات", "Compétences"),
                            value=_tr(lang, "Unlock powerful abilities for your class", "افتح قدرات قوية لفئتك", "Débloquez des compétences puissantes pour votre classe"),
                            inline=False)

            footer_text = _tr(lang,
                "Choose a category to browse. Your equipment may determine your fate in the dungeons!",
                "اختر فئة لتصفحها. قد يحدد تجهيزك مصيرك في الأبراج!",
                "Choisissez une catégorie à parcourir. Votre équipement peut déterminer votre destin dans les donjons !"
            )
            if premium_level > 0:
                tiers = {1: _tr(lang,"National Ranker","رتبة وطنية","Rang National"),
                         2: _tr(lang,"Ruler","الحاكم","Souverain"),
                         3: _tr(lang,"Monarch","العاهل","Monarque")}
                footer_text = f"{tiers[premium_level]} | {footer_text}"
            embed.set_footer(text=footer_text)

            message = await ctx.send(embed=embed, view=view)
            self.last_shop_message = message.id

        except Exception as e:
            await ctx.send(_tr(lang,
                f"An error occurred while displaying the shop: {str(e)}",
                f"حدث خطأ أثناء عرض المتجر: {str(e)}",
                f"Une erreur s'est produite lors de l'affichage de la boutique : {str(e)}"
            ))

    async def show_category(self, interaction: discord.Interaction, category: ItemType):
        lang = await getlang(interaction.user.id)
        try:
            user_result = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(interaction.user.id),)
            )
            premium_level = 0
            if user_result:
                premium_level = await self.check_premium_status(user_result[0]['user_id'])

            items = await self.get_category_items(category)
            if not items:
                await interaction.response.send_message(_tr(lang,
                    "No items found in this category.",
                    "لا توجد عناصر في هذه الفئة.",
                    "Aucun objet trouvé dans cette catégorie."
                ), ephemeral=True)
                return

            view = ShopPaginationView(self, category, interaction.user, lang)
            view.items = items
            await view.update_button_states(len(items))

            embed = self.create_category_embed(category, items, 1, premium_level, lang)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            await interaction.response.send_message(_tr(lang, f"An error occurred: {e}", f"حدث خطأ: {e}", f"Une erreur s'est produite : {e}"), ephemeral=True)

    async def update_category_page(self, interaction: discord.Interaction, category: ItemType, page: int):
        lang = await getlang(interaction.user.id)
        try:
            user_result = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s",
                (str(interaction.user.id),)
            )
            premium_level = 0
            if user_result:
                premium_level = await self.check_premium_status(user_result[0]['user_id'])

            items = await self.get_category_items(category)

            embed = self.create_category_embed(category, items, page, premium_level, lang)
            view = ShopPaginationView(self, category, interaction.user, lang)
            view.current_page = page
            view.items = items
            await view.update_button_states(len(items))

            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(_tr(lang, f"An error occurred: {e}", f"حدث خطأ: {e}", f"Une erreur s'est produite : {e}"), ephemeral=True)

    # (Optional) If you keep this helper for ad-hoc displays in other flows:
    def format_item(self, category: ItemType, item: Dict[str, Union[int, str]], lang: int) -> tuple[str, str]:
        def price_label(amount: int) -> str:
            return _tr(lang, f"Price: {amount} 💰", f"السعر: {amount} 💰", f"Prix : {amount} 💰")
        print("langformat: ",lang)
        # PICKAXE
        if category == ItemType.PICKAXE:
            icon = item.get('pickaxe_icon', '')
            pname = item.get('name_pickaxe') or item.get('pickaxe_name')
            name = f"{icon}| {pname} | {price_label(item['price'])}"
            if 'gprice' in item and item['gprice'] > 0:
                name += f" | {item['gprice']} <:gamble:1314341368176574564>"
            if lang == 1 and item.get('pickaxe_ardescription'):
                desc = item['pickaxe_ardescription']
            elif lang == 3 and item.get('pickaxe_frdescription'):
                desc = item['pickaxe_frdescription']
            else:
                desc = item.get('pickaxe_description', '')
            value = f"```{desc}```"
            return name, value

        # WEAPON
        if category == ItemType.WEAPON:
            icon = item.get('weapon_icon', '')
            name = f"{icon}| {item['weapon_name']}"
            val = price_label(item['price'])
            if 'gprice' in item and item['gprice'] > 0:
                val += f" | {item['gprice']} <:gamble:1314341368176574564>"
            val += f" | " + _tr(lang, f"Class: {item.get('card_class_name','')}", f"الفئة: {item.get('card_class_name','')}", f"Classe : {item.get('card_class_name','')}")
            stats = []
            if item.get('attack_boost', 0) > 0:   stats.append(_tr(lang, f"Attack+{item['attack_boost']}", "هجوم+", "Attaque+") + str(item['attack_boost']))
            if item.get('health_boost', 0) > 0:   stats.append(_tr(lang, f"Health+{item['health_boost']}", "صحة+", "Santé+") + str(item['health_boost']))
            if item.get('speed_boost', 0) > 0:    stats.append(_tr(lang, f"Speed+{item['speed_boost']}", "سرعة+", "Vitesse+") + str(item['speed_boost']))
            if item.get('defense_boost', 0) > 0:  stats.append(_tr(lang, f"Power+{item['defense_boost']}", "قوة+", "Puissance+") + str(item['defense_boost']))
            if stats:
                val += "\n" + " | ".join(stats)
            return name, val

        # BOOSTER (product)
        if category == ItemType.BOOSTER:
            icon = item.get('product_icon', '')
            name = f"{icon}| {item['product_name']} | {price_label(item['price'])}"
            if 'gprice' in item and item['gprice'] > 0:
                name += f" | {item['gprice']} <:gamble:1314341368176574564>"
            if lang == 1 and item.get('product_ardescription'):
                print("arb")
                desc = item['product_ardescription']
            elif lang == 3:
                print("frb")
                desc = item['product_frdescription']
            else:
                print("engb")
                desc = item.get('product_description', '')
            value = f"```{desc}```"
            return name, value

        # SKILL
        if category == ItemType.SKILL:
            name = f"🎯| {item['skill_name']} | {price_label(item['price'])}"
            value = f"```{item.get('description', _tr(lang, 'No description available.', 'لا يوجد وصف متاح.', 'Pas de description disponible.'))}```"
            return name, value

        # KEY (dungeon_key)
        icon = item.get('dungeonkey_icon', '')
        name = f"{icon}| {item['dungeonkey_name']} | {price_label(item['price'])}"
        if 'gprice' in item and item['gprice'] > 0:
            name += f" | {item['gprice']} <:gamble:1314341368176574564>"
        if lang == 1 and item.get('dungeonkey_ardescription'):
            desc = item['dungeonkey_ardescription']
        elif lang == 3 and item.get('dungeonkey_frdescription'):
            desc = item['dungeonkey_frdescription']
        else:
            desc = item.get('dungeonkey_description', '')
        value = f"```{desc}```"
        return name, value

    async def handle_purchase(self, interaction: discord.Interaction, item_data: Dict[str, Union[int, str]], category: ItemType, quantity: int = 1):
        user_id = interaction.user.id
       
        try:
            # Get user's inventory and balance
            inventory_query = "SELECT * FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
           
            if not inventory_result:
                await interaction.response.send_message("❌ You don't have an inventory! Please create a character first.", ephemeral=True)
                return
           
            inventory = inventory_result[0]
            current_balance = inventory['balance']
           
            # Calculate total cost
            if category == ItemType.WEAPON:
                item_price = item_data['price']
                item_id = item_data['weapon_id']
                item_name = item_data['weapon_name']
            elif category == ItemType.BOOSTER:
                item_price = item_data['price']
                item_id = item_data['product_id']
                item_name = item_data['product_name']
            elif category == ItemType.KEY:
                item_price = item_data['price']
                item_id = item_data['id_dungeon_key']
                item_name = item_data['key_name']
            elif category == ItemType.PICKAXE:
                item_price = item_data['price']
                item_id = item_data['id_pickaxe']
                item_name = item_data['pickaxe_name']
            elif category == ItemType.SKILL:
                item_price = item_data['price']
                item_id = item_data['skill_id']
                item_name = item_data['skill_name']
           
            total_cost = item_price * quantity
           
            # Check if user has enough balance
            if current_balance < total_cost:
                await interaction.response.send_message(f"❌ Insufficient funds! You need {total_cost:,} 💰 but only have {current_balance:,} 💰.", ephemeral=True)
                return
           
            # Deduct balance
            new_balance = current_balance - total_cost
            update_balance_query = "UPDATE inventory SET balance = %s WHERE user_id = %s"
            await execute_query_with_retry(update_balance_query, (new_balance, user_id))
           
            # Add item to appropriate inventory table
            if category == ItemType.WEAPON:
                # Check if weapon already exists in inventory
                check_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
                existing_item = await execute_query_with_retry(check_query, (inventory['inventory_id'], item_id))
               
                if existing_item:
                    # Update quantity
                    new_quantity = existing_item[0]['quantity'] + quantity
                    update_query = "UPDATE inventory_weapon SET quantity = %s WHERE inventory_id = %s AND weapon_id = %s"
                    await execute_query_with_retry(update_query, (new_quantity, inventory['inventory_id'], item_id))
                else:
                    # Insert new item
                    insert_query = "INSERT INTO inventory_weapon (inventory_id, weapon_id, quantity) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory['inventory_id'], item_id, quantity))
           
            elif category == ItemType.BOOSTER:
                # Check if booster already exists in inventory
                check_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
                existing_item = await execute_query_with_retry(check_query, (inventory['inventory_id'], item_id))
               
                if existing_item:
                    # Update quantity
                    new_quantity = existing_item[0]['quantity'] + quantity
                    update_query = "UPDATE inventory_product SET quantity = %s WHERE inventory_id = %s AND product_id = %s"
                    await execute_query_with_retry(update_query, (new_quantity, inventory['inventory_id'], item_id))
                else:
                    # Insert new item
                    insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantity) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory['inventory_id'], item_id, quantity))
           
            elif category == ItemType.KEY:
                # Check if key already exists in inventory
                check_query = "SELECT * FROM inventory_dungeonkey WHERE inventory_id = %s AND dungeon_key_id = %s"
                existing_item = await execute_query_with_retry(check_query, (inventory['inventory_id'], item_id))
               
                if existing_item:
                    # Update quantity
                    new_quantity = existing_item[0]['quantity'] + quantity
                    update_query = "UPDATE inventory_dungeonkey SET quantity = %s WHERE inventory_id = %s AND dungeon_key_id = %s"
                    await execute_query_with_retry(update_query, (new_quantity, inventory['inventory_id'], item_id))
                else:
                    # Insert new item
                    insert_query = "INSERT INTO inventory_dungeonkey (inventory_id, dungeon_key_id, quantity) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory['inventory_id'], item_id, quantity))
           
            elif category == ItemType.PICKAXE:
                # Check if pickaxe already exists in inventory
                check_query = "SELECT * FROM inventory_pickaxe WHERE inventory_id = %s AND pickaxe_id = %s"
                existing_item = await execute_query_with_retry(check_query, (inventory['inventory_id'], item_id))
               
                if existing_item:
                    # Update quantity
                    new_quantity = existing_item[0]['quantity'] + quantity
                    update_query = "UPDATE inventory_pickaxe SET quantity = %s WHERE inventory_id = %s AND pickaxe_id = %s"
                    await execute_query_with_retry(update_query, (new_quantity, inventory['inventory_id'], item_id))
                else:
                    # Insert new item
                    insert_query = "INSERT INTO inventory_pickaxe (inventory_id, pickaxe_id, quantity) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory['inventory_id'], item_id, quantity))
           
            elif category == ItemType.SKILL:
                # Check if skill already exists in inventory
                check_query = "SELECT * FROM inventory_skills WHERE inventory_id = %s AND skill_id = %s"
                existing_item = await execute_query_with_retry(check_query, (inventory['inventory_id'], item_id))
               
                if existing_item:
                    # Update quantity
                    new_quantity = existing_item[0]['quantiter'] + quantity
                    update_query = "UPDATE inventory_skills SET quantiter = %s WHERE inventory_id = %s AND skill_id = %s"
                    await execute_query_with_retry(update_query, (new_quantity, inventory['inventory_id'], item_id))
                else:
                    # Insert new item
                    insert_query = "INSERT INTO inventory_skills (inventory_id, skill_id, quantiter) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory['inventory_id'], item_id, quantity))
           
            # Success message
            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=f"You have successfully purchased **{quantity}x {item_name}** for **{total_cost:,} 💰**!",
                color=discord.Color.green()
            )
            embed.add_field(name="Remaining Balance", value=f"{new_balance:,} 💰", inline=True)
           
            await interaction.response.send_message(embed=embed, ephemeral=True)
           
        except Exception as e:
            print(f"Error in handle_purchase: {e}")
            await interaction.response.send_message("❌ An error occurred while processing your purchase. Please try again.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ShopCommands(bot))
