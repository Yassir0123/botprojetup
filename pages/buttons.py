import discord
from discord.ext import commands

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

bot = Bot()

@bot.command()
async def info(ctx):
    embed = discord.Embed(title="User Information", color=discord.Color.blue())
    
    # Using Unicode characters to simulate buttons
    add_button = "➕"
    remove_button = "➖"
    
    embed.add_field(name="Name", value=f"Not provided {add_button} {remove_button}", inline=False)
    embed.add_field(name="Email", value=f"Not provided {add_button} {remove_button}", inline=False)
    embed.add_field(name="Phone", value=f"Not provided {add_button} {remove_button}", inline=False)

    await ctx.send(embed=embed)

bot.run("MTIzMTI5ODMyOTU0ODI5MjE5Nw.Gq9b9L.NYkmjq8tu9iBu0k25gMxiZI8WV5b4FPQkDPaRI")