# app.py
import json
import websocket
import time
import random
import requests
import ssl
import re
from flask import Flask, render_template_string, request, redirect, url_for, flash
from threading import Thread

# --- Flask App Configuration ---
app = Flask(__name__)
# Session management ke liye ek secret key zaroori hai
app.secret_key = 'your_very_secret_key_12345' 

# --- Bot's Core Logic (Aapke dc.py se liya gaya) ---
# Global variable bot ko control karne ke liye
bot_thread = None
ws_connection = None

BOT_MASTER = "yasin" # Isko aap web form se bhi le sakte hain, abhi ke liye hardcoded hai

# Static lists
GREETING_MESSAGES = ["aa gaya main", "hello dosto", f"master ({BOT_MASTER}) ne bulaya aur hum chale aaye", "kya haal hai?"]
LEAVING_MESSAGES = ["chalo bye", "nikalta hu", "master ka agla order aa gaya", "bye guys"]
PONG_MESSAGE = ["pong!", "ji master?", "order master!"]

# Bot ke current rooms ko track karne ke liye
current_rooms = {}

# --- Helper Functions (dc.py se) ---
def send_payload(ws, payload_dict):
    if ws and ws.connected:
        try:
            payload_str = json.dumps(payload_dict)
            print(f"SENT by BOT: {payload_str}")
            ws.send(payload_str)
        except Exception as e:
            print(f"Error sending payload: {e}")

def send_chat_message(ws, room_id, message):
    print(f"INFO: Preparing to send message '{message}' to room {room_id}")
    time.sleep(random.uniform(0.5, 1.5))
    chat_payload = {"handler": "chatmessage", "roomid": room_id, "message": message}
    send_payload(ws, chat_payload)

# --- Master Commands (dc.py se) ---
def handle_master_command(ws, command_message, source_room_id):
    parts = command_message.strip().split(' ', 1)
    command = parts[0].lower()
    args_str = parts[1] if len(parts) > 1 else ""

    if command == "!ping":
        send_chat_message(ws, source_room_id, random.choice(PONG_MESSAGE))
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
            send_chat_message(ws, source_room_id, 'Join karne ke liye room ka naam to batao. `!join "Room Name"`')
            return
        join_payload = {"handler": "joinchatroom", "name": room_name, "roomPassword": password}
        send_payload(ws, join_payload)
    elif command == "!leave" and args_str:
        room_to_leave_name = args_str
        found_room_id = next((rid for rid, rname in current_rooms.items() if rname.lower() == room_to_leave_name.lower()), None)
        if found_room_id:
            send_chat_message(ws, found_room_id, random.choice(LEAVING_MESSAGES))
            time.sleep(1.5)
            leave_payload = {"handler": "leavechatroom", "roomid": found_room_id}
            send_payload(ws, leave_payload)
            current_rooms.pop(found_room_id, None) # Room list se hatayein
        else:
            send_chat_message(ws, source_room_id, f"Main '{room_to_leave_name}' naam ke kisi room me nahi hu.")

