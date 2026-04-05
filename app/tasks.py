from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass

from .readarr import ReadarrClient


@dataclass
class RequestTask:
    id: str
    status: str
    message: str
    title: str
    author: str
    target: str
    author_status: str = 'pending'
    book_status: str = 'pending'
    search_status: str = 'pending'
    author_message: str = ''
    book_message: str = ''
    search_message: str = ''
    error_detail: str = ''


_tasks: dict[str, RequestTask] = {}
_order: list[str] = []


def create_request_task(client: ReadarrClient, title: str, author: str, target: str, goodreads_id: str | None) -> RequestTask:
    existing = next((task for task in _tasks.values() if task.title == title and task.author == author and task.target == target and task.status in {'submitted', 'processing', 'success'}), None)
    if existing is not None:
        return existing

    task_id = str(uuid.uuid4())
    task = RequestTask(
        id=task_id,
        status='submitted',
        message='Request submitted',
        title=title,
        author=author,
        target=target,
        author_status='processing',
        book_status='pending',
        search_status='pending',
        author_message='Adding author',
        book_message='Waiting to add requested book',
        search_message='Waiting to start search',
    )
    _tasks[task_id] = task
    _order.insert(0, task_id)
    del _order[20:]

    async def runner() -> None:
        try:
            _tasks[task_id] = RequestTask(
                id=task_id,
                status='processing',
                message='Submitting to Readarr',
                title=title,
                author=author,
                target=target,
                author_status='processing',
                book_status='pending',
                search_status='pending',
                author_message='Adding author',
                book_message='Waiting to add requested book',
                search_message='Waiting to start search',
            )
            result = await client.request_book(title=title, author=author, goodreads_id=goodreads_id, task_id=task_id)
            _tasks[task_id] = RequestTask(
                id=task_id,
                status='success',
                message=result,
                title=title,
                author=author,
                target=target,
                author_status='success',
                book_status='success',
                search_status='success',
                author_message='Author added',
                book_message='Requested book added',
                search_message='Search started',
            )
        except Exception as exc:
            msg = f'{type(exc).__name__}: {exc}'
            detail = traceback.format_exc()
            current = _tasks.get(task_id)
            _tasks[task_id] = RequestTask(
                id=task_id,
                status='error',
                message=msg,
                title=title,
                author=author,
                target=target,
                author_status='success' if current and current.author_status == 'success' else 'error',
                book_status='error',
                search_status='pending',
                author_message='Author added' if current and current.author_status == 'success' else 'Author failed',
                book_message='Requested book failed',
                search_message='Search not started',
                error_detail=detail,
            )

    asyncio.create_task(runner())
    return task


def update_quality_profile_for_request(task_id: str, quality_profile_id: int) -> RequestTask | None:
    task = _tasks.get(task_id)
    if task is None:
        return None
    task.book_message = f'Quality profile selected: {quality_profile_id}'
    return task


def get_request_task(task_id: str) -> RequestTask | None:
    return _tasks.get(task_id)


def get_latest_request_task() -> RequestTask | None:
    if not _order:
        return None
    return _tasks.get(_order[0])


def get_latest_request_task_id() -> str | None:
    if not _order:
        return None
    return _order[0]


def update_request_task(task_id: str, task: RequestTask) -> None:
    _tasks[task_id] = task


def update_request_order(task_id: str) -> None:
    if task_id in _order:
        _order.remove(task_id)
    _order.insert(0, task_id)


def retry_request_task(client: ReadarrClient, task_id: str) -> RequestTask:
    previous = _tasks.get(task_id)
    if previous is None:
        raise KeyError(task_id)
    return create_request_task(client, previous.title, previous.author, previous.target, None)


def list_request_tasks() -> list[RequestTask]:
    return [_tasks[task_id] for task_id in _order if task_id in _tasks]
