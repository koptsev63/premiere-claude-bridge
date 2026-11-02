#!/usr/bin/env node
// Write server.json (the MCP Registry card) for one released version.
//
// Why: the registry installs the .mcpb attached to a GitHub release and checks
// its sha256. Until 0.8.2 server.json was edited by hand and drifted: on
// 02.11.2026 the registry still offered 0.3.0 while the bridge was at 0.8.1.
// Now the card is built from the release file itself.
//
//   node scripts/server-json.mjs <version> <path/to/premiere-claude-bridge-<version>.mcpb>
//   node scripts/server-json.mjs 0.8.2 mcpb/dist/premiere-claude-bridge-0.8.2.mcpb

import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const [version, bundle] = process.argv.slice(2);
if (!/^\d+\.\d+\.\d+$/.test(version || "") || !bundle) {
  console.error("usage: node scripts/server-json.mjs <x.y.z> <file.mcpb>");
  process.exit(2);
}
const want = `premiere-claude-bridge-${version}.mcpb`;
if (!bundle.endsWith(want)) {
  console.error(`the bundle must be ${want}, got ${bundle}`);
  process.exit(2);
}

const file = fileURLToPath(new URL("../server.json", import.meta.url));
const card = JSON.parse(readFileSync(file, "utf8"));
const sha = createHash("sha256").update(readFileSync(bundle)).digest("hex");
card.version = version;
const pkg = card.packages[0];
pkg.identifier = `https://github.com/koptsev63/premiere-claude-bridge/releases/download/v${version}/${want}`;
pkg.version = version;
pkg.fileSha256 = sha;
writeFileSync(file, JSON.stringify(card, null, 2) + "\n");
console.log(`server.json -> ${version}, sha256 ${sha}`);
