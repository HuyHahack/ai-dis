import os
import threading
import time
import random
import discord
from discord.ext import commands, tasks
from flask import Flask, jsonify
import re
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import asyncio
import subprocess
import tempfile
import logging

# ===== LOGGING CHI TIẾT =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# === DÙNG THƯ VIỆN MỚI: google.genai ===
try:
    from google import genai
    logger.info("✅ google.genai imported successfully")
except ImportError as e:
    logger.error(f"❌ Lỗi import google.genai: {e}")
    genai = None

# === CHECK FFMPEG ===
def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except:
        return False
HAS_FFMPEG = check_ffmpeg()
logger.info(f"📌 FFmpeg: {'✅ Có' if HAS_FFMPEG else '❌ Không'}")

# === CHECK VOICE ===
try:
    import nacl
    HAS_VOICE = True
except ImportError:
    HAS_VOICE = False
    logger.warning("⚠️ PyNaCl chưa cài, voice sẽ không hoạt động")
logger.info(f"📌 PyNaCl: {'✅ Có' if HAS_VOICE else '❌ Không'}")

# === TTS ===
try:
    from gtts import gTTS
    HAS_TTS = True
except ImportError:
    HAS_TTS = False
    logger.warning("⚠️ gTTS chưa cài, TTS sẽ không hoạt động")
logger.info(f"📌 TTS: {'✅ Có' if HAS_TTS else '❌ Không'}")

# ===== FLASK APP =====
app = Flask(__name__)

# ===== BIẾN MÔI TRƯỜNG =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("Thiếu DISCORD_TOKEN")

TEXT_CHANNEL_ID = os.getenv("TEXT_CHANNEL_ID")

api_keys_str = os.getenv("GEMINI_API_KEYS", "")
if api_keys_str:
    API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]
else:
    default_key = os.getenv("GEMINI_API_KEY", "")
    if default_key:
        API_KEYS = [default_key]
    else:
        API_KEYS = ["YOUR_API_KEY_HERE"]

logger.info(f"📌 Đã tải {len(API_KEYS)} API key(s) cho Gemini.")
logger.info(f"📌 TEXT_CHANNEL_ID: {TEXT_CHANNEL_ID or 'AUTO'}")

# ===== DISCORD BOT =====
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="", intents=intents)

# ===== TEMP FOLDER =====
TEMP_DIR = tempfile.mkdtemp(prefix="mineflayer_tts_")
logger.info(f"📁 Temp folder: {TEMP_DIR}")

def cleanup_temp_files():
    try:
        for f in os.listdir(TEMP_DIR):
            if f.endswith('.mp3'):
                os.remove(os.path.join(TEMP_DIR, f))
    except:
        pass

# ===== MEMORY =====
class ServerMemory:
    def __init__(self):
        self.messages = defaultdict(list)
        self.user_stats = defaultdict(lambda: {
            "messages": 0,
            "voice_time": 0,
            "last_seen": None,
            "first_seen": None,
            "nicknames": [],
            "avatars": [],
            "activity": [],
            "mood": "neutral"
        })
        self.server_stats = defaultdict(lambda: {
            "total_messages": 0,
            "active_users": set(),
            "word_counter": Counter()
        })
        self.conversation_context = defaultdict(list)
        self.voice_states = {}
        self.voice_channel_ids = {}
        self.last_voice_activity = {}
        self.text_channels = []
        self.last_voice_announce = {}
        self.servers = set()
        
    def add_message(self, user_id, channel_id, content, guild_id):
        self.messages[channel_id].append({
            "user": user_id,
            "content": content,
            "time": datetime.now()
        })
        if len(self.messages[channel_id]) > 1000:
            self.messages[channel_id] = self.messages[channel_id][-500:]
        self.user_stats[user_id]["messages"] += 1
        self.user_stats[user_id]["last_seen"] = datetime.now()
        if not self.user_stats[user_id]["first_seen"]:
            self.user_stats[user_id]["first_seen"] = datetime.now()
        self.server_stats[guild_id]["total_messages"] += 1
        self.server_stats[guild_id]["active_users"].add(user_id)
        
        words = content.lower().split()
        for w in words:
            if len(w) > 2:
                self.server_stats[guild_id]["word_counter"][w] += 1
        
        self.conversation_context[user_id].append(content)
        if len(self.conversation_context[user_id]) > 20:
            self.conversation_context[user_id] = self.conversation_context[user_id][-10:]
    
    def get_recent_messages(self, channel_id, limit=10):
        return self.messages[channel_id][-limit:] if channel_id in self.messages else []
    
    def get_user_context(self, user_id):
        return self.conversation_context.get(user_id, [])
    
    def get_top_words(self, guild_id, limit=10):
        return self.server_stats[guild_id]["word_counter"].most_common(limit)

