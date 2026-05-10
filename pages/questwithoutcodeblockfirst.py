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

class GateType(Enum):
    RED_GATE = "Red Gate"
    BLUE_GATE = "Blue Gate"
    YELLOW_GATE = "Yellow Gate"
    BLACK_GATE = "Black Gate"
    DEMON_GATE = "Demon Gate"

class MissionType(Enum):
    GATE_CLEAR = "Gate Clear"
    BOSS_HUNT = "Boss Hunt"
    MONSTER_SUBJUGATION = "Monster Subjugation"
    DUNGEON_EXPLORATION = "Dungeon Exploration"
    ARTIFACT_RETRIEVAL = "Artifact Retrieval"

class QuestCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rewards = {
            "common": ["Healing Potion", "Mana Crystal", "Experience Crystal"],
            "rare": ["High Grade Magic Core", "Demon Core", "Ancient Rune"],
            "unique": ["Dragon's Breath", "Proof of Kill", "Soul Fragment"],
            "legendary": ["Gates Authority Stone", "Black Heart", "Sovereign's Essence"]
        }
        
        self.mission_emojis = {
            "Gate Clear": "🌀",
            "Boss Hunt": "👾",
            "Monster Subjugation": "💀",
            "Dungeon Exploration": "🏰",
            "Artifact Retrieval": "🗝️"
        }
        
        self.gate_colors = {
            "Red Gate": discord.Color.from_rgb(255, 0, 0),
            "Blue Gate": discord.Color.from_rgb(0, 100, 255),
            "Yellow Gate": discord.Color.from_rgb(255, 255, 0),
            "Black Gate": discord.Color.from_rgb(20, 20, 20),
            "Demon Gate": discord.Color.from_rgb(138, 43, 226)
        }

    # Keep existing database methods unchanged
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

    def get_gate_type(self, required_count: int) -> str:
        if required_count <= 5:
            return "Blue Gate"
        elif required_count <= 10:
            return "Yellow Gate"
        elif required_count <= 15:
            return "Red Gate"
        elif required_count <= 20:
            return "Black Gate"
        else:
            return "Demon Gate"

    def get_mission_type(self, quest_name: str) -> str:
        if "gate" in quest_name.lower():
            return "Gate Clear"
        elif "boss" in quest_name.lower():
            return "Boss Hunt"
        elif "monster" in quest_name.lower():
            return "Monster Subjugation"
        elif "dungeon" in quest_name.lower():
            return "Dungeon Exploration"
        else:
            return "Artifact Retrieval"

    @commands.command(name="quest")
    async def quest(self, ctx):
        discord_id = str(ctx.author.id)
        conn = create_db_connection()
        try:
            user_data = self.execute_query(conn, "SELECT user_id FROM user WHERE user_authorid = %s", (discord_id,))

            if not user_data:
                embed = discord.Embed(
                    title="⚠️ System Warning",
                    description="You have not awakened as a hunter.\nRegister first to receive your Hunter License.",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Korean Hunter Association")
                await ctx.send(embed=embed)
                return

            user_id = user_data[0]['user_id']
            quest_data = self.execute_query(conn, "SELECT quest_status, quest_daily FROM user WHERE user_id = %s", (user_id,))

            current_time = datetime.now()
            quest_daily = quest_data[0]['quest_daily']

            if not quest_daily or (current_time - quest_daily).days >= 1:
                await self.reset_quests(conn, user_id, current_time)
                embed = discord.Embed(
                    title="🌟 Gates Have Appeared",
                    description="The System has detected new gates.\nHunter, your intervention is required.",
                    color=discord.Color.gold()
                )
                await ctx.send(embed=embed)
                await self.display_quests(ctx, conn, user_id)
            else:
                if quest_data[0]['quest_status'] == QuestStatus.COMPLETE.value:
                    time_left = quest_daily + timedelta(days=1) - current_time
                    embed = discord.Embed(
                        title="⌛ System Notice",
                        description=f"All gates have been cleared.\nNext gate emergence: {self.format_time_left(time_left)}",
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=embed)
                else:
                    await self.display_quests(ctx, conn, user_id)

        except Error as e:
            print(f"Error: {e}")
            embed = discord.Embed(
                title="System Malfunction",
                description="A dimensional disturbance is blocking access to the mission board.",
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
            title="Hunter Association Mission Board",
            description="ᴀᴄᴛɪᴠᴇ ɢᴀᴛᴇs ʀᴇǫᴜɪʀɪɴɢ ɪᴍᴍᴇᴅɪᴀᴛᴇ ᴀᴛᴛᴇɴᴛɪᴏɴ",
            color=discord.Color.from_rgb(25, 25, 25)
        )

        completion_rewards = (
            "**Clear All Gates Reward:**\n"
            "» 1x S-Grade Magic Core\n"
            "» 1x Demon King's Fragment\n"
            "» Random SSS-Grade Item"
        )
        embed.add_field(name="⚜️ Completion Rewards", value=completion_rewards, inline=False)
        embed.add_field(name="═══════════════", value="", inline=False)

        for quest in quests:
            gate_type = self.get_gate_type(quest['required_count'])
            mission_type = self.get_mission_type(quest['quest_name'])
            emoji = self.mission_emojis[mission_type]
            
            progress = int((quest['current_count'] / quest['required_count']) * 100)
            progress_bar = self.create_progress_bar(quest['current_count'], quest['required_count'])
            
            status = "ACTIVE" if quest['completion_status'] == 0 else "CLEARED"
            status_emoji = "⚡" if quest['completion_status'] == 0 else "✨"

            reward_tier = "legendary" if gate_type in ["Demon Gate", "Black Gate"] else \
                         "unique" if gate_type == "Red Gate" else \
                         "rare" if gate_type == "Yellow Gate" else "common"
            
            mission_rewards = random.sample(self.rewards[reward_tier], 2)

            field_value = (
                f"{emoji} **Type:** {mission_type}\n"
                f"**Description:** {quest['quest_name']}\n"
                f"**Progress:** {progress_bar} {progress}%\n"
                f"**Status:** {status_emoji} {status}\n"
                f"**Rewards:**\n"
                f"» {mission_rewards[0]}\n"
                f"» {mission_rewards[1]}"
            )
            
            embed.add_field(
                name=f"【{gate_type}】", 
                value=field_value,
                inline=False
            )

        user_data = self.execute_query(conn, "SELECT quest_status, quest_daily FROM user WHERE user_id = %s", (user_id,))[0]
        next_reset = user_data['quest_daily'] + timedelta(days=1)
        time_left = next_reset - datetime.now()
        
        footer_text = (
            f"Next Gate Emergence: {self.format_time_left(time_left)} | "
            "Hunters can use 'Quickclear' once per day"
        )
        embed.set_footer(text=footer_text)

        await ctx.send(embed=embed)

    def create_progress_bar(self, current: int, total: int, length: int = 15) -> str:
        filled = int(length * current / total)
        empty = length - filled
        bar = "█" * filled + "░" * empty
        return f"`{bar}`"

    def format_time_left(self, time_left: timedelta) -> str:
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"

async def setup(bot):
    await bot.add_cog(QuestCommands(bot))