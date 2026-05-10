# auto_react_fixed.py
import asyncio
import discord
from discord.ext import commands

# IDs you specified
TARGET_GUILD_ID = 993291181469487104
TARGET_CHANNEL_ID = 993474580935094332

class FixedAutoReact(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.upvote_emoji = "👍"
        self.downvote_emoji = "👎"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore messages from bots
        if message.author.bot:
            return

        # Check guild & channel match
        if not message.guild or message.guild.id != TARGET_GUILD_ID:
            return
        if message.channel.id != TARGET_CHANNEL_ID:
            return

        # Ensure bot has permission
        perms = message.channel.permissions_for(message.guild.me)
        if not perms.add_reactions:
            return

        try:
            await message.add_reaction(self.upvote_emoji)
            await asyncio.sleep(0.1)  # small delay for safety
            await message.add_reaction(self.downvote_emoji)
        except discord.HTTPException:
            pass  # ignore errors silently

async def setup(bot: commands.Bot):
    await bot.add_cog(FixedAutoReact(bot))