memory = ServerMemory()

# ===== SLANG DICTIONARY =====
SLANG_DICT = {
    "lọ": "sục", "sục": "lọ", "cmm": "con mẹ mày",
    "vcl": "vãi cả lồn", "vcc": "vãi cả cặc",
    "đmm": "đèo mẹ mày", "dm": "đèo mẹ", "dmm": "đèo mẹ mày",
    "địt": "đèo", "địt mẹ": "đèo mẹ",
    "cc": "cặc", "cl": "cặc", "clgt": "cặc gì thế",
    "cmn": "con mẹ nó", "cmnr": "con mẹ nó rồi",
    "đm": "đèo mẹ", "vl": "vãi lồn", "vc": "vãi cả",
    "hết pin": "chết", "cày": "chơi game", "phọt": "nói",
    "gato": "ghen tị", "xàm": "vô nghĩa", "xàm loz": "vô nghĩa",
    "cringe": "xấu hổ", "flex": "khoe khoang", "fomo": "sợ bỏ lỡ",
    "ghệ": "người yêu", "gấu": "người yêu", "ny": "người yêu",
    "crush": "người thích thầm", "thả thính": "tán tỉnh",
    "tấu hài": "làm trò hài", "trend": "xu hướng",
    "vibe": "cảm xúc", "btw": "nhân tiện", "lol": "cười lớn",
    "omg": "trời ơi", "wtf": "cái quái gì", "idk": "tôi không biết",
    "ikr": "tôi biết mà", "tbh": "thật lòng", "stfu": "câm mồm",
    "lmao": "cười chết", "rofl": "lăn lộn cười",
    "gg": "good game", "ez": "dễ dàng", "noob": "gà", "pro": "chuyên nghiệp",
    "afk": "rời bàn phím", "lag": "giật", "buff": "tăng", "nerf": "giảm",
    "op": "quá mạnh", "chill": "thư giãn", "cool": "tuyệt", "fake": "giả",
    "fen": "ní", "bạn": "ní", "mấy fen": "mấy ní",
    "đéo": "đéo má"
}

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

# ===== HÀM GỌI GEMINI =====
def generate_with_gemini(prompt: str) -> str:
    logger.info("🔄 Gọi Gemini API...")
    
    if genai is None:
        logger.error("❌ genai chưa được import")
        return "ACTION:REPLY|Đèo mẹ, API chưa cài! Cài google-genai đi!"
    
    for idx, key in enumerate(API_KEYS):
        try:
            logger.info(f"🔄 Thử key {idx+1}/{len(API_KEYS)}...")
            client = genai.Client(api_key=key)
            
            try:
                logger.info("🔄 Gọi gemma-4-31b-it...")
                response = client.models.generate_content(
                    model="gemma-4-31b-it",
                    contents=prompt,
                    config={
                        "max_output_tokens": 100,
                        "temperature": 0.8
                    }
                )
                if response and hasattr(response, 'text') and response.text:
                    logger.info(f"✅ gemma-4-31b-it thành công với key {idx+1}")
                    logger.info(f"📝 Response: {response.text[:100]}...")
                    return response.text
                else:
                    logger.warning(f"⚠️ gemma-4-31b-it trả về rỗng với key {idx+1}")
                    
            except Exception as e:
                logger.warning(f"⚠️ gemma-4-31b-it lỗi: {e}, thử gemini-2.0-flash...")
                try:
                    logger.info("🔄 Gọi gemini-2.0-flash...")
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=prompt,
                        config={
                            "max_output_tokens": 100,
                            "temperature": 0.8
                        }
                    )
                    if response and hasattr(response, 'text') and response.text:
                        logger.info(f"✅ gemini-2.0-flash thành công với key {idx+1}")
                        logger.info(f"📝 Response: {response.text[:100]}...")
                        return response.text
                    else:
                        logger.warning(f"⚠️ gemini-2.0-flash trả về rỗng với key {idx+1}")
                except Exception as e2:
                    logger.warning(f"⚠️ gemini-2.0-flash lỗi: {e2}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Key {idx+1} lỗi: {e}")
            continue
    
    logger.error("❌ Tất cả key đều thất bại!")
    return "ACTION:REPLY|Đèo mẹ, hết key rồi ní! 😭"

