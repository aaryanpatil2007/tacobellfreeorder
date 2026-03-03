import threading
from flask import Flask, render_template
from flask_socketio import SocketIO
import mail_handler

app = Flask(__name__)
app.config["SECRET_KEY"] = "tacobell"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# One active session at a time
_session = {}


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("start")
def handle_start():
    if _session.get("running"):
        socketio.emit("status", {"type": "error", "msg": "Already running."})
        return

    _session["running"] = True
    thread = threading.Thread(target=_run_flow, daemon=True)
    thread.start()


def _run_flow():
    try:
        # Step 1: create temp email
        socketio.emit("status", {"type": "log", "msg": "Creating temp email..."})
        email, token = mail_handler.create_account()
        _session["token"] = token
        socketio.emit("status", {"type": "email", "msg": email})

        # Step 2: poll for verification code
        socketio.emit(
            "status",
            {"type": "log", "msg": "Waiting for verification email (up to 3 min)..."},
        )
        for event, value in mail_handler.poll_for_code(token):
            if event == "waiting":
                socketio.emit(
                    "status", {"type": "log", "msg": "No email yet, checking again..."}
                )
            elif event == "code":
                socketio.emit("status", {"type": "code", "msg": value})
                socketio.emit(
                    "status",
                    {"type": "log", "msg": "Done! Enter the code above in the Taco Bell app."},
                )
                return

    except TimeoutError:
        socketio.emit(
            "status",
            {
                "type": "error",
                "msg": "Timed out — no verification email arrived. Try again.",
            },
        )
    except Exception as e:
        socketio.emit("status", {"type": "error", "msg": f"Error: {e}"})
    finally:
        _session["running"] = False


if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000, debug=True)
