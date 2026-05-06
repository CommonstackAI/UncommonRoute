# Docker

Build a local image from the repository root:

```bash
docker build -f docker/Dockerfile -t uncommon-route .
```

Run the proxy with a configured upstream:

```bash
docker run --rm -p 8403:8403 \
  -e UNCOMMON_ROUTE_UPSTREAM=https://api.commonstack.ai/v1 \
  -e UNCOMMON_ROUTE_API_KEY=... \
  -v uncommon-route-data:/data \
  uncommon-route
```

Optional extras can be installed at build time:

```bash
docker build -f docker/Dockerfile \
  --build-arg UNCOMMON_ROUTE_INSTALL_EXTRAS=v2 \
  -t uncommon-route:v2 .
```

The container stores runtime state under `/data`, mapped from
`UNCOMMON_ROUTE_DATA_DIR`.
