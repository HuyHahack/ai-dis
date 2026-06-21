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
import base64
import re

# ===== Flask app =====
app = Flask(__name__)

# ===== Biến môi trường =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("Thiếu DISCORD_TOKEN")

GUILD_ID = os.getenv("GUILD_ID")
if not GUILD_ID:
    print("⚠️ Không có GUILD_ID, sẽ đăng ký global (mất 1-2h)")

# Đọc danh sách API keys
api_keys_str = os.getenv("GEMINI_API_KEYS", "")
if api_keys_str:
    API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]
else:
    default_key = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6KXHMxGFhINizxK_9lCTnCyQx-18nL6KbF7mIUs2Jtv-g")
    API_KEYS = [default_key]

if not API_KEYS:
    raise ValueError("Không có API key nào được cấu hình")

print(f"📌 Đã tải {len(API_KEYS)} API key(s) cho Gemini.")

# ===== Discord bot =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== Dictionary slang / tiếng lóng =====
SLANG_DICT = {
    "lọ": "sục",
    "sục": "lọ",
    "cmm": "con mẹ mày",
    "vcl": "vãi cả lồn",
    "vcc": "vãi cả cặc",
    "đmm": "địt mẹ mày",
    "dm": "địt mẹ",
    "dmm": "địt mẹ mày",
    "cc": "cặc",
    "cl": "cặc",
    "clgt": "cặc gì thế",
    "cmn": "con mẹ nó",
    "cmnr": "con mẹ nó rồi",
    "đm": "địt mẹ",
    "vl": "vãi lồn",
    "vc": "vãi cả",
    "hết pin": "chết",
    "cày": "chơi game",
    "phọt": "nói",
    "gato": "ghen tị",
    "xàm": "vô nghĩa",
    "xàm loz": "vô nghĩa",
    "cringe": "xấu hổ, ngại",
    "flex": "khoe khoang",
    "fomo": "sợ bỏ lỡ",
    "ghệ": "người yêu",
    "gấu": "người yêu",
    "ny": "người yêu",
    "crush": "người thích thầm",
    "thanh xuân": "tuổi trẻ",
    "thả thính": "tán tỉnh",
    "tấu hài": "làm trò hài",
    "trend": "xu hướng",
    "vibe": "cảm xúc, không khí",
    "btw": "nhân tiện",
    "lol": "cười lớn",
    "omg": "trời ơi",
    "wtf": "cái quái gì",
    "idk": "tôi không biết",
    "ikr": "tôi biết mà",
    "tbh": "thật lòng mà nói",
    "feed": "nuôi địch",
    "carry": "gánh team",
    "gank": "tập kích",
    "farming": "cày vàng",
    "afk": "rời bàn phím",
    "lag": "giật, chậm",
    "buff": "tăng sức mạnh",
    "nerf": "giảm sức mạnh",
    "op": "quá mạnh",
    "noob": "người mới, gà",
    "pro": "chuyên nghiệp",
    "gg": "good game",
    "ez": "dễ dàng",
    "chill": "thư giãn",
    "cool": "tuyệt",
    "fake": "giả",
    "emo": "buồn, cảm xúc",
    "stfu": "câm mồm",
    "lmao": "cười chết mất",
    "rofl": "lăn lộn cười",
}

# ===== Hàm dịch slang =====
def translate_slang(text: str) -> str:
    words = re.findall(r'\b\w+\b|[^\w\s]', text, re.UNICODE)
    result = []
    for word in words:
        if re.match(r'^\w+$', word, re.UNICODE):
            word_lower = word.lower()
            if word_lower in SLANG_DICT:
                result.append(SLANG_DICT[word_lower])
            else:
                result.append(word)
        else:
            result.append(word)
    return ''.join(result)

# ===== Lưu phong cách user =====
user_styles = {}
DEFAULT_STYLE = "angry"

