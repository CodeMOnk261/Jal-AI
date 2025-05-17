# emotion_utils.py

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
