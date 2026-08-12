"""Read-only edit_plan.json loading (Stage 5 spec, ch. 06).

The Plan Loader never mutates the file it reads and never writes anything
back to it - "이 원칙 덕분에... 항상 같은 입력에서 시작한다는 결정론성이
보장된다." A `LoadError` here is what triggers STUDIO EXECUTION RESULT =
ERROR (ch. 05): the file couldn't even be turned into a dict, so nothing
downstream (Pre-flight, execution result) can be run against it at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass
class LoadError:
    kind: Literal["file_error", "json_parse_error"]
    message: str


@dataclass
class LoadResult:
    data: dict[str, Any] | None
    error: LoadError | None

    @property
    def ok(self) -> bool:
        return self.error is None


def load_plan_file(path: Path) -> LoadResult:
    try:
        raw_text = path.read_text()
    except OSError as exc:
        return LoadResult(data=None, error=LoadError("file_error", str(exc)))

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return LoadResult(data=None, error=LoadError("json_parse_error", str(exc)))

    return LoadResult(data=data, error=None)
