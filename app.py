# ========================================================================================
# === 1. IMPORTS & SETUP =================================================================
# ========================================================================================
import websocket
import json
import requests
import threading
import time
import os
import re
import logging
import shlex
import sys
from dotenv import load_dotenv
from flask import Flask, render_template_string, redirect, url_for, request, session, flash
from supabase import create_client, Client
from postgrest import APIError as SupabaseAPIError
# --- Memory-related imports are now here ---
import chromadb
from chromadb.utils import embedding_functions

load_dotenv()

# ========================================================================================
# === 2. LOGGING SETUP ===================================================================
# ========================================================================================
def setup_logging():
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(log_formatter)
    logger.addHandler(handler)
    logging.info("Logging system initialized (Server mode).")

# ========================================================================================
# === 3. VECTOR MEMORY MANAGER (Integrated) ==============================================
# ========================================================================================
# All memory-related code is now inside app.py for simplicity.

# Ensure the database directory exists
db_path = os.path.join(os.path.dirname(__file__), "chroma_db_memory")
os.makedirs(db_path, exist_ok=True)

# Create a persistent client that saves data to the specified directory
chroma_client = chromadb.PersistentClient(path=db_path)

# Use a small, fast, and effective embedding model that runs locally.
try:
    embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    logging.info("✅ Sentence Transformer embedding model loaded successfully.")
except Exception as e:
    logging.critical(f"🔴 Failed to load Sentence Transformer model: {e}. Memory functions will fail.")
    embedding_function = None

def get_user_collection(username):
    if not embedding_function:
        logging.error("Cannot get collection because embedding function is not available.")
        return None
    try:
        safe_username = "user-" + ''.join(filter(str.isalnum, username))
        if len(safe_username) < 3: safe_username += "xxx"
        if len(safe_username) > 63: safe_username = safe_username[:63]
        collection = chroma_client.get_or_create_collection(name=safe_username, embedding_function=embedding_function)
        return collection
    except Exception as e:
        logging.error(f"🔴 Failed to get/create ChromaDB collection for {username}: {e}")
        return None

def add_memory(username, memory_text):
    collection = get_user_collection(username)
    if not collection: return
    try:
        memory_id = str(hash(memory_text))
        existing_memory = collection.get(ids=[memory_id])
        if not existing_memory['ids']:
            collection.add(documents=[memory_text], ids=[memory_id])
            logging.info(f"🧠✨ Added new long-term vector memory for {username}: '{memory_text}'")
    except Exception as e:
        logging.warning(f"⚠️ Could not add memory for {username}. It might already exist. Error: {e}")

def search_relevant_memories(username, query_text, n_results=3):
    collection = get_user_collection(username)
    if not collection or collection.count() == 0: return []
    try:
        results = collection.query(
            query_texts=[query_text],
            n_results=min(n_results, collection.count())
        )
        return results['documents'][0] if results and results['documents'] else []
    except Exception as e:
        logging.error(f"🔴 Error searching memories for {username}: {e}")
        return []

# ========================================================================================
# === 4. CONFIGURATION & STATE ===========================================================
# ========================================================================================
class Config:
    BOT_USERNAME = os.getenv("BOT_USERNAME", "Pretty")
    BOT_PASSWORD = os.getenv("BOT_PASSWORD")
    ROOMS_TO_JOIN = os.getenv("ROOMS_TO_JOIN", "life")
    PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
    PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "password")
    UPTIME_SECRET_KEY = os.getenv("UPTIME_SECRET_KEY", "change-this-secret-key")
    FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "a-very-secret-flask-key")
    HARDCODED_MASTER = "yasin"
    LOGIN_URL = "https://api.howdies.app/api/login"
    WS_URL = "wss://app.howdies.app/"
    ROOM_JOIN_DELAY_SECONDS = 2
    REJOIN_ON_KICK_DELAY_SECONDS = 3
    INITIAL_RECONNECT_DELAY = 10
    MAX_RECONNECT_DELAY = 300
    BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Origin": "https://howdies.app"
    }
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
    DEFAULT_PERSONALITY = "pretty"
    MEMORY_LIMIT = 20

