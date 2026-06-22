import os
import threading
import time
import random
import discord
from discord.ext import commands, tasks
from flask import Flask, jsonify
import aiohttp
import base64
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import asyncio
import subprocess
import tempfile
import shutil

# === CHECK FFMPEG ===
def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except:
        return False
HAS_FFMPEG = check_ffmpeg()

# === CHECK VOICE ===
try:
    import nacl
    HAS_VOICE = True
except ImportError:
    HAS_VOICE = False
    print("⚠️ PyNaCl chưa cài, voice sẽ không hoạt động")

# === TTS ===
try:
    from gtts import gTTS
    HAS_TTS = True
except ImportError:
    HAS_TTS = False
    print("⚠️ gTTS chưa cài, TTS sẽ không hoạt động")

# === GENAI ===
try:
    import google.generativeai as genai
    print("✅ google.generativeai imported")
except ImportError as e:
    print(f"❌ Lỗi import genai: {e}")
    genai = None

# ===== FLASK APP =====
app = Flask(__name__)

# ===== BIẾN MÔI TRƯỜNG =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("Thiếu DISCORD_TOKEN")

GUILD_ID = os.getenv("GUILD_ID")
TEXT_CHANNEL_ID = os.getenv("TEXT_CHANNEL_ID")
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")

# Đọc danh sách API keys
api_keys_str = os.getenv("GEMINI_API_KEYS", "")
if api_keys_str:
    API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]
else:
    default_key = os.getenv("GEMINI_API_KEY", "")
    if default_key:
        API_KEYS = [default_key]
    else:
        API_KEYS = ["YOUR_API_KEY_HERE"]

print(f"📌 Đã tải {len(API_KEYS)} API key(s) cho Gemini.")
print(f"📌 TEXT_CHANNEL_ID: {TEXT_CHANNEL_ID or 'AUTO'}")
print(f"📌 VOICE_CHANNEL_ID: {VOICE_CHANNEL_ID or 'AUTO'}")
print(f"📌 FFmpeg: {'✅ Có' if HAS_FFMPEG else '❌ Không'}")
print(f"📌 PyNaCl: {'✅ Có' if HAS_VOICE else '❌ Không'}")
print(f"📌 TTS: {'✅ Có' if HAS_TTS else '❌ Không'}")

# ===== DISCORD BOT =====
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="", intents=intents)

# ===== TEMP FOLDER =====
TEMP_DIR = tempfile.mkdtemp(prefix="mineflayer_tts_")
print(f"📁 Temp folder: {TEMP_DIR}")

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
        self.server_stats = {
            "total_messages": 0,
            "active_users": set(),
            "peak_online": 0,
            "word_counter": Counter(),
            "voice_activity": defaultdict(int),
            "events": [],
            "quotes": []
        }
        self.conversation_context = defaultdict(list)
        self.voice_states = {}
        self.voice_channel_ids = {}
        self.last_voice_activity = {}
        self.text_channels = []
        self.main_text_channel = None
        self.main_voice_channel = None
        self.last_voice_announce = {}
        
    def add_message(self, user_id, channel_id, content):
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
        self.server_stats["total_messages"] += 1
        self.server_stats["active_users"].add(user_id)
        
        words = content.lower().split()
        for w in words:
            if len(w) > 2:
                self.server_stats["word_counter"][w] += 1
        
        self.conversation_context[user_id].append(content)
        if len(self.conversation_context[user_id]) > 20:
            self.conversation_context[user_id] = self.conversation_context[user_id][-10:]
    
    def get_recent_messages(self, channel_id, limit=10):
        return self.messages[channel_id][-limit:] if channel_id in self.messages else []
    
    def get_user_context(self, user_id):
        return self.conversation_context.get(user_id, [])
    
    def get_top_words(self, limit=10):
        return self.server_stats["word_counter"].most_common(limit)

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
def generate_with_gemini(prompt: str, image_data: dict = None) -> str:
    if genai is None:
        return "ACTION:REPLY|Đèo mẹ, API chưa cài! Cài google-generativeai đi!"
    
    contents_parts = [{"text": prompt}]
    if image_data:
        contents_parts.append({
            "inline_data": {
                "mime_type": image_data["mime_type"],
                "data": image_data["data"]
            }
        })
    
    for idx, key in enumerate(API_KEYS):
        try:
            genai.configure(api_key=key)
            
            # Thử gemma-4-31b-it trước
            try:
                model = genai.GenerativeModel("gemma-4-31b-it")
                response = model.generate_content(
                    prompt,
                    generation_config={
                        "max_output_tokens": 100,
                        "temperature": 0.8
                    }
                )
                print(f"✅ gemma-4-31b-it thành công với key {idx+1}")
                return response.text
            except Exception as e:
                print(f"⚠️ gemma-4-31b-it lỗi: {e}, thử gemini-2.0-flash...")
                
                # Fallback gemini-2.0-flash
                try:
                    model = genai.GenerativeModel("gemini-2.0-flash")
                    response = model.generate_content(
                        prompt,
                        generation_config={
                            "max_output_tokens": 100,
                            "temperature": 0.8
                        }
                    )
                    print(f"✅ gemini-2.0-flash thành công với key {idx+1}")
                    return response.text
                except Exception as e2:
                    print(f"⚠️ gemini-2.0-flash lỗi: {e2}")
                    continue
                    
        except Exception as e:
            print(f"❌ Key {idx+1} lỗi: {e}")
            continue
    
    return "ACTION:REPLY|Đèo mẹ, hết key rồi ní! 😭"

