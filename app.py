import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from functools import wraps

import requests as http_requests
from flask import Flask, jsonify, render_template, request, redirect, url_for
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from ldap3 import Server, Connection, ALL, SUBTREE
    LDAP3_AVAILABLE = True
except ImportError:
    LDAP3_AVAILABLE = False

from bookshelf import BookshelfClient
from readarr import ReadarrClient
from lazylibrarian import LazyLibrarianClient

app = Flask(__name__)


def _load_or_create_secret_key():
    """Load secret key from env, or persist one to data/secret_key."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_file = os.path.join(os.path.dirname(__file__), "data", "secret_key")
    if os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    key = os.urandom(32).hex()
    with open(key_file, "w") as f:
        f.write(key)
    return key


app.secret_key = _load_or_create_secret_key()

# Configure logging to stdout so it shows in docker logs
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
app.logger.setLevel(logging.DEBUG)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "config.json")
REQUESTS_FILE = os.path.join(os.path.dirname(__file__), "data", "requests.json")
USERS_FILE = os.path.join(os.path.dirname(__file__), "data", "users.json")

# In-memory state
config = {"ebook": {}, "audiobook": {}, "ldap": {}}
requests_history = []
users = []
lock = threading.Lock()

# ─── Flask-Login Setup ───

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class User:
    """Flask-Login user wrapper."""

    def __init__(self, data):
        self._data = data

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def username(self):
        return self._data["username"]

    @property
    def role(self):
        return self._data.get("role", "user")

    def get_id(self):
        return self.username


@login_manager.user_loader
def load_user(username):
    load_users()
    for u in users:
        if u["username"] == username:
            return User(u)
    return None


@login_manager.unauthorized_handler
def handle_unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("login"))


def admin_required(f):
    """Decorator: require admin role."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


# ─── Data Persistence ───

def ensure_data_dir():
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)


def save_config():
    ensure_data_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass


def save_requests():
    ensure_data_dir()
    with open(REQUESTS_FILE, "w") as f:
        json.dump(requests_history, f, indent=2, default=str)


def load_requests():
    global requests_history
    if os.path.exists(REQUESTS_FILE):
        with open(REQUESTS_FILE) as f:
            requests_history = json.load(f)


def save_users():
    ensure_data_dir()
    # Strip password_hash before logging
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def load_users():
    global users
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                users = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass


def init_default_admin():
    """Create a default admin account if no users exist."""
    if not users:
        users.append({
            "username": "admin",
            "password_hash": generate_password_hash("admin"),
            "role": "admin",
            "created_at": datetime.utcnow().isoformat(),
        })
        save_users()
        app.logger.warning(
            "Default admin account created (username: admin, password: admin). "
            "Please change the password immediately!"
        )


load_config()
load_requests()
load_users()
init_default_admin()


# ─── LDAP Auth ───

def _get_ldap_defaults():
    return {
        "enabled": False,
        "server_url": "",
        "bind_dn": "",
        "bind_password": "",
        "base_dn": "",
        "user_search_filter": "(sAMAccountName={username})",
        "default_role": "user",
    }


def try_ldap_auth(username, password):
    """Attempt LDAP bind authentication.

    Returns (success: bool, user_dn: str, error: str).
    """
    if not LDAP3_AVAILABLE:
        return False, "", "ldap3 library is not installed"

    ldap = config.get("ldap", {})
    if not ldap.get("enabled"):
        return False, "", "LDAP is not enabled"

    server_url = ldap.get("server_url", "")
    bind_dn = ldap.get("bind_dn", "")
    bind_password = ldap.get("bind_password", "")
    base_dn = ldap.get("base_dn", "")
    search_filter = ldap.get("user_search_filter", "(sAMAccountName={username})")

    if not server_url or not base_dn:
        return False, "", "LDAP server_url or base_dn not configured"

    search_filter = search_filter.replace("{username}", username)

    try:
        server = Server(server_url, get_info=ALL)
        conn = Connection(server, bind_dn, bind_password, auto_bind=True)
        conn.search(base_dn, search_filter, search_scope=SUBTREE)
        if not conn.entries:
            conn.unbind()
            return False, "", "User not found in LDAP directory"
        user_dn = conn.entries[0].entry_dn
        conn.unbind()

        # Attempt to bind as the user to verify their password
        user_conn = Connection(server, user_dn, password, auto_bind=True)
        user_conn.unbind()
        return True, user_dn, ""
    except Exception as e:
        return False, "", str(e)


def get_client(server_type: str) -> ReadarrClient | BookshelfClient | LazyLibrarianClient | None:
    """Get a client for the given server type based on server_software setting."""
    load_config()
    server = config.get(server_type, {})
    if server.get("url") and server.get("api_key"):
        if server.get("server_software") == "bookshelf":
            return BookshelfClient(server["url"], server["api_key"])
        if server.get("server_software") == "lazylibrarian":
            return LazyLibrarianClient(server["url"], server["api_key"])
        return ReadarrClient(server["url"], server["api_key"])
    return None


