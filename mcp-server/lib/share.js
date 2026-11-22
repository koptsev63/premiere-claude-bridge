// Sharing the editor's journal (0.8): an explicit yes, then automatic.
//
// What leaves the machine: editing decisions and the shape of sequences,
// with every name, path and free text replaced by a salted hash. Numbers
// (frames, seconds, track indexes, fps, sizes) and the decision type stay.
// No frames, no audio, no file names, no project names, no IP stored on the
// receiving side. Off until the editor says yes; "no" later deletes what
// was sent and starts a fresh anonymous id. Details: DATA.md.
//
// 0.8.3: on by default (the owner's decision, 02.11.2026), off with one
// click or one sentence, and that "no" is kept. Never chosen = on from the
// first run of 0.8.3; an explicit no from 0.8.1/0.8.2 stays a no. The editor
// is told once (pr_status tellEditor, the panel line). Lawful basis moves from
// consent to legitimate interest with the right to object (DATA.md).
//
// 0.8.2: only work done after the yes is sent (sharingSince), never the
// journal or autosaves that were there before; and the editor can keep any
// project out (projects under NDA): «не отправляй этот проект».
//
// 0.8.5: the bridge used to learn a project folder only when Claude asked the
// open Premiere for it, so an editor who cuts with Claude closed sent nothing.
// Now it also reads Premiere's own list of recent projects and its auto-save
// setting from the preferences on disk, and reads the saved .prproj files as
// well as the autosaves. A diff whose starting point is from before the yes
// stays home. Every round leaves a trace (lastTick, lastImportError), and
// doctor() says in words why nothing went.
//
// 0.8.7: an editor who cuts in DaVinci Resolve had the box ticked and nothing
// went: the bridge only knew Premiere. Now it also reads Resolve's own project
// library (every project is a Project.db file there), the Project backups
// Resolve writes and any .drp in the backup folder (lib/drp.js), with the same
// rules: only what was saved after the yes, projects kept out by name, no
// names or paths leave. doctor() says whether Resolve is found and whether its
// backups are on.

import { randomUUID, createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, statSync, openSync, closeSync, unlinkSync, renameSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { homedir } from "node:os";
import { gzipSync } from "node:zlib";
import * as JR from "./journal.js";
import { snapshotFromProject, readProjectXml } from "./prproj.js";
import * as RS from "./drp.js";
import { LOCAL_ONLY_KEYS } from "./origin.js";

export const HOME = process.env.PREMIERE_BRIDGE_HOME || join(homedir(), ".premiere-bridge");
export const ENDPOINT = (process.env.PREMIERE_SHARE_URL || "https://bridge.204-168-219-232.sslip.io").replace(/\/+$/, "");
export const CONSENT_VERSION = 2; // 2 = on by default, off on request
export const DATA_URL = "https://github.com/koptsev63/premiere-claude-bridge/blob/main/DATA.md";
const MAX_BATCH = 4000;

export const CONSENT_TEXT = {
  ru: "Мост помогает обучать ассистента монтажу: сам отправляет обезличенную структуру вашего монтажа (где разрезы, сколько кадров снято с начала и конца плана, длины планов, темп). Без видео, звука, имён файлов и названий проектов. Это включено по умолчанию и касается только работы с момента обновления. Не хотите: снимите галочку «Помогать обучать ассистента» в панели или скажите «не отправляй журнал», и всё отправленное удалится. Проект под NDA можно исключить отдельно: «не отправляй этот проект». Подробно: " + DATA_URL,
  en: "The bridge helps train the editing assistant: it sends an anonymized outline of your edit (cut points, frames trimmed, shot lengths, pace). No video, no audio, no file or project names. This is on by default and covers only work from the update on. Don't want it: untick «Помогать обучать ассистента» in the panel or tell Claude «don't send my journal», and everything sent is deleted. A project under NDA can be kept out on its own. Details: " + DATA_URL,
};
export const NOTICE_TEXT = CONSENT_TEXT;

// On unless the editor said no. null = never chose = on by default.
export function isOn(s) { return !!s && s.consent !== false; }

// The anonymous id, the salt and the start moment, made on first use.
export function ensureStarted(home = HOME) {
  let st = loadState(home);
  if (!isOn(st) || (st.installId && st.salt && st.sharingSince)) return st;
  updateState((s) => {
    if (!isOn(s)) return;
    if (!s.installId) s.installId = randomUUID();
    if (!s.salt) s.salt = randomUUID() + randomUUID();
    if (!s.sharingSince) {
      // never chose: on by default from now; a 0.8.1 yes had no start moment: from now too, never back to the beginning
      s.sharingSince = new Date().toISOString();
      if (s.consent !== true) { s.consentVia = s.consentVia || "default"; s.consentVersion = CONSENT_VERSION; }
    }
  }, home);
  return loadState(home);
}

// The editor has been told once that sharing is on by default.
export function markNoticeGiven(home = HOME) {
  updateState((s) => { s.noticeGiven = new Date().toISOString(); }, home);
}

// ---- state -----------------------------------------------------------------

export function statePath(home = HOME) { return join(home, "share.json"); }

const FRESH = () => ({ consent: null, sent: {}, autosaveSeen: {}, projectDirs: [] });
const pause = (ms) => { try { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); } catch {} };

