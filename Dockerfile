FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend-v2

COPY frontend-v2/package.json frontend-v2/package-lock.json ./
RUN npm ci

COPY frontend-v2/ ./
RUN npm run build


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRPG_WEB_HOST=0.0.0.0 \
    TRPG_WEB_PORT=9876 \
    TRPG_DATA_DIR=/app/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend-build /build/static-v2 ./static-v2

RUN mkdir -p /app/data

EXPOSE 9876
VOLUME ["/app/data"]

CMD ["python", "web_server.py"]
