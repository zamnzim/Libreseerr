from __future__ import annotations

import httpx

from .config import ReadarrTargetSettings


class ReadarrClient:
    def __init__(self, target: ReadarrTargetSettings):
        self.target = target

    async def request_book(self, title: str, author: str, goodreads_id: str | None = None, task_id: str | None = None) -> str:
        timeout = httpx.Timeout(20.0, connect=5.0, read=20.0, write=20.0, pool=5.0)
        headers = {'X-Api-Key': self.target.api_key}
        async with httpx.AsyncClient(timeout=timeout) as client:
            author_resource = await self._lookup_or_create_author(client, headers, author)
            book_resource = await self._lookup_book(client, headers, title)
            if not book_resource:
                raise ValueError(f'No Readarr book found for {title}')
            book_id = await self._ensure_book_exists(client, headers, author_resource, book_resource, goodreads_id)
            if book_id is not None and author_resource.get('id') is not None:
                await self._search_book(client, headers, author_resource['id'], book_id)
        return 'Author added, book added, search started'

    async def _search_book(self, client: httpx.AsyncClient, headers: dict[str, str], author_id: int, book_id: int) -> None:
        response = await client.post(f'{self.target.base_url}/api/v1/command', headers=headers, json={
            'name': 'SearchSingleBook',
            'authorIds': [author_id],
            'bookIds': [book_id],
        })
        if response.status_code >= 400:
            raise ValueError(f'Readarr search failed: {self._format_error(response)}')

    async def _lookup_or_create_author(self, client: httpx.AsyncClient, headers: dict[str, str], author_name: str) -> dict:
        lookup = await client.get(f'{self.target.base_url}/api/v1/author/lookup', headers=headers, params={'term': author_name})
        if lookup.status_code >= 400:
            raise ValueError(f'Readarr author lookup failed: {self._format_error(lookup)}')
        authors = lookup.json()
        if not authors:
            raise ValueError(f'No Readarr author found for {author_name}')
        candidate = authors[0]
        current = await self._get_author_by_id(client, headers, candidate.get('id'))
        if current is not None:
            return current
        root_folder = await self._first_root_folder(client, headers)
        quality_profile = await self._first_quality_profile(client, headers)
        metadata_profile = await self._first_metadata_profile(client, headers)
        if not root_folder or not quality_profile or not metadata_profile:
            raise ValueError('Readarr author add failed: missing root folder or profile configuration')
        payload = self._build_author_payload(candidate, author_name, root_folder, quality_profile, metadata_profile)
        create = await client.post(f'{self.target.base_url}/api/v1/author', headers=headers, json=payload)
        if create.status_code >= 400:
            raise ValueError(f'Readarr author add failed: {self._format_error(create)}')
        return create.json()

    async def _get_author_by_id(self, client: httpx.AsyncClient, headers: dict[str, str], author_id: int | None) -> dict | None:
        if author_id is None:
            return None
        response = await client.get(f'{self.target.base_url}/api/v1/author/{author_id}', headers=headers)
        if response.status_code == 200:
            return response.json()
        return None

    def _build_author_payload(self, lookup_author: dict, author_name: str, root_folder: str, quality_profile: int, metadata_profile: int) -> dict:
        author_id = lookup_author.get('id')
        path = f"{root_folder.rstrip('/')}/{self._sanitize(author_name)}"
        payload = {
            'authorName': lookup_author.get('authorName') or lookup_author.get('name') or author_name,
            'foreignAuthorId': lookup_author.get('foreignAuthorId'),
            'monitored': True,
            'monitorNewItems': 'all',
            'qualityProfileId': quality_profile,
            'metadataProfileId': metadata_profile,
            'rootFolderPath': root_folder,
            'path': path,
            'addOptions': {
                'monitor': 'all',
                'searchForMissingBooks': True,
                'booksToMonitor': [self._best_book_title(lookup_author, author_name)],
            },
        }
        if author_id is not None:
            payload['id'] = author_id
        return payload

    def _best_book_title(self, lookup_author: dict, author_name: str) -> str:
        books = lookup_author.get('books') or []
        for book in books:
            if book.get('title'):
                return book['title']
        return author_name

    async def _lookup_book(self, client: httpx.AsyncClient, headers: dict[str, str], title: str) -> dict:
        lookup = await client.get(f'{self.target.base_url}/api/v1/book/lookup', headers=headers, params={'term': title})
        if lookup.status_code >= 400:
            raise ValueError(f'Readarr book lookup failed: {self._format_error(lookup)}')
        books = lookup.json()
        if not books:
            raise ValueError(f'No Readarr book found for {title}')
        return books[0]

    async def _first_root_folder(self, client: httpx.AsyncClient, headers: dict[str, str]) -> str | None:
        response = await client.get(f'{self.target.base_url}/api/v1/rootfolder', headers=headers)
        if response.status_code >= 400:
            return None
        folders = response.json()
        return folders[0].get('path') if folders else None

    async def _first_quality_profile(self, client: httpx.AsyncClient, headers: dict[str, str]) -> int | None:
        response = await client.get(f'{self.target.base_url}/api/v1/qualityprofile', headers=headers)
        if response.status_code >= 400:
            return None
        profiles = response.json()
        return profiles[0].get('id') if profiles else None

    async def _first_metadata_profile(self, client: httpx.AsyncClient, headers: dict[str, str]) -> int | None:
        response = await client.get(f'{self.target.base_url}/api/v1/metadataprofile', headers=headers)
        if response.status_code >= 400:
            return None
        profiles = response.json()
        return profiles[0].get('id') if profiles else None

    async def _ensure_book_exists(self, client: httpx.AsyncClient, headers: dict[str, str], author: dict, book: dict, goodreads_id: str | None) -> int | None:
        existing = await client.get(f'{self.target.base_url}/api/v1/book', headers=headers, params={'authorId': author.get('id')})
        if existing.status_code >= 400:
            raise ValueError(f'Readarr book lookup failed: {self._format_error(existing)}')
        existing_books = existing.json()
        target_title = (book.get('title') or '').casefold()
        for item in existing_books:
            if item.get('title', '').casefold() == target_title:
                return item.get('id')

        lookup = await client.get(f'{self.target.base_url}/api/v1/book/lookup', headers=headers, params={'term': book.get('title')})
        if lookup.status_code >= 400:
            raise ValueError(f'Readarr book lookup failed: {self._format_error(lookup)}')
        candidates = lookup.json()
        candidate = candidates[0] if candidates else book

        payload = self._build_minimal_book_payload(candidate, author, goodreads_id)
        add_response = await client.post(f'{self.target.base_url}/api/v1/book', headers=headers, json=payload)
        if add_response.status_code >= 400:
            raise ValueError(f'Readarr book add failed: {self._format_error(add_response)}')
        added_book = add_response.json()
        return added_book.get('id') or payload.get('id')

    def _build_minimal_book_payload(self, candidate: dict, author: dict, goodreads_id: str | None) -> dict:
        book_id = candidate.get('id')
        payload = {
            'id': book_id,
            'title': candidate.get('title'),
            'author': author,
            'authorId': author.get('id'),
            'foreignBookId': goodreads_id or candidate.get('foreignBookId'),
            'foreignEditionId': candidate.get('foreignEditionId'),
            'monitored': False,
            'anyEditionOk': False,
            'addOptions': {
                'addType': 'automatic',
                'searchForNewBook': True,
                'booksToMonitor': [candidate.get('title')] if candidate.get('title') else [],
            },
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _sanitize(self, value: str) -> str:
        return ''.join(ch if ch.isalnum() or ch in {' ', '-', '_', '.', '(', ')'} else '_' for ch in value).strip().replace('  ', ' ')

    def _format_error(self, response: httpx.Response) -> str:
        return response.text.strip() or response.reason_phrase