// Several bridges share this file. Only a missing file means "never chose"; a
// file that does not parse is retried, never replaced by the default, or a
// stored "no" or an exclusion could be lost.
export function loadState(home = HOME) {
  for (let i = 0; ; i++) {
    let raw;
    try { raw = readFileSync(statePath(home), "utf8"); }
    catch (e) { if (e && e.code === "ENOENT") return FRESH(); if (i >= 4) throw e; pause(25); continue; }
    try { return JSON.parse(raw); }
    catch (e) { if (i >= 4) throw new Error("share.json не читается: " + e.message); pause(25); }
  }
}

export function saveState(s, home = HOME) {
  mkdirSync(home, { recursive: true });
  const tmp = statePath(home) + "." + process.pid + "." + randomUUID().slice(0, 8) + ".tmp";
  writeFileSync(tmp, JSON.stringify(s, null, 2));
  renameSync(tmp, statePath(home)); // atomic: a reader sees the old file or the new one, never half
  return s;
}

// Re-read, change, write: the autopilot and a consent click may overlap,
// and a stale copy must never bring back a "yes" the editor took away.
export function updateState(fn, home = HOME) {
  const s = loadState(home);
  fn(s);
  return saveState(s, home);
}

export function consentStatus(home = HOME) {
  const s = loadState(home);
  return {
    consent: s.consent ?? null, // true | false | null (never chose = on by default)
    on: isOn(s),
    byDefault: s.consent == null,
    noticeGiven: s.noticeGiven || null,
    at: s.consentAt || null,
    via: s.consentVia || null,
    installId: isOn(s) ? s.installId || null : null,
    since: isOn(s) ? s.sharingSince || null : null,
    excluded: s.excluded || [],
    lastUpload: s.lastUpload || null,
    sentItems: s.sentItems || 0,
    lastError: s.lastError || null,
    endpoint: ENDPOINT,
    text: CONSENT_TEXT,
  };
}

// yes: start (new anonymous id if none). no after yes: ask the receiver to
// delete everything under the old id, then forget it.
export async function setConsent(choice, { via = "claude", home = HOME, fetchImpl = globalThis.fetch } = {}) {
  let was = null, oldId = null;
  updateState((s) => {
    was = s.consent ?? null;
    oldId = s.installId || null;
    if (choice) {
      if (!s.installId) s.installId = randomUUID();
      if (!s.salt) s.salt = randomUUID() + randomUUID(); // never leaves this machine: the receiver cannot reverse a hash by guessing names
      if (!s.sharingSince) s.sharingSince = new Date().toISOString(); // nothing from before the start ever goes
      s.consent = true;
    } else {
      s.consent = false;
      s.installId = null;
      s.salt = null;
      s.sent = {};
      s.sentItems = 0;
      delete s.sharingSince;
      delete s.lastImportError;
      s.lastError = null;
    }
    s.consentAt = new Date().toISOString();
    s.consentVia = via;
    s.consentVersion = CONSENT_VERSION;
  }, home);
  const result = { consent: !!choice, was };
  // The "no" is on disk first; only then ask the receiver to forget the old id.
  if (!choice && oldId) result.deleted = await deleteRemote(oldId, { fetchImpl }).catch((e) => ({ ok: false, error: e.message }));
  return { ...result, status: consentStatus(home) };
}

// Keep one project out of sharing, or let it back in. The name stays on this
// machine. Work done while a project was out never goes, even after it is let
// back in. What was sent before stays until the editor unticks sharing.
export function projectKey(project) {
  const base = String(project || "").split(/[\\/]/).pop();
  return JR.slug(base);
}

