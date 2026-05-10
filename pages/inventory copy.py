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
                    await ctx.send(f"⏰ Command on cooldown. Please wait {remaining} seconds.")
                    return
            else:
                # Create a new cooldown bucket
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
                
            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator

class InventoryCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
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
        pattern = r'$$(.*?)$$'
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
            update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (money_reward, user_id))
            update_query = "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (gamble_reward, user_id))
        except Exception as e:
            print(f"Error updating user currency: {e}")

    async def get_available_items(self, chest_id):
        """Get both products and dungeon keys available for a specific chest with their icons."""
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

            # Get dungeon keys with icons
            dungeonkey_query = """
                SELECT 'dungeonkey' as item_type, dk.dungeonkey_id as item_id, 
                       dk.dungeonkey_name as item_name, dk.dungeonkey_icon as item_icon
                FROM chest_dungeonkey cd
                JOIN dungeon_key dk ON cd.dungeonkey_id = dk.dungeonkey_id
                WHERE cd.chest_id = %s
            """
            dungeonkeys = await execute_query_with_retry(dungeonkey_query, (chest_id,))
            
            return (products or []) + (dungeonkeys or [])

        except Exception as e:
            print(f"Error getting available items: {e}")
            return []

    def get_quantity_range(self, item_type, item_id, chest_id, quantity_multiplier):
        # Base ranges for different chest types
        ranges = {
            # Format: (shadow_min, shadow_max, hunter_min, hunter_max, monarch_min, monarch_max)
            'boost': (5, 10, 10, 15, 15, 20),
            'return_crystal': (5, 10, 10, 15, 15, 20),
            'consumables': (1, 2, 1, 3, 1, 4),
            'resets': (1, 3, 1, 3, 1, 3),
            'dungeonkey': (1, 2, 1, 3, 1, 4)  # Same ranges as consumables
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

            check_query = f"SELECT quantiter FROM {table_name} WHERE inventory_id = %s AND {id_column} = %s"
            check_result = await execute_query_with_retry(check_query, (inventory_id, item_id))

            if check_result:
                new_quantity = check_result[0]['quantiter'] + quantity
                update_query = f"UPDATE {table_name} SET quantiter = %s WHERE inventory_id = %s AND {id_column} = %s"
                await execute_query_with_retry(update_query, (new_quantity, inventory_id, item_id))
            else:
                insert_query = f"INSERT INTO {table_name} (inventory_id, {id_column}, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

        except Exception as e:
            print(f"Error updating inventory item: {e}")

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
            # Get user's Discord ID
            user_author_id = str(ctx.author.id)

            # Check if user exists in the database
            user_query = "SELECT * FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (user_author_id,))

            if not user_result:
                await ctx.send("You need to register first. Use `sl register` to get started.")
                return
            user = user_result[0]
            
            # Check if pickaxe is equipped
            if user['pickaxe_id'] is None or user['pickaxe_id'] == 0:
                await ctx.send("You haven't equipped a pickaxe yet. Use `sl pickadd <pickaxe_name>` to equip one.")
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
                    await ctx.send(f"You can mine again in {hours}h {minutes}m {seconds}s.")
                    return

            # Get pickaxe details
            pickaxe_query = "SELECT * FROM pickaxe WHERE id_pickaxe = %s"
            pickaxe_result = await execute_query_with_retry(pickaxe_query, (user['pickaxe_id'],))

            if not pickaxe_result:
                await ctx.send("Error: Pickaxe not found.")
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
            embed = discord.Embed(title="Mining Results", color=discord.Color.green())
            embed.description = f"You used your **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']} to mine a cluster of mana crystals, yielding a total of **{mined_amount} <:won:1304034228966658048>**!"
            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Mining error: {e}")
            await ctx.send("An error occurred during mining.")

    @commands.command(name='pickadd', aliases=['pa', 'pc'])
    async def pickadd_command(self, ctx, *, pickaxe_name: str):
        try:
            # Get user's Discord ID
            user_author_id = str(ctx.author.id)

            # Check if user exists in the database
            user_query = "SELECT * FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (user_author_id,))

            if not user_result:
                await ctx.send("You need to register first. Use `sl register` to get started.")
                return

            user = user_result[0]
            user_id = user['user_id']
            
            # Check if user is already in pickaxe_status table
            status_query = "SELECT * FROM pickaxe_status WHERE user_id = %s"
            status_result = await execute_query_with_retry(status_query, (user_id,))
            
            if status_result:
                await ctx.send("You already have a pending pickaxe operation. Please wait until it completes.")
                return
                
            # Check for active trades
            trade_query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(trade_query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Find pickaxe by name (using LIKE for partial matches)
            pickaxe_query = "SELECT * FROM pickaxe WHERE pickaxe_name LIKE %s"
            pickaxe_result = await execute_query_with_retry(pickaxe_query, (f'%{pickaxe_name}%',))

            if not pickaxe_result:
                await ctx.send(f"No pickaxe found matching '{pickaxe_name}'.")
                return
            if len(pickaxe_result) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title="Multiple Results Found",
                    description="I found multiple pickaxes matching your search. Please specify which one you'd like to equip:",
                    color=discord.Color.blue()
                )

                # List all matching pickaxes
                pickaxe_list = "\n".join([f"• {pickaxe['pickaxe_name']}" 
                                    for pickaxe in pickaxe_result])
                embed.add_field(name="Matching Pickaxes", value=pickaxe_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the pickaxe you want to equip.")
                
                await ctx.send(embed=embed)
                return
                
            pickaxe = pickaxe_result[0]
            pickaxe_id = pickaxe['id_pickaxe']
            
            # Get user's inventory ID
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("Inventory not found.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check if user has this pickaxe in inventory
            inventory_pickaxe_query = """
            SELECT * FROM inventory_pickaxe 
            WHERE inventory_id = %s AND id_pickaxe = %s
            """
            inventory_pickaxe_result = await execute_query_with_retry(inventory_pickaxe_query, (inventory_id, pickaxe_id))

            if not inventory_pickaxe_result:
                await ctx.send(f"You don't have the {pickaxe['pickaxe_name']} pickaxe in your inventory.")
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
                embed = discord.Embed(title="Replace Pickaxe", description=f"You already have a pickaxe equipped. Do you want to replace it with {pickaxe['pickaxe_name']}?", color=discord.Color.orange())
                
                view = discord.ui.View()
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.green, label="Yes", custom_id="replace_pickaxe_yes"))
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.red, label="No", custom_id="replace_pickaxe_no"))
                
                message = await ctx.send(embed=embed, view=view)

                def check(interaction):
                    return interaction.user == ctx.author and interaction.message.id == message.id

                try:
                    interaction = await self.bot.wait_for('interaction', check=check, timeout=60.0)
                    
                    if interaction.data['custom_id'] == 'replace_pickaxe_no':
                        # Clean up status entry if user cancels
                        delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                        await execute_query_with_retry(delete_status_query, (user_id,))
                        await interaction.response.send_message("Pickaxe replacement cancelled.", ephemeral=True)
                        return

                    # If yes, continue with replacement process
                    await interaction.response.defer()
                    
                    # Check for active trades again before proceeding
                    trade_result = await execute_query_with_retry(trade_query, (user_id, user_id))
                    if trade_result:
                        # Clean up status entry if user is in trade
                        delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                        await execute_query_with_retry(delete_status_query, (user_id,))
                        await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                        return
                        
                    # Check if user still has the pickaxe in inventory
                    inventory_pickaxe_result = await execute_query_with_retry(inventory_pickaxe_query, (inventory_id, pickaxe_id))
                    if not inventory_pickaxe_result:
                        # Clean up status entry if pickaxe not in inventory
                        delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                        await execute_query_with_retry(delete_status_query, (user_id,))
                        await ctx.send(f"You no longer have the {pickaxe['pickaxe_name']} pickaxe in your inventory.")
                        return

                except asyncio.TimeoutError:
                    # Clean up status entry if timeout
                    delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                    await execute_query_with_retry(delete_status_query, (user_id,))
                    await ctx.send("Pickaxe replacement timed out.")
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
                await ctx.send(f"You no longer have the {pickaxe['pickaxe_name']} pickaxe in your inventory.")
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
            embed = discord.Embed(title="Pickaxe Equipped", color=discord.Color.green())
            embed.description = f"You have successfully equipped the **{pickaxe['pickaxe_name']}** {pickaxe['pickaxe_icon']}!"
            
            # Delete user from pickaxe_status table after successful operation
            delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
            await execute_query_with_retry(delete_status_query, (user_id,))
            
            await ctx.send(embed=embed)

        except Exception as e:
            # Make sure to clean up status entry if there's an error
            try:
                user_id = user_result[0]['user_id'] if user_result else None
                if user_id:
                    delete_status_query = "DELETE FROM pickaxe_status WHERE user_id = %s"
                    await execute_query_with_retry(delete_status_query, (user_id,))
            except:
                pass
                
            print(f"Pickaxe add error: {e}")
            await ctx.send("An error occurred while adding the pickaxe.")

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
            # Handle default case (no args)
            if not args:
                await ctx.send("Usage : sl loot <rarity> <amount>")
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
                            await ctx.send("Invalid quantity. Please provide a number or 'all'.")
                            return
                    except ValueError:
                        await ctx.send("Invalid quantity. Please provide a positive number or 'all'.")
                        return

            # Get user data
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("User not registered.")
                return
            
            user_id = user_result[0]['user_id']

            # Check premium status
            premium_tier = await self.check_premium_status(user_id)

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_result:
                await ctx.send("Inventory not found.")
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
                await ctx.send(f"No chest or bag found matching '{chest_name}'.")
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
                await ctx.send(f"No chest or bag found matching '{chest_name}'.")
                return

            # Find a chest the user actually has (quantiter > 0)
            chest_data = None
            for chest in chest_result:
                if chest['quantiter'] > 0:
                    chest_data = chest
                    break
                    
            if not chest_data:
                await ctx.send(f"You don't have any {chest_name} in your inventory.")
                return
                
            chest_id = chest_data['chest_id']

            # Premium tier checks for bags (chest_id 4-6)
            if chest_id >= 4:
                required_tier = chest_id - 3  # 4->1, 5->2, 6->3
                
                if premium_tier == 0:
                    tier_name = self.get_premium_tier_name(required_tier)
                    await ctx.send(f"❌ This bag requires {tier_name} premium tier to loot!")
                    return
                
                # Check if user has sufficient premium tier
                if premium_tier < required_tier:
                    tier_name = self.get_premium_tier_name(required_tier)
                    await ctx.send(f"❌ This bag requires {tier_name} premium tier to loot! Your current tier is insufficient.")
                    return

            available_quantity = chest_data['quantiter'] or 0
            
            # Handle 'all' quantity
            if quantity == -1:
                quantity = available_quantity

            # Verify user has enough chests
            if quantity > available_quantity:
                await ctx.send("You don't have enough chests/bags.")
                return

            # Update chest quantity - FIXED: Check if record exists first
            check_query = "SELECT 1 FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            check_result = await execute_query_with_retry(check_query, (inventory_id, chest_id))
            
            new_chest_quantity = available_quantity - quantity
            if new_chest_quantity == 0:
                if check_result:  # If record exists, delete it
                    delete_query = "DELETE FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
                    await execute_query_with_retry(delete_query, (inventory_id, chest_id))
            else:
                if check_result:  # If record exists, update it
                    update_query = "UPDATE inventory_chest SET quantiter = %s WHERE inventory_id = %s AND chest_id = %s"
                    await execute_query_with_retry(update_query, (new_chest_quantity, inventory_id, chest_id))
                else:  # If record doesn't exist, insert it (this branch shouldn't be reached due to our previous check, but added for safety)
                    insert_query = "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, %s)"
                    await execute_query_with_retry(insert_query, (inventory_id, chest_id, new_chest_quantity))

            # Process rewards based on chest/bag type
            rewards = []
            if chest_id in [4, 5, 6]:  # Bags with fixed rewards
                fixed_rewards = self.get_fixed_bag_rewards(chest_id, quantity)  # Pass quantity here
                rewards = await self.process_fixed_rewards(inventory_id, fixed_rewards)
            else:  # Regular chests with random rewards
                # Get all available items
                available_items = await self.get_available_items(chest_id)
                
                if not available_items:
                    await ctx.send("No items available in this chest.")
                    return

                # Determine item limit based on chest type
                item_limits = {1: 4, 2: 5, 3: 6}
                item_limit = item_limits.get(chest_id, 4)

                # Calculate quantity multiplier
                quantity_multiplier = (quantity - 1) // 4

                # Select random number of items
                num_items = random.randint(1, item_limit)
                selected_items = random.sample(available_items, num_items)

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
                title=f"🎉 Loot Results",
                description=f"You looted {quantity}x {chest_data['chest_icon']} {chest_name}{'s' if quantity > 1 else ''}!",
                color=self.get_chest_color(chest_id)
            )

            # Set thumbnail
            if os.path.exists(chest_image):
                file = discord.File(chest_image, filename="chest_image.png")
                embed.set_thumbnail(url="attachment://chest_image.png")
            
            # Add rewards to embed
            rewards_text = ""
            for reward in rewards:
                rewards_text += f"{reward['icon']} `{reward['quantity']}x` {reward['name']}\n"

            if rewards_text:
                embed.add_field(
                    name="<:arrowpurple:1309253795909468160> Items Received",
                    value=rewards_text,
                    inline=False
                )

            # Add currency reward
            embed.add_field(
                name="<:arrowpurple:1309253795909468160> Currency Reward",
                value=f"<:won:1304034228966658048> `{money_reward:,}x` Won\n<:gamble:1314341368176574564> `{gamble_reward:,}x` GWon",
                inline=False
            )
            embed.set_footer(text=f"{ctx.author.name}'s loot rewards", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)

            # Send embed and update quest progress
            if os.path.exists(chest_image):
                await ctx.send(file=file, embed=embed)
            else:
                await ctx.send(embed=embed)
                
            try:
                await self.update_quest_progress(ctx, user_id, "Open 5 chests", quantity)
            except Exception as e:
                print(f"Error in quest tracking: {e}")

        except Exception as e:
            await ctx.send(f"An error occurred while processing your request: {str(e)}")

    def get_chest_color(self, chest_id):
        """Return embed color based on chest type."""
        colors = {
            1: 0x00FFFF,  # Shadow chest - Gray
            2: 0xFFD700,  # Hunter chest - Green
            3: 0xB22222,  # Monarch chest - Gold
            4: 0x00FFFF,  # Bronze bag - Same as Shadow
            5: 0xFFD700,  # Silver bag - Same as Hunter
            6: 0xB22222   # Golden bag - Same as Monarch
        }
        return colors.get(chest_id, 0x7289DA)  # Default Discord blue if chest_id not found

    def get_fixed_bag_rewards(self, chest_id, quantity=1):
        """Return fixed rewards for bags."""
        capsule_ids = [4, 5, 6, 7]  # attack, speed, health, defense capsules
        booster_ids = [9, 10, 11, 12]  # attack, speed, defence, health boosters
        
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
                {'type': 'dungeonkey', 'id': 1, 'quantity': 1 * multiplier},  # easy gate
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
                {'type': 'dungeonkey', 'id': 1, 'quantity': 1 * multiplier},  # easy gate
                {'type': 'dungeonkey', 'id': 2, 'quantity': 1 * multiplier},  # medium gate
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
                {'type': 'dungeonkey', 'id': 1, 'quantity': 1 * multiplier},  # easy gate
                {'type': 'dungeonkey', 'id': 2, 'quantity': 1 * multiplier},  # medium gate
                {'type': 'dungeonkey', 'id': 3, 'quantity': 1 * multiplier},  # hard gate
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


    async def use_product(self, ctx, *, args):
        parsed_args = self.parse_input(args)
        if len(parsed_args) != 2:
            await ctx.send("Invalid input. Please use the format: sl use (product name) (card name)")
            return

        product_name, card_name = parsed_args

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return
            user_id = user_result[0]['user_id']

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check product existence and get info
            product_query = "SELECT * FROM product WHERE product_name = %s"
            product_result = await execute_query_with_retry(product_query, (product_name,))
            if not product_result:
                await ctx.send(f"Product '{product_name}' not found.")
                return
            product = product_result[0]

            # Check if user has the product
            inv_product_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            inv_product_result = await execute_query_with_retry(inv_product_query, (inventory_id, product['product_id']))
            if not inv_product_result:
                await ctx.send(f"You don't have the product '{product_name}' in your inventory.")
                return

            # Check card existence and get info
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, card_name))
            if not user_card_result:
                await ctx.send(f"You don't have the card '{card_name}' in your collection.")
                return
            user_card = user_card_result[0]
            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = await execute_query_with_retry(card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(f"Card '{card_name}' not found.")
                return
            card = card_result[0]

            # Process product use (only for charm products now)
            if any([product['attack_boost'], product['health_boost'], product['speed_boost']]):
                # Charm product
                charm_query = "SELECT * FROM usercards_charm WHERE id_ligneusercards = %s AND product_id = %s"
                charm_result = await execute_query_with_retry(charm_query, (user_card['id_ligneusercards'], product['product_id']))
                if charm_result:
                    await ctx.send(f"The '{product_name}' is already active on this card. Wait until its effects are over.")
                    return

                # Apply charm
                insert_charm_query = "INSERT INTO usercards_charm (id_ligneusercards, product_id, counter) VALUES (%s, %s, 3)"
                await execute_query_with_retry(insert_charm_query, (user_card['id_ligneusercards'], product['product_id']))

                # Update card stats
                update_stats_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat + %s, health_stat = health_stat + %s, speed_stat = speed_stat + %s 
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_stats_query, (
                    product['attack_boost'], product['health_boost'], product['speed_boost'], user_card['id_ligneusercards']
                ))

                await ctx.send(f"Applied '{product_name}' to '{card_name}'. Stats boosted for 3 turns.")

                # Update inventory
                if inv_product_result[0]['quantiter'] == 1:
                    delete_product_query = "DELETE FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
                    await execute_query_with_retry(delete_product_query, (inventory_id, product['product_id']))
                else:
                    update_quantity_query = """
                    UPDATE inventory_product 
                    SET quantiter = quantiter - 1 
                    WHERE inventory_id = %s AND product_id = %s
                    """
                    await execute_query_with_retry(update_quantity_query, (inventory_id, product['product_id']))
            else:
                await ctx.send(f"The product '{product_name}' has no effect when used directly. Try 'sl xpboost' for XP boosting products.")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name='xpboost', aliases=["xb", "xp"])
    async def xp_boost(self, ctx, *, args):
        def parse_input(args):
            parts = args.split()
            
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
            await ctx.send("Invalid quantity.")
            return
        
        # Hardcoded XP Boost product ID
        product_id = 1
        product_name = "boost"

        try:
            # Get user_id
            # Check if the user is in a trade                
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return
            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check if user has the XP Boost product
            inv_product_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            inv_product_result = await execute_query_with_retry(inv_product_query, (inventory_id, product_id))
            if not inv_product_result:
                await ctx.send(f"You don't have any {product_name} items in your inventory.")
                return
            
            if inv_product_result[0]['quantiter'] < quantity:
                await ctx.send(f"You don't have enough {product_name} items. You have {inv_product_result[0]['quantiter']} but tried to use {quantity}.")
                return
            
            # Find user cards with LIKE matching
            user_card_query = "SELECT u.*,c.type_card,c.max_level,c.xp_cap,c.card_icon FROM user_cards u,card c WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, f"%{card_name}%"))
            if not user_card_result:
                await ctx.send(f"You don't have any cards matching '{card_name}' in your collection.")
                return
            
            if len(user_card_result) > 1:
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to boost:",
                    color=discord.Color.blue()
                )

                # List all matching cards
                card_list = "\n".join([f"• {card['name']}" for card in user_card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact name of the card you want to boost.")
                
                await ctx.send(embed=embed)
                return

            # If we get here, we have exactly one card
            user_card = user_card_result[0]

            # Check card existence and type
            if user_card['type_card'] in ['Support','Booster']:
                await ctx.send("This command can only be used with Offence-type cards.")
                return

            if user_card['current_level'] == user_card['max_level']:
                await ctx.send(f"'{user_card['name']}' has already reached its maximum level.")
                return

            # Get XP boost value for the product
            product_query = "SELECT xp_boost FROM product WHERE product_id = %s"
            product_result = await execute_query_with_retry(product_query, (product_id,))
            if not product_result or product_result[0]['xp_boost'] == 0:
                await ctx.send("XP boost product is not configured correctly.")
                return
            xp_boost_value = product_result[0]['xp_boost']

            # Pre-calculate XP boost needed to reach max level
            current_xp = user_card['current_xp']
            current_level = user_card['current_level']
            max_level = user_card['max_level']
            xp_cap = user_card['xp_cap']

            # Calculate XP boost needed and actual boosts to use
            xp_to_max = 0
            boosts_to_use = 0
            remaining_xp = current_xp
            current_calc_level = current_level

            while current_calc_level < max_level:
                # Calculate XP needed to next level
                xp_needed_to_next = xp_cap - remaining_xp
                
                # If we can apply a full boost
                if xp_boost_value >= xp_needed_to_next:
                    # Add XP needed to reach next level
                    xp_to_max += xp_needed_to_next
                    boosts_to_use += 1
                    
                    # Reset remaining XP and move to next level
                    remaining_xp = 0
                    current_calc_level += 1
                else:
                    # Partial boost doesn't reach next level
                    xp_to_max += xp_boost_value
                    boosts_to_use += 1
                    remaining_xp += xp_boost_value
                    
                    # If we've filled up the current level
                    if remaining_xp >= xp_cap:
                        remaining_xp -= xp_cap
                        current_calc_level += 1
                
                # Stop if we've reached max level
                if current_calc_level >= max_level:
                    break

            # Ensure we don't use more boosts than requested
            boosts_to_use = min(boosts_to_use, quantity)

            # If no boosts are needed
            if boosts_to_use == 0:
                await ctx.send(f"'{user_card['name']}' is already at maximum potential XP for its current level.")
                return

            # Calculate XP and levels
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

            while new_xp >= xp_cap and new_level < max_level:
                new_level += 1
                new_xp -= xp_cap
                levels_gained += 1
                stat_increases['health_stat'] += random.randint(3, 5)
                stat_increases['attack_stat'] += random.randint(2, 4)
                stat_increases['speed_stat'] += random.randint(2, 4)
                stat_increases['defence_stat'] += random.randint(1, 2)
                stat_increases['current_statpoints'] += 2
            
            if new_level == max_level:
                new_xp = 0

            # Check if card has equipment
            has_equipment = user_card['weapon_id'] is not None or user_card['armor_id'] is not None or user_card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, user_card['card_id'])

            # Check if card is in a team with support cards
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            
            has_support_in_team = False
            team_id = None
            
            if team_result:
                team_id = team_result[0]['team_id']
                
                # Check if the card is in the team
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, user_card['card_id']))
                
                if team_card_result:
                    # Check if there are support cards in the team
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

            # SCENARIO HANDLING:
            # 1. No support in team and no equipment
            if not has_support_in_team and not has_equipment:
                # Apply stat buff directly to both base stats and current stats
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
                    new_xp, 
                    new_level,
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'], 
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
            
            # 2. Support cards but no equipment
            elif has_support_in_team and not has_equipment:
                # Reset to base stats first
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (user_card['id_ligneusercards'],))
                
                # Apply stat buff to both base stats and current stats
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
                    new_xp, 
                    new_level,
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'], 
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
                
                # Now apply support boosts to current stats
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )
            
            # 3. No support cards but has equipment
            elif not has_support_in_team and has_equipment:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Update base stats and current stats with the buff
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
                
                new_power = new_base_stats['attack'] + new_base_stats['health'] + new_base_stats['defence'] + new_base_stats['speed']
                
                await execute_query_with_retry(update_base_query, (
                    new_xp, 
                    new_level,
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_power,
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
            
            # 4. Has both support cards and equipment
            else:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Reset current stats to true base stats + stat increases (without equipment boosts)
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
                
                # Update base stats and level info
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
                    new_xp, 
                    new_level,
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
                
                # Apply equipment boosts to current stats
                await self.apply_equipment_boosts(user_id, user_card['card_id'], equipment_boosts)
                
                # Then apply support boosts
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )

            # Update inventory - only remove used boosts
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

            # Get updated stats for response message
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

            # Prepare the response message
            response = f"Applied {product_name} x{boosts_to_use} to {user_card['card_icon']} {user_card['name']}. XP boosted to {new_xp}, now at level {new_level}."
            if levels_gained > 0:
                response += f"\nLeveled up {levels_gained} time(s)! Stat increases:"
                for stat, increase in stat_increases.items():
                    if increase > 0:
                        response += f"\n- {stat.replace('_', ' ').title()}: +{increase}"
            
            # If fewer boosts were used than requested, mention the remaining
            if boosts_to_use < quantity:
                response += f"\n{quantity - boosts_to_use} {product_name} items were not used as the card reached max level."

            await ctx.send(response)
            try:
                await self.update_quest_progress(ctx, user_id, "Use 10 XpBottles", boosts_to_use)
            except Exception as e:
                print(f"Error in quest tracking: {e}")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    def parsed_input(self, args):
        # Remove this method or modify it to simply split the input
        parts = args.split()
        if len(parts) < 2:
            return None, None
        return ' '.join(parts[:-1]), parts[-1]

    @commands.command(name="level",aliases=["lv", "lvl"])
    async def level(self, ctx, *, args):
        try:
            # Split args into name and quantity
            if ',' not in args:
                await ctx.send("Please provide both card name and quantity (e.g., 'sl level CardName, 5')")
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
                        await ctx.send("Please provide a positive quantity.")
                        return
                except ValueError:
                    await ctx.send("Please provide a valid number for quantity.")
                    return

            # Get user data
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return

            user_id = user_result[0]['user_id']
            
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Get inventory ID
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return
                
            inventory_id = inventory_result[0]['inventory_id']
            
            # Check if user has the card
            card_query = """
            SELECT uc.*, c.type_card, c.max_level, c.xp_cap, c.id_fragment, c.card_icon 
            FROM user_cards uc 
            JOIN card c ON uc.card_id = c.card_id 
            WHERE uc.user_id = %s AND uc.name LIKE %s
            """
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))
            
            if not card_result:
                await ctx.send(f"You don't have a card named '{card_name}'.")
                return
                
            user_card = card_result[0]
            
            # Check card type
            if user_card['type_card'] in ['Support', 'Booster']:
                await ctx.send("This command can only be used with Offence-type cards.")
                return

            # Check if card is already at max level
            if user_card['current_level'] >= user_card['max_level']:
                await ctx.send(f"'{user_card['name']}' has already reached its maximum level.")
                return
                
            # Check if user has fragments
            frag_query = """
            SELECT quantiter FROM inventory_frags 
            WHERE inventory_id = %s AND frag_id = %s
            """
            frag_result = await execute_query_with_retry(frag_query, (inventory_id, user_card['id_fragment']))
            
            if not frag_result:
                await ctx.send(f"You don't have any stones for '{user_card['name']}'.")
                return

            # Calculate XP and levels
            fragment_xp = 100  # XP per fragment
            current_xp = user_card['current_xp']
            current_level = user_card['current_level']
            max_level = user_card['max_level']
            xp_cap = user_card['xp_cap']
            available_fragments = frag_result[0]['quantiter']

            # Check if user has enough fragments
            if not is_all and quantity > available_fragments:
                await ctx.send(f"You only have {available_fragments} stones available.")
                return

            # Calculate fragments needed to reach max level
            remaining_xp = current_xp
            current_calc_level = current_level
            fragments_needed_for_max = 0
            
            while current_calc_level < max_level:
                xp_needed_to_next = xp_cap - remaining_xp
                fragments_for_level = math.ceil(xp_needed_to_next / fragment_xp)
                fragments_needed_for_max += fragments_for_level
                remaining_xp = 0
                current_calc_level += 1

            # Determine how many fragments to actually use
            requested_fragments = min(quantity if not is_all else available_fragments, available_fragments)
            fragments_to_use = min(requested_fragments, fragments_needed_for_max)

            total_xp_gain = fragments_to_use * fragment_xp
            new_xp = current_xp + total_xp_gain
            new_level = current_level
            
            # Calculate stat increases
            levels_gained = 0
            stat_increases = {
                'attack_stat': 0,
                'health_stat': 0,
                'defence_stat': 0,
                'speed_stat': 0,
                'current_statpoints': 0
            }

            while new_xp >= xp_cap and new_level < max_level:
                new_level += 1
                new_xp -= xp_cap
                levels_gained += 1
                stat_increases['health_stat'] += random.randint(3, 5)
                stat_increases['attack_stat'] += random.randint(2, 4)
                stat_increases['speed_stat'] += random.randint(2, 4)
                stat_increases['defence_stat'] += random.randint(1, 2)
                stat_increases['current_statpoints'] += 2

            if new_level == max_level:
                new_xp = 0

            # Check if card has equipment
            has_equipment = user_card['weapon_id'] is not None or user_card['armor_id'] is not None or user_card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, user_card['card_id'])

            # Check if card is in a team with support cards
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            
            has_support_in_team = False
            team_id = None
            
            if team_result:
                team_id = team_result[0]['team_id']
                
                # Check if the card is in the team
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, user_card['card_id']))
                
                if team_card_result:
                    # Check if there are support cards in the team
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

            # SCENARIO HANDLING:
            # 1. No support in team and no equipment
            if not has_support_in_team and not has_equipment:
                # Apply stat buff directly to both base stats and current stats
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
                    new_xp, 
                    new_level,
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'], 
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
            
            # 2. Support cards but no equipment
            elif has_support_in_team and not has_equipment:
                # Reset to base stats first
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (user_card['id_ligneusercards'],))
                
                # Apply stat buff to both base stats and current stats
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
                    new_xp, 
                    new_level,
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'], 
                    stat_increases['attack_stat'],
                    stat_increases['health_stat'],
                    stat_increases['defence_stat'],
                    stat_increases['speed_stat'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
                
                # Now apply support boosts to current stats
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (user_card['id_ligneusercards'],)
                )
            
            # 3. No support cards but has equipment
            elif not has_support_in_team and has_equipment:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Update base stats and current stats with the buff
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
                
                new_power = new_base_stats['attack'] + new_base_stats['health'] + new_base_stats['defence'] + new_base_stats['speed']
                
                await execute_query_with_retry(update_base_query, (
                    new_xp, 
                    new_level,
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_power,
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
            
            # 4. Has both support cards and equipment
            else:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': user_card['base_attack'],
                    'health': user_card['base_health'],
                    'defence': user_card['base_defence'],
                    'speed': user_card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + stat_increases['attack_stat'],
                    'health': true_base_stats['health'] + stat_increases['health_stat'],
                    'defence': true_base_stats['defence'] + stat_increases['defence_stat'],
                    'speed': true_base_stats['speed'] + stat_increases['speed_stat']
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Reset current stats to true base stats + stat increases (without equipment boosts)
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
                
                # Update base stats and level info
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
                    new_xp, 
                    new_level,
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    stat_increases['current_statpoints'],
                    user_card['id_ligneusercards']
                ))
                
                # Apply equipment boosts to current stats
                await self.apply_equipment_boosts(user_id, user_card['card_id'], equipment_boosts)
                
                # Then apply support boosts
                await self.apply_support_boosts_to_single_offense(user_id, user_card['card_id'], support_boosts)
                
                # Update power_stat
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
                    fragments_to_use, 
                    inventory_id, 
                    user_card['id_fragment']
                ))
            
            update_frags_query = """
            UPDATE user set quantiter_fragment = quantiter_fragment - %s WHERE user_id = %s
            """
            await execute_query_with_retry(update_frags_query, (fragments_to_use, user_id))
            
            # Get updated stats for response message
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

            # Prepare response message
            response = f"Used {fragments_to_use} fragments on {user_card['card_icon']} {user_card['name']}. XP increased to {new_xp}, now at level {new_level}."
            if levels_gained > 0:
                response += f"\nLeveled up {levels_gained} time(s)! Stat increases:"
                for stat, increase in stat_increases.items():
                    if increase > 0:
                        response += f"\n- {stat.replace('_', ' ').title()}: +{increase}"

            # Add message about unused fragments if any were saved
            if requested_fragments > fragments_to_use:
                fragments_saved = requested_fragments - fragments_to_use
                response += f"\n{fragments_saved} stones were saved as they weren't needed to reach max level."

            await ctx.send(response)

        except Exception as e:
            print(f"Error in level command: {e}")
            await ctx.send("An error occurred while processing your request.")

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
    @commands.command(name="equip", aliases=["eq", "ep"])
    async def equip(self, ctx, *, args):
        # Split args by comma
        parts = [part.strip() for part in args.split(',')]
        if len(parts) < 2:
            await ctx.send("Usage: sl equip Sword of Power, Mighty Warrior")
            return

        # The second part is the card name, the first part is the weapon name
        weapon_name = parts[0]
        card_name = parts[1]
        
        if not weapon_name or not card_name:
            await ctx.send("Please provide both weapon and card names. Example: sl equip Sword of Power, Mighty Warrior")
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return

            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check weapon
            weapon_query = "SELECT * FROM weapon WHERE weapon_name LIKE %s"
            weapon_result = await execute_query_with_retry(weapon_query, (f'%{weapon_name}%',))

            if not weapon_result:
                await ctx.send(f"No weapons found matching '{weapon_name}'.")
                return

            # If multiple weapons found, show embed
            if len(weapon_result) > 1:
                embed = discord.Embed(
                    title="Multiple Weapons Found",
                    description="I found multiple weapons matching your search. Please specify which one you'd like to equip:",
                    color=discord.Color.blue()
                )   
                weapon_list = "\n".join([f"• {weapon['weapon_name']}" for weapon in weapon_result])
                embed.add_field(name="Matching Weapons", value=weapon_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact weapon name.")
                await ctx.send(embed=embed)
                return

            weapon = weapon_result[0]

            # Check if user has the weapon
            inv_weapon_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            inv_weapon_result = await execute_query_with_retry(inv_weapon_query, (inventory_id, weapon['weapon_id']))

            if not inv_weapon_result:
                await ctx.send(f"You don't have the weapon '{weapon_name}' in your inventory.")
                return

            # Check card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name LIKE %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, f'%{card_name}%'))
            
            if not user_card_result:
                await ctx.send(f"No cards found matching '{card_name}' in your collection.")
                return

            # If multiple cards found, show embed
            if len(user_card_result) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to equip the weapon to:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in user_card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                await ctx.send(embed=embed)
                return
            
            user_card = user_card_result[0]
            
            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = await execute_query_with_retry(card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(f"Card '{card_name}' not found.")
                return
            
            card = card_result[0]

            # Check weapon class compatibility
            weapon_class_query = "SELECT card_class_id FROM weapon_class WHERE weapon_id = %s"
            weapon_class_result = await execute_query_with_retry(weapon_class_query, (weapon['weapon_id'],))
            
            if not weapon_class_result:
                await ctx.send(f"This weapon has no class compatibility defined.")
                return

            # Check if card class matches any of the weapon's compatible classes
            is_compatible = False
            for weapon_class in weapon_class_result:
                if weapon_class['card_class_id'] == card['card_class_id']:
                    is_compatible = True
                    break

            if not is_compatible:
                await ctx.send(f"The weapon '{weapon_name}' is not compatible with the card '{card_name}'.")
                return
                
            if weapon['weapon_rank'] == 1:
                # Check card type and class for weapon equipping
                if card['type_card'] == 'Support':
                    if card['card_class_id'] == 5:  # Assuming you have the tanker class ID
                        await ctx.send("Support Tanker cards cannot equip weapons.")
                        return

            # For weapon_rank 2 (shield) and 3 (armor)
            elif weapon['weapon_rank'] in [2, 3]:
                # Check card type and class for shield/armor equipping
                if card['type_card'] == 'Support':
                    if card['card_class_id'] != 5:  # Non-Tanker Support cards
                        await ctx.send("Only Tanker Support cards can equip shields or armor.")
                        return
                        
            # Check equipment slot based on weapon rank
            if weapon['weapon_rank'] == 1:
                if user_card['weapon_id'] is not None:
                    await ctx.send("This card already has a weapon equipped. Please unequip it first.")
                    return
            elif weapon['weapon_rank'] == 2:
                if user_card['shield_id'] is not None:
                    await ctx.send("This card already has a shield equipped. Please unequip it first.")
                    return
            elif weapon['weapon_rank'] == 3:
                if user_card['armor_id'] is not None:
                    await ctx.send("This card already has armor equipped. Please unequip it first.")
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
                # Check if the offense card is in a team
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                
                # Check if the card is in a team and if there are support cards in that team
                has_support_in_team = False
                team_id = None
                
                if team_result:
                    team_id = team_result[0]['team_id']
                    
                    # Check if the offense card is in the team
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    
                    if team_card_result:
                        # Check if there are support cards in the team
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
                    # 1. Get the true base stats by removing existing equipment buffs
                    # Get all currently equipped items for this card (BEFORE adding the new one)
                    equipment_query = """
                    SELECT weapon_id, shield_id, armor_id
                    FROM user_cards
                    WHERE id_ligneusercards = %s
                    """
                    equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))
                    
                    # Store original base stats to work with
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
                        true_base_speed = original_base_stats.get('base_speed', 0) or 0
                        true_base_defence = original_base_stats.get('base_defence', 0) or 0
                        
                        # Calculate and remove existing equipment buffs
                        if equipment_result:
                            equipment = equipment_result[0]
                            
                            # Initialize existing equipment boosts
                            existing_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            
                            # Check each equipment slot (excluding the one we're about to equip)
                            equipment_slots = []
                            if equipment.get('weapon_id') and weapon['weapon_rank'] != 1:
                                equipment_slots.append(equipment.get('weapon_id'))
                            if equipment.get('shield_id') and weapon['weapon_rank'] != 2:
                                equipment_slots.append(equipment.get('shield_id'))
                            if equipment.get('armor_id') and weapon['weapon_rank'] != 3:
                                equipment_slots.append(equipment.get('armor_id'))
                            
                            for eq_id in equipment_slots:
                                if eq_id:
                                    # Query equipment details
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
                                        # Add equipment flat boosts
                                        existing_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        existing_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        existing_equipment_boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                                        existing_equipment_boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                                        
                                        # Add equipment percentage boosts
                                        existing_equipment_boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                                        existing_equipment_boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                                        existing_equipment_boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                                        existing_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0
                            
                            # Remove percentage boosts first (reverse order of application)
                            if existing_equipment_boosts['pct_attack'] > 0:
                                true_base_attack = true_base_attack / (1 + existing_equipment_boosts['pct_attack']/100)
                            if existing_equipment_boosts['pct_health'] > 0:
                                true_base_health = true_base_health / (1 + existing_equipment_boosts['pct_health']/100)
                            if existing_equipment_boosts['pct_speed'] > 0:
                                true_base_speed = true_base_speed / (1 + existing_equipment_boosts['pct_speed']/100)
                            if existing_equipment_boosts['pct_defence'] > 0:
                                true_base_defence = true_base_defence / (1 + existing_equipment_boosts['pct_defence']/100)
                            
                            # Then remove flat boosts
                            true_base_attack -= existing_equipment_boosts['flat_attack']
                            true_base_health -= existing_equipment_boosts['flat_health']
                            true_base_speed -= existing_equipment_boosts['flat_speed']
                            true_base_defence -= existing_equipment_boosts['flat_defence']
                        
                        # 2. Reset the card to TRUE base stats (without any equipment boosts)
                        reset_query = """
                        UPDATE user_cards 
                        SET base_attack = %s,
                            base_health = %s,
                            base_speed = %s,
                            base_defence = %s,
                            attack_stat = %s,
                            health_stat = %s,
                            speed_stat = %s,
                            defence_stat = %s
                        WHERE id_ligneusercards = %s
                        """
                        await execute_query_with_retry(reset_query, (
                            true_base_attack, true_base_health, true_base_speed, true_base_defence,
                            true_base_attack, true_base_health, true_base_speed, true_base_defence,
                            user_card['id_ligneusercards']
                        ))
                        # herega
                        update_equipment_query = f"UPDATE user_cards SET {equipment_column} = %s WHERE id_ligneusercards = %s"
                        await execute_query_with_retry(update_equipment_query, (weapon['weapon_id'], user_card['id_ligneusercards']))

                        # 3. Calculate total equipment boosts (including the newly equipped item)
                        # Get all equipped items for this card AFTER adding the new one
                        equipment_query = """
                        SELECT weapon_id, shield_id, armor_id
                        FROM user_cards
                        WHERE id_ligneusercards = %s
                        """
                        equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))
                        
                        if equipment_result:
                            equipment = equipment_result[0]
                            
                            # Initialize total equipment boosts
                            total_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            
                            # Check each equipment slot
                            equipment_slots = [
                                equipment.get('weapon_id'),
                                equipment.get('shield_id'),
                                equipment.get('armor_id')
                            ]
                            
                            for eq_id in equipment_slots:
                                if eq_id:
                                    # Query equipment details
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
                                        # Add equipment flat boosts
                                        total_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        total_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        total_equipment_boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                                        total_equipment_boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                                        
                                        # Add equipment percentage boosts
                                        total_equipment_boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                                        total_equipment_boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                                        total_equipment_boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                                        total_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0
                            
                            # 4. Apply total equipment boosts to base stats
                            # Apply flat boosts first
                            base_flat_query = """
                            UPDATE user_cards 
                            SET base_attack = base_attack + %s,
                                base_health = base_health + %s,
                                base_speed = base_speed + %s,
                                base_defence = base_defence + %s
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(base_flat_query, (
                                total_equipment_boosts['flat_attack'], 
                                total_equipment_boosts['flat_health'], 
                                total_equipment_boosts['flat_speed'], 
                                total_equipment_boosts['flat_defence'],
                                user_card['id_ligneusercards']
                            ))
                            
                            # Apply percentage boosts
                            base_pct_query = """
                            UPDATE user_cards 
                            SET base_attack = base_attack * (1 + %s/100),
                                base_health = base_health * (1 + %s/100),
                                base_speed = base_speed * (1 + %s/100),
                                base_defence = base_defence * (1 + %s/100)
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(base_pct_query, (
                                total_equipment_boosts['pct_attack'], 
                                total_equipment_boosts['pct_health'], 
                                total_equipment_boosts['pct_speed'], 
                                total_equipment_boosts['pct_defence'],
                                user_card['id_ligneusercards']
                            ))
                            
                            # Update current stats to match base stats (before support boosts are applied)
                            update_stats_query = """
                            UPDATE user_cards 
                            SET attack_stat = base_attack,
                                health_stat = base_health,
                                speed_stat = base_speed,
                                defence_stat = base_defence
                            WHERE id_ligneusercards = %s
                            """
                            await execute_query_with_retry(update_stats_query, (user_card['id_ligneusercards'],))
                        
                        # 5. Recalculate total support boosts
                        total_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                        
                        # 6. Apply support boosts to this specific offense card
                        await self.apply_support_boosts_to_single_card(user_card['id_ligneusercards'], total_boosts)
                        
                        # 7. Update power_stat
                        await execute_query_with_retry(
                            "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                            (user_card['id_ligneusercards'],)
                        )
                else:
                    # For Fighter cards not in a team with supports
                    # 1. Get the true base stats by removing existing equipment buffs
                    # Get all currently equipped items for this card (BEFORE adding the new one)
                    equipment_query = """
                    SELECT weapon_id, shield_id, armor_id
                    FROM user_cards
                    WHERE id_ligneusercards = %s
                    """
                    equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))
                    
                    # Store original base stats to work with
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
                        true_base_speed = original_stats.get('base_speed', 0) or 0
                        true_base_defence = original_stats.get('base_defence', 0) or 0
                        
                        true_attack = original_stats.get('attack_stat', 0) or 0
                        true_health = original_stats.get('health_stat', 0) or 0
                        true_speed = original_stats.get('speed_stat', 0) or 0
                        true_defence = original_stats.get('defence_stat', 0) or 0
                        
                        # Calculate and remove existing equipment buffs
                        if equipment_result:
                            equipment = equipment_result[0]
                            
                            # Initialize existing equipment boosts
                            existing_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            
                            # Check each equipment slot (excluding the one we're about to equip)
                            equipment_slots = []
                            if equipment.get('weapon_id') and weapon['weapon_rank'] != 1:
                                equipment_slots.append(equipment.get('weapon_id'))
                            if equipment.get('shield_id') and weapon['weapon_rank'] != 2:
                                equipment_slots.append(equipment.get('shield_id'))
                            if equipment.get('armor_id') and weapon['weapon_rank'] != 3:
                                equipment_slots.append(equipment.get('armor_id'))
                            
                            for eq_id in equipment_slots:
                                if eq_id:
                                    # Query equipment details
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
                                        # Add equipment flat boosts
                                        existing_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        existing_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        existing_equipment_boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                                        existing_equipment_boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                                        
                                        # Add equipment percentage boosts
                                        existing_equipment_boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                                        existing_equipment_boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                                        existing_equipment_boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                                        existing_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0
                            
                            # Remove percentage boosts first (reverse order of application)
                            if existing_equipment_boosts['pct_attack'] > 0:
                                true_base_attack = true_base_attack / (1 + existing_equipment_boosts['pct_attack']/100)
                                true_attack = true_attack / (1 + existing_equipment_boosts['pct_attack']/100)
                            if existing_equipment_boosts['pct_health'] > 0:
                                true_base_health = true_base_health / (1 + existing_equipment_boosts['pct_health']/100)
                                true_health = true_health / (1 + existing_equipment_boosts['pct_health']/100)
                            if existing_equipment_boosts['pct_speed'] > 0:
                                true_base_speed = true_base_speed / (1 + existing_equipment_boosts['pct_speed']/100)
                                true_speed = true_speed / (1 + existing_equipment_boosts['pct_speed']/100)
                            if existing_equipment_boosts['pct_defence'] > 0:
                                true_base_defence = true_base_defence / (1 + existing_equipment_boosts['pct_defence']/100)
                                true_defence = true_defence / (1 + existing_equipment_boosts['pct_defence']/100)
                            
                            # Then remove flat boosts
                            true_base_attack -= existing_equipment_boosts['flat_attack']
                            true_base_health -= existing_equipment_boosts['flat_health']
                            true_base_speed -= existing_equipment_boosts['flat_speed']
                            true_base_defence -= existing_equipment_boosts['flat_defence']
                            
                            true_attack -= existing_equipment_boosts['flat_attack']
                            true_health -= existing_equipment_boosts['flat_health']
                            true_speed -= existing_equipment_boosts['flat_speed']
                            true_defence -= existing_equipment_boosts['flat_defence']
                        
                        # 2. Reset the card to TRUE base stats (without any equipment boosts)
                        reset_query = """
                        UPDATE user_cards 
                        SET base_attack = %s,
                            base_health = %s,
                            base_speed = %s,
                            base_defence = %s,
                            attack_stat = %s,
                            health_stat = %s,
                            speed_stat = %s,
                            defence_stat = %s
                        WHERE id_ligneusercards = %s
                        """
                        await execute_query_with_retry(reset_query, (
                            true_base_attack, true_base_health, true_base_speed, true_base_defence,
                            true_attack, true_health, true_speed, true_defence,
                            user_card['id_ligneusercards']
                        ))
                        update_equipment_query = f"UPDATE user_cards SET {equipment_column} = %s WHERE id_ligneusercards = %s"
                        await execute_query_with_retry(update_equipment_query, (weapon['weapon_id'], user_card['id_ligneusercards']))
                        # 3. Calculate total equipment boosts (including the newly equipped item)
                        # Get all equipped items for this card AFTER adding the new one
                        equipment_query = """
                        SELECT weapon_id, shield_id, armor_id
                        FROM user_cards
                        WHERE id_ligneusercards = %s
                        """
                        equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))
                        
                        if equipment_result:
                            equipment = equipment_result[0]
                            
                            # Initialize total equipment boosts
                            total_equipment_boosts = {
                                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0,  'flat_defence': 0, 'flat_speed': 0,
                                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                            }
                            
                            # Check each equipment slot
                            equipment_slots = [
                                equipment.get('weapon_id'),
                                equipment.get('shield_id'),
                                equipment.get('armor_id')
                            ]
                            
                            for eq_id in equipment_slots:
                                if eq_id:
                                    # Query equipment details
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
                                        # Add equipment flat boosts
                                        total_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                        total_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                        total_equipment_boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                                        total_equipment_boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                                        
                                        # Add equipment percentage boosts
                                        total_equipment_boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                                        total_equipment_boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                                        total_equipment_boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                                        total_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0
                            
                            # 4. Apply total equipment boosts to both base stats and current stats
                            # Apply flat boosts first
                            flat_boost_query = """
                            UPDATE user_cards 
                            SET attack_stat = attack_stat + %s,
                                health_stat = health_stat + %s,
                                speed_stat = speed_stat + %s,
                                defence_stat = defence_stat + %s,
                                base_attack = base_attack + %s,
                                base_health = base_health + %s,
                                base_speed = base_speed + %s,
                                base_defence = base_defence + %s
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
                            
                            # Apply percentage boosts
                            pct_boost_query = """
                            UPDATE user_cards 
                            SET attack_stat = attack_stat * (1 + %s/100),
                                health_stat = health_stat * (1 + %s/100),
                                speed_stat = speed_stat * (1 + %s/100),
                                defence_stat = defence_stat * (1 + %s/100),
                                base_attack = base_attack * (1 + %s/100),
                                base_health = base_health * (1 + %s/100),
                                base_speed = base_speed * (1 + %s/100),
                                base_defence = base_defence * (1 + %s/100)
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
                            
                            # Update power_stat
                            await execute_query_with_retry(
                                "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                                (user_card['id_ligneusercards'],)
                            )
                    
            elif card['type_card'] == 'Support':
                # For Support cards, update the card's stats
                update_equipment_query = f"UPDATE user_cards SET {equipment_column} = %s WHERE id_ligneusercards = %s"
                await execute_query_with_retry(update_equipment_query, (weapon['weapon_id'], user_card['id_ligneusercards']))
           
                # First, check if there are existing equipment buffs to remove
                equipment_query = """
                SELECT weapon_id, shield_id, armor_id
                FROM user_cards
                WHERE id_ligneusercards = %s
                """
                equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))
                
                # Store original stats to work with
                get_stats_query = """
                SELECT attack_stat, health_stat, speed_stat, defence_stat
                FROM user_cards
                WHERE id_ligneusercards = %s
                """
                stats_result = await execute_query_with_retry(get_stats_query, (user_card['id_ligneusercards'],))
                
                if stats_result:
                    original_stats = stats_result[0]
                    true_attack = original_stats.get('attack_stat', 0) or 0
                    true_health = original_stats.get('health_stat', 0) or 0
                    true_speed = original_stats.get('speed_stat', 0) or 0
                    true_defence = original_stats.get('defence_stat', 0) or 0
                    
                    # Calculate and remove existing equipment buffs
                    if equipment_result:
                        equipment = equipment_result[0]
                        
                        # Initialize existing equipment boosts
                        existing_equipment_boosts = {
                            'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                            'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                        }
                        
                        # Check each equipment slot (excluding the one we're about to equip)
                        equipment_slots = []
                        if equipment.get('weapon_id') and weapon['weapon_rank'] != 1:
                            equipment_slots.append(equipment.get('weapon_id'))
                        if equipment.get('shield_id') and weapon['weapon_rank'] != 2:
                            equipment_slots.append(equipment.get('shield_id'))
                        if equipment.get('armor_id') and weapon['weapon_rank'] != 3:
                            equipment_slots.append(equipment.get('armor_id'))
                        
                        for eq_id in equipment_slots:
                            if eq_id:
                                # Query equipment details
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
                                    # Add equipment flat boosts
                                    existing_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                    existing_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                    existing_equipment_boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                                    existing_equipment_boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                                    
                                    # Add equipment percentage boosts
                                    existing_equipment_boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                                    existing_equipment_boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                                    existing_equipment_boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                                    existing_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0
                        
                        # Remove percentage boosts first (reverse order of application)
                        if existing_equipment_boosts['pct_attack'] > 0:
                            true_attack = true_attack / (1 + existing_equipment_boosts['pct_attack']/100)
                        if existing_equipment_boosts['pct_health'] > 0:
                            true_health = true_health / (1 + existing_equipment_boosts['pct_health']/100)
                        if existing_equipment_boosts['pct_speed'] > 0:
                            true_speed = true_speed / (1 + existing_equipment_boosts['pct_speed']/100)
                        if existing_equipment_boosts['pct_defence'] > 0:
                            true_defence = true_defence / (1 + existing_equipment_boosts['pct_defence']/100)
                        
                        # Then remove flat boosts
                        true_attack -= existing_equipment_boosts['flat_attack']
                        true_health -= existing_equipment_boosts['flat_health']
                        true_speed -= existing_equipment_boosts['flat_speed']
                        true_defence -= existing_equipment_boosts['flat_defence']
                    
                    # Reset to true stats without equipment
                    reset_query = """
                    UPDATE user_cards 
                    SET attack_stat = %s,
                        health_stat = %s,
                        speed_stat = %s,
                        defence_stat = %s
                    WHERE id_ligneusercards = %s
                    """
                    await execute_query_with_retry(reset_query, (
                        true_attack, true_health, true_speed, true_defence,
                        user_card['id_ligneusercards']
                    ))
                
                # Now apply the new equipment boosts
                # Apply flat boosts to the support card itself
                flat_boost_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    speed_stat = speed_stat + %s,
                    defence_stat = defence_stat + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(flat_boost_query, (
                    flat_attack, flat_health, flat_speed, flat_defence,
                    user_card['id_ligneusercards']
                ))
                
                # Apply percentage boosts to the support card itself
                pct_boost_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat * (1 + %s/100),
                    health_stat = health_stat * (1 + %s/100),
                    speed_stat = speed_stat * (1 + %s/100),
                    defence_stat = defence_stat * (1 + %s/100)
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(pct_boost_query, (
                    pct_attack, pct_health, pct_speed, pct_defence,
                    user_card['id_ligneusercards']
                ))
                
                # Check if the support card is in a team
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                
                if team_result:
                    team_id = team_result[0]['team_id']
                    
                    # Check if the support card is in the team
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    
                    if team_card_result:
                        # Reset all fighter cards to base stats
                        reset_query = """
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
                        await execute_query_with_retry(reset_query, (team_id, user_id))
                        
                        # Calculate total boosts from all support cards in the team
                        total_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                        
                        # Apply these boosts to all fighter cards
                        await self.apply_support_boosts_to_offense(team_id, user_id, total_boosts)

            await ctx.send(f"Successfully equipped {weapon['weapon_name']} to {user_card['name']}!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="unequip", aliases=["ue", "uq"])
    async def unequip(self, ctx, *, args):
        # Split args by comma
        parts = [part.strip() for part in args.split(',')]
        if len(parts) < 2:
            await ctx.send("Usage: sl unequip Sword of Power, Mighty Warrior")
            return

        # The second part is the card name, the first part is the weapon name
        weapon_name = parts[0]
        card_name = parts[1]
        
        if not weapon_name or not card_name:
            await ctx.send("Please provide both weapon and card names. Example: sl unequip Sword of Power, Mighty Warrior")
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return

            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check weapon
            weapon_query = "SELECT * FROM weapon WHERE weapon_name LIKE %s"
            weapon_result = await execute_query_with_retry(weapon_query, (f'%{weapon_name}%',))

            if not weapon_result:
                await ctx.send(f"No weapons found matching '{weapon_name}'.")
                return

            # If multiple weapons found, show embed
            if len(weapon_result) > 1:
                embed = discord.Embed(
                    title="Multiple Weapons Found",
                    description="I found multiple weapons matching your search. Please specify which one you'd like to unequip:",
                    color=discord.Color.blue()
                )
                weapon_list = "\n".join([f"• {weapon['weapon_name']}" for weapon in weapon_result])
                embed.add_field(name="Matching Weapons", value=weapon_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact weapon name.")
                await ctx.send(embed=embed)
                return

            weapon = weapon_result[0]

            # Check card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name LIKE %s"
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, f'%{card_name}%'))
            
            if not user_card_result:
                await ctx.send(f"No cards found matching '{card_name}' in your collection.")
                return

            # If multiple cards found, show embed
            if len(user_card_result) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to unequip the weapon from:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in user_card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                await ctx.send(embed=embed)
                return

            user_card = user_card_result[0]
            
            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = await execute_query_with_retry(card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(f"Card '{card_name}' not found.")
                return
            
            card = card_result[0]

            # Check if the weapon is actually equipped
            equipment_column = {
                1: 'weapon_id',
                2: 'shield_id',
                3: 'armor_id'
            }.get(weapon['weapon_rank'])

            if user_card[equipment_column] != weapon['weapon_id']:
                await ctx.send(f"This {['weapon', 'shield', 'armor'][weapon['weapon_rank']-1]} is not equipped on this card.")
                return

            # Update inventory - add the weapon back to inventory
            inv_weapon_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            inv_weapon_result = await execute_query_with_retry(inv_weapon_query, (inventory_id, weapon['weapon_id']))

            if not inv_weapon_result:
                insert_inv_weapon_query = "INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter) VALUES (%s, %s, 1)"
                await execute_query_with_retry(insert_inv_weapon_query, (inventory_id, weapon['weapon_id']))
            else:
                update_inv_weapon_query = "UPDATE inventory_weapon SET quantiter = quantiter + 1 WHERE inventory_id = %s AND weapon_id = %s"
                await execute_query_with_retry(update_inv_weapon_query, (inventory_id, weapon['weapon_id']))

            # Get weapon boosts for the weapon being unequipped
            flat_attack = weapon.get('attack_boost', 0) or 0
            flat_health = weapon.get('health_boost', 0) or 0
            flat_speed = weapon.get('speed_boost', 0) or 0
            flat_defence = weapon.get('defense_boost', 0) or 0
            
            pct_attack = weapon.get('attack_percentage', 0) or 0
            pct_health = weapon.get('health_percentage', 0) or 0
            pct_speed = weapon.get('speed_percentage', 0) or 0
            pct_defence = weapon.get('defense_percentage', 0) or 0

            # IMPROVED HANDLING FOR CARDS BASED ON TYPE AND TEAM STATUS
            if card['type_card'] == 'Fighter':
                # Check if the offense card is in a team
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                
                has_support_in_team = False
                team_id = None
                
                if team_result:
                    team_id = team_result[0]['team_id']
                    
                    # Check if the offense card is in the team
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    
                    if team_card_result:
                        # Check if there are support cards in the team
                        support_query = """
                        SELECT COUNT(*) as support_count
                        FROM team_cards tc
                        JOIN card c ON tc.card_id = c.card_id
                        WHERE tc.team_id = %s AND c.type_card = 'Support'
                        """
                        support_result = await execute_query_with_retry(support_query, (team_id,))
                        
                        if support_result and support_result[0]['support_count'] > 0:
                            has_support_in_team = True
                
                # First, calculate all equipment boosts currently on the card
                # Get all currently equipped items for this card
                equipment_query = """
                SELECT weapon_id, shield_id, armor_id
                FROM user_cards
                WHERE id_ligneusercards = %s
                """
                equipment_result = await execute_query_with_retry(equipment_query, (user_card['id_ligneusercards'],))
                
                if equipment_result:
                    equipment = equipment_result[0]
                    
                    # Initialize total equipment boosts
                    total_equipment_boosts = {
                        'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                        'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                    }
                    
                    # Check each equipment slot
                    equipment_slots = [
                        equipment.get('weapon_id'),
                        equipment.get('shield_id'),
                        equipment.get('armor_id')
                    ]
                    
                    for eq_id in equipment_slots:
                        if eq_id:
                            # Query equipment details
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
                                # Add equipment flat boosts
                                total_equipment_boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                                total_equipment_boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                                total_equipment_boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                                total_equipment_boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                                
                                # Add equipment percentage boosts
                                total_equipment_boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                                total_equipment_boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                                total_equipment_boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                                total_equipment_boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0
                
                # Now calculate the equipment boosts without the weapon being unequipped
                remaining_equipment_boosts = {
                    'flat_attack': total_equipment_boosts['flat_attack'] - flat_attack,
                    'flat_health': total_equipment_boosts['flat_health'] - flat_health,
                    'flat_speed': total_equipment_boosts['flat_speed'] - flat_speed,
                    'flat_defence': total_equipment_boosts['flat_defence'] - flat_defence,
                    'pct_attack': total_equipment_boosts['pct_attack'] - pct_attack,
                    'pct_health': total_equipment_boosts['pct_health'] - pct_health,
                    'pct_speed': total_equipment_boosts['pct_speed'] - pct_speed,
                    'pct_defence': total_equipment_boosts['pct_defence'] - pct_defence
                }
                
                if has_support_in_team:
                    # 1. First, reset the card to base stats (removing all boosts)
                    reset_query = """
                    UPDATE user_cards
                    SET attack_stat = base_attack,
                        health_stat = base_health,
                        defence_stat = base_defence,
                        speed_stat = base_speed
                    WHERE id_ligneusercards = %s
                    """
                    await execute_query_with_retry(reset_query, (user_card['id_ligneusercards'],))
                    
                    # 2. Remove the weapon boosts from base stats
                    # First remove percentage boosts from base stats
                    base_pct_query = """
                    UPDATE user_cards 
                    SET base_attack = base_attack / (1 + %s/100),
                        base_health = base_health / (1 + %s/100),
                        base_speed = base_speed / (1 + %s/100),
                        base_defence = base_defence / (1 + %s/100)
                    WHERE id_ligneusercards = %s
                    """
                    await execute_query_with_retry(base_pct_query, (
                        total_equipment_boosts['pct_attack'], total_equipment_boosts['pct_health'], total_equipment_boosts['pct_speed'], total_equipment_boosts['pct_defence'],
                        user_card['id_ligneusercards']
                    ))
                    
                    # Then remove flat boosts from base stats
                    base_flat_query = """
                    UPDATE user_cards 
                    SET base_attack = base_attack - %s,
                        base_health = base_health - %s,
                        base_speed = base_speed - %s,
                        base_defence = base_defence - %s,
                        {equipment_column} = NULL
                    WHERE id_ligneusercards = %s
                    """
                    base_flat_query = base_flat_query.format(equipment_column=equipment_column)
                    await execute_query_with_retry(base_flat_query, (
                        total_equipment_boosts['flat_attack'], total_equipment_boosts['flat_health'], total_equipment_boosts['flat_speed'], total_equipment_boosts['flat_defence'],
                        user_card['id_ligneusercards']
                    ))
                    
                    # 3. Apply remaining equipment boosts to base stats
                    if any(remaining_equipment_boosts.values()):
                        # Apply flat boosts first
                        remaining_flat_query = """
                        UPDATE user_cards 
                        SET base_attack = base_attack + %s,
                            base_health = base_health + %s,
                            base_speed = base_speed + %s,
                            base_defence = base_defence + %s
                        WHERE id_ligneusercards = %s
                        """
                        await execute_query_with_retry(remaining_flat_query, (
                            remaining_equipment_boosts['flat_attack'],
                            remaining_equipment_boosts['flat_health'],
                            remaining_equipment_boosts['flat_speed'],
                            remaining_equipment_boosts['flat_defence'],
                            user_card['id_ligneusercards']
                        ))
                        
                        # Then apply percentage boosts
                        remaining_pct_query = """
                        UPDATE user_cards 
                        SET base_attack = base_attack * (1 + %s/100),
                            base_health = base_health * (1 + %s/100),
                            base_speed = base_speed * (1 + %s/100),
                            base_defence = base_defence * (1 + %s/100)
                        WHERE id_ligneusercards = %s
                        """
                        await execute_query_with_retry(remaining_pct_query, (
                            remaining_equipment_boosts['pct_attack'],
                            remaining_equipment_boosts['pct_health'],
                            remaining_equipment_boosts['pct_speed'],
                            remaining_equipment_boosts['pct_defence'],
                            user_card['id_ligneusercards']
                        ))
                    
                    # 4. Reset current stats to match the new base stats
                    update_current_query = """
                    UPDATE user_cards
                    SET attack_stat = base_attack,
                        health_stat = base_health,
                        defence_stat = base_defence,
                        speed_stat = base_speed
                    WHERE id_ligneusercards = %s
                    """
                    await execute_query_with_retry(update_current_query, (user_card['id_ligneusercards'],))
                    
                    # 5. Recalculate support boosts and apply them
                    support_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                    
                    # Apply support boosts to the offense card
                    await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                    
                    # 6. Update power_stat
                    await execute_query_with_retry(
                        "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                        (user_card['id_ligneusercards'],)
                    )
                else:
                    # For Fighter cards not in teams with supports
                    # Get the true base stats by removing all equipment boosts
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
                        true_base_speed = original_stats.get('base_speed', 0) or 0
                        true_base_defence = original_stats.get('base_defence', 0) or 0
                        
                        true_attack = original_stats.get('attack_stat', 0) or 0
                        true_health = original_stats.get('health_stat', 0) or 0
                        true_speed = original_stats.get('speed_stat', 0) or 0
                        true_defence = original_stats.get('defence_stat', 0) or 0
                        
                        # Remove all equipment boosts from base stats
                        # Remove percentage boosts first (reverse order of application)
                        if total_equipment_boosts['pct_attack'] > 0:
                            true_base_attack = true_base_attack / (1 + total_equipment_boosts['pct_attack']/100)
                            true_attack = true_attack / (1 + total_equipment_boosts['pct_attack']/100)
                        if total_equipment_boosts['pct_health'] > 0:
                            true_base_health = true_base_health / (1 + total_equipment_boosts['pct_health']/100)
                            true_health = true_health / (1 + total_equipment_boosts['pct_health']/100)
                        if total_equipment_boosts['pct_speed'] > 0:
                            true_base_speed = true_base_speed / (1 + total_equipment_boosts['pct_speed']/100)
                            true_speed = true_speed / (1 + total_equipment_boosts['pct_speed']/100)
                        if total_equipment_boosts['pct_defence'] > 0:
                            true_base_defence = true_base_defence / (1 + total_equipment_boosts['pct_defence']/100)
                            true_defence = true_defence / (1 + total_equipment_boosts['pct_defence']/100)
                        
                        # Then remove flat boosts
                        true_base_attack -= total_equipment_boosts['flat_attack']
                        true_base_health -= total_equipment_boosts['flat_health']
                        true_base_speed -= total_equipment_boosts['flat_speed']
                        true_base_defence -= total_equipment_boosts['flat_defence']
                        
                        true_attack -= total_equipment_boosts['flat_attack']
                        true_health -= total_equipment_boosts['flat_health']
                        true_speed -= total_equipment_boosts['flat_speed']
                        true_defence -= total_equipment_boosts['flat_defence']
                        
                        # Now apply remaining equipment boosts (without the unequipped weapon)
                        new_base_attack = true_base_attack
                        new_base_health = true_base_health
                        new_base_speed = true_base_speed
                        new_base_defence = true_base_defence
                        
                        new_attack = true_attack
                        new_health = true_health
                        new_speed = true_speed
                        new_defence = true_defence
                        
                        # Apply flat boosts first
                        new_base_attack += remaining_equipment_boosts['flat_attack']
                        new_base_health += remaining_equipment_boosts['flat_health']
                        new_base_speed += remaining_equipment_boosts['flat_speed']
                        new_base_defence += remaining_equipment_boosts['flat_defence']
                        
                        new_attack += remaining_equipment_boosts['flat_attack']
                        new_health += remaining_equipment_boosts['flat_health']
                        new_speed += remaining_equipment_boosts['flat_speed']
                        new_defence += remaining_equipment_boosts['flat_defence']
                        
                        # Then apply percentage boosts
                        if remaining_equipment_boosts['pct_attack'] > 0:
                            new_base_attack *= (1 + remaining_equipment_boosts['pct_attack']/100)
                            new_attack *= (1 + remaining_equipment_boosts['pct_attack']/100)
                        if remaining_equipment_boosts['pct_health'] > 0:
                            new_base_health *= (1 + remaining_equipment_boosts['pct_health']/100)
                            new_health *= (1 + remaining_equipment_boosts['pct_health']/100)
                        if remaining_equipment_boosts['pct_speed'] > 0:
                            new_base_speed *= (1 + remaining_equipment_boosts['pct_speed']/100)
                            new_speed *= (1 + remaining_equipment_boosts['pct_speed']/100)
                        if remaining_equipment_boosts['pct_defence'] > 0:
                            new_base_defence *= (1 + remaining_equipment_boosts['pct_defence']/100)
                            new_defence *= (1 + remaining_equipment_boosts['pct_defence']/100)
                        
                        # Update the card with new stats and remove the equipment
                        update_query = f"""
                        UPDATE user_cards 
                        SET {equipment_column} = NULL,
                            base_attack = %s,
                            base_health = %s,
                            base_speed = %s,
                            base_defence = %s,
                            attack_stat = %s,
                            health_stat = %s,
                            speed_stat = %s,
                            defence_stat = %s,
                            power_stat = %s
                        WHERE id_ligneusercards = %s
                        """
                        
                        new_power = new_attack + new_health + new_speed + new_defence
                        
                        await execute_query_with_retry(update_query, (
                            new_base_attack, new_base_health, new_base_speed, new_base_defence,
                            new_attack, new_health, new_speed, new_defence,
                            new_power,
                            user_card['id_ligneusercards']
                        ))
            
            elif card['type_card'] == 'Support':
                # For Support cards, first remove percentage boosts from the support card itself
                pct_update_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat / (1 + %s/100),
                    health_stat = health_stat / (1 + %s/100),
                    speed_stat = speed_stat / (1 + %s/100),
                    defence_stat = defence_stat / (1 + %s/100)
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(pct_update_query, (
                    pct_attack, pct_health, pct_speed, pct_defence,
                    user_card['id_ligneusercards']
                ))
                
                # Then remove flat boosts from the support card itself
                flat_update_query = f"""
                UPDATE user_cards 
                SET {equipment_column} = NULL,
                    attack_stat = attack_stat - %s,
                    health_stat = health_stat - %s,
                    speed_stat = speed_stat - %s,
                    defence_stat = defence_stat - %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(flat_update_query, (
                    flat_attack, flat_health, flat_speed, flat_defence,
                    user_card['id_ligneusercards']
                ))
                
                # Check if the support card is in a team
                team_query = "SELECT team_id FROM team WHERE user_id = %s"
                team_result = await execute_query_with_retry(team_query, (user_id,))
                
                if team_result:
                    team_id = team_result[0]['team_id']
                    
                    # Check if the support card is in the team
                    team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                    team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                    
                    if team_card_result:
                        # Reset all fighter cards to base stats
                        reset_query = """
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
                        await execute_query_with_retry(reset_query, (team_id, user_id))
                        
                        # Calculate total boosts from all support cards in the team
                        total_boosts = await self.calculate_total_support_boosts(team_id, user_id)
                        
                        # Apply these boosts to all fighter cards
                        await self.apply_support_boosts_to_offense(team_id, user_id, total_boosts)

            await ctx.send(f"Successfully unequipped {weapon['weapon_name']} from {user_card['name']}!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="rstat", aliases=["rs", "rts"])
    async def rstat(self, ctx, *, card_name):
        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to register first.")
                return

            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Check if user has the card
            card_query = "SELECT u.*,c.card_icon,c.type_card FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            card_result = await execute_query_with_retry(card_query, (user_id, f'%{card_name}%'))

            if not card_result:
                await ctx.send(f"You don't have a card named {card_name}.")
                return
            if len(card_result) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to reset stats for:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                await ctx.send(embed=embed)
                return
            card = card_result[0]
            if card['type_card'] in ['Support','Booster']:
                await ctx.send("This command can only be used with Offence-type cards.")
                return

            # Check if user has the reset stat product
            inventory_query = "SELECT ip.*,p.product_icon FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id JOIN product p ON ip.product_id = ip.product_id WHERE i.user_id = %s AND ip.product_id = 3"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("You don't have a reset stat item in your inventory.")
                return

            # Update inventory
            if inventory_result[0]['quantiter'] == 1:
                delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
            else:
                update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

            # Check if card has equipment
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card['card_id'])

            # Check if card is in a team with support cards
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            
            has_support_in_team = False
            team_id = None
            
            if team_result:
                team_id = team_result[0]['team_id']
                
                # Check if the card is in the team
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                
                if team_card_result:
                    # Check if there are support cards in the team
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

            # SCENARIO HANDLING:
            # 1. No support in team and no equipment
            if not has_support_in_team and not has_equipment:
                # Simply reset stats
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
                    current_statpoints = current_statpoints + used_statpoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0,
                    power_stat = attack_stat + speed_stat + health_stat + defence_stat - used_attackpoints - used_speedpoints - used_healthpoints - used_defencepoints
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))
            
            # 2. Has equipment but no support cards in team
            elif has_equipment and not has_support_in_team:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Calculate new true base stats after removing used stat points
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] - card['used_attackpoints'],
                    'health': true_base_stats['health'] - card['used_healthpoints'],
                    'defence': true_base_stats['defence'] - card['used_defencepoints'],
                    'speed': true_base_stats['speed'] - card['used_speedpoints']
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats - used stats
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Update base stats and current stats with the new values
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
                    current_statpoints = current_statpoints + used_statpoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0
                WHERE id_ligneusercards = %s
                """
                
                new_power = new_base_stats['attack'] + new_base_stats['health'] + new_base_stats['defence'] + new_base_stats['speed']
                
                await execute_query_with_retry(update_base_query, (
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_power,
                    card['id_ligneusercards']
                ))
            
            # 3. No equipment but has support cards in team
            elif not has_equipment and has_support_in_team:
                # Reset to base stats first
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))
                
                # Remove used stat points from base stats
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
                    current_statpoints = current_statpoints + used_statpoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (card['id_ligneusercards'],))
                
                # Now apply support boosts to current stats
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )
            
            # 4. Has both equipment and support cards in team
            else:
                # First, reset card to base stats
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))
                
                # Get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Calculate new true base stats after removing used stat points
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] - card['used_attackpoints'],
                    'health': true_base_stats['health'] - card['used_healthpoints'],
                    'defence': true_base_stats['defence'] - card['used_defencepoints'],
                    'speed': true_base_stats['speed'] - card['used_speedpoints']
                }
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats - used stats
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Reset current stats to new true base stats (without equipment boosts)
                reset_current_query = """
                UPDATE user_cards
                SET attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(
                    reset_current_query,
                    (new_true_base_stats['attack'], new_true_base_stats['health'], 
                    new_true_base_stats['defence'], new_true_base_stats['speed'],
                    card['id_ligneusercards'])
                )
                
                # Update base stats and reset used stats
                update_base_query = """
                UPDATE user_cards 
                SET base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    current_statpoints = current_statpoints + used_statpoints,
                    used_attackpoints = 0,
                    used_healthpoints = 0,
                    used_defencepoints = 0,
                    used_speedpoints = 0,
                    used_statpoints = 0
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    card['id_ligneusercards']
                ))
                
                # Apply equipment boosts to current stats
                await self.apply_equipment_boosts(user_id, card['card_id'], equipment_boosts)
                
                # Then apply support boosts
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )

            # Get updated stats for response message
            updated_stats_query = """
            SELECT attack_stat, health_stat, defence_stat, speed_stat, current_statpoints
            FROM user_cards
            WHERE id_ligneusercards = %s
            """
            updated_stats = await execute_query_with_retry(updated_stats_query, (card['id_ligneusercards'],))
            
            await ctx.send(f"Applied 1x {inventory_result[0]['product_icon']} statreset to {card['card_icon']} {card['name']}. Stats have been reset and {updated_stats[0]['current_statpoints']} stat points are now available.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    def calculate_remaining_time(self, last_timestamp, duration):
        """ Calculate remaining time for a specific command. """
        if last_timestamp is None:
            return None
            
        # Convert string to datetime if it's not already a datetime object
        if isinstance(last_timestamp, str):
            try:
                last_timestamp = datetime.strptime(last_timestamp, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # If parsing fails, return None
                return None
                
        current_time = datetime.now()
        time_since_last_use = current_time - last_timestamp
        remaining_time = duration - time_since_last_use
        return remaining_time if remaining_time.total_seconds() > 0 else None

    def format_time_remaining(self, remaining_time):
        """ Format remaining time in human-readable format. """
        total_seconds = int(remaining_time.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    @commands.command(name="reset", aliases=["rst", "rt"])
    async def reset(self, ctx, reset_type: str):
        try:
            # Get user_id and squad_id
            user_query = "SELECT user_id, squad_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to register first.")
                return

            user_id = user_result[0]['user_id']
            squad_id = user_result[0]['squad_id']
            
            # Check for active trades
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Define reset types and their corresponding product IDs, timer columns, and cooldown durations
            reset_types = {
                'rolls': {
                    'product_id': 2, 
                    'timer_column': 'roll_timer', 
                    'count_column': 'roll_count',
                    'cooldown_duration': timedelta(hours=5)
                },
                'dungeon': {
                    'product_id': 14, 
                    'timer_column': 'dungeon_timer', 
                    'count_column': None,
                    'cooldown_duration': timedelta(minutes=10)
                },
                'mission': {
                    'product_id': 15, 
                    'timer_column': 'mission_timer', 
                    'count_column': None,
                    'cooldown_duration': timedelta(minutes=3)
                },
                'fight': {
                    'product_id': 16, 
                    'timer_column': 'fight_timer', 
                    'count_column': None,
                    'cooldown_duration': timedelta(minutes=5)
                },
                'squad': {
                    'product_id': 18, 
                    'timer_column': 'squad_timer', 
                    'count_column': None,
                    'cooldown_duration': timedelta(minutes=5)
                }
            }

            # Check if reset type is valid
            reset_type = reset_type.lower()
            if reset_type not in reset_types:
                await ctx.send("Invalid reset type. Use rolls, dungeon, mission, fight, or squad.")
                return

            # Check current timer status
            timer_query = f"""
            SELECT {reset_types[reset_type]['timer_column']} 
            FROM user 
            WHERE user_id = %s
            """
            timer_result = await execute_query_with_retry(timer_query, (user_id,))
            
            if timer_result:
                current_timer = timer_result[0][reset_types[reset_type]['timer_column']]
                remaining_time = self.calculate_remaining_time(
                    current_timer, 
                    reset_types[reset_type]['cooldown_duration']
                )
                
                if remaining_time is None:
                    await ctx.send(f"Your {reset_type} timer is not on cooldown. No reset needed.")
                    return

            # Get reset item from inventory
            inventory_query = """
            SELECT * FROM inventory_product 
            WHERE inventory_id = %s AND product_id = %s
            """
            inventory_result = await execute_query_with_retry(
                inventory_query, 
                (inventory_id, reset_types[reset_type]['product_id'])
            )

            if not inventory_result:
                await ctx.send(f"You don't have a {reset_type} reset item in your inventory.")
                return

            # Update inventory based on quantity
            if inventory_result[0]['quantiter'] == 1:
                delete_query = """
                DELETE FROM inventory_product 
                WHERE id_ligneinventoryproduct = %s
                """
                await execute_query_with_retry(
                    delete_query, 
                    (inventory_result[0]['id_ligneinventoryproduct'],)
                )
            else:
                update_query = """
                UPDATE inventory_product 
                SET quantiter = quantiter - 1 
                WHERE id_ligneinventoryproduct = %s
                """
                await execute_query_with_retry(
                    update_query, 
                    (inventory_result[0]['id_ligneinventoryproduct'],)
                )

            # Build and execute the update query based on reset type
            if reset_type == 'squad' and squad_id is not None:
                # Reset squad timer for all users in the same squad
                update_query = f"""
                UPDATE user 
                SET {reset_types[reset_type]['timer_column']} = NULL
                WHERE squad_id = %s
                """
                await execute_query_with_retry(update_query, (squad_id,))
                message = f"Successfully reset the squad timer for all squad members."
            else:
                # Build the update query for other reset types
                update_parts = [f"{reset_types[reset_type]['timer_column']} = NULL"]
                if reset_types[reset_type]['count_column']:
                    update_parts.append(f"{reset_types[reset_type]['count_column']} = 0")
            
                update_query = f"""
                UPDATE user 
                SET {', '.join(update_parts)}
                WHERE user_id = %s
                """
                await execute_query_with_retry(update_query, (user_id,))
            
                message = f"Successfully reset your {reset_type} timer"
                if reset_types[reset_type]['count_column']:
                    message += " and count"
                message += "."

            await ctx.send(message)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="charm",aliases=["chm", "chr"])
    async def charm(self, ctx, charm_type: str, *, card_name: str):
        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to register first.")
                return

            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Check if user has the card
            card_query = "SELECT u.*,c.card_icon,c.type_card FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))

            if not card_result:
                await ctx.send(f"You don't have a card named {card_name}.")
                return
            if len(card_result) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to unequip the weapon from:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                await ctx.send(embed=embed)
                return
            card = card_result[0]
            if card['type_card'] in ['Support','Booster']:
                await ctx.send("This command can only be used with Offence-type cards.")
                return

            # Determine product_id and charm attributes based on charm type
            charm_info = {
                'attack': {'product_id': 9, 'charm_column': 'charm_attack', 'count_column': 'attack_count'},
                'speed': {'product_id': 10, 'charm_column': 'charm_speed', 'count_column': 'speed_count'},
                'defence': {'product_id': 11, 'charm_column': 'charm_defence', 'count_column': 'defence_count'},
                'health': {'product_id': 12, 'charm_column': 'charm_health', 'count_column': 'health_count'}
            }

            charm_data = charm_info.get(charm_type.lower())
            if not charm_data:
                await ctx.send("Invalid charm type. Use attack, speed, defence, or health.")
                return

            # NEW: Check if the specific charm is already active
            if card[charm_data['charm_column']] == 1:
                await ctx.send(f"This card already has an active {charm_type} charm.")
                return

            # Check if user has the specific card with ID 105
            card_105_query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = 105"
            card_105_result = await execute_query_with_retry(card_105_query, (user_id,))

            # Determine charm count based on card 105 presence
            charm_count = 5 if card_105_result else 3

            # Check if user has the charm product
            inventory_query = """
        SELECT ip.*, p.product_icon FROM inventory i 
        JOIN inventory_product ip ON i.inventory_id = ip.inventory_id JOIN product p ON ip.product_id = p.product_id
        WHERE i.user_id = %s AND ip.product_id = %s
        """
            inventory_result = await execute_query_with_retry(inventory_query, (user_id, charm_data['product_id']))

            if not inventory_result:
                await ctx.send(f"You don't have a {charm_type} charm in your inventory.")
                return

            # Update inventory
            if inventory_result[0]['quantiter'] == 1:
                delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
            else:
                update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

            # Apply charm to card
            update_query = f"""
        UPDATE user_cards
        SET 
            {charm_data['charm_column']} = 1,
            {charm_data['count_column']} = {charm_data['count_column']} + %s
        WHERE user_id = %s AND name = %s
        """
            await execute_query_with_retry(update_query, (charm_count, user_id, card['name']))

            await ctx.send(f"Applied 1x {inventory_result[0]['product_icon']} {charm_type} charm to {card['card_icon']} {card['name']}. The charm stat has been increased and count set to {charm_count}.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="capsule", aliases=["cap", "cs"])
    async def capsule(self, ctx, capsule_type, *args):
        try:
            # Parse arguments to extract card name and optional quantity
            quantity = 1
            card_name = ' '.join(args)
            
            # Check if last argument is a number (quantity)
            try:
                if args and args[-1].isdigit():
                    quantity = int(args[-1])
                    card_name = ' '.join(args[:-1])
            except (IndexError, ValueError):
                pass
            
            # Validate quantity
            if quantity < 1:
                await ctx.send("Quantity must be a positive number.")
                return
            
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
            
            if not user_result:
                await ctx.send("You need to register first.")
                return
            
            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Check if user has the card (flexible matching)
            card_query = "SELECT u.*,c.card_icon,c.type_card FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND LOWER(name) LIKE LOWER(%s)"
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))
            
            if not card_result:
                await ctx.send(f"You don't have a card matching '{card_name}'.")
                return
            
            if len(card_result) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to use the capsule on:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                await ctx.send(embed=embed)
                return
            
            card = card_result[0]
            if card['type_card'] in ['Support','Booster']:
                await ctx.send("This command can only be used with Offence-type cards.")
                return
            
            # Determine product_id based on capsule type
            product_id_map = {'acap': 4, 'scap': 5, 'hcap': 6, 'dcap': 7}
            product_id = product_id_map.get(capsule_type.lower())
            
            if not product_id:
                await ctx.send("Invalid capsule type. Use acap, scap, hcap, or dcap.")
                return
            
            # Check if user has enough capsules in inventory
            inventory_query = """
            SELECT 
                ip.*, 
                p.product_icon 
            FROM 
                inventory i 
            JOIN 
                inventory_product ip ON i.inventory_id = ip.inventory_id 
            JOIN 
                product p ON ip.product_id = p.product_id 
            WHERE 
                i.user_id = %s AND ip.product_id = %s
            """
            inventory_result = await execute_query_with_retry(inventory_query, (user_id, product_id))
            
            if not inventory_result:
                await ctx.send(f"You don't have a {capsule_type} in your inventory.")
                return
            
            inventory_item = inventory_result[0]
            
            # Check if user has enough quantity of capsules
            if inventory_item['quantiter'] < quantity:
                await ctx.send(f"You only have {inventory_item['quantiter']} {capsule_type} in your inventory.")
                return
            
            # Update inventory
            if inventory_item['quantiter'] == quantity:
                # Delete the entire inventory product row
                delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(delete_query, (inventory_item['id_ligneinventoryproduct'],))
            else:
                # Reduce quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter - %s WHERE id_ligneinventoryproduct = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_item['id_ligneinventoryproduct']))
            
            # Determine which stat to update
            stat_map = {
                'acap': ('attack_stat', 'used_attackpoints', 'base_attack'),
                'scap': ('speed_stat', 'used_speedpoints', 'base_speed'),
                'hcap': ('health_stat', 'used_healthpoints', 'base_health'),
                'dcap': ('defence_stat', 'used_defencepoints', 'base_defence')
            }
            stat_column, used_column, base_column = stat_map[capsule_type.lower()]
            
            # Check if card has equipment
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card['card_id'])

            # Check if card is in a team with support cards
            team_query = "SELECT team_id FROM team WHERE user_id = %s"
            team_result = await execute_query_with_retry(team_query, (user_id,))
            
            has_support_in_team = False
            team_id = None
            
            if team_result:
                team_id = team_result[0]['team_id']
                
                # Check if the card is in the team
                team_card_query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
                team_card_result = await execute_query_with_retry(team_card_query, (team_id, card['card_id']))
                
                if team_card_result:
                    # Check if there are support cards in the team
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

            # SCENARIO HANDLING:
            # 1. No support in team and no equipment
            if not has_support_in_team and not has_equipment:
                # Apply stat buff directly to both base stats and current stats
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
            
            # 2. Support cards but no equipment
            elif has_support_in_team and not has_equipment:
                # Reset to base stats first
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(reset_query, (card['id_ligneusercards'],))
                
                # Apply stat buff to both base stats and current stats
                update_base_query = f"""
                UPDATE user_cards
                SET 
                    {base_column} = {base_column} + %s,
                    {used_column} = {used_column} + %s,
                    used_statpoints = used_statpoints + %s
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_base_query, (quantity, quantity, quantity, card['id_ligneusercards']))
                
                # Update current stats to match base stats
                update_current_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE id_ligneusercards = %s
                """
                await execute_query_with_retry(update_current_query, (card['id_ligneusercards'],))
                
                # Now apply support boosts to current stats
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )
            
            # 3. No support cards but has equipment
            elif not has_support_in_team and has_equipment:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = dict(true_base_stats)
                if stat_column == 'attack_stat':
                    new_true_base_stats['attack'] += quantity
                elif stat_column == 'health_stat':
                    new_true_base_stats['health'] += quantity
                elif stat_column == 'defence_stat':
                    new_true_base_stats['defence'] += quantity
                elif stat_column == 'speed_stat':
                    new_true_base_stats['speed'] += quantity
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Update base stats and current stats with the buff
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
                
                # Add the specific used_column update
                if used_column == 'used_attackpoints':
                    update_base_query += ", used_attackpoints = used_attackpoints + %s"
                elif used_column == 'used_healthpoints':
                    update_base_query += ", used_healthpoints = used_healthpoints + %s"
                elif used_column == 'used_defencepoints':
                    update_base_query += ", used_defencepoints = used_defencepoints + %s"
                elif used_column == 'used_speedpoints':
                    update_base_query += ", used_speedpoints = used_speedpoints + %s"
                
                update_base_query += " WHERE id_ligneusercards = %s"
                
                new_power = new_base_stats['attack'] + new_base_stats['health'] + new_base_stats['defence'] + new_base_stats['speed']
                
                params = [
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    new_power,
                    quantity,
                    quantity,
                    card['id_ligneusercards']
                ]
                
                await execute_query_with_retry(update_base_query, params)
            
            # 4. Has both support cards and equipment
            else:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
                
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
                
                # Apply stat buff to true base stats
                new_true_base_stats = dict(true_base_stats)
                if stat_column == 'attack_stat':
                    new_true_base_stats['attack'] += quantity
                elif stat_column == 'health_stat':
                    new_true_base_stats['health'] += quantity
                elif stat_column == 'defence_stat':
                    new_true_base_stats['defence'] += quantity
                elif stat_column == 'speed_stat':
                    new_true_base_stats['speed'] += quantity
                
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
                
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
                
                # Reset current stats to true base stats + stat increases (without equipment boosts)
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
                    card['id_ligneusercards'])
                )
                
                # Update base stats and used stats
                update_base_query = """
                UPDATE user_cards 
                SET base_attack = %s,
                    base_health = %s,
                    base_defence = %s,
                    base_speed = %s,
                    used_statpoints = used_statpoints + %s
                """
                
                # Add the specific used_column update
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
                    new_base_stats['attack'],
                    new_base_stats['health'],
                    new_base_stats['defence'],
                    new_base_stats['speed'],
                    quantity,
                    quantity,
                    card['id_ligneusercards']
                ]
                
                await execute_query_with_retry(update_base_query, params)
                
                # Apply equipment boosts to current stats
                await self.apply_equipment_boosts(user_id, card['card_id'], equipment_boosts)
                
                # Then apply support boosts
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
                
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE id_ligneusercards = %s",
                    (card['id_ligneusercards'],)
                )

            # Define a mapping for readable capsule names
            capsule_name_map = {
                'acap': 'Attack Capsule',
                'scap': 'Speed Capsule',
                'hcap': 'Health Capsule',
                'dcap': 'Defense Capsule'
            }

            # Get updated stats for response message
            updated_stats_query = """
            SELECT attack_stat, health_stat, defence_stat, speed_stat
            FROM user_cards
            WHERE id_ligneusercards = %s
            """
            updated_stats = await execute_query_with_retry(updated_stats_query, (card['id_ligneusercards'],))
            
            # In the final message, use this map
            readable_capsule_name = capsule_name_map.get(capsule_type.lower(), capsule_type.upper())
            
            # Create a more detailed response message
            response = f"Applied {quantity}x {inventory_item['product_icon']} {readable_capsule_name} to {card['card_icon']} {card['name']}."
            
            # Add stat information
            if stat_column == 'attack_stat':
                response += f" Attack increased to {updated_stats[0]['attack_stat']}."
            elif stat_column == 'health_stat':
                response += f" Health increased to {updated_stats[0]['health_stat']}."
            elif stat_column == 'defence_stat':
                response += f" Defence increased to {updated_stats[0]['defence_stat']}."
            elif stat_column == 'speed_stat':
                response += f" Speed increased to {updated_stats[0]['speed_stat']}."
            
            await ctx.send(response)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="enhance",aliases=["enh", "en"])
    async def enhance(self, ctx, *, card_name):
        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to register first.")
                return

            user_id = user_result[0]['user_id']
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Check if user has the card
            card_query = "SELECT u.*,c.card_icon,c.type_card,c.max_level FROM user_cards u, card c  WHERE u.card_id = c.card_id AND user_id = %s AND name LIKE %s"
            card_result = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))

            if not card_result:
                await ctx.send(f"You don't have a card named {card_name}.")
                return
            if len(card_result) > 1:
                embed = discord.Embed(
                    title="Multiple Cards Found",
                    description="I found multiple cards matching your search. Please specify which one you'd like to unequip the weapon from:",
                    color=discord.Color.blue()
                )
                card_list = "\n".join([f"• {card['name']}" for card in card_result])
                embed.add_field(name="Matching Cards", value=card_list, inline=False)
                embed.set_footer(text="Please use the command again with the exact card name.")
                await ctx.send(embed=embed)
                return
            card = card_result[0]
            if card['type_card'] in ['Support','Booster']:
                await ctx.send("This command can only be used with Offence-type cards.")
                return
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    # Check if card is at max level
        if card['current_level'] >= card['max_level']:
            await ctx.send(f"{card['card_icon']} {card['name']} is already at max level and cannot be enhanced further.")
            return
        # NEW: Check if card already has an enhancer
        if card['id_enhancer']:
            await ctx.send(f"{card['card_icon']} {card['name']} already has an active enhancer.")
            return

        # Check if user has the enhancer product
        inventory_query = "SELECT ip.*,p.product_icon FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id JOIN product p ON ip.product_id = p.product_id  WHERE i.user_id = %s AND ip.product_id = 17"
        inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

        if not inventory_result:
            await ctx.send("You don't have an enhancer in your inventory.")
            return
        # Update inventory
        if inventory_result[0]['quantiter'] == 1:
            delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
            await execute_query_with_retry(delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
        else:
            update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
            await execute_query_with_retry(update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

        # Apply enhancer to card
        update_query = "UPDATE user_cards SET id_enhancer = 17,fight_count=3 WHERE user_id = %s AND name = %s"
        await execute_query_with_retry(update_query, (user_id, card['name']))

        await ctx.send(f"Applied 1x {inventory_result[0]['product_icon']} enhancer to {card['card_icon']} {card['name']}. The enhancement process has started.")
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
    @commands.command(name="inventory",aliases=["inv", "i"])
    async def inventoryview(self, ctx):
        user_id = ctx.author.id
        
        try:
            user_id = ctx.author.id
        

            user_query = """
            SELECT u.user_id, u.quantiter_fragment, u.fragment_cap 
            FROM user u 
            WHERE u.user_authorid = %s
            """
            user_result = await execute_query_with_retry(user_query, (str(user_id),))

            if not user_result:
                await ctx.send("Error: User not registered.")
                return

            db_user_id = user_result[0]['user_id']
            essence_stone_data = {
                'current': user_result[0]['quantiter_fragment'],
                'cap': user_result[0]['fragment_cap']
            }

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (db_user_id,))

            if not inventory_result:
                await ctx.send("Error: Inventory not found.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            categories = {
                'weapons': ('inventory_weapon', 'weapon', 'weapon_name', 'weapon_id', 'weapon_id', 'weapon_icon'),
                'boosters_enhancers': ('inventory_product', 'product', 'product_name', 'product_id', 'product_id', 'product_icon'),
                'essence_stones': ('inventory_frags', 'fragments', 'name_fragment', 'id_fragment', 'frag_id', 'fragments_icon'),
                'chests': ('inventory_chest', 'chest', 'chest_name', 'chest_id', 'chest_id', 'chest_icon'),
                'dungeon_key': ('inventory_dungeonkey', 'dungeon_key', 'dungeonkey_name', 'dungeonkey_id', 'dungeonkey_id', 'dungeonkey_icon'),
                'pickaxes': ('inventory_pickaxe', 'pickaxe', 'pickaxe_name', 'id_pickaxe', 'id_pickaxe', 'pickaxe_icon')  # Add this line
            }

            inventory_data = {}
            for category, (inv_table, item_table, name_column, id_column, invid_column, icon_column) in categories.items():
                query = f"""
                SELECT i.quantiter as quantity, 
                       t.{name_column} as name,
                       t.{icon_column} as icon
                FROM {inv_table} i
                JOIN {item_table} t ON i.{invid_column} = t.{id_column}
                WHERE i.inventory_id = %s
                """
                result = await execute_query_with_retry(query, (inventory_id,))
                result = self.remove_duplicates(result)
                inventory_data[category] = result



            view = InventoryMainView(inventory_data, essence_stone_data, original_user=ctx.author)
            embed = view.get_main_menu_embed(ctx.author)
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    async def get_user_inventory_data(self, ctx):
        user_id = ctx.author.id
        
        try:
            user_id = ctx.author.id
        
            user_query = """
            SELECT u.user_id, u.quantiter_fragment, u.fragment_cap 
            FROM user u 
            WHERE u.user_authorid = %s
            """
            user_result = await execute_query_with_retry(user_query, (str(user_id),))

            if not user_result:
                await ctx.send("Error: User not registered.")
                return None, None, None

            db_user_id = user_result[0]['user_id']
            essence_stone_data = {
                'current': user_result[0]['quantiter_fragment'],
                'cap': user_result[0]['fragment_cap']
            }

            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (db_user_id,))

            if not inventory_result:
                await ctx.send("Error: Inventory not found.")

                return None, None, None

            return inventory_result[0]['inventory_id'], essence_stone_data
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="sell", aliases=["sl", "se"])
    async def sell_fragment(self, ctx, *, args):
        try:
            # Parse arguments
            args_parts = args.split()
            if len(args_parts) < 2:
                await ctx.send("Error: Please provide either fragment name and quantity or rank. Usage: `sl sell <fragment_name> <quantity/all>` or `sl sell rank <S/A/B/C/D/E>`")
                return

            # Initial database connection and user verification
            # Get user information
            user_query = """
            SELECT user_id 
            FROM user 
            WHERE user_authorid = %s
            """
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("Error: User not registered.")
                return

            user_id = user_result[0]['user_id']
            
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

            # Check if user already has a pending sell operation
            pending_sell_query = """
            SELECT id
            FROM sell_status
            WHERE user_id = %s
            """
            pending_sell_result = await execute_query_with_retry(pending_sell_query, (user_id,))
            
            if pending_sell_result:
                await ctx.send("⚠️ You already have a pending sell operation. Please complete or cancel your previous sell before starting a new one.")
                return

            # Add user to sell_status table
            insert_sell_status_query = """
            INSERT INTO sell_status (user_id)
            VALUES (%s)
            """
            await execute_query_with_retry(insert_sell_status_query, (user_id,))

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                # Remove user from sell_status if inventory not found
                await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                await ctx.send("Error: Inventory not found.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check if it's a rank-based sale
            is_rank_sale = args_parts[0].lower() == "rank"
            
            if is_rank_sale:
                if len(args_parts) != 2:
                    # Remove user from sell_status on error
                    await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send("Error: For rank-based sales, please use the format: `sl sell rank <S/A/B/C/D/E>`")
                    return

                rank = args_parts[1].upper()
                rank_map = {'S': 2, 'A': 3, 'B': 4, 'C': 5, 'D': 6, 'E': 7}
                
                if rank not in rank_map:
                    await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send("Error: Invalid rank. Please use S, A, B, C, D, or E.")
                    return

                cardrank_id = rank_map[rank]

                # Get all fragments of the specified rank
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
                fragment_results = await execute_query_with_retry( rank_query, (inventory_id, cardrank_id))

                if not fragment_results:
                    await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))                
                    await ctx.send(f"Error: You don't have any fragments of rank {rank}.")
                    return

                # Calculate total value and create fragment summary
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

                # Create confirmation embed for rank-based sale
                confirm_embed = discord.Embed(
                    title=f"Sell All Rank {rank} Essence Stones",
                    description="Are you sure you want to sell the following essence stones?",
                    color=discord.Color.gold()
                )

                summary_text = ""
                for frag in fragments_summary:
                    summary_text += f"{frag['icon']} {frag['name']} x{frag['quantity']} ({frag['amount']:,} <:won:1304034228966658048>)\n"

                confirm_embed.add_field(name="Essence Stones to Sell", value=summary_text, inline=False)
                confirm_embed.add_field(name="Total Amount", value=f"{total_amount:,} <:won:1304034228966658048>", inline=False)

            else:
                # Original single fragment sale logic
                quantity = args_parts[-1].lower()
                fragment_name = " ".join(args_parts[:-1])

                fragment_query = """
                SELECT id_fragment, name_fragment, price, fragments_icon
                FROM fragments
                WHERE name_fragment LIKE %s
                """
                fragment_results = await execute_query_with_retry( fragment_query, (f"%{fragment_name}%",))

                if not fragment_results:
                    await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send("Error: Fragment not found.")
                    return

                if len(fragment_results) > 1:
                    embed = discord.Embed(
                        title="Multiple Essence Stones Found",
                        description="I found multiple stones matching your search. Please specify which one you'd like to sell:",
                        color=discord.Color.blue()
                    )
                    
                    fragment_list = "\n".join([f"• {frag['name_fragment']}" for frag in fragment_results])
                    embed.add_field(name="Matching Stones", value=fragment_list, inline=False)
                    embed.set_footer(text="Please use the command again with the exact name of the essence stone you want to sell.")
                    await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send(embed=embed)
                    return

                fragment_data = fragment_results[0]
                frag_id = fragment_data['id_fragment']
                fragment_price = fragment_data['price']

                # Check if user has the fragment
                inventory_check_query = """
                SELECT quantiter
                FROM inventory_frags
                WHERE inventory_id = %s AND frag_id = %s
                """
                inventory_check = await execute_query_with_retry( inventory_check_query, (inventory_id, frag_id))

                if not inventory_check:
                    await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
                    await ctx.send("Error: You don't have any of these essence stones.")
                    return

                current_quantity = inventory_check[0]['quantiter']

                # Handle quantity logic
                if quantity == 'all':
                    sell_quantity = current_quantity
                else:
                    try:
                        sell_quantity = int(quantity)
                        if sell_quantity <= 0:
                            await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))             
                            await ctx.send("Error: Please enter a positive quantity.")
                            return
                        if sell_quantity > current_quantity:
                            await ctx.send(f"Error: You only have {current_quantity} fragments.")
                            await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))             
                            return
                    except ValueError:
                        await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))             
                        await ctx.send("Error: Invalid quantity. Please use a number or 'all'.")
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

                # Create confirmation embed for single fragment sale
                confirm_embed = discord.Embed(
                    title="Sell Confirmation",
                    description=f"Are you sure you want to sell {sell_quantity} {fragment_data['fragments_icon']} {fragment_data['name_fragment']}?",
                    color=discord.Color.gold()
                )
                confirm_embed.add_field(name="Price per Stone", value=f"{fragment_price:,} <:won:1304034228966658048>", inline=True)
                confirm_embed.add_field(name="Total Amount", value=f"{total_amount:,} <:won:1304034228966658048>", inline=True)

            # Send confirmation message with buttons
            view = SellConfirmation(ctx, fragments_summary, total_amount)
            initial_embed = await view.start(ctx)
            confirm_message = await ctx.send(embed=initial_embed, view=view)
            
            # Wait for user response
            await view.wait()
            
            # Always remove user from sell_status table regardless of outcome
            await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
            
            if view.value is None:
                await ctx.send("Sale cancelled - you didn't respond in time.")
                return
            
            if not view.value:
                await ctx.send("Sale cancelled.")
                return

            # Check if user is in a trade again before proceeding with the sale
            trade_result = await execute_query_with_retry(query, (user_id, user_id))
            if trade_result:
                await ctx.send(f"You are currently in an active trade, make sure to finish it or wait for its cooldown to end to use this command, if any issue make sure to open a ticket in the support server")
                return

            # Verify the user still has the fragments
            for fragment in fragments_summary:
                inventory_check_query = """
                SELECT quantiter
                FROM inventory_frags
                WHERE inventory_id = %s AND frag_id = %s
                """
                inventory_check = await execute_query_with_retry(inventory_check_query, (inventory_id, fragment['id']))
                
                if not inventory_check or inventory_check[0]['quantiter'] < fragment['quantity']:
                    await ctx.send(f"Error: You no longer have enough {fragment['name']} to complete this sale.")
                    return

            # Process the sale
            for fragment in fragments_summary:
                # Delete the fragment from inventory
                delete_query = """
                DELETE FROM inventory_frags
                WHERE inventory_id = %s AND frag_id = %s
                """
                await execute_query_with_retry(delete_query, (inventory_id, fragment['id']))

            # Update user's currency and total fragment count
            total_fragments_sold = sum(f['quantity'] for f in fragments_summary)
            currency_update_query = """
            UPDATE user
            SET won_currency = won_currency + %s,
                quantiter_fragment = quantiter_fragment - %s
            WHERE user_id = %s
            """
            await execute_query_with_retry(currency_update_query, (total_amount, total_fragments_sold, user_id))

            # Check and update quest progress
            quest_check_query = """
            SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count 
            FROM user_quests uq 
            JOIN quest q ON uq.quest_id = q.quest_id 
            WHERE uq.user_id = %s AND q.quest_name = 'Sacrifice 5 Essence Stones'
            """
            quest_result = await execute_query_with_retry( quest_check_query, (user_id,))

            if quest_result:
                quest = quest_result[0]
                if quest['completion_status'] == 0:
                    new_count = quest['current_count'] + total_fragments_sold
                    if new_count > quest['required_count']:
                        new_count = quest['required_count']
                    
                    update_quest_query = """ 
                    UPDATE user_quests 
                    SET current_count = %s, completion_status = %s 
                    WHERE userquests_id = %s 
                    """
                    completion_status = 1 if new_count >= quest['required_count'] else 0
                    await execute_query_with_retry(update_quest_query, (new_count, completion_status, quest['userquests_id']))
                    
                    if completion_status == 1:
                        check_chest_query = """
                        SELECT * 
                        FROM inventory_chest 
                        WHERE inventory_id = %s AND chest_id = 1
                        """
                        chest_result = await execute_query_with_retry( check_chest_query, (inventory_id,))
                        
                        if chest_result:
                            update_chest_query = """
                            UPDATE inventory_chest 
                            SET quantiter = quantiter + 1 
                            WHERE inventory_id = %s AND chest_id = 1
                            """
                            await execute_query_with_retry( update_chest_query, (inventory_id,))
                        else:
                            insert_chest_query = """
                            INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                            VALUES (%s, 1, 1)
                            """
                            await execute_query_with_retry(insert_chest_query, (inventory_id,))

            # Send success message
            if is_rank_sale:
                success_embed = discord.Embed(
                    title="Rank Sale Successful",
                    description=f"Successfully sold all Rank {rank} essence stones for {total_amount:,} 💰",
                    color=discord.Color.green()
                )
            else:
                success_embed = discord.Embed(
                    title="Sale Successful",
                    description=f"Successfully sold {fragments_summary[0]['quantity']} {fragments_summary[0]['name']} for {total_amount:,} 💰",
                    color=discord.Color.green()
                )
            await ctx.send(embed=success_embed)
        except Exception as e:
            # Make sure to remove user from sell_status on error
            if 'user_id' in locals():
                await execute_query_with_retry( "DELETE FROM sell_status WHERE user_id = %s", (user_id,))
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="invpickaxe", aliases=["ip","ivp"])
    async def inventory_pickaxes(self, ctx):
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)


            view = SingleCategoryView(ctx,self.bot,  inventory_id, 'pickaxes', 'inventory_pickaxe', 'pickaxe',
                                    'pickaxe_name', 'id_pickaxe', 'id_pickaxe', 'pickaxe_icon',
                                    "⛏️ Pickaxes", 
                                    "Tools of resource extraction, each pickaxe a testament to your mining prowess. "
                                    "Forge your path through stone and ore, uncovering hidden treasures.")
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="invweapon",aliases=["iw", "iwp"])
    async def inventory_weapons(self, ctx):
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)

            view = SingleCategoryView(ctx,self.bot,  inventory_id, 'weapons', 'inventory_weapon', 'weapon',
                                    'weapon_name', 'weapon_id', 'weapon_id', 'weapon_icon',
                                    "⚔️ Weapons", 
                                    "In the darkness of dungeons, these weapons are your trusted companions. "
                                    "Each blade, infused with power, stands ready to strike down those who would challenge your ascension.")
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="invbooster",aliases=["ib", "ibt"])
    async def inventory_boosters(self, ctx):
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)


            view = SingleCategoryView(ctx,self.bot,  inventory_id, 'boosters_enhancers', 'inventory_product', 'product',
                                    'product_name', 'product_id', 'product_id', 'product_icon',
                                    "📊 Boosters & Enhancers",
                                    "Tools of advancement that push beyond mortal limits. "
                                    "Through these enhancers, even E-rank hunters can glimpse the power of S-rank might.")
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="invstone",aliases=["is", "ist"])
    async def inventory_stones(self, ctx):
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)


            view = SingleCategoryView(ctx,self.bot,  inventory_id, 'essence_stones', 'inventory_frags', 'fragments',
                                    'name_fragment', 'id_fragment', 'frag_id', 'fragments_icon',
                                    "💎 Essence Stones",
                                    "Crystallized essence of fallen monsters, pulsing with raw power. "
                                    "These stones hold the key to strengthening your abilities and ascending to greater heights.",
                                    essence_stone_data)
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="invchest",aliases=["ic", "ich"])
    async def inventory_chests(self, ctx):
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)


            view = SingleCategoryView(ctx,self.bot,  inventory_id, 'chests', 'inventory_chest', 'chest',
                                    'chest_name', 'chest_id', 'chest_id', 'chest_icon',
                                    "🎁 Chests",
                                    "Rewards claimed from conquered dungeons and fallen foes. "
                                    "Each chest holds the promise of power, waiting to be unleashed.")
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(name="invkey",aliases=["ik", "ikey"])
    async def inventory_keys(self, ctx):
        try:
            inventory_id, essence_stone_data = await self.get_user_inventory_data(ctx)


            view = SingleCategoryView(ctx, self.bot, inventory_id, 'dungeon_key', 'inventory_dungeonkey', 'dungeon_key',
                                    'dungeonkey_name', 'dungeonkey_id', 'dungeonkey_id', 'dungeonkey_icon',
                                    "🗝️ Dungeon Keys",
                                    "Gates to new challenges await. These keys unlock dungeons of varying difficulty, "
                                    "each promising greater rewards for those brave enough to enter.")
            embed = await view.get_category_embed()
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
class SellConfirmation(discord.ui.View):
    def __init__(self, ctx, fragments_summary, total_amount):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.fragments_summary = fragments_summary
        self.total_amount = total_amount
        self.value = None
        self.current_page = 0
        self.items_per_page = 5
        self.max_pages = (len(fragments_summary) - 1) // self.items_per_page + 1

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
        # Update button states
        self.previous_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page >= self.max_pages - 1

        # Create new embed with current page content
        current_fragments = self.get_current_page_content()
        
