FROM python:3.12-slim

ARG UNCOMMON_ROUTE_INSTALL_EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UNCOMMON_ROUTE_HOST=0.0.0.0 \
    UNCOMMON_ROUTE_PORT=8403 \
    UNCOMMON_ROUTE_DATA_DIR=/data

WORKDIR /app

RUN groupadd --system uncommon-route \
    && useradd --system --create-home --gid uncommon-route uncommon-route

COPY . /app
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install --upgrade pip \
    && if [ -n "${UNCOMMON_ROUTE_INSTALL_EXTRAS}" ]; then \
         pip install ".[${UNCOMMON_ROUTE_INSTALL_EXTRAS}]"; \
       else \
         pip install .; \
       fi \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data \
    && chown -R uncommon-route:uncommon-route /app /data

USER uncommon-route

EXPOSE 8403
VOLUME ["/data"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD []
