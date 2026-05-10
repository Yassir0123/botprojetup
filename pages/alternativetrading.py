import discord
from discord.ext import commands
from utils.database import create_db_connection
from mysql.connector import Error
import asyncio

# Use Unicode emojis
EMOJI_TRADE = "🔄"
EMOJI_LOCK = "🔒"
EMOJI_UNLOCK = "🔓"
EMOJI_CANCEL = "❌"
EMOJI_CONFIRM = "✅"
EMOJI_HOURGLASS = "⏳"

class TradeCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.font = Font.poppins(size=70, variant="bold")
        self.active_trades = {}
        self.item_types = {
            'weapons': ('inventory_weapon', 'weapon', 'weapon_name', 'weapon_id', 'weapon_id'),
            'orbs': ('inventory_orbs', 'leveling_orbs', 'orb_name', 'orb_id', 'orb_id'),
            'boosters_enhancers': ('inventory_product', 'product', 'product_name', 'product_id', 'product_id'),
            'fragments': ('inventory_frags', 'fragments', 'name_fragment', 'id_fragment', 'frag_id'),
        }

    def execute_query(self, connection, query, params=None):
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params) if params else cursor.execute(query)
            result = cursor.fetchall()
            connection.commit()
            return result
        except Error as e:
            print(f"Error executing query: {e}")
            return None
        finally:
            cursor.close()

    @commands.command()
    async def trade(self, ctx, target: discord.Member):
        if target == ctx.author:
            await ctx.send(f"{EMOJI_CANCEL} You can't trade with yourself!")
            return

        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        # Get user_id for initiator
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (str(ctx.author.id),))
        
        if not user_result:
            await ctx.send(f"{ctx.author.mention}, you need to be registered to initiate a trade.")
            return

        user_id = user_result[0]['user_id']

        # Get inventory_id for initiator
        inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        inventory_result = self.execute_query(connection, inventory_query, (user_id,))
        
        if not inventory_result:
            await ctx.send(f"{ctx.author.mention}, you don't have an inventory. Please contact an administrator.")
            return

        user_inventory_id = inventory_result[0]['inventory_id']

        # Get user_id for target
        target_result = self.execute_query(connection, user_query, (str(target.id),))
        
        if not target_result:
            await ctx.send(f"{target.mention} needs to be registered to participate in a trade.")
            return

        target_id = target_result[0]['user_id']

        # Get inventory_id for target
        target_inventory_result = self.execute_query(connection, inventory_query, (target_id,))
        
        if not target_inventory_result:
            await ctx.send(f"{target.mention} doesn't have an inventory. Please contact an administrator.")
            return

        target_inventory_id = target_inventory_result[0]['inventory_id']

        # Initialize trade data
        trade_data = {
            'initiator': {
                'id': user_id,
                'inventory_id': user_inventory_id,
                'weapons': [],
                'orbs': [],
                'boosters_enhancers': [],
                'fragments': [],
                'currency': 0,
                'ready': False
            },
            'target': {
                'id': target_id,
                'inventory_id': target_inventory_id,
                'weapons': [],
                'orbs': [],
                'boosters_enhancers': [],
                'fragments': [],
                'currency': 0,
                'ready': False
            },
            'state': 'waiting_acceptance',
            'current_turn': ctx.author.id
        }

        self.active_trades[ctx.author.id] = trade_data

        # Create and send initial embed
        embed = discord.Embed(title=f"{EMOJI_TRADE} New Trade Request", description=f"{ctx.author.mention} wants to trade with {target.mention}", color=discord.Color.blue())
        if ctx.author.avatar:
            embed.set_thumbnail(url=ctx.author.avatar.url)
        embed.set_footer(text="React with ✅ to accept the trade!")
        message = await ctx.send(embed=embed)

        # Add initial reactions
        await message.add_reaction(EMOJI_CONFIRM)
        await message.add_reaction(EMOJI_CANCEL)

        # Update the message reference in trade_data
        trade_data['message'] = message
        # Wait for reactions
        try:
            await self.handle_trade_process(ctx, trade_data)
        except asyncio.TimeoutError:
            await message.edit(content=f"{EMOJI_CANCEL} Trade cancelled due to inactivity.", embed=None)
            await message.clear_reactions()
            del self.active_trades[ctx.author.id]
        finally:
            connection.close()

    async def handle_trade_process(self, ctx, trade_data):
        await ctx.send("i'm here")
        while trade_data['state'] != 'completed':
            await ctx.send("trade complete not")
            if trade_data['state'] == 'waiting_acceptance':
                await ctx.send("i'm trade waiting")
                def reaction_check(reaction, user):
                    return (
                        reaction.message.id == trade_data['message'].id and
                        str(reaction.emoji) in [EMOJI_CONFIRM, EMOJI_CANCEL] and
                        user.id == trade_data['target']['id']
                    )

                try:
                    await ctx.send("i'm before")
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=300.0, check=reaction_check)
                    await ctx.send(f"reaction : {reaction}")
                    await ctx.send("leee")
                    if str(reaction.emoji) == EMOJI_CONFIRM:
                        trade_data['state'] = 'initiator_adding'
                        await self.update_trade_embed(ctx, trade_data['message'], trade_data)
                    elif str(reaction.emoji) == EMOJI_CANCEL:
                        await trade_data['message'].edit(content=f"{EMOJI_CANCEL} The trade has been cancelled.", embed=None)
                        await trade_data['message'].clear_reactions()
                        del self.active_trades[ctx.author.id]
                        return
                except asyncio.TimeoutError:
                    await trade_data['message'].edit(content=f"{EMOJI_CANCEL} Trade cancelled due to inactivity.", embed=None)
                    await trade_data['message'].clear_reactions()
                    del self.active_trades[ctx.author.id]
                    return

            elif trade_data['state'] in ['initiator_adding', 'target_adding']:
                current_user = ctx.author if trade_data['state'] == 'initiator_adding' else ctx.guild.get_member(trade_data['target']['id'])
                
                def message_check(m):
                    return m.author.id == current_user.id and m.channel == ctx.channel

                def reaction_check(reaction, user):
                    return (
                        reaction.message.id == trade_data['message'].id and
                        str(reaction.emoji) in [EMOJI_LOCK, EMOJI_CANCEL] and
                        user.id == current_user.id
                    )

                while True:
                    done, pending = await asyncio.wait([
                        self.bot.wait_for('message', check=message_check),
                        self.bot.wait_for('reaction_add', check=reaction_check)
                    ], return_when=asyncio.FIRST_COMPLETED, timeout=300.0)

                    try:
                        result = done.pop().result()
                    except asyncio.TimeoutError:
                        await trade_data['message'].edit(content=f"{EMOJI_CANCEL} Trade cancelled due to inactivity.", embed=None)
                        await trade_data['message'].clear_reactions()
                        del self.active_trades[ctx.author.id]
                        return

                    for future in pending:
                        future.cancel()

                    if isinstance(result, discord.Message):
                        msg = result
                        if msg.content.lower().startswith('sl '):
                            command = msg.content.split()[1].lower()
                            if command in ['additem', 'removeitem', 'addcurrency', 'removecurrency']:
                                await self.handle_trade_command(ctx, msg, trade_data)
                                await self.update_trade_embed(ctx, trade_data['message'], trade_data)
                    else:
                        reaction, user = result
                        if str(reaction.emoji) == EMOJI_LOCK:
                            current_side = 'initiator' if trade_data['state'] == 'initiator_adding' else 'target'
                            trade_data[current_side]['ready'] = True
                            if trade_data['state'] == 'initiator_adding':
                                trade_data['state'] = 'target_adding'
                            else:
                                trade_data['state'] = 'confirming'
                            await self.update_trade_embed(ctx, trade_data['message'], trade_data)
                            await reaction.remove(user)
                            break
                        elif str(reaction.emoji) == EMOJI_CANCEL:
                            await trade_data['message'].edit(content=f"{EMOJI_CANCEL} The trade has been cancelled.", embed=None)
                            await trade_data['message'].clear_reactions()
                            del self.active_trades[ctx.author.id]
                            return

            elif trade_data['state'] == 'confirming':
                def reaction_check(reaction, user):
                    return (
                        reaction.message.id == trade_data['message'].id and
                        str(reaction.emoji) in [EMOJI_CONFIRM, EMOJI_CANCEL] and
                        user.id == trade_data['target']['id']
                    )

                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=300.0, check=reaction_check)
                    await ctx.send(f"reaction : {str(reaction.emoji)}")
                    if str(reaction.emoji) == EMOJI_CONFIRM:
                        await self.complete_trade(ctx, trade_data)
                        trade_data['state'] = 'completed'
                        await trade_data['message'].edit(content=f"{EMOJI_CONFIRM} Trade completed successfully!", embed=None)
                        await trade_data['message'].clear_reactions()
                        del self.active_trades[ctx.author.id]
                        return
                    elif str(reaction.emoji) == EMOJI_CANCEL:
                        await trade_data['message'].edit(content=f"{EMOJI_CANCEL} The trade has been cancelled.", embed=None)
                        await trade_data['message'].clear_reactions()
                        del self.active_trades[ctx.author.id]
                        return
                except asyncio.TimeoutError:
                    await trade_data['message'].edit(content=f"{EMOJI_CANCEL} Trade cancelled due to inactivity.", embed=None)
                    await trade_data['message'].clear_reactions()
                    del self.active_trades[ctx.author.id]
                    return


    async def handle_trade_command(self, ctx, msg, trade_data):
        command_parts = msg.content.split()
        command = command_parts[1].lower()
        args = ' '.join(command_parts[2:]).split(',')

        current_side = 'initiator' if trade_data['state'] == 'initiator_adding' else 'target'

        if command == 'additem':
            if len(args) != 2:
                await ctx.send("Usage: sl additem item_name, quantity")
                return

            item_name, quantity = args
            quantity = int(quantity)

            connection = create_db_connection()
            if not connection:
                await ctx.send("Unable to connect to the database. Please try again later.")
                return

            # Check which table the item belongs to
            item_data = None
            item_type = None
            for type_key, (inv_table, item_table, name_field, id_field, inv_id_field) in self.item_types.items():
                query = f"SELECT * FROM {item_table} WHERE {name_field} = %s"
                result = self.execute_query(connection, query, (item_name,))
                if result:
                    item_data = result[0]
                    item_type = type_key
                    break

            if not item_data:
                await ctx.send(f"Item '{item_name}' not found.")
                return

            # Check if the user has enough of the item
            inv_table, item_table, name_field, id_field, inv_id_field = self.item_types[item_type]
            inventory_query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_field} = %s"
            inventory_result = self.execute_query(connection, inventory_query, (trade_data[current_side]['inventory_id'], item_data[id_field]))

            if not inventory_result or inventory_result[0]['quantiter'] < quantity:
                await ctx.send(f"You don't have enough of '{item_name}'.")
                return

            # Add item to the trade
            trade_data[current_side][item_type].append({
                'id': item_data[id_field],
                'name': item_name,
                'quantity': quantity,
                'price': item_data.get('price', 0)
            })

            await ctx.send(f"Added {quantity}x {item_name} to the trade.")

        elif command == 'removeitem':
            if len(args) != 1:
                await ctx.send("Usage: sl removeitem item_name")
                return

            item_name = args[0]

            for category in self.item_types.keys():
                for item in trade_data[current_side][category]:
                    if item['name'] == item_name:
                        trade_data[current_side][category].remove(item)
                        await ctx.send(f"Removed {item_name} from the trade.")
                        return

            await ctx.send(f"Item '{item_name}' not found in your trade offer.")

        elif command == 'addcurrency':
            if len(args) != 1:
                await ctx.send("Usage: sl addcurrency amount")
                return

            amount = int(args[0])

            connection = create_db_connection()
            if not connection:
                await ctx.send("Unable to connect to the database. Please try again later.")
                return

            query = "SELECT won_currency FROM user WHERE user_id = %s"
            result = self.execute_query(connection, query, (trade_data[current_side]['id'],))

            if not result or result[0]['won_currency'] < amount:
                await ctx.send("You don't have enough currency.")
                return

            trade_data[current_side]['currency'] = amount
            await ctx.send(f"Added {amount} currency to the trade.")

        elif command == 'removecurrency':
            trade_data[current_side]['currency'] = 0
            await ctx.send("Removed all currency from the trade.")

        connection.close()

    async def update_trade_embed(self, ctx, message, trade_data):
        embed = discord.Embed(title=f"{EMOJI_TRADE} Trade in Progress", color=discord.Color.gold())

        for side in ['initiator', 'target']:
            user = ctx.author if side == 'initiator' else ctx.guild.get_member(trade_data['target']['id'])
            items = []
            for category in self.item_types.keys():
                for item in trade_data[side][category]:
                    items.append(f"{item['quantity']}x {item['name']}")
            if trade_data[side]['currency'] > 0:
                items.append(f"{trade_data[side]['currency']} currency")
            
            status = EMOJI_LOCK if trade_data[side]['ready'] else EMOJI_UNLOCK
            embed.add_field(name=f"{status} {user.name}'s offer:", value='\n'.join(items) if items else "No items offered", inline=False)

        # Add progress bar
        total_steps = 4
        current_step = 1 if trade_data['state'] == 'waiting_acceptance' else \
                       2 if trade_data['state'] == 'initiator_adding' else \
                       3 if trade_data['state'] == 'target_adding' else 4
        progress = "█" * current_step + "░" * (total_steps - current_step)
        embed.add_field(name="Trade Progress", value=f"{progress} Step {current_step}/{total_steps}", inline=False)

        if trade_data['state'] == 'waiting_acceptance':
            status = f"{EMOJI_HOURGLASS} Waiting for {ctx.guild.get_member(trade_data['target']['id']).name} to accept the trade."
        elif trade_data['state'] == 'initiator_adding':
            status = f"{EMOJI_UNLOCK} {ctx.author.name} is adding items. React with 🔒 when ready."
        elif trade_data['state'] == 'target_adding':
            status = f"{EMOJI_UNLOCK} {ctx.guild.get_member(trade_data['target']['id']).name} is adding items. React with 🔒 when ready."
        elif trade_data['state'] == 'confirming':
            status = f"{EMOJI_CONFIRM} Waiting for {ctx.guild.get_member(trade_data['target']['id']).name} to confirm the trade."

        embed.set_footer(text=status)
        if ctx.author.avatar:
            embed.set_thumbnail(url=ctx.author.avatar.url)
        if ctx.guild.icon:
            embed.set_author(name="Trading System", icon_url=ctx.guild.icon.url)
        else:
            embed.set_author(name="Trading System")

        await message.edit(embed=embed)

        # Update reactions based on the current state
        await message.clear_reactions()
        if trade_data['state'] == 'waiting_acceptance':
            await message.add_reaction(EMOJI_CONFIRM)
            await message.add_reaction(EMOJI_CANCEL)
        elif trade_data['state'] in ['initiator_adding', 'target_adding']:
            await message.add_reaction(EMOJI_LOCK)
            await message.add_reaction(EMOJI_CANCEL)
        elif trade_data['state'] == 'confirming':
            await message.add_reaction(EMOJI_CONFIRM)
            await message.add_reaction(EMOJI_CANCEL)

    async def complete_trade(self, ctx, trade_data):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to complete the trade due to a database error. Please try again later.")
            return
        
        for side in ['initiator', 'target']:
            opposite_side = 'target' if side == 'initiator' else 'initiator'
            
            # Remove items from inventory
            for category, (inv_table, item_table, name_field, id_field, inv_id_field) in self.item_types.items():
                for item in trade_data[side][category]:
                    query = f"UPDATE {inv_table} SET quantiter = quantiter - %s WHERE inventory_id = %s AND {inv_id_field} = %s"
                    self.execute_query(connection, query, (item['quantity'], trade_data[side]['inventory_id'], item['id']))

            # Add items to inventory
            for category, (inv_table, item_table, name_field, id_field, inv_id_field) in self.item_types.items():
                for item in trade_data[opposite_side][category]:
                    query = f"INSERT INTO {inv_table} (inventory_id, {inv_id_field}, quantiter) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE quantiter = quantiter + %s"
                    self.execute_query(connection, query, (trade_data[side]['inventory_id'], item['id'], item['quantity'], item['quantity']))

            # Update currency
            if trade_data[side]['currency'] > 0:
                query = "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s"
                self.execute_query(connection, query, (trade_data[side]['currency'], trade_data[side]['id']))

            if trade_data[opposite_side]['currency'] > 0:
                query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                self.execute_query(connection, query, (trade_data[opposite_side]['currency'], trade_data[side]['id']))

        connection.close()

async def setup(bot):
    await bot.add_cog(TradeCommands(bot))