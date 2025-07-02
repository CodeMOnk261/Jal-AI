from flask import Flask, request, jsonify
from transformers import pipeline

app = Flask(__name__)

# Load model once at startup
emotion_classifier = pipeline(
    "text-classification",
    model="bhadresh-savani/distilbert-base-uncased-emotion"
)

@app.route('/detect-emotion', methods=['POST'])
def detect_emotion():
    data = request.get_json()
    text = data.get("text", "")
    
    if not text:
        return jsonify({"error": "No text provided"}), 400

    result = emotion_classifier(text)
    top_emotion = max(result, key=lambda x: x["score"])
    
    return jsonify({
        "emotion": top_emotion["label"],
        "score": top_emotion["score"],
        "all": result
    })

if __name__ == '__main__':
    app.run()
