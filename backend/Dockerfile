# Combined backend + agent-making image. agent-making is imported as a
# plain Python module by path (app/agent_client.py does sys.path.insert),
# never called over HTTP/as a separate service — same reasoning as the
# prior TP-review project's own backend/Dockerfile.
#
# MUST be built with the REPO ROOT as build context (not ./backend alone)
# — this Dockerfile COPYs from ../agent-making relative to this file, which
# only resolves if the build context includes both directories. See
# docker-compose.yml's `build: {context: ., dockerfile: backend/Dockerfile}`.

FROM python:3.12-slim AS build

WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/app ./app

RUN pip install --no-cache-dir --prefix=/install .

# agent-making's own runtime-necessary subset ONLY — never .env, .env.example,
# app.py (the Streamlit dev tool), tests/, or .venv. This is the one thing
# CLAUDE.md's own hard rule requires being verified explicitly here (not
# assumed): a real key sitting in agent-making/.env or agent-making/agent/.env
# on the host can NEVER leak into this image, because neither path is ever
# COPYed — only pipeline/ (api.py's own import chain pulls in the rest of
# the package) and rules/rules.json (the one file _load_rules() reads).
COPY agent-making/agent/pipeline /agent-making-src/agent/pipeline
COPY agent-making/agent/rules/rules.json /agent-making-src/agent/rules/rules.json


FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=build /install /usr/local
COPY --from=build /agent-making-src /agent-making
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

# Matches settings.agent_making_path's default ("../agent-making", resolved
# relative to backend/ — i.e. one level above /app/app, which is /app
# itself) — so /agent-making, a sibling of /app at the container root, is
# exactly where the existing default already looks. AGENT_MAKING_PATH
# remains available to override if this layout ever changes.

# Session-note-extraction cache — not yet written by anything in this
# backend (session_note_extraction.py isn't wired into a real call site
# yet, see this phase's own report), but created here so the mount point
# exists with the right ownership the moment it is, matching the SAME
# "never let a restart silently re-trigger billed calls for files already
# processed" reasoning as the prior project's own volume.
RUN mkdir -p /agent-making/agent/.cache/session_note_extractions

# Upload storage — settings.upload_storage_dir defaults to "./data/uploads",
# resolved relative to this WORKDIR. A named volume mounts over
# /app/data/uploads in docker-compose.yml so uploaded batch PDFs survive
# restarts/redeploys.
RUN mkdir -p /app/data/uploads

EXPOSE 8000

# Root cause of a real crash Krishna hit: this CMD used to skip migrations
# entirely, so a container whose Postgres volume predates a schema change
# (e.g. the reviewers table) served 500s from any endpoint touching the
# missing table/column, which the frontend's route loader turned into a
# full-page "This page didn't load" crash. `alembic upgrade head` is
# idempotent (a no-op once the DB is already current), so running it on
# every start is safe and never re-applies a migration twice.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
