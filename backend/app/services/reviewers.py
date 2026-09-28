from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Reviewer


def get_or_create_reviewer(db: Session, *, name: str, email: str | None = None) -> Reviewer:
    """No login/auth system — this IS the typeahead's "auto-add a new name"
    behavior. Case-insensitive match on name so 'k. kumar' and 'K. Kumar'
    resolve to the same reviewer rather than silently creating duplicates."""
    name = name.strip()
    reviewer = db.execute(
        select(Reviewer).where(Reviewer.name.ilike(name))
    ).scalar_one_or_none()
    if reviewer is not None:
        return reviewer
    reviewer = Reviewer(name=name, email=email)
    db.add(reviewer)
    db.flush()
    return reviewer
