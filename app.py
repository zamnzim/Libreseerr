import json
import logging
import os
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime
from functools import wraps
from tempfile import NamedTemporaryFile

import requests as http_requests
from flask import Flask, jsonify, render_template, request, redirect, url_for
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from filelock import FileLock
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from ldap3 import Server, Connection, ALL, SUBTREE
    LDAP3_AVAILABLE = True
except ImportError:
    LDAP3_AVAILABLE = False

try:
    import oidc as oidc_helper
    OIDC_AVAILABLE = True
except ImportError:
    OIDC_AVAILABLE = False

from bookshelf import BookshelfClient
from readarr import ReadarrClient
from lazylibrarian import LazyLibrarianClient

app = Flask(__name__)

# Honor X-Forwarded-* headers from a reverse proxy (haproxy, nginx, traefik, etc.)
# so url_for(..., _external=True) generates correct https URLs. Required for the
# OIDC redirect_uri to match what's registered at the IdP when the app sits
# behind a proxy. No-op when no proxy is in front (headers absent).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


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
REQUESTS_LOCK_FILE = os.path.join(os.path.dirname(__file__), "data", "requests.lock")
USERS_FILE = os.path.join(os.path.dirname(__file__), "data", "users.json")
REQUEST_CLAIM_TTL_SECONDS = int(os.environ.get("REQUEST_CLAIM_TTL_SECONDS", "120"))
REQUEST_RECOVERY_SCAN_INTERVAL_SECONDS = int(
    os.environ.get("REQUEST_RECOVERY_SCAN_INTERVAL_SECONDS", "30")
)
REQUEST_RECOVERY_BATCH_SIZE = int(os.environ.get("REQUEST_RECOVERY_BATCH_SIZE", "10"))

# In-memory state
config = {"ebook": {}, "audiobook": {}, "ldap": {}, "oidc": {}}
requests_history = []
users = []
lock = threading.RLock()
requests_file_lock = FileLock(REQUESTS_LOCK_FILE, timeout=10)
active_request_workers = set()
active_request_workers_lock = threading.Lock()
recovery_scan_lock = threading.Lock()
last_recovery_scan_at = 0.0

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


def utcnow_isoformat() -> str:
    return datetime.utcnow().isoformat()


def write_json_atomic(path: str, payload):
    ensure_data_dir()
    with NamedTemporaryFile("w", dir=os.path.dirname(path), delete=False) as tmp:
        json.dump(payload, tmp, indent=2, default=str)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = tmp.name
    os.replace(temp_path, path)


