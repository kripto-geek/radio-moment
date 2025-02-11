import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from mutagen.mp3 import MP3
from collections import deque

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_123'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB limit
socketio = SocketIO(app, cors_allowed_origins="*")
limiter = Limiter(app=app, key_func=get_remote_address)

# Data structures
queue = deque()
current_song = None
listeners = set()  # Track active listeners by Socket.IO SID

def sanitize_song(song):
    """Convert sets to lists for JSON serialization"""
    if song:
        sanitized = song.copy()
        sanitized['listeners'] = list(sanitized.get('listeners', []))
        return sanitized
    return None

@app.route('/upload', methods=['POST'])
@limiter.limit("1 per 5 minutes")
def upload():
    global current_song

    if 'file' not in request.files:
        return jsonify(error="No file uploaded"), 400

    file = request.files['file']
    song_name = request.form.get('song_name', 'Untitled').strip() or 'Untitled'
    uploader_name = request.form.get('uploader_name', 'Anonymous').strip() or 'Anonymous'

    # Validate MP3
    if not (file.filename.endswith('.mp3') and file.mimetype == 'audio/mpeg'):
        return jsonify(error="Invalid file type"), 400

    # Save file
    filename = f"{datetime.now().timestamp()}.mp3"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # Validate MP3 using mutagen
    try:
        MP3(filepath).info  # Check if valid
    except:
        os.remove(filepath)
        return jsonify(error="Invalid MP3 file"), 400

    # Add to queue (max 10 songs)
    if len(queue) >= 10:
        return jsonify(error="Queue is full"), 400

    queue.append({
        'filename': filename,
        'song_name': song_name,
        'uploader': uploader_name,
        'likes': 0,
        'dislikes': 0,
        'listeners': []
    })

    # Start playback if queue was empty
    if not current_song and len(queue) == 1:
        next_song()

    # Broadcast queue update
    sanitized_queue = [sanitize_song(song) for song in queue]
    socketio.emit('queue_update', {'queue': sanitized_queue})

    return jsonify(success=True)

@socketio.on('connect')
def handle_connect():
    listeners.add(request.sid)
    if current_song:
        emit('current_song', sanitize_song(current_song))

@socketio.on('disconnect')
def handle_disconnect():
    listeners.discard(request.sid)

@socketio.on('vote')
def handle_vote(vote_type):
    global current_song
    if current_song and request.sid in listeners:
        if vote_type == 'like':
            current_song['likes'] += 1
        elif vote_type == 'dislike':
            current_song['dislikes'] += 1

        total = current_song['likes'] + current_song['dislikes']
        if total > 0 and (current_song['dislikes'] / total) > 0.5:
            next_song()
        else:
            socketio.emit('vote_update', sanitize_song(current_song))

def next_song():
    global current_song
    if queue:
        current_song = queue.popleft()
        current_song['listeners'] = list(listeners)
        socketio.emit('new_song', sanitize_song(current_song))
    else:
        current_song = None
        socketio.emit('player_stop')

    sanitized_queue = [sanitize_song(song) for song in queue]
    socketio.emit('queue_update', {'queue': sanitized_queue})

@app.route('/')
def home():
    return render_template('index.html',
                         current_song=sanitize_song(current_song),
                         queue=[sanitize_song(s) for s in queue])

if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    socketio.run(app, allow_unsafe_werkzeug=True, debug=True)
