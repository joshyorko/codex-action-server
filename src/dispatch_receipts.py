"""Durable request acknowledgements, never a scheduler or thread state database.

A reserved key is never automatically executed again, even after a crash. Only
request hash and native identifiers are stored, never prompts or native payloads.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _path(root, key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("invalid_request_id")
    return Path(root) / (key + ".json")


class Receipt:
    def __init__(self, path, data, replayed=False):
        self.path, self.data, self.replayed = path, data, replayed

    def update(self, **values):
        if self.replayed:
            raise ValueError("cannot_update_replayed_receipt")
        if set(values) - {"state", "thread_id", "turn_id", "error_code", "wait_mode"}:
            raise ValueError("invalid_receipt_fields")
        self.data.update(values)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".receipt-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def public(self):
        return {k: v for k, v in self.data.items() if k != "fingerprint"} | {
            "replayed": self.replayed,
            "retry_same_request_id": "returns_receipt_without_dispatch",
            "native_state_authoritative": True,
        }


def read(root, key):
    path = _path(root, key)
    try:
        return Receipt(path, json.loads(path.read_text()), True).public()
    except FileNotFoundError:
        return {"request_id": key, "state": "not_found"}


@contextmanager
def reserve(root, key, payload):
    path = _path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The directory is operator-controlled, must not be writable by other users.
    if path.parent.stat().st_mode & 0o022:
        raise ValueError("unsafe_receipt_directory")
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    locked = False
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            try:
                data = json.loads(path.read_text())
            except FileNotFoundError:
                raise ValueError("request_id_reservation_pending") from None
            if data.get("fingerprint") != fingerprint:
                raise ValueError("request_id_conflict")
            if data.get("state") == "unknown":
                data["state"] = "in_progress"
            yield Receipt(path, data, True)
            return
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("fingerprint") != fingerprint:
                raise ValueError("request_id_conflict")
            yield Receipt(path, data, True)
            return
        receipt = Receipt(
            path, {"request_id": key, "fingerprint": fingerprint, "state": "unknown"}
        )
        receipt.update()  # fsync reservation BEFORE any native mutation
        yield receipt
    finally:
        if locked:
            fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
