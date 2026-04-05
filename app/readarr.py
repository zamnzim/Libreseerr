from __future__ import annotations

import httpx

from .config import ReadarrTargetSettings


class ReadarrClient:
    def __init__(self, target: ReadarrTargetSettings):
        self.target = target

    async def request_book(self, title: str, author: str, goodreads_id: str | None = None) -> str:
        payload = {
            'title': title,
            'author': author,
            'searchForBook': True,
        }
        if goodreads_id:
            payload['foreignBookId'] = goodreads_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f'{self.target.base_url}/api/v1/book',
                headers={'X-Api-Key': self.target.api_key},
                json=payload,
            )
        response.raise_for_status()
        return 'Requested successfully'
