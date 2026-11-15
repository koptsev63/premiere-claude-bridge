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

// ---- 0.8.6: updates arrive on their own ------------------------------------
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const git = (cwd, ...a) => execFileSync("git", a, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
const setVersion = (d, v) => writeFileSync(join(d, "mcp-server", "package.json"), JSON.stringify({ version: v }));

// origin with v0.8.0 and v0.9.0 tagged on main; a clone sitting at v0.8.0.
function gitPair() {
  const origin = mkdtempSync(join(tmpdir(), "origin-"));
  git(origin, "init", "-q", "-b", "main");
  git(origin, "config", "user.email", "t@t"); git(origin, "config", "user.name", "t");
  mkdirSync(join(origin, "mcp-server"));
  setVersion(origin, "0.8.0"); git(origin, "add", "-A"); git(origin, "commit", "-qm", "0.8.0"); git(origin, "tag", "v0.8.0");
  setVersion(origin, "0.9.0"); git(origin, "commit", "-qam", "0.9.0"); git(origin, "tag", "v0.9.0");
  writeFileSync(join(origin, "unreleased.txt"), "x"); git(origin, "add", "-A"); git(origin, "commit", "-qm", "unreleased on main");
  const clone = mkdtempSync(join(tmpdir(), "clone-"));
  execFileSync("git", ["clone", "-q", origin, clone]);
  git(clone, "config", "user.email", "t@t"); git(clone, "config", "user.name", "t");
  git(clone, "reset", "-q", "--hard", "v0.8.0");
  return { origin, clone };
}
const runNoNpm = (log = []) => (cmd, args, cwd) => { if (cmd === "npm") { log.push("npm"); return ""; } return execFileSync(cmd, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim(); };

test("auto-update: a clean install moves to the latest release tag, not to unreleased main", async () => {
  const { clone } = gitPair();
  const home = mkdtempSync(join(tmpdir(), "upd-auto-"));
  const npm = [];
  const r = await U.autoUpdate("0.8.0", { dir: clone, run: runNoNpm(npm), fetchImpl: release("0.9.0"), home, force: true });
  assert.equal(r.updated, true);
  assert.equal(U.diskVersion(clone), "0.9.0");
  assert.equal(git(clone, "describe", "--tags", "--exact-match"), "v0.9.0", "the release, not main HEAD");
  assert.deepEqual(npm, ["npm"]);
  assert.match(U.restartNotice("0.8.0", clone).say, /обновился до 0\.9\.0/);
  assert.equal(U.restartNotice("0.9.0", clone), null);
  const again = await U.autoUpdate("0.8.0", { dir: clone, run: runNoNpm(), fetchImpl: release("0.9.0"), home, force: true });
  assert.equal(again.skipped, "последняя версия", "on disk already, waits for the restart");
});

test("auto-update never touches the editor's edits, commits or branch", async () => {
  const home = mkdtempSync(join(tmpdir(), "upd-auto-"));
  const a = gitPair(); setVersion(a.clone, "0.8.0-mine");
  const r1 = await U.autoUpdate("0.8.0", { dir: a.clone, run: runNoNpm(), fetchImpl: release("0.9.0"), home, force: true });
  assert.match(r1.skipped, /правки/);
  assert.match(readFileSync(join(a.clone, "mcp-server", "package.json"), "utf8"), /0\.8\.0-mine/);
  const b = gitPair(); writeFileSync(join(b.clone, "mine.txt"), "x"); git(b.clone, "add", "-A"); git(b.clone, "commit", "-qm", "mine");
  const r2 = await U.autoUpdate("0.8.0", { dir: b.clone, run: runNoNpm(), fetchImpl: release("0.9.0"), home, force: true });
  assert.match(r2.skipped, /свои коммиты/);
  assert.equal(git(b.clone, "log", "-1", "--format=%s"), "mine");
  const c = gitPair(); git(c.clone, "checkout", "-q", "-b", "wip");
  const r3 = await U.autoUpdate("0.8.0", { dir: c.clone, run: runNoNpm(), fetchImpl: release("0.9.0"), home, force: true });
  assert.match(r3.skipped, /ветка wip/);
  assert.equal(U.diskVersion(c.clone), "0.8.0");
});

test("auto-update off: by setting, by env, and never for a bundle", async () => {
  const home = mkdtempSync(join(tmpdir(), "upd-auto-"));
  const { clone } = gitPair();
  U.setAuto(false, home);
  assert.equal((await U.autoUpdate("0.8.0", { dir: clone, run: runNoNpm(), fetchImpl: release("0.9.0"), home, force: true })).skipped, "выключено");
  U.setAuto(true, home);
  process.env.PREMIERE_BRIDGE_AUTO_UPDATE = "0";
  assert.equal((await U.autoUpdate("0.8.0", { dir: clone, run: runNoNpm(), fetchImpl: release("0.9.0"), home, force: true })).skipped, "выключено");
  delete process.env.PREMIERE_BRIDGE_AUTO_UPDATE;
  assert.equal((await U.autoUpdate("0.8.0", { dir: repo(false), fetchImpl: release("0.9.0"), home, force: true })).skipped, "bundle");
  assert.equal(U.diskVersion(clone), "0.8.0");
});

test("pk update auto: on, off and status never start a pull", async () => {
  const pk = await import("../pk.js");
  const off = await pk.typed("update", ["auto", "off"]);
  assert.equal(off.enabled, false);
  const st = await pk.typed("update", ["auto"]);
  assert.equal(st.enabled, false);
  assert.ok(!("kind" in st), "status, not an update run");
  assert.equal((await pk.typed("update", ["auto", "on"])).enabled, true);
});
