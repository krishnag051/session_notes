import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.agent_client import classify_batch_pdf
from app.config import settings
from app.db.base import get_db
from app.db.models import PersonDocument, SessionNoteBatch
from app.services.people import get_or_create_person

router = APIRouter(prefix="/batches", tags=["batches"])


class PersonDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str | None
    service_code: str | None
    date_of_service: date | None
    appendix: str | None
    page_start: int
    page_end: int
    classification_confidence: str
    classification_note: str | None


class BatchOut(BaseModel):
    id: str
    uploaded_by: str | None
    uploaded_at: datetime
    admin_noise_pages: list[dict]
    unresolved: list[dict]
    documents: list[PersonDocumentOut]


def _batch_out(batch: SessionNoteBatch) -> BatchOut:
    return BatchOut(
        id=batch.id,
        uploaded_by=batch.uploaded_by,
        uploaded_at=batch.uploaded_at,
        admin_noise_pages=batch.classification_result.get("admin_noise_pages", []),
        unresolved=batch.classification_result.get("unresolved", []),
        documents=[PersonDocumentOut.model_validate(d) for d in batch.documents],
    )


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(
    file: UploadFile = File(...),
    uploaded_by: str | None = Form(None),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Accepts a PDF upload, classifies it (zero model calls — see
    classify_batch_pdf's own docstring), and for each person found: matches
    against an existing Person by global_key or creates a new one, then
    creates PersonDocument rows from the returned documents list. Every
    unresolved page range also gets a PersonDocument row (person_id=None,
    classification_confidence='unresolved') — nothing about a shaky
    classification is hidden. admin_noise_pages/unresolved are surfaced
    directly on the response from classification_result, kept verbatim.
    """
    storage_dir = Path(settings.upload_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    saved_path = storage_dir / f"{uuid.uuid4()}.pdf"
    saved_path.write_bytes(file.file.read())

    classification = classify_batch_pdf(str(saved_path))

    batch = SessionNoteBatch(
        uploaded_by=uploaded_by,
        original_pdf_path=str(saved_path),
        classification_result=classification,
    )
    db.add(batch)
    db.flush()

    for person_data in classification["people"]:
        person = get_or_create_person(
            db,
            global_key=person_data["global_key"],
            full_name=person_data["full_name"],
            dob=date.fromisoformat(person_data["dob"]),
        )
        for doc in person_data["documents"]:
            db.add(PersonDocument(
                batch_id=batch.id,
                person_id=person.id,
                service_code=doc["service_code"],
                date_of_service=date.fromisoformat(doc["date_of_service"]),
                appendix=doc["appendix"],
                page_start=doc["page_start"],
                page_end=doc["page_end"],
                classification_confidence="confident",
            ))

    for item in classification["unresolved"]:
        db.add(PersonDocument(
            batch_id=batch.id,
            person_id=None,
            service_code=None,
            date_of_service=None,
            appendix=None,
            page_start=item["page_start"],
            page_end=item["page_end"],
            classification_confidence="unresolved",
            classification_note=item.get("note"),
        ))

    db.commit()
    db.refresh(batch)
    return _batch_out(batch)


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> BatchOut:
    batch = db.get(SessionNoteBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="batch not found")
    return _batch_out(batch)
