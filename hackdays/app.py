
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

from dotenv import load_dotenv
from flask import (
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from services.gemini_service import GeminiService

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY")
if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] == "replace-with-a-long-random-secret":
    raise RuntimeError("Set FLASK_SECRET_KEY to a long, random value in .env before running the app.")
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["DATABASE_PATH"] = os.environ.get(
    "TRACE_DATABASE_PATH", str(BASE_DIR / "trace.db")
)
app.config["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")
app.config["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def get_db_connection():
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                name TEXT,
                education TEXT,
                coding_level TEXT,
                target_role TEXT,
                preferred_language TEXT,
                target_company TEXT,
                goals TEXT,
                weak_topics TEXT,
                profile_json TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mock_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interview_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                interview_date TEXT NOT NULL,
                focus_areas TEXT NOT NULL,
                roadmap TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )


initialize_database()


def current_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return check_password_hash(stored_hash, password)


def request_data():
    return request.get_json(silent=True) or request.form


def validate_text(value, field, max_length, required=False):
    if not isinstance(value, str):
        value = ""
    value = value.strip()
    if required and not value:
        return None, f"{field} is required."
    if len(value) > max_length:
        return None, f"{field} must be {max_length} characters or fewer."
    return value, None


def gemini_error_response(exc):
    current_app.logger.exception("Gemini request failed", exc_info=exc)
    message = str(exc)
    if "GEMINI_API_KEY" in message:
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503
    return jsonify({"error": "Gemini could not complete this request. Please try again shortly."}), 502


