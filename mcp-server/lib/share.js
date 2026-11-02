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

import { randomUUID, createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";
import { gzipSync } from "node:zlib";
import * as JR from "./journal.js";
import { snapshotFromProject } from "./prproj.js";

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
  if (!isOn(st) || (st.installId && st.salt && (st.sharingSince || st.consent === true))) return st;
  updateState((s) => {
    if (!isOn(s)) return;
    if (!s.installId) s.installId = randomUUID();
    if (!s.salt) s.salt = randomUUID() + randomUUID();
    if (!s.sharingSince && s.consent !== true) { s.sharingSince = new Date().toISOString(); s.consentVia = s.consentVia || "default"; s.consentVersion = CONSENT_VERSION; }
  }, home);
  return loadState(home);
}

// The editor has been told once that sharing is on by default.
export function markNoticeGiven(home = HOME) {
  updateState((s) => { s.noticeGiven = new Date().toISOString(); }, home);
}

// ---- state -----------------------------------------------------------------

export function statePath(home = HOME) { return join(home, "share.json"); }

export function loadState(home = HOME) {
  try { return JSON.parse(readFileSync(statePath(home), "utf8")); }
  catch { return { consent: null, sent: {}, autosaveSeen: {}, projectDirs: [] }; }
}

export function saveState(s, home = HOME) {
  mkdirSync(home, { recursive: true });
  writeFileSync(statePath(home), JSON.stringify(s, null, 2));
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
  }, home);
  return { project: key, excluded: exclude, all: list };
}

export function isExcluded(project, home = HOME) {
  return (loadState(home).excluded || []).includes(projectKey(project));
}

// ---- anonymizing -----------------------------------------------------------

// Strings allowed through as they are: enum-like fields and timestamps.
const KEEP_STRING = new Set(["type", "kind", "item", "source", "t", "from_t", "to_t", "unit"]);
const DROP = new Set(["file", "path", "comment", "comments", "note", "projectPath", "mediaPath", "hash"]);
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z$/;

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
      if ((k === "name" || k === "path") && typeof v === "string" && /\.[a-z0-9]{2,5}$/i.test(v)) out.ext = v.split(".").pop().toLowerCase();
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
    const key = d.split("/").pop();
    const p = hashWith(salt)(key);
    const done = state.sent?.[key] || { lines: 0, snapshot: null };
    const rows = JR.readEvents(d);
    const snaps = JR.listSnapshots(d);
    const last = snaps[snaps.length - 1];
    if (excluded.has(key)) { marks[key] = { lines: rows.length, snapshot: last || done.snapshot }; continue; } // skipped for good
    for (const r of rows.slice(done.lines)) if (after(r.t)) items.push({ item: "decision", project: p, ...anonymize(r, salt) });
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
  updateState((s) => { s.projectDirs = Array.from(new Set([dir, ...(s.projectDirs || [])])).slice(0, 50); }, home);
}

export function autosaveFolders(state) {
  return (state.projectDirs || []).map((d) => join(d, "Adobe Premiere Pro Auto-Save")).filter((d) => existsSync(d));
}

// A few files per round, yielding between them: a big project's autosave
// takes a while to parse, and the bridge must keep answering meanwhile.
export const AUTOSAVES_PER_TICK = 6;
const yieldNow = () => new Promise((r) => setImmediate(r));

export async function importNewAutosaves({ home = HOME, root = JR.JOURNAL_ROOT, limit = AUTOSAVES_PER_TICK } = {}) {
  const s = ensureStarted(home);
  if (!isOn(s)) return { files: 0, events: 0, left: 0, stopped: true };
  let files = 0, events = 0, left = 0;
  for (const folder of autosaveFolders(s)) {
    const seen = s.autosaveSeen?.[folder] || 0;
    const since = s.sharingSince ? Date.parse(s.sharingSince) : 0; // autosaves from before the yes are never read for sharing
    const fresh = readdirSync(folder).filter((f) => f.toLowerCase().endsWith(".prproj")).map((f) => join(folder, f))
      .map((f) => ({ f, m: statSync(f).mtimeMs })).filter((x) => x.m > seen && x.m >= since).sort((a, b) => a.m - b.m);
    for (const { f, m } of fresh) {
      if (files >= limit) { left++; continue; }
      await yieldNow();
      if (!isOn(loadState(home))) return { files, events, stopped: true };
      try {
        const snap = snapshotFromProject(f, { mtime: new Date(m) });
        const r = JR.record(JR.journalDir(snap.project, root), snap, { source: snap.source, file: f });
        files++; events += r.events || 0;
      } catch { files++; }
      updateState((x) => { x.autosaveSeen = { ...(x.autosaveSeen || {}), [folder]: Math.max(x.autosaveSeen?.[folder] || 0, m) }; }, home);
    }
  }
  return { files, events, left };
}

// One round of the autopilot: read new autosaves, send what is new.
export async function tick({ home = HOME, root = JR.JOURNAL_ROOT, fetchImpl, version } = {}) {
  const s = ensureStarted(home);
  if (!isOn(s)) return { skipped: true };
  const imported = await importNewAutosaves({ home, root });
  const sent = await upload({ home, root, fetchImpl, version });
  return { imported, ...sent };
}
