// DaVinci Resolve: read a project into the same snapshot shape as a saved
// .prproj (lib/prproj.js snapshotFromProject), so the journal, the diff and
// sharing work for an editor who cuts in Resolve exactly as for Premiere.
//
// Two ways in, one model:
//   .drp       a zip: project.xml (SM_Project, ProjectName), MediaPool/**/
//              MpFolder.xml (Sm2MpTimelineClip > Sm2Timeline{Name} > Sm2Sequence
//              {FrameRate, Resolution, MediaExtents}) and SeqContainer/*.xml
//              (Sm2SequenceContainer > VideoTrackVec|AudioTrackVec > Sm2TiTrack
//              {Sequence} > Items > Sm2TiVideoClip|Sm2TiAudioClip|Sm2TiGenerator...).
//   Project.db the project library itself (Resolve 18+ keeps every project as
//              a SQLite file, Resolve Projects/Users/<user>/Projects/<name>/Project.db)
//              and the "Project backups" Resolve writes, which are copies of it
//              (Resolve 21 binary: "Project.db.%1", "Incremental backup to %s").
//              Same objects as tables: Sm2Timeline, Sm2Sequence, Sm2TiTrack,
//              Sm2SequenceContainer_Sm2TiTrack (track order), Sm2TiItem.
//
// Field notes (a Resolve 20.3 .drp from an editor and Resolve 21.0.3 Project.db
// files on the owner's Mac, 15.11.2026; core/drt_audit.py has the timeline XML):
//   <Start> record frame with the timeline start included (01:00:00:00 at 24 fps
//     = 86400); that start is MediaExtents[0] in seconds (3600) * fps.
//   <Duration> timeline frames. <In> source frame, "int|hexdouble" (a little-endian
//     IEEE double fraction added to the integer part); empty for generators/titles.
//   FrameRate / MediaFrameRate: 16 hex bytes, a little-endian double first.
//   Resolution: two big-endian 64-bit integers, width then height.
//   MediaExtents: two little-endian doubles, start and length in seconds.
//   MediaTimemapBA: 9 bytes (0x02 + double) for a plain clip, else a Qt map whose
//     KeyframesBA holds {X clip seconds, Y source seconds}; a retimed clip's <In>
//     counts clip time, so its source range comes from the keyframes.
// Nothing here writes to Resolve's files: a Project.db is copied first and the
// copy is read, so Resolve never waits on a lock of ours.

import { readFileSync, copyFileSync, unlinkSync, statSync, statfsSync, existsSync, readdirSync, mkdirSync } from "node:fs";
import { inflateRawSync } from "node:zlib";
import { basename, dirname, join } from "node:path";
import { homedir, tmpdir } from "node:os";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";

// ---- small binary readers --------------------------------------------------

const hexBuf = (h) => (typeof h === "string" && h.length && /^[0-9a-f]+$/i.test(h) && h.length % 2 === 0 ? Buffer.from(h, "hex") : null);

// FrameRate, MediaFrameRate, MediaExtents: little-endian doubles.
export function doubleLE(hex, i = 0) {
  const b = hexBuf(hex);
  if (!b || b.length < (i + 1) * 8) return null;
  const v = b.readDoubleLE(i * 8);
  return Number.isFinite(v) ? v : null;
}

// Resolution: width and height as big-endian 64-bit integers.
export function resolution(hex) {
  const b = hexBuf(hex);
  if (!b || b.length < 16) return {};
  return { width: Number(b.readBigUInt64BE(0)), height: Number(b.readBigUInt64BE(8)) };
}

// "5509|80ffffffffffef3f" -> 5510 (frames). "191" -> 191. "" -> null.
export function parseIn(raw) {
  if (raw == null || raw === "") return null;
  const [ip, hx] = String(raw).split("|");
  let v = Number(ip);
  if (!Number.isFinite(v)) return null;
  if (hx && hx.length === 16) { const f = hexBuf(hx); if (f) { const d = f.readDoubleLE(0); if (Number.isFinite(d)) v += d; } }
  return Math.round(v);
}

// A Qt-serialised QVariantMap as Resolve writes it (port of core/drt_audit.py _qmap).
export function qmap(b) {
  let o = 4;
  const n = b.readUInt32BE(o); o += 4;
  const d = {};
  for (let i = 0; i < n; i++) {
    const kl = b.readUInt32BE(o); o += 4;
    const k = Buffer.from(b.subarray(o, o + kl)).swap16().toString("utf16le"); o += kl;
    const t = b.readUInt32BE(o); o += 5; // type + null flag
    let v;
    if (t === 6) { v = b.readDoubleBE(o); o += 8; }
    else if (t === 2) { v = b.readInt32BE(o); o += 4; }
    else if (t === 1) { v = b[o]; o += 1; }
    else if (t === 10 || t === 12) {
      const ln = b.readUInt32BE(o); o += 4;
      const raw = ln === 0xffffffff ? Buffer.alloc(0) : Buffer.from(b.subarray(o, o + ln));
      if (ln !== 0xffffffff) o += ln;
      v = t === 10 ? raw : raw.length >= 8 && raw.readUInt32BE(0) === 1 ? qmap(raw) : raw;
    } else throw new Error("timemap value type " + t);
    d[k] = v;
  }
  return d;
}

// MediaTimemapBA -> sorted [[X clip s, Y source s]]; null for a plain clip.
export function timemapPoints(hex) {
  if (!hex || hex.length <= 18) return null;
  const b = hexBuf(hex);
  if (!b) return null;
  let kf;
  try { kf = qmap(b).KeyframesBA; } catch { return null; }
  if (!kf || typeof kf !== "object" || Buffer.isBuffer(kf)) return null;
  const pts = Object.values(kf).filter((p) => p && typeof p === "object" && typeof p.X === "number" && typeof p.Y === "number").map((p) => [p.X, p.Y]);
  pts.sort((a, b) => a[0] - b[0]);
  return pts.length ? pts : null;
}

function interp(pts, x) {
  if (pts.length === 1) return pts[0][1];
  let i = 1;
  while (i < pts.length - 1 && pts[i][0] < x) i++;
  const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
  return x1 === x0 ? y1 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
}

// Source range of one clip in seconds. inFrames counts clip time on a retimed clip.
export function sourceRange(inFrames, durFrames, { fps, mediaFps, points } = {}) {
  if (inFrames == null) return { inPoint: null, outPoint: null };
  const mfps = mediaFps > 0 ? mediaFps : fps;
  const x0 = inFrames / mfps, len = durFrames / fps;
  const identity = !points || points.every(([, y]) => y === 0) || (points.length === 2 && points[0][0] === 0 && points[0][1] === 0 && Math.abs(points[1][1] - points[1][0]) < 1e-6);
  if (identity) return { inPoint: x0, outPoint: x0 + len };
  const y0 = interp(points, x0), y1 = interp(points, x0 + len);
  return { inPoint: Math.min(y0, y1), outPoint: Math.max(y0, y1), reverse: y1 < y0 || undefined };
}

// ---- the model -> snapshot -------------------------------------------------
// model: { project, timelines: [{ id, name, fps, width, height, extStart,
//   tracks: [{ kind, index, items: [{ type, name, start, duration, in, path, timemap, mediaFps }] }] }] }

export function snapshotFromModel(model, { t, source, file } = {}) {
  const sequences = [];
  for (const tl of model.timelines) {
    const fps = tl.fps > 0 ? tl.fps : 25;
    const offset = Math.round((tl.extStart || 0) * fps);
    const all = tl.tracks.flatMap((tr) => tr.items);
    const base = all.length && all.every((it) => Number(it.start) >= offset) ? offset : 0; // never push a clip before zero
    const seq = { name: tl.name || "", sequenceID: tl.id, width: tl.width, height: tl.height, fps, video: [], audio: [], markers: null, end: 0 };
    for (const tr of tl.tracks) {
      const clips = [];
      for (const it of tr.items) {
        const s = Number(it.start), d = Number(it.duration);
        if (it.start == null || it.duration == null || !Number.isFinite(s) || !Number.isFinite(d)) continue;
        const start = (s - base) / fps, end = start + d / fps;
        const r = sourceRange(parseIn(it.in), d, { fps, mediaFps: it.mediaFps, points: timemapPoints(it.timemap) });
        const row = { name: it.name || basename(it.path || "") || "", start, end, duration: end - start, inPoint: r.inPoint, outPoint: r.outPoint, path: it.path || "" };
        if (r.reverse) row.reverse = true;
        clips.push(row);
        if (end > seq.end) seq.end = end;
      }
      clips.sort((a, b) => a.start - b.start);
      seq[tr.kind].push({ kind: tr.kind, index: tr.index, name: "", clipCount: clips.length, clips });
    }
    for (const k of ["video", "audio"]) seq[k].sort((a, b) => a.index - b.index);
    sequences.push(seq);
  }
  sequences.sort((a, b) => a.name.localeCompare(b.name));
  return { t: (t || new Date()).toISOString(), source, file, project: model.project, sequences };
}

// ---- .drp ------------------------------------------------------------------

// Just enough of a zip reader for a .drp: central directory, stored or deflated.
export function readZip(path, want = () => true) {
  const buf = readFileSync(path);
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error("это не .drp: внутри нет каталога zip");
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const out = new Map();
  for (let i = 0; i < count; i++) {
    if (p + 46 > buf.length || buf.readUInt32LE(p) !== 0x02014b50) throw new Error(".drp повреждён: каталог zip не читается");
    const method = buf.readUInt16LE(p + 10), csize = buf.readUInt32LE(p + 20), nlen = buf.readUInt16LE(p + 28), xlen = buf.readUInt16LE(p + 30), clen = buf.readUInt16LE(p + 32), local = buf.readUInt32LE(p + 42);
    const name = buf.subarray(p + 46, p + 46 + nlen).toString("utf8");
    p += 46 + nlen + xlen + clen;
    if (!want(name)) continue;
    const lnl = buf.readUInt16LE(local + 26), lxl = buf.readUInt16LE(local + 28);
    const data = buf.subarray(local + 30 + lnl + lxl, local + 30 + lnl + lxl + csize);
    if (method === 0) out.set(name, data.toString("utf8"));
    else if (method === 8) out.set(name, inflateRawSync(data).toString("utf8"));
  }
  return out;
}

const unxml = (s) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");
const tag = (body, name) => { const m = new RegExp(`<${name}>([^<]*)</${name}>`).exec(body); return m ? unxml(m[1]) : null; };

function section(xml, name) {
  const a = xml.indexOf(`<${name}>`), b = xml.indexOf(`</${name}>`, a);
  return a < 0 || b < 0 ? "" : xml.slice(a, b);
}

function tracksOfSection(body, kind) {
  const out = [];
  body.split(/<Sm2TiTrack DbId="[^"]+">/).slice(1).forEach((tb, index) => {
    const items = [];
    const re = /<Sm2Ti(?!Track)(\w+) DbId="[^"]+">([\s\S]*?)<\/Sm2Ti\1>/g;
    const list = section(tb, "Items");
    let m;
    while ((m = re.exec(list))) {
      const b = m[2];
      items.push({ type: m[1], name: tag(b, "Name"), start: tag(b, "Start"), duration: tag(b, "Duration"), in: tag(b, "In"), path: tag(b, "MediaFilePath") || "", timemap: tag(b, "MediaTimemapBA"), mediaFps: doubleLE(tag(b, "MediaFrameRate")) });
    }
    out.push({ kind, index, seqId: tag(tb, "Sequence"), items });
  });
  return out;
}

export function readDrpModel(path) {
  const files = readZip(path, (n) => n === "project.xml" || (/^(MediaPool|SeqContainer)\//.test(n) && n.endsWith(".xml")));
  const project = tag(files.get("project.xml") || "", "ProjectName") || basename(path).replace(/\.drp$/i, "");
  // timelines: Sm2Timeline{Name} with its Sm2Sequence (a compound clip has a sequence but no timeline: skipped)
  const timelines = new Map();
  for (const [n, x] of files) {
    if (!n.startsWith("MediaPool/")) continue;
    const re = /<Sm2Timeline DbId="([^"]+)">([\s\S]*?)<\/Sm2Timeline>/g;
    let m;
    while ((m = re.exec(x))) {
      const body = m[2];
      const seq = /<Sm2Sequence DbId="([^"]+)">([\s\S]*?)<\/Sm2Sequence>/.exec(body);
      if (!seq) continue;
      timelines.set(seq[1], { id: m[1], name: tag(body.slice(0, seq.index), "Name") || "", fps: doubleLE(tag(seq[2], "FrameRate")), ...resolution(tag(seq[2], "Resolution")), extStart: doubleLE(tag(seq[2], "MediaExtents"), 0) || 0, tracks: [] });
    }
  }
  for (const [n, x] of files) {
    if (!n.startsWith("SeqContainer/")) continue;
    const tracks = [...tracksOfSection(section(x, "VideoTrackVec"), "video"), ...tracksOfSection(section(x, "AudioTrackVec"), "audio")];
    const seqId = tracks.find((t) => t.seqId)?.seqId;
    const tl = seqId && timelines.get(seqId);
    if (tl) tl.tracks.push(...tracks);
  }
  return { project, timelines: [...timelines.values()] };
}

