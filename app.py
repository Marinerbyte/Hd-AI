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

# Global variable to hold the bot thread and its state
bot_thread = None
# We use a lock to prevent race conditions when accessing shared bot_status
status_lock = threading.Lock()
bot_status = {
    "is_running": False,
    "username": None,
    "room": None,
    "log": ["INFO: Control panel initialized. Bot is stopped."]
}

# --- BOT LOGIC ---
class HowdiesBot:
    def __init__(self, username, password, initial_rooms):
        # Bot configuration
        self.BOT_MASTER = username
        self.MASTER_USERNAME = "yasin"
        self.USERNAME = username
        self.PASSWORD = password
        self.INITIAL_ROOMS = initial_rooms
        
        # Standard headers and messages
        self.ANDROID_USER_AGENT = "Howdies/1.0.0 (Linux; Android 12; Pixel 5) Dalvik/2.1.0"
        self.HEADERS = {
            "User-Agent": self.ANDROID_USER_AGENT, "Accept": "application/json",
            "Content-Type": "application/json", "X-Platform": "Android"
        }
        self.GREETING_MESSAGES = ["main aa gaya", "hello friends", f"master ({self.BOT_MASTER}) ne bulaya", "kya haal hai?"]
        self.LEAVING_MESSAGES = ["chalo bye", "nikalta hu", "master ka agla order aa gaya", "bye guys"]
        self.PONG_MESSAGE = ["pong!", "ji master?", "order master!"]
        
        # State
        self.current_rooms = {}
        self.ws = None
        self.should_run = True
        self.room_data_lock = threading.Lock()
        
        # Naya Signal System
        self.scan_trigger = threading.Event()
        self.scan_started = False

    def log(self, message):
        """Log messages for web UI and console."""
        print(message)
        with status_lock:
            bot_status["log"].append(f"[{time.strftime('%H:%M:%S')}] {message}")
            if len(bot_status["log"]) > 100:
                bot_status["log"] = bot_status["log"][-100:]

    def send_payload(self, payload_dict):
        try:
            payload_str = json.dumps(payload_dict)
            self.log(f"[SENT by BOT] -> {json.dumps(payload_dict, indent=2)}")
            if self.ws and self.ws.connected:
                self.ws.send(payload_str)
        except Exception as e:
            self.log(f"ERROR sending payload: {e}")

    def send_chat_message(self, room_id, message):
        time.sleep(random.uniform(0.5, 1.5))
        chat_payload = {"handler": "chatmessage", "roomid": room_id, "message": message}
        self.send_payload(chat_payload)
        
    def send_dm_message(self, target_username, message):
        time.sleep(random.uniform(0.5, 1.5))
        dm_payload = {"handler": "message", "type": "text", "to": target_username, "text": message}
        self.send_payload(dm_payload)
        
    def visit_user_profile(self, username):
        self.log(f"INFO: Visiting profile of user: {username}")
        profile_visit_payload = {"handler": "profile", "username": username}
        self.send_payload(profile_visit_payload)
        time.sleep(random.uniform(1.5, 4.0))

    def scan_all_rooms_and_report(self):
        """Profile visit ka kaam karta hai."""
        with self.room_data_lock:
            rooms_to_scan = list(self.current_rooms.values())
        
        if not rooms_to_scan:
            self.log("WARNING: Scan shuru hua lekin koi room join nahi mila.")
            self.send_dm_message(self.MASTER_USERNAME, "⚠️ Bot kisi bhi room mein nahi hai, isliye profile visit nahi kar sakta.")
            return

        self.log(f"INFO: Profile visiting started. Total rooms process karne hain: {len(rooms_to_scan)}")
        self.send_dm_message(self.MASTER_USERNAME, f"Profile visit shuru. Total {len(rooms_to_scan)} rooms process kiye jayenge...")
        time.sleep(2)

        for room_data in rooms_to_scan:
            room_name = room_data.get("name", "Unknown Room")
            users = room_data.get("users", [])
            
            if not users:
                self.log(f"INFO: Room '{room_name}' mein koi user list nahi mili, isliye skip kar raha hoon.")
                continue

            users_to_visit = [u for u in users if u.get('username', '').lower() != self.USERNAME.lower()]
            total_users = len(users)
            visited_count = 0
            
            for user in users_to_visit:
                username = user.get('username')
                if username:
                    self.visit_user_profile(username)
                    visited_count += 1
            
            report_message = (f"✅ Room '{room_name}' visit complete. Total {total_users} users me se {visited_count} profiles successfully visited.")
            self.send_dm_message(self.MASTER_USERNAME, report_message)
            self.log(f"REPORT: Room '{room_name}' ki report master ko DM kar di gayi hai.")
            time.sleep(5) 
            
        self.log("INFO: Sabhi rooms process ho gaye hain.")
        self.send_dm_message(self.MASTER_USERNAME, "🎉 All profile visiting tasks are finished.")

    def join_initial_rooms(self):
        """Sirf initial rooms join karne ka request bhejta hai."""
        self.log("INFO: Initial rooms join karna shuru.")
        time.sleep(5) # Login ke baad thoda saans lene do
        for room in self.INITIAL_ROOMS:
            self.send_payload({"handler": "joinchatroom", "name": room, "roomPassword": ""})
            time.sleep(random.uniform(2, 4))
        self.log("INFO: Sabhi initial room join requests bhej di gayi hain.")

    def wait_and_scan(self):
        """Signal ka intezaar karta hai aur phir scan shuru karta hai."""
        self.log("INFO: Scanner taiyaar hai, rooms join hone ka intezaar kar raha hai...")
        # 2 minute tak intezaar karega. Agar signal mila to theek, nahi to aage badhega.
        self.scan_trigger.wait(timeout=120) 
        self.log("INFO: Intezaar khatam. Ab profile visit shuru hoga.")
        self.scan_all_rooms_and_report()

    def get_token(self):
        # ... (get_token function waisa hi hai, koi badlav nahi)
        url = "https://api.howdies.app/api/login"
        payload = {"username": self.USERNAME, "password": self.PASSWORD}
        try:
            response = requests.post(url, headers=self.HEADERS, json=payload, timeout=10)
            if response.status_code == 200:
                self.log("INFO: Token mil gaya.")
                return response.json().get("token")
            else:
                self.log(f"ERROR: Token nahi mila. Status: {response.status_code}, Response: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Token lene me network exception: {e}")
            return None

    def stop(self):
        self.log("INFO: Bot ko rokne ka signal mila.")
        self.should_run = False
        if self.ws and self.ws.connected:
            self.ws.close()

    def connect_and_run(self):
        with status_lock:
            bot_status["is_running"] = True
        
        token = self.get_token()
        if not token:
            self.log("CRITICAL: Token nahi mila. Bot band ho raha hai.")
            with status_lock: bot_status["is_running"] = False
            return

        ws_url = f"wss://howdies.app:3000/?token={token}"
        
        while self.should_run:
            self.ws = websocket.WebSocket()
            try:
                self.ws.connect(ws_url, header={"User-Agent": self.ANDROID_USER_AGENT}, sslopt={"cert_reqs": ssl.CERT_NONE})
                self.log("INFO: WebSocket se connect ho gaya!")
                self.send_payload({"handler": "login", "username": self.USERNAME, "password": self.PASSWORD})

                while self.should_run:
                    message = self.ws.recv()
                    data = {}
                    try:
                        data = json.loads(message)
                        if data.get("handler") != "profile":
                             self.log(f"[RECEIVED by BOT] <- {json.dumps(data, indent=2)}")
                    except Exception as e:
                        if self.should_run: self.log(f"ERROR processing received message: {e}")
                        break
                    
                    handler = data.get("handler")

                    if handler == "login" and data.get("status") == "success":
                        self.log("INFO: Login successful!")
                        
                        # Alag-alag kaam ke liye alag-alag thread
                        threading.Thread(target=self.join_initial_rooms, daemon=True).start()
                        threading.Thread(target=self.wait_and_scan, daemon=True).start()

                    elif handler == "joinchatroom" and data.get("success"):
                        room_id = data.get('roomid')
                        name = data.get('name')
                        if room_id and name:
                            with self.room_data_lock:
                                self.current_rooms[room_id] = {"name": name, "users": []}
                            self.log(f"SUCCESS: Room '{name}' join kar liya. User list ka intezaar hai.")
                            self.send_chat_message(room_id, random.choice(self.GREETING_MESSAGES))

                    elif handler in ["roomusers", "activeoccupants"]:
                        room_id = data.get("roomid")
                        users_list = data.get("users", [])
                        if room_id in self.current_rooms:
                             with self.room_data_lock:
                                self.current_rooms[room_id]["users"] = users_list
                             self.log(f"INFO: Room ID {room_id} ke liye {len(users_list)} users ki list mil gayi.")

                             # Check karo ki kya signal bhejne ka time aa gaya hai
                             if not self.scan_started:
                                 # Agar 3 se zyada room join ho gaye hain to signal bhej do
                                 if len(self.current_rooms) >= 3:
                                     self.log("INFO: Kafi rooms join ho gaye. Scanner ko green signal de raha hoon.")
                                     self.scan_trigger.set()
                                     self.scan_started = True

                    elif handler == "chatroommessage" or handler == "message":
                        sender = data.get('username', data.get('from'))
                        msg_text = data.get('text', '')
                        if sender and sender.lower() == self.MASTER_USERNAME.lower() and msg_text.startswith('!'):
                            self.handle_master_command(msg_text, data.get('roomid'))
                    
            except Exception as e:
                if self.should_run: self.log(f"CRITICAL ERROR: {e}. 5 second me reconnect kar raha hu...")
            finally:
                if self.ws and self.ws.connected: self.ws.close()
                if self.should_run: time.sleep(5)
        
        self.log("INFO: Bot has stopped.")
        with status_lock:
            bot_status["is_running"] = False

