import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta
from utils.database import create_db_connection
from mysql.connector import Error
import asyncio
from typing import List, Dict, Union
from enum import Enum

class QuestStatus(Enum):
    INCOMPLETE = 0
    COMPLETE = 1

class DifficultyType(Enum):
    NORMAL = "Normal"
    HIGH = "High"
    DANGER = "Danger"
    DISASTER = "Disaster"

class QuestCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rewards = {
            "Normal": ["Magic Core", "Healing Crystal", "Mana Stone"],
            "High": ["Greater Magic Core", "Monster Core", "Ancient Rune"],
            "Danger": ["Demon Core", "Dragon's Essence", "Soul Fragment"],
            "Disaster": ["Black Heart", "Sovereign's Breath", "S-Grade Magic Core"]
        }
        
        self.difficulty_colors = {
            "Normal": discord.Color.from_rgb(67, 181, 129),
            "High": discord.Color.from_rgb(250, 168, 26),
            "Danger": discord.Color.from_rgb(240, 71, 71),
            "Disaster": discord.Color.from_rgb(138, 43, 226)
        }
        
        self.status_indicators = {
            0: "⚡ ACTIVE",
            1: "✨ CLEARED"
        }

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

    def get_difficulty(self, required_count: int) -> str:
        if required_count <= 5:
            return "Normal"
        elif required_count <= 10:
            return "High"
        elif required_count <= 15:
            return "Danger"
        else:
            return "Disaster"

    @commands.command(name="quest")
    async def quest(self, ctx):
        discord_id = str(ctx.author.id)
        conn = create_db_connection()
        try:
            user_data = self.execute_query(conn, "SELECT user_id FROM user WHERE user_authorid = %s", (discord_id,))

            if not user_data:
                embed = discord.Embed(
                    title="System Notice",
                    description="Hunter awakening required.\nComplete registration to receive your license.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            user_id = user_data[0]['user_id']
            quest_data = self.execute_query(conn, "SELECT quest_status, quest_daily FROM user WHERE user_id = %s", (user_id,))

            current_time = datetime.now()
            quest_daily = quest_data[0]['quest_daily']

            if not quest_daily or (current_time - quest_daily).days >= 1:
                await self.reset_quests(conn, user_id, current_time)
                embed = discord.Embed(
                    title="New Gates Detected",
                    description="The System has assigned new missions.\nProceed with caution, Hunter.",
                    color=discord.Color.gold()
                )
                await ctx.send(embed=embed)
                await self.display_quests(ctx, conn, user_id)
            else:
                if quest_data[0]['quest_status'] == QuestStatus.COMPLETE.value:
                    time_left = quest_daily + timedelta(days=1) - current_time
                    embed = discord.Embed(
                        title="Gates Cleared",
                        description=f"Next emergence in: {self.format_time_left(time_left)}",
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=embed)
                else:
                    await self.display_quests(ctx, conn, user_id)

        except Error as e:
            print(f"Error: {e}")
            embed = discord.Embed(
                title="System Error",
                description="A dimensional barrier is interfering with the connection.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        finally:
            if conn.is_connected():
                conn.close()

    async def display_quests(self, ctx, conn, user_id: int):
        quests = self.execute_query(conn, """
            SELECT uq.quest_id, q.quest_name, uq.current_count, q.required_count, 
                   uq.completion_status
            FROM user_quests uq 
            JOIN quest q ON uq.quest_id = q.quest_id 
            WHERE uq.user_id = %s
        """, (user_id,))

        embed = discord.Embed(
            title="Hunter Association Terminal",
            description="ᴘʀɪᴏʀɪᴛʏ ᴍɪssɪᴏɴs ʀᴇǫᴜɪʀɪɴɢ ɪᴍᴍᴇᴅɪᴀᴛᴇ ᴀᴛᴛᴇɴᴛɪᴏɴ",
            color=discord.Color.from_rgb(25, 25, 25)
        )

        # Daily Completion Rewards Section
        rewards_text = (
            "**❯ SSS-Grade Magic Core**\n"
            "**❯ Demon King's Fragment**\n"
            "**❯ Random S-Grade Item**"
        )
        embed.add_field(
            name="⚜️ Clear All Gates Rewards",
            value=rewards_text,
            inline=False
        )
        
        embed.add_field(name="═══════════════", value="", inline=False)

        # Missions Section
        for quest in quests:
            difficulty = self.get_difficulty(quest['required_count'])
            progress = int((quest['current_count'] / quest['required_count']) * 100)
            status = self.status_indicators[quest['completion_status']]
            
            quest_text = (
                f"**Progress:** {self.create_progress_bar(quest['current_count'], quest['required_count'])} {progress}%\n"
                f"**Status:** {status}\n"
                f"**Reward:** {random.choice(self.rewards[difficulty])}"
            )
            
            embed.add_field(
                name=f"{quest['quest_name']}",
                value=quest_text,
                inline=False
            )

        # Footer with time remaining
        user_data = self.execute_query(conn, "SELECT quest_status, quest_daily FROM user WHERE user_id = %s", (user_id,))[0]
        next_reset = user_data['quest_daily'] + timedelta(days=1)
        time_left = next_reset - datetime.now()
        
        footer_text = f"Next Reset: {self.format_time_left(time_left)} | Daily Quickclear Available"
        embed.set_footer(text=footer_text)

        await ctx.send(embed=embed)

    def create_progress_bar(self, current: int, total: int, length: int = 10) -> str:
        filled = int(length * current / total)
        empty = length - filled
        bar = "■" * filled + "□" * empty
        return f"`{bar}`"

    def format_time_left(self, time_left: timedelta) -> str:
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}h {minutes:02d}m"

async def setup(bot):
    await bot.add_cog(QuestCommands(bot))