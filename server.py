import os
import re
import uuid
import json
import logging
import requests
from flask import Flask, request, jsonify, send_file, make_response, after_this_request
from flask_cors import CORS
from datetime import datetime, timedelta
from dotenv import load_dotenv
from together import Together
from serpapi import GoogleSearch
import firebase_admin
from firebase_admin import firestore, credentials
from functools import lru_cache
from tenacity import retry, stop_after_attempt, wait_fixed

# Load environment variables
load_dotenv()

# Initialize Flask app and configure CORS
app = Flask(__name__)
CORS(
    app,
    origins=["https://felix-c7ba9.web.app"],
    supports_credentials=True,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"]
)

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Firebase setup
cred_dict = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Emotion setup
emotion_keywords = {
    "happy": ["happy", "joy", "excited", "yay", "cheerful", "delighted"],
    "sad": ["sad", "depressed", "unhappy", "cry", "tears", "gloomy"],
    "angry": ["angry", "mad", "furious", "irritated", "annoyed"],
    "love": ["love", "affection", "romantic", "sweetheart", "dear"],
    "fear": ["scared", "afraid", "fear", "terrified", "panic", "nervous"]
}

def tone_happy(text): return f"😊 {text} Yay!"
def tone_sad(text): return f"😢 {text} Things will get better!"
def tone_angry(text): return f"😠 {text} Try to calm down."
def tone_love(text): return f"❤️ {text} You're special!"
def tone_fear(text): return f"😨 {text} Stay strong, I'm with you."

def detect_emotion(text):
    text_lower = text.lower()
    for emotion, keywords in emotion_keywords.items():
        if any(word in text_lower for word in keywords):
            return emotion
    return "neutral"

def apply_tone(response, user_input):
    emotion = detect_emotion(user_input)
    tone_func = {
        "happy": tone_happy,
        "sad": tone_sad,
        "angry": tone_angry,
        "love": tone_love,
        "fear": tone_fear
    }.get(emotion, lambda x: x)
    return tone_func(response)

def store_message(uid, sender, message):
    chat_ref = db.collection("users").document(uid).collection("chats")
    chat_ref.add({"sender": sender, "message": message, "timestamp": datetime.utcnow()})

def get_recent_messages(uid, limit=20):
    chat_ref = db.collection("users").document(uid).collection("chats")
    docs = chat_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
    return list(reversed([{**doc.to_dict()} for doc in docs]))

def should_trigger_search(message):
    patterns = [r"\b(what|who|where|how|define|tell me about|explain)\b.*", r".*\b(news|latest|update|today|current)\b.*", r"\b(how to|tutorial|guide)\b.*"]
    return any(re.search(pattern, message, re.IGNORECASE) for pattern in patterns)

def get_user_profile(uid):
    doc = db.collection("users").document(uid).get()
    return doc.to_dict() if doc.exists else {}

def update_user_profile(uid, profile_data):
    db.collection("users").document(uid).set(profile_data, merge=True)

def update_profile_from_message(uid, message):
    name_match = re.search(r"my name is (\w+)", message, re.IGNORECASE)
    hobby_match = re.search(r"i like (\w+)", message, re.IGNORECASE)
    if name_match:
        update_user_profile(uid, {"name": name_match.group(1)})
    if hobby_match:
        update_user_profile(uid, {"hobby": hobby_match.group(1)})

def store_recent_query(uid, query):
    db.collection("users").document(uid).collection("recent_queries").add({"query": query.lower().strip(), "timestamp": datetime.utcnow()})

def is_duplicate_query(uid, query, time_window_minutes=10):
    time_threshold = datetime.utcnow() - timedelta(minutes=time_window_minutes)
    docs = db.collection("users").document(uid).collection("recent_queries").where("timestamp", ">", time_threshold).stream()
    normalized_query = query.lower().strip()
    return any(doc.to_dict().get("query") == normalized_query for doc in docs)

@lru_cache(maxsize=2000)
def cached_recent_query(uid, query):
    return is_duplicate_query(uid, query)

def is_rate_limited(uid, limit=20, window_sec=60):
    now = datetime.utcnow()
    threshold = now - timedelta(seconds=window_sec)
    logs = db.collection("users").document(uid).collection("rate_limit").where("timestamp", ">", threshold).stream()
    if len(list(logs)) >= limit:
        return True
    db.collection("users").document(uid).collection("rate_limit").add({"timestamp": now})
    return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def call_playht_tts(payload, headers):
    return requests.post("https://api.play.ht/api/v2/tts/stream", json=payload, headers=headers)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def call_serpapi(params):
    return GoogleSearch(params).get_dict()

client = Together(api_key=os.getenv("TOGETHER_API_KEY"))

@app.route("/", methods=["POST"])
def index():
    return jsonify({"status": "Felix backend is alive!"}), 200

