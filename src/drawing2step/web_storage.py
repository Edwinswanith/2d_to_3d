"""Storage backends for web drawing jobs."""

import json
import os
import shutil
from pathlib import Path
from typing import Any, Protocol, cast

from drawing2step.storage import canonical_json


class WebStorage(Protocol):
    def put_bytes(self, job_id: str, relative: str, content: bytes, content_type: str) -> None: ...

    def get_bytes(self, job_id: str, relative: str) -> bytes: ...

    def exists(self, job_id: str, relative: str) -> bool: ...

    def list_statuses(self) -> list[tuple[str, dict[str, Any]]]: ...

    def publish_tree(self, job_id: str, directory: Path) -> None: ...


CONTENT_TYPES = {
    "original": "application/octet-stream",
    "status.json": "application/json",
    "drawing.png": "image/png",
    "reader-response.json": "application/json",
    "draft.json": "application/json",
    "prompt.txt": "text/plain; charset=utf-8",
    "reader-schema.json": "application/json",
    "mesh.json": "application/json",
    "model.stl": "model/stl",
    "manifest.json": "application/json",
    "cad/evaluation-body.step": "application/step",
    "cad/spec.json": "application/json",
    "cad/verification.json": "application/json",
}


class LocalWebStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str, relative: str) -> Path:
        return self.root / job_id / relative

    def put_bytes(self, job_id: str, relative: str, content: bytes, content_type: str) -> None:
        path = self.path(job_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get_bytes(self, job_id: str, relative: str) -> bytes:
        return self.path(job_id, relative).read_bytes()

    def exists(self, job_id: str, relative: str) -> bool:
        return self.path(job_id, relative).is_file()

    def list_statuses(self) -> list[tuple[str, dict[str, Any]]]:
        result = []
        for path in self.root.glob("*/status.json"):
            result.append((path.parent.name, json.loads(path.read_text())))
        return result

    def publish_tree(self, job_id: str, directory: Path) -> None:
        destination = self.root / job_id
        destination.mkdir(parents=True, exist_ok=True)
        for path in directory.rglob("*"):
            if not path.is_file() or path.name in {"status.json", "status.pending"}:
                continue
            target = destination / path.relative_to(directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


class R2WebStorage:
    def __init__(self, bucket: str, endpoint_url: str, access_key_id: str, secret_access_key: str):
        import boto3  # type: ignore[import-untyped]

        self.bucket = bucket
        self.prefix = os.getenv("CLOUDFLARE_R2_PREFIX", "drawings").strip("/")
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    @classmethod
    def from_env(cls) -> "R2WebStorage | None":
        from dotenv import load_dotenv

        load_dotenv()
        bucket = os.getenv("CLOUDFLARE_R2_BUCKET")
        endpoint = os.getenv("CLOUDFLARE_R2_ENDPOINT_URL") or os.getenv("R2_ENDPOINT_URL")
        access_key = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID") or os.getenv("R2_ACCESS_KEY_ID")
        secret_key = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY") or os.getenv(
            "R2_SECRET_ACCESS_KEY"
        )
        if bucket is None or endpoint is None or access_key is None or secret_key is None:
            return None
        return cls(bucket, endpoint, access_key, secret_key)

    def key(self, job_id: str, relative: str) -> str:
        clean = relative.strip("/")
        return f"{self.prefix}/{job_id}/{clean}"

    def put_bytes(self, job_id: str, relative: str, content: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self.key(job_id, relative),
            Body=content,
            ContentType=content_type,
        )

    def get_bytes(self, job_id: str, relative: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self.key(job_id, relative))
        return cast(bytes, response["Body"].read())

    def exists(self, job_id: str, relative: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(job_id, relative))
        except Exception:
            return False
        return True

    def list_statuses(self) -> list[tuple[str, dict[str, Any]]]:
        statuses = []
        prefix = f"{self.prefix}/"
        token = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = self.client.list_objects_v2(**kwargs)
            for item in response.get("Contents", []):
                key = item["Key"]
                if not key.endswith("/status.json"):
                    continue
                parts = key.split("/")
                if len(parts) < 3:
                    continue
                job_id = parts[-2]
                body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
                statuses.append((job_id, json.loads(body)))
            if not response.get("IsTruncated"):
                return statuses
            token = response.get("NextContinuationToken")

    def publish_tree(self, job_id: str, directory: Path) -> None:
        for path in directory.rglob("*"):
            if not path.is_file() or path.name in {"status.json", "status.pending"}:
                continue
            relative = path.relative_to(directory).as_posix()
            content_type = CONTENT_TYPES.get(relative, "application/octet-stream")
            self.put_bytes(job_id, relative, path.read_bytes(), content_type)


def default_web_storage(root: Path) -> WebStorage:
    return R2WebStorage.from_env() or LocalWebStorage(root)


def put_json(storage: WebStorage, job_id: str, relative: str, value: Any) -> None:
    storage.put_bytes(job_id, relative, canonical_json(value), "application/json")


def get_json(storage: WebStorage, job_id: str, relative: str) -> Any:
    return json.loads(storage.get_bytes(job_id, relative))
