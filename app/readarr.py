from __future__ import annotations

import httpx

from .config import ReadarrTargetSettings


class ReadarrClient:
    def __init__(self, target: ReadarrTargetSettings):
        self.target = target

    async def request_book(self, title: str, author: str, goodreads_id: str | None = None) -> str:
        headers = {'X-Api-Key': self.target.api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            author_resource = await self._find_author(client, headers, author)
            book_resource = await self._find_book(client, headers, title)
            if not book_resource:
                raise ValueError(f'No Readarr book found for {title}')

            payload = self._build_payload(title, author_resource, book_resource, goodreads_id)
            response = await client.post(f'{self.target.base_url}/api/v1/book', headers=headers, json=payload)

        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise ValueError(f'Readarr rejected request: {detail}')

        return 'Requested successfully'

    async def _find_author(self, client: httpx.AsyncClient, headers: dict[str, str], author: str) -> dict:
        response = await client.get(f'{self.target.base_url}/api/v1/author/lookup', headers=headers, params={'term': author})
        if response.status_code >= 400:
            raise ValueError(f'Readarr author lookup failed: {response.text.strip() or response.reason_phrase}')
        authors = response.json()
        if not authors:
            raise ValueError(f'No Readarr author found for {author}')
        return authors[0]

    async def _find_book(self, client: httpx.AsyncClient, headers: dict[str, str], title: str) -> dict | None:
        response = await client.get(f'{self.target.base_url}/api/v1/book/lookup', headers=headers, params={'term': title})
        if response.status_code >= 400:
            raise ValueError(f'Readarr book lookup failed: {response.text.strip() or response.reason_phrase}')
        books = response.json()
        return books[0] if books else None

    def _build_payload(self, title: str, author_resource: dict, book_resource: dict, goodreads_id: str | None) -> dict:
        payload = {
            'title': book_resource.get('title') or title,
            'author': author_resource,
            'authorId': author_resource.get('id'),
            'foreignBookId': goodreads_id or book_resource.get('foreignBookId'),
            'foreignEditionId': book_resource.get('foreignEditionId'),
            'monitored': True,
            'anyEditionOk': True,
            'addOptions': {
                'addType': 'automatic',
                'searchForNewBook': True,
            },
            'editions': self._editions_from_book(book_resource),
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _editions_from_book(self, book_resource: dict) -> list[dict]:
        editions = book_resource.get('editions') or []
        if editions:
            return [
                {
                    'id': edition.get('id'),
                    'bookId': edition.get('bookId') or book_resource.get('id'),
                    'foreignEditionId': edition.get('foreignEditionId'),
                    'title': edition.get('title'),
                    'language': edition.get('language'),
                    'overview': edition.get('overview') or book_resource.get('overview'),
                    'format': edition.get('format'),
                    'isEbook': edition.get('isEbook', False),
                    'disambiguation': edition.get('disambiguation'),
                    'publisher': edition.get('publisher'),
                    'pageCount': edition.get('pageCount', 0),
                    'releaseDate': edition.get('releaseDate'),
                    'images': edition.get('images') or book_resource.get('images') or [],
                    'links': edition.get('links') or book_resource.get('links') or [],
                    'ratings': edition.get('ratings') or book_resource.get('ratings') or {'votes': 0, 'value': 0},
                    'monitored': edition.get('monitored', True),
                    'manualAdd': edition.get('manualAdd', True),
                }
                for edition in editions
            ]

        return [
            {
                'title': book_resource.get('title') or 'Unknown title',
                'bookId': book_resource.get('id'),
                'foreignEditionId': book_resource.get('foreignEditionId') or book_resource.get('foreignBookId') or str(book_resource.get('id') or ''),
                'isEbook': False,
                'monitored': True,
                'manualAdd': True,
                'pageCount': book_resource.get('pageCount', 0),
                'overview': book_resource.get('overview'),
                'images': book_resource.get('images') or [],
                'links': book_resource.get('links') or [],
                'ratings': book_resource.get('ratings') or {'votes': 0, 'value': 0},
                'publisher': None,
                'format': None,
                'language': None,
                'releaseDate': book_resource.get('releaseDate'),
            }
        ]
