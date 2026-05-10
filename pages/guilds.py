import discord
from discord.ext import commands
import random
from datetime import datetime
from utils.database import create_db_connection
from mysql.connector import Error
import asyncio
import re
class GuildCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
    async def get_user_id(self, connection, user_authorid):
        user_query = "SELECT user_id, guild_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (user_authorid,))
        if not user_result:
            return None, None
        return user_result[0]['user_id'], user_result[0]['guild_id']
    def parse_input(self, input_string):
        pattern = r'\((.*?)\)'
        matches = re.findall(pattern, input_string)
        return matches
    @commands.command(name='donate')
    async def donate(self, ctx, *, args):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()

        # Split the input by comma
        try:
            guild_name, won_currency = args.split(',')
            guild_name = guild_name.strip()
            won_currency = int(won_currency.strip())
        except ValueError:
            await ctx.send("Error: Invalid input format. Please use 'sl donate guild_name, amount'.")
            return

        user_id, _ = await self.get_user_id(connection, user_authorid)
        if not user_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        # Check if user has enough currency
        currency_query = "SELECT won_currency FROM user WHERE user_id = %s"
        currency_result = self.execute_query(connection, currency_query, (user_id,))
        if not currency_result or currency_result[0]['won_currency'] < won_currency:
            await ctx.send("Error: You don't have enough won currency to make this donation.")
            return

        # Get guild_id from guild_name
        guild_query = "SELECT guild_id, fund_cash FROM guild WHERE guild_name = %s"
        guild_result = self.execute_query(connection, guild_query, (guild_name,))
        if not guild_result:
            await ctx.send(f"Error: Guild '{guild_name}' does not exist.")
            return

        guild_id = guild_result[0]['guild_id']
        current_fund_cash = guild_result[0]['fund_cash']

        # Update user's currency
        update_user_query = "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s"
        self.execute_query(connection, update_user_query, (won_currency, user_id))

        # Update guild's fund cash
        update_guild_query = "UPDATE guild SET fund_cash = fund_cash + %s WHERE guild_id = %s"
        self.execute_query(connection, update_guild_query, (won_currency, guild_id))

        await ctx.send(f"You have successfully donated {won_currency} won to the guild '{guild_name}'.")

    @commands.command(name='updateguildpermission')
    async def update_guild_permission(self, ctx):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()

        user_id, guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        if not guild_id:
            await ctx.send("Error: You're not in any guild.")
            return

        # Check if user is the guild owner
        guild_query = "SELECT user_id, private FROM guild WHERE guild_id = %s"
        guild_result = self.execute_query(connection, guild_query, (guild_id,))
        if not guild_result or guild_result[0]['user_id'] != user_id:
            await ctx.send("Error: Only the guild owner can update guild permissions.")
            return

        current_status = "free to join" if guild_result[0]['private'] == 0 else "on demand"
        new_status = "on demand" if guild_result[0]['private'] == 0 else "free to join"

        confirm_message = await ctx.send(f"Your current guild status is {current_status}. Do you want to switch it to {new_status}? React with ✅ to confirm or ❌ to cancel.")
        await confirm_message.add_reaction("✅")
        await confirm_message.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_message.id

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            if str(reaction.emoji) == "✅":
                new_private_value = 1 if guild_result[0]['private'] == 0 else 0
                update_guild_query = "UPDATE guild SET private = %s WHERE guild_id = %s"
                self.execute_query(connection, update_guild_query, (new_private_value, guild_id))
                await ctx.send(f"Guild status has been successfully updated to {new_status}.")
            else:
                await ctx.send("Guild status update cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("Guild status update timed out.")
    @commands.command(name='giveguildownership')
    async def give_guild_ownership(self, ctx, new_owner: discord.Member):
        current_owner_authorid = str(ctx.author.id)
        new_owner_authorid = str(new_owner.id)
        connection = create_db_connection()
        
        current_owner_id, current_guild_id = await self.get_user_id(connection, current_owner_authorid)
        if not current_owner_id or not current_guild_id:
            await ctx.send("Error: You need to be registered and in a guild to use this command.")
            return

        owner_query = "SELECT * FROM guild WHERE guild_id = %s AND user_id = %s"
        owner_result = self.execute_query(connection, owner_query, (current_guild_id, current_owner_id))
        if not owner_result:
            await ctx.send("Error: Only the owner of the guild can transfer ownership.")
            return

        new_owner_id, new_owner_guild_id = await self.get_user_id(connection, new_owner_authorid)
        if not new_owner_id or new_owner_guild_id:
            await ctx.send("Error: The specified user is not registered or is already in a guild.")
            return

        confirm_message = await ctx.send(f"Are you sure you want to transfer guild ownership to {new_owner.mention}? React with ✅ to confirm or ❌ to cancel.")
        await confirm_message.add_reaction("✅")
        await confirm_message.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_message.id

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            if str(reaction.emoji) == "✅":
                #update_current_owner_query = "UPDATE user SET guild_id = NULL WHERE user_id = %s"
                update_new_owner_query = "UPDATE user SET guild_id = %s WHERE user_id = %s"
                update_guild_query = "UPDATE guild SET user_id = %s WHERE guild_id = %s"

                #self.execute_query(connection, update_current_owner_query, (current_owner_id,))
                self.execute_query(connection, update_new_owner_query, (current_guild_id, new_owner_id))
                self.execute_query(connection, update_guild_query, (new_owner_id, current_guild_id))

                await ctx.send(f"Guild ownership has been successfully transferred to {new_owner.mention}.")
            else:
                await ctx.send("Guild ownership transfer cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("Guild ownership transfer timed out.")

    @commands.command(name='leaveguild')
    async def leave_guild(self, ctx):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        user_id, guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        if not guild_id:
            await ctx.send("Error: You're not in any guild to leave.")
            return

        # Check if the user is the guild owner
        owner_query = "SELECT * FROM guild WHERE guild_id = %s AND user_id = %s"
        owner_result = self.execute_query(connection, owner_query, (guild_id, user_id))
        if owner_result:
            await ctx.send("Error: You can't leave your own guild. You need to completely remove it or transfer ownership to someone else.")
            return

        # Remove user from guild
        update_user_query = "UPDATE user SET guild_id = NULL WHERE user_id = %s"
        update_guild_query = "UPDATE guild SET quantiter = quantiter - 1 WHERE guild_id = %s"
        self.execute_query(connection, update_user_query, (user_id,))
        self.execute_query(connection, update_guild_query, (guild_id,))

        await ctx.send("You have successfully left your guild.")

    @commands.command(name='removemyguild')
    async def remove_my_guild(self, ctx):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        user_id, guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id or not guild_id:
            await ctx.send("Error: You need to be registered and in a guild to use this command.")
            return

        owner_query = "SELECT * FROM guild WHERE guild_id = %s AND user_id = %s"
        owner_result = self.execute_query(connection, owner_query, (guild_id, user_id))
        if not owner_result:
            await ctx.send("Error: You don't own a guild. Use 'sl createguild (name) (description)' to create one.")
            return

        confirm_message = await ctx.send("Are you sure you want to remove your guild? This action cannot be undone. React with ✅ to confirm or ❌ to cancel.")
        await confirm_message.add_reaction("✅")
        await confirm_message.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_message.id

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            if str(reaction.emoji) == "✅":
                update_users_query = "UPDATE user SET guild_id = NULL WHERE guild_id = %s"
                delete_invitations_query = "DELETE FROM invitation WHERE guild_id = %s"
                delete_guild_query = "DELETE FROM guild WHERE guild_id = %s"

                self.execute_query(connection, update_users_query, (guild_id,))
                self.execute_query(connection, delete_invitations_query, (guild_id,))
                self.execute_query(connection, delete_guild_query, (guild_id,))

                await ctx.send("Your guild has been successfully removed.")
            else:
                await ctx.send("Guild removal cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("Guild removal timed out.")


    @commands.command(name='createguild')
    async def create_guild(self, ctx, *, args):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        user_id, guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id or guild_id:
            await ctx.send("Error: You need to be registered and not in a guild to use this command.")
            return

        currency_query = "SELECT won_currency FROM user WHERE user_id = %s"
        currency_result = self.execute_query(connection, currency_query, (user_id,))
        if not currency_result or currency_result[0]['won_currency'] < 100000:
            await ctx.send("Error: You don't have enough won (100,000) to create a guild.")
            return

        parsed_input = self.parse_input(args)
        if len(parsed_input) == 0 or len(parsed_input) > 2:
            await ctx.send("Error: Invalid command format. Use 'sl createguild (name) (description)'.")
            return

        guild_name = parsed_input[0]
        guild_description = parsed_input[1] if len(parsed_input) == 2 else ""

        privacy_message = await ctx.send("Do you want your guild to be free to join (✅) or on demand (❌)?")
        await privacy_message.add_reaction("✅")
        await privacy_message.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == privacy_message.id

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            private = 0 if str(reaction.emoji) == "✅" else 1

            insert_guild_query = """
            INSERT INTO guild (guild_name, guild_description, private, user_id, level_cap, money_cap, quantiter)
            VALUES (%s, %s, %s, %s, 50, 20000, 1)
            """
            self.execute_query(connection, insert_guild_query, (guild_name, guild_description, private, user_id))

            update_user_query = """
            UPDATE user SET won_currency = won_currency - 100000, guild_id = LAST_INSERT_ID()
            WHERE user_id = %s
            """
            self.execute_query(connection, update_user_query, (user_id,))

            await ctx.send(f"Your guild '{guild_name}' has been successfully created!")
        except asyncio.TimeoutError:
            await ctx.send("Guild creation timed out.")

    @commands.command(name='seemyguild')
    async def see_my_guild(self, ctx):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        user_id, guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        if not guild_id:
            await ctx.send("Error: You're not in any guild.")
            return

        # Get guild info
        guild_query = """
        SELECT guild_name, guild_description, quantiter, quantiter_max, level, private, fund_cash, money_cap
        FROM guild
        WHERE guild_id = %s
        """
        guild_result = self.execute_query(connection, guild_query, (guild_id,))

        if not guild_result:
            await ctx.send("Error: Could not retrieve guild information.")
            return

        guild = guild_result[0]

        embed = discord.Embed(title=f"Your Guild: {guild['guild_name']}", color=discord.Color.green())
        
        description = guild['guild_description'] if guild['guild_description'] else "No description"
        embed.add_field(name="Description", value=description, inline=False)
        
        embed.add_field(name="Members", value=f"{guild['quantiter']}/{guild['quantiter_max']}", inline=True)
        embed.add_field(name="Level", value=guild['level'], inline=True)
        embed.add_field(name="Next Level", value=f"{guild['fund_cash']}/{guild['money_cap']}", inline=True)
        
        join_status = "On Demand" if guild['private'] == 1 else "Free to Join"
        embed.add_field(name="Join Status", value=join_status, inline=True)

        await ctx.send(embed=embed)

    @commands.command(name='joinguild')
    async def join_guild(self, ctx, *, guild_name: str):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        user_id, current_guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id or current_guild_id:
            await ctx.send("Error: You need to be registered and not in a guild to use this command.")
            return

        guild_query = "SELECT guild_id, private, quantiter, quantiter_max FROM guild WHERE guild_name = %s"
        guild_result = self.execute_query(connection, guild_query, (guild_name,))
        if not guild_result:
            await ctx.send(f"Error: Guild '{guild_name}' does not exist.")
            return
        
        guild_id = guild_result[0]['guild_id']
        is_private = guild_result[0]['private']
        current_members = guild_result[0]['quantiter']
        max_members = guild_result[0]['quantiter_max']

        invitation_query = "SELECT * FROM invitation WHERE user_id = %s AND guild_id = %s"
        invitation_result = self.execute_query(connection, invitation_query, (user_id, guild_id))
        if invitation_result:
            await ctx.send("Your application to this guild is still under review.")
            return

        if not is_private:
            if current_members >= max_members:
                await ctx.send("This guild is already at maximum capacity.")
                return
            update_user_query = "UPDATE user SET guild_id = %s WHERE user_id = %s"
            self.execute_query(connection, update_user_query, (guild_id, user_id))
            update_guild_query = "UPDATE guild SET quantiter = quantiter + 1 WHERE guild_id = %s"
            self.execute_query(connection, update_guild_query, (guild_id,))
            await ctx.send(f"You have successfully joined the guild '{guild_name}'!")
        else:
            confirm_message = await ctx.send(
                f"The guild '{guild_name}' is currently on demand. Do you wish to apply for membership? Your application will be reviewed by the guild owner. React with ✅ to confirm or ❌ to cancel."
            )
            await confirm_message.add_reaction("✅")
            await confirm_message.add_reaction("❌")

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_message.id

            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
                if str(reaction.emoji) == "✅":
                    insert_invitation_query = "INSERT INTO invitation (guild_id, user_id) VALUES (%s, %s)"
                    self.execute_query(connection, insert_invitation_query, (guild_id, user_id))

                    owner_query = "SELECT u.user_authorid FROM user u JOIN guild g ON u.user_id = g.user_id WHERE g.guild_id = %s"
                    owner_result = self.execute_query(connection, owner_query, (guild_id,))
                    if owner_result:
                        owner_authorid = owner_result[0]['user_authorid']
                        owner = await self.bot.fetch_user(int(owner_authorid))
                        await owner.send(f"You have received a joining request for your guild '{guild_name}' from user {ctx.author.name} ({ctx.author.id}).")

                    await ctx.send("Your application has been sent successfully. Please be patient while it's being reviewed.")
                else:
                    await ctx.send("Application cancelled.")
            except asyncio.TimeoutError:
                await ctx.send("Application timed out.")

    @commands.command(name='seeguildsrequests')
    async def see_guilds_requests(self, ctx):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        user_id, guild_id = await self.get_user_id(connection, user_authorid)
        if not user_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        if not guild_id:
            await ctx.send("Error: You're not in any guild to use this command.")
            return

        # Check if the user is the guild owner
        owner_query = "SELECT * FROM guild WHERE guild_id = %s AND user_id = %s"
        owner_result = self.execute_query(connection, owner_query, (guild_id, user_id))
        if not owner_result:
            await ctx.send("Error: Only the owner of your guild can view the invites.")
            return

        # Get pending invitations
        invitations_query = """
        SELECT u.user_authorid 
        FROM invitation i 
        JOIN user u ON i.user_id = u.user_id 
        WHERE i.guild_id = %s
        """
        invitations_result = self.execute_query(connection, invitations_query, (guild_id,))

        if not invitations_result:
            await ctx.send("There are no current invitations for your guild.")
            return

        embed = discord.Embed(title="Guild Join Requests", color=discord.Color.blue())
        for i, invite in enumerate(invitations_result, 1):
            embed.add_field(name=f"Request #{i}", value=f"User ID: {invite['user_authorid']}", inline=False)

        await ctx.send(embed=embed)

    @commands.command(name='acceptguildrequest')
    async def accept_guild_request(self, ctx, user_authorid: str):
        connection = create_db_connection()
        
        owner_id, owner_guild_id = await self.get_user_id(connection, str(ctx.author.id))
        if not owner_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        if not owner_guild_id:
            await ctx.send("Error: You're not in any guild to use this command.")
            return

        # Check if the user is the guild owner
        owner_query = "SELECT * FROM guild WHERE guild_id = %s AND user_id = %s"
        owner_result = self.execute_query(connection, owner_query, (owner_guild_id, owner_id))
        if not owner_result:
            await ctx.send("Error: Only the owner of your guild can accept invites.")
            return

        # Get the user_id of the applicant
        applicant_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        applicant_result = self.execute_query(connection, applicant_query, (user_authorid,))
        if not applicant_result:
            await ctx.send("Error: The specified user is not registered.")
            return
        applicant_id = applicant_result[0]['user_id']

        # Delete the invitation and update the user's guild
        delete_invitation_query = "DELETE FROM invitation WHERE user_id = %s"
        update_user_query = "UPDATE user SET guild_id = %s WHERE user_id = %s"
        update_guild_query = "UPDATE guild SET quantiter = quantiter + 1 WHERE guild_id = %s"

        self.execute_query(connection, delete_invitation_query, (applicant_id,))
        self.execute_query(connection, update_user_query, (owner_guild_id, applicant_id))
        self.execute_query(connection, update_guild_query, (owner_guild_id,))

        # Get guild name
        guild_name_query = "SELECT guild_name FROM guild WHERE guild_id = %s"
        guild_name_result = self.execute_query(connection, guild_name_query, (owner_guild_id,))
        guild_name = guild_name_result[0]['guild_name']

        # Send DM to the accepted user
        try:
            user = await self.bot.fetch_user(int(user_authorid))
            embed = discord.Embed(title="Guild Application Accepted!", color=discord.Color.green())
            embed.description = f"Your application to join the guild '{guild_name}' has been accepted!"
            await user.send(embed=embed)
        except discord.HTTPException:
            await ctx.send(f"Couldn't send a DM to the user, but their application to '{guild_name}' has been accepted.")

        await ctx.send(f"You've successfully accepted the user into your guild.")

    @commands.command(name='denyguildrequest')
    async def deny_guild_request(self, ctx, user_authorid: str):
        connection = create_db_connection()
        
        owner_id, owner_guild_id = await self.get_user_id(connection, str(ctx.author.id))
        if not owner_id:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        if not owner_guild_id:
            await ctx.send("Error: You're not in any guild to use this command.")
            return

        # Check if the user is the guild owner
        owner_query = "SELECT * FROM guild WHERE guild_id = %s AND user_id = %s"
        owner_result = self.execute_query(connection, owner_query, (owner_guild_id, owner_id))
        if not owner_result:
            await ctx.send("Error: Only the owner of your guild can deny invites.")
            return

        # Get the user_id of the applicant
        applicant_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        applicant_result = self.execute_query(connection, applicant_query, (user_authorid,))
        if not applicant_result:
            await ctx.send("Error: The specified user is not registered.")
            return
        applicant_id = applicant_result[0]['user_id']

        # Delete the invitation
        delete_invitation_query = "DELETE FROM invitation WHERE user_id = %s AND guild_id = %s"
        self.execute_query(connection, delete_invitation_query, (applicant_id, owner_guild_id))

        # Get guild name
        guild_name_query = "SELECT guild_name FROM guild WHERE guild_id = %s"
        guild_name_result = self.execute_query(connection, guild_name_query, (owner_guild_id,))
        guild_name = guild_name_result[0]['guild_name']

        # Send DM to the denied user
        try:
            user = await self.bot.fetch_user(int(user_authorid))
            embed = discord.Embed(title="Guild Application Denied", color=discord.Color.red())
            embed.description = f"Your application to join the guild '{guild_name}' has been denied."
            await user.send(embed=embed)
        except discord.HTTPException:
            await ctx.send(f"Couldn't send a DM to the user, but their application to '{guild_name}' has been denied.")

        await ctx.send(f"You've successfully denied the user's application to your guild.")

    @commands.command(name='seeallguilds')
    async def see_all_guilds(self, ctx):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']

        # Get all guilds ordered by level
        guilds_query = """
        SELECT guild_id, guild_name, guild_description, quantiter, quantiter_max, level, private
        FROM guild
        ORDER BY level DESC
        """
        guilds_result = self.execute_query(connection, guilds_query)

        if not guilds_result:
            await ctx.send("No guilds found.")
            return

        embed = discord.Embed(title="All Guilds", color=discord.Color.blue())

        for i, guild in enumerate(guilds_result, 1):
            name = f"{i}# {guild['guild_name']}"
            description = guild['guild_description'] if guild['guild_description'] else "No description"
            members = f"{guild['quantiter']}/{guild['quantiter_max']}"
            level = guild['level']
            join_status = "On Demand" if guild['private'] == 1 else "Free to Join"

            value = f"Description: {description}\n"
            value += f"Members: {members}\n"
            value += f"Level: {level}\n"
            value += f"Join Status: {join_status}"

            embed.add_field(name=name, value=value, inline=False)

        await ctx.send(embed=embed)

    @commands.command(name='seeguild')
    async def see_guild(self, ctx, *, guild_name: str):
        user_authorid = str(ctx.author.id)
        connection = create_db_connection()
        
        # Get user_id
        user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, user_query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to be registered to use this command.")
            return

        user_id = user_result[0]['user_id']

        # Get guild info
        guild_query = """
        SELECT guild_id, guild_name, guild_description, quantiter, quantiter_max, level, private, fund_cash, money_cap
        FROM guild
        WHERE guild_name = %s
        """
        guild_result = self.execute_query(connection, guild_query, (guild_name,))

        if not guild_result:
            await ctx.send(f"No guild found with the name '{guild_name}'.")
            return

        guild = guild_result[0]

        embed = discord.Embed(title=f"Guild: {guild['guild_name']}", color=discord.Color.green())
        
        description = guild['guild_description'] if guild['guild_description'] else "No description"
        embed.add_field(name="Description", value=description, inline=False)
        
        embed.add_field(name="Members", value=f"{guild['quantiter']}/{guild['quantiter_max']}", inline=True)
        embed.add_field(name="Level", value=guild['level'], inline=True)
        
        join_status = "On Demand" if guild['private'] == 1 else "Free to Join"
        embed.add_field(name="Join Status", value=join_status, inline=True)
        
        embed.add_field(name="Fund Cash", value=f"{guild['fund_cash']:,}", inline=True)
        embed.add_field(name="Money Cap", value=f"{guild['money_cap']:,}", inline=True)

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(GuildCommands(bot)) 