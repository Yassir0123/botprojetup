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

class QuestRank(Enum):
    E = "E"
    D = "D"
    C = "C"
    B = "B"
    A = "A"
    S = "S"

class QuestType(Enum):
    HUNT = "Hunt"
    DUNGEON = "Dungeon"
    RAID = "Raid"
    GATHER = "Gather"
    ELIMINATION = "Elimination"

class QuestCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rewards = {
            "E": ["Gate Fragment", "Weak Mana Crystal", "Common Hunter Token"],
            "D": ["Gate Shard", "Mana Crystal", "Hunter Token"],
            "C": ["Gate Core", "Pure Mana Crystal", "Elite Hunter Token"],
            "B": ["Lesser Gate Stone", "Concentrated Mana Crystal", "Veteran Hunter Token"],
            "A": ["Gate Stone", "Perfect Mana Crystal", "Master Hunter Token"],
            "S": ["Gates Authority Stone", "Black Mana Crystal", "Sovereign Hunter Token"]
        }
        self.quest_emojis = {
            "Hunt": "☠️",
            "Dungeon": "🏰",
            "Raid": "⚔️",
            "Gather": "💎",
            "Elimination": "🗡️"
        }
        self.rank_colors = {
            "E": discord.Color.from_rgb(128, 128, 128),  # Gray
            "D": discord.Color.from_rgb(139, 69, 19),    # Brown
            "C": discord.Color.from_rgb(0, 128, 0),      # Green
            "B": discord.Color.from_rgb(0, 0, 255),      # Blue
            "A": discord.Color.from_rgb(148, 0, 211),    # Purple
            "S": discord.Color.from_rgb(255, 215, 0)     # Gold
        }

    # Keeping the existing database methods unchanged
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

    def get_quest_rank(self, required_count: int) -> str:
        if required_count <= 5:
            return "E"
        elif required_count <= 10:
            return "D"
        elif required_count <= 15:
            return "C"
        elif required_count <= 20:
            return "B"
        elif required_count <= 25:
            return "A"
        else:
            return "S"

    def get_quest_type(self, quest_name: str) -> str:
        if "hunt" in quest_name.lower():
            return "Hunt"
        elif "dungeon" in quest_name.lower():
            return "Dungeon"
        elif "raid" in quest_name.lower():
            return "Raid"
        elif "gather" in quest_name.lower():
            return "Gather"
        else:
            return "Elimination"

    @commands.command(name="quest")
    async def quest(self, ctx):
        discord_id = str(ctx.author.id)
        conn = create_db_connection()
        try:
            user_data = self.execute_query(conn, "SELECT user_id FROM user WHERE user_authorid = %s", (discord_id,))

            if not user_data:
                embed = discord.Embed(
                    title="System Notice",
                    description="❌ You haven't awakened as a hunter yet. Please register to receive your hunter rank.",
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
                    title="🌟 System Notice",
                    description="The System has generated new missions for you, Hunter.\nComplete them to grow stronger.",
                    color=discord.Color.gold()
                )
                await ctx.send(embed=embed)
                await self.display_quests(ctx, conn, user_id)
            else:
                if quest_data[0]['quest_status'] == QuestStatus.COMPLETE.value:
                    time_left = quest_daily + timedelta(days=1) - current_time
                    embed = discord.Embed(
                        title="System Notice",
                        description=f"All daily missions completed. Time until next missions: {self.format_time_left(time_left)}",
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=embed)
                else:
                    await self.display_quests(ctx, conn, user_id)

        except Error as e:
            print(f"Error: {e}")
            embed = discord.Embed(
                title="System Error",
                description="A barrier prevents access to the mission board. Try again later.",
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
            title="📜 Hunter Association Mission Board",
            color=discord.Color.from_rgb(25, 25, 25)
        )
        
        embed.description = (
            "```\n"
            "SYSTEM NOTICE:\n"
            "Complete all missions to receive:\n"
            "- 1x S-Rank Gate Key\n"
            "- Random S-Rank Reward\n"
            "```"
        )

        for i, quest in enumerate(quests, 1):
            rank = self.get_quest_rank(quest['required_count'])
            quest_type = self.get_quest_type(quest['quest_name'])
            emoji = self.quest_emojis[quest_type]
            
            progress_bar = self.create_progress_bar(quest['current_count'], quest['required_count'])
            
            status = "IN PROGRESS" if quest['completion_status'] == 0 else "COMPLETED"
            status_emoji = "⏳" if quest['completion_status'] == 0 else "✅"

            quest_text = (
                f"```\n"
                f"[Rank {rank}] {emoji} {quest_type} Mission\n"
                f"➤ {quest['quest_name']}\n"
                f"Progress: {progress_bar} ({quest['current_count']}/{quest['required_count']})\n"
                f"Status: {status_emoji} {status}\n"
                f"Rewards: {', '.join(random.sample(self.rewards[rank], 2))}\n"
                f"```"
            )
            
            embed.add_field(name=f"Mission #{i}", value=quest_text, inline=False)

        user_data = self.execute_query(conn, "SELECT quest_status, quest_daily FROM user WHERE user_id = %s", (user_id,))[0]
        next_reset = user_data['quest_daily'] + timedelta(days=1)
        time_left = next_reset - datetime.now()
        
        embed.set_footer(text=(
            f"⏰ Mission Board Refresh: {self.format_time_left(time_left)}\n"
            "💫 Hunters can instantly complete one mission per day"
        ))

        await ctx.send(embed=embed)

    def create_progress_bar(self, current: int, total: int, length: int = 20) -> str:
        filled = int(length * current / total)
        bar = "█" * filled + "░" * (length - filled)
        return bar

    def format_time_left(self, time_left: timedelta) -> str:
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def setup(bot):
    await bot.add_cog(QuestCommands(bot))