import os
import threading
import time
import random
import discord
from discord.ext import commands
from flask import Flask, jsonify
from google import genai
from google.genai import errors

# ===== Flask app =====
app = Flask(__name__)

# ===== Đọc biến môi trường =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Thiếu biến môi trường DISCORD_TOKEN hoặc GEMINI_API_KEY")

# ===== Cấu hình Gemini với SDK mới =====
client = genai.Client(api_key=GEMINI_API_KEY)

# ===== Cấu hình Discord bot =====
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ===== Cooldown =====
user_cooldowns = {}
COOLDOWN_SECONDS = 30

# ===== Emoji react ngẫu nhiên =====
REACT_EMOJIS = ["👍", "❤️", "😂", "🤔", "👀", "🔥", "✨", "💯", "😎", "🤖", "🧠", "💪"]

def is_on_cooldown(user_id: int) -> bool:
    current_time = time.time()
    if user_id in user_cooldowns:
        if current_time - user_cooldowns[user_id] < COOLDOWN_SECONDS:
            return True
    user_cooldowns[user_id] = current_time
    return False

@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập với tên {bot.user}")
    await bot.change_presence(activity=discord.Game(name="đợi bạn ping"))

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        # Cooldown
        if is_on_cooldown(message.author.id):
            await message.reply(f"⏳ Vui lòng đợi {COOLDOWN_SECONDS} giây trước khi hỏi tiếp.")
            return

        # Lấy câu hỏi
        question = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
        if not question:
            await message.reply("Hãy gửi câu hỏi cùng với tag tôi nhé!")
            return

        # Thả react ngẫu nhiên
        try:
            await message.add_reaction(random.choice(REACT_EMOJIS))
        except:
            pass

        # Xử lý Gemini
        async with message.channel.typing():
            try:
                # Dùng model miễn phí, ít bị quota hơn
                response = client.models.generate_content(
                    model="gemini-2.0-flash-lite",  # hoặc "gemini-1.5-flash"
                    contents=question,
                )
                reply = response.text
                if len(reply) > 2000:
                    reply = reply[:1997] + "..."
                await message.reply(reply)

            except errors.ClientError as e:
                # Xử lý riêng lỗi quota
                if e.status_code == 429:
                    await message.reply("⏳ **Quota API đã hết, vui lòng thử lại sau vài phút.**")
                else:
                    # Rút gọn thông báo lỗi để không vượt 2000 ký tự
                    error_msg = str(e)
                    if len(error_msg) > 1900:
                        error_msg = error_msg[:1900] + "..."
                    await message.reply(f"❌ Lỗi API: {error_msg}")

            except Exception as e:
                # Lỗi khác (mạng, timeout, ...)
                error_msg = str(e)
                if len(error_msg) > 1900:
                    error_msg = error_msg[:1900] + "..."
                await message.reply(f"❌ Lỗi không xác định: {error_msg}")

    await bot.process_commands(message)

# ===== Chạy bot trong thread =====
def run_discord_bot():
    bot.run(DISCORD_TOKEN)

# ===== Endpoint health =====
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({"status": "alive", "bot": "Discord Gemini Bot"})

# ===== Main =====
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
