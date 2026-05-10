import discord
from discord.ext import commands
import asyncio
import time
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import io
from utils.database import get_metrics, execute_query_with_retry

class DatabaseMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.connection_history = []  # Store historical connection data
        self.history_length = 60  # Keep 60 data points
        self.monitor_task = asyncio.create_task(self.background_monitor())
    
    async def background_monitor(self):
        """Background task to collect connection metrics over time"""
        try:
            while True:
                metrics = get_metrics()
                timestamp = datetime.now()
                
                # Add to history
                self.connection_history.append({
                    "timestamp": timestamp,
                    "active_connections": metrics["active_connections"],
                    "peak_connections": metrics["peak_connections"]
                })
                
                # Trim history if needed
                if len(self.connection_history) > self.history_length:
                    self.connection_history.pop(0)
                
                # Wait before next check
                await asyncio.sleep(60)  # Collect data every minute
        except asyncio.CancelledError:
            # Task was cancelled, clean up
            pass
        except Exception as e:
            print(f"Error in background monitor: {e}")
    
    async def cog_unload(self):
        """Clean up when cog is unloaded"""
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
    
    @commands.command(name="dbconnections", aliases=["dbc"])
    @commands.has_permissions(administrator=True)
    async def db_connections(self, ctx):
        """Display current database connection information"""
        try:
            # Get current metrics
            metrics = get_metrics()
            
            # Get XAMPP process information
            xampp_info = await self.get_xampp_process_info()
            
            # Create embed
            embed = discord.Embed(
                title="📊 Database Connection Monitor",
                description="Current database connection status",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            # Add connection metrics
            embed.add_field(
                name="Connection Pool",
                value=(
                    f"Active: **{metrics['active_connections']}**\n"
                    f"Peak: **{metrics['peak_connections']}**\n"
                    f"Errors: **{metrics['connection_errors']}**"
                ),
                inline=True
            )
            
            # Add query metrics
            embed.add_field(
                name="Query Performance",
                value=(
                    f"Total: **{metrics['total_queries']}**\n"
                    f"Slow: **{metrics['slow_queries']}** "
                    f"({metrics['slow_query_percentage']:.1f}%)\n"
                    f"Avg Time: **{metrics['avg_query_time']:.2f}ms**"
                ),
                inline=True
            )
            
            # Add XAMPP info if available
            if xampp_info:
                embed.add_field(
                    name="MySQL Process",
                    value=(
                        f"Max Connections: **{xampp_info['max_connections']}**\n"
                        f"Current Threads: **{xampp_info['threads_connected']}**\n"
                        f"Connection Errors: **{xampp_info['connection_errors']}**"
                    ),
                    inline=False
                )
            
            # Add last error if any
            if metrics["last_error"]:
                error_time = metrics["last_error_time"].strftime("%Y-%m-%d %H:%M:%S") if metrics["last_error_time"] else "Unknown"
                embed.add_field(
                    name="Last Error",
                    value=f"Time: {error_time}\nError: {metrics['last_error'][:100]}...",
                    inline=False
                )
            
            # Generate and attach connection history graph
            if len(self.connection_history) > 1:
                graph = await self.generate_connection_graph()
                file = discord.File(graph, filename="connections.png")
                embed.set_image(url="attachment://connections.png")
                await ctx.send(embed=embed, file=file)
            else:
                embed.add_field(
                    name="Connection History",
                    value="Not enough data collected yet. Try again in a few minutes.",
                    inline=False
                )
                await ctx.send(embed=embed)
        
        except Exception as e:
            await ctx.send(f"Error retrieving database connection information: {str(e)}")
    
    async def get_xampp_process_info(self):
        """Get MySQL process information from XAMPP"""
        try:
            # Query MySQL status variables
            variables_query = """
            SHOW GLOBAL STATUS WHERE Variable_name IN (
                'Threads_connected', 
                'Connection_errors_max_connections',
                'Connection_errors_internal',
                'Connection_errors_accept'
            )
            """
            variables_result = await execute_query_with_retry(variables_query)
            
            if not variables_result:
                return None
            
            # Query max_connections setting
            max_conn_query = "SHOW VARIABLES LIKE 'max_connections'"
            max_conn_result = await execute_query_with_retry(max_conn_query)
            
            # Process results
            variables = {}
            for row in variables_result:
                variables[row['Variable_name']] = int(row['Value'])
            
            max_connections = int(max_conn_result[0]['Value']) if max_conn_result else 0
            
            # Calculate total connection errors
            connection_errors = (
                variables.get('Connection_errors_max_connections', 0) +
                variables.get('Connection_errors_internal', 0) +
                variables.get('Connection_errors_accept', 0)
            )
            
            return {
                'max_connections': max_connections,
                'threads_connected': variables.get('Threads_connected', 0),
                'connection_errors': connection_errors
            }
        
        except Exception as e:
            print(f"Error getting XAMPP process info: {e}")
            return None
    
    @commands.command(name="dbstatus", aliases=["dbs"])
    @commands.has_permissions(administrator=True)
    async def db_status(self, ctx):
        """Display detailed MySQL server status"""
        try:
            # Query MySQL status
            status_query = """
            SHOW GLOBAL STATUS WHERE Variable_name IN (
                'Threads_connected', 
                'Threads_running',
                'Threads_created',
                'Connections',
                'Aborted_connects',
                'Aborted_clients',
                'Max_used_connections',
                'Connection_errors_max_connections',
                'Connection_errors_internal',
                'Uptime'
            )
            """
            status_result = await execute_query_with_retry(status_query)
            
            if not status_result:
                await ctx.send("❌ Could not retrieve MySQL status information.")
                return
            
            # Query MySQL variables
            variables_query = """
            SHOW GLOBAL VARIABLES WHERE Variable_name IN (
                'max_connections',
                'connect_timeout',
                'wait_timeout',
                'interactive_timeout',
                'max_connect_errors'
            )
            """
            variables_result = await execute_query_with_retry(variables_query)
            
            # Process results
            status = {row['Variable_name']: row['Value'] for row in status_result}
            variables = {row['Variable_name']: row['Value'] for row in variables_result}
            
            # Calculate uptime in a readable format
            uptime_seconds = int(status.get('Uptime', 0))
            uptime = str(timedelta(seconds=uptime_seconds))
            
            # Calculate connection usage percentage
            max_connections = int(variables.get('max_connections', 100))
            current_connections = int(status.get('Threads_connected', 0))
            connection_percentage = (current_connections / max_connections) * 100 if max_connections > 0 else 0
            
            # Create embed
            embed = discord.Embed(
                title="🔍 MySQL Server Status",
                description=f"Server has been up for {uptime}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            # Add connection information
            embed.add_field(
                name="Connection Status",
                value=(
                    f"Current: **{status.get('Threads_connected', '0')}** "
                    f"({connection_percentage:.1f}% of max)\n"
                    f"Running: **{status.get('Threads_running', '0')}**\n"
                    f"Total Created: **{status.get('Threads_created', '0')}**\n"
                    f"Max Used: **{status.get('Max_used_connections', '0')}**\n"
                    f"Max Allowed: **{variables.get('max_connections', '0')}**"
                ),
                inline=True
            )
            
            # Add connection errors
            embed.add_field(
                name="Connection Errors",
                value=(
                    f"Max Connections: **{status.get('Connection_errors_max_connections', '0')}**\n"
                    f"Internal: **{status.get('Connection_errors_internal', '0')}**\n"
                    f"Aborted Connects: **{status.get('Aborted_connects', '0')}**\n"
                    f"Aborted Clients: **{status.get('Aborted_clients', '0')}**"
                ),
                inline=True
            )
            
            # Add timeout settings
            embed.add_field(
                name="Timeout Settings",
                value=(
                    f"Connect Timeout: **{variables.get('connect_timeout', '0')}s**\n"
                    f"Wait Timeout: **{variables.get('wait_timeout', '0')}s**\n"
                    f"Interactive Timeout: **{variables.get('interactive_timeout', '0')}s**\n"
                    f"Max Connect Errors: **{variables.get('max_connect_errors', '0')}**"
                ),
                inline=False
            )
            
            # Add recommendations based on current status
            recommendations = []
            
            if connection_percentage > 80:
                recommendations.append("⚠️ Connection usage is high. Consider increasing max_connections.")
            
            if int(status.get('Connection_errors_max_connections', 0)) > 0:
                recommendations.append("⚠️ Max connection errors detected. Increase max_connections in my.ini.")
            
            if int(status.get('Aborted_connects', 0)) > 100:
                recommendations.append("⚠️ High number of aborted connections. Check client timeout settings.")
            
            if recommendations:
                embed.add_field(
                    name="Recommendations",
                    value="\n".join(recommendations),
                    inline=False
                )
            
            await ctx.send(embed=embed)
        
        except Exception as e:
            await ctx.send(f"Error retrieving MySQL status: {str(e)}")
    
    @commands.command(name="dbprocesslist", aliases=["dbpl"])
    @commands.has_permissions(administrator=True)
    async def db_process_list(self, ctx):
        """Display current MySQL process list"""
        try:
            # Query process list
            query = "SELECT id, user, host, db, command, time, state, info FROM information_schema.processlist"
            result = await execute_query_with_retry(query)
            
            if not result:
                await ctx.send("No active database processes found.")
                return
            
            # Create embed
            embed = discord.Embed(
                title="🔄 MySQL Process List",
                description=f"Found {len(result)} active processes",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            # Group processes by state
            processes_by_state = {}
            for process in result:
                state = process['state'] or 'None'
                if state not in processes_by_state:
                    processes_by_state[state] = []
                processes_by_state[state].append(process)
            
            # Add summary field
            state_summary = []
            for state, processes in processes_by_state.items():
                state_summary.append(f"**{state}**: {len(processes)}")
            
            embed.add_field(
                name="Process Summary",
                value="\n".join(state_summary) if state_summary else "No processes",
                inline=False
            )
            
            # Add long-running queries (if any)
            long_running = [p for p in result if p['time'] > 10 and p['command'] != 'Sleep']
            if long_running:
                long_running.sort(key=lambda x: x['time'], reverse=True)
                long_queries = []
                
                for i, process in enumerate(long_running[:5]):  # Show top 5
                    query_info = process['info']
                    if query_info:
                        # Truncate query text
                        if len(query_info) > 50:
                            query_info = query_info[:47] + "..."
                    else:
                        query_info = "No query"
                    
                    long_queries.append(
                        f"ID: {process['id']} | Time: {process['time']}s | {query_info}"
                    )
                
                embed.add_field(
                    name="Long-Running Queries",
                    value="\n".join(long_queries),
                    inline=False
                )
            
            await ctx.send(embed=embed)
        
        except Exception as e:
            await ctx.send(f"Error retrieving process list: {str(e)}")
    
    async def generate_connection_graph(self):
        """Generate a graph of connection history"""
        plt.figure(figsize=(10, 6))
        
        # Extract data
        timestamps = [entry["timestamp"] for entry in self.connection_history]
        active_connections = [entry["active_connections"] for entry in self.connection_history]
        peak_connections = [entry["peak_connections"] for entry in self.connection_history]
        
        # Plot data
        plt.plot(timestamps, active_connections, 'b-', label='Active Connections')
        plt.plot(timestamps, peak_connections, 'r-', label='Peak Connections')
        
        # Add labels and title
        plt.title('Database Connection History')
        plt.xlabel('Time')
        plt.ylabel('Connections')
        plt.legend()
        plt.grid(True)
        
        # Format x-axis to show time
        plt.gcf().autofmt_xdate()
        
        # Save to buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        
        return buf

async def setup(bot):
    await bot.add_cog(DatabaseMonitor(bot))