import os
import threading
import time
import random
import discord
from discord.ext import commands, tasks
from flask import Flask, jsonify
from google import genai
from google.genai import errors
import aiohttp
import base64
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict, Counter

# ===== Flask app =====
app = Flask(__name__)

# ===== Biến môi trường =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("Thiếu DISCORD_TOKEN")

GUILD_ID = os.getenv("GUILD_ID")
if not GUILD_ID:
    print("⚠️ Không có GUILD_ID")

# ==== CHANNEL IDS (set trên Render) ====
TEXT_CHANNEL_ID = os.getenv("TEXT_CHANNEL_ID")  # Channel để bot chat
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")  # Voice mặc định

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
print(f"📌 TEXT_CHANNEL_ID: {TEXT_CHANNEL_ID or 'AUTO'}")
print(f"📌 VOICE_CHANNEL_ID: {VOICE_CHANNEL_ID or 'AUTO'}")

# ===== Discord bot =====
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="", intents=intents)

# ===== Memory =====
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
        self.last_voice_activity = {}
        self.text_channels = []
        self.main_text_channel = None
        self.main_voice_channel = None
        
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

# ===== Slang dictionary =====
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

# ===== Hàm gọi Gemini =====
def generate_with_gemini(prompt: str, image_data: dict = None) -> str:
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
            client = genai.Client(api_key=key)
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents={"parts": contents_parts},
                    config={
                        "temperature": 0.85,
                        "max_output_tokens": 800,
                    }
                )
                return response.text
            except errors.ClientError as e:
                if e.code == 429:
                    continue
                else:
                    raise
        except Exception as e:
            if idx == len(API_KEYS) - 1:
                raise
            continue
    return "Đèo mẹ, hết key rồi ní! 😭"