export function snapshotFromDrp(path, { mtime } = {}) {
  return snapshotFromModel(readDrpModel(path), { t: mtime, source: "resolve-drp", file: path });
}

// ---- Project.db ------------------------------------------------------------

let sqliteMod;
export function sqlite() {
  if (sqliteMod !== undefined) return sqliteMod;
  try { sqliteMod = process.getBuiltinModule?.("node:sqlite") || createRequire(import.meta.url)("node:sqlite"); }
  catch { sqliteMod = null; }
  return sqliteMod;
}

function dbModel(db, fallbackName) {
  const all = (sql, ...p) => db.prepare(sql).all(...p);
  let project = fallbackName;
  try { project = all("SELECT ProjectName FROM SM_Project LIMIT 1")[0]?.ProjectName || fallbackName; } catch {}
  const timelines = [];
  for (const tl of all("SELECT t.Sm2Timeline_id AS id, t.Name AS name, s.Sm2Sequence_id AS seqId, hex(s.FrameRate) AS fr, hex(s.Resolution) AS res, hex(s.MediaExtents) AS ext FROM Sm2Timeline t JOIN Sm2Sequence s ON s.Sm2Sequence_id = t.Sequence")) {
    const model = { id: tl.id, name: tl.name || "", fps: doubleLE(tl.fr), ...resolution(tl.res), extStart: doubleLE(tl.ext, 0) || 0, tracks: [] };
    const tracks = all(`SELECT tr.Sm2TiTrack_id AS id, tr.Type AS type, a.DbPropertyName AS prop, a.DbIndex AS idx
      FROM Sm2TiTrack tr LEFT JOIN Sm2SequenceContainer_Sm2TiTrack a ON a.DbAssociate = tr.Sm2TiTrack_id
      WHERE tr.Sequence = ? ORDER BY tr.Type, a.DbIndex`, tl.seqId);
    const seen = { video: 0, audio: 0 };
    for (const tr of tracks) {
      const kind = tr.prop === "VideoTrackVec" || (!tr.prop && tr.type === 0) ? "video" : tr.prop === "AudioTrackVec" || (!tr.prop && tr.type === 1) ? "audio" : null;
      if (!kind) continue; // subtitles, geometry
      const index = tr.idx != null ? Number(tr.idx) : seen[kind];
      seen[kind]++;
      const items = all(`SELECT i.DbType AS type, i.Name AS name, i.Start AS start, i.Duration AS duration, i."In" AS cin, i.MediaFilePath AS path, hex(i.MediaTimemapBA) AS timemap, hex(i.MediaFrameRate) AS mfr
        FROM Sm2TiItem i WHERE i.Sm2TiItem_id IN (SELECT DbAssociate FROM Sm2TiItem_Sm2TiTrack WHERE DbOwner = ?) OR i.Sm2TiTrack_id = ?`, tr.id, tr.id)
        .map((r) => ({ type: String(r.type || "").replace(/^Sm2Ti/, ""), name: r.name, start: r.start, duration: r.duration, in: r.cin, path: r.path || "", timemap: r.timemap, mediaFps: doubleLE(r.mfr) }));
      model.tracks.push({ kind, index, items });
    }
    timelines.push(model);
  }
  return { project, timelines };
}

// <...>/Projects/<project>/Project.db -> "<project>"; a backup copy is named by what is inside it.
export function projectNameFromPath(path) {
  return /^Project\.db$/i.test(basename(path)) ? basename(dirname(path)) : basename(path);
}

const RESERVE = 2 * 1024 ** 3; // never fill the editor's disk with our copy
const gb = (n) => (n / 1024 ** 3).toFixed(1);