# ===== Định nghĩa style prompts (tsundere chỉ dùng cậu/tớ) =====
STYLE_PROMPTS = {
    "angry": (
        "Trả lời bằng tiếng Việt (có thể dùng tiếng lóng, viết tắt như: lọ=sục, đmm, vcl, cc, cmnr), "
        "phong cách bố láo, chửi thề, nhưng vẫn cung cấp thông tin hữu ích. "
        "Trả lời cực ngắn gọn, tối đa 2-3 câu, không dài dòng."
    ),
    "tsundere": (
        "Trả lời bằng tiếng Việt (có thể dùng tiếng lóng, viết tắt), "
        "phong cách tsundere (ngại ngùng, khó chịu nhưng thực ra quan tâm). "
        "Chỉ dùng xưng hô 'cậu' và 'tớ' (KHÔNG dùng 'anh/chị' hoặc 'em'). "
        "Dùng các từ như: 'H-Hở?!', 'C-cậu nói dì dạ?!', 'T-tớ dỗi đó!', 'Hmmp~', 'Đ-đồ ngốc!', "
        "'Tớ không quan tâm đâu nhé!', 'Nhưng mà... cảm ơn...', 'Hứ!', 'C-cậu ngốc quá!'. "
        "Thỉnh thoảng lắp bắp, đỏ mặt (trong lời nói). "
        "Trả lời cực ngắn gọn, tối đa 2-3 câu, vẫn cung cấp thông tin hữu ích."
    )
}

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

# ===== Hàm đọc nội dung file txt =====
async def read_txt_attachment(attachment: discord.Attachment) -> str:
    if not attachment.filename.lower().endswith('.txt'):
        return None
    if attachment.size > 1_000_000:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    content = await resp.text(encoding='utf-8', errors='ignore')
                    return content
    except Exception:
        return None
    return None

# ===== Hàm đọc ảnh =====
async def read_image_attachment(attachment: discord.Attachment) -> dict:
    image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
    if not any(attachment.filename.lower().endswith(ext) for ext in image_extensions):
        return None
    if attachment.size > 5_000_000:
        return None
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    mime_type = attachment.content_type or 'image/jpeg'
                    base64_data = base64.b64encode(image_data).decode('utf-8')
                    return {
                        "mime_type": mime_type,
                        "data": base64_data
                    }
    except Exception as e:
        print(f"Lỗi đọc ảnh: {e}")
        return None
    return None

# ===== Hàm lấy tin nhắn reply =====
async def get_replied_message_content(message: discord.Message) -> str:
    if message.reference and message.reference.message_id:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            content = replied_msg.clean_content
            
            if replied_msg.attachments:
                image_names = [att.filename for att in replied_msg.attachments 
                              if att.filename.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp'))]
                if image_names:
                    content += f"\n[Ảnh đính kèm: {', '.join(image_names)}]"
                
                txt_names = [att.filename for att in replied_msg.attachments 
                            if att.filename.lower().endswith('.txt')]
                if txt_names:
                    content += f"\n[File txt: {', '.join(txt_names)}]"
            
            if replied_msg.author == bot.user:
                content = f"Tin nhắn trước của bot: {content}\nHãy trả lời dựa trên nội dung này"
            
            return content
        except Exception as e:
            print(f"Lỗi lấy tin nhắn reply: {e}")
            return None
    return None

# ===== Hàm gọi Gemini =====
def generate_with_fallback(question: str, image_data: dict = None, replied_content: str = None) -> str:
    last_error = None
    
    contents_parts = []
    
    if question:
        contents_parts.append({"text": question})
    
    if replied_content:
        contents_parts.append({"text": f"\n[Tin nhắn được reply]: {replied_content}"})
    
    if image_data:
        contents_parts.append({
            "inline_data": {
                "mime_type": image_data["mime_type"],
                "data": image_data["data"]
            }
        })
    
    if not question and replied_content and not image_data:
        contents_parts = [{"text": f"Trả lời tin nhắn này: {replied_content}"}]
    
    for idx, key in enumerate(API_KEYS):
        try:
            client = genai.Client(api_key=key)
            
            try:
                response = client.models.generate_content(
                    model="gemma-4-31b-it",
                    contents={"parts": contents_parts},
                    config={
                        "temperature": 0.9,
                        "max_output_tokens": 1000,
                    }
                )
                return response.text
            except errors.ClientError as e:
                if e.code == 404:
                    print(f"⚠️ Model gemma-4-31b-it không tồn tại, thử gemini-2.0-flash...")
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents={"parts": contents_parts},
                    )
                    return response.text
                elif e.code == 429:
                    print(f"⚠️ Key {idx+1} bị quota (429), chuyển key...")
                    continue
                else:
                    raise
        except errors.ClientError as e:
            last_error = e
            if e.code == 429:
                continue
            else:
                raise
        except Exception as e:
            last_error = e
            raise
    
    raise Exception(f"Tất cả API key đều hết quota. Lỗi cuối: {last_error}")

# ===== Slash command: /phongcach =====
@bot.tree.command(
    name="phongcach",
    description="Đổi phong cách trả lời của bot: tsundere (dễ thương) hoặc angry (bố láo)",
    guild=discord.Object(id=int(GUILD_ID)) if GUILD_ID else None
)
async def phongcach(interaction: discord.Interaction, style: str):
    """Đổi phong cách: tsundere hoặc angry"""
    style = style.lower()
    if style not in ["tsundere", "angry"]:
        await interaction.response.send_message(
            "❌ Phong cách không hợp lệ! Chọn `tsundere` hoặc `angry`.",
            ephemeral=True
        )
        return
    
    user_styles[interaction.user.id] = style
    style_names = {
        "tsundere": "Tsundere (ngại ngùng, dễ thương, xưng cậu/tớ)",
        "angry": "Angry (bố láo, chửi thề)"
    }
    await interaction.response.send_message(
        f"✅ Đã đổi phong cách sang **{style_names[style]}**!",
        ephemeral=True
    )

# ===== Sync slash commands =====
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập với tên {bot.user}")
    await bot.change_presence(activity=discord.Game(name="đợi bạn ping"))
    
    print(f"📖 Đã tải {len(SLANG_DICT)} từ slang/viết tắt")
    
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ Đã sync {len(synced)} slash command(s) cho guild {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"✅ Đã sync {len(synced)} slash command(s) global")
    except Exception as e:
        print(f"❌ Lỗi sync slash commands: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        # Cooldown
        if is_on_cooldown(message.author.id):
            await message.reply(f"⏳ Mày đã hỏi gần đây, đợi {COOLDOWN_SECONDS} giây đi đã.")
            return

        # Lấy style của user
        user_style = user_styles.get(message.author.id, DEFAULT_STYLE)
        style_prompt = STYLE_PROMPTS.get(user_style, STYLE_PROMPTS[DEFAULT_STYLE])

        # Lấy câu hỏi và dịch slang
        raw_question = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
        translated_question = translate_slang(raw_question)

        # Lấy nội dung tin nhắn được reply
        replied_content = await get_replied_message_content(message)
        if replied_content:
            replied_content = translate_slang(replied_content)

        # Đọc file txt và ảnh
        file_content = None
        image_data = None
        
        if message.attachments:
            for attachment in message.attachments:
                if attachment.filename.lower().endswith('.txt') and attachment.size <= 1_000_000:
                    file_content = await read_txt_attachment(attachment)
                    if file_content:
                        file_content = translate_slang(file_content)
                        break
                
                if not image_data:
                    image_data = await read_image_attachment(attachment)

        # Ghép nội dung
        if file_content:
            if translated_question:
                full_question = f"Câu hỏi: {translated_question}\n\nNội dung file txt:\n{file_content}"
            else:
                full_question = f"Nội dung file txt:\n{file_content}"
        else:
            full_question = translated_question

        if not full_question and not replied_content and not image_data:
            await message.reply("Thế mày tag tao để hỏi cái loz gì? Nói đi, hoặc gửi file/ảnh, hoặc reply tin nhắn khác!")
            return

        # Tạo prompt với style
        if full_question:
            enhanced_question = f"{style_prompt} Nội dung: {full_question}"
        else:
            enhanced_question = None

        # Thả react
        try:
            await message.add_reaction(random.choice(REACT_EMOJIS))
        except:
            pass

        # Gọi Gemini
        async with message.channel.typing():
            try:
                reply = generate_with_fallback(
                    question=enhanced_question,
                    image_data=image_data,
                    replied_content=replied_content
                )
                parts = split_message(reply, 2000)
                for part in parts:
                    await message.reply(part)
            except Exception as e:
                error_msg = str(e)
                if len(error_msg) > 1900:
                    error_msg = error_msg[:1900] + "..."
                await message.reply(f"❌ Lỗi: {error_msg}")

    await bot.process_commands(message)

# ===== Chạy bot =====
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
