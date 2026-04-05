from __future__ import annotations

import httpx

from .config import ReadarrTargetSettings


class ReadarrClient:
    def __init__(self, target: ReadarrTargetSettings):
        self.target = target

    async def request_book(self, title: str, author: str, goodreads_id: str | None = None) -> str:
        headers = {'X-Api-Key': self.target.api_key}
        async with httpx.AsyncClient(timeout=20.0) as client:
            author_resource = await self._ensure_author(client, headers, author)
            book_resource = await self._find_book(client, headers, title)
            if not book_resource:
                raise ValueError(f'No Readarr book found for {title}')
            payload = self._build_payload(title, author_resource, book_resource, goodreads_id)
            response = await client.post(f'{self.target.base_url}/api/v1/book', headers=headers, json=payload)
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise ValueError(f'Readarr rejected request: {detail}')
        return 'Requested successfully'

    async def _ensure_author(self, client: httpx.AsyncClient, headers: dict[str, str], author_name: str) -> dict:
        lookup = await client.get(f'{self.target.base_url}/api/v1/author/lookup', headers=headers, params={'term': author_name})
        if lookup.status_code >= 400:
            raise ValueError(f'Readarr author lookup failed: {lookup.text.strip() or lookup.reason_phrase}')
        authors = lookup.json()
        if not authors:
            raise ValueError(f'No Readarr author found for {author_name}')

        candidate = authors[0]
        if candidate.get('id'):
            current = await client.get(f'{self.target.base_url}/api/v1/author/{candidate["id"]}', headers=headers)
            if current.status_code == 200:
                return current.json()

        root_folder = await self._first_root_folder(client, headers)
        quality_profile = await self._first_quality_profile(client, headers)
        metadata_profile = await self._first_metadata_profile(client, headers)
        if not root_folder or not quality_profile or not metadata_profile:
            raise ValueError('Readarr author add failed: missing root folder or profile configuration')

        candidate['monitored'] = True
        candidate['monitorNewItems'] = candidate.get('monitorNewItems') or 'all'
        candidate['qualityProfileId'] = quality_profile
        candidate['metadataProfileId'] = metadata_profile
        candidate['rootFolderPath'] = root_folder
        candidate['path'] = root_folder.rstrip('/') + '/' + self._sanitize_path_segment(candidate.get('authorName') or author_name)
        candidate['addOptions'] = {
            'monitor': 'all',
            'monitored': True,
            'searchForMissingBooks': True,
        }

        create = await client.post(f'{self.target.base_url}/api/v1/author', headers=headers, json=candidate)
        if create.status_code >= 400:
            raise ValueError(f'Readarr author add failed: {create.text.strip() or create.reason_phrase}')
        return create.json()

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

    def _sanitize_path_segment(self, value: str) -> str:
        keep = []
        for ch in value:
            if ch.isalnum() or ch in {' ', '-', '_', '.', '(' , ')'}:
                keep.append(ch)
            else:
                keep.append('_')
        return ''.join(keep).strip().replace('  ', ' ')

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
                'searchForNewBook': False,
            },
            'editions': self._editions_from_book(book_resource),
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _editions_from_book(self, book_resource: dict) -> list[dict]:
        editions = book_resource.get('editions') or []
        if editions:
            output = []
            for edition in editions:
                item = {
                    'id': int(edition['id']) if edition.get('id') is not None else None,
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
                if edition.get('bookId') is not None:
                    item['bookId'] = int(edition['bookId'])
                else:
                    item.pop('bookId', None)
                output.append(item)
            return output

        return [
            {
                'title': book_resource.get('title') or 'Unknown title',
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