// A copy left by a bridge that died mid-read: gone after an hour.
function sweep(dir) {
  let names = [];
  try { names = readdirSync(dir); } catch { return; }
  for (const n of names) {
    if (!/^resolve-read-[0-9a-f]{8}\.db$/.test(n)) continue;
    try { if (Date.now() - statSync(join(dir, n)).mtimeMs > 3600e3) unlinkSync(join(dir, n)); } catch {}
  }
}

// Copy, then read the copy: Resolve keeps writing its own file and must never
// wait on us. A rollback journal next to it means a save is half done: try later.
export function readResolveDbModel(path, { scratch = tmpdir() } = {}) {
  const mod = sqlite();
  if (!mod) throw new Error("эта версия Node не читает базу Resolve (нужен Node 22.5 или новее)");
  const busy = () => Object.assign(new Error("Resolve как раз сохраняет проект, прочитаю в следующий раз"), { retry: true, quiet: true });
  if (existsSync(path + "-journal") || existsSync(path + "-wal")) throw busy();
  const st = statSync(path), before = st.mtimeMs;
  mkdirSync(scratch, { recursive: true });
  sweep(scratch);
  let free = Infinity;
  try { const fs = statfsSync(scratch); free = Number(fs.bavail) * Number(fs.bsize); } catch {}
  if (free < st.size + RESERVE) throw Object.assign(new Error(`мало места на диске для копии проекта (${gb(st.size)} ГБ, свободно ${gb(free)} ГБ, нужен запас ${gb(RESERVE)} ГБ): прочитаю, когда место освободится`), { retry: true });
  const copy = join(scratch, "resolve-read-" + randomUUID().slice(0, 8) + ".db");
  copyFileSync(path, copy);
  try {
    if (statSync(path).mtimeMs !== before) throw busy();
    const db = new mod.DatabaseSync(copy, { readOnly: true });
    try { return dbModel(db, projectNameFromPath(path)); } finally { db.close(); }
  } finally { try { unlinkSync(copy); } catch {} }
}

export function snapshotFromResolveDb(path, { mtime, scratch, source = "resolve-db" } = {}) {
  return snapshotFromModel(readResolveDbModel(path, { scratch }), { t: mtime, source, file: path });
}

// One entry point for whatever Resolve wrote.
export function snapshotFromResolve(path, opts = {}) {
  return /\.drp$/i.test(path) ? snapshotFromDrp(path, opts) : snapshotFromResolveDb(path, opts);
}

// ---- where Resolve keeps things --------------------------------------------
// Checked on the owner's Mac (Resolve 21.0.3, 15.11.2026) and, for Windows, in
// Blackmagic forum answers (not on a Windows machine):
//   preferences      macOS ~/Library/Preferences/Blackmagic Design/DaVinci Resolve
//                    Windows %APPDATA%\Blackmagic Design\DaVinci Resolve\Preferences
//   config.user.xml  AutoSaveMode (project backups, 0 = off), AutoSaveDuration
//                    (minutes), IsProjectLiveSaveEnabled, ProjectBackupLocation (empty = default)
//   dblist.conf      "<name>:<library path>:*:::DISK", one line per project library
//   default backups  macOS ~/Movies/Resolve Project Backups (Windows: Videos, same
//                    rule, unchecked); "Timeline.<stamp>" files there are timeline
//                    backups in another format and are not read
//   default library  macOS ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Resolve Project Library
//                    Windows %APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Resolve Project Library
//                    (older installs: "Resolve Disk Database")
// RESOLVE_PREFS_DIR overrides the preferences folder and switches every default
// off (tests point it at a temporary folder, never at the real one).

export const BACKUP_DIR_NAME = "Resolve Project Backups";

export function resolvePrefsDirs() {
  if (process.env.RESOLVE_PREFS_DIR) return [process.env.RESOLVE_PREFS_DIR];
  const dirs = [join(homedir(), "Library", "Preferences", "Blackmagic Design", "DaVinci Resolve")];
  if (process.env.APPDATA) dirs.push(join(process.env.APPDATA, "Blackmagic Design", "DaVinci Resolve", "Preferences"));
  return dirs;
}