# ===== BUILD PROMPT =====
def build_prompt(user_message: str, user_name: str, user_id: int, channel_id: int) -> str:
    recent_msgs = memory.get_recent_messages(channel_id, 5)
    context_str = "\n".join([f"{memory.user_stats[msg['user']].get('name', 'ai đó')}: {msg['content']}" for msg in recent_msgs[-3:]])
    user_context = memory.get_user_context(user_id)
    user_context_str = "\n".join(user_context[-3:])
    
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
- Server: {len(bot.guilds[0].members) if bot.guilds else '?'} thành viên
- Voice status: {voice_info}
- User status: {user_voice}

NGỮ CẢNH GẦN ĐÂY:
{context_str}

TIN NHẮN CỦA {user_name}:
"{user_message}"

=== NHIỆM VỤ ===
Bạn phải TỰ HIỂU ý định của người dùng:

1. Nếu user muốn JOIN VOICE (ví dụ: "join voice", "vô voice", "vô phòng", "vô đi"):
   -> ACTION:JOIN_VOICE|nội dung trả lời (ngắn, tự nhiên)

2. Nếu user muốn LEAVE VOICE (ví dụ: "leave", "ra", "out", "thoát"):
   -> ACTION:LEAVE_VOICE|nội dung trả lời

3. Nếu user muốn NÓI (TTS) trong voice (ví dụ: "nói tao nghe đi", "kể tao nghe"):
   -> CHỈ dùng ACTION:SPEAK khi bot ĐANG Ở VOICE
   -> Nếu bot KHÔNG ở voice, dùng ACTION:REPLY và bảo user kêu join voice trước
   -> ACTION:SPEAK|nội dung muốn đọc (ngắn, 1-2 câu)

4. Nếu user hỏi về JOIN VOICE (ví dụ "join voice là gì"):
   -> KHÔNG dùng ACTION:JOIN_VOICE, dùng ACTION:REPLY

5. Nếu user chat bình thường:
   -> ACTION:REPLY|nội dung trả lời

6. Nếu tin nhắn không rõ ràng:
   -> ACTION:NONE

QUAN TRỌNG:
- Trả lời CỰC NGẮN, max 3 câu
- Style DÂN CHƠI: dùng "ní", "đèo mẹ" ở đầu câu khi chửi
- Tiếng Việt