# ===== BUILD PROMPT =====
def build_prompt(user_message: str, user_name: str, user_id: int, channel_id: int, guild_id: int) -> str:
    recent_msgs = memory.get_recent_messages(channel_id, 5)
    context_str = "\n".join([f"{memory.user_stats[msg['user']].get('name', 'ai đó')}: {msg['content']}" for msg in recent_msgs[-3:]])
    
    voice_info = ""
    if bot.voice_clients:
        for vc in bot.voice_clients:
            if vc.channel:
                voice_info = f"Hiện đang ở voice {vc.channel.name} với {len(vc.channel.members)} thằng."
    else:
        voice_info = "Hiện không ở voice nào."
    
    user_voice = ""
    if user_id in memory.voice_states:
        user_voice = f"Người dùng đang ở voice {memory.voice_states[user_id]}"
    else:
        user_voice = "Người dùng không ở voice nào."
    
    return f"""Bạn là bot Discord tên "Mineflayer" - một AI sống động, có cá tính riêng.

THÔNG TIN HIỆN TẠI:
- Voice status: {voice_info}
- User status: {user_voice}

NGỮ CẢNH GẦN ĐÂY:
{context_str}

TIN NHẮN CỦA {user_name}:
"{user_message}"

=== NHIỆM VỤ ===
Bạn phải TỰ HIỂU ý định của người dùng:

1. Nếu user muốn JOIN VOICE -> ACTION:JOIN_VOICE|nội dung
2. Nếu user muốn LEAVE VOICE -> ACTION:LEAVE_VOICE|nội dung
3. Nếu user muốn NÓI TTS -> ACTION:SPEAK|nội dung
4. Nếu user chat bình thường -> ACTION:REPLY|nội dung
5. Nếu không rõ ràng -> ACTION:NONE

QUAN TRỌNG:
- Trả lời CỰC NGẮN, max 3 câu
- Style DÂN CHƠI: dùng "ní", "đèo mẹ"
- Tiếng Việt

TRẢ LỜI:"""

# ===== PARSE RESPONSE =====
def parse_response(response: str) -> dict:
    if not response:
        logger.warning("⚠️ Response rỗng, dùng fallback")
        return {"action": "REPLY", "content": "Đèo mẹ, tao bị đơ!"}
    
    action_match = re.search(r"ACTION\s*:\s*(\w+)", response, re.I)
    
    if action_match:
        action = action_match.group(1).upper()
        content = re.sub(r"ACTION\s*:\s*\w+\s*\|?\s*", "", response, flags=re.I).strip()
        if not content:
            content = re.sub(r"ACTION\s*:\s*\w+", "", response, flags=re.I).strip()
        if not content:
            content = response
        logger.info(f"🎯 Action: {action}, Content: {content[:50]}...")
        return {"action": action, "content": content}
    
    # Không tìm thấy ACTION -> dọn dẹp
    logger.warning("⚠️ Không tìm thấy ACTION, dọn dẹp response...")
    lines = response.split('\n')
    clean_lines = []
    skip_keywords = ['action', 'reply', 'speak', 'join_voice', 'leave_voice', 
                    'trả lời', 'nội dung', 'ví dụ', 'nếu user', 'phản hồi']
    
    for line in lines:
        line_lower = line.lower().strip()
        if not line_lower:
            continue
        if any(kw in line_lower for kw in skip_keywords):
            continue
        if len(line) > 100 and ('bạn là' in line_lower or 'mineflayer' in line_lower):
            continue
        clean_lines.append(line.strip())
    
    content = '\n'.join(clean_lines).strip()
    if not content:
        content = response[:200].strip()
    
    return {"action": "REPLY", "content": content}

