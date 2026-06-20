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

# ===== Biến môi trường =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN:
    raise ValueError("Thiếu DISCORD_TOKEN")

# ===== Gemini client =====
client = genai.Client(api_key=GEMINI_API_KEY)

# ===== Discord bot =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== Cooldown =====
user_cooldowns = {}
COOLDOWN_SECONDS = 30

# ===== React emojis =====
REACT_EMOJIS = ["👍", "❤️", "😂", "🤔", "👀", "🔥", "✨", "💯", "😎", "🤖", "🧠", "💪"]

def is_on_cooldown(user_id: int) -> bool:
    now = time.time()
    if user_id in user_cooldowns and now - user_cooldowns[user_id] < COOLDOWN_SECONDS:
        return True
    user_cooldowns[user_id] = now
    return False

# ===== Hàm chia tin nhắn dài thành nhiều phần =====
def split_message(text: str, limit: int = 2000) -> list:
    """Chia text thành các đoạn ≤ limit, ưu tiên cắt theo dòng hoặc dấu cách."""
    if len(text) <= limit:
        return [text]
    parts = []
    lines = text.split('\n')
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 <= limit:
            current += line + '\n'
        else:
            if current:
                parts.append(current.rstrip('\n'))
            # Nếu một dòng dài hơn limit, phải cắt cứng
            if len(line) > limit:
                # Cắt thành từng đoạn nhỏ
                for i in range(0, len(line), limit):
                    parts.append(line[i:i+limit])
                current = ""
            else:
                current = line + '\n'
    if current:
        parts.append(current.rstrip('\n'))
    return parts

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
            await message.reply(f"⏳ Mày đã hỏi gần đây, đợi {COOLDOWN_SECONDS} giây đi đã.")
            return

        # Lấy câu hỏi
        raw_question = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
        if not raw_question:
            await message.reply("Thế mày tag tao để hỏi cái loz gì? Nói đi!")
            return

        # Thả react ngẫu nhiên
        try:
            await message.add_reaction(random.choice(REACT_EMOJIS))
        except:
            pass

        # Thêm instruction bố láo, tiếng Việt, ngắn gọn
        enhanced_question = (
            f"Trả lời bằng tiếng Việt, phong cách bố láo, chửi thề, nhưng vẫn cung cấp thông tin hữu ích. "
            f"Trả lời cực ngắn gọn, tối đa 2-3 câu, không dài dòng. "
            f"Câu hỏi: {raw_question}"
        )

        async with message.channel.typing():
            reply = None
            last_error = None

            models_to_try = [
                "gemini-3.5-flash"
            ]

            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=enhanced_question,
                    )
                    reply = response.text
                    break
                except errors.ClientError as e:
                    last_error = e
                    if e.code in (404, 429):
                        continue
                    else:
                        break
                except Exception as e:
                    last_error = e
                    break

            if reply:
                # Chia nhỏ nếu dài
                parts = split_message(reply, 2000)
                for part in parts:
                    await message.reply(part)
            else:
                error_msg = str(last_error) if last_error else "Tao chịu, không hiểu lỗi gì."
                if len(error_msg) > 1900:
                    error_msg = error_msg[:1900] + "..."
                await message.reply(f"❌ Lỗi: {error_msg}")

    await bot.process_commands(message)

# ===== Chạy bot trong thread =====
def run_discord_bot():
    bot.run(DISCORD_TOKEN)

# ===== Health check =====
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
