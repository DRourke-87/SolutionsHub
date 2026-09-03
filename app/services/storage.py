"""Attachment storage backends: local filesystem (dev) and Azure Blob Storage via managed identity."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitise_filename(name: str) -> str:
    base = os.path.basename(name or "file").strip().replace(" ", "_")
    base = _SAFE.sub("", base)[:120] or "file"
    return base


def blob_path_for(submission_id: int, attachment_id: int, filename: str) -> str:
    return f"submissions/{submission_id}/{attachment_id}/{sanitise_filename(filename)}"


class Storage:
    def save(self, path: str, data: BinaryIO, content_type: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def read(self, path: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def delete(self, path: str) -> None:  # pragma: no cover
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _full(self, path: str) -> Path:
        full = (self.root / path).resolve()
        if self.root.resolve() not in full.parents:
            raise ValueError("invalid storage path")
        return full

    def save(self, path: str, data: BinaryIO, content_type: str) -> None:
        full = self._full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "wb") as fh:
            while chunk := data.read(1024 * 1024):
                fh.write(chunk)

    def read(self, path: str) -> bytes:
        return self._full(path).read_bytes()

    def delete(self, path: str) -> None:
        full = self._full(path)
        if full.exists():
            full.unlink()


class AzureBlobStorage(Storage):
    """Azure Blob Storage. Uses the account connection string (key auth) when given, else managed identity."""

    def __init__(self, container: str, connection_string: str | None = None, account_url: str | None = None) -> None:
        from azure.storage.blob import BlobServiceClient

        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        elif account_url:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        else:
            raise RuntimeError(
                "STORAGE_BACKEND=azure requires AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL"
            )
        self._container = service.get_container_client(container)

    def save(self, path: str, data: BinaryIO, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        self._container.upload_blob(
            path, data, overwrite=True, content_settings=ContentSettings(content_type=content_type)
        )

    def read(self, path: str) -> bytes:
        return self._container.download_blob(path).readall()

    def delete(self, path: str) -> None:
        try:
            self._container.delete_blob(path)
        except Exception:  # blob already gone; soft delete on the account keeps a copy anyway
            pass


_override: Storage | None = None


def set_storage_for_tests(storage: Storage | None) -> None:
    global _override
    _override = storage
    get_storage.cache_clear()


@lru_cache
def get_storage() -> Storage:
    if _override is not None:
        return _override
    s = get_settings()
    if s.storage_backend.lower() == "azure":
        return AzureBlobStorage(
            s.azure_storage_container,
            connection_string=s.azure_storage_connection_string,
            account_url=s.azure_storage_account_url,
        )
    return LocalStorage(s.local_storage_path)
