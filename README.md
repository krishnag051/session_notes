# session_notes

Automated ABA session-note compliance auditing, with mandatory human
review. See `PROJECT.md` for what this app does and how it's built.
This file covers **deploying it** — written for whoever is running these
commands by hand against the server.

## Before you deploy for the first time

Production (`docker-compose.yml`) pulls prebuilt images from Docker Hub
rather than building on the server — **this has never actually been
pushed to Docker Hub yet**. `docker-compose.yml` currently has a
placeholder repo name (`krishna0051/session-notes-backend` /
`-frontend`) with a comment asking you to confirm it before the first
real push. Pick (or confirm) the real repo names and update that file
before following the steps below, or substitute your own names into
every command here.

You'll also need, once, on the server:
- Docker + Docker Compose installed.
- This repo checked out (or at minimum `docker-compose.yml` — the
  compose file is all the server needs; images come from Docker Hub).
- A `.env` file sitting **next to** `docker-compose.yml` (same
  directory), containing the real secrets — see "Required environment
  variables" below. This file is gitignored; create it by hand on the
  server, never commit it.

## Required environment variables / secrets

Set these in the server's own `.env` file (next to `docker-compose.yml`):

| Variable | Required | Notes |
|---|---|---|
| `POSTGRES_USER` | yes | |
| `POSTGRES_PASSWORD` | yes | |
| `POSTGRES_DB` | yes | |
| `ANTHROPIC_API_KEY` | yes | The real, billed key — every judgment and humanization call uses it. |
| `OPENROUTER_API_KEY` | only if you want the cheap/dev model path available in production | Not required for the default real-Anthropic path. |
| `CORS_ALLOW_ORIGINS` | yes | Comma-separated, e.g. `https://your-real-domain.com` — must include whatever origin the frontend is actually served from. |

The per-document/per-batch spend caps (`RULE_ENGINE_MAX_SPEND_USD`,
`BATCH_MAX_SPEND_USD`, `PER_DOCUMENT_HARD_CAP_USD`, `HUMANIZE_MAX_CALLS`,
etc. — see `backend/app/config.py` for the full list and current
defaults) are **not** set in `docker-compose.yml` today, so they'll use
the code's own defaults ($2.00/document hard cap, $4.00/document rule-
engine cap, $4.00/batch cap). Add them to the `environment:` block in
`docker-compose.yml` (or to the server's `.env`, with a matching
`environment:` entry) if you want different real-money limits in
production than the defaults baked into the code.

`DATABASE_URL` is assembled automatically by `docker-compose.yml` from
the Postgres variables above — you don't set it directly.

## Deploying a new build

Run these from your local machine (where the repo is checked out and
Docker can build images), **not** on the server — the server only ever
pulls.

```bash
# 1. From the repo root. Build the backend image (combined backend +
#    agent-making — context MUST be the repo root, not ./backend, since
#    the Dockerfile COPYs from ../agent-making relative to itself).
docker build -f backend/Dockerfile -t <your-dockerhub-user>/session-notes-backend:latest .

# 2. Build the frontend image. VITE_API_BASE_URL is baked in at BUILD
#    time (Vite replaces it at compile time, not read at container
#    start) — point this at your real public backend URL, WITH the
#    /api suffix (every backend route lives under /api — see
#    backend/app/main.py).
docker build --build-arg VITE_API_BASE_URL=https://your-real-domain.com/api \
  -f frontend/Dockerfile -t <your-dockerhub-user>/session-notes-frontend:latest .

# 3. Push both.
docker push <your-dockerhub-user>/session-notes-backend:latest
docker push <your-dockerhub-user>/session-notes-frontend:latest
```

Then, on the server, in the same directory as `docker-compose.yml` and
the server's own `.env`:

```bash
# 4. Pull the new images.
docker compose pull

# 5. Recreate the containers with the new images. Postgres is untouched
#    (same image, same volume) unless you're also bumping the postgres
#    version — this only restarts backend/frontend.
docker compose up -d --force-recreate backend frontend
```