# Continuing from where we left off in the SellConfirmation class

        embed = discord.Embed(
            title="Sell Confirmation",
            description="Are you sure you want to sell these essence stones?",
            color=discord.Color.gold()
        )

        summary_text = ""
        for frag in current_fragments:
            summary_text += f"{frag['icon']} {frag['name']} x{frag['quantity']} ({frag['amount']:,} <:won:1304034228966658048>)\n"

        if len(self.fragments_summary) > self.items_per_page:
            embed.add_field(
                name=f"Essence Stones to Sell (Page {self.current_page + 1}/{self.max_pages})", 
                value=summary_text, 
                inline=False
            )
        else:
            embed.add_field(name="Essence Stones to Sell", value=summary_text, inline=False)

        embed.add_field(name="Total Amount", value=f"{self.total_amount:,} <:won:1304034228966658048>", inline=False)

        await interaction.response.edit_message(embed=embed, view=self)

    async def start(self, ctx):
        # Initial embed
        embed = discord.Embed(
            title="Sell Confirmation",
            description="Are you sure you want to sell these essence stones?",
            color=discord.Color.gold()
        )

        current_fragments = self.get_current_page_content()
        summary_text = ""
        for frag in current_fragments:
            summary_text += f"{frag['icon']} {frag['name']} x{frag['quantity']} ({frag['amount']:,} <:won:1304034228966658048>)\n"

        if len(self.fragments_summary) > self.items_per_page:
            embed.add_field(
                name=f"Essence Stones to Sell (Page {self.current_page + 1}/{self.max_pages})", 
                value=summary_text, 
                inline=False
            )
            # Enable/disable navigation buttons
            self.previous_page.disabled = True
            self.next_page.disabled = self.max_pages <= 1
        else:
            embed.add_field(name="Essence Stones to Sell", value=summary_text, inline=False)
            # Hide navigation buttons if not needed
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)

        embed.add_field(name="Total Amount", value=f"{self.total_amount:,} <:won:1304034228966658048>", inline=False)
        return embed