export function setExcluded(project, exclude = true, home = HOME) {
  if (!project) throw new Error("Не знаю, какой проект: откройте его в Premiere или назовите.");
  const key = projectKey(project);
  let list = [];
  updateState((s) => {
    const set = new Set(s.excluded || []);
    if (exclude) set.add(key); else set.delete(key);
    s.excluded = list = Array.from(set).sort();
    // A Resolve project is not journalled while out; saves from before this moment never become a diff
    s.resolveGap = { ...(s.resolveGap || {}), [resolveJournalKey(String(project).split(/[\\/]/).pop())]: new Date().toISOString() };
  }, home);
  return { project: key, excluded: exclude, all: list };
}

export function isExcluded(project, home = HOME) {
  return (loadState(home).excluded || []).includes(projectKey(project));
}

// ---- anonymizing -----------------------------------------------------------

// Strings allowed through as they are: enum-like fields and timestamps.
const KEEP_STRING = new Set(["type", "kind", "item", "source", "t", "from_t", "to_t", "unit"]);
// LOCAL_ONLY_KEYS: where an edit came from stays on this machine (lib/origin.js).
const DROP = new Set(["file", "path", "comment", "comments", "note", "projectPath", "mediaPath", "hash", ...LOCAL_ONLY_KEYS]);
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z$/;

// The only file extensions that go out: a known media type, never the tail of a free-text name ("Mr.Smith").
const MEDIA_EXT = new Set("mov mp4 m4v mxf avi mkv webm mts m2ts braw r3d ari arx crm dng exr dpx tif tiff png jpg jpeg heic gif psd ai svg wav aif aiff mp3 m4a aac flac ogg caf bwf mpg mpeg prproj drp".split(" "));

export function hashWith(salt) {
  return (v) => "h" + createHash("sha256").update(String(salt)).update("\u0000").update(String(v)).digest("hex").slice(0, 12);
}

export function anonymize(value, salt, key = "") {
  const h = hashWith(salt);
  if (value == null || typeof value === "number" || typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (KEEP_STRING.has(key) && (key !== "t" || ISO.test(value))) return value;
    if ((key === "from" || key === "to") && ISO.test(value)) return value;
    return h(value);
  }
  if (Array.isArray(value)) return value.map((v) => anonymize(v, salt, key));
  if (typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (DROP.has(k)) continue;
      // keep the file extension of media: "mov", "mp4" says something about the material, nothing about the client
      if (k === "name" && typeof v === "string") { const e = /\.([a-z0-9]{2,5})$/i.exec(v)?.[1]?.toLowerCase(); if (e && MEDIA_EXT.has(e)) out.ext = e; }
      out[k] = anonymize(v, salt, k);
    }
    return out;
  }
  return null;
}

// ---- what to send ----------------------------------------------------------

function projectDirs(root = JR.JOURNAL_ROOT) {
  if (!existsSync(root)) return [];
  return readdirSync(root).map((n) => join(root, n)).filter((d) => { try { return statSync(d).isDirectory(); } catch { return false; } });
}

// New decisions (events.jsonl past the line we stopped at) and the current
// shape of every sequence whose latest snapshot is new.
export function collect(state, { root = JR.JOURNAL_ROOT } = {}) {
  const salt = state.salt || state.installId;
  const since = state.sharingSince ? Date.parse(state.sharingSince) : 0;
  const after = (t) => !since || (Date.parse(t) || 0) >= since;
  const excluded = new Set(state.excluded || []);
  const items = [], marks = {};
  for (const d of projectDirs(root)) {
    const key = basename(d); // on Windows a split on "/" kept the whole path and exclusions never matched
    const p = hashWith(salt)(key);
    const done = state.sent?.[key] || { lines: 0, snapshot: null };
    const rows = JR.readEvents(d);
    const snaps = JR.listSnapshots(d);
    const last = snaps[snaps.length - 1];
    if (excluded.has(key) || excluded.has(key.replace(RESOLVE_SUFFIX_RE, ""))) { marks[key] = { lines: rows.length, snapshot: last || done.snapshot }; continue; } // skipped for good
    const baseline = (r) => (typeof r.from_t === "string" ? r.from_t : typeof r.from === "string" && ISO.test(r.from) ? r.from : null);
    for (const r of rows.slice(done.lines)) if (after(r.t) && (!baseline(r) || after(baseline(r)))) items.push({ item: "decision", project: p, ...anonymize(r, salt) });
    let snapshot = done.snapshot;
    if (last && last !== snapshot) {
      try {
        const snap = JR.loadSnapshot(last);
        if (!after(snap.t)) throw new Error("before the yes");
        for (const seq of snap.sequences || []) {
          const shape = JR.sequenceShape(seq);
          items.push({ item: "shape", project: p, t: snap.t, sequence: hashWith(salt)(seq.sequenceID || seq.name), width: seq.width, height: seq.height, fps: seq.fps, end: seq.end, ...anonymize(shape, salt) });
        }
      } catch {}
      snapshot = last;
    }
    marks[key] = { lines: rows.length, snapshot };
  }
  return { items, marks };
}

