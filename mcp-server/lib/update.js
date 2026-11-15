// Updates in one sentence (0.8): the editor says "обнови мост" to Claude,
// Claude calls pr_update_bridge. A git install pulls and reinstalls; an
// .mcpb install gets the download link of the new bundle.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { HOME, acquireLock } from "./share.js";

export const REPO = "koptsev63/premiere-claude-bridge";
export const RELEASES_URL = `https://github.com/${REPO}/releases/latest`;
const CACHE_MS = 12 * 3600 * 1000;

export const SERVER_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_DIR = resolve(SERVER_DIR, "..");

export function cmpVersion(a, b) {
  const pa = String(a).replace(/^v/, "").split(".").map(Number), pb = String(b).replace(/^v/, "").split(".").map(Number);
  for (let i = 0; i < 3; i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d > 0 ? 1 : -1; }
  return 0;
}

function cachePath() { return join(HOME, "update-check.json"); }

export async function latestRelease({ fetchImpl = globalThis.fetch, force = false } = {}) {
  try {
    const c = JSON.parse(readFileSync(cachePath(), "utf8"));
    if (!force && Date.now() - c.at < CACHE_MS) return c.release;
  } catch {}
  const res = await fetchImpl(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { Accept: "application/vnd.github+json", "User-Agent": "premiere-claude-bridge" }, signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`GitHub answered ${res.status}`);
  const j = await res.json();
  const release = { version: String(j.tag_name || "").replace(/^v/, ""), url: j.html_url, bundle: (j.assets || []).map((a) => a.browser_download_url).find((u) => u.endsWith(".mcpb")) || null };
  try { mkdirSync(HOME, { recursive: true }); writeFileSync(cachePath(), JSON.stringify({ at: Date.now(), release })); } catch {}
  return release;
}

export async function updateInfo(current, opts = {}) {
  try {
    const r = await latestRelease(opts);
    const available = cmpVersion(r.version, current) > 0;
    return { current, latest: r.version, available, ...(available ? { say: "Есть новая версия моста. Скажите Claude: «обнови мост».", url: r.url, bundle: r.bundle } : {}) };
  } catch (e) {
    return { current, latest: null, available: false, error: e.message };
  }
}

export function installKind(dir = REPO_DIR) {
  return existsSync(join(dir, ".git")) ? "git" : "bundle";
}

function sh(cmd, args, cwd) {
  return execFileSync(cmd, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 180000 }).trim();
}

// Pull and reinstall. Never discards the editor's local changes: a dirty
// tree or a diverged branch stops with an explanation.
export async function updateBridge(current, { dir = REPO_DIR, run = sh, fetchImpl } = {}) {
  const info = await updateInfo(current, { fetchImpl, force: true });
  if (installKind(dir) === "bundle") {
    return { ok: info.available ? false : true, kind: "bundle", ...info,
      steps: info.available ? ["Скачайте новый файл моста: " + (info.bundle || info.url || RELEASES_URL), "Дважды щёлкните по нему, Claude Desktop установит его сам", "Перезапустите Claude Desktop"] : ["У вас последняя версия"] };
  }
  const before = run("git", ["rev-parse", "--short", "HEAD"], dir);
  const dirty = run("git", ["status", "--porcelain", "--untracked-files=no"], dir);
  if (dirty) return { ok: false, kind: "git", ...info, error: "В папке моста есть ваши правки, обновление их не трогает. Сохраните или отмените их и попросите снова.", changed: dirty.split("\n").slice(0, 10) };
  try { run("git", ["pull", "--ff-only"], dir); }
  catch (e) { return { ok: false, kind: "git", ...info, error: "Не получилось скачать обновление: " + String(e.stderr || e.message).slice(0, 400) }; }
  const after = run("git", ["rev-parse", "--short", "HEAD"], dir);
  if (after !== before) {
    try { run("npm", ["install", "--omit=dev", "--no-audit", "--no-fund"], join(dir, "mcp-server")); }
    catch (e) { return { ok: false, kind: "git", before, after, error: "Обновление скачано, но npm install упал: " + String(e.stderr || e.message).slice(0, 400) }; }
  }
  let version = current;
  try { version = JSON.parse(readFileSync(join(dir, "mcp-server", "package.json"), "utf8")).version; } catch {}
  return {
    ok: true, kind: "git", before, after, updated: after !== before, version,
    next: after !== before
      ? ["Закройте Claude Code (или Claude Desktop) и откройте снова, чтобы включилась новая версия", "Панель в Premiere: закройте и снова откройте Окно > Расширения > Claude Bridge"]
      : ["У вас последняя версия"],
  };
}

