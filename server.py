import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from together import Together
from datetime import datetime
from serpapi import GoogleSearch
import re
import json
import firebase_admin
from firebase_admin import firestore
from firebase_admin import credentials

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
        update_profile_from_message(uid, user_message)
        profile = get_user_profile(uid)
        
        # Inject as a system prompt
        if profile:
            profile_intro = f"The user's name is {profile.get('name', 'unknown')} and their hobby is {profile.get('hobby', 'unknown')}."
            messages.insert(0, {"role": "system", "content": profile_intro})


        if should_trigger_search(user_message):
            query = user_message.strip()
            result_snippet = search_serpapi_duckduckgo(query)
            # Store the snippet as a bot/system message so it appears in chat
            store_message(uid, "bot", f"[Search Info] {result_snippet}")

            messages.append({"role": "system", "content": f"Use the following info to answer: {result_snippet}"})


        for chat in recent_chats:
            role = "user" if chat["sender"] == "user" else "assistant"
            messages.append({"role": role, "content": chat["message"]})

        messages.append({"role": "user", "content": user_message})  # Add current message

        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
            messages=messages,
            stream=False
        )

        reply = response.choices[0].message.content
        store_message(uid, "user", user_message)
        store_message(uid, "bot", reply)

        return jsonify({"response": reply})
    
    except Exception as e:
        return jsonify({"response": f"Error: {str(e)}"}), 500

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
        return "\n".join(f"- {res.get('title')}: {res.get('link')}" for res in top_results)
    else:
        return "No relevant answer found."



if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