class SingleCategoryView(discord.ui.View):
    def __init__(self, ctx,botinstance,  inventory_id, category, inv_table, item_table, 
                 name_column, id_column, invid_column, icon_column, title, description, 
                 essence_stone_data=None):
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
        self.essence_stone_data = essence_stone_data
        self.current_page = 1
        self.items_per_page = 10
        self.add_item(self.PreviousButton())
        self.add_item(self.NextButton())
        self.db_pool = None
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

    async def get_category_embed(self):
        # Use the cog's execute_query_with_retry method through the bot
        for cog in self.bot.cogs.values():
            if isinstance(cog, InventoryCommands):
                inventory_cog = cog
                break
        
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
        total_pages = math.ceil(len(result) / self.items_per_page)
        
        if self.current_page < 1:
            self.current_page = total_pages
        elif self.current_page > total_pages:
            self.current_page = 1

        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = start_idx + self.items_per_page
        current_items = result[start_idx:end_idx]

        embed = discord.Embed(title=self.title, color=0x000000)
        embed.set_author(name=f"{self.ctx.author.name}'s Inventory", icon_url=self.ctx.author.avatar.url if self.ctx.author.avatar else None)
        embed.description = self.description

        if not current_items:
            embed.description += "\n\n*Your inventory for this category is empty. Venture forth to gather more power!*"
        else:
            items_display = []
            for item in current_items:
                icon = item['icon'] if item['icon'] else "❓"
                items_display.append(
                    f"{icon} **{item['name']}** (x{item['quantity']})"
                )
            
            embed.description += "\n\n" + "\n\n".join(items_display)

        if self.category == 'essence_stones' and self.essence_stone_data:
            current = self.essence_stone_data['current']
            cap = self.essence_stone_data['cap']
            footer_text = f"Page {self.current_page}/{total_pages or 1} | Essence Stone Capacity: {current:,}/{cap:,}"
        else:
            footer_text = f"Page {self.current_page}/{total_pages or 1}"
            
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
    def __init__(self, inventory_data, essence_stone_data, original_user):
        super().__init__(timeout=None)
        self.inventory_data = inventory_data
        self.essence_stone_data = essence_stone_data
        self.original_user = original_user
        self.add_item(self.CategorySelect(original_user))

    def clear_items(self):
        self.children.clear()

    def get_main_menu_embed(self, user):
        embed = discord.Embed(title="Shadow Sovereign's Inventory", color=0x000000)
        embed.set_author(name=f"Welcome, {user.name}", icon_url=user.avatar.url if user.avatar else None)
        embed.description = (
            "As you rise through the ranks of Hunters, your inventory grows in power and mystery. "
            "What secrets and strengths have you gathered on your journey?\n\n"
            "Choose a category to inspect your items:"
        )

        categories = [
            ("⚔️", "Weapons", "Blades that cleave through shadows"),
            ("📊", "Boosters & Enhancers", "Enhance your potential, surpass your limits"),
            ("💎", "Essence Stones", "Crystallized power of fallen monsters"),
            ("🎁", "Chests", "Treasures awaiting to be claimed"),
            ("🗝️", "Dungeon keys", "Keys to new challenges"),
            ("⛏️", "Pickaxes", "Tools of resource extraction and mining")  # Add this line
        ]

        for emoji, category, description in categories:
            embed.add_field(name=f"{emoji} {category}", value=description, inline=False)

        embed.set_footer(text="Solo Leveling: Rise as the Shadow Monarch")
        embed.set_image(url="https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/01/solo-leveling-anime-to-premiere-this-week-on-crunchyroll.jpg")
        return embed

    class CategorySelect(discord.ui.Select):
        def __init__(self, original_user):
            self.original_user = original_user
            options = [
                discord.SelectOption(label="Weapons", emoji="⚔️", value="weapons"),
                discord.SelectOption(label="Boosters & Enhancers", emoji="📊", value="boosters_enhancers"),
                discord.SelectOption(label="Essence Stones", emoji="💎", value="essence_stones"),
                discord.SelectOption(label="Chests", emoji="🎁", value="chests"),
                discord.SelectOption(label="Dungeon Keys", emoji="🗝️", value="dungeon_key"),
                discord.SelectOption(label="Pickaxes", emoji="⛏️", value="pickaxes"),
            ]
            super().__init__(placeholder="Select a category", options=options)

        async def callback(self, interaction: discord.Interaction):
            # Check if the interacting user is the original user
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message("You cannot interact with this menu.", ephemeral=True)
                return
            
            view = CategoryView(self.view.inventory_data, self.values[0], 
                                self.view.essence_stone_data, 
                                original_user=interaction.user)
            embed = view.get_category_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)