export async function upload({ home = HOME, root = JR.JOURNAL_ROOT, fetchImpl = globalThis.fetch, version = "0" } = {}) {
  const s = ensureStarted(home);
  if (!isOn(s) || !s.installId) return { sent: 0, skipped: "editor said no" };
  const id = s.installId;
  const { items, marks } = collect(s, { root });
  const stillYes = () => { const now = loadState(home); return isOn(now) && now.installId === id; };
  if (!items.length) { if (stillYes()) updateState((x) => { x.sent = { ...x.sent, ...marks }; }, home); return { sent: 0, nothingNew: true }; }
  let sent = 0, error = null;
  try {
    for (let i = 0; i < items.length; i += MAX_BATCH) {
      if (!stillYes()) break; // the editor said no while we were sending
      const batch = items.slice(i, i + MAX_BATCH);
      const body = gzipSync(Buffer.from(JSON.stringify({ installId: id, bridgeVersion: version, consentVersion: s.consentVersion || CONSENT_VERSION, batch })));
      const res = await fetchImpl(`${ENDPOINT}/v1/journal`, { method: "POST", headers: { "Content-Type": "application/json", "Content-Encoding": "gzip" }, body, signal: AbortSignal.timeout(20000) });
      if (!res.ok) throw new Error(`receiver answered ${res.status}`);
      sent += batch.length;
    }
  } catch (e) { error = e.message; }
  if (!stillYes()) {
    // Withdrawn mid-send: whatever reached the receiver under the old id goes too.
    if (sent) await deleteRemote(id, { fetchImpl }).catch(() => {});
    return { sent: 0, withdrawn: true };
  }
  updateState((x) => {
    if (error) { x.lastError = { at: new Date().toISOString(), error }; return; }
    x.sent = { ...x.sent, ...marks };
    x.sentItems = (x.sentItems || 0) + sent;
    x.lastUpload = new Date().toISOString();
    x.lastError = null;
  }, home);
  return error ? { sent, error } : { sent };
}

// Exactly what the next send would contain, without sending it.
export function preview({ home = HOME, root = JR.JOURNAL_ROOT, limit = 20 } = {}) {
  const s0 = loadState(home);
  const s = isOn(s0) && !s0.sharingSince && s0.consent !== true ? { ...s0, sharingSince: new Date().toISOString() } : s0;
  const st = s.salt ? s : { ...s, salt: "preview-" + randomUUID(), installId: "preview" };
  const { items } = collect(st, { root });
  const note = isOn(s)
    ? "Уходит только работа, сделанная после включения" + (s.excluded?.length ? ", кроме проектов, которые вы исключили." : ".")
    : "Отправка выключена, ничего не уходит. Это пример того, как выглядели бы данные.";
  return { consent: s.consent ?? null, wouldSend: items.length, note, excluded: s.excluded || [], sample: items.slice(0, limit) };
}

export async function deleteRemote(installId, { fetchImpl = globalThis.fetch } = {}) {
  const res = await fetchImpl(`${ENDPOINT}/v1/install/${installId}`, { method: "DELETE", signal: AbortSignal.timeout(15000) });
  if (!res.ok) throw new Error(`receiver answered ${res.status}`);
  return res.json();
}

// ---- autosaves: the journal fills itself ------------------------------------
// Premiere writes "<project folder>/Adobe Premiere Pro Auto-Save/*.prproj"
// every few minutes. We learn project folders from the live bridge and read
// the new autosaves; Premiere is never asked anything for this.

export function rememberProject(projectPath, home = HOME) {
  if (!projectPath || typeof projectPath !== "string") return;
  const dir = dirname(projectPath);
  updateState((s) => {
    s.projectDirs = Array.from(new Set([dir, ...(s.projectDirs || [])])).slice(0, 50);
    if (/\.prproj$/i.test(projectPath) && !/Auto-Save/i.test(basename(dirname(projectPath)))) s.projectFiles = Array.from(new Set([projectPath, ...(s.projectFiles || [])])).slice(0, 50);
  }, home);
}

// ---- Premiere's own preferences: recent projects and where autosaves go ----
// <Documents>/Adobe/Premiere Pro/<version>/Profile-<user>/Adobe Premiere Pro Prefs
// is XML with BE.Prefs.MRU.Document.N (recent projects) and the auto-save
// settings. Read on this machine only; nothing from it leaves as it is.

export const AUTOSAVE_DIR = "Adobe Premiere Pro Auto-Save";

// Where Premiere keeps its preferences: Documents, or OneDrive's Documents on
// a Windows machine with folder redirection. PREMIERE_PREFS_DIR overrides
// (tests point it at a temporary folder, never at the real one).
export function prefsBases() {
  if (process.env.PREMIERE_PREFS_DIR) return [process.env.PREMIERE_PREFS_DIR];
  const docs = [join(homedir(), "Documents"), join(homedir(), "OneDrive", "Documents")];
  if (process.env.OneDrive) docs.push(join(process.env.OneDrive, "Documents"));
  return Array.from(new Set(docs.map((d) => join(d, "Adobe", "Premiere Pro"))));
}
export const prefsBase = () => prefsBases()[0];

const unxml = (v) => v.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");

export function premierePrefs(bases = prefsBases()) {
  const out = [];
  out.errors = [];
  for (const base of [].concat(bases)) {
  let versions = [];
  try { versions = readdirSync(base); } catch (e) { if (e && e.code !== "ENOENT") out.errors.push(`${e.code || "error"}: ${base}`); continue; }
  for (const ver of versions) {
    const vdir = join(base, ver);
    let profiles = [];
    try { profiles = readdirSync(vdir).filter((n) => n.startsWith("Profile-")); } catch { continue; }
    for (const prof of profiles) {
      const file = join(vdir, prof, "Adobe Premiere Pro Prefs");
      let xml;
      try { xml = readFileSync(file, "utf8"); } catch { continue; }
      const val = (k) => { const m = new RegExp("<" + k.replace(/\./g, "\\.") + ">([^<]*)<").exec(xml); return m ? unxml(m[1]) : null; };
      const mru = [...xml.matchAll(/<BE\.Prefs\.MRU\.Document\.\d+>([^<]+)</g)].map((m) => unxml(m[1]));
      out.push({
        version: ver, versionDir: vdir, mru,
        autoSave: val("BE.Prefs.AutoSave.DoSave") !== "false",
        autoSaveFolder: val("BE.Prefs.ScratchDisks.FirstAutoSaveFolder"),
        toCloud: val("BE.Prefs.AutoSave.DoSaveToCloud") === "true",
      });
    }
  }
  }
  return out;
}

const inAutoSave = (f) => /Auto-Save/i.test(basename(dirname(f)));
const isDir = (d) => { try { return statSync(d).isDirectory(); } catch { return false; } };
const isFile = (f) => { try { return statSync(f).isFile(); } catch { return false; } };

// Everything the autopilot reads: saved projects and auto-save folders, from
// what the live bridge saw and from Premiere's preferences.
export function sources(state, { bases = prefsBases(), resolveDirs = RS.resolvePrefsDirs() } = {}) {
  const prefs = premierePrefs(bases);
  const projects = new Set((state.projectFiles || []).filter((f) => !inAutoSave(f)));
  for (const p of prefs) for (const f of p.mru) if (/\.prproj$/i.test(f) && !inAutoSave(f)) projects.add(f);
  const projectFiles = [...projects].filter(isFile);
  const folders = new Set(autosaveFolders(state));
  for (const f of projectFiles) folders.add(join(dirname(f), AUTOSAVE_DIR));
  for (const p of prefs) {
    folders.add(join(p.versionDir, AUTOSAVE_DIR)); // "My Documents" location
    const v = p.autoSaveFolder;
    if (v && !/^SameAsProject$|^MyDocuments$/i.test(v)) folders.add(/Auto-Save/i.test(basename(v)) ? v : join(v, AUTOSAVE_DIR));
  }
  if (prefs.some((p) => p.toCloud)) {
    try { for (const n of readdirSync(homedir())) if (/^Creative Cloud Files/.test(n)) folders.add(join(homedir(), n, AUTOSAVE_DIR)); } catch {}
  }
  return { prefs, prefsErrors: prefs.errors, projectFiles, folders: [...folders].filter(isDir), resolve: RS.resolveSources(resolveDirs) };
}

// A Resolve project's journal lives apart from a Premiere project of the same name.
// The suffix goes on after the name is cut to length, so it is always there.
export const RESOLVE_SUFFIX = ".resolve";
const RESOLVE_SUFFIX_RE = /\.resolve$/;
export const resolveJournalKey = (project) => JR.slug(project) + RESOLVE_SUFFIX;

export function autosaveFolders(state) {
  return (state.projectDirs || []).map((d) => join(d, "Adobe Premiere Pro Auto-Save")).filter((d) => existsSync(d));
}

