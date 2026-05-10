import random
import math
from discord.ext import commands
import discord
from utils.database import create_db_connection
import re
#keep this in case you wanna have a separate code for chest, js make sure to add the progress tracking
class InventoryCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
        except Exception as e:
            print(f"Error executing query: {e}")
            return None
        finally:
            cursor.close()

    def parse_input(self, input_string):
        pattern = r'\((.*?)\)'
        matches = re.findall(pattern, input_string)
        return matches

    def calculate_money_reward(self, money_cap, quantity):
        try:
            if quantity <= 10:
                return random.randint(100, money_cap)
            else:
                adjusted_cap = math.floor(money_cap * (quantity / 10))
                return random.randint(100, adjusted_cap)
        except Exception as e:
            print(f"Error calculating money reward: {e}")
            return 0

    def update_user_currency(self, connection, user_id, money_reward):
        try:
            update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            self.execute_query(connection, update_query, (money_reward, user_id))
        except Exception as e:
            print(f"Error updating user currency: {e}")

    def get_available_items(self, connection, chest_id):
        """Get both products and dungeon keys available for a specific chest."""
        try:
            # Get products
            products_query = """
                SELECT 'product' as item_type, p.product_id as item_id, p.product_name as item_name 
                FROM chest_product cp 
                JOIN product p ON cp.product_id = p.product_id 
                WHERE cp.chest_id = %s
            """
            products = self.execute_query(connection, products_query, (chest_id,))

            # Get dungeon key if exists for this chest
            dungeonkey_query = """
                SELECT 'dungeonkey' as item_type, dk.dungeonkey_id as item_id, 
                       dk.dungeonkey_name as item_name
                FROM chest_dungeonkey cd
                JOIN dungeon_key dk ON cd.dungeonkey_id = dk.dungeonkey_id
                WHERE cd.chest_id = %s
            """
            dungeonkeys = self.execute_query(connection, dungeonkey_query, (chest_id,))
            # Combine both results
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
        elif item_id in [14, 15, 16, 17]:  # resets
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

    async def update_inventory_item(self, connection, inventory_id, item_id, quantity, is_dungeon_key=False):
        try:
            if is_dungeon_key:
                table_name = "inventory_dungeonkey"
                id_column = "dungeonkey_id"
            else:
                table_name = "inventory_product"
                id_column = "product_id"

            check_query = f"SELECT quantiter FROM {table_name} WHERE inventory_id = %s AND {id_column} = %s"
            check_result = self.execute_query(connection, check_query, (inventory_id, item_id))

            if check_result:
                new_quantity = check_result[0]['quantiter'] + quantity
                update_query = f"UPDATE {table_name} SET quantiter = %s WHERE inventory_id = %s AND {id_column} = %s"
                self.execute_query(connection, update_query, (new_quantity, inventory_id, item_id))
            else:
                insert_query = f"INSERT INTO {table_name} (inventory_id, {id_column}, quantiter) VALUES (%s, %s, %s)"
                self.execute_query(connection, insert_query, (inventory_id, item_id, quantity))

        except Exception as e:
            print(f"Error updating inventory item: {e}")

    @commands.command(name="loot")
    async def loot(self, ctx, *, args=None):
        try:
            # Handle default case (no args)
            if args is None:
                await ctx.send("Please specify a chest type. Usage: sl loot (chest name) [quantity]")
                return

            # Parse input
            parsed_args = self.parse_input(args)
            if not parsed_args:
                await ctx.send("Invalid format. Use: sl loot (chest name) [quantity]")
                return

            chest_name = parsed_args[0]
            quantity = 1  # Default quantity

            if len(parsed_args) > 1:
                if parsed_args[1].lower() == 'all':
                    quantity = -1  # Special flag for 'all'
                else:
                    try:
                        quantity = int(parsed_args[1])
                    except ValueError:
                        await ctx.send("Invalid quantity. Please provide a number or 'all'.")
                        return

            # Database connection
            connection = create_db_connection()
            if not connection:
                await ctx.send("Unable to connect to the database.")
                return

            # Get user data
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("User not registered.")
                return

            user_id = user_result[0]['user_id']

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = self.execute_query(connection, inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("Inventory not found.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Get chest information
            chest_query = """
                SELECT c.chest_id, c.chest_name, c.chest_rank, c.money_cap, ic.quantiter 
                FROM chest c 
                LEFT JOIN inventory_chest ic ON c.chest_id = ic.chest_id AND ic.inventory_id = %s 
                WHERE c.chest_name = %s
            """
            chest_result = self.execute_query(connection, chest_query, (inventory_id, chest_name))

            if not chest_result:
                await ctx.send("Chest not found.")
                return

            chest_data = chest_result[0]
            chest_id = chest_data['chest_id']
            available_quantity = chest_data['quantiter'] or 0

            # Handle 'all' quantity
            if quantity == -1:
                quantity = available_quantity

            # Verify user has enough chests
            if quantity > available_quantity:
                await ctx.send("You don't have enough chests.")
                return

            # Update chest quantity
            new_chest_quantity = available_quantity - quantity
            if new_chest_quantity == 0:
                delete_query = "DELETE FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
                self.execute_query(connection, delete_query, (inventory_id, chest_id))
            else:
                update_query = "UPDATE inventory_chest SET quantiter = %s WHERE inventory_id = %s AND chest_id = %s"
                self.execute_query(connection, update_query, (new_chest_quantity, inventory_id, chest_id))

            # Get all available items (products and dungeon keys)
            available_items = self.get_available_items(connection, chest_id)

            if not available_items:
                await ctx.send("No items available in this chest.")
                return

            # Determine item limit based on chest type
            item_limits = {1: 4, 2: 5, 3: 6}
            item_limit = item_limits.get(chest_id, 4)

            # Calculate quantity multiplier
            quantity_multiplier = (quantity - 1) // 20

            # Select random number of items
            num_items = random.randint(1, item_limit)
            selected_items = random.sample(available_items, num_items)

            # Process rewards
            rewards = []
            for item in selected_items:
                min_qty, max_qty = self.get_quantity_range(
                    item['item_type'],
                    item['item_id'],
                    chest_id,
                    quantity_multiplier
                )
                reward_quantity = random.randint(min_qty, max_qty)

                # Update inventory based on item type
                await self.update_inventory_item(
                    connection,
                    inventory_id,
                    item['item_id'],
                    reward_quantity,
                    is_dungeon_key=(item['item_type'] == 'dungeonkey')
                )

                rewards.append((item['item_name'], reward_quantity))

            # Calculate and update currency reward
            money_reward = self.calculate_money_reward(chest_data['money_cap'], quantity)
            self.update_user_currency(connection, user_id, money_reward)

            # Create response message
            response = f"You looted {quantity} {chest_name} chest(s)! You received:\n"
            for name, qty in rewards:
                response += f"• {qty}x {name}\n"
            response += f"You also received {money_reward} currency!"

            await ctx.send(response)

        except Exception as e:
            print(f"Error in loot command: {e}")
            await ctx.send("An error occurred while processing your request.")
        finally:
            if connection:
                connection.close()

async def setup(bot):
    await bot.add_cog(InventoryCommands(bot))