function defaultLibraries(prefsDir) {
  if (process.env.RESOLVE_PREFS_DIR) return [];
  const win = process.env.APPDATA && prefsDir.startsWith(process.env.APPDATA);
  const support = win ? join(dirname(prefsDir), "Support") : join(homedir(), "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve");
  return [join(support, "Resolve Project Library"), join(support, "Resolve Disk Database")];
}

function defaultBackupDir() {
  if (process.env.RESOLVE_PREFS_DIR) return null;
  return join(homedir(), process.platform === "win32" ? "Videos" : "Movies", BACKUP_DIR_NAME);
}

export function parseDbList(text) {
  const libs = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const parts = line.trim().split(":");
    if (parts.length < 6 || !/DISK/i.test(parts[parts.length - 1])) continue; // a PostgreSQL library has no files to read
    const p = parts.slice(1, -4).join(":");
    if (p) libs.push({ name: parts[0], path: p });
  }
  return libs;
}

export function resolveConfigs(dirs = resolvePrefsDirs()) {
  const out = [];
  out.errors = [];
  for (const dir of [].concat(dirs)) {
    let xml;
    try { xml = readFileSync(join(dir, "config.user.xml"), "utf8"); }
    catch (e) { if (e && e.code !== "ENOENT" && e.code !== "ENOTDIR") out.errors.push(`${e.code || "error"}: ${dir}`); continue; }
    const val = (k) => tag(xml, k) ?? (new RegExp(`<${k}/>`).test(xml) ? "" : null);
    let version = null;
    try { version = /Resolve\.Version=(\S+)/.exec(readFileSync(join(dir, ".version"), "utf8"))?.[1] || null; } catch {}
    if (!version) version = /DbAppVer="([^"]+)"/.exec(xml)?.[1] || null;
    let libraries = [];
    try { libraries = parseDbList(readFileSync(join(dir, "dblist.conf"), "utf8")).map((l) => l.path); } catch {}
    if (!libraries.length) libraries = defaultLibraries(dir);
    const mode = val("AutoSaveMode");
    out.push({
      prefsDir: dir, version, libraries,
      backups: mode == null ? null : mode !== "0",
      backupEvery: Number(val("AutoSaveDuration")) || null,
      liveSave: val("IsProjectLiveSaveEnabled") === "true",
      backupLocation: val("ProjectBackupLocation") || defaultBackupDir(),
    });
  }
  return out;
}

const isDir = (d) => { try { return statSync(d).isDirectory(); } catch { return false; } };

// Every file under dir (to depth) whose name passes keep; a budget keeps a huge tree from stalling a round.
function walk(dir, depth, keep, out = [], budget = { n: 5000 }) {
  let names = [];
  try { names = readdirSync(dir, { withFileTypes: true }); } catch { return out; }
  for (const e of names) {
    if (--budget.n < 0) break;
    const p = join(dir, e.name);
    if (e.isDirectory()) { if (depth > 0) walk(p, depth - 1, keep, out, budget); }
    else if (e.isFile() && keep(e.name)) out.push(p);
  }
  return out;
}

const isProjectDb = (n) => /^Project\.db$/i.test(n);
export const isBackupFile = (n) => /\.drp$/i.test(n) || (/^Project\.db(\.[^.]+)?$/i.test(n) && !/-(journal|wal|shm)$/i.test(n));

// Projects in the libraries and backups in the backup folder.
export function resolveSources(dirs = resolvePrefsDirs()) {
  const configs = resolveConfigs(dirs);
  const projects = new Set(), backups = new Set(), folders = new Set();
  for (const c of configs) {
    for (const lib of c.libraries) {
      const users = join(lib, "Resolve Projects", "Users");
      let names = [];
      try { names = readdirSync(users); } catch { continue; }
      for (const u of names) {
        const pdir = join(users, u, "Projects");
        if (isDir(pdir)) for (const f of walk(pdir, 4, isProjectDb)) projects.add(f);
      }
    }
    if (c.backupLocation && isDir(c.backupLocation)) {
      folders.add(c.backupLocation);
      for (const f of walk(c.backupLocation, 3, isBackupFile)) backups.add(f);
    }
  }
  return { configs, errors: configs.errors, projects: [...projects], backups: [...backups], backupFolders: [...folders], canReadDb: !!sqlite() };
}
