import os
import threading
import time
import random
import discord
from discord.ext import commands
from flask import Flask, jsonify
from google import genai
from google.genai import types

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
intents.message_content = True   # Cần để đọc nội dung tin nhắn

bot = commands.Bot(command_prefix="!", intents=intents)

# ===== Dictionary lưu thời gian cooldown của từng user =====
user_cooldowns = {}
COOLDOWN_SECONDS = 30

# ===== Danh sách emoji để react ngẫu nhiên =====
REACT_EMOJIS = ["👍", "❤️", "😂", "🤔", "👀", "🔥", "✨", "💯", "😎", "🤖", "🧠", "💪"]

# ===== Hàm kiểm tra cooldown =====
def is_on_cooldown(user_id: int) -> bool:
    current_time = time.time()
    if user_id in user_cooldowns:
        last_used = user_cooldowns[user_id]
        if current_time - last_used < COOLDOWN_SECONDS:
            return True
    user_cooldowns[user_id] = current_time
    return False

# ===== Sự kiện khi bot sẵn sàng =====
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập với tên {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="đợi bạn ping"))

# ===== Xử lý tin nhắn khi bị tag =====
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Kiểm tra nếu bot bị tag
    if bot.user in message.mentions:
        # Kiểm tra cooldown
        if is_on_cooldown(message.author.id):
            await message.reply(f"⏳ Bạn đang bị giới hạn tốc độ! Vui lòng đợi {COOLDOWN_SECONDS} giây trước khi hỏi tiếp.")
            return

        # Lấy nội dung tin nhắn, bỏ phần mention
        clean_content = message.clean_content
        question = clean_content.replace(f"@{bot.user.display_name}", "").strip()

        if not question:
            await message.reply("Bạn muốn hỏi gì? Hãy gửi câu hỏi cùng với tag tôi nhé!")
            return

        # Thả react ngẫu nhiên vào tin nhắn của user
        try:
            random_emoji = random.choice(REACT_EMOJIS)
            await message.add_reaction(random_emoji)
        except Exception as e:
            print(f"Lỗi khi thả react: {e}")

        # Gửi phản hồi "đang suy nghĩ"
        async with message.channel.typing():
            try:
                # Gọi Gemini API với SDK mới
                response = client.models.generate_content(
                    model="gemini-3.1-pro-preview",  # Hoặc "gemini-3.1-flash-lite"
                    contents=question,
                )
                reply = response.text
                if len(reply) > 2000:
                    reply = reply[:1997] + "..."
                await message.reply(reply)
            except Exception as e:
                await message.reply(f"❌ Có lỗi xảy ra: {e}")

    await bot.process_commands(message)

# ===== Hàm chạy bot trong thread riêng =====
def run_discord_bot():
    bot.run(DISCORD_TOKEN)

# ===== Endpoint health check cho Render =====
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "alive",
        "bot": "Discord Gemini Bot",
        "gemini": "connected"
    })

# ===== Khởi chạy =====
if __name__ == "__main__":
    # Chạy bot Discord trong thread riêng (non-blocking)
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()

    # Chạy Flask web server
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