// A few files per round, yielding between them: a big project's autosave
// takes a while to parse, and the bridge must keep answering meanwhile.
export const AUTOSAVES_PER_TICK = 6;
const yieldNow = () => new Promise((r) => setImmediate(r));

export async function importNewAutosaves({ home = HOME, root = JR.JOURNAL_ROOT, limit = AUTOSAVES_PER_TICK, bases, resolveDirs } = {}) {
  const s = ensureStarted(home);
  if (!isOn(s)) return { files: 0, events: 0, left: 0, stopped: true };
  const since = s.sharingSince ? Date.parse(s.sharingSince) : 0; // nothing saved before the yes is read for sharing
  const src = sources(s, { bases: bases ?? prefsBases(), resolveDirs: resolveDirs ?? RS.resolvePrefsDirs() });
  const seenOf = (k) => s.autosaveSeen?.[k] || 0;
  const fresh = [];
  for (const folder of src.folders) {
    let names = [];
    try { names = readdirSync(folder); } catch { continue; }
    for (const n of names) {
      if (!n.toLowerCase().endsWith(".prproj")) continue;
      const f = join(folder, n);
      let m; try { m = statSync(f).mtimeMs; } catch { continue; }
      if (m > seenOf(folder) && m >= since) fresh.push({ f, m, key: folder });
    }
  }
  for (const f of src.projectFiles) {
    let m; try { m = statSync(f).mtimeMs; } catch { continue; }
    if (m > seenOf(f) && m >= since) fresh.push({ f, m, key: f });
  }
  // Resolve: the live Project.db of every project, then backups and .drp in the backup folder
  for (const f of [...src.resolve.projects, ...src.resolve.backups]) {
    if (!/\.drp$/i.test(f) && !src.resolve.canReadDb) continue; // doctor says why
    let m; try { m = statSync(f).mtimeMs; } catch { continue; }
    if (m > seenOf(f) && m >= since) fresh.push({ f, m, key: f, resolve: true });
  }
  fresh.sort((a, b) => a.m - b.m); // oldest first, so every diff runs forward
  let files = 0, events = 0, left = 0, failed = 0, lastFail = null;
  for (const { f, m, key, resolve } of fresh) {
    if (files >= limit) { left++; continue; }
    await yieldNow();
    if (!isOn(loadState(home))) return { files, events, stopped: true };
    if (resolve) {
      // Kept out by name: not even journalled, and the next save after it is let
      // back in starts a fresh baseline, so work done while out never becomes a diff.
      // setExcluded leaves a mark (resolveGap) on every exclude and include: a save older than the
      // mark is from before or during the exclusion and is skipped, the first one after it starts a
      // fresh baseline. Work done while out never becomes a diff, even if no round saw it.
      const st = loadState(home);
      const out = (name) => !!name && (st.excluded || []).includes(projectKey(name));
      const markOf = (...names) => names.filter(Boolean).map((n) => st.resolveGap?.[resolveJournalKey(n)]).filter(Boolean).sort().pop();
      try {
        const live = src.resolve.projects.includes(f);
        const folderName = live ? RS.projectNameFromPath(f) : null;
        if (!(out(folderName) || (markOf(folderName) && m < Date.parse(markOf(folderName))))) { // a library project is named by its folder: no copy of an NDA project at all
          const snap = RS.snapshotFromResolve(f, { mtime: new Date(m), scratch: join(home, "tmp"), source: live ? "resolve-db" : "resolve-backup" });
          const k = resolveJournalKey(snap.project), dir = join(root, k);
          const mark = markOf(snap.project, folderName);
          if (out(snap.project) || out(folderName) || (mark && m < Date.parse(mark))) {} // kept out, or saved before it was let back in
          else if (mark) {
            JR.saveSnapshot(dir, snap); // a new starting point, no decisions from across the gap
            updateState((x) => { for (const n of [snap.project, folderName]) if (n && x.resolveGap) delete x.resolveGap[resolveJournalKey(n)]; }, home);
          } else if (JR.listSnapshots(dir).some((p) => basename(p) > JR.stamp(snap.t))) {
            JR.saveSnapshot(dir, snap); // read out of order (the live file was busy): kept, but no diff, or a decision would count twice
          } else {
            const r = JR.record(dir, snap, { source: snap.source, file: f });
            events += r.events || 0;
          }
        }
      } catch (e) {
        const why = { at: new Date().toISOString(), file: RS.projectNameFromPath(f), error: String(e && e.message || e).slice(0, 300) };
        if (e && e.retry) { if (!e.quiet) lastFail = why; left++; continue; } // Resolve was saving, or no disk room: the same file, next round
        failed++; lastFail = why;
      }
      files++;
      updateState((x) => { x.autosaveSeen = { ...(x.autosaveSeen || {}), [key]: Math.max(x.autosaveSeen?.[key] || 0, m) }; }, home);
      continue;
    }
    try {
      const snap = snapshotFromProject(f, { mtime: new Date(m) });
      const seqs = snap.sequences || [];
      if (seqs.every((q) => !(q.video || []).length && !(q.audio || []).length)) {
        // nothing on any track: fine for a project with media only, a reading failure if the file has clips on tracks
        if (/<(Video|Audio)ClipTrackItem\s/.test(readProjectXml(f))) throw new Error("в проекте есть клипы на дорожках, но мост их не прочитал: новый формат Premiere или файл повреждён");
      }
      const r = JR.record(JR.journalDir(snap.project, root), snap, { source: snap.source, file: f });
      events += r.events || 0;
    } catch (e) { failed++; lastFail = { at: new Date().toISOString(), file: basename(f), error: String(e && e.message || e).slice(0, 300) }; }
    files++;
    updateState((x) => { x.autosaveSeen = { ...(x.autosaveSeen || {}), [key]: Math.max(x.autosaveSeen?.[key] || 0, m) }; }, home);
  }
  updateState((x) => {
    // Resolve keeps a few rolling backups and deletes the old ones: forget what is gone
    for (const k of Object.keys(x.autosaveSeen || {})) if (RS.isResolveFile(k) && !existsSync(k)) delete x.autosaveSeen[k];
    x.lastImport = { at: new Date().toISOString(), files, events, failed, left, projects: src.projectFiles.length, folders: src.folders.length, resolveProjects: src.resolve.projects.length, resolveBackups: src.resolve.backups.length };
    if (lastFail) x.lastImportError = lastFail;
    else if (files) delete x.lastImportError; // a clean round: the old reason no longer holds
  }, home);
  return { files, events, left, failed };
}

