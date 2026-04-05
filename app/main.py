from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .metadata import get_book, search_books
from .readarr import ReadarrClient

app = FastAPI(title='Libreseerr')
templates = Jinja2Templates(directory='app/templates')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/', response_class=HTMLResponse)
async def home(request: Request, q: str = ''):
    settings = get_settings()
    results = await search_books(q) if q else []
    return templates.TemplateResponse('index.html', {'request': request, 'settings': settings, 'targets': settings.targets(), 'query': q, 'results': results})


@app.get('/book/{book_id}', response_class=HTMLResponse)
async def book_detail(request: Request, book_id: str):
    settings = get_settings()
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail='Book not found')
    return templates.TemplateResponse('book.html', {'request': request, 'settings': settings, 'targets': settings.targets(), 'book': book})


@app.post('/request')
async def request_book(title: str = Form(...), author: str = Form(...), target_name: str = Form(...), goodreads_id: str | None = Form(default=None)):
    settings = get_settings()
    target = next((t for t in settings.targets() if t.name == target_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Readarr target not found')
    client = ReadarrClient(target)
    await client.request_book(title=title, author=author, goodreads_id=goodreads_id)
    return RedirectResponse(url='/', status_code=303)
