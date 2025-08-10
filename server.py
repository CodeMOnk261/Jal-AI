import os
import re
import json
import logging
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
import firebase_admin
from firebase_admin import firestore, credentials
from serpapi import GoogleSearch
from functools import lru_cache

# Load environment variables
load_dotenv()

# Initialize Flask
app = Flask(__name__)
CORS(app, origins=["https://felix-c7ba9.web.app", "http://localhost:5173"], supports_credentials=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Firebase init
cred_dict = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Groq client
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

# === Utility functions ===

def build_cors_response():
    origin = request.headers.get("Origin")
    allowed_origins = ["https://felix-c7ba9.web.app", "http://localhost:3000"]
    response = make_response()
    if origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response, 204

def store_message(uid, sender, message):
    chat_ref = db.collection("users").document(uid).collection("chats")
    chat_ref.add({"sender": sender, "message": message, "timestamp": datetime.utcnow()})

def get_recent_messages(uid, limit=20):
    chat_ref = db.collection("users").document(uid).collection("chats")
    docs = chat_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
    return list(reversed([{**doc.to_dict()} for doc in docs]))

def get_user_profile(uid):
    doc = db.collection("users").document(uid).get()
    return doc.to_dict() if doc.exists else {}

def should_trigger_search(message):
    patterns = [r"\b(what|who|where|how|define|tell me about|explain)\b", r"\b(news|latest|today|current)\b"]
    return any(re.search(pattern, message, re.IGNORECASE) for pattern in patterns)

def store_recent_query(uid, query):
    db.collection("users").document(uid).collection("recent_queries").add({
        "query": query.lower().strip(),
        "timestamp": datetime.utcnow()
    })

@lru_cache(maxsize=1000)
def cached_recent_query(uid, query):
    time_threshold = datetime.utcnow() - timedelta(minutes=10)
    docs = db.collection("users").document(uid).collection("recent_queries").where("timestamp", ">", time_threshold).stream()
    normalized_query = query.lower().strip()
    return any(doc.to_dict().get("query") == normalized_query for doc in docs)

def count_tokens_approx(text):
    # Simple token estimation for LLaMA models (approx. 4 chars = 1 token)
    return int(len(text) / 4)

def trim_chat_to_fit(messages, max_tokens=8192, reserve=2048):
    """Trims oldest messages to keep input tokens within limit using approx token count"""
    while True:
        total = sum(count_tokens_approx(json.dumps(m["content"])) for m in messages)
        if total + reserve <= max_tokens:
            break
        if len(messages) > 1:
            messages.pop(1)  # preserve system prompt
        else:
            break
    return messages

# === Main Chat Endpoint ===

@app.route("/", methods=["POST", "OPTIONS"])
def index_chat():
    if request.method == "OPTIONS":
        return build_cors_response()

    if request.content_type != 'application/json':
        return jsonify({"error": "Content-Type must be application/json"}), 415

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON data."}), 400

    uid = data.get("uid", "").strip()
    user_message = data.get("message", "").strip()

    if not uid or not user_message:
        return jsonify({"response": "Please provide a message and UID."}), 400

    # Build prompt
    messages = [{"role": "system", "content": "Your name is Felix. You are an intelligent, thoughtful assistant who responds with clarity, empathy, and helpfulness."}]
    profile = get_user_profile(uid)
    if profile:
        name = profile.get("name", "user")
        messages.append({"role": "system", "content": f"The user’s name is {name}. Be respectful and informative."})

    for chat in get_recent_messages(uid):
        messages.append({
            "role": "user" if chat["sender"] == "user" else "assistant",
            "content": chat["message"]
        })

    messages.append({"role": "user", "content": user_message})

    # DuckDuckGo search if needed
    if should_trigger_search(user_message) and not cached_recent_query(uid, user_message):
        try:
            params = {
                "engine": "duckduckgo",
                "q": user_message,
                "kl": "us-en",
                "api_key": os.getenv("SERPAPI_KEY")
            }
            results = GoogleSearch(params).get_dict()
            if "organic_results" in results:
                snippet = "\n".join(f"{r['title']} — {r['snippet']}" for r in results["organic_results"][:3] if r.get("snippet"))
                messages.append({"role": "system", "content": f"Use this info to answer: {snippet}"})
                store_recent_query(uid, user_message)
                store_message(uid, "bot", f"[Search Info] {snippet}")
        except Exception as e:
            logger.warning(f"Search error: {e}")

    # Token trimming
    messages = trim_chat_to_fit(messages)

    # Call Groq API with streaming (buffered)
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=1,
            max_completion_tokens=1024,
            top_p=1,
            stream=True,
            stop=None,
        )

        reply_parts = []
        for chunk in completion:
            content = chunk.choices[0].delta.content
            if content:
                reply_parts.append(content)

        reply = "".join(reply_parts).strip()

        store_message(uid, "user", user_message)
        store_message(uid, "bot", reply)

        return jsonify({"response": reply})
    except Exception as e:
        logger.exception("Groq API error")
        return jsonify({"error": "AI model failed to respond."}), 500

# === GET health check ===

@app.route("/", methods=["GET"])
def root():
    return jsonify({"status": "Felix backend is running."})

# === Error Handling ===

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception("Unhandled server error")
    return jsonify({"error": "Internal server error."}), 500

# === Entry Point ===

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Felix backend running on port {port}")
    app.run(host="0.0.0.0", port=port)

