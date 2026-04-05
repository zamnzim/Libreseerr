from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(slots=True)
class ReadarrTargetSettings:
    name: str
    base_url: str
    api_key: str
    kind: str


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv('APP_NAME', 'Libreseerr')
        self.readarr_targets = self._parse_targets(os.getenv('READARR_TARGETS', '[]'))

    def _parse_targets(self, raw: str) -> list[ReadarrTargetSettings]:
        if not raw.strip():
            return []
        data = json.loads(raw)
        return [
            ReadarrTargetSettings(
                name=item['name'],
                base_url=item['base_url'].rstrip('/'),
                api_key=item['api_key'],
                kind=item['kind'],
            )
            for item in data
        ]

    def targets(self) -> list[ReadarrTargetSettings]:
        return self.readarr_targets


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
