import json
import websocket
import time
import random
import requests
import ssl
import re
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
import threading

# --- Flask App Setup ---
app = Flask(__name__)

# Multiple bot instances storage
bots = {}
bot_threads = {}
status_lock = threading.Lock()

def make_bot_status(name):
    return {
        "is_running": False,
        "username": None,
        "room": None,
        "proxy": None,
        "log": [f"INFO: Control panel initialized for {name}. Bot is stopped."]
    }

# Predefine 5 slots
bot_status = {f"bot{i}": make_bot_status(f"Bot{i}") for i in range(1, 6)}


# --- BOT LOGIC ---
class HowdiesBot:
    def __init__(self, bot_id, username, password, initial_room, proxy_url):
        self.bot_id = bot_id
        self.BOT_MASTER = username
        self.USERNAME = username
        self.PASSWORD = password
        self.INITIAL_ROOM = initial_room
        self.proxy_url = proxy_url
        self.ANDROID_USER_AGENT = "Howdies/1.0.0 (Linux; Android 12; Pixel 5) Dalvik/2.1.0"
        self.HEADERS = {
            "User-Agent": self.ANDROID_USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Platform": "Android"
        }
        self.GREETING_MESSAGES = ["main aa gaya", "hello friends", f"master ({self.BOT_MASTER}) ne bulaya", "kya haal hai?"]
        self.LEAVING_MESSAGES = ["chalo bye", "nikalta hu", "master ka agla order aa gaya", "bye guys"]
        self.PONG_MESSAGE = ["pong!", "ji master?", "order master!"]
        self.current_rooms = {}
        self.ws = None
        self.should_run = True

    def log(self, message):
        with status_lock:
            bot_status[self.bot_id]["log"].append(f"[{time.strftime('%H:%M:%S')}] {message}")
            if len(bot_status[self.bot_id]["log"]) > 200:
                bot_status[self.bot_id]["log"] = bot_status[self.bot_id]["log"][-200:]

    def send_payload(self, payload_dict):
        try:
            if self.ws and self.ws.connected:
                self.ws.send(json.dumps(payload_dict))
                self.log(f"[SENT] {payload_dict}")
        except Exception as e:
            self.log(f"ERROR sending payload: {e}")

    def send_chat_message(self, room_id, message):
        time.sleep(random.uniform(0.5, 1.5))
        chat_payload = {"handler": "chatmessage", "roomid": room_id, "message": message}
        self.send_payload(chat_payload)

    def get_token(self):
        url = "https://api.howdies.app/api/login"
        payload = {"username": self.USERNAME, "password": self.PASSWORD}
        proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
        try:
            response = requests.post(url, headers=self.HEADERS, json=payload, proxies=proxies, timeout=10)
            if response.status_code == 200:
                self.log("INFO: Token mil gaya.")
                return response.json().get("token")
            self.log(f"ERROR: Token nahi mila. Status: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log(f"ERROR: Token lene me problem: {e}")
        return None

    def stop(self):
        self.should_run = False
        if self.ws and self.ws.connected:
            self.ws.close()
        self.log("INFO: Bot stopped.")

    def connect_and_run(self):
        with status_lock:
            bot_status[self.bot_id]["is_running"] = True
        token = self.get_token()
        if not token:
            self.log("CRITICAL: Token nahi mila. Bot band ho gaya.")
            with status_lock:
                bot_status[self.bot_id]["is_running"] = False
            return

        ws_url = f"wss://howdies.app:3000/?token={token}"

        while self.should_run:
            try:
                # WebSocket connection with proxy
                proxy_host, proxy_port = None, None
                if self.proxy_url and self.proxy_url.startswith("http"):
                    proxy_host = self.proxy_url.split("://")[1].split(":")[0]
                    proxy_port = int(self.proxy_url.split(":")[-1])

                self.ws = websocket.WebSocket()
                self.ws.connect(
                    ws_url,
                    header={"User-Agent": self.ANDROID_USER_AGENT},
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    http_proxy_host=proxy_host,
                    http_proxy_port=proxy_port
                )
                self.log("INFO: WebSocket connected!")

                self.send_payload({"handler": "login", "username": self.USERNAME, "password": self.PASSWORD})

                while self.should_run:
                    message = self.ws.recv()
                    data = json.loads(message)
                    self.log(f"[RECV] {data}")
            except Exception as e:
                self.log(f"WARN: Connection error: {e}. Reconnecting in 5s...")
                time.sleep(5)
        with status_lock:
            bot_status[self.bot_id]["is_running"] = False
        self.log("INFO: Bot exited.")


# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Howdies Multi-Bot Panel</title>
    <style>
        body { font-family: sans-serif; margin:20px; background:#f7f7f7; }
        .bot-card { background:#fff; padding:15px; margin-bottom:20px; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.1); }
        textarea { width:100%; height:150px; }
        input { width:100%; margin:5px 0; padding:8px; }
        button { padding:8px 12px; margin-top:8px; }
    </style>
</head>
<body>
    <h1>Howdies Multi-Bot Control Panel</h1>
    {% for bid, status in statuses.items() %}
    <div class="bot-card">
        <h2>{{ bid }} - Status: {{ "Running" if status.is_running else "Stopped" }}</h2>
        <form method="post" action="/start/{{ bid }}">
            <input name="username" placeholder="Username" required>
            <input name="password" placeholder="Password" required type="password">
            <input name="room" placeholder="Room name" required>
            <input name="proxy" placeholder="Proxy URL (http://ip:port)">
            <button type="submit">Start</button>
        </form>
        <form method="get" action="/stop/{{ bid }}">
            <button type="submit">Stop</button>
        </form>
        <h3>Logs</h3>
        <textarea readonly>{{ "\\n".join(status.log) }}</textarea>
    </div>
    {% endfor %}
</body>
</html>
"""

# --- ROUTES ---
@app.route("/")
def index():
    with status_lock:
        return render_template_string(HTML_TEMPLATE, statuses=bot_status)

@app.route("/start/<bot_id>", methods=["POST"])
def start(bot_id):
    if bot_id not in bot_status: return "Invalid bot", 400
    username = request.form["username"]
    password = request.form["password"]
    room = request.form["room"]
    proxy = request.form.get("proxy", "")

    with status_lock:
        if bot_threads.get(bot_id) and bot_threads[bot_id].is_alive():
            bot_status[bot_id]["log"].append("Bot already running!")
            return redirect(url_for("index"))
        bot_status[bot_id].update({"username": username, "room": room, "proxy": proxy, "log": ["INFO: Starting..."]})

    bot = HowdiesBot(bot_id, username, password, room, proxy)
    bots[bot_id] = bot
    t = threading.Thread(target=bot.connect_and_run, daemon=True)
    bot_threads[bot_id] = t
    t.start()
    time.sleep(1)
    return redirect(url_for("index"))

@app.route("/stop/<bot_id>")
def stop(bot_id):
    if bot_id in bots and bots[bot_id]:
        bots[bot_id].stop()
        bots[bot_id] = None
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
