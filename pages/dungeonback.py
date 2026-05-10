import discord
from discord.ext import commands
import asyncio

class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.categories = {
            "User Commands": {
                "description": "Manage your hunter profile and track progress.",
                "emoji": "👤",
                "commands": [
                    {"name": "daily ", "shortcuts": ["`dl`", "`dy`"], "description": "Check and claim daily rewards", "example": "sl daily"},
                    {"name": "bal ", "shortcuts": ["`bal`", "`b`"], "description": "Check your hunter's balance", "example": "sl bal"},
                    {"name": "guide ", "shortcuts": ["`gd`", "`help`"], "description": "View the hunter's guide", "example": "sl guide"},
                    {"name": "profile ", "shortcuts": ["`prof`", "`pf`"], "description": "View your hunter profile", "example": "sl profile"},
                    {"name": "register ", "shortcuts": ["`reg`", "`rg`"], "description": "Register as a hunter", "example": "sl register"},
                    {"name": "quest ", "shortcuts": ["`qst`", "`qs`"], "description": "View and manage quests", "example": "sl quest"}
                ]
            },
            "Mission/Dungeon Commands": {
                "description": "Complete missions and explore dangerous dungeons.",
                "emoji": "🏹",
                "commands": [
                    {"name": "mission ", "shortcuts": ["`ms`", "`mis`"], "description": "View and start missions", "example": "sl mission"},
                    {"name": "dungeon ", "shortcuts": ["`dg`", "`dgn`"], "description": "Enter and challenge dungeons for rewards", "example": "sl dungeon"}
                ]
            },
            "Fight Commands": {
                "description": "Engage in epic battles and challenge other hunters.",
                "emoji": "⚔️",
                "commands": [
                    {"name": "challenge ", "shortcuts": ["`ch`", "`chl`"], "description": "Challenge other hunters", "example": "sl challenge @username"},
                    {"name": "fight ", "shortcuts": ["`f`", "`fg`"], "description": "Start a hunter battle", "example": "sl fight"}
                ]
            },
            "Inventory Commands": {
                "description": "Manage your hunter's equipment, items, and resources.",
                "emoji": "🎒",
                "commands": [
                    {"name": "capsule ", "shortcuts": ["`cap`", "`cp`"], "description": "Open item capsules", "example": "sl capsule acap/dcap/scap/hcap character name"},
                    {"name": "charm ", "shortcuts": ["`chm`", "`chr`"], "description": "Enhance your character's chosen stat by 20%", "example": "sl charm attack/health/defence/speed character name"},
                    {"name": "enhance ", "shortcuts": ["`enh`", "`en`"], "description": "Enhance your character's XP gains by 20%", "example": "sl enhance character"},
                    {"name": "equip ", "shortcuts": ["`eq`", "`ep`"], "description": "Equip weapons from characters", "example": "sl equip legendary_sword character_name"},
                    {"name": "inv ", "shortcuts": ["`inv`", "`i`"], "description": "View your inventory", "example": "sl inv"},
                    {"name": "invbooster ", "shortcuts": ["`ib`", "`ibt`"], "description": "View your boosters", "example": "sl invbooster"},
                    {"name": "invchest ", "shortcuts": ["`ic`", "`ich`"], "description": "View your chests", "example": "sl invchest"},
                    {"name": "invkey ", "shortcuts": ["`ik`", "`ikey`"], "description": "View your keys", "example": "sl invkey"},
                    {"name": "invstone ", "shortcuts": ["`is`", "`ist`"], "description": "View your stones", "example": "sl invstone"},
                    {"name": "invweapon ", "shortcuts": ["`iw`", "`iwp`"], "description": "View your weapons", "example": "sl invweapon"},
                    {"name": "loot ", "shortcuts": ["`lt`", "`lo`"], "description": "View and collect loot", "example": "sl loot chest_name 3"},
                    {"name": "reset ", "shortcuts": ["`rst`", "`rt`"], "description": "Reset timers", "example": "sl reset mission/dungeon/fight/rolls"},
                    {"name": "rstat ", "shortcuts": ["`rs`", "`rts`"], "description": "Resets your character's manually assigned stat points", "example": "sl rstat character"},
                    {"name": "sell ", "shortcuts": ["`sl`", "`se`"], "description": "Sell Essence Stones from inventory", "example": "sl sell Izawa"},
                    {"name": "unequip ", "shortcuts": ["`ue`", "`uq`"], "description": "Unequip weapons from characters", "example": "sl unequip weapon character_name"},
                    {"name": "xpboost ", "shortcuts": ["`xb`", "`xp`"], "description": "Boosts your character's xp", "example": "sl xpboost character name quantity(Optional)"}
                ]
            },
            "Shop Commands": {
                "description": "Purchase items and gear to enhance your hunter abilities.",
                "emoji": "🛒",
                "commands": [
                    {"name": "buy ", "shortcuts": ["`by`", "`bu`"], "description": "Buy items from the shop", "example": "sl buy legendary_sword quantity"},
                    {"name": "shop ", "shortcuts": ["`sh`", "`sp`"], "description": "View shop inventory", "example": "sl shop"}
                ]
            },
            "Spawn Commands": {
                "description": "Summon and manage your hunter cards and weapons.",
                "emoji": "🃏",
                "commands": [
                    {"name": "arise ", "shortcuts": ["`ars`", "`ar`"], "description": "Summon new hunter cards", "example": "sl arise"},
                    {"name": "carddetails ", "shortcuts": ["`cd`", "`cdt`"], "description": "View detailed card information", "example": "sl carddetails legendary_hunter"},
                    {"name": "cardlist ", "shortcuts": ["`cl`", "`clt`"], "description": "View all available cards", "example": "sl cardlist"},
                    {"name": "cardview ", "shortcuts": ["`cv`", "`cvw`"], "description": "View specific card details", "example": "sl cardview shadow monarch"},
                    {"name": "mycards ", "shortcuts": ["`mc`", "`myc`"], "description": "Display your card collection", "example": "sl mycards"},
                    {"name": "myweapons ", "shortcuts": ["`mw`", "`myw`"], "description": "Show your weapons", "example": "sl myweapons"},
                    {"name": "weaponlist ", "shortcuts": ["`wl`", "`wlt`"], "description": "View all available weapons", "example": "sl weaponlist"},
                    {"name": "weaponview ", "shortcuts": ["`wv`", "`wvw`"], "description": "View specific weapon details", "example": "sl weaponview shadow_blade"}
                ]
            },
            "Team Commands": {
                "description": "Create and manage your hunter team.",
                "emoji": "👥",
                "commands": [
                    {"name": "add ", "shortcuts": ["`ad`", "`tad`"], "description": "Add a member to your team", "example": "sl add hunter_name"},
                    {"name": "remove ", "shortcuts": ["`rm`", "`trm`"], "description": "Remove a team member", "example": "sl remove hunter_name"},
                    {"name": "swap ", "shortcuts": ["`sw`", "`tsw`"], "description": "Swap team member positions", "example": "sl swap first_hunter,second_hunter"},
                    {"name": "team ", "shortcuts": ["`tv`", "`tvw`"], "description": "View your current team", "example": "sl team"}
                ]
            },
            "Trade Commands": {
                "description": "Trade items and weapons with other hunters.",
                "emoji": "🤝",
                "commands": [
                    {"name": "iadd ", "shortcuts": ["`ia`", "`ida`"], "description": "Add items to trade", "example": "sl iadd item_name quantity"},
                    {"name": "iremove ", "shortcuts": ["`ir`", "`irm`"], "description": "Remove items from trade", "example": "sl iremove item_name"},
                    {"name": "trade ", "shortcuts": ["`tr`", "`td`"], "description": "Initiate a trade", "example": "sl trade @username"},
                    {"name": "wadd ", "shortcuts": ["`wa`", "`wda`"], "description": "Add Won Currency to trade", "example": "sl wadd amount"},
                    {"name": "wremove ", "shortcuts": ["`wr`", "`wrm`"], "description": "Remove Won Currency from trade", "example": "sl wremove amount"}
                ]
            }
        }

    @commands.command(name='help', aliases=['h', 'commands'])
    async def help_command(self, ctx, *args):
        """
        Displays the help menu or details for a specific command
        """
        # If a specific command is requested
        if args:
            cmd_name = args[0].lower()
            for category, data in self.categories.items():
                for cmd in data['commands']:
                    if (cmd_name == cmd['name'].strip().lower() or 
                        any(cmd_name == shortcut.strip('`').lower() for shortcut in cmd['shortcuts'])):
                        
                        embed = discord.Embed(
                            title=f"📘 Command Details: {cmd['name']}",
                            color=discord.Color.blue()
                        )
                        embed.add_field(name="Name", value=cmd['name'], inline=False)
                        embed.add_field(name="Shortcuts", value=' | '.join(cmd['shortcuts']), inline=False)
                        embed.add_field(name="Description", value=cmd['description'], inline=False)
                        embed.add_field(name="Example Usage", value=cmd['example'], inline=False)
                        
                        await ctx.send(embed=embed)
                        return

            # If no command found
            await ctx.send(f"❌ No command found with name or shortcut: {cmd_name}")
            return

        # Default help menu
        help_view = SoloLevelingHelpView(self.categories, ctx.author)
        help_view.message = await ctx.send(embed=help_view.get_initial_embed(), view=help_view)

