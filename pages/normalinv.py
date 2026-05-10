import random
import math
from discord.ext import commands
import discord
from utils.database import create_db_connection
import re

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

    @commands.command(name="loot")
    async def loot(self, ctx, *, args):
        try:
            # Parse input
            parsed_args = self.parse_input(args)
            if len(parsed_args) != 2:
                await ctx.send("Invalid format. Use: sl loot (chest name) (quantity)")
                return

            chest_name, quantity = parsed_args
            try:
                quantity = int(quantity)
            except ValueError:
                await ctx.send("Invalid quantity. Please provide a number.")
                return

            # Database connection
            connection = create_db_connection()
            if not connection:
                await ctx.send("Unable to connect to the database.")
                return

            # Get user_id
            user_id_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_id_query, (str(ctx.author.id),))
            
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

            # Verify chest exists and get chest_id
            chest_query = "SELECT chest_id, chest_rank, money_cap FROM chest WHERE chest_name = %s"
            chest_result = self.execute_query(connection, chest_query, (chest_name,))
            
            if not chest_result:
                await ctx.send("Chest not found.")
                return

            chest_id = chest_result[0]['chest_id']
            chest_rank = chest_result[0]['chest_rank']
            money_cap = chest_result[0]['money_cap']

            # Check if user has enough chests
            user_chest_query = "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            user_chest_result = self.execute_query(connection, user_chest_query, (inventory_id, chest_id))
            
            if not user_chest_result or user_chest_result[0]['quantiter'] < quantity:
                await ctx.send("You don't have enough chests.")
                return

            # Update user's chest quantity
            new_quantity = user_chest_result[0]['quantiter'] - quantity
            if new_quantity == 0:
                delete_chest_query = "DELETE FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
                self.execute_query(connection, delete_chest_query, (inventory_id, chest_id))
            else:
                update_chest_query = "UPDATE inventory_chest SET quantiter = %s WHERE inventory_id = %s AND chest_id = %s"
                self.execute_query(connection, update_chest_query, (new_quantity, inventory_id, chest_id))

            # Get chest contents
            chest_contents = self.get_chest_contents(connection, chest_id)

            # Process loot
            loot_results = self.process_loot(chest_contents, chest_rank, quantity)

            # Update user's inventory with loot
            self.update_user_inventory(connection, inventory_id, loot_results)

            # Calculate and award money
            money_reward = self.calculate_money_reward(money_cap, quantity)
            self.update_user_currency(connection, user_id, money_reward)

            # Generate loot message
            loot_message = self.generate_loot_message(loot_results, money_reward)

            await ctx.send(f"You looted {quantity} {chest_name} chest(s)!\n\n{loot_message}")
            try:
                await self.update_quest_progress(ctx, connection, user_id, "Open 5 chests", quantity)
            except Exception as e:
                print(f"Error in quest tracking: {e}")
        except Exception as e:
            await ctx.send(f"An error occurred while processing your request: {str(e)}")
    async def update_quest_progress(self, ctx, connection, user_id, quest_name, quantity):
        user_quests_query = """
        SELECT uq.*, q.quest_name, q.required_count 
        FROM user_quests uq
        JOIN quest q ON uq.quest_id = q.quest_id
        WHERE uq.user_id = %s AND q.quest_name = %s
        """
        user_quests_result = self.execute_query(connection, user_quests_query, (user_id, quest_name))

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
                self.execute_query(connection, update_count_query, (new_count, quest['userquests_id']))

                # Check if quest is completed
                if new_count >= required_count:
                    complete_quest_query = """
                    UPDATE user_quests 
                    SET completion_status = 1, current_count = %s
                    WHERE userquests_id = %s
                    """
                    self.execute_query(connection, complete_quest_query, (required_count, quest['userquests_id']))

                    await ctx.send(f"Quest '{quest_name}' completed! Progress: {required_count}/{required_count}")
                else:
                    await ctx.send(f"Quest progress for '{quest_name}': {new_count}/{required_count}")
    def get_chest_contents(self, connection, chest_id):
        try:
            contents = {
                'products': {'xp_boost': [], 'stat_boost': [], 'special': []},
                'weapons': [],
                'orbs': [],
                'dungeon_keys': []
            }

            # Get products
            product_query = """
            SELECT p.*, cp.chest_id FROM product p
            JOIN chest_product cp ON p.product_id = cp.product_id
            WHERE cp.chest_id = %s
            """
            products = self.execute_query(connection, product_query, (chest_id,))

            for product in products:
                if product['xp_boost'] > 0 and product['attack_boost'] == 0 and product['health_boost'] == 0 and product['speed_boost'] == 0:
                    contents['products']['xp_boost'].append(product)
                elif product['xp_boost'] == 0 and (product['attack_boost'] > 0 or product['health_boost'] > 0 or product['speed_boost'] > 0):
                    contents['products']['stat_boost'].append(product)
                else:
                    contents['products']['special'].append(product)

            # Get weapons
            weapon_query = """
            SELECT w.* FROM weapon w
            JOIN chest_weapon cw ON w.weapon_id = cw.weapon_id
            WHERE cw.chest_id = %s
            """
            contents['weapons'] = self.execute_query(connection, weapon_query, (chest_id,))

            # Get orbs
            orb_query = """
            SELECT o.* FROM leveling_orbs o
            JOIN chest_orbs co ON o.orb_id = co.orb_id
            WHERE co.chest_id = %s
            """
            contents['orbs'] = self.execute_query(connection, orb_query, (chest_id,))

            # Get dungeon keys
            key_query = """
            SELECT dk.* FROM dungeon_key dk
            JOIN chest_dungeonkey cdk ON dk.dungeonkey_id = cdk.dungeonkey_id
            WHERE cdk.chest_id = %s
            """
            contents['dungeon_keys'] = self.execute_query(connection, key_query, (chest_id,))

            return contents
        except Exception as e:
            print(f"Error getting chest contents: {e}")
            return None

    def process_loot(self, chest_contents, chest_rank, quantity):
        try:
            loot_results = []

            # Define loot ranges and item limits based on chest rank
            loot_ranges = {
                1: {'xp_boost': (5, 15), 'others': (1, 2), 'item_limit': 4},  # Shadow chest
                2: {'xp_boost': (10, 20), 'others': (1, 3), 'item_limit': 5},  # Hunter chest
                3: {'xp_boost': (10, 30), 'others': (1, 5), 'item_limit': 5}   # Monarch chest
            }

            # Adjust ranges based on quantity
            quantity_multiplier = (quantity - 1) // 20
            loot_ranges[chest_rank]['xp_boost'] = (
                loot_ranges[chest_rank]['xp_boost'][0],
                loot_ranges[chest_rank]['xp_boost'][1] + (quantity_multiplier * 5)
            )
            loot_ranges[chest_rank]['others'] = (
                loot_ranges[chest_rank]['others'][0],
                loot_ranges[chest_rank]['others'][1] + quantity_multiplier
            )

            # Choose number of item types to loot from
            loot_types = list(chest_contents.keys())
            num_types = random.randint(1, len(loot_types))
            chosen_types = random.sample(loot_types, num_types)

            potential_loot = []

            for chosen_type in chosen_types:
                if chosen_type == 'products':
                    product_types = list(chest_contents['products'].keys())
                    num_product_types = random.randint(1, len(product_types))
                    chosen_product_types = random.sample(product_types, num_product_types)
                    
                    for chosen_product_type in chosen_product_types:
                        items = chest_contents['products'][chosen_product_type]
                        self.add_potential_loot_items(items, chosen_type, potential_loot)
                else:
                    items = chest_contents[chosen_type]
                    self.add_potential_loot_items(items, chosen_type, potential_loot)

            # Limit the number of items based on chest rarity and adjusted probabilities
            item_limit = loot_ranges[chest_rank]['item_limit']
            if len(potential_loot) > item_limit:
                if chest_rank == 1:  # Shadow chest
                    weights = [0.4, 0.4, 0.1, 0.1]  # Higher probability for 1-2 items
                else:  # Hunter and Monarch chests
                    weights = [0.2, 0.3, 0.3, 0.1, 0.1]  # Higher probability for 1-3 items
                
                num_items = random.choices(range(1, item_limit + 1), weights=weights[:item_limit], k=1)[0]
                potential_loot = random.sample(potential_loot, num_items)

            # Assign quantities to the selected items
            for loot_item in potential_loot:
                item_type = loot_item['type']
                if item_type == 'products' and loot_item['item'].get('xp_boost', 0) > 0:
                    loot_range = loot_ranges[chest_rank]['xp_boost']
                else:
                    loot_range = loot_ranges[chest_rank]['others']
                
                quantity = random.randint(loot_range[0], loot_range[1])
                loot_item['quantity'] = quantity
                loot_results.append(loot_item)

            return loot_results
        except Exception as e:
            print(f"Error processing loot: {e}")
            return []

    def add_potential_loot_items(self, items, chosen_type, potential_loot):
        if items:
            num_items = random.randint(1, len(items))
            chosen_items = random.sample(items, num_items)

            for item in chosen_items:
                potential_loot.append({
                    'type': chosen_type,
                    'item': item
                })

    def update_user_inventory(self, connection, inventory_id, loot_results):
        try:
            for loot in loot_results:
                item_type = loot['type']
                item = loot['item']
                quantity = loot['quantity']

                if item_type == 'products':
                    self.update_inventory_item(connection, inventory_id, 'inventory_product', 'product_id', item['product_id'], quantity)
                elif item_type == 'weapons':
                    self.update_inventory_item(connection, inventory_id, 'inventory_weapon', 'weapon_id', item['weapon_id'], quantity)
                elif item_type == 'orbs':
                    self.update_inventory_item(connection, inventory_id, 'inventory_orbs', 'orb_id', item['orb_id'], quantity)
                elif item_type == 'dungeon_keys':
                    self.update_inventory_item(connection, inventory_id, 'inventory_dungeonkey', 'dungeonkey_id', item['dungeonkey_id'], quantity)
        except Exception as e:
            print(f"Error updating user inventory: {e}")

    def update_inventory_item(self, connection, inventory_id, table_name, id_column, item_id, quantity):
        try:
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

    def generate_loot_message(self, loot_results, money_reward):
        try:
            message = "You received:\n"
            for loot in loot_results:
                item_type = loot['type']
                item = loot['item']
                quantity = loot['quantity']

                if item_type == 'products':
                    message += f"- {quantity}x {item['product_name']} (Product)\n"
                elif item_type == 'weapons':
                    message += f"- {quantity}x {item['weapon_name']} (Weapon)\n"
                elif item_type == 'orbs':
                    message += f"- {quantity}x {item['orb_name']} (Orb)\n"
                elif item_type == 'dungeon_keys':
                    message += f"- {quantity}x {item['dungeonkey_name']} (Dungeon Key)\n"

            message += f"\nYou also received {money_reward} currency!"
            return message
        except Exception as e:
            print(f"Error generating loot message")

    @commands.command(name='use')
    async def use_product(self, ctx, *, args):
        parsed_args = self.parse_input(args)
        if len(parsed_args) != 2:
            await ctx.send("Invalid input. Please use the format: sl use (product name) (card name)")
            return

        product_name, card_name = parsed_args

        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return
            user_id = user_result[0]['user_id']

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = self.execute_query(connection, inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check product existence and get info
            product_query = "SELECT * FROM product WHERE product_name = %s"
            product_result = self.execute_query(connection, product_query, (product_name,))
            if not product_result:
                await ctx.send(f"Product '{product_name}' not found.")
                return
            product = product_result[0]

            # Check if user has the product
            inv_product_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            inv_product_result = self.execute_query(connection, inv_product_query, (inventory_id, product['product_id']))
            if not inv_product_result:
                await ctx.send(f"You don't have the product '{product_name}' in your inventory.")
                return

            # Check card existence and get info
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
            user_card_result = self.execute_query(connection, user_card_query, (user_id, card_name))
            if not user_card_result:
                await ctx.send(f"You don't have the card '{card_name}' in your collection.")
                return
            user_card = user_card_result[0]
            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = self.execute_query(connection, card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(f"Card '{card_name}' not found.")
                return
            card = card_result[0]

            # Process product use (only for charm products now)
            if any([product['attack_boost'], product['health_boost'], product['speed_boost']]):
                # Charm product
                charm_query = "SELECT * FROM usercards_charm WHERE id_ligneusercards = %s AND product_id = %s"
                charm_result = self.execute_query(connection, charm_query, (user_card['id_ligneusercards'], product['product_id']))
                if charm_result:
                    await ctx.send(f"The '{product_name}' is already active on this card. Wait until its effects are over.")
                    return

                # Apply charm
                insert_charm_query = "INSERT INTO usercards_charm (id_ligneusercards, product_id, counter) VALUES (%s, %s, 3)"
                self.execute_query(connection, insert_charm_query, (user_card['id_ligneusercards'], product['product_id']))

                # Update card stats
                update_stats_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat + %s, health_stat = health_stat + %s, speed_stat = speed_stat + %s 
                WHERE id_ligneusercards = %s
                """
                self.execute_query(connection, update_stats_query, (
                    product['attack_boost'], product['health_boost'], product['speed_boost'], user_card['id_ligneusercards']
                ))

                await ctx.send(f"Applied '{product_name}' to '{card_name}'. Stats boosted for 3 turns.")

                # Update inventory
                if inv_product_result[0]['quantiter'] == 1:
                    delete_product_query = "DELETE FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
                    self.execute_query(connection, delete_product_query, (inventory_id, product['product_id']))
                else:
                    update_quantity_query = """
                    UPDATE inventory_product 
                    SET quantiter = quantiter - 1 
                    WHERE inventory_id = %s AND product_id = %s
                    """
                    self.execute_query(connection, update_quantity_query, (inventory_id, product['product_id']))
            else:
                await ctx.send(f"The product '{product_name}' has no effect when used directly. Try 'sl xpboost' for XP boosting products.")

        finally:
            connection.close()

    @commands.command(name='xpboost')
    async def xp_boost(self, ctx, *, args):
        parsed_args = self.parse_input(args)
        if len(parsed_args) < 2 or len(parsed_args) > 3:
            await ctx.send("Invalid input. Please use the format: sl xpboost (product name) (card name) (optional: quantity)")
            return

        product_name, card_name = parsed_args[:2]
        quantity = 1
        if len(parsed_args) == 3:
            try:
                quantity = int(parsed_args[2])
                if quantity <= 0:
                    raise ValueError
            except ValueError:
                await ctx.send("Invalid quantity. Please provide a positive integer.")
                return

        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))
            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return
            user_id = user_result[0]['user_id']

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = self.execute_query(connection, inventory_query, (user_id,))
            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return
            inventory_id = inventory_result[0]['inventory_id']

            # Check product existence and get info
            product_query = "SELECT * FROM product WHERE product_name = %s"
            product_result = self.execute_query(connection, product_query, (product_name,))
            if not product_result:
                await ctx.send(f"Product '{product_name}' not found.")
                return
            product = product_result[0]

            if product['xp_boost'] == 0:
                await ctx.send(f"The product '{product_name}' is not an XP boost product.")
                return

            # Check if user has the product
            inv_product_query = "SELECT * FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            inv_product_result = self.execute_query(connection, inv_product_query, (inventory_id, product['product_id']))
            if not inv_product_result:
                await ctx.send(f"You don't have the product '{product_name}' in your inventory.")
                return
            
            if inv_product_result[0]['quantiter'] < quantity:
                await ctx.send(f"You don't have enough of '{product_name}'. You have {inv_product_result[0]['quantiter']} but tried to use {quantity}.")
                return



            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
            user_card_result = self.execute_query(connection, user_card_query, (user_id, card_name))
            if not user_card_result:
                await ctx.send(f"You don't have the card '{card_name}' in your collection.")
                return
            user_card = user_card_result[0]
            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = self.execute_query(connection, card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(f"Card '{card_name}' not found.")
                return
            card = card_result[0]
            if user_card['current_level'] == card['max_level']:
                await ctx.send(f"'{card_name}' has already reached its maximum level.")
                return

            # Apply XP boost
            total_xp_boost = product['xp_boost'] * quantity
            new_xp = user_card['current_xp'] + total_xp_boost
            new_level = user_card['current_level']
            
            levels_gained = 0
            stat_increases = {
                'attack_stat': 0,
                'health_stat': 0,
                'defence_stat': 0,
                'speed_stat': 0
            }

            while new_xp >= card['xp_cap'] and new_level < card['max_level']:
                new_level += 1
                new_xp -= card['xp_cap']
                levels_gained += 1

                # Add random stat increases for each level gained
                for stat in stat_increases:
                    stat_increases[stat] += random.randint(10, 15)

            if new_level == card['max_level']:
                new_xp = 0

            # Prepare the update query with stat increases
            update_xp_query = """
            UPDATE user_cards 
            SET current_xp = %s, 
                current_level = %s,
                attack_stat = attack_stat + %s,
                health_stat = health_stat + %s,
                defence_stat = defence_stat + %s,
                speed_stat = speed_stat + %s,
                power_stat = attack_stat+health_stat+defence_stat+speed_stat,
                current_statpoints = current_statpoints + 5
            WHERE id_ligneusercards = %s
            """
            self.execute_query(connection, update_xp_query, (
                new_xp, 
                new_level, 
                stat_increases['attack_stat'],
                stat_increases['health_stat'],
                stat_increases['defence_stat'],
                stat_increases['speed_stat'],
                user_card['id_ligneusercards']
            ))

            # Update inventory
            if inv_product_result[0]['quantiter'] == quantity:
                delete_product_query = "DELETE FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
                self.execute_query(connection, delete_product_query, (inventory_id, product['product_id']))
            else:
                update_quantity_query = """
                UPDATE inventory_product 
                SET quantiter = quantiter - %s 
                WHERE inventory_id = %s AND product_id = %s
                """
                self.execute_query(connection, update_quantity_query, (quantity, inventory_id, product['product_id']))

            # Prepare the response message
            response = f"Applied '{product_name}' x{quantity} to '{card_name}'. XP boosted to {new_xp}, now at level {new_level}."
            if levels_gained > 0:
                response += f"\nLeveled up {levels_gained} time(s)! Stat increases:"
                for stat, increase in stat_increases.items():
                    if increase > 0:
                        response += f"\n- {stat.replace('_', ' ').title()}: +{increase}"

            await ctx.send(response)
            try:
               await self.update_quest_progress(ctx, connection, user_id, "Use 10 XpBottles", quantity)
            except Exception as e:
               print(f"Error in quest tracking: {e}")

        finally:
            connection.close()
    @commands.command(name="equip")
    async def equip(self, ctx, *, args):
        weapon_name, card_name = self.parse_input(args)
        if not weapon_name or not card_name:
            await ctx.send("Please provide both weapon and card names in parentheses. Example: sl equip (Sword of Power) (Mighty Warrior)")
            return

        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database.")
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return

            user_id = user_result[0]['user_id']

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = self.execute_query(connection, inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check weapon
            weapon_query = "SELECT * FROM weapon WHERE weapon_name = %s"
            weapon_result = self.execute_query(connection, weapon_query, (weapon_name,))

            if not weapon_result:
                await ctx.send(f"Weapon '{weapon_name}' not found.")
                return

            weapon = weapon_result[0]

            # Check if user has the weapon
            inv_weapon_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            inv_weapon_result = self.execute_query(connection, inv_weapon_query, (inventory_id, weapon['weapon_id']))

            if not inv_weapon_result:
                await ctx.send(f"You don't have the weapon '{weapon_name}' in your inventory.")
                return

            # Check card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
            user_card_result = self.execute_query(connection, user_card_query, (user_id, card_name))
            if not user_card_result:
                await ctx.send(f"You don't have the card '{card_name}' in your collection.")
                return
            
            user_card = user_card_result[0]
            
            # Check card existence and get info
            card_query = "SELECT * FROM card WHERE card_id = %s"
            card_result = self.execute_query(connection, card_query, (user_card['card_id'],))
            if not card_result:
                await ctx.send(f"Card '{card_name}' not found.")
                return
            
            card = card_result[0]

            # Check weapon class compatibility
            weapon_class_query = "SELECT card_class_id FROM weapon_class WHERE weapon_id = %s"
            weapon_class_result = self.execute_query(connection, weapon_class_query, (weapon['weapon_id'],))
            
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
                self.execute_query(connection, delete_inv_weapon_query, (inventory_id, weapon['weapon_id']))
            else:
                update_inv_weapon_query = "UPDATE inventory_weapon SET quantiter = quantiter - 1 WHERE inventory_id = %s AND weapon_id = %s"
                self.execute_query(connection, update_inv_weapon_query, (inventory_id, weapon['weapon_id']))

            # Apply stat changes based on boosts and percentages
            has_boosts = any(weapon.get(f'{stat}_boost', 0) != 0 for stat in ['attack', 'health', 'speed', 'defense'])
            has_percentages = any(weapon.get(f'{stat}_percentage', 0) != 0 for stat in ['attack', 'health', 'speed', 'defense'])

            # Update equipment slot based on weapon rank
            equipment_column = {
                1: 'weapon_id',
                2: 'shield_id',
                3: 'armor_id'
            }.get(weapon['weapon_rank'])

            # First apply flat boosts if they exist
            if has_boosts:
                boost_update_query = f"""
                UPDATE user_cards 
                SET {equipment_column} = %s,
                    attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    speed_stat = speed_stat + %s,
                    defence_stat = defence_stat + %s
                WHERE id_ligneusercards = %s
                """
                self.execute_query(connection, boost_update_query, (
                    weapon['weapon_id'],
                    weapon.get('attack_boost', 0),
                    weapon.get('health_boost', 0),
                    weapon.get('speed_boost', 0),
                    weapon.get('defense_boost', 0),
                    user_card['id_ligneusercards']
                ))

            # Then apply percentage increases if they exist
            if has_percentages:
                percentage_update_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat * (1 + %s/100),
                    health_stat = health_stat * (1 + %s/100),
                    speed_stat = speed_stat * (1 + %s/100),
                    defence_stat = defence_stat * (1 + %s/100)
                WHERE id_ligneusercards = %s
                """
                self.execute_query(connection, percentage_update_query, (
                    weapon.get('attack_percentage', 0),
                    weapon.get('health_percentage', 0),
                    weapon.get('speed_percentage', 0),
                    weapon.get('defense_percentage', 0),
                    user_card['id_ligneusercards']
                ))

            await ctx.send(f"Successfully equipped {weapon_name} to {card_name}!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
        finally:
            connection.close()

    @commands.command(name="unequip")
    async def unequip(self, ctx, *, args):
        weapon_name, card_name = self.parse_input(args)
        if not weapon_name or not card_name:
            await ctx.send("Please provide both weapon and card names in parentheses. Example: sl unequip (Sword of Power) (Mighty Warrior)")
            return

        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database.")
            return

        try:
            # Get user_id
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You need to be registered to use this command.")
                return

            user_id = user_result[0]['user_id']

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = self.execute_query(connection, inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("You don't have an inventory.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Check weapon
            weapon_query = "SELECT * FROM weapon WHERE weapon_name = %s"
            weapon_result = self.execute_query(connection, weapon_query, (weapon_name,))

            if not weapon_result:
                await ctx.send(f"Weapon '{weapon_name}' not found.")
                return

            weapon = weapon_result[0]

            # Check card
            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
            user_card_result = self.execute_query(connection, user_card_query, (user_id, card_name))
            if not user_card_result:
                await ctx.send(f"You don't have the card '{card_name}' in your collection.")
                return

            user_card = user_card_result[0]

            # Check if the weapon is actually equipped
            equipment_column = {
                1: 'weapon_id',
                2: 'shield_id',
                3: 'armor_id'
            }.get(weapon['weapon_rank'])

            if user_card[equipment_column] != weapon['weapon_id']:
                await ctx.send(f"This {['weapon', 'shield', 'armor'][weapon['weapon_rank']-1]} is not equipped on this card.")
                return

            # Update inventory
            inv_weapon_query = "SELECT * FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            inv_weapon_result = self.execute_query(connection, inv_weapon_query, (inventory_id, weapon['weapon_id']))

            if not inv_weapon_result:
                insert_inv_weapon_query = "INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter) VALUES (%s, %s, 1)"
                self.execute_query(connection, insert_inv_weapon_query, (inventory_id, weapon['weapon_id']))
            else:
                update_inv_weapon_query = "UPDATE inventory_weapon SET quantiter = quantiter + 1 WHERE inventory_id = %s AND weapon_id = %s"
                self.execute_query(connection, update_inv_weapon_query, (inventory_id, weapon['weapon_id']))

            # Remove stat changes based on boosts and percentages
            has_boosts = any(weapon.get(f'{stat}_boost', 0) != 0 for stat in ['attack', 'health', 'speed', 'defense'])
            has_percentages = any(weapon.get(f'{stat}_percentage', 0) != 0 for stat in ['attack', 'health', 'speed', 'defense'])

            # First remove percentage increases if they exist (in reverse order from equipping)
            if has_percentages:
                percentage_update_query = """
                UPDATE user_cards 
                SET attack_stat = attack_stat * (1 - %s/100),
                    health_stat = health_stat * (1 - %s/100),
                    speed_stat = speed_stat * (1 - %s/100),
                    defence_stat = defence_stat * (1 - %s/100)
                WHERE id_ligneusercards = %s
                """
                self.execute_query(connection, percentage_update_query, (
                    weapon.get('attack_percentage', 0),
                    weapon.get('health_percentage', 0),
                    weapon.get('speed_percentage', 0),
                    weapon.get('defense_percentage', 0),
                    user_card['id_ligneusercards']
                ))

            # Then remove flat boosts if they exist
            if has_boosts:
                boost_update_query = f"""
                UPDATE user_cards 
                SET {equipment_column} = NULL,
                    attack_stat = attack_stat - %s,
                    health_stat = health_stat - %s,
                    speed_stat = speed_stat - %s,
                    defence_stat = defence_stat - %s
                WHERE id_ligneusercards = %s
                """
                self.execute_query(connection, boost_update_query, (
                    weapon.get('attack_boost', 0),
                    weapon.get('health_boost', 0),
                    weapon.get('speed_boost', 0),
                    weapon.get('defense_boost', 0),
                    user_card['id_ligneusercards']
                ))

            await ctx.send(f"Successfully unequipped {weapon_name} from {card_name}!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
        finally:
            connection.close()
    @commands.command(name="rstat")
    async def rstat(self, ctx, *, card_name):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Failed to connect to the database.")
            return

        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to register first.")
            return

        user_id = user_result[0]['user_id']

        # Check if user has the card
        card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
        card_result = self.execute_query(connection, card_query, (user_id, card_name))

        if not card_result:
            await ctx.send(f"You don't have a card named {card_name}.")
            return

        card = card_result[0]

        # Check if user has the reset stat product
        inventory_query = "SELECT ip.* FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id WHERE i.user_id = %s AND ip.product_id = 3"
        inventory_result = self.execute_query(connection, inventory_query, (user_id,))

        if not inventory_result:
            await ctx.send("You don't have a reset stat item in your inventory.")
            return

        # Update inventory
        if inventory_result[0]['quantiter'] == 1:
            delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
        else:
            update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

        # Reset stats
        reset_query = """
        UPDATE user_cards
        SET 
            attack_stat = attack_stat - used_attackpoints,
            health_stat = health_stat - used_healthpoints,
            defence_stat = defence_stat - used_defencepoints,
            speed_stat = speed_stat - used_speedpoints,
            current_statpoints = current_statpoints + used_statpoints,
            used_attackpoints = 0,
            used_healthpoints = 0,
            used_defencepoints = 0,
            used_speedpoints = 0,
            used_statpoints = 0
        WHERE user_id = %s AND name = %s
        """
        self.execute_query(connection, reset_query, (user_id, card_name))

        await ctx.send(f"Stats for {card_name} have been reset.")
    @commands.command(name="reset")
    async def reset(self, ctx, reset_type: str):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Failed to connect to the database.")
            return

        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to register first.")
            return

        user_id = user_result[0]['user_id']

        # Get inventory_id
        inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        inventory_result = self.execute_query(connection, inventory_query, (user_id,))

        if not inventory_result:
            await ctx.send("You don't have an inventory.")
            return

        inventory_id = inventory_result[0]['inventory_id']

        # Define reset types and their corresponding product IDs and timer columns
        reset_types = {
            'rolls': {'product_id': 2, 'timer_column': 'roll_timer', 'count_column': 'roll_count'},
            'dungeon': {'product_id': 14, 'timer_column': 'dungeon_timer', 'count_column': None},
            'mission': {'product_id': 15, 'timer_column': 'mission_timer', 'count_column': None},
            'fight': {'product_id': 16, 'timer_column': 'fight_timer', 'count_column': None}
        }

        # Check if reset type is valid
        reset_type = reset_type.lower()
        if reset_type not in reset_types:
            await ctx.send("Invalid reset type. Use rolls, dungeon, mission, or fight.")
            return

        # Get reset item from inventory
        inventory_query = """
        SELECT * FROM inventory_product 
        WHERE inventory_id = %s AND product_id = %s
        """
        inventory_result = self.execute_query(
            connection, 
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
            self.execute_query(
                connection, 
                delete_query, 
                (inventory_result[0]['id_ligneinventoryproduct'],)
            )
        else:
            update_query = """
            UPDATE inventory_product 
            SET quantiter = quantiter - 1 
            WHERE id_ligneinventoryproduct = %s
            """
            self.execute_query(
                connection, 
                update_query, 
                (inventory_result[0]['id_ligneinventoryproduct'],)
            )

        # Build the update query for the user table
        update_parts = [f"{reset_types[reset_type]['timer_column']} = NULL"]
        if reset_types[reset_type]['count_column']:
            update_parts.append(f"{reset_types[reset_type]['count_column']} = 0")
        
        update_query = f"""
        UPDATE user 
        SET {', '.join(update_parts)}
        WHERE user_id = %s
        """
        self.execute_query(connection, update_query, (user_id,))

        # Send success message
        message = f"Successfully reset your {reset_type} timer"
        if reset_types[reset_type]['count_column']:
            message += " and count"
        await ctx.send(message + ".")
    @commands.command(name="charm")
    async def charm(self, ctx, charm_type: str, *, card_name: str):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Failed to connect to the database.")
            return

        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to register first.")
            return

        user_id = user_result[0]['user_id']

        # Check if user has the card
        card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
        card_result = self.execute_query(connection, card_query, (user_id, card_name))

        if not card_result:
            await ctx.send(f"You don't have a card named {card_name}.")
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

        # Check if user has the charm product
        inventory_query = """
        SELECT ip.* FROM inventory i 
        JOIN inventory_product ip ON i.inventory_id = ip.inventory_id 
        WHERE i.user_id = %s AND ip.product_id = %s
        """
        inventory_result = self.execute_query(connection, inventory_query, (user_id, charm_data['product_id']))

        if not inventory_result:
            await ctx.send(f"You don't have a {charm_type} charm in your inventory.")
            return

        # Update inventory
        if inventory_result[0]['quantiter'] == 1:
            delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
        else:
            update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

        # Apply charm to card
        update_query = f"""
        UPDATE user_cards
        SET 
            {charm_data['charm_column']} = {charm_data['charm_column']} + 1,
            {charm_data['count_column']} = 3
        WHERE user_id = %s AND name = %s
        """
        self.execute_query(connection, update_query, (user_id, card_name))

        await ctx.send(f"Applied {charm_type} charm to {card_name}. The charm stat has been increased and count set to 3.")
    @commands.command(name="capsule")
    async def capsule(self, ctx, capsule_type, *, card_name):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Failed to connect to the database.")
            return

        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to register first.")
            return

        user_id = user_result[0]['user_id']

        # Check if user has the card
        card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
        card_result = self.execute_query(connection, card_query, (user_id, card_name))

        if not card_result:
            await ctx.send(f"You don't have a card named {card_name}.")
            return

        # Determine product_id based on capsule type
        product_id_map = {'acap': 4, 'scap': 5, 'hcap': 6, 'dcap': 7}
        product_id = product_id_map.get(capsule_type.lower())

        if not product_id:
            await ctx.send("Invalid capsule type. Use acap, scap, hcap, or dcap.")
            return

        # Check if user has the capsule
        inventory_query = "SELECT ip.* FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id WHERE i.user_id = %s AND ip.product_id = %s"
        inventory_result = self.execute_query(connection, inventory_query, (user_id, product_id))

        if not inventory_result:
            await ctx.send(f"You don't have a {capsule_type} in your inventory.")
            return

        # Update inventory
        if inventory_result[0]['quantiter'] == 1:
            delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
        else:
            update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

        # Update card stats
        stat_map = {
            'acap': ('attack_stat', 'used_attackpoints'),
            'scap': ('speed_stat', 'used_speedpoints'),
            'hcap': ('health_stat', 'used_healthpoints'),
            'dcap': ('defence_stat', 'used_defencepoints')
        }
        stat_column, used_column = stat_map[capsule_type.lower()]

        update_query = f"""
        UPDATE user_cards
        SET 
            {stat_column} = {stat_column} + 1,
            {used_column} = {used_column} + 1,
            used_statpoints = used_statpoints + 1
        WHERE user_id = %s AND name = %s
        """
        self.execute_query(connection, update_query, (user_id, card_name))

        await ctx.send(f"Applied {capsule_type} to {card_name}. The stat has been increased.")

    @commands.command(name="enhance")
    async def enhance(self, ctx, *, card_name):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Failed to connect to the database.")
            return

        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

        if not user_result:
            await ctx.send("You need to register first.")
            return

        user_id = user_result[0]['user_id']

        # Check if user has the card
        card_query = "SELECT * FROM user_cards WHERE user_id = %s AND name = %s"
        card_result = self.execute_query(connection, card_query, (user_id, card_name))

        if not card_result:
            await ctx.send(f"You don't have a card named {card_name}.")
            return

        # Check if card already has an enhancer
        if card_result[0]['id_enhancer']:
            await ctx.send(f"{card_name} already has an enhancer. Wait for it to finish.")
            return

        # Check if user has the enhancer product
        inventory_query = "SELECT ip.* FROM inventory i JOIN inventory_product ip ON i.inventory_id = ip.inventory_id WHERE i.user_id = %s AND ip.product_id = 17"
        inventory_result = self.execute_query(connection, inventory_query, (user_id,))

        if not inventory_result:
            await ctx.send("You don't have an enhancer in your inventory.")
            return

        # Update inventory
        if inventory_result[0]['quantiter'] == 1:
            delete_query = "DELETE FROM inventory_product WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, delete_query, (inventory_result[0]['id_ligneinventoryproduct'],))
        else:
            update_query = "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE id_ligneinventoryproduct = %s"
            self.execute_query(connection, update_query, (inventory_result[0]['id_ligneinventoryproduct'],))

        # Apply enhancer to card
        update_query = "UPDATE user_cards SET id_enhancer = 17,fight_count=3 WHERE user_id = %s AND name = %s"
        self.execute_query(connection, update_query, (user_id, card_name))

        await ctx.send(f"Applied enhancer to {card_name}. The enhancement process has started.")
    @commands.command(name="inv")
    async def inventoryview(self, ctx):
        user_id = ctx.author.id
        
        connection = create_db_connection()
        if not connection:
            await ctx.send("Error: Unable to connect to the database.")
            return

        # Get user_id from user table
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(user_id),))

        if not user_result:
            await ctx.send("Error: User not registered.")
            connection.close()
            return

        db_user_id = user_result[0]['user_id']

        # Get inventory_id from inventory table
        inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        inventory_result = self.execute_query(connection, inventory_query, (db_user_id,))

        if not inventory_result:
            await ctx.send("Error: Inventory not found.")
            connection.close()
            return

        inventory_id = inventory_result[0]['inventory_id']

        # Fetch inventory items
        categories = {
            'weapons': ('inventory_weapon', 'weapon', 'weapon_name', 'weapon_id', 'weapon_id'),
            'orbs': ('inventory_orbs', 'leveling_orbs', 'orb_name', 'orb_id', 'orb_id'),
            'boosters_enhancers': ('inventory_product', 'product', 'product_name', 'product_id', 'product_id'),
            'fragments': ('inventory_frags', 'fragments', 'name_fragment', 'id_fragment', 'frag_id'),
            'chests': ('inventory_chest', 'chest', 'chest_name', 'chest_id', 'chest_id'),
            'dungeon_key': ('inventory_dungeonkey', 'dungeon_key', 'dungeonkey_name', 'dungeonkey_id', 'dungeonkey_id')
        }

        inventory_data = {}
        for category, (inv_table, item_table, name_column, id_column, invid_column) in categories.items():
            query = f"""
                SELECT i.quantiter as quantity, t.{name_column} as name
                FROM {inv_table} i
                JOIN {item_table} t ON i.{invid_column} = t.{id_column}
                WHERE i.inventory_id = %s
            """
            result = self.execute_query(connection, query, (inventory_id,))
            inventory_data[category] = result

        connection.close()

        view = InventoryMainView(inventory_data)
        embed = view.get_main_menu_embed(ctx.author)
        await ctx.send(embed=embed, view=view)

class InventoryMainView(discord.ui.View):
    def __init__(self, inventory_data):
        super().__init__(timeout=None)
        self.inventory_data = inventory_data
        self.add_item(self.CategorySelect())

    def clear_items(self):
        self.children.clear()

    def get_main_menu_embed(self, user):
        embed = discord.Embed(title="Shadow Sovereign's Inventory", color=0x000000)
        embed.set_author(name=f"Welcome, {user.name}", icon_url=user.avatar.url)
        embed.description = (
            "As you rise through the ranks of Hunters, your inventory grows in power and mystery. "
            "What secrets and strengths have you gathered on your journey?\n\n"
            "Choose a category to inspect your items:"
        )

        categories = [
            ("⚔️", "Weapons", "Blades that cleave through shadows"),
            ("🔮", "Orbs", "Mystical spheres of untold power"),
            ("📊", "Boosters & Enhancers", "Enhance your potential, surpass your limits"),
            ("🧩", "Fragments", "Pieces of greater power"),
            ("🎁", "Chests", "Treasures awaiting to be claimed"),
            ("🗝️", "Dungeon keys", "Keys to new challenges")
        ]

        for emoji, category, description in categories:
            embed.add_field(name=f"{emoji} {category}", value=description, inline=False)

        embed.set_footer(text="Solo Leveling: Rise as the Shadow Monarch")
        embed.set_image(url="https://static1.dualshockersimages.com/wordpress/wp-content/uploads/2024/01/solo-leveling-anime-to-premiere-this-week-on-crunchyroll.jpg")
        return embed

    def get_category_embed(self):
        items = self.inventory_data.get(self.current_category, [])
        total_pages = math.ceil(len(items) / self.items_per_page)

        if self.current_page < 1:
            self.current_page = total_pages
        elif self.current_page > total_pages:
            self.current_page = 1

        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = start_idx + self.items_per_page
        current_items = items[start_idx:end_idx]

        embed = discord.Embed(title=f"{self.current_category.capitalize()} Inventory", color=0x000000)
        
        if not current_items:
            embed.description = f"Your {self.current_category} inventory is empty. Venture forth to gather more power!"
        else:
            for item in current_items:
                embed.add_field(name=f"🔴 {item['name']} ({item['quantity']})", value="\u200b", inline=False)

        embed.set_footer(text=f"Page {self.current_page}/{total_pages}")
        return embed

    def update_view_for_category(self):
        self.clear_items()
        self.add_item(self.PreviousButton())
        self.add_item(self.NextButton())
        self.add_item(self.GoBackButton())

    def update_view_for_main_menu(self):
        self.clear_items()
        self.add_item(self.CategorySelect())

    class CategorySelect(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label="Weapons", emoji="⚔️", value="weapons"),
                discord.SelectOption(label="Orbs", emoji="🔮", value="orbs"),
                discord.SelectOption(label="Boosters & Enhancers", emoji="📊", value="boosters_enhancers"),
                discord.SelectOption(label="Fragments", emoji="🧩", value="fragments"),
                discord.SelectOption(label="Chests", emoji="🎁", value="chests"),
                discord.SelectOption(label="Dungeon Keys", emoji="🗝️", value="dungeon_key"),
            ]
            super().__init__(placeholder="Select a category", options=options)

        async def callback(self, interaction: discord.Interaction):
            view = CategoryView(self.view.inventory_data, self.values[0])
            embed = view.get_category_embed()
            await interaction.response.edit_message(embed=embed, view=view)

class CategoryView(discord.ui.View):
    def __init__(self, inventory_data, category):
        super().__init__(timeout=None)
        self.inventory_data = inventory_data
        self.current_category = category
        self.current_page = 1
        self.items_per_page = 5
        self.add_item(self.PreviousButton())
        self.add_item(self.NextButton())
        self.add_item(self.GoBackButton())

    def get_category_embed(self):
        items = self.inventory_data.get(self.current_category, [])
        total_pages = math.ceil(len(items) / self.items_per_page)

        if self.current_page < 1:
            self.current_page = total_pages
        elif self.current_page > total_pages:
            self.current_page = 1
    
        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = start_idx + self.items_per_page
        current_items = items[start_idx:end_idx]

        embed = discord.Embed(title=f"{self.current_category.capitalize()} Inventory", color=0x000000)
        
        if not current_items:
            embed.description = f"Your {self.current_category} inventory is empty. Venture forth to gather more power!"
        else:
            for item in current_items:
                embed.add_field(name=f"🔴 {item['name']} ({item['quantity']})", value="\u200b", inline=False)

        embed.set_footer(text=f"Page {self.current_page}/{total_pages}")
        return embed

    class PreviousButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label="<", custom_id="previous")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            view.current_page -= 1
            embed = view.get_category_embed()
            await interaction.response.edit_message(embed=embed, view=view)

    class NextButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.secondary, label=">", custom_id="next")

        async def callback(self, interaction: discord.Interaction):
            view: CategoryView = self.view
            view.current_page += 1
            embed = view.get_category_embed()
            await interaction.response.edit_message(embed=embed, view=view)

    class GoBackButton(discord.ui.Button):
        def __init__(self):
            super().__init__(style=discord.ButtonStyle.danger, label="Go Back", custom_id="go_back")

        async def callback(self, interaction: discord.Interaction):
            main_view = InventoryMainView(self.view.inventory_data)
            embed = main_view.get_main_menu_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=main_view)

async def setup(bot):
    await bot.add_cog(InventoryCommands(bot))