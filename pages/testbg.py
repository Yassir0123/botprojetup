import discord
from discord import File
from discord.ext import commands
from easy_pil import Editor, Font
import asyncio

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='sl ', intents=intents)

# Preload images and font
background_path = "../images/finabg.png"
file_path = "../images/file.png"
font = None

async def load_resources():
    global font
    font = Font.poppins(size=40)

@bot.command()
async def bg(ctx):
    try:
        # Load background
        background = Editor(background_path)

        # Load and paste images
        for position in [(-295, 120), (180, 120), (720, 120), (1250, 120)]:
            pic = Editor(file_path)
            background.paste(pic.image, position)

        # Add text
        for position, text in [
            ((55, 870), "Sung jin woo(Lv.12)"),
            ((1060, 870), "Sung jin woo(Lv.12)"),
            ((535, 870), "Sung jin woo(Lv.12)"),
            ((1560, 870), "Sung jin woo(Lv.12)")
        ]:
            background.text(position, text, font=font, color="white")

        # Save the image to a bytes object
        image_bytes = background.image_bytes

        # Create a discord.File object directly from the bytes
        file = File(fp=image_bytes, filename="card.png")
        
        await ctx.send(file=file)
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await load_resources()

TOKEN = 'MTIzMTI5ODMyOTU0ODI5MjE5Nw.Gq9b9L.NYkmjq8tu9iBu0k25gMxiZI8WV5b4FPQkDPaRI'  # Replace this with your actual token when running the bot
bot.run(TOKEN)