TRẢ LỜI:"""

# ===== PARSE RESPONSE =====
def parse_response(response: str) -> dict:
    action_match = re.search(r"ACTION\s*:\s*(\w+)", response, re.I)
    
    if action_match:
        action = action_match.group(1).upper()
        content = re.sub(r"ACTION\s*:\s*\w+\s*\|?\s*", "", response, flags=re.I).strip()
        if not content:
            content = re.sub(r"ACTION\s*:\s*\w+", "", response, flags=re.I).strip()
        if not content:
            content = response
        return {"action": action, "content": content}
    
    # Không tìm thấy ACTION
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
        print("❌ gTTS chưa cài")
        return False
    
    if not HAS_FFMPEG:
        print("❌ FFmpeg chưa cài")
        return False
    
    if not bot.voice_clients:
        print("❌ Bot chưa ở voice")
        return False
    
    vc = bot.voice_clients[0]
    if not vc.is_connected():
        return False
    
    filename = os.path.join(TEMP_DIR, f"tts_{random.randint(1000, 9999)}.mp3")
    
    try:
        await asyncio.to_thread(lambda: gTTS(text=text, lang="vi", slow=False).save(filename))
    except Exception as e:
        print(f"TTS tạo file lỗi: {e}")
        return False
    
    def cleanup(error):
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except:
            pass
    
    try:
        if vc.is_playing():
            vc.stop()
        
        vc.play(discord.FFmpegPCMAudio(filename), after=cleanup)
        return True
    except Exception as e:
        print(f"TTS phát lỗi: {e}")
        cleanup(None)
        return False

# ===== VOICE HANDLER =====
async def handle_voice_action(message, action):
    if action == "join_voice":
        if not message.author.voice:
            await message.reply("Đèo mẹ, mày ở trong voice mới gọi tao được chứ!")
            return False
        
        channel = message.author.voice.channel
        
        if bot.voice_clients:
            vc = bot.voice_clients[0]
            if vc.channel and vc.channel.id == channel.id:
                await message.reply(f"Đèo mẹ, tao đang ở {channel.name} rồi mà ní 😭")
                return True
            await vc.move_to(channel)
        else:
            try:
                if HAS_VOICE:
                    await channel.connect()
                else:
                    await message.reply("Đèo mẹ, tao chưa cài voice!")
                    return False
            except Exception as e:
                await message.reply(f"Lỗi voice: {e}")
                return False
        
        try:
            voice_chat = bot.get_channel(channel.id)
            if isinstance(voice_chat, discord.VoiceChannel):
                await voice_chat.send(f"Chào mấy ní! {message.author.display_name} rủ t vô đây.")
        except:
            pass
        return True
    
    elif action == "leave_voice":
        if bot.voice_clients:
            await bot.voice_clients[0].disconnect()
        else:
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
    else:
        if member.id in memory.voice_states:
            del memory.voice_states[member.id]
        if member.id in memory.voice_channel_ids:
            del memory.voice_channel_ids[member.id]
    
    if before.channel is None and after.channel is not None:
        text_channel = None
        if TEXT_CHANNEL_ID:
            text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
        if not text_channel:
            for channel in member.guild.text_channels:
                if channel.permissions_for(member.guild.me).send_messages:
                    text_channel = channel
                    break
        
        if text_channel:
            now = time.time()
            last = memory.last_voice_announce.get(text_channel.id, 0)
            if now - last > 30:
                memory.last_voice_announce[text_channel.id] = now
                join_messages = [
                    f"🎤 {member.display_name} vừa vào voice {after.channel.name}!",
                    f"📢 {member.display_name} vào voice rồi mấy ní!",
                    f"👋 {member.display_name} đã gia nhập {after.channel.name}!",
                ]
                await text_channel.send(random.choice(join_messages))
    
    if before.channel is not None and after.channel is None:
        text_channel = None
        if TEXT_CHANNEL_ID:
            text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
        if not text_channel:
            for channel in member.guild.text_channels:
                if channel.permissions_for(member.guild.me).send_messages:
                    text_channel = channel
                    break
        
        if text_channel:
            now = time.time()
            last = memory.last_voice_announce.get(text_channel.id, 0)
            if now - last > 30:
                memory.last_voice_announce[text_channel.id] = now
                leave_messages = [
                    f"👋 {member.display_name} rời voice {before.channel.name}!",
                    f"🚪 {member.display_name} out voice rồi!",
                    f"💨 {member.display_name} bay khỏi {before.channel.name}!",
                ]
                await text_channel.send(random.choice(leave_messages))

# ===== ON MESSAGE =====
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    memory.add_message(message.author.id, message.channel.id, message.content)
    memory.user_stats[message.author.id]["name"] = message.author.display_name
    
    if isinstance(message.channel, discord.TextChannel):
        if message.channel not in memory.text_channels:
            memory.text_channels.append(message.channel)
        
        if bot.user in message.mentions:
            raw_content = message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
            if not raw_content:
                await message.reply(random.choice(["Hả?", "Gì?", "Ờ?", "Sao?"]))
                await bot.process_commands(message)
                return
            
            translated = translate_slang(raw_content)
            
            # Nếu "nói:" -> TTS luôn
            if translated.lower().startswith("nói:") or translated.lower().startswith("đọc:"):
                content = translated[4:].strip()
                if content:
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
            
            # Build prompt
            prompt = build_prompt(translated, message.author.display_name, message.author.id, message.channel.id)
            
            # Gọi Gemini
            response = generate_with_gemini(prompt)
            print(f"🤖 Response: {response[:100]}...")
            
            # Parse
            parsed = parse_response(response)
            action = parsed["action"]
            content = parsed["content"]
            
            # Thả react
            try:
                await message.add_reaction(random.choice(["👍", "❤️", "😂", "🤔", "👀", "🔥"]))
            except:
                pass
            
            # Xử lý action
            if action == "JOIN_VOICE":
                await handle_voice_action(message, "join_voice")
                if content and content != response:
                    await message.reply(content)
            
            elif action == "LEAVE_VOICE":
                await handle_voice_action(message, "leave_voice")
                if content and content != response:
                    await message.reply(content)
            
            elif action == "SPEAK":
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
                if content and content != response:
                    if len(content) > 2000:
                        for part in [content[i:i+1997] for i in range(0, len(content), 1997)]:
                            await message.reply(part)
                    else:
                        await message.reply(content)
                else:
                    clean = re.sub(r'ACTION:REPLY\s*\|?\s*', '', response, flags=re.I).strip()
                    await message.reply(clean[:1900] if clean else "Đèo mẹ, tao đang bị đơ!")
            
            # action == "NONE" -> không làm gì
    
    await bot.process_commands(message)

# ===== RANDOM CHAT TASK =====
@tasks.loop(minutes=5)
async def random_chat_task():
    if TEXT_CHANNEL_ID:
        channel = bot.get_channel(int(TEXT_CHANNEL_ID))
        if not channel:
            return
    else:
        if not memory.text_channels:
            return
        channel = random.choice(memory.text_channels)
    
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
                    await channel.send(random.choice(messages))
    except Exception as e:
        print(f"Lỗi random chat: {e}")

# ===== ON READY =====
@bot.event
async def on_ready():
    print(f"✅ Mineflayer đã đăng nhập với tên {bot.user}")
    await bot.change_presence(activity=discord.Game(name="quan sát server | Tag tao để nói chuyện"))
    
    if GUILD_ID:
        guild = bot.get_guild(int(GUILD_ID))
        if guild:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    memory.text_channels.append(channel)
            
            if TEXT_CHANNEL_ID:
                memory.main_text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
                if memory.main_text_channel:
                    print(f"📌 Main text channel: #{memory.main_text_channel.name}")
            
            if VOICE_CHANNEL_ID:
                memory.main_voice_channel = bot.get_channel(int(VOICE_CHANNEL_ID))
                if memory.main_voice_channel:
                    print(f"📌 Main voice channel: {memory.main_voice_channel.name}")
    
    print(f"📖 Đã tải {len(memory.text_channels)} text channel(s)")
    print(f"🔊 TTS: {'✅ Sẵn sàng' if HAS_TTS else '❌ Chưa cài gTTS'}")
    print(f"🎬 FFmpeg: {'✅ Có' if HAS_FFMPEG else '❌ Không'}")
    print(f"🎙️ PyNaCl: {'✅ Có' if HAS_VOICE else '❌ Không'}")
    print(f"📁 Temp folder: {TEMP_DIR}")
    
    # Cleanup temp files định kỳ
    async def cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            cleanup_temp_files()
    
    asyncio.create_task(cleanup_loop())
    random_chat_task.start()

# ===== RUN BOT =====
def run_discord_bot():
    bot.run(DISCORD_TOKEN)

# ===== FLASK HEALTH CHECK =====
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "alive",
        "bot": str(bot.user) if bot.user else "Unknown",
        "guild": GUILD_ID,
        "text_channel": memory.main_text_channel.name if memory.main_text_channel else "AUTO",
        "voice_channel": memory.main_voice_channel.name if memory.main_voice_channel else "AUTO",
        "messages": sum(len(msgs) for msgs in memory.messages.values()),
        "tts": HAS_TTS,
        "ffmpeg": HAS_FFMPEG,
        "voice": HAS_VOICE
    })

# ===== MAIN =====
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
