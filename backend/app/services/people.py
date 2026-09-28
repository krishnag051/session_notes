from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Person


def get_or_create_person(db: Session, *, global_key: str, full_name: str, dob: date) -> Person:
    """global_key is derived (agent-making's own person_identity.global_key())
    — this function only ever matches or creates against it, never accepts
    a hand-typed global_key from an endpoint. See CLAUDE.md's own invariant."""
    person = db.execute(select(Person).where(Person.global_key == global_key)).scalar_one_or_none()
    if person is not None:
        return person
    person = Person(global_key=global_key, full_name=full_name, dob=dob)
    db.add(person)
    db.flush()
    return person