# ===== TTS =====
async def speak_in_voice(text: str) -> bool:
    if not HAS_TTS:
        logger.error("❌ gTTS chưa cài")
        return False
    
    if not HAS_FFMPEG:
        logger.error("❌ FFmpeg chưa cài")
        return False
    
    if not bot.voice_clients:
        logger.warning("⚠️ Bot chưa ở voice")
        return False
    
    vc = bot.voice_clients[0]
    if not vc.is_connected():
        logger.warning("⚠️ Voice không kết nối")
        return False
    
    filename = os.path.join(TEMP_DIR, f"tts_{random.randint(1000, 9999)}.mp3")
    logger.info(f"🔊 Tạo TTS: {text}")
    
    try:
        await asyncio.to_thread(lambda: gTTS(text=text, lang="vi", slow=False).save(filename))
        logger.info(f"✅ TTS đã lưu: {filename}")
    except Exception as e:
        logger.error(f"❌ TTS tạo file lỗi: {e}")
        return False
    
    def cleanup(error):
        try:
            if os.path.exists(filename):
                os.remove(filename)
                logger.info(f"🗑️ Đã xóa file: {filename}")
        except:
            pass
    
    try:
        if vc.is_playing():
            vc.stop()
            logger.info("⏹️ Đã dừng phát trước đó")
        vc.play(discord.FFmpegPCMAudio(filename), after=cleanup)
        logger.info("▶️ Đang phát TTS...")
        return True
    except Exception as e:
        logger.error(f"❌ TTS phát lỗi: {e}")
        cleanup(None)
        return False

# ===== VOICE HANDLER =====
async def handle_voice_action(message, action):
    if action == "join_voice":
        if not message.author.voice:
            logger.warning(f"⚠️ {message.author.display_name} không ở voice")
            await message.reply("Đèo mẹ, mày ở trong voice mới gọi tao được chứ!")
            return False
        
        channel = message.author.voice.channel
        logger.info(f"🎙️ {message.author.display_name} yêu cầu join voice {channel.name}")
        
        if bot.voice_clients:
            vc = bot.voice_clients[0]
            if vc.channel and vc.channel.id == channel.id:
                logger.info(f"ℹ️ Đã ở voice {channel.name} rồi")
                await message.reply(f"Đèo mẹ, tao đang ở {channel.name} rồi mà ní 😭")
                return True
            logger.info(f"🔄 Di chuyển từ {vc.channel.name} sang {channel.name}")
            await vc.move_to(channel)
        else:
            try:
                if HAS_VOICE:
                    logger.info(f"🔗 Đang kết nối voice {channel.name}...")
                    await channel.connect()
                    logger.info(f"✅ Đã kết nối voice {channel.name}")
                else:
                    logger.error("❌ PyNaCl chưa cài")
                    await message.reply("Đèo mẹ, tao chưa cài voice!")
                    return False
            except Exception as e:
                logger.error(f"❌ Lỗi voice: {e}")
                await message.reply(f"Lỗi voice: {e}")
                return False
        
        try:
            voice_chat = bot.get_channel(channel.id)
            if isinstance(voice_chat, discord.VoiceChannel):
                await voice_chat.send(f"Chào mấy ní! {message.author.display_name} rủ t vô đây.")
                logger.info(f"📢 Đã gửi tin chào trong voice")
        except:
            pass
        return True
    
    elif action == "leave_voice":
        if bot.voice_clients:
            logger.info("🔇 Đang rời voice...")
            await bot.voice_clients[0].disconnect()
            logger.info("✅ Đã rời voice")
        else:
            logger.warning("⚠️ Bot không ở voice nào")
            await message.reply("Đéo má, tao ở voice nào cả!")
        return True

