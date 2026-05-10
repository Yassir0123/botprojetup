import discord
from discord.ext import commands
from discord import ui
import random
from datetime import datetime, timedelta
import logging
import time
import asyncio
from utils.database import execute_query_with_retry, get_metrics
import functools
from utils.captcha import captcha_handler
# --- Localization helpers provided by you (DO NOT import anything extra) ---
async def get_user_multilanguage(user_authorid: int) -> int:
    """
    Fetch multilanguage flag for a Discord user. Fallback to 0 (English).
    0 = English, 1 = Arabic, 3 = French
    """
    try:
        q = "SELECT multilanguage FROM user WHERE user_authorid = %s"
        res = await execute_query_with_retry(q, (str(user_authorid),))
        if res and 'multilanguage' in res[0] and res[0]['multilanguage'] is not None:
            return int(res[0]['multilanguage'])
    except Exception as e:
        # Do not log per your request (no logging changes); silently fall back
        pass
    return 0

def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

async def _get_lang(discord_user_id: int) -> int:
    try:
        return await get_user_multilanguage(int(discord_user_id))
    except Exception:
        return 0
# ---------------------------------------------------------------------------

# Create a command cooldown decorator
def command_cooldown(rate=1, per=3.0, type=commands.BucketType.user):
    """
    A robust cooldown decorator that prevents command spam
    """
    cooldown = commands.Cooldown(rate, per)
    cooldowns = {}
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            # Get the bucket key based on the cooldown type
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id
                
            # Check if the command is on cooldown
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    # Command is on cooldown
                    remaining = round(retry_after, 1)
                    await ctx.send(_tr(
                        await _get_lang(ctx.author.id),
                        f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                        f"⏰ الأمر قيد التهدئة. الرجاء الانتظار {remaining} ثانية.",
                        f"⏰ Commande en recharge. Veuillez attendre {remaining} secondes."
                    ))
                    return
            else:
                # Create a new cooldown bucket
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
                
            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator


class UserCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds
        self.image_sets = {
            "spawn_team_upgrade": [
                "https://i.imgur.com/w9HGDs8.png", "https://i.imgur.com/eXDoVtt.png",
                "https://i.imgur.com/BfP3SOb.png", "https://i.imgur.com/0eom9m2.png",
                "https://i.imgur.com/SR5BaxV.png", "https://i.imgur.com/5Zt3z4s.png",
                "https://i.imgur.com/UUYgw06.png", "https://i.imgur.com/dMhOikE.png",
                "https://i.imgur.com/KmTcWGw.png", "https://i.imgur.com/HOJ3lQm.png",
                "https://i.imgur.com/pYO9Hdw.png", "https://i.imgur.com/GosywXi.png"
            ],
            "shop_inventory": [
                "https://i.imgur.com/taYs4nE.png", "https://i.imgur.com/N6Vt0Sl.png",
                "https://i.imgur.com/GUTEKl7.png", "https://i.imgur.com/sVZ0Wh9.png",
                "https://i.imgur.com/C15hUhb.png", "https://i.imgur.com/D8G0dKd.png",
                "https://i.imgur.com/I0TMl0z.png", "https://i.imgur.com/5RWHQpQ.png"
            ],
            "trading": [
                "https://i.imgur.com/nFplh2w.png", "https://i.imgur.com/cg4dNQb.png",
                "https://i.imgur.com/OzMTaLu.png", "https://i.imgur.com/QMRXHRa.png",
                "https://i.imgur.com/WmvzrfW.png", "https://i.imgur.com/a2jtSty.png"
            ],
            "dailies_quests": [
                "https://i.imgur.com/LLZVIRn.png", "https://i.imgur.com/vFQlJYN.png",
                "https://i.imgur.com/EK0zmxg.png"
            ],
            "dungeons_missions": [
                "https://i.imgur.com/qqx1Zqf.png", "https://i.imgur.com/1Zu8XwN.png",
                "https://i.imgur.com/IYNjRCU.png"
            ],
            "fight_challenges": [
                "https://i.imgur.com/AzvQR77.png", "https://i.imgur.com/udyM9uH.png",
                "https://i.imgur.com/KkkP00m.png"
            ]
        }
        self.default_images = [
            "https://64.media.tumblr.com/26e86a3d6bad8d52abcff52e4a80982a/835b8a36ae6f0bc1-3f/s500x750/46d03db11288f7fb36a96439262fcd4464285d34.jpg",
            "https://i.imgur.com/rGROBpj.jpeg",
            "https://i.imgur.com/MjY6B04.jpeg",
            "https://i.imgur.com/LFbWzgl.jpeg",
            "https://i.imgur.com/tXEx2pf.jpeg",
            "https://i.imgur.com/sCw46hJ.jpeg",
            "https://i.imgur.com/c0Yps3b.jpeg",
            "https://i.imgur.com/npesxBf.jpeg",
            "https://i.imgur.com/IeScbWE.png"
        ]
        self.fast_scroll_sets = ["dungeons_missions", "trading", "fight_challenges"]

    @commands.command(name='guide',aliases=["gd"])
    async def guide(self, ctx):
        lang = await _get_lang(ctx.author.id)

        current_index = 0
        current_set = "default"
        image_links = self.default_images

        # Localized UI strings
        title_guide = _tr(lang, "Hunter's Guide", "دليل الصياد", "Guide du Chasseur")
        label_skip_intro = _tr(lang, "Skip Intro", "تخطي المقدمة", "Passer l’intro")
        label_go_back = _tr(lang, "Go Back", "عودة", "Retour")
        placeholder_select = _tr(lang, "Choose an option", "اختر خيارًا", "Choisissez une option")
        cat_spawn = _tr(lang, "Spawn-Team-Upgrade", "الاستدعاء-الفريق-الترقية", "Invocation-Équipe-Amélioration")
        cat_shop = _tr(lang, "Shop-Inventory", "المتجر-الجرد", "Boutique-Inventaire")
        cat_dungeon = _tr(lang, "Dungeons-Missions", "الأبراج-المهام", "Donjons-Missions")
        cat_trading = _tr(lang, "Trading", "التبادل", "Échanges")
        cat_daily = _tr(lang, "Dailies-Quests", "اليومي-المهام", "Quotidiens-Quêtes")
        cat_fight = _tr(lang, "Fight-Challenges", "القتال-التحديات", "Combats-Défis")

        async def update_message():
            new_embed = discord.Embed(
                title=title_guide,
                color=discord.Color.blue()
            )
            new_embed.set_image(url=image_links[current_index])
            
            view = discord.ui.View(timeout=None)
            
            if current_set == "default":
                if current_index == len(image_links) - 1:
                    view.add_item(select_menu)
                    view.add_item(previous_button)
                    view.add_item(next_button)
                else:
                    view.add_item(previous_button)
                    view.add_item(next_button)
                    view.add_item(skip_intro_button)
            else:
                view.add_item(select_menu)
                view.add_item(previous_button)
                view.add_item(next_button)
                view.add_item(go_back_button)
            
            await message.edit(embed=new_embed, view=view)

        async def scroll_callback(interaction, direction):
            nonlocal current_index
            if direction == "previous":
                current_index = (current_index - 1) % len(image_links)
            else:
                current_index = (current_index + 1) % len(image_links)
            await update_message()
            await interaction.response.defer()

        async def previous_callback(interaction):
            await scroll_callback(interaction, "previous")

        async def next_callback(interaction):
            await scroll_callback(interaction, "next")

        async def skip_intro_callback(interaction):
            nonlocal current_index
            current_index = len(image_links) - 1
            await update_message()
            await interaction.response.defer()

        async def go_back_callback(interaction):
            nonlocal current_set, image_links, current_index
            current_set = "default"
            image_links = self.default_images
            current_index = len(image_links) - 1
            await update_message()
            await interaction.response.defer()

        async def select_callback(interaction):
            nonlocal current_set, image_links, current_index
            selected_option = interaction.data['values'][0]
            current_set = selected_option
            image_links = self.image_sets[selected_option]
            current_index = 0
            await update_message()
            await interaction.response.defer()

        previous_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="◀️")
        next_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="▶️")
        skip_intro_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label=label_skip_intro)
        go_back_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label=label_go_back)

        previous_button.callback = previous_callback
        next_button.callback = next_callback
        skip_intro_button.callback = skip_intro_callback
        go_back_button.callback = go_back_callback

        select_menu = discord.ui.Select(
            placeholder=placeholder_select,
            options=[
                discord.SelectOption(label=cat_spawn, value="spawn_team_upgrade"),
                discord.SelectOption(label=cat_shop, value="shop_inventory"),
                discord.SelectOption(label=cat_dungeon, value="dungeons_missions"),
                discord.SelectOption(label=cat_trading, value="trading"),
                discord.SelectOption(label=cat_daily, value="dailies_quests"),
                discord.SelectOption(label=cat_fight, value="fight_challenges")
            ]
        )
        select_menu.callback = select_callback

        initial_embed = discord.Embed(
            title=title_guide,
            color=discord.Color.blue()
        )
        initial_embed.set_image(url=image_links[current_index])

        initial_view = discord.ui.View(timeout=None)
        initial_view.add_item(previous_button)
        initial_view.add_item(next_button)
        initial_view.add_item(skip_intro_button)

        message = await ctx.send(embed=initial_embed, view=initial_view)
        await update_message()

    @commands.command(aliases=['balance', 'wallet', 'b'])
    async def bal(self, ctx):
        lang = await _get_lang(ctx.author.id)

        # Predefined rank colors
        rank_colors = {
            2: discord.Color.from_rgb(255, 215, 0),  # S Rank - Gold
            3: discord.Color.from_rgb(120, 0, 164),  # A Rank - Purple
            4: discord.Color.from_rgb(0, 0, 255),    # B Rank - Blue
            5: discord.Color.from_rgb(32, 131, 39),  # C Rank - Green
            6: discord.Color.from_rgb(154, 12, 6),   # D Rank - Red
            7: discord.Color.from_rgb(128, 128, 128) # E Rank - Gray
        }

        user_authorid = str(ctx.author.id)
        
        # Updated query to use user_cards table and join with card_rank
        query = """
        SELECT 
            u.won_currency,u.gamble_currency,u.candy_currency,
            COUNT(uc.card_id) as total_cards,
            MAX(c.card_rank) as highest_rank_id,
            MAX(cr.cardrank_name) as highest_rank_name
        FROM 
            user u
        LEFT JOIN 
            user_cards uc ON u.user_id = uc.user_id
        LEFT JOIN 
            card c ON uc.card_id = c.card_id
        LEFT JOIN
            card_rank cr ON c.card_rank = cr.cardrank_id
        WHERE 
            u.user_authorid = %s
        GROUP BY 
            u.user_id
        """
        result = await execute_query_with_retry(query, (user_authorid,))
        
        # Check if user exists
        if not result:
            embed = discord.Embed(
                title=_tr(lang, "❌ Account Not Found", "❌ الحساب غير موجود", "❌ Compte introuvable"),
                description=_tr(
                    lang,
                    "You haven't registered yet. Use `sl register` to start your journey!",
                    "لم تقم بالتسجيل بعد. استخدم `sl register` لبدء رحلتك!",
                    "Vous ne vous êtes pas encore inscrit. Utilisez `sl register` pour commencer votre aventure !"
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Extract user data
        user_data = result[0]
        balance = user_data['won_currency']
        gbalance = user_data['gamble_currency']
        cbalance = user_data['candy_currency']
        total_cards = user_data.get('total_cards', 0)
        highest_rank_id = user_data.get('highest_rank_id', None)
        highest_rank_name = user_data.get('highest_rank_name', 'No Rank')

        # Determine embed color based on highest rank
        embed_color = rank_colors.get(highest_rank_id, discord.Color.gold())

        # Create a stylish embed
        embed = discord.Embed(
            title=_tr(lang, f"Hunter's Wallet: {ctx.author.name}", f"محفظة الصياد: {ctx.author.name}", f"Portefeuille du Chasseur : {ctx.author.name}"),
            color=embed_color
        )

        # Set user's avatar as thumbnail
        if ctx.author.avatar and hasattr(ctx.author.avatar, 'url'):
            embed.set_thumbnail(url=ctx.author.avatar.url)

        # Wallet section with animated emoji
        wallet_emoji = "💳"
        embed.add_field(
            name=_tr(lang, f"{wallet_emoji} Current Balance", f"{wallet_emoji} الرصيد الحالي", f"{wallet_emoji} Solde actuel"),
            value=_tr(
                lang,
                f"Won: **{balance:,}** <:won:1304034228966658048>\nGWon: **{gbalance:,}** <:gamble:1314341368176574564>\nCandy: **{cbalance:,}** <:candy:1426599572012269578>",
                f"وون: **{balance:,}** <:won:1304034228966658048>\nجي-وون: **{gbalance:,}** <:gamble:1314341368176574564>\nحلوى: **{cbalance:,}** <:candy:1426599572012269578>",
                f"Won : **{balance:,}** <:won:1304034228966658048>\nGWon : **{gbalance:,}** <:gamble:1314341368176574564>\nBonbons : **{cbalance:,}** <:candy:1426599572012269578>"
            ),

            inline=False
        )

        # Dynamic progress and tips (localized)
        tips_en = [
            "Every card brings you closer to becoming a legend!",
            "Keep collecting and watch your power grow!",
            "Your collection tells the story of your journey!",
            "Each card is a step towards greatness!"
        ]
        tips_ar = [
            "كل بطاقة تقربك من أن تصبح أسطورة!",
            "واصل الجمع وشاهد قوتك تنمو!",
            "مجموعتك تحكي قصة رحلتك!",
            "كل بطاقة خطوة نحو العظمة!"
        ]
        tips_fr = [
            "Chaque carte te rapproche de la légende !",
            "Continue de collectionner et vois ta puissance grandir !",
            "Ta collection raconte l’histoire de ton voyage !",
            "Chaque carte est un pas vers la grandeur !"
        ]
        tips = tips_en if lang == 0 else (tips_ar if lang == 1 else tips_fr)
        
        embed.set_footer(text=random.choice(tips))

        await ctx.send(embed=embed)

    @commands.command(name='register',aliases=["reg", "rg"])
    async def register(self, ctx):
        # *** DO NOT TOUCH per your request ***
        user_authorid = str(ctx.author.id)
        
        # Check if user already exists
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (user_authorid,))
        
        if result:
            embed = discord.Embed(
                title="🚫 Registration Failed",
                description="It seems you've already joined the ranks, Hunter. Use sl mycards or sl mc to view your current collection.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://i.imgur.com/X3ViR9W.png")
            await ctx.send(embed=embed)
            return

        # If user is not registered, show introduction embed
        intro_embed = discord.Embed(
            title="Welcome to the World of Hunters!",
            description="Greetings, aspiring Hunter! You stand at the threshold of a world filled with danger, excitement, and untold treasures. Are you ready to join the ranks of the Hunter's Association?",
            color=discord.Color.blue()
        )
        intro_embed.set_image(url="https://i.imgur.com/DDjTlYa.jpeg")
        intro_embed.add_field(name="Your Destiny Awaits", value="Choose wisely, for your journey as a Hunter begins now!", inline=False)

        view = discord.ui.View()
        register_button = discord.ui.Button(style=discord.ButtonStyle.success, label="Register")
        guide_button = discord.ui.Button(style=discord.ButtonStyle.primary, label="Guide")

        async def register_callback(interaction):
            # Check if the interaction user is the same as the command invoker
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("You cannot use this button. Only the original user can register.", ephemeral=True)
                return

            await interaction.response.defer()
            
            # RECHECK: Ensure user is not already registered
            user_authorid = str(interaction.user.id)
            check_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            existing_user = await execute_query_with_retry(check_query, (user_authorid,))
            
            if existing_user:
                # User is already registered, disable buttons and show error
                view.clear_items()
                register_button.disabled = True
                guide_button.disabled = True
                await interaction.edit_original_response(view=view)
                
                embed = discord.Embed(
                    title="🚫 Registration Failed",
                    description="It seems you've already joined the ranks, Hunter. Use sl mycards or sl mc to view your current collection.",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(url="https://i.imgur.com/X3ViR9W.png")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Disable buttons after interaction
            view.clear_items()
            register_button.disabled = True
            guide_button.disabled = True
            await interaction.edit_original_response(view=view)
            
            await self.complete_registration(ctx, user_authorid)

        async def guide_callback(interaction):
            # Check if the interaction user is the same as the command invoker
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("You cannot use this button. Only the original user can view the guide.", ephemeral=True)
                return

            await interaction.response.defer()
            
            # Disable buttons after interaction
            view.clear_items()
            register_button.disabled = True
            guide_button.disabled = True
            await interaction.edit_original_response(view=view)
            
            await self.show_guide(ctx, intro_embed, message)

        register_button.callback = register_callback
        guide_button.callback = guide_callback

        view.add_item(register_button)
        view.add_item(guide_button)

        message = await ctx.send(embed=intro_embed, view=view)

    async def complete_registration(self, ctx, user_authorid):
        # *** DO NOT TOUCH per your request ***
        query = """INSERT INTO user (user_authorid, entry_date, rank, won_currency, roll_cap, fragment_cap,team_count,team_cap) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        await execute_query_with_retry(query, (user_authorid, current_date, 1, 100000, 11, 200, 1, 4))

        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (user_authorid,))
        user_id = result[0]['user_id']

        query = "INSERT INTO team (user_id,team_bg) VALUES (%s,'curr.png')"
        await execute_query_with_retry(query, (user_id,))
        queryd = "SELECT team_id FROM team WHERE user_id = %s"
        results = await execute_query_with_retry(queryd, (user_id,))
        team_id = results[0]['team_id']
        query = "UPDATE user SET current_team = %s WHERE user_id = %s"
        await execute_query_with_retry(query, (team_id,user_id))

        query = "INSERT INTO inventory (user_id) VALUES (%s)"
        await execute_query_with_retry(query, (user_id,))
        embed = discord.Embed(
            title="🏆 Welcome to the Hunter's Association!",
            description=(
                "*Congratulations on your registration, brave Hunter!*\n\n"
                "You have taken your first step into a world of adventure, danger, and untold riches. "
                "As a newly registered Hunter, you've been granted a set of powerful starter cards to aid you in your journey."
            ),
            color=discord.Color.gold()
        )

        embed.add_field(
            name="💰 Starting Resources",
            value=f"• `Won`: 100000 <:won:1304034228966658048>\n",
            inline=False
        )
        embed.add_field(
            name="⚔️ Ready for Battle?",
            value=(
                "Your journey to becoming an S-Rank Hunter starts now! Here are some commands to get you started:\n"
                "• `sl mycards` - View your card collection\n"
                "• `sl dungeon` - Start your first dungeon challenge\n"
                "• `sl shop` - Visit the Hunter's shop for upgrades\n"
                "• `sl daily` - Claim your daily rewards\n"
                "• `sl tutorial` - Simple set of tutorials & rewards to understand the bot"
            ),
            inline=False
        )

        embed.set_image(url="https://cdn-www.konbini.com/files/2023/01/Une-Solo-Leveling.jpg?width=3840&quality=75&format=webp")
        embed.set_footer(text="May your legend echo through the annals of Hunter history!")
        if ctx.author.avatar and hasattr(ctx.author.avatar, 'url'):
            embed.set_thumbnail(url=ctx.author.avatar.url)

        await ctx.send(embed=embed)

    async def show_guide(self, ctx, intro_embed, message):
        lang = await _get_lang(ctx.author.id)

        current_index = 0
        current_set = "default"
        image_links = self.default_images

        # Localized strings
        title_welcome = _tr(lang, "Welcome to the World of Hunters!", "مرحبًا بك في عالم الصيادين!", "Bienvenue dans le monde des chasseurs !")
        label_skip_intro = _tr(lang, "Skip Intro", "تخطي المقدمة", "Passer l’intro")
        label_go_back = _tr(lang, "Go Back", "عودة", "Retour")
        placeholder_select = _tr(lang, "Choose an option", "اختر خيارًا", "Choisissez une option")
        cat_spawn = _tr(lang, "Spawn-Team-Upgrade", "الاستدعاء-الفريق-الترقية", "Invocation-Équipe-Amélioration")
        cat_shop = _tr(lang, "Shop-Inventory", "المتجر-الجرد", "Boutique-Inventaire")
        cat_dungeon = _tr(lang, "Dungeons-Missions", "الأبراج-المهام", "Donjons-Missions")
        cat_trading = _tr(lang, "Trading", "التبادل", "Échanges")
        cat_daily = _tr(lang, "Dailies-Quests", "اليومي-المهام", "Quotidiens-Quêtes")
        cat_fight = _tr(lang, "Fight-Challenges", "القتال-التحديات", "Combats-Défis")
        label_register = _tr(lang, "Register", "تسجيل", "S’inscrire")

        async def update_message():
            new_embed = discord.Embed(
                title=title_welcome,
                color=discord.Color.blue()
            )
            new_embed.set_image(url=image_links[current_index])
            
            view = discord.ui.View(timeout=None)
            
            if current_set == "default":
                if current_index == len(image_links) - 1:
                    view.add_item(select_menu)
                    view.add_item(previous_button)
                    view.add_item(next_button)
                    view.add_item(register_button)
                else:
                    view.add_item(previous_button)
                    view.add_item(next_button)
                    view.add_item(skip_intro_button)
            else:
                view.add_item(previous_button)
                view.add_item(next_button)
                view.add_item(go_back_button)
            
            await message.edit(embed=new_embed, view=view)
        async def scroll_callback(interaction, direction):
            nonlocal current_index
            if direction == "previous":
                current_index = (current_index - 1) % len(image_links)
            else:
                current_index = (current_index + 1) % len(image_links)
            await update_message()
            await interaction.response.defer()

        async def previous_callback(interaction):
            await scroll_callback(interaction, "previous")

        async def next_callback(interaction):
            await scroll_callback(interaction, "next")

        async def skip_intro_callback(interaction):
            nonlocal current_index
            current_index = len(image_links) - 1
            await update_message()
            await interaction.response.defer()

        async def go_back_callback(interaction):
            nonlocal current_set, image_links, current_index
            current_set = "default"
            image_links = self.default_images
            current_index = len(image_links) - 1
            await update_message()
            await interaction.response.defer()

        async def select_callback(interaction):
            nonlocal current_set, image_links, current_index
            selected_option = interaction.data['values'][0]
            current_set = selected_option
            image_links = self.image_sets[selected_option]
            current_index = 0
            await update_message()
            await interaction.response.defer()

        async def register_callback(interaction):
            await interaction.response.defer()
            await self.complete_registration(ctx, str(ctx.author.id))

        previous_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="◀️")
        next_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="▶️")
        skip_intro_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label=label_skip_intro)
        go_back_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label=label_go_back)

        previous_button.callback = previous_callback
        next_button.callback = next_callback
        skip_intro_button.callback = skip_intro_callback
        go_back_button.callback = go_back_callback

        select_menu = discord.ui.Select(
            placeholder=placeholder_select,
            options=[
                discord.SelectOption(label=cat_spawn, value="spawn_team_upgrade"),
                discord.SelectOption(label=cat_shop, value="shop_inventory"),
                discord.SelectOption(label=cat_dungeon, value="dungeons_missions"),
                discord.SelectOption(label=cat_trading, value="trading"),
                discord.SelectOption(label=cat_daily, value="dailies_quests"),
                discord.SelectOption(label=cat_fight, value="fight_challenges")
            ]
        )
        select_menu.callback = select_callback

        register_button = discord.ui.Button(style=discord.ButtonStyle.success, label=label_register)
        register_button.callback = register_callback

        await update_message()

    @commands.command(name='profile',aliases=["prof", "pf"])
    async def profile(self, ctx):
        lang = await _get_lang(ctx.author.id)
        try:
            # 1. Find user in database by Discord user ID
            user_query = """
            SELECT user_id,prenium, entry_date, won_currency,gamble_currency,
                   e_dungeon_points, c_dungeon_points, s_dungeon_points,a_dungeon_points,b_dungeon_points,d_dungeon_points,
                   win_streak, alltimewins, mission_streak, alltime_squad, squad_streak,votecount,votestreak
            FROM user 
            WHERE user_authorid = %s
            """
            user_results = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_results:
                await ctx.send(_tr(lang, "❌ User not found in the database. Please register first.", "❌ لم يتم العثور على المستخدم في قاعدة البيانات. يرجى التسجيل أولاً.", "❌ Utilisateur introuvable dans la base de données. Veuillez vous enregistrer d'abord."))
                return

            user_data = user_results[0]
            user_id = user_data['user_id']

            # 2. Get inventory ID
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(_tr(lang, "❌ No inventory found for this user.", "❌ لم يتم العثور على مخزون لهذا المستخدم.", "❌ Aucun inventaire trouvé pour cet utilisateur."))
                return

            inventory_id = inventory_results[0]['inventory_id']

            # 3. Card Collection
            card_collection_query = """
            SELECT 
                COUNT(*) as total_cards,
                SUM(CASE WHEN c.type_card = 'Fighter' THEN 1 ELSE 0 END) as offence,
                SUM(CASE WHEN c.type_card = 'Support' THEN 1 ELSE 0 END) as support,
                SUM(CASE WHEN c.type_card = 'Booster' THEN 1 ELSE 0 END) as booster
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            WHERE uc.user_id = %s
            """
            card_results = await execute_query_with_retry(card_collection_query, (user_id,))
            card_results = card_results[0]

            # 4. Resources
            frag_query = """
            SELECT SUM(quantiter) as total_fragments 
            FROM inventory_frags 
            WHERE inventory_id = %s
            """
            frag_results = await execute_query_with_retry(frag_query, (inventory_id,))
            frag_results = frag_results[0]

            equip_query = """
            SELECT SUM(quantiter) as total_equipment 
            FROM inventory_weapon 
            WHERE inventory_id = %s
            """
            equip_results = await execute_query_with_retry(equip_query, (inventory_id,))
            equip_results = equip_results[0]

            product_query = """
            SELECT 
                SUM(CASE WHEN product_id IN (9, 10, 11, 12) THEN quantiter ELSE 0 END) as boosters,
                SUM(CASE WHEN product_id IN (2, 3, 14, 15, 16) THEN quantiter ELSE 0 END) as resets,
                SUM(CASE WHEN product_id = 17 THEN quantiter ELSE 0 END) as enhancers
                FROM inventory_product
            WHERE inventory_id = %s
            """
            product_results = await execute_query_with_retry(product_query, (inventory_id,))
            product_results = product_results[0]

            # Create embed
            embed = discord.Embed(
                title=_tr(lang, f"Hunter Profile: {ctx.author.name}", f"ملف الصياد: {ctx.author.name}", f"Profil du Chasseur : {ctx.author.name}"),
                color=discord.Color.gold(),
                description=_tr(lang, "Your journey through the hunter world continues!", "رحلتك في عالم الصيادين مستمرة!", "Votre aventure dans le monde des chasseurs continue !")
            )
            
            if ctx.author.avatar and hasattr(ctx.author.avatar, 'url'):
                embed.set_thumbnail(url=ctx.author.avatar.url)

            # Premium tier mapping (keep English tier names or translate?)
            premium = user_data['prenium']
            if premium == 0:
                tier = _tr(lang, "Hunter", "صياد", "Chasseur")
            elif premium == 1:
                tier = _tr(lang, "National Hunter", "صياد وطني", "Chasseur National")
            elif premium == 2:
                tier = _tr(lang, "Ruler", "حاكم", "Dirigeant")
            else:
                tier = _tr(lang, "Monarch", "ملك", "Monarque")

            # Account Overview
            embed.add_field(
                name=_tr(lang, "📋 Account Overview", "📋 نظرة عامة على الحساب", "📋 Aperçu du compte"), 
                value=_tr(
                    lang,
                    f"**<:blue_dot:1311418123404181585> Started**: `{user_data['entry_date'].strftime('%Y-%m-%d')}`\n"
                    f"**<:blue_dot:1311418123404181585> Premium**: `{tier}`",
                    f"**<:blue_dot:1311418123404181585> تاريخ البدء**: `{user_data['entry_date'].strftime('%Y-%m-%d')}`\n"
                    f"**<:blue_dot:1311418123404181585> البريميوم**: `{tier}`",
                    f"**<:blue_dot:1311418123404181585> Début** : `{user_data['entry_date'].strftime('%Y-%m-%d')}`\n"
                    f"**<:blue_dot:1311418123404181585> Abonnement** : `{tier}`"
                ), 
                inline=False
            )
            
            # Card Collection
            card_collection_text = _tr(
                lang,
                f"<:blue_dot:1311418123404181585> **Total Cards**: `{card_results['total_cards']}`\n"
                f"<:blue_dot:1311418123404181585> **Offence**: `{card_results['offence']}`\n"
                f"<:blue_dot:1311418123404181585> **Support**: `{card_results['support']}`\n"
                f"<:blue_dot:1311418123404181585> **Booster**: `{card_results['booster']}`",
                f"<:blue_dot:1311418123404181585> **إجمالي البطاقات**: `{card_results['total_cards']}`\n"
                f"<:blue_dot:1311418123404181585> **هجوم**: `{card_results['offence']}`\n"
                f"<:blue_dot:1311418123404181585> **دعم**: `{card_results['support']}`\n"
                f"<:blue_dot:1311418123404181585> **معزز**: `{card_results['booster']}`",
                f"<:blue_dot:1311418123404181585> **Cartes totales** : `{card_results['total_cards']}`\n"
                f"<:blue_dot:1311418123404181585> **Offense** : `{card_results['offence']}`\n"
                f"<:blue_dot:1311418123404181585> **Soutien** : `{card_results['support']}`\n"
                f"<:blue_dot:1311418123404181585> **Booster** : `{card_results['booster']}`"
            )
            embed.add_field(
                name=_tr(lang, "<:cards:1311415093514342441> Card Collection", "<:cards:1311415093514342441> مجموعة البطاقات", "<:cards:1311415093514342441> Collection de Cartes"), 
                value=card_collection_text, 
                inline=False
            )
            
            # Resources
            resources_text = _tr(
                lang,
                f"<:blue_dot:1311418123404181585> **Won Currency**: `{user_data['won_currency']} Won`\n"
                f"<:blue_dot:1311418123404181585> **GWon Currency**: `{user_data['gamble_currency']} Won`\n"
                f"<:blue_dot:1311418123404181585> **Essence Stones**: `{frag_results['total_fragments'] or 0}`\n"
                f"<:blue_dot:1311418123404181585> **Equipments**: `{equip_results['total_equipment'] or 0}`\n"
                f"<:blue_dot:1311418123404181585> **Boosters**: `{product_results['boosters']}`\n"
                f"<:blue_dot:1311418123404181585> **Resets**: `{product_results['resets']}`\n"
                f"<:blue_dot:1311418123404181585> **Enhancers**: `{product_results['enhancers']}`",
                f"<:blue_dot:1311418123404181585> **عملة الوون**: `{user_data['won_currency']} Won`\n"
                f"<:blue_dot:1311418123404181585> **عملة الجي-وون**: `{user_data['gamble_currency']} Won`\n"
                f"<:blue_dot:1311418123404181585> **حجارة الجوهر**: `{frag_results['total_fragments'] or 0}`\n"
                f"<:blue_dot:1311418123404181585> **معدات**: `{equip_results['total_equipment'] or 0}`\n"
                f"<:blue_dot:1311418123404181585> **معززات**: `{product_results['boosters']}`\n"
                f"<:blue_dot:1311418123404181585> **إعادات**: `{product_results['resets']}`\n"
                f"<:blue_dot:1311418123404181585> **محسنات**: `{product_results['enhancers']}`",
                f"<:blue_dot:1311418123404181585> **Devise Won** : `{user_data['won_currency']} Won`\n"
                f"<:blue_dot:1311418123404181585> **Devise GWon** : `{user_data['gamble_currency']} Won`\n"
                f"<:blue_dot:1311418123404181585> **Pierres d’Essence** : `{frag_results['total_fragments'] or 0}`\n"
                f"<:blue_dot:1311418123404181585> **Équipements** : `{equip_results['total_equipment'] or 0}`\n"
                f"<:blue_dot:1311418123404181585> **Boosters** : `{product_results['boosters']}`\n"
                f"<:blue_dot:1311418123404181585> **Réinitialisations** : `{product_results['resets']}`\n"
                f"<:blue_dot:1311418123404181585> **Améliorants** : `{product_results['enhancers']}`"
            )
            embed.add_field(
                name=_tr(lang, "<:treasurechest:1312772522432925746> Resources", "<:treasurechest:1312772522432925746> الموارد", "<:treasurechest:1312772522432925746> Ressources"), 
                value=resources_text, 
                inline=False
            )
            
            # Dungeon Progress
            dungeon_text = _tr(
                lang,
                f"<:blue_dot:1311418123404181585> **S-Rank Dungeons**: `{user_data['s_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **A-Rank Dungeons**: `{user_data['a_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **B-Rank Dungeons**: `{user_data['b_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **C-Rank Dungeons**: `{user_data['c_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **D-Rank Dungeons**: `{user_data['d_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **E-Rank Dungeons**: `{user_data['e_dungeon_points']}`",
                f"<:blue_dot:1311418123404181585> **أبراج رتبة S**: `{user_data['s_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **أبراج رتبة A**: `{user_data['a_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **أبراج رتبة B**: `{user_data['b_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **أبراج رتبة C**: `{user_data['c_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **أبراج رتبة D**: `{user_data['d_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **أبراج رتبة E**: `{user_data['e_dungeon_points']}`",
                f"<:blue_dot:1311418123404181585> **Donjons Rang S** : `{user_data['s_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **Donjons Rang A** : `{user_data['a_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **Donjons Rang B** : `{user_data['b_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **Donjons Rang C** : `{user_data['c_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **Donjons Rang D** : `{user_data['d_dungeon_points']}`\n"
                f"<:blue_dot:1311418123404181585> **Donjons Rang E** : `{user_data['e_dungeon_points']}`"
            )
            embed.add_field(
                name=_tr(lang, "<:dungeonchaos:1311414124990824570> Dungeon Progress", "<:dungeonchaos:1311414124990824570> تقدم الأبراج", "<:dungeonchaos:1311414124990824570> Progression Donjons"), 
                value=dungeon_text, 
                inline=False
            )
            
            # PvP Statistics
            pvp_text = _tr(
                lang,
                f"<:blue_dot:1311418123404181585> **Win-Streak**: `{user_data['win_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **All-Time Wins**: `{user_data['alltimewins']}`\n"
                f"<:blue_dot:1311418123404181585> **Missions Completed**: `{user_data['mission_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **Squad-Streak**: `{user_data['squad_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **All-Time Squad Wins**: `{user_data['alltime_squad']}`\n"
                f"<:blue_dot:1311418123404181585> **Vote Streak**: `{user_data['votecount']}`\n"
                f"<:blue_dot:1311418123404181585> **Vote Count**: `{user_data['votestreak']}`",
                f"<:blue_dot:1311418123404181585> **سلسلة الانتصارات**: `{user_data['win_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **إجمالي الانتصارات**: `{user_data['alltimewins']}`\n"
                f"<:blue_dot:1311418123404181585> **المهام المنجزة**: `{user_data['mission_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **سلسلة الفرقة**: `{user_data['squad_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **انتصارات الفرقة الإجمالية**: `{user_data['alltime_squad']}`\n"
                f"<:blue_dot:1311418123404181585> **سلسلة التصويت**: `{user_data['votecount']}`\n"
                f"<:blue_dot:1311418123404181585> **عدد التصويتات**: `{user_data['votestreak']}`",
                f"<:blue_dot:1311418123404181585> **Série de victoires** : `{user_data['win_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **Victoires totales** : `{user_data['alltimewins']}`\n"
                f"<:blue_dot:1311418123404181585> **Missions terminées** : `{user_data['mission_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **Série d’escouade** : `{user_data['squad_streak']}`\n"
                f"<:blue_dot:1311418123404181585> **Victoires d’escouade totales** : `{user_data['alltime_squad']}`\n"
                f"<:blue_dot:1311418123404181585> **Série de votes** : `{user_data['votecount']}`\n"
                f"<:blue_dot:1311418123404181585> **Nombre de votes** : `{user_data['votestreak']}`"
            )
            embed.add_field(
                name=_tr(lang, "<:Skill_Stats_icon56:1311414108259881052> Records Statistics", "<:Skill_Stats_icon56:1311414108259881052> إحصائيات السجلات", "<:Skill_Stats_icon56:1311414108259881052> Statistiques des Records"), 
                value=pvp_text, 
                inline=False
            )
            
            # Add last updated timestamp in footer
            last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            embed.set_footer(text=_tr(lang, f"🕒 Last Updated: {last_updated}", f"🕒 آخر تحديث: {last_updated}", f"🕒 Dernière mise à jour : {last_updated}"))
            
            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error in profile command: {e}")
            await ctx.send(_tr(lang, "❌ An error occurred while retrieving your profile.", "❌ حدث خطأ أثناء جلب ملفك الشخصي.", "❌ Une erreur s’est produite lors de la récupération de votre profil."))


    def calculate_remaining_time(self, last_timestamp, duration):
        """ Calculate remaining time for a specific command. """
        if last_timestamp is None:
            return None
            
        if isinstance(last_timestamp, str):
            try:
                last_timestamp = datetime.strptime(last_timestamp, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return None
                
        current_time = datetime.now()
        time_since_last_use = current_time - last_timestamp
        remaining_time = duration - time_since_last_use
        return remaining_time if remaining_time.total_seconds() > 0 else None

    def format_time_remaining(self, remaining_time):
        """ Format remaining time in human-readable format. """
        total_seconds = int(remaining_time.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    @commands.command(name='cooldown', aliases=['cd','cdt'])
    async def cooldown(self, ctx):
        """ Check cooldowns for various commands in Solo Leveling bot. """
        lang = await _get_lang(ctx.author.id)
        try:
            query = """ 
            SELECT user_id, roll_timer,date_daily, fight_timer, mission_timer, dungeon_timer, mine_timer, squad_timer
            FROM `user` 
            WHERE user_authorid = %s 
            """
            user_data = await execute_query_with_retry(query, (str(ctx.author.id),))
            if not user_data:
                await ctx.send(_tr(lang, "User not found in the database.", "المستخدم غير موجود في قاعدة البيانات.", "Utilisateur introuvable dans la base de données."))
                return

            user_data = user_data[0]

            # Define cooldowns with their respective icons
            cooldowns_data = {
                'Roll': {'timestamp': user_data['roll_timer'], 'duration': timedelta(hours=5), 'icon': '<:reroll:1302672409278545940>'},
                'Fight': {'timestamp': user_data['fight_timer'], 'duration': timedelta(minutes=5), 'icon': '<:fightreset:1302674341418242210>'},
                'Mission': {'timestamp': user_data['mission_timer'], 'duration': timedelta(minutes=3), 'icon': '<:missionreset:1302670646010052628>'},
                'Dungeon': {'timestamp': user_data['dungeon_timer'], 'duration': timedelta(minutes=10), 'icon': '<:dungeonreset:1302673213859299421>'},
                'Mining': {'timestamp': user_data['mine_timer'], 'duration': timedelta(hours=4), 'icon': '⛏️'},
                'Daily': {'timestamp': user_data['date_daily'], 'duration': timedelta(hours=24), 'icon': '☀️'}
            }

            embed = discord.Embed(
                title=_tr(lang, f"⚔️ {ctx.author.name}'s Cooldowns ⚔️", f"⚔️ تبريدات {ctx.author.name} ⚔️", f"⚔️ Temps de recharge de {ctx.author.name} ⚔️"),
                color=discord.Color(0x8B0000)
            )

            for command_name, data in cooldowns_data.items():
                timestamp = data['timestamp']
                duration = data['duration']
                icon = data['icon']

                remaining = self.calculate_remaining_time(timestamp, duration)
                
                # Localized field names
                field_name = _tr(
                    lang,
                    f"{icon} {command_name} Cooldown",
                    f"{icon} تبريد {command_name}",
                    f"{icon} Recharge {command_name}"
                )
                
                if remaining is None:
                    field_value = _tr(lang, "✅ Available Now", "✅ متاح الآن", "✅ Disponible maintenant")
                else:
                    human = self.format_time_remaining(remaining)
                    field_value = _tr(
                        lang,
                        f"⏳ Cooldown: {human} remaining",
                        f"⏳ التهدئة: متبقّي {human}",
                        f"⏳ Recharge : {human} restante"
                    )
                
                embed.add_field(
                    name=field_name, 
                    value=field_value, 
                    inline=False
                )

            await ctx.send(embed=embed)

        except Exception as e:
            print(f"An error occurred: {str(e)}")
            await ctx.send(_tr(lang, "❌ An error occurred.", "❌ حدث خطأ.", "❌ Une erreur s’est produite."))

    @commands.command(name="giveboost")
    async def give_itemboost(self, ctx, user: discord.Member = None, amount: int = 1):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl give @user [amount]`")
            return
            
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User {user.mention} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if product_id 2 exists for this inventory
            check_product_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = 1"
            product_results = await execute_query_with_retry(check_product_query, (inventory_id,))
            
            if product_results:
                # Update existing product quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = 1"
                await execute_query_with_retry(update_query, (amount, inventory_id))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory.")
            else:
                # Insert new product entry
                insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, 1, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, amount))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory (new item).")
                
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="ggive")
    async def give_gwoncurrency(self, ctx, user: discord.Member = None, amount: int = None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127","1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl wgive @user amount`")
            return
            
        if amount is None:
            await ctx.send("Please specify an amount: `sl wgive @user amount`")
            return
        
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Update user's won_currency
            update_query = "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (amount, user_id))
            
            await ctx.send(f"Added {amount} currency to {user.mention}'s account.")
            
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")
    @commands.command(name="cgive")
    async def give_gwoncurrency(self, ctx, user: discord.Member = None, amount: int = None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127","1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl wgive @user amount`")
            return
            
        if amount is None:
            await ctx.send("Please specify an amount: `sl wgive @user amount`")
            return
        
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Update user's won_currency
            update_query = "UPDATE user SET candy_currency = candy_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (amount, user_id))
            
            await ctx.send(f"Added {amount} currency to {user.mention}'s account.")
            
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")
    @commands.command(name="give")
    async def give_item(self, ctx, user: discord.Member = None, amount: int = 1):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127","1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl give @user [amount]`")
            return
            
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User {user.mention} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if product_id 2 exists for this inventory
            check_product_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = 2"
            product_results = await execute_query_with_retry(check_product_query, (inventory_id,))
            
            if product_results:
                # Update existing product quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = 2"
                await execute_query_with_retry(update_query, (amount, inventory_id))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory.")
            else:
                # Insert new product entry
                insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, 2, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, amount))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory (new item).")
                
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="wgive")
    async def give_currency(self, ctx, user: discord.Member = None, amount: int = None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl wgive @user amount`")
            return
            
        if amount is None:
            await ctx.send("Please specify an amount: `sl wgive @user amount`")
            return
        
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Update user's won_currency
            update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (amount, user_id))
            
            await ctx.send(f"Added {amount} currency to {user.mention}'s account.")
            
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    #@commands.command(name="registerdbstats")
    async def db_stats(self, ctx):
        """Display database connection statistics"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send("You don't have permission to use this command.")
            return
        
        try:
            metrics = get_metrics()
            
            embed = discord.Embed(
                title="📊 Database Connection Statistics",
                color=discord.Color.blue()
            )
            
            embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
            embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
            
            embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
            embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
            embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
            
            if metrics["last_error"]:
                error_time = metrics["last_error_time"].strftime("%Y-%m-%d %H:%M:%S") if metrics["last_error_time"] else "Unknown"
                embed.add_field(
                    name="Last Error",
                    value=f"Time: {error_time}\nError: {metrics['last_error'][:100]}...",
                    inline=False
                )
            
            embed.set_footer(text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="givechest")
    async def give_chest(self, ctx, *, args=None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if args is None:
            await ctx.send("Please use format: `sl givechest rarity,discord_id,quantity`")
            return
            
        try:
            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            if len(parts) < 2:
                await ctx.send("Please use format: `sl givechest rarity,discord_id,quantity`")
                return
                
            rarity = parts[0]
            discord_id = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(f"User with Discord ID {discord_id} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User with Discord ID {discord_id} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if chest rarity exists using LIKE
            chest_query = "SELECT chest_id FROM chest WHERE chest_name LIKE %s"
            chest_results = await execute_query_with_retry(chest_query, (f"%{rarity}%",))
            
            if not chest_results:
                await ctx.send(f"Chest rarity '{rarity}' doesn't exist.")
                return
                
            chest_id = chest_results[0]['chest_id']
            
            # Check if chest exists in inventory
            check_chest_query = "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            existing_chest = await execute_query_with_retry(check_chest_query, (inventory_id, chest_id))
            
            if existing_chest:
                # Update existing chest quantity
                update_query = "UPDATE inventory_chest SET quantiter = quantiter + %s WHERE inventory_id = %s AND chest_id = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_id, chest_id))
                await ctx.send(f"Added {quantity} {rarity} chest(s) to user {discord_id}'s inventory.")
            else:
                # Insert new chest entry
                insert_query = "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, chest_id, quantity))
                await ctx.send(f"Added {quantity} {rarity} chest(s) to user {discord_id}'s inventory (new item).")
                
        except ValueError:
            await ctx.send("Invalid format. Please use: `sl givechest rarity,discord_id,quantity`")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="giveproduct")
    async def give_product(self, ctx, *, args=None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if args is None:
            await ctx.send("Please use format: `sl giveproduct product_name,discord_id,quantity`")
            return
            
        try:
            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            if len(parts) < 2:
                await ctx.send("Please use format: `sl giveproduct product_name,discord_id,quantity`")
                return
                
            product_name = parts[0]
            discord_id = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(f"User with Discord ID {discord_id} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User with Discord ID {discord_id} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if product exists using LIKE
            product_query = "SELECT product_id FROM product WHERE product_name LIKE %s"
            product_results = await execute_query_with_retry(product_query, (f"%{product_name}%",))
            
            if not product_results:
                await ctx.send(f"Product '{product_name}' doesn't exist.")
                return
                
            product_id = product_results[0]['product_id']
            
            # Check if product exists in inventory
            check_product_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            existing_product = await execute_query_with_retry(check_product_query, (inventory_id, product_id))
            
            if existing_product:
                # Update existing product quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_id, product_id))
                await ctx.send(f"Added {quantity} {product_name}(s) to user {discord_id}'s inventory.")
            else:
                # Insert new product entry
                insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, product_id, quantity))
                await ctx.send(f"Added {quantity} {product_name}(s) to user {discord_id}'s inventory (new item).")
                
        except ValueError:
            await ctx.send("Invalid format. Please use: `sl giveproduct product_name,discord_id,quantity`")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="giveweapon")
    async def give_weapon(self, ctx, *, args=None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if args is None:
            await ctx.send("Please use format: `sl giveweapon weapon_name,discord_id,quantity`")
            return
            
        try:
            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            if len(parts) < 2:
                await ctx.send("Please use format: `sl giveweapon weapon_name,discord_id,quantity`")
                return
                
            weapon_name = parts[0]
            discord_id = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(f"User with Discord ID {discord_id} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User with Discord ID {discord_id} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if weapon exists using LIKE
            weapon_query = "SELECT weapon_id FROM weapon WHERE weapon_name LIKE %s"
            weapon_results = await execute_query_with_retry(weapon_query, (f"%{weapon_name}%",))
            
            if not weapon_results:
                await ctx.send(f"Weapon '{weapon_name}' doesn't exist.")
                return
                
            weapon_id = weapon_results[0]['weapon_id']
            
            # Check if weapon exists in inventory
            check_weapon_query = "SELECT quantiter FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            existing_weapon = await execute_query_with_retry(check_weapon_query, (inventory_id, weapon_id))
            
            if existing_weapon:
                # Update existing weapon quantity
                update_query = "UPDATE inventory_weapon SET quantiter = quantiter + %s WHERE inventory_id = %s AND weapon_id = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_id, weapon_id))
                await ctx.send(f"Added {quantity} {weapon_name}(s) to user {discord_id}'s inventory.")
            else:
                # Insert new weapon entry
                insert_query = "INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, weapon_id, quantity))
                await ctx.send(f"Added {quantity} {weapon_name}(s) to user {discord_id}'s inventory (new item).")
                
        except ValueError:
            await ctx.send("Invalid format. Please use: `sl giveweapon weapon_name,discord_id,quantity`")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")
async def setup(bot):
    await bot.add_cog(UserCommands(bot))
    @commands.command(name="giveboost")
    async def give_itemboost(self, ctx, user: discord.Member = None, amount: int = 1):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl give @user [amount]`")
            return
            
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User {user.mention} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if product_id 2 exists for this inventory
            check_product_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = 1"
            product_results = await execute_query_with_retry(check_product_query, (inventory_id,))
            
            if product_results:
                # Update existing product quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = 1"
                await execute_query_with_retry(update_query, (amount, inventory_id))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory.")
            else:
                # Insert new product entry
                insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, 1, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, amount))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory (new item).")
                
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="ggive")
    async def give_gwoncurrency(self, ctx, user: discord.Member = None, amount: int = None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127","1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl wgive @user amount`")
            return
            
        if amount is None:
            await ctx.send("Please specify an amount: `sl wgive @user amount`")
            return
        
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Update user's won_currency
            update_query = "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (amount, user_id))
            
            await ctx.send(f"Added {amount} currency to {user.mention}'s account.")
            
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")
    @commands.command(name="give")
    async def give_item(self, ctx, user: discord.Member = None, amount: int = 1):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559", "1384189515199021127","1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl give @user [amount]`")
            return
            
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User {user.mention} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if product_id 2 exists for this inventory
            check_product_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = 2"
            product_results = await execute_query_with_retry(check_product_query, (inventory_id,))
            
            if product_results:
                # Update existing product quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = 2"
                await execute_query_with_retry(update_query, (amount, inventory_id))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory.")
            else:
                # Insert new product entry
                insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, 2, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, amount))
                await ctx.send(f"Added {amount} item(s) to {user.mention}'s inventory (new item).")
                
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="wgive")
    async def give_currency(self, ctx, user: discord.Member = None, amount: int = None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if user is None:
            await ctx.send("Please specify a user: `sl wgive @user amount`")
            return
            
        if amount is None:
            await ctx.send("Please specify an amount: `sl wgive @user amount`")
            return
        
        try:
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (str(user.id),))
            
            if not user_results:
                await ctx.send(f"User {user.mention} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Update user's won_currency
            update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            await execute_query_with_retry(update_query, (amount, user_id))
            
            await ctx.send(f"Added {amount} currency to {user.mention}'s account.")
            
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    #@commands.command(name="registerdbstats")
    async def db_stats(self, ctx):
        """Display database connection statistics"""
        # Check if the command invoker is authorized
        if str(ctx.author.id) != "818132161534230559" and not ctx.author.guild_permissions.administrator:
            await ctx.send("You don't have permission to use this command.")
            return
        
        try:
            metrics = get_metrics()
            
            embed = discord.Embed(
                title="📊 Database Connection Statistics",
                color=discord.Color.blue()
            )
            
            embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
            embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
            embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
            
            embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
            embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
            embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
            
            if metrics["last_error"]:
                error_time = metrics["last_error_time"].strftime("%Y-%m-%d %H:%M:%S") if metrics["last_error_time"] else "Unknown"
                embed.add_field(
                    name="Last Error",
                    value=f"Time: {error_time}\nError: {metrics['last_error'][:100]}...",
                    inline=False
                )
            
            embed.set_footer(text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
    @commands.command(name="givechest")
    async def give_chest(self, ctx, *, args=None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if args is None:
            await ctx.send("Please use format: `sl givechest rarity,discord_id,quantity`")
            return
            
        try:
            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            if len(parts) < 2:
                await ctx.send("Please use format: `sl givechest rarity,discord_id,quantity`")
                return
                
            rarity = parts[0]
            discord_id = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(f"User with Discord ID {discord_id} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User with Discord ID {discord_id} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if chest rarity exists using LIKE
            chest_query = "SELECT chest_id FROM chest WHERE chest_name LIKE %s"
            chest_results = await execute_query_with_retry(chest_query, (f"%{rarity}%",))
            
            if not chest_results:
                await ctx.send(f"Chest rarity '{rarity}' doesn't exist.")
                return
                
            chest_id = chest_results[0]['chest_id']
            
            # Check if chest exists in inventory
            check_chest_query = "SELECT quantiter FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            existing_chest = await execute_query_with_retry(check_chest_query, (inventory_id, chest_id))
            
            if existing_chest:
                # Update existing chest quantity
                update_query = "UPDATE inventory_chest SET quantiter = quantiter + %s WHERE inventory_id = %s AND chest_id = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_id, chest_id))
                await ctx.send(f"Added {quantity} {rarity} chest(s) to user {discord_id}'s inventory.")
            else:
                # Insert new chest entry
                insert_query = "INSERT INTO inventory_chest (inventory_id, chest_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, chest_id, quantity))
                await ctx.send(f"Added {quantity} {rarity} chest(s) to user {discord_id}'s inventory (new item).")
                
        except ValueError:
            await ctx.send("Invalid format. Please use: `sl givechest rarity,discord_id,quantity`")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="giveproduct")
    async def give_product(self, ctx, *, args=None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if args is None:
            await ctx.send("Please use format: `sl giveproduct product_name,discord_id,quantity`")
            return
            
        try:
            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            if len(parts) < 2:
                await ctx.send("Please use format: `sl giveproduct product_name,discord_id,quantity`")
                return
                
            product_name = parts[0]
            discord_id = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(f"User with Discord ID {discord_id} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User with Discord ID {discord_id} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if product exists using LIKE
            product_query = "SELECT product_id FROM product WHERE product_name LIKE %s"
            product_results = await execute_query_with_retry(product_query, (f"%{product_name}%",))
            
            if not product_results:
                await ctx.send(f"Product '{product_name}' doesn't exist.")
                return
                
            product_id = product_results[0]['product_id']
            
            # Check if product exists in inventory
            check_product_query = "SELECT quantiter FROM inventory_product WHERE inventory_id = %s AND product_id = %s"
            existing_product = await execute_query_with_retry(check_product_query, (inventory_id, product_id))
            
            if existing_product:
                # Update existing product quantity
                update_query = "UPDATE inventory_product SET quantiter = quantiter + %s WHERE inventory_id = %s AND product_id = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_id, product_id))
                await ctx.send(f"Added {quantity} {product_name}(s) to user {discord_id}'s inventory.")
            else:
                # Insert new product entry
                insert_query = "INSERT INTO inventory_product (inventory_id, product_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, product_id, quantity))
                await ctx.send(f"Added {quantity} {product_name}(s) to user {discord_id}'s inventory (new item).")
                
        except ValueError:
            await ctx.send("Invalid format. Please use: `sl giveproduct product_name,discord_id,quantity`")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name="giveweapon")
    async def give_weapon(self, ctx, *, args=None):
        # Check if command user is authorized
        if str(ctx.author.id) not in ("818132161534230559","1384189515199021127", "1076303866854899762", "1204879494901407805"):
            return  # Silently ignore if not authorized
            
        if args is None:
            await ctx.send("Please use format: `sl giveweapon weapon_name,discord_id,quantity`")
            return
            
        try:
            # Split arguments by comma
            parts = [part.strip() for part in args.split(',')]
            
            if len(parts) < 2:
                await ctx.send("Please use format: `sl giveweapon weapon_name,discord_id,quantity`")
                return
                
            weapon_name = parts[0]
            discord_id = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            
            # Check if user exists in database by Discord ID
            check_user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_results = await execute_query_with_retry(check_user_query, (discord_id,))
            
            if not user_results:
                await ctx.send(f"User with Discord ID {discord_id} is not registered in the database.")
                return
                
            user_id = user_results[0]['user_id']
            
            # Get the inventory_id for the user
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_results = await execute_query_with_retry(inventory_query, (user_id,))
            
            if not inventory_results:
                await ctx.send(f"User with Discord ID {discord_id} doesn't have an inventory.")
                return
                
            inventory_id = inventory_results[0]['inventory_id']
            
            # Check if weapon exists using LIKE
            weapon_query = "SELECT weapon_id FROM weapon WHERE weapon_name LIKE %s"
            weapon_results = await execute_query_with_retry(weapon_query, (f"%{weapon_name}%",))
            
            if not weapon_results:
                await ctx.send(f"Weapon '{weapon_name}' doesn't exist.")
                return
                
            weapon_id = weapon_results[0]['weapon_id']
            
            # Check if weapon exists in inventory
            check_weapon_query = "SELECT quantiter FROM inventory_weapon WHERE inventory_id = %s AND weapon_id = %s"
            existing_weapon = await execute_query_with_retry(check_weapon_query, (inventory_id, weapon_id))
            
            if existing_weapon:
                # Update existing weapon quantity
                update_query = "UPDATE inventory_weapon SET quantiter = quantiter + %s WHERE inventory_id = %s AND weapon_id = %s"
                await execute_query_with_retry(update_query, (quantity, inventory_id, weapon_id))
                await ctx.send(f"Added {quantity} {weapon_name}(s) to user {discord_id}'s inventory.")
            else:
                # Insert new weapon entry
                insert_query = "INSERT INTO inventory_weapon (inventory_id, weapon_id, quantiter) VALUES (%s, %s, %s)"
                await execute_query_with_retry(insert_query, (inventory_id, weapon_id, quantity))
                await ctx.send(f"Added {quantity} {weapon_name}(s) to user {discord_id}'s inventory (new item).")
                
        except ValueError:
            await ctx.send("Invalid format. Please use: `sl giveweapon weapon_name,discord_id,quantity`")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")
async def setup(bot):
    await bot.add_cog(UserCommands(bot))