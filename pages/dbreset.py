import discord
from discord.ext import commands
import asyncio
from datetime import datetime
from discord.ui import Button, View
from utils.database import execute_query_with_retry

class ConfirmationView(View):
    def __init__(self, ctx, mode, callback, timeout=30):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.mode = mode
        self.callback = callback
        self.value = None
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        
        try:
            await self.message.edit(content="⏱️ Connection reset confirmation timed out.", view=self)
        except:
            pass
    
    async def interaction_check(self, interaction):
        # Only allow the original command invoker to use the buttons
        return interaction.user.id == self.ctx.author.id
    
    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = True
        
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await self.message.edit(view=self)
        self.stop()
        
        # Call the callback with the result
        await self.callback(True, self.mode)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await self.message.edit(content="✅ Connection reset cancelled.", view=self)
        self.stop()
        
        # Call the callback with the result
        await self.callback(False, None)

class KillConfirmationView(View):
    def __init__(self, ctx, connection_id, callback, timeout=30):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.connection_id = connection_id
        self.callback = callback
        self.value = None
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        
        try:
            await self.message.edit(content="⏱️ Kill confirmation timed out.", view=self)
        except:
            pass
    
    async def interaction_check(self, interaction):
        # Only allow the original command invoker to use the buttons
        return interaction.user.id == self.ctx.author.id
    
    @discord.ui.button(label="Force Kill", style=discord.ButtonStyle.danger)
    async def force_kill(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = True
        
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await self.message.edit(view=self)
        self.stop()
        
        # Call the callback with the result
        await self.callback(True, self.connection_id)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await self.message.edit(content="✅ Kill operation cancelled.", view=self)
        self.stop()
        
        # Call the callback with the result
        await self.callback(False, None)

class DatabaseConnectionReset(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reset_in_progress = False
        self.confirmation_timeout = 30  # seconds
        self.admin_id = 818132161534230559  # Only this user can use these commands
    
    async def cog_check(self, ctx):
        """Global check for all commands in this cog"""
        # Only allow the specified admin user
        return ctx.author.id == self.admin_id
    
    @commands.command(name="dbreset", aliases=["dbr"])
    async def db_reset_connections(self, ctx, mode: str = "safe"):
        """
        Reset database connections to avoid max connections error
        
        Parameters:
        - mode: The reset mode (safe, idle, all)
          - safe: Only kill idle connections older than 100 seconds (default)
          - idle: Kill all idle connections
          - all: Kill all user connections (dangerous)
        """
        if self.reset_in_progress:
            await ctx.send("⚠️ A connection reset is already in progress. Please wait.")
            return
        
        # Validate mode
        valid_modes = ["safe", "idle", "all"]
        if mode.lower() not in valid_modes:
            await ctx.send(f"❌ Invalid mode. Please use one of: {', '.join(valid_modes)}")
            return
        
        # Get current connection count
        try:
            # Set reset in progress flag
            self.reset_in_progress = True
            
            # Get process list to analyze
            process_query = "SELECT id, user, host, db, command, time, state, info FROM information_schema.processlist"
            processes = await execute_query_with_retry(process_query)
            
            if not processes:
                await ctx.send("❓ No database processes found to reset.")
                self.reset_in_progress = False
                return
            
            # Count different types of connections
            system_processes = [p for p in processes if p['user'] == 'system user' or p['command'] == 'Daemon']
            idle_processes = [p for p in processes if p['command'] == 'Sleep']
            active_processes = [p for p in processes if p['command'] != 'Sleep' and p['user'] != 'system user']
            
            # For safe mode, only target idle connections older than 100 seconds
            old_idle_processes = [p for p in idle_processes if p['time'] > 100]
            
            # Determine which processes to kill based on mode
            processes_to_kill = []
            if mode.lower() == "safe":
                processes_to_kill = old_idle_processes
                mode_description = "idle connections older than 100 seconds"
            elif mode.lower() == "idle":
                processes_to_kill = idle_processes
                mode_description = "all idle connections"
            elif mode.lower() == "all":
                # Don't kill system processes
                processes_to_kill = idle_processes + active_processes
                mode_description = "all user connections (including active ones)"
            
            # Create confirmation embed
            embed = discord.Embed(
                title="🔄 Database Connection Reset",
                description=f"You are about to reset {len(processes_to_kill)} database connections ({mode_description}).",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="Connection Summary",
                value=(
                    f"Total Connections: **{len(processes)}**\n"
                    f"System Processes: **{len(system_processes)}**\n"
                    f"Idle Connections: **{len(idle_processes)}**\n"
                    f"Active Queries: **{len(active_processes)}**\n"
                    f"Connections to Reset: **{len(processes_to_kill)}**"
                ),
                inline=False
            )
            
            # Add warning for all mode
            if mode.lower() == "all":
                embed.add_field(
                    name="⚠️ WARNING",
                    value=(
                        "This will kill ALL user connections, including active queries!\n"
                        "This may cause data loss or application errors.\n"
                        "Only use this as a last resort."
                    ),
                    inline=False
                )
            
            # Create confirmation view with buttons
            async def confirmation_callback(confirmed, confirmed_mode):
                if not confirmed:
                    self.reset_in_progress = False
                    return
                
                # Proceed with reset
                status_message = await ctx.send("🔄 Resetting connections... Please wait.")
                
                # Execute the reset
                success_count = 0
                error_count = 0
                
                for process in processes_to_kill:
                    try:
                        # Kill the connection
                        kill_query = f"KILL {process['id']}"
                        await execute_query_with_retry(kill_query)
                        success_count += 1
                    except Exception as e:
                        print(f"Error killing connection {process['id']}: {e}")
                        error_count += 1
                
                # Get updated connection count
                updated_processes = await execute_query_with_retry(process_query)
                current_count = len(updated_processes) if updated_processes else 0
                
                # Create result embed
                result_embed = discord.Embed(
                    title="🔄 Connection Reset Complete",
                    description=f"Reset completed with {success_count} connections closed.",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                
                result_embed.add_field(
                    name="Reset Results",
                    value=(
                        f"Connections Before: **{len(processes)}**\n"
                        f"Connections After: **{current_count}**\n"
                        f"Successfully Closed: **{success_count}**\n"
                        f"Failed to Close: **{error_count}**"
                    ),
                    inline=False
                )
                
                # Add recommendations
                if error_count > 0:
                    result_embed.add_field(
                        name="⚠️ Warnings",
                        value="Some connections could not be closed. They may be system processes or have already been closed.",
                        inline=False
                    )
                
                if current_count > 50:  # Still a high number of connections
                    result_embed.add_field(
                        name="Recommendations",
                        value=(
                            "There are still many active connections. Consider:\n"
                            "1. Checking for connection leaks in your code\n"
                            "2. Increasing max_connections in my.ini\n"
                            "3. Reducing connection timeout values"
                        ),
                        inline=False
                    )
                
                await status_message.edit(embed=result_embed)
                
                # Log the action
                log_query = """
                INSERT INTO admin_logs (action_type, performed_by, details, timestamp)
                VALUES (%s, %s, %s, %s)
                """
                try:
                    log_details = f"Reset {success_count} connections using mode '{mode}'"
                    await execute_query_with_retry(
                        log_query, 
                        ("connection_reset", str(ctx.author.id), log_details, datetime.now())
                    )
                except Exception as e:
                    print(f"Failed to log connection reset: {e}")
                
                self.reset_in_progress = False
            
            # Create and send the confirmation view
            view = ConfirmationView(ctx, mode, confirmation_callback, timeout=self.confirmation_timeout)
            view.message = await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            self.reset_in_progress = False
            await ctx.send(f"❌ Error during connection reset: {str(e)}")
    
    #@commands.command(name="dbkill", aliases=["dbk"]) NEVER USE WILL RUIN DATABASE
    async def db_kill_connection(self, ctx, connection_id: int):
        """Kill a specific database connection by ID"""
        try:
            # Verify the connection exists
            verify_query = f"SELECT id, user, host, db, command, time, info FROM information_schema.processlist WHERE id = {connection_id}"
            result = await execute_query_with_retry(verify_query)
            
            if not result:
                await ctx.send(f"❌ Connection ID {connection_id} not found.")
                return
            
            connection = result[0]
            
            # Check if it's a system process
            if connection['user'] == 'system user':
                # Create confirmation embed for system process
                embed = discord.Embed(
                    title="⚠️ System Process Warning",
                    description=f"Connection ID {connection_id} is a system process. Killing it may cause database instability.",
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                
                embed.add_field(
                    name="Process Details",
                    value=(
                        f"User: **{connection['user']}**\n"
                        f"Host: **{connection['host']}**\n"
                        f"Database: **{connection['db'] or 'None'}**\n"
                        f"Command: **{connection['command']}**\n"
                        f"Time: **{connection['time']}s**\n"
                        f"Query: **{connection['info'][:100] if connection['info'] else 'None'}**"
                    ),
                    inline=False
                )
                
                # Create confirmation view with buttons
                async def kill_callback(confirmed, conn_id):
                    if not confirmed or conn_id is None:
                        return
                    
                    # Kill the connection
                    kill_query = f"KILL {conn_id}"
                    await execute_query_with_retry(kill_query)
                    
                    # Verify it was killed
                    verify_after = await execute_query_with_retry(verify_query)
                    
                    if not verify_after:
                        await ctx.send(f"✅ Successfully killed connection ID {conn_id}.")
                    else:
                        await ctx.send(f"⚠️ Failed to kill connection ID {conn_id}. It may be a protected system process.")
                
                # Create and send the confirmation view
                view = KillConfirmationView(ctx, connection_id, kill_callback, timeout=30)
                view.message = await ctx.send(embed=embed, view=view)
                return
            
            # For non-system processes, kill directly
            kill_query = f"KILL {connection_id}"
            await execute_query_with_retry(kill_query)
            
            # Verify it was killed
            verify_after = await execute_query_with_retry(verify_query)
            
            if not verify_after:
                await ctx.send(f"✅ Successfully killed connection ID {connection_id}.")
            else:
                await ctx.send(f"⚠️ Failed to kill connection ID {connection_id}. It may be a protected system process.")
        
        except Exception as e:
            await ctx.send(f"❌ Error killing connection: {str(e)}")
    
    @commands.command(name="dboptimize", aliases=["dbo"])
    async def db_optimize_connections(self, ctx):
        """Optimize database connection settings"""
        try:
            # Get current settings
            variables_query = """
            SHOW VARIABLES WHERE Variable_name IN (
                'max_connections',
                'wait_timeout',
                'interactive_timeout',
                'connect_timeout',
                'max_connect_errors',
                'thread_cache_size'
            )
            """
            variables_result = await execute_query_with_retry(variables_query)
            
            if not variables_result:
                await ctx.send("❌ Could not retrieve database variables.")
                return
            
            # Get current status
            status_query = """
            SHOW GLOBAL STATUS WHERE Variable_name IN (
                'Threads_connected',
                'Max_used_connections',
                'Connection_errors_max_connections',
                'Aborted_connects',
                'Aborted_clients'
            )
            """
            status_result = await execute_query_with_retry(status_query)
            
            # Process results
            variables = {row['Variable_name']: row['Value'] for row in variables_result}
            status = {row['Variable_name']: row['Value'] for row in status_result}
            
            # Calculate recommended settings
            current_max = int(variables.get('max_connections', 151))
            max_used = int(status.get('Max_used_connections', 0))
            
            # Recommend max_connections based on usage patterns
            recommended_max = max(int(max_used * 1.5), current_max + 50)
            
            # Recommend thread_cache_size
            current_thread_cache = int(variables.get('thread_cache_size', 0))
            recommended_thread_cache = max(current_thread_cache, 10)
            
            # Recommend timeouts
            current_wait_timeout = int(variables.get('wait_timeout', 28800))
            recommended_wait_timeout = min(current_wait_timeout, 3600)  # 1 hour max
            
            # Create embed with recommendations
            embed = discord.Embed(
                title="🔧 Database Connection Optimization",
                description="Based on your current usage, here are recommended settings to optimize database connections.",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="Current Settings",
                value=(
                    f"max_connections: **{variables.get('max_connections', 'N/A')}**\n"
                    f"wait_timeout: **{variables.get('wait_timeout', 'N/A')}s**\n"
                    f"interactive_timeout: **{variables.get('interactive_timeout', 'N/A')}s**\n"
                    f"thread_cache_size: **{variables.get('thread_cache_size', 'N/A')}**"
                ),
                inline=True
            )
            
            embed.add_field(
                name="Current Usage",
                value=(
                    f"Connected: **{status.get('Threads_connected', 'N/A')}**\n"
                    f"Max Used: **{status.get('Max_used_connections', 'N/A')}**\n"
                    f"Max Errors: **{status.get('Connection_errors_max_connections', 'N/A')}**\n"
                    f"Aborted: **{status.get('Aborted_connects', 'N/A')}**"
                ),
                inline=True
            )
            
            embed.add_field(
                name="Recommended Settings",
                value=(
                    f"max_connections: **{recommended_max}**\n"
                    f"wait_timeout: **{recommended_wait_timeout}s**\n"
                    f"thread_cache_size: **{recommended_thread_cache}**"
                ),
                inline=False
            )
            
            # Add my.ini configuration instructions
            embed.add_field(
                name="How to Apply Changes",
                value=(
                    "1. Locate your my.ini file in the XAMPP MySQL directory\n"
                    "2. Add or modify these lines in the [mysqld] section:\n"
                    f"```\nmax_connections={recommended_max}\n"
                    f"wait_timeout={recommended_wait_timeout}\n"
                    f"interactive_timeout={recommended_wait_timeout}\n"
                    f"thread_cache_size={recommended_thread_cache}\n```"
                    "3. Restart MySQL service in XAMPP"
                ),
                inline=False
            )
            
            # Create apply button
            class OptimizeView(View):
                def __init__(self, timeout=60):
                    super().__init__(timeout=timeout)
                
                async def interaction_check(self, interaction):
                    # Only allow the original command invoker to use the buttons
                    return interaction.user.id == ctx.author.id
                
                @discord.ui.button(label="Copy to Clipboard", style=discord.ButtonStyle.primary)
                async def copy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Create a formatted text block for easy copying
                    config_text = (
                        "# MySQL Optimization Settings\n"
                        f"max_connections={recommended_max}\n"
                        f"wait_timeout={recommended_wait_timeout}\n"
                        f"interactive_timeout={recommended_wait_timeout}\n"
                        f"thread_cache_size={recommended_thread_cache}"
                    )
                    
                    await interaction.response.send_message(f"```ini\n{config_text}\n```", ephemeral=True)
            
            # Send the embed with the view
            view = OptimizeView()
            await ctx.send(embed=embed, view=view)
        
        except Exception as e:
            await ctx.send(f"❌ Error optimizing connections: {str(e)}")

async def setup(bot):
    await bot.add_cog(DatabaseConnectionReset(bot))