import discord
from discord.ext import commands
from discord import ButtonStyle, SelectOption
from typing import List, Dict, Union, Tuple
from enum import Enum
import time
import asyncio
from datetime import datetime, timedelta
from utils.database import execute_query_with_retry, get_metrics

# Constants
MAIN_SHOP_IMAGE = "https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/01/solo-leveling-anime-to-premiere-this-week-on-crunchyroll.jpg"
WEAPONS_SHOP_IMAGE = "https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/02/solo-leveling-6-strongest-weapons-of-sung-jinwoo.jpg"
BOOSTERS_SHOP_IMAGE = "https://static0.gamerantimages.com/wordpress/wp-content/uploads/2024/03/orb-of-avarice.jpg"
KEYS_SHOP_IMAGE = "https://i.imgur.com/TN4CdVv.png"  # Replace with actual image URL
PICKAXES_SHOP_IMAGE = "https://i.imgur.com/ariM9iv.png"  # Replace with an actual image URL

# Update ItemType enum
class ItemType(Enum):
    WEAPON = "Weapon"
    BOOSTER = "Booster"
    KEY = "Key"
    PICKAXE = "Pickaxe"

# Modify ShopCategoryView
class ShopCategorySelect(discord.ui.Select):
    def __init__(self, cog, original_user):
        self.cog = cog
        self.original_user = original_user
        
        options = [
            SelectOption(label="Weapons", value="WEAPON", emoji="⚔️"),
            SelectOption(label="Boosters", value="BOOSTER", emoji="🔮"),
            SelectOption(label="Dungeon Keys", value="KEY", emoji="🗝️"),
            SelectOption(label="Pickaxes", value="PICKAXE", emoji="⛏️")
        ]
        
        super().__init__(
            placeholder="Choose a Shop Category",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("You cannot interact with someone else's shop menu.", ephemeral=True)
            return
            
        selected_category = ItemType[self.values[0]]
        await self.cog.show_category(interaction, selected_category)

class ShopCategoryView(discord.ui.View):
    def __init__(self, cog, original_user):
        super().__init__(timeout=180.0)
        self.original_user = original_user
        self.add_item(ShopCategorySelect(cog, original_user))

class ShopPaginationView(discord.ui.View):
    def __init__(self, cog, category: ItemType, original_user):
        super().__init__(timeout=180.0)
        self.cog = cog
        self.category = category
        self.current_page = 1
        self.original_user = original_user
        self.items = []  # Store items to avoid repeated database calls

        self.next_button = discord.ui.Button(
            label="Next", 
            style=ButtonStyle.primary, 
            emoji="▶️"
        )
        self.next_button.callback = self.next_page

        self.prev_button = discord.ui.Button(
            label="Previous", 
            style=ButtonStyle.primary, 
            emoji="◀️"
        )
        self.prev_button.callback = self.previous_page

        self.back_button = discord.ui.Button(
            label="Back to Categories", 
            style=ButtonStyle.secondary, 
            emoji="🏠"
        )
        self.back_button.callback = self.go_back_to_categories

        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("You cannot interact with someone else's shop menu.", ephemeral=True)
            return False
        return True

    async def update_button_states(self, total_items: int):
        total_pages = -(-total_items // self.cog.items_per_page)
        self.prev_button.disabled = (self.current_page <= 1)
        self.next_button.disabled = (self.current_page >= total_pages)
        self.back_button.disabled = False

    async def fetch_items(self):
        self.items = await self.cog.get_category_items(self.category)
        return self.items

    async def go_back_to_categories(self, interaction: discord.Interaction):
        try:
            view = ShopCategoryView(self.cog, self.original_user)
            embed = discord.Embed(
                title="🏯 Hunter's Association Shop",
                description="Welcome, brave hunter! What would you like to purchase today?",
                color=discord.Color.dark_purple()
            )
            embed.set_image(url=MAIN_SHOP_IMAGE)
            embed.add_field(name="⚔️ Weapons", value="Powerful weapons to slay monsters", inline=False)
            embed.add_field(name="🔮 Boosters", value="Magical items to enhance your abilities", inline=False)
            embed.add_field(name="🗝️ Dungeon Keys", value="Keys to access special dungeons", inline=False)
            embed.add_field(name="⛏️ Pickaxes", value="Tools to enhance your mining capabilities", inline=False)
            embed.set_footer(text="Choose a category to browse. Your equipment may determine your fate in the dungeons!")

            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"Go back to categories error: {e}")

    async def previous_page(self, interaction: discord.Interaction):
        try:
            # Ensure we have items
            if not self.items:
                await self.fetch_items()
            
            total_pages = -(-len(self.items) // self.cog.items_per_page)
            
            if self.current_page > 1:
                self.current_page -= 1
                
                # Get premium level
                user_result = await execute_query_with_retry(
                    "SELECT user_id FROM user WHERE user_authorid = %s", 
                    (str(interaction.user.id),))
                
                premium_level = 0
                if user_result:
                    premium_level = await self.cog.check_premium_status(user_result[0]['user_id'])
                
                # Create embed for new page
                embed = self.cog.create_category_embed(self.category, self.items, self.current_page, premium_level)
                
                # Update button states
                await self.update_button_states(len(self.items))
                
                # Update message
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"Previous page error: {e}")

    async def next_page(self, interaction: discord.Interaction):
        try:
            # Ensure we have items
            if not self.items:
                await self.fetch_items()
            
            total_pages = -(-len(self.items) // self.cog.items_per_page)
            
            if self.current_page < total_pages:
                self.current_page += 1
                
                # Get premium level
                user_result = await execute_query_with_retry(
                    "SELECT user_id FROM user WHERE user_authorid = %s", 
                    (str(interaction.user.id),))
                
                premium_level = 0
                if user_result:
                    premium_level = await self.cog.check_premium_status(user_result[0]['user_id'])
                
                # Create embed for new page
                embed = self.cog.create_category_embed(self.category, self.items, self.current_page, premium_level)
                
                # Update button states
                await self.update_button_states(len(self.items))
                
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
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("Only the user who initiated this purchase can use these buttons!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Purchase", style=ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.interaction = interaction
        
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
                await interaction.response.send_message("You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return
            if trade_result:
                await interaction.response.send_message("You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
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
                await interaction.response.send_message("Error: Could not retrieve your currency information.")
                return
            
            if self.is_gamble:
                current_currency = currency_result[0]['gamble_currency']
            else:
                current_currency = currency_result[0]['won_currency']
            
            # 3. Compare with final price
            if current_currency < self.final_price:
                if self.is_gamble:
                    await interaction.response.send_message(f"You don't have enough gwon. You need {self.final_price:,} gwon but only have {current_currency:,} gwon.")
                else:
                    await interaction.response.send_message(f"You don't have enough won. You need {self.final_price:,} won but only have {current_currency:,} won.")
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

    def create_category_embed(self, category: ItemType, items: List[Dict[str, Union[int, str]]], page: int, premium_level: int = 0) -> discord.Embed:
        if category == ItemType.WEAPON:
            return self.create_weapon_embed(items, page, premium_level)
            
        # Original logic for other categories remains unchanged
        start = (page - 1) * self.items_per_page
        end = start + self.items_per_page
        page_items = items[start:end]

        if category == ItemType.PICKAXE:
            title = "⛏️ Pickaxes Shop"
            image_url = PICKAXES_SHOP_IMAGE
        elif category == ItemType.BOOSTER:
            title = "🔮 Boosters & Enhancers Shop"
            image_url = BOOSTERS_SHOP_IMAGE
        else:  # ItemType.KEY
            title = "🗝️ Dungeon Keys Shop"
            image_url = KEYS_SHOP_IMAGE

        embed = discord.Embed(title=title, color=discord.Color.gold())
        embed.set_image(url=image_url)

        for item in page_items:
            name, value = self.format_item(category, item)
            embed.add_field(name=name, value=value, inline=False)

        total_pages = -(-len(items) // self.items_per_page)
        
        # Add premium status to footer if applicable
        footer_text = f"Page {page}/{total_pages}"
        if premium_level > 0:
            premium_tiers = {1: "National Ranker", 2: "Ruler", 3: "Monarch"}
            footer_text += f" | {premium_tiers[premium_level]} Premium Member"
            
        embed.set_footer(text=footer_text)
        return embed

    def create_weapon_embed(self, items: List[Dict[str, Union[int, str]]], page: int, premium_level: int = 0) -> discord.Embed:
        start = (page - 1) * self.items_per_page
        end = start + self.items_per_page
        page_items = items[start:end]

        embed = discord.Embed(title="⚔️ Weapons Shop", color=discord.Color.gold())
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
                embed.add_field(name=f"📋 {class_display}", value="─" * 20, inline=False)

            # Calculate discounted price if applicable
            original_price = item['price']
            discounted_price, has_discount = self.calculate_discounted_price(
                item, premium_level
            )

            # Format weapon stats
            stats = []
            if item['attack_boost'] > 0:
                stats.append(f"Attack+{item['attack_boost']}")
            if item['attack_percentage'] > 0:
                stats.append(f"Attack+{item['attack_percentage']}%")
            if item['health_boost'] > 0:
                stats.append(f"Health+{item['health_boost']}")
            if item['health_percentage'] > 0:
                stats.append(f"Health+{item['health_percentage']}%")
            if item['speed_boost'] > 0:
                stats.append(f"Speed+{item['speed_boost']}")
            if item['speed_percentage'] > 0:
                stats.append(f"Speed+{item['speed_percentage']}%")
            if item['defense_boost'] > 0:
                stats.append(f"Defense+{item['defense_boost']}")
            if item['defense_percentage'] > 0:
                stats.append(f"Defense+{item['defense_percentage']}%")

            # Add weapon icon and format price display
            icon_prefix = item.get('weapon_icon', '') if item.get('weapon_icon') else ''
            name = f"{icon_prefix} | {item['weapon_name']} ({item['weaponrank_name']})"
            
            # Format price display with discount if applicable
            if has_discount:
                value = f"Price: ~~{original_price:,}~~ {discounted_price:,} 💰\n{' | '.join(stats)}"
            else:
                value = f"Price: {original_price:,} 💰\n{' | '.join(stats)}"
            
            embed.add_field(name=name, value=value, inline=False)

        total_pages = -(-len(items) // self.items_per_page)
        print("done")        
        # Add premium status to footer if applicable
        footer_text = f"Page {page}/{total_pages}"
        if premium_level > 0:
            premium_tiers = {1: "National Rank", 2: "Ruler", 3: "Monarch"}
            footer_text += f" | {premium_tiers[premium_level]} Premium Member"
            
        embed.set_footer(text=footer_text)
        return embed

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
        if not args:
            await ctx.send("Usage: sl buy <item_name> <quantity>")
            return
        print("1")
        args = args.split()
        item_name = " ".join(args) if len(args) == 1 else " ".join(args[:-1])
        print(item_name)
        try:
            quantity = 1 if len(args) == 1 else int(args[-1])
            if quantity <= 0:   
                await ctx.send("Quantity must be a positive number.")
                return
        except ValueError:
            # If the last argument can't be converted to a number, treat it as part of the item name
            item_name = " ".join(args)
            quantity = 1

        try:
            # Get user details and premium status
            user_result = await execute_query_with_retry(
                "SELECT user_id, won_currency FROM user WHERE user_authorid = %s", 
                (str(ctx.author.id),))
            print(user_result)
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return  
            user_id = user_result[0]['user_id']
            user_currency = user_result[0]['won_currency']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Check premium status
            premium_level = await self.check_premium_status(user_id)

            # Check if item exists and get its details
            item_details = None
            item_type = None
            weapon_rank_in_class = None

            # Modified weapon query to include proper rank calculation
            weapon_query = """
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
            SELECT *
            FROM WeaponRanks
            WHERE weapon_name = %s
            """

            # Try to find the item in different tables
            weapon_result = await execute_query_with_retry(weapon_query, (item_name,))
            if weapon_result:
                item_details = weapon_result[0]
                item_type = 'weapon'
                weapon_rank_in_class = item_details['rank_in_class']
            else:
                # Check other item types
                for table, name_column, item_typed in [
                    ('dungeon_key', 'dungeonkey_name', 'dungeonkey'),
                    ('product', 'product_name', 'product'),
                    ('pickaxe', 'pickaxe_name', 'pickaxe')
                ]:
                    item_result = await execute_query_with_retry(f"SELECT * FROM {table} WHERE {name_column} = %s", (item_name,))
                    if item_result:
                        if table =="product":
                            if item_result[0]['buyable'] == 0:
                                await ctx.send("Error : this item is not listed for sale")
                                return
                        item_details = item_result[0]
                        item_type = item_typed
                        break
                    print("result")
                    print(item_result)
            if not item_details:
                await ctx.send("Error: The specified item does not exist.")
                return

            # Calculate final price based on item type and premium status
            base_price = item_details['price'] if item_type == 'pickaxe' else item_details['price']
            discount_applied = False
            
            if item_type == 'weapon' and premium_level > 0:
                base_price, discount_applied = self.calculate_discounted_price(
                    item_details, premium_level
                )

            final_price = base_price * quantity

            if user_currency < final_price:
                await ctx.send(f"You don't have enough won. You need {final_price} won.")
                return

            # Check for pending purchase in the shop_status table
            item_id_column = f"{item_type}_id"
            if item_type == 'pickaxe':
                item_id_column = 'id_pickaxe'
            
            item_id = item_details[item_id_column]
            print("item detail :", item_id)

            # Query to check for existing pending purchase
            pending_check_query = f"""
            SELECT id FROM shop_status 
            WHERE user_id = %s
            """
            
            pending_result = await execute_query_with_retry(pending_check_query, (user_id,))
            
            if pending_result:
                # User already has a pending purchase for this item
                await ctx.send("⚠️ You already have a pending purchase. Please complete or cancel your previous purchase before buying this item again.")
                return

            # Insert new pending purchase entry
            pending_columns = ["user_id"]
            pending_values = [user_id]
            
            # Only add the relevant item ID column based on the item type
            pending_columns.append(item_id_column)
            pending_values.append(item_id)
            
            # Create the placeholders for the SQL query
            placeholders = ', '.join(['%s'] * len(pending_columns))
            columns_str = ', '.join(pending_columns)
            
            insert_pending_query = f"""
            INSERT INTO shop_status ({columns_str})
            VALUES ({placeholders})
            """
            
            await execute_query_with_retry(insert_pending_query, pending_values)
            
            # Create confirmation embed
            embed = discord.Embed(title="🛒 Purchase Confirmation", color=discord.Color.gold())
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            embed.add_field(name="Item", value=f"**{item_name}**", inline=False)
            embed.add_field(name="Quantity", value=f"**{quantity}**", inline=True)
            
            # Add class information for weapons
            if item_type == 'weapon':
                embed.add_field(name="Class", value=f"**{item_details['class_names']}**", inline=True)
            
            # Price field varies by item type
            if item_type == 'weapon' and discount_applied:
                original_price = item_details['price']
                embed.add_field(
                    name="Price per Item", 
                    value=f"**~~{original_price:,}~~ {base_price:,} won (Premium Discount)**", 
                    inline=True
                )
            else:
                embed.add_field(name="Price per Item", value=f"**{base_price:,} won**", inline=True)

            embed.add_field(name="Total Price", value=f"**{final_price:,} won**", inline=True)
            embed.add_field(name="Your Balance", value=f"**{user_currency:,} won**", inline=True)
            embed.add_field(name="Remaining Balance", value=f"**{user_currency - final_price:,} won**", inline=True)
            
            # Add item-specific details
            if item_type == 'weapon':
                stats = []
                if item_details['attack_boost'] > 0:
                    stats.append(f"Attack +{item_details['attack_boost']}")
                if item_details['health_boost'] > 0:
                    stats.append(f"Health +{item_details['health_boost']}")
                if item_details['speed_boost'] > 0:
                    stats.append(f"Speed +{item_details['speed_boost']}")
                if item_details['defense_boost'] > 0:
                    stats.append(f"Power +{item_details['defense_boost']}")
                embed.add_field(name="Stats", value="**" + " | ".join(stats) + "**" if stats else "No stat boosts", inline=False)
            elif item_type == 'product':
                if item_details['xp_boost'] > 0:
                    embed.add_field(name="XP Boost", value=f"**+{item_details['xp_boost']}**", inline=True)
                embed.add_field(name="Description", value=f"*{item_details['product_description']}*", inline=False)
            elif item_type == 'dungeon_key':
                embed.add_field(name="Dungeon Rank", value=f"**{item_details['dungeonkey_name']}**", inline=True)
            elif item_type == 'pickaxe':
                embed.add_field(
                    name="Pickaxe Stats", 
                    value=f"**Min Value: {item_details['min_value']} <:won:1304034228966658048>\nMax Value: {item_details['max_value']} <:won:1304034228966658048>**", 
                    inline=False
                )

            if premium_level > 0:
                premium_tiers = {1: "National Ranker", 2: "Ruler", 3: "Monarch"}
                embed.set_footer(text=f"{premium_tiers[premium_level]} Premium Member | Please confirm your purchase within 60 seconds.")
            else:
                embed.set_footer(text="Please confirm your purchase within 60 seconds.")

            # Create and send confirmation view
            view = EnhancedConfirmView(user_id)
            view.original_user = ctx.author
            view.ctx = ctx
            view.cog = self
            view.final_price = final_price  # Pass the final price to the view
            confirmation_message = await ctx.send(embed=embed, view=view)
            
            # Wait for interaction
            await view.wait()
            
            # Delete confirmation message
            await confirmation_message.delete()

            # Always remove the pending purchase entry regardless of the outcome
            delete_pending_query = f"""
            DELETE FROM shop_status 
            WHERE user_id = %s
            """
            await execute_query_with_retry(delete_pending_query, (user_id,))
            # Always remove the pending purchase entry regardless of the outcome

            if view.value is None:
                await ctx.send("Purchase timed out.")
                return
            elif not view.value:
                await ctx.send("Purchase cancelled.")
                return

            won_result = await execute_query_with_retry(
                "SELECT won_currency FROM user WHERE user_id = %s", 
                (user_id,))
            user_currency = won_result[0]['won_currency']

            # Process the purchase
            new_currency = user_currency - final_price
            await execute_query_with_retry("UPDATE user SET won_currency = %s WHERE user_id = %s", (new_currency, user_id))

            # Update inventory
            inventory_result = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s", 
                (user_id,))
                
            if not inventory_result:
                await ctx.send("Error: You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']
            inventory_table = f"inventory_{item_type}"
            item_id_column = f"{item_type}_id" if item_type != 'pickaxe' else 'id_pickaxe'
            print("item :")
            print(item_id_column)
            existing_item = await execute_query_with_retry(
                f"SELECT * FROM {inventory_table} WHERE inventory_id = %s AND {item_id_column} = %s",
                (inventory_id, item_details[item_id_column]))

            if existing_item:
                new_quantity = existing_item[0]['quantiter'] + quantity
                await execute_query_with_retry(
                    f"UPDATE {inventory_table} SET quantiter = %s WHERE inventory_id = %s AND {item_id_column} = %s",
                    (new_quantity, inventory_id, item_details[item_id_column]))
            else:
                await execute_query_with_retry(
                    f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantiter) VALUES (%s, %s, %s)",
                    (inventory_id, item_details[item_id_column], quantity))

            # Create success embed
            success_embed = discord.Embed(title="🎉 Purchase Successful!", color=discord.Color.green())
            success_embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            success_embed.add_field(name="Item", value=item_name, inline=True)
            success_embed.add_field(name="Quantity", value=str(quantity), inline=True)
            
            if item_type == 'weapon' and premium_level > 0 and base_price != item_details['price']:
                success_embed.add_field(
                    name="Total Price", 
                    value=f"~~{item_details['price'] * quantity:,}~~ {final_price:,} won (Premium Discount Applied)", 
                    inline=True
                )
            else:
                success_embed.add_field(name="Total Price", value=f"{final_price:,} won", inline=True)
                
            success_embed.add_field(name="New Balance", value=f"{new_currency:,} won", inline=True)
            
            if premium_level > 0:
                premium_tiers = {1: "National Ranker", 2: "Ruler", 3: "Monarch"}
                success_embed.set_footer(text=f"{premium_tiers[premium_level]} Premium Member | Thank you for your purchase!")
            else:
                success_embed.set_footer(text="Thank you for your purchase!")

            # Send success message
            if view.interaction:
                await view.interaction.response.send_message(embed=success_embed)
            else:
                await ctx.send(embed=success_embed)

            # Update quest progress
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
                    inventory_result = await execute_query_with_retry(
                        "SELECT inventory_id FROM inventory WHERE user_id = %s", 
                        (user_id,))
                        
                    if inventory_result:
                        inventory_id = inventory_result[0]['inventory_id']
                        existing_chest = await execute_query_with_retry(
                            "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = 1", 
                            (inventory_id,))
                        
                        if existing_chest:
                            await execute_query_with_retry(
                                "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = 1", 
                                (inventory_id,))
                        else:
                            await execute_query_with_retry(
                                "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, 1, 1)", 
                                (inventory_id,))

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @commands.command(name="gbuy", aliases=["gby", "gbu"])
    async def gbuy(self, ctx, *, args):
        if not args:
            await ctx.send("Usage: sl gbuy <item_name> <quantity>")
            return
        
        args = args.split()
        item_name = " ".join(args) if len(args) == 1 else " ".join(args[:-1])
        
        try:
            quantity = 1 if len(args) == 1 else int(args[-1])
            if quantity <= 0:   
                await ctx.send("Quantity must be a positive number.")
                return
        except ValueError:
            # If the last argument can't be converted to a number, treat it as part of the item name
            item_name = " ".join(args)
            quantity = 1

        try:
            # Get user details and premium status
            user_result = await execute_query_with_retry(
                "SELECT user_id, gamble_currency FROM user WHERE user_authorid = %s", 
                (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("Error: You need to register to the bot first.")
                return
                
            user_id = user_result[0]['user_id']
            user_currency = user_result[0]['gamble_currency']
            gamble_query = """
            SELECT * from gamble_status WHERE user_id = %s
            """
            gamble_result = await execute_query_with_retry(gamble_query, (user_id,))
            if gamble_result:
                await ctx.send("⏰ You are already in a gambling session. Please wait until it completes.")
                return
            # Check if user is in a trade
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return
        # Check premium status
            premium_level = await self.check_premium_status(user_id)

            # Check if item exists and get its details
            item_details = None
            item_type = None
            weapon_rank_in_class = None

            # Modified weapon query to include proper rank calculation
            weapon_query = """
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
            SELECT *
            FROM WeaponRanks
            WHERE weapon_name = %s
            """

            # Try to find the item in different tables
            weapon_result = await execute_query_with_retry(weapon_query, (item_name,))
            if weapon_result:
                item_details = weapon_result[0]
                item_type = 'weapon'
                weapon_rank_in_class = item_details['rank_in_class']
            else:
                # Check other item types
                for table, name_column, item_typed in [
                    ('dungeon_key', 'dungeonkey_name', 'dungeonkey'),
                    ('product', 'product_name', 'product'),
                    ('pickaxe', 'pickaxe_name', 'pickaxe')
                ]:
                    item_result = await execute_query_with_retry(f"SELECT * FROM {table} WHERE {name_column} = %s", (item_name,))
                    if item_result:
                        item_details = item_result[0]
                        item_type = item_typed
                        break
            
            if not item_details:
                await ctx.send("Error: The specified item does not exist.")
                return

            # Check if the item can be purchased with gwon
            if 'gprice' not in item_details or item_details['gprice'] == 0:
                await ctx.send("This item cannot be purchased with gwon.")
                return

            # Calculate final price based on item type
            base_price = item_details['gprice']
            final_price = base_price * quantity

            if user_currency < final_price:
                await ctx.send(f"You don't have enough gwon. You need {final_price:,} gwon but only have {user_currency:,} gwon.")
                return

            # Check for pending purchase in the shop_status table
            item_id_column = f"{item_type}_id"
            if item_type == 'pickaxe':
                item_id_column = 'id_pickaxe'
            
            item_id = item_details[item_id_column]

            # Query to check for existing pending purchase
            pending_check_query = f"""
            SELECT id FROM shop_status 
            WHERE user_id = %s
            """
            
            pending_result = await execute_query_with_retry(pending_check_query, (user_id,))
            
            if pending_result:
                # User already has a pending purchase for this item
                await ctx.send("⚠️ You already have a pending purchase. Please complete or cancel your previous purchase before buying this item again.")
                return

            # Insert new pending purchase entry
            pending_columns = ["user_id"]
            pending_values = [user_id]
            
            # Only add the relevant item ID column based on the item type
            pending_columns.append(item_id_column)
            pending_values.append(item_id)
            
            # Create the placeholders for the SQL query
            placeholders = ', '.join(['%s'] * len(pending_columns))
            columns_str = ', '.join(pending_columns)
            
            insert_pending_query = f"""
            INSERT INTO shop_status ({columns_str})
            VALUES ({placeholders})
            """
            
            await execute_query_with_retry(insert_pending_query, pending_values)
            await execute_query_with_retry("INSERT INTO gamble_status(user_id) values(%s)",(user_id,))
            # Create confirmation embed
            embed = discord.Embed(title="🛒 Purchase Confirmation (Gwon)", color=discord.Color.gold())
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            embed.add_field(name="Item", value=f"**{item_name}**", inline=False)
            embed.add_field(name="Quantity", value=f"**{quantity}**", inline=True)
            
            # Add class information for weapons
            if item_type == 'weapon':
                embed.add_field(name="Class", value=f"**{item_details['class_names']}**", inline=True)
            
            # Price field
            embed.add_field(name="Price per Item", value=f"**{base_price:,} <:gamble:1314341368176574564>**", inline=True)
            embed.add_field(name="Total Price", value=f"**{final_price:,} <:gamble:1314341368176574564>**", inline=True)
            embed.add_field(name="Your Balance", value=f"**{user_currency:,} <:gamble:1314341368176574564>**", inline=True)
            embed.add_field(name="Remaining Balance", value=f"**{user_currency - final_price:,} <:gamble:1314341368176574564>**", inline=True)
            
            # Add item-specific details
            if item_type == 'weapon':
                stats = []
                if item_details['attack_boost'] > 0:
                    stats.append(f"Attack +{item_details['attack_boost']}")
                if item_details['health_boost'] > 0:
                    stats.append(f"Health +{item_details['health_boost']}")
                if item_details['speed_boost'] > 0:
                    stats.append(f"Speed +{item_details['speed_boost']}")
                if item_details['defense_boost'] > 0:
                    stats.append(f"Power +{item_details['defense_boost']}")
                embed.add_field(name="Stats", value="**" + " | ".join(stats) + "**" if stats else "No stat boosts", inline=False)
            elif item_type == 'product':
                if item_details['xp_boost'] > 0:
                    embed.add_field(name="XP Boost", value=f"**+{item_details['xp_boost']}**", inline=True)
                embed.add_field(name="Description", value=f"*{item_details['product_description']}*", inline=False)
            elif item_type == 'dungeon_key':
                embed.add_field(name="Dungeon Rank", value=f"**{item_details['dungeonkey_name']}**", inline=True)
            elif item_type == 'pickaxe':
                embed.add_field(
                    name="Pickaxe Stats", 
                    value=f"**Min Value: {item_details['min_value']} <:won:1304034228966658048>\nMax Value: {item_details['max_value']} <:won:1304034228966658048>**", 
                    inline=False
                )

            embed.set_footer(text="Please confirm your purchase within 60 seconds.")

            # Create and send confirmation view
            view = EnhancedConfirmView(user_id)
            view.original_user = ctx.author
            view.ctx = ctx
            view.cog = self
            view.final_price = final_price  # Pass the final price to the view
            view.is_gamble = True  # Flag this as a gamble purchase
            confirmation_message = await ctx.send(embed=embed, view=view)
            
            # Wait for interaction
            await view.wait()
            
            # Delete confirmation message
            await confirmation_message.delete()

            # Always remove the pending purchase entry regardless of the outcome
            delete_pending_query = f"""
            DELETE FROM shop_status 
            WHERE user_id = %s
            """
            await execute_query_with_retry(delete_pending_query, (user_id,))
            delete_gamble_query = f"""
            DELETE FROM gamble_status 
            WHERE user_id = %s
            """
            await execute_query_with_retry(delete_gamble_query, (user_id,))
            if view.value is None:
                await ctx.send("Purchase timed out.")
                return
            elif not view.value:
                await ctx.send("Purchase cancelled.")
                return
            gwon_result = await execute_query_with_retry(
                "SELECT gamble_currency FROM user WHERE user_id = %s", 
                (user_id,))
            user_currency = gwon_result[0]['gamble_currency']            # Process the purchase
            new_currency = user_currency - final_price
            await execute_query_with_retry("UPDATE user SET gamble_currency = %s WHERE user_id = %s", (new_currency, user_id))

            # Update inventory
            inventory_result = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s", 
                (user_id,))
                
            if not inventory_result:
                await ctx.send("Error: You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']
            inventory_table = f"inventory_{item_type}"
            item_id_column = f"{item_type}_id" if item_type != 'pickaxe' else 'id_pickaxe'
            
            existing_item = await execute_query_with_retry(
                f"SELECT * FROM {inventory_table} WHERE inventory_id = %s AND {item_id_column} = %s",
                (inventory_id, item_details[item_id_column]))

            if existing_item:
                new_quantity = existing_item[0]['quantiter'] + quantity
                await execute_query_with_retry(
                    f"UPDATE {inventory_table} SET quantiter = %s WHERE inventory_id = %s AND {item_id_column} = %s",
                    (new_quantity, inventory_id, item_details[item_id_column]))
            else:
                await execute_query_with_retry(
                    f"INSERT INTO {inventory_table} (inventory_id, {item_id_column}, quantiter) VALUES (%s, %s, %s)",
                    (inventory_id, item_details[item_id_column], quantity))

            # Create success embed
            success_embed = discord.Embed(title="🎉 Purchase Successful! (Gwon)", color=discord.Color.green())
            success_embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            success_embed.add_field(name="Item", value=item_name, inline=True)
            success_embed.add_field(name="Quantity", value=str(quantity), inline=True)
            success_embed.add_field(name="Total Price", value=f"{final_price:,} <:gamble:1314341368176574564>", inline=True)
            success_embed.add_field(name="New Balance", value=f"{new_currency:,} <:gamble:1314341368176574564>", inline=True)
            success_embed.set_footer(text="Thank you for your purchase!")

            # Send success message
            if view.interaction:
                await view.interaction.response.send_message(embed=success_embed)
            else:
                await ctx.send(embed=success_embed)

            # Update quest progress
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
                    inventory_result = await execute_query_with_retry(
                        "SELECT inventory_id FROM inventory WHERE user_id = %s", 
                        (user_id,))
                        
                    if inventory_result:
                        inventory_id = inventory_result[0]['inventory_id']
                        existing_chest = await execute_query_with_retry(
                            "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = 1", 
                            (inventory_id,))
                        
                        if existing_chest:
                            await execute_query_with_retry(
                                "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = 1", 
                                (inventory_id,))
                        else:
                            await execute_query_with_retry(
                                "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, 1, 1)", 
                                (inventory_id,))

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @commands.command(name="shop", aliases=["sh", "sp"])
    async def shop(self, ctx):
        try:
            user_id = ctx.author.id
            
            try:
                query = "SELECT * FROM user WHERE user_authorid = %s"
                user_result = await execute_query_with_retry(query, (str(user_id),))

                if not user_result:
                    await ctx.send("Error: You need to register to the bot first.")
                    return

                premium_level = await self.check_premium_status(user_result[0]['user_id'])

                view = ShopCategoryView(self, ctx.author)  # Pass the original user
                embed = discord.Embed(
                    title="🏯 Hunter's Association Shop",
                    description="Welcome, brave hunter! What would you like to purchase today?",
                    color=discord.Color.dark_purple()
                )
                embed.set_image(url=MAIN_SHOP_IMAGE)
                embed.add_field(name="⚔️ Weapons", value="Powerful weapons to slay monsters", inline=False)
                embed.add_field(name="🔮 Boosters", value="Magical items to enhance your abilities", inline=False)
                embed.add_field(name="🗝️ Dungeon Keys", value="Keys to access special dungeons", inline=False)
                embed.add_field(name="⛏️ Pickaxes", value="Tools to enhance your mining capabilities", inline=False)
                
                footer_text = "Choose a category to browse. Your equipment may determine your fate in the dungeons!"
                if premium_level > 0:
                    premium_tiers = {1: "National Ranker", 2: "Ruler", 3: "Monarch"}
                    footer_text = f"{premium_tiers[premium_level]} Premium Member | {footer_text}"
                
                embed.set_footer(text=footer_text)
                message = await ctx.send(embed=embed, view=view)
                self.last_shop_message = message.id
                
            except Exception as e:
                await ctx.send(f"An error occurred while displaying the shop: {str(e)}")
                print(f"Shop command error: {str(e)}")
                
        except Exception as e:
            await ctx.send(f"A critical error occurred: {str(e)}")
            print(f"Critical shop command error: {str(e)}")

    async def show_category(self, interaction: discord.Interaction, category: ItemType):
        try:
            user_result = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s", 
                (str(interaction.user.id),))
                
            premium_level = 0
            if user_result:
                premium_level = await self.check_premium_status(user_result[0]['user_id'])
                
            items = await self.get_category_items(category)

            if not items:
                await interaction.response.send_message("No items found in this category.", ephemeral=True)
                return

            view = ShopPaginationView(self, category, interaction.user)
            view.items = items  # Store items directly in the view
            
            total_items = len(items)
            await view.update_button_states(total_items)

            embed = self.create_category_embed(category, items, 1, premium_level)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"Show category error: {e}")
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    async def update_category_page(self, interaction: discord.Interaction, category: ItemType, page: int):
        try:
            user_result = await execute_query_with_retry(
                "SELECT user_id FROM user WHERE user_authorid = %s", 
                (str(interaction.user.id),))
                
            premium_level = 0
            if user_result:
                premium_level = await self.check_premium_status(user_result[0]['user_id'])
                
            items = await self.get_category_items(category)

            embed = self.create_category_embed(category, items, page, premium_level)
            view = ShopPaginationView(self, category, interaction.user)
            view.current_page = page
            view.items = items  # Store items directly in the view

            total_items = len(items)
            await view.update_button_states(total_items)

            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"Update category page error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    def format_item(self, category: ItemType, item: Dict[str, Union[int, str]]) -> Tuple[str, str]:
        if category == ItemType.PICKAXE:
            icon_prefix = item.get('pickaxe_icon', '') if item.get('pickaxe_icon') else ''
            name = f"{icon_prefix}| {item['pickaxe_name']} | Price: {item['price']} 💰"
            value = f"```{item['pickaxe_description']}```"
                        # Add gprice if it exists
            if 'gprice' in item and item['gprice'] > 0:
                name += f" | {item['gprice']} <:gamble:1314341368176574564>"
            return name, value        
        if category == ItemType.WEAPON:
            icon_prefix = item.get('weapon_icon', '') if item.get('weapon_icon') else ''
            name = f"{icon_prefix}| {item['weapon_name']}"
            value = f"Price: {item['price']} 💰"
            
            # Add gprice if it exists
            if 'gprice' in item and item['gprice'] > 0:
                value += f" | {item['gprice']} <:gamble:1314341368176574564>"
                
            value += f" | Class: {item['card_class_name']}\n"
            stats = []
            if item['attack_boost'] > 0:
                stats.append(f"Attack+{item['attack_boost']}")
            if item['health_boost'] > 0:
                stats.append(f"Health+{item['health_boost']}")
            if item['speed_boost'] > 0:
                stats.append(f"Speed+{item['speed_boost']}")
            if item['defense_boost'] > 0:
                stats.append(f"Power+{item['defense_boost']}")
            value += " | ".join(stats)
        elif category == ItemType.BOOSTER:
            icon_prefix = item.get('product_icon', '') if item.get('product_icon') else ''
            name = f"{icon_prefix}| {item['product_name']} | Price: {item['price']} 💰"
            
            # Add gprice if it exists
            if 'gprice' in item and item['gprice'] > 0:
                name += f" | {item['gprice']} <:gamble:1314341368176574564>"
                
            value = f"```{item['product_description']}```"
        else:  # ItemType.KEY
            icon_prefix = item.get('dungeonkey_icon', '') if item.get('dungeonkey_icon') else ''
            name = f"{icon_prefix}| {item['dungeonkey_name']} | Price: {item['price']} 💰"
            
            # Add gprice if it exists
            if 'gprice' in item and item['gprice'] > 0:
                name += f" | {item['gprice']} <:gamble:1314341368176574564>"
                
            value = f"```{item['dungeonkey_description']}```"
        
        return name, value

async def setup(bot):
    await bot.add_cog(ShopCommands(bot))