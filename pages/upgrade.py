import discord
from discord.ext import commands
from utils.database import create_db_connection
from utils.activity_state import block_during_user_activity
from mysql.connector import Error
from asyncio import TimeoutError

class UpgradeCommands(commands.Cog):
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
        except Error as e:
            print(f"Error executing query: {e}")
            return None
        finally:
            cursor.close()

    @commands.command(name="upgrade")
    @block_during_user_activity()
    async def upgrade(self, ctx, *, card_name):
        if not card_name:
            await ctx.send("Please provide a card name to upgrade.")
            return

        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        try:
            # Get user_id from user table
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send("You are not registered. Please register first.")
                return

            user_id = user_result[0]['user_id']

            # Get inventory_id from inventory table
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = self.execute_query(connection, inventory_query, (user_id,))

            if not inventory_result:
                await ctx.send("Your inventory was not found. Please contact an administrator.")
                return

            inventory_id = inventory_result[0]['inventory_id']

            user_card_query = "SELECT * FROM user_cards WHERE user_id = %s AND LOWER(name) = LOWER(%s)"
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
            card_id = card['card_id']

            # Check if the card can be upgraded
            if not card['upgradedcard_id']:
                await ctx.send(f"The card '{card_name}' cannot be upgraded further.")
                return
            # Check upgrade requirements
            frag_required = False
            orb_required = False
            money_required = False

            frag_query = "SELECT * FROM cards_levelup_frags WHERE card_id = %s"
            frag_result = self.execute_query(connection, frag_query, (card_id,))

            orb_query = "SELECT * FROM cards_levelup_orbs WHERE card_id = %s"
            orb_result = self.execute_query(connection, orb_query, (card_id,))

            if frag_result:
                frag_required = True
            if orb_result:
                orb_required = True
            money_required = card['rankingup_cap_money'] > 0

            if not (frag_required or orb_required or money_required):
                await ctx.send(f"The card '{card_name}' cannot be upgraded.")
                return

            # Check if user has required items/currency
            requirements_met = True
            user_resources = {}

            if frag_required:
                frag_id = frag_result[0]['frag_id']
                frag_quantity = frag_result[0]['quantiter']
                user_frag_query = "SELECT quantiter FROM inventory_frags WHERE inventory_id = %s AND frag_id = %s"
                user_frag_result = self.execute_query(connection, user_frag_query, (inventory_id, frag_id))

                user_frag_quantity = user_frag_result[0]['quantiter'] if user_frag_result else 0
                if user_frag_quantity < frag_quantity:
                    requirements_met = False
                user_resources['fragments'] = {'required': frag_quantity, 'current': user_frag_quantity}

            if orb_required:
                orb_id = orb_result[0]['orb_id']
                orb_quantity = orb_result[0]['quantiter']
                user_orb_query = "SELECT quantiter FROM inventory_orbs WHERE inventory_id = %s AND orb_id = %s"
                user_orb_result = self.execute_query(connection, user_orb_query, (inventory_id, orb_id))

                user_orb_quantity = user_orb_result[0]['quantiter'] if user_orb_result else 0
                if user_orb_quantity < orb_quantity:
                    requirements_met = False
                user_resources['orbs'] = {'required': orb_quantity, 'current': user_orb_quantity}

            if money_required:
                money_required = card['rankingup_cap_money']
                user_money_query = "SELECT won_currency FROM user WHERE user_id = %s"
                user_money_result = self.execute_query(connection, user_money_query, (user_id,))

                user_money = user_money_result[0]['won_currency']
                if user_money < money_required:
                    requirements_met = False
                user_resources['currency'] = {'required': money_required, 'current': user_money}

            if not requirements_met:
                await ctx.send(f"You don't have the required items to upgrade '{card_name}'.")
                return

            # Create confirmation embed
            embed = discord.Embed(title=f"Upgrade Confirmation for {card_name}", color=0x00ff00)
            embed.description = "Here's what will be used for the upgrade:"

            for resource, data in user_resources.items():
                embed.add_field(
                    name=f"{resource.capitalize()}",
                    value=f"Required: {data['required']}\nCurrent: {data['current']}\nRemaining: {data['current'] - data['required']}",
                    inline=False
                )

            embed.set_footer(text="React with ✅ to confirm or ❌ to cancel.")

            # Confirmation message with reaction buttons
            confirm_message = await ctx.send(embed=embed)
            await confirm_message.add_reaction("✅")
            await confirm_message.add_reaction("❌")

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_message.id

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
            except TimeoutError:
                await ctx.send("Upgrade cancelled due to timeout.")
                return

            if str(reaction.emoji) == "❌":
                await ctx.send("Upgrade cancelled.")
                return

            # Process upgrade
            if frag_required:
                update_frag_query = "UPDATE inventory_frags SET quantiter = quantiter - %s WHERE inventory_id = %s AND frag_id = %s"
                self.execute_query(connection, update_frag_query, (frag_quantity, inventory_id, frag_id))

            if orb_required:
                update_orb_query = "UPDATE inventory_orbs SET quantiter = quantiter - %s WHERE inventory_id = %s AND orb_id = %s"
                self.execute_query(connection, update_orb_query, (orb_quantity, inventory_id, orb_id))

            if money_required:
                update_money_query = "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s"
                self.execute_query(connection, update_money_query, (money_required, user_id))

            # Get upgraded card data
            upgraded_card_query = "SELECT * FROM card WHERE card_id = %s"
            upgraded_card_result = self.execute_query(connection, upgraded_card_query, (card['upgradedcard_id'],))
            
            if not upgraded_card_result:
                await ctx.send(f"Error: Upgraded card data not found for '{card_name}'. Please contact an administrator.")
                return

            upgraded_card = upgraded_card_result[0]

            # Update user_cards table
            update_user_card_query = """
            UPDATE user_cards 
            SET card_id = %s, current_level = %s, current_xp = %s, current_mana = %s, 
                power_stat = %s, attack_stat = %s, health_stat = %s, speed_stat = %s 
            WHERE user_id = %s AND card_id = %s
            """
            update_result = self.execute_query(connection, update_user_card_query, (
                upgraded_card['card_id'], upgraded_card['min_level'], 0, 0,
                upgraded_card['power'], upgraded_card['attack'], upgraded_card['health'], upgraded_card['speed'],
                user_id, card_id
            ))

            if update_result is None:
                await ctx.send(f"Error occurred while upgrading '{card_name}'. Please try again or contact an administrator.")
                return

            await ctx.send(f"Successfully upgraded '{card_name}' to '{upgraded_card['card_name']}'!")

        except Error as e:
            await ctx.send(f"An error occurred while processing your request: {str(e)}")
        finally:
            if connection.is_connected():
                connection.close()

async def setup(bot):
    await bot.add_cog(UpgradeCommands(bot))
