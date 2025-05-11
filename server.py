from flask import Flask, request, jsonify
from flask_cors import CORS
from together import Together

app = Flask(__name__)
CORS(app)  # Allows frontend from any origin to talk to this API

client = Together(api_key="8ba2c94539b1819450e85066fa53857e6b86e75062e52a06cbc377ef78ae4d36")  # Use your real API key here

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"response": "Please enter a message."}), 400

    try:
        messages = [
            {"role": "user", "content": user_message}
        ]
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
            messages=messages,
            stream=False
        )

        reply = response.choices[0].message.content
        return jsonify({"response": reply})
    
    except Exception as e:
        return jsonify({"response": f"Error: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
