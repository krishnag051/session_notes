# Plain static SPA build — a Vite SPA using @tanstack/router-plugin for
# file-based routing at BUILD time, not TanStack Start's SSR framework (see
# docs/ for the Phase 0 TanStack-Start-to-plain-Vite conversion) — no
# server bundle, `dist/` is just index.html + assets/. node_modules, src/,
# and the test suite never reach the final image — build-stage only.
#
# VITE_API_BASE_URL is read via `import.meta.env`, a Vite BUILD-TIME
# replacement — it gets baked into the compiled JS, not read at container
# start. One build per environment — pass the real backend URL as a build
# arg: docker build --build-arg VITE_API_BASE_URL=https://api.example.com ...
# NOT YET READ BY ANY FRONTEND CODE (this frontend still runs on mock data,
# per its own Phase 0 build) — wired here now so the compose files have
# somewhere real to plug the real backend URL into once that wiring lands.

FROM node:20-slim AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* frontend/bun.lock* ./
RUN npm ci

COPY frontend/ ./

ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build


FROM nginx:1.27-alpine AS runtime

# SPA fallback — every unmatched path serves index.html so the client-side
# router (TanStack Router, file-based routes) handles it, instead of nginx
# 404ing on a real, deep-linked route.
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
