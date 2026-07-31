import json
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# --- Request-resolution hardening -------------------------------------------
# Fixes three failure modes when handing a request to LazyLibrarian:
#   (1) findBook results[0] grabs "summary"/"study guide" editions instead of
#       the real book (e.g. "The Martian" -> "Book Summary of THE MARTIAN"),
#   (2) a non-LazyLibrarian id (e.g. an Open Library OL...W id) is passed to
#       addBook and silently does nothing -> request stuck on "processing",
#   (3) the ISBN lookup (searchItem) can return HTTP 500 and hang the caller.
_JUNK_TITLE_MARKERS = (
    "summary", "study guide", "studyguide", "reviewed by",
    "conversation starters", "key takeaways", "instaread", "quicklet",
    "blinkist", "sidekick by", "summaries", "trivia-on", "novel unit",
    "teacher's guide", "teachers guide", "discussion guide", "book club kit",
    "lesson plan",
)

_JUNK_AUTHOR_MARKERS = (
    "supersummary", "bookrags", "good prints", "good reads publishing",
    "instaread", "quicklet", "worth books", "everest media",
    "brightsummaries", "intro books", "hyper summary", "ant hive media",
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def _is_junk_title(title: str) -> bool:
    t = _norm(title)
    return any(m in t for m in _JUNK_TITLE_MARKERS)


def _is_junk_author(author_name: str) -> bool:
    a = _norm(author_name)
    return any(m in a for m in _JUNK_AUTHOR_MARKERS)


def _looks_like_ll_id(value) -> bool:
    """True for a plausible LazyLibrarian BookID: either a numeric GoodReads-style
    id, or an Open Library work id (e.g. OL21745884W), now that LazyLibrarian's own
    Primary Information Source is OpenLibrary."""
    if not value:
        return False
    s = str(value)
    if s.isdigit():
        return True
    return bool(re.fullmatch(r"OL\d+[A-Za-z]", s))


def _rank(books: list, query: str) -> list:
    """Drop junk editions (summaries etc.) and sort by how well title+author match the query."""
    qtokens = set(_norm(query).split())
    scored = []
    for b in books:
        if not b.get("foreignBookId"):
            continue
        if _is_junk_title(b.get("title", "")):
            continue
        if _is_junk_author((b.get("author") or {}).get("authorName", "")):
            continue
        ttokens = set(_norm(b.get("title", "")).split())
        atokens = set(_norm((b.get("author") or {}).get("authorName", "")).split())
        title_cov = len(ttokens & qtokens) / len(ttokens) if ttokens else 0.0
        author_hit = 1.0 if (atokens and (atokens & qtokens)) else 0.0
        scored.append((title_cov + 0.5 * author_hit, b))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored]


