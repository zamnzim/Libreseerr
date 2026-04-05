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
            await self._lookup_or_create_author(client, headers, author)
            book = await self._find_book_by_title(client, headers, title)
            if book is None:
                raise ValueError(f'No Readarr book found for {title}')
            await self._monitor_requested_book(client, headers, book['id'])
        return 'Author added, requested book monitored'

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
        quality_profile = await self._quality_profile_id(client, headers)
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
            'monitorNewItems': 'none',
            'qualityProfileId': quality_profile,
            'metadataProfileId': metadata_profile,
            'rootFolderPath': root_folder,
            'path': path,
            'addOptions': {
                'monitor': 'all',
                'booksToMonitor': [],
                'searchForMissingBooks': False,
            },
        }
        if author_id is not None:
            payload['id'] = author_id
        return payload

    async def _find_book_by_title(self, client: httpx.AsyncClient, headers: dict[str, str], title: str) -> dict | None:
        response = await client.get(f'{self.target.base_url}/api/v1/book', headers=headers)
        if response.status_code >= 400:
            raise ValueError(f'Readarr book lookup failed: {self._format_error(response)}')
        books = response.json()
        normalized = title.casefold()
        for book in books:
            if book.get('title', '').casefold() == normalized:
                return book
        for book in books:
            if normalized in book.get('title', '').casefold():
                return book
        return None

    async def _monitor_requested_book(self, client: httpx.AsyncClient, headers: dict[str, str], book_id: int) -> None:
        response = await client.put(f'{self.target.base_url}/api/v1/book/editor', headers=headers, json={
            'bookIds': [book_id],
            'monitored': True,
        })
        if response.status_code >= 400:
            raise ValueError(f'Readarr book monitor failed: {self._format_error(response)}')

    async def _first_root_folder(self, client: httpx.AsyncClient, headers: dict[str, str]) -> str | None:
        response = await client.get(f'{self.target.base_url}/api/v1/rootfolder', headers=headers)
        if response.status_code >= 400:
            return None
        folders = response.json()
        return folders[0].get('path') if folders else None

    async def _quality_profile_id(self, client: httpx.AsyncClient, headers: dict[str, str]) -> int | None:
        response = await client.get(f'{self.target.base_url}/api/v1/qualityprofile', headers=headers)
        if response.status_code >= 400:
            return None
        profiles = response.json()
        target_name = 'Spoken' if self.target.kind == 'audio' else 'eBook'
        for profile in profiles:
            if profile.get('name') == target_name:
                return profile.get('id')
        return profiles[0].get('id') if profiles else None

    async def _first_metadata_profile(self, client: httpx.AsyncClient, headers: dict[str, str]) -> int | None:
        response = await client.get(f'{self.target.base_url}/api/v1/metadataprofile', headers=headers)
        if response.status_code >= 400:
            return None
        profiles = response.json()
        return profiles[0].get('id') if profiles else None

    def _sanitize(self, value: str) -> str:
        return ''.join(ch if ch.isalnum() or ch in {' ', '-', '_', '.', '(', ')'} else '_' for ch in value).strip().replace('  ', ' ')

    def _format_error(self, response: httpx.Response) -> str:
        return response.text.strip() or response.reason_phrase
