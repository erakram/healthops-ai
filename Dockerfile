# All-in-one image: the Next.js app and the FastAPI API in one container, for a single
# web service (e.g. a Render Web Service with no root directory). Next.js listens on $PORT
# and forwards /api/* to the API on 127.0.0.1:8010 inside the container.
#
# Data lives in SQLite inside the container: on a free plan it resets whenever the service
# restarts or wakes from sleep, and the demo data is re-seeded automatically.
#
#   docker build -t healthops . && docker run -p 3000:3000 -e GROQ_API_KEY=... -e COOKIE_SECURE=false healthops

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV BACKEND_URL=http://127.0.0.1:8010 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM python:3.12-slim
# Next.js's standalone server only needs the node binary (same Debian base as node:22-slim).
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/migrations ./migrations
COPY backend/alembic.ini .
COPY backend/sample_data ./sample_data
COPY --from=web /web/.next/standalone /app/web
COPY --from=web /web/.next/static /app/web/.next/static
COPY --from=web /web/public /app/web/public
COPY start.sh /app/start.sh

# Served over https on Render, so the session cookie is https-only; set COOKIE_SECURE=false for plain http.
ENV PORT=3000 LLM_PROVIDER=groq COOKIE_SECURE=true PYTHONUNBUFFERED=1 NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
EXPOSE 3000
CMD ["bash", "/app/start.sh"]
