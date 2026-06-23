#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

set -- uncommon-route serve \
  --host "${UNCOMMON_ROUTE_HOST:-0.0.0.0}" \
  --port "${UNCOMMON_ROUTE_PORT:-8403}"

if [ -n "${UNCOMMON_ROUTE_UPSTREAM:-}" ]; then
  set -- "$@" --upstream "${UNCOMMON_ROUTE_UPSTREAM}"
fi

if [ -n "${UNCOMMON_ROUTE_COMPOSITION_CONFIG:-}" ]; then
  set -- "$@" --composition-config "${UNCOMMON_ROUTE_COMPOSITION_CONFIG}"
fi

exec "$@"
