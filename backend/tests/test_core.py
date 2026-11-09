import pytest
from sqlalchemy import update

from app.core import audit
from app.core.workflow import InvalidTransition, Status, enqueue, transition
from app.db import SessionLocal
from app.models import AuditLog, Claim


def test_audit_chain_detects_tampering(client):
    with SessionLocal() as db:
        first = audit.record(db, "tester", "test.one", "test", 1, {"n": 1})
        audit.record(db, "tester", "test.two", "test", 2, {"n": 2})
        assert audit.verify(db)["ok"]

        db.execute(update(AuditLog).where(AuditLog.id == first.id).values(details={"n": 999}))
        db.commit()
        result = audit.verify(db)
        assert not result["ok"] and result["broken_at"] == first.id

        db.execute(update(AuditLog).where(AuditLog.id == first.id).values(details={"n": 1}))
        db.commit()
        assert audit.verify(db)["ok"]


def test_illegal_transition_is_refused(client):
    with SessionLocal() as db:
        claim = Claim(number="CLM-T1", status=Status.RECEIVED)
        db.add(claim)
        db.commit()
        with pytest.raises(InvalidTransition):
            transition(db, claim, Status.APPROVED, "tester")  # can't skip the agents and the review


def test_enqueue_is_idempotent(client):
    with SessionLocal() as db:
        claim = Claim(number="CLM-T2", status=Status.RECEIVED)
        db.add(claim)
        db.commit()
        assert enqueue(db, "noop", claim, key="same-key") is not None
        assert enqueue(db, "noop", claim, key="same-key") is None


def test_migrations_match_the_models(tmp_path):
    """A model change without a migration fails here (run `alembic revision --autogenerate`)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.db import Base
    from app.db_migrate import migrate

    engine = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    migrate(engine)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff
