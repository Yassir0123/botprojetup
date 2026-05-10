import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import aiomysql
from typing import Optional, Dict, Any
# Add these constants at the top of the UserphoneCommands class
EMOJI_TRADE = "🔄"
EMOJI_CHECK = "✅"
EMOJI_CROSS = "❌"
EMOJI_LOCK = "🔒"
EMOJI_UNLOCK = "🔓"
EMOJI_MONEY = "💰"
class UserphoneCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_inactive_calls.start()
        self.connection_locks = {}
        self.pool: Optional[aiomysql.Pool] = None
        self.channel_cache = {}
        self.call_start_times = {}
        # Track last message time per user instead of per connection
        self.last_message_times = {}
        self.first_message_sent = {}  # Track first message per user instead of per connection
        self.active_crosstrades = {}
        self.crosstrading_locks = {}
        self.item_tables = {
            'weapons': ('inventory_weapon', 'weapon', 'weapon_name', 'weapon_id', 'weapon_id'),
            'orbs': ('inventory_orbs', 'leveling_orbs', 'orb_name', 'orb_id', 'orb_id'),
            'boosters_enhancers': ('inventory_product', 'product', 'product_name', 'product_id', 'product_id'),
            'fragments': ('inventory_frags', 'fragments', 'name_fragment', 'id_fragment', 'frag_id'),
        }

    def cog_unload(self):
        self.check_inactive_calls.cancel()

    async def get_pool(self) -> aiomysql.Pool:
        if self.pool is None:
            self.pool = await aiomysql.create_pool(
                host='localhost',
                user='root',
                password='',
                db='sololevelingboting',
                port= 3307,
                autocommit=True
            )
        return self.pool

    async def execute_query(self, query: str, params: tuple = None) -> Optional[list[Dict[str, Any]]]:
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                try:
                    await cursor.execute(query, params)
                    if query.lower().strip().startswith('select'):
                        result = await cursor.fetchall()
                        return result
                    await conn.commit()
                    return None
                except Exception as e:
                    print(f"Database error: {e}")
                    return None

    @tasks.loop(seconds=5)
    async def check_inactive_calls(self):
        try:
            current_time = datetime.datetime.now()

            # Get ALL active connections
            query = """
                SELECT * FROM userphone_connections 
                WHERE status = 1
            """
            all_connections = await self.execute_query(query)

            if not all_connections:
                return

            # Group connections by pairs
            connection_pairs = {}
            for conn in all_connections:
                user_id = conn['user_id']
                connected_to = conn['connected_to']
                pair_key = tuple(sorted([user_id, connected_to]))
                if pair_key not in connection_pairs:
                    connection_pairs[pair_key] = []
                connection_pairs[pair_key].append(conn)

            # Process each pair
            for pair_key, connections in connection_pairs.items():
                if len(connections) != 2:
                    continue

                conn1, conn2 = connections
                
                # Check each user's activity independently
                for conn in [conn1, conn2]:
                    user_id = conn['user_id']
                    
                    # Check if user has sent their first message
                    if user_id in self.first_message_sent:
                        last_message_time = self.last_message_times.get(user_id)
                        if last_message_time:
                            time_since_last = (current_time - last_message_time).total_seconds()
                            print(f"Time since last message: {time_since_last} seconds")
                            if time_since_last > 100:
                                await self.end_call(conn['connection_id'], f"Call ended due to inactivity from user {user_id}")
                                break
                    else:
                        # Check initial timer if user hasn't sent first message
                        user_start = self.call_start_times.get(user_id)
                        if user_start:
                            time_since_start = (current_time - user_start['start_time']).total_seconds()
                            print(f"Time since last message: {time_since_start} seconds")
                            if time_since_start > 100:
                                await self.end_call(conn['connection_id'], f"Call ended - no message from user {user_id}")
                                break

            # Check for expired searches
            query = """
                SELECT * FROM userphone_connections 
                WHERE status = 0 
                AND search_start_time < NOW() - INTERVAL 20 SECOND
            """
            expired_searches = await self.execute_query(query)

            if expired_searches:
                for search in expired_searches:
                    await self.cancel_search(search['user_id'])
                    channel = await self.get_user_channel(search['user_id'])
                    if channel:
                        await channel.send("No connection found. Search timed out.")

        except Exception as e:
            print(f"Error in check_inactive_calls: {e}")
    async def check_user_registered(self, user_id: str) -> bool:
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await self.execute_query(query, (str(user_id),))
        return bool(result)

    async def send_registration_embed(self, ctx):
        embed = discord.Embed(
            title="Registration Required",
            description="You need to be registered to use the phone system!\nPlease register first using the registration command.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="How to Register",
            value="Use the registration command to create your account.",
            inline=False
        )
        await ctx.send(embed=embed)

    async def get_user_channel(self, user_id: str) -> Optional[tuple]:
        query = "SELECT server_id, channel_id FROM userphone_connections WHERE user_id = %s AND status = 1"
        result = await self.execute_query(query, (user_id,))
        if result:
            cache_key = f"{result[0]['server_id']}_{result[0]['channel_id']}"
            if cache_key not in self.channel_cache:
                guild = self.bot.get_guild(int(result[0]['server_id']))
                if guild:
                    channel = guild.get_channel(int(result[0]['channel_id']))
                    self.channel_cache[cache_key] = channel
            return self.channel_cache.get(cache_key)
        return None

    async def get_connection_details(self, user_id: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT * FROM userphone_connections 
            WHERE user_id = %s OR connected_to = %s
        """
        result = await self.execute_query(query, (str(user_id), str(user_id)))
        return result[0] if result else None

    async def check_cooldown(self, user_id: str) -> bool:
        query = "SELECT last_command_use FROM userphone_cooldowns WHERE user_id = %s"
        result = await self.execute_query(query, (str(user_id),))
        
        if not result:
            return True
            
        last_use = result[0]['last_command_use']
        if datetime.datetime.now() - last_use < datetime.timedelta(seconds=10):
            return False
        return True

    async def update_cooldown(self, user_id: str):
        query = """
            INSERT INTO userphone_cooldowns (user_id, last_command_use) 
            VALUES (%s, NOW())
            ON DUPLICATE KEY UPDATE last_command_use = NOW()
        """
        await self.execute_query(query, (str(user_id),))



    async def end_call(self, connection_id: int, reason: str, hangup_user: str = None):
        try:
            # Get connection details before deletion
            query = "SELECT * FROM userphone_connections WHERE connection_id = %s"
            connections = await self.execute_query(query, (connection_id,))
            
            if not connections:
                return
                
            connection = connections[0]
            
            # Get both users' channels
            user1_channel = await self.get_user_channel(connection['user_id'])
            user2_channel = await self.get_user_channel(connection['connected_to'])

            # Delete all chat logs
            await self.execute_query(
                """
                DELETE cl FROM userphone_chatlogs cl
                INNER JOIN userphone_connections uc1 ON cl.connection_id = uc1.connection_id
                WHERE (uc1.user_id = %s AND uc1.connected_to = %s)
                OR (uc1.user_id = %s AND uc1.connected_to = %s)
                """,
                (connection['user_id'], connection['connected_to'],
                 connection['connected_to'], connection['user_id'])
            )
            
            # Delete the connections
            await self.execute_query(
                """
                DELETE FROM userphone_connections 
                WHERE connection_id = %s OR 
                      (user_id = %s AND connected_to = %s) OR 
                      (user_id = %s AND connected_to = %s)
                """,
                (connection_id, connection['user_id'], connection['connected_to'], 
                 connection['connected_to'], connection['user_id'])
            )
            
            # Clean up tracking data for both users
            for user_id in [connection['user_id'], connection['connected_to']]:
                self.call_start_times.pop(user_id, None)
                self.last_message_times.pop(user_id, None)
                self.first_message_sent.pop(user_id, None)
            
            # Send end messages to both users in all cases
            if user1_channel:
                await user1_channel.send("🔴 Call ended")
            if user2_channel:
                await user2_channel.send("🔴 Call ended")
                
        except Exception as e:
            print(f"Error in end_call: {e}")

    async def cancel_search(self, user_id: str):
        await self.execute_query(
            "DELETE FROM userphone_connections WHERE user_id = %s AND status = 0",
            (str(user_id),)
        )

    @commands.command(name="connect")
    async def connect(self, ctx):
        if not await self.check_user_registered(ctx.author.id):
            await self.send_registration_embed(ctx)
            return

        if not await self.check_cooldown(ctx.author.id):
            await ctx.send("Please wait before using this command again.", delete_after=5)
            return
            
        await self.update_cooldown(ctx.author.id)
        
        # Check if user is already in a call or searching
        query = "SELECT * FROM userphone_connections WHERE user_id = %s OR connected_to = %s"
        existing = await self.execute_query(query, (str(ctx.author.id), str(ctx.author.id)))
        
        if existing:
            if existing[0]['status'] == 1:
                await ctx.send("You are already in a call. Use `sl hangup` to end it.", delete_after=5)
            else:
                await ctx.send("You are already searching for a connection.", delete_after=5)
            return
            
        # Add user to waiting list
        query = """
            INSERT INTO userphone_connections 
            (user_id, server_id, channel_id, status, search_start_time) 
            VALUES (%s, %s, %s, 0, NOW())
        """
        await self.execute_query(query, (str(ctx.author.id), str(ctx.guild.id), str(ctx.channel.id)))
        
        searching_msg = await ctx.send("🔍 Searching for a connection...")
        
        # Look for another waiting user
        query = """
            SELECT c.* FROM userphone_connections c
            INNER JOIN user u ON c.user_id = u.user_authorid
            WHERE c.status = 0 AND c.user_id != %s 
            ORDER BY c.search_start_time ASC LIMIT 1
        """
        waiting_users = await self.execute_query(query, (str(ctx.author.id),))
        
        if waiting_users:
            other_user = waiting_users[0]
            
            # Store initial server and channel info for both users
            current_time = datetime.datetime.now()
            self.call_start_times[str(ctx.author.id)] = {
                'server_id': str(ctx.guild.id),
                'channel_id': str(ctx.channel.id),
                'start_time': current_time
            }
            self.call_start_times[other_user['user_id']] = {
                'server_id': other_user['server_id'],
                'channel_id': other_user['channel_id'],
                'start_time': current_time
            }
            
            # Update both users to connected status
            update_query = """
                UPDATE userphone_connections 
                SET status = 1, connected_to = %s
                WHERE user_id = %s
            """
            await self.execute_query(update_query, (other_user['user_id'], str(ctx.author.id)))
            await self.execute_query(update_query, (str(ctx.author.id), other_user['user_id']))
            
            # Get the connection ID
            connection = await self.get_connection_details(str(ctx.author.id))
            
            # Delete searching message and send connected messages
            await searching_msg.delete()
            await ctx.send("🟢 Connected! You can now chat. Use `sl hangup` to end the call.")
            
            other_channel = await self.get_user_channel(other_user['user_id'])
            if other_channel:
                await other_channel.send("🟢 Connected! You can now chat. Use `sl hangup` to end the call.")

    @commands.command(name="hangup")
    async def hangup(self, ctx):
        if not await self.check_user_registered(ctx.author.id):
            await self.send_registration_embed(ctx)
            return

        if not await self.check_cooldown(ctx.author.id):
            await ctx.send("Please wait before using this command again.", delete_after=5)
            return
            
        await self.update_cooldown(ctx.author.id)
        
        # Check if user is in a call
        connection = await self.get_connection_details(str(ctx.author.id))
        
        if not connection:
            await ctx.send("You are not in a call or searching.", delete_after=5)
            return
            
        if connection['status'] == 0:
            await self.cancel_search(str(ctx.author.id))
            await ctx.send("Search cancelled.", delete_after=5)
        else:
            # Pass the user ID who initiated the hangup
            await self.end_call(connection['connection_id'], "Call ended", str(ctx.author.id))
            # Delete the hangup command message
            try:
                await ctx.message.delete()
            except:
                pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Ignore "sl hangup" messages in the chat forwarding
        if message.content.lower().strip() == "sl hangup":
            return

        # Rest of the on_message handler remains the same
        connection = await self.get_connection_details(str(message.author.id))
        if not connection or connection['status'] != 1:
            return

        # Check if the message is from the original server where the call started
        user_start_info = self.call_start_times.get(str(message.author.id))
        if not user_start_info or str(message.guild.id) != user_start_info['server_id']:
            return

        current_time = datetime.datetime.now()
        user_id = str(message.author.id)
        
        # Update last message time and first message sent for the current user
        self.last_message_times[user_id] = current_time
        self.first_message_sent[user_id] = True

        # Get the other user's ID and channel
        other_user_id = connection['connected_to'] if connection['user_id'] == user_id else connection['user_id']
        other_channel = await self.get_user_channel(other_user_id)

        # Store the message in the chat log
        await self.execute_query(
            """
            INSERT INTO userphone_chatlogs 
            (connection_id, sender_id, content) 
            VALUES (%s, %s, %s)
            """,
            (connection['connection_id'], user_id, message.content)
        )

        # Forward the message to the other channel
        if other_channel:
            await other_channel.send(f"**{message.author.name}**: {message.content}")
    async def setup_crosstrade(self, connection_id: int, initiator_id: str, target_id: str):
        """Initialize a new cross-trade session"""
        trade_data = {
            'connection_id': connection_id,
            'initiator_id': initiator_id,
            'target_id': target_id,
            'initiator_offer': [],
            'target_offer': [],
            'initiator_money': 0,
            'target_money': 0,
            'initiator_ready': False,
            'target_ready': False,
            'current_turn': initiator_id,
            'trade_state': 'waiting_acceptance',
            'initiator_total': 0,
            'target_total': 0,
            'message_ids': {}
        }
        self.active_crosstrades[connection_id] = trade_data
        return trade_data

    async def create_trade_embed(self, trade_data: Dict[str, Any], notification: str = None) -> discord.Embed:
        """Create the trade status embed"""
        embed = discord.Embed(title=f"{EMOJI_TRADE} Cross-Server Trade", color=discord.Color.blue())
        
        # Add initiator's offer
        initiator_status = EMOJI_LOCK if trade_data['initiator_ready'] else EMOJI_UNLOCK
        initiator_items = "\n".join([f"• {item['name']} x{item['quantity']}" for item in trade_data['initiator_offer']])
        if trade_data['initiator_money'] > 0:
            initiator_items += f"\n• {EMOJI_MONEY} {trade_data['initiator_money']:,} currency"
        
        embed.add_field(
            name=f"{initiator_status} Initiator's Offer (Total: {trade_data['initiator_total']:,})",
            value=initiator_items if initiator_items else "No items offered",
            inline=False
        )
        
        # Add target's offer
        target_status = EMOJI_LOCK if trade_data['target_ready'] else EMOJI_UNLOCK
        target_items = "\n".join([f"• {item['name']} x{item['quantity']}" for item in trade_data['target_offer']])
        if trade_data['target_money'] > 0:
            target_items += f"\n• {EMOJI_MONEY} {trade_data['target_money']:,} currency"
        
        embed.add_field(
            name=f"{target_status} Target's Offer (Total: {trade_data['target_total']:,})",
            value=target_items if target_items else "No items offered",
            inline=False
        )
        
        # Add status
        status = "Waiting for acceptance"
        if trade_data['trade_state'] == 'initiator_adding':
            status = "Initiator's turn to add items"
        elif trade_data['trade_state'] == 'target_adding':
            status = "Target's turn to add items"
        elif trade_data['trade_state'] == 'confirming':
            status = "Waiting for final confirmation"
        
        embed.set_footer(text=status)
        
        if notification:
            embed.description = notification
        
        return embed

    async def update_trade_messages(self, trade_data: Dict[str, Any], notification: str = None):
        """Update trade messages in both channels"""
        embed = await self.create_trade_embed(trade_data, notification)
        
        # Update messages in both channels
        for user_id in [trade_data['initiator_id'], trade_data['target_id']]:
            channel = await self.get_user_channel(user_id)
            if channel:
                message_id = trade_data['message_ids'].get(user_id)
                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(embed=embed)
                    except:
                        new_message = await channel.send(embed=embed)
                        trade_data['message_ids'][user_id] = new_message.id
                else:
                    new_message = await channel.send(embed=embed)
                    trade_data['message_ids'][user_id] = new_message.id

    @commands.command()
    async def crosstrade(self, ctx):
        """Initiate a cross-server trade with your current call partner"""
        # Check if user is in a call
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['status'] != 1:
            await ctx.send("You must be in an active call to initiate a trade.")
            return
            
        # Get user IDs
        initiator_id = str(ctx.author.id)
        target_id = connection['connected_to'] if connection['user_id'] == initiator_id else connection['user_id']
        
        # Check if either user is already in a trade
        if connection['connection_id'] in self.active_crosstrades:
            await ctx.send("There's already an active trade in this call.")
            return
            
        # Initialize trade
        trade_data = await self.setup_crosstrade(connection['connection_id'], initiator_id, target_id)
        
        # Create initial embed
        embed = discord.Embed(
            title=f"{EMOJI_TRADE} Trade Request",
            description=f"React with {EMOJI_CHECK} to accept or {EMOJI_CROSS} to decline",
            color=discord.Color.blue()
        )
        
        # Send to both channels and store message IDs
        for user_id in [initiator_id, target_id]:
            channel = await self.get_user_channel(user_id)
            if channel:
                message = await channel.send(embed=embed)
                trade_data['message_ids'][user_id] = message.id
                await message.add_reaction(EMOJI_CHECK)
                await message.add_reaction(EMOJI_CROSS)
        
        # Wait for reaction
        def check(reaction, user):
            return (
                str(user.id) == target_id and
                str(reaction.emoji) in [EMOJI_CHECK, EMOJI_CROSS] and
                reaction.message.id in trade_data['message_ids'].values()
            )
        
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
            
            if str(reaction.emoji) == EMOJI_CROSS:
                del self.active_crosstrades[connection['connection_id']]
                await self.update_trade_messages(trade_data, "Trade cancelled by target")
                return
                
            if str(reaction.emoji) == EMOJI_CHECK:
                trade_data['trade_state'] = 'initiator_adding'
                await self.update_trade_messages(trade_data, "Trade accepted! Initiator can now add items.")
                
        except asyncio.TimeoutError:
            del self.active_crosstrades[connection['connection_id']]
            await self.update_trade_messages(trade_data, "Trade request timed out")
            return

    @commands.command(name="lock")
    async def lock(self, ctx):
        """Lock your side of the trade and move to the next phase"""
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['connection_id'] not in self.active_crosstrades:
            await ctx.send("You must be in an active trade call to lock your offer.", delete_after=5)
            return
            
        trade_data = self.active_crosstrades[connection['connection_id']]
        
        if str(ctx.author.id) != trade_data['current_turn']:
            await ctx.send("It's not your turn to lock the trade.", delete_after=5)
            return

        is_initiator = str(ctx.author.id) == trade_data['initiator_id']
        if is_initiator:
            trade_data['initiator_ready'] = True
            trade_data['current_turn'] = trade_data['target_id']
            trade_data['trade_state'] = 'target_adding'
            await self.update_trade_messages(trade_data, "Initiator locked their offer. Target's turn to add items.")
        else:
            if not trade_data['initiator_ready']:
                await ctx.send("The initiator hasn't locked their offer yet.", delete_after=5)
                return
            trade_data['target_ready'] = True
            trade_data['trade_state'] = 'confirming'
            await self.update_trade_messages(trade_data, "Target locked their offer. Use 'sl confirm' to finalize the trade.")

                
            ratio = trade_data['target_total'] / trade_data['initiator_total']
            
            # Check if trade only contains currency
            initiator_only_currency = not trade_data['initiator_offer'] and trade_data['initiator_money'] > 0
            target_only_currency = not trade_data['target_offer'] and trade_data['target_money'] > 0
            
            if initiator_only_currency and target_only_currency:
                await ctx.send("Trade cannot consist of only currency. At least one item must be included.", delete_after=5)
                return
            
            if not (0.8 <= ratio <= 1.2):
                if ratio < 0.8:
                    min_value = trade_data['initiator_total'] * 0.8
                    await ctx.send(f"Trade values are too unbalanced. Your offer must be at least {min_value:,.0f} in value.", delete_after=5)
                else:
                    max_value = trade_data['initiator_total'] * 1.2
                    await ctx.send(f"Trade values are too unbalanced. Your offer must be at most {max_value:,.0f} in value.", delete_after=5)
                return
                
            trade_data['target_ready'] = True
            trade_data['trade_state'] = 'confirming'
            await self.update_trade_messages(trade_data, "Target locked offer. Use 'sl confirm' to complete trade.")

        # Delete the lock command message
        try:
            await ctx.message.delete()
        except:
            pass

    async def transfer_items(self, cursor, items, from_id, to_id):
        """Helper function to transfer multiple items between inventories with detailed logging"""
        # Get user IDs from author IDs
        print(f"[Transfer] Starting transfer from user {from_id} to user {to_id}")
        
        from_user = await self.execute_query("SELECT user_id FROM user WHERE user_authorid = %s", (from_id,))
        to_user = await self.execute_query("SELECT user_id FROM user WHERE user_authorid = %s", (to_id,))
        
        if not from_user or not to_user:
            print(f"[Transfer] Error: Could not find user IDs for {from_id} or {to_id}")
            raise Exception("Could not find user IDs")
            
        from_id = from_user[0]['user_id']
        to_id = to_user[0]['user_id']
        print(f"[Transfer] Resolved database user IDs - From: {from_id}, To: {to_id}")

        # Get inventory IDs
        from_inv = await self.execute_query("SELECT inventory_id FROM inventory WHERE user_id = %s", (from_id,))
        to_inv = await self.execute_query("SELECT inventory_id FROM inventory WHERE user_id = %s", (to_id,))
        
        if not from_inv or not to_inv:
            print(f"[Transfer] Error: Could not find inventory IDs for users {from_id} or {to_id}")
            raise Exception("Could not find inventory IDs")

        from_inv_id = from_inv[0]['inventory_id']
        to_inv_id = to_inv[0]['inventory_id']
        print(f"[Transfer] Found inventory IDs - From: {from_inv_id}, To: {to_inv_id}")

        # Process each item
        for item in items:
            try:
                print(f"[Transfer] Processing item: {item}")
                
                # Get correct table info based on category
                table_info = self.item_tables.get(item['category'])
                if not table_info:
                    print(f"[Transfer] Error: Unknown category '{item['category']}'")
                    raise Exception(f"Unknown item category: {item['category']}")

                inv_table, item_table, name_col, id_col, inv_id_col = table_info
                item_id = item['id']
                quantity_to_transfer = item['quantity']
                print("id ", item_id)
                print("quantity ", quantity_to_transfer)
                # Check current quantity before transfer
                check_qty_query = f"""
                    SELECT quantiter 
                    FROM {inv_table} 
                    WHERE inventory_id = %s AND {inv_id_col} = %s
                """
                await cursor.execute(check_qty_query, (from_inv_id, item_id))
                current_qty = (await cursor.fetchone())['quantiter']
                print(f"[Transfer] Current quantity: {current_qty}")
                if current_qty > quantity_to_transfer:
                    # Update quantity if more than transfer amount
                    print("updating")
                    remove_query = f"""
                        UPDATE {inv_table} 
                        SET quantiter = quantiter - %s 
                        WHERE inventory_id = %s AND {inv_id_col} = %s
                    """
                    await cursor.execute(remove_query, (quantity_to_transfer, from_inv_id, item_id))
                else:
                    # Delete row if quantity will be 0 or less
                    print("deleting")
                    delete_query = f"""
                        DELETE FROM {inv_table}
                        WHERE inventory_id = %s AND {inv_id_col} = %s
                    """
                    await cursor.execute(delete_query, (from_inv_id, item_id))

                # Check if receiver already has the item
                check_query = f"""
                    SELECT quantiter 
                    FROM {inv_table} 
                    WHERE inventory_id = %s AND {inv_id_col} = %s
                """
                await cursor.execute(check_query, (to_inv_id, item_id))
                existing = await cursor.fetchone()

                if existing:
                    # Update existing quantity
                    update_query = f"""
                        UPDATE {inv_table} 
                        SET quantiter = quantiter + %s 
                        WHERE inventory_id = %s AND {inv_id_col} = %s
                    """
                    await cursor.execute(update_query, (quantity_to_transfer, to_inv_id, item_id))
                else:
                    # Insert new item
                    insert_query = f"""
                        INSERT INTO {inv_table} (inventory_id, {inv_id_col}, quantiter) 
                        VALUES (%s, %s, %s)
                    """
                    await cursor.execute(insert_query, (to_inv_id, item_id, quantity_to_transfer))

                print(f"[Transfer] Successfully transferred item {item_id}")

            except Exception as e:
                print(f"[Transfer] Error processing item {item}: {str(e)}")
                print(f"[Transfer] Full item data: {item}")
                raise Exception(f"Error transferring item {item['name']}: {str(e)}")

    async def transfer_currency(self, cursor, from_id, to_id, amount):
        """Helper function to transfer currency between users with improved error handling and logging"""
        try:
            print(f"[Currency] Starting currency transfer of {amount} from {from_id} to {to_id}")
            
            # Get sender's user ID from author ID
            from_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            from_user = await self.execute_query(from_user_query, (from_id,))
            if not from_user:
                print(f"[Currency] Error: Could not find user ID for sender {from_id}")
                raise Exception(f"Could not find user ID for sender {from_id}")
            from_user_id = from_user[0]['user_id']
            
            # Get receiver's user ID from author ID
            to_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            to_user = await self.execute_query(to_user_query, (to_id,))
            if not to_user:
                print(f"[Currency] Error: Could not find user ID for receiver {to_id}")
                raise Exception(f"Could not find user ID for receiver {to_id}")
            to_user_id = to_user[0]['user_id']
            
            print(f"[Currency] Resolved database user IDs - From: {from_user_id}, To: {to_user_id}")
            
            # Verify sender has enough currency
            balance_query = "SELECT won_currency FROM user WHERE user_id = %s"
            balance = await self.execute_query(balance_query, (from_user_id,))
            if not balance or balance[0]['won_currency'] < amount:
                print(f"[Currency] Error: Insufficient funds for user {from_user_id}")
                raise Exception(f"Insufficient currency for transfer: {amount}")
            
            print(f"[Currency] Verified balance for sender: {balance[0]['won_currency']}")
            
            # Remove currency from sender
            remove_query = "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s"
            await cursor.execute(remove_query, (amount, from_user_id))
            print(f"[Currency] Removed {amount} from user {from_user_id}")
            
            # Add currency to receiver
            add_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            await cursor.execute(add_query, (amount, to_user_id))
            print(f"[Currency] Added {amount} to user {to_user_id}")
            
            print(f"[Currency] Successfully transferred {amount} currency")
            
        except Exception as e:
            print(f"[Currency] Error during currency transfer: {str(e)}")
            raise Exception(f"Currency transfer failed: {str(e)}")

    async def process_trade(self, trade_data):
        """Process the trade by transferring items and currency between users"""
        try:
            # Get user IDs first (ensure they exist)
            initiator_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            initiator_user = await self.execute_query(initiator_user_query, (trade_data['initiator_id'],))
            target_user = await self.execute_query(initiator_user_query, (trade_data['target_id'],))
            
            if not initiator_user or not target_user:
                print(f"[Trade] Error: Could not find users - Initiator: {initiator_user}, Target: {target_user}")
                return False, "Could not find trade participants"

            # Get inventory IDs
            initiator_inv_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            initiator_result = await self.execute_query(initiator_inv_query, (initiator_user[0]['user_id'],))
            target_result = await self.execute_query(initiator_inv_query, (target_user[0]['user_id'],))
            
            print(f"[Trade] Initiator inventory: {initiator_result}")
            print(f"[Trade] Target inventory: {target_result}")

            if not initiator_result or not target_result:
                print("[Trade] Could not find user inventories")
                return False, "Could not find user inventories"

            initiator_inv_id = initiator_result[0]['inventory_id']
            target_inv_id = target_result[0]['inventory_id']

            # Process initiator's items
            for item in trade_data['initiator_offer']:
                # Remove from initiator
                if item['category'] == 'boosters_enhancers':
                    table = 'inventory_product'
                    id_col = 'product_id'
                elif item['category'] == 'weapons':
                    table = 'inventory_weapon'
                    id_col = 'weapon_id'
                elif item['category'] == 'orbs':
                    table = 'inventory_orbs'
                    id_col = 'orb_id'
                elif item['category'] == 'fragments':
                    table = 'inventory_frags'
                    id_col = 'frag_id'
                else:
                    continue

                # Remove from initiator
                # Check current quantity first
                check_query = f"""
                    SELECT quantiter 
                    FROM {table} 
                    WHERE inventory_id = %s AND {id_col} = %s
                """
                result = await self.execute_query(check_query, (initiator_inv_id, item['id']))
                
                if result and result[0]['quantiter'] > item['quantity']:
                    # If quantity is more than the transfer amount, just reduce
                    remove_query = f"""
                        UPDATE {table} 
                        SET quantiter = quantiter - %s 
                        WHERE inventory_id = %s AND {id_col} = %s
                    """
                    await self.execute_query(remove_query, (item['quantity'], initiator_inv_id, item['id']))
                else:
                    # If quantity will become 0 or matches transfer amount, delete the row
                    delete_query = f"""
                        DELETE FROM {table}
                        WHERE inventory_id = %s AND {id_col} = %s
                    """
                    await self.execute_query(delete_query, (initiator_inv_id, item['id']))

                # Add to target
                check_query = f"SELECT quantiter FROM {table} WHERE inventory_id = %s AND {id_col} = %s"
                existing = await self.execute_query(check_query, (target_inv_id, item['id']))

                if existing:
                    update_query = f"""
                        UPDATE {table} 
                        SET quantiter = quantiter + %s 
                        WHERE inventory_id = %s AND {id_col} = %s
                    """
                    await self.execute_query(update_query, (item['quantity'], target_inv_id, item['id']))
                else:
                    insert_query = f"""
                        INSERT INTO {table} (inventory_id, {id_col}, quantiter)
                        VALUES (%s, %s, %s)
                    """
                    await self.execute_query(insert_query, (target_inv_id, item['id'], item['quantity']))

            # Process target's items
            for item in trade_data['target_offer']:
                # Remove from target
                if item['category'] == 'boosters_enhancers':
                    table = 'inventory_product'
                    id_col = 'product_id'
                elif item['category'] == 'weapons':
                    table = 'inventory_weapon'
                    id_col = 'weapon_id'
                elif item['category'] == 'orbs':
                    table = 'inventory_orbs'
                    id_col = 'orb_id'
                elif item['category'] == 'fragments':
                    table = 'inventory_frags'
                    id_col = 'frag_id'
                else:
                    continue

                # Remove from target
                remove_query = f"""
                    UPDATE {table} 
                    SET quantiter = quantiter - %s 
                    WHERE inventory_id = %s AND {id_col} = %s
                """
                await self.execute_query(remove_query, (item['quantity'], target_inv_id, item['id']))

                # Add to initiator
                check_query = f"SELECT quantiter FROM {table} WHERE inventory_id = %s AND {id_col} = %s"
                existing = await self.execute_query(check_query, (initiator_inv_id, item['id']))

                if existing:
                    update_query = f"""
                        UPDATE {table} 
                        SET quantiter = quantiter + %s 
                        WHERE inventory_id = %s AND {id_col} = %s
                    """
                    await self.execute_query(update_query, (item['quantity'], initiator_inv_id, item['id']))
                else:
                    insert_query = f"""
                        INSERT INTO {table} (inventory_id, {id_col}, quantiter)
                        VALUES (%s, %s, %s)
                    """
                    await self.execute_query(insert_query, (initiator_inv_id, item['id'], item['quantity']))

            # Process currency exchange
            if trade_data['initiator_money'] > 0:
                await self.execute_query(
                    "UPDATE user SET won_currency = won_currency - %s WHERE user_authorid = %s",
                    (trade_data['initiator_money'], trade_data['initiator_id'])
                )
                await self.execute_query(
                    "UPDATE user SET won_currency = won_currency + %s WHERE user_authorid = %s",
                    (trade_data['initiator_money'], trade_data['target_id'])
                )

            if trade_data['target_money'] > 0:
                await self.execute_query(
                    "UPDATE user SET won_currency = won_currency - %s WHERE user_authorid = %s",
                    (trade_data['target_money'], trade_data['target_id'])
                )
                await self.execute_query(
                    "UPDATE user SET won_currency = won_currency + %s WHERE user_authorid = %s",
                    (trade_data['target_money'], trade_data['initiator_id'])
                )

            return True, "Trade completed successfully!"
        except Exception as e:
            print(f"Error processing trade: {e}")
            return False, f"Error processing trade: {str(e)}"

    async def update_trade_embed(self, trade_data):
        embed = discord.Embed(title="Cross-Trade in Progress", color=discord.Color.blue())
        
        # Add initiator's offer
        initiator_items = "\n".join([f"• {item['name']} x{item['quantity']} ({item['price']} each)" 
                                   for item in trade_data['initiator_offer']])
        if trade_data['initiator_money'] > 0:
            initiator_items += f"\n• Currency: {trade_data['initiator_money']:,}"
        embed.add_field(
            name=f"Initiator's Offer (Total: {trade_data['initiator_total']:,})",
            value=initiator_items if initiator_items else "No items offered",
            inline=False
        )

        # Add target's offer
        target_items = "\n".join([f"• {item['name']} x{item['quantity']} ({item['price']} each)" 
                                for item in trade_data['target_offer']])
        if trade_data['target_money'] > 0:
            target_items += f"\n• Currency: {trade_data['target_money']:,}"
        embed.add_field(
            name=f"Target's Offer (Total: {trade_data['target_total']:,})",
            value=target_items if target_items else "No items offered",
            inline=False
        )

        # Add status
        status = []
        if trade_data['initiator_ready']:
            status.append("Initiator: ✅")
        else:
            status.append("Initiator: ❌")
        if trade_data['target_ready']:
            status.append("Target: ✅")
        else:
            status.append("Target: ❌")

        embed.add_field(name="Status", value="\n".join(status), inline=False)
        
        # Update messages for both users
        for user_id, message_id in trade_data['message_ids'].items():
            channel = self.bot.get_channel(message_id)
            if channel:
                try:
                    message = await channel.fetch_message(message_id)
                    await message.edit(embed=embed)
                except:
                    pass

    @commands.command(name="confirm")
    async def confirm_trade(self, ctx):
        """Confirm the cross-trade"""
        print("[Cross-Trade] Confirm trade initiated")
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['connection_id'] not in self.active_crosstrades:
            print("[Cross-Trade] No active trade found")
            await ctx.send("No active cross-trade found.")
            return

        trade_data = self.active_crosstrades[connection['connection_id']]
        
        # Verify it's the target's turn to confirm
        print(f"[Cross-Trade] Current turn: {trade_data['current_turn']}")
        print(f"[Cross-Trade] Author ID: {ctx.author.id}")
        print(f"[Cross-Trade] Target ID: {trade_data['target_id']}")
        
        if str(ctx.author.id) != trade_data['target_id']:
            print("[Cross-Trade] Not target's turn to confirm")
            await ctx.send("Only the target can confirm the trade at this stage.", delete_after=5)
            return

        # Ensure both sides have locked their offers
        if not trade_data['initiator_ready']:
            print("[Cross-Trade] Initiator has not locked their offer")
            await ctx.send("The initiator must lock their offer first.", delete_after=5)
            return

        try:
            # Process the trade
            print("[Cross-Trade] Processing trade")
            success, message = await self.process_trade(trade_data)
            
            if success:
                print("[Cross-Trade] Trade completed successfully")
                await ctx.send("Cross-trade completed successfully!")
                # Remove the active trade
                del self.active_crosstrades[connection['connection_id']]
            else:
                print(f"[Cross-Trade] Trade failed: {message}")
                await ctx.send(f"Cross-trade failed: {message}")
        except Exception as e:
            print(f"[Cross-Trade] Error during trade: {str(e)}")
            await ctx.send(f"An error occurred during trade: {str(e)}")

        await ctx.message.delete()
    async def update_quest_progress(self, cursor, user_id):
        """Helper function to update trade-related quest progress"""
        await cursor.execute("""
            UPDATE user_quests uq 
            JOIN quest q ON uq.quest_id = q.quest_id 
            SET uq.current_count = uq.current_count + 1,
                uq.completion_status = CASE 
                    WHEN uq.current_count + 1 >= q.required_count THEN 1
                    ELSE 0
                END
            WHERE uq.user_id = %s 
            AND q.quest_name = 'Trade 3 items'
            AND uq.completion_status = 0
        """, (user_id,))
        
        # If quest was completed, add reward chest
        if cursor.rowcount > 0:
            await cursor.execute("""
                SELECT inventory_id 
                FROM inventory 
                WHERE user_id = %s
            """, (user_id,))
            inventory = await cursor.fetchone()
            
            if inventory:
                await cursor.execute("""
                    INSERT INTO inventory_chest (inventory_id, chest_id, quantiter)
                    VALUES (%s, 1, 1)
                    ON DUPLICATE KEY UPDATE quantiter = quantiter + 1
                """, (inventory[0],))
    @commands.command(aliases=['cia'])
    async def crossitemadd(self, ctx, *, item_info):
        """Add an item to your cross-trade offer"""
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['connection_id'] not in self.active_crosstrades:
            return
            
        trade_data = self.active_crosstrades[connection['connection_id']]
        
        if trade_data['current_turn'] != str(ctx.author.id):
            await ctx.send("It's not your turn to add items.", delete_after=5)
            return

        # Parse item info
        try:
            item_name, quantity = item_info.rsplit(',', 1)
            item_name = item_name.strip()
            quantity = int(quantity.strip())
        except ValueError:
            await ctx.send("Invalid format. Use: sl ciadd item_name, quantity", delete_after=5)
            return

        # Determine user role
        is_initiator = str(ctx.author.id) == trade_data['initiator_id']
        offer_list = trade_data['initiator_offer'] if is_initiator else trade_data['target_offer']
        total_field = 'initiator_total' if is_initiator else 'target_total'

        # Comprehensive item search across all tables
        item_data = None
        for category, (inv_table, item_table, name_col, id_col, inv_id_col) in self.item_tables.items():
            query = f"""
            SELECT {item_table}.{id_col}, {item_table}.price, 
                {inv_table}.quantiter as inventory_quantity
            FROM {item_table}
            JOIN user u ON 1=1
            JOIN inventory i ON i.user_id = u.user_id
            JOIN {inv_table} ON {inv_table}.inventory_id = i.inventory_id AND {inv_table}.{inv_id_col} = {item_table}.{id_col}
            WHERE u.user_authorid = %s AND {item_table}.{name_col} LIKE %s
            """
            result = await self.execute_query(query, (str(ctx.author.id), f'%{item_name}%'))
            
            if result:
                item_data = result[0]
                item_data['category'] = category
                item_data['inv_table'] = inv_table
                item_data['inv_id_col'] = inv_id_col
                item_data['name_col'] = name_col
                break

        if not item_data:
            await ctx.send(f"The item '{item_name}' doesn't exist or you don't have it.", delete_after=5)
            return

        # Validate quantity
        if item_data['inventory_quantity'] < quantity:
            await ctx.send(f"You don't have enough of '{item_name}'. You have {item_data['inventory_quantity']}.", delete_after=5)
            return

        # Update offer and totals
        offer_list.append({
            'name': item_name,
            'quantity': quantity,
            'id': item_data[id_col],
            'price': item_data['price'],
            'category': item_data['category']
        })
        trade_data[total_field] += item_data['price'] * quantity
        
        await self.update_trade_messages(trade_data)
        await ctx.message.delete()

    @commands.command(aliases=['cir'])
    async def crossitemremove(self, ctx, *, item_name):
        """Remove an item from your cross-trade offer"""
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['connection_id'] not in self.active_crosstrades:
            return
            
        trade_data = self.active_crosstrades[connection['connection_id']]
        
        if trade_data['current_turn'] != str(ctx.author.id):
            await ctx.send("It's not your turn to modify items.", delete_after=5)
            return

        # Determine user role
        is_initiator = str(ctx.author.id) == trade_data['initiator_id']
        offer_list = trade_data['initiator_offer'] if is_initiator else trade_data['target_offer']
        total_field = 'initiator_total' if is_initiator else 'target_total'

        # Find and remove the item (case-insensitive partial match)
        removed_item = None
        for item in offer_list[:]:
            if item_name.lower() in item['name'].lower():
                removed_item = item
                offer_list.remove(item)
                trade_data[total_field] -= item['price'] * item['quantity']
                break

        if not removed_item:
            await ctx.send(f"Could not find '{item_name}' in your offer.", delete_after=5)
            return

        await self.update_trade_messages(trade_data)
        await ctx.message.delete()


    @commands.command(aliases=['cwa'])
    async def crosswonadd(self, ctx, amount: int):
        """Add currency to your cross-trade offer"""
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['connection_id'] not in self.active_crosstrades:
            return
            
        trade_data = self.active_crosstrades[connection['connection_id']]
        
        if trade_data['current_turn'] != str(ctx.author.id):
            await ctx.send("It's not your turn to add currency.", delete_after=5)
            return

        # Determine user role
        is_initiator = str(ctx.author.id) == trade_data['initiator_id']
        money_field = 'initiator_money' if is_initiator else 'target_money'
        total_field = 'initiator_total' if is_initiator else 'target_total'

        # Check if user has enough currency
        user_id = str(ctx.author.id)
        userd = "SELECT user_id FROM user WHERE user_authorid = %s"
        userdr = await self.execute_query(userd, (user_id,))
        user_id = userdr[0]['user_id']

        query = "SELECT won_currency FROM user WHERE user_id = %s"
        result = await self.execute_query(query, (user_id,))
        
        if not result or result[0]['won_currency'] < amount:
            await ctx.send("You don't have enough currency.", delete_after=5)
            return

        # Update trade data - add to existing amount instead of replacing
        existing_amount = trade_data.get(money_field, 0)
        new_total_amount = existing_amount + amount
        trade_data[money_field] = new_total_amount
        
        # Adjust total by the difference
        trade_data[total_field] = trade_data[total_field] - existing_amount + new_total_amount

        await self.update_trade_messages(trade_data)
        await ctx.message.delete()
    @commands.command(aliases=['cwr'])
    async def crosswonremove(self, ctx, amount: Optional[int] = None):
        """Remove currency from your cross-trade offer"""
        connection = await self.get_connection_details(str(ctx.author.id))
        if not connection or connection['connection_id'] not in self.active_crosstrades:
            return
            
        trade_data = self.active_crosstrades[connection['connection_id']]
        
        if trade_data['current_turn'] != str(ctx.author.id):
            await ctx.send("It's not your turn to modify currency.", delete_after=5)
            return

        # Determine user role
        is_initiator = str(ctx.author.id) == trade_data['initiator_id']
        money_field = 'initiator_money' if is_initiator else 'target_money'
        total_field = 'initiator_total' if is_initiator else 'target_total'

        # Get current money amount
        current_money = trade_data.get(money_field, 0)

        # If no amount specified, remove all money
        if amount is None:
            trade_data[total_field] -= current_money
            trade_data[money_field] = 0
        else:
            # Check if trying to remove more than available
            if amount > current_money:
                await ctx.send(f"You can only remove up to {current_money} currency.", delete_after=5)
                return

            # Remove specified amount
            trade_data[money_field] -= amount
            trade_data[total_field] -= amount

        await self.update_trade_messages(trade_data)
        await ctx.message.delete()

async def setup(bot):
    await bot.add_cog(UserphoneCommands(bot))
