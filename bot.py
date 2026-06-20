import os
import threading
import time
import random
import discord
from discord.ext import commands
from flask import Flask, jsonify
from google import genai
from google.genai import errors
import aiohttp
import io

# ===== Flask app =====
app = Flask(__name__)

# ===== Biến môi trường =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("Thiếu DISCORD_TOKEN")

# Đọc danh sách API keys
api_keys_str = os.getenv("GEMINI_API_KEYS", "")
if api_keys_str:
    API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]
else:
    default_key = os.getenv("GEMINI_API_KEY")
    API_KEYS = [default_key]

if not API_KEYS:
    raise ValueError("Không có API key nào được cấu hình")

print(f"📌 Đã tải {len(API_KEYS)} API key(s) cho Gemini.")

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

# ===== Hàm chia tin nhắn dài =====
def split_message(text: str, limit: int = 2000) -> list:
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
            if len(line) > limit:
                for i in range(0, len(line), limit):
                    parts.append(line[i:i+limit])
                current = ""
            else:
                current = line + '\n'
    if current:
        parts.append(current.rstrip('\n'))
    return parts

# ===== Hàm đọc nội dung file txt từ attachment =====
async def read_txt_attachment(attachment: discord.Attachment) -> str:
    """Tải file txt và trả về nội dung dạng string."""
    if not attachment.filename.lower().endswith('.txt'):
        return None
    if attachment.size > 1_000_000:  # giới hạn 1MB
        return None  # quá lớn, bỏ qua
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    content = await resp.text(encoding='utf-8', errors='ignore')
                    return content
    except Exception:
        return None
    return None

# ===== Hàm gọi Gemini với fallback key =====
def generate_with_fallback(question: str) -> str:
    last_error = None
    for idx, key in enumerate(API_KEYS):
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=question,
            )
            return response.text
        except errors.ClientError as e:
            last_error = e
            if e.code == 429:
                print(f"⚠️ Key {idx+1} bị quota (429), chuyển sang key tiếp theo...")
                continue
            else:
                raise
        except Exception as e:
            last_error = e
            raise
    raise Exception(f"Tất cả {len(API_KEYS)} API key đều hết quota. Lỗi cuối: {last_error}")

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

        # Lấy câu hỏi từ nội dung tin nhắn
        raw_question = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()

        # Đọc file txt đính kèm
        file_content = None
        if message.attachments:
            for attachment in message.attachments:
                if attachment.filename.lower().endswith('.txt') and attachment.size <= 1_000_000:
                    file_content = await read_txt_attachment(attachment)
                    if file_content:
                        break  # chỉ lấy file đầu tiên

        # Ghép nội dung file vào câu hỏi nếu có
        if file_content:
            # Nếu có cả câu hỏi và file
            if raw_question:
                full_question = f"Câu hỏi: {raw_question}\n\nNội dung file txt:\n{file_content}"
            else:
                full_question = f"Nội dung file txt:\n{file_content}"
        else:
            full_question = raw_question

        if not full_question:
            await message.reply("Thế mày tag tao để hỏi cái loz gì? Nói đi, hoặc gửi file txt!")
            return

        # Thả react ngẫu nhiên
        try:
            await message.add_reaction(random.choice(REACT_EMOJIS))
        except:
            pass

        # Prompt bố láo, ngắn gọn, tiếng Việt
        enhanced_question = (
            f"Trả lời bằng tiếng Việt, phong cách bố láo, chửi thề, nhưng vẫn cung cấp thông tin hữu ích. "
            f"Trả lời cực ngắn gọn, tối đa 2-3 câu, không dài dòng. "
            f"Nội dung: {full_question}"
        )

        async with message.channel.typing():
            try:
                reply = generate_with_fallback(enhanced_question)
                parts = split_message(reply, 2000)
                for part in parts:
                    await message.reply(part)
            except Exception as e:
                error_msg = str(e)
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
