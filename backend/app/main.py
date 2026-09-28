from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import audits, batches, people, person_documents, reviewers, rule_results, rules, session_note_reviews

app = FastAPI(title="Session Note Compliance API")

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
