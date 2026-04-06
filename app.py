import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

import requests as http_requests
from flask import Flask, jsonify, render_template, request
from readarr import ReadarrClient

app = Flask(__name__)

# Configure logging to stdout so it shows in docker logs
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
app.logger.setLevel(logging.DEBUG)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "config.json")
REQUESTS_FILE = os.path.join(os.path.dirname(__file__), "data", "requests.json")

# In-memory state
config = {"ebook": {}, "audiobook": {}}
requests_history = []
lock = threading.Lock()


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
        with open(CONFIG_FILE) as f:
            config = json.load(f)


def save_requests():
    ensure_data_dir()
    with open(REQUESTS_FILE, "w") as f:
        json.dump(requests_history, f, indent=2, default=str)


def load_requests():
    global requests_history
    if os.path.exists(REQUESTS_FILE):
        with open(REQUESTS_FILE) as f:
            requests_history = json.load(f)


load_config()
load_requests()


def get_client(server_type: str) -> ReadarrClient | None:
    """Get a ReadarrClient for the given server type."""
    server = config.get(server_type, {})
    if server.get("url") and server.get("api_key"):
        return ReadarrClient(server["url"], server["api_key"])
    return None


# ---------- Pages ----------

@app.route("/")
def index():
    return render_template("index.html")


# ---------- Config API ----------

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "ebook": {
            "url": config["ebook"].get("url", ""),
            "api_key": config["ebook"].get("api_key", ""),
            "configured": bool(config["ebook"].get("url") and config["ebook"].get("api_key")),
        },
        "audiobook": {
            "url": config["audiobook"].get("url", ""),
            "api_key": config["audiobook"].get("api_key", ""),
            "configured": bool(config["audiobook"].get("url") and config["audiobook"].get("api_key")),
        },
    })


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.json
    server_type = data.get("server_type")
    if server_type not in ("ebook", "audiobook"):
        return jsonify({"error": "server_type must be 'ebook' or 'audiobook'"}), 400

    config[server_type] = {
        "url": data.get("url", "").strip(),
        "api_key": data.get("api_key", "").strip(),
    }
    save_config()
    return jsonify({"success": True})


@app.route("/api/config/test", methods=["POST"])
def test_config():
    data = request.json
    url = data.get("url", "").strip()
    api_key = data.get("api_key", "").strip()
    if not url or not api_key:
        return jsonify({"error": "url and api_key are required"}), 400
    try:
        client = ReadarrClient(url, api_key)
        status = client.test_connection()
        return jsonify({"success": True, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------- Search API (Google Books) ----------

@app.route("/api/search")
def search_books():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        resp = http_requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": query, "maxResults": 20},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("items", []):
            info = item.get("volumeInfo", {})
            identifiers = info.get("industryIdentifiers", [])
            isbn_13 = ""
            isbn_10 = ""
            for ident in identifiers:
                if ident.get("type") == "ISBN_13":
                    isbn_13 = ident["identifier"]
                elif ident.get("type") == "ISBN_10":
                    isbn_10 = ident["identifier"]
            cover = info.get("imageLinks", {}).get("thumbnail", "")
            if cover:
                cover = cover.replace("http://", "https://")
            results.append({
                "id": item.get("id", ""),
                "title": info.get("title", "Unknown"),
                "authors": info.get("authors", []),
                "publishedDate": info.get("publishedDate", ""),
                "description": info.get("description", ""),
                "pageCount": info.get("pageCount", 0),
                "categories": info.get("categories", []),
                "isbn_13": isbn_13,
                "isbn_10": isbn_10,
                "cover": cover,
                "language": info.get("language", "en"),
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profiles/<server_type>")
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
def get_root_folders(server_type):
    client = get_client(server_type)
    if not client:
        return jsonify({"error": f"{server_type} server not configured"}), 400
    try:
        folders = client.get_root_folders()
        return jsonify(folders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------- Download / Request API ----------

@app.route("/api/request", methods=["POST"])
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
        # First, try to find the book in Readarr via ISBN lookup
        readarr_books = []
        if isbn:
            readarr_books = client.lookup_by_isbn(isbn)
        if not readarr_books:
            readarr_books = client.lookup_by_title(f"{title} {author_name}")

        if readarr_books:
            # Use the full Readarr lookup result — it has the correct
            # editions, images, links, etc. that Readarr expects.
            # We only override the author if Readarr returned empty data.
            readarr_book = readarr_books[0]
            if not readarr_book.get("author", {}).get("authorName"):
                readarr_book["author"] = {
                    "authorName": author_name,
                    "foreignAuthorId": "",
                }
            app.logger.info(
                "Readarr match for '%s': title='%s', author=%s",
                title, readarr_book.get("title"), json.dumps(readarr_book.get("author", {})),
            )
            request_entry["status"] = "processing"
        else:
            # Fallback: build data from Google Books
            readarr_book = {
                "title": title,
                "author": {
                    "authorName": author_name,
                    "foreignAuthorId": "",
                },
                "foreignBookId": isbn or book_data.get("id", ""),
            }
            app.logger.info("No Readarr match, using Google Books fallback for '%s' by '%s'", title, author_name)
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
def get_requests():
    with lock:
        return jsonify(requests_history)


@app.route("/api/requests/refresh", methods=["POST"])
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
def delete_request(request_id):
    with lock:
        global requests_history
        requests_history = [r for r in requests_history if r["id"] != request_id]
        save_requests()
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