@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        return build_cors_response()

    if not request.is_json:
        return jsonify({"error": "Invalid request format. JSON expected."}), 400

    data = request.get_json()
    user_message = data.get("message", "").strip()
    uid = data.get("uid")

    if not user_message:
        return jsonify({"response": "Please enter a message."}), 400

    if is_rate_limited(uid):
        return jsonify({"error": "Too many requests. Please slow down."}), 429

    recent_chats = get_recent_messages(uid)
    messages = [{"role": "system", "content": "Your name is Felix. You are a helpful, emotionally intelligent AI assistant."}]

    update_profile_from_message(uid, user_message)
    profile = get_user_profile(uid)
    if profile:
        messages.append({"role": "system", "content": f"The user's name is {profile.get('name', 'unknown')} and their hobby is {profile.get('hobby', 'unknown')}."})

    for chat in recent_chats:
        role = "user" if chat["sender"] == "user" else "assistant"
        messages.append({"role": role, "content": chat["message"]})

    messages.append({"role": "user", "content": user_message})

    if should_trigger_search(user_message) and not cached_recent_query(uid, user_message):
        params = {
            "engine": "duckduckgo",
            "q": user_message,
            "kl": "us-en",
            "api_key": os.getenv("SERPAPI_KEY")
        }
        results = call_serpapi(params)
        if "organic_results" in results:
            snippet = "\n".join(f"{r['title']} — {r['snippet']}" for r in results["organic_results"][:3] if r.get("snippet"))
            messages.append({"role": "system", "content": f"Use the following info to answer: {snippet}"})
            store_recent_query(uid, user_message)
            store_message(uid, "bot", f"[Search Info] {snippet}")

    response = client.chat.completions.create(
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        messages=messages,
        stream=False
    )

    reply = response.choices[0].message.content
    store_message(uid, "user", user_message)
    toned = apply_tone(reply, user_message)
    store_message(uid, "bot", reply)

    return jsonify({"response": toned})

VOICE_FOLDER = "voices"
os.makedirs(VOICE_FOLDER, exist_ok=True)

def update_tts_usage(uid, char_count):
    doc_ref = db.collection("users").document(uid).collection("usage").document("tts")
    today = datetime.utcnow().date().isoformat()
    doc = doc_ref.get()
    data = doc.to_dict() if doc.exists else {}
    if data.get("date") == today:
        data["char_count"] = data.get("char_count", 0) + char_count
    else:
        data = {"char_count": char_count, "date": today}
    doc_ref.set(data)

def has_exceeded_tts_limit(uid, max_chars=1000):
    doc = db.collection("users").document(uid).collection("usage").document("tts").get()
    today = datetime.utcnow().date().isoformat()
    if doc.exists:
        data = doc.to_dict()
        return data.get("date") == today and data.get("char_count", 0) >= max_chars
    return False

@app.route("/api/tts", methods=["POST", "OPTIONS"])
def tts():
    if request.method == "OPTIONS":
        return build_cors_response()

    if not request.is_json:
        return jsonify({"error": "Invalid request format. JSON expected."}), 400

    data = request.get_json()
    text = data.get("text", "").strip()
    uid = data.get("uid", "").strip()

    if not text or not uid:
        return jsonify({"error": "Text and UID are required."}), 400

    if has_exceeded_tts_limit(uid):
        return jsonify({"error": "TTS daily limit reached (1,000 characters). Try again tomorrow."}), 429

    payload = {
        "text": text,
        "voice": "s3://voice-cloning-zero-shot/d9ff78ba-d016-47f6-b0ef-dd630f59414e/female-cs/manifest.json",
        "output_format": "wav",
        "voice_engine": "PlayDialog"
    }

    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "X-User-Id": os.getenv("PLAY_HT_USER_ID"),
        "Authorization": os.getenv("PLAY_HT_API_KEY")
    }

    response = call_playht_tts(payload, headers)
    if response.status_code == 200:
        filename = f"{uid}_{uuid.uuid4()}.wav"
        filepath = os.path.join(VOICE_FOLDER, filename)
        with open(filepath, "wb") as f:
            f.write(response.content)
        update_tts_usage(uid, len(text))
        return jsonify({"url": f"/api/tts/play/{filename}"})
    else:
        return jsonify({"error": "TTS request failed", "details": response.text}), 500

@app.route("/api/tts/play/<filename>", methods=["GET"])
def play_voice(filename):
    filepath = os.path.join(VOICE_FOLDER, filename)
    if os.path.exists(filepath):
        response = send_file(filepath, mimetype="audio/wav")

        @after_this_request
        def cleanup(response):
            try:
                os.remove(filepath)
                logger.info(f"TTS file deleted: {filepath}")
            except Exception as e:
                logger.warning(f"Could not delete TTS file: {e}")
            return response

        return response
    return jsonify({"error": "Voice not found"}), 404

def build_cors_response():
    response = make_response()
    response.headers["Access-Control-Allow-Origin"] = "https://felix-c7ba9.web.app"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response, 204

@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404 Error: {request.path}")
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    logger.warning(f"405 Method Not Allowed: {request.method} {request.path}")
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception("Unhandled exception")
    return jsonify({"error": "An unexpected error occurred."}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Felix backend on port {port}")
    app.run(host="0.0.0.0", port=port)
