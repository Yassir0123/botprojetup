import discord
import os
from discord.ext import commands, tasks
import random
import math
from datetime import datetime, timedelta
import asyncio
import time
import re
from utils.database import execute_query_with_retry, get_metrics
import functools

class AutoresetCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Server and channel IDs for reset notifications
        self.notification_server_id = 832022150713704479
        self.notification_channel_id = 1378811753055387812
        # Start the auto-reset task when the cog is loaded
        self.auto_reset_task.start()
    
    def cog_unload(self):
        # Stop the task when the cog is unloaded
        self.auto_reset_task.cancel()
    
    @tasks.loop(minutes=10)  # Check every 10 minutes
    async def auto_reset_task(self):
        """
        Automatically check if it's time to run the reset command.
        Runs every 10 minutes and executes reset if 1-2 hours have passed.
        """
        try:
            print(f"[{datetime.now()}] Auto-reset task running - checking database...")
            
            # Check the reset table for the last reset time
            check_query = "SELECT last_reset FROM reset WHERE reset_id = 1"
            result = await execute_query_with_retry(check_query)
            
            current_time = datetime.now()
            should_reset = False
            
            if not result or result[0]['last_reset'] is None:
                # No previous reset recorded, perform first reset
                print(f"[{current_time}] No previous reset found. Performing initial reset...")
                should_reset = True
                
                # Insert or update the reset record with current time
                update_query = """
                INSERT INTO reset (reset_id, last_reset) VALUES (1, %s)
                ON DUPLICATE KEY UPDATE last_reset = %s
                """
                await execute_query_with_retry(update_query, (current_time, current_time))
                
            else:
                last_reset = result[0]['last_reset']
                time_elapsed = current_time - last_reset
                
                # Check if 2 hours (7200 seconds) have passed
                if time_elapsed.total_seconds() >= 7200:  # 2 hours
                    print(f"[{current_time}] 2+ hours have passed since last reset. Time elapsed: {time_elapsed}")
                    should_reset = True
                    
                    # Update the last_reset time
                    update_query = "UPDATE reset SET last_reset = %s WHERE reset_id = 1"
                    await execute_query_with_retry(update_query, (current_time,))
                else:
                    # Calculate remaining time
                    remaining_seconds = 7200 - time_elapsed.total_seconds()
                    remaining_minutes = remaining_seconds / 60
                    print(f"[{current_time}] Reset not needed. Time remaining: {remaining_minutes:.1f} minutes")
            
            # Execute the reset if needed
            if should_reset:
                await self.execute_admin_reset()
                print(f"[{current_time}] Auto-reset completed successfully!")
                
        except Exception as e:
            print(f"[{datetime.now()}] Error in auto_reset_task: {str(e)}")
            # Log error to console but don't crash the task
            import traceback
            traceback.print_exc()
    
    @auto_reset_task.before_loop
    async def before_auto_reset_task(self):
        """Wait until the bot is ready before starting the task"""
        await self.bot.wait_until_ready()
        print(f"[{datetime.now()}] Auto-reset task initialized and ready!")
    
    async def execute_admin_reset(self):
        """
        Execute the same reset functionality as the adminreset command.
        This resets all user progress metrics.
        """
        try:
            # SQL query to reset all user progress (same as adminreset command)
            query = """
            UPDATE user SET 
                fight_current = 0,
                mission_current = 0,
                dungeon_current = 0,
                chapter_count = 0,
                squad_current = 0,
                fight_timer = NULL,
                squad_timer = NULL,
                dungeon_timer = NULL,
                mission_timer = NULL
            """
            
            # Execute the update query
            await execute_query_with_retry(query)
            
            print(f"[{datetime.now()}] Successfully reset all user progress metrics via auto-reset")
            
            # Send notification to the specified channel
            try:
                # Get the server and channel
                server = self.bot.get_guild(self.notification_server_id)
                if server:
                    channel = server.get_channel(self.notification_channel_id)
                    if channel:
                        # Create the embed notification
                        embed = discord.Embed(
                            title="🔄 Automatic Reset Executed",
                            description="All user progress metrics have been automatically reset.",
                            color=discord.Color.green(),
                            timestamp=datetime.utcnow()
                        )
                        embed.add_field(
                            name="Reset Fields",
                            value="• fight_current\n• mission_current\n• dungeon_current\n• chapter_count\n• squad_current\n• fight_timer\n• squad_timer\n• dungeon_timer\n• mission_timer",
                            inline=False
                        )
                        await channel.send(embed=embed)
                        print(f"[{datetime.now()}] Reset notification sent to channel {self.notification_channel_id}")
                    else:
                        print(f"[{datetime.now()}] Could not find channel with ID {self.notification_channel_id}")
                else:
                    print(f"[{datetime.now()}] Could not find server with ID {self.notification_server_id}")
            except Exception as notify_error:
                print(f"[{datetime.now()}] Failed to send reset notification: {str(notify_error)}")
                import traceback
                traceback.print_exc()
                
        except Exception as e:
            print(f"[{datetime.now()}] Error during auto-reset execution: {str(e)}")
            import traceback
            traceback.print_exc()
            raise e
    
    @commands.command(name='checkreset', hidden=True)
    async def check_reset_status(self, ctx):
        """
        Manual command to check the current reset status.
        Only accessible by authorized users.
        """
        # Check if the command invoker is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
        
        try:
            # Get the last reset time
            check_query = "SELECT last_reset FROM reset WHERE reset_id = 1"
            result = await execute_query_with_retry(check_query)
            
            current_time = datetime.now()
            
            if not result or result[0]['last_reset'] is None:
                embed = discord.Embed(
                    title="🔄 Reset Status",
                    description="No reset has been performed yet.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="Next Action",
                    value="The next auto-reset check will initialize the system.",
                    inline=False
                )
            else:
                last_reset = result[0]['last_reset']
                time_elapsed = current_time - last_reset
                time_until_next = timedelta(hours=2) - time_elapsed
                
                embed = discord.Embed(
                    title="🔄 Reset Status",
                    color=discord.Color.blue()
                )
                
                embed.add_field(
                    name="Last Reset",
                    value=last_reset.strftime("%Y-%m-%d %H:%M:%S"),
                    inline=True
                )
                
                embed.add_field(
                    name="Time Elapsed",
                    value=f"{int(time_elapsed.total_seconds() // 3600)}h {int((time_elapsed.total_seconds() % 3600) // 60)}m",
                    inline=True
                )
                
                if time_until_next.total_seconds() > 0:
                    embed.add_field(
                        name="Next Reset In",
                        value=f"{int(time_until_next.total_seconds() // 3600)}h {int((time_until_next.total_seconds() % 3600) // 60)}m",
                        inline=True
                    )
                    embed.color = discord.Color.green()
                else:
                    embed.add_field(
                        name="Status",
                        value="⚠️ Reset is overdue and will run on next check",
                        inline=True
                    )
                    embed.color = discord.Color.red()
            
            # Add task status
            task_status = "🟢 Running" if self.auto_reset_task.is_running() else "🔴 Stopped"
            embed.add_field(
                name="Auto-Reset Task",
                value=task_status,
                inline=False
            )
            
            # Add notification channel info
            server = self.bot.get_guild(self.notification_server_id)
            channel = server.get_channel(self.notification_channel_id) if server else None
            channel_status = f"✅ {channel.mention}" if channel else "❌ Not found"
            
            embed.add_field(
                name="Notification Channel",
                value=channel_status,
                inline=False
            )
            
            embed.timestamp = current_time
            await ctx.send(embed=embed, delete_after=30)
            
            # Delete the command message
            try:
                await ctx.message.delete()
            except:
                pass
                
        except Exception as e:
            await ctx.send(f"Error checking reset status: {str(e)}", delete_after=10)
    
    @commands.command(name='forcereset', hidden=True)
    async def force_reset(self, ctx):
        """
        Manual command to force an immediate reset.
        Only accessible by authorized users.
        """
        # Check if the command invoker is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
        
        try:
            # Execute the reset
            await self.execute_admin_reset()
            
            # Update the reset timestamp
            current_time = datetime.now()
            update_query = """
            INSERT INTO reset (reset_id, last_reset) VALUES (1, %s)
            ON DUPLICATE KEY UPDATE last_reset = %s
            """
            await execute_query_with_retry(update_query, (current_time, current_time))
            
            # Send confirmation
            embed = discord.Embed(
                title="🔄 Force Reset Complete",
                description="Successfully executed manual reset of all user progress metrics.",
                color=discord.Color.green(),
                timestamp=current_time
            )
            
            embed.add_field(
                name="Reset Fields",
                value="• fight_current\n• mission_current\n• dungeon_current\n• chapter_count\n• squad_current\n• fight_timer\n• squad_timer\n• dungeon_timer\n• mission_timer",
                inline=False
            )
            
            await ctx.send(embed=embed, delete_after=30)
            
            # Delete the command message
            try:
                await ctx.message.delete()
            except:
                pass
                
        except Exception as e:
            await ctx.send(f"Error during force reset: {str(e)}", delete_after=10)
            print(f"Force reset error: {str(e)}")
            import traceback
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(AutoresetCommands(bot))