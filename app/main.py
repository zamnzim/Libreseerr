from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .readarr import ReadarrClient

app = FastAPI(title='Libreseerr')
templates = Jinja2Templates(directory='app/templates')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    settings = get_settings()
    return templates.TemplateResponse('index.html', {'request': request, 'settings': settings, 'targets': settings.targets()})


@app.post('/request')
async def request_book(title: str = Form(...), author: str = Form(...), target_name: str = Form(...)):
    settings = get_settings()
    target = next((t for t in settings.targets() if t.name == target_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail='Readarr target not found')
    client = ReadarrClient(target)
    await client.search_or_add(title=title, author=author)
    return RedirectResponse(url='/', status_code=303)