# ─── Pages ───

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


# ─── Auth API ───

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    load_users()
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    for u in users:
        if u["username"] == username and check_password_hash(u["password_hash"], password):
            login_user(User(u))
            return jsonify({"success": True, "username": u["username"], "role": u.get("role", "user")})

    # Fall through to LDAP if configured
    ldap = config.get("ldap", {})
    if ldap.get("enabled"):
        app.logger.info("LDAP enabled, attempting auth for '%s'", username)
        success, _user_dn, error = try_ldap_auth(username, password)
        app.logger.info("LDAP result: success=%s, dn=%s, error=%s", success, _user_dn, error)
        if success:
            existing = next((u for u in users if u["username"] == username), None)
            if not existing:
                existing = {
                    "username": username,
                    "password_hash": "ldap",
                    "role": ldap.get("default_role", "user"),
                    "created_at": datetime.utcnow().isoformat(),
                }
                users.append(existing)
                save_users()
            app.logger.info("About to call login_user for '%s'", username)
            ok = login_user(User(existing))
            app.logger.info("login_user returned %s for '%s'", ok, username)
            return jsonify({"success": True, "username": existing["username"], "role": existing.get("role", "user")})
        app.logger.info("LDAP auth failed for '%s': %s", username, error)

    return jsonify({"error": "Invalid username or password"}), 401


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    logout_user()
    return jsonify({"success": True})


@app.route("/api/auth/me", methods=["GET"])
@login_required
def api_me():
    return jsonify({
        "username": current_user.username,
        "role": current_user.role,
    })


# ─── User Management API ───

@app.route("/api/users", methods=["GET"])
@admin_required
def get_users():
    load_users()
    safe_users = []
    for u in users:
        safe_users.append({
            "username": u["username"],
            "role": u.get("role", "user"),
            "created_at": u.get("created_at", ""),
        })
    return jsonify(safe_users)


@app.route("/api/users", methods=["POST"])
@admin_required
def create_user():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400

    for u in users:
        if u["username"] == username:
            return jsonify({"error": "Username already exists"}), 400

    new_user = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
        "created_at": datetime.utcnow().isoformat(),
    }
    users.append(new_user)
    save_users()
    return jsonify({"success": True, "username": username, "role": role}), 201


@app.route("/api/users/<username>", methods=["PUT"])
@admin_required
def update_user(username):
    data = request.json

    target = None
    for u in users:
        if u["username"] == username:
            target = u
            break

    if not target:
        return jsonify({"error": "User not found"}), 404

    if "password" in data and data["password"]:
        target["password_hash"] = generate_password_hash(data["password"])

    if "role" in data:
        if data["role"] not in ("admin", "user"):
            return jsonify({"error": "Role must be 'admin' or 'user'"}), 400
        target["role"] = data["role"]

    save_users()
    return jsonify({"success": True, "username": target["username"], "role": target.get("role", "user")})


@app.route("/api/users/<username>", methods=["DELETE"])
@admin_required
def delete_user(username):
    if username == current_user.username:
        return jsonify({"error": "Cannot delete your own account"}), 400

    global users
    original_len = len(users)
    users = [u for u in users if u["username"] != username]

    if len(users) == original_len:
        return jsonify({"error": "User not found"}), 404

    save_users()
    return jsonify({"success": True})


# ─── LDAP Config API ───

@app.route("/api/ldap", methods=["GET"])
@admin_required
def get_ldap():
    load_config()
    ldap = config.get("ldap", _get_ldap_defaults())
    return jsonify({
        "enabled": ldap.get("enabled", False),
        "server_url": ldap.get("server_url", ""),
        "bind_dn": ldap.get("bind_dn", ""),
        "bind_password": ldap.get("bind_password", ""),
        "base_dn": ldap.get("base_dn", ""),
        "user_search_filter": ldap.get("user_search_filter", "(sAMAccountName={username})"),
        "default_role": ldap.get("default_role", "user"),
    })


@app.route("/api/ldap", methods=["POST"])
@admin_required
def update_ldap():
    data = request.json
    if data.get("default_role") not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400
    config["ldap"] = {
        "enabled": bool(data.get("enabled")),
        "server_url": data.get("server_url", "").strip(),
        "bind_dn": data.get("bind_dn", "").strip(),
        "bind_password": data.get("bind_password", ""),
        "base_dn": data.get("base_dn", "").strip(),
        "user_search_filter": data.get("user_search_filter", "").strip() or "(sAMAccountName={username})",
        "default_role": data.get("default_role", "user"),
    }
    save_config()
    return jsonify({"success": True})