# Baaki Flask aur HTML code waisa hi hai, usmein koi badlav nahi hai.
# ...
# --- (Flask App and HTML Template code neeche waisa hi hai) ---
bot_instance = None
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Howdies Bot Control Panel</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px; background: #f7f7f7; color: #333; }
        .container { max-width: 800px; margin: 0 auto; background: #fff; padding: 20px 40px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        h1, h2 { color: #1a1a1a; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 600; }
        input[type="text"], input[type="password"] { width: 100%; padding: 12px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        .btn { padding: 12px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; font-weight: 600; text-decoration: none; display: inline-block; }
        .btn-start { background-color: #28a745; color: white; }
        .btn-stop { background-color: #dc3545; color: white; }
        .btn-action { background-color: #007bff; color: white; }
        .status { margin-top: 30px; padding: 20px; border-radius: 8px; }
        .status.running { background-color: #e9f7ec; border: 1px solid #a3d9b1; }
        .status.stopped { background-color: #fceeee; border: 1px solid #f1b0b7; }
        .status-dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; }
        .running .status-dot { background-color: #28a745; }
        .stopped .status-dot { background-color: #dc3545; }
        #log-container {
            margin-top: 20px; background-color: #2b2b2b; color: #f1f1f1; padding: 15px;
            border-radius: 4px; height: 300px; overflow-y: auto; font-family: 'Courier New', Courier, monospace;
            font-size: 14px; white-space: pre-wrap; word-wrap: break-word;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Howdies Bot Control Panel</h1>
        
        <div id="status-div" class="status">
            <h2>
                <span id="status-dot" class="status-dot"></span>Status: <span id="status-text"></span>
            </h2>
            <div id="status-info"></div>
        </div>

        <form action="/start" method="post" style="margin-top: 20px;">
            <div class="form-group">
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Password:</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn btn-start">Start Bot</button>
            <a href="/stop" class="btn btn-stop">Stop Bot</a>
        </form>
        
        <hr style="margin: 30px 0;">
        <h2>Live Actions</h2>
        <form action="/join_room" method="post">
            <div class="form-group">
                <label for="room_name_live">Join New Room:</label>
                <input type="text" id="room_name_live" name="room_name_live" placeholder="Room ka naam daalein" required>
            </div>
             <div class="form-group">
                <label for="room_password_live">Room Password (agar hai toh):</label>
                <input type="text" id="room_password_live" name="room_password_live" placeholder="Password daalein (optional)">
            </div>
            <button type="submit" class="btn btn-action">Join Room</button>
        </form>
        
        <h2>Live Log</h2>
        <div id="log-container"></div>
    </div>
    
    <script>
        const statusDiv = document.getElementById('status-div');
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        const statusInfo = document.getElementById('status-info');
        const logContainer = document.getElementById('log-container');

        async function updateStatus() {
            try {
                const response = await fetch('/status');
                const data = await response.json();

                const isRunning = data.is_running;
                statusText.textContent = isRunning ? 'Running' : 'Stopped';
                
                statusDiv.className = isRunning ? 'status running' : 'status stopped';

                if (isRunning) {
                    statusInfo.innerHTML = `<p><strong>Username:</strong> ${data.username}</p><p><strong>Default Rooms:</strong> ${data.room}</p>`;
                } else {
                    statusInfo.innerHTML = '';
                }

                const isScrolledToBottom = logContainer.scrollHeight - logContainer.clientHeight <= logContainer.scrollTop + 1;
                logContainer.innerHTML = data.log.join('<br>');

                if(isScrolledToBottom) {
                    logContainer.scrollTop = logContainer.scrollHeight;
                }

            } catch (error)
                {
                console.error("Could not fetch status:", error);
                logContainer.innerHTML += "<br>ERROR: Control panel connection lost.";
            }
        }
        setInterval(updateStatus, 3000);
        document.addEventListener('DOMContentLoaded', updateStatus);
    </script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)
@app.route('/status')
def status():
    with status_lock: return jsonify(bot_status)
@app.route('/start', methods=['POST'])
def start_bot():
    global bot_thread, bot_instance
    with status_lock:
        if bot_thread and bot_thread.is_alive():
            bot_status['log'].append("INFO: Bot pehle se chal raha hai. Pehle stop karein.")
            return redirect(url_for('index'))
        username = request.form['username']
        password = request.form['password']
        default_rooms = ["smile", "gujarat", "dragon", "london", "news"]
        bot_status["username"] = username
        bot_status["room"] = ", ".join(default_rooms)
        bot_status["log"] = ["INFO: Bot ko start karne ka request mila..."]
        bot_instance = HowdiesBot(username, password, default_rooms)
        bot_thread = threading.Thread(target=bot_instance.connect_and_run)
        bot_thread.daemon = True
        bot_thread.start()
    time.sleep(1)
    return redirect(url_for('index'))
@app.route('/stop')
def stop_bot():
    global bot_thread, bot_instance
    with status_lock:
        if bot_instance:
            bot_instance.stop()
            bot_instance = None
            bot_thread = None
            bot_status['log'].append("INFO: Bot ko rokne ka signal bhej diya gaya hai.")
        else:
            bot_status['log'].append("INFO: Bot pehle se hi ruka hua hai.")
            bot_status["is_running"] = False
    return redirect(url_for('index'))
@app.route('/join_room', methods=['POST'])
def join_room_live():
    with status_lock:
        if bot_instance and bot_status["is_running"]:
            room_name = request.form.get('room_name_live')
            password = request.form.get('room_password_live', '')
            if room_name:
                bot_instance.join_room_from_panel(room_name, password)
                bot_status['log'].append(f"INFO: Panel se '{room_name}' join karne ka request bheja gaya.")
            else:
                bot_status['log'].append("ERROR: Room ka naam nahi daala gaya.")
        else:
            bot_status['log'].append("WARN: Bot abhi nahi chal raha hai. Pehle start karein.")
    return redirect(url_for('index'))
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)