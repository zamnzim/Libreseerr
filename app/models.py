from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RequestKind(str, Enum):
    book = 'book'
    audiobook = 'audiobook'


@dataclass(slots=True)
class ReadarrTarget:
    name: str
    base_url: str
    api_key: str
    request_kind: RequestKind


@dataclass(slots=True)
class RequestResult:
    target_name: str
    title: str
    author: str
    request_kind: RequestKind
    success: bool
    message: str