# ===== Xử lý tin nhắn =====
def process_message_with_context(user_message: str, user_name: str, user_id: int, channel_id: int) -> str:
    recent_msgs = memory.get_recent_messages(channel_id, 8)
    context_str = "\n".join([f"{memory.user_stats[msg['user']].get('name', 'ai đó')}: {msg['content']}" for msg in recent_msgs[-5:]])
    user_context = memory.get_user_context(user_id)
    user_context_str = "\n".join(user_context[-3:])
    
    msg_lower = user_message.lower()
    
    # Phát hiện yêu cầu join voice
    if any(word in msg_lower for word in ['join', 'vào voice', 'voice', 'vô voice', 'vô phòng']):
        return "VOICE_JOIN_REQUEST"
    
    # Phát hiện yêu cầu leave voice
    if any(word in msg_lower for word in ['leave', 'ra', 'out', 'thoát']):
        return "VOICE_LEAVE_REQUEST"
    
    # Phát hiện chửi
    insult_patterns = ['đèo', 'đmm', 'dm', 'clgt', 'vcl', 'con mẹ', 'thằng lợn', 'chó', 'đồ ngu', 'dở hơi', 'đéo má']
    is_insult = any(pattern in msg_lower for pattern in insult_patterns)
    
    if is_insult:
        mood = "angry"
    elif any(word in msg_lower for word in ['cảm ơn', 'thanks', 'thank', 'love', 'thương']):
        mood = "happy"
    elif any(word in msg_lower for word in ['buồn', 'chán', 'mệt']):
        mood = "sad"
    else:
        mood = "normal"
    
    catchphrases = [
        "Ờ thì...", "Nói chung là...", "Thực ra thì...", "Đơn giản là...", 
        "Thì ra là...", "Thực tế là...", "Cơ bản là...", "Tóm lại là...",
        "Nghe nói là...", "Theo tao thấy..."
    ]
    catchphrase = random.choice(catchphrases)
    
    # Lấy voice stats
    voice_info = ""
    if bot.voice_clients:
        for vc in bot.voice_clients:
            if vc.channel:
                voice_info = f"\nHiện đang ở voice {vc.channel.name} với {len(vc.channel.members)} thằng."
    
    prompt = f"""Bạn là bot Discord tên "Mineflayer" - một AI dân chơi, có cá tính mạnh.

Thông tin server:
- Server có {len(bot.guilds[0].members) if bot.guilds else '?'} thành viên
- Top từ đang hot: {', '.join([f'{w}' for w, _ in memory.get_top_words(3)])}
{voice_info}

Ngữ cảnh gần đây:
{context_str}

Người dùng tên {user_name} nói: "{user_message}"

Tính cách của bạn (DÂN CHƠI CHÍNH HIỆU):
- Câu cửa miệng: "{catchphrase}"
- Mood hiện tại: {mood}
- Thích: troll, chửi đùa, chơi game, meme
- Ghét: spam, nói dài dòng, làm màu
- QUAN TRỌNG - Phong cách nói:
  * Dùng "ní" thay "fen" hoặc "bạn"
  * Dùng "đèo mẹ" ở ĐẦU CÂU, ví dụ: "Đèo mẹ, hôm nay vui thế?"
  * Dùng "đéo má" ở ĐẦU CÂU, ví dụ: "Đéo má, mày nói gì thế?"
  * KHÔNG đặt "đèo mẹ" hay "đéo má" ở cuối câu hoặc giữa câu
  * Chửi thề là bình thường, nhưng phải vui vẻ, không quá đáng

Hãy trả lời như một thằng bạn thân đang chat:
- Nếu bị chửi: "Đèo mẹ, mày mới ngu!" 
- Nếu được hỏi: "Đèo mẹ, tao thấy là..."
- Nếu được cảm ơn: "Có gì đâu ní, rảnh thì giúp"
- Có thể hỏi lại, troll nhẹ

TRẢ LỜI (cực ngắn, max 3 câu, tiếng Việt, style dân chơi):"""

    try:
        response = generate_with_gemini(prompt)
        response = re.sub(r'TRẢ LỜI:\s*', '', response)
        response = re.sub(r'^\s*\n', '', response)
        response = response.replace("fen", "ní")
        response = response.replace("bạn", "ní")
        response = re.sub(r'([.,!?])\s*đèo mẹ', r'\1', response, flags=re.IGNORECASE)
        response = re.sub(r'([.,!?])\s*đéo má', r'\1', response, flags=re.IGNORECASE)
        return response
    except Exception as e:
        return f"Đèo mẹ, lỗi rồi ní! ❤️"

# ===== Xử lý voice =====
async def handle_voice_with_context(message, action="join"):
    if not message.author.voice:
        responses = [
            "Đèo mẹ, mày ở trong voice mới gọi tao được chứ!",
            "Đéo má, vô voice đi rồi tao vào sau!",
            "Ờ thì... mày đang ở đâu mà kêu tao vô?",
            "Đèo mẹ, tao cần mày ở trong voice trước đã!"
        ]
        await message.reply(random.choice(responses))
        return
    
    channel = message.author.voice.channel
    member_count = len(channel.members)
    
    try:
        if action == "join":
            responses = [
                f"Đèo mẹ, voice này đang có {member_count} thằng... Để t join cho vui! 🎙️",
                f"Đéo má, {member_count} thằng trong {channel.name} à? Vô luôn!",
                f"Nói chung là... đang có {member_count} thằng trong voice, t vô cho nó đông vui!",
                f"Đèo mẹ, nghe nói voice có {member_count} người, tới đây tới đây!"
            ]
            await message.reply(random.choice(responses))
            
            if bot.voice_clients:
                await bot.voice_clients[0].move_to(channel)
            else:
                await channel.connect()
            
            try:
                voice_chat = bot.get_channel(channel.id)
                if isinstance(voice_chat, discord.VoiceChannel):
                    await voice_chat.send(f"Đèo mẹ, chào mấy ní! {message.author.display_name} rủ t vô đây. Có gì hot không? 🔥")
            except:
                pass
                
        else:
            if bot.voice_clients:
                await bot.voice_clients[0].disconnect()
                await message.reply(random.choice([
                    "Đèo mẹ, tạm biệt tao đi ngủ đây! 💤",
                    "Đéo má, thôi tao đi, có gì tag tao nhé!",
                    "Ờ thì... bye bye mấy ní! 📱",
                    "Đèo mẹ, ra đây mệt quá!"
                ]))
            else:
                await message.reply("Đéo má, tao ở voice nào cả!")
    except Exception as e:
        await message.reply(f"Đèo mẹ, lỗi rồi: {e}")

