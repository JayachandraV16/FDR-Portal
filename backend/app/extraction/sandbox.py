"""Runs a TextExtractor in a short-lived child process with a hard timeout.

`SandboxedExtractor` implements the same `TextExtractor` interface as the in-process class, so the
service can use either. Parsing untrusted PDFs and running OCR happen only in the child, which has no
application secrets in its environment and is killed if it exceeds the time limit. A semaphore keeps
the number of simultaneous children small; a request that cannot get a slot is refused as busy rather
than queued.
"""

import dataclasses
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from app.extraction.types import ErrorCode, Limits, PageText, TextResult

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_SLOTS = threading.BoundedSemaphore(2)
_ENV_KEYS = ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "TEMP", "TMP")


class Busy(Exception):
    """Every extraction slot is in use."""


class SandboxedExtractor:
    def __init__(self, ocr_command: list[str] | None = None, ocr_language: str = "eng"):
        self.ocr_command = ocr_command
        self.ocr_language = ocr_language

    def extract(self, data: bytes, content_type: str, limits: Limits) -> TextResult:
        if not _SLOTS.acquire(blocking=False):
            raise Busy
        try:
            return self._run(data, content_type, limits)
        finally:
            _SLOTS.release()

    def _run(self, data: bytes, content_type: str, limits: Limits) -> TextResult:
        args = json.dumps({
            "content_type": content_type, "limits": dataclasses.asdict(limits),
            "ocr_command": self.ocr_command, "ocr_language": self.ocr_language,
        })
        env = {k: v for k, v in os.environ.items() if k in _ENV_KEYS}
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        try:
            done = subprocess.run(
                [sys.executable, "-E", "-s", "-m", "app.extraction.worker", args],
                input=data, capture_output=True, timeout=limits.timeout_seconds + 5, cwd=BACKEND_DIR, env=env,
            )
        except subprocess.TimeoutExpired:  # subprocess.run kills the child before raising
            return TextResult(error=ErrorCode.TIMEOUT)
        except OSError:
            return TextResult(error=ErrorCode.WORKER_FAILED)
        if done.returncode != 0 or len(done.stdout) > MAX_OUTPUT_BYTES:
            return TextResult(error=ErrorCode.WORKER_FAILED)
        try:
            raw = json.loads(done.stdout)
            return TextResult(
                pages=[PageText(**p) for p in raw["pages"]], error=raw["error"], warnings=list(raw["warnings"]),
                truncated=bool(raw["truncated"]), pages_total=int(raw["pages_total"]), engine=str(raw["engine"]),
            )
        except (ValueError, KeyError, TypeError):
            return TextResult(error=ErrorCode.WORKER_FAILED)
