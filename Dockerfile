FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend-v2

COPY frontend-v2/package.json frontend-v2/package-lock.json ./
RUN npm ci

COPY frontend-v2/ ./
RUN npm run build


FROM python:3.11-slim AS update-build

ARG VERSION=""
ARG COMMIT_SHA=""
WORKDIR /build
COPY . ./
COPY --from=frontend-build /build/static-v2 ./static-v2
RUN DICEFRAME_BUILD_VERSION="${VERSION}" python scripts/build_docker_update.py \
      --allow-dirty --skip-frontend --commit "${COMMIT_SHA}" --output-dir /seed-dist \
    && archive="$(find /seed-dist -maxdepth 1 -name '*-docker-update-linux-amd64.zip' -type f -print -quit)" \
    && test -n "${archive}" \
    && cp "${archive}" /seed-dist/update.zip \
    && cd /seed-dist \
    && sha256sum update.zip > update.sha256


FROM python:3.11-slim AS managed-runtime-base

# Runtime log timestamps follow this zone; override with `docker run -e TZ=...`
# or a TZ entry in .env for compose. Debian slim ships no tzdata, install it.
ENV TZ=Asia/Shanghai

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRPG_WEB_HOST=0.0.0.0 \
    TRPG_WEB_PORT=9876 \
    TRPG_DATA_DIR=/app/data \
    TRPG_INSTALL_MODE=docker-managed \
    TRPG_DOCKER_RUNTIME_ROOT=/app/data/_updater \
    TRPG_DOCKER_SEED_ARCHIVE=/opt/diceframe-seed/update.zip \
    TRPG_DOCKER_SEED_CHECKSUM=/opt/diceframe-seed/update.sha256

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/data /app/plugins /opt/diceframe-launcher /opt/diceframe-seed

# Bot 卡片渲染需要真实 CJK 字形（src/bots/bridge_core/card_renderer.py 的
# _font_paths()）。这里在构建期确认 apt 包把字体装在了被查找的路径上：包名或
# 路径一旦变化就构建失败，而不是发布一个只能画出 □□□□ 的镜像。
RUN for candidate in \
        /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
        /usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf \
        /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc \
        /usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc; do \
        if [ -f "$candidate" ]; then found="$candidate"; break; fi; \
    done; \
    if [ -z "$found" ]; then \
        echo "fonts-noto-cjk installed but no expected CJK font path exists" >&2; \
        exit 1; \
    fi; \
    echo "CJK font present: $found"

COPY src/docker_launcher/ /opt/diceframe-launcher/

WORKDIR /app
EXPOSE 9876
VOLUME ["/app/data", "/app/plugins"]
ENTRYPOINT ["python", "/opt/diceframe-launcher/launcher.py"]


# The release workflow targets this stage after placing its already-built
# workflow artifact at dist/docker-update.zip.  Registry images and GitHub
# Release therefore consume byte-for-byte the same application package.
FROM managed-runtime-base AS managed-artifact
COPY dist/docker-update.zip /opt/diceframe-seed/update.zip
COPY dist/docker-update.sha256 /opt/diceframe-seed/update.sha256


# Local `docker compose build` remains self-contained.
FROM managed-runtime-base AS runtime
COPY --from=update-build /seed-dist/update.zip /opt/diceframe-seed/update.zip
COPY --from=update-build /seed-dist/update.sha256 /opt/diceframe-seed/update.sha256