class CategoryView(discord.ui.View):
    def __init__(self, inventory_data, category, essence_stone_data, original_user):
        super().__init__(timeout=None)
        self.inventory_data = inventory_data
        self.essence_stone_data = essence_stone_data
        self.current_category = category
        self.current_page = 1
        self.items_per_page = 5
        self.original_user = original_user  # Store the original user
        self.add_item(self.PreviousButton())
        self.add_item(self.NextButton())
        self.add_item(self.GoBackButton())


    def get_category_embed(self, user):
        items = self.inventory_data.get(self.current_category, [])
        total_pages = math.ceil(len(items) / self.items_per_page)

        if self.current_page < 1:
            self.current_page = total_pages
        elif self.current_page > total_pages:
            self.current_page = 1
    
        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = start_idx + self.items_per_page
        current_items = items[start_idx:end_idx]

        embed = discord.Embed(title=f"{self.current_category.replace('_', ' ').title()} Inventory", 
                            color=0x000000)
        
        category_descriptions = {
            'weapons': (
                "In the darkness of dungeons, these weapons are your trusted companions. "
                "Each blade, infused with power, stands ready to strike down those who would challenge your ascension."
            ),
            'boosters_enhancers': (
                "Tools of advancement that push beyond mortal limits. "
                "Through these enhancers, even E-rank hunters can glimpse the power of S-rank might."
            ),
            'essence_stones': (
                "Crystallized essence of fallen monsters, pulsing with raw power. "
                "These stones hold the key to strengthening your abilities and ascending to greater heights."
            ),
            'chests': (
                "Rewards claimed from conquered dungeons and fallen foes. "
                "Each chest holds the promise of power, waiting to be unleashed."
            ),
            'dungeon_key': (
                "Gates to new challenges await. These keys unlock dungeons of varying difficulty, "
                "each promising greater rewards for those brave enough to enter."
            ),
            'pickaxes': (
                "Tools of resource extraction, each pickaxe a testament to your mining prowess. "
                "Forge your path through stone and ore, uncovering hidden treasures."
            )

        }
        
        embed.description = category_descriptions.get(self.current_category)
        embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
        
        if not current_items:
            embed.description += "\n\n*Your inventory for this category is empty. Venture forth to gather more power!*"
        else:
            items_display = []
            for item in current_items:
                icon = item['icon'] if item['icon'] else "❓"
                items_display.append(
                    f"{icon} **{item['name']}** (x{item['quantity']})"
                )
            
            embed.description += "\n\n" + "\n\n".join(items_display)

        if self.current_category == 'essence_stones':
            current = self.essence_stone_data['current']
            cap = self.essence_stone_data['cap']
            footer_text = f"Page {self.current_page}/{total_pages or 1} | Essence Stone Capacity: {current:,}/{cap:,}"
        else:
            footer_text = f"Page {self.current_page}/{total_pages or 1}"
            
        embed.set_footer(text=footer_text)
        return embed

    class PreviousButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label="<", custom_id="previous")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            # Check if the interacting user is the original user
            if interaction.user.id != view.original_user.id:
                await interaction.response.send_message("You cannot interact with this menu.", ephemeral=True)
                return
            
            view.current_page -= 1
            embed = view.get_category_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)

    class NextButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label=">", custom_id="next")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            # Check if the interacting user is the original user
            if interaction.user.id != view.original_user.id:
                await interaction.response.send_message("You cannot interact with this menu.", ephemeral=True)
                return
            
            view.current_page += 1
            embed = view.get_category_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)

    class GoBackButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.danger, label="Go Back", custom_id="go_back")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            # Check if the interacting user is the original user
            if interaction.user.id != view.original_user.id:
                await interaction.response.send_message("You cannot interact with this menu.", ephemeral=True)
                return
            
            main_view = InventoryMainView(view.inventory_data, view.essence_stone_data, view.original_user)
            embed = main_view.get_main_menu_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=main_view)

async def setup(bot):
    await bot.add_cog(InventoryCommands(bot))
