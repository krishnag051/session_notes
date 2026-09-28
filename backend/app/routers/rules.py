from fastapi import APIRouter
from pydantic import BaseModel

from app.agent_client import load_rules

router = APIRouter(prefix="/rules", tags=["rules"])


class RuleOut(BaseModel):
    rule_id: str
    question: str
    codes: list[str]
    type: str
    check_type: str
    severity: str
    active: bool


@router.get("", response_model=list[RuleOut])
def list_rules() -> list[RuleOut]:
    """The real 65-rule rules.json (Brellium transcription), replacing the
    Lovable export's hardcoded RULES mock list."""
    return [
        RuleOut(
            rule_id=r["rule_id"],
            question=r["description"],
            codes=[r["service_code"]],
            type=r["type"],
            check_type=r["check_type"],
            severity=r["severity"],
            active=r["active"],
        )
        for r in load_rules()
    ]
