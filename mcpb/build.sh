#!/usr/bin/env bash
# Build the MCPB bundle (Claude Desktop extension) for the MCP server.
# Output: mcpb/dist/premiere-claude-bridge-<version>.mcpb
# The bundle holds only mcp-server/server.js with production node_modules.
# The Premiere CEP panel is installed separately (docs/install.md).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STAGE="$HERE/build"
DIST="$HERE/dist"
VERSION="$(node -p "require('$HERE/manifest.json').version")"
OUT="$DIST/premiere-claude-bridge-$VERSION.mcpb"

rm -rf "$STAGE"
mkdir -p "$STAGE" "$DIST"
cp "$HERE/manifest.json" "$STAGE/manifest.json"
cp "$ROOT/mcp-server/server.js" "$ROOT/mcp-server/package.json" "$ROOT/mcp-server/package-lock.json" "$STAGE/"
cp "$ROOT/LICENSE" "$STAGE/LICENSE"

(cd "$STAGE" && npm ci --omit=dev --ignore-scripts --no-audit --no-fund)

npx --yes @anthropic-ai/mcpb@2.1.2 validate "$STAGE/manifest.json"
rm -f "$OUT"
npx --yes @anthropic-ai/mcpb@2.1.2 pack "$STAGE" "$OUT"

echo "bundle: $OUT"
shasum -a 256 "$OUT"