def validated_platform_url(platform, profile_url):
    domains = {
        "Codeforces": ("codeforces.com",),
        "GeeksforGeeks": ("geeksforgeeks.org",),
        "LeetCode": ("leetcode.com",),
        "GitHub": ("github.com",),
    }
    if platform not in domains:
        return None
    try:
        parsed = urlparse(profile_url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    if not any(host == domain for domain in domains[platform]):
        return None
    if not parsed.path.strip("/"):
        return None
    return parsed.geturl()


def extract_platform_url_from_json(data, platform):
    platform_aliases = {
        "codeforces": "Codeforces",
        "geeksforgeeks": "GeeksforGeeks",
        "leetcode": "LeetCode",
    }
    if not isinstance(data, (dict, list)):
        return None, "JSON must contain an object or an array of profile data."

    platform_nodes = []
    pending = [(data, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > 20:
            continue
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).strip().lower() == "platform":
                    declared = platform_aliases.get(str(value).strip().lower())
                    if declared != platform:
                        return None, "The JSON platform does not match the selected platform."
                if str(key).strip().lower() == platform.lower():
                    platform_nodes.append(value)
                if isinstance(value, (dict, list)):
                    pending.append((value, depth + 1))
        elif isinstance(node, list):
            pending.extend((value, depth + 1) for value in node if isinstance(value, (dict, list)))

    nodes = [(data, 0)]
    for node in platform_nodes:
        nodes.append((node, 1))
    profile_url = None
    username = None
    url_keys = {"profile_url", "profileurl", "url"}
    username_keys = {"username", "user_name", "handle"}
    while nodes:
        node, depth = nodes.pop(0)
        if depth > 20:
            continue
        if isinstance(node, str):
            candidate = validated_platform_url(platform, node.strip())
            if candidate:
                profile_url = candidate
                break
            continue
        if isinstance(node, list):
            nodes.extend((value, depth + 1) for value in node)
            continue
        if not isinstance(node, dict):
            continue

        nested = []
        for key, value in node.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in url_keys and isinstance(value, str) and not profile_url:
                candidate = validated_platform_url(platform, value.strip())
                if candidate:
                    profile_url = candidate
            elif normalized_key in username_keys and isinstance(value, str) and not username:
                username = value.strip()
            if isinstance(value, (dict, list)):
                nested.append((value, depth + 1))
            elif isinstance(value, str) and not profile_url:
                candidate = validated_platform_url(platform, value.strip())
                if candidate:
                    profile_url = candidate
        if profile_url:
            break
        nodes.extend(nested)

    if profile_url is None and username:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", username):
            return None, "The username in the JSON file is invalid."
        encoded_username = quote(username, safe="")
        profile_paths = {
            "Codeforces": f"https://codeforces.com/profile/{encoded_username}",
            "GeeksforGeeks": f"https://www.geeksforgeeks.org/user/{encoded_username}/",
            "LeetCode": f"https://leetcode.com/u/{encoded_username}/",
        }
        profile_url = profile_paths[platform]

    if not profile_url:
        return None, "Could not find a matching profile URL or username in this JSON file."
    if len(profile_url) > 2048:
        return None, "The profile URL in the JSON file is invalid."
    return profile_url, None


def get_user_by_id(user_id):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def get_profile_for_user(user_id):
    with get_db_connection() as conn:
        profile = conn.execute(
            "SELECT * FROM profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(profile) if profile else {}


def profile_payload(data, fallback_name=""):
    weak_topics = data.get("weak_topics", [])
    if isinstance(weak_topics, str):
        try:
            parsed_topics = json.loads(weak_topics)
        except json.JSONDecodeError:
            parsed_topics = [topic.strip() for topic in weak_topics.split(",") if topic.strip()]
        weak_topics = parsed_topics
    if not isinstance(weak_topics, list):
        weak_topics = []
    weak_topics = [topic[:80] for topic in weak_topics if isinstance(topic, str)][:20]
    return {
        "name": str(data.get("name") or data.get("full_name") or fallback_name).strip()[:80],
        "education": str(data.get("education") or "").strip()[:120],
        "coding_level": str(data.get("coding_level") or data.get("codingLevel") or "").strip()[:40],
        "target_role": str(data.get("target_role") or data.get("targetRole") or "").strip()[:100],
        "preferred_language": str(
            data.get("preferred_language") or data.get("language") or ""
        ).strip()[:40],
        "target_company": str(data.get("target_company") or "").strip()[:100],
        "goals": str(data.get("goals") or "").strip()[:1000],
        "weak_topics": weak_topics,
    }


def save_profile_for_user(user_id, payload):
    with get_db_connection() as conn:
        profile = conn.execute(
            "SELECT * FROM profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        data = payload or {}
        now = current_timestamp()

        if profile:
            conn.execute(
                """
                UPDATE profiles
                SET name = ?, education = ?, coding_level = ?, target_role = ?, preferred_language = ?,
                    target_company = ?, goals = ?, weak_topics = ?, profile_json = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    data.get("name"),
                    data.get("education"),
                    data.get("coding_level"),
                    data.get("target_role"),
                    data.get("preferred_language"),
                    data.get("target_company"),
                    data.get("goals"),
                    json.dumps(data.get("weak_topics") or []),
                    json.dumps(data, ensure_ascii=True),
                    now,
                    user_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO profiles (
                    user_id, name, education, coding_level, target_role, preferred_language,
                    target_company, goals, weak_topics, profile_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    data.get("name"),
                    data.get("education"),
                    data.get("coding_level"),
                    data.get("target_role"),
                    data.get("preferred_language"),
                    data.get("target_company"),
                    data.get("goals"),
                    json.dumps(data.get("weak_topics") or []),
                    json.dumps(data, ensure_ascii=True),
                    now,
                ),
            )


def get_platform_connections(user_id):
    with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT platform, profile_url, created_at FROM platform_connections WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def save_history(user_id, entry_type, title, payload):
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO history (user_id, entry_type, title, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                entry_type,
                title,
                json.dumps(payload, ensure_ascii=True),
                current_timestamp(),
            ),
        )


def get_recent_history(user_id, limit=10):
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        history = [dict(row) for row in rows]
    for item in history:
        item["payload"] = json.loads(item["payload"])
    return history


def get_dashboard_summary(user_id):
    with get_db_connection() as conn:
        analyses = conn.execute(
            "SELECT COUNT(*) FROM history WHERE user_id = ? AND entry_type = 'analysis'",
            (user_id,),
        ).fetchone()[0]
        interviews = conn.execute(
            "SELECT COUNT(*) FROM history WHERE user_id = ? AND entry_type = 'interview'",
            (user_id,),
        ).fetchone()[0]
        mock_sessions = conn.execute(
            "SELECT COUNT(*) FROM history WHERE user_id = ? AND entry_type = 'mock'",
            (user_id,),
        ).fetchone()[0]
        plans = conn.execute(
            "SELECT COUNT(*) FROM interview_plans WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        profile = get_profile_for_user(user_id)
        return {
            "analyses": analyses,
            "interviews": interviews,
            "mock_sessions": mock_sessions,
            "plans": plans,
            "activity_total": analyses + interviews + mock_sessions + plans,
            "profile": profile,
        }


module_gemini = GeminiService(
    app.config["GEMINI_API_KEY"],
    app.config["GEMINI_MODEL"],
)


# ---------------- LOGIN PROTECTION ----------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "error")
            return redirect(url_for("home"))
        return view(*args, **kwargs)

    return wrapped


# ---------------- HOME ----------------

@app.route("/")
def home():
    return render_template("index.html")


# ---------------- LOGIN ----------------

@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("Enter your username and password.", "error")
        return redirect(url_for("home"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if user and verify_password(password, user["password_hash"]):
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        flash("Login successful.", "success")
        return redirect(url_for("dashboard"))

    flash("Invalid username or password.", "error")
    return redirect(url_for("home"))


# ---------------- REGISTRATION ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password or not confirm_password:
            flash("Please complete all registration fields.", "error")
            return redirect(url_for("register"))

        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
            flash("Username must be 3–30 characters and use letters, numbers, dots, underscores, or hyphens.", "error")
            return redirect(url_for("register"))

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            flash("Enter a valid email address.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        with get_db_connection() as conn:
            existing_user = conn.execute(
                "SELECT id FROM users WHERE username = ? OR email = ?",
                (username, email),
            ).fetchone()

        if existing_user:
            flash("That username or email is already in use.", "error")
            return redirect(url_for("register"))

        now = current_timestamp()
        try:
            with get_db_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (username, email, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (username, email, hash_password(password), now, now),
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            flash("That username or email is already in use.", "error")
            return redirect(url_for("register"))

        session.clear()
        session["user_id"] = user_id
        session["username"] = username
        flash("Account created successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
@login_required
def dashboard():
    user = get_user_by_id(session["user_id"])
    profile = get_profile_for_user(session["user_id"])
    summary = get_dashboard_summary(session["user_id"])
    history = get_recent_history(session["user_id"], limit=5)
    return render_template(
        "dashboard.html",
        user=dict(user) if user else {},
        profile=profile,
        summary=summary,
        recent_history=history,
    )


# ---------------- CODING INTELLIGENCE ----------------

@app.route("/coding-intelligence")
@login_required
def coding_intelligence():
    profile = get_profile_for_user(session["user_id"])
    history = get_recent_history(session["user_id"], limit=100)
    analyses = [
        item for item in history
        if item["entry_type"] == "analysis"
    ]
    status_counts = {
        status: sum(
            item["payload"].get("status") == status
            for item in analyses
            if isinstance(item["payload"], dict)
        )
        for status in ("correct", "incomplete", "buggy", "insufficient_info")
    }
    latest_insight = next(
        (
            item["payload"]
            for item in history
            if item["entry_type"] == "insight" and isinstance(item["payload"], dict)
        ),
        None,
    )
    return render_template(
        "codingintelligence.html",
        profile=profile_payload(profile, session.get("username")),
        status_counts=status_counts,
        history=history,
        latest_insight=latest_insight,
    )


# ---------------- MOCK INTERVIEW ----------------

@app.route("/mock-interview")
@login_required
def mock_interview():
    return render_template(
        "mock_interview.html",
        profile=profile_payload(
            get_profile_for_user(session["user_id"]),
            session.get("username"),
        ),
    )


# ---------------- INTERVIEW PREPARATION ----------------

@app.route("/interview-preparation")
@login_required
def interview_preparation():
    with get_db_connection() as conn:
        plans = conn.execute(
            "SELECT * FROM interview_plans WHERE user_id = ? ORDER BY interview_date ASC",
            (session["user_id"],),
        ).fetchall()
    return render_template(
        "interview_preperation.html",
        interview_plans=[dict(row) for row in plans],
    )


# Keep the old revision URL working.
@app.route("/revision")
@login_required
def revision():
    return redirect(url_for("interview_preparation"))


# ---------------- CONNECT PLATFORMS ----------------

@app.route("/connect-platforms", methods=["GET", "POST"])
@login_required
def connect_platforms():
    if request.method == "POST":
        data = request_data()
        platform = data.get("platform", "").strip()
        profile_url = data.get("profile_url", "").strip()
        valid_url = validated_platform_url(platform, profile_url)
        if not valid_url:
            flash("Please provide both a platform and a profile URL.", "error")
            return redirect(url_for("connect_platforms"))

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO platform_connections (user_id, platform, profile_url, created_at)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM platform_connections
                    WHERE user_id = ? AND platform = ? AND profile_url = ?
                )
                """,
                (
                    session["user_id"],
                    platform,
                    valid_url,
                    current_timestamp(),
                    session["user_id"],
                    platform,
                    valid_url,
                ),
            )
        flash(f"{platform} profile link saved for reference. Account access and data import are not enabled.", "success")
        return redirect(url_for("connect_platforms"))

    with get_db_connection() as conn:
        connections = conn.execute(
            "SELECT * FROM platform_connections WHERE user_id = ? ORDER BY created_at DESC",
            (session["user_id"],),
        ).fetchall()
    return render_template(
        "connectplat.html",
        connections=[dict(row) for row in connections],
    )


# ---------------- SETTINGS / PROFILE ----------------

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        data = request_data()
        payload = profile_payload(data, session.get("username"))
        save_profile_for_user(session["user_id"], payload)
        flash("Profile updated successfully.", "success")
        return redirect(url_for("settings"))

    profile = get_profile_for_user(session["user_id"])
    user = get_user_by_id(session["user_id"])
    connections = get_platform_connections(session["user_id"])
    summary = get_dashboard_summary(session["user_id"])
    return render_template(
        "profile_index.html",
        profile=profile,
        user=dict(user) if user else {},
        connections=connections,
        summary=summary,
    )


@app.route("/profile")
@login_required
def profile():
    return redirect(url_for("settings"))


# ---------------- CODING HISTORY ----------------

@app.route("/coding-history")
@login_required
def coding_history():
    history = [
        item
        for item in get_recent_history(session["user_id"], limit=5000)
        if item["entry_type"] == "platform_import"
    ]
    return render_template("coding_history.html", history=history)


# ---------------- LOGOUT ----------------

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


# ---------------- APPLICATION API ROUTES ----------------

@app.route("/api/profile", methods=["GET", "PUT"])
@login_required
def api_profile():
    user_id = session["user_id"]
    if request.method == "GET":
        user = get_user_by_id(user_id)
        data = profile_payload(get_profile_for_user(user_id), session.get("username"))
        data["email"] = user["email"] if user else ""
        return jsonify(data)

    payload = profile_payload(request_data(), session.get("username"))
    if not payload["name"]:
        return jsonify({"error": "Full name is required."}), 400
    save_profile_for_user(user_id, payload)
    return jsonify({"message": "Profile saved.", "profile": payload})


@app.route("/api/platform-links", methods=["POST"])
@login_required
def api_platform_links():
    data = request_data()
    platform = str(data.get("platform", "")).strip()
    profile_url = str(data.get("profile_url", "")).strip()
    valid_url = validated_platform_url(platform, profile_url)
    if not valid_url:
        return jsonify({"error": "Enter an HTTPS profile URL for a supported platform."}), 400

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO platform_connections (user_id, platform, profile_url, created_at)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM platform_connections
                WHERE user_id = ? AND platform = ? AND profile_url = ?
            )
            """,
            (
                session["user_id"],
                platform,
                valid_url,
                current_timestamp(),
                session["user_id"],
                platform,
                valid_url,
            ),
        )
    return jsonify({
        "message": "Profile link saved for reference. This does not connect or import account data.",
        "platform": platform,
        "profile_url": valid_url,
        "connected": False,
    })


@app.route("/api/platform-links/import", methods=["POST"])
@login_required
def api_platform_links_import():
    platform = request.form.get("platform", "").strip()
    if platform not in ("Codeforces", "GeeksforGeeks", "LeetCode"):
        return jsonify({"error": "Choose a supported platform."}), 400

    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Choose a JSON file to import."}), 400
    if not uploaded_file.filename.lower().endswith(".json"):
        return jsonify({"error": "Upload a file with a .json extension."}), 400

    raw_content = uploaded_file.stream.read(256 * 1024 + 1)
    if len(raw_content) > 256 * 1024:
        return jsonify({"error": "JSON files must be 256 KB or smaller."}), 400
    try:
        data = json.loads(raw_content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return jsonify({"error": "The uploaded file is not valid UTF-8 JSON."}), 400

    profile_url, error = extract_platform_url_from_json(data, platform)
    if error:
        return jsonify({"error": error}), 400

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO platform_connections (user_id, platform, profile_url, created_at)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM platform_connections
                WHERE user_id = ? AND platform = ? AND profile_url = ?
            )
            """,
            (
                session["user_id"],
                platform,
                profile_url,
                current_timestamp(),
                session["user_id"],
                platform,
                profile_url,
            ),
        )
    return jsonify({
        "message": f"{platform} profile URL imported and saved. Account data was not accessed.",
        "platform": platform,
        "profile_url": profile_url,
        "connected": False,
    })


@app.route("/api/leetcode-history/import", methods=["POST"])
@login_required
def api_leetcode_history_import():
    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Choose a LeetCode history JSON file to import."}), 400
    if not uploaded_file.filename.lower().endswith(".json"):
        return jsonify({"error": "Upload a file with a .json extension."}), 400

    raw_content = uploaded_file.stream.read(1024 * 1024 + 1)
    if len(raw_content) > 1024 * 1024:
        return jsonify({"error": "JSON files must be 1 MB or smaller."}), 400
    try:
        records = json.loads(raw_content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return jsonify({"error": "The uploaded file is not valid UTF-8 JSON."}), 400
    if not isinstance(records, list):
        return jsonify({"error": "LeetCode history JSON must be an array of problem records."}), 400
    if len(records) > 5000:
        return jsonify({"error": "A JSON file can contain at most 5,000 problem records."}), 400

    def field(record, name):
        return next(
            (
                value
                for key, value in record.items()
                if str(key).strip().casefold() == name.casefold()
            ),
            None,
        )

    valid_records = []
    skipped = 0
    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            continue

        problem_id = field(record, "ID")
        title = field(record, "Title")
        difficulty = field(record, "Difficulty")
        problem_url = field(record, "URL")
        timestamp = field(record, "Timestamp")
        submissions = field(record, "Submissions")

        if not all(isinstance(value, str) for value in (problem_id, title, difficulty, problem_url, timestamp, submissions)):
            skipped += 1
            continue
        problem_id = problem_id.strip()
        title = title.strip()
        difficulty = difficulty.strip().title()
        problem_url = problem_url.strip()
        timestamp = timestamp.strip()
        submissions = submissions.strip()
        try:
            parsed_url = urlparse(problem_url)
            submission_count = int(submissions)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if (
            not problem_id
            or len(problem_id) > 100
            or not title
            or len(title) > 300
            or difficulty not in {"Easy", "Medium", "Hard"}
            or parsed_url.scheme != "https"
            or (parsed_url.hostname or "").lower().removeprefix("www.") != "leetcode.com"
            or not re.fullmatch(r"/problems/[A-Za-z0-9-]+/?", parsed_url.path)
            or parsed_url.username
            or parsed_url.password
            or not timestamp
            or len(timestamp) > 80
            or submission_count < 0
        ):
            skipped += 1
            continue

        valid_records.append({
            "problem_id": problem_id,
            "title": title,
            "difficulty": difficulty,
            "url": parsed_url.geturl(),
            "timestamp": timestamp,
            "submissions": submission_count,
        })

    if not valid_records:
        return jsonify({
            "error": "No valid LeetCode problem records were found. Expected ID, Title, Difficulty, URL, Timestamp, and Submissions fields.",
            "skipped": skipped,
        }), 400

    user_id = session["user_id"]
    imported_count = 0
    with get_db_connection() as conn:
        existing_rows = conn.execute(
            "SELECT payload FROM history WHERE user_id = ? AND entry_type = 'platform_import'",
            (user_id,),
        ).fetchall()
        existing_signatures = set()
        for row in existing_rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            if payload.get("platform") == "LeetCode":
                existing_signatures.add(
                    (payload.get("problem_id"), payload.get("url"), payload.get("timestamp"), payload.get("submissions"))
                )

        seen_signatures = set()
        now = current_timestamp()
        for record in valid_records:
            signature = (
                record["problem_id"],
                record["url"],
                record["timestamp"],
                record["submissions"],
            )
            if signature in existing_signatures or signature in seen_signatures:
                skipped += 1
                continue
            seen_signatures.add(signature)
            payload = {"platform": "LeetCode", **record}
            conn.execute(
                """
                INSERT INTO history (user_id, entry_type, title, payload, created_at)
                VALUES (?, 'platform_import', ?, ?, ?)
                """,
                (user_id, f"LeetCode: {record['title']}", json.dumps(payload), now),
            )
            imported_count += 1

    return jsonify({
        "message": f"Imported {imported_count} LeetCode problem(s) to Coding History; skipped {skipped} invalid or already imported record(s).",
        "imported": imported_count,
        "skipped": skipped,
        "history_url": url_for("coding_history"),
        "connected": False,
    })


@app.route("/api/interview-plans", methods=["GET", "POST"])
@login_required
def api_interview_plans():
    user_id = session["user_id"]
    if request.method == "GET":
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM interview_plans WHERE user_id = ? ORDER BY interview_date ASC",
                (user_id,),
            ).fetchall()
        return jsonify({"plans": [dict(row) for row in rows]})

    data = request_data()
    company, error = validate_text(data.get("company"), "Company", 80, required=True)
    if error:
        return jsonify({"error": error}), 400
    role, error = validate_text(data.get("role"), "Target role", 100, required=True)
    if error:
        return jsonify({"error": error}), 400
    interview_date, error = validate_text(data.get("date"), "Interview date", 10, required=True)
    if error:
        return jsonify({"error": error}), 400
    try:
        date_value = datetime.strptime(interview_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Choose a valid interview date."}), 400
    if date_value < datetime.now().date():
        return jsonify({"error": "Interview date must be today or later."}), 400

    focus_areas = data.get("focus_areas", [])
    if not isinstance(focus_areas, list):
        return jsonify({"error": "Focus areas must be a list."}), 400
    focus_areas = [
        str(topic).strip()[:80]
        for topic in focus_areas
        if isinstance(topic, str) and topic.strip()
    ][:20]
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO interview_plans (user_id, company, role, interview_date, focus_areas, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                company,
                role,
                interview_date,
                json.dumps(focus_areas),
                current_timestamp(),
            ),
        )
        plan_id = cursor.lastrowid
    return jsonify({
        "plan": {
            "id": plan_id,
            "company": company,
            "role": role,
            "date": interview_date,
            "focus_areas": focus_areas,
        }
    }), 201


@app.route("/api/analyze-code", methods=["POST"])
@login_required
def api_analyze_code():
    if not module_gemini.is_configured():
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503

    data = request_data()
    problem, error = validate_text(data.get("problem_statement"), "Problem description", 12000)
    if error:
        return jsonify({"error": error}), 400
    language, error = validate_text(data.get("language"), "Language", 80)
    if error:
        return jsonify({"error": error}), 400
    code, error = validate_text(data.get("code"), "Code", 40000)
    if error:
        return jsonify({"error": error}), 400
    if not problem and not code:
        return jsonify({"error": "Provide at least a problem description or code."}), 400
    try:
        result = module_gemini.analyze_code(
            problem,
            language,
            code,
            get_profile_for_user(session["user_id"]),
        )
    except Exception as exc:
        return gemini_error_response(exc)
    save_history(session["user_id"], "analysis", "Code analysis", result)
    return jsonify(result)


@app.route("/api/roadmap", methods=["POST"])
@login_required
def api_roadmap():
    if not module_gemini.is_configured():
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503

    data = request_data()
    profile = profile_payload(
        get_profile_for_user(session["user_id"]),
        session.get("username"),
    )
    company, error = validate_text(data.get("company"), "Company", 80)
    if error:
        return jsonify({"error": error}), 400
    role, error = validate_text(data.get("role"), "Target role", 100)
    if error:
        return jsonify({"error": error}), 400
    profile["target_company"] = company or profile.get("target_company")
    profile["target_role"] = role or profile.get("target_role")
    topics = data.get("weak_topics", profile.get("weak_topics", []))
    if not isinstance(topics, list):
        return jsonify({"error": "Topics must be a list."}), 400
    topics = [str(topic).strip()[:80] for topic in topics if isinstance(topic, str) and topic.strip()][:20]
    profile["goals"] = str(data.get("goals") or profile.get("goals") or "")[:1000]
    history = get_recent_history(session["user_id"], limit=5)
    try:
        result = module_gemini.build_study_roadmap(profile, topics, history)
    except Exception as exc:
        return gemini_error_response(exc)
    save_history(session["user_id"], "roadmap", "Study roadmap", result)
    return jsonify(result)


@app.route("/api/performance-insights", methods=["POST"])
@login_required
def api_performance_insights():
    if not module_gemini.is_configured():
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503
    profile = profile_payload(
        get_profile_for_user(session["user_id"]),
        session.get("username"),
    )
    history = get_recent_history(session["user_id"], limit=20)
    try:
        result = module_gemini.generate_performance_insights(profile, history)
    except Exception as exc:
        return gemini_error_response(exc)
    save_history(session["user_id"], "insight", "Performance insights", result)
    return jsonify(result)


@app.route("/api/interview-question", methods=["POST"])
@login_required
def api_interview_question():
    if not module_gemini.is_configured():
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503
    data = request_data()
    profile = profile_payload(
        get_profile_for_user(session["user_id"]),
        session.get("username"),
    )
    company = str(data.get("company") or profile.get("target_company") or "Any company")[:80]
    role = str(data.get("role") or profile.get("target_role") or "Software Engineer")[:100]
    level = str(data.get("level") or profile.get("coding_level") or "Intermediate")[:40]
    topic = str(data.get("topic", "General coding"))[:200]
    try:
        question = module_gemini.generate_interview_question(company, role, level, topic)
    except Exception as exc:
        return gemini_error_response(exc)
    return jsonify(question)


@app.route("/api/interview-evaluate", methods=["POST"])
@login_required
def api_interview_evaluate():
    if not module_gemini.is_configured():
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503
    data = request_data()
    question, error = validate_text(data.get("question"), "Question", 12000, required=True)
    if error:
        return jsonify({"error": error}), 400
    answer, error = validate_text(data.get("answer"), "Answer", 12000, required=True)
    if error:
        return jsonify({"error": error}), 400
    role = str(data.get("role", ""))[:100]
    level = str(data.get("level", ""))[:40]
    try:
        result = module_gemini.evaluate_interview_answer(question, answer, role, level)
    except Exception as exc:
        return gemini_error_response(exc)
    save_history(
        session["user_id"],
        "interview",
        "Interview feedback",
        {"question": question, "answer": answer, "feedback": result},
    )
    return jsonify(result)


@app.route("/api/mock-summary", methods=["POST"])
@login_required
def api_mock_summary():
    if not module_gemini.is_configured():
        return jsonify({"error": "Gemini is not configured. Add GEMINI_API_KEY to .env and restart the app."}), 503
    data = request_data()
    raw_questions = data.get("questions", [])
    raw_answers = data.get("answers", [])
    if not isinstance(raw_questions, list) or not isinstance(raw_answers, list):
        return jsonify({"error": "Questions and answers must be lists."}), 400
    questions = [str(item)[:12000] for item in raw_questions[:20]]
    answers = [str(item)[:12000] for item in raw_answers[:20]]
    if not questions or not answers or len(questions) != len(answers):
        return jsonify({"error": "Provide matching question and answer entries."}), 400
    try:
        summary = module_gemini.generate_mock_summary(
            questions,
            answers,
            profile_payload(get_profile_for_user(session["user_id"]), session.get("username")),
        )
    except Exception as exc:
        return gemini_error_response(exc)
    save_history(session["user_id"], "mock", "Mock interview summary", summary)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO mock_sessions (user_id, title, summary, created_at) VALUES (?, ?, ?, ?)",
            (session["user_id"], "Mock interview", json.dumps(summary), current_timestamp()),
        )
    return jsonify(summary)


@app.route("/api/dashboard-data")
@login_required
def api_dashboard_data():
    summary = get_dashboard_summary(session["user_id"])
    return jsonify(summary)


# ---------------- RUN APP ----------------

if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG") == "1",
        host="127.0.0.1",
        port=5000,
    )