// Every Claude session starts its own bridge, so several autopilots can run on
// one machine. One round at a time across all of them, or the same work goes
// twice. A lock older than LOCK_STALE_MS is from a bridge that died mid-round.
export const LOCK_STALE_MS = 5 * 60 * 1000;
export function acquireLock(home = HOME, name = "share.lock") {
  mkdirSync(home, { recursive: true });
  const f = join(home, name);
  const token = process.pid + ":" + randomUUID();
  for (let i = 0; i < 2; i++) {
    try {
      const fd = openSync(f, "wx"); writeFileSync(fd, token); closeSync(fd);
      // release only our own lock: a round that outlived the stale limit must not delete the next owner's
      return () => { try { if (readFileSync(f, "utf8") === token) unlinkSync(f); } catch {} };
    } catch {
      try {
        const age = Date.now() - statSync(f).mtimeMs;
        if (age > LOCK_STALE_MS || age < -LOCK_STALE_MS) unlinkSync(f); else return null; // stale, or dated in the future by a clock change
      } catch { return null; }
    }
  }
  return null;
}

// One round of the autopilot: read new autosaves, send what is new.
export async function tick({ home = HOME, root = JR.JOURNAL_ROOT, fetchImpl, version } = {}) {
  const s = ensureStarted(home);
  if (!isOn(s)) return { skipped: true };
  const release = acquireLock(home);
  if (!release) return { busy: true }; // another bridge on this machine is in a round
  try {
    const imported = await importNewAutosaves({ home, root });
    const sent = await upload({ home, root, fetchImpl, version });
    updateState((x) => { x.lastTick = new Date().toISOString(); }, home);
    return { imported, ...sent };
  } finally { release(); }
}

