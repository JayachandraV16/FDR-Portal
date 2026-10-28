"""Private file storage for uploaded documents.

Files live under STORAGE_DIR, outside any web root and never served statically; the only way to read
one is through the authorised download endpoint. A file's name on disk is a server-generated random
key (32 hex characters), so no user-supplied text ever reaches a path. Keys are validated again
before every path is built, and the final path is checked to be inside the storage root.

Writes go to a temporary file in the same storage area and are then moved into place under a fresh
key; an existing file is never replaced.
"""

import contextlib
import hashlib
import os
import re
import uuid
from pathlib import Path

_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class StorageError(Exception):
    """The file store could not complete an operation. Never carries file contents or paths."""


class DocumentStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.documents = self.root / "documents"
        self.incoming = self.root / "incoming"

    @staticmethod
    def new_key() -> str:
        return uuid.uuid4().hex

    def _ensure(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):  # POSIX only; harmless elsewhere
            directory.chmod(0o700)

    def path_for(self, key: str) -> Path:
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise StorageError("invalid storage key")
        path = (self.documents / key[:2] / key).resolve()
        if not path.is_relative_to(self.documents):
            raise StorageError("invalid storage key")
        return path

    def new_incoming_path(self) -> Path:
        self._ensure(self.incoming)
        return self.incoming / f"{uuid.uuid4().hex}.part"

    def promote(self, incoming: Path, key: str) -> None:
        """Move a fully written, validated file into place under `key`. Refuses to overwrite."""
        target = self.path_for(key)
        try:
            self._ensure(target.parent)
            if target.exists():
                raise StorageError("storage key already in use")
            os.replace(incoming, target)
            with contextlib.suppress(OSError):
                target.chmod(0o600)
        except OSError:
            raise StorageError("could not store the file") from None

    def read(self, key: str) -> bytes | None:
        """The file's bytes, or None if it is missing."""
        try:
            return self.path_for(key).read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise StorageError("could not read the file") from None

    def delete(self, key: str) -> None:
        with contextlib.suppress(FileNotFoundError, OSError, StorageError):
            self.path_for(key).unlink()

    @staticmethod
    def discard(path: Path) -> None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
