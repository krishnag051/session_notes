from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.base import SessionLocal
from app.routers import audits, batches, people, person_documents, reviewers, rule_results, rules, session_note_reviews
from app.services.batch_reviews import recover_orphaned_processing_reviews

app = FastAPI(title="Session Note Compliance API")


@app.on_event("startup")
def _recover_orphaned_processing_reviews_on_startup() -> None:
    """Real incident (2026-10-15): a backend process killed mid-batch left
    a PersonDocument's review stuck at status="processing" forever, needing
    a manual, by-hand fix before the resume feature existed. Runs once per
    process start, after alembic's own migration (see Dockerfile's CMD —
    migrations always run before uvicorn starts), so this is never a
    manual step again.
    """
    db = SessionLocal()
    try:
        recovered = recover_orphaned_processing_reviews(db)
        if recovered:
            print(f"[startup] recovered {recovered} orphaned 'processing' review(s) back to 'pending'")
    finally:
        db.close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every real route lives under /api — matches the old TP-review project's
# own convention, and what the frontend's VITE_API_BASE_URL build args
# (docker-compose.yml / docker-compose.staging.yml) already assume.
API_PREFIX = "/api"
app.include_router(batches.router, prefix=API_PREFIX)
app.include_router(person_documents.router, prefix=API_PREFIX)
app.include_router(rule_results.router, prefix=API_PREFIX)
app.include_router(session_note_reviews.router, prefix=API_PREFIX)
app.include_router(people.router, prefix=API_PREFIX)
app.include_router(reviewers.router, prefix=API_PREFIX)
app.include_router(audits.router, prefix=API_PREFIX)
app.include_router(rules.router, prefix=API_PREFIX)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
