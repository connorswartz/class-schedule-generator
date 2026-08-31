import csv
import io
import json
import os
import re
import secrets
import sqlite3
from functools import wraps
from io import StringIO

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from schedule_backend import ScheduleConfigError, ScheduleGenerator

app = Flask(__name__)

# Where the database and session key live. Hosting platforms mount their
# persistent storage outside the code directory, so allow this to be moved.
DATA_DIR = os.environ.get("DATA_DIR", app.root_path)
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(DATA_DIR, "scheduler.db"))
SECRET_KEY_PATH = os.path.join(DATA_DIR, ".flask_secret")


def load_secret_key() -> str:
    """Use FLASK_SECRET_KEY, else a random key persisted next to the app.

    Persisting it keeps sign-ins alive across restarts without shipping a
    guessable default that would let anyone forge a session cookie.
    """
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key:
        return env_key

    try:
        with open(SECRET_KEY_PATH, "r", encoding="utf-8") as handle:
            stored = handle.read().strip()
        if stored:
            return stored
    except OSError:
        pass

    generated = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_PATH, "w", encoding="utf-8") as handle:
            handle.write(generated)
    except OSError:
        # Read-only deployment: fall back to a per-process key. Sessions will
        # not survive a restart, which is safe, just less convenient.
        pass
    return generated


app.secret_key = load_secret_key()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            csv_content TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )
    db.commit()


def login_required(route_fn):
    @wraps(route_fn)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return route_fn(*args, **kwargs)

    return wrapped


def get_next_schedule_name(user_id: int) -> str:
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS count FROM schedules WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    next_index = (row["count"] if row else 0) + 1
    return f"Schedule {next_index}"


def get_password_validation_errors(password: str):
    errors = []
    if len(password) < 8:
        errors.append("at least 8 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("at least 1 uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("at least 1 lowercase letter")
    if not re.search(r"\d", password):
        errors.append("at least 1 number")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("at least 1 special character")
    return errors


@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session.get("username", ""))


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not password or not confirm_password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        password_errors = get_password_validation_errors(password)
        if password_errors:
            flash(
                "Password must include: " + ", ".join(password_errors) + ".",
                "error",
            )
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            db.commit()
            flash("Account created. Please sign in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already in use.", "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/generate", methods=["POST"])
@login_required
def generate_schedule():
    try:
        config = request.get_json(silent=True) or {}
        generator = ScheduleGenerator(config)
        generator.generate_schedule()

        csv_output = generator.export_to_csv_string()
        summary = generator.get_summary()

        return jsonify({"success": True, "csv": csv_output, "summary": summary})
    except ScheduleConfigError as e:
        # The configuration itself cannot work - show the teacher exactly why.
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        app.logger.exception("Schedule generation failed")
        return jsonify({"success": False, "error": f"Unexpected error: {e}"}), 500


@app.route("/schedule-default-name", methods=["GET"])
@login_required
def schedule_default_name():
    default_name = get_next_schedule_name(session["user_id"])
    return jsonify({"default_name": default_name})


@app.route("/save-schedule", methods=["POST"])
@login_required
def save_schedule():
    try:
        data = request.get_json(silent=True) or {}
        csv_content = data.get("csv", "")
        summary = data.get("summary")
        config = data.get("config")
        name = (data.get("name") or "").strip()

        if not csv_content:
            return jsonify({"success": False, "error": "No schedule data to save."}), 400
        if summary is None or config is None:
            return jsonify({"success": False, "error": "Missing schedule metadata."}), 400

        if not name:
            name = get_next_schedule_name(session["user_id"])

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO schedules (user_id, name, csv_content, summary_json, config_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                name,
                csv_content,
                json.dumps(summary),
                json.dumps(config),
            ),
        )
        db.commit()

        return jsonify(
            {"success": True, "schedule_id": cursor.lastrowid, "name": name}
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/saved-schedules")
@login_required
def saved_schedules():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, name, created_at
        FROM schedules
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template(
        "saved_schedules.html",
        schedules=rows,
        username=session.get("username", ""),
    )


@app.route("/saved-schedules/<int:schedule_id>")
@login_required
def saved_schedule_detail(schedule_id: int):
    db = get_db()
    schedule = db.execute(
        """
        SELECT id, name, csv_content, summary_json, created_at
        FROM schedules
        WHERE id = ? AND user_id = ?
        """,
        (schedule_id, session["user_id"]),
    ).fetchone()
    if schedule is None:
        abort(404)

    rows = list(csv.reader(StringIO(schedule["csv_content"])))
    header = rows[0] if rows else []
    body_rows = rows[1:] if len(rows) > 1 else []
    summary = json.loads(schedule["summary_json"]) if schedule["summary_json"] else {}

    return render_template(
        "saved_schedule_detail.html",
        schedule=schedule,
        summary=summary,
        header=header,
        body_rows=body_rows,
        username=session.get("username", ""),
    )


@app.route("/saved-schedules/<int:schedule_id>/download")
@login_required
def download_saved_schedule(schedule_id: int):
    db = get_db()
    schedule = db.execute(
        """
        SELECT name, csv_content
        FROM schedules
        WHERE id = ? AND user_id = ?
        """,
        (schedule_id, session["user_id"]),
    ).fetchone()
    if schedule is None:
        abort(404)

    output = io.BytesIO()
    output.write(schedule["csv_content"].encode("utf-8"))
    output.seek(0)

    filename = f"{schedule['name'].replace(' ', '_')}.csv"
    return send_file(
        output,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/download-csv", methods=["POST"])
@login_required
def download_csv():
    try:
        payload = request.get_json(silent=True) or {}
        csv_content = payload.get("csv")
        if not csv_content:
            return jsonify({"error": "No schedule data to download."}), 400

        output = io.BytesIO()
        output.write(csv_content.encode("utf-8"))
        output.seek(0)

        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name="class_schedule.csv",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400


with app.app_context():
    init_db()


if __name__ == "__main__":
    # Debug is off unless asked for: the Werkzeug debugger allows running
    # arbitrary code, so it must never be on for anything reachable by others.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    # Bind to localhost by default. Hosting platforms should set HOST=0.0.0.0.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5001"))

    print(f"\n  Schedule Generator running at http://localhost:{port}\n")
    app.run(debug=debug, host=host, port=port)
