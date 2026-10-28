"""OCR adapters. The interface is tiny so another engine can replace Tesseract later.

Tesseract is run as a local program on a temporary file: nothing is sent over the network and the
engine never sees anything but the image. It is optional; without it, documents that need OCR report
OCR_UNAVAILABLE instead of producing values.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

MAX_OCR_OUTPUT = 400_000
_LANGUAGE = re.compile(r"^[A-Za-z_]{3,12}(\+[A-Za-z_]{3,12}){0,3}$")


class OcrError(Exception):
    """Recognition failed. Carries a fixed reason code, never text from the document."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "timeout" | "failed"


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognize(self, image: bytes, suffix: str, timeout: float) -> str: ...


class NoOcr:
    name = "none"

    def available(self) -> bool:
        return False

    def recognize(self, image: bytes, suffix: str, timeout: float) -> str:  # pragma: no cover - never called
        raise OcrError("failed")


class TesseractEngine:
    def __init__(self, command: list[str], language: str = "eng"):
        if not _LANGUAGE.match(language):
            raise ValueError("invalid OCR language")
        self.command = command
        self.language = language
        self.name = "tesseract"
        self._version: str | None = None
        self._languages: set[str] | None = None

    def available(self) -> bool:
        return bool(self.command) and bool(shutil.which(self.command[0]) or Path(self.command[0]).is_file())

    def installed_languages(self) -> set[str]:
        """Language data the engine has (from `--list-langs`), cached. Empty if it cannot be listed."""
        if self._languages is None:
            try:
                out = subprocess.run([*self.command, "--list-langs"], capture_output=True, timeout=15, text=True)
                lines = [ln.strip() for ln in (out.stdout or "").splitlines()[1:] if ln.strip()]
                self._languages = set(lines)
            except Exception:
                self._languages = set()
        return self._languages

    def missing_languages(self) -> list[str]:
        """Requested languages (OCR_LANGUAGE, e.g. eng+hin+mar) whose data is NOT installed. Checked
        before every OCR so a missing language is reported, never silently dropped."""
        installed = self.installed_languages()
        return [lang for lang in self.language.split("+") if lang not in installed]

    def version(self) -> str:
        if self._version is None:
            try:
                out = subprocess.run([*self.command, "--version"], capture_output=True, timeout=10, text=True)
                text = (out.stdout or out.stderr).strip().splitlines()
                self._version = text[0].replace("tesseract", "").strip(" v") if text else "unknown"
            except Exception:
                self._version = "unknown"
        return self._version

    def recognize(self, image: bytes, suffix: str, timeout: float) -> str:
        if timeout <= 0:
            raise OcrError("timeout")
        with tempfile.TemporaryDirectory(prefix="fdr-ocr-") as directory:
            source = Path(directory) / f"page{suffix}"
            source.write_bytes(image)
            try:
                done = subprocess.run(
                    [*self.command, str(source), "stdout", "-l", self.language],
                    capture_output=True, timeout=timeout, cwd=directory,
                    env={k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH", "TEMP", "TMP")},
                )
            except subprocess.TimeoutExpired:
                raise OcrError("timeout") from None
            except OSError:
                raise OcrError("failed") from None
        if done.returncode != 0:
            raise OcrError("failed")
        return done.stdout[:MAX_OCR_OUTPUT].decode("utf-8", errors="replace")


def discover_command(configured: str | None) -> list[str] | None:
    """The OCR program to use: the configured path if it exists, else `tesseract` on PATH, else none.
    Nothing is guessed beyond that, so enabling OCR is an explicit deployment decision."""
    if configured:
        return [configured] if Path(configured).is_file() or shutil.which(configured) else None
    found = shutil.which("tesseract")
    return [found] if found else None