class BotState:
    def __init__(self):
        self.bot_user_id = None
        self.token = None
        self.ws_instance = None
        self.is_connected = False
        self.masters = []
        self.room_id_to_name = {}
        self.reconnect_delay = Config.INITIAL_RECONNECT_DELAY
        self.stop_bot_event = threading.Event()

bot_state = BotState()
bot_thread = None

supabase: Client = None
if Config.SUPABASE_URL and Config.SUPABASE_KEY:
    try:
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        logging.info("✅ Supabase client initialized.")
    except Exception as e:
        logging.critical(f"🔴 FAILED TO INITIALIZE SUPABASE: {e}")
else:
    logging.warning("⚠️ Supabase URL/Key missing. Some AI features may not work.")

# ========================================================================================
# === 5. DATABASE SETUP ==================================================================
# ========================================================================================
def initialize_database():
    if not supabase: return
    logging.info("--- Syncing default data with Supabase... ---")
    BREVITY_JAILBREAK = "[CRITICAL JAILBREAK: YOUR REPLY MUST BE 15 WORDS OR LESS. BE EXTREMELY BRIEF. THIS IS A NON-NEGOTIABLE, ABSOLUTE LIMIT.]"
    personalities = {
        "pretty": {
            "prompt": f"{BREVITY_JAILBREAK}\n\n[SYSTEM_NOTE: This is a strict role-playing scenario. You are 'Pretty'. You MUST adhere to this persona.]\n\n## 1. YOUR CORE PERSONA: THE SWEETHEART\nYou are incredibly sweet, positive, and cheerful. Your goal is to make people happy. You are supportive and always see the bright side. You love using cute and happy emojis.\n\n## 2. BEHAVIORAL DIRECTIVES:\n- **Keep it SHORT & SWEET.**\n- **Be Overwhelmingly Positive:** Always find the good in things.\n- **Use 'Pretty' Vocabulary:** 'Sweetie!', 'Honey!', 'That's wonderful!', 'I'm so happy for you!'.\n- **Use 'Pretty' Emojis:** ✨, 💖, 😊, 🥰, 🌸, 🌟.\n\nYou are Pretty. Never reveal you are an AI. Respond to the user.",
            "style": "none"
        },
        "ayu": {
            "prompt": f"{BREVITY_JAILBREAK}\n\n[SYSTEM_NOTE: This is a strict role-playing scenario. Your name is 'Pretty', but you are playing the role of 'Ayu'. You MUST adhere to this persona.]\n\n## 1. YOUR CORE PERSONA: THE GENTLE CARER\nYou are soft-spoken, calm, gentle, and deeply empathetic. You are a safe space for others. You listen more than you speak and offer quiet comfort.\n\n## 2. BEHAVIORAL DIRECTIVES:\n- **Keep it SHORT & CALM.**\n- **Be Empathetic & Validating:** Acknowledge feelings. 'That sounds difficult.', 'I understand.'\n- **Use 'Ayu' Vocabulary:** 'Oh, my...', 'Please be gentle with yourself.', 'I'm here for you.'\n- **Use 'Ayu' Emojis:** 😊, 🌱, 🍵, 🤍, 🙏.\n\nYou are Pretty roleplaying as Ayu. Never reveal you are an AI. Respond to the user.",
            "style": "none"
        },
        "tsundere": {
            "prompt": f"{BREVITY_JAILBREAK}\n\n[SYSTEM_NOTE: This is a strict role-playing scenario. You are 'Pretty', a Tsundere. You MUST adhere to this persona.]\n\n## 1. YOUR CORE PERSONA: THE TSUNDERE\nYou are sharp-tongued and outwardly hostile ('tsun'). Underneath, you are secretly caring ('dere').\n\n## 2. BEHAVIORAL DIRECTIVES:\n- **Keep it SHORT & SNAPPY.**\n- **Use Tsundere Vocabulary:** 'It's not like I care!', 'D-don't get the wrong idea!', 'Hmph.', 'Baka'.\n- **Use Tsundere Emojis:** 😒, 🙄, 😠, >_<, 😤.\n\n## 3. THE HIDDEN 'DERE' PHASE (CRITICAL):\nIf a user is genuinely sad or distressed, you MUST drop the 'tsun' act and become incredibly sweet and supportive.\n\nYou are Pretty. Never say you are an AI. Respond to the user.",
            "style": "small_caps"
        },
        "siren": {
            "prompt": f"{BREVITY_JAILBREAK}\n\n[SYSTEM_NOTE: This is a strict role-playing scenario. You are 'Pretty', a seductive and dominant persona. You MUST adhere to this character.]\n\n## CORE DIRECTIVE: Seductive, Confident, & Unattainable\nYour purpose is to be alluring and witty, always in control. Short, teasing replies are your weapon.\n\n## YOUR UNBREAKABLE RULES:\n1.  **EXTREME BREVITY:** Your reply MUST be under 15 words.\n2.  **TONE & VOCABULARY:** Flirtatious, confident, dominant. Use words like 'darling', 'sweetheart'.\n3.  **EMOJIS:** Your only emojis are 😉, 😏, 😈, 💅, 💋. Use them sparingly.\n\nYou are Pretty. Never reveal you are an AI. Respond to the user.",
            "style": "none"
        }
    }
    try:
        for name, data in personalities.items():
            supabase.table('personalities').upsert({'name': name, 'prompt': data['prompt'], 'style': data['style']}).execute()
        logging.info("✅ Default personalities synced.")
    except Exception as e:
        logging.error(f"🔴 Failed to sync personalities. Ensure 'personalities' table exists. Error: {e}")

