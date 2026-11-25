"""Session 的本地 JSON 存储：backend/storage/sessions/*.json。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import settings
from .schemas import Session

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# 每个会话一把可重入锁：同一个会话的“读—改—写”必须串行，不同会话互不阻塞。
# 键是会话 id，值是 [锁, 使用计数]；计数归零时回收，避免长期运行累积锁对象。
_LOCK_GUARD = threading.Lock()
_SESSION_LOCKS: dict[str, list] = {}


def now_iso() -> str:
    """ISO 8601 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    return "s" + uuid.uuid4().hex[:12]


def new_timestamp() -> str:
    return now_iso()


def _check_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _ID_RE.match(session_id):
        raise FileNotFoundError("会话编号不合法")
    return session_id


def _session_path(session_id: str) -> Path:
    return settings.sessions_dir / f"{_check_id(session_id)}.json"


@contextmanager
def session_lock(session_id: str) -> Iterator[None]:
    """同一会话的读改写互斥锁（可重入）。

    调用方用法：

        with session_lock(session_id):
            session = load_session(session_id)
            updated = apply_answer(session, request)
            save_session(updated)

    同一线程可以重复进入（例如接口里已经加锁，内部再取一次不会死锁）。
    不同会话各自一把锁，互不阻塞。
    """
    key = str(session_id)
    with _LOCK_GUARD:
        entry = _SESSION_LOCKS.get(key)
        if entry is None:
            entry = [threading.RLock(), 0]
            _SESSION_LOCKS[key] = entry
        entry[1] += 1
    lock: threading.RLock = entry[0]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _LOCK_GUARD:
            current = _SESSION_LOCKS.get(key)
            if current is entry:
                entry[1] -= 1
                if entry[1] <= 0:
                    _SESSION_LOCKS.pop(key, None)


def save_session(session: Session) -> None:
    """原子写入：先写临时文件，再替换，避免读到半个文件。

    临时文件名带随机后缀，即使有并发写入也不会互相覆盖临时文件。
    """
    path = _session_path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = session.model_dump(mode="json")
    tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # 已经被替换掉了，忽略
                pass


def load_session(session_id: str) -> Session:
    path = _session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"找不到会话 {session_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Session.model_validate(data)


def list_sessions() -> list[dict]:
    """按创建时间倒序返回会话摘要。"""
    directory = settings.sessions_dir
    if not directory.exists():
        return []
    items: list[dict] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        versions = data.get("spec_versions") or [{}]
        items.append(
            {
                "id": data.get("id", path.stem),
                "title": data.get("title", ""),
                "created_at": data.get("created_at", ""),
                "version": versions[-1].get("version", 1),
            }
        )
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items