# --- Background WebSocket Task ---
def run_bot_logic(username, password, initial_room):
    global ws_connection, current_rooms
    
    # API se token lene ki koshish
    url = "https://api.howdies.app/api/login"
    payload = {"username": username, "password": password}
    headers = {
        "User-Agent": "Howdies/1.0.0 (Linux; Android 12; Pixel 5) Dalvik/2.1.0",
        "Accept": "application/json", "Content-Type": "application/json", "X-Platform": "Android"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            token = response.json().get("token")
            print("INFO: Token mil gaya.")
        else:
            print(f"ERROR: Token nahi mila. Status: {response.status_code}, Response: {response.text}")
            return
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Token lene me network/request exception: {e}")
        return

    ws_url = f"wss://howdies.app:3000/?token={token}"
    ws_connection = websocket.WebSocket()
    
    try:
        ws_connection.connect(ws_url, header={"User-Agent": headers["User-Agent"]}, sslopt={"cert_reqs": ssl.CERT_NONE})
        print("INFO: WebSocket se connect ho gaya!")
        
        # Login payload
        send_payload(ws_connection, {"handler": "login", "username": username, "password": password})

        while ws_connection.connected:
            message = ws_connection.recv()
            data = json.loads(message)
            print(f"RECEIVED by BOT: {json.dumps(data, indent=2)}")

            handler = data.get("handler")
            if handler == "login" and data.get("status") == "success":
                print("INFO: Login successful!")
                time.sleep(1)
                # Web form se mila initial room join karein
                send_payload(ws_connection, {"handler": "joinchatroom", "name": initial_room, "roomPassword": ""})

            elif handler == "joinchatroom" and data.get("success"):
                room_id = data.get('roomid') or data.get('id') or data.get('data', {}).get('_id')
                name = data.get('room') or data.get('name') or data.get('data', {}).get('name')
                if room_id and name:
                    current_rooms[room_id] = name
                    print(f"SUCCESS: Room '{name}' join kar liya. Current rooms: {list(current_rooms.values())}")
                    send_chat_message(ws_connection, room_id, random.choice(GREETING_MESSAGES))

            elif handler == "chatroommessage":
                if data.get('username') == BOT_MASTER and data.get('text', '').startswith('!'):
                    handle_master_command(ws_connection, data['text'], data['roomid'])
            
            elif data.get("error"):
                print(f"ERROR from Server: {data.get('error')}")

    except Exception as e:
        print(f"CRITICAL ERROR in bot logic: {e}")
    finally:
        if ws_connection and ws_connection.connected:
            ws_connection.close()
        print("INFO: Bot connection closed.")
        ws_connection = None
        current_rooms = {}

# --- Flask Web Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    global bot_thread, ws_connection

    if request.method == 'POST':
        # Agar bot pehle se chal raha hai, to usko rokne ki koshish karein
        if bot_thread and bot_thread.is_alive():
            flash("Bot pehle se hi chal raha hai. Stop karne ke liye /stop use karein.", "warning")
            return redirect(url_for('index'))

        username = request.form.get('username')
        password = request.form.get('password')
        room_name = request.form.get('room_name')

        if not all([username, password, room_name]):
            flash("Username, Password, aur Room Name, teeno zaroori hain!", "danger")
            return redirect(url_for('index'))

        # Background thread mein bot ko start karein
        bot_thread = Thread(target=run_bot_logic, args=(username, password, room_name))
        bot_thread.daemon = True # Main app band hone par thread bhi band ho jaye
        bot_thread.start()
        
        flash(f"Bot ko login karne ki koshish jaari hai Username: '{username}' Room: '{room_name}'.", "success")
        return redirect(url_for('status'))

    # Bot ka current status check karein
    is_running = bot_thread is not None and bot_thread.is_alive()
    
    # HTML Template
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Howdies Bot Control Panel</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { background-color: #f4f7f6; }
            .container { max-width: 500px; margin-top: 5rem; }
            .card { border: none; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
            .card-header { background-color: #007bff; color: white; text-align: center; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <div class="card-header">
                    <h4>Bot Login Panel</h4>
                </div>
                <div class="card-body">
                    {% with messages = get_flashed_messages(with_categories=true) %}
                      {% if messages %}
                        {% for category, message in messages %}
                          <div class="alert alert-{{ category }}">{{ message }}</div>
                        {% endfor %}
                      {% endif %}
                    {% endwith %}

                    {% if not is_running %}
                    <form action="/" method="post">
                        <div class="mb-3">
                            <label for="username" class="form-label">Username</label>
                            <input type="text" class="form-control" id="username" name="username" required>
                        </div>
                        <div class="mb-3">
                            <label for="password" class="form-label">Password</label>
                            <input type="password" class="form-control" id="password" name="password" required>
                        </div>
                        <div class="mb-3">
                            <label for="room_name" class="form-label">Room Name</label>
                            <input type="text" class="form-control" id="room_name" name="room_name" required>
                        </div>
                        <div class="d-grid">
                            <button type="submit" class="btn btn-primary">Login & Start Bot</button>
                        </div>
                    </form>
                    {% else %}
                    <div class="alert alert-success">
                        Bot is currently running. Check status for more details.
                    </div>
                    <a href="{{ url_for('status') }}" class="btn btn-info w-100 mb-2">Check Status</a>
                    <a href="{{ url_for('stop_bot') }}" class="btn btn-danger w-100">Stop Bot</a>
                    {% endif %}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html_template, is_running=is_running)

@app.route('/status')
def status():
    if bot_thread and bot_thread.is_alive():
        # Yahan aap bot ke baare mein aur jaankari de sakte hain
        rooms_joined = ", ".join(current_rooms.values()) or "None yet"
        return f"<h1>Bot Status: Running</h1><p>Rooms Joined: {rooms_joined}</p><a href='/'>Go Back</a>"
    else:
        return "<h1>Bot Status: Not Running</h1><a href='/'>Go Back</a>"

@app.route('/stop')
def stop_bot():
    global bot_thread, ws_connection
    if ws_connection and ws_connection.connected:
        ws_connection.close() # WebSocket connection band karein
    bot_thread = None # Thread ko reset karein
    flash("Bot has been stopped.", "info")
    return redirect(url_for('index'))

# --- Main execution ---
if __name__ == "__main__":
    # Development ke liye, ise aise hi chalayein.
    # Production (Render) ke liye, gunicorn iska istemal karega.
    app.run(host='0.0.0.0', port=5000)```

### Render par Deploy kaise karein?

1.  **GitHub Repository Banayein:** Apne code ko GitHub par ek new repository mein push karein. Is repository mein `app.py` aur `requirements.txt` dono files honi chahiye.

2.  **Render par Account Banayein:** [Render.com](https://render.com/) par sign up karein aur apne GitHub account ko connect karein.

3.  **New Web Service Banayein:**
    *   Render dashboard par "New +" par click karein aur "Web Service" chunein.
    *   Apni GitHub repository ko select karein.
    *   Render aapki settings ko automatically detect kar lega:
        *   **Name:** Apne app ka ek naam dein (jaise `howdies-bot`).
        *   **Root Directory:** Khali rehne dein agar files root mein hain.
        *   **Runtime:** `Python 3`.
        *   **Build Command:** `pip install -r requirements.txt`. Yah `requirements.txt` file ko dekh kar automatically set ho jayega.
        *   **Start Command:** `gunicorn app:app`. `gunicorn` `app.py` file ke andar `app` naam ke Flask instance ko dhundega aur chalayega.

4.  **Create Web Service par Click karein:** Ab "Create Web Service" button par click karein. Render aapki application ko deploy karna shuru kar dega. Ismein kuch minutes lag sakte hain.

5.  **Live URL:** Deployment poora hone ke baad, Render aapko ek live URL dega (jaise `https://howdies-bot.onrender.com`). Is URL par jaakar aap apne bot ko web interface se control kar sakte hain.

Ab aapke paas ek web application hai jo aapke CLI bot ki tarah hi kaam karta hai, lekin use koi bhi browser se control kar sakta hai.