@app.route("/api/ldap/test", methods=["POST"])
@admin_required
def test_ldap():
    if not LDAP3_AVAILABLE:
        return jsonify({"error": "ldap3 library is not installed"}), 400
    data = request.json
    server_url = data.get("server_url", "").strip()
    bind_dn = data.get("bind_dn", "").strip()
    bind_password = data.get("bind_password", "")
    base_dn = data.get("base_dn", "").strip()
    search_filter = data.get("user_search_filter", "").strip() or "(sAMAccountName={username})"

    if not server_url or not base_dn:
        return jsonify({"error": "server_url and base_dn are required"}), 400

    try:
        server = Server(server_url, get_info=ALL)
        conn = Connection(server, bind_dn, bind_password, auto_bind=True)
        test_filter = search_filter.replace("{username}", "test")
        conn.search(base_dn, test_filter, search_scope=SUBTREE, size_limit=1)
        conn.unbind()
        return jsonify({"success": True, "message": "Connected to LDAP server successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── Config API ───

@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    load_config()
    return jsonify({
        "ebook": {
            "url": config["ebook"].get("url", ""),
            "api_key": config["ebook"].get("api_key", ""),
            "server_software": config["ebook"].get("server_software", "readarr"),
            "configured": bool(config["ebook"].get("url") and config["ebook"].get("api_key")),
        },
        "audiobook": {
            "url": config["audiobook"].get("url", ""),
            "api_key": config["audiobook"].get("api_key", ""),
            "server_software": config["audiobook"].get("server_software", "readarr"),
            "configured": bool(config["audiobook"].get("url") and config["audiobook"].get("api_key")),
        },
    })


@app.route("/api/config", methods=["POST"])
@admin_required
def update_config():
    data = request.json
    server_type = data.get("server_type")
    if server_type not in ("ebook", "audiobook"):
        return jsonify({"error": "server_type must be 'ebook' or 'audiobook'"}), 400

    config[server_type] = {
        "url": data.get("url", "").strip(),
        "api_key": data.get("api_key", "").strip(),
        "server_software": data.get("server_software", "readarr"),
    }
    save_config()
    return jsonify({"success": True})


@app.route("/api/library")
@login_required
def get_library():
    """Return the set of downloaded book titles across all configured servers."""
    titles = set()
    for server_type in ("ebook", "audiobook"):
        client = get_client(server_type)
        if not client:
            continue
        try:
            titles |= client.get_downloaded_titles()
        except Exception as e:
            app.logger.warning("Failed to fetch library for %s: %s", server_type, e)
    return jsonify(list(titles))


@app.route("/api/config/test", methods=["POST"])
@admin_required
def test_config():
    data = request.json
    url = data.get("url", "").strip()
    api_key = data.get("api_key", "").strip()
    if not url or not api_key:
        return jsonify({"error": "url and api_key are required"}), 400
    try:
        server_software = data.get("server_software", "readarr")
        if server_software == "bookshelf":
            client = BookshelfClient(url, api_key)
        elif server_software == "lazylibrarian":
            client = LazyLibrarianClient(url, api_key)
        else:
            client = ReadarrClient(url, api_key)
        status = client.test_connection()
        return jsonify({"success": True, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── Search API (Open Library) ───

@app.route("/api/search")
@login_required
def search_books():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        resp = http_requests.get(
            "https://openlibrary.org/search.json",
            params={"q": query, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for doc in data.get("docs", []):
            # Extract ISBNs
            isbns = doc.get("isbn", [])
            isbn_13 = next((i for i in isbns if len(i) == 13), "")
            isbn_10 = next((i for i in isbns if len(i) == 10), "")
            if not isbn_13 and not isbn_10 and isbns:
                isbn_13 = isbns[0]

            # Build cover URL from cover_i
            cover_i = doc.get("cover_i")
            cover = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else ""

            # Build a unique ID from the Open Library key
            ol_key = doc.get("key", "")
            ol_id = ol_key.split("/")[-1] if ol_key else ""

            # Year as string to match existing publishedDate format
            year = doc.get("first_publish_year")
            published_date = str(year) if year else ""

            results.append({
                "id": ol_id,
                "title": doc.get("title", "Unknown"),
                "authors": doc.get("author_name", []),
                "publishedDate": published_date,
                "description": "",
                "pageCount": doc.get("number_of_pages_median", 0),
                "categories": doc.get("subject", [])[:5] if doc.get("subject") else [],
                "isbn_13": isbn_13,
                "isbn_10": isbn_10,
                "cover": cover,
                "language": (doc.get("language", ["en"])[0]
                             if doc.get("language") else "en"),
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profiles/<server_type>")
@login_required
def get_profiles(server_type):
    client = get_client(server_type)
    if not client:
        return jsonify({"error": f"{server_type} server not configured"}), 400
    try:
        profiles = client.get_quality_profiles()
        return jsonify(profiles)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rootfolders/<server_type>")
@login_required
def get_root_folders(server_type):
    client = get_client(server_type)
    if not client:
        return jsonify({"error": f"{server_type} server not configured"}), 400
    try:
        folders = client.get_root_folders()
        return jsonify(folders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Download / Request API ───

@app.route("/api/request", methods=["POST"])
@login_required
def create_request():
    data = request.json
    server_type = data.get("server_type")
    book_data = data.get("book")
    quality_profile_id = data.get("quality_profile_id")
    root_folder = data.get("root_folder")

    if not all([server_type, book_data, quality_profile_id, root_folder]):
        return jsonify({"error": "Missing required fields"}), 400

    client = get_client(server_type)
    if not client:
        return jsonify({"error": f"{server_type} server not configured"}), 400

    title = book_data.get("title", "Unknown")
    authors = book_data.get("authors", [])
    author_name = authors[0] if authors else "Unknown"
    cover_url = book_data.get("cover", "")
    isbn = book_data.get("isbn_13") or book_data.get("isbn_10", "")

    request_entry = {
        "id": int(time.time() * 1000),
        "title": title,
        "author": author_name,
        "cover_url": cover_url,
        "server_type": server_type,
        "quality_profile_id": quality_profile_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
    }

    try:
        # Try to find the book via the configured server's own lookup.
        # The server requires its own metadata ID (e.g. Goodreads) as
        # foreignBookId — Open Library IDs will always be rejected.
        readarr_books = []
        if isbn:
            readarr_books = client.lookup_by_isbn(isbn)
        if not readarr_books:
            readarr_books = client.search_books(f"{title} {author_name}")
        if not readarr_books:
            # Retry with title only — combined queries sometimes miss
            readarr_books = client.search_books(title)

        if not readarr_books:
            raise ValueError(
                f"'{title}' by {author_name} was not found in the configured "
                f"server's catalog. Try searching for it directly in your "
                f"Readarr/Bookshelf instance to confirm it's available."
            )

        # Use the full server lookup result — it has the correct metadata
        # IDs, editions, images, etc. that the server expects.
        readarr_book = readarr_books[0]
        if not readarr_book.get("author", {}).get("authorName"):
            readarr_book["author"] = {
                "authorName": author_name,
                "foreignAuthorId": "",
            }
        app.logger.info(
            "Server match for '%s': title='%s', author=%s",
            title, readarr_book.get("title"), json.dumps(readarr_book.get("author", {})),
        )
        request_entry["status"] = "processing"

        result = client.add_book(readarr_book, quality_profile_id, root_folder)
        request_entry["readarr_book_id"] = result.get("id")
    except Exception as e:
        request_entry["status"] = "error"
        request_entry["error"] = str(e)

    with lock:
        requests_history.insert(0, request_entry)
        save_requests()

    return jsonify(request_entry)


@app.route("/api/requests", methods=["GET"])
@login_required
def get_requests():
    with lock:
        load_requests()
        return jsonify(requests_history)


@app.route("/api/requests/refresh", methods=["POST"])
@login_required
def refresh_requests():
    """Refresh the status of all processing/downloading requests."""
    with lock:
        for req in requests_history:
            if req["status"] in ("completed", "error"):
                continue
            client = get_client(req["server_type"])
            if not client:
                continue
            try:
                queue = client.get_queue()
                req_book_id = req.get("readarr_book_id")
                matching = [
                    q for q in queue
                    if q.get("title", "").lower() == req["title"].lower()
                    or (req_book_id and str(q.get("bookId")) == str(req_book_id))
                ]
                if matching:
                    q = matching[0]
                    status = q.get("status", "").lower()
                    size = q.get("size", 0)
                    size_left = q.get("sizeleft", 0)
                    # Book is in the download queue
                    req["status"] = "downloading"
                    if size > 0:
                        req["progress"] = round((1 - size_left / size) * 100)
                    if status == "completed":
                        req["status"] = "completed"
                        req["progress"] = 100
                    elif status in ("failed", "warning"):
                        req["status"] = "error"
                        req["error"] = q.get("errorMessage", "Download failed")
                else:
                    # Check Readarr history
                    book_id = req.get("readarr_book_id")
                    if book_id:
                        book = client.get_book_status(book_id)
                        if book and book.get("statistics"):
                            stats = book["statistics"]
                            if stats.get("bookFileCount", 0) > 0:
                                req["status"] = "completed"
                                req["progress"] = 100
            except Exception as e:
                pass  # Keep current status on error
        save_requests()
    return jsonify(requests_history)


@app.route("/api/requests/<int:request_id>", methods=["DELETE"])
@login_required
def delete_request(request_id):
    with lock:
        global requests_history
        requests_history = [r for r in requests_history if r["id"] != request_id]
        save_requests()
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
