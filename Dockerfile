FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package*.json ./
RUN npm ci
COPY index.html tsconfig*.json vite.config.* ./
COPY src ./src
COPY public ./public
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HARBOR_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 harbor \
    && useradd --uid 10001 --gid harbor --no-create-home --shell /usr/sbin/nologin harbor \
    && mkdir -p /app/data \
    && chown harbor:harbor /app/data
COPY server ./server
COPY scripts/harbor_upload.py ./scripts/harbor_upload.py
COPY --from=frontend /build/dist ./dist
USER harbor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