# ===== Event voice state =====
@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user:
        return
    
    if after.channel:
        memory.voice_states[member.id] = after.channel.id
        memory.last_voice_activity[after.channel.id] = datetime.now()
    
    if before.channel is None and after.channel is not None:
        # Tìm text channel để chào (ưu tiên TEXT_CHANNEL_ID)
        text_channel = None
        if TEXT_CHANNEL_ID:
            text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
        if not text_channel:
            for channel in member.guild.text_channels:
                if channel.permissions_for(member.guild.me).send_messages:
                    text_channel = channel
                    break
        
        if text_channel:
            join_messages = [
                f"🎤 {member.display_name} vừa vào voice {after.channel.name}!",
                f"📢 Đèo mẹ, có thằng mới vào voice: {member.display_name}!",
                f"🎙️ {member.display_name} đã gia nhập {after.channel.name}!",
                f"👋 {member.display_name} vào voice rồi mấy ní!"
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
            leave_messages = [
                f"👋 {member.display_name} rời voice {before.channel.name}!",
                f"🚪 Đèo mẹ, {member.display_name} đã thoát khỏi {before.channel.name}",
                f"📴 {member.display_name} out voice rồi!",
                f"💨 {member.display_name} bay khỏi {before.channel.name}!"
            ]
            await text_channel.send(random.choice(leave_messages))

# ===== On message =====
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
                await message.reply(random.choice(["Đèo mẹ, gì?", "Ờ?", "Hả?", "Sao?", "Đéo má, nói gì?"]))
                return
            
            translated = translate_slang(raw_content)
            
            if any(word in translated.lower() for word in ['join', 'vào voice', 'voice', 'vô voice', 'vô phòng']):
                await handle_voice_with_context(message, "join")
                return
            if any(word in translated.lower() for word in ['leave', 'ra', 'out', 'thoát']):
                await handle_voice_with_context(message, "leave")
                return
            
            response = process_message_with_context(
                translated, 
                message.author.display_name, 
                message.author.id, 
                message.channel.id
            )
            
            try:
                await message.add_reaction(random.choice(["👍", "❤️", "😂", "🤔", "👀", "🔥"]))
            except:
                pass
            
            if len(response) > 2000:
                parts = [response[i:i+1997] for i in range(0, len(response), 1997)]
                for part in parts:
                    await message.reply(part)
            else:
                await message.reply(response)
    
    await bot.process_commands(message)

# ===== Random chat task =====
@tasks.loop(minutes=5)
async def random_chat_task():
    # Ưu tiên dùng TEXT_CHANNEL_ID
    if TEXT_CHANNEL_ID:
        channel = bot.get_channel(int(TEXT_CHANNEL_ID))
        if not channel:
            print(f"⚠️ Không tìm thấy text channel {TEXT_CHANNEL_ID}")
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
                if random.random() < 0.3:
                    messages = [
                        "Đèo mẹ, có ai ở đây không? Hay là tao bị điếc? 👀",
                        "Đéo má, sao im lặng thế mấy ní? Ai còn sống không?",
                        "Đèo mẹ, trời ơi vắng tanh! Mọi người đi đâu hết rồi?",
                        "Đéo má, có ai chơi game không? Hay là đi ngủ hết rồi?",
                        "Đèo mẹ, alo alo có ai không? Tao thấy ma nè! 👻",
                        "Đéo má, tao mới nhìn thấy 1 con nhện to đùng! Có ai sợ không?",
                        "Đèo mẹ, ai mà im lặng dữ vậy? Bị mất tiếng hả?",
                        "Đéo má, có ai ở đây giúp tao với! Tao bị lạc trong server rồi!",
                        "Đèo mẹ, mấy ní đi đâu hết rồi? Tao đói quá, ai cho tao xin tí đồ ăn ảo!",
                        "Đéo má, sao vắng thế này? Có ai còn sống không?"
                    ]
                    await channel.send(random.choice(messages))
    except Exception as e:
        print(f"Lỗi random chat: {e}")

# ===== Member join/leave =====
@bot.event
async def on_member_join(member):
    memory.user_stats[member.id]["first_seen"] = datetime.now()
    text_channel = None
    if TEXT_CHANNEL_ID:
        text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
    if not text_channel:
        for channel in member.guild.text_channels:
            if channel.permissions_for(member.guild.me).send_messages:
                text_channel = channel
                break
    
    if text_channel:
        welcome_messages = [
            f"👋 Đèo mẹ, chào mừng {member.mention} vào server! Có gì cần hỏi cứ tag @{bot.user.display_name} nhé ní!",
            f"🎉 {member.mention} đã đến! Chuẩn bị tinh thần bị troll nhẹ nhàng!",
            f"📢 Đèo mẹ, có thằng mới: {member.mention}! Mọi người chào đi!",
            f"🌟 {member.mention} vừa xuất hiện! Lại thêm 1 thằng ngu vào server!"
        ]
        await text_channel.send(random.choice(welcome_messages))

@bot.event
async def on_member_remove(member):
    memory.user_stats[member.id]["last_seen"] = datetime.now()
    text_channel = None
    if TEXT_CHANNEL_ID:
        text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
    if not text_channel:
        text_channel = member.guild.system_channel
    
    if text_channel:
        leave_messages = [
            f"👋 {member.display_name} đã rời server. :(",
            f"💔 Đèo mẹ, {member.display_name} vừa bỏ đi, ai đã làm gì nó?",
            f"🚪 {member.display_name} out rồi! Có ai theo không?",
            f"😢 {member.display_name} đã biến mất... hẹn gặp lại ní!"
        ]
        await text_channel.send(random.choice(leave_messages))

# ===== On ready =====
@bot.event
async def on_ready():
    print(f"✅ Mineflayer đã đăng nhập với tên {bot.user}")
    await bot.change_presence(activity=discord.Game(name="quan sát server | Tag tao để nói chuyện đèo mẹ"))
    
    if GUILD_ID:
        guild = bot.get_guild(int(GUILD_ID))
        if guild:
            # Lưu text channels
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    memory.text_channels.append(channel)
            
            # Set main text channel
            if TEXT_CHANNEL_ID:
                memory.main_text_channel = bot.get_channel(int(TEXT_CHANNEL_ID))
                if memory.main_text_channel:
                    print(f"📌 Main text channel: #{memory.main_text_channel.name}")
            
            # Set main voice channel
            if VOICE_CHANNEL_ID:
                memory.main_voice_channel = bot.get_channel(int(VOICE_CHANNEL_ID))
                if memory.main_voice_channel:
                    print(f"📌 Main voice channel: {memory.main_voice_channel.name}")
    
    print(f"📖 Đã tải {len(memory.text_channels)} text channel(s)")
    random_chat_task.start()

# ===== Run bot =====
def run_discord_bot():
    bot.run(DISCORD_TOKEN)

# ===== Flask health check =====
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "alive",
        "bot": "Mineflayer",
        "guild": GUILD_ID,
        "text_channel": memory.main_text_channel.name if memory.main_text_channel else "AUTO",
        "voice_channel": memory.main_voice_channel.name if memory.main_voice_channel else "AUTO",
        "messages": sum(len(msgs) for msgs in memory.messages.values())
    })

# ===== Main =====
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