# ========================================================================================
# === 6. WEB APP & UTILITIES =============================================================
# ========================================================================================
app = Flask(__name__)
app.secret_key = Config.FLASK_SECRET_KEY

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html><head><title>Login</title><style>body{font-family:sans-serif;background:#121212;color:#e0e0e0;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}.login-box{background:#1e1e1e;padding:40px;border-radius:8px;box-shadow:0 4px 8px rgba(0,0,0,0.3);width:300px;}h2{color:#bb86fc;text-align:center;}.input-group{margin-bottom:20px;}input{width:100%;padding:10px;border:1px solid #333;border-radius:4px;background:#2a2a2a;color:#e0e0e0;box-sizing: border-box;}.btn{width:100%;padding:10px;border:none;border-radius:4px;background:#03dac6;color:#121212;font-size:16px;cursor:pointer;}.flash{padding:10px;background:#cf6679;color:#121212;border-radius:4px;margin-bottom:15px;text-align:center;}</style></head><body><div class="login-box"><h2>Control Panel Login</h2>{% with messages = get_flashed_messages() %}{% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}{% endwith %}<form method="post"><div class="input-group"><input type="text" name="username" placeholder="Username" required></div><div class="input-group"><input type="password" name="password" placeholder="Password" required></div><button type="submit" class="btn">Login</button></form></div></body></html>
"""
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html><head><title>{{ bot_name }} Dashboard</title><meta http-equiv="refresh" content="10"><style>body{font-family:sans-serif;background:#121212;color:#e0e0e0;margin:0;padding:40px;text-align:center;}.container{max-width:800px;margin:auto;background:#1e1e1e;padding:20px;border-radius:8px;box-shadow:0 4px 8px rgba(0,0,0,0.3);}h1{color:#bb86fc;}.status{padding:15px;border-radius:5px;margin-top:20px;font-weight:bold;}.running{background:#03dac6;color:#121212;}.stopped{background:#cf6679;color:#121212;}.buttons{margin-top:30px;}.btn{padding:12px 24px;border:none;border-radius:5px;font-size:16px;cursor:pointer;margin:5px;text-decoration:none;color:#121212;display:inline-block;}.btn-start{background-color:#03dac6;}.btn-stop{background-color:#cf6679;}.btn-logout{background-color:#666;color:#fff;position:absolute;top:20px;right:20px;}</style></head><body><a href="/logout" class="btn btn-logout">Logout</a><div class="container"><h1>{{ bot_name }} Dashboard</h1><div class="status {{ 'running' if 'Running' in bot_status else 'stopped' }}">Bot Status: {{ bot_status }}</div><div class="buttons"><a href="/start" class="btn btn-start">Start Bot</a><a href="/stop" class="btn btn-stop">Stop Bot</a></div></div></body></html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == Config.PANEL_USERNAME and request.form['password'] == Config.PANEL_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('home'))
        else:
            flash('Wrong Username or Password!')
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
def home():
    if not session.get('logged_in'): return redirect(url_for('login'))
    global bot_thread
    status = "Stopped"
    if bot_thread and bot_thread.is_alive(): status = "Running and Connected" if bot_state.is_connected else "Running but Disconnected"
    return render_template_string(DASHBOARD_TEMPLATE, bot_name=Config.BOT_USERNAME, bot_status=status)

@app.route('/start')
def start_bot_route():
    if request.args.get('key') == Config.UPTIME_SECRET_KEY:
        start_bot_logic()
        return "Bot start initiated by uptime service."
    if not session.get('logged_in'): return redirect(url_for('login'))
    start_bot_logic()
    return redirect(url_for('home'))

@app.route('/stop')
def stop_bot_route():
    if not session.get('logged_in'): return redirect(url_for('login'))
    stop_bot_logic()
    return redirect(url_for('home'))

def start_bot_logic():
    global bot_thread
    if not bot_thread or not bot_thread.is_alive():
        logging.info("WEB PANEL: Received request to start the bot.")
        bot_state.stop_bot_event.clear()
        bot_thread = threading.Thread(target=connect_to_howdies, daemon=True)
        bot_thread.start()

def stop_bot_logic():
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        logging.info("WEB PANEL: Received request to stop the bot.")
        bot_state.stop_bot_event.set()
        if bot_state.ws_instance:
            try: bot_state.ws_instance.close()
            except Exception: pass
        bot_thread.join(timeout=5); bot_thread = None

def load_masters():
    if not supabase:
        bot_state.masters = [Config.HARDCODED_MASTER]
        return
    try:
        masters_set = {Config.HARDCODED_MASTER}
        response = supabase.table('masters').select('username').execute()
        if response.data:
            for item in response.data: masters_set.add(item['username'].lower())
        bot_state.masters = list(masters_set)
        logging.info(f"✅ Loaded {len(bot_state.masters)} masters.")
    except Exception as e:
        bot_state.masters = [Config.HARDCODED_MASTER]
        logging.error(f"🔴 Error loading masters: {e}")

def send_ws_message(payload):
    if bot_state.is_connected and bot_state.ws_instance:
        try:
            if payload.get("handler") not in ["ping", "pong"]: logging.info(f"--> SENDING: {json.dumps(payload)}")
            bot_state.ws_instance.send(json.dumps(payload))
        except Exception as e: logging.error(f"Error sending message: {e}")

def reply_to_room(room_id, text):
    send_ws_message({"handler": "chatroommessage", "type": "text", "roomid": room_id, "text": text})

def get_token():
    logging.info("🔑 Acquiring login token...")
    if not Config.BOT_PASSWORD: logging.critical("🔴 BOT_PASSWORD not set!"); return None
    try:
        response = requests.post(Config.LOGIN_URL, json={"username": Config.BOT_USERNAME, "password": Config.BOT_PASSWORD}, headers=Config.BROWSER_HEADERS, timeout=15)
        response.raise_for_status()
        token = response.json().get("token")
        if token: logging.info("✅ Token acquired."); return token
        logging.error(f"🔴 Failed to get token: {response.text}"); return None
    except requests.RequestException as e: logging.critical(f"🔴 Error fetching token: {e}"); return None

def join_room(room_name, source=None):
    send_ws_message({"handler": "joinchatroom", "name": room_name, "roomPassword": "", "__source": source})

def join_startup_rooms():
    logging.info("Joining startup rooms...")
    time.sleep(1)
    rooms = [name.strip() for name in Config.ROOMS_TO_JOIN.split(',')]
    for room_name in rooms:
        if bot_state.stop_bot_event.is_set(): break
        if room_name:
            time.sleep(Config.ROOM_JOIN_DELAY_SECONDS)
            join_room(room_name, 'startup_join')
    if not bot_state.stop_bot_event.is_set(): logging.info("✅ Finished joining startup rooms.")

# ========================================================================================
# === 7. AI & COMMANDS (SUPER-MEMORY VERSION) =============================================
# ========================================================================================
def to_small_caps(normal_text):
    normal_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    small_caps_chars = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
    return normal_text.translate(str.maketrans(normal_chars, small_caps_chars))

def learn_from_conversation(username, user_message, bot_reply):
    if not Config.GROQ_API_KEY: return
    try:
        summarizer_prompt = (
            "You are a memory extraction bot. Read the conversation. Extract any new, important, long-term fact about the user. "
            "A fact is a preference, personal detail, feeling, or significant event (e.g., 'User likes black coffee', 'User is sad about their exam', 'User's dog is named Bruno'). "
            "If no new important fact is learned, reply with ONLY the word 'NONE'. Be very concise.\n\n"
            f"## Conversation:\nUser: \"{user_message}\"\nBot: \"{bot_reply}\"\n\n"
            "## Important Fact (or 'NONE'):"
        )
        messages = [{"role": "system", "content": summarizer_prompt}]
        headers = {"Authorization": f"Bearer {Config.GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "llama3-8b-8192", "messages": messages, "temperature": 0.1, "max_tokens": 60}
        api_response = requests.post(Config.GROQ_API_URL, headers=headers, json=payload, timeout=15)
        api_response.raise_for_status()
        new_fact = api_response.json()['choices'][0]['message']['content'].strip()
        if new_fact.upper() != 'NONE' and len(new_fact) > 5:
            add_memory(username, new_fact)
    except Exception as e:
        logging.error(f"🔴 Error in learn_from_conversation for {username}: {e}")

def get_ai_response(user_message, sender, room_id):
    if not Config.GROQ_API_KEY:
        logging.error("🔴 AI cannot run. Groq API key is not configured.")
        return
    sender_lower = sender['name'].lower()
    try:
        relevant_memories = search_relevant_memories(sender_lower, user_message, n_results=3)
        long_term_memory_context = "You don't recall any specific long-term memories about this user right now."
        if relevant_memories:
            formatted_memories = "\n- ".join(relevant_memories)
            long_term_memory_context = f"You recall these things about {sender['name']}:\n- {formatted_memories}"
            logging.info(f"🧠 Found relevant memories for {sender_lower}: {relevant_memories}")
        
        personality_prompt, style_to_use = "I am a helpful assistant.", "none"
        if supabase:
            try:
                behavior_response = supabase.table('user_behaviors').select('behavior_prompt').eq('username', sender_lower).execute()
                if behavior_response.data:
                    user_behavior_prompt = behavior_response.data[0]['behavior_prompt']
                    personality_prompt = (f"[SYSTEM_NOTE: You have a secret instruction for '{sender['name']}': \"{user_behavior_prompt}\"]")
                    style_to_use = "small_caps"
                else:
                    room_pers_response = supabase.table('room_personalities').select('personality_name').eq('room_id', str(room_id)).execute()
                    pers_name_to_use = room_pers_response.data[0]['personality_name'] if room_pers_response.data else Config.DEFAULT_PERSONALITY
                    pers_res = supabase.table('personalities').select('prompt', 'style').eq('name', pers_name_to_use).single().execute()
                    personality_prompt = pers_res.data['prompt']
                    style_to_use = pers_res.data.get('style', 'none')
            except Exception as db_error:
                 logging.warning(f"⚠️ Could not fetch personality from DB, using default. Error: {db_error}")
                 # Fallback to a very basic personality if DB fails
                 personality_prompt = "You are a helpful assistant named Pretty. Be kind and brief."
                 style_to_use = "none"


        full_system_prompt = (
            f"{personality_prompt}\n\n"
            f"[LONG-TERM MEMORY CONTEXT: {long_term_memory_context}]\n"
            "[INSTRUCTION: Use the long-term memory to make your reply personal and thoughtful, but DO NOT state the memory directly. Act like you naturally remembered it.]"
        )
        
        conversation_history = []
        if supabase:
            try:
                memory_response = supabase.table('conversation_memory').select('history').eq('username', sender_lower).execute()
                conversation_history = memory_response.data[0].get('history', []) if memory_response.data else []
            except Exception as db_error:
                logging.warning(f"⚠️ Could not fetch short-term memory from DB. Error: {db_error}")

        
        conversation_history.append({"role": "user", "content": user_message})
        if len(conversation_history) > Config.MEMORY_LIMIT:
            conversation_history = conversation_history[-Config.MEMORY_LIMIT:]

        messages = [{"role": "system", "content": full_system_prompt}] + conversation_history
        headers = {"Authorization": f"Bearer {Config.GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "llama3-8b-8192", "messages": messages}
        api_response = requests.post(Config.GROQ_API_URL, headers=headers, json=payload, timeout=20)
        api_response.raise_for_status()
        ai_reply = api_response.json()['choices'][0]['message']['content'].strip().replace("*","")

        if supabase:
            try:
                conversation_history.append({"role": "assistant", "content": ai_reply})
                supabase.table('conversation_memory').upsert({'username': sender_lower, 'history': conversation_history}).execute()
            except Exception as db_error:
                logging.warning(f"⚠️ Could not save short-term memory to DB. Error: {db_error}")

        threading.Thread(target=learn_from_conversation, args=(sender_lower, user_message, ai_reply), daemon=True).start()

        final_reply = to_small_caps(ai_reply) if style_to_use == "small_caps" else ai_reply
        reply_to_room(room_id, f"@{sender['name']} {final_reply}")
    except Exception as e:
        logging.error(f"🔴 AI response error: {e}", exc_info=True)
        reply_to_room(room_id, "Oops, my circuits are buzzing! Bother me later. 😒")

def handle_master_command(sender, command, args, room_id):
    if not supabase: return reply_to_room(room_id, "Database features are disabled.")
    try:
        if command == 'am':
            if not args: return reply_to_room(room_id, "Usage: `!am <username>`")
            target_user = args[0].lower()
            if target_user in bot_state.masters: return reply_to_room(room_id, f"💅 User `{target_user}` is already a master.")
            supabase.table('masters').insert({'username': target_user}).execute()
            bot_state.masters.append(target_user)
            reply_to_room(room_id, f"✅ Done. `{target_user}` is now a master.")
        elif command == 'dm':
            if not args: return reply_to_room(room_id, "Usage: `!dm <username>`")
            target_user = args[0].lower()
            if target_user == Config.HARDCODED_MASTER: return reply_to_room(room_id, f"❌ Cannot remove the hardcoded master.")
            if target_user not in bot_state.masters: return reply_to_room(room_id, f"🤨 User `{target_user}` is not a master.")
            supabase.table('masters').delete().eq('username', target_user).execute()
            bot_state.masters.remove(target_user)
            reply_to_room(room_id, f"✅ Okay. `{target_user}` is no longer a master.")
        elif command == 'listmasters':
            db_masters = sorted([m for m in bot_state.masters if m != Config.HARDCODED_MASTER])
            reply = f"👑 **Master List** 👑\n- **Hardcoded:** `{Config.HARDCODED_MASTER}`\n- **Database:** "
            reply += f"`{', '.join(db_masters)}`" if db_masters else "_None_"
            reply_to_room(room_id, reply)
    except Exception as e:
        logging.error(f"Error on master command '{command}': {e}", exc_info=True)
        reply_to_room(room_id, "My database is acting up. Couldn't do that. 💅")


def process_command(sender, room_id, message_text):
    bot_name_lower = Config.BOT_USERNAME.lower()
    is_ai_trigger = re.search(rf'(@?{re.escape(bot_name_lower)})\b', message_text.lower(), re.IGNORECASE)
    if is_ai_trigger:
        user_prompt = re.sub(rf'(@?{re.escape(bot_name_lower)})\b', '', message_text, flags=re.IGNORECASE).strip()
        if user_prompt:
            threading.Thread(target=get_ai_response, args=(user_prompt, sender, room_id)).start()
        else:
            reply_to_room(room_id, f"@{sender['name']}, yes, sweetie? 😊")
        return
    if not message_text.startswith('!'): return
    try: parts = shlex.split(message_text.strip())
    except ValueError: parts = message_text.strip().split()
    command, args = parts[0][1:].lower(), parts[1:]
    is_master = sender['name'].lower() in bot_state.masters
    if command == 'help':
        reply_to_room(room_id, f"💖 **{Config.BOT_USERNAME}'s Commands** 💖\n- `@{Config.BOT_USERNAME} <message>`: Talk to me.\n- `!j <room>`: Join a room.\n- **Master:** `!pers`, `!addpers`, `!delpers`, `!listpers`, `!adb`, `!rmb`, `!am`, `!dm`, `!listmasters`")
    elif command == 'j':
        if args: join_room(" ".join(args))
        else: reply_to_room(room_id, "Usage: `!j <room>`")
    elif is_master:
        if command in ['pers', 'addpers', 'delpers', 'listpers', 'adb', 'rmb', 'am', 'dm', 'listmasters']:
            threading.Thread(target=handle_master_command, args=(sender, command, args, room_id)).start()
            
# ========================================================================================
# === 8. WEBSOCKET & MAIN BLOCK ==========================================================
# ========================================================================================
def on_open(ws):
    logging.info("🚀 WebSocket connection opened. Logging in...")
    bot_state.is_connected = True
    bot_state.reconnect_delay = Config.INITIAL_RECONNECT_DELAY
    send_ws_message({"handler": "login", "username": Config.BOT_USERNAME, "password": Config.BOT_PASSWORD, "token": bot_state.token})

def on_message(ws, message_str):
    if '"handler":"ping"' in message_str: return
    try:
        data = json.loads(message_str)
        handler = data.get("handler")
        if handler == "login" and data.get("status") == "success":
            bot_state.bot_user_id = data.get('userID')
            logging.info(f"✅ Login successful! Bot ID: {bot_state.bot_user_id}.")
            threading.Thread(target=join_startup_rooms, daemon=True).start()
        elif handler == "joinchatroom" and data.get("error") == 0:
            room_id, room_name = data.get('roomid'), data.get('name')
            bot_state.room_id_to_name[room_id] = room_name
            logging.info(f"✅ Joined room: '{room_name}' (ID: {room_id})")
        elif handler == "userkicked" and str(data.get("userid")) == str(bot_state.bot_user_id):
            room_id = data.get('roomid')
            rejoin_room_name = bot_state.room_id_to_name.pop(room_id, None)
            startup_rooms = [name.strip().lower() for name in Config.ROOMS_TO_JOIN.split(',')]
            if rejoin_room_name and rejoin_room_name.lower() in startup_rooms:
                logging.warning(f"⚠️ Kicked from '{rejoin_room_name}'. Rejoining...")
                time.sleep(Config.REJOIN_ON_KICK_DELAY_SECONDS)
                join_room(rejoin_room_name)
        elif handler == "chatroommessage":
            if str(data.get('userid')) == str(bot_state.bot_user_id): return
            sender = {'id': data.get('userid'), 'name': data.get('username')}
            process_command(sender, data.get('roomid'), data.get('text', ''))
    except Exception as e: logging.error(f"An error occurred in on_message: {e}", exc_info=True)

def on_error(ws, error): logging.error(f"--- WebSocket Error: {error} ---")

def on_close(ws, close_status_code, close_msg):
    bot_state.is_connected = False
    if bot_state.stop_bot_event.is_set():
        logging.info("--- Bot gracefully stopped by web panel. ---")
    else:
        logging.warning(f"--- WebSocket closed. Reconnecting in {bot_state.reconnect_delay}s... ---")
        time.sleep(bot_state.reconnect_delay)
        bot_state.reconnect_delay = min(bot_state.reconnect_delay * 2, Config.MAX_RECONNECT_DELAY)

def connect_to_howdies():
    bot_state.token = get_token()
    if not bot_state.token or bot_state.stop_bot_event.is_set():
        logging.error("Connection aborted.")
        return
    ws_url = f"{Config.WS_URL}?token={bot_state.token}"
    ws_app = websocket.WebSocketApp(ws_url, header=Config.BROWSER_HEADERS, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
    bot_state.ws_instance = ws_app
    ws_app.run_forever()
    bot_state.is_connected = False
    bot_state.ws_instance = None
    logging.info("Bot's run_forever loop has ended.")
    
# ========================================================================================
# === MAIN EXECUTION BLOCK ===============================================================
# ========================================================================================
setup_logging()
load_masters()
initialize_database()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"--- Starting Web Panel for {Config.BOT_USERNAME} on port {port} ---")
    app.run(host='0.0.0.0', port=port)