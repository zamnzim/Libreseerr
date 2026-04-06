import requests
from typing import Optional


class ReadarrClient:
    """Client for interacting with a Readarr instance."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": api_key})

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def test_connection(self) -> dict:
        """Test connection to the Readarr instance."""
        resp = self.session.get(self._url("/system/status"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def search_books(self, query: str) -> list:
        """Search for books using the Readarr lookup endpoint."""
        resp = self.session.get(
            self._url("/book/lookup"), params={"term": query}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def lookup_by_isbn(self, isbn: str) -> list:
        """Look up a book in Readarr by ISBN."""
        resp = self.session.get(
            self._url("/book/lookup"), params={"term": f"isbn:{isbn}"}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def lookup_author(self, name: str) -> list:
        """Look up an author in Readarr by name."""
        resp = self.session.get(
            self._url("/author/lookup"), params={"term": name}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def get_quality_profiles(self) -> list:
        """Get available quality profiles."""
        resp = self.session.get(self._url("/qualityprofile"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_root_folders(self) -> list:
        """Get configured root folders."""
        resp = self.session.get(self._url("/rootfolder"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def add_book(self, book_data: dict, quality_profile_id: int, root_folder: str) -> dict:
        """Add a book to Readarr for downloading."""
        # Build the author payload from book data
        author_data = book_data.get("author", {})
        author = {
            "authorName": author_data.get("authorName", "Unknown"),
            "foreignAuthorId": author_data.get("foreignAuthorId", ""),
            "qualityProfileId": quality_profile_id,
            "metadataProfileId": 1,
            "rootFolderPath": root_folder,
            "addOptions": {
                "monitor": "all",
                "searchForMissingAlbums": True,
            },
        }

        # Add the author first
        resp = self.session.post(
            self._url("/author"), json=author, timeout=30
        )
        resp.raise_for_status()
        added_author = resp.json()

        # Add the book
        book_payload = {
            "foreignBookId": book_data.get("foreignBookId", ""),
            "title": book_data.get("title", "Unknown"),
            "authorId": added_author.get("id"),
            "edition": {
                "title": book_data.get("title", "Unknown"),
                "foreignEditionId": book_data.get("foreignBookId", ""),
                "monitored": True,
            },
            "monitored": True,
            "addOptions": {
                "addType": "automatic",
                "searchForNewBook": True,
            },
        }

        resp = self.session.post(
            self._url("/book"), json=book_payload, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def get_queue(self) -> list:
        """Get current download queue."""
        resp = self.session.get(self._url("/queue"), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("records", data) if isinstance(data, dict) else data

    def get_book_status(self, book_id: int) -> Optional[dict]:
        """Get the status of a specific book."""
        resp = self.session.get(self._url(f"/book/{book_id}"), timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_history(self) -> list:
        """Get download history."""
        resp = self.session.get(
            self._url("/history"), params={"pageSize": 50}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("records", data) if isinstance(data, dict) else data
