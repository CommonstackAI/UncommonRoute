FROM python:3.12-slim

# Install the v2 ensemble extras by default (scikit-learn + sentence-transformers
# + xgboost) so the trained embedding classifier is active. Override with
# --build-arg UNCOMMON_ROUTE_INSTALL_EXTRAS="" for a slim, metadata/structural-only build.
ARG UNCOMMON_ROUTE_INSTALL_EXTRAS="v2"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UNCOMMON_ROUTE_HOST=0.0.0.0 \
    UNCOMMON_ROUTE_PORT=8403 \
    UNCOMMON_ROUTE_DATA_DIR=/data \
    HF_HOME=/opt/hf

WORKDIR /app

# libgomp1: OpenMP runtime required by xgboost (v2 embedding classifier).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system uncommon-route \
    && useradd --system --create-home --gid uncommon-route uncommon-route

COPY . /app
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN python -m pip install --upgrade pip \
    && if [ -n "${UNCOMMON_ROUTE_INSTALL_EXTRAS}" ]; then \
         python -m pip install ".[${UNCOMMON_ROUTE_INSTALL_EXTRAS}]"; \
       else \
         python -m pip install .; \
       fi \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data \
    && chown -R uncommon-route:uncommon-route /app /data

# Pre-bake the BGE embedding model into the image so routing needs no runtime
# HuggingFace download (offline + no cold-start fetch). Cached under $HF_HOME.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')" \
    && chown -R uncommon-route:uncommon-route /opt/hf

# Bake the pinned routing pool into the data dir. RoutingConfigStore reads
# $UNCOMMON_ROUTE_DATA_DIR/routing_config.json at startup. /data is a normal
# image dir (no VOLUME), so this is always present and never masked by a mount.
COPY --chown=uncommon-route:uncommon-route deploy/routing_config.json /data/routing_config.json

USER uncommon-route

EXPOSE 8403

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD []
