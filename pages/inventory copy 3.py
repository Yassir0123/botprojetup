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
import functools
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
# =========================================================
# Localization helper
# EN = 0 (default), AR = 1, FR = 3
# =========================================================
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    print(lang)
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

# Create a command cooldown decorator
def command_cooldown(rate=1, per=3.0, type=commands.BucketType.user):
    """
    A robust cooldown decorator that prevents command spam
    """
    cooldown = commands.Cooldown(rate, per)
    cooldowns = {}
   
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            # Get the bucket key based on the cooldown type
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id
               
            # Check if the command is on cooldown
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    # Command is on cooldown
                    remaining = round(retry_after, 1)
                    lang = await get_user_multilanguage(ctx.author.id)
                    msg = _tr(
                        lang,
                        "⏰ Command on cooldown. Please wait {remaining} seconds.",
                        "⏰ الأمر قيد الانتظار. الرجاء الانتظار {remaining} ثانية.",
                        "⏰ Commande en recharge. Veuillez patienter {remaining} secondes."
                    ).format(remaining=remaining)
                    await ctx.send(msg)
                    return
            else:
                # Create a new cooldown bucket
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
               
            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator


class InventoryCommands(commands.Cog):
    def __init__(self, bot, lang: int = 0):
        self.bot = bot
        self.lang = lang
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.user_cooldowns = {}  # Store user cooldowns
        self.COOLDOWN_TIME = 2  # Cooldown time in seconds
   
    def check_cooldown(self, user_id, command_name):
        """
        Check if a user is on cooldown for a specific command
        Returns (bool, float): (is_on_cooldown, time_remaining)
        """
        current_time = time.time()
        cooldown_key = f"{user_id}_{command_name}"
       
        if cooldown_key in self.user_cooldowns:
            last_use = self.user_cooldowns[cooldown_key]
            time_passed = current_time - last_use
           
            if time_passed < self.COOLDOWN_TIME:
                return True, round(self.COOLDOWN_TIME - time_passed, 1)
       
        self.user_cooldowns[cooldown_key] = current_time
        return False, 0

    def parse_input(self, input_string):
        pattern = r'\$\$(.*?)\$\$'
        matches = re.findall(pattern, input_string)
        return matches

    def calculate_money_reward(self, money_cap, quantity, case):
        try:
            if case == 0:
                if quantity <= 3:
                    return random.randint(100, money_cap)
                else:
                    adjusted_cap = math.floor(money_cap * (quantity / 3))
                    return random.randint(100, adjusted_cap)
            else:
                if quantity <= 3:
                    return random.randint(50, money_cap)
                else:
                    adjusted_cap = math.floor(money_cap * (quantity / 3))
                    return random.randint(50, adjusted_cap)
        except Exception as e:
            print(f"Error calculating money reward: {e}")
            return 0

    async def update_user_currency(self, user_id, money_reward, gamble_reward):
        try:
            # Use a single query to update both currencies at once
            update_query = """
            UPDATE user
            SET won_currency = won_currency + %s, gamble_currency = gamble_currency + %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_query, (money_reward, gamble_reward, user_id))
        except Exception as e:
            print(f"Error updating user currency: {e}")

    async def get_available_items(self, chest_id):
        """Get both products and dungeon keys available for a specific chest with their icons and rarity handling."""
        try:
            # Get products with icons
            products_query = """
                SELECT 'product' as item_type, p.product_id as item_id,
                       p.product_name as item_name, p.product_icon as item_icon
                FROM chest_product cp
                JOIN product p ON cp.product_id = p.product_id
                WHERE cp.chest_id = %s
            """
            products = await execute_query_with_retry(products_query, (chest_id,))
           
            # Get dungeon keys based on chest type with rarity
            dungeonkeys = []
           
            if chest_id == 1:  # Shadow chest - E-rank and D-rank gates (common)
                dungeonkey_query = """
                    SELECT 'dungeonkey' as item_type, dk.dungeonkey_id as item_id,
                           dk.dungeonkey_name as item_name, dk.dungeonkey_icon as item_icon,
                           'common' as rarity
                    FROM dungeon_key dk
                    WHERE dk.dungeonkey_id IN (1, 6)  -- E-rank and D-rank gates
                """
                dungeonkeys = await execute_query_with_retry(dungeonkey_query)
               
            elif chest_id == 2:  # Hunter chest - B-rank and C-rank gates (rare)
                dungeonkey_query = """
                    SELECT 'dungeonkey' as item_type, dk.dungeonkey_id as item_id,
                           dk.dungeonkey_name as item_name, dk.dungeonkey_icon as item_icon,
                           'rare' as rarity
                    FROM dungeon_key dk
                    WHERE dk.dungeonkey_id IN (4, 2)  -- B-rank and C-rank gates
                """
                dungeonkeys = await execute_query_with_retry(dungeonkey_query)
               
            elif chest_id == 3:  # Monarch chest - A-rank and S-rank gates (very rare)
                dungeonkey_query = """
                    SELECT 'dungeonkey' as item_type, dk.dungeonkey_id as item_id,
                           dk.dungeonkey_name as item_name, dk.dungeonkey_icon as item_icon,
                           'very_rare' as rarity
                    FROM dungeon_key dk
                    WHERE dk.dungeonkey_id IN (5, 3)  -- A-rank and S-rank gates
                """
                dungeonkeys = await execute_query_with_retry(dungeonkey_query)
           
            return (products or []) + (dungeonkeys or [])
        except Exception as e:
            print(f"Error getting available items: {e}")
            return []

    def should_include_item(self, item, chest_id):
        """Determine if an item should be included based on rarity and chest type."""
        if item['item_type'] != 'dungeonkey':
            return True  # Always include products
       
        # Handle dungeon key rarity
        rarity = item.get('rarity', 'common')
        _ = rarity  # not used further but kept for clarity
       
        if chest_id == 1:  # Shadow chest
            # E-rank and D-rank gates - 25% chance each
            return random.random() < 0.25
        elif chest_id == 2:  # Hunter chest  
            # B-rank and C-rank gates - 15% chance each (rare)
            return random.random() < 0.15
        elif chest_id == 3:  # Monarch chest
            # A-rank and S-rank gates - 8% chance each (very rare)
            # Dungeon reset (id=14) - 5% chance (even rarer)
            if item['item_id'] == 14:  # dungeon reset
                return random.random() < 0.05
            else:
                return random.random() < 0.08
       
        return False

    def get_quantity_range(self, item_type, item_id, chest_id, quantity_multiplier):
        # Base ranges for different chest types
        ranges = {
            # Format: (shadow_min, shadow_max, hunter_min, hunter_max, monarch_min, monarch_max)
            'boost': (5, 10, 10, 15, 15, 20),
            'return_crystal': (5, 10, 10, 15, 15, 20),
            'consumables': (1, 2, 1, 3, 1, 4),
            'resets': (1, 3, 1, 3, 1, 3),
            'dungeonkey': (1, 2, 1, 2, 1, 2)  # Limited range for dungeon keys
        }
       
        # Determine item type
        if item_type == 'dungeonkey':
            base_range = ranges['dungeonkey']
        elif item_id in [1, 13]:  # boost and return crystal
            base_range = ranges['boost']
        elif item_id in [14, 15, 16, 17, 18]:  # resets
            base_range = ranges['resets']
        else:  # consumables
            base_range = ranges['consumables']
       
        # Get range based on chest type
        if chest_id == 1:  # Shadow
            min_val, max_val = base_range[0], base_range[1]
        elif chest_id == 2:  # Hunter
            min_val, max_val = base_range[2], base_range[3]
        else:  # Monarch
            min_val, max_val = base_range[4], base_range[5]
       
        # Adjust for quantity multiplier
        if quantity_multiplier > 0:
            if item_id in [1, 13]:  # Only boost and return crystal get +2
                min_val += quantity_multiplier * 2
                max_val += quantity_multiplier * 2
            else:  # Everything else (including dungeon keys) gets +1
                min_val += quantity_multiplier
                max_val += quantity_multiplier
       
        return min_val, max_val

    async def update_inventory_item(self, inventory_id, item_id, quantity, is_dungeon_key=False):
        try:
            if is_dungeon_key:
                table_name = "inventory_dungeonkey"
                id_column = "dungeonkey_id"
            else:
                table_name = "inventory_product"
                id_column = "product_id"
           
            # Check if item already exists
            check_query = f"SELECT quantiter FROM {table_name} WHERE inventory_id = %s AND {id_column} = %s"
            check_result = await execute_query_with_retry(check_query, (inventory_id, item_id))
           
            if check_result:
                # Update existing item
                new_quantity = check_result[0]['quantiter'] + quantity
                update_query = f"UPDATE {table_name} SET quantiter = %s WHERE inventory_id = %s AND {id_column} = %s"
                await execute_query_with_retry(update_query, (new_quantity, inventory_id, item_id))
            else:
                # Insert new item - handle potential duplicate key errors
                try:
                    insert_query = f"INSERT INTO {table_name} (inventory_id, {id_column}, quantiter) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))
                except Exception as insert_error:
                    if "Duplicate entry" in str(insert_error):
                        # If duplicate entry error, try to update instead (race condition)
                        update_query = f"UPDATE {table_name} SET quantiter = quantiter + %s WHERE inventory_id = %s AND {id_column} = %s"
                        await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
                    else:
                        print(f"Insert error for {table_name}: {insert_error}")
                        raise insert_error
                       
        except Exception as e:
            print(f"Error updating inventory item: {e}")
            raise e

    async def get_inventory_id(self, user_id):
        """Retrieve the inventory_id for a given user."""
        try:
            query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            result = await execute_query_with_retry(query, (user_id,))
            return result[0]['inventory_id'] if result else None
        except Exception as e:
            print(f"Error getting inventory ID: {e}")
            return None

    async def update_inventory_chest(self, inventory_id):
        """
        Update or insert a row in inventory_chest for Shadow Chest (chest_id=1)
        If a row exists, increment quantiter by 1
        If no row exists, insert a new row with quantiter = 1
        """
        try:
            # First, check if a row exists for this inventory_id and chest_id
            check_query = """
            SELECT quantiter FROM inventory_chest
            WHERE inventory_id = %s AND chest_id = 1
            """
            check_result = await execute_query_with_retry(check_query, (inventory_id,))
           
            if check_result:
                # Update existing row, increment quantiter
                update_query = """
                UPDATE inventory_chest
                SET quantiter = quantiter + 1
                WHERE inventory_id = %s AND chest_id = 1
                """
                await execute_query_with_retry(update_query, (inventory_id,))
            else:
                # Insert new row
                insert_query = """
                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                VALUES (%s, 1, 1)
                """
                await execute_query_with_retry(insert_query, (inventory_id,))
        except Exception as e:
            print(f"Error updating inventory chest: {e}")

    async def check_premium_status(self, user_id):
        """Check and update premium status if expired."""
        try:
            # Get user's premium info
            query = """
            SELECT prenium, prenium_date
            FROM user
            WHERE user_id = %s
            """
            result = await execute_query_with_retry(query, (user_id,))
           
            if not result:
                return 0
               
            user_data = result[0]
            premium = user_data['prenium']
            premium_date = user_data['prenium_date']
           
            # If no premium, return early
            if premium == 0:
                return 0
               
            # If premium date exists, check if expired
            if premium_date:
                current_date = datetime.now()
                days_difference = (current_date - premium_date).days
               
                # If premium expired (30 days passed)
                if days_difference >= 30:
                    # Reset premium status
                    update_query = """
                    UPDATE user
                    SET prenium = 0, prenium_date = NULL
                    WHERE user_id = %s
                    """
                    await execute_query_with_retry(update_query, (user_id,))
                    return 0
                   
            return premium
                   
        except Exception as e:
            print(f"Error checking premium status: {e}")
            return 0

    def get_premium_tier_name(self, tier):
        """Get the premium tier name."""
        tier_names = {
            1: "National Ranker",
            2: "Ruler",
            3: "Monarch"
        }
        return tier_names.get(tier, "Unknown")

    @commands.command(name="loot", aliases=["lt", "lo"])
    async def loot(self, ctx, *args):
        try:
            lang = await get_user_multilanguage(ctx.author.id)
            # Handle default case (no args)
            if not args:
                await ctx.send(_tr(
                    lang,
                    "Usage: sl loot <rarity> <amount>",
                    "طريقة الاستخدام: sl loot <الندرة> <الكمية>",
                    "Utilisation : sl loot <rareté> <quantité>"
                ))
                return

            # Parse chest name with flexible matching
            chest_name = ' '.join(args[:-1]) if args[-1].isdigit() or args[-1].lower() == 'all' else ' '.join(args)
           
            # Determine quantity
            quantity = 1  # Default quantity
            if args and (args[-1].isdigit() or args[-1].lower() == 'all'):
                if args[-1].lower() == 'all':
                    quantity = -1  # Special flag for 'all'
                else:
                    try:
                        quantity = int(args[-1])
                        if quantity <= 0:
                            await ctx.send(_tr(
                                lang,
                                "Invalid quantity. Please provide a number or 'all'.",
                                "كمية غير صالحة. يرجى إدخال رقم أو 'all'.",
                                "Quantité invalide. Veuillez fournir un nombre ou 'all'."
                            ))
                            return
                    except ValueError:
                        await ctx.send(_tr(
                            lang,
                            "Invalid quantity. Please provide a positive number or 'all'.",
                            "كمية غير صالحة. يرجى إدخال رقم موجب أو 'all'.",
                            "Quantité invalide. Indiquez un nombre positif ou 'all'."
                        ))
                        return

            # Get user data
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
           
            if not user_result:
                await ctx.send(_tr(lang, "User not registered.", "المستخدم غير مسجل.", "Utilisateur non enregistré."))
                return
               
            user_id = user_result[0]['user_id']

            # Check premium status
            premium_tier = await self.check_premium_status(user_id)

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
           
            if not inventory_result:
                await ctx.send(_tr(lang, "Inventory not found.", "لم يتم العثور على الجرد.", "Inventaire introuvable."))
                return
               
            inventory_id = inventory_result[0]['inventory_id']

            # First, find matching chests in the system
            chest_search_query = """
                SELECT chest_id, chest_name, chest_rank, money_cap, chest_icon
                FROM chest
                WHERE chest_name LIKE %s
            """
            matching_chests = await execute_query_with_retry(chest_search_query, (f"%{chest_name}%",))
           
            if not matching_chests:
                await ctx.send(_tr(
                    lang,
                    "No chest or bag found matching this name.",
                    "لا توجد صناديق/حقائب تطابق هذا الاسم.",
                    "Aucun coffre/sac correspondant à ce nom."
                ))
                return
               
            # Now check if the user has any of these matching chests in their inventory
            chest_query = """
                SELECT c.chest_id, c.chest_name, c.chest_rank, c.money_cap, c.chest_icon, COALESCE(ic.quantiter, 0) as quantiter
                FROM chest c
                LEFT JOIN inventory_chest ic ON c.chest_id = ic.chest_id AND ic.inventory_id = %s
                WHERE c.chest_name LIKE %s
            """
            chest_result = await execute_query_with_retry(chest_query, (inventory_id, f"%{chest_name}%"))
           
            if not chest_result:
                await ctx.send(_tr(
                    lang,
                    "No chest or bag found matching this name.",
                    "لا توجد صناديق/حقائب تطابق هذا الاسم.",
                    "Aucun coffre/sac correspondant à ce nom."
                ))
                return

            # Find a chest the user actually has (quantiter > 0)
            chest_data = None
            for chest in chest_result:
                if chest['quantiter'] > 0:
                    chest_data = chest
                    break
                   
            if not chest_data:
                await ctx.send(_tr(
                    lang,
                    f"You don't have any {chest_name} in your inventory.",
                    f"لا تملك أي {chest_name} في جردك.",
                    f"Vous n'avez aucun {chest_name} dans votre inventaire."
                ))
                return
               
            chest_id = chest_data['chest_id']

            # Premium tier checks for bags (chest_id 4-6)
            if chest_id >= 4:
                required_tier = chest_id - 3  # 4->1, 5->2, 6->3
               
                if premium_tier == 0:
                    tier_name = self.get_premium_tier_name(required_tier)
                    await ctx.send(_tr(
                        lang,
                        f"❌ This bag requires {tier_name} premium tier to loot!",
                        f"❌ هذه الحقيبة تتطلب فئة بريميوم {tier_name} للنهب!",
                        f"❌ Ce sac nécessite l'abonnement {tier_name} pour être ouvert !"
                    ))
                    return
                   
                # Check if user has sufficient premium tier
                if premium_tier < required_tier:
                    tier_name = self.get_premium_tier_name(required_tier)
                    await ctx.send(_tr(
                        lang,
                        f"❌ This bag requires {tier_name} premium tier to loot! Your current tier is insufficient.",
                        f"❌ هذه الحقيبة تتطلب فئة بريميوم {tier_name} للنهب! مستواك الحالي غير كافٍ.",
                        f"❌ Ce sac nécessite l'abonnement {tier_name} pour être ouvert ! Votre niveau actuel est insuffisant."
                    ))
                    return

            available_quantity = chest_data['quantiter'] or 0
           
            # Handle 'all' quantity
            if quantity == -1:
                quantity = available_quantity

            # Verify user has enough chests
            if quantity > available_quantity:
                await ctx.send(_tr(
                    lang,
                    "You don't have enough chests/bags.",
                    "ليس لديك ما يكفي من الصناديق/الحقائب.",
                    "Vous n'avez pas assez de coffres/sacs."
                ))
                return

            # Update chest quantity - FIXED: Check if record exists first
            check_query = "SELECT 1 FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            await execute_query_with_retry(check_query, (inventory_id, chest_id))
           
            new_chest_quantity = available_quantity - quantity
           
            if new_chest_quantity == 0:
                try:
                    delete_query = "DELETE FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
                    await execute_query_with_retry(delete_query, (inventory_id, chest_id))
                except Exception as delete_error:
                    print(f"Error deleting chest: {delete_error}")
            else:
                try:
                    # Try update first
                    update_query = "UPDATE inventory_chest SET quantiter = %s WHERE inventory_id = %s AND chest_id = %s"
                    result = await execute_query_with_retry(update_query, (new_chest_quantity, inventory_id, chest_id))
                   
                    # If no rows affected, insert new record
                    if result is None or (hasattr(result, 'rowcount') and result.rowcount == 0):
                        insert_query = "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, %s)"
                        await execute_query_with_retry(insert_query, (inventory_id, chest_id, new_chest_quantity))
                except Exception as chest_error:
                    print(f"Error updating chest quantity: {chest_error}")

            # Process rewards based on chest/bag type
            rewards = []
            if chest_id in [4, 5, 6]:  # Bags with fixed rewards
                fixed_rewards = self.get_fixed_bag_rewards(chest_id, quantity)  # Pass quantity here
                rewards = await self.process_fixed_rewards(inventory_id, fixed_rewards)
            else:  # Regular chests with random rewards
                # Get all available items
                available_items = await self.get_available_items(chest_id)
               
                if not available_items:
                    await ctx.send(_tr(
                        lang,
                        "No items available in this chest.",
                        "لا توجد عناصر متاحة في هذا الصندوق.",
                        "Aucun objet disponible dans ce coffre."
                    ))
                    return

                # Filter items based on rarity (for dungeon keys)
                filtered_items = []
                for item in available_items:
                    if self.should_include_item(item, chest_id):
                        filtered_items.append(item)
               
                # If no items passed the rarity filter, use regular products only
                if not filtered_items:
                    filtered_items = [item for item in available_items if item['item_type'] == 'product']
               
                # Determine item limit based on chest type
                item_limits = {1: 4, 2: 5, 3: 6}
                item_limit = item_limits.get(chest_id, 4)
               
                # Calculate quantity multiplier
                quantity_multiplier = (quantity - 1) // 4
               
                # Select random number of items
                num_items = min(random.randint(1, item_limit), len(filtered_items))
                selected_items = random.sample(filtered_items, num_items)
               
                # Process rewards
                for item in selected_items:
                    min_qty, max_qty = self.get_quantity_range(
                        item['item_type'],
                        item['item_id'],
                        chest_id,
                        quantity_multiplier
                    )
                    reward_quantity = random.randint(min_qty, max_qty)
                   
                    await self.update_inventory_item(
                        inventory_id,
                        item['item_id'],
                        reward_quantity,
                        is_dungeon_key=(item['item_type'] == 'dungeonkey')
                    )
                   
                    rewards.append({
                        'name': item['item_name'],
                        'quantity': reward_quantity,
                        'icon': item['item_icon']
                    })

            # Calculate and update currency reward
            money_reward = self.calculate_money_reward(chest_data['money_cap'], quantity, 0)
            gamble_reward = self.calculate_money_reward(chest_data['money_cap'], quantity, 1)
            await self.update_user_currency(user_id, money_reward, gamble_reward)

            # Get chest/bag image path
            chest_image = None
            if chest_id == 1:
                chest_image = os.path.join(self.image_path, 'shadow.png')
            elif chest_id == 2:
                chest_image = os.path.join(self.image_path, 'hunter.png')
            elif chest_id == 3:
                chest_image = os.path.join(self.image_path, 'mona.png')
            elif chest_id == 4:
                chest_image = os.path.join(self.image_path, 'bronze.png')
            elif chest_id == 5:
                chest_image = os.path.join(self.image_path, 'silver.png')
            elif chest_id == 6:
                chest_image = os.path.join(self.image_path, 'gold.png')

            # Create embed
            embed = discord.Embed(
                title=_tr(lang, "🎉 Loot Results", "🎉 نتائج الغنائم", "🎉 Résultats du butin"),
                description=_tr(
                    lang,
                    f"You looted {quantity}x {chest_data['chest_icon']} {chest_name}!",
                    f"لقد نهبت {quantity}× {chest_data['chest_icon']} {chest_name}!",
                    f"Vous avez ouvert {quantity}× {chest_data['chest_icon']} {chest_name} !"
                ),
                color=self.get_chest_color(chest_id)
            )

            # Set thumbnail
            file = None
            if chest_image and os.path.exists(chest_image):
                file = discord.File(chest_image, filename="chest_image.png")
                embed.set_thumbnail(url="attachment://chest_image.png")
           
            # Add rewards to embed
            rewards_text = ""
            for reward in rewards:
                rewards_text += f"{reward['icon']} `{reward['quantity']}x` {reward['name']}\n"
           
            if rewards_text:
                embed.add_field(
                    name=f"<:arrowpurple:1309253795909468160> " + _tr(lang, "Items Received", "العناصر المستلمة", "Objets reçus"),
                    value=rewards_text,
                    inline=False
                )

            # Add currency reward
            embed.add_field(
                name=f"<:arrowpurple:1309253795909468160> " + _tr(lang, "Currency Reward", "مكافأة العملات", "Récompense en devises"),
                value=f"<:won:1304034228966658048> `{money_reward:,}x` Won\n<:gamble:1314341368176574564> `{gamble_reward:,}x` GWon",
                inline=False
            )
            embed.set_footer(
                text=_tr(lang, f"{ctx.author.name}'s loot rewards", f"غنائم {ctx.author.name}", f"Récompenses de butin de {ctx.author.name}"),
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )

            # Send embed and update quest progress
            if file:
                await ctx.send(file=file, embed=embed)
            else:
                await ctx.send(embed=embed)
               
            try:
                # Keep quest name literal to match DB entry
                await self.update_quest_progress(ctx, user_id, "Open 5 chests", quantity)
            except Exception as e:
                print(f"Error in quest tracking: {e}")

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred while processing your request: {str(e)}",
                f"حدث خطأ أثناء معالجة طلبك: {str(e)}",
                f"Une erreur est survenue lors du traitement de votre demande : {str(e)}"
            ))

    def get_chest_color(self, chest_id):
        """Return embed color based on chest type."""
        colors = {
            1: 0x00FFFF,  # Shadow chest
            2: 0xFFD700,  # Hunter chest
            3: 0xB22222,  # Monarch chest
            4: 0x00FFFF,  # Bronze bag
            5: 0xFFD700,  # Silver bag
            6: 0xB22222   # Golden bag
        }
        return colors.get(chest_id, 0x7289DA)  # Default Discord blue if chest_id not found

    def get_fixed_bag_rewards(self, chest_id, quantity=1):
        """Return fixed rewards for bags."""
        capsule_ids = [4, 5, 6, 7]  # attack, speed, health, defense capsules
        booster_ids = [9, 10, 11, 12]  # attack, speed, defence, health boosters
        first_keys =[1,6]
        second_keys = [2,4]
        third_keys = [3,5]
        rewards = []
       
        # Calculate multiplier for champion/elite/master packs
        multiplier = quantity if chest_id in [4, 5, 6] else 1
       
        if chest_id == 4:  # Bronze bag (Champion pack)
            rewards.extend([
                {'type': 'product', 'id': random.choice(capsule_ids), 'quantity': 2 * multiplier},
                {'type': 'product', 'id': 17, 'quantity': 2 * multiplier},  # enhance
                {'type': 'product', 'id': 13, 'quantity': 5 * multiplier},  # return crystal
                {'type': 'product', 'id': 2, 'quantity': 1 * multiplier},   # roll reset
                {'type': 'product', 'id': 16, 'quantity': 1 * multiplier},  # fight reset
                {'type': 'product', 'id': 14, 'quantity': 1 * multiplier},  # dungeon reset
                {'type': 'product', 'id': 15, 'quantity': 1 * multiplier},  # mission reset
                {'type': 'product', 'id': 18, 'quantity': 1 * multiplier},  # squad reset
                {'type': 'dungeonkey', 'id': random.choice(first_keys), 'quantity': 2 * multiplier},  # easy gate
                {'type': 'product', 'id': 1, 'quantity': 10 * multiplier},  # xp boost
            ])
        elif chest_id == 5:  # Silver bag (Elite pack)
            rewards.extend([
                {'type': 'product', 'id': random.choice(capsule_ids), 'quantity': 2 * multiplier},
                {'type': 'product', 'id': 17, 'quantity': 2 * multiplier},  # enhance
                {'type': 'product', 'id': random.choice(booster_ids), 'quantity': 2 * multiplier},  # random booster
                {'type': 'product', 'id': 13, 'quantity': 10 * multiplier},  # return crystal
                {'type': 'product', 'id': 2, 'quantity': 2 * multiplier},   # roll reset
                {'type': 'product', 'id': 16, 'quantity': 2 * multiplier},  # fight reset
                {'type': 'product', 'id': 14, 'quantity': 2 * multiplier},  # dungeon reset
                {'type': 'product', 'id': 15, 'quantity': 2 * multiplier},  # mission reset
                {'type': 'product', 'id': 18, 'quantity': 2 * multiplier},  # squad reset
                {'type': 'product', 'id': 1, 'quantity': 20 * multiplier},  # xp boost
                {'type': 'dungeonkey', 'id': random.choice(first_keys), 'quantity': 2 * multiplier},  # easy gate
                {'type': 'dungeonkey', 'id': random.choice(second_keys), 'quantity': 2 * multiplier},  # medium gate
            ])
        elif chest_id == 6:  # Golden bag (Master pack)
            rewards.extend([
                {'type': 'product', 'id': random.choice(capsule_ids), 'quantity': 2 * multiplier},
                {'type': 'product', 'id': 17, 'quantity': 2 * multiplier},  # enhance
                {'type': 'product', 'id': random.choice(booster_ids), 'quantity': 2 * multiplier},  # random booster
                {'type': 'product', 'id': 13, 'quantity': 10 * multiplier},  # return crystal
                {'type': 'product', 'id': 2, 'quantity': 3 * multiplier},   # roll reset
                {'type': 'product', 'id': 16, 'quantity': 3 * multiplier},  # fight reset
                {'type': 'product', 'id': 14, 'quantity': 3 * multiplier},  # dungeon reset
                {'type': 'product', 'id': 15, 'quantity': 3 * multiplier},  # mission reset
                {'type': 'product', 'id': 18, 'quantity': 3 * multiplier},  # squad reset
                {'type': 'product', 'id': 1, 'quantity': 20 * multiplier},  # xp boost
                {'type': 'dungeonkey', 'id': random.choice(first_keys), 'quantity': 2 * multiplier},  # easy gate
                {'type': 'dungeonkey', 'id': random.choice(second_keys), 'quantity': 2 * multiplier},  # medium gate
                {'type': 'dungeonkey', 'id': random.choice(third_keys), 'quantity': 2 * multiplier},  # hard gate
            ])
       
        return rewards

    async def process_fixed_rewards(self, inventory_id, rewards):
        """Process fixed rewards for bags."""
        processed_rewards = []
       
        for reward in rewards:
            # Update inventory
            await self.update_inventory_item(
                inventory_id,
                reward['id'],
                reward['quantity'],
                is_dungeon_key=(reward['type'] == 'dungeonkey')
            )
           
            # Get item details for display
            if reward['type'] == 'product':
                query = "SELECT product_name as name, product_icon as icon FROM product WHERE product_id = %s"
            else:
                query = "SELECT dungeonkey_name as name, dungeonkey_icon as icon FROM dungeon_key WHERE dungeonkey_id = %s"
           
            item_result = await execute_query_with_retry(query, (reward['id'],))
            if item_result:
                processed_rewards.append({
                    'name': item_result[0]['name'],
                    'quantity': reward['quantity'],
                    'icon': item_result[0]['icon']
                })
       
        return processed_rewards

    async def update_quest_progress(self, ctx, user_id, quest_name, quantity):
        """Track progress for chest opening quests."""
        try:
            user_quests_query = """
            SELECT uq.*, q.quest_name, q.required_count
            FROM user_quests uq
            JOIN quest q ON uq.quest_id = q.quest_id
            WHERE uq.user_id = %s AND q.quest_name = %s
            """
            user_quests_result = await execute_query_with_retry(user_quests_query, (user_id, quest_name))

            if user_quests_result:
                quest = user_quests_result[0]
                if quest['completion_status'] == 0:
                    current_count = quest['current_count']
                    required_count = quest['required_count']
                   
                    # Calculate the increment, ensuring we don't exceed the required count
                    increment = min(quantity, required_count - current_count)
                    new_count = current_count + increment

                    # Update the current count
                    update_count_query = """
                    UPDATE user_quests
                    SET current_count = %s
                    WHERE userquests_id = %s
                    """
                    await execute_query_with_retry(update_count_query, (new_count, quest['userquests_id']))

                    # Check if quest is completed
                    if new_count >= required_count:
                        # Complete the quest
                        complete_quest_query = """
                        UPDATE user_quests
                        SET completion_status = 1, current_count = %s
                        WHERE userquests_id = %s
                        """
                        await execute_query_with_retry(complete_quest_query, (required_count, quest['userquests_id']))
                       
                        # Get inventory ID
                        inventory_id = await self.get_inventory_id(user_id)
                       
                        # If inventory_id exists, update inventory_chest
                        if inventory_id is not None:
                            await self.update_inventory_chest(inventory_id)

        except Exception as e:
            print(f"Error in quest tracking: {e}")
            raise e

    @commands.command(name='mine', aliases=['mn', 'mining'])
    async def mine_command(self, ctx):
        try:
            lang = await get_user_multilanguage(ctx.author.id)

            # Get user's Discord ID
            user_author_id = str(ctx.author.id)

            # Check if user exists in the database
            user_query = "SELECT * FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (user_author_id,))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to register first. Use `sl register` to get started.",
                    "يجب أن تسجل أولاً. استخدم `sl register` للبدء.",
                    "Vous devez d'abord vous inscrire. Utilisez `sl register` pour commencer."
                ))
                return
            user = user_result[0]
       
            # Check if pickaxe is equipped
            if user['pickaxe_id'] is None or user['pickaxe_id'] == 0:
                await ctx.send(_tr(
                    lang,
                    "You haven't equipped a pickaxe yet. Use `sl pickadd <pickaxe_name>` to equip one.",
                    "لم تقم بتجهيز معول بعد. استخدم `sl pickadd <pickaxe_name>` لتجهيزه.",
                    "Vous n'avez pas encore équipé de pioche. Utilisez `sl pickadd <pickaxe_name>` pour en équiper une."
                ))
                return
       
            # Check mining timer
            current_time = datetime.now()
            mine_timer = user['mine_timer']

            if mine_timer:
                time_diff = current_time - mine_timer
                if time_diff.total_seconds() < 4 * 3600:  # Less than 4 hours
                    remaining_time = timedelta(seconds=4 * 3600 - time_diff.total_seconds())
                    hours, remainder = divmod(remaining_time.seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    await ctx.send(_tr(
                        lang,
                        f"You can mine again in {hours}h {minutes}m {seconds}s.",
                        f"يمكنك التعدين مرة أخرى خلال {hours}س {minutes}د {seconds}ث.",
                        f"Vous pourrez miner à nouveau dans {hours}h {minutes}m {seconds}s."
                    ))
                    return

            # Get pickaxe details
            pickaxe_query = "SELECT * FROM pickaxe WHERE id_pickaxe = %s"
            pickaxe_result = await execute_query_with_retry(pickaxe_query, (user['pickaxe_id'],))

            if not pickaxe_result:
                await ctx.send(_tr(lang, "Error: Pickaxe not found.", "خطأ: لم يتم العثور على المعول.", "Erreur : pioche introuvable."))
                return

            pickaxe = pickaxe_result[0]

            # Generate mining amount
            mined_amount = random.randint(pickaxe['min_value'], pickaxe['max_value'])

            # Update user's won currency and mine timer
            update_query = """
            UPDATE user
            SET won_currency = won_currency + %s,
                mine_timer = %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_query, (mined_amount, current_time, user['user_id']))

            # Create embed with mining result
            embed = discord.Embed(
                title=_tr( lang, "Mining Results", "نتائج التعدين", "Résultats du minage"),
                color=discord.Color.green()
            )
            embed.description = _tr(
                lang,
                f"You used your **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']} to mine a cluster of mana crystals, yielding a total of **{mined_amount} <:won:1304034228966658048>**!",
                f"لقد استخدمت **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']} لاستخراج مجموعة من بلورات المانا، وحصلت على **{mined_amount} <:won:1304034228966658048>**!",
                f"Vous avez utilisé votre **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']} pour extraire un amas de cristaux de mana, obtenant **{mined_amount} <:won:1304034228966658048>** !"
            )
            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Mining error: {e}")
            await ctx.send(_tr(
                getattr(self, "lang", 0),
                "An error occurred during mining.",
                "حدث خطأ أثناء التعدين.",
                "Une erreur est survenue lors du minage."
            ))


    @commands.command(name="skip_dungeon", aliases=["sd","skd"])
    async def skip_dungeon(self, ctx):
        try:
            lang = await get_user_multilanguage(ctx.author.id)

            # Get user data
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
       
            if not user_result:
                await ctx.send(_tr(lang, "❌ User not registered.", "❌ المستخدم غير مسجل.", "❌ Utilisateur non enregistré."))
                return
           
            user_id = user_result[0]['user_id']
       
            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
       
            if not inventory_result:
                await ctx.send(_tr(lang, "❌ Inventory not found.", "❌ لم يتم العثور على الجرد.", "❌ Inventaire introuvable."))
                return
           
            inventory_id = inventory_result[0]['inventory_id']
       
            # Check if user has evasion pass (product_id = 19)
            evasion_pass_query = """
            SELECT quantiter FROM inventory_product
            WHERE inventory_id = %s AND product_id = 19
            """
            evasion_pass_result = await execute_query_with_retry(evasion_pass_query, (inventory_id,))
       
            if not evasion_pass_result or evasion_pass_result[0]['quantiter'] <= 0:
                await ctx.send(_tr(
                    lang,
                    "❌ You don't have any Evasion Pass in your inventory.",
                    "❌ ليس لديك أي Evasion Pass في جردك.",
                    "❌ Vous n'avez aucun Evasion Pass dans votre inventaire."
                ))
                return
       
            # Check current dungeon_skip status
            dungeon_status_query = "SELECT dungeon_skip FROM user WHERE user_id = %s"
            dungeon_status_result = await execute_query_with_retry(dungeon_status_query, (user_id,))
       
            if not dungeon_status_result:
                await ctx.send(_tr(
                    lang,
                    "❌ Unable to retrieve dungeon status.",
                    "❌ تعذر استرجاع حالة الدنجن.",
                    "❌ Impossible de récupérer l'état du donjon."
                ))
                return
       
            current_dungeon_skip = dungeon_status_result[0]['dungeon_skip']
       
            # Check if user already has an evasion pass active
            if current_dungeon_skip == 1:
                await ctx.send(_tr(
                    lang,
                    "⚠️ You already have an evasion pass active!",
                    "⚠️ لديك بالفعل تصريح هروب مفعل!",
                    "⚠️ Vous avez déjà un passe d'évasion actif !"
                ))
                return
       
            # Update user's dungeon_skip to 1 and dungeon_round to 2
            update_user_query = """
            UPDATE user
            SET dungeon_skip = 1, dungeon_rounds = 2
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_user_query, (user_id,))
       
            # Handle evasion pass quantity in inventory
            current_quantity = evasion_pass_result[0]['quantiter']
       
            if current_quantity == 1:
                # Delete the row if quantity is 1
                delete_query = """
                DELETE FROM inventory_product
                WHERE inventory_id = %s AND product_id = 19
                """
                await execute_query_with_retry(delete_query, (inventory_id,))
            else:
                # Decrease quantity by 1
                update_quantity_query = """
                UPDATE inventory_product
                SET quantiter = quantiter - 1
                WHERE inventory_id = %s AND product_id = 19
                """
                await execute_query_with_retry(update_quantity_query, (inventory_id,))
       
            # Success message
            embed = discord.Embed(
                title=_tr(lang, "✅ Evasion Pass Activated!", "✅ تم تفعيل تصريح الهروب!", "✅ Passe d'évasion activé !"),
                description=_tr(
                    lang,
                    "You have successfully activated an Evasion Pass!\n\n"
                    "🔹 **Dungeon Skip:** Activated\n"
                    "🔹 **Dungeon Round:** Advanced to 2\n"
                    "🔹 **Evasion Pass:** Consumed",
                    "لقد قمت بتفعيل تصريح الهروب بنجاح!\n\n"
                    "🔹 **تخطي الدنجن:** مُفعل\n"
                    "🔹 **جولة الدنجن:** تمت الترقية إلى 2\n"
                    "🔹 **تصريح الهروب:** تم استخدامه",
                    "Vous avez activé un Passe d'évasion avec succès !\n\n"
                    "🔹 **Saut de donjon :** Activé\n"
                    "🔹 **Manche du donjon :** Avancée à 2\n"
                    "🔹 **Passe d'évasion :** Consommé"
                ),
                color=0x00FF00
            )
       
            embed.set_footer(
                text=_tr(lang, f"Requested by {ctx.author.name}", f"تم الطلب بواسطة {ctx.author.name}", f"Demandé par {ctx.author.name}"),
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
       
            await ctx.send(embed=embed)
       
        except Exception as e:
            print(f"Error in skip_dungeon command: {e}")
            await ctx.send(_tr(
                getattr(self, "lang", 0),
                "❌ An error occurred while processing your request. Please try again.",
                "❌ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى.",
                "❌ Une erreur s'est produite lors du traitement de votre demande. Veuillez réessayer."
            ))


    @commands.command(name='pickadd', aliases=['pa', 'pc'])
    async def pickadd_command(self, ctx, *, pickaxe_name: str):
        try:
            lang = await get_user_multilanguage(ctx.author.id)

            # Get user's Discord ID
            user_author_id = str(ctx.author.id)

            # Check if user exists in the database
            user_query = "SELECT * FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (user_author_id,))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to register first. Use `sl register` to get started.",
                    "يجب أن تسجل أولاً. استخدم `sl register` للبدء.",
                    "Vous devez d'abord vous inscrire. Utilisez `sl register` pour commencer."
                ))
                return

            user = user_result[0]
            user_id = user['user_id']
       
            # Check if user is already in pickaxe_status table
            status_query = "SELECT * FROM pickaxe_status WHERE user_id = %s"
            status_result = await execute_query_with_retry(status_query, (user_id,))
       
            if status_result:
                await ctx.send(_tr(
                    lang,
                    "You already have a pending pickaxe operation. Please wait until it completes.",
                    "لديك بالفعل عملية معول قيد الانتظار. يرجى الانتظار حتى تكتمل.",
                    "Vous avez déjà une opération de pioche en cours. Veuillez attendre qu'elle se termine."
                ))
                return
           
            # Check for active trades
            trade_query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(trade_query, (user_id, user_id))
       
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade. Finish it or wait for its cooldown before using this command. If there’s any issue, open a ticket in the support server.",
                    "أنت حالياً في عملية تبادل نشطة. أنهها أو انتظر فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez son délai de recharge avant d'utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur de support."
                ))
                return

            # Find pickaxe by name (using LIKE for partial matches)
            pickaxe_query = "SELECT * FROM pickaxe WHERE pickaxe_name LIKE %s"
            pickaxe_result = await execute_query_with_retry(pickaxe_query, (f'%{pickaxe_name}%',))

            if not pickaxe_result:
                await ctx.send(_tr(
                    lang,
                    "No pickaxe found matching the name you specified.",
                    "لم يتم العثور على معول يطابق الاسم الذي أدخلته.",
                    "Aucune pioche ne correspond au nom spécifié."
                ))
                return
            if len(pickaxe_result) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Results Found", "تم العثور على عدة نتائج", "Plusieurs résultats trouvés"),
                    description=_tr(
                        lang,
                        "I found multiple pickaxes matching your search. Please specify which one you'd like to equip:",
                        "لقد وجدت عدة معاول تطابق بحثك. يرجى تحديد أيها تريد تجهيزه:",
                        "J'ai trouvé plusieurs pioches correspondant à votre recherche. Veuillez préciser laquelle équiper :"
                    ),
                    color=discord.Color.blue()
                )

                # List all matching pickaxes
                pickaxe_list = "\n".join([f"• {pickaxe['pickaxe_name']}" for pickaxe in pickaxe_result])
                embed.add_field(
                    name=_tr(lang, "Matching Pickaxes", "المعاول المطابقة", "Pioches correspondantes"),
                    value=pickaxe_list,
                    inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact name of the pickaxe you want to equip.",
                    "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للمعول الذي تريد تجهيزه.",
                    "Veuillez réutiliser la commande avec le nom exact de la pioche à équiper."
                ))
           
                await ctx.send(embed=embed)
                return
           
            pickaxe = pickaxe_result[0]
            pickaxe_id = pickaxe['id_pickaxe']
       
            # Get user's inventory ID
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send(_tr(lang, "Inventory not found.", "لم يتم العثور على الجرد.", "Inventaire introuvable."))
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check if user has this pickaxe in inventory
            inventory_pickaxe_query = """
            SELECT * FROM inventory_pickaxe
            WHERE inventory_id = %s AND id_pickaxe = %s
            """
            inventory_pickaxe_result = await execute_query_with_retry(inventory_pickaxe_query, (inventory_id, pickaxe_id))

            if not inventory_pickaxe_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have this pickaxe in your inventory.",
                    "ليس لديك هذا المعول في جردك.",
                    "Vous n'avez pas cette pioche dans votre inventaire."
                ))
                return
           
            # Insert user into pickaxe_status table
            insert_status_query = """
            INSERT INTO pickaxe_status (id_pickaxe, user_id)
            VALUES (%s, %s)
            """
            await execute_query_with_retry(insert_status_query, (pickaxe_id, user_id))

            # Check if user already has a pickaxe equipped
            if user['pickaxe_id'] != 0:
                # Ask for confirmation to replace
                embed = discord.Embed(
                    title=_tr(lang, "Replace Pickaxe", "استبدال المعول", "Remplacer la pioche"),
                    description=_tr(
                        lang,
                        f"You already have a pickaxe equipped. Do you want to replace it with {pickaxe['pickaxe_name']}?",
                        f"لديك بالفعل معول مجهز. هل تريد استبداله بـ {pickaxe['pickaxe_name']}؟",
                        f"Vous avez déjà une pioche équipée. Voulez-vous la remplacer par {pickaxe['pickaxe_name']} ?"
                    ),
                    color=discord.Color.orange()
                )
           
                view = discord.ui.View()
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.green, label=_tr(lang, "Yes", "نعم", "Oui"), custom_id="replace_pickaxe_yes"))
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.red, label=_tr(lang, "No", "لا", "Non"), custom_id="replace_pickaxe_no"))
           
                message = await ctx.send(embed=embed, view=view)

                def check(interaction):
                    return interaction.user == ctx.author and interaction.message.id == message.id

                try:
                    interaction = await self.bot.wait_for('interaction', check=check, timeout=60.0)
               
                    if interaction.data['custom_id'] == 'replace_pickaxe_no':
                        # Clean up status entry if user cancels
                        delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                        await execute_query_with_retry(delete_status_query, (user_id,))
                        await interaction.response.send_message(_tr(lang, "Pickaxe replacement cancelled.", "تم إلغاء استبدال المعول.", "Remplacement de la pioche annulé."), ephemeral=True)
                        return

                    # If yes, continue with replacement process
                    await interaction.response.defer()
               
                    # Check for active trades again before proceeding
                    trade_result = await execute_query_with_retry(trade_query, (user_id, user_id))
                    if trade_result:
                        # Clean up status entry if user is in trade
                        delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                        await execute_query_with_retry(delete_status_query, (user_id,))
                        await ctx.send(_tr(
                            lang,
                            "You are currently in an active trade. Finish it or wait for its cooldown before using this command. If there’s any issue, open a ticket in the support server",
                            "أنت حالياً في عملية تبادل نشطة. أنهها أو انتظر فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم",
                            "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez son délai de recharge avant d'utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur de support"
                        ))
                        return
                   
                    # Check if user still has the pickaxe in inventory
                    inventory_pickaxe_result = await execute_query_with_retry(inventory_pickaxe_query, (inventory_id, pickaxe_id))
                    if not inventory_pickaxe_result:
                        # Clean up status entry if pickaxe not in inventory
                        delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                        await execute_query_with_retry(delete_status_query, (user_id,))
                        await ctx.send(_tr(
                            lang,
                            f"You no longer have the {pickaxe['pickaxe_name']} pickaxe in your inventory.",
                            f"لم يعد لديك معول {pickaxe['pickaxe_name']} في جردك.",
                            f"Vous n'avez plus la pioche {pickaxe['pickaxe_name']} dans votre inventaire."
                        ))
                        return

                except asyncio.TimeoutError:
                    # Clean up status entry if timeout
                    delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                    await execute_query_with_retry(delete_status_query, (user_id,))
                    await ctx.send(_tr(lang, "Pickaxe replacement timed out.", "انتهى وقت استبدال المعول.", "Le remplacement de la pioche a expiré."))
                    return

            # Handle current equipped pickaxe
            current_pickaxe_id = user['pickaxe_id']
       
            if current_pickaxe_id != 0:
                # Check quantity of current pickaxe in inventory
                current_pickaxe_inventory_query = """
                SELECT * FROM inventory_pickaxe
                WHERE inventory_id = %s AND id_pickaxe = %s
                """
                current_pickaxe_inventory = await execute_query_with_retry(current_pickaxe_inventory_query, (inventory_id, current_pickaxe_id))

                if current_pickaxe_inventory:
                    # increase quantity
                    update_current_pickaxe_query = """
                    UPDATE inventory_pickaxe
                    SET quantiter = quantiter + 1
                    WHERE inventory_id = %s AND id_pickaxe = %s
                    """
                    await execute_query_with_retry(update_current_pickaxe_query, (inventory_id, current_pickaxe_id))
                else:
                    update_current_pickaxe_query = """
                    INSERT into inventory_pickaxe(inventory_id, id_pickaxe, quantiter) values(%s,%s,1)
                    """
                    await execute_query_with_retry(update_current_pickaxe_query, (inventory_id, current_pickaxe_id))                    
       
            # Check if user still has the pickaxe in inventory
            inventory_pickaxe_result = await execute_query_with_retry(inventory_pickaxe_query, (inventory_id, pickaxe_id))
            if not inventory_pickaxe_result:
                # Clean up status entry if pickaxe not in inventory
                delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                await execute_query_with_retry(delete_status_query, (user_id,))
                await ctx.send(_tr(
                    lang,
                    f"You no longer have the {pickaxe['pickaxe_name']} pickaxe in your inventory.",
                    f"لم يعد لديك معول {pickaxe['pickaxe_name']} في جردك.",
                    f"Vous n'avez plus la pioche {pickaxe['pickaxe_name']} dans votre inventaire."
                ))
                return
       
            # Handle new pickaxe
            new_pickaxe_inventory_query = """
            SELECT * FROM inventory_pickaxe
            WHERE inventory_id = %s AND id_pickaxe = %s
            """
            new_pickaxe_inventory = await execute_query_with_retry(new_pickaxe_inventory_query, (inventory_id, pickaxe_id))

            if new_pickaxe_inventory:
                if new_pickaxe_inventory[0]['quantiter'] == 1:
                    # Remove row if quantity is 1
                    remove_new_pickaxe_query = """
                    DELETE FROM inventory_pickaxe
                    WHERE inventory_id = %s AND id_pickaxe = %s
                    """
                    await execute_query_with_retry(remove_new_pickaxe_query, (inventory_id, pickaxe_id))
                else:
                    # Decrease quantity
                    update_new_pickaxe_query = """
                    UPDATE inventory_pickaxe
                    SET quantiter = quantiter - 1
                    WHERE inventory_id = %s AND id_pickaxe = %s
                    """
                    await execute_query_with_retry(update_new_pickaxe_query, (inventory_id, pickaxe_id))

            # Update user's equipped pickaxe
            update_user_pickaxe_query = """
            UPDATE user
            SET pickaxe_id = %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(update_user_pickaxe_query, (pickaxe_id, user_id))

            # Create embed with pickaxe equip result
            embed = discord.Embed(
                title=_tr(lang, "Pickaxe Equipped", "تم تجهيز المعول", "Pioche équipée"),
                color=discord.Color.green()
            )
            embed.description = _tr(
                lang,
                f"You have successfully equipped the **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']}!",
                f"لقد قمت بتجهيز **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']} بنجاح!",
                f"Vous avez équipé **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']} avec succès !"
            )
       
            # Delete user from pickaxe_status table after successful operation
            delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
            await execute_query_with_retry(delete_status_query, (user_id,))
       
            await ctx.send(embed=embed)

        except Exception as e:
            # Make sure to clean up status entry if there's an error
            try:
                user_id = user_result[0]['user_id'] if 'user_result' in locals() and user_result else None
                if user_id:
                    delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                    await execute_query_with_retry(delete_status_query, (user_id,))
            except:
                pass
           
            print(f"Pickaxe add error: {e}")
            await ctx.send(_tr(
                getattr(self, "lang", 0),
                "An error occurred while adding the pickaxe.",
                "حدث خطأ أثناء إضافة المعول.",
                "Une erreur est survenue lors de l'ajout de la pioche."
            ))

    @commands.command(name='xpboost', aliases=["xb", "xp"])
    async def xp_boost(self, ctx, *, args):
        lang = await get_user_multilanguage(ctx.author.id)

        def parse_input(local_args):
            parts = local_args.split()
            # If only one part, assume it's the card name
            if len(parts) == 1:
                return parts[0], 1
            # Try to convert the last part to an integer (quantity)
            try:
                quantity = int(parts[-1])
                card_name = ' '.join(parts[:-1])
                return card_name, quantity
            except ValueError:
                # If last part is not a number, assume full input is the card name
                return ' '.join(parts), 1

        # Parse input
        card_name, quantity = parse_input(args)
        if quantity <= 0:
            await ctx.send(_tr(lang, "Invalid quantity.", "كمية غير صالحة.", "Quantité invalide."))
            return

        # Hardcoded XP Boost product ID
        product_id = 1
        product_name = _tr(lang, "boost", "تعزيز", "boost")

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to be registered to use this command.",
                    "يجب أن تكون مسجلاً لاستخدام هذا الأمر.",
                    "Vous devez être inscrit pour utiliser cette commande."
                ))
                return
            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, finish it or wait for its cooldown before using this command. If there's any issue, open a ticket in the support server.",
                    "أنت حالياً في عملية تبادل نشطة، أنهِها أو انتظر انتهاء التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif, terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de problème, ouvrez un ticket sur le serveur d'assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء عرض. يرجى الانتظار حتى يكتمل إنشاء العرض الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Inventory id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have an inventory.",
                    "لا تملك جردًا.",
                    "Vous n'avez pas d'inventaire."
                ))
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check product availability
            inv_product_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            inv_product_result = await execute_query_with_retry(inv_product_query, (inventory_id, product_id))
            if not inv_product_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have any {product_name} items in your inventory.",
                    f"ليس لديك أي عناصر {product_name} في جردك.",
                    f"Vous n'avez aucun objet {product_name} dans votre inventaire."
                ))
                return
            if inv_product_result[0]['quantiter'] < quantity:
                await ctx.send(_tr(
                    lang,
                    f"You don't have enough {product_name} items. You have {inv_product_result[0]['quantiter']} but tried to use {quantity}.",
                    f"ليس لديك عناصر {product_name} كافية. لديك {inv_product_result[0]['quantiter']} وحاولت استخدام {quantity}.",
                    f"Vous n'avez pas assez d'objets {product_name}. Vous en avez {inv_product_result[0]['quantiter']} mais avez essayé d'en utiliser {quantity}."
                ))
                return

            # Find user cards with LIKE matching
            user_card_query = "SELECT u.*,c.type_card,c.xp_cap,c.card_icon FROM user_cards u,card c WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, f"%{card_name}%"))
            if not user_card_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have any cards matching '{card_name}' in your collection.",
                    f"لا توجد لديك أي بطاقات تطابق '{card_name}' في مجموعتك.",
                    f"Vous n'avez aucune carte correspondant à '{card_name}' dans votre collection."
                ))
                return

            if len(user_card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to boost:",
                        "وجدت عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد تعزيزها:",
                        "J'ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser laquelle vous souhaitez améliorer :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in user_card_result])
                embed.add_field(
                    name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                    value=card_list,
                    inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact name of the card you want to boost.",
                    "يرجى استخدام الأمر مرة أخرى مع الاسم الدقيق للبطاقة التي تريد تعزيزها.",
                    "Veuillez réutiliser la commande avec le nom exact de la carte à améliorer."
                ))
                await ctx.send(embed=embed)
                return

            # Exactly one card
            user_card = user_card_result[0]

            # Type check
            if user_card['type_card'] in ['Support', 'Booster']:
                await ctx.send(_tr(
                    lang,
                    "This command can only be used with Offence-type cards.",
                    "يمكن استخدام هذا الأمر مع بطاقات الهجوم فقط.",
                    "Cette commande ne peut être utilisée qu'avec des cartes de type Attaque."
                ))
                return

            if user_card['current_level'] == user_card['max_leveled']:
                await ctx.send(_tr(
                    lang,
                    f"'{user_card['name']}' has already reached its maximum level.",
                    f"'{user_card['name']}' وصل بالفعل إلى أقصى مستوى.",
                    f"« {user_card['name']} » a déjà atteint son niveau maximal."
                ))
                return

            # Get XP boost value
            product_query = "SELECT xp_boost FROM product WHERE product_id = %s"
            product_result = await execute_query_with_retry(product_query, (product_id,))
            if not product_result or product_result[0]['xp_boost'] == 0:
                await ctx.send(_tr(
                    lang,
                    "XP boost product is not configured correctly.",
                    "عنصر زيادة الخبرة غير مُعد بشكل صحيح.",
                    "Le produit d'XP boost n'est pas configuré correctement."
                ))
                return
            xp_boost_value = product_result[0]['xp_boost']

            # Pre-calc XP to max and boosts to use
            current_xp = user_card['current_xp']
            current_level = user_card['current_level']
            max_leveled = user_card['max_leveled']
            xp_cap = user_card['xp_cap']

            xp_to_max = 0
            boosts_to_use = 0
            remaining_xp = current_xp
            current_calc_level = current_level

            while current_calc_level < max_leveled:
                xp_needed_to_next = xp_cap - remaining_xp
                if xp_boost_value >= xp_needed_to_next:
                    xp_to_max += xp_needed_to_next
                    boosts_to_use += 1
                    remaining_xp = 0
                    current_calc_level += 1
                else:
                    xp_to_max += xp_boost_value
                    boosts_to_use += 1
                    remaining_xp += xp_boost_value
                    if remaining_xp >= xp_cap:
                        remaining_xp -= xp_cap
                        current_calc_level += 1
                if current_calc_level >= max_leveled:
                    break

            boosts_to_use = min(boosts_to_use, quantity)
            if boosts_to_use == 0:
                await ctx.send(_tr(
                    lang,
                    f"'{user_card['name']}' is already at maximum potential XP for its current level.",
                    f"'{user_card['name']}' وصل بالفعل إلى الحد الأقصى من الخبرة لمستواه الحالي.",
                    f"« {user_card['name']} » est déjà au potentiel XP maximum pour son niveau actuel."
                ))
                return

            # Calculate XP and levels gained
            total_xp_boost = xp_boost_value * boosts_to_use
            new_xp = user_card['current_xp'] + total_xp_boost
            new_level = user_card['current_level']

            levels_gained = 0
            stat_increases = {
                'attack_stat': 0,
                'health_stat': 0,
                'defence_stat': 0,
                'speed_stat': 0,
                'current_statpoints': 0
            }

            while new_xp >= xp_cap and new_level < max_leveled:
                new_level += 1
                new_xp -= xp_cap
                levels_gained += 1
                stat_increases['health_stat'] += random.randint(3, 5)
                stat_increases['attack_stat'] += random.randint(2, 4)
                stat_increases['speed_stat'] += random.randint(2, 4)
                stat_increases['defence_stat'] += random.randint(1, 2)
                stat_increases['current_statpoints'] += 2

            if new_level == max_leveled:
                new_xp = 0

            # Equipment/support context
            has_equipment = user_card['weapon_id'] is not None or user_card['armor_id'] is not None or user_card['shield_id'] is not None
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, user_card['card_id'])

            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            has_support_in_team = False
            if team_result:
                team_id = team_result[0]['team_id']
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, user_card['card_id']))
                if team_card_result:
                    support_query = """
                    SELECT COUNT(*) as support_count
                    FROM team_cards tc
                    JOIN card c ON tc.card_id = c.card_id
                    WHERE tc.team_id = %s AND c.type_card = 'Support'
                    """
                    support_result = await execute_query_with_retry(support_query, (team_id,))
                    if support_result and support_result[0]['support_count'] > 0:
                        has_support_in_team = True
                        support_boosts = await self.calculate_total_support_boosts(team_id, user_id)

            # Four scenarios
            if not has_support_in_team and not has_equipment:
                update_xp_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = base_attack + %s,
                    base_health = base_health + %s,
                    base_defence = base_defence + %s,
                    base_speed = base_speed + %s,
                    attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    defence_stat = defence_stat + %s,
                    speed_stat = speed_stat + %s,
                    power_stat = attack_stat+health_stat+defence_stat+speed_stat,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_xp_query, (
                    new_xp, new_level,
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

            elif has_support_in_team and not has_equipment:
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (user_card['id_ligneusercards'],))

                update_base_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = base_attack + %s,
                    base_health = base_health + %s,
                    base_defence = base_defence + %s,
                    base_speed = base_speed + %s,
                    attack_stat = base_attack + %s,
                    health_stat = base_health + %s,
                    defence_stat = base_defence + %s,
                    speed_stat = base_speed + %s,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_xp, new_level,
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )

            elif not has_support_in_team and has_equipment:
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                if equipment_boosts:
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed']  = true_base_stats['speed']  / (1 + equipment_boosts['pct_speed']/100)
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence']-= equipment_boosts['flat_defence']
                    true_base_stats['speed']  -= equipment_boosts['flat_speed']

                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                new_base_stats = dict(new_true_base_stats)
                if equipment_boosts:
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence']+= equipment_boosts['flat_defence']
                    new_base_stats['speed']  += equipment_boosts['flat_speed']
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence']*= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed']  *= (1 + equipment_boosts['pct_speed']/100)

                new_power = new_base_stats['attack'] + new_base_stats['health'] + new_base_stats['defence'] + new_base_stats['speed']

                update_base_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s,
                    power_stat = %s,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_xp, new_level,
                    new_base_stats['attack'], new_base_stats['health'],
                    new_base_stats['defence'], new_base_stats['speed'],
                    new_base_stats['attack'], new_base_stats['health'],
                    new_base_stats['defence'], new_base_stats['speed'],
                    new_power, stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

            else:
                # both support and equipment
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                if equipment_boosts:
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed']  = true_base_stats['speed']  / (1 + equipment_boosts['pct_speed']/100)
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence']-= equipment_boosts['flat_defence']
                    true_base_stats['speed']  -= equipment_boosts['flat_speed']

                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                new_base_stats = dict(new_true_base_stats)
                if equipment_boosts:
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence']+= equipment_boosts['flat_defence']
                    new_base_stats['speed']  += equipment_boosts['flat_speed']
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence']*= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed']  *= (1 + equipment_boosts['pct_speed']/100)

                # reset current to new true base
                reset_query = """
                UPDATE user_cards
                SET attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(
                    reset_query,
                    (new_true_base_stats['attack'], new_true_base_stats['health'],
                    new_true_base_stats['defence'], new_true_base_stats['speed'],
                    user_card['id_ligneusercards'])
                )

                update_base_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_xp, new_level,
                    new_base_stats['attack'], new_base_stats['health'],
                    new_base_stats['defence'], new_base_stats['speed'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

                await self.apply_equipment_boosts(user_id, user_card['card_id'], equipment_boosts)
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )

            # Update inventory (remove only used boosts)
            if inv_product_result[0]['quantiter'] == boosts_to_use:
                delete_product_query = "DELETE FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
                await execute_query_with_retry(delete_product_query, (inventory_id, product_id))
            else:
                update_quantity_query = """
                UPDATE inventory_product
                SET quantiter = quantiter - %s
                WHERE inventory_id = %s AND product_id = %s
                """
                await execute_query_with_retry(update_quantity_query, (boosts_to_use, inventory_id, product_id))

            # Get updated stats for message
            updated_stats_query = """
            SELECT attack_stat, health_stat, defence_stat, speed_stat
            FROM user_cards
            WHERE id_ligneusercards = %s
            """
            updated_stats = await execute_query_with_retry(updated_stats_query, (user_card['id_ligneusercards'],))
            new_stats = {
                'attack': updated_stats[0]['attack_stat'],
                'health': updated_stats[0]['health_stat'],
                'defence': updated_stats[0]['defence_stat'],
                'speed': updated_stats[0]['speed_stat']
            }

            # Response text
            base_response = _tr(
                lang,
                f"Applied {product_name} x{boosts_to_use} to {user_card['card_icon']} {user_card['name']}. XP boosted to {new_xp}, now at level {new_level}.",
                f"تم تطبيق {product_name} ×{boosts_to_use} على {user_card['card_icon']} {user_card['name']}. تمت زيادة الخبرة إلى {new_xp}، المستوى الحالي {new_level}.",
                f"{product_name} ×{boosts_to_use} appliqué à {user_card['card_icon']} {user_card['name']}. XP augmenté à {new_xp}, niveau actuel {new_level}."
            )

            if levels_gained > 0:
                base_response += "\n" + _tr(
                    lang,
                    f"Leveled up {levels_gained} time(s)! Stat increases:",
                    f"تمت الترقية {levels_gained} مرة! زيادات الإحصائيات:",
                    f"Niveau augmenté {levels_gained} fois ! Augmentations de stats :"
                )
                # List increases (only those > 0)
                for stat, inc in stat_increases.items():
                    if inc > 0:
                        label = {
                            'attack_stat': _tr(lang, "Attack", "الهجوم", "Attaque"),
                            'health_stat': _tr(lang, "Health", "الصحة", "Santé"),
                            'defence_stat': _tr(lang, "Defense", "الدفاع", "Défense"),
                            'speed_stat': _tr(lang, "Speed", "السرعة", "Vitesse"),
                            'current_statpoints': _tr(lang, "Stat Points", "نقاط الإحصائيات", "Points de stats")
                        }[stat]
                        base_response += f"\n- {label}: +{inc}"

            if boosts_to_use < quantity:
                base_response += "\n" + _tr(
                    lang,
                    f"{quantity - boosts_to_use} {product_name} items were not used as the card reached max level.",
                    f"{quantity - boosts_to_use} من عناصر {product_name} لم تُستخدم لأن البطاقة وصلت إلى الحد الأقصى للمستوى.",
                    f"{quantity - boosts_to_use} objet(s) {product_name} n'ont pas été utilisés car la carte a atteint le niveau maximum."
                )

            await ctx.send(base_response)

            # Quest progress
            try:
                await self.update_quest_progress(ctx, user_id, "Use 10 XpBottles", boosts_to_use)
            except Exception as e:
                print(f"Error in quest tracking: {e}")

        except Exception as e:
            await ctx.send(_tr(
                getattr(self, "lang", 0),
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))


    def parsed_input(self, args):
        # (kept as your minimal splitter — no localization needed here)
        parts = args.split()
        if len(parts) < 2:
            return None, None
        return ' '.join(parts[:-1]), parts[-1]

    @commands.command(name="level", aliases=["lv", "lvl"])
    async def level(self, ctx, *, args):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Split args into name and quantity (expects "Card Name, 5" or "Card Name, all")
            if ',' not in args:
                await ctx.send(_tr(
                    lang,
                    "Please provide both card name and quantity (e.g., 'sl level CardName, 5')",
                    "يرجى إدخال اسم البطاقة والكمية (مثال: 'sl level CardName, 5')",
                    "Veuillez indiquer le nom de la carte et la quantité (ex. : 'sl level CardName, 5')"
                ))
                return

            card_name, quantity = args.rsplit(',', 1)
            card_name = card_name.strip()
            quantity = quantity.strip()

            is_all = quantity.lower() == 'all'
            if is_all:
                quantity = float('inf')
            else:
                try:
                    quantity = int(quantity)
                    if quantity <= 0:
                        await ctx.send(_tr(
                            lang,
                            "Please provide a positive quantity.",
                            "يرجى إدخال كمية موجبة.",
                            "Veuillez indiquer une quantité positive."
                        ))
                        return
                except ValueError:
                    await ctx.send(_tr(
                        lang,
                        "Please provide a valid number for quantity.",
                        "يرجى إدخال رقم صحيح للكمية.",
                        "Veuillez indiquer un nombre valide pour la quantité."
                    ))
                    return

            # Get user data
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to be registered to use this command.",
                    "تحتاج إلى التسجيل لاستخدام هذا الأمر.",
                    "Vous devez être inscrit pour utiliser cette commande."
                ))
                return

            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, finish it or wait for its cooldown before using this command. If there's any issue, open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d'assistance."
                ))
                return

            # Ascending state lock
            ascending_check_query = "SELECT * FROM ascending_state WHERE user_id = %s"
            ascending_result = await execute_query_with_retry(ascending_check_query, (user_id,))
            if ascending_result:
                await ctx.send(_tr(
                    lang,
                    "⏳ You are already performing an ascension. Please wait for it to complete.",
                    "⏳ أنت تقوم بالفعل بعملية ترقية. يرجى الانتظار حتى تكتمل.",
                    "⏳ Vous effectuez déjà une ascension. Veuillez attendre qu'elle se termine."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Get inventory ID
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have an inventory.",
                    "ليس لديك جرد.",
                    "Vous n'avez pas d'inventaire."
                ))
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check if user has the card
            card_query = """
            SELECT uc.*, c.type_card, uc.max_leveled, c.xp_cap, c.id_fragment, c.card_icon
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            WHERE uc.user_id = %s AND uc.name LIKE %s
            """
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))

            if not card_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have a card named '{card_name}'.",
                    f"ليس لديك بطاقة باسم '{card_name}'.",
                    f"Vous n'avez pas de carte nommée « {card_name} »."
                ))
                return

            user_card = card_result[0]

            # Card type check
            if user_card['type_card'] in ['Support', 'Booster']:
                await ctx.send(_tr(
                    lang,
                    "This command can only be used with Offence-type cards.",
                    "يمكن استخدام هذا الأمر مع بطاقات الهجوم فقط.",
                    "Cette commande ne peut être utilisée qu'avec des cartes de type Attaque."
                ))
                return

            # Max level check
            if user_card['current_level'] >= user_card['max_leveled']:
                await ctx.send(_tr(
                    lang,
                    f"'{user_card['name']}' has already reached its maximum level.",
                    f"'{user_card['name']}' وصل بالفعل إلى أقصى مستوى.",
                    f"« {user_card['name']} » a déjà atteint son niveau maximal."
                ))
                return

            # Prevent conflicts with stat adjustment session
            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, user_card['card_id']))
            if stat_status_result:
                await ctx.send(_tr(
                    lang,
                    "This card is currently being adjusted. Please wait until the process is completed.",
                    "يتم ضبط هذه البطاقة حالياً. يرجى الانتظار حتى اكتمال العملية.",
                    "Cette carte est en cours d'ajustement. Veuillez patienter jusqu'à la fin du processus."
                ))
                return

            # Check fragments
            frag_query = """
            SELECT quantiter FROM inventory_frags
            WHERE inventory_id = %s AND frag_id = %s
            """
            frag_result = await execute_query_with_retry(frag_query, (inventory_id, user_card['id_fragment']))
            if not frag_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have any stones for '{user_card['name']}'.",
                    f"لا تملك أي أحجار لـ '{user_card['name']}'.",
                    f"Vous n'avez aucune pierre pour « {user_card['name']} »."
                ))
                return

            # Calculate XP & levels
            fragment_xp = 100  # XP per fragment
            current_xp = user_card['current_xp']
            current_level = user_card['current_level']
            max_leveled = user_card['max_leveled']
            xp_cap = user_card['xp_cap']
            available_fragments = frag_result[0]['quantiter']

            # Sanity on requested quantity vs available
            if not is_all and quantity > available_fragments:
                await ctx.send(_tr(
                    lang,
                    f"You only have {available_fragments} stones available.",
                    f"لديك فقط {available_fragments} حجر/أحجار متاحة.",
                    f"Vous n'avez que {available_fragments} pierre(s) disponible(s)."
                ))
                return

            # Fragments needed to reach max level
            remaining_xp = current_xp
            current_calc_level = current_level
            fragments_needed_for_max = 0
            while current_calc_level < max_leveled:
                xp_needed_to_next = xp_cap - remaining_xp
                fragments_for_level = math.ceil(xp_needed_to_next / fragment_xp)
                fragments_needed_for_max += fragments_for_level
                remaining_xp = 0
                current_calc_level += 1

            requested_fragments = min(quantity if not is_all else available_fragments, available_fragments)
            fragments_to_use = min(requested_fragments, fragments_needed_for_max)

            total_xp_gain = fragments_to_use * fragment_xp
            new_xp = current_xp + total_xp_gain
            new_level = current_level

            # Stat increases per level up
            levels_gained = 0
            stat_increases = {
                'attack_stat': 0,
                'health_stat': 0,
                'defence_stat': 0,
                'speed_stat': 0,
                'current_statpoints': 0
            }

            while new_xp >= xp_cap and new_level < max_leveled:
                new_level += 1
                new_xp -= xp_cap
                levels_gained += 1
                stat_increases['health_stat'] += random.randint(3, 5)
                stat_increases['attack_stat'] += random.randint(2, 4)
                stat_increases['speed_stat']  += random.randint(2, 4)
                stat_increases['defence_stat']+= random.randint(1, 2)
                stat_increases['current_statpoints'] += 2

            if new_level == max_leveled:
                new_xp = 0

            # Equipment / support context
            has_equipment = user_card['weapon_id'] is not None or user_card['armor_id'] is not None or user_card['shield_id'] is not None
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, user_card['card_id'])

            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            has_support_in_team = False
            if team_result:
                team_id = team_result[0]['team_id']
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, user_card['card_id']))
                if team_card_result:
                    support_query = """
                    SELECT COUNT(*) as support_count
                    FROM team_cards tc
                    JOIN card c ON tc.card_id = c.card_id
                    WHERE tc.team_id = %s AND c.type_card = 'Support'
                    """
                    support_result = await execute_query_with_retry(support_query, (team_id,))
                    if support_result and support_result[0]['support_count'] > 0:
                        has_support_in_team = True
                        support_boosts = await self.calculate_total_support_boosts(team_id, user_id)

            # SCENARIOS
            # 1) No support, no equipment
            if not has_support_in_team and not has_equipment:
                update_card_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = base_attack + %s,
                    base_health = base_health + %s,
                    base_defence = base_defence + %s,
                    base_speed = base_speed + %s,
                    attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    defence_stat = defence_stat + %s,
                    speed_stat = speed_stat + %s,
                    power_stat = attack_stat+health_stat+defence_stat+speed_stat,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_card_query, (
                    new_xp, new_level,
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

            # 2) Support, no equipment
            elif has_support_in_team and not has_equipment:
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (user_card['id_ligneusercards'],))

                update_base_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = base_attack + %s,
                    base_health = base_health + %s,
                    base_defence = base_defence + %s,
                    base_speed = base_speed + %s,
                    attack_stat = base_attack + %s,
                    health_stat = base_health + %s,
                    defence_stat = base_defence + %s,
                    speed_stat = base_speed + %s,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_xp, new_level,
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['attack_stat'], stat_increases['health_stat'],
                    stat_increases['defence_stat'], stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )

            # 3) No support, has equipment
            elif not has_support_in_team and has_equipment:
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                if equipment_boosts:
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed']  = true_base_stats['speed']  / (1 + equipment_boosts['pct_speed']/100)
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence']-= equipment_boosts['flat_defence']
                    true_base_stats['speed']  -= equipment_boosts['flat_speed']

                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                new_base_stats = dict(new_true_base_stats)
                if equipment_boosts:
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence']+= equipment_boosts['flat_defence']
                    new_base_stats['speed']  += equipment_boosts['flat_speed']
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence']*= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed']  *= (1 + equipment_boosts['pct_speed']/100)

                new_power = new_base_stats['attack'] + new_base_stats['health'] + new_base_stats['defence'] + new_base_stats['speed']

                update_base_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s,
                    power_stat = %s,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_xp, new_level,
                    new_base_stats['attack'], new_base_stats['health'],
                    new_base_stats['defence'], new_base_stats['speed'],
                    new_base_stats['attack'], new_base_stats['health'],
                    new_base_stats['defence'], new_base_stats['speed'],
                    new_power, stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

            # 4) Support + equipment
            else:
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                if equipment_boosts:
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed']  = true_base_stats['speed']  / (1 + equipment_boosts['pct_speed']/100)
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence']-= equipment_boosts['flat_defence']
                    true_base_stats['speed']  -= equipment_boosts['flat_speed']

                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                new_base_stats = dict(new_true_base_stats)
                if equipment_boosts:
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence']+= equipment_boosts['flat_defence']
                    new_base_stats['speed']  += equipment_boosts['flat_speed']
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence']*= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed']  *= (1 + equipment_boosts['pct_speed']/100)

                # Reset current to true-base+increases (no equipment)
                reset_query = """
                UPDATE user_cards
                SET attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(
                    reset_query,
                    (new_true_base_stats['attack'], new_true_base_stats['health'],
                    new_true_base_stats['defence'], new_true_base_stats['speed'],
                    user_card['id_ligneusercards'])
                )

                update_base_query = """
                UPDATE user_cards
                SET current_xp = %s,
                    current_level = %s,
                    base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    current_statpoints = current_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_xp, new_level,
                    new_base_stats['attack'], new_base_stats['health'],
                    new_base_stats['defence'], new_base_stats['speed'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))

                # Re-apply equipment, then support
                await self.apply_equipment_boosts(user_id, user_card['card_id'], equipment_boosts)
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )

            # Update fragments inventory
            if available_fragments == fragments_to_use:
                delete_frag_query = """
                DELETE FROM inventory_frags
                WHERE inventory_id = %s AND frag_id = %s
                """
                await execute_query_with_retry(delete_frag_query, (inventory_id, user_card['id_fragment']))
            else:
                update_frag_query = """
                UPDATE inventory_frags
                SET quantiter = quantiter - %s
                WHERE inventory_id = %s AND frag_id = %s
                """
                await execute_query_with_retry(update_frag_query, (
                    fragments_to_use, inventory_id, user_card['id_fragment']
                ))

            update_frags_query = "UPDATE user set quantiter_fragment = quantiter_fragment - %s WHERE user_id = %s"
            await execute_query_with_retry(update_frags_query, (fragments_to_use, user_id))

            # Get updated stats for response
            updated_stats_query = """
            SELECT attack_stat, health_stat, defence_stat, speed_stat
            FROM user_cards
            WHERE id_ligneusercards = %s
            """
            updated_stats = await execute_query_with_retry(updated_stats_query, (user_card['id_ligneusercards'],))
            new_stats = {
                'attack': updated_stats[0]['attack_stat'],
                'health': updated_stats[0]['health_stat'],
                'defence': updated_stats[0]['defence_stat'],
                'speed': updated_stats[0]['speed_stat']
            }

            # Build response
            response = _tr(
                lang,
                f"Used {fragments_to_use} fragments on {user_card['card_icon']} {user_card['name']}. XP increased to {new_xp}, now at level {new_level}.",
                f"تم استخدام {fragments_to_use} من الشظايا على {user_card['card_icon']} {user_card['name']}. زادت الخبرة إلى {new_xp}، المستوى الحالي {new_level}.",
                f"{fragments_to_use} fragments utilisés sur {user_card['card_icon']} {user_card['name']}. XP passée à {new_xp}, niveau actuel {new_level}."
            )
            if levels_gained > 0:
                response += "\n" + _tr(
                    lang,
                    f"Leveled up {levels_gained} time(s)! Stat increases:",
                    f"تمت الترقية {levels_gained} مرة! زيادات الإحصائيات:",
                    f"Niveau augmenté {levels_gained} fois ! Augmentations de stats :"
                )
                for stat, increase in stat_increases.items():
                    if increase > 0:
                        label = {
                            'attack_stat': _tr(lang, "Attack", "الهجوم", "Attaque"),
                            'health_stat': _tr(lang, "Health", "الصحة", "Santé"),
                            'defence_stat': _tr(lang, "Defense", "الدفاع", "Défense"),
                            'speed_stat': _tr(lang, "Speed", "السرعة", "Vitesse"),
                            'current_statpoints': _tr(lang, "Stat Points", "نقاط الإحصائيات", "Points de stats")
                        }[stat]
                        response += f"\n- {label}: +{increase}"

            # Note about saved fragments
            if requested_fragments > fragments_to_use:
                fragments_saved = requested_fragments - fragments_to_use
                response += "\n" + _tr(
                    lang,
                    f"{fragments_saved} stones were saved as they weren't needed to reach max level.",
                    f"{fragments_saved} من الأحجار تم توفيرها لأنها لم تكن مطلوبة للوصول إلى الحد الأقصى للمستوى.",
                    f"{fragments_saved} pierre(s) ont été économisées car elles n'étaient pas nécessaires pour atteindre le niveau max."
                )

            await ctx.send(response)

        except Exception as e:
            print(f"Error in level command: {e}")
            await ctx.send(_tr(
                getattr(self, "lang", 0),
                "An error occurred while processing your request.",
                "حدث خطأ أثناء معالجة طلبك.",
                "Une erreur s'est produite lors du traitement de votre demande."
            ))


    # Helper methods needed for the improved commands

    # Helper methods needed for the improved commands

    async def calculate_equipment_boosts(self, user_id, card_id):
        """Calculate the boosts provided by a card's equipment"""
        try:
            # Get the card's equipment
            equipment_query = """
                SELECT weapon_id, armor_id, shield_id
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
            """
            equipment = await execute_query_with_retry(equipment_query, (user_id, card_id))

            if not equipment:
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                }

            equipment = equipment[0]  # Now safe to access first element

            # Initialize boosts
            boosts = {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }

            # Check each equipment type
            equipment_types = [
                equipment.get('weapon_id'),
                equipment.get('armor_id'),
                equipment.get('shield_id')
            ]

            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage,
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details = await execute_query_with_retry(query, (eq_id,))
                   
                    if eq_details:
                        eq_details = eq_details[0]
                        # Add equipment flat boosts
                        boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                        boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                        boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                        boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                       
                        # Add equipment percentage boosts
                        boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                        boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                        boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                        boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

            return boosts
        except Exception as e:
            print(f"Error in calculate_equipment_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }

    # Fix for the calculate_support_card_boosts function
    async def calculate_support_card_boosts(self, user_id, card_id):
        """Calculate the total boosts from a single support card and its equipment"""
        try:
            # Get support card details
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence,
                c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM card c
            JOIN user_cards uc ON c.card_id = uc.card_id
            WHERE c.card_id = %s AND uc.user_id = %s
            """
            support_card_result = await execute_query_with_retry(query, (card_id, user_id))
           
            if not support_card_result:
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_speed': 0, 'pct_defence': 0
                }
               
            support_card = support_card_result[0]  # Now safe to access first element
           
            # Initialize boosts
            flat_attack = support_card.get('attack', 0) or 0
            flat_health = support_card.get('health', 0) or 0
            flat_speed = support_card.get('speed', 0) or 0
            flat_defence = support_card.get('defence', 0) or 0
           
            pct_attack = support_card.get('attack_percentage', 0) or 0
            pct_health = support_card.get('health_percentage', 0) or 0
            pct_speed = support_card.get('speed_percentage', 0) or 0
            pct_defence = support_card.get('defence_percentage', 0) or 0
           
            # Add equipment boosts
            equipment_types = [
                support_card.get('weapon_id'),
                support_card.get('armor_id'),
                support_card.get('shield_id')
            ]
           
            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage,
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details_result = await execute_query_with_retry(query, (eq_id,))
                   
                    if eq_details_result:
                        eq_details = eq_details_result[0]
                        # Add equipment flat boosts
                        flat_attack += eq_details.get('attack_boost', 0) or 0
                        flat_health += eq_details.get('health_boost', 0) or 0
                        flat_speed += eq_details.get('speed_boost', 0) or 0
                        flat_defence += eq_details.get('defense_boost', 0) or 0
                       
                        # Add equipment percentage boosts
                        pct_attack += eq_details.get('attack_percentage', 0) or 0
                        pct_health += eq_details.get('health_percentage', 0) or 0
                        pct_speed += eq_details.get('speed_percentage', 0) or 0
                        pct_defence += eq_details.get('defense_percentage', 0) or 0
           
            return {
                'flat_attack': flat_attack,
                'flat_health': flat_health,
                'flat_speed': flat_speed,
                'flat_defence': flat_defence,
                'pct_attack': pct_attack,
                'pct_health': pct_health,
                'pct_speed': pct_speed,
                'pct_defence': pct_defence
            }
        except Exception as e:
            print(f"Error in calculate_support_card_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_speed': 0, 'pct_defence': 0
            }
    async def apply_equipment_boosts(self, user_id, card_id, equipment_boosts):
        """Apply equipment boosts to a card's stats"""
        if not equipment_boosts:
            return

        # Apply flat boosts first
        flat_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat + %s,
            health_stat = health_stat + %s,
            defence_stat = defence_stat + %s,
            speed_stat = speed_stat + %s
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            flat_query,
            (equipment_boosts['flat_attack'], equipment_boosts['flat_health'],
            equipment_boosts['flat_defence'], equipment_boosts['flat_speed'],
            user_id, card_id)
        )

        # Then apply percentage boosts
        pct_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat * (1 + %s/100),
            health_stat = health_stat * (1 + %s/100),
            defence_stat = defence_stat * (1 + %s/100),
            speed_stat = speed_stat * (1 + %s/100)
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            pct_query,
            (equipment_boosts['pct_attack'], equipment_boosts['pct_health'],
            equipment_boosts['pct_defence'], equipment_boosts['pct_speed'],
            user_id, card_id)
        )

    async def calculate_total_support_boosts(self, team_id, user_id, exclude_card_id=None):
        """
        Calculate the total flat and percentage boosts from all support cards in the team
        Optionally exclude a specific card (useful when removing a support)
        """
        # Get all support cards in the team
        query = """
        SELECT c.card_id, c.attack, c.health, c.speed, c.defence,
            c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
            uc.weapon_id, uc.armor_id, uc.shield_id
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id
        JOIN card c ON uc.card_id = c.card_id
        WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Support'
        """
        params = (team_id, user_id)
       
        if exclude_card_id:
            query += " AND c.card_id != %s"
            params += (exclude_card_id,)
           
        support_cards = await execute_query_with_retry(query, params)
       
        # Initialize total boosts
        total_flat_attack = 0
        total_flat_health = 0
        total_flat_speed = 0
        total_flat_defence = 0
       
        total_pct_attack = 0
        total_pct_health = 0
        total_pct_speed = 0
        total_pct_defence = 0
       
        # Calculate total boosts from all support cards and their equipment
        for support in support_cards:
            # Add support card flat boosts
            total_flat_attack += support['attack'] or 0
            total_flat_health += support['health'] or 0
            total_flat_speed += support['speed'] or 0
            total_flat_defence += support['defence'] or 0
           
            # Add support card percentage boosts
            total_pct_attack += support['attack_percentage'] or 0
            total_pct_health += support['health_percentage'] or 0
            total_pct_speed += support['speed_percentage'] or 0
            total_pct_defence += support['defence_percentage'] or 0
           
            # Add equipment boosts
            equipment_types = [
                support['weapon_id'],
                support['armor_id'],
                support['shield_id']
            ]
           
            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage,
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details = await execute_query_with_retry(query, (eq_id,))
                   
                    if eq_details:
                        eq_details = eq_details[0]
                        # Add equipment flat boosts
                        total_flat_attack += eq_details.get('attack_boost', 0) or 0
                        total_flat_health += eq_details.get('health_boost', 0) or 0
                        total_flat_speed += eq_details.get('speed_boost', 0) or 0
                        total_flat_defence += eq_details.get('defense_boost', 0) or 0
                       
                        # Add equipment percentage boosts
                        total_pct_attack += eq_details.get('attack_percentage', 0) or 0
                        total_pct_health += eq_details.get('health_percentage', 0) or 0
                        total_pct_speed += eq_details.get('speed_percentage', 0) or 0
                        total_pct_defence += eq_details.get('defense_percentage', 0) or 0
       
        return {
            'flat_attack': total_flat_attack,
            'flat_health': total_flat_health,
            'flat_speed': total_flat_speed,
            'flat_defence': total_flat_defence,
            'pct_attack': total_pct_attack,
            'pct_health': total_pct_health,
            'pct_speed': total_pct_speed,
            'pct_defence': total_pct_defence
        }
   
    async def reset_offense_cards_to_base(self, team_id, user_id):
        """Reset all offense cards in the team to their base stats"""
        query = """
        UPDATE user_cards uc
        JOIN team_cards tc ON uc.card_id = tc.card_id
        JOIN card c ON uc.card_id = c.card_id
        SET
            uc.attack_stat = uc.base_attack,
            uc.health_stat = uc.base_health,
            uc.speed_stat = uc.base_speed,
            uc.defence_stat = uc.base_defence
        WHERE
            tc.team_id = %s AND
            uc.user_id = %s AND
            c.type_card = 'Fighter'
        """
        await execute_query_with_retry(query, (team_id, user_id))
   
    async def apply_support_boosts_to_offense(self, team_id, user_id, boosts):
        """Apply the given boosts to all offense cards in the team"""
        # Apply flat boosts first
        query = """
        UPDATE user_cards uc
        JOIN team_cards tc ON uc.card_id = tc.card_id
        JOIN card c ON uc.card_id = c.card_id
        SET
            uc.attack_stat = uc.attack_stat + %s,
            uc.health_stat = uc.health_stat + %s,
            uc.speed_stat = uc.speed_stat + %s,
            uc.defence_stat = uc.defence_stat + %s
        WHERE
            tc.team_id = %s AND
            uc.user_id = %s AND
            c.type_card = 'Fighter'
        """
        await execute_query_with_retry(query, (
            boosts['flat_attack'],
            boosts['flat_health'],
            boosts['flat_speed'],
            boosts['flat_defence'],
            team_id,
            user_id
        ))
       
        # Then apply percentage boosts
        query = """
        UPDATE user_cards uc
        JOIN team_cards tc ON uc.card_id = tc.card_id
        JOIN card c ON uc.card_id = c.card_id
        SET
            uc.attack_stat = uc.attack_stat * (1 + %s/100),
            uc.health_stat = uc.health_stat * (1 + %s/100),
            uc.speed_stat = uc.speed_stat * (1 + %s/100),
            uc.defence_stat = uc.defence_stat * (1 + %s/100)
        WHERE
            tc.team_id = %s AND
            uc.user_id = %s AND
            c.type_card = 'Fighter'
        """
        await execute_query_with_retry(query, (
            boosts['pct_attack'],
            boosts['pct_health'],
            boosts['pct_speed'],
            boosts['pct_defence'],
            team_id,
            user_id
        ))
   
    async def apply_support_boosts_to_single_offense(self, user_id, card_id, boosts):
        """Apply the given boosts to a single offense card"""
        # Apply flat boosts first
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
        await execute_query_with_retry(query, (
            boosts['flat_attack'],
            boosts['flat_health'],
            boosts['flat_speed'],
            boosts['flat_defence'],
            user_id,
            card_id
        ))
       
        # Then apply percentage boosts
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
        await execute_query_with_retry(query, (
            boosts['pct_attack'],
            boosts['pct_health'],
            boosts['pct_speed'],
            boosts['pct_defence'],
            user_id,
            card_id
        ))
   
    async def apply_support_boosts_to_single_card(self, card_id, total_boosts):
        """Apply support boosts to a single offense card"""
        try:
            # Extract boosts from the total_boosts dictionary
            flat_attack = total_boosts.get('flat_attack', 0)
            flat_health = total_boosts.get('flat_health', 0)
            flat_speed = total_boosts.get('flat_speed', 0)
            flat_defence = total_boosts.get('flat_defence', 0)
           
            pct_attack = total_boosts.get('pct_attack', 0)
            pct_health = total_boosts.get('pct_health', 0)
            pct_speed = total_boosts.get('pct_speed', 0)
            pct_defence = total_boosts.get('pct_defence', 0)
           
            # Apply flat boosts
            flat_boost_query = """
            UPDATE user_cards
            SET attack_stat = attack_stat + %s,
                health_stat = health_stat + %s,
                speed_stat = speed_stat + %s,
                defence_stat = defence_stat + %s
            WHERE id_ligneusercards = %s
            """
            await execute_query_with_retry(flat_boost_query, (
                flat_attack, flat_health, flat_speed, flat_defence, card_id
            ))
           
            # Apply percentage boosts
            pct_boost_query = """
            UPDATE user_cards
            SET attack_stat = attack_stat * (1 + %s/100),
                health_stat = health_stat * (1 + %s/100),
                speed_stat = speed_stat * (1 + %s/100),
                defence_stat = defence_stat * (1 + %s/100)
            WHERE id_ligneusercards = %s
            """
            await execute_query_with_retry(pct_boost_query, (
                pct_attack, pct_health, pct_speed, pct_defence, card_id
            ))
        except Exception as e:
            print(f"Error applying support boosts to single card: {e}")
    @commands.command(name="exchange", aliases=["ex", "exch"])
    @command_cooldown(rate=1, per=3.0, type=commands.BucketType.user)
    async def exchange(self, ctx, firstcap, secondcap, amount: int = 1):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Validate amount
            if amount < 1:
                await ctx.send(_tr(
                    lang,
                    "Amount must be a positive number.",
                    "يجب أن تكون الكمية رقماً موجباً.",
                    "La quantité doit être un nombre positif."
                ))
                return

            # Capsule to product_id mapping
            capsule_map = {
                'acap': 4,  # Attack capsule
                'scap': 5,  # Speed capsule
                'hcap': 6,  # Health capsule
                'dcap': 7   # Defence capsule
            }

            # Validate capsule types
            if firstcap.lower() not in capsule_map:
                await ctx.send(_tr(
                    lang,
                    "Invalid first capsule type. Use acap, scap, hcap, or dcap.",
                    "نوع الكبسولة الأولى غير صالح. استخدم acap أو scap أو hcap أو dcap.",
                    "Type de première capsule invalide. Utilisez acap, scap, hcap ou dcap."
                ))
                return

            if secondcap.lower() not in capsule_map:
                await ctx.send(_tr(
                    lang,
                    "Invalid second capsule type. Use acap, scap, hcap, or dcap.",
                    "نوع الكبسولة الثانية غير صالح. استخدم acap أو scap أو hcap أو dcap.",
                    "Type de seconde capsule invalide. Utilisez acap, scap, hcap ou dcap."
                ))
                return

            if firstcap.lower() == secondcap.lower():
                await ctx.send(_tr(
                    lang,
                    "Cannot exchange the same capsule type.",
                    "لا يمكن استبدال نفس نوع الكبسولة.",
                    "Impossible d'échanger le même type de capsule."
                ))
                return

            # Get product IDs
            first_product_id = capsule_map[firstcap.lower()]
            second_product_id = capsule_map[secondcap.lower()]

            # Calculate required amount to take off from firstcap (3:1 ratio)
            required_amount = amount * 3

            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to register first.",
                    "يجب أن تقوم بالتسجيل أولاً.",
                    "Vous devez d'abord vous inscrire."
                ))
                return

            user_id = user_result[0]['user_id']

            # Check for active trades
            trade_query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            trade_result = await execute_query_with_retry(trade_query, (user_id, user_id))
            if trade_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, finish it or wait for its cooldown before using this command. If there's any issue, open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d'assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have an inventory.",
                    "ليس لديك جرد.",
                    "Vous n'avez pas d'inventaire."
                ))
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check if user has enough of firstcap
            firstcap_query = """
            SELECT ip.*, p.product_icon, p.product_name
            FROM inventory_product ip
            JOIN product p ON ip.product_id = p.product_id
            WHERE ip.inventory_id = %s AND ip.product_id = %s
            """
            firstcap_result = await execute_query_with_retry(firstcap_query, (inventory_id, first_product_id))
            if not firstcap_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have any {firstcap.upper()} in your inventory.",
                    f"لا تملك أي {firstcap.upper()} في جردك.",
                    f"Vous n'avez aucun {firstcap.upper()} dans votre inventaire."
                ))
                return

            firstcap_item = firstcap_result[0]

            # Check if user has enough quantity
            if firstcap_item['quantiter'] < required_amount:
                await ctx.send(_tr(
                    lang,
                    f"You need {required_amount} {firstcap.upper()} to exchange for {amount} {secondcap.upper()}, but you only have {firstcap_item['quantiter']}.",
                    f"تحتاج إلى {required_amount} من {firstcap.upper()} لتبديلها بـ {amount} من {secondcap.upper()}، لكن لديك فقط {firstcap_item['quantiter']}.",
                    f"Il vous faut {required_amount} {firstcap.upper()} pour échanger contre {amount} {secondcap.upper()}, mais vous n'en avez que {firstcap_item['quantiter']}."
                ))
                return

            # Update firstcap quantity (reduce by required_amount)
            if firstcap_item['quantiter'] == required_amount:
                delete_firstcap_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(delete_firstcap_query, (firstcap_item['id_ligneinventoryproduct'],))
            else:
                update_firstcap_query = "UPDATE inventory_product SET quantiter = quantiter - %s WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(update_firstcap_query, (required_amount, firstcap_item['id_ligneinventoryproduct']))

            # Check if user already has secondcap in inventory
            secondcap_query = """
            SELECT ip.*, p.product_icon, p.product_name
            FROM inventory_product ip
            JOIN product p ON ip.product_id = p.product_id
            WHERE ip.inventory_id = %s AND ip.product_id = %s
            """
            secondcap_result = await execute_query_with_retry(secondcap_query, (inventory_id, second_product_id))

            if secondcap_result:
                # Update existing secondcap quantity
                secondcap_item = secondcap_result[0]
                update_secondcap_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(update_secondcap_query, (amount, secondcap_item['id_ligneinventoryproduct']))
                secondcap_icon = secondcap_item['product_icon']
            else:
                # Insert new row for secondcap
                insert_secondcap_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_secondcap_query, (inventory_id, second_product_id, amount))

                # Get product icon for response
                product_query = "SELECT product_icon FROM product WHERE product_id = %s"
                product_result = await execute_query_with_retry(product_query, (second_product_id,))
                secondcap_icon = product_result[0]['product_icon'] if product_result else secondcap.upper()

            # Create response message
            firstcap_icon = firstcap_item['product_icon']
            await ctx.send(_tr(
                lang,
                f"Successfully exchanged {required_amount}x {firstcap_icon} {firstcap.upper()} for {amount}x {secondcap_icon} {secondcap.upper()}!",
                f"تم بنجاح استبدال {required_amount}× {firstcap_icon} {firstcap.upper()} مقابل {amount}× {secondcap_icon} {secondcap.upper()}!",
                f"Échange réussi de {required_amount}× {firstcap_icon} {firstcap.upper()} contre {amount}× {secondcap_icon} {secondcap.upper()} !"
            ))

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))

    @commands.command(name="equip", aliases=["eq", "ep"])
    async def equip(self, ctx, *, args):
        lang = await get_user_multilanguage(ctx.author.id)  # 0=en, 1=ar, 2=fr

        # Split args by comma
        parts = [part.strip() for part in args.split(',')]
        if len(parts) < 2:
            await ctx.send(_tr(
                lang,
                "Usage: sl equip Sword of Power, Mighty Warrior",
                "طريقة الاستخدام: sl equip Sword of Power, Mighty Warrior",
                "Utilisation : sl equip Sword of Power, Mighty Warrior"
            ))
            return

        # The second part is the card name, the first part is the weapon name
        weapon_name = parts[0]
        card_name = parts[1]

        if not weapon_name or not card_name:
            await ctx.send(_tr(
                lang,
                "Please provide both weapon and card names. Example: sl equip Sword of Power, Mighty Warrior",
                "يرجى إدخال اسمي السلاح والبطاقة. مثال: sl equip Sword of Power, Mighty Warrior",
                "Veuillez fournir le nom de l’arme et de la carte. Exemple : sl equip Sword of Power, Mighty Warrior"
            ))
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to be registered to use this command.",
                    "يجب أن تقوم بالتسجيل لاستخدام هذا الأمر.",
                    "Vous devez être inscrit pour utiliser cette commande."
                ))
                return

            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d'assistance."
                ))
                return

            # Market session lock
            market_session_query = """
            SELECT user_id FROM market_session WHERE user_id = %s
            """
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have an inventory.",
                    "ليس لديك جرد.",
                    "Vous n'avez pas d'inventaire."
                ))
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check weapon
            weapon_query = "SELECT * FROM weapon WHERE weapon_name LIKE %s"
            weapon_result = await execute_query_with_retry(weapon_query, (f'%{weapon_name}%',))

            if not weapon_result:
                await ctx.send(_tr(
                    lang,
                    "No weapons found matching the name you specified.",
                    "لا توجد أي أسلحة تطابق الاسم الذي أدخلته.",
                    "Aucune arme trouvée correspondant au nom indiqué."
                ))
                return

            # If multiple weapons found, show embed
            if len(weapon_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Weapons Found", "تم العثور على عدة أسلحة", "Plusieurs armes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple weapons matching your search. Please specify which one you'd like to equip:",
                        "عثرتُ على عدة أسلحة تطابق بحثك. يرجى تحديد أيها تريد تجهيزها:",
                        "J’ai trouvé plusieurs armes correspondant à votre recherche. Veuillez préciser laquelle vous souhaitez équiper :"
                    ),
                    color=discord.Color.blue()
                )
                weapon_list = "\n".join([f"• {weapon['weapon_name']}" for weapon in weapon_result])
                embed.add_field(
                    name=_tr(lang, "Matching Weapons", "الأسلحة المطابقة", "Armes correspondantes"),
                    value=weapon_list,
                    inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact weapon name.",
                    "يرجى إعادة استخدام الأمر باسم السلاح الدقيق.",
                    "Veuillez relancer la commande avec le nom exact de l’arme."
                ))
                await ctx.send(embed=embed)
                return

            weapon = weapon_result[0]

            # Check if user has the weapon
            inv_weapon_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            inv_weapon_result = await execute_query_with_retry(inv_weapon_query, (inventory_id, weapon['weapon_id']))

            if not inv_weapon_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have the weapon '{weapon_name}' in your inventory.",
                    f"لا تملك السلاح '{weapon_name}' في جردك.",
                    f"Vous n’avez pas l’arme « {weapon_name} » dans votre inventaire."
                ))
                return

            # Check card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name LIKE %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, f'%{card_name}%'))

            if not user_card_result:
                await ctx.send(_tr(
                    lang,
                    "No cards found matching the name you specified in your collection.",
                    "لا توجد بطاقات في مجموعتك تطابق الاسم الذي أدخلته.",
                    "Aucune carte trouvée dans votre collection correspondant au nom indiqué."
                ))
                return

            # If multiple cards found, show embed
            if len(user_card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to equip the weapon to:",
                        "عثرتُ على عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد تجهيز السلاح لها:",
                        "J’ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser celle sur laquelle équiper l’arme :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in user_card_result])
                embed.add_field(
                    name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                    value=card_list,
                    inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact card name.",
                    "يرجى إعادة استخدام الأمر باسم البطاقة الدقيق.",
                    "Veuillez relancer la commande avec le nom exact de la carte."
                ))
                await ctx.send(embed=embed)
                return

            user_card = user_card_result[0]

            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = await execute_query_with_retry(card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(_tr(
                    lang,
                    f"Card '{card_name}' not found.",
                    f"البطاقة '{card_name}' غير موجودة.",
                    f"Carte « {card_name} » introuvable."
                ))
                return

            card = card_result[0]

            # Stat session lock
            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card['card_id']))
            if stat_status_result:
                await ctx.send(_tr(
                    lang,
                    "This card is currently being adjusted. Please wait until the process is completed.",
                    "يتم تعديل هذه البطاقة حالياً. يرجى الانتظار حتى يكتمل الإجراء.",
                    "Cette carte est en cours d’ajustement. Veuillez attendre la fin du processus."
                ))
                return

            # Check weapon class compatibility
            weapon_class_query = "SELECT card_class_id FROM weapon_class WHERE weapon_id = %s"
            weapon_class_result = await execute_query_with_retry(weapon_class_query, (weapon['weapon_id'],))

            if not weapon_class_result:
                await ctx.send(_tr(
                    lang,
                    "This weapon has no class compatibility defined.",
                    "لا يوجد توافق صنفي محدد لهذه السلاح.",
                    "Cette arme n’a pas de compatibilité de classe définie."
                ))
                return

            # Check if card class matches any of the weapon's compatible classes
            is_compatible = any(weapon_class['card_class_id'] == card['card_class_id'] for weapon_class in weapon_class_result)
            if not is_compatible:
                await ctx.send(_tr(
                    lang,
                    f"The weapon '{weapon_name}' is not compatible with the card '{card_name}'.",
                    f"السلاح '{weapon_name}' غير متوافق مع البطاقة '{card_name}'.",
                    f"L’arme « {weapon_name} » n’est pas compatible avec la carte « {card_name} »."
                ))
                return

            if weapon['weapon_rank'] == 1:
                # Check card type and class for weapon equipping
                if card['type_card'] == 'Support':
                    if card['card_class_id'] == 5:  # Tanker class assumed
                        await ctx.send(_tr(
                            lang,
                            "Support Tanker cards cannot equip weapons.",
                            "بطاقات الدعم من فئة Tanker لا يمكنها تجهيز أسلحة.",
                            "Les cartes Support Tanker ne peuvent pas équiper d’armes."
                        ))
                        return

            # For weapon_rank 2 (shield) and 3 (armor)
            elif weapon['weapon_rank'] in [2, 3]:
                # Check card type and class for shield/armor equipping
                if card['type_card'] == 'Support':
                    if card['card_class_id'] != 5:  # Non-Tanker Support cards
                        await ctx.send(_tr(
                            lang,
                            "Only Tanker Support cards can equip shields or armor.",
                            "فقط بطاقات الدعم من فئة Tanker يمكنها تجهيز الدروع أو الدروع الواقية.",
                            "Seules les cartes Support Tanker peuvent équiper des boucliers ou des armures."
                        ))
                        return

            # Check equipment slot based on weapon rank
            if weapon['weapon_rank'] == 1:
                if user_card['weapon_id'] is not None:
                    await ctx.send(_tr(
                        lang,
                        "This card already has a weapon equipped. Please unequip it first.",
                        "هذه البطاقة لديها سلاح مجهز بالفعل. يرجى نزعه أولاً.",
                        "Cette carte a déjà une arme équipée. Veuillez la retirer d’abord."
                    ))
                    return
            elif weapon['weapon_rank'] == 2:
                if user_card['shield_id'] is not None:
                    await ctx.send(_tr(
                        lang,
                        "This card already has a shield equipped. Please unequip it first.",
                        "هذه البطاقة لديها درع مجهز بالفعل. يرجى نزعه أولاً.",
                        "Cette carte a déjà un bouclier équipé. Veuillez le retirer d’abord."
                    ))
                    return
            elif weapon['weapon_rank'] == 3:
                if user_card['armor_id'] is not None:
                    await ctx.send(_tr(
                        lang,
                        "This card already has armor equipped. Please unequip it first.",
                        "هذه البطاقة لديها درع جسدي مجهز بالفعل. يرجى نزعه أولاً.",
                        "Cette carte a déjà une armure équipée. Veuillez la retirer d’abord."
                    ))
                    return

            # Update inventory quantity
            if inv_weapon_result[0]['quantiter'] == 1:
                delete_inv_weapon_query = "DELETE FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
                await execute_query_with_retry(delete_inv_weapon_query, (inventory_id, weapon['weapon_id']))
            else:
                update_inv_weapon_query = "UPDATE inventory_weapon SET quantiter = quantiter - 1 WHERE inventory_id = %s AND weapon_id = %s"
                await execute_query_with_retry(update_inv_weapon_query, (inventory_id, weapon['weapon_id']))

            # Get weapon boosts for the new weapon
            flat_attack = weapon.get('attack_boost', 0) or 0
            flat_health = weapon.get('health_boost', 0) or 0
            flat_speed = weapon.get('speed_boost', 0) or 0
            flat_defence = weapon.get('defense_boost', 0) or 0

            pct_attack = weapon.get('attack_percentage', 0) or 0
            pct_health = weapon.get('health_percentage', 0) or 0
            pct_speed = weapon.get('speed_percentage', 0) or 0
            pct_defence = weapon.get('defense_percentage', 0) or 0

            # Update equipment slot based on weapon rank
            equipment_column = {
                1: 'weapon_id',
                2: 'shield_id',
                3: 'armor_id'
            }.get(weapon['weapon_rank'])

            # First, update the equipment slot for the card

            # Handle equipping based on card type
            if card['type_card'] == 'Fighter':
                # Check team and supports
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))

                has_support_in_team = False
                team_id = None

                if team_result:
                    team_id = team_result[0]['team_id']
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    if team_card_result:
                        support_query = """
                        SELECT tc.*
                        FROM team_cards tc
                        JOIN card c ON tc.card_id = c.card_id
                        WHERE tc.team_id = %s AND c.type_card = 'Support'
                        """
                        support_result = await execute_query_with_retry(support_query, (team_id,))
                        if support_result:
                            has_support_in_team = True

                # IMPROVED HANDLING FOR OFFENSE CARDS
                if has_support_in_team:
                    # 1) Remove existing equipment buffs from base to get true base
                    equipment_query = """
                    SELECT weapon_id, shield_id, armor_id
                    FROM user_cards
                    WHERE id_ligneusercards = %s
                    """
                    equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))

                    get_base_stats_query = """
                    SELECT base_attack, base_health, base_speed, base_defence
                    FROM user_cards
                    WHERE id_ligneusercards = %s
                    """
                    base_stats_result = await execute_query_with_retry(get_base_stats_query, (user_card['id_ligneusercards'],))

                    if base_stats_result:
                        original_base_stats = base_stats_result[0]
                        true_base_attack = original_base_stats.get('base_attack', 0) or 0
                        true_base_health = original_base_stats.get('base_health', 0) or 0
                        true_base_speed  = original_base_stats.get('base_speed', 0)  or 0
                        true_base_defence= original_base_stats.get('base_defence', 0) or 0

                        if equipment_result:
                            equipment = equipment_result[0]
                            existing_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            equipment_slots = []
                            if equipment.get('weapon_id') and weapon['weapon_rank'] != 1:
                                equipment_slots.append(equipment.get('weapon_id'))
                            if equipment.get('shield_id') and weapon['weapon_rank'] != 2:
                                equipment_slots.append(equipment.get('shield_id'))
                            if equipment.get('armor_id') and weapon['weapon_rank'] != 3:
                                equipment_slots.append(equipment.get('armor_id'))

                            for eq_id in equipment_slots:
                                if eq_id:
                                    eq_query = """
                                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                        attack_percentage, health_percentage,
                                        speed_percentage, defense_percentage
                                    FROM weapon
                                    WHERE weapon_id = %s
                                    """
                                    eq_details = await execute_query_with_retry(eq_query, (eq_id,))
                                    if eq_details:
                                        eq_details = eq_details[0]
                                        existing_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        existing_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        existing_equipment_boosts['flat_speed']  += eq_details.get('speed_boost', 0)  or 0
                                        existing_equipment_boosts['flat_defence']+= eq_details.get('defense_boost', 0) or 0
                                        existing_equipment_boosts['pct_attack']  += eq_details.get('attack_percentage', 0) or 0
                                        existing_equipment_boosts['pct_health']  += eq_details.get('health_percentage', 0) or 0
                                        existing_equipment_boosts['pct_speed']   += eq_details.get('speed_percentage', 0) or 0
                                        existing_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

                            if existing_equipment_boosts['pct_attack'] > 0:
                                true_base_attack /= (1 + existing_equipment_boosts['pct_attack']/100)
                            if existing_equipment_boosts['pct_health'] > 0:
                                true_base_health /= (1 + existing_equipment_boosts['pct_health']/100)
                            if existing_equipment_boosts['pct_speed'] > 0:
                                true_base_speed  /= (1 + existing_equipment_boosts['pct_speed']/100)
                            if existing_equipment_boosts['pct_defence'] > 0:
                                true_base_defence/= (1 + existing_equipment_boosts['pct_defence']/100)

                            true_base_attack  -= existing_equipment_boosts['flat_attack']
                            true_base_health  -= existing_equipment_boosts['flat_health']
                            true_base_speed   -= existing_equipment_boosts['flat_speed']
                            true_base_defence -= existing_equipment_boosts['flat_defence']

                        # 2) Reset to true base
                        reset_query = """
                        UPDATE user_cards
                        SET base_attack = %s,
                            base_health = %s,
                            base_speed  = %s,
                            base_defence= %s,
                            attack_stat = %s,
                            health_stat = %s,
                            speed_stat  = %s,
                            defence_stat= %s
                        WHERE id_ligneusercards = %s
                        """
                        await execute_query_with_retry(reset_query, (
                            true_base_attack, true_base_health, true_base_speed, true_base_defence,
                            true_base_attack, true_base_health, true_base_speed, true_base_defence,
                            user_card['id_ligneusercards']
                        ))

                        # Equip the new item
                        update_equipment_query = f"UPDATE user_cards SET {equipment_column} = %s WHERE id_ligneusercards = %s"
                        await execute_query_with_retry(update_equipment_query, (weapon['weapon_id'], user_card['id_ligneusercards']))

                        # 3) Rebuild total equipment boosts including new item
                        equipment_query = """
                        SELECT weapon_id, shield_id, armor_id
                        FROM user_cards
                        WHERE id_ligneusercards = %s
                        """
                        equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))

                        if equipment_result:
                            equipment = equipment_result[0]
                            total_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            equipment_slots = [equipment.get('weapon_id'), equipment.get('shield_id'), equipment.get('armor_id')]
                            for eq_id in equipment_slots:
                                if eq_id:
                                    eq_query = """
                                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                        attack_percentage, health_percentage,
                                        speed_percentage, defense_percentage
                                    FROM weapon
                                    WHERE weapon_id = %s
                                    """
                                    eq_details = await execute_query_with_retry(eq_query, (eq_id,))
                                    if eq_details:
                                        eq_details = eq_details[0]
                                        total_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        total_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        total_equipment_boosts['flat_speed']  += eq_details.get('speed_boost', 0)  or 0
                                        total_equipment_boosts['flat_defence']+= eq_details.get('defense_boost', 0) or 0
                                        total_equipment_boosts['pct_attack']  += eq_details.get('attack_percentage', 0) or 0
                                        total_equipment_boosts['pct_health']  += eq_details.get('health_percentage', 0) or 0
                                        total_equipment_boosts['pct_speed']   += eq_details.get('speed_percentage', 0) or 0
                                        total_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

                            # 4) Apply equipment boosts to base stats
                            base_flat_query = """
                            UPDATE user_cards
                            SET base_attack = base_attack + %s,
                                base_health = base_health + %s,
                                base_speed  = base_speed + %s,
                                base_defence= base_defence + %s
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(base_flat_query, (
                                total_equipment_boosts['flat_attack'],
                                total_equipment_boosts['flat_health'],
                                total_equipment_boosts['flat_speed'],
                                total_equipment_boosts['flat_defence'],
                                user_card['id_ligneusercards']
                            ))

                            base_pct_query = """
                            UPDATE user_cards
                            SET base_attack = base_attack * (1 + %s/100),
                                base_health = base_health * (1 + %s/100),
                                base_speed  = base_speed  * (1 + %s/100),
                                base_defence= base_defence* (1 + %s/100)
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(base_pct_query, (
                                total_equipment_boosts['pct_attack'],
                                total_equipment_boosts['pct_health'],
                                total_equipment_boosts['pct_speed'],
                                total_equipment_boosts['pct_defence'],
                                user_card['id_ligneusercards']
                            ))

                            # Sync current stats to base (before supports)
                            update_stats_query = """
                            UPDATE user_cards
                            SET attack_stat = base_attack,
                                health_stat = base_health,
                                speed_stat  = base_speed,
                                defence_stat= base_defence
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(update_stats_query, (user_card['id_ligneusercards'],))

                        # 5) Recalculate support boosts
                        total_boosts = await self.calculate_total_support_boosts(team_id, user_id)

                        # 6) Apply support boosts to this specific offense card
                        await self.apply_support_boosts_to_single_card(user_card['id_ligneusercards'], total_boosts)

                        # 7) Update power
                        await execute_query_with_retry(
                            "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                            (user_card['id_ligneusercards'],)
                        )
                else:
                    # Fighter without supports in team
                    equipment_query = """
                    SELECT weapon_id, shield_id, armor_id
                    FROM user_cards
                    WHERE id_ligneusercards = %s
                    """
                    equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))

                    get_base_stats_query = """
                    SELECT base_attack, base_health, base_speed, base_defence,
                        attack_stat, health_stat, speed_stat, defence_stat
                    FROM user_cards
                    WHERE id_ligneusercards = %s
                    """
                    base_stats_result = await execute_query_with_retry(get_base_stats_query, (user_card['id_ligneusercards'],))

                    if base_stats_result:
                        original_stats = base_stats_result[0]
                        true_base_attack = original_stats.get('base_attack', 0) or 0
                        true_base_health = original_stats.get('base_health', 0) or 0
                        true_base_speed  = original_stats.get('base_speed', 0)  or 0
                        true_base_defence= original_stats.get('base_defence', 0) or 0

                        true_attack  = original_stats.get('attack_stat', 0) or 0
                        true_health  = original_stats.get('health_stat', 0) or 0
                        true_speed   = original_stats.get('speed_stat', 0)  or 0
                        true_defence = original_stats.get('defence_stat', 0) or 0

                        if equipment_result:
                            equipment = equipment_result[0]
                            existing_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            equipment_slots = []
                            if equipment.get('weapon_id') and weapon['weapon_rank'] != 1:
                                equipment_slots.append(equipment.get('weapon_id'))
                            if equipment.get('shield_id') and weapon['weapon_rank'] != 2:
                                equipment_slots.append(equipment.get('shield_id'))
                            if equipment.get('armor_id') and weapon['weapon_rank'] != 3:
                                equipment_slots.append(equipment.get('armor_id'))

                            for eq_id in equipment_slots:
                                if eq_id:
                                    eq_query = """
                                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                        attack_percentage, health_percentage,
                                        speed_percentage, defense_percentage
                                    FROM weapon
                                    WHERE weapon_id = %s
                                    """
                                    eq_details = await execute_query_with_retry(eq_query, (eq_id,))
                                    if eq_details:
                                        eq_details = eq_details[0]
                                        existing_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        existing_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        existing_equipment_boosts['flat_speed']  += eq_details.get('speed_boost', 0)  or 0
                                        existing_equipment_boosts['flat_defence']+= eq_details.get('defense_boost', 0) or 0
                                        existing_equipment_boosts['pct_attack']  += eq_details.get('attack_percentage', 0) or 0
                                        existing_equipment_boosts['pct_health']  += eq_details.get('health_percentage', 0) or 0
                                        existing_equipment_boosts['pct_speed']   += eq_details.get('speed_percentage', 0) or 0
                                        existing_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

                            if existing_equipment_boosts['pct_attack'] > 0:
                                true_base_attack /= (1 + existing_equipment_boosts['pct_attack']/100)
                                true_attack      /= (1 + existing_equipment_boosts['pct_attack']/100)
                            if existing_equipment_boosts['pct_health'] > 0:
                                true_base_health /= (1 + existing_equipment_boosts['pct_health']/100)
                                true_health      /= (1 + existing_equipment_boosts['pct_health']/100)
                            if existing_equipment_boosts['pct_speed'] > 0:
                                true_base_speed  /= (1 + existing_equipment_boosts['pct_speed']/100)
                                true_speed       /= (1 + existing_equipment_boosts['pct_speed']/100)
                            if existing_equipment_boosts['pct_defence'] > 0:
                                true_base_defence/= (1 + existing_equipment_boosts['pct_defence']/100)
                                true_defence     /= (1 + existing_equipment_boosts['pct_defence']/100)

                            true_base_attack  -= existing_equipment_boosts['flat_attack']
                            true_base_health  -= existing_equipment_boosts['flat_health']
                            true_base_speed   -= existing_equipment_boosts['flat_speed']
                            true_base_defence -= existing_equipment_boosts['flat_defence']

                            true_attack  -= existing_equipment_boosts['flat_attack']
                            true_health  -= existing_equipment_boosts['flat_health']
                            true_speed   -= existing_equipment_boosts['flat_speed']
                            true_defence -= existing_equipment_boosts['flat_defence']

                        # 2) Reset to true stats
                        reset_query = """
                        UPDATE user_cards
                        SET base_attack = %s,
                            base_health = %s,
                            base_speed  = %s,
                            base_defence= %s,
                            attack_stat = %s,
                            health_stat = %s,
                            speed_stat  = %s,
                            defence_stat= %s
                        WHERE id_ligneusercards = %s
                        """
                        await execute_query_with_retry(reset_query, (
                            true_base_attack, true_base_health, true_base_speed, true_base_defence,
                            true_attack, true_health, true_speed, true_defence,
                            user_card['id_ligneusercards']
                        ))

                        # Equip new item
                        update_equipment_query = f"UPDATE user_cards SET {equipment_column} = %s WHERE id_ligneusercards = %s"
                        await execute_query_with_retry(update_equipment_query, (weapon['weapon_id'], user_card['id_ligneusercards']))

                        # 3) Recalculate total equipment boosts including new item
                        equipment_query = """
                        SELECT weapon_id, shield_id, armor_id
                        FROM user_cards
                        WHERE id_ligneusercards = %s
                        """
                        equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))

                        if equipment_result:
                            equipment = equipment_result[0]
                            total_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            equipment_slots = [equipment.get('weapon_id'), equipment.get('shield_id'), equipment.get('armor_id')]
                            for eq_id in equipment_slots:
                                if eq_id:
                                    eq_query = """
                                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                        attack_percentage, health_percentage,
                                        speed_percentage, defense_percentage
                                    FROM weapon
                                    WHERE weapon_id = %s
                                    """
                                    eq_details = await execute_query_with_retry(eq_query, (eq_id,))
                                    if eq_details:
                                        eq_details = eq_details[0]
                                        total_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        total_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        total_equipment_boosts['flat_speed']  += eq_details.get('speed_boost', 0)  or 0
                                        total_equipment_boosts['flat_defence']+= eq_details.get('defense_boost', 0) or 0
                                        total_equipment_boosts['pct_attack']  += eq_details.get('attack_percentage', 0) or 0
                                        total_equipment_boosts['pct_health']  += eq_details.get('health_percentage', 0) or 0
                                        total_equipment_boosts['pct_speed']   += eq_details.get('speed_percentage', 0) or 0
                                        total_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

                            # 4) Apply boosts to both base and current stats
                            flat_boost_query = """
                            UPDATE user_cards
                            SET attack_stat = attack_stat + %s,
                                health_stat = health_stat + %s,
                                speed_stat  = speed_stat  + %s,
                                defence_stat= defence_stat+ %s,
                                base_attack = base_attack + %s,
                                base_health = base_health + %s,
                                base_speed  = base_speed  + %s,
                                base_defence= base_defence+ %s
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(flat_boost_query, (
                                total_equipment_boosts['flat_attack'],
                                total_equipment_boosts['flat_health'],
                                total_equipment_boosts['flat_speed'],
                                total_equipment_boosts['flat_defence'],
                                total_equipment_boosts['flat_attack'],
                                total_equipment_boosts['flat_health'],
                                total_equipment_boosts['flat_speed'],
                                total_equipment_boosts['flat_defence'],
                                user_card['id_ligneusercards']
                            ))

                            pct_boost_query = """
                            UPDATE user_cards
                            SET attack_stat = attack_stat * (1 + %s/100),
                                health_stat = health_stat * (1 + %s/100),
                                speed_stat  = speed_stat  * (1 + %s/100),
                                defence_stat= defence_stat* (1 + %s/100),
                                base_attack = base_attack * (1 + %s/100),
                                base_health = base_health * (1 + %s/100),
                                base_speed  = base_speed  * (1 + %s/100),
                                base_defence= base_defence* (1 + %s/100)
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(pct_boost_query, (
                                total_equipment_boosts['pct_attack'],
                                total_equipment_boosts['pct_health'],
                                total_equipment_boosts['pct_speed'],
                                total_equipment_boosts['pct_defence'],
                                total_equipment_boosts['pct_attack'],
                                total_equipment_boosts['pct_health'],
                                total_equipment_boosts['pct_speed'],
                                total_equipment_boosts['pct_defence'],
                                user_card['id_ligneusercards']
                            ))

                            await execute_query_with_retry(
                                "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                                (user_card['id_ligneusercards'],)
                            )

            elif card['type_card'] == 'Support':
                # Update equipment slot
                update_equipment_query = f"UPDATE user_cards SET {equipment_column} = %s WHERE id_ligneusercards = %s"
                await execute_query_with_retry(update_equipment_query, (weapon['weapon_id'], user_card['id_ligneusercards']))

                # Remove existing equipment buffs (to get true stats)
                equipment_query = """
                SELECT weapon_id, shield_id, armor_id
                FROM user_cards
                WHERE id_ligneusercards = %s
                """
                equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))

                get_stats_query = """
                SELECT attack_stat, health_stat, speed_stat, defence_stat
                FROM user_cards
                WHERE id_ligneusercards = %s
                """
                stats_result = await execute_query_with_retry(get_stats_query, (user_card['id_ligneusercards'],))

                if stats_result:
                    original_stats = stats_result[0]
                    true_attack  = original_stats.get('attack_stat', 0) or 0
                    true_health  = original_stats.get('health_stat', 0) or 0
                    true_speed   = original_stats.get('speed_stat', 0)  or 0
                    true_defence = original_stats.get('defence_stat', 0) or 0

                    if equipment_result:
                        equipment = equipment_result[0]
                        existing_equipment_boosts = {
                            'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                            'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                        }
                        equipment_slots = []
                        if equipment.get('weapon_id') and weapon['weapon_rank'] != 1:
                            equipment_slots.append(equipment.get('weapon_id'))
                        if equipment.get('shield_id') and weapon['weapon_rank'] != 2:
                            equipment_slots.append(equipment.get('shield_id'))
                        if equipment.get('armor_id') and weapon['weapon_rank'] != 3:
                            equipment_slots.append(equipment.get('armor_id'))

                        for eq_id in equipment_slots:
                            if eq_id:
                                eq_query = """
                                SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                    attack_percentage, health_percentage,
                                    speed_percentage, defense_percentage
                                FROM weapon
                                WHERE weapon_id = %s
                                """
                                eq_details = await execute_query_with_retry(eq_query, (eq_id,))
                                if eq_details:
                                    eq_details = eq_details[0]
                                    existing_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                    existing_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                    existing_equipment_boosts['flat_speed']  += eq_details.get('speed_boost', 0)  or 0
                                    existing_equipment_boosts['flat_defence']+= eq_details.get('defense_boost', 0) or 0
                                    existing_equipment_boosts['pct_attack']  += eq_details.get('attack_percentage', 0) or 0
                                    existing_equipment_boosts['pct_health']  += eq_details.get('health_percentage', 0) or 0
                                    existing_equipment_boosts['pct_speed']   += eq_details.get('speed_percentage', 0) or 0
                                    existing_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

                    if existing_equipment_boosts['pct_attack'] > 0:
                        true_attack  /= (1 + existing_equipment_boosts['pct_attack']/100)
                    if existing_equipment_boosts['pct_health'] > 0:
                        true_health  /= (1 + existing_equipment_boosts['pct_health']/100)
                    if existing_equipment_boosts['pct_speed'] > 0:
                        true_speed   /= (1 + existing_equipment_boosts['pct_speed']/100)
                    if existing_equipment_boosts['pct_defence'] > 0:
                        true_defence /= (1 + existing_equipment_boosts['pct_defence']/100)

                    true_attack  -= existing_equipment_boosts['flat_attack']
                    true_health  -= existing_equipment_boosts['flat_health']
                    true_speed   -= existing_equipment_boosts['flat_speed']
                    true_defence -= existing_equipment_boosts['flat_defence']

                    # Reset stats
                    reset_query = """
                    UPDATE user_cards
                    SET attack_stat = %s,
                        health_stat = %s,
                        speed_stat  = %s,
                        defence_stat= %s
                    WHERE id_ligneusercards = %s
                    """
                    await execute_query_with_retry(reset_query, (
                        true_attack, true_health, true_speed, true_defence,
                        user_card['id_ligneusercards']
                    ))

                # Apply new equipment boosts to support card
                flat_boost_query = """
                UPDATE user_cards
                SET attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    speed_stat  = speed_stat  + %s,
                    defence_stat= defence_stat+ %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(flat_boost_query, (
                    flat_attack, flat_health, flat_speed, flat_defence,
                    user_card['id_ligneusercards']
                ))

                pct_boost_query = """
                UPDATE user_cards
                SET attack_stat = attack_stat * (1 + %s/100),
                    health_stat = health_stat * (1 + %s/100),
                    speed_stat  = speed_stat  * (1 + %s/100),
                    defence_stat= defence_stat* (1 + %s/100)
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(pct_boost_query, (
                    pct_attack, pct_health, pct_speed, pct_defence,
                    user_card['id_ligneusercards']
                ))

                # If support is in team, reapply boosts to fighters
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                if team_result:
                    team_id = team_result[0]['team_id']
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    if team_card_result:
                        reset_query = """
                        UPDATE user_cards uc
                        JOIN team_cards tc ON uc.card_id = tc.card_id
                        JOIN card c ON uc.card_id = c.card_id
                        SET
                            uc.attack_stat = uc.base_attack,
                            uc.health_stat = uc.base_health,
                            uc.speed_stat  = uc.base_speed,
                            uc.defence_stat= uc.base_defence
                        WHERE
                            tc.team_id = %s AND
                            uc.user_id = %s AND
                            c.type_card = 'Fighter'
                        """
                        await execute_query_with_retry(reset_query, (team_id, user_id))

                        total_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                        await self.apply_support_boosts_to_offense(team_id, user_id, total_boosts)

            await ctx.send(_tr(
                lang,
                f"Successfully equipped {weapon['weapon_name']} to {user_card['name']}!",
                f"تم تجهيز {weapon['weapon_name']} بنجاح للبطاقة {user_card['name']}!",
                f"{weapon['weapon_name']} a été équipé avec succès sur {user_card['name']} !"
            ))
        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))

    @commands.command(name="unequip", aliases=["ue", "uq"])
    async def unequip(self, ctx, *, args):
        lang = await get_user_multilanguage(ctx.author.id)  # 0=en, 1=ar, 2=fr

        # Split args by comma
        parts = [part.strip() for part in args.split(',')]
        if len(parts) < 2:
            await ctx.send(_tr(
                lang,
                "Usage: sl unequip Sword of Power, Mighty Warrior",
                "طريقة الاستخدام: sl unequip Sword of Power, Mighty Warrior",
                "Utilisation : sl unequip Sword of Power, Mighty Warrior"
            ))
            return

        # The second part is the card name, the first part is the weapon name
        weapon_name = parts[0]
        card_name = parts[1]

        if not weapon_name or not card_name:
            await ctx.send(_tr(
                lang,
                "Please provide both weapon and card names. Example: sl unequip Sword of Power, Mighty Warrior",
                "يرجى إدخال اسمي السلاح والبطاقة. مثال: sl unequip Sword of Power, Mighty Warrior",
                "Veuillez fournir le nom de l’arme et de la carte. Exemple : sl unequip Sword of Power, Mighty Warrior"
            ))
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to be registered to use this command.",
                    "يجب أن تقوم بالتسجيل لاستخدام هذا الأمر.",
                    "Vous devez être inscrit pour utiliser cette commande."
                ))
                return

            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have an inventory.",
                    "ليس لديك جرد.",
                    "Vous n'avez pas d'inventaire."
                ))
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check weapon
            weapon_query = "SELECT * FROM weapon WHERE weapon_name LIKE %s"
            weapon_result = await execute_query_with_retry(weapon_query, (f'%{weapon_name}%',))
            if not weapon_result:
                await ctx.send(_tr(
                    lang,
                    "No weapons found matching the name you specified.",
                    "لا توجد أي أسلحة تطابق الاسم الذي أدخلته.",
                    "Aucune arme trouvée correspondant au nom indiqué."
                ))
                return

            # If multiple weapons found, show embed
            if len(weapon_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Weapons Found", "تم العثور على عدة أسلحة", "Plusieurs armes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple weapons matching your search. Please specify which one you'd like to unequip:",
                        "عثرتُ على عدة أسلحة تطابق بحثك. يرجى تحديد أيها تريد نزع تجهيزها:",
                        "J’ai trouvé plusieurs armes correspondant à votre recherche. Veuillez préciser laquelle vous souhaitez déséquiper :"
                    ),
                    color=discord.Color.blue()
                )
                weapon_list = "\n".join([f"• {weapon['weapon_name']}" for weapon in weapon_result])
                embed.add_field(
                    name=_tr(lang, "Matching Weapons", "الأسلحة المطابقة", "Armes correspondantes"),
                    value=weapon_list,
                    inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact weapon name.",
                    "يرجى إعادة استخدام الأمر باسم السلاح الدقيق.",
                    "Veuillez relancer la commande avec le nom exact de l’arme."
                ))
                await ctx.send(embed=embed)
                return

            weapon = weapon_result[0]

            # Check card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name LIKE %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, f'%{card_name}%'))
            if not user_card_result:
                await ctx.send(_tr(
                    lang,
                    "No cards found matching the name you specified in your collection.",
                    "لا توجد بطاقات في مجموعتك تطابق الاسم الذي أدخلته.",
                    "Aucune carte trouvée dans votre collection correspondant au nom indiqué."
                ))
                return

            # If multiple cards found, show embed
            if len(user_card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to unequip the weapon from:",
                        "عثرتُ على عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد نزع السلاح منها:",
                        "J’ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser celle dont vous souhaitez retirer l’arme :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in user_card_result])
                embed.add_field(
                    name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                    value=card_list,
                    inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact card name.",
                    "يرجى إعادة استخدام الأمر باسم البطاقة الدقيق.",
                    "Veuillez relancer la commande avec le nom exact de la carte."
                ))
                await ctx.send(embed=embed)
                return

            user_card = user_card_result[0]

            # Check card info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = await execute_query_with_retry(card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(_tr(
                    lang,
                    f"Card '{card_name}' not found.",
                    f"البطاقة '{card_name}' غير موجودة.",
                    f"Carte « {card_name} » introuvable."
                ))
                return
            card = card_result[0]

            # Stat session lock
            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card['card_id']))
            if stat_status_result:
                await ctx.send(_tr(
                    lang,
                    "This card is currently being adjusted. Please wait until the process is completed.",
                    "يتم تعديل هذه البطاقة حالياً. يرجى الانتظار حتى يكتمل الإجراء.",
                    "Cette carte est en cours d’ajustement. Veuillez attendre la fin du processus."
                ))
                return

            # Check equipped slot
            equipment_column = {1: 'weapon_id', 2: 'shield_id', 3: 'armor_id'}.get(weapon['weapon_rank'])
            if user_card[equipment_column] != weapon['weapon_id']:
                slot_name = ['weapon', 'shield', 'armor'][weapon['weapon_rank']-1]
                await ctx.send(_tr(
                    lang,
                    f"This {slot_name} is not equipped on this card.",
                    f"هذا الـ{slot_name} غير مُجهّز على هذه البطاقة.",
                    f"Cet(te) {slot_name} n’est pas équipé(e) sur cette carte."
                ))
                return

            # Return weapon to inventory
            inv_weapon_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            inv_weapon_result = await execute_query_with_retry(inv_weapon_query, (inventory_id, weapon['weapon_id']))
            if not inv_weapon_result:
                insert_inv_weapon_query = "INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter) VALUES (%s, %s, 1)"
                await execute_query_with_retry(insert_inv_weapon_query, (inventory_id, weapon['weapon_id']))
            else:
                update_inv_weapon_query = "UPDATE inventory_weapon SET quantiter = quantiter + 1 WHERE inventory_id = %s AND weapon_id = %s"
                await execute_query_with_retry(update_inv_weapon_query, (inventory_id, weapon['weapon_id']))

            # Weapon boosts (to remove)
            flat_attack  = weapon.get('attack_boost', 0) or 0
            flat_health  = weapon.get('health_boost', 0) or 0
            flat_speed   = weapon.get('speed_boost', 0) or 0
            flat_defence = weapon.get('defense_boost', 0) or 0
            pct_attack   = weapon.get('attack_percentage', 0) or 0
            pct_health   = weapon.get('health_percentage', 0) or 0
            pct_speed    = weapon.get('speed_percentage', 0) or 0
            pct_defence  = weapon.get('defense_percentage', 0) or 0

            # Branch by card type
            if card['type_card'] == 'Fighter':
                # Is in team with supports?
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                has_support_in_team = False
                team_id = None
                if team_result:
                    team_id = team_result[0]['team_id']
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    if team_card_result:
                        support_query = """
                        SELECT COUNT(*) as support_count
                        FROM team_cards tc
                        JOIN card c ON tc.card_id = c.card_id
                        WHERE tc.team_id = %s AND c.type_card = 'Support'
                        """
                        support_result = await execute_query_with_retry(support_query, (team_id,))
                        if support_result and support_result[0]['support_count'] > 0:
                            has_support_in_team = True

                # Collect currently equipped items
                equipment_query = """
                SELECT weapon_id, shield_id, armor_id
                FROM user_cards
                WHERE id_ligneusercards = %s
                """
                equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))

                total_equipment_boosts = {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                }
                if equipment_result:
                    equipment = equipment_result[0]
                    for eq_id in [equipment.get('weapon_id'), equipment.get('shield_id'), equipment.get('armor_id')]:
                        if eq_id:
                            eq_details = await execute_query_with_retry("""
                                SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                    attack_percentage, health_percentage, speed_percentage, defense_percentage
                                FROM weapon WHERE weapon_id = %s
                            """, (eq_id,))
                            if eq_details:
                                eq = eq_details[0]
                                total_equipment_boosts['flat_attack']  += eq.get('attack_boost', 0) or 0
                                total_equipment_boosts['flat_health']  += eq.get('health_boost', 0) or 0
                                total_equipment_boosts['flat_speed']   += eq.get('speed_boost', 0) or 0
                                total_equipment_boosts['flat_defence'] += eq.get('defense_boost', 0) or 0
                                total_equipment_boosts['pct_attack']   += eq.get('attack_percentage', 0) or 0
                                total_equipment_boosts['pct_health']   += eq.get('health_percentage', 0) or 0
                                total_equipment_boosts['pct_speed']    += eq.get('speed_percentage', 0) or 0
                                total_equipment_boosts['pct_defence']  += eq.get('defense_percentage', 0) or 0

                # Remaining equipment after removing this weapon
                remaining_equipment_boosts = {
                    'flat_attack':  total_equipment_boosts['flat_attack']  - flat_attack,
                    'flat_health':  total_equipment_boosts['flat_health']  - flat_health,
                    'flat_speed':   total_equipment_boosts['flat_speed']   - flat_speed,
                    'flat_defence': total_equipment_boosts['flat_defence'] - flat_defence,
                    'pct_attack':   total_equipment_boosts['pct_attack']   - pct_attack,
                    'pct_health':   total_equipment_boosts['pct_health']   - pct_health,
                    'pct_speed':    total_equipment_boosts['pct_speed']    - pct_speed,
                    'pct_defence':  total_equipment_boosts['pct_defence']  - pct_defence
                }

                if has_support_in_team:
                    # 1) Reset current to base
                    await execute_query_with_retry("""
                        UPDATE user_cards
                        SET attack_stat = base_attack,
                            health_stat = base_health,
                            defence_stat = base_defence,
                            speed_stat = base_speed
                        WHERE id_ligneusercards = %s
                    """, (user_card['id_ligneusercards'],))

                    # 2) Remove ALL equipment from base (percentage then flat), and clear slot
                    await execute_query_with_retry("""
                        UPDATE user_cards
                        SET base_attack = base_attack / (1 + %s/100),
                            base_health = base_health / (1 + %s/100),
                            base_speed  = base_speed  / (1 + %s/100),
                            base_defence= base_defence/ (1 + %s/100)
                        WHERE id_ligneusercards = %s
                    """, (
                        total_equipment_boosts['pct_attack'], total_equipment_boosts['pct_health'],
                        total_equipment_boosts['pct_speed'], total_equipment_boosts['pct_defence'],
                        user_card['id_ligneusercards']
                    ))

                    await execute_query_with_retry(f"""
                        UPDATE user_cards
                        SET base_attack = base_attack - %s,
                            base_health = base_health - %s,
                            base_speed  = base_speed  - %s,
                            base_defence= base_defence - %s,
                            {equipment_column} = NULL
                        WHERE id_ligneusercards = %s
                    """, (
                        total_equipment_boosts['flat_attack'], total_equipment_boosts['flat_health'],
                        total_equipment_boosts['flat_speed'], total_equipment_boosts['flat_defence'],
                        user_card['id_ligneusercards']
                    ))

                    # 3) Reapply remaining equipment (flat then pct)
                    await execute_query_with_retry("""
                        UPDATE user_cards
                        SET base_attack = base_attack + %s,
                            base_health = base_health + %s,
                            base_speed  = base_speed  + %s,
                            base_defence= base_defence + %s
                        WHERE id_ligneusercards = %s
                    """, (
                        remaining_equipment_boosts['flat_attack'], remaining_equipment_boosts['flat_health'],
                        remaining_equipment_boosts['flat_speed'], remaining_equipment_boosts['flat_defence'],
                        user_card['id_ligneusercards']
                    ))

                    await execute_query_with_retry("""
                        UPDATE user_cards
                        SET base_attack = base_attack * (1 + %s/100),
                            base_health = base_health * (1 + %s/100),
                            base_speed  = base_speed  * (1 + %s/100),
                            base_defence= base_defence* (1 + %s/100)
                        WHERE id_ligneusercards = %s
                    """, (
                        remaining_equipment_boosts['pct_attack'], remaining_equipment_boosts['pct_health'],
                        remaining_equipment_boosts['pct_speed'], remaining_equipment_boosts['pct_defence'],
                        user_card['id_ligneusercards']
                    ))

                    # 4) Sync current to base and reapply support boosts
                    await execute_query_with_retry("""
                        UPDATE user_cards
                        SET attack_stat = base_attack,
                            health_stat = base_health,
                            defence_stat = base_defence,
                            speed_stat = base_speed
                        WHERE id_ligneusercards = %s
                    """, (user_card['id_ligneusercards'],))

                    support_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                    await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)

                    await execute_query_with_retry(
                        "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                        (user_card['id_ligneusercards'],)
                    )

                else:
                    # Fighter without supports
                    base_stats_result = await execute_query_with_retry("""
                        SELECT base_attack, base_health, base_speed, base_defence,
                            attack_stat, health_stat, speed_stat, defence_stat
                        FROM user_cards
                        WHERE id_ligneusercards = %s
                    """, (user_card['id_ligneusercards'],))

                    if base_stats_result:
                        s = base_stats_result[0]
                        true_base_attack  = s.get('base_attack', 0)  or 0
                        true_base_health  = s.get('base_health', 0)  or 0
                        true_base_speed   = s.get('base_speed', 0)   or 0
                        true_base_defence = s.get('base_defence', 0) or 0
                        true_attack  = s.get('attack_stat', 0)  or 0
                        true_health  = s.get('health_stat', 0)  or 0
                        true_speed   = s.get('speed_stat', 0)   or 0
                        true_defence = s.get('defence_stat', 0) or 0

                        # Remove all current equipment from both base and current
                        if total_equipment_boosts['pct_attack'] > 0:
                            true_base_attack /= (1 + total_equipment_boosts['pct_attack']/100)
                            true_attack      /= (1 + total_equipment_boosts['pct_attack']/100)
                        if total_equipment_boosts['pct_health'] > 0:
                            true_base_health /= (1 + total_equipment_boosts['pct_health']/100)
                            true_health      /= (1 + total_equipment_boosts['pct_health']/100)
                        if total_equipment_boosts['pct_speed'] > 0:
                            true_base_speed  /= (1 + total_equipment_boosts['pct_speed']/100)
                            true_speed       /= (1 + total_equipment_boosts['pct_speed']/100)
                        if total_equipment_boosts['pct_defence'] > 0:
                            true_base_defence/= (1 + total_equipment_boosts['pct_defence']/100)
                            true_defence     /= (1 + total_equipment_boosts['pct_defence']/100)

                        true_base_attack  -= total_equipment_boosts['flat_attack']
                        true_base_health  -= total_equipment_boosts['flat_health']
                        true_base_speed   -= total_equipment_boosts['flat_speed']
                        true_base_defence -= total_equipment_boosts['flat_defence']

                        true_attack  -= total_equipment_boosts['flat_attack']
                        true_health  -= total_equipment_boosts['flat_health']
                        true_speed   -= total_equipment_boosts['flat_speed']
                        true_defence -= total_equipment_boosts['flat_defence']

                        # Reapply remaining equipment (without removed weapon)
                        new_base_attack  = true_base_attack  + remaining_equipment_boosts['flat_attack']
                        new_base_health  = true_base_health  + remaining_equipment_boosts['flat_health']
                        new_base_speed   = true_base_speed   + remaining_equipment_boosts['flat_speed']
                        new_base_defence = true_base_defence + remaining_equipment_boosts['flat_defence']

                        new_attack  = true_attack  + remaining_equipment_boosts['flat_attack']
                        new_health  = true_health  + remaining_equipment_boosts['flat_health']
                        new_speed   = true_speed   + remaining_equipment_boosts['flat_speed']
                        new_defence = true_defence + remaining_equipment_boosts['flat_defence']

                        if remaining_equipment_boosts['pct_attack'] > 0:
                            new_base_attack *= (1 + remaining_equipment_boosts['pct_attack']/100)
                            new_attack      *= (1 + remaining_equipment_boosts['pct_attack']/100)
                        if remaining_equipment_boosts['pct_health'] > 0:
                            new_base_health *= (1 + remaining_equipment_boosts['pct_health']/100)
                            new_health      *= (1 + remaining_equipment_boosts['pct_health']/100)
                        if remaining_equipment_boosts['pct_speed'] > 0:
                            new_base_speed *= (1 + remaining_equipment_boosts['pct_speed']/100)
                            new_speed      *= (1 + remaining_equipment_boosts['pct_speed']/100)
                        if remaining_equipment_boosts['pct_defence'] > 0:
                            new_base_defence *= (1 + remaining_equipment_boosts['pct_defence']/100)
                            new_defence      *= (1 + remaining_equipment_boosts['pct_defence']/100)

                        new_power = new_attack + new_health + new_speed + new_defence

                        await execute_query_with_retry(f"""
                            UPDATE user_cards
                            SET {equipment_column} = NULL,
                                base_attack = %s,
                                base_health = %s,
                                base_speed  = %s,
                                base_defence= %s,
                                attack_stat = %s,
                                health_stat = %s,
                                speed_stat  = %s,
                                defence_stat= %s,
                                power_stat  = %s
                            WHERE id_ligneusercards = %s
                        """, (
                            new_base_attack, new_base_health, new_base_speed, new_base_defence,
                            new_attack, new_health, new_speed, new_defence,
                            new_power, user_card['id_ligneusercards']
                        ))

            elif card['type_card'] == 'Support':
                # Remove percent boosts from support itself
                await execute_query_with_retry("""
                    UPDATE user_cards
                    SET attack_stat = attack_stat / (1 + %s/100),
                        health_stat = health_stat / (1 + %s/100),
                        speed_stat  = speed_stat  / (1 + %s/100),
                        defence_stat= defence_stat/ (1 + %s/100)
                    WHERE id_ligneusercards = %s
                """, (pct_attack, pct_health, pct_speed, pct_defence, user_card['id_ligneusercards']))

                # Remove flat boosts and clear slot
                await execute_query_with_retry(f"""
                    UPDATE user_cards
                    SET {equipment_column} = NULL,
                        attack_stat = attack_stat - %s,
                        health_stat = health_stat - %s,
                        speed_stat  = speed_stat  - %s,
                        defence_stat= defence_stat- %s
                    WHERE id_ligneusercards = %s
                """, (flat_attack, flat_health, flat_speed, flat_defence, user_card['id_ligneusercards']))

                # If support is in a team, reapply boosts to fighters
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                if team_result:
                    team_id = team_result[0]['team_id']
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    if team_card_result:
                        # Reset fighters to base
                        await execute_query_with_retry("""
                            UPDATE user_cards uc
                            JOIN team_cards tc ON uc.card_id = tc.card_id
                            JOIN card c ON uc.card_id = c.card_id
                            SET
                                uc.attack_stat = uc.base_attack,
                                uc.health_stat = uc.base_health,
                                uc.speed_stat  = uc.base_speed,
                                uc.defence_stat= uc.base_defence
                            WHERE
                                tc.team_id = %s AND
                                uc.user_id = %s AND
                                c.type_card = 'Fighter'
                        """, (team_id, user_id))

                        total_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                        await self.apply_support_boosts_to_offense(team_id, user_id, total_boosts)

            await ctx.send(_tr(
                lang,
                f"Successfully unequipped {weapon['weapon_name']} from {user_card['name']}!",
                f"تم نزع تجهيز {weapon['weapon_name']} من {user_card['name']} بنجاح!",
                f"{weapon['weapon_name']} a été retiré avec succès de {user_card['name']} !"
            ))

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))


    async def rstat(self, ctx, *, card_name):
        lang = await get_user_multilanguage(ctx.author.id)  # 0=en, 1=ar, 2=fr
        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to register first.",
                    "يجب أن تقوم بالتسجيل أولاً.",
                    "Vous devez d’abord vous inscrire."
                ))
                return

            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Get card
            card_query = "SELECT u.*,c.card_icon,c.type_card FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            card_result = await execute_query_with_retry(card_query, (user_id, f'%{card_name}%'))

            if not card_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have a card with the name you specified.",
                    "لا تملك بطاقة بالاسم الذي أدخلته.",
                    "Vous n’avez aucune carte portant le nom indiqué."
                ))
                return

            if len(card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to reset stats for:",
                        "عثرتُ على عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد إعادة ضبط إحصاءاتها:",
                        "J’ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser celle dont vous souhaitez réinitialiser les statistiques :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(
                    name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                    value=card_list, inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact card name.",
                    "يرجى إعادة استخدام الأمر باسم البطاقة الدقيق.",
                    "Veuillez relancer la commande avec le nom exact de la carte."
                ))
                await ctx.send(embed=embed)
                return

            card = card_result[0]

            if card['type_card'] in ['Support', 'Booster']:
                await ctx.send(_tr(
                    lang,
                    "This command can only be used with Offence-type cards.",
                    "هذا الأمر مخصص لبطاقات الهجوم فقط.",
                    "Cette commande ne peut être utilisée qu’avec des cartes d’attaque."
                ))
                return

            # Stat session lock
            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card['card_id']))
            if stat_status_result:
                await ctx.send(_tr(
                    lang,
                    "This card is currently being adjusted. Please wait until the process is completed.",
                    "يتم تعديل هذه البطاقة حالياً. يرجى الانتظار حتى يكتمل الإجراء.",
                    "Cette carte est en cours d’ajustement. Veuillez attendre la fin du processus."
                ))
                return

            # Check reset-stat item (product_id = 3)
            inventory_query = "SELECT ip.*,p.product_icon FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id JOIN product p ON ip.product_id = ip.product_id WHERE i.user_id = %s AND ip.product_id = 3"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have a reset stat item in your inventory.",
                    "لا تملك عنصر إعادة ضبط الإحصاءات في الجرد.",
                    "Vous n’avez pas d’objet de réinitialisation des stats dans votre inventaire."
                ))
                return

            # Consume item
            if inventory_result[0]['quantiter'] == 1:
                delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
            else:
                update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

            # Equipment flags
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card['card_id'])

            # Team / support status
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))

            has_support_in_team = False
            support_boosts = None
            team_id = None

            if team_result:
                team_id = team_result[0]['team_id']
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                if team_card_result:
                    support_query = """
                    SELECT COUNT(*) as support_count
                    FROM team_cards tc
                    JOIN card c ON tc.card_id = c.card_id
                    WHERE tc.team_id = %s AND c.type_card = 'Support'
                    """
                    support_result = await execute_query_with_retry(support_query, (team_id,))
                    if support_result and support_result[0]['support_count'] > 0:
                        has_support_in_team = True
                        support_boosts = await self.calculate_total_support_boosts(team_id, user_id)

            # --- Scenario handling ---

            # 1) No support, no equipment
            if not has_support_in_team and not has_equipment:
                reset_query = """
                UPDATE user_cards
                SET
                    base_attack = base_attack - used_attackpoints,
                    base_health = base_health - used_healthpoints,
                    base_defence = base_defence - used_defencepoints,
                    base_speed = base_speed - used_speedpoints,
                    attack_stat = attack_stat - used_attackpoints,
                    health_stat = health_stat - used_healthpoints,
                    defence_stat = defence_stat - used_defencepoints,
                    speed_stat = speed_stat - used_speedpoints,
                    current_attackpoints = current_attackpoints + used_attackpoints,
                    current_speedpoints = current_speedpoints + used_speedpoints,
                    current_healthpoints = current_healthpoints + used_healthpoints,
                    current_defencepoints = current_defencepoints + used_defencepoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0,
                    power_stat = attack_stat + speed_stat + health_stat + defence_stat - used_attackpoints - used_speedpoints - used_healthpoints - used_defencepoints
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))

            # 2) Equipment only
            elif has_equipment and not has_support_in_team:
                true_base = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                if equipment_boosts:
                    true_base['attack']  = true_base['attack']  / (1 + equipment_boosts['pct_attack']/100) - equipment_boosts['flat_attack']
                    true_base['health']  = true_base['health']  / (1 + equipment_boosts['pct_health']/100) - equipment_boosts['flat_health']
                    true_base['defence'] = true_base['defence'] / (1 + equipment_boosts['pct_defence']/100) - equipment_boosts['flat_defence']
                    true_base['speed']   = true_base['speed']   / (1 + equipment_boosts['pct_speed']/100) - equipment_boosts['flat_speed']

                new_true = {
                    'attack': true_base['attack']  - card['used_attackpoints'],
                    'health': true_base['health']  - card['used_healthpoints'],
                    'defence': true_base['defence']- card['used_defencepoints'],
                    'speed':  true_base['speed']   - card['used_speedpoints']
                }
                new_base = dict(new_true)
                if equipment_boosts:
                    new_base['attack']  = (new_base['attack']  + equipment_boosts['flat_attack'])  * (1 + equipment_boosts['pct_attack']/100)
                    new_base['health']  = (new_base['health']  + equipment_boosts['flat_health'])  * (1 + equipment_boosts['pct_health']/100)
                    new_base['defence'] = (new_base['defence'] + equipment_boosts['flat_defence']) * (1 + equipment_boosts['pct_defence']/100)
                    new_base['speed']   = (new_base['speed']   + equipment_boosts['flat_speed'])   * (1 + equipment_boosts['pct_speed']/100)

                update_base_query = """
                UPDATE user_cards
                SET base_attack = %s,
                    base_health = %s,  
                    base_defence = %s,
                    base_speed = %s,
                    attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s,
                    power_stat = %s,
                    current_attackpoints = current_attackpoints + used_attackpoints,
                    current_speedpoints = current_speedpoints + used_speedpoints,
                    current_healthpoints = current_healthpoints + used_healthpoints,
                    current_defencepoints = current_defencepoints + used_defencepoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0
                WHERE id_ligneusercards = %s
                """
                new_power = new_base['attack'] + new_base['health'] + new_base['defence'] + new_base['speed']
                await execute_query_with_retry(update_base_query, (
                    new_base['attack'], new_base['health'], new_base['defence'], new_base['speed'],
                    new_base['attack'], new_base['health'], new_base['defence'], new_base['speed'],
                    new_power, card['id_ligneusercards']
                ))

            # 3) Support only
            elif not has_equipment and has_support_in_team:
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))

                update_base_query = """
                UPDATE user_cards
                SET base_attack = base_attack - used_attackpoints,
                    base_health = base_health - used_healthpoints,
                    base_defence = base_defence - used_defencepoints,
                    base_speed = base_speed - used_speedpoints,
                    attack_stat = base_attack - used_attackpoints,
                    health_stat = base_health - used_healthpoints,
                    defence_stat = base_defence - used_defencepoints,
                    speed_stat = base_speed - used_speedpoints,
                    current_attackpoints = current_attackpoints + used_attackpoints,
                    current_speedpoints = current_speedpoints + used_speedpoints,
                    current_healthpoints = current_healthpoints + used_healthpoints,
                    current_defencepoints = current_defencepoints + used_defencepoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (card['id_ligneusercards'],))

                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )

            # 4) Both equipment and support
            else:
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))

                true_base = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                if equipment_boosts:
                    true_base['attack']  = true_base['attack']  / (1 + equipment_boosts['pct_attack']/100) - equipment_boosts['flat_attack']
                    true_base['health']  = true_base['health']  / (1 + equipment_boosts['pct_health']/100) - equipment_boosts['flat_health']
                    true_base['defence'] = true_base['defence'] / (1 + equipment_boosts['pct_defence']/100) - equipment_boosts['flat_defence']
                    true_base['speed']   = true_base['speed']   / (1 + equipment_boosts['pct_speed']/100) - equipment_boosts['flat_speed']

                new_true = {
                    'attack': true_base['attack']  - card['used_attackpoints'],
                    'health': true_base['health']  - card['used_healthpoints'],
                    'defence': true_base['defence']- card['used_defencepoints'],
                    'speed':  true_base['speed']   - card['used_speedpoints']
                }
                new_base = dict(new_true)
                if equipment_boosts:
                    new_base['attack']  = (new_base['attack']  + equipment_boosts['flat_attack'])  * (1 + equipment_boosts['pct_attack']/100)
                    new_base['health']  = (new_base['health']  + equipment_boosts['flat_health'])  * (1 + equipment_boosts['pct_health']/100)
                    new_base['defence'] = (new_base['defence'] + equipment_boosts['flat_defence']) * (1 + equipment_boosts['pct_defence']/100)
                    new_base['speed']   = (new_base['speed']   + equipment_boosts['flat_speed'])   * (1 + equipment_boosts['pct_speed']/100)

                # Set current to new_true (no equip), then set base, then reapply equip & support
                await execute_query_with_retry("""
                    UPDATE user_cards
                    SET attack_stat = %s,
                        health_stat = %s,
                        defence_stat = %s,
                        speed_stat = %s
                    WHERE id_ligneusercards = %s
                """, (new_true['attack'], new_true['health'], new_true['defence'], new_true['speed'], card['id_ligneusercards']))

                await execute_query_with_retry("""
                    UPDATE user_cards
                    SET base_attack = %s,
                        base_health = %s,
                        base_defence = %s,
                        base_speed = %s,
                        current_attackpoints = current_attackpoints + used_attackpoints,
                        current_speedpoints = current_speedpoints + used_speedpoints,
                        current_healthpoints = current_healthpoints + used_healthpoints,
                        current_defencepoints = current_defencepoints + used_defencepoints,
                        used_attackpoints = 0,
                        used_healthpoints = 0,
                        used_defencepoints = 0,
                        used_speedpoints = 0,
                        used_statpoints = 0
                    WHERE id_ligneusercards = %s
                """, (new_base['attack'], new_base['health'], new_base['defence'], new_base['speed'], card['id_ligneusercards']))

                await self.apply_equipment_boosts(user_id, card['card_id'], equipment_boosts)
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )

            # Final message
            await ctx.send(_tr(
                lang,
                f"Applied 1x {inventory_result[0]['product_icon']} statreset to {card['card_icon']} {card['name']}.",
                f"تم تطبيق 1x {inventory_result[0]['product_icon']} لإعادة الضبط على {card['card_icon']} {card['name']}.",
                f"1x {inventory_result[0]['product_icon']} de réinitialisation appliqué à {card['card_icon']} {card['name']}."
            ))

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))


    @commands.command(name="reset", aliases=["rst", "rt"])
    async def reset(self, ctx, reset_type: str):
        lang = await get_user_multilanguage(ctx.author.id)  # 0=en, 1=ar, 2=fr
        try:
            # Get user_id and squad_id
            user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to register first.",
                    "يجب أن تقوم بالتسجيل أولاً.",
                    "Vous devez d’abord vous inscrire."
                ))
                return
            user_id = user_result[0]['user_id']
            squad_id = user_result[0]['squad_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Inventory
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have an inventory.",
                    "ليس لديك جرد.",
                    "Vous n'avez pas d'inventaire."
                ))
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Reset map
            reset_types = {
                'rolls':   {'product_id': 2,  'timer_column': 'roll_timer',    'count_column': 'roll_count'},
                'rt':      {'product_id': 2,  'timer_column': 'roll_timer',    'count_column': 'roll_count'},
                'dungeon': {'product_id': 14, 'timer_column': 'dungeon_timer', 'count_column': None},
                'dg':      {'product_id': 14, 'timer_column': 'dungeon_timer', 'count_column': None},
                'mission': {'product_id': 15, 'timer_column': 'mission_timer', 'count_column': None},
                'ms':      {'product_id': 15, 'timer_column': 'mission_timer', 'count_column': None},
                'fight':   {'product_id': 16, 'timer_column': 'fight_timer',   'count_column': None},
                'f':       {'product_id': 16, 'timer_column': 'fight_timer',   'count_column': None},
                'squad':   {'product_id': 18, 'timer_column': 'squad_timer',   'count_column': None}
            }

            reset_type = reset_type.lower()
            if reset_type not in reset_types:
                await ctx.send(_tr(
                    lang,
                    "Invalid reset type. Use rolls/rt, dungeon/dg, mission/ms, fight/f, or squad.",
                    "نوع إعادة الضبط غير صالح. استخدم rolls/rt أو dungeon/dg أو mission/ms أو fight/f أو squad.",
                    "Type de réinitialisation invalide. Utilisez rolls/rt, dungeon/dg, mission/ms, fight/f ou squad."
                ))
                return

            # Check current timer (already available?)
            timer_col = reset_types[reset_type]['timer_column']
            if reset_type == 'squad' and squad_id is not None:
                timer_check_query = f"SELECT {timer_col} FROM user WHERE user_id = %s"
                timer_result = await execute_query_with_retry(timer_check_query, (user_id,))
            else:
                timer_check_query = f"SELECT {timer_col} FROM user WHERE user_id = %s"
                timer_result = await execute_query_with_retry(timer_check_query, (user_id,))

            if timer_result and timer_result[0][timer_col] is None:
                display_name = {'rt':'rolls', 'dg':'dungeon', 'ms':'mission', 'f':'fight'}.get(reset_type, reset_type)
                await ctx.send(_tr(
                    lang,
                    f"Your {display_name} timer is already available to use. No reset needed!",
                    f"مؤقّت {display_name} لديك متاح بالفعل. لا حاجة لإعادة الضبط!",
                    f"Votre minuterie {display_name} est déjà disponible. Aucune réinitialisation nécessaire !"
                ))
                return

            # Check item
            inv_item_query = """
            SELECT * FROM inventory_product
            WHERE inventory_id = %s AND product_id = %s
            """
            inv_item_result = await execute_query_with_retry(
                inv_item_query, (inventory_id, reset_types[reset_type]['product_id'])
            )

            if not inv_item_result:
                display_name = {'rt':'rolls', 'dg':'dungeon', 'ms':'mission', 'f':'fight'}.get(reset_type, reset_type)
                await ctx.send(_tr(
                    lang,
                    f"You don't have a {display_name} reset item in your inventory.",
                    f"لا تملك عنصر إعادة ضبط {display_name} في جردك.",
                    f"Vous n’avez pas d’objet de réinitialisation {display_name} dans votre inventaire."
                ))
                return

            # Consume item
            if inv_item_result[0]['quantiter'] == 1:
                await execute_query_with_retry(
                    "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s",
                    (inv_item_result[0]['id_ligneinventoryproduct'],)
                )
            else:
                await execute_query_with_retry(
                    "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s",
                    (inv_item_result[0]['id_ligneinventoryproduct'],)
                )

            # Apply reset
            count_col = reset_types[reset_type]['count_column']
            if reset_type == 'squad' and squad_id is not None:
                update_query = f"UPDATE user SET {timer_col} = NULL WHERE squad_id = %s"
                await execute_query_with_retry(update_query, (squad_id,))
                message_en = "Successfully reset the squad timer for all squad members."
                message_ar = "تمت إعادة ضبط مؤقّت الفرقة لجميع الأعضاء بنجاح."
                message_fr = "Minuterie d’escouade réinitialisée avec succès pour tous les membres."
                await ctx.send(_tr(lang, message_en, message_ar, message_fr))
            else:
                parts = [f"{timer_col} = NULL"]
                if count_col:
                    parts.append(f"{count_col} = 0")
                await execute_query_with_retry(
                    f"UPDATE user SET {', '.join(parts)} WHERE user_id = %s",
                    (user_id,)
                )

                display_name = {'rt':'rolls', 'dg':'dungeon', 'ms':'mission', 'f':'fight'}.get(reset_type, reset_type)
                tail_en = " and count" if count_col else ""
                tail_ar = " والعدّاد" if count_col else ""
                tail_fr = " et le compteur" if count_col else ""
                await ctx.send(_tr(
                    lang,
                    f"Successfully reset your {display_name} timer{tail_en}.",
                    f"تمت إعادة ضبط مؤقّت {display_name}{tail_ar} بنجاح.",
                    f"Votre minuterie {display_name}{tail_fr} a été réinitialisée avec succès."
                ))

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))


    @commands.command(name="charm", aliases=["chm", "chr"])
    async def charm(self, ctx, charm_type: str, *, card_name: str):
        lang = await get_user_multilanguage(ctx.author.id)  # 0=en, 1=ar, 2=fr
        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "You need to register first.",
                    "يجب أن تقوم بالتسجيل أولاً.",
                    "Vous devez d’abord vous inscrire."
                ))
                return

            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Card lookup
            card_query = "SELECT u.*,c.card_icon,c.type_card FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))

            if not card_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have a card with the name you specified.",
                    "لا تملك بطاقة بالاسم الذي أدخلته.",
                    "Vous n’avez aucune carte portant le nom indiqué."
                ))
                return

            if len(card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to unequip the weapon from:",
                        "عثرنا على عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة المقصودة:",
                        "Plusieurs cartes correspondent à votre recherche. Veuillez préciser laquelle viser :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(
                    name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                    value=card_list, inline=False
                )
                embed.set_footer(text=_tr(
                    lang,
                    "Please use the command again with the exact card name.",
                    "يرجى إعادة استخدام الأمر باسم البطاقة الدقيق.",
                    "Veuillez relancer la commande avec le nom exact de la carte."
                ))
                await ctx.send(embed=embed)
                return

            card = card_result[0]
            if card['type_card'] in ['Support', 'Booster']:
                await ctx.send(_tr(
                    lang,
                    "This command can only be used with Offence-type cards.",
                    "هذا الأمر مخصص لبطاقات الهجوم فقط.",
                    "Cette commande ne peut être utilisée qu’avec des cartes d’attaque."
                ))
                return

            # Charm mapping
            charm_info = {
                'attack':  {'product_id': 9,  'charm_column': 'charm_attack',  'count_column': 'attack_count'},
                'speed':   {'product_id': 10, 'charm_column': 'charm_speed',   'count_column': 'speed_count'},
                'defence': {'product_id': 11, 'charm_column': 'charm_defence', 'count_column': 'defence_count'},
                'health':  {'product_id': 12, 'charm_column': 'charm_health',  'count_column': 'health_count'}
            }
            charm_data = charm_info.get(charm_type.lower())
            if not charm_data:
                await ctx.send(_tr(
                    lang,
                    "Invalid charm type. Use attack, speed, defence, or health.",
                    "نوع التعويذة غير صالح. استخدم attack أو speed أو defence أو health.",
                    "Type de charme invalide. Utilisez attack, speed, defence ou health."
                ))
                return

            # Already active?
            if card[charm_data['charm_column']] == 1:
                await ctx.send(_tr(
                    lang,
                    f"This card already has an active {charm_type} charm.",
                    f"هذه البطاقة لديها بالفعل تعويذة {charm_type} مفعّلة.",
                    f"Cette carte a déjà un charme {charm_type} actif."
                ))
                return

            # Bonus count by card 105
            card_105_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = 105"
            card_105_result = await execute_query_with_retry(card_105_query, (user_id,))
            charm_count = 5 if card_105_result else 3

            # Inventory check
            inventory_query = """
            SELECT ip.*, p.product_icon FROM inventory i
            JOIN inventory_product ip ON i.inventory_id = ip.inventory_id
            JOIN product p ON ip.product_id = p.product_id
            WHERE i.user_id = %s AND ip.product_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (user_id, charm_data['product_id']))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have a {charm_type} charm in your inventory.",
                    f"لا تملك تعويذة {charm_type} في جردك.",
                    f"Vous n’avez pas de charme {charm_type} dans votre inventaire."
                ))
                return

            # Consume one
            if inventory_result[0]['quantiter'] == 1:
                await execute_query_with_retry("DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s",
                                            (inventory_result[0]['id_ligneinventoryproduct'],))
            else:
                await execute_query_with_retry("UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s",
                                            (inventory_result[0]['id_ligneinventoryproduct'],))

            # Apply charm
            update_query = f"""
            UPDATE user_cards
            SET
                {charm_data['charm_column']} = 1,
                {charm_data['count_column']} = {charm_data['count_column']} + %s
            WHERE user_id = %s AND name = %s
            """
            await execute_query_with_retry(update_query, (charm_count, user_id, card['name']))

            await ctx.send(_tr(
                lang,
                f"Applied 1x {inventory_result[0]['product_icon']} {charm_type} charm to {card['card_icon']} {card['name']}. The charm stat has been increased and count set to {charm_count}.",
                f"تم تطبيق 1x {inventory_result[0]['product_icon']} تعويذة {charm_type} على {card['card_icon']} {card['name']}. تم تعزيز الإحصائية وتعيين العداد إلى {charm_count}.",
                f"1x {inventory_result[0]['product_icon']} charme {charm_type} appliqué à {card['card_icon']} {card['name']}. Stat augmentée, compteur réglé à {charm_count}."
            ))

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))


    @commands.command(name="capsule", aliases=["cap", "cs"])
    async def capsule(self, ctx, capsule_type, *args):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Parse args
            quantity = 1
            card_name = ' '.join(args)
            try:
                if args and args[-1].isdigit():
                    quantity = int(args[-1])
                    card_name = ' '.join(args[:-1])
            except (IndexError, ValueError):
                pass

            if quantity < 1:
                await ctx.send(_tr(lang, "Quantity must be a positive number.", "يجب أن تكون الكمية رقمًا موجبًا.", "La quantité doit être un nombre positif."))
                return

            # User
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send(_tr(lang, "You need to register first.", "يجب أن تقوم بالتسجيل أولاً.", "Vous devez d’abord vous inscrire."))
                return
            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Market lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Card (case-insensitive)
            card_query = "SELECT u.*,c.card_icon,c.type_card FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND LOWER(name) LIKE LOWER(%s)"
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))
            if not card_result:
                await ctx.send(_tr(
                    lang,
                    "You don't have a card matching the name you specified.",
                    "لا تملك بطاقة تطابق الاسم الذي أدخلته.",
                    "Vous n’avez aucune carte correspondant au nom indiqué."
                ))
                return

            if len(card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to use the capsule on:",
                        "عثرنا على عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد استخدام الكبسولة عليها:",
                        "Plusieurs cartes correspondent à votre recherche. Indiquez celle sur laquelle appliquer la capsule :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                                value=card_list, inline=False)
                embed.set_footer(text=_tr(lang, "Please use the command again with the exact card name.",
                                        "يرجى إعادة استخدام الأمر باسم البطاقة الدقيق.",
                                        "Veuillez relancer la commande avec le nom exact de la carte."))
                await ctx.send(embed=embed)
                return

            card = card_result[0]

            cap_map = {
                'acap': ('used_attackpoints', 'current_cap_attack'),
                'scap': ('used_speedpoints',  'current_cap_speed'),
                'hcap': ('used_healthpoints', 'current_cap_health'),
                'dcap': ('used_defencepoints','current_cap_defence')
            }
            if capsule_type.lower() not in cap_map:
                await ctx.send(_tr(
                    lang,
                    "Invalid capsule type. Use acap, scap, hcap, or dcap.",
                    "نوع الكبسولة غير صالح. استخدم acap أو scap أو hcap أو dcap.",
                    "Type de capsule invalide. Utilisez acap, scap, hcap ou dcap."
                ))
                return

            used_column_check, cap_column = cap_map[capsule_type.lower()]

            # Stat adjust lock
            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card['card_id']))
            if stat_status_result:
                await ctx.send(_tr(
                    lang,
                    "This card is currently being adjusted. Please wait until the process is completed.",
                    "يتم تعديل هذه البطاقة حالياً. يرجى الانتظار حتى يكتمل الإجراء.",
                    "Cette carte est en cours d’ajustement. Veuillez attendre la fin du processus."
                ))
                return

            # Cap overflow?
            if card[used_column_check] + quantity > card[cap_column]:
                remaining_capacity = card[cap_column] - card[used_column_check]
                await ctx.send(_tr(
                    lang,
                    f"Cannot apply {quantity} capsules. This would exceed the stat cap limit.\nCurrent {used_column_check.replace('used_', '').replace('points', '')}: {card[used_column_check]}/{card[cap_column]}\nYou can only apply {remaining_capacity} more capsule(s) to this stat.",
                    f"لا يمكن تطبيق {quantity} كبسولة، ذلك سيتجاوز الحد الأقصى للإحصائية.\nالحالي {used_column_check.replace('used_', '').replace('points', '')}: {card[used_column_check]}/{card[cap_column]}\nيمكنك تطبيق {remaining_capacity} كبسولة أخرى فقط لهذه الإحصائية.",
                    f"Impossible d’appliquer {quantity} capsules : cela dépasserait la limite de la statistique.\nActuel {used_column_check.replace('used_', '').replace('points', '')} : {card[used_column_check]}/{card[cap_column]}\nVous pouvez n’appliquer que {remaining_capacity} capsule(s) de plus pour cette stat."
                ))
                return

            # Must use individual points first?
            individual_points_map = {
                'acap': 'current_attackpoints',
                'scap': 'current_speedpoints',
                'hcap': 'current_healthpoints',
                'dcap': 'current_defencepoints'
            }
            individual_points_column = individual_points_map[capsule_type.lower()]
            if card[individual_points_column] > 0:
                stat_name = capsule_type.lower().replace('cap', '').capitalize()
                await ctx.send(_tr(
                    lang,
                    f"You have {card[individual_points_column]} {stat_name} Points available for this stat. Please use those first before using capsules.",
                    f"لديك {card[individual_points_column]} نقاط {stat_name} متاحة لهذه الإحصائية. يرجى استخدامها أولاً قبل استخدام الكبسولات.",
                    f"Vous avez {card[individual_points_column]} points {stat_name} disponibles pour cette statistique. Utilisez-les avant les capsules."
                ))
                return

            if card['type_card'] in ['Support', 'Booster']:
                await ctx.send(_tr(
                    lang,
                    "This command can only be used with Offence-type cards.",
                    "هذا الأمر مخصص لبطاقات الهجوم فقط.",
                    "Cette commande ne peut être utilisée qu’avec des cartes d’attaque."
                ))
                return

            # Product id
            product_id_map = {'acap': 4, 'scap': 5, 'hcap': 6, 'dcap': 7}
            product_id = product_id_map[capsule_type.lower()]

            # Inventory check
            inventory_query = """
            SELECT ip.*, p.product_icon
            FROM inventory i
            JOIN inventory_product ip ON i.inventory_id = ip.inventory_id
            JOIN product p ON ip.product_id = p.product_id
            WHERE i.user_id = %s AND ip.product_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (user_id, product_id))
            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have a {capsule_type} in your inventory.",
                    f"لا تملك {capsule_type} في جردك.",
                    f"Vous n’avez pas de {capsule_type} dans votre inventaire."
                ))
                return
            inventory_item = inventory_result[0]
            if inventory_item['quantiter'] < quantity:
                await ctx.send(_tr(
                    lang,
                    f"You only have {inventory_item['quantiter']} {capsule_type} in your inventory.",
                    f"لديك فقط {inventory_item['quantiter']} من {capsule_type} في جردك.",
                    f"Vous n’avez que {inventory_item['quantiter']} {capsule_type} dans votre inventaire."
                ))
                return

            # Consume capsules
            if inventory_item['quantiter'] == quantity:
                await execute_query_with_retry("DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s",
                                            (inventory_item['id_ligneinventoryproduct'],))
            else:
                await execute_query_with_retry("UPDATE inventory_product SET quantiter = quantiter - %s WHERE id_ligneinventoryproduct = %s",
                                            (quantity, inventory_item['id_ligneinventoryproduct']))

            # Stat mapping
            stat_map = {
                'acap': ('attack_stat',  'used_attackpoints',  'base_attack'),
                'scap': ('speed_stat',   'used_speedpoints',   'base_speed'),
                'hcap': ('health_stat',  'used_healthpoints',  'base_health'),
                'dcap': ('defence_stat', 'used_defencepoints', 'base_defence')
            }
            stat_column, used_column, base_column = stat_map[capsule_type.lower()]

            # Equipment + support context
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card['card_id'])

            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            has_support_in_team = False
            if team_result:
                team_id = team_result[0]['team_id']
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                if team_card_result:
                    support_query = """
                    SELECT COUNT(*) as support_count
                    FROM team_cards tc
                    JOIN card c ON tc.card_id = c.card_id
                    WHERE tc.team_id = %s AND c.type_card = 'Support'
                    """
                    support_result = await execute_query_with_retry(support_query, (team_id,))
                    if support_result and support_result[0]['support_count'] > 0:
                        has_support_in_team = True
                        support_boosts = await self.calculate_total_support_boosts(team_id, user_id)

            # Scenario handling
            if not has_support_in_team and not has_equipment:
                update_query = f"""
                UPDATE user_cards
                SET
                    {stat_column} = {stat_column} + %s,
                    {used_column} = {used_column} + %s,
                    {base_column} = {base_column} + %s,
                    used_statpoints = used_statpoints + %s,
                    power_stat = attack_stat + speed_stat + health_stat + defence_stat
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_query, (quantity, quantity, quantity, quantity, card['id_ligneusercards']))

            elif has_support_in_team and not has_equipment:
                # reset -> buff base -> reapply support
                await execute_query_with_retry("""
                    UPDATE user_cards
                    SET attack_stat = base_attack,
                        health_stat = base_health,
                        defence_stat = base_defence,
                        speed_stat = base_speed
                    WHERE id_ligneusercards = %s
                """, (card['id_ligneusercards'],))

                await execute_query_with_retry(f"""
                    UPDATE user_cards
                    SET
                        {base_column} = {base_column} + %s,
                        {used_column} = {used_column} + %s,
                        used_statpoints = used_statpoints + %s
                    WHERE id_ligneusercards = %s
                """, (quantity, quantity, quantity, card['id_ligneusercards']))

                await execute_query_with_retry("""
                    UPDATE user_cards
                    SET attack_stat = base_attack,
                        health_stat = base_health,
                        defence_stat = base_defence,
                        speed_stat = base_speed
                    WHERE id_ligneusercards = %s
                """, (card['id_ligneusercards'],))

                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )

            elif not has_support_in_team and has_equipment:
                # remove equip -> buff -> reapply equip
                true_base = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed':  card['base_speed']
                }
                if equipment_boosts:
                    true_base['attack']  = true_base['attack']  / (1 + equipment_boosts['pct_attack']/100) - equipment_boosts['flat_attack']
                    true_base['health']  = true_base['health']  / (1 + equipment_boosts['pct_health']/100) - equipment_boosts['flat_health']
                    true_base['defence'] = true_base['defence'] / (1 + equipment_boosts['pct_defence']/100) - equipment_boosts['flat_defence']
                    true_base['speed']   = true_base['speed']   / (1 + equipment_boosts['pct_speed']/100) - equipment_boosts['flat_speed']

                new_true = dict(true_base)
                if stat_column == 'attack_stat':  new_true['attack']  += quantity
                if stat_column == 'health_stat':  new_true['health']  += quantity
                if stat_column == 'defence_stat': new_true['defence'] += quantity
                if stat_column == 'speed_stat':   new_true['speed']   += quantity

                new_base = dict(new_true)
                if equipment_boosts:
                    new_base['attack']  = (new_base['attack']  + equipment_boosts['flat_attack'])  * (1 + equipment_boosts['pct_attack']/100)
                    new_base['health']  = (new_base['health']  + equipment_boosts['flat_health'])  * (1 + equipment_boosts['pct_health']/100)
                    new_base['defence'] = (new_base['defence'] + equipment_boosts['flat_defence']) * (1 + equipment_boosts['pct_defence']/100)
                    new_base['speed']   = (new_base['speed']   + equipment_boosts['flat_speed'])   * (1 + equipment_boosts['pct_speed']/100)

                update_base_query = """
                UPDATE user_cards
                SET base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s,
                    power_stat = %s,
                    used_statpoints = used_statpoints + %s
                """
                if used_column == 'used_attackpoints':
                    update_base_query += ", used_attackpoints = used_attackpoints + %s"
                elif used_column == 'used_healthpoints':
                    update_base_query += ", used_healthpoints = used_healthpoints + %s"
                elif used_column == 'used_defencepoints':
                    update_base_query += ", used_defencepoints = used_defencepoints + %s"
                elif used_column == 'used_speedpoints':
                    update_base_query += ", used_speedpoints = used_speedpoints + %s"
                update_base_query += " WHERE id_ligneusercards = %s"

                new_power = new_base['attack'] + new_base['health'] + new_base['defence'] + new_base['speed']
                params = [
                    new_base['attack'], new_base['health'], new_base['defence'], new_base['speed'],
                    new_base['attack'], new_base['health'], new_base['defence'], new_base['speed'],
                    new_power, quantity, quantity, card['id_ligneusercards']
                ]
                await execute_query_with_retry(update_base_query, params)

            else:
                # both equipment & support
                true_base = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed':  card['base_speed']
                }
                if equipment_boosts:
                    true_base['attack']  = true_base['attack']  / (1 + equipment_boosts['pct_attack']/100) - equipment_boosts['flat_attack']
                    true_base['health']  = true_base['health']  / (1 + equipment_boosts['pct_health']/100) - equipment_boosts['flat_health']
                    true_base['defence'] = true_base['defence'] / (1 + equipment_boosts['pct_defence']/100) - equipment_boosts['flat_defence']
                    true_base['speed']   = true_base['speed']   / (1 + equipment_boosts['pct_speed']/100) - equipment_boosts['flat_speed']

                new_true = dict(true_base)
                if stat_column == 'attack_stat':  new_true['attack']  += quantity
                if stat_column == 'health_stat':  new_true['health']  += quantity
                if stat_column == 'defence_stat': new_true['defence'] += quantity
                if stat_column == 'speed_stat':   new_true['speed']   += quantity

                new_base = dict(new_true)
                if equipment_boosts:
                    new_base['attack']  = (new_base['attack']  + equipment_boosts['flat_attack'])  * (1 + equipment_boosts['pct_attack']/100)
                    new_base['health']  = (new_base['health']  + equipment_boosts['flat_health'])  * (1 + equipment_boosts['pct_health']/100)
                    new_base['defence'] = (new_base['defence'] + equipment_boosts['flat_defence']) * (1 + equipment_boosts['pct_defence']/100)
                    new_base['speed']   = (new_base['speed']   + equipment_boosts['flat_speed'])   * (1 + equipment_boosts['pct_speed']/100)

                await execute_query_with_retry("""
                    UPDATE user_cards
                    SET attack_stat = %s,
                        health_stat = %s,
                        defence_stat = %s,
                        speed_stat = %s
                    WHERE id_ligneusercards = %s
                """, (new_true['attack'], new_true['health'], new_true['defence'], new_true['speed'], card['id_ligneusercards']))

                update_base_query = """
                UPDATE user_cards
                SET base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    used_statpoints = used_statpoints + %s
                """
                if used_column == 'used_attackpoints':
                    update_base_query += ", used_attackpoints = used_attackpoints + %s"
                elif used_column == 'used_healthpoints':
                    update_base_query += ", used_healthpoints = used_healthpoints + %s"
                elif used_column == 'used_defencepoints':
                    update_base_query += ", used_defencepoints = used_defencepoints + %s"
                elif used_column == 'used_speedpoints':
                    update_base_query += ", used_speedpoints = used_speedpoints + %s"
                update_base_query += " WHERE id_ligneusercards = %s"

                params = [
                    new_base['attack'], new_base['health'], new_base['defence'], new_base['speed'],
                    quantity, quantity, card['id_ligneusercards']
                ]
                await execute_query_with_retry(update_base_query, params)

                await self.apply_equipment_boosts(user_id, card['card_id'], equipment_boosts)
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )

            # Names
            capsule_name_map = {
                'acap': _tr(lang, 'Attack Capsule', 'كبسولة هجوم', 'Capsule d’Attaque'),
                'scap': _tr(lang, 'Speed Capsule',  'كبسولة سرعة', 'Capsule de Vitesse'),
                'hcap': _tr(lang, 'Health Capsule', 'كبسولة صحة',  'Capsule de Santé'),
                'dcap': _tr(lang, 'Defense Capsule','كبسولة دفاع', 'Capsule de Défense')
            }
            readable_capsule_name = capsule_name_map.get(capsule_type.lower(), capsule_type.upper())

            # Updated stats
            updated_stats = await execute_query_with_retry("""
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE id_ligneusercards = %s
            """, (card['id_ligneusercards'],))

            base_msg = _tr(
                lang,
                f"Applied {quantity}x {inventory_item['product_icon']} {readable_capsule_name} to {card['card_icon']} {card['name']}.",
                f"تم تطبيق {quantity}× {inventory_item['product_icon']} {readable_capsule_name} على {card['card_icon']} {card['name']}.",
                f"{quantity}× {inventory_item['product_icon']} {readable_capsule_name} appliquées à {card['card_icon']} {card['name']}."
            )

            if stat_column == 'attack_stat':
                stat_tail = _tr(lang,
                                f" Attack increased to {updated_stats[0]['attack_stat']}.",
                                f" تم رفع الهجوم إلى {updated_stats[0]['attack_stat']}.",
                                f" Attaque augmentée à {updated_stats[0]['attack_stat']}.")
            elif stat_column == 'health_stat':
                stat_tail = _tr(lang,
                                f" Health increased to {updated_stats[0]['health_stat']}.",
                                f" تم رفع الصحة إلى {updated_stats[0]['health_stat']}.",
                                f" Santé augmentée à {updated_stats[0]['health_stat']}.")
            elif stat_column == 'defence_stat':
                stat_tail = _tr(lang,
                                f" Defence increased to {updated_stats[0]['defence_stat']}.",
                                f" تم رفع الدفاع إلى {updated_stats[0]['defence_stat']}.",
                                f" Défense augmentée à {updated_stats[0]['defence_stat']}.")
            else:
                stat_tail = _tr(lang,
                                f" Speed increased to {updated_stats[0]['speed_stat']}.",
                                f" تم رفع السرعة إلى {updated_stats[0]['speed_stat']}.",
                                f" Vitesse augmentée à {updated_stats[0]['speed_stat']}.")
            await ctx.send(base_msg + stat_tail)

        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s'est produite : {str(e)}"
            ))


    @commands.command(name="enhance", aliases=["enh", "en"])
    async def enhance(self, ctx, *, card_name):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send(_tr(lang, "You need to register first.", "يجب أن تقوم بالتسجيل أولاً.", "Vous devez d’abord vous inscrire."))
                return

            user_id = user_result[0]['user_id']

            # Trade lock
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    lang,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في عملية تبادل نشطة. أنهِها أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. إذا كانت هناك مشكلة، افتح تذكرة في خادم الدعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d'utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Market session lock
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    lang,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تقوم حالياً بإنشاء إعلان. يرجى الانتظار حتى يكتمل إنشاء الإعلان الحالي.",
                    "Vous êtes en train de créer une annonce. Veuillez attendre la fin de la création en cours."
                ))
                return

            # Card
            card_query = "SELECT u.*,c.card_icon,c.type_card,uc.max_leveled FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))
            if not card_result:
                await ctx.send(_tr(
                    lang,
                    f"You don't have a card named {card_name}.",
                    f"لا تملك بطاقة باسم {card_name}.",
                    f"Vous n’avez pas de carte nommée {card_name}."
                ))
                return
            if len(card_result) > 1:
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to unequip the weapon from:",
                        "عثرنا على عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة المقصودة:",
                        "Plusieurs cartes correspondent à votre recherche. Veuillez préciser laquelle viser :"
                    ),
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"),
                                value=card_list, inline=False)
                embed.set_footer(text=_tr(lang, "Please use the command again with the exact card name.",
                                        "يرجى إعادة استخدام الأمر باسم البطاقة الدقيق.",
                                        "Veuillez relancer la commande avec le nom exact de la carte."))
                await ctx.send(embed=embed)
                return

            card = card_result[0]
            if card['type_card'] in ['Support','Booster']:
                await ctx.send(_tr(
                    lang,
                    "This command can only be used with Offence-type cards.",
                    "هذا الأمر مخصص لبطاقات الهجوم فقط.",
                    "Cette commande ne peut être utilisée qu’avec des cartes d’attaque."
                ))
                return

        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s'est produite : {str(e)}"))
            return

        # Post-try logic preserved
        if card['current_level'] >= card['max_leveled']:
            await ctx.send(_tr(
                lang,
                f"{card['card_icon']} {card['name']} is already at max level and cannot be enhanced further.",
                f"{card['card_icon']} {card['name']} في الحد الأقصى للمستوى ولا يمكن تعزيزه أكثر.",
                f"{card['card_icon']} {card['name']} est déjà au niveau max et ne peut pas être amélioré davantage."
            ))
            return

        if card['id_enhancer']:
            await ctx.send(_tr(
                lang,
                f"{card['card_icon']} {card['name']} already has an active enhancer.",
                f"{card['card_icon']} {card['name']} لديه مُعزز مفعل بالفعل.",
                f"{card['card_icon']} {card['name']} a déjà un amplificateur actif."
            ))
            return

        # Inventory check (product 17)
        inventory_query = "SELECT ip.*,p.product_icon FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id JOIN product p ON ip.product_id = p.product_id  WHERE i.user_id = %s AND ip.product_id = 17"
        inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
        if not inventory_result:
            await ctx.send(_tr(
                lang,
                "You don't have an enhancer in your inventory.",
                "لا تملك مُعززًا في جردك.",
                "Vous n’avez pas d’amplificateur dans votre inventaire."
            ))
            return

        # Consume enhancer
        if inventory_result[0]['quantiter'] == 1:
            await execute_query_with_retry("DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s",
                                        (inventory_result[0]['id_ligneinventoryproduct'],))
        else:
            await execute_query_with_retry("UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s",
                                        (inventory_result[0]['id_ligneinventoryproduct'],))

        # Apply
        await execute_query_with_retry("UPDATE user_cards SET id_enhancer = 17,fight_count=3 WHERE user_id = %s AND name = %s",
                                    (user_id, card['name']))

        await ctx.send(_tr(
            lang,
            f"Applied 1x {inventory_result[0]['product_icon']} enhancer to {card['card_icon']} {card['name']}. The enhancement process has started.",
            f"تم تطبيق 1x {inventory_result[0]['product_icon']} مُعزز على {card['card_icon']} {card['name']}. بدأت عملية التعزيز.",
            f"1x {inventory_result[0]['product_icon']} amplificateur appliqué à {card['card_icon']} {card['name']}. Le processus d’amélioration a commencé."
        ))


    def remove_duplicates(self, items):
        """
        Remove duplicate items from inventory results by keeping only the first
        occurrence of each item based on name. Quantity is not merged.
        """
        seen = set()
        unique_items = []
        for item in items:
            item_name = item['name']
            if item_name not in seen:
                seen.add(item_name)
                unique_items.append(item)
        return unique_items

    @commands.command(name="inventory", aliases=["inv", "i"])
    async def inventoryview(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        print("lang: ",self.lang)
        try:
            user_id = ctx.author.id
            user_query = """
            SELECT u.user_id, u.quantiter_fragment, u.fragment_cap
            FROM user u
            WHERE u.user_authorid = %s
            """
            user_result = await execute_query_with_retry(user_query, (str(user_id),))
            print(user_result)
            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: User not registered.",
                    "خطأ: المستخدم غير مسجّل.",
                    "Erreur : utilisateur non enregistré."
                ))
                return

            db_user_id = user_result[0]['user_id']
            essence_stone_data = {
                'current': user_result[0]['quantiter_fragment'],
                'cap': user_result[0]['fragment_cap']
            }

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (db_user_id,))

            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "Error: Inventory not found.",
                    "خطأ: لم يتم العثور على الجرد.",
                    "Erreur : inventaire introuvable."
                ))
                return

            inventory_id = inventory_result[0]['inventory_id']

            categories = {
                'weapons': ('inventory_weapon', 'weapon', 'weapon_name', 'weapon_id', 'weapon_id', 'weapon_icon'),
                'boosters_enhancers': ('inventory_product', 'product', 'product_name', 'product_id', 'product_id', 'product_icon'),
                'essence_stones': ('inventory_frags', 'fragments', 'name_fragment', 'id_fragment', 'frag_id', 'fragments_icon'),
                'chests': ('inventory_chest', 'chest', 'chest_name', 'chest_id', 'chest_id', 'chest_icon'),
                'dungeon_key': ('inventory_dungeonkey', 'dungeon_key', 'dungeonkey_name', 'dungeonkey_id', 'dungeonkey_id', 'dungeonkey_icon'),
                'pickaxes': ('inventory_pickaxe', 'pickaxe', 'pickaxe_name', 'id_pickaxe', 'id_pickaxe', 'pickaxe_icon'),
                'dungeon_orbs': ('inventory_orbs', 'leveling_orbs', 'orb_name', 'orb_id', 'orb_id', 'orb_icon'),
                'skills': ('inventory_skills', 'skill', 'skill_name', 'skill_id', 'skill_id', 'skill_icon')
            }
            inventory_data = {}
            for category, (inv_table, item_table, name_column, id_column, invid_column, icon_column) in categories.items():
                quantity_column = 'quantiter'
                query = f"""
                SELECT i.{quantity_column} as quantity,
                    t.{name_column} as name,
                    t.{icon_column} as icon
                FROM {inv_table} i
                JOIN {item_table} t ON i.{invid_column} = t.{id_column}
                WHERE i.inventory_id = %s
                """
                result = await execute_query_with_retry(query, (inventory_id,))
                result = self.remove_duplicates(result)
                inventory_data[category] = result

            view = InventoryMainView(inventory_data, essence_stone_data, ctx.author, lang)
            embed = view.get_main_menu_embed(ctx.author)
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s’est produite : {str(e)}"
            ))


    async def get_user_inventory_data(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        user_id = ctx.author.id
        try:
            user_query = """
            SELECT u.user_id, u.quantiter_fragment, u.fragment_cap
            FROM user u
            WHERE u.user_authorid = %s
            """
            user_result = await execute_query_with_retry(user_query, (str(user_id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: User not registered.",
                    "خطأ: المستخدم غير مسجّل.",
                    "Erreur : utilisateur non enregistré."
                ))
                return None, None

            db_user_id = user_result[0]['user_id']
            essence_stone_data = {
                'current': user_result[0]['quantiter_fragment'],
                'cap': user_result[0]['fragment_cap']
            }

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (db_user_id,))

            if not inventory_result:
                await ctx.send(_tr(
                    lang,
                    "Error: Inventory not found.",
                    "خطأ: لم يتم العثور على الجرد.",
                    "Erreur : inventaire introuvable."
                ))
                return None, None

            return inventory_result[0]['inventory_id'], essence_stone_data
        except Exception as e:
            await ctx.send(_tr(
                lang,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s’est produite : {str(e)}"
            ))
            return None, None


    @commands.command(name="sell", aliases=["sl", "se"])
    async def sell_fragment(self, ctx, *, args):
        lang = await get_user_multilanguage(ctx.author.id)
        langs = await get_user_multilanguage(ctx.author.id)
        try:
            # Parse arguments
            args_parts = args.split()
            if len(args_parts) < 2:
                await ctx.send(_tr(
                    langs,
                    "Error: Please provide either fragment name and quantity or rank. Usage: `sl sell <fragment_name> <quantity/all>` or `sl sell rank <S/A/B/C/D/E>`",
                    "خطأ: يرجى إدخال اسم الحجر والكمية أو الرتبة. الصيغة: `sl sell <اسم_الحجر> <الكمية/all>` أو `sl sell rank <S/A/B/C/D/E>`",
                    "Erreur : indiquez le nom du fragment et la quantité ou le rang. Utilisation : `sl sell <fragment> <quantité/all>` ou `sl sell rank <S/A/B/C/D/E>`"
                ))
                return

            # User
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send(_tr(
                    langs,
                    "Error: User not registered.",
                    "خطأ: المستخدم غير مسجّل.",
                    "Erreur : utilisateur non enregistré."
                ))
                return

            user_id = user_result[0]['user_id']

            # Ascending guard
            ascending_check_query = "SELECT * FROM ascending_state WHERE user_id = %s"
            ascending_result = await execute_query_with_retry(ascending_check_query, (user_id,))
            if ascending_result:
                await ctx.send(_tr(
                    langs,
                    "⏳ You are already performing an ascension. Please wait for it to complete.",
                    "⏳ أنت تقوم بالفعل بعملية ارتقاء. يرجى الانتظار حتى تكتمل.",
                    "⏳ Vous êtes déjà en ascension. Veuillez attendre la fin."
                ))
                return

            # Trade & market locks
            query = """
            SELECT id_tradestatus, initiator_id, target_id
            FROM trade_status
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            if result:
                await ctx.send(_tr(
                    langs,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في تبادل نشط. أنهِ العملية أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. عند وجود مشكلة، افتح تذكرة دعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d’utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return
            market_session_query = "SELECT user_id FROM market_session WHERE user_id = %s"
            market_session_result = await execute_query_with_retry(market_session_query, (user_id,))
            if market_session_result:
                await ctx.send(_tr(
                    langs,
                    "You are currently creating a listing. Please wait for the current listing creation to complete.",
                    "أنت تنشئ حالياً قائمة. يرجى الانتظار حتى تكتمل العملية.",
                    "Vous créez actuellement une annonce. Veuillez attendre la fin de la création."
                ))
                return

            # Pending sell guard
            pending_sell_query = "SELECT id FROM sell_status WHERE user_id = %s"
            pending_sell_result = await execute_query_with_retry(pending_sell_query, (user_id,))
            if pending_sell_result:
                await ctx.send(_tr(
                    langs,
                    "⚠️ You already have a pending sell operation. Please complete or cancel your previous sell before starting a new one.",
                    "⚠️ لديك عملية بيع قيد الانتظار بالفعل. أكمل أو ألغِ السابقة قبل البدء بجديدة.",
                    "⚠️ Vous avez déjà une vente en attente. Terminez ou annulez-la avant d’en commencer une nouvelle."
                ))
                return

            # Mark as pending
            await execute_query_with_retry("INSERT INTO sell_status (user_id) VALUES (%s)", (user_id,))

            # Inventory
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                await ctx.send(_tr(
                    langs,
                    "Error: Inventory not found.",
                    "خطأ: لم يتم العثور على الجرد.",
                    "Erreur : inventaire introuvable."
                ))
                return

            inventory_id = inventory_result[0]['inventory_id']
            is_rank_sale = args_parts[0].lower() == "rank"

            if is_rank_sale:
                if len(args_parts) != 2:
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(_tr(
                        langs,
                        "Error: For rank-based sales, please use the format: `sl sell rank <S/A/B/C/D/E>`",
                        "خطأ: لبيع حسب الرتبة استخدم: `sl sell rank <S/A/B/C/D/E>`",
                        "Erreur : pour la vente par rang, utilisez : `sl sell rank <S/A/B/C/D/E>`"
                    ))
                    return

                rank = args_parts[1].upper()
                rank_map = {'S': 2, 'A': 3, 'B': 4, 'C': 5, 'D': 6, 'E': 7}
                if rank not in rank_map:
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(_tr(
                        langs,
                        "Error: Invalid rank. Please use S, A, B, C, D, or E.",
                        "خطأ: رتبة غير صالحة. استخدم S أو A أو B أو C أو D أو E.",
                        "Erreur : rang invalide. Utilisez S, A, B, C, D ou E."
                    ))
                    return

                cardrank_id = rank_map[rank]
                rank_query = """
                    SELECT f.id_fragment, f.name_fragment, f.price, f.fragments_icon,
                        inf.quantiter as owned_quantity
                    FROM fragments f
                    JOIN card c ON f.id_fragment = c.id_fragment
                    JOIN inventory_frags inf ON f.id_fragment = inf.frag_id
                    WHERE inf.inventory_id = %s
                    AND c.card_rank = %s
                    AND inf.quantiter > 0
                """
                fragment_results = await execute_query_with_retry(rank_query, (inventory_id, cardrank_id))
                if not fragment_results:
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(_tr(
                        langs,
                        f"Error: You don't have any fragments of rank {rank}.",
                        f"خطأ: ليس لديك أي أحجار جوهرية برتبة {rank}.",
                        f"Erreur : vous n’avez aucun fragment de rang {rank}."
                    ))
                    return

                total_amount = 0
                fragments_summary = []
                for frag in fragment_results:
                    amount = frag['owned_quantity'] * frag['price']
                    total_amount += amount
                    fragments_summary.append({
                        'id': frag['id_fragment'],
                        'name': frag['name_fragment'],
                        'quantity': frag['owned_quantity'],
                        'price': frag['price'],
                        'amount': amount,
                        'icon': frag['fragments_icon']
                    })

                confirm_embed = discord.Embed(
                    title=_tr(langs,
                            f"Sell All Rank {rank} Essence Stones",
                            f"بيع كل أحجار الجوهر رتبة {rank}",
                            f"Vendre toutes les pierres d’essence rang {rank}"),
                    description=_tr(langs,
                                    "Are you sure you want to sell the following essence stones?",
                                    "هل أنت متأكد من بيع أحجار الجوهر التالية؟",
                                    "Êtes-vous sûr de vouloir vendre ces pierres d’essence ?"),
                    color=discord.Color.gold()
                )
                summary_text = ""
                for frag in fragments_summary:
                    summary_text += f"{frag['icon']} {frag['name']} x{frag['quantity']} ({frag['amount']:,} <:won:1304034228966658048>)\n"
                confirm_embed.add_field(
                    name=_tr(langs, "Essence Stones to Sell", "أحجار الجوهر المراد بيعها", "Pierres d’essence à vendre"),
                    value=summary_text, inline=False
                )
                confirm_embed.add_field(
                    name=_tr(langs, "Total Amount", "المجموع", "Montant total"),
                    value=f"{total_amount:,} <:won:1304034228966658048>", inline=False
                )
            else:
                quantity = args_parts[-1].lower()
                fragment_name = " ".join(args_parts[:-1])
                fragment_query = """
                SELECT id_fragment, name_fragment, price, fragments_icon
                FROM fragments
                WHERE name_fragment LIKE %s
                """
                fragment_results = await execute_query_with_retry(fragment_query, (f"%{fragment_name}%",))
                if not fragment_results:
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(_tr(
                        langs,
                        "Error: Fragment not found.",
                        "خطأ: لم يتم العثور على الحجر.",
                        "Erreur : fragment introuvable."
                    ))
                    return

                if len(fragment_results) > 1:
                    embed = discord.Embed(
                        title=_tr(langs, "Multiple Essence Stones Found", "تم العثور على عدة أحجار جوهر", "Plusieurs pierres d’essence trouvées"),
                        description=_tr(langs,
                                        "I found multiple stones matching your search. Please specify which one you'd like to sell:",
                                        "عثرنا على عدة أحجار تطابق بحثك. حدد أيّها تريد بيعها:",
                                        "Plusieurs pierres correspondent à votre recherche. Précisez laquelle vendre :"),
                        color=discord.Color.blue()
                    )
                    fragment_list = "\n".join([f"• {frag['name_fragment']}" for frag in fragment_results])
                    embed.add_field(
                        name=_tr(langs, "Matching Stones", "الأحجار المطابقة", "Pierres correspondantes"),
                        value=fragment_list, inline=False
                    )
                    embed.set_footer(text=_tr(
                        langs,
                        "Please use the command again with the exact name of the essence stone you want to sell.",
                        "يرجى إعادة استخدام الأمر باسم الحجر الدقيق الذي تريد بيعه.",
                        "Veuillez relancer la commande avec le nom exact de la pierre à vendre."
                    ))
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(embed=embed)
                    return

                fragment_data = fragment_results[0]
                frag_id = fragment_data['id_fragment']
                fragment_price = fragment_data['price']

                inventory_check_query = """
                SELECT quantiter
                FROM inventory_frags
                WHERE inventory_id = %s AND frag_id = %s AND locked != 1
                """
                inventory_check = await execute_query_with_retry(inventory_check_query, (inventory_id, frag_id))
                if not inventory_check:
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(_tr(
                        langs,
                        "Error: You don't have any of these essence stones.",
                        "خطأ: لا تملك أيًا من هذه الأحجار.",
                        "Erreur : vous ne possédez aucune de ces pierres."
                    ))
                    return

                current_quantity = inventory_check[0]['quantiter']
                if quantity == 'all':
                    sell_quantity = current_quantity
                else:
                    try:
                        sell_quantity = int(quantity)
                        if sell_quantity <= 0:
                            await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                            await ctx.send(_tr(
                                langs,
                                "Error: Please enter a positive quantity.",
                                "خطأ: أدخل كمية موجبة.",
                                "Erreur : saisissez une quantité positive."
                            ))
                            return
                        if sell_quantity > current_quantity:
                            await ctx.send(_tr(
                                langs,
                                f"Error: You only have {current_quantity} fragments.",
                                f"خطأ: لديك فقط {current_quantity} حجرًا.",
                                f"Erreur : vous n’avez que {current_quantity} fragments."
                            ))
                            await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                            return
                    except ValueError:
                        await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                        await ctx.send(_tr(
                            langs,
                            "Error: Invalid quantity. Please use a number or 'all'.",
                            "خطأ: كمية غير صالحة. استخدم رقمًا أو 'all'.",
                            "Erreur : quantité invalide. Utilisez un nombre ou 'all'."
                        ))
                        return

                total_amount = sell_quantity * fragment_price
                fragments_summary = [{
                    'id': frag_id,
                    'name': fragment_data['name_fragment'],
                    'quantity': sell_quantity,
                    'price': fragment_price,
                    'amount': total_amount,
                    'icon': fragment_data['fragments_icon']
                }]

                confirm_embed = discord.Embed(
                    title=_tr(lang, "Sell Confirmation", "تأكيد البيع", "Confirmation de vente"),
                    description=_tr(
                        langs,
                        f"Are you sure you want to sell {sell_quantity} {fragment_data['fragments_icon']} {fragment_data['name_fragment']}?",
                        f"هل أنت متأكد من بيع {sell_quantity} {fragment_data['fragments_icon']} {fragment_data['name_fragment']}؟",
                        f"Voulez-vous vraiment vendre {sell_quantity} {fragment_data['fragments_icon']} {fragment_data['name_fragment']} ?"
                    ),
                    color=discord.Color.gold()
                )
                confirm_embed.add_field(
                    name=_tr(langs, "Price per Stone", "السعر لكل حجر", "Prix par pierre"),
                    value=f"{fragment_price:,} <:won:1304034228966658048>",
                    inline=True
                )
                confirm_embed.add_field(
                    name=_tr(langs, "Total Amount", "المجموع", "Montant total"),
                    value=f"{total_amount:,} <:won:1304034228966658048>",
                    inline=True
                )

            # Interactive confirmation
            view = SellConfirmation(ctx, fragments_summary, total_amount, langs)
            initial_embed = await view.start(ctx)
            confirm_message = await ctx.send(embed=initial_embed, view=view)

            await view.wait()
            await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))

            if view.value is None:
                await ctx.send(_tr(
                    langs,
                    "Sale cancelled - you didn't respond in time.",
                    "تم إلغاء البيع - لم ترد في الوقت المناسب.",
                    "Vente annulée – vous n’avez pas répondu à temps."
                ))
                return

            if not view.value:
                await ctx.send(_tr(
                    langs,
                    "Sale cancelled.",
                    "تم إلغاء البيع.",
                    "Vente annulée."
                ))
                return

            # Re-check trade
            trade_result = await execute_query_with_retry(query, (user_id, user_id))
            if trade_result:
                await ctx.send(_tr(
                    langs,
                    "You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server",
                    "أنت حالياً في تبادل نشط. أنهِ العملية أو انتظر انتهاء فترة التهدئة قبل استخدام هذا الأمر. عند وجود مشكلة، افتح تذكرة دعم.",
                    "Vous êtes actuellement dans un échange actif. Terminez-le ou attendez la fin du délai de recharge avant d’utiliser cette commande. En cas de souci, ouvrez un ticket sur le serveur d’assistance."
                ))
                return

            # Verify fragments still present
            for fragment in fragments_summary:
                inventory_check_query = """
                SELECT quantiter
                FROM inventory_frags
                WHERE inventory_id = %s AND frag_id = %s AND locked != 1
                """
                inventory_check = await execute_query_with_retry(inventory_check_query, (inventory_id, fragment['id']))
                if not inventory_check or inventory_check[0]['quantiter'] < fragment['quantity']:
                    await ctx.send(_tr(
                        langs,
                        f"Error: You no longer have enough {fragment['name']} to complete this sale.",
                        f"خطأ: لم يعد لديك ما يكفي من {fragment['name']} لإكمال البيع.",
                        f"Erreur : vous n’avez plus assez de {fragment['name']} pour finaliser la vente."
                    ))
                    return

            # Process sale
            for fragment in fragments_summary:
                await execute_query_with_retry(
                    "DELETE FROM inventory_frags WHERE inventory_id = %s AND frag_id = %s",
                    (inventory_id, fragment['id'])
                )

            total_fragments_sold = sum(f['quantity'] for f in fragments_summary)
            currency_update_query = """
            UPDATE user
            SET won_currency = won_currency + %s,
                quantiter_fragment = quantiter_fragment - %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(currency_update_query, (total_amount, total_fragments_sold, user_id))

            # Quest progress (Sacrifice 5 Essence Stones)
            quest_check_query = """
            SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count
            FROM user_quests uq
            JOIN quest q ON uq.quest_id = q.quest_id
            WHERE uq.user_id = %s AND q.quest_name = 'Sacrifice 5 Essence Stones'
            """
            quest_result = await execute_query_with_retry(quest_check_query, (user_id,))
            if quest_result:
                quest = quest_result[0]
                if quest['completion_status'] == 0:
                    new_count = quest['current_count'] + total_fragments_sold
                    if new_count > quest['required_count']:
                        new_count = quest['required_count']
                    completion_status = 1 if new_count >= quest['required_count'] else 0
                    await execute_query_with_retry(
                        "UPDATE user_quests SET current_count = %s, completion_status = %s WHERE userquests_id = %s",
                        (new_count, completion_status, quest['userquests_id'])
                    )
                    if completion_status == 1:
                        check_chest_query = "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = 1"
                        chest_result = await execute_query_with_retry(check_chest_query, (inventory_id,))
                        if chest_result:
                            await execute_query_with_retry(
                                "UPDATE inventory_chest SET quantiter = quantiter + 1 WHERE inventory_id = %s AND chest_id = 1",
                                (inventory_id,)
                            )
                        else:
                            await execute_query_with_retry(
                                "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, 1, 1)",
                                (inventory_id,)
                            )

            # Success embed
            if is_rank_sale:
                success_embed = discord.Embed(
                    title=_tr(langs, "Rank Sale Successful", "تم بيع رتبة بنجاح", "Vente par rang réussie"),
                    description=_tr(
                        langs,
                        f"Successfully sold all Rank {rank} essence stones for {total_amount:,} 💰",
                        f"تم بيع كل أحجار الجوهر رتبة {rank} مقابل {total_amount:,} 💰",
                        f"Toutes les pierres d’essence de rang {rank} vendues pour {total_amount:,} 💰"
                    ),
                    color=discord.Color.green()
                )
            else:
                success_embed = discord.Embed(
                    title=_tr(langs, "Sale Successful", "تم البيع بنجاح", "Vente réussie"),
                    description=_tr(
                        langs,
                        f"Successfully sold {fragments_summary[0]['quantity']} {fragments_summary[0]['name']} for {total_amount:,} 💰",
                        f"تم بيع {fragments_summary[0]['quantity']} {fragments_summary[0]['name']} مقابل {total_amount:,} 💰",
                        f"{fragments_summary[0]['quantity']} {fragments_summary[0]['name']} vendus pour {total_amount:,} 💰"
                    ),
                    color=discord.Color.green()
                )
            await ctx.send(embed=success_embed)

        except Exception as e:
            # Ensure cleanup
            if 'user_id' in locals():
                try:
                    await execute_query_with_retry("DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                except:
                    pass
            await ctx.send(_tr(
                langs,
                f"An error occurred: {str(e)}",
                f"حدث خطأ: {str(e)}",
                f"Une erreur s’est produite : {str(e)}"
            ))


    @commands.command(name="invpickaxe", aliases=["ip", "ivp"])
    async def inventory_pickaxes(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'pickaxes',
                'inventory_pickaxe', 'pickaxe',
                'pickaxe_name', 'id_pickaxe', 'id_pickaxe', 'pickaxe_icon',
                _tr(lang, "⛏️ Pickaxes", "⛏️ المعاول", "⛏️ Pioches"),
                _tr(lang,
                    "Tools of resource extraction, each pickaxe a testament to your mining prowess. Forge your path through stone and ore, uncovering hidden treasures.",
                    "أدوات لاستخراج الموارد، كل معول شهادة على براعتك في التعدين. اشق طريقك عبر الحجر والخام لاكتشاف الكنوز.",
                    "Outils d’extraction, chaque pioche témoigne de votre maîtrise minière. Frayez-vous un chemin dans la roche pour découvrir des trésors cachés."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


    @commands.command(name="invweapon", aliases=["iw", "iwp"])
    async def inventory_weapons(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'weapons',
                'inventory_weapon', 'weapon',
                'weapon_name', 'weapon_id', 'weapon_id', 'weapon_icon',
                _tr(lang, "⚔️ Weapons", "⚔️ الأسلحة", "⚔️ Armes"),
                _tr(lang,
                    "In the darkness of dungeons, these weapons are your trusted companions. Each blade, infused with power, stands ready to strike down those who would challenge your ascension.",
                    "في ظلمات الأبراج، هذه الأسلحة رفقاؤك الموثوقون. كل شفرة مشحونة بالقوة، مستعدة لإسقاط من يتحدّى صعودك.",
                    "Dans l’obscurité des donjons, ces armes sont vos compagnes fidèles. Chaque lame, emplie de puissance, est prête à abattre vos adversaires."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


    @commands.command(name="invskill", aliases=["iskill", "isk"])
    async def inventory_skills(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'skills',
                'inventory_skills', 'skill',
                'skill_name', 'skill_id', 'skill_id', 'skill_icon',
                _tr(lang, "🎯 Skills", "🎯 المهارات", "🎯 Compétences"),
                _tr(lang,
                    "Abilities honed through countless battles and training. Each skill represents your growth as a Hunter, ready to unleash devastating power upon your enemies.",
                    "قدرات صُقلت عبر معارك وتدريبات لا تُحصى. كل مهارة تمثل نمّوك كصيّاد، مستعدة لإطلاق قوة مدمرة على أعدائك.",
                    "Des aptitudes forgées par d’innombrables combats et entraînements. Chaque compétence reflète votre progression de chasseur, prête à libérer une puissance dévastatrice."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


    @commands.command(name="invorbs", aliases=["io", "iorb"])
    async def inventory_orbs(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'dungeon_orbs',
                'inventory_orbs', 'leveling_orbs',
                'orb_name', 'orb_id', 'orb_id', 'orb_icon',
                _tr(lang, "🔮 Dungeon Orbs", "🔮 كُرات الزنزانات", "🔮 Orbes de donjon"),
                _tr(lang,
                    "Mystical orbs earned from conquering dungeons of various ranks. Each orb pulses with the essence of victory, ready to enhance your leveling journey.",
                    "كرات سحرية تُكتسب من غزو زنزانات بمستويات مختلفة. كل كرة تنبض بجوهر النصر، جاهزة لتعزيز رحلة تطويرك.",
                    "Des orbes mystiques gagnés en conquérant des donjons de rangs variés. Chaque orbe vibre de l’essence de la victoire pour accélérer votre progression."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


    @commands.command(name="invbooster", aliases=["ib", "ibt"])
    async def inventory_boosters(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'boosters_enhancers',
                'inventory_product', 'product',
                'product_name', 'product_id', 'product_id', 'product_icon',
                _tr(lang, "📊 Boosters & Enhancers", "📊 المعززات والمقويات", "📊 Boosters & Amplificateurs"),
                _tr(lang,
                    "Tools of advancement that push beyond mortal limits. Through these enhancers, even E-rank hunters can glimpse the power of S-rank might.",
                    "أدوات للتقدّم تتجاوز الحدود البشرية. عبر هذه المقويات، حتى الصيادون رتبة E يمكنهم تذوق قوة رتبة S.",
                    "Des outils de progression qui dépassent les limites humaines. Grâce à ces amplificateurs, même les chasseurs de rang E goûtent à la puissance du rang S."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


    @commands.command(name="invstone", aliases=["is", "ist"])
    async def inventory_stones(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'essence_stones',
                'inventory_frags', 'fragments',
                'name_fragment', 'id_fragment', 'frag_id', 'fragments_icon',
                _tr(lang, "💎 Essence Stones", "💎 أحجار الجوهر", "💎 Pierres d’essence"),
                _tr(lang,
                    "Crystallized essence of fallen monsters, pulsing with raw power. These stones hold the key to strengthening your abilities and ascending to greater heights.",
                    "جوهر متبلور لوحوش ساقطة، ينبض بقوة خام. تحمل هذه الأحجار مفتاح تقوية قدراتك وترقيتك لمراتب أعلى.",
                    "L’essence cristallisée de monstres vaincus, vibrant d’une puissance brute. Ces pierres sont la clé pour renforcer vos capacités et vous élever."
                ),
                lang,
                essence_stone_data
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


    @commands.command(name="invchest", aliases=["ic", "ich"])
    async def inventory_chests(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'chests',
                'inventory_chest', 'chest',
                'chest_name', 'chest_id', 'chest_id', 'chest_icon',
                _tr(lang, "🎁 Chests", "🎁 الصناديق", "🎁 Coffres"),
                _tr(lang,
                    "Rewards claimed from conquered dungeons and fallen foes. Each chest holds the promise of power, waiting to be unleashed.",
                    "مكافآت تُنتزع من زنزانات مقهورة وأعداء ساقطين. كل صندوق يحمل وعدًا بالقوة بانتظار إطلاقه.",
                    "Des récompenses arrachées aux donjons conquis et aux ennemis vaincus. Chaque coffre promet du pouvoir, prêt à être libéré."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur س’est produite : {str(e)}"))


    @commands.command(name="invkey", aliases=["ik", "ikey"])
    async def inventory_keys(self, ctx):
        lang = await get_user_multilanguage(ctx.author.id)
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)
            view = SingleCategoryView(
                ctx, self.bot, inventory_id, 'dungeon_key',
                'inventory_dungeonkey', 'dungeon_key',
                'dungeonkey_name', 'dungeonkey_id', 'dungeonkey_id', 'dungeonkey_icon',
                _tr(lang, "🗝️ Dungeon Keys", "🗝️ مفاتيح الزنزانات", "🗝️ Clés de donjon"),
                _tr(lang,
                    "Gates to new challenges await. These keys unlock dungeons of varying difficulty, each promising greater rewards for those brave enough to enter.",
                    "بوابات لتحديات جديدة بانتظارك. هذه المفاتيح تفتح زنزانات متفاوتة الصعوبة، كل منها يعد بمكافآت أكبر للأشجع.",
                    "Des portes vers de nouveaux défis. Ces clés ouvrent des donjons de difficultés variées, promettant de plus grandes récompenses aux plus audacieux."
                ),
                self.lang
            )
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))


class SellConfirmation(discord.ui.View):
    def __init__(self, ctx, fragments_summary, total_amount, lang: int):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.fragments_summary = fragments_summary
        self.total_amount = total_amount
        self.value = None
        self.current_page = 0
        self.items_per_page = 5
        self.max_pages = (len(fragments_summary) - 1) // self.items_per_page + 1
        self.lang = lang

    def get_current_page_content(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = start_idx + self.items_per_page
        return self.fragments_summary[start_idx:end_idx]

    @discord.ui.button(label="<", style=discord.ButtonStyle.gray, disabled=True, custom_id="previous")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.current_page = max(0, self.current_page - 1)
        await self.update_message(interaction)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.value = True
        self.stop()
        await interaction.message.delete()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.value = False
        self.stop()
        await interaction.message.delete()

    @discord.ui.button(label=">", style=discord.ButtonStyle.gray, disabled=True, custom_id="next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.current_page = min(self.max_pages - 1, self.current_page + 1)
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        self.previous_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page >= self.max_pages - 1

        embed = discord.Embed(
            title=_tr(self.lang, "Sell Confirmation", "تأكيد البيع", "Confirmation de vente"),
            description=_tr(self.lang, "Are you sure you want to sell these essence stones?",
                            "هل أنت متأكد من بيع أحجار الجوهر هذه؟",
                            "Êtes-vous sûr de vouloir vendre ces pierres d’essence ?"),
            color=discord.Color.gold()
        )

        current_fragments = self.get_current_page_content()
        summary_text = ""
        for frag in current_fragments:
            summary_text += f"{frag['icon']} {frag['name']} x{frag['quantity']} ({frag['amount']:,} <:won:1304034228966658048>)\n"

        field_name = (_tr(self.lang,
                        f"Essence Stones to Sell (Page {self.current_page + 1}/{self.max_pages})",
                        f"أحجار الجوهر المراد بيعها (الصفحة {self.current_page + 1}/{self.max_pages})",
                        f"Pierres d’essence à vendre (Page {self.current_page + 1}/{self.max_pages})")
                    if len(self.fragments_summary) > self.items_per_page
                    else _tr(self.lang, "Essence Stones to Sell", "أحجار الجوهر المراد بيعها", "Pierres d’essence à vendre"))

        embed.add_field(name=field_name, value=summary_text, inline=False)
        embed.add_field(name=_tr(self.lang, "Total Amount", "المجموع", "Montant total"),
                        value=f"{self.total_amount:,} <:won:1304034228966658048>", inline=False)

        # Localize button labels
        self.confirm.label = _tr(self.lang, "Confirm", "تأكيد", "Confirmer")
        self.cancel.label = _tr(self.lang, "Cancel", "إلغاء", "Annuler")

        await interaction.response.edit_message(embed=embed, view=self)

    async def start(self, ctx):
        # Localize button labels for the first render
        self.confirm.label = _tr(self.lang, "Confirm", "تأكيد", "Confirmer")
        self.cancel.label = _tr(self.lang, "Cancel", "إلغاء", "Annuler")
        self.previous_page.label = "<"
        self.next_page.label = ">"

        embed = discord.Embed(
            title=_tr(self.lang, "Sell Confirmation", "تأكيد البيع", "Confirmation de vente"),
            description=_tr(self.lang, "Are you sure you want to sell these essence stones?",
                            "هل أنت متأكد من بيع أحجار الجوهر هذه؟",
                            "Êtes-vous sûr de vouloir vendre ces pierres d’essence ?"),
            color=discord.Color.gold()
        )

        current_fragments = self.get_current_page_content()
        summary_text = ""
        for frag in current_fragments:
            summary_text += f"{frag['icon']} {frag['name']} x{frag['quantity']} ({frag['amount']:,} <:won:1304034228966658048>)\n"

        if len(self.fragments_summary) > self.items_per_page:
            embed.add_field(
                name=_tr(self.lang,
                        f"Essence Stones to Sell (Page {self.current_page + 1}/{self.max_pages})",
                        f"أحجار الجوهر المراد بيعها (الصفحة {self.current_page + 1}/{self.max_pages})",
                        f"Pierres d’essence à vendre (Page {self.current_page + 1}/{self.max_pages})"),
                value=summary_text, inline=False
            )
            self.previous_page.disabled = True
            self.next_page.disabled = self.max_pages <= 1
        else:
            embed.add_field(name=_tr(self.lang, "Essence Stones to Sell", "أحجار الجوهر المراد بيعها", "Pierres d’essence à vendre"),
                            value=summary_text, inline=False)
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)

        embed.add_field(name=_tr(self.lang, "Total Amount", "المجموع", "Montant total"),
                        value=f"{self.total_amount:,} <:won:1304034228966658048>", inline=False)
        return embed


class SingleCategoryView(discord.ui.View):
    def __init__(self, ctx, botinstance, inventory_id, category, inv_table, item_table,
                name_column, id_column, invid_column, icon_column, title, description,
                lang: int, essence_stone_data=None):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = botinstance
        self.inventory_id = inventory_id
        self.category = category
        self.inv_table = inv_table
        self.item_table = item_table
        self.name_column = name_column
        self.id_column = id_column
        self.invid_column = invid_column
        self.icon_column = icon_column
        self.title = title
        self.description = description
        self.lang = lang
        self.essence_stone_data = essence_stone_data
        self.current_page = 1
        self.items_per_page = 10
        self.add_item(self.PreviousButton())
        self.add_item(self.NextButton())
        self.db_pool = None

    def remove_duplicates(self, items):
        seen = set()
        unique_items = []
        for item in items:
            item_name = item['name']
            if item_name not in seen:
                seen.add(item_name)
                unique_items.append(item)
        return unique_items

    async def get_category_embed(self):
        if self.category == 'skills':
            query = f"""
                SELECT i.quantiter as quantity,
                    t.{self.name_column} as name,
                    t.{self.icon_column} as icon,
                    t.card_class as class
                FROM {self.inv_table} i
                JOIN {self.item_table} t ON i.{self.invid_column} = t.{self.id_column}
                WHERE i.inventory_id = %s
            """
        else:
            query = f"""
                SELECT i.quantiter as quantity,
                    t.{self.name_column} as name,
                    t.{self.icon_column} as icon
                FROM {self.inv_table} i
                JOIN {self.item_table} t ON i.{self.invid_column} = t.{self.id_column}
                WHERE i.inventory_id = %s
            """
        result = await execute_query_with_retry(query, (self.inventory_id,))
        result = self.remove_duplicates(result)
        total_pages = math.ceil(len(result) / self.items_per_page) or 1

        if self.current_page < 1:
            self.current_page = total_pages
        elif self.current_page > total_pages:
            self.current_page = 1

        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = start_idx + self.items_per_page
        current_items = result[start_idx:end_idx]

        embed = discord.Embed(title=self.title, color=0x000000)
        embed.set_author(name=_tr(self.lang, f"{self.ctx.author.name}'s Inventory",
                                f"جرد {self.ctx.author.name}",
                                f"Inventaire de {self.ctx.author.name}"),
                        icon_url=self.ctx.author.avatar.url if self.ctx.author.avatar else None)
        embed.description = self.description

        if not current_items:
            embed.description += "\n\n" + _tr(
                self.lang,
                "*Your inventory for this category is empty. Venture forth to gather more power!*",
                "*جردك في هذه الفئة فارغ. انطلق لجمع المزيد من القوة!*",
                "*Votre inventaire pour cette catégorie est vide. Partez gagner en puissance !*"
            )
        else:
            items_display = []
            for item in current_items:
                if self.category == 'skills':
                    class_emotes = {1: "⚔️", 3: "🔮", 4: "🏹", 5: "🛡️", 6: "🗡️"}
                    skill_class = item.get('class', 0)
                    icon = class_emotes.get(skill_class, "🎯")
                else:
                    icon = item['icon'] if item['icon'] else "❓"
                items_display.append(f"{icon} **{item['name']}** (x{item['quantity']})")

            embed.description += "\n\n" + "\n\n".join(items_display)

        if self.category == 'essence_stones' and self.essence_stone_data:
            current = self.essence_stone_data['current']
            cap = self.essence_stone_data['cap']
            footer_text = _tr(
                self.lang,
                f"Page {self.current_page}/{total_pages} | Essence Stone Capacity: {current:,}/{cap:,}",
                f"الصفحة {self.current_page}/{total_pages} | سعة أحجار الجوهر: {current:,}/{cap:,}",
                f"Page {self.current_page}/{total_pages} | Capacité des pierres d’essence : {current:,}/{cap:,}"
            )
        else:
            footer_text = _tr(
                self.lang,
                f"Page {self.current_page}/{total_pages}",
                f"الصفحة {self.current_page}/{total_pages}",
                f"Page {self.current_page}/{total_pages}"
            )
        embed.set_footer(text=footer_text)
        return embed

    class PreviousButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label="<", custom_id="previous")

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.view.ctx.author.id:
                return
            self.view.current_page -= 1
            embed = await self.view.get_category_embed()
            await interaction.response.edit_message(embed=embed, view=self.view)

    class NextButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label=">", custom_id="next")

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.view.ctx.author.id:
                return
            self.view.current_page += 1
            embed = await self.view.get_category_embed()
            await interaction.response.edit_message(embed=embed, view=self.view)


class InventoryMainView(discord.ui.View):
    def __init__(self, inventory_data, essence_stone_data, original_user, lang: int):
        super().__init__(timeout=None)
        self.inventory_data = inventory_data
        self.essence_stone_data = essence_stone_data
        self.original_user = original_user
        self.lang = lang
        self.add_item(self.CategorySelect(original_user, lang))

    def clear_items(self):
        self.children.clear()

    def get_main_menu_embed(self, user):
        lang = self.lang
        print(lang)
        title = _tr(lang, "Shadow Sovereign's Inventory", "جرد سيد الظلال", "Inventaire du Souverain des Ombres")
        embed = discord.Embed(title=title, color=0x000000)
        embed.set_author(
            name=_tr(lang, f"Welcome, {user.name}", f"مرحباً، {user.name}", f"Bienvenue, {user.name}"),
            icon_url=user.avatar.url if user.avatar else None
        )
        print("cat lang:", lang)
        embed.description = _tr(
            lang,
            "As you rise through the ranks of Hunters, your inventory grows in power and mystery. "
            "What secrets and strengths have you gathered on your journey?\n\n"
            "Choose a category to inspect your items:",
            "مع صعودك عبر رتب الصيادين، ينمو جردك قوةً وغموضًا. "
            "ما الأسرار والقوى التي جمعتها في رحلتك؟\n\n"
            "اختر فئة لمعاينة مقتنياتك:",
            "À mesure que vous gravissez les rangs des chasseurs, votre inventaire gagne en puissance et en mystère. "
            "Quels secrets et forces avez-vous rassemblés ?\n\n"
            "Choisissez une catégorie pour examiner vos objets :"
        )

        categories = [
            ("⚔️", _tr(lang, "Weapons", "الأسلحة", "Armes"),
            _tr(lang, "Blades that cleave through shadows", "شفرات تشق الظلال", "Des lames qui fendent les ombres")),
            ("📊", _tr(lang, "Boosters & Enhancers", "المعززات والمقويات", "Boosters & Amplificateurs"),
            _tr(lang, "Enhance your potential, surpass your limits", "عزّز إمكاناتك وتجاوز حدودك", "Développez votre potentiel, dépassez vos limites")),
            ("💎", _tr(lang, "Essence Stones", "أحجار الجوهر", "Pierres d’essence"),
            _tr(lang, "Crystallized power of fallen monsters", "قوة متبلورة لوحوش ساقطة", "Pouvoir cristallisé de monstres vaincus")),
            ("🎁", _tr(lang, "Chests", "الصناديق", "Coffres"),
            _tr(lang, "Treasures awaiting to be claimed", "كنوز تنتظر أن تُنتزع", "Des trésors n’attendent qu’à être réclamés")),
            ("🗝️", _tr(lang, "Dungeon keys", "مفاتيح الزنزانات", "Clés de donjon"),
            _tr(lang, "Keys to new challenges", "مفاتيح لتحديات جديدة", "Des clés vers de nouveaux défis")),
            ("⛏️", _tr(lang, "Pickaxes", "المعاول", "Pioches"),
            _tr(lang, "Tools of resource extraction and mining", "أدوات لاستخراج الموارد والتعدين", "Outils d’extraction et de minage")),
            ("🔮", _tr(lang, "Dungeon Orbs", "كرات الزنزانات", "Orbes de donjon"),
            _tr(lang, "Mystical orbs earned from dungeon victories", "كرات سحرية تُكتسب من الانتصارات في الزنزانات", "Orbes mystiques gagnés dans les donjons")),
            ("🎯", _tr(lang, "Skills", "المهارات", "Compétences"),
            _tr(lang, "Abilities mastered through training and battle", "قدرات أتقنتها عبر التدريب والقتال", "Aptitudes maîtrisées par l’entraînement et le combat")),
        ]

        for emoji, category, description in categories:
            embed.add_field(name=f"{emoji} {category}", value=description, inline=False)

        embed.set_footer(text=_tr(lang,
                                "Solo Leveling: Rise as the Shadow Monarch",
                                "Solo Leveling: انهض كملك الظلال",
                                "Solo Leveling : Élevez-vous en Monarque des Ombres"))
        embed.set_image(url="https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/01/solo-leveling-anime-to-premiere-this-week-on-crunchyroll.jpg")
        return embed

    class CategorySelect(discord.ui.Select):
        def __init__(self, original_user, lang: int):
            self.original_user = original_user
            self.lang = lang
            options = [
                discord.SelectOption(label=_tr(lang, "Weapons", "الأسلحة", "Armes"), emoji="⚔️", value="weapons"),
                discord.SelectOption(label=_tr(lang, "Boosters & Enhancers", "المعززات والمقويات", "Boosters & Amplificateurs"), emoji="📊", value="boosters_enhancers"),
                discord.SelectOption(label=_tr(lang, "Essence Stones", "أحجار الجوهر", "Pierres d’essence"), emoji="💎", value="essence_stones"),
                discord.SelectOption(label=_tr(lang, "Chests", "الصناديق", "Coffres"), emoji="🎁", value="chests"),
                discord.SelectOption(label=_tr(lang, "Dungeon Keys", "مفاتيح الزنزانات", "Clés de donjon"), emoji="🗝️", value="dungeon_key"),
                discord.SelectOption(label=_tr(lang, "Pickaxes", "المعاول", "Pioches"), emoji="⛏️", value="pickaxes"),
                discord.SelectOption(label=_tr(lang, "Dungeon Orbs", "كرات الزنزانات", "Orbes de donjon"), emoji="🔮", value="dungeon_orbs"),
                discord.SelectOption(label=_tr(lang, "Skills", "المهارات", "Compétences"), emoji="🎯", value="skills"),
            ]
            super().__init__(placeholder=_tr(lang, "Select a category", "اختر فئة", "Sélectionnez une catégorie"), options=options)

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message(
                    _tr(self.lang, "You cannot interact with this menu.", "لا يمكنك التفاعل مع هذه القائمة.", "Vous ne pouvez pas interagir avec ce menu."),
                    ephemeral=True
                )
                return

            view = CategoryView(self.view.inventory_data, self.values[0], self.view.essence_stone_data,
                                original_user=interaction.user, lang=self.lang)
            embed = view.get_category_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)


class CategoryView(discord.ui.View):
    def __init__(self, inventory_data, category, essence_stone_data, original_user, lang: int):
        super().__init__(timeout=None)
        self.inventory_data = inventory_data
        self.essence_stone_data = essence_stone_data
        self.current_category = category
        self.current_page = 1
        self.items_per_page = 5
        self.original_user = original_user
        self.lang = lang
        self.add_item(self.PreviousButton())
        self.add_item(self.NextButton())
        self.add_item(self.GoBackButton())

    def get_category_embed(self, user):
        lang = self.lang
        items = self.inventory_data.get(self.current_category, [])
        total_pages = math.ceil(len(items) / self.items_per_page) or 1

        if self.current_page < 1:
            self.current_page = total_pages
        elif self.current_page > total_pages:
            self.current_page = 1

        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = start_idx + self.items_per_page
        current_items = items[start_idx:end_idx]

        title = _tr(lang, f"{self.current_category.replace('_', ' ').title()} Inventory",
                    f"جرد {self.current_category.replace('_', ' ')}",
                    f"Inventaire {self.current_category.replace('_', ' ').title()}")
        embed = discord.Embed(title=title, color=0x000000)

        category_descriptions = {
            'weapons': _tr(lang,
                        "In the darkness of dungeons, these weapons are your trusted companions. Each blade, infused with power, stands ready to strike down those who would challenge your ascension.",
                        "في ظلمات الأبراج، هذه الأسلحة رفقاؤك الموثوقون. كل شفرة مشحونة بالقوة، مستعدة لإسقاط من يتحدّى صعودك.",
                        "Dans l’obscurité des donjons, ces armes sont vos compagnes fidèles. Chaque lame, emplie de puissance, est prête à abattre vos adversaires."),
            'boosters_enhancers': _tr(lang,
                                    "Tools of advancement that push beyond mortal limits. Through these enhancers, even E-rank hunters can glimpse the power of S-rank might.",
                                    "أدوات للتقدّم تتجاوز الحدود البشرية. عبر هذه المقويات، حتى الصيادون رتبة E يمكنهم تذوق قوة رتبة S.",
                                    "Des outils de progression qui dépassent les limites humaines. Grâce à ces amplificateurs, même les chasseurs de rang E goûtent à la puissance du rang S."),
            'essence_stones': _tr(lang,
                                "Crystallized essence of fallen monsters, pulsing with raw power. These stones hold the key to strengthening your abilities and ascending to greater heights.",
                                "جوهر متبلور لوحوش ساقطة، ينبض بقوة خام. تحمل هذه الأحجار مفتاح تقوية قدراتك وترقيتك لمراتب أعلى.",
                                "L’essence cristallisée de monstres vaincus, vibrant d’une puissance brute. Ces pierres sont la clé pour renforcer vos capacités et vous élever."),
            'chests': _tr(lang,
                        "Rewards claimed from conquered dungeons and fallen foes. Each chest holds the promise of power, waiting to be unleashed.",
                        "مكافآت تُنتزع من زنزانات مقهورة وأعداء ساقطين. كل صندوق يحمل وعدًا بالقوة بانتظار إطلاقه.",
                        "Des récompenses arrachées aux donjons conquis et aux ennemis vaincus. Chaque coffre promet du pouvoir, prêt à être libéré."),
            'dungeon_key': _tr(lang,
                            "Gates to new challenges await. These keys unlock dungeons of varying difficulty, each promising greater rewards for those brave enough to enter.",
                            "بوابات لتحديات جديدة بانتظارك. هذه المفاتيح تفتح زنزانات متفاوتة الصعوبة، كل منها يعد بمكافآت أكبر للأشجع.",
                            "Des portes vers de nouveaux défis. Ces clés ouvrent des donjons de difficultés variées, promettant de plus grandes récompenses aux plus audacieux."),
            'pickaxes': _tr(lang,
                            "Tools of resource extraction, each pickaxe a testament to your mining prowess. Forge your path through stone and ore, uncovering hidden treasures.",
                            "أدوات لاستخراج الموارد، كل معول شهادة على براعتك في التعدين. اشق طريقك عبر الحجر والخام لاكتشاف الكنوز.",
                            "Outils d’extraction, chaque pioche témoigne de votre maîtrise minière. Frayez-vous un chemin dans la roche pour découvrir des trésors cachés."),
            'dungeon_orbs': _tr(lang,
                                "Mystical orbs earned from conquering dungeons of various ranks. Each orb pulses with the essence of victory, ready to enhance your leveling journey.",
                                "كرات سحرية تُكتسب من غزو زنزانات بمستويات مختلفة. كل كرة تنبض بجوهر النصر، جاهزة لتعزيز رحلة تطويرك.",
                                "Des orbes mystiques gagnés en conquérant des donjons de rangs variés. Chaque orbe vibre de l’essence de la victoire pour accélérer votre progression."),
            'skills': _tr(lang,
                        "Abilities honed through countless battles and training. Each skill represents your growth as a Hunter, ready to unleash devastating power upon your enemies.",
                        "قدرات صُقلت عبر معارك وتدريبات لا تُحصى. كل مهارة تمثل نمّوك كصيّاد، مستعدة لإطلاق قوة مدمرة على أعدائك.",
                        "Des aptitudes forgées par d’innombrables combats et entraînements. Chaque compétence reflète votre progression de chasseur, prête à libérer une puissance dévastatrice."),
        }

        embed.description = category_descriptions.get(self.current_category)
        embed.set_thumbnail(url=user.avatar.url if user.avatar else None)

        if not current_items:
            embed.description += "\n\n" + _tr(
                lang,
                "*Your inventory for this category is empty. Venture forth to gather more power!*",
                "*جردك في هذه الفئة فارغ. انطلق لجمع المزيد من القوة!*",
                "*Votre inventaire pour cette catégorie est vide. Partez gagner en puissance !*"
            )
        else:
            items_display = []
            for item in current_items:
                icon = item['icon'] if item['icon'] else "❓"
                items_display.append(f"{icon} **{item['name']}** (x{item['quantity']})")
            embed.description += "\n\n" + "\n\n".join(items_display)

        if self.current_category == 'essence_stones':
            current = self.essence_stone_data['current']
            cap = self.essence_stone_data['cap']
            footer_text = _tr(
                lang,
                f"Page {self.current_page}/{total_pages} | Essence Stone Capacity: {current:,}/{cap:,}",
                f"الصفحة {self.current_page}/{total_pages} | سعة أحجار الجوهر: {current:,}/{cap:,}",
                f"Page {self.current_page}/{total_pages} | Capacité des pierres d’essence : {current:,}/{cap:,}"
            )
        else:
            footer_text = _tr(
                lang,
                f"Page {self.current_page}/{total_pages}",
                f"الصفحة {self.current_page}/{total_pages}",
                f"Page {self.current_page}/{total_pages}"
            )
        embed.set_footer(text=footer_text)
        return embed

    class PreviousButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label="<", custom_id="previous")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            if interaction.user.id != view.original_user.id:
                await interaction.response.send_message(
                    _tr(view.lang, "You cannot interact with this menu.", "لا يمكنك التفاعل مع هذه القائمة.", "Vous ne pouvez pas interagir avec ce menu."),
                    ephemeral=True
                )
                return
            view.current_page -= 1
            embed = view.get_category_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)

    class NextButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label=">", custom_id="next")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            if interaction.user.id != view.original_user.id:
                await interaction.response.send_message(
                    _tr(view.lang, "You cannot interact with this menu.", "لا يمكنك التفاعل مع هذه القائمة.", "Vous ne pouvez pas interagir avec ce menu."),
                    ephemeral=True
                )
                return
            view.current_page += 1
            embed = view.get_category_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)

    class GoBackButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.danger, label="Go Back", custom_id="go_back")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            if interaction.user.id != view.original_user.id:
                await interaction.response.send_message(
                    _tr(view.lang, "You cannot interact with this menu.", "لا يمكنك التفاعل مع هذه القائمة.", "Vous ne pouvez pas interagir avec ce menu."),
                    ephemeral=True
                )
                return
            main_view = InventoryMainView(view.inventory_data, view.essence_stone_data, view.original_user, view.lang)
            embed = main_view.get_main_menu_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=main_view)


async def setup(bot):
    await bot.add_cog(InventoryCommands(bot))
