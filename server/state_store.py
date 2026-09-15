# -*- coding: utf-8 -*-
"""
AtomicJsonStore — MeTube-এর `app/state_store.py` পোর্ট (stdlib-only)।

MeTube কেন এটা বানিয়েছে (এবং আমরা কেন কপি করলাম):
  * ডাউনলোড কিউ কখনো আধা-লেখা অবস্থায় পড়া যাবে না → `mkstemp` + `fsync` + `os.replace`।
  * NFS/নেটওয়ার্ক মাউন্টে atomic রিনেম সমর্থিত নয় → নির্দিষ্ট errno-তে সরল লেখায় ফলব্যাক,
    কিন্তু ENOSPC/EIO-তে **কখনোই** ফলব্যাক নয় (নাহলে ভালো স্টেট ফাইল নষ্ট হবে)।
  * করাপ্ট ফাইল → চুপচাপ মুছে না ফেলে `.invalid.<ts>` নামে সরিয়ে রাখা (ডিবাগ করার জন্য)।
  * `kind` + `schema_version` ফিল্ড → ভুল ফাইল/পুরোনো স্কিমা ধরা পড়ে।
  * 0600 পারমিশন → স্টেটে URL/অপশন থাকতে পারে, শেয়ার করা মাউন্টে ফাঁস হবে না।
"""

from __future__ import annotations

import base64
import collections.abc
import errno
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("state_store")

STATE_SCHEMA_VERSION = 1
_BYTES_MARKER = "__bytes__"
_DATETIME_MARKER = "__datetime__"

# এই errno-গুলো মানে "atomic পদ্ধতিটি এখানে সমর্থিত নয়", ডেটা হারানো নয় → ফলব্যাক নিরাপদ।
_ATOMIC_UNSUPPORTED_ERRNOS = frozenset(
    e for e in (
        errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EINVAL,
        getattr(errno, "EOPNOTSUPP", None), getattr(errno, "ENOTSUP", None),
    ) if e is not None
)


def to_json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {_BYTES_MARKER: base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return {_DATETIME_MARKER: value.isoformat()}
    if isinstance(value, collections.abc.Mapping):
        return {str(k): to_json_compatible(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json_compatible(v) for v in value]
    raise TypeError("সিরিয়ালাইজ করা যাবে না: %s" % type(value).__name__)


def from_json_compatible(value: Any) -> Any:
    if isinstance(value, list):
        return [from_json_compatible(v) for v in value]
    if isinstance(value, dict):
        if set(value.keys()) == {_BYTES_MARKER}:
            return base64.b64decode(value[_BYTES_MARKER].encode("ascii"))
        if set(value.keys()) == {_DATETIME_MARKER}:
            return datetime.fromisoformat(value[_DATETIME_MARKER])
        return {k: from_json_compatible(v) for k, v in value.items()}
    return value


class AtomicJsonStore:
    """একটি JSON ফাইলকে নিরাপদে পড়া/লেখা (atomic rename + fsync + quarantine)।"""

    def __init__(self, path: str, *, kind: str, schema_version: int = STATE_SCHEMA_VERSION):
        self.path = path
        self.kind = kind
        self.schema_version = schema_version
        self._fallback_warned = False

    # ---------------------------------------------------------------- পাবলিক API
    def load(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("স্টেট ফাইলে JSON অবজেক্ট থাকা দরকার")
            if payload.get("kind") != self.kind:
                raise ValueError("kind মেলেনি: প্রত্যাশিত %s, পাওয়া গেছে %s"
                                 % (self.kind, payload.get("kind")))
            return payload
        except Exception as exc:  # noqa: BLE001
            self.quarantine_invalid_file(exc)
            return None

    def save(self, data: Dict[str, Any]) -> None:
        payload = {"schema_version": self.schema_version, "kind": self.kind}
        payload.update(data)
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        try:
            self._atomic_write(payload)
        except OSError as exc:
            if exc.errno not in _ATOMIC_UNSUPPORTED_ERRNOS:
                raise
            if not self._fallback_warned:
                self._fallback_warned = True
                log.warning("atomic লিখন ব্যর্থ (%s) — সরল লিখনে যাচ্ছি: %s", exc, self.path)
            self._direct_write(payload)

    def quarantine_invalid_file(self, exc: Exception) -> None:
        """করাপ্ট ফাইল মুছে না ফেলে পাশে সরিয়ে রাখা — পরে বিশ্লেষণ করা যাবে।"""
        if not os.path.exists(self.path):
            return
        backup = "%s.invalid.%s" % (self.path, time.strftime("%Y%m%d%H%M%S"))
        try:
            os.replace(self.path, backup)
            log.warning("স্টেট ফাইল অবৈধ (%s) → %s এ সরানো হয়েছে", exc, backup)
        except OSError as move_exc:
            log.error("করাপ্ট ফাইল সরানো যায়নি: %s", move_exc)

    # ------------------------------------------------------------------ ইন্টার্নাল
    def _atomic_write(self, payload: Dict[str, Any]) -> None:
        text = self._serialize(payload)
        parent = os.path.dirname(self.path) or "."
        fd, tmp_path = tempfile.mkstemp(
            prefix=".%s." % os.path.basename(self.path), suffix=".tmp", dir=parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                self._best_effort_fsync(fh.fileno())
            os.replace(tmp_path, self.path)
            self._fsync_directory(parent)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _direct_write(self, payload: Dict[str, Any]) -> None:
        text = self._serialize(payload)          # আগে সিরিয়ালাইজ, পরে truncate
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            try:
                os.fchmod(fh.fileno(), 0o600)
            except OSError:
                pass
            fh.write(text)
            fh.flush()
            self._best_effort_fsync(fh.fileno())
        self._fsync_directory(os.path.dirname(self.path) or ".")

    @staticmethod
    def _best_effort_fsync(fileno: int) -> None:
        try:
            os.fsync(fileno)
        except OSError as exc:
            if exc.errno not in _ATOMIC_UNSUPPORTED_ERRNOS:
                raise

    @staticmethod
    def _fsync_directory(path: str) -> None:
        try:
            dir_fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)

    @staticmethod
    def _serialize(payload: Dict[str, Any]) -> str:
        return json.dumps(to_json_compatible(payload), ensure_ascii=False,
                          separators=(",", ":")) + "\n"


def load_list(store: AtomicJsonStore, key: str = "items") -> List[Dict[str, Any]]:
    """স্টেট থেকে লিস্ট লোড — ফাইল না থাকলে []।"""
    payload = store.load()
    if not payload:
        return []
    value = payload.get(key)
    return from_json_compatible(value) if isinstance(value, list) else []


def save_list(store: AtomicJsonStore, items: List[Dict[str, Any]], key: str = "items") -> None:
    store.save({key: items})


def path_of(*parts: str) -> str:
    return os.path.join(*parts) if parts else "."
