#!/usr/bin/env bash
# Regenerate the TypeScript API contract from the engine's Pydantic models.
#
# Run after changing anything in engine/adib_engine/models/ or the API routes.
# The frontend must never hand-write these types.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA_DIR="$ROOT/packages/schema"

echo "==> exporting OpenAPI from engine"
(cd "$ROOT/engine" && uv run python scripts/export_openapi.py "$SCHEMA_DIR/openapi.json")

echo "==> generating TypeScript types"
mkdir -p "$SCHEMA_DIR/src"
npx --yes openapi-typescript@7 "$SCHEMA_DIR/openapi.json" -o "$SCHEMA_DIR/src/api.ts"

echo "==> wrote $SCHEMA_DIR/src/api.ts"
