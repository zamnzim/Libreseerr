from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel


class QualityProfileSelection(BaseModel):
    name: str
    id: int
    target_name: str

from .config import get_settings
from .metadata import get_book, search_books
from .readarr import ReadarrClient
from .tasks import create_request_task, get_request_task, list_request_tasks, retry_request_task

app = FastAPI(title='Libreseerr')
templates = Jinja2Templates(directory='app/templates')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/', response_class=HTMLResponse)
async def home(request: Request, q: str = ''):
    settings = get_settings()
    results = []
    if q:
        try:
            results = await search_books(q)
        except Exception:
            results = []
    return templates.TemplateResponse('index.html', {'request': request, 'settings': settings, 'targets': settings.targets(), 'query': q, 'results': results, 'history': list_request_tasks()})


@app.get('/book/{source}/{book_id}', response_class=HTMLResponse)
async def book_detail(request: Request, source: str, book_id: str):
    settings = get_settings()
    book = await get_book(book_id, source=source)
    if book is None:
        raise HTTPException(status_code=404, detail='Book not found')
    quality_profiles = []
    for target in settings.targets():
        try:
            quality_profiles.extend(await ReadarrClient(target).list_quality_profiles())
        except Exception:
            continue
    return templates.TemplateResponse('book.html', {'request': request, 'settings': settings, 'targets': settings.targets(), 'book': book, 'quality_profiles': quality_profiles})


@app.post('/request')
async def request_book(title: str = Form(...), author: str = Form(...), target_name: str = Form(...), quality_profile_id: int = Form(...), goodreads_id: str | None = Form(default=None)):
    settings = get_settings()
    target = next((t for t in settings.targets() if t.name == target_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Readarr target not found')
    client = ReadarrClient(target)
    task = create_request_task(client, title=title, author=author, target=target_name, goodreads_id=goodreads_id)
    return JSONResponse({'task_id': task.id, 'status': task.status, 'message': task.message, 'quality_profile_id': quality_profile_id})


@app.post('/request/{task_id}/retry')
async def retry_request(task_id: str):
    settings = get_settings()
    previous = get_request_task(task_id)
    if previous is None:
        raise HTTPException(status_code=404, detail='Request not found')
    target = next((t for t in settings.targets() if t.name == previous.target), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Readarr target not found')
    client = ReadarrClient(target)
    task = retry_request_task(client, task_id)
    return {'task_id': task.id, 'status': task.status, 'message': task.message}


@app.get('/request/{task_id}')
async def request_status(task_id: str):
    task = get_request_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Request not found')
    return {'task_id': task.id, 'status': task.status, 'message': task.message, 'author_status': task.author_status, 'book_status': task.book_status, 'author_message': task.author_message, 'book_message': task.book_message}