// In words: is sharing working on this machine, and if not, why.
// light: for pr_status, without walking the journal; cached a few minutes.
let doctorCache = null;
export function doctor({ home = HOME, root = JR.JOURNAL_ROOT, bases, resolveDirs, light = false } = {}) {
  if (light && doctorCache && doctorCache.home === home && Date.now() - doctorCache.at < 5 * 60 * 1000) return doctorCache.value;
  const s = loadState(home);
  const src = sources(s, { bases: bases ?? prefsBases(), resolveDirs: resolveDirs ?? RS.resolvePrefsDirs() });
  const on = isOn(s);
  const since = s.sharingSince ? Date.parse(s.sharingSince) : 0;
  const savedSince = (f) => { try { return statSync(f).mtimeMs >= since; } catch { return false; } };
  const recent = src.projectFiles.filter(savedSince);
  const rs = src.resolve, resolveFound = rs.configs.length > 0;
  const rsFiles = [...rs.projects, ...rs.backups], rsRecent = rsFiles.filter(savedSince);
  const pending = on && !light ? collect({ ...s, salt: s.salt || "doctor" }, { root }).items.length : null;
  let lockAge = null;
  try { lockAge = Math.round((Date.now() - statSync(join(home, "share.lock")).mtimeMs) / 1000); } catch {}
  const problems = [];
  if (!on) problems.push("Отправка выключена: редактор снял галочку или сказал «не отправляй».");
  if (on && src.prefsErrors.length) problems.push(`Нет доступа к настройкам Premiere (${src.prefsErrors.join("; ")}): разрешите Claude доступ к папке Документы.`);
  else if (on && !src.prefs.length && !resolveFound) problems.push("Не нашёл настройки Premiere в папке Документы и настройки DaVinci Resolve: не знаю, какие проекты открывались.");
  if (src.prefs.length && src.prefs.every((p) => !p.autoSave)) problems.push("В Premiere выключено автосохранение: мост увидит только сохранённые вручную версии проекта.");
  if (on && rs.errors.length) problems.push(`Нет доступа к настройкам DaVinci Resolve (${rs.errors.join("; ")}): разрешите Claude доступ к этой папке.`);
  if (on && resolveFound && rs.projects.length && !rs.canReadDb) problems.push(`DaVinci Resolve найден, но эта версия Node (${process.versions.node}) не читает его базу проектов: нужен Node 22.5 или новее. Пока мост видит только .drp в папке бэкапов.`);
  if (resolveFound && rs.configs.every((c) => !c.liveSave && c.backups === false)) problems.push("В DaVinci Resolve выключены и Live Save, и Project backups: мост увидит работу только после ручного сохранения проекта (Cmd+S / Ctrl+S).");
  if (on && resolveFound && !rsFiles.length) problems.push("DaVinci Resolve найден, но ни одного его проекта на этом компьютере не нашёл: проекты на сетевой базе (PostgreSQL) мост не читает.");
  if (on && !src.projectFiles.length && !src.folders.length && !rsFiles.length && (src.prefs.length || !resolveFound)) problems.push("Не нашёл ни одного проекта Premiere или DaVinci Resolve и ни одной папки автосохранений.");
  if (on && (src.projectFiles.length || rsFiles.length) && !recent.length && !rsRecent.length) problems.push("После включения галочки ни один проект не сохранялся.");
  if (on && !s.lastTick && !s.lastUpload) problems.push("Автопилот ещё ни разу не запускался: он работает, пока открыт Claude с мостом. Откройте Claude хотя бы раз за день, он догонит сохранённое.");
  if (on && s.lastImport && s.lastImport.left) problems.push(`В очереди ещё ${s.lastImport.left} сохранений: мост читает по ${AUTOSAVES_PER_TICK} за 10 минут, пока открыт Claude.`);
  if (lockAge != null && lockAge > LOCK_STALE_MS / 1000) problems.push("Висит старая блокировка отправки, следующий круг её снимет.");
  if (s.lastImportError) problems.push(`Не прочитался проект ${s.lastImportError.file}: ${s.lastImportError.error}`);
  if (s.lastError) problems.push(`Сервер не принял отправку: ${s.lastError.error}`);
  if (on && !problems.length && !(s.sentItems > 0)) problems.push("Проекты находятся, но отправлять пока нечего: уйдёт, когда после включения появятся новые сохранения.");
  const value = {
    on, since: s.sharingSince || null, installId: on ? s.installId || null : null,
    premiere: src.prefs.map((p) => ({ version: p.version, autoSave: p.autoSave, autoSaveFolder: p.autoSaveFolder, recentProjects: p.mru.length })),
    resolve: rs.configs.map((c) => ({ version: c.version, backups: c.backups, backupEveryMinutes: c.backupEvery, liveSave: c.liveSave, backupFolderFound: rs.backupFolders.includes(c.backupLocation), libraries: c.libraries.length })),
    resolveProjects: rs.projects.length, resolveBackups: rs.backups.length, resolveSavedSinceOn: rsRecent.length, resolveReadable: rs.canReadDb,
    projectsFound: src.projectFiles.length, savedSinceOn: recent.length, autosaveFolders: src.folders.length,
    lastTick: s.lastTick || null, lastImport: s.lastImport || null, lastUpload: s.lastUpload || null, sentItems: s.sentItems || 0,
    waitingToSend: pending,
    verdict: problems.length ? problems : ["Всё в порядке: проекты находятся, отправка идёт."],
  };
  if (light) doctorCache = { home, at: Date.now(), value };
  return value;
}