// ---- 0.8.6: updates arrive on their own -------------------------------------
// A git install moves itself to the latest RELEASE tag (never to an unreleased
// main), only fast-forward, only from the default branch, never over the
// editor's own edits or commits. The running bridge keeps its code until
// Claude restarts; pr_status then says a new version is waiting. A bundle
// (.mcpb) install cannot replace itself: it keeps the "обнови мост" hint.
// Off: PREMIERE_BRIDGE_AUTO_UPDATE=0, or `pk update auto off` (kept in
// ~/.premiere-bridge/update-auto.json).

function autoPath(home = HOME) { return join(home, "update-auto.json"); }
export function autoState(home = HOME) {
  try { return JSON.parse(readFileSync(autoPath(home), "utf8")); } catch { return { enabled: true }; }
}
function saveAuto(s, home = HOME) { try { mkdirSync(home, { recursive: true }); writeFileSync(autoPath(home), JSON.stringify(s, null, 2)); } catch {} return s; }
export function setAuto(enabled, home = HOME) { return saveAuto({ ...autoState(home), enabled: !!enabled, changedAt: new Date().toISOString() }, home); }
export function autoEnabled(home = HOME) {
  if (/^(0|false|off|no)$/i.test(process.env.PREMIERE_BRIDGE_AUTO_UPDATE || "")) return false;
  return autoState(home).enabled !== false;
}

// The version on disk: after an update it is ahead of the running one.
export function diskVersion(dir = REPO_DIR) {
  try { return JSON.parse(readFileSync(join(dir, "mcp-server", "package.json"), "utf8")).version; } catch { return null; }
}

export async function autoUpdate(current, { dir = REPO_DIR, run = sh, fetchImpl, home = HOME, force = false } = {}) {
  const note = (r) => { saveAuto({ ...autoState(home), last: { at: new Date().toISOString(), ...r } }, home); return r; };
  if (!autoEnabled(home)) return { skipped: "выключено" };
  if (installKind(dir) !== "git") return { skipped: "bundle" };
  const info = await updateInfo(current, { fetchImpl, force });
  if (!info.latest) return note({ skipped: "нет связи с GitHub", error: info.error || null });
  const onDisk = diskVersion(dir) || current;
  if (cmpVersion(info.latest, onDisk) <= 0) return { skipped: "последняя версия", latest: info.latest };
  const release = acquireLock(home, "update.lock"); // several bridges per machine: one git at a time
  if (!release) return { skipped: "обновляет другая копия моста" };
  try {
    const branch = run("git", ["rev-parse", "--abbrev-ref", "HEAD"], dir);
    let base = "main";
    try { base = run("git", ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], dir).replace(/^origin\//, ""); } catch {}
    if (branch !== base) return note({ skipped: `ветка ${branch}, не ${base}: обновляю только основную` });
    if (run("git", ["status", "--porcelain", "--untracked-files=no"], dir)) return note({ skipped: "в папке моста есть правки, не трогаю" });
    const tag = "v" + info.latest;
    run("git", ["fetch", "--quiet", "--tags", "origin"], dir);
    const before = run("git", ["rev-parse", "--short", "HEAD"], dir);
    try { run("git", ["merge", "--ff-only", "--quiet", `refs/tags/${tag}`], dir); }
    catch (e) { return note({ skipped: "есть свои коммиты поверх релиза, не трогаю", error: String(e.stderr || e.message).slice(0, 300) }); }
    const after = run("git", ["rev-parse", "--short", "HEAD"], dir);
    if (after !== before) run("npm", ["install", "--omit=dev", "--no-audit", "--no-fund"], join(dir, "mcp-server"));
    return note({ updated: after !== before, from: onDisk, to: diskVersion(dir) || info.latest, before, after });
  } catch (e) {
    return note({ error: String(e.stderr || e.message).slice(0, 400) });
  } finally { release(); }
}

// For pr_status: the editor hears once per version that it is waiting for a restart.
export function restartNotice(running, dir = REPO_DIR) {
  const disk = diskVersion(dir);
  if (!disk || cmpVersion(disk, running) <= 0) return null;
  return { installed: disk, running, say: `Мост сам обновился до ${disk}. Закройте и снова откройте Claude, а в Premiere панель Claude Bridge, чтобы включилась новая версия.` };
}
