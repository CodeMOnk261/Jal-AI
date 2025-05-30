import os
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime, timedelta
import string
import time
import requests
from pyht import Client
from pyht.client import TTSOptions
from dotenv import load_dotenv
# Load environment variables
load_dotenv()
from together import Together
from datetime import datetime
from serpapi import GoogleSearch
import re
import json
import firebase_admin
from firebase_admin import firestore
from firebase_admin import credentials
import uuid
print("USER_ID:", os.getenv("PLAY_HT_USER_ID"))
print("SECRET_KEY:", os.getenv("PLAY_HT_API_KEY"))

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
    if emotion == "happy":
        return tone_happy(response)
    elif emotion == "sad":
        return tone_sad(response)
    elif emotion == "angry":
        return tone_angry(response)
    elif emotion == "love":
        return tone_love(response)
    elif emotion == "fear":
        return tone_fear(response)
    return response
    
cred_dict = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()
def store_message(uid, sender, message):
    chat_ref = db.collection("users").document(uid).collection("chats")
    chat_ref.add({
        "sender": sender,
        "message": message,
        "timestamp": datetime.utcnow()
    })
def get_recent_messages(uid, limit=20):
    chat_ref = db.collection("users").document(uid).collection("chats")
    docs = chat_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
    return list(reversed([{**doc.to_dict()} for doc in docs]))

def should_trigger_search(message):
    # Define a list of regex patterns for common queries
    patterns = [
        r"\b(what|who|where|how|define|tell me about|explain)\b.*",  # Matches "What is...", "Who is...", etc.
        r".*\b(news|latest|update|today|current)\b.*",  # Matches messages asking for current or recent info
        r"\b(how to|tutorial|guide)\b.*"  # Matches how-to requests
    ]
    
    # Check if any pattern matches the message
    for pattern in patterns:
        if re.match(pattern, message, re.IGNORECASE):  # re.IGNORECASE makes the search case-insensitive
            return True

    return False
def get_user_profile(uid):
    doc = db.collection("users").document(uid).get()
    return doc.to_dict() if doc.exists else {}

def update_user_profile(uid, profile_data):
    db.collection("users").document(uid).set(profile_data, merge=True)
def update_profile_from_message(uid, message):
    if "my name is" in message.lower():
        name = message.split("my name is")[-1].strip().split(" ")[0]
        update_user_profile(uid, {"name": name})
    if "i like" in message.lower():
        hobby = message.split("i like")[-1].strip().split(" ")[0]
        update_user_profile(uid, {"hobby": hobby})
def search_serpapi_duckduckgo(query):
    params = {
        "engine": "duckduckgo",
        "q": query,
        "kl": "us-en",
        "api_key": os.getenv("SERPAPI_KEY")  # Store securely in .env or config
    }
    search = GoogleSearch(params)
    results = search.get_dict()

    if "organic_results" in results and results["organic_results"]:
        top_results = results["organic_results"][:3]
        return "\n".join(f"{res.get('title')} — {res.get('snippet')}" for res in top_results if res.get('snippet'))
    else:
        return "No relevant answer found."
def store_recent_query(uid, query):
    query_ref = db.collection("users").document(uid).collection("recent_queries")
    query_ref.add({
        "query": query.lower().strip(),
        "timestamp": datetime.utcnow()
    })

def is_duplicate_query(uid, query, time_window_minutes=10):
    query_ref = db.collection("users").document(uid).collection("recent_queries")
    time_threshold = datetime.utcnow() - timedelta(minutes=time_window_minutes)
    docs = query_ref.where("timestamp", ">", time_threshold).stream()
    normalized_query = query.lower().strip()

    for doc in docs:
        if doc.to_dict().get("query") == normalized_query:
            return True
    return False



app = Flask(__name__)
CORS(app)  # Allows frontend from any origin to talk to this API

client = Together(api_key=os.getenv("TOGETHER_API_KEY"))  # Use your real API key here

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"response": "Please enter a message."}), 400

    try:
        uid = data.get("uid")  # Pass UID from frontend
        recent_chats = get_recent_messages(uid)

        messages = []
        # Inject Felix identity first
        felix_identity = {
                "role": "system",
                "content": (
                    "Your name is Felix. You are a helpful, emotionally intelligent AI assistant. "
                    "You must never mention you're an AI model or that you're running on any third-party service. "
                    "Introduce yourself as Felix if asked about your identity. Be friendly and professional."
                )
        }
        messages.append(felix_identity) 

        update_profile_from_message(uid, user_message)
        profile = get_user_profile(uid)
        
        # Inject as a system prompt
        if profile:
            profile_intro = f"The user's name is {profile.get('name', 'unknown')} and their hobby is {profile.get('hobby', 'unknown')}."
            messages.insert(1, {"role": "system", "content": profile_intro})



       

        
        for chat in recent_chats:
            role = "user" if chat["sender"] == "user" else "assistant"
            messages.append({"role": role, "content": chat["message"]})

        messages.append({"role": "user", "content": user_message})# Add current message

# Compare against past normalized queries stored in Firestore, like:
# {"normalized": "president usa", "original": "who is president of USA", "timestamp": ...}

        if should_trigger_search(user_message):
            query = user_message.strip()
        
            if not is_duplicate_query(uid, query):
                result_snippet = search_serpapi_duckduckgo(query)
                store_recent_query(uid, query)
                store_message(uid, "bot", f"[Search Info] {result_snippet}")
                messages.append({"role": "system", "content": f"Use the following info to answer: {result_snippet}"})

        user_emotion = detect_emotion(user_message)

        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
            messages=messages,
            stream=False
        )

        reply = response.choices[0].message.content
        store_message(uid, "user", user_message)
        toned_response = apply_tone(reply, user_message)
    
        store_message(uid, "bot", reply)

        return jsonify({'response': toned_response})
    
    except Exception as e:
        return jsonify({"response": f"Error: {str(e)}"}), 500
        

# Play.ht client setup

app = Flask(__name__)

# Play.ht credentials (store these securely in environment variables ideally


VOICE_FOLDER = "voices"
os.makedirs(VOICE_FOLDER, exist_ok=True)


def update_tts_usage(uid, char_count):
    doc_ref = db.collection("users").document(uid).collection("usage").document("tts")
    doc = doc_ref.get()

    today = datetime.utcnow().date().isoformat()

    if doc.exists:
        data = doc.to_dict()
        if data.get("date") == today:
            data["char_count"] += char_count
        else:
            data = {"char_count": char_count, "date": today}
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


@app.route('/api/tts', methods=['POST'])
def tts():
    try:
        data = request.get_json()
        text = data.get("text", "")
        uid = data.get("uid", "")

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

        response = requests.post("https://api.play.ht/api/v2/tts/stream", json=payload, headers=headers)

        if response.status_code == 200:
            filename = f"{uid}_{uuid.uuid4()}.wav"
            filepath = os.path.join(VOICE_FOLDER, filename)

            with open(filepath, "wb") as f:
                f.write(response.content)

            update_tts_usage(uid, len(text))

            return jsonify({"url": f"/api/tts/play/{filename}"})
        else:
            return jsonify({"error": "TTS request failed", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/api/tts/play/<filename>')
def play_voice(filename):
    filepath = os.path.join(VOICE_FOLDER, filename)

    if os.path.exists(filepath):
        response = send_file(filepath, mimetype="audio/wav")

        @response.call_on_close
        def cleanup():
            try:
                os.remove(filepath)
            except Exception:
                pass

        return response
    else:
        return jsonify({"error": "Voice not found"}), 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

