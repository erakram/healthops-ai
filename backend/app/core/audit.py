"""Tamper-evident audit log.

hash = sha256(prev_hash + canonical JSON of the row). Verifying walks the chain and
recomputes every hash; the first mismatch is where someone edited or deleted a row.

The lock makes "read the last hash, then insert" atomic within this process. With
several API processes on Postgres you would take an advisory lock instead.
"""

import hashlib
import json
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog

GENESIS = "0" * 64
_lock = threading.Lock()


def _digest(prev_hash: str, at: datetime, actor: str, action: str, entity_type: str,
            entity_id: str, details: dict) -> str:
    # SQLite drops the timezone on read, so hash a naive UTC timestamp to get the same bytes back.
    at_utc = at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo else at
    payload = json.dumps(
        {"at": at_utc.isoformat(timespec="microseconds"), "actor": actor, "action": action,
         "entity_type": entity_type, "entity_id": entity_id, "details": details},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


def record(db: Session, actor: str, action: str, entity_type: str, entity_id: str | int,
           details: dict | None = None, commit: bool = True) -> AuditLog:
    details = details or {}
    with _lock:
        last = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
        prev_hash = last.hash if last else GENESIS
        at = datetime.now(timezone.utc)
        row = AuditLog(at=at, actor=actor, action=action, entity_type=entity_type,
                       entity_id=str(entity_id), details=details, prev_hash=prev_hash,
                       hash=_digest(prev_hash, at, actor, action, entity_type, str(entity_id), details))
        db.add(row)
        if commit:
            db.commit()
        else:
            db.flush()
    return row


def verify(db: Session) -> dict:
    prev_hash = GENESIS
    count = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        expected = _digest(prev_hash, row.at, row.actor, row.action, row.entity_type, row.entity_id, row.details)
        if row.prev_hash != prev_hash or row.hash != expected:
            return {"ok": False, "entries": count, "broken_at": row.id}
        prev_hash = row.hash
        count += 1
    return {"ok": True, "entries": count, "broken_at": None}
