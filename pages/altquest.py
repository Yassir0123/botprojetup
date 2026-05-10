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

class QuestCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.quest_emojis = ["⚔️", "🏹", "🛡️", "🧪", "📚", "🎭", "🎣", "🍳", "🧵", "🎨", "🏃‍♂️", "🐉", "🧙‍♂️", "🏰", "💎"]

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

    @commands.command(name="quest")
    async def quest(self, ctx):
        discord_id = str(ctx.author.id)
        conn = create_db_connection()
        try:
            # Verify user registration
            user_data = self.execute_query(conn, "SELECT user_id FROM user WHERE user_authorid = %s", (discord_id,))
            if not user_data:
                await ctx.send("You haven't registered yet. Please register before using the quest system.")
                return

            user_id = user_data[0]['user_id']
            
            # Fetch current quest data
            quest_data = self.execute_query(conn, "SELECT quest_daily, quest_status FROM user WHERE user_id = %s", (user_id,))[0]
            
            current_time = datetime.now()
            quest_daily = quest_data['quest_daily']

            # Check if quest daily is None or if 24 hours have passed
            if quest_daily is None or (current_time - quest_daily) >= timedelta(hours=24):
                # Reset quests
                await self.reset_quests(conn, user_id, current_time)
                await ctx.send("New daily quests have appeared, brave adventurer!")
            
            # Display quests
            await self.display_quests(ctx, conn, user_id)

        except Error as e:
            print(f"Error: {e}")
            await ctx.send("A mystical force prevents me from retrieving your quests. Try again later.")
        finally:
            if conn.is_connected():
                conn.close()

    async def reset_quests(self, conn, user_id: int, current_time: datetime):
        self.execute_query(conn, "UPDATE user SET quest_status = %s, quest_daily = %s WHERE user_id = %s",
                           (QuestStatus.INCOMPLETE.value, current_time, user_id))
        self.execute_query(conn, "DELETE FROM user_quests WHERE user_id = %s", (user_id,))

        all_quests = self.execute_query(conn, "SELECT quest_id, required_count FROM quest")
        new_quests = random.sample(all_quests, min(5, len(all_quests)))

        for quest in new_quests:
            self.execute_query(conn, "INSERT INTO user_quests (user_id, quest_id, current_count, completion_status) VALUES (%s, %s, 0, %s)",
                               (user_id, quest['quest_id'], QuestStatus.INCOMPLETE.value))

    async def display_quests(self, ctx, conn, user_id: int):
        quests = self.execute_query(conn, "SELECT uq.quest_id, q.quest_name, uq.current_count, q.required_count, uq.completion_status "
                                          "FROM user_quests uq JOIN quest q ON uq.quest_id = q.quest_id WHERE uq.user_id = %s", (user_id,))

        embed = discord.Embed(title="📜 Your Daily Quests", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=ctx.author.avatar.url)  # Replace with an epic quest icon
        embed.description = "*Embark on these quests to prove your worth and reap great rewards!*"

        random.shuffle(self.quest_emojis)
        for i, quest in enumerate(quests):
            quest_emoji = self.quest_emojis[i]
            status_emoji = "🏆" if quest['completion_status'] == QuestStatus.COMPLETE.value else "⏳"
            progress = f"**{quest['current_count']}** / {quest['required_count']}"
            
            quest_field = f"{quest_emoji} **{quest['quest_name']}**\n{status_emoji} Progress: {progress}"
            embed.add_field(name="\u200b", value=quest_field, inline=False)

        user_data = self.execute_query(conn, "SELECT quest_status, quest_daily FROM user WHERE user_id = %s", (user_id,))[0]
        completed_quests = sum(1 for quest in quests if quest['completion_status'] == QuestStatus.COMPLETE.value)
        total_quests = len(quests)

        if user_data['quest_status'] == QuestStatus.COMPLETE.value:
            next_reset = user_data['quest_daily'] + timedelta(days=1)
            time_left = next_reset - datetime.now()
            embed.set_footer(text=f"⏳ The saga continues in: {self.format_time_left(time_left)}")
        else:
            embed.set_footer(text=f"🏆 Quests completed: {completed_quests}/{total_quests} | Complete all for epic rewards!")

        await ctx.send(embed=embed)

    def format_time_left(self, time_left: timedelta) -> str:
        if time_left.days > 0:
            return f"{time_left.days} days"
        elif time_left.seconds // 3600 > 0:
            return f"{time_left.seconds // 3600} hours"
        elif time_left.seconds // 60 > 0:
            return f"{time_left.seconds // 60} minutes"
        else:
            return f"{time_left.seconds} seconds"

async def setup(bot):
    await bot.add_cog(QuestCommands(bot))