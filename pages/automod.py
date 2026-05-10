import discord
from discord.ext import commands
import aiomysql
import re
import datetime
import traceback
import asyncio

class AutoModCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_pool = None
        self.target_guild_id = 832022150713704479  # The specific server ID to monitor
        self.mod_role_id = 832035274535993384  # Role ID to notify when users reach 3 warnings
        self.filtered_words = [
            "nigga", "fuck", "retard", "cunt", "hoe", "bitch", "slur"
            # Add more filtered words as needed
        ]
        # Compile regex patterns for faster matching
        self.word_patterns = [
            re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE) 
            for word in self.filtered_words
        ]
        
    async def create_db_pool(self):
        try:
            self.db_pool = await aiomysql.create_pool(
                host='localhost',
                user='root',
                password='',
                db='sololevelingboting',
                port=3307,
                autocommit=True
            )
            print("Database pool created successfully for AutoMod")
        except Exception as e:
            print(f"Error creating database pool for AutoMod: {e}")
            self.db_pool = None

    async def execute_query(self, query, params=None):
        if not self.db_pool:
            await self.create_db_pool()
            if not self.db_pool:
                return None
                
        async with self.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                try:
                    await cursor.execute(query, params)
                    if query.strip().upper().startswith(('SELECT', 'SHOW')):
                        result = await cursor.fetchall()
                        return result
                    else:
                        return cursor.rowcount
                except Exception as e:
                    print(f"Database query error in AutoMod: {e}")
                    print(f"Query: {query}")
                    print(f"Params: {params}")
                    return None

    @commands.Cog.listener()
    async def on_ready(self):
        """When bot is ready, create the warnings table if it doesn't exist"""
        await self.create_db_pool()
        
        # Create warnings table if it doesn't exist
        create_table_query = """
        CREATE TABLE IF NOT EXISTS user_warnings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(20) NOT NULL,
            guild_id VARCHAR(20) NOT NULL,
            warn_count INT NOT NULL DEFAULT 1,
            last_warned_at DATETIME NOT NULL,
            last_warned_for TEXT,
            UNIQUE KEY unique_user_guild (user_id, guild_id)
        )
        """
        await self.execute_query(create_table_query)
        print("AutoMod system initialized")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Process each message to check for filtered words"""
        # Ignore bot messages
        if message.author.bot:
            return
            
        # Only process messages from the target guild
        if not message.guild or message.guild.id != self.target_guild_id:
            return
            
        # Check message content against filtered words
        content = message.content.lower()
        
        # Fast check using regex patterns
        found_word = None
        for i, pattern in enumerate(self.word_patterns):
            if pattern.search(content):
                found_word = self.filtered_words[i]
                break
                
        if found_word:
            try:
                # Delete the message
                await message.delete()
                
                # Log the warning to the database
                await self.add_warning(message.author.id, message.guild.id, found_word)
                
                # Get the user's current warning count
                warn_count = await self.get_warning_count(message.author.id, message.guild.id)
                
                # Send a warning message to the user via DM
                try:
                    await message.author.send(
                        f"⚠️ Your message in {message.guild.name} was removed for containing inappropriate language.\n"
                        f"This is warning #{warn_count}. Continued violations may result in further action."
                    )
                except discord.Forbidden:
                    # Can't DM the user, send in channel instead
                    warning_msg = await message.channel.send(
                        f"⚠️ {message.author.mention} Your message was removed for containing inappropriate language.\n"
                        f"This is warning #{warn_count}. Continued violations may result in further action."
                    )
                    # Delete the warning message after 5 seconds
                    await asyncio.sleep(5)
                    try:
                        await warning_msg.delete()
                    except:
                        pass
                
                # Check if user has reached 3 warnings
                if warn_count >= 3:
                    await self.timeout_user(message.author, message.guild, found_word)
                
                # Log to a mod channel if one exists
                mod_log_channel = discord.utils.get(message.guild.text_channels, name="mod-logs")
                if mod_log_channel:
                    embed = discord.Embed(
                        title="AutoMod Action",
                        description=f"Message from {message.author.mention} was removed",
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now()
                    )
                    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                    embed.add_field(name="Reason", value=f"Filtered word: {found_word}", inline=True)
                    embed.add_field(name="Warning Count", value=str(warn_count), inline=True)
                    embed.set_footer(text=f"User ID: {message.author.id}")
                    await mod_log_channel.send(embed=embed)
                    
            except Exception as e:
                print(f"Error in AutoMod processing: {e}")
                traceback.print_exc()

    async def timeout_user(self, user, guild, filtered_word):
        """Timeout a user who has reached 3 warnings"""
        try:
            # Set timeout duration (1 hour)
            timeout_duration = datetime.timedelta(hours=1)
            
            # Apply timeout
            await user.timeout_for(timeout_duration, reason=f"AutoMod: 3+ warnings for inappropriate language")
            
            # Notify moderators with the specific role
            mod_role = guild.get_role(self.mod_role_id)
            if not mod_role:
                print(f"Could not find mod role with ID {self.mod_role_id}")
                return
                
            # Find a suitable channel to notify mods
            mod_log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
            if not mod_log_channel:
                # Try to find a general channel if mod-logs doesn't exist
                mod_log_channel = discord.utils.get(guild.text_channels, name="general")
                if not mod_log_channel:
                    # Just use the first text channel we can find
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).send_messages:
                            mod_log_channel = channel
                            break
            
            if mod_log_channel:
                timeout_end = datetime.datetime.now() + timeout_duration
                
                embed = discord.Embed(
                    title="⚠️ User Automatically Timed Out",
                    description=f"{mod_role.mention}\n{user.mention} has been timed out for 1 hour.",
                    color=discord.Color.dark_red(),
                    timestamp=datetime.datetime.now()
                )
                embed.add_field(name="Reason", value=f"Reached 3+ warnings for using inappropriate language", inline=False)
                embed.add_field(name="Last Filtered Word", value=filtered_word, inline=True)
                embed.add_field(name="Timeout Ends", value=f"<t:{int(timeout_end.timestamp())}:R>", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                
                await mod_log_channel.send(embed=embed)
                
                # Try to DM the user about the timeout
                try:
                    user_embed = discord.Embed(
                        title="You have been timed out",
                        description=f"You have been automatically timed out in {guild.name} for 1 hour.",
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now()
                    )
                    user_embed.add_field(name="Reason", value="You have received 3 or more warnings for using inappropriate language", inline=False)
                    user_embed.add_field(name="Timeout Ends", value=f"<t:{int(timeout_end.timestamp())}:R>", inline=False)
                    
                    await user.send(embed=user_embed)
                except discord.Forbidden:
                    pass  # User has DMs disabled
                    
        except discord.Forbidden:
            print(f"Bot doesn't have permission to timeout {user}")
        except Exception as e:
            print(f"Error timing out user: {e}")
            traceback.print_exc()

    async def add_warning(self, user_id, guild_id, filtered_word):
        """Add or increment a warning for a user"""
        try:
            # Check if user already has warnings
            check_query = """
            SELECT warn_count FROM user_warnings 
            WHERE user_id = %s AND guild_id = %s
            """
            result = await self.execute_query(check_query, (str(user_id), str(guild_id)))
            
            current_time = datetime.datetime.now()
            
            if result and len(result) > 0:
                # User exists, increment warning count
                update_query = """
                UPDATE user_warnings 
                SET warn_count = warn_count + 1, 
                    last_warned_at = %s,
                    last_warned_for = %s
                WHERE user_id = %s AND guild_id = %s
                """
                await self.execute_query(update_query, (
                    current_time, 
                    filtered_word, 
                    str(user_id), 
                    str(guild_id)
                ))
            else:
                # New user, insert record
                insert_query = """
                INSERT INTO user_warnings 
                (user_id, guild_id, warn_count, last_warned_at, last_warned_for) 
                VALUES (%s, %s, 1, %s, %s)
                """
                await self.execute_query(insert_query, (
                    str(user_id), 
                    str(guild_id), 
                    current_time, 
                    filtered_word
                ))
                
        except Exception as e:
            print(f"Error adding warning: {e}")

    async def get_warning_count(self, user_id, guild_id):
        """Get the current warning count for a user"""
        try:
            query = """
            SELECT warn_count FROM user_warnings 
            WHERE user_id = %s AND guild_id = %s
            """
            result = await self.execute_query(query, (str(user_id), str(guild_id)))
            
            if result and len(result) > 0:
                return result[0]['warn_count']
            return 1  # Default to 1 if no record found (this is their first warning)
            
        except Exception as e:
            print(f"Error getting warning count: {e}")
            return 1  # Default to 1 on error
            
    @commands.command(name="warnings")
    @commands.has_permissions(kick_members=True)
    async def check_warnings(self, ctx, member: discord.Member = None):
        """Check warnings for a user"""
        if not member:
            member = ctx.author
            
        query = """
        SELECT * FROM user_warnings 
        WHERE user_id = %s AND guild_id = %s
        """
        result = await self.execute_query(query, (str(member.id), str(ctx.guild.id)))
        
        if result and len(result) > 0:
            warning_data = result[0]
            embed = discord.Embed(
                title=f"Warnings for {member.display_name}",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="Warning Count", value=str(warning_data['warn_count']), inline=False)
            embed.add_field(name="Last Warning", value=warning_data['last_warned_at'].strftime("%Y-%m-%d %H:%M:%S"), inline=True)
            embed.add_field(name="Last Reason", value=warning_data['last_warned_for'] or "Not recorded", inline=True)
            embed.set_footer(text=f"User ID: {member.id}")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{member.display_name} has no warnings.")
            
    @commands.command(name="clearwarnings")
    @commands.has_permissions(administrator=True)
    async def clear_warnings(self, ctx, member: discord.Member):
        """Clear all warnings for a user"""
        query = """
        DELETE FROM user_warnings 
        WHERE user_id = %s AND guild_id = %s
        """
        result = await self.execute_query(query, (str(member.id), str(ctx.guild.id)))
        
        if result:
            await ctx.send(f"✅ Cleared all warnings for {member.display_name}.")
        else:
            await ctx.send(f"{member.display_name} had no warnings to clear.")
            
    @commands.command(name="addfilter")
    @commands.has_permissions(administrator=True)
    async def add_filter(self, ctx, word: str):
        """Add a word to the filter list"""
        # Convert to lowercase for case-insensitive matching
        word = word.lower()
        
        if word in self.filtered_words:
            await ctx.send(f"'{word}' is already in the filter list.")
            return
            
        # Add to the list and compile a new regex pattern
        self.filtered_words.append(word)
        self.word_patterns.append(re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE))
        
        await ctx.send(f"✅ Added '{word}' to the filter list.")
        
        # Delete the command message to hide the filtered word
        try:
            await ctx.message.delete()
        except:
            pass
            
    @commands.command(name="removefilter")
    @commands.has_permissions(administrator=True)
    async def remove_filter(self, ctx, word: str):
        """Remove a word from the filter list"""
        # Convert to lowercase for case-insensitive matching
        word = word.lower()
        
        if word not in self.filtered_words:
            await ctx.send(f"'{word}' is not in the filter list.")
            return
            
        # Find the index and remove from both lists
        index = self.filtered_words.index(word)
        self.filtered_words.pop(index)
        self.word_patterns.pop(index)
        
        await ctx.send(f"✅ Removed '{word}' from the filter list.")
        
        # Delete the command message to hide the filtered word
        try:
            await ctx.message.delete()
        except:
            pass
            
    @commands.command(name="listfilters")
    @commands.has_permissions(administrator=True)
    async def list_filters(self, ctx):
        """List all filtered words (admin only)"""
        if not self.filtered_words:
            await ctx.send("No words are currently filtered.")
            return
            
        # Send the list via DM to avoid showing filtered words in public channels
        try:
            words_list = "\n".join([f"• {word}" for word in self.filtered_words])
            embed = discord.Embed(
                title="Filtered Words List",
                description=words_list,
                color=discord.Color.blue()
            )
            await ctx.author.send(embed=embed)
            await ctx.send("📬 I've sent you the list of filtered words via DM.")
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM. Please enable DMs from server members.")

async def setup(bot):
    # Make sure to import traceback at the top of the file

    await bot.add_cog(AutoModCommands(bot))
