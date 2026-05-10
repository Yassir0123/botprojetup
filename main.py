import discord
from discord.ext import commands
from config import TOKEN

# Set up bot with message content intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=('sl ', 'Sl ','SL ','sL '), intents=intents)
bot.remove_command('help')

async def main():
    # Load all command extensions
    await bot.load_extension("pages.register")#done aio
    await bot.load_extension("pages.databasemonitor")
    await bot.load_extension("pages.tutorial")
    await bot.load_extension("pages.team")#done aio (make sure to avoid doing[0]next to execuyte result and get it alone )
    await bot.load_extension("pages.spawn_see")#done aio
    await bot.load_extension("pages.shop")#done aio
    await bot.load_extension("pages.inventory")#done aio
    #await bot.load_extension("pages.upgrade")
    await bot.load_extension("pages.dbreset")
  # await bot.load_extension("pages.guilds")
    #await bot.load_extension("pages.squad")
    await bot.load_extension("pages.dungeons")#dungeon done aio
    await bot.load_extension("pages.missions")#done aio
    await bot.load_extension("pages.trade")#done aio
    #await bot.load_extension("pages.games")
    #await bot.load_extension("pages.botadmin")#done aio
    await bot.load_extension("pages.dailies")#done aio
    await bot.load_extension("pages.quest")#done aio
    await bot.load_extension("pages.help")#done aio
    await bot.load_extension("pages.gambling")#done aio
    await bot.load_extension("pages.prenium")#done aio
    await bot.load_extension("pages.snail")#done aio
    await bot.load_extension("pages.autoreact")#done aio
    #await bot.load_extension("pages.userphone")#done aio
    await bot.load_extension("pages.adventure")#done aio
    await bot.load_extension("pages.ascend")#done aio
    #await bot.load_extension("pages.squad")#done
    await bot.load_extension("pages.autoreset")
    await bot.load_extension("pages.vote")
    await bot.load_extension("pages.skill")#done aio
    await bot.load_extension("pages.market")#done aio
    await bot.load_extension("pages.referal")
    await bot.load_extension("pages.leaderboard")
    #await bot.load_extension("pages.inventory_view")
#    await bot.load_extension("pages.fight_challenge")
    await bot.load_extension("pages.fight_challenge_embed")#done aio
    #await bot.load_extension("pages.automod")#done
    #await bot.load_extension("pages.autofight")#done
    # Run the bot with the token
    await bot.start(TOKEN)

# Run the main function
import asyncio
asyncio.run(main())
