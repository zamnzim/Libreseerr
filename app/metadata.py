from __future__ import annotations

import httpx

GOOGLE_BOOKS_URL = 'https://www.googleapis.com/books/v1/volumes'


def _pick_isbn(identifiers: list[dict] | None) -> str | None:
    if not identifiers:
        return None
    for item in identifiers:
        if item.get('type') in {'ISBN_13', 'ISBN_10'} and item.get('identifier'):
            return item['identifier']
    return None


async def search_books(query: str) -> list[dict]:
    params = {'q': query, 'maxResults': 20, 'printType': 'books'}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(GOOGLE_BOOKS_URL, params=params)
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get('items', []):
        volume = item.get('volumeInfo', {})
        image_links = volume.get('imageLinks', {})
        results.append({
            'id': item.get('id'),
            'title': volume.get('title', 'Unknown title'),
            'author': ', '.join(volume.get('authors', [])) if volume.get('authors') else 'Unknown author',
            'description': volume.get('description', 'No description available.'),
            'thumbnail': image_links.get('thumbnail') or image_links.get('smallThumbnail') or '',
            'isbn': _pick_isbn(volume.get('industryIdentifiers')),
        })
    return results


async def get_book(book_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f'{GOOGLE_BOOKS_URL}/{book_id}')
        if response.status_code == 404:
            return None
        response.raise_for_status()
        item = response.json()

    volume = item.get('volumeInfo', {})
    image_links = volume.get('imageLinks', {})
    return {
        'id': item.get('id'),
        'title': volume.get('title', 'Unknown title'),
        'author': ', '.join(volume.get('authors', [])) if volume.get('authors') else 'Unknown author',
        'description': volume.get('description', 'No description available.'),
        'thumbnail': image_links.get('thumbnail') or image_links.get('smallThumbnail') or '',
        'isbn': _pick_isbn(volume.get('industryIdentifiers')),
    }
