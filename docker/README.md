# Docker

Build a local image from the repository root:

```bash
docker build -f docker/Dockerfile -t uncommon-route .
```

Run the proxy with a configured upstream:

```bash
docker run --rm -p 8403:8403 \
  -v uncommon-route-data:/data \
  -e UNCOMMON_ROUTE_UPSTREAM=https://api.commonstack.ai/v1 \
  -e UNCOMMON_ROUTE_API_KEY=your-key \
  uncommon-route
```

If the upstream runs on the Docker host, use `host.docker.internal`:

```bash
docker run --rm -p 8403:8403 \
  -v uncommon-route-data:/data \
  -e UNCOMMON_ROUTE_UPSTREAM=http://host.docker.internal:9001/v1 \
  -e UNCOMMON_ROUTE_API_KEY=your-key \
  uncommon-route
```

Optional extras can be installed at build time:

```bash
docker build -f docker/Dockerfile \
  --build-arg UNCOMMON_ROUTE_INSTALL_EXTRAS=v2 \
  -t uncommon-route:v2 .
```

Runtime parameters:

- `UNCOMMON_ROUTE_UPSTREAM`
- `UNCOMMON_ROUTE_API_KEY`
- `UNCOMMON_ROUTE_HOST`
- `UNCOMMON_ROUTE_PORT`
- `UNCOMMON_ROUTE_DATA_DIR`
- `UNCOMMON_ROUTE_COMPOSITION_CONFIG`

The container stores runtime state under `/data`, mapped from
`UNCOMMON_ROUTE_DATA_DIR`.