# ===== VOICE STATE UPDATE =====
@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user:
        return
    
    if after.channel:
        memory.voice_states[member.id] = after.channel.name
        memory.voice_channel_ids[member.id] = after.channel.id
        memory.last_voice_activity[after.channel.id] = datetime.now()
        logger.info(f"🎤 {member.display_name} vào voice {after.channel.name}")
    else:
        if member.id in memory.voice_states:
            del memory.voice_states[member.id]
        if member.id in memory.voice_channel_ids:
            del memory.voice_channel_ids[member.id]
        logger.info(f"🚪 {member.display_name} rời voice")
    
    text_channel = None
    if TEXT_CHANNEL_ID:
        text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
    if not text_channel:
        for channel in member.guild.text_channels:
            if channel.permissions_for(member.guild.me).send_messages:
                text_channel = channel
                break
    
    if text_channel and before.channel is None and after.channel is not None:
        now = time.time()
        last = memory.last_voice_announce.get(text_channel.id, 0)
        if now - last > 30:
            memory.last_voice_announce[text_channel.id] = now
            await text_channel.send(f"🎤 {member.display_name} vừa vào voice {after.channel.name}!")
    
    if text_channel and before.channel is not None and after.channel is None:
        now = time.time()
        last = memory.last_voice_announce.get(text_channel.id, 0)
        if now - last > 30:
            memory.last_voice_announce[text_channel.id] = now
            await text_channel.send(f"👋 {member.display_name} rời voice {before.channel.name}!")

# ===== ON MESSAGE =====
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    logger.info(f"💬 Tin nhắn từ {message.author.display_name}: {message.content[:50]}...")
    
    memory.add_message(
        message.author.id, 
        message.channel.id, 
        message.content,
        message.guild.id if message.guild else 0
    )
    memory.user_stats[message.author.id]["name"] = message.author.display_name
    
    if isinstance(message.channel, discord.TextChannel):
        if message.channel not in memory.text_channels:
            memory.text_channels.append(message.channel)
            logger.info(f"📢 Đã thêm channel: {message.channel.name}")
        
        if bot.user in message.mentions:
            logger.info(f"🏷️ Bot bị tag bởi {message.author.display_name}")
            
            raw_content = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
            if not raw_content:
                await message.reply(random.choice(["Hả?", "Gì?", "Ờ?", "Sao?"]))
                await bot.process_commands(message)
                return
            
            translated = translate_slang(raw_content)
            logger.info(f"📝 Nội dung sau khi dịch slang: {translated}")
            
            if translated.lower().startswith("nói:") or translated.lower().startswith("đọc:"):
                content = translated[4:].strip()
                if content:
                    logger.info(f"🔊 Yêu cầu TTS: {content}")
                    if not bot.voice_clients:
                        if message.author.voice:
                            await message.reply("Đèo mẹ, tao tự join voice cho mày đây!")
                            await handle_voice_action(message, "join_voice")
                            await asyncio.sleep(0.5)
                        else:
                            await message.reply("Đèo mẹ, mày ở voice mới nói được!")
                            await bot.process_commands(message)
                            return
                    
                    success = await speak_in_voice(content)
                    if success:
                        await message.reply(f"🎙️ {content}")
                    else:
                        await message.reply("Đèo mẹ, TTS lỗi!")
                await bot.process_commands(message)
                return
            
            prompt = build_prompt(
                translated, 
                message.author.display_name, 
                message.author.id, 
                message.channel.id,
                message.guild.id if message.guild else 0
            )
            logger.info(f"📋 Prompt gửi Gemini: {prompt[:100]}...")
            
            response = generate_with_gemini(prompt)
            
            if response is None:
                logger.error("❌ Response từ Gemini là None!")
                await message.reply("Đèo mẹ, API đang bị lỗi! Thử lại sau!")
                await bot.process_commands(message)
                return
            
            logger.info(f"📥 Response từ Gemini: {response[:100]}...")
            
            parsed = parse_response(response)
            action = parsed["action"]
            content = parsed["content"]
            logger.info(f"🎯 Action: {action}")
            
            try:
                await message.add_reaction(random.choice(["👍", "❤️", "😂", "🤔", "👀", "🔥"]))
                logger.info("✅ Đã thả react")
            except Exception as e:
                logger.warning(f"⚠️ Lỗi thả react: {e}")
            
            if action == "JOIN_VOICE":
                logger.info("🎯 Xử lý JOIN_VOICE")
                await handle_voice_action(message, "join_voice")
                if content and content != response:
                    await message.reply(content)
            
            elif action == "LEAVE_VOICE":
                logger.info("🎯 Xử lý LEAVE_VOICE")
                await handle_voice_action(message, "leave_voice")
                if content and content != response:
                    await message.reply(content)
            
            elif action == "SPEAK":
                logger.info(f"🎯 Xử lý SPEAK: {content}")
                if not bot.voice_clients:
                    if message.author.voice:
                        await message.reply("Đèo mẹ, tao tự join voice cho mày đây!")
                        await handle_voice_action(message, "join_voice")
                        await asyncio.sleep(0.5)
                    else:
                        await message.reply("Đèo mẹ, mày ở voice mới nói được!")
                        await bot.process_commands(message)
                        return
                
                success = await speak_in_voice(content)
                if success:
                    await message.reply(f"🎙️ {content}")
                else:
                    await message.reply("Đèo mẹ, TTS lỗi!")
            
            elif action == "REPLY":
                logger.info(f"🎯 Xử lý REPLY: {content[:50]}...")
                if content and content != response:
                    if len(content) > 2000:
                        for part in [content[i:i+1997] for i in range(0, len(content), 1997)]:
                            await message.reply(part)
                    else:
                        await message.reply(content)
                else:
                    clean = re.sub(r'ACTION:REPLY\s*\|?\s*', '', response, flags=re.I).strip()
                    await message.reply(clean[:1900] if clean else "Đèo mẹ, tao đang bị đơ!")
    
    await bot.process_commands(message)

