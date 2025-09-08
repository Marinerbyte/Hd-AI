# ==============================================================================
# Render Setup Information
# ==============================================================================
#
# 1. requirements.txt File:
#    Render me 'Build Command' ke liye aapko in libraries ki zaroorat padegi.
#    Aap Render dashboard me Build Command set kar sakte hain:
#    pip install Flask Flask-SocketIO requests websocket-client gunicorn gevent gevent-websocket
#
# 2. Start Command:
#    Render me 'Start Command' yeh set karein:
#    gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 app:app
#
# ==============================================================================

import json
import websocket
import time
import requests
import ssl
import threading
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# --- HTML, CSS, JavaScript Frontend (Sab ek saath) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale-1.0">
    <title>Howdies Web Bot</title>
    <style>
        :root {
            --bg-color: #1e1e1e; --panel-bg: #2d2d2d; --border-color: #444;
            --text-color: #d4d4d4; --accent-color: #0e639c; --accent-hover: #1177bb;
            --header-color: #cccccc; --error-color: #f44747; --success-color: #47f484;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background-color: var(--bg-color); color: var(--text-color); }
        .hidden { display: none !important; }
        #login-view { display: flex; align-items: center; justify-content: center; height: 100vh; }
        .login-box { background-color: var(--panel-bg); padding: 40px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); text-align: center; }
        .login-box h1 { margin-top: 0; color: var(--header-color); }
        .login-box input { display: block; width: 250px; padding: 12px; margin-bottom: 15px; border: 1px solid var(--border-color); background-color: var(--bg-color); color: var(--text-color); border-radius: 4px; }
        .login-box button { width: 100%; padding: 12px; background-color: var(--accent-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; transition: background-color 0.2s; }
        .login-box button:hover { background-color: var(--accent-hover); }
        #login-error { color: var(--error-color); margin-top: 15px; min-height: 20px; }
        #chat-view { display: flex; height: 100vh; }
        .sidebar { width: 280px; background-color: var(--panel-bg); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; }
        .sidebar-header { padding: 20px; border-bottom: 1px solid var(--border-color); }
        .sidebar-header h2 { margin: 0; color: var(--header-color); }
        #room-list { flex-grow: 1; overflow-y: auto; }
        .room-item { padding: 15px 20px; cursor: pointer; border-bottom: 1px solid var(--border-color); transition: background-color 0.2s; }
        .room-item:hover, .room-item.active { background-color: var(--accent-color); }
        .join-form { padding: 20px; border-top: 1px solid var(--border-color); }
        .join-form input { width: calc(100% - 22px); }
        .main-chat { flex-grow: 1; display: flex; flex-direction: column; }
        .chat-header { padding: 20px; border-bottom: 1px solid var(--border-color); background-color: var(--panel-bg); }
        .chat-header h2 { margin: 0; color: var(--header-color); }
        #chat-messages { flex-grow: 1; padding: 20px; overflow-y: auto; }
        .message { margin-bottom: 15px; }
        .message-sender { font-weight: bold; color: var(--accent-color); margin-bottom: 4px; }
        .message-text { white-space: pre-wrap; word-wrap: break-word; }
        .message.system { color: var(--success-color); font-style: italic; }
        .message-form { padding: 20px; border-top: 1px solid var(--border-color); background-color: var(--panel-bg); display: flex; }
        #message-input { flex-grow: 1; margin-right: 10px; }
    </style>
</head>
<body>
    <div id="login-view">
        <div class="login-box">
            <h1>Howdies Bot Login</h1>
            <input type="text" id="username" placeholder="Username">
            <input type="password" id="password" placeholder="Password">
            <button id="login-button">Login</button>
            <p id="login-error"></p>
        </div>
    </div>
    <div id="chat-view" class="hidden">
        <div class="sidebar">
            <div class="sidebar-header"><h2>Rooms</h2></div>
            <div id="room-list"></div>
            <div class="join-form">
                <input type="text" id="join-room-name" placeholder="Join room (e.g. &quot;Avatar Chat&quot;)">
                <button onclick="joinRoom()">Join</button>
            </div>
        </div>
        <div class="main-chat">
            <div class="chat-header"><h2 id="current-room-name">Select a Room</h2></div>
            <div id="chat-messages"></div>
            <form class="message-form" id="message-form" action="javascript:void(0);">
                <input type="text" id="message-input" placeholder="Type a message..." autocomplete="off" disabled>
                <button type="submit" disabled>Send</button>
            </form>
        </div>
    </div>
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const socket = io();
            const loginView = document.getElementById('login-view');
            const chatView = document.getElementById('chat-view');
            const usernameInput = document.getElementById('username');
            const passwordInput = document.getElementById('password');
            const loginButton = document.getElementById('login-button');
            const loginError = document.getElementById('login-error');
            const roomList = document.getElementById('room-list');
            const currentRoomName = document.getElementById('current-room-name');
            const chatMessages = document.getElementById('chat-messages');
            const messageForm = document.getElementById('message-form');
            const messageInput = document.getElementById('message-input');
            const messageButton = messageForm.querySelector('button');
            let currentRooms = {}, chatHistory = {}, activeRoomId = null;
            loginButton.addEventListener('click', () => {
                const username = usernameInput.value, password = passwordInput.value;
                if (username && password) {
                    loginButton.textContent = 'Logging in...';
                    loginButton.disabled = true;
                    socket.emit('login', { username, password });
                }
            });
            socket.on('login_success', (data) => {
                loginView.classList.add('hidden');
                chatView.style.display = 'flex';
                updateRooms(data.rooms);
                chatHistory = data.history;
                if (Object.keys(currentRooms).length > 0) activateRoom(Object.keys(currentRooms)[0]);
            });
            socket.on('login_error', (data) => {
                loginError.textContent = data.error;
                loginButton.textContent = 'Login';
                loginButton.disabled = false;
            });
            function updateRooms(rooms) {
                currentRooms = rooms;
                roomList.innerHTML = '';
                for (const [id, name] of Object.entries(rooms)) {
                    const roomItem = document.createElement('div');
                    roomItem.className = 'room-item';
                    roomItem.textContent = name;
                    roomItem.dataset.roomId = id;
                    if (id == activeRoomId) roomItem.classList.add('active');
                    roomList.appendChild(roomItem);
                }
            }
            function activateRoom(roomId) {
                activeRoomId = roomId;
                document.querySelectorAll('.room-item').forEach(item => item.classList.toggle('active', item.dataset.roomId == roomId));
                currentRoomName.textContent = currentRooms[roomId] || 'Unknown Room';
                messageInput.disabled = false;
                messageButton.disabled = false;
                chatMessages.innerHTML = '';
                (chatHistory[roomId] || []).forEach(appendMessage);
            }
            function appendMessage(msg) {
                if (!chatHistory[msg.roomid]) chatHistory[msg.roomid] = [];
                if (!chatHistory[msg.roomid].some(m => m.id === msg.id)) chatHistory[msg.roomid].push(msg);
                if (msg.roomid == activeRoomId) {
                    const msgDiv = document.createElement('div');
                    msgDiv.className = 'message';
                    const senderDiv = document.createElement('div');
                    senderDiv.className = 'message-sender';
                    senderDiv.textContent = msg.username || 'System';
                    const textDiv = document.createElement('div');
                    textDiv.className = 'message-text';
                    textDiv.textContent = msg.text || JSON.stringify(msg);
                    msgDiv.appendChild(senderDiv);
                    msgDiv.appendChild(textDiv);
                    chatMessages.appendChild(msgDiv);
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                }
            }
            window.joinRoom = () => {
                const roomName = document.getElementById('join-room-name').value;
                if (roomName) socket.emit('join_room', { name: roomName });
                document.getElementById('join-room-name').value = '';
            };
            messageForm.addEventListener('submit', () => {
                const message = messageInput.value;
                if (message && activeRoomId) {
                    socket.emit('send_message', { room_id: activeRoomId, message });
                    messageInput.value = '';
                }
            });
            roomList.addEventListener('click', (e) => {
                if (e.target && e.target.classList.contains('room-item')) activateRoom(e.target.dataset.roomId);
            });
            socket.on('update_rooms', (data) => updateRooms(data.rooms));
            socket.on('new_message', (msg) => appendMessage(msg));
        });
    </script>
