"""Local content-addressed experiment storage; not a cloud WORM implementation."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False).encode()


def write_once(path: Path, content: bytes) -> None:
    """Publish a complete file atomically; never replace different existing contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != content:
                raise ValueError(f"Existing artifact integrity conflict: {path.name}") from None
    finally:
        Path(temporary).unlink(missing_ok=True)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        write_once(self.root / digest, content)
        return digest

    def get(self, digest: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid artifact digest")
        path = self.root / digest
        if path.is_symlink():
            raise ValueError("Artifact integrity violation: symbolic link")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Artifact integrity violation")
        return content
