"""Google Drive client stub (public version).

Real uploads live in the private build. The public demo writes outputs to
the local filesystem only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def ensure_applicant_folder(*_args: Any, **_kwargs: Any) -> str:
    return ""


def upload_file(local_path: str, *_args: Any, **_kwargs: Any) -> dict:
    p = Path(local_path)
    return {"id": "stub", "name": p.name, "webViewLink": str(p.absolute())}