class SoloLevelingHelpView(discord.ui.View):
    def __init__(self, categories, author):
        super().__init__(timeout=180)  # 3 minutes timeout
        self.categories = categories
        self.current_category = None
        self.current_page = 0
        self.message = None
        self.author = author
        
        # Color palette
        self.colors = {
            "primary": discord.Color.from_rgb(87, 99, 255),  # Soft blue
            "secondary": discord.Color.from_rgb(120, 130, 230),  # Lighter blue
            "background": discord.Color.from_rgb(240, 242, 255)  # Very light blue
        }

        # Enhanced navigation
        self.add_item(self.create_category_select())
        self.create_navigation_buttons()

    def create_navigation_buttons(self):
        # Style buttons for better visual hierarchy
        self.prev_button = discord.ui.Button(
            label="◀️ Previous", 
            style=discord.ButtonStyle.gray, 
            custom_id="prev"
        )
        self.next_button = discord.ui.Button(
            label="Next ▶️", 
            style=discord.ButtonStyle.gray, 
            custom_id="next"
        )
        self.back_button = discord.ui.Button(
            label="↩️ Categories", 
            style=discord.ButtonStyle.blurple, 
            custom_id="back"
        )

        # Add callbacks
        self.prev_button.callback = self.previous_callback
        self.next_button.callback = self.next_callback
        self.back_button.callback = self.back_callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                f"🚫 Only {self.author.mention} can interact with this help menu.", 
                ephemeral=True
            )
            return False
        return True

    def get_initial_embed(self):
        embed = discord.Embed(
            title="Solo Leveling Bot - Hunter Command Central",
            description=(
                "**Welcome, Emerging Hunter!**\n\n"
                "Explore your abilities and master the command system:\n\n"
                "• 🔍 Select a category from the dropdown\n\n"
                "• 🔼 Navigate through commands\n\n"
                "• 📚 Learn command details and examples\n\n"
                "**Pro Tip:** Use `sl help <command>` for specific command details!"
            ),
            color=self.colors["primary"]
        )
        embed.set_footer(
            text="Your journey begins with knowledge. Choose wisely, Hunter!",
        )
        return embed

    def create_category_select(self):
        options = [
            discord.SelectOption(
                label=category, 
                value=category, 
                description=self.categories[category]['description'][:100],
                emoji=self.categories[category].get('emoji', '📋')
            ) for category in self.categories
        ]
        select = discord.ui.Select(
            placeholder="🔍 Select a Command Category",
            options=options,
            custom_id="category_select"
        )
        select.callback = self.category_select_callback
        return select

    def create_category_embed(self):
        if not self.current_category:
            return self.get_initial_embed()

        commands = self.categories[self.current_category]['commands']
        description = self.categories[self.current_category]['description']
        
        commands_per_page = 5  # Reduced to 5 for better readability
        total_pages = (len(commands) - 1) // commands_per_page + 1
        
        start_index = self.current_page * commands_per_page
        end_index = start_index + commands_per_page
        page_commands = commands[start_index:end_index]

        embed = discord.Embed(
            title=f"{self.categories[self.current_category].get('emoji', '📋')} {self.current_category}",
            description=f"*{description}*\n\n" + 
                       "\n\n".join([
                           f"**`sl {cmd['name']}`**\n"
                           f"**Shortcuts:** {' | '.join(cmd['shortcuts'])}\n"
                           f"📍 {cmd['description']}\n"
                           f"🔹 Example: **{cmd['example']}**" 
                           for cmd in page_commands
                       ]),
            color=self.colors["secondary"]
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • Navigate with buttons")
        return embed

    async def update_view(self, interaction: discord.Interaction):
        self.clear_items()
        
        if self.current_category:
            total_commands = len(self.categories[self.current_category]['commands'])
            commands_per_page = 5
            total_pages = (total_commands - 1) // commands_per_page + 1
            
            if total_pages > 1:
                self.add_item(self.prev_button)
                self.add_item(self.next_button)
            self.add_item(self.back_button)
        else:
            self.add_item(self.create_category_select())
        
        await interaction.response.edit_message(embed=self.create_category_embed(), view=self)

    async def category_select_callback(self, interaction: discord.Interaction):
        self.current_category = interaction.data['values'][0]
        self.current_page = 0
        await self.update_view(interaction)

    async def previous_callback(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view(interaction)

    async def next_callback(self, interaction: discord.Interaction):
        total_commands = len(self.categories[self.current_category]['commands'])
        total_pages = (total_commands - 1) // 5 + 1
        
        if self.current_page < total_pages - 1:
            self.current_page += 1
        await self.update_view(interaction)

    async def back_callback(self, interaction: discord.Interaction):
        self.current_category = None
        self.current_page = 0
        await self.update_view(interaction)

async def setup(bot):
    await bot.add_cog(HelpCommands(bot))