# Docker

Build from the repository root so Docker can use the full source tree as the build context:

```bash
docker build -f docker/Dockerfile -t uncommon-route .
```

Build with the optional `v2` extras:

```bash
docker build \
  -f docker/Dockerfile \
  --build-arg UNCOMMON_ROUTE_INSTALL_EXTRAS=v2 \
  -t uncommon-route:v2 .
```

Run the service:

```bash
docker run --rm -p 8403:8403 \
  -v uncommon-route-data:/data \
  -e UNCOMMON_ROUTE_UPSTREAM="http://host.docker.internal:9001/v1" \
  -e UNCOMMON_ROUTE_API_KEY="your-key" \
  uncommon-route
```

Runtime parameters:

- `UNCOMMON_ROUTE_UPSTREAM`
- `UNCOMMON_ROUTE_API_KEY`
- `UNCOMMON_ROUTE_HOST`
- `UNCOMMON_ROUTE_PORT`
- `UNCOMMON_ROUTE_DATA_DIR`
- `UNCOMMON_ROUTE_COMPOSITION_CONFIG`

`.dockerignore` stays at the repository root because Docker reads ignore rules from the build context root.
