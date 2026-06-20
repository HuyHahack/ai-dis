import os
import threading
import discord
from discord.ext import commands
import google.generativeai as genai
from flask import Flask, jsonify

# ===== Flask app =====
app = Flask(__name__)

# ===== Đọc biến môi trường =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Thiếu biến môi trường DISCORD_TOKEN hoặc GEMINI_API_KEY")

# ===== Cấu hình Gemini =====
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")  # hoặc "gemini-1.5-pro"

# ===== Cấu hình Discord bot =====
intents = discord.Intents.default()
intents.message_content = True
intents.mentions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ----- Sự kiện khi bot sẵn sàng -----
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập với tên {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="đợi bạn ping"))

# ----- Xử lý tin nhắn khi bị tag -----
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        # Lấy nội dung tin nhắn, bỏ phần mention
        clean_content = message.clean_content
        question = clean_content.replace(f"@{bot.user.display_name}", "").strip()

        if not question:
            await message.channel.send("Bạn muốn hỏi gì? Hãy gửi câu hỏi cùng với tag tôi nhé!")
            return

        async with message.channel.typing():
            try:
                response = model.generate_content(question)
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