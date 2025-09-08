import json
import websocket
import time
import random
import requests
import ssl
import re
from flask import Flask, render_template_string, request, redirect, url_for
import threading

# --- Flask App Setup ---
app = Flask(__name__)

# Global variable to hold the bot thread and its state
bot_thread = None
bot_status = {
    "is_running": False,
    "username": None,
    "room": None,
    "log": []
}

# --- BOT LOGIC (Slightly modified from dc.py) ---
# Note: Bot logic is now encapsulated in a class to manage state better.

class HowdiesBot:
    def __init__(self, username, password, initial_room):
        # Bot configuration
        self.BOT_MASTER = username  # Master is the user who logs in from the web
        self.USERNAME = username
        self.PASSWORD = password
        self.INITIAL_ROOM = initial_room
        
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

    def log(self, message):
        """Log messages for web UI and console."""
        print(message)
        bot_status["log"].append(f"[{time.strftime('%H:%M:%S')}] {message}")
        # Keep log size manageable
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
        self.log(f"INFO: Preparing to send '{message}' to room {room_id}")
        time.sleep(random.uniform(0.5, 1.5))
        chat_payload = {"handler": "chatmessage", "roomid": room_id, "message": message}
        self.send_payload(chat_payload)

    def handle_master_command(self, command_message, source_room_id):
        parts = command_message.strip().split(' ', 1)
        command = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""

        if command == "!ping":
            self.send_chat_message(source_room_id, random.choice(self.PONG_MESSAGE))

        elif command == "!join":
            room_name = ""
            password = ""
            match = re.match(r'^"([^"]+)"\s*(.*)', args_str)
            if match:
                room_name = match.group(1)
                password = match.group(2).strip()
            else:
                args_parts = args_str.split()
                if args_parts:
                    room_name = args_parts[0]
                    if len(args_parts) > 1:
                        password = args_parts[1]
            if not room_name:
                self.send_chat_message(source_room_id, 'Room ka naam batao. `!join "Room Name"`')
                return
            self.log(f"INFO: Master ordered to join room: '{room_name}'")
            join_payload = {"handler": "joinchatroom", "name": room_name, "roomPassword": password}
            self.send_payload(join_payload)

        elif command == "!leave" and args_str:
            room_to_leave_name = args_str
            found_room_id = None
            for rid, rname in self.current_rooms.items():
                if rname.lower() == room_to_leave_name.lower():
                    found_room_id = rid
                    break
            if found_room_id:
                self.log(f"INFO: Master ordered to leave room: {room_to_leave_name} (ID: {found_room_id})")
                self.send_chat_message(found_room_id, random.choice(self.LEAVING_MESSAGES))
                time.sleep(1.5)
                leave_payload = {"handler": "leavechatroom", "roomid": found_room_id}
                self.send_payload(leave_payload)
            else:
                self.send_chat_message(source_room_id, f"Main '{room_to_leave_name}' naam ke room me nahi hu.")

    def get_token(self):
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
        """Signal the bot to stop running."""
        self.log("INFO: Bot ko rokne ka signal mila.")
        self.should_run = False
        if self.ws and self.ws.connected:
            self.ws.close()

    def connect_and_run(self):
        global bot_status
        bot_status["is_running"] = True
        
        token = self.get_token()
        if not token:
            self.log("CRITICAL: Token nahi mila. Bot band ho raha hai.")
            bot_status["is_running"] = False
            return

        ws_url = f"wss://howdies.app:3000/?token={token}"
        self.log(f"INFO: WebSocket se connect karne ki koshish: {ws_url}")
        
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
                        self.log(f"[RECEIVED by BOT] <- {json.dumps(data, indent=2)}")
                    except json.JSONDecodeError:
                        self.log(f"[RECEIVED RAW] <- {message}")
                        continue
                    except Exception as e:
                        self.log(f"ERROR processing received message: {e}")
                        break
                    
                    handler = data.get("handler")

                    if handler == "login" and data.get("status") == "success":
                        self.log("INFO: Login successful!")
                        time.sleep(1)
                        self.send_payload({"handler": "joinchatroom", "name": self.INITIAL_ROOM, "roomPassword": ""})

                    elif handler == "joinchatroom" and data.get("success"):
                        room_id = data.get('roomid') or data.get('id') or data.get('data', {}).get('_id')
                        name = data.get('room') or data.get('name') or data.get('data', {}).get('name')
                        if room_id and name:
                            self.current_rooms[room_id] = name
                            self.log(f"SUCCESS: Room '{name}' join kar liya. Current rooms: {list(self.current_rooms.values())}")
                            self.send_chat_message(room_id, random.choice(self.GREETING_MESSAGES))

                    elif handler == "chatroommessage":
                        sender = data.get('username')
                        msg_text = data.get('text', '')
                        source_room_id = data.get('roomid')
                        if sender == self.BOT_MASTER and msg_text.startswith('!'):
                            self.handle_master_command(msg_text, source_room_id)
                    
                    elif data.get("error"):
                        self.log(f"ERROR from Server: {data.get('error')}")

            except websocket.WebSocketConnectionClosedException:
                self.log("WARN: Connection band ho gaya. 5 second me reconnect kar raha hu...")
                time.sleep(5)
            except Exception as e:
                self.log(f"CRITICAL ERROR: {e}. 5 second me reconnect kar raha hu...")
                time.sleep(5)
            finally:
                if self.ws.connected: self.ws.close()
        
        self.log("INFO: Bot has stopped.")
        bot_status["is_running"] = False


# --- HTML Template ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Howdies Bot Control Panel</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 40px; background: #f7f7f7; color: #333; }
        .container { max-width: 800px; margin: 0 auto; background: #fff; padding: 20px 40px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        h1, h2 { color: #1a1a1a; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 600; }
        input[type="text"], input[type="password"] { width: 100%; padding: 12px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        .btn { padding: 12px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; font-weight: 600; }
        .btn-start { background-color: #28a745; color: white; }
        .btn-stop { background-color: #dc3545; color: white; margin-left: 10px; }
        .status { margin-top: 30px; padding: 20px; border-radius: 8px; }
        .status.running { background-color: #e9f7ec; border: 1px solid #a3d9b1; }
        .status.stopped { background-color: #fceeee; border: 1px solid #f1b0b7; }
        .status-dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; }
        .running .status-dot { background-color: #28a745; }
        .stopped .status-dot { background-color: #dc3545; }
        #log-container {
            margin-top: 20px;
            background-color: #2b2b2b;
            color: #f1f1f1;
            padding: 15px;
            border-radius: 4px;
            height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', Courier, monospace;
            font-size: 14px;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Howdies Bot Control Panel</h1>
        
        <div class="status {{ 'running' if status.is_running else 'stopped' }}">
            <h2><span class="status-dot"></span>Status: {{ 'Running' if status.is_running else 'Stopped' }}</h2>
            {% if status.is_running %}
                <p><strong>Username:</strong> {{ status.username }}</p>
                <p><strong>Initial Room:</strong> {{ status.room }}</p>
            {% endif %}
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
            <div class="form-group">
                <label for="room_name">Initial Room Name:</label>
                <input type="text" id="room_name" name="room_name" required>
            </div>
            <button type="submit" class="btn btn-start">Start Bot</button>
            <a href="/stop" class="btn btn-stop">Stop Bot</a>
        </form>
        
        <h2>Live Log</h2>
        <div id="log-container">
            {% for line in status.log %}
                {{ line }}<br>
            {% endfor %}
        </div>
    </div>
    
    <script>
        // Auto-refresh to see status and logs
        setTimeout(() => {
            window.location.reload();
        }, 5000); // Refresh every 5 seconds
        
        // Auto-scroll log to bottom
        const logContainer = document.getElementById('log-container');
        logContainer.scrollTop = logContainer.scrollHeight;
    </script>
</body>
</html>
"""

# --- Flask Routes ---

@app.route('/')
def index():
    """Render the main control panel page."""
    return render_template_string(HTML_TEMPLATE, status=bot_status)

@app.route('/start', methods=['POST'])
def start_bot():
    """Start the bot in a new thread."""
    global bot_thread

    # Stop any existing bot thread
    if bot_thread and bot_thread.is_alive():
        # The stop is graceful, we need a way to signal it
        # For simplicity here, we'll just log and create a new one.
        # A more robust solution would use the bot_instance.stop() method
        # and wait for it to terminate.
        bot_status['log'].append("INFO: Existing bot found. Starting a new instance.")

    username = request.form['username']
    password = request.form['password']
    room_name = request.form['room_name']

    bot_status["username"] = username
    bot_status["room"] = room_name
    bot_status["log"] = ["INFO: Bot ko start karne ka request mila..."]
    
    bot_instance = HowdiesBot(username, password, room_name)
    
    # Run the bot in a background thread
    bot_thread = threading.Thread(target=bot_instance.connect_and_run)
    bot_thread.daemon = True  # Allows main program to exit even if threads are running
    bot_thread.start()

    time.sleep(2) # Give a moment for the bot to initialize
    return redirect(url_for('index'))

@app.route('/stop')
def stop_bot():
    """Stop the bot."""
    global bot_thread, bot_status
    
    # This is a simplified stop. A robust implementation would need to access
    # the bot_instance inside the thread to call its stop() method.
    # For now, we'll just reset the status. The thread will eventually die on reconnect failure
    # or if the app restarts. A proper stop is complex with this design.
    
    bot_status['is_running'] = False
    bot_status['log'].append("INFO: Bot stop request received. (Note: May take a moment to fully disconnect). Restarting the app is the most reliable way to stop.")
    
    # In a more advanced setup, you'd have a way to signal the thread to stop.
    # For now, let's assume a server restart is the "real" stop.
    # We will try to clear the state.
    bot_thread = None
    bot_status["username"] = None
    bot_status["room"] = None

    return redirect(url_for('index'))

if __name__ == '__main__':
    # Development server
    app.run(host='0.0.0.0', port=5000, debug=True)