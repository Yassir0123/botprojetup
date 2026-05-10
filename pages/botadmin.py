import discord
from discord.ext import commands
import asyncio
from datetime import datetime

class BotServers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_id = 818132161534230559  # Your specified user ID
    @commands.command(name="get_username")
    async def get_username(self, ctx, user_id: int):
        try:
            if ctx.author.id != self.owner_id:
                await ctx.send("You don't have permission to use this command.")
                return

            user = await self.bot.fetch_user(user_id)
            await ctx.send(f"User: {user.name}#{user.discriminator} (ID: {user.id})")
        except Exception as e:
            await ctx.send(f"❌ Could not fetch user: {str(e)}")
    @commands.command(name="servers", aliases=["guilds", "botservers"])
    async def list_servers(self, ctx):
        """Lists all servers the bot is in. Only usable by the bot owner."""
        # Check if the command user is the authorized user
        if ctx.author.id != self.owner_id:
            await ctx.send("You don't have permission to use this command.")
            return

        # Add debug message
        await ctx.send("Fetching server list...")

        # Get all servers the bot is in
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)
        
        # Debug message to confirm guilds were retrieved
        print(f"Found {len(guilds)} guilds")
        
        # Create paginated embeds (10 servers per page)
        embeds = []
        for i in range(0, len(guilds), 10):
            chunk = guilds[i:i+10]
            
            embed = discord.Embed(
                title=f"Bot Servers ({len(guilds)} total)",
                description="List of all servers this bot is in",
                color=discord.Color.blue()
            )
            
            # Debug to check field construction
            print(f"Creating embed for {len(chunk)} servers (page {i//10 + 1})")
            
            for index, guild in enumerate(chunk, start=i+1):
                # Get server info
                created_at = guild.created_at.strftime("%Y-%m-%d") if guild.created_at else "Unknown"
                
                # Handle potential None for joined_at safely
                try:
                    joined_at = guild.me.joined_at.strftime("%Y-%m-%d") if guild.me.joined_at else "Unknown"
                except AttributeError:
                    joined_at = "Unknown"
                
                # Handle potential None for owner safely
                try:
                    owner_info = f"{guild.owner} ({guild.owner.id})" if guild.owner else "Unknown"
                except AttributeError:
                    owner_info = "Unknown"
                
                # Format server details
                value = (
                    f"**ID:** {guild.id}\n"
                    f"**Owner:** {owner_info}\n"
                    f"**Members:** {guild.member_count}\n"
                    f"**Created:** {created_at}\n"
                    f"**Joined:** {joined_at}"
                )
                
                embed.add_field(
                    name=f"{index}. {guild.name}",
                    value=value,
                    inline=False
                )
            
            embed.set_footer(text=f"Page {i//10 + 1}/{(len(guilds)-1)//10 + 1}")
            embeds.append(embed)
        
        # Send the first embed with try/except for better error handling
        if not embeds:
            await ctx.send("No servers found.")
            return
            
        try:
            # Debug message before sending embed
            print("Attempting to send first embed...")
            
            # Alternative approach using a simpler embed first
            test_embed = discord.Embed(
                title="Server List",
                description=f"The bot is in {len(guilds)} servers.",
                color=discord.Color.blue()
            )
            await ctx.send(embed=test_embed)
            
            # Now try the full embed
            message = await ctx.send(embed=embeds[0])
            print("First embed sent successfully!")
            
            # If only one page, don't add reactions
            if len(embeds) == 1:
                return
                
            # Add navigation reactions
            await message.add_reaction("⬅️")
            await message.add_reaction("➡️")
            
            def check(reaction, user):
                return (
                    user == ctx.author and 
                    reaction.message.id == message.id and 
                    str(reaction.emoji) in ["⬅️", "➡️"]
                )
            
            current_page = 0
            
            # Reaction handler loop
            while True:
                try:
                    reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
                    
                    # Remove user reaction
                    await message.remove_reaction(reaction, user)
                    
                    # Change page based on reaction
                    if str(reaction.emoji) == "➡️" and current_page < len(embeds) - 1:
                        current_page += 1
                        await message.edit(embed=embeds[current_page])
                        
                    elif str(reaction.emoji) == "⬅️" and current_page > 0:
                        current_page -= 1
                        await message.edit(embed=embeds[current_page])
                        
                except asyncio.TimeoutError:
                    # Remove reactions after timeout
                    await message.clear_reactions()
                    break
        except Exception as e:
            print(f"Error sending embed: {str(e)}")
            await ctx.send(f"Error displaying server list: {str(e)}")
            
            # Fallback to text-based list if embeds fail
            server_list = "\n".join([f"{i+1}. {guild.name} (ID: {guild.id}) - {guild.member_count} members" 
                                   for i, guild in enumerate(guilds[:20])])
            
            if len(guilds) > 20:
                server_list += f"\n... and {len(guilds) - 20} more servers"
                
            await ctx.send(f"**Server List** (Total: {len(guilds)})\n```\n{server_list}\n```")

    # Simplified alternative with server links
    @commands.command(name="serverlist")
    async def simple_server_list(self, ctx):
        """A simpler version of the servers command with invite links. Only usable by the bot owner."""
        if ctx.author.id != self.owner_id:
            return
            
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)
        
        # Send a basic message first to check if the bot can send messages
        await ctx.send(f"I am in {len(guilds)} servers. Here's the list:")
        
        # Split server list into chunks to stay within Discord's message limit
        server_chunks = []
        current_chunk = ""
        
        for i, guild in enumerate(guilds, 1):
            server_line = f"{i}. {guild.name} (ID: {guild.id}) - {guild.member_count} members\n"
            
            # If adding this line would exceed Discord's limit, start a new chunk
            if len(current_chunk) + len(server_line) > 1900:
                server_chunks.append(current_chunk)
                current_chunk = server_line
            else:
                current_chunk += server_line
                
        # Add the last chunk if it has content
        if current_chunk:
            server_chunks.append(current_chunk)
            
        # Send each chunk as a code block
        for chunk in server_chunks:
            await ctx.send(f"```\n{chunk}\n```")

    @commands.command(name="serverinvite", aliases=["getinvite"])
    async def get_server_invite(self, ctx, server_id: int):
        """Gets an invite link for a specific server. Only usable by the bot owner."""
        if ctx.author.id != self.owner_id:
            await ctx.send("You don't have permission to use this command.")
            return
            
        guild = self.bot.get_guild(server_id)
        if not guild:
            await ctx.send(f"Cannot find server with ID {server_id}")
            return
            
        # Send a status message
        status_msg = await ctx.send(f"Attempting to create an invite for **{guild.name}**...")
        
        # Try to find a suitable channel for the invite
        invite = None
        error_message = None
        
        # Try to find a text channel where the bot has permissions to create invites
        for channel in guild.text_channels:
            try:
                # Check if bot has permission to create invites in this channel
                perms = channel.permissions_for(guild.me)
                if perms.create_instant_invite:
                    invite = await channel.create_invite(
                        max_age=86400,  # 24 hours
                        max_uses=1,     # Single use invite
                        unique=True,
                        reason=f"Requested by bot owner ({ctx.author.id})"
                    )
                    break
            except discord.Forbidden:
                continue  # Try next channel
            except Exception as e:
                error_message = str(e)
                break
                
        if invite:
            await status_msg.edit(content=f"**Server:** {guild.name}\n**Invite Link:** {invite.url}\n**Expires:** 24 hours from now\n**Uses:** One-time use")
        else:
            if error_message:
                await status_msg.edit(content=f"Failed to create invite for **{guild.name}**: {error_message}")
            else:
                await status_msg.edit(content=f"Cannot create invite for **{guild.name}**. The bot may not have the required permissions.")

    @commands.command(name="serverlinks", aliases=["allserverinvites", "allinvites"])
    async def get_all_server_invites(self, ctx):
        """Attempts to create invite links for all servers. Only usable by the bot owner."""
        if ctx.author.id != self.owner_id:
            await ctx.send("You don't have permission to use this command.")
            return
            
        guilds = self.bot.guilds
        status_msg = await ctx.send(f"Attempting to create invites for {len(guilds)} servers. This may take a while...")
        
        # To store results
        successful_invites = []
        failed_invites = []
        
        for guild in guilds:
            invite = None
            error_message = None
            
            # Try to find a text channel where the bot has permissions to create invites
            for channel in guild.text_channels:
                try:
                    # Check if bot has permission to create invites in this channel
                    perms = channel.permissions_for(guild.me)
                    if perms.create_instant_invite:
                        invite = await channel.create_invite(
                            max_age=86400,  # 24 hours
                            max_uses=1,     # Single use invite
                            unique=True,
                            reason=f"Requested by bot owner ({ctx.author.id})"
                        )
                        break
                except discord.Forbidden:
                    continue  # Try next channel
                except Exception as e:
                    error_message = str(e)
                    break
                    
            if invite:
                successful_invites.append((guild, invite))
            else:
                failed_invites.append((guild, error_message or "Missing permissions"))
                
        # Update status message when complete
        await status_msg.edit(content=f"Created {len(successful_invites)} invites out of {len(guilds)} servers.")
        
        # Send results in chunks
        if successful_invites:
            successful_chunks = []
            current_chunk = "**SUCCESSFUL INVITES:**\n"
            
            for guild, invite in successful_invites:
                line = f"• **{guild.name}** (ID: {guild.id}) - {invite.url}\n"
                
                if len(current_chunk) + len(line) > 1900:
                    successful_chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line
                    
            if current_chunk:
                successful_chunks.append(current_chunk)
                
            for chunk in successful_chunks:
                await ctx.send(chunk)
        
        if failed_invites:
            failed_chunks = []
            current_chunk = "**FAILED INVITES:**\n"
            
            for guild, reason in failed_invites:
                line = f"• **{guild.name}** (ID: {guild.id}) - Reason: {reason}\n"
                
                if len(current_chunk) + len(line) > 1900:
                    failed_chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line
                    
            if current_chunk:
                failed_chunks.append(current_chunk)
                
            for chunk in failed_chunks:
                await ctx.send(chunk)

async def setup(bot):
    await bot.add_cog(BotServers(bot))