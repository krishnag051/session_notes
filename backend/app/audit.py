from sqlalchemy.orm import Session

from app.db.models import AuditLog


def record(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    changes: dict[str, tuple],
    actor: str | None,
) -> list[AuditLog]:
    """The single shared audit-write helper — every mutating endpoint calls
    this, inside its own transaction, never a separate commit. One row per
    changed field (`changes`: {field: (old_value, new_value)}), never a
    single row with a details blob — matches CLAUDE.md's own invariant.
    `actor=None` only for a scheduled/system action, never a human-initiated
    one.
    """
    entries = []
    for field, (old_value, new_value) in changes.items():
        entry = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            actor=actor,
        )
        session.add(entry)
        entries.append(entry)
    session.flush()
    return entries
