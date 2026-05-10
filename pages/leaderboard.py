# Existing imports from wrap.py
import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta
import logging
import time
import asyncio
from utils.database import execute_query_with_retry, get_metrics
import functools

# Configure logging for the cog
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LeaderboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_username_from_discord_id(self, user_authorid: str) -> str:
        """Fetches the Discord username from a user_authorid (Discord ID)."""
        try:
            user = await self.bot.fetch_user(int(user_authorid))
            # Prefer global_name if available, otherwise use name
            return user.global_name or user.name
        except discord.NotFound:
            logging.warning(f"Discord user with ID {user_authorid} not found.")
            return f"Unknown User ({user_authorid})"
        except Exception as e:
            logging.error(f"Error fetching user {user_authorid}: {e}")
            return f"Error User ({user_authorid})"

    async def _send_leaderboard_embed(self, ctx, title: str, description: str, fields: list):
        """Helper to send a formatted leaderboard embed."""
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blue()
        )
        if not fields:
            embed.add_field(name="No Data", value="No entries found for this leaderboard.", inline=False)
        else:
            for name, value in fields:
                embed.add_field(name=name, value=value, inline=False)
        await ctx.send(embed=embed)

    # --- Card-based Leaderboards ---

    @commands.command(name="lb_power", help="Shows the top cards by power_stat.")
    async def leaderboard_power_stat(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25: # Limit to prevent excessively long embeds
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.power_stat, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        ORDER BY uc.power_stat DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                power_stat = row['power_stat']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nPower: {power_stat}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Power Stat",
                "Ranking of cards based on their `power_stat`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching power stat leaderboard: {e}")
            await ctx.send("An error occurred while fetching the power stat leaderboard.")

    @commands.command(name="lb_attack", help="Shows the top cards by attack_stat.")
    async def leaderboard_attack_stat(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.attack_stat, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        ORDER BY uc.attack_stat DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                attack_stat = row['attack_stat']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nAttack: {attack_stat}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Attack Stat",
                "Ranking of cards based on their `attack_stat`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching attack stat leaderboard: {e}")
            await ctx.send("An error occurred while fetching the attack stat leaderboard.")

    @commands.command(name="lb_health", help="Shows the top cards by health_stat.")
    async def leaderboard_health_stat(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.health_stat, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        ORDER BY uc.health_stat DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                health_stat = row['health_stat']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nHealth: {health_stat}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Health Stat",
                "Ranking of cards based on their `health_stat`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching health stat leaderboard: {e}")
            await ctx.send("An error occurred while fetching the health stat leaderboard.")

    @commands.command(name="lb_speed", help="Shows the top cards by speed_stat.")
    async def leaderboard_speed_stat(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.speed_stat, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        ORDER BY uc.speed_stat DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                speed_stat = row['speed_stat']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nSpeed: {speed_stat}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Speed Stat",
                "Ranking of cards based on their `speed_stat`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching speed stat leaderboard: {e}")
            await ctx.send("An error occurred while fetching the speed stat leaderboard.")

    @commands.command(name="lb_defence", help="Shows the top cards by defence_stat.")
    async def leaderboard_defence_stat(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.defence_stat, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        ORDER BY uc.defence_stat DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                defence_stat = row['defence_stat']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nDefence: {defence_stat}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Defence Stat",
                "Ranking of cards based on their `defence_stat`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching defence stat leaderboard: {e}")
            await ctx.send("An error occurred while fetching the defence stat leaderboard.")

    @commands.command(name="lb_base_attack", help="Shows the top cards by base_attack.")
    async def leaderboard_base_attack(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.base_attack, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        WHERE uc.base_attack IS NOT NULL
        ORDER BY uc.base_attack DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                base_attack = row['base_attack']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nBase Attack: {base_attack}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Base Attack",
                "Ranking of cards based on their `base_attack`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching base attack leaderboard: {e}")
            await ctx.send("An error occurred while fetching the base attack leaderboard.")

    @commands.command(name="lb_base_health", help="Shows the top cards by base_health.")
    async def leaderboard_base_health(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.base_health, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        WHERE uc.base_health IS NOT NULL
        ORDER BY uc.base_health DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                base_health = row['base_health']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nBase Health: {base_health}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Base Health",
                "Ranking of cards based on their `base_health`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching base health leaderboard: {e}")
            await ctx.send("An error occurred while fetching the base health leaderboard.")

    @commands.command(name="lb_base_speed", help="Shows the top cards by base_speed.")
    async def leaderboard_base_speed(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.base_speed, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        WHERE uc.base_speed IS NOT NULL
        ORDER BY uc.base_speed DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                base_speed = row['base_speed']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nBase Speed: {base_speed}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Base Speed",
                "Ranking of cards based on their `base_speed`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching base speed leaderboard: {e}")
            await ctx.send("An error occurred while fetching the base speed leaderboard.")

    @commands.command(name="lb_base_defence", help="Shows the top cards by base_defence.")
    async def leaderboard_base_defence(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT uc.name AS card_name, uc.base_defence, u.user_authorid
        FROM user_cards uc
        JOIN user u ON uc.user_id = u.user_id
        WHERE uc.base_defence IS NOT NULL
        ORDER BY uc.base_defence DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                card_name = row['card_name']
                base_defence = row['base_defence']
                user_authorid = row['user_authorid']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {card_name}", f"Owner: {username}\nBase Defence: {base_defence}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Cards by Base Defence",
                "Ranking of cards based on their `base_defence`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching base defence leaderboard: {e}")
            await ctx.send("An error occurred while fetching the base defence leaderboard.")

    # --- User-based Leaderboards ---

    @commands.command(name="lb_wins", help="Shows the top users by all-time wins.")
    async def leaderboard_alltimewins(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT user_authorid, alltimewins
        FROM user
        ORDER BY alltimewins DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                user_authorid = row['user_authorid']
                alltimewins = row['alltimewins']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {username}", f"Wins: {alltimewins}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Users by All-Time Wins",
                "Ranking of users based on their `alltimewins`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching all-time wins leaderboard: {e}")
            await ctx.send("An error occurred while fetching the all-time wins leaderboard.")

    @commands.command(name="lb_low_dungeon", help="Shows the top users by low dungeon points.")
    async def leaderboard_low_dungeon_points(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT user_authorid, low_dungeon_points
        FROM user
        ORDER BY low_dungeon_points DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                user_authorid = row['user_authorid']
                low_dungeon_points = row['low_dungeon_points']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {username}", f"Low Dungeon Points: {low_dungeon_points}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Users by Low Dungeon Points",
                "Ranking of users based on their `low_dungeon_points`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching low dungeon points leaderboard: {e}")
            await ctx.send("An error occurred while fetching the low dungeon points leaderboard.")

    @commands.command(name="lb_mid_dungeon", help="Shows the top users by mid dungeon points.")
    async def leaderboard_mid_dungeon_points(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT user_authorid, mid_dungeon_points
        FROM user
        ORDER BY mid_dungeon_points DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                user_authorid = row['user_authorid']
                mid_dungeon_points = row['mid_dungeon_points']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {username}", f"Mid Dungeon Points: {mid_dungeon_points}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Users by Mid Dungeon Points",
                "Ranking of users based on their `mid_dungeon_points`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching mid dungeon points leaderboard: {e}")
            await ctx.send("An error occurred while fetching the mid dungeon points leaderboard.")

    @commands.command(name="lb_high_dungeon", help="Shows the top users by high dungeon points.")
    async def leaderboard_high_dungeon_points(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT user_authorid, high_dungeon_points
        FROM user
        ORDER BY high_dungeon_points DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                user_authorid = row['user_authorid']
                high_dungeon_points = row['high_dungeon_points']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {username}", f"High Dungeon Points: {high_dungeon_points}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Users by High Dungeon Points",
                "Ranking of users based on their `high_dungeon_points`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching high dungeon points leaderboard: {e}")
            await ctx.send("An error occurred while fetching the high dungeon points leaderboard.")

    @commands.command(name="lb_missions_streak", help="Shows the top users by mission streak.")
    async def leaderboard_missions_streak(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT user_authorid, mission_streak
        FROM user
        ORDER BY mission_streak DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                user_authorid = row['user_authorid']
                mission_streak = row['mission_streak']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {username}", f"Mission Streak: {mission_streak}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Users by Mission Streak",
                "Ranking of users based on their `mission_streak`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching mission streak leaderboard: {e}")
            await ctx.send("An error occurred while fetching the mission streak leaderboard.")

    @commands.command(name="lb_roll_total", help="Shows the top users by total rolls.")
    async def leaderboard_roll_total(self, ctx, limit: int = 10):
        if not 1 <= limit <= 25:
            return await ctx.send("Please provide a limit between 1 and 25.")

        query = """
        SELECT user_authorid, roll_total
        FROM user
        ORDER BY roll_total DESC
        LIMIT %s;
        """
        try:
            results = await execute_query_with_retry(query, (limit,))
            fields = []
            for i, row in enumerate(results):
                user_authorid = row['user_authorid']
                roll_total = row['roll_total']
                username = await self.get_username_from_discord_id(user_authorid)
                fields.append((f"#{i+1} {username}", f"Total Rolls: {roll_total}"))
            
            await self._send_leaderboard_embed(
                ctx,
                f"Top {limit} Users by Total Rolls",
                "Ranking of users based on their `roll_total`.",
                fields
            )
        except Exception as e:
            logging.error(f"Error fetching roll total leaderboard: {e}")
            await ctx.send("An error occurred while fetching the roll total leaderboard.")

# This function is required by discord.py to load the cog
async def setup(bot):
    await bot.add_cog(LeaderboardCommands(bot))
