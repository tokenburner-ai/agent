"""Token Agent — admin Flask app entry point."""

import os
from flask import Flask, jsonify, send_from_directory

from admin_api import admin_bp

app = Flask(__name__, static_folder="../static")
app.secret_key = os.environ.get("SECRET_KEY", "agent-dev")

app.register_blueprint(admin_bp)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "admin.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8085, debug=False, use_reloader=False)