class LazyLibrarianClient:
    """Client for interacting with a LazyLibrarian instance."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()

    def _get(self, cmd: str, **params) -> dict | list | str:
        """Make a GET request to the LazyLibrarian API."""
        query = {"apikey": self.api_key, "cmd": cmd}
        query.update(params)
        resp = self.session.get(f"{self.base_url}/api", params=query, timeout=15)
        resp.raise_for_status()
        try:
            return resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            return resp.text.strip()

    def test_connection(self) -> dict:
        """Test connection to the LazyLibrarian instance."""
        result = self._get("getVersion")
        if isinstance(result, str):
            return {"version": result}
        if isinstance(result, list) and result:
            return result[0] if isinstance(result[0], dict) else {"version": str(result[0])}
        if isinstance(result, dict):
            return result
        return {"version": "unknown"}

    def _map_book(self, b: dict) -> dict:
        return {
            "title": b.get("bookname", "Unknown"),
            "author": {
                "authorName": b.get("authorname", "Unknown"),
                "foreignAuthorId": b.get("authorid", ""),
            },
            "foreignBookId": b.get("bookid", ""),
            "foreignEditionId": b.get("bookisbn", ""),
            "overview": b.get("bookdesc", ""),
            "releaseDate": b.get("bookdate", ""),
            "ratings": {"value": float(b.get("bookrate", 0))} if b.get("bookrate") else {},
        }

    def search_books(self, query: str) -> list:
        """Search for books by name using findBook. Junk editions (summaries/study
        guides) are dropped and the best title/author match is returned first."""
        result = self._get("findBook", name=query)
        if not isinstance(result, list):
            return []
        return _rank([self._map_book(b) for b in result], query)

    def lookup_by_isbn(self, isbn: str) -> list:
        """Look up a book by ISBN using searchItem. Returns [] on LazyLibrarian
        errors (searchItem can return HTTP 500) so callers fall back to name search."""
        try:
            result = self._get("searchItem", item=isbn)
        except requests.exceptions.RequestException as e:
            logger.warning("ISBN lookup (searchItem) failed for %s: %s", isbn, e)
            return []
        if not isinstance(result, list):
            return []
        return [self._map_book(b) for b in result]

    def lookup_author(self, name: str) -> list:
        """Look up an author by name using findAuthor."""
        result = self._get("findAuthor", name=name)
        if not isinstance(result, list):
            return []
        return [
            {
                "authorName": a.get("authorname", "Unknown"),
                "foreignAuthorId": a.get("authorid", ""),
            }
            for a in result
        ]

    def get_quality_profiles(self) -> list:
        """Return a synthetic quality profile since LazyLibrarian doesn't have them."""
        return [{"id": 1, "name": "Default"}]

    def get_root_folders(self) -> list:
        """Return a synthetic root folder since LazyLibrarian manages its own paths."""
        return [{"path": "/books"}]

    def add_book(self, book_data: dict, quality_profile_id: int, root_folder: str) -> dict:
        """Add a book to LazyLibrarian and mark it as wanted.

        Resolves the correct BookID robustly: an incoming numeric id is trusted only
        if its title is not an obvious junk edition; otherwise we re-resolve by
        title+author via findBook and take the best real match. Raises ValueError
        (loud, not silent) when only a non-LazyLibrarian id (e.g. an Open Library
        OL...W id) is available, so a bad request surfaces as an error instead of
        grabbing the wrong book.
        """
        title = book_data.get("title", "Unknown")
        author = (book_data.get("author") or {}).get("authorName", "") or ""
        incoming_id = str(book_data.get("foreignBookId", "") or "")

        if _looks_like_ll_id(incoming_id) and not _is_junk_title(title):
            book_id = incoming_id
        else:
            matches = self.search_books(f"{title} {author}".strip())
            book_id = matches[0].get("foreignBookId", "") if matches else ""

        if not _looks_like_ll_id(book_id):
            raise ValueError(
                f"No valid LazyLibrarian book id could be resolved for '{title}' by "
                f"'{author or 'unknown'}' (only non-matching/summary results or a "
                f"non-LazyLibrarian id '{incoming_id}'). Rejecting the request "
                f"instead of adding the wrong book."
            )

        logger.info("Adding book to LazyLibrarian: '%s' (id=%s)", title, book_id)
        add_result = self._get("addBook", id=book_id)
        logger.info("addBook result: %s", add_result)

        # Mark the book as wanted to trigger a search
        queue_result = self._get("queueBook", id=book_id, type="eBook")
        logger.info("queueBook result: %s", queue_result)

        return {
            "id": book_id,
            "title": title,
            "foreignBookId": book_id,
        }

    def get_queue(self) -> list:
        """Get wanted books (equivalent to a download queue)."""
        result = self._get("getWanted")
        if not isinstance(result, list):
            return []
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "status": "downloading",
                "size": 0,
                "sizeleft": 0,
                "bookId": b.get("bookid", ""),
            }
            for b in result
        ]

    def get_book_status(self, book_id: int) -> Optional[dict]:
        """Get the status of a specific book."""
        # LazyLibrarian doesn't have a direct "get book by ID" for status,
        # so we check snatched books
        result = self._get("getSnatched")
        if isinstance(result, list):
            for b in result:
                if str(b.get("bookid", "")) == str(book_id):
                    return {
                        "id": book_id,
                        "title": b.get("bookname", "Unknown"),
                        "statistics": {"bookFileCount": 1},
                    }
        return None

    def get_books(self) -> list:
        """Get all books from the LazyLibrarian library."""
        result = self._get("getBooks")
        if not isinstance(result, list):
            return []
        return result

    def get_history(self) -> list:
        """Get snatched/download history."""
        result = self._get("getSnatched")
        if not isinstance(result, list):
            return []
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "status": "completed",
                "date": b.get("added", ""),
            }
            for b in result
        ]
