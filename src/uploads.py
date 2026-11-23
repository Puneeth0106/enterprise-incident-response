"""Bounded, request-local staging for document ingestion."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO, Callable

from fastapi import HTTPException

from src.config import get_settings
from src.ingestion import SUPPORTED


def ingest_upload(
    stream: BinaryIO,
    filename: str | None,
    staging_root: Path,
    ingest: Callable[[Path], int],
) -> tuple[str, int]:
    # Browsers may supply either POSIX or Windows path separators.
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name.encode("utf-8")) > 240 or any(ord(c) < 32 for c in name):
        raise HTTPException(status_code=400, detail="Invalid document filename")
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(status_code=400, detail="Supported: PDF, TXT, MD, DOCX")

    limit = get_settings().max_upload_bytes
    # Keep the original basename for stable chunk IDs while isolating requests.
    with TemporaryDirectory(prefix="ingest-", dir=staging_root) as directory:
        target = Path(directory) / name
        size = 0
        with target.open("wb") as output:
            while block := stream.read(min(64 * 1024, limit - size + 1)):
                size += len(block)
                if size > limit:
                    raise HTTPException(status_code=413, detail="Document exceeds upload size limit")
                output.write(block)
        if size == 0:
            raise HTTPException(status_code=400, detail="Document is empty")
        return name, ingest(target)
