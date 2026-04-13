import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


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

    def search_books(self, query: str) -> list:
        """Search for books by name using findBook."""
        result = self._get("findBook", name=query)
        if not isinstance(result, list):
            return []
        return [
            {
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
            for b in result
        ]

    def lookup_by_isbn(self, isbn: str) -> list:
        """Look up a book by ISBN using searchItem."""
        result = self._get("searchItem", item=isbn)
        if not isinstance(result, list):
            return []
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "author": {
                    "authorName": b.get("authorname", "Unknown"),
                    "foreignAuthorId": b.get("authorid", ""),
                },
                "foreignBookId": b.get("bookid", ""),
                "foreignEditionId": b.get("bookisbn", ""),
                "overview": b.get("bookdesc", ""),
                "releaseDate": b.get("bookdate", ""),
            }
            for b in result
        ]

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
        """Add a book to LazyLibrarian and mark it as wanted."""
        book_id = book_data.get("foreignBookId", "")
        title = book_data.get("title", "Unknown")

        if not book_id:
            # Try to find the book first
            results = self.search_books(f"{title} {book_data.get('author', {}).get('authorName', '')}")
            if results:
                book_id = results[0].get("foreignBookId", "")

        if not book_id:
            raise ValueError(f"Could not find book ID for '{title}' in LazyLibrarian")

        # Add the book to the database
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
