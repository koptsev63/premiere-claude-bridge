import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

process.env.PREMIERE_BRIDGE_HOME = mkdtempSync(join(tmpdir(), "upd-home-"));
const U = await import("../lib/update.js");

const release = (v) => async () => ({ ok: true, json: async () => ({ tag_name: "v" + v, html_url: "https://github.com/x/releases/tag/v" + v, assets: [{ browser_download_url: "https://x/premiere-claude-bridge-" + v + ".mcpb" }] }) });

function repo(withGit = true) {
  const d = mkdtempSync(join(tmpdir(), "repo-"));
  if (withGit) mkdirSync(join(d, ".git"));
  mkdirSync(join(d, "mcp-server"));
  writeFileSync(join(d, "mcp-server", "package.json"), JSON.stringify({ version: "0.9.0" }));
  return d;
}

test("version compare", () => {
  assert.equal(U.cmpVersion("0.8.0", "0.7.9"), 1);
  assert.equal(U.cmpVersion("v0.8.0", "0.8.0"), 0);
  assert.equal(U.cmpVersion("0.10.0", "0.9.1"), 1);
});

test("update info says what to tell the editor", async () => {
  const i = await U.updateInfo("0.7.0", { fetchImpl: release("0.8.0"), force: true });
  assert.equal(i.available, true);
  assert.match(i.say, /обнови мост/);
});

test("git install: local edits stop the update", async () => {
  const calls = [];
  const run = (cmd, args) => { calls.push(args.join(" ")); if (args[0] === "status") return " M server.js"; return "abc"; };
  const r = await U.updateBridge("0.8.0", { dir: repo(), run, fetchImpl: release("0.9.0") });
  assert.equal(r.ok, false);
  assert.match(r.error, /ваши правки/);
  assert.ok(!calls.some((c) => c.startsWith("pull")));
});

test("git install: pull, npm install, restart advice", async () => {
  let head = "aaa";
  const calls = [];
  const run = (cmd, args) => { calls.push(cmd + " " + args.join(" ")); if (args[0] === "status") return ""; if (args[0] === "pull") { head = "bbb"; return "ok"; } if (args[0] === "rev-parse") return head; return ""; };
  const r = await U.updateBridge("0.8.0", { dir: repo(), run, fetchImpl: release("0.9.0") });
  assert.equal(r.ok, true);
  assert.equal(r.updated, true);
  assert.equal(r.version, "0.9.0");
  assert.ok(calls.some((c) => c.startsWith("npm install")));
  assert.match(r.next[0], /Закройте Claude/);
});

test("bundle install: download link instead of git", async () => {
  const r = await U.updateBridge("0.8.0", { dir: repo(false), fetchImpl: release("0.9.0") });
  assert.equal(r.kind, "bundle");
  assert.match(r.steps[0], /\.mcpb$/);
});