def load_requests_from_disk_unlocked() -> list:
    if not os.path.exists(REQUESTS_FILE):
        return []
    try:
        with open(REQUESTS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_requests_to_disk_unlocked(data: list):
    write_json_atomic(REQUESTS_FILE, data)


def read_requests_snapshot() -> list:
    global requests_history

    ensure_data_dir()
    with lock:
        with requests_file_lock:
            requests_history = load_requests_from_disk_unlocked()
            return deepcopy(requests_history)


def mutate_requests_history(mutation):
    global requests_history

    ensure_data_dir()
    with lock:
        with requests_file_lock:
            current_requests = load_requests_from_disk_unlocked()
            result = mutation(current_requests)
            save_requests_to_disk_unlocked(current_requests)
            requests_history = current_requests
            return result


def get_request_entry(request_id: int) -> dict | None:
    for req in read_requests_snapshot():
        if req.get("id") == request_id:
            return req
    return None


def update_request_entry(request_id: int, **updates) -> dict | None:
    updated_request = None
    now = utcnow_isoformat()

    def mutate(current_requests):
        nonlocal updated_request
        for req in current_requests:
            if req.get("id") == request_id:
                req.update(updates)
                req["updated_at"] = now
                updated_request = deepcopy(req)
                break
        return updated_request

    mutate_requests_history(mutate)
    return updated_request


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_request_claim_stale(req: dict, now: datetime | None = None) -> bool:
    claimed_at = parse_iso_datetime(req.get("claimed_at"))
    if claimed_at is None:
        return True
    now = now or datetime.utcnow()
    return (now - claimed_at).total_seconds() >= REQUEST_CLAIM_TTL_SECONDS


def claim_request(request_id: int, *, claimed_by: str) -> dict | None:
    claimed_request = None
    now = utcnow_isoformat()

    def mutate(current_requests):
        nonlocal claimed_request
        current_time = datetime.utcnow()
        for req in current_requests:
            if req.get("id") != request_id:
                continue
            if req.get("status") != "pending":
                return None

            already_claimed = req.get("claimed_by")
            stale_claim = is_request_claim_stale(req, current_time)
            if already_claimed and not stale_claim:
                return None

            req["claimed_by"] = claimed_by
            req["claimed_at"] = now
            req["attempts"] = int(req.get("attempts", 0)) + 1
            req["updated_at"] = now
            claimed_request = deepcopy(req)
            return claimed_request
        return None

    mutate_requests_history(mutate)
    return claimed_request


def claim_recoverable_requests(limit: int = REQUEST_RECOVERY_BATCH_SIZE) -> list[int]:
    claimed_ids = []
    now = utcnow_isoformat()

    def mutate(current_requests):
        current_time = datetime.utcnow()
        for req in current_requests:
            if len(claimed_ids) >= limit:
                break
            if req.get("status") != "pending":
                continue
            if req.get("claimed_by") and not is_request_claim_stale(req, current_time):
                continue

            req["claimed_by"] = f"recovery:{os.getpid()}"
            req["claimed_at"] = now
            req["attempts"] = int(req.get("attempts", 0)) + 1
            req["updated_at"] = now
            claimed_ids.append(req["id"])

    mutate_requests_history(mutate)
    return claimed_ids

def build_request_book_payload(book_data: dict) -> dict:
    return {
        "id": book_data.get("id", ""),
        "title": book_data.get("title", "Unknown"),
        "authors": list(book_data.get("authors", [])),
        "cover": book_data.get("cover", ""),
        "isbn_10": book_data.get("isbn_10", ""),
        "isbn_13": book_data.get("isbn_13", ""),
    }


def request_worker_identity(request_id: int) -> str:
    return f"{os.getpid()}:{threading.get_ident()}:{request_id}"


def start_request_worker(request_id: int, *, already_claimed: bool = False):
    with active_request_workers_lock:
        if request_id in active_request_workers:
            return
        active_request_workers.add(request_id)

    try:
        worker = threading.Thread(
            target=process_request_background,
            args=(request_id,),
            kwargs={"already_claimed": already_claimed},
            daemon=True,
            name=f"request-{request_id}",
        )
        worker.start()
    except Exception:
        with active_request_workers_lock:
            active_request_workers.discard(request_id)
        raise


def maybe_schedule_request_recovery(force: bool = False):
    global last_recovery_scan_at

    now = time.monotonic()
    with recovery_scan_lock:
        if (
            not force
            and now - last_recovery_scan_at < REQUEST_RECOVERY_SCAN_INTERVAL_SECONDS
        ):
            return
        last_recovery_scan_at = now

    claimed_ids = claim_recoverable_requests()
    if claimed_ids:
        app.logger.info("Recovered %d pending request(s): %s", len(claimed_ids), claimed_ids)

    for request_id in claimed_ids:
        start_request_worker(request_id, already_claimed=True)


def process_request_background(request_id: int, *, already_claimed: bool = False):
    try:
        if already_claimed:
            update_request_entry(
                request_id,
                claimed_by=request_worker_identity(request_id),
                claimed_at=utcnow_isoformat(),
            )
            request_entry = get_request_entry(request_id)
        else:
            request_entry = claim_request(
                request_id,
                claimed_by=request_worker_identity(request_id),
            )

        if not request_entry or request_entry.get("status") != "pending":
            return

        book_data = request_entry.get("book_payload", {})
        title = book_data.get("title", request_entry.get("title", "Unknown"))
        authors = book_data.get("authors", [])
        author_name = authors[0] if authors else request_entry.get("author", "Unknown")
        isbn = book_data.get("isbn_13") or book_data.get("isbn_10") or request_entry.get("isbn", "")
        server_type = request_entry.get("server_type")
        quality_profile_id = request_entry.get("quality_profile_id")
        root_folder = request_entry.get("root_folder")

        client = get_client(server_type)
        if not client:
            update_request_entry(
                request_id,
                status="error",
                error=f"{server_type} server is not configured",
                claimed_by=None,
                claimed_at=None,
            )
            return

        matched_books = []
        if isbn:
            try:
                matched_books = client.lookup_by_isbn(isbn)
            except Exception as exc:
                app.logger.warning(
                    "Request %s: ISBN lookup failed for %r: %s",
                    request_id,
                    isbn,
                    exc,
                )
            if matched_books:
                update_request_entry(request_id, claimed_at=utcnow_isoformat())

        if not matched_books:
            try:
                matched_books = client.search_books(f"{title} {author_name}")
            except Exception as exc:
                app.logger.warning(
                    "Request %s: title/author lookup failed for %r by %r: %s",
                    request_id,
                    title,
                    author_name,
                    exc,
                )
            if matched_books:
                update_request_entry(request_id, claimed_at=utcnow_isoformat())

        if matched_books:
            server_book = matched_books[0]
            if not server_book.get("author", {}).get("authorName"):
                server_book["author"] = {
                    "authorName": author_name,
                    "foreignAuthorId": "",
                }
            app.logger.info(
                "Request %s: matched %r to backend metadata result %r",
                request_id,
                title,
                server_book.get("title"),
            )
        else:
            server_book = {
                "title": title,
                "author": {
                    "authorName": author_name,
                    "foreignAuthorId": "",
                },
                "foreignBookId": isbn or book_data.get("id", ""),
            }
            app.logger.info(
                "Request %s: no backend metadata match; using fallback for %r by %r",
                request_id,
                title,
                author_name,
            )

        update_request_entry(request_id, claimed_at=utcnow_isoformat(), error=None)
        result = client.add_book(server_book, quality_profile_id, root_folder)

        update_request_entry(
            request_id,
            status="processing",
            readarr_book_id=result.get("id"),
            error=None,
            claimed_by=None,
            claimed_at=None,
        )
        app.logger.info(
            "Request %s: successfully sent %r to %s",
            request_id,
            title,
            server_type,
        )
    except Exception as exc:
        request_entry = get_request_entry(request_id) or {}
        app.logger.exception(
            "Request %s: failed while processing %r by %r",
            request_id,
            request_entry.get("title", "Unknown"),
            request_entry.get("author", "Unknown"),
        )
        update_request_entry(
            request_id,
            status="error",
            error=str(exc),
            claimed_by=None,
            claimed_at=None,
        )
    finally:
        with active_request_workers_lock:
            active_request_workers.discard(request_id)


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
    def mutate(current_requests):
        current_requests[:] = deepcopy(requests_history)

    mutate_requests_history(mutate)


def load_requests():
    global requests_history
    requests_history = read_requests_snapshot()
    return requests_history


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

if OIDC_AVAILABLE:
    oidc_helper.init_oidc(app, config)

maybe_schedule_request_recovery(force=True)


@app.before_request
def reload_state():
    """Reload shared state from disk so multiple Gunicorn workers stay in sync."""
    load_config()
    load_requests()
    load_users()
    maybe_schedule_request_recovery()
    # Re-init the OIDC client if config changed in another worker. init_oidc
    # is idempotent — registers/unregisters the client based on enabled flag.
    if OIDC_AVAILABLE and not app.extensions.get("oidc_client") and config.get("oidc", {}).get("enabled"):
        oidc_helper.init_oidc(app, config)


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


# ─── OIDC Config API ───

@app.route("/api/oidc", methods=["GET"])
@admin_required
def get_oidc():
    if not OIDC_AVAILABLE:
        return jsonify({
            "available": False,
            "enabled": False, "display_name": "OIDC", "issuer_url": "",
            "client_id": "", "client_secret": "", "scope": "openid profile email",
            "username_claim": "preferred_username", "default_role": "user",
            "auto_create_users": False, "auto_redirect": False,
        })
    defaults = oidc_helper.get_oidc_defaults()
    oidc = config.get("oidc", defaults)
    return jsonify({
        "available": True,
        "enabled": oidc.get("enabled", False),
        "display_name": oidc.get("display_name", defaults["display_name"]),
        "issuer_url": oidc.get("issuer_url", ""),
        "client_id": oidc.get("client_id", ""),
        "client_secret": oidc.get("client_secret", ""),
        "scope": oidc.get("scope", defaults["scope"]),
        "username_claim": oidc.get("username_claim", defaults["username_claim"]),
        "default_role": oidc.get("default_role", "user"),
        "auto_create_users": oidc.get("auto_create_users", False),
        "auto_redirect": oidc.get("auto_redirect", False),
    })


@app.route("/api/oidc", methods=["POST"])
@admin_required
def update_oidc():
    if not OIDC_AVAILABLE:
        return jsonify({"error": "authlib library is not installed"}), 400
    data = request.json
    if data.get("default_role") not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400
    config["oidc"] = {
        "enabled": bool(data.get("enabled")),
        "display_name": data.get("display_name", "").strip() or "OIDC",
        "issuer_url": data.get("issuer_url", "").strip(),
        "client_id": data.get("client_id", "").strip(),
        "client_secret": data.get("client_secret", ""),
        "scope": data.get("scope", "").strip() or "openid profile email",
        "username_claim": data.get("username_claim", "").strip() or "preferred_username",
        "default_role": data.get("default_role", "user"),
        "auto_create_users": bool(data.get("auto_create_users")),
        "auto_redirect": bool(data.get("auto_redirect")),
    }
    save_config()
    # Re-register the OAuth client so the new config takes effect immediately.
    oidc_helper.init_oidc(app, config)
    return jsonify({"success": True})


@app.route("/api/oidc/test", methods=["POST"])
@admin_required
def test_oidc():
    if not OIDC_AVAILABLE:
        return jsonify({"error": "authlib library is not installed"}), 400
    data = request.json
    issuer_url = data.get("issuer_url", "").strip()
    if not issuer_url:
        return jsonify({"error": "issuer_url is required"}), 400
    try:
        doc = oidc_helper.fetch_discovery(issuer_url)
        ok, msg = oidc_helper.validate_discovery(doc)
        if not ok:
            return jsonify({"error": msg}), 400
        return jsonify({
            "success": True,
            "message": f"Discovery OK. Issuer: {doc.get('issuer', '')}",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── OIDC Auth Flow ───

@app.route("/api/auth/oidc/login")
def oidc_login():
    """Initiates the OIDC redirect to the IdP."""
    if not OIDC_AVAILABLE or not config.get("oidc", {}).get("enabled"):
        return redirect(url_for("login"))
    client = oidc_helper.get_client(app)
    if client is None:
        # Configured but client failed to init (bad issuer, etc.) — fall back.
        return redirect(url_for("login"))
    redirect_uri = url_for("oidc_callback", _external=True)
    return client.authorize_redirect(redirect_uri)


@app.route("/api/auth/oidc/callback")
def oidc_callback():
    """Handles the redirect back from the IdP, exchanges code for tokens,
    finds or provisions the user, logs them in."""
    oidc_cfg = config.get("oidc", {})
    if not OIDC_AVAILABLE or not oidc_cfg.get("enabled"):
        return redirect(url_for("login"))
    client = oidc_helper.get_client(app)
    if client is None:
        return redirect(url_for("login") + "?error=oidc_not_initialized")
    try:
        token = client.authorize_access_token()
    except Exception as e:
        app.logger.warning("OIDC token exchange failed: %s", e)
        return redirect(url_for("login") + "?error=oidc_token_exchange_failed")

    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            userinfo = client.userinfo(token=token)
        except Exception as e:
            app.logger.warning("OIDC userinfo fetch failed: %s", e)
            return redirect(url_for("login") + "?error=oidc_userinfo_failed")

    username = oidc_helper.extract_username(userinfo, oidc_cfg.get("username_claim", "preferred_username"))
    if not username:
        app.logger.warning("OIDC token contains no usable username claim: %s", userinfo)
        return redirect(url_for("login") + "?error=oidc_no_username")

    existing = next((u for u in users if u["username"] == username), None)
    if not existing:
        if not oidc_cfg.get("auto_create_users"):
            app.logger.info("OIDC login rejected for '%s' — user does not exist and auto_create_users is off", username)
            return redirect(url_for("login") + "?error=account_not_found")
        existing = {
            "username": username,
            "password_hash": "oidc",
            "role": oidc_cfg.get("default_role", "user"),
            "auth_source": "oidc",
            "created_at": datetime.utcnow().isoformat(),
        }
        users.append(existing)
        save_users()
        app.logger.info("Auto-provisioned OIDC user '%s'", username)

    login_user(User(existing))
    return redirect(url_for("index"))


# ─── Auth provider discovery (for login page UI) ───

@app.route("/api/auth/providers", methods=["GET"])
def auth_providers():
    """Tells the login page which alt providers (beyond local username/password)
    are enabled, so it can render the appropriate buttons. Public — no auth
    required, since the login page is itself public."""
    oidc_cfg = config.get("oidc") or {}
    return jsonify({
        "oidc": {
            "enabled": bool(OIDC_AVAILABLE and oidc_cfg.get("enabled")),
            "display_name": oidc_cfg.get("display_name") or "OIDC",
            "auto_redirect": bool(oidc_cfg.get("auto_redirect")),
        },
    })


# ─── Config API ───

@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
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


# ─── Search & Discovery API (Open Library) ───

def _normalize_ol_doc(doc):
    """Normalize a single Open Library search.json doc to our book schema."""
    isbns = doc.get("isbn", [])
    isbn_13 = next((i for i in isbns if len(i) == 13), "")
    isbn_10 = next((i for i in isbns if len(i) == 10), "")
    if not isbn_13 and not isbn_10 and isbns:
        isbn_13 = isbns[0]

    cover_i = doc.get("cover_i")
    cover = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else ""

    ol_key = doc.get("key", "")
    ol_id = ol_key.split("/")[-1] if ol_key else ""

    year = doc.get("first_publish_year")
    published_date = str(year) if year else ""

    return {
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
    }


def _normalize_ol_subject_work(work):
    """Normalize a single work from Open Library /subjects/{subject}.json."""
    authors = [a.get("name", "") for a in work.get("authors", []) if a.get("name")]

    cover_id = work.get("cover_id")
    cover = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""

    ol_key = work.get("key", "")
    ol_id = ol_key.split("/")[-1] if ol_key else ""

    year = work.get("first_publish_year")
    published_date = str(year) if year else ""

    return {
        "id": ol_id,
        "title": work.get("title", "Unknown"),
        "authors": authors,
        "publishedDate": published_date,
        "description": "",
        "pageCount": 0,
        "categories": [],
        "isbn_13": "",
        "isbn_10": "",
        "cover": cover,
        "language": "en",
    }


# Category keys mapped to Open Library API details
_DISCOVER_CATEGORIES = {
    "new_releases":   ("search.json",  {"sort": "new", "limit": 20}),
    "trending":       ("search.json",  {"sort": "rating", "limit": 20}),
    "best_sellers":   ("search.json",  {"q": "subject:bestsellers", "sort": "rating", "limit": 20}),
    "classics":       ("search.json",  {"q": "subject:classics", "sort": "rating", "limit": 20}),
    "fiction":        ("subjects/fiction.json",          {"limit": 20}),
    "science_fiction":("subjects/science_fiction.json",  {"limit": 20}),
    "mystery":        ("subjects/mystery.json",          {"limit": 20}),
    "fantasy":        ("subjects/fantasy.json",          {"limit": 20}),
    "romance":        ("subjects/romance.json",          {"limit": 20}),
    "nonfiction":     ("subjects/non-fiction.json",      {"limit": 20}),
    "history":        ("subjects/history.json",          {"limit": 20}),
}


@app.route("/api/discover")
@login_required
def discover_books():
    category = request.args.get("category", "").strip()
    if not category or category not in _DISCOVER_CATEGORIES:
        return jsonify({"error": "Invalid category"}), 400
    try:
        endpoint, params = _DISCOVER_CATEGORIES[category]
        resp = http_requests.get(
            f"https://openlibrary.org/{endpoint}",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if endpoint == "search.json":
            results = [_normalize_ol_doc(doc) for doc in data.get("docs", [])]
        else:
            # /subjects/ endpoint returns a "works" array
            results = [_normalize_ol_subject_work(w) for w in data.get("works", [])]

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        results = [_normalize_ol_doc(doc) for doc in data.get("docs", [])]
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/availability")
@login_required
def check_availability():
    """Check which books are already on the configured ebook/audiobook servers."""
    result = {"ebook": {"isbns": [], "titles": []}, "audiobook": {"isbns": [], "titles": []}}

    for server_type in ("ebook", "audiobook"):
        client = get_client(server_type)
        if not client:
            continue
        try:
            books = client.get_books()
            isbns = set()
            titles = set()
            for book in books:
                title = ""
                # Readarr/Bookshelf format: editions array with isbn fields
                if isinstance(book.get("editions"), list):
                    for edition in book["editions"]:
                        for key in ("isbn13", "isbn_13"):
                            val = edition.get(key, "")
                            if val:
                                isbns.add(val)
                        for key in ("isbn10", "isbn_10"):
                            val = edition.get(key, "")
                            if val:
                                isbns.add(val)
                    # Also check top-level isbn fields
                    for key in ("isbn13", "isbn_13", "isbn10", "isbn_10"):
                        val = book.get(key, "")
                        if val:
                            isbns.add(val)
                    title = book.get("title", "")
                # LazyLibrarian format: flat dicts with bookisbn, bookname
                else:
                    isbn = book.get("bookisbn", book.get("isbn", ""))
                    if isbn:
                        isbns.add(isbn)
                    title = book.get("bookname", book.get("title", ""))
                if title:
                    titles.add(title.lower())
            result[server_type] = {
                "isbns": list(isbns),
                "titles": list(titles),
            }
        except Exception as e:
            app.logger.warning("Failed to get books from %s: %s", server_type, e)

    # Also include books with active requests (pending/processing/downloading)
    active_statuses = {"pending", "processing", "downloading"}
    requests_by_type = {"ebook": {"isbns": set(), "titles": set()}, "audiobook": {"isbns": set(), "titles": set()}}
    for req in read_requests_snapshot():
        if req.get("status") not in active_statuses:
            continue
        server = req.get("server_type", "")
        if server not in requests_by_type:
            continue
        isbn = req.get("isbn", "")
        if isbn:
            requests_by_type[server]["isbns"].add(isbn)
        title = req.get("title", "")
        if title:
            requests_by_type[server]["titles"].add(title.lower())

    result["ebook_requests"] = {
        "isbns": list(requests_by_type["ebook"]["isbns"]),
        "titles": list(requests_by_type["ebook"]["titles"]),
    }
    result["audiobook_requests"] = {
        "isbns": list(requests_by_type["audiobook"]["isbns"]),
        "titles": list(requests_by_type["audiobook"]["titles"]),
    }

    return jsonify(result)


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
    data = request.json or {}
    server_type = data.get("server_type")
    book_data = data.get("book")
    quality_profile_id = data.get("quality_profile_id")
    root_folder = data.get("root_folder")

    if not all([server_type, book_data, quality_profile_id, root_folder]):
        return jsonify({"error": "Missing required fields"}), 400

    if not get_client(server_type):
        return jsonify({"error": f"{server_type} server not configured"}), 400

    title = book_data.get("title", "Unknown")
    authors = book_data.get("authors", [])
    author_name = authors[0] if authors else "Unknown"
    cover_url = book_data.get("cover", "")
    isbn = book_data.get("isbn_13") or book_data.get("isbn_10") or ""
    now = utcnow_isoformat()
    request_id = int(time.time() * 1000)

    request_entry = {
        "id": request_id,
        "title": title,
        "author": author_name,
        "cover_url": cover_url,
        "server_type": server_type,
        "quality_profile_id": quality_profile_id,
        "root_folder": root_folder,
        "isbn": isbn,
        "status": "pending",
        "progress": 0,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "book_payload": build_request_book_payload(book_data),
        "claimed_by": f"request:{os.getpid()}",
        "claimed_at": now,
        "attempts": 1,
    }

    mutate_requests_history(lambda current_requests: current_requests.insert(0, request_entry))
    start_request_worker(request_id, already_claimed=True)

    app.logger.info(
        "Queued request %s instantly: %r by %r",
        request_id,
        title,
        author_name,
    )
    return jsonify(request_entry), 202


@app.route("/api/requests", methods=["GET"])
@login_required
def get_requests():
    return jsonify(read_requests_snapshot())


@app.route("/api/requests/refresh", methods=["POST"])
@login_required
def refresh_requests():
    """Refresh the status of all processing/downloading requests."""
    for req in read_requests_snapshot():
        if req.get("status") in ("completed", "error", "pending"):
            continue

        client = get_client(req.get("server_type"))
        if not client:
            continue

        try:
            queue = client.get_queue()
            req_book_id = req.get("readarr_book_id")
            matching = [
                q for q in queue
                if q.get("title", "").lower() == req.get("title", "").lower()
                or (req_book_id and str(q.get("bookId")) == str(req_book_id))
            ]
            if matching:
                queue_entry = matching[0]
                status = queue_entry.get("status", "").lower()
                size = queue_entry.get("size", 0)
                size_left = queue_entry.get("sizeleft", 0)
                progress = req.get("progress", 0)
                if size > 0:
                    progress = round((1 - size_left / size) * 100)

                updates = {
                    "status": "downloading",
                    "progress": progress,
                    "error": None,
                }
                if status == "completed":
                    updates["status"] = "completed"
                    updates["progress"] = 100
                elif status in ("failed", "warning"):
                    updates["status"] = "error"
                    updates["error"] = queue_entry.get("errorMessage", "Download failed")

                update_request_entry(req["id"], **updates)
                continue

            book_id = req.get("readarr_book_id")
            if book_id:
                book = client.get_book_status(book_id)
                if book and book.get("statistics"):
                    stats = book["statistics"]
                    if stats.get("bookFileCount", 0) > 0:
                        update_request_entry(
                            req["id"],
                            status="completed",
                            progress=100,
                            error=None,
                        )
        except Exception:
            continue

    return jsonify(read_requests_snapshot())


@app.route("/api/requests/<int:request_id>", methods=["DELETE"])
@login_required
def delete_request(request_id):
    def mutate(current_requests):
        current_requests[:] = [r for r in current_requests if r.get("id") != request_id]

    mutate_requests_history(mutate)
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
