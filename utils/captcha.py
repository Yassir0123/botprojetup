import discord
from discord.ui import Modal, TextInput, Button, View
from datetime import datetime, timedelta
from utils.database import execute_query_with_retry
import random
import string
from PIL import Image, ImageDraw, ImageFont
import io
import os

class CaptchaModal(Modal):
    """Modal form for captcha code input"""
    def __init__(self, correct_code: str, user_id: int, callback):
        super().__init__(title="Human Verification")
        self.correct_code = correct_code
        self.user_id = user_id
        self.callback = callback
        
        self.code_input = TextInput(
            label="Enter the code from the image",
            placeholder="e.g., ABC123",
            required=True,
            max_length=10
        )
        self.add_item(self.code_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        user_input = self.code_input.value.upper().strip()
        
        if user_input == self.correct_code:
            # Correct code - update database
            user_authorid = str(self.user_id)
            update_query = "UPDATE user SET verified_until = %s, code = NULL, usage_count = 0 WHERE user_authorid = %s"
            new_verified_until = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            await execute_query_with_retry(update_query, (new_verified_until, user_authorid))
            
            # Notify user
            embed = discord.Embed(
                title="✅ Verification Successful",
                description="You're verified for the next hour!",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.callback(True)
        else:
            # Wrong code
            embed = discord.Embed(
                title="❌ Wrong Code",
                description="The code you entered is incorrect. Please try again.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.callback(False)

class CaptchaView(View):
    """View with button to open captcha modal"""
    def __init__(self, captcha_code: str, user_id: int, callback):
        super().__init__(timeout=300)  # 5 minute timeout
        self.captcha_code = captcha_code
        self.user_id = user_id
        self.callback = callback
    
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.primary)
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        """Show captcha modal when button is clicked"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This verification is not for you.", ephemeral=True)
            return
        
        modal = CaptchaModal(self.captcha_code, self.user_id, self.callback)
        await interaction.response.send_modal(modal)

class CaptchaHandler:
    def __init__(self):
        self.pending_verifications = {}  # Track users waiting for captcha
        self.USAGE_THRESHOLD =   5# Threshold before requiring captcha
    
    def _generate_captcha_image(self, code: str) -> discord.File:
        """Generate captcha image with code"""
        width, height = 300, 150
        image = Image.new('RGB', (width, height), color='white')
        draw = ImageDraw.Draw(image)
        
        # Add noise/lines for security
        for _ in range(5):
            x1, y1 = random.randint(0, width), random.randint(0, height)
            x2, y2 = random.randint(0, width), random.randint(0, height)
            draw.line([(x1, y1), (x2, y2)], fill='gray', width=2)
        
        # Add dots for noise
        for _ in range(50):
            x, y = random.randint(0, width), random.randint(0, height)
            draw.point((x, y), fill='gray')
        
        # Draw text
        try:
            # Try to use a system font, fallback to default
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
        except:
            font = ImageFont.load_default()
        
        # Center text
        bbox = draw.textbbox((0, 0), code, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) // 2
        y = (height - text_height) // 2
        
        draw.text((x, y), code, fill='black', font=font)
        
        # Save to bytes
        image_bytes = io.BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes.seek(0)
        
        return discord.File(image_bytes, filename='captcha.png')
    
    async def check_and_show_captcha(self, ctx, user_id: int, force: bool = False) -> bool:
        """
        Check if user needs captcha verification.
        Shows captcha in chat with image and button.
        Returns True if verification passed or not needed, False if verification failed.
        """
        user_authorid = str(user_id)
        
        # Get user's verification status
        query = "SELECT verified_until, code, code_created_at, usage_count FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (user_authorid,))
        
        if not result:
            return True  # User not found, allow command
        
        user_data = result[0]
        verified_until = user_data['verified_until']
        current_code = user_data['code']
        code_created_at = user_data['code_created_at']
        usage_count = user_data['usage_count'] or 0
        current_time = datetime.now()
        
        # Check if user is still in verification window
        if verified_until and not force:
            if isinstance(verified_until, str):
                verified_until = datetime.strptime(verified_until, '%Y-%m-%d %H:%M:%S')
            
            if current_time < verified_until:
                return True  # Still verified, no captcha needed
            else:
                reset_query = "UPDATE user SET usage_count = 0 WHERE user_authorid = %s"
                await execute_query_with_retry(reset_query, (user_authorid,))
                usage_count = 0
        
        if usage_count < self.USAGE_THRESHOLD:
            # Increment usage count and allow command
            new_count = usage_count + 1
            update_query = "UPDATE user SET usage_count = %s WHERE user_authorid = %s"
            await execute_query_with_retry(update_query, (new_count, user_authorid))
            return True
        
        # User needs captcha verification (threshold reached)
        return await self._show_captcha_in_chat(ctx, user_authorid, user_id, current_code, code_created_at)
    
    async def _show_captcha_in_chat(self, ctx, user_authorid: str, user_id: int, current_code: str = None, code_created_at: str = None) -> bool:
        """
        Show captcha in chat with image and button.
        Returns True if captcha passed, False if failed or timed out.
        """
        current_time = datetime.now()
        
        if current_code and code_created_at:
            if isinstance(code_created_at, str):
                code_created_at = datetime.strptime(code_created_at, '%Y-%m-%d %H:%M:%S')
            
            # If code is less than 5 minutes old, reuse it
            if (current_time - code_created_at).total_seconds() < 300:
                captcha_code = current_code
            else:
                # Code expired, generate new one
                captcha_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                store_query = "UPDATE user SET code = %s, code_created_at = %s WHERE user_authorid = %s"
                await execute_query_with_retry(store_query, (captcha_code, current_time.strftime('%Y-%m-%d %H:%M:%S'), user_authorid))
        else:
            # Generate new code for first time
            captcha_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            store_query = "UPDATE user SET code = %s, code_created_at = %s WHERE user_authorid = %s"
            await execute_query_with_retry(store_query, (captcha_code, current_time.strftime('%Y-%m-%d %H:%M:%S'), user_authorid))
        
        # Generate captcha image
        captcha_file = self._generate_captcha_image(captcha_code)
        
        # Create embed with image
        embed = discord.Embed(
            title="🔐 Human Verification Required",
            description="Please enter the code from the image below to verify you're human.",
            color=discord.Color.red()
        )
        embed.set_image(url="attachment://captcha.png")
        embed.set_footer(text="You have 5 minutes to complete verification.")
        
        # Create callback to track verification result
        verification_result = {'passed': False}
        
        async def verification_callback(passed: bool):
            verification_result['passed'] = passed
        
        # Create view with button
        view = CaptchaView(captcha_code, user_id, verification_callback)
        
        # Send message with image and button
        try:
            message = await ctx.send(embed=embed, file=captcha_file, view=view)
        except Exception as e:
            print(f"Error sending captcha: {e}")
            return False
        
        # Wait for verification to complete (with timeout)
        try:
            await view.wait()
            return verification_result['passed']
        except:
            # Timeout or error
            embed = discord.Embed(
                title="⏱️ Verification Timeout",
                description="You took too long to respond. Please try the command again.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return False

# Create global instance
captcha_handler = CaptchaHandler()
