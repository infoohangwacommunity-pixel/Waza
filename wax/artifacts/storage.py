"""
Durable artifact storage.

Backends:
- local: WAX_ARTIFACT_ROOT filesystem
- s3: S3-compatible (AWS S3, R2, MinIO) via env

Workspace TTL files are temporary. Artifacts here survive restarts and cleanup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class StorageBackend(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or os.environ.get("WAX_ARTIFACT_ROOT") or "/tmp/wax-artifacts")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Prevent path traversal
        safe = key.replace("..", "").lstrip("/")
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("path_escape")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.write_bytes(data)
        return f"local://{key}"

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3Storage:
    """S3-compatible object storage. Requires boto3 when backend=s3."""

    def __init__(self):
        import boto3

        self.bucket = os.environ.get("WAX_S3_BUCKET") or getattr(settings, "s3_bucket", "") or ""
        if not self.bucket:
            raise RuntimeError("WAX_S3_BUCKET required for s3 backend")
        endpoint = os.environ.get("WAX_S3_ENDPOINT") or getattr(settings, "s3_endpoint", None)
        region = os.environ.get("WAX_S3_REGION") or getattr(settings, "s3_region", None) or "auto"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            region_name=region,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("WAX_S3_ACCESS_KEY"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
            or os.environ.get("WAX_S3_SECRET_KEY"),
        )
        self.prefix = (os.environ.get("WAX_S3_PREFIX") or "wax/").rstrip("/") + "/"

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        full = f"{self.prefix}{key.lstrip('/')}"
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self.client.put_object(Bucket=self.bucket, Key=full, Body=data, **extra)
        return f"s3://{self.bucket}/{full}"

    def get(self, key: str) -> bytes:
        full = f"{self.prefix}{key.lstrip('/')}"
        obj = self.client.get_object(Bucket=self.bucket, Key=full)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        full = f"{self.prefix}{key.lstrip('/')}"
        self.client.delete_object(Bucket=self.bucket, Key=full)

    def exists(self, key: str) -> bool:
        full = f"{self.prefix}{key.lstrip('/')}"
        try:
            self.client.head_object(Bucket=self.bucket, Key=full)
            return True
        except Exception:
            return False


def get_storage() -> StorageBackend:
    backend = getattr(settings, "effective_storage_backend", None)
    if callable(backend):
        backend = settings.effective_storage_backend
    else:
        backend = (
            backend
            or os.environ.get("WAX_STORAGE_BACKEND")
            or getattr(settings, "storage_backend", None)
            or "local"
        )
    backend = str(backend).lower()
    if backend == "s3":
        try:
            return S3Storage()
        except Exception as e:
            logger.error("s3_storage_init_failed", error=str(e)[:200])
            if settings.app_env == "production":
                raise
            logger.warning("s3_fallback_to_local")
            return LocalStorage()
    if settings.app_env == "production":
        logger.warning(
            "storage_local_in_production",
            hint="Set WAX_STORAGE_BACKEND=s3 and WAX_S3_BUCKET for durable artifacts",
        )
    return LocalStorage()


def store_bytes(principal_id: Any, filename: str, data: bytes, content_type: str | None = None) -> str:
    safe_p = str(principal_id).replace("/", "_")[:64]
    name = "".join(c for c in filename if c.isalnum() or c in "._-")[:120] or "file"
    key = f"{safe_p}/{uuid4().hex[:10]}-{name}"
    uri = get_storage().put(key, data, content_type=content_type)
    return uri


def read_bytes(uri_or_key: str) -> bytes:
    storage = get_storage()
    if uri_or_key.startswith("local://"):
        return storage.get(uri_or_key[len("local://") :])
    if uri_or_key.startswith("s3://"):
        # s3://bucket/prefix/key → use key after bucket
        parts = uri_or_key[5:].split("/", 1)
        key = parts[1] if len(parts) > 1 else parts[0]
        prefix = (os.environ.get("WAX_S3_PREFIX") or "wax/").rstrip("/") + "/"
        if key.startswith(prefix):
            key = key[len(prefix) :]
        return storage.get(key)
    return storage.get(uri_or_key)