**Database migrations run automatically** — the backend image's own
`CMD` is `alembic upgrade head && uvicorn ...`, so every container start
applies any pending migration before the app server even begins
listening. `alembic upgrade head` is idempotent (a no-op once the
database is already current), so this is safe to run on every restart,
not just the ones that actually changed the schema. **You do not need a
separate migration step** — step 5 above already does it. If you ever
need to run a migration by hand (e.g. to check what it *would* do,
or to run just the migration without starting the app), do it inside a
one-off container against the same database:

```bash
docker compose run --rm backend alembic upgrade head
```

**No seed-data step exists.** The 65 compliance rules live in
`agent-making/agent/rules/rules.json`, which is baked into the backend
image at *build* time (step 1 above) — there is no separate database
seed command to run, on first deploy or any later one. If you change a
rule's wording/severity/active flag, that change ships the next time you
rebuild and redeploy the backend image; it is not something to apply to
the running database directly.

## After deploying — smoke test

```bash
# Backend is actually up and serving real data (not just "container
# running" — confirms Postgres connected and migrations applied cleanly):
curl -s https://your-real-domain.com/api/rules | head -c 200

# Frontend is serving the SPA:
curl -s -o /dev/null -w "%{http_code}\n" https://your-real-domain.com/

# Confirm the migration actually ran against the real database (should
# show the most recent revision id — compare against the newest file in
# backend/alembic/versions/). Substitute your real POSTGRES_USER/DB, or
# `source .env` first so the shell has them:
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version_num from alembic_version;"
```

Then, in a browser: upload a real (or test) session-note batch, and
check that a document actually completes review (not stuck at
"processing") and shows real findings. **This is a real, billed action**
— uploading a batch auto-triggers real Anthropic calls for every
document in it, per `CLAUDE.md`'s own hard rule — don't do this as a
"quick smoke test" without that in mind. `POST /api/batches` accepts a
`skip_auto_review=true` form field if you want to confirm the upload/
classification path alone without spending anything.

## Footguns — read before you deploy

- **Never run `alembic upgrade head` against a database that a currently-
  running container is also mid-migration against.** In practice this
  almost never happens here (step 5's own container start already does
  it, and it's idempotent), but if you ever run the manual command above
  while also restarting containers, do one or the other, not both at the
  same time.
- **`docker compose up -d --force-recreate backend frontend` does not
  touch Postgres** — this is deliberate (the compose file's own
  `postgres` service isn't named in that command). Don't add `postgres`
  to that command unless you specifically mean to recreate it; the named
  volume (`session_notes_postgres_data`) is what actually holds your real
  data, and nothing here is a backup.
- **Postgres is not published on the host** (no `ports:` for it in
  production, only in staging) — this is intentional zero-trust
  hardening, not an oversight. If you need `psql` access on the real
  server, tunnel over SSH:
  `ssh -L 5432:localhost:5432 <user>@<server>`, then connect to
  `localhost:5432` locally — never add a `ports:` entry for it in
  production.
- **The frontend image must be rebuilt, not just restarted, whenever the
  real backend URL changes** — `VITE_API_BASE_URL` is a Vite build-time
  replacement baked into the compiled JS. Changing `docker-compose.yml`
  alone does nothing; you need a fresh `docker build --build-arg
  VITE_API_BASE_URL=...` (step 2 above).
- **Confirm the real Docker Hub repo names** in `docker-compose.yml`
  before the very first deploy — see "Before you deploy for the first
  time" above. The names currently in that file are an unconfirmed
  placeholder.

## Staging (local verification before a real deploy)

`docker-compose.staging.yml` is a separate, local-only stack — it
**builds from your local checkout** (not Docker Hub), uses its own
`.env.staging` file, and its own ports/volumes so it can never collide
with a real production deployment on the same host. Useful for
verifying a change actually works before pushing it through the real
deploy steps above:

```bash
docker compose -f docker-compose.staging.yml -p session-notes-staging build backend frontend
docker compose -f docker-compose.staging.yml -p session-notes-staging up -d --force-recreate backend frontend
```

Same automatic-migration behavior applies. Staging's own ports: backend
`8006`, frontend `3006`, Postgres `5435` (published on the host, unlike
production, since staging never runs anywhere zero-trust rules apply).
