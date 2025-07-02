from flask import Flask, request, jsonify
from transformers import AutoTokenizer
from onnxruntime import InferenceSession
import numpy as np

app = Flask(__name__)

# Load ONNX model
session = InferenceSession("onnx_emotion/model.onnx")
tokenizer = AutoTokenizer.from_pretrained("onnx_emotion")

def predict(text):
    inputs = tokenizer(text, return_tensors="np", truncation=True, max_length=128)
    outputs = session.run(None, dict(inputs))
    logits = outputs[0]
    scores = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    label_ids = np.argmax(scores, axis=1)
    label = tokenizer.model.config.id2label[label_ids[0]]
    return label, float(scores[0, label_ids[0]])

@app.route("/detect-emotion", methods=["POST"])
def detect():
    data = request.json or {}
    text = data.get("text", "")
    if not text:
        return jsonify(error="No text provided"), 400
    label, score = predict(text)
    return jsonify(emotion=label, score=score)

if __name__ == "__main__":
    app.run()