</body>
</html>
"""

# --- Python Flask Backend ---

app = Flask(__name__)
# Koi secret key nahi
socketio = SocketIO(app, async_mode='gevent')

# Global bot state
bot_thread = None
howdies_ws = None
current_rooms = {}
chat_history = {}

HEADERS = {
    "User-Agent": "Howdies/1.0.0 (Linux; Android 12; Pixel 5) Dalvik/2.1.0",
    "Accept": "application/json", "Content-Type": "application/json", "X-Platform": "Android"
}

def run_bot(username, password, sio):
    global howdies_ws, current_rooms, chat_history

    def get_token(user, pwd):
        url = "https://api.howdies.app/api/login"
        payload = {"username": user, "password": pwd}
        try:
            response = requests.post(url, headers=HEADERS, json=payload, timeout=10)
            if response.status_code == 200: return response.json().get("token")
            sio.emit('login_error', {'error': f'Login Failed (HTTP {response.status_code})'})
            return None
        except Exception as e:
            sio.emit('login_error', {'error': f'Network error: {e}'})
            return None

    token = get_token(username, password)
    if not token: return

    sio.emit('login_success', {'rooms': current_rooms, 'history': chat_history})
    ws_url = f"wss://howdies.app:3000/?token={token}"

    while True:
        try:
            ws = websocket.WebSocket()
            ws.connect(ws_url, header={"User-Agent": HEADERS["User-Agent"]}, sslopt={"cert_reqs": ssl.CERT_NONE})
            howdies_ws = ws
            ws.send(json.dumps({"handler": "login", "username": username, "password": password}))
            ws.send(json.dumps({"handler": "joinchatroom", "name": "life", "roomPassword": ""}))

            while True:
                message = ws.recv()
                data = json.loads(message)
                handler, room_id = data.get("handler"), data.get('roomid')
                if room_id and room_id not in chat_history: chat_history[room_id] = []
                if handler == "joinchatroom" and data.get("success"):
                    room_id, name = data.get('roomid'), data.get('room')
                    if room_id and name:
                        current_rooms[room_id] = name
                        sio.emit('update_rooms', {'rooms': current_rooms})
                elif handler == "chatroommessage":
                    chat_history[room_id].append(data)
                    sio.emit('new_message', data)
                else:
                    sio.emit('new_message', {'roomid': room_id or 'global', 'username': 'System', 'text': json.dumps(data)})

        except Exception as e:
            howdies_ws = None
            sio.emit('new_message', {'roomid': 'global', 'username': 'System', 'text': f'Connection lost: {e}. Reconnecting...'})
            time.sleep(5)

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('login')
def handle_login(data):
    global bot_thread, current_rooms, chat_history
    # Agar pehle se koi bot chal raha hai, to use chhod do. Naya login naya bot banayega.
    # Production me isko aaur behtar handle karna padega, par abhi ke liye yeh theek hai.
    current_rooms, chat_history = {}, {}
    username, password = data.get('username'), data.get('password')
    bot_thread = threading.Thread(target=run_bot, args=(username, password, socketio))
    bot_thread.daemon = True
    bot_thread.start()

@socketio.on('join_room')
def handle_join_room(data):
    if howdies_ws:
        payload = {"handler": "joinchatroom", "name": data.get('name', ''), "roomPassword": ""}
        howdies_ws.send(json.dumps(payload))

@socketio.on('send_message')
def handle_send_message(data):
    if howdies_ws:
        payload = {"handler": "chatmessage", "roomid": data.get('room_id'), "message": data.get('message')}
        howdies_ws.send(json.dumps(payload))

# Yeh block Render par istemal nahi hoga, woh seedha 'app' object dhoondega.
# Local testing ke liye aap 'python app.py' chala sakte hain.
if __name__ == '__main__':
    print("Local web server starting... Open your browser to http://127.0.0.1:5000")
    # Local me chalane ke liye 'gevent' ki zaroorat nahi
    socketio.run(app, host='127.0.0.1', port=5000)