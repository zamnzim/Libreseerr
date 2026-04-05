from __future__ import annotations

import httpx

from .config import ReadarrTargetSettings


class ReadarrClient:
    def __init__(self, target: ReadarrTargetSettings):
        self.target = target

    async def request_book(self, title: str, author: str, quality_profile_id: int | None = None, goodreads_id: str | None = None, task_id: str | None = None) -> str:
        timeout = httpx.Timeout(20.0, connect=5.0, read=20.0, write=20.0, pool=5.0)
        headers = {'X-Api-Key': self.target.api_key}
        async with httpx.AsyncClient(timeout=timeout) as client:
            author_resource = await self._ensure_author(client, headers, author, quality_profile_id)
            if quality_profile_id is not None and author_resource.get('id') is not None:
                await self._update_author_quality_profile(client, headers, author_resource['id'], quality_profile_id)
            book_resource = await self._ensure_book(client, headers, title, author_resource)
            await self._monitor_book(client, headers, book_resource['id'])
            await self._search_book(client, headers, book_resource['id'])
        return 'Author added, requested book monitored and searched'

    async def request_quality_profiles(self) -> list[dict]:
        return await self.list_quality_profiles()

    async def quality_profile_by_id(self, profile_id: int) -> dict | None:
        profiles = await self.list_quality_profiles()
        for profile in profiles:
            if profile.get('id') == profile_id:
                return profile
        return None

    async def list_quality_profiles(self) -> list[dict]:
        timeout = httpx.Timeout(20.0, connect=5.0, read=20.0, write=20.0, pool=5.0)
        headers = {'X-Api-Key': self.target.api_key}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f'{self.target.base_url}/api/v1/qualityprofile', headers=headers)
            if response.status_code >= 400:
                raise ValueError(f'Readarr quality profile lookup failed: {self._format_error(response)}')
            return response.json()

    async def quality_profile_id_for_name(self, name: str) -> int:
        profiles = await self.list_quality_profiles()
        for profile in profiles:
            if profile.get('name') == name:
                return profile['id']
        raise ValueError(f'Readarr quality profile not found: {name}')

    async def _ensure_author(self, client: httpx.AsyncClient, headers: dict[str, str], author_name: str, quality_profile_id: int | None) -> dict:
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
        if not root_folder:
            raise ValueError('Readarr author add failed: missing root folder')
        if quality_profile_id is None:
            raise ValueError('Readarr author add failed: missing quality profile')

        payload = self._build_author_payload(candidate, author_name, root_folder, quality_profile_id)
        create = await client.post(f'{self.target.base_url}/api/v1/author', headers=headers, json=payload)
        if create.status_code >= 400:
            print(f'Readarr author add failed for {author_name}: {self._format_error(create)}')
            raise ValueError(f'Readarr author add failed: {self._format_error(create)}')
        return create.json()

    async def _update_author_quality_profile(self, client: httpx.AsyncClient, headers: dict[str, str], author_id: int, quality_profile_id: int) -> None:
        response = await client.put(f'{self.target.base_url}/api/v1/author/{author_id}', headers=headers, json={
            'qualityProfileId': quality_profile_id,
        })
        if response.status_code >= 400:
            print(f'Readarr author quality profile update failed for {author_id}: {self._format_error(response)}')
            raise ValueError(f'Readarr author quality profile update failed: {self._format_error(response)}')

    async def _ensure_book(self, client: httpx.AsyncClient, headers: dict[str, str], title: str, author_resource: dict) -> dict:
        lookup = await client.get(f'{self.target.base_url}/api/v1/book/lookup', headers=headers, params={'term': title})
        if lookup.status_code >= 400:
            raise ValueError(f'Readarr book lookup failed: {self._format_error(lookup)}')
        books = lookup.json()
        if not books:
            raise ValueError(f'No Readarr book found for {title}')

        normalized = title.casefold()
        for book in books:
            if book.get('title', '').casefold() == normalized:
                return book
        for book in books:
            if normalized in book.get('title', '').casefold():
                return book
        return books[0]

    async def _get_author_by_id(self, client: httpx.AsyncClient, headers: dict[str, str], author_id: int | None) -> dict | None:
        if author_id is None:
            return None
        response = await client.get(f'{self.target.base_url}/api/v1/author/{author_id}', headers=headers)
        if response.status_code == 200:
            return response.json()
        return None

    def _build_author_payload(self, lookup_author: dict, author_name: str, root_folder: str, quality_profile: int) -> dict:
        path = f"{root_folder.rstrip('/')}/{self._sanitize(author_name)}"
        return {
            'authorName': lookup_author.get('authorName') or lookup_author.get('name') or author_name,
            'path': path,
            'rootFolderPath': root_folder,
            'qualityProfileId': quality_profile,
        }

    async def _monitor_book(self, client: httpx.AsyncClient, headers: dict[str, str], book_id: int) -> None:
        response = await client.put(f'{self.target.base_url}/api/v1/book/monitor', headers=headers, json={
            'bookIds': [book_id],
            'monitored': True,
        })
        if response.status_code >= 400:
            raise ValueError(f'Readarr book monitor failed: {self._format_error(response)}')

    async def _search_book(self, client: httpx.AsyncClient, headers: dict[str, str], book_id: int) -> None:
        response = await client.post(f'{self.target.base_url}/api/v1/command', headers=headers, json={
            'name': 'BookSearch',
            'bookIds': [book_id],
        })
        if response.status_code >= 400:
            raise ValueError(f'Readarr book search failed: {self._format_error(response)}')

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
        if self.target.kind == 'audio':
            # audiobook servers should always use the Spoken profile
            pass
        for profile in profiles:
            if profile.get('name') == target_name:
                return profile.get('id')
        raise ValueError(f'Readarr quality profile not found: {target_name}')

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
