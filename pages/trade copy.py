import discord
from discord.ext import commands
import asyncio
import time
from datetime import datetime, timedelta
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

# Use Unicode emojis
EMOJI_TRADE = "🔄"
EMOJI_LOCK = "🔒"
EMOJI_UNLOCK = "🔓"
EMOJI_CANCEL = "❌"
EMOJI_CONFIRM = "✅"
EMOJI_HOURGLASS = "⏳"
EMOJI_MONEY = "💰"
EMOJI_WARNING = "⚠️"

# Timeout constants
TRADE_INVITATION_TIMEOUT = 30  # seconds
TRADE_SESSION_TIMEOUT = 5 * 60  # 5 minutes in seconds

# Trade log channel constants
TRADE_LOG_SERVER_ID = 832022150713704479
TRADE_LOG_CHANNEL_ID = 1370415867463995464
TRADE_LOG_ROLE_ID = 1370416075136831608

class TradeCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trade_views = {}  # Store active trade views (not users)
        self.item_tables = {
            'weapons': ('inventory_weapon', 'weapon', 'weapon_name', 'weapon_id', 'weapon_id'),
            'orbs': ('inventory_orbs', 'leveling_orbs', 'orb_name', 'orb_id', 'orb_id'),
            'boosters_enhancers': ('inventory_product', 'product', 'product_name', 'product_id', 'product_id'),
            'fragments': ('inventory_frags', 'fragments', 'name_fragment', 'id_fragment', 'frag_id'),
            'dungeon_keys' : ('inventory_dungeonkey','dungeon_key','dungeonkey_name','dungeonkey_id','dungeonkey_id'),
            'pickaxe': ('inventory_pickaxe','pickaxe','pickaxe_name','id_pickaxe','id_pickaxe')
        }
        self.timeout_task = None  # Initialize as None, will start in cog_load

    async def cog_unload(self):
        """Clean up resources when the cog is unloaded"""
        if self.timeout_task:
            self.timeout_task.cancel()
    
    async def cog_load(self):
        """Called when the cog is loaded. This is an async context where we can safely start tasks."""
        self.timeout_task = asyncio.create_task(self.check_trade_timeouts())
    
    async def check_trade_timeouts(self):
        """Background task to check for trade timeouts"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # Get current time
                current_time = datetime.now()
                
                # Check for trades that have timed out
                query = """
                SELECT ts.id_tradestatus, ts.initiator_id, ts.target_id, ts.created_at, ts.status
                FROM trade_status ts
                WHERE 
                    (ts.status = 'pending' AND ts.created_at < %s) OR
                    (ts.status = 'active' AND ts.created_at < %s)
                """
                
                # Calculate timeout thresholds
                invitation_timeout = current_time - timedelta(seconds=TRADE_INVITATION_TIMEOUT)
                session_timeout = current_time - timedelta(seconds=TRADE_SESSION_TIMEOUT)
                
                results = await execute_query_with_retry(query, (invitation_timeout, session_timeout))
                
                if results:
                    for trade in results:
                        trade_id = trade['id_tradestatus']
                        status = trade['status']
                        
                        # Delete the timed out trade
                        delete_query = "DELETE FROM trade_status WHERE id_tradestatus = %s"
                        await execute_query_with_retry(delete_query, (trade_id,))
                        
                        # If we have a view for this trade, cancel it
                        view_key = f"{trade['initiator_id']}_{trade['target_id']}"
                        if view_key in self.trade_views:
                            view = self.trade_views[view_key]
                            
                            # Create timeout embed
                            timeout_embed = discord.Embed(
                                title=f"{EMOJI_CANCEL} Trade Timed Out", 
                                description=f"The trade has been automatically cancelled due to inactivity.", 
                                color=discord.Color.red()
                            )
                            
                            # Update the message
                            try:
                                await view.message.edit(content=None, embed=timeout_embed, view=None)
                            except:
                                pass  # Message might be deleted or inaccessible
                            
                            # Stop the view and remove it
                            view.stop()
                            del self.trade_views[view_key]
                            
                            print(f"Trade {trade_id} timed out and was cancelled.")
                            
                            # Log the timeout to the trade log channel
                            await self.log_trade_event(
                                "Trade Timed Out", 
                                f"Trade ID: {trade_id} timed out and was cancelled due to inactivity.",
                                discord.Color.red(),
                                is_error=True
                            )
            
            except Exception as e:
                print(f"Error in trade timeout checker: {e}")
                # Log the error to the trade log channel
                await self.log_trade_event(
                    "Trade System Error", 
                    f"Error in trade timeout checker: {e}",
                    discord.Color.red(),
                    is_error=True
                )
            
            # Check every 10 seconds
            await asyncio.sleep(10)
    
    async def log_trade_event(self, title, description, color=discord.Color.blue(), is_error=False, trade_data=None):
        """
        Log a trade event to the designated channel
        
        Parameters:
        - title: The title of the embed
        - description: The description of the embed
        - color: The color of the embed
        - is_error: Whether this is an error message (will ping the role)
        - trade_data: Optional dictionary containing trade details
        """
        try:
            # Get the server and channel
            server = self.bot.get_guild(TRADE_LOG_SERVER_ID)
            if not server:
                print(f"Could not find server with ID {TRADE_LOG_SERVER_ID}")
                return
                
            channel = server.get_channel(TRADE_LOG_CHANNEL_ID)
            if not channel:
                print(f"Could not find channel with ID {TRADE_LOG_CHANNEL_ID}")
                return
            
            # Create the embed
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=datetime.now()
            )
            
            # Add trade data if provided
            if trade_data:
                # Add initiator details
                if 'initiator' in trade_data:
                    initiator_info = trade_data['initiator']
                    embed.add_field(
                        name="Initiator",
                        value=f"ID: {initiator_info.get('id')}\n"
                              f"Name: {initiator_info.get('name')}\n"
                              f"Balance: {initiator_info.get('balance', 0):,}",
                        inline=True
                    )
                
                # Add target details
                if 'target' in trade_data:
                    target_info = trade_data['target']
                    embed.add_field(
                        name="Target",
                        value=f"ID: {target_info.get('id')}\n"
                              f"Name: {target_info.get('name')}\n"
                              f"Balance: {target_info.get('balance', 0):,}",
                        inline=True
                    )
                
                # Add items exchanged
                if 'initiator_items' in trade_data:
                    embed.add_field(
                        name="Items from Initiator",
                        value=trade_data['initiator_items'] or "None",
                        inline=False
                    )
                
                if 'target_items' in trade_data:
                    embed.add_field(
                        name="Items from Target",
                        value=trade_data['target_items'] or "None",
                        inline=False
                    )
                
                # Add currency exchanged
                if 'initiator_currency' in trade_data or 'target_currency' in trade_data:
                    currency_info = []
                    if 'initiator_currency' in trade_data and trade_data['initiator_currency'] > 0:
                        currency_info.append(f"Initiator → Target: {trade_data['initiator_currency']:,}")
                    
                    if 'target_currency' in trade_data and trade_data['target_currency'] > 0:
                        currency_info.append(f"Target → Initiator: {trade_data['target_currency']:,}")
                    
                    if currency_info:
                        embed.add_field(
                            name="Currency Exchanged",
                            value="\n".join(currency_info),
                            inline=False
                        )
            
            # Create the content with role ping if this is an error
            content = None
            if is_error:
                content = f"<@&{TRADE_LOG_ROLE_ID}> Trade System Alert!"
            
            # Send the message
            await channel.send(content=content, embed=embed)
            
        except Exception as e:
            print(f"Error logging trade event: {e}")
    
    async def is_user_in_trade(self, user_id):
        """Check if a user is already in a trade"""
        query = """
        SELECT id_tradestatus FROM trade_status 
        WHERE initiator_id = %s OR target_id = %s
        """
        result = await execute_query_with_retry(query, (user_id, user_id))
        return bool(result)
    
    async def create_trade_entry(self, initiator_id, target_id):
        """Create a new trade entry in the database"""
        # First, check if either user is already in a trade
        if await self.is_user_in_trade(initiator_id) or await self.is_user_in_trade(target_id):
            return None
        
        # Create a new trade entry
        query = """
        INSERT INTO trade_status (initiator_id, target_id, status, created_at) 
        VALUES (%s, %s, 'pending', %s)
        """
        current_time = datetime.now()
        await execute_query_with_retry(query, (initiator_id, target_id, current_time))
        
        # Get the ID of the newly created trade
        get_id_query = """
        SELECT id_tradestatus FROM trade_status 
        WHERE initiator_id = %s AND target_id = %s
        ORDER BY created_at DESC LIMIT 1
        """
        result = await execute_query_with_retry(get_id_query, (initiator_id, target_id))
        
        if result:
            return result[0]['id_tradestatus']
        return None
    
    async def update_trade_status(self, trade_id, status):
        """Update the status of a trade"""
        if status == 'delete':
            # Delete the trade entry
            query = "DELETE FROM trade_status WHERE id_tradestatus = %s"
            await execute_query_with_retry(query, (trade_id,))
        else:
            # Update the trade status
            query = """
            UPDATE trade_status SET status = %s, created_at = %s
            WHERE id_tradestatus = %s
            """
            current_time = datetime.now()
            await execute_query_with_retry(query, (status, current_time, trade_id))
        
        return True

    class ConfirmButton(discord.ui.Button):
        def __init__(self, label="Confirm", style=discord.ButtonStyle.green, emoji=EMOJI_CONFIRM):
            super().__init__(label=label, style=style, emoji=emoji)
        
        async def callback(self, interaction: discord.Interaction):
            if interaction.user != self.view.expected_user:
                await interaction.response.send_message(f"{EMOJI_CANCEL} This confirmation is not for you.", ephemeral=True)
                return
            
            self.view.value = True
            self.view.stop()
            await interaction.response.defer()

    class CancelButton(discord.ui.Button):
        def __init__(self, label="Cancel", style=discord.ButtonStyle.red, emoji=EMOJI_CANCEL):
            super().__init__(label=label, style=style, emoji=emoji)
        
        async def callback(self, interaction: discord.Interaction):
            if interaction.user != self.view.expected_user:
                await interaction.response.send_message(f"{EMOJI_CANCEL} This confirmation is not for you.", ephemeral=True)
                return
            
            self.view.value = False
            self.view.stop()
            await interaction.response.defer()

    class ConfirmationView(discord.ui.View):
        def __init__(self, user):
            super().__init__(timeout=60)
            self.expected_user = user
            self.value = None
            self.add_item(TradeCommands.ConfirmButton())
            self.add_item(TradeCommands.CancelButton())

    class TradeView(discord.ui.View):
        def __init__(self, cog, ctx, target, initiator_id, target_id, initiator_inventory_id, target_inventory_id, trade_id):
            super().__init__(timeout=TRADE_SESSION_TIMEOUT)  # Use the constant for timeout
            self.cog = cog
            self.ctx = ctx
            self.target = target
            self.initiator_id = initiator_id
            self.target_id = target_id
            self.initiator_inventory_id = initiator_inventory_id
            self.target_inventory_id = target_inventory_id
            self.trade_id = trade_id
            self.initiator_offer = []
            self.target_offer = []
            self.initiator_ready = False
            self.target_ready = False
            self.initiator_confirmed = False
            self.target_confirmed = False
            self.current_turn = ctx.author
            self.trade_state = "waiting_acceptance"
            self.initiator_money = 0
            self.target_money = 0
            self.initiator_overall_price = 0
            self.target_overall_price = 0
            self.message = None
            
            # Track available currency for each user
            self.initiator_available_currency = 0
            self.target_available_currency = 0
        
        async def on_timeout(self):
            """Handle view timeout"""
            # Create timeout embed
            timeout_embed = discord.Embed(
                title=f"{EMOJI_CANCEL} Trade Timed Out", 
                description=f"The trade has been automatically cancelled due to inactivity.", 
                color=discord.Color.red()
            )
            
            # Update the message
            try:
                await self.message.edit(content=None, embed=timeout_embed, view=None)
            except:
                pass  # Message might be deleted or inaccessible
            
            # Delete the trade from the database
            await self.cog.update_trade_status(self.trade_id, 'delete')
            
            # Remove the view from the cog's dictionary
            view_key = f"{self.initiator_id}_{self.target_id}"
            if view_key in self.cog.trade_views:
                del self.cog.trade_views[view_key]
            
            # Log the timeout to the trade log channel
            await self.cog.log_trade_event(
                "Trade Timed Out", 
                f"Trade between {self.ctx.author.name} and {self.target.name} timed out due to inactivity.",
                discord.Color.red(),
                is_error=True
            )

        @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.green, emoji=EMOJI_TRADE)
        async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user == self.target and self.trade_state == "waiting_acceptance":
                await interaction.response.defer()
                self.trade_state = "initiator_adding"
                
                # Update trade status in database
                await self.cog.update_trade_status(self.trade_id, 'active')
                
                # Fetch available currency for both users
                # Get both users' currency in a single query for efficiency
                query = """
                SELECT 
                    (SELECT won_currency FROM user WHERE user_id = %s) as initiator_currency,
                    (SELECT won_currency FROM user WHERE user_id = %s) as target_currency
                """
                result = await execute_query_with_retry(query, (self.initiator_id, self.target_id))
                if result:
                    self.initiator_available_currency = result[0]['initiator_currency'] or 0
                    self.target_available_currency = result[0]['target_currency'] or 0
                
                self.clear_items()
                self.add_item(discord.ui.Button(label="Lock Offer", style=discord.ButtonStyle.primary, emoji=EMOJI_LOCK, custom_id="lock"))
                self.add_item(discord.ui.Button(label="Cancel Trade", style=discord.ButtonStyle.red, emoji=EMOJI_CANCEL, custom_id="cancel"))
                await self.update_trade_embed(f"{EMOJI_TRADE} {self.ctx.author.mention}, it's your turn to add items to the trade!")

        @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.red, emoji=EMOJI_CANCEL)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.handle_trade_cancel(interaction)

        async def handle_trade_cancel(self, interaction: discord.Interaction):
            """
            Handle trade cancellation with comprehensive checks and updates
            """
            # Check if the cancelling user is part of the trade
            if interaction.user not in [self.ctx.author, self.target]:
                await interaction.response.send_message(
                    f"{EMOJI_CANCEL} You are not part of this trade.", 
                    ephemeral=True
                )
                return

            # Check if cancellation is allowed based on current trade state
            is_cancellable = (
                interaction.user == self.current_turn or 
                self.trade_state == "waiting_acceptance"
            )

            if not is_cancellable:
                await interaction.response.send_message(
                    f"{EMOJI_CANCEL} You can only cancel during your turn.", 
                    ephemeral=True
                )
                return

            # Create cancellation embed
            cancel_embed = discord.Embed(
                title=f"{EMOJI_CANCEL} Trade Cancelled", 
                description=f"{interaction.user.mention} has cancelled the trade.", 
                color=discord.Color.red()
            )

            # Add user's avatar if available
            if interaction.user.avatar:
                cancel_embed.set_thumbnail(url=interaction.user.avatar.url)

            # Update the message to show only the cancellation embed
            await self.message.edit(
                content=None,  # Remove any previous content 
                embed=cancel_embed, 
                view=None
            )

            # Stop the view to prevent further interactions
            self.stop()

            # Delete the trade from the database
            await self.cog.update_trade_status(self.trade_id, 'delete')
            
            # Remove the view from the cog's dictionary
            view_key = f"{self.initiator_id}_{self.target_id}"
            if view_key in self.cog.trade_views:
                del self.cog.trade_views[view_key]
            
            # Log the cancellation to the trade log channel
            await self.cog.log_trade_event(
                "Trade Cancelled", 
                f"Trade between {self.ctx.author.name} and {self.target.name} was cancelled by {interaction.user.name}.",
                discord.Color.orange()
            )

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            # First, check if the interaction is from a trade participant
            if interaction.user not in [self.ctx.author, self.target]:
                await interaction.response.send_message(
                    f"{EMOJI_CANCEL} You are not part of this trade.", 
                    ephemeral=True
                )
                return False

            if interaction.data.get("custom_id") == "cancel":
                await self.handle_trade_cancel(interaction)
                return False

            # If it's the lock button
            if interaction.data.get("custom_id") == "lock":
                if interaction.user == self.current_turn:
                    if self.trade_state == "initiator_adding" and interaction.user == self.ctx.author:
                        self.initiator_ready = True
                        self.current_turn = self.target
                        self.trade_state = "target_adding"
                        await self.update_trade_embed(f"{EMOJI_TRADE} {self.target.mention}, it's your turn to add items to the trade!")
                    elif self.trade_state == "target_adding" and interaction.user == self.target:
                        # Check if both users only offered currency
                        initiator_only_currency = len(self.initiator_offer) == 0 and self.initiator_money > 0
                        target_only_currency = len(self.target_offer) == 0 and self.target_money > 0
                        
                        if initiator_only_currency and target_only_currency:
                            await interaction.response.send_message(
                                f"{EMOJI_CANCEL} Trades cannot consist of only currency. At least one item must be included in the trade.", 
                                ephemeral=True
                            )
                            return True
                        
                        price_ratio = self.target_overall_price / self.initiator_overall_price if self.initiator_overall_price else 0
                        if 0.8 <= price_ratio <= 1.2:
                            self.target_ready = True
                            self.trade_state = "confirming"
                            
                            # Update the buttons to show Confirm buttons for both users
                            self.clear_items()
                            self.add_item(discord.ui.Button(label="Confirm Trade", style=discord.ButtonStyle.success, emoji=EMOJI_CONFIRM, custom_id="confirm"))
                            self.add_item(discord.ui.Button(label="Cancel Trade", style=discord.ButtonStyle.red, emoji=EMOJI_CANCEL, custom_id="cancel"))
                            
                            await self.update_trade_embed(f"{EMOJI_CONFIRM} Both users need to confirm the trade. {self.target.mention} and {self.ctx.author.mention}, please click 'Confirm Trade' to proceed.")
                        elif price_ratio < 0.8:
                            await interaction.response.send_message(
                                f"{EMOJI_WARNING} Your offer is less than 80% of the initiator's offer. Please add more items or currency to reach at least {self.initiator_overall_price * 0.8:.2f}.", 
                                ephemeral=True
                            )
                        else:
                            await interaction.response.send_message(
                                f"{EMOJI_WARNING} Your offer is more than 120% of the initiator's offer. Please remove some items or currency to be at most {self.initiator_overall_price * 1.2:.2f}.", 
                                ephemeral=True
                            )
                    await interaction.response.defer()
                    return True
            
            # If it's the confirm button
            if interaction.data.get("custom_id") == "confirm":
                if self.trade_state == "confirming":
                    if interaction.user == self.ctx.author:
                        self.initiator_confirmed = True
                        await interaction.response.send_message(f"{EMOJI_CONFIRM} You have confirmed the trade.", ephemeral=True)
                        await self.update_trade_embed()
                    elif interaction.user == self.target:
                        self.target_confirmed = True
                        await interaction.response.send_message(f"{EMOJI_CONFIRM} You have confirmed the trade.", ephemeral=True)
                        await self.update_trade_embed()
                    
                    # Check if both users have confirmed
                    if self.initiator_confirmed and self.target_confirmed:
                        # Both confirmed, process the trade
                        await self.process_trade()
                        return False
                    
                    return True
                
            return interaction.user in [self.ctx.author, self.target]
        
        async def validate_trade_items(self):
            """Validate all items and currency in the trade before processing"""
            validation_errors = []
            
            # 1. Validate initiator's items
            for item in self.initiator_offer:
                inv_table, item_table, name_col, id_col, inv_id_col = self.cog.item_tables[item['category']]
                
                # Check if item exists in database
                query = f"SELECT {id_col} FROM {item_table} WHERE {id_col} = %s"
                result = await execute_query_with_retry(query, (item['id'],))
                if not result:
                    validation_errors.append(f"Initiator's item {item['name']} (ID: {item['id']}) does not exist in database")
                    continue
                    
                # Check if initiator has enough quantity
                query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                result = await execute_query_with_retry(query, (self.initiator_inventory_id, item['id']))
                
                if not result or result[0]['quantiter'] < item['quantity']:
                    available = result[0]['quantiter'] if result else 0
                    validation_errors.append(f"Initiator doesn't have enough {item['name']} (has: {available}, needs: {item['quantity']})")
            
            # 2. Validate target's items
            for item in self.target_offer:
                inv_table, item_table, name_col, id_col, inv_id_col = self.cog.item_tables[item['category']]
                
                # Check if item exists in database
                query = f"SELECT {id_col} FROM {item_table} WHERE {id_col} = %s"
                result = await execute_query_with_retry(query, (item['id'],))
                if not result:
                    validation_errors.append(f"Target's item {item['name']} (ID: {item['id']}) does not exist in database")
                    continue
                    
                # Check if target has enough quantity
                query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                result = await execute_query_with_retry(query, (self.target_inventory_id, item['id']))
                
                if not result or result[0]['quantiter'] < item['quantity']:
                    available = result[0]['quantiter'] if result else 0
                    validation_errors.append(f"Target doesn't have enough {item['name']} (has: {available}, needs: {item['quantity']})")
            
            # 3. Validate currency
            if self.initiator_money > 0:
                query = "SELECT won_currency FROM user WHERE user_id = %s"
                result = await execute_query_with_retry(query, (self.initiator_id,))
                
                if not result or result[0]['won_currency'] < self.initiator_money:
                    available = result[0]['won_currency'] if result else 0
                    validation_errors.append(f"Initiator doesn't have enough currency (has: {available}, offering: {self.initiator_money})")
            
            if self.target_money > 0:
                query = "SELECT won_currency FROM user WHERE user_id = %s"
                result = await execute_query_with_retry(query, (self.target_id,))
                
                if not result or result[0]['won_currency'] < self.target_money:
                    available = result[0]['won_currency'] if result else 0
                    validation_errors.append(f"Target doesn't have enough currency (has: {available}, offering: {self.target_money})")
            
            # If there are validation errors, show them and return False
            if validation_errors:
                error_embed = discord.Embed(
                    title=f"{EMOJI_WARNING} Trade Validation Failed",
                    description="The trade cannot be completed due to the following issues:",
                    color=discord.Color.red()
                )
                
                error_list = "\n".join([f"• {error}" for error in validation_errors])
                error_embed.add_field(name="Issues", value=error_list, inline=False)
                
                await self.message.edit(content=None, embed=error_embed, view=None)
                self.stop()
                
                # Delete the trade from the database
                await self.cog.update_trade_status(self.trade_id, 'delete')
                
                # Remove the view from the cog's dictionary
                view_key = f"{self.initiator_id}_{self.target_id}"
                if view_key in self.cog.trade_views:
                    del self.cog.trade_views[view_key]
                
                # Log the validation failure to the trade log channel
                await self.cog.log_trade_event(
                    "Trade Validation Failed", 
                    f"Trade between {self.ctx.author.name} and {self.target.name} failed validation.",
                    discord.Color.red(),
                    is_error=True,
                    trade_data={
                        'initiator': {'name': self.ctx.author.name, 'id': self.initiator_id},
                        'target': {'name': self.target.name, 'id': self.target_id},
                        'validation_errors': error_list
                    }
                )
                    
                return False
            
            return True
        
        async def process_trade(self):
            try:
                # VALIDATION STEP: Check all items and currency before proceeding
                validation_passed = await self.validate_trade_items()
                if not validation_passed:
                    # If validation fails, stop the trade process
                    return
                
                # Create a flag to track if the trade was successful
                trade_successful = True
                
                # Get current balances for logging
                initiator_balance_before = 0
                target_balance_before = 0
                
                query = "SELECT won_currency FROM user WHERE user_id = %s"
                result = await execute_query_with_retry(query, (self.initiator_id,))
                if result:
                    initiator_balance_before = result[0]['won_currency'] or 0
                
                result = await execute_query_with_retry(query, (self.target_id,))
                if result:
                    target_balance_before = result[0]['won_currency'] or 0
                
                try:
                    # Process initiator's items - REMOVE from initiator's inventory
                    for item in self.initiator_offer:
                        inv_table, _, _, id_col, inv_id_col = self.cog.item_tables[item['category']]
                        query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                        result = await execute_query_with_retry(query, (self.initiator_inventory_id, item['id']))
                        
                        if result:
                            current_quantity = result[0]['quantiter']
                            if current_quantity == item['quantity']:
                                query = f"DELETE FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                                await execute_query_with_retry(query, (self.initiator_inventory_id, item['id']))
                            else:
                                query = f"UPDATE {inv_table} SET quantiter = quantiter - %s WHERE inventory_id = %s AND {inv_id_col} = %s"
                                await execute_query_with_retry(query, (item['quantity'], self.initiator_inventory_id, item['id']))
                    
                    # Process target's items - REMOVE from target's inventory
                    for item in self.target_offer:
                        inv_table, _, _, id_col, inv_id_col = self.cog.item_tables[item['category']]
                        query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                        result = await execute_query_with_retry(query, (self.target_inventory_id, item['id']))
                        
                        if result:
                            current_quantity = result[0]['quantiter']
                            if current_quantity == item['quantity']:
                                query = f"DELETE FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                                await execute_query_with_retry(query, (self.target_inventory_id, item['id']))
                            else:
                                query = f"UPDATE {inv_table} SET quantiter = quantiter - %s WHERE inventory_id = %s AND {inv_id_col} = %s"
                                await execute_query_with_retry(query, (item['quantity'], self.target_inventory_id, item['id']))
                    
                    # Process currency exchange
                    if self.initiator_money > 0:
                        query = "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s"
                        await execute_query_with_retry(query, (self.initiator_money, self.initiator_id))
                        
                        query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                        await execute_query_with_retry(query, (self.initiator_money, self.target_id))
                    
                    if self.target_money > 0:
                        query = "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s"
                        await execute_query_with_retry(query, (self.target_money, self.target_id))
                        
                        query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                        await execute_query_with_retry(query, (self.target_money, self.initiator_id))
                    
                    # Transfer items from initiator to target - ADD to target's inventory
                    for item in self.initiator_offer:
                        inv_table, _, _, id_col, inv_id_col = self.cog.item_tables[item['category']]
                        query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                        result = await execute_query_with_retry(query, (self.target_inventory_id, item['id']))
                        
                        if result:
                            query = f"UPDATE {inv_table} SET quantiter = quantiter + %s WHERE inventory_id = %s AND {inv_id_col} = %s"
                            await execute_query_with_retry(query, (item['quantity'], self.target_inventory_id, item['id']))
                        else:
                            query = f"INSERT INTO {inv_table} (inventory_id, {inv_id_col}, quantiter) VALUES (%s, %s, %s)"
                            await execute_query_with_retry(query, (self.target_inventory_id, item['id'], item['quantity']))
                    
                    # Transfer items from target to initiator - ADD to initiator's inventory
                    for item in self.target_offer:
                        inv_table, _, _, id_col, inv_id_col = self.cog.item_tables[item['category']]
                        query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                        result = await execute_query_with_retry(query, (self.initiator_inventory_id, item['id']))
                        
                        if result:
                            query = f"UPDATE {inv_table} SET quantiter = quantiter + %s WHERE inventory_id = %s AND {inv_id_col} = %s"
                            await execute_query_with_retry(query, (item['quantity'], self.initiator_inventory_id, item['id']))
                        else:
                            query = f"INSERT INTO {inv_table} (inventory_id, {inv_id_col}, quantiter) VALUES (%s, %s, %s)"
                            await execute_query_with_retry(query, (self.initiator_inventory_id, item['id'], item['quantity']))
                    
                    # Process quest progress for both users
                    for user_id, inventory_id in [(self.initiator_id, self.initiator_inventory_id), (self.target_id, self.target_inventory_id)]:
                        # Check if the user has any active quests
                        quest_query = """
                        SELECT uq.userquests_id, uq.quest_id, uq.current_count, uq.completion_status, 
                            q.quest_name, q.required_count 
                        FROM user_quests uq 
                        JOIN quest q ON uq.quest_id = q.quest_id 
                        WHERE uq.user_id = %s AND q.quest_name = 'Trade 3 items' AND uq.completion_status = 0
                        """
                        result = await execute_query_with_retry(quest_query, (user_id,))
                        
                        if result:
                            quest = result[0]
                            new_count = quest['current_count'] + 1
                            
                            # Update the current_count
                            query = "UPDATE user_quests SET current_count = %s WHERE userquests_id = %s"
                            await execute_query_with_retry(query, (new_count, quest['userquests_id']))
                            
                            # Check if the quest is completed
                            if new_count >= quest['required_count']:
                                # Mark quest as completed
                                query = "UPDATE user_quests SET completion_status = 1 WHERE userquests_id = %s"
                                await execute_query_with_retry(query, (quest['userquests_id'],))
                                
                                # Use UPSERT pattern for chest
                                query = """
                                INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) 
                                VALUES (%s, %s, 1)
                                ON DUPLICATE KEY UPDATE quantiter = quantiter + 1
                                """
                                await execute_query_with_retry(query, (inventory_id, 1))  # Assuming chest_id is 1
                    
                    # Get updated balances for logging
                    initiator_balance_after = 0
                    target_balance_after = 0
                    
                    query = "SELECT won_currency FROM user WHERE user_id = %s"
                    result = await execute_query_with_retry(query, (self.initiator_id,))
                    if result:
                        initiator_balance_after = result[0]['won_currency'] or 0
                    
                    result = await execute_query_with_retry(query, (self.target_id,))
                    if result:
                        target_balance_after = result[0]['won_currency'] or 0
                    
                except Exception as e:
                    trade_successful = False
                    print(f"Error processing trade: {e}")
                    await self.message.edit(
                        content=f"{EMOJI_CANCEL} An error occurred while processing the trade: {str(e)}", 
                        embed=None, 
                        view=None
                    )
                    self.stop()
                    
                    # Delete the trade from the database on error
                    await self.cog.update_trade_status(self.trade_id, 'delete')
                    
                    # Remove the view from the cog's dictionary
                    view_key = f"{self.initiator_id}_{self.target_id}"
                    if view_key in self.cog.trade_views:
                        del self.cog.trade_views[view_key]
                    
                    # Log the error to the trade log channel
                    await self.cog.log_trade_event(
                        "Trade Processing Error", 
                        f"Error processing trade between {self.ctx.author.name} and {self.target.name}: {str(e)}",
                        discord.Color.red(),
                        is_error=True,
                        trade_data={
                            'initiator': {'name': self.ctx.author.name, 'id': self.initiator_id},
                            'target': {'name': self.target.name, 'id': self.target_id}
                        }
                    )
                
                # If the trade was successful, show the success message
                if trade_successful:
                    # Create success embed
                    success_embed = discord.Embed(
                        title=f"{EMOJI_TRADE} Trade Completed Successfully!",
                        description="Both parties have confirmed the trade and all items have been exchanged.",
                        color=discord.Color.green()
                    )
                    
                    # Add trade details to success embed
                    success_embed.add_field(
                        name=f"{self.ctx.author.name} Received:",
                        value=self.format_offer(self.target_offer, self.target_money),
                        inline=False
                    )
                    success_embed.add_field(
                        name=f"{self.target.name} Received:",
                        value=self.format_offer(self.initiator_offer, self.initiator_money),
                        inline=False
                    )
                    
                    # Add timestamp
                    success_embed.set_footer(text=f"Trade completed at {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
                    
                    # Update the message with the success embed
                    try:
                        await self.message.edit(content=None, embed=success_embed, view=None)
                    except Exception as e:
                        print(f"Error displaying trade success message: {e}")
                    
                    self.stop()
                    
                    # Delete the trade from the database
                    await self.cog.update_trade_status(self.trade_id, 'delete')
                    
                    # Remove the view from the cog's dictionary
                    view_key = f"{self.initiator_id}_{self.target_id}"
                    if view_key in self.cog.trade_views:
                        del self.cog.trade_views[view_key]
                    
                    # Log the successful trade to the trade log channel
                    initiator_items_formatted = self.format_offer(self.initiator_offer, 0)
                    target_items_formatted = self.format_offer(self.target_offer, 0)
                    
                    await self.cog.log_trade_event(
                        "Trade Completed Successfully", 
                        f"Trade between {self.ctx.author.name} and {self.target.name} was completed successfully.",
                        discord.Color.green(),
                        is_error=False,
                        trade_data={
                            'initiator': {
                                'name': self.ctx.author.name, 
                                'id': self.initiator_id,
                                'balance': initiator_balance_after
                            },
                            'target': {
                                'name': self.target.name, 
                                'id': self.target_id,
                                'balance': target_balance_after
                            },
                            'initiator_items': initiator_items_formatted,
                            'target_items': target_items_formatted,
                            'initiator_currency': self.initiator_money,
                            'target_currency': self.target_money
                        }
                    )
                    
            except Exception as e:
                print(f"Error processing trade: {e}")
                await self.message.edit(
                    content=f"{EMOJI_CANCEL} An error occurred while processing the trade: {str(e)}", 
                    embed=None, 
                    view=None
                )
                self.stop()
                
                # Delete the trade from the database on error
                await self.cog.update_trade_status(self.trade_id, 'delete')
                
                # Remove the view from the cog's dictionary
                view_key = f"{self.initiator_id}_{self.target_id}"
                if view_key in self.cog.trade_views:
                    del self.cog.trade_views[view_key]
                
                # Log the error to the trade log channel
                await self.cog.log_trade_event(
                    "Trade Processing Error", 
                    f"Unexpected error processing trade between {self.ctx.author.name} and {self.target.name}: {str(e)}",
                    discord.Color.red(),
                    is_error=True
                )

        async def update_trade_embed(self, notification=None):
            embed = discord.Embed(title=f"{EMOJI_TRADE} Trade in Progress", color=discord.Color.gold())

            # Initiator's overall price
            embed.add_field(
                name=f"{EMOJI_MONEY} {self.ctx.author.name}'s Total Offer Value:",
                value=f"{self.initiator_overall_price:,}",
                inline=False
            )

            # Target's overall price
            embed.add_field(
                name=f"{EMOJI_MONEY} {self.target.name}'s Total Offer Value:",
                value=f"{self.target_overall_price:,}",
                inline=False
            )

            # Initiator's offer
            initiator_status = EMOJI_LOCK if self.initiator_ready else EMOJI_UNLOCK
            if self.trade_state == "confirming":
                initiator_status = EMOJI_CONFIRM if self.initiator_confirmed else EMOJI_HOURGLASS
            embed.add_field(
                name=f"{initiator_status} {self.ctx.author.name}'s offer:",
                value=self.format_offer(self.initiator_offer, self.initiator_money),
                inline=False
            )

            # Target's offer
            target_status = EMOJI_LOCK if self.target_ready else EMOJI_UNLOCK
            if self.trade_state == "confirming":
                target_status = EMOJI_CONFIRM if self.target_confirmed else EMOJI_HOURGLASS
            embed.add_field(
                name=f"{target_status} {self.target.name}'s offer:",
                value=self.format_offer(self.target_offer, self.target_money),
                inline=False
            )

            # Add available currency information
            if self.trade_state != "waiting_acceptance":
                embed.add_field(
                    name="Available Currency",
                    value=f"{self.ctx.author.name}: {self.initiator_available_currency - self.initiator_money:,}\n"
                          f"{self.target.name}: {self.target_available_currency - self.target_money:,}",
                    inline=False
                )

            if self.trade_state == "waiting_acceptance":
                status = f"{EMOJI_HOURGLASS} Waiting for {self.target.name} to accept the trade."
            elif self.trade_state == "initiator_adding":
                status = f"{EMOJI_UNLOCK} {self.ctx.author.name} is adding items. Lock when ready."
            elif self.trade_state == "target_adding":
                status = f"{EMOJI_UNLOCK} {self.target.name} is adding items. Lock when ready."
            elif self.trade_state == "confirming":
                initiator_status_text = "✅ Confirmed" if self.initiator_confirmed else "⏳ Waiting"
                target_status_text = "✅ Confirmed" if self.target_confirmed else "⏳ Waiting"
                status = f"Confirmation Status: {self.ctx.author.name}: {initiator_status_text} | {self.target.name}: {target_status_text}"

            embed.set_footer(text=status)
            if self.ctx.author.avatar:
                embed.set_thumbnail(url=self.ctx.author.avatar.url)
            if self.ctx.guild.icon:
                embed.set_author(name="Trading System", icon_url=self.ctx.guild.icon.url)
            else:
                embed.set_author(name="Trading System")

            # Add progress bar
            progress = self.get_progress_bar()
            embed.add_field(name="Trade Progress", value=progress, inline=False)

            content = notification if notification else None
            await self.message.edit(content=content, embed=embed, view=self)

        def format_offer(self, offer, money):
            formatted_offer = "\n".join(f"• {item['name']} x {item['quantity']} (ID: {item['id']})" for item in offer)
            if money > 0:
                formatted_offer += f"\n• Currency: {money:,}"
            return formatted_offer if formatted_offer else "No items offered yet"

        def get_progress_bar(self):
            total_steps = 4
            current_step = 1 if self.trade_state == "waiting_acceptance" else \
                           2 if self.trade_state == "initiator_adding" else \
                           3 if self.trade_state == "target_adding" else 4

            progress = "█" * current_step + "░" * (total_steps - current_step)
            return f"{progress} Step {current_step}/{total_steps}"

    @commands.command(aliases=["tr", "td"])
    @command_cooldown(1, 3.0)
    async def trade(self, ctx, target: discord.Member):
        start_time = time.time()
        
        # Immediate validation checks (no DB access needed)
        if target == ctx.author:
            await ctx.send(f"{EMOJI_CANCEL} You can't trade with yourself!")
            return

        if target.bot:
            await ctx.send(f"{EMOJI_CANCEL} You can't trade with a bot!")
            return
        
        # Check if either user is already in a trade using the database
        if await self.is_user_in_trade(ctx.author.id):
            await ctx.send(f"{EMOJI_CANCEL} You are already in an active trade!")
            return
            
        if await self.is_user_in_trade(target.id):
            await ctx.send(f"{EMOJI_CANCEL} {target.mention} is already in an active trade!")
            return

        # Create and send the initial embed immediately to improve perceived speed
        initial_embed = discord.Embed(
            title=f"{EMOJI_TRADE} Initializing Trade...",
            description=f"Setting up trade between {ctx.author.mention} and {target.mention}",
            color=discord.Color.blue()
        )
        if ctx.author.avatar:
            initial_embed.set_thumbnail(url=ctx.author.avatar.url)
        initial_message = await ctx.send(embed=initial_embed)
        
        # Create a task for the database operations to run in parallel
        async def setup_trade():
            # Optimize the query to be faster - get everything in one query
            query = """
            SELECT 
                u1.user_id as initiator_id, 
                u2.user_id as target_id,
                i1.inventory_id as initiator_inventory_id,
                i2.inventory_id as target_inventory_id
            FROM 
                user u1
                JOIN inventory i1 ON u1.user_id = i1.user_id
                JOIN user u2 ON u2.user_authorid = %s
                JOIN inventory i2 ON u2.user_id = i2.user_id
            WHERE 
                u1.user_authorid = %s
            LIMIT 1
            """
            result = await execute_query_with_retry(query, (str(target.id), str(ctx.author.id)))
            
            if not result:
                return None
            
            return result[0]
        
        # Run the database query
        data = await setup_trade()
        
        if not data:
            await initial_message.edit(content=f"{EMOJI_CANCEL} One or both users are not registered or don't have an inventory.", embed=None)
            return
        
        # Create a trade entry in the database
        initiator_id = data['initiator_id']
        target_id = data['target_id']
        trade_id = await self.create_trade_entry(initiator_id, target_id)
        
        if not trade_id:
            await initial_message.edit(content=f"{EMOJI_CANCEL} Could not create trade. One or both users might already be in a trade.", embed=None)
            return
        
        # Create the trade view with the data
        initiator_inventory_id = data['initiator_inventory_id']
        target_inventory_id = data['target_inventory_id']
        
        view = self.TradeView(self, ctx, target, initiator_id, target_id, initiator_inventory_id, target_inventory_id, trade_id)
        embed = discord.Embed(
            title=f"{EMOJI_TRADE} New Trade Request",
            description=f"{ctx.author.mention} wants to trade with {target.mention}",
            color=discord.Color.blue()
        )
        if ctx.author.avatar:
            embed.set_thumbnail(url=ctx.author.avatar.url)
        embed.set_footer(text=f"Click 'Accept Trade' to begin! This request will expire in {TRADE_INVITATION_TIMEOUT} seconds.")
        
        # Update the existing message instead of sending a new one
        await initial_message.edit(embed=embed, view=view)
        view.message = initial_message

        # Store the view in the cog's dictionary
        view_key = f"{initiator_id}_{target_id}"
        self.trade_views[view_key] = view
        
        # Log the time it took to initialize the trade
        end_time = time.time()
        print(f"Trade initialization took {(end_time - start_time) * 1000:.2f}ms")
        
        # Log the trade initiation to the trade log channel
        await self.log_trade_event(
            "Trade Initiated", 
            f"{ctx.author.name} initiated a trade with {target.name}.",
            discord.Color.blue()
        )

    @commands.command(aliases=["ia", "ica"])
    async def iadd(self, ctx, *, item_info):
        """Add an item to your trade offer"""
        # Check if the user is in a trade
        try:
            # Get the user's ID from the database
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You don't have an account.")
                return
                
            user_id = result[0]['user_id']
            
            # Check if the user is in a trade
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You are not in a trade.")
                return
                
            trade_data = result[0]
            trade_id = trade_data['id_tradestatus']
            initiator_id = trade_data['initiator_id']
            target_id = trade_data['target_id']
            
            # Find the trade view
            view_key = f"{initiator_id}_{target_id}"
            if view_key not in self.trade_views:
                await ctx.send(f"{EMOJI_CANCEL} Could not find your trade session.")
                return
                
            view = self.trade_views[view_key]
            
            # Check if it's the user's turn
            if view.current_turn != ctx.author:
                await ctx.send(f"{EMOJI_CANCEL} It's not your turn to add items.")
                return
                
            # Check if the user has already locked their offer
            is_initiator = user_id == initiator_id
            if (is_initiator and view.initiator_ready) or (not is_initiator and view.target_ready):
                await ctx.send(f"{EMOJI_CANCEL} You have already locked your offer.")
                return
                
            # Split item_info into name and quantity
            try:
                item_name, quantity = item_info.rsplit(',', 1)
                item_name = item_name.strip()
                quantity = int(quantity.strip())
                
                if quantity <= 0:
                    await ctx.send(f"{EMOJI_CANCEL} Quantity must be greater than 0.")
                    return
            except ValueError:
                await ctx.send(f"{EMOJI_CANCEL} Invalid format. Use: sl iadd item_name, quantity")
                return

            # Determine which user is adding items
            is_initiator = user_id == initiator_id
            inventory_id = view.initiator_inventory_id if is_initiator else view.target_inventory_id
            
            # First, find the item in the database
            item_found = False
            item_data = None
            
            # Check each item category
            for category, (inv_table, item_table, name_col, id_col, inv_id_col) in self.item_tables.items():
                # Search for the item by name
                item_query = f"SELECT {id_col}, {name_col}, price FROM {item_table} WHERE {name_col} LIKE %s"
                item_results = await execute_query_with_retry(item_query, (f"%{item_name}%",))
                
                if item_results:
                    # If multiple items match, look for exact match
                    if len(item_results) > 1:
                        exact_match = next((r for r in item_results if r[name_col].lower() == item_name.lower()), None)
                        if exact_match:
                            item_result = exact_match
                        else:
                            # Multiple items found, ask for clarification
                            embed = discord.Embed(
                                title="Multiple Items Found",
                                description="I found multiple items matching your search. Please specify which one you'd like to add:",
                                color=discord.Color.blue()
                            )
                            
                            item_list = "\n".join([f"• {r[name_col]}" for r in item_results])
                            embed.add_field(name="Matching Items", value=item_list, inline=False)
                            embed.set_footer(text="Please use the command again with the exact name of the item you want to add.")
                            
                            await ctx.send(embed=embed)
                            return
                    else:
                        item_result = item_results[0]
                    
                    # Now get the user's quantity of this item
                    item_id = item_result[id_col]
                    inventory_query = f"SELECT quantiter FROM {inv_table} WHERE inventory_id = %s AND {inv_id_col} = %s"
                    inventory_result = await execute_query_with_retry(inventory_query, (inventory_id, item_id))
                    
                    user_quantity = 0
                    if inventory_result:
                        user_quantity = inventory_result[0]['quantiter']
                    
                    # Create item data
                    item_data = {
                        'id': item_id,
                        'name': item_result[name_col],
                        'price': item_result['price'],
                        'category': category,
                        'user_quantity': user_quantity
                    }
                    
                    item_found = True
                    break
            
            if not item_found:
                await ctx.send(f"{EMOJI_CANCEL} The item '{item_name}' doesn't exist.")
                return
            
            # Check if the user has the item
            if item_data['user_quantity'] == 0:
                await ctx.send(f"{EMOJI_CANCEL} You don't have the item '{item_data['name']}' in your inventory.")
                return
            
            # Calculate the total quantity of this item already in the trade
            offer_list = view.initiator_offer if is_initiator else view.target_offer
            existing_quantity = sum(item['quantity'] for item in offer_list 
                       if item['id'] == item_data['id'] and item['category'] == item_data['category'])
            
            # Check if the new total quantity exceeds the user's inventory
            if existing_quantity + quantity > item_data['user_quantity']:
                await ctx.send(f"{EMOJI_CANCEL} You don't have enough of '{item_data['name']}'. You have {item_data['user_quantity']} in total, and {existing_quantity} already in the trade.")
                return
            
            # Add the item to the trade offer
            offer_item = {
                'id': item_data['id'],
                'name': item_data['name'],
                'quantity': quantity,
                'price': item_data['price'],
                'category': item_data['category']
            }
            
            # Check if the item already exists in the offer list
            if is_initiator:
                # Find existing item with same ID AND category
                existing_item = next((item for item in view.initiator_offer 
                                     if item['id'] == offer_item['id'] and item['category'] == offer_item['category']), None)
                
                if existing_item:
                    # Update existing item quantity
                    existing_item['quantity'] += quantity
                    view.initiator_overall_price += offer_item['price'] * quantity
                else:
                    # Add as new item
                    view.initiator_offer.append(offer_item)
                    view.initiator_overall_price += offer_item['price'] * quantity
            else:
                # Find existing item with same ID AND category
                existing_item = next((item for item in view.target_offer 
                                     if item['id'] == offer_item['id'] and item['category'] == offer_item['category']), None)
                
                if existing_item:
                    # Update existing item quantity
                    existing_item['quantity'] += quantity
                    view.target_overall_price += offer_item['price'] * quantity
                else:
                    # Add as new item
                    view.target_offer.append(offer_item)
                    view.target_overall_price += offer_item['price'] * quantity
            
            # Update the trade status in the database to reset the timeout
            await self.update_trade_status(view.trade_id, 'active')
            
            await view.update_trade_embed()
            await ctx.message.delete()
        
        except Exception as e:
            print(f"Error executing query: {e}")
            await ctx.send(f"{EMOJI_CANCEL} An error occurred: {str(e)}")
            
            # Log the error to the trade log channel
            await self.log_trade_event(
                "Trade Item Add Error", 
                f"Error adding item to trade: {str(e)}",
                discord.Color.red(),
                is_error=True
            )

    @commands.command(aliases=["ir", "irm"])
    async def iremove(self, ctx, *, item_name):
        """Remove an item from your trade offer"""
        # Check if the user is in a trade
        try:
            # Get the user's ID from the database
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You don't have an account.")
                return
                
            user_id = result[0]['user_id']
            
            # Check if the user is in a trade
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You are not in a trade.")
                return
                
            trade_data = result[0]
            trade_id = trade_data['id_tradestatus']
            initiator_id = trade_data['initiator_id']
            target_id = trade_data['target_id']
            
            # Find the trade view
            view_key = f"{initiator_id}_{target_id}"
            if view_key not in self.trade_views:
                await ctx.send(f"{EMOJI_CANCEL} Could not find your trade session.")
                return
                
            view = self.trade_views[view_key]
            
            # Check if it's the user's turn
            if view.current_turn != ctx.author:
                await ctx.send(f"{EMOJI_CANCEL} It's not your turn to modify items.")
                return
                
            # Check if the user has already locked their offer
            is_initiator = user_id == initiator_id
            if (is_initiator and view.initiator_ready) or (not is_initiator and view.target_ready):
                await ctx.send(f"{EMOJI_CANCEL} You have already locked your offer.")
                return
                
            # Get the user's offer list
            offer_list = view.initiator_offer if is_initiator else view.target_offer
            
            if not offer_list:
                await ctx.send(f"{EMOJI_CANCEL} You haven't added any items to the trade yet.")
                return
                
            # Find the item to remove
            item_to_remove = None
            for item in offer_list:
                if item['name'].lower() == item_name.lower():
                    item_to_remove = item
                    break
                    
            if not item_to_remove:
                await ctx.send(f"{EMOJI_CANCEL} The item '{item_name}' is not in your trade offer.")
                return
                
            # Remove the item from the offer
            offer_list.remove(item_to_remove)
            
            # Update the overall price
            if is_initiator:
                view.initiator_overall_price -= item_to_remove['price'] * item_to_remove['quantity']
            else:
                view.target_overall_price -= item_to_remove['price'] * item_to_remove['quantity']
                
            # Update the trade status in the database to reset the timeout
            await self.update_trade_status(view.trade_id, 'active')
            
            await view.update_trade_embed()
            await ctx.message.delete()
        
        except Exception as e:
            print(f"Error executing query: {e}")
            await ctx.send(f"{EMOJI_CANCEL} An error occurred: {str(e)}")
            
            # Log the error to the trade log channel
            await self.log_trade_event(
                "Trade Item Remove Error", 
                f"Error removing item from trade: {str(e)}",
                discord.Color.red(),
                is_error=True
            )

    @commands.command(aliases=["wa", "wda"])
    async def wadd(self, ctx, amount: int):
        """Add currency to your trade offer"""
        # Check if the user is in a trade
        try:
            # Get the user's ID from the database
            query = "SELECT user_id, won_currency FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You don't have an account.")
                return
                
            user_id = result[0]['user_id']
            user_currency = result[0]['won_currency'] or 0
            
            # Check if the user is in a trade
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You are not in a trade.")
                return
                
            trade_data = result[0]
            trade_id = trade_data['id_tradestatus']
            initiator_id = trade_data['initiator_id']
            target_id = trade_data['target_id']
            
            # Find the trade view
            view_key = f"{initiator_id}_{target_id}"
            if view_key not in self.trade_views:
                await ctx.send(f"{EMOJI_CANCEL} Could not find your trade session.")
                return
                
            view = self.trade_views[view_key]
            
            # Check if it's the user's turn
            if view.current_turn != ctx.author:
                await ctx.send(f"{EMOJI_CANCEL} It's not your turn to add currency.")
                return
                
            # Check if the user has already locked their offer
            is_initiator = user_id == initiator_id
            if (is_initiator and view.initiator_ready) or (not is_initiator and view.target_ready):
                await ctx.send(f"{EMOJI_CANCEL} You have already locked your offer.")
                return
                
            # Check if the amount is valid
            if amount <= 0:
                await ctx.send(f"{EMOJI_CANCEL} Please enter a positive amount.")
                return
                
            # Check if the user has enough currency
            current_offer = view.initiator_money if is_initiator else view.target_money
            if amount > user_currency - current_offer:
                await ctx.send(f"{EMOJI_CANCEL} You don't have enough currency. Your available balance: {user_currency - current_offer:,}")
                return
                
            # Add the currency to the offer
            if is_initiator:
                view.initiator_money += amount
                view.initiator_overall_price += amount
            else:
                view.target_money += amount
                view.target_overall_price += amount
                
            # Update the trade status in the database to reset the timeout
            await self.update_trade_status(view.trade_id, 'active')
            
            await view.update_trade_embed()
            await ctx.message.delete()
        
        except Exception as e:
            print(f"Error executing query: {e}")
            await ctx.send(f"{EMOJI_CANCEL} An error occurred: {str(e)}")
            
            # Log the error to the trade log channel
            await self.log_trade_event(
                "Trade Currency Add Error", 
                f"Error adding currency to trade: {str(e)}",
                discord.Color.red(),
                is_error=True
            )

    @commands.command(aliases=["wr", "wrm"])
    async def wremove(self, ctx):
        """Remove all currency from your trade offer"""
        # Check if the user is in a trade
        try:
            # Get the user's ID from the database
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            result = await execute_query_with_retry(query, (str(ctx.author.id),))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You don't have an account.")
                return
                
            user_id = result[0]['user_id']
            
            # Check if the user is in a trade
            query = """
            SELECT id_tradestatus, initiator_id, target_id 
            FROM trade_status 
            WHERE initiator_id = %s OR target_id = %s
            """
            result = await execute_query_with_retry(query, (user_id, user_id))
            
            if not result:
                await ctx.send(f"{EMOJI_CANCEL} You are not in a trade.")
                return
                
            trade_data = result[0]
            trade_id = trade_data['id_tradestatus']
            initiator_id = trade_data['initiator_id']
            target_id = trade_data['target_id']
            
            # Find the trade view
            view_key = f"{initiator_id}_{target_id}"
            if view_key not in self.trade_views:
                await ctx.send(f"{EMOJI_CANCEL} Could not find your trade session.")
                return
                
            view = self.trade_views[view_key]
            
            # Check if it's the user's turn
            if view.current_turn != ctx.author:
                await ctx.send(f"{EMOJI_CANCEL} It's not your turn to modify currency.")
                return
                
            # Check if the user has already locked their offer
            is_initiator = user_id == initiator_id
            if (is_initiator and view.initiator_ready) or (not is_initiator and view.target_ready):
                await ctx.send(f"{EMOJI_CANCEL} You have already locked your offer.")
                return
                
            # Remove the currency from the offer
            if is_initiator:
                view.initiator_overall_price -= view.initiator_money
                view.initiator_money = 0
            else:
                view.target_overall_price -= view.target_money
                view.target_money = 0
                
            # Update the trade status in the database to reset the timeout
            await self.update_trade_status(view.trade_id, 'active')
            
            await view.update_trade_embed()
            await ctx.message.delete()
        
        except Exception as e:
            print(f"Error executing query: {e}")
            await ctx.send(f"{EMOJI_CANCEL} An error occurred: {str(e)}")
            
            # Log the error to the trade log channel
            await self.log_trade_event(
                "Trade Currency Remove Error", 
                f"Error removing currency from trade: {str(e)}",
                discord.Color.red(),
                is_error=True
            )

    async def get_user_id(self, user_authorid):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (str(user_authorid),))
        return result[0]['user_id'] if result else None

    async def get_inventory_id(self, user_id):
        query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['inventory_id'] if result else None
        
    @commands.command(name="tradedbstat")
    @commands.has_permissions(administrator=True)
    async def tradedbstats(self, ctx):
        """Display database connection statistics for the trade system"""
        metrics = get_metrics()
        
        embed = discord.Embed(
            title="Trade System Database Metrics",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
        embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
        embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
        
        embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
        embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
        embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
        
        # Add active trades information
        query = "SELECT COUNT(*) as count FROM trade_status"
        result = await execute_query_with_retry(query)
        active_trades = result[0]['count'] if result else 0
        
        embed.add_field(name="Active Trades", value=str(active_trades), inline=True)
        
        # Add recent trade activity
        query = """
        SELECT COUNT(*) as count 
        FROM trade_status 
        WHERE created_at > DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """
        result = await execute_query_with_retry(query)
        recent_trades = result[0]['count'] if result else 0
        
        embed.add_field(name="Trades (Last Hour)", value=str(recent_trades), inline=True)
        
        if metrics["last_error"]:
            embed.add_field(
                name="Last Error", 
                value=f"{metrics['last_error']}\nTime: {metrics['last_error_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
            
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TradeCommands(bot))
