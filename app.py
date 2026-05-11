import json
import os
from flask import Flask, jsonify, render_template

app = Flask(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "data.json")

def load_data():
    with open(DATA_PATH) as f:
        return json.load(f)

try:
    _data = load_data()
except FileNotFoundError:
    _data = {
        "generated_at": None,
        "repo": "PostHog/posthog",
        "days_back": 90,
        "leaderboard": [],
        "graph": {"nodes": [], "links": []},
        "trends": [],
        "coverage": [],
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/data")
def api_data():
    return jsonify(_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