# ===== RANDOM CHAT TASK =====
@tasks.loop(minutes=5)
async def random_chat_task():
    if not bot.guilds:
        return
    
    guild = random.choice(bot.guilds)
    logger.info(f"🔄 Random chat task: chọn server {guild.name}")
    
    channel = None
    if TEXT_CHANNEL_ID:
        channel = bot.get_channel(int(TEXT_CHANNEL_ID))
    if not channel:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                channel = ch
                break
    
    if not channel:
        logger.warning("⚠️ Không tìm thấy channel để random chat")
        return
    
    try:
        last_msgs = memory.get_recent_messages(channel.id, 3)
        if last_msgs:
            last_time = last_msgs[-1]["time"]
            if (datetime.now() - last_time) > timedelta(minutes=30):
                if random.random() < 0.02:
                    messages = [
                        "Đèo mẹ, có ai ở đây không? 👀",
                        "Sao im lặng thế mấy ní?",
                        "Trời ơi vắng tanh!",
                        "Có ai chơi game không?",
                        "Alo alo có ai không? 👻",
                        "Mấy ní đi đâu hết rồi?",
                        "Sao vắng thế này?"
                    ]
                    msg = random.choice(messages)
                    await channel.send(msg)
                    logger.info(f"📢 Random chat: {msg}")
    except Exception as e:
        logger.error(f"❌ Lỗi random chat: {e}")

# ===== ON READY =====
@bot.event
async def on_ready():
    logger.info(f"✅ Mineflayer đã đăng nhập với tên {bot.user}")
    logger.info(f"📌 Bot đang ở {len(bot.guilds)} server(s):")
    for guild in bot.guilds:
        logger.info(f"   - {guild.name} (ID: {guild.id})")
    
    await bot.change_presence(activity=discord.Game(name=f"quan sát {len(bot.guilds)} server | Tag tao để nói chuyện"))
    
    for guild in bot.guilds:
        memory.servers.add(guild.id)
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                memory.text_channels.append(channel)
    
    logger.info(f"📖 Đã tải {len(memory.text_channels)} text channel(s)")
    
    async def cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            cleanup_temp_files()
            logger.info("🗑️ Đã dọn dẹp temp files")
    
    asyncio.create_task(cleanup_loop())
    
    if not random_chat_task.is_running():
        random_chat_task.start()
        logger.info("✅ Đã start random chat task")

# ===== RUN BOT =====
def run_discord_bot():
    logger.info("🚀 Đang chạy bot...")
    bot.run(DISCORD_TOKEN)

# ===== FLASK HEALTH CHECK =====
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "alive",
        "bot": str(bot.user) if bot.user else "Unknown",
        "servers": len(bot.guilds),
        "server_list": [g.name for g in bot.guilds],
        "text_channels": len(memory.text_channels),
        "tts": HAS_TTS,
        "ffmpeg": HAS_FFMPEG,
        "voice": HAS_VOICE
    })

# ===== MAIN =====
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", 10000))
    logger.info(f"🌐 Flask đang chạy trên port {port}")
    app.run(host="0.0.0.0", port=port)
