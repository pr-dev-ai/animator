"""
Kids Animation Studio — Web UI server.

This is a placeholder that will be replaced by the full server implementation.
Run ./start_ui.sh to launch.
"""
from flask import Flask

app = Flask(__name__)


@app.route("/")
def index():
    return (
        "<h1>Kids Animation Studio</h1>"
        "<p>Server is running. Full UI coming soon.</p>"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
