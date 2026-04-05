from __future__ import annotations

import httpx

from .config import ReadarrTargetSettings


class ReadarrClient:
    def __init__(self, target: ReadarrTargetSettings):
        self.target = target

    async def request_book(self, title: str, author: str, goodreads_id: str | None = None) -> str:
        headers = {'X-Api-Key': self.target.api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            author_search = await client.get(f'{self.target.base_url}/api/v1/author', headers=headers, params={'term': author})
            if author_search.status_code >= 400:
                raise ValueError(f'Readarr author search failed: {author_search.text.strip() or author_search.reason_phrase}')
            authors = author_search.json()
            if not authors:
                raise ValueError(f'No Readarr author found for {author}')
            author_resource = authors[0]

            book_search = await client.get(f'{self.target.base_url}/api/v1/search', headers=headers, params={'term': title})
            if book_search.status_code >= 400:
                raise ValueError(f'Readarr book search failed: {book_search.text.strip() or book_search.reason_phrase}')
            books = book_search.json()
            if not books:
                raise ValueError(f'No Readarr book found for {title}')
            book_resource = books[0]
            editions = book_resource.get('editions') or []
            if not editions:
                return await self._request_by_lookup(client, headers, title, author_resource, goodreads_id)

            payload = {
                'title': book_resource.get('title', title),
                'author': author_resource,
                'foreignBookId': goodreads_id or book_resource.get('foreignBookId'),
                'editions': editions,
                'monitored': True,
                'searchForNewBook': True,
                'addOptions': {
                    'searchForBook': True,
                },
            }

            response = await client.post(f'{self.target.base_url}/api/v1/book', headers=headers, json=payload)

        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise ValueError(f'Readarr rejected request: {detail}')

        return 'Requested successfully'

    async def _request_by_lookup(self, client: httpx.AsyncClient, headers: dict[str, str], title: str, author_resource: dict, goodreads_id: str | None) -> str:
        payload = {
            'title': title,
            'author': author_resource,
            'foreignBookId': goodreads_id,
            'monitored': True,
            'searchForNewBook': True,
            'addOptions': {
                'searchForBook': True,
            },
        }
        response = await client.post(f'{self.target.base_url}/api/v1/book', headers=headers, json=payload)
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise ValueError(f'Readarr rejected request: {detail}')
        return 'Requested successfully'
