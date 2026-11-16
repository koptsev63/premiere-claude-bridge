// DaVinci Resolve: a synthetic .drp and a synthetic Project.db, built here.
// No real client project, no real Resolve folder: RESOLVE_PREFS_DIR and
// PREMIERE_PREFS_DIR point at temporary folders in every test.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync, existsSync, readdirSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import * as Z from "node:zlib";
const { deflateRawSync, gunzipSync, zstdCompressSync } = Z;
const crc32 = Z.crc32 || (() => 0); // the reader does not check it; Node before 22.2 has no zlib.crc32
import * as R from "../lib/drp.js";
import * as S from "../lib/share.js";

const NO_PREFS = mkdtempSync(join(tmpdir(), "no-prefs-"));
process.env.PREMIERE_PREFS_DIR = NO_PREFS;
process.env.RESOLVE_PREFS_DIR = NO_PREFS;

// ---- building a .drp --------------------------------------------------------

const dbl = (v) => { const b = Buffer.alloc(8); b.writeDoubleLE(v); return b.toString("hex"); };
const fr = (fps) => dbl(fps) + "0000000000000000";
const res = (w, h) => { const b = Buffer.alloc(16); b.writeBigUInt64BE(BigInt(w), 0); b.writeBigUInt64BE(BigInt(h), 8); return b.toString("hex"); };
const ext = (start, len) => dbl(start) + dbl(len);
const PLAIN = "02" + "3ff0000000000000"; // 0x02 + a double: a clip at normal speed

// Qt map with KeyframesBA -> {"0": {X, Y}, "1": {X, Y}}, as Resolve writes a retime.
function qstr(s) { const b = Buffer.from(s, "utf16le").swap16(); const h = Buffer.alloc(4); h.writeUInt32BE(b.length); return Buffer.concat([h, b]); }
function qmapBuf(entries) {
  const parts = [Buffer.from([0, 0, 0, 1]), Buffer.alloc(4)];
  parts[1].writeUInt32BE(entries.length);
  for (const [k, t, v] of entries) {
    parts.push(qstr(k));
    const tb = Buffer.alloc(5); tb.writeUInt32BE(t); parts.push(tb);
    if (t === 6) { const d = Buffer.alloc(8); d.writeDoubleBE(v); parts.push(d); }
    else { const l = Buffer.alloc(4); l.writeUInt32BE(v.length); parts.push(l, v); }
  }
  return Buffer.concat(parts);
}
function timemap(points) {
  const kf = qmapBuf(points.map(([x, y], i) => [String(i), 12, qmapBuf([["X", 6, x], ["Y", 6, y]])]));
  return qmapBuf([["KeyframesBA", 12, kf]]).toString("hex");
}

function item(type, { name, start, dur, cin = "", path = "", map = PLAIN, mfps = 24 }) {
  return `<Element><Sm2Ti${type} DbId="${Math.random().toString(16).slice(2)}"><FieldsBlob/><PrettyType/><Name>${name}</Name><Start>${start}</Start><Duration>${dur}</Duration><In>${cin}</In>`
    + (type.endsWith("Clip") ? `<MediaFilePath>${path}</MediaFilePath><MediaFrameRate>${fr(mfps)}</MediaFrameRate><MediaTimemapBA>${map}</MediaTimemapBA>` : "")
    + `<Thumbnail><BtThumnail DbId="t"><Buffer/></BtThumnail></Thumbnail></Sm2Ti${type}></Element>`;
}
function track(seq, items) { return `<Element><Sm2TiTrack DbId="${Math.random().toString(16).slice(2)}"><Type>0</Type><Sequence>${seq}</Sequence><Items>${items.join("")}</Items></Sm2TiTrack></Element>`; }
function container(seq, video, audio = []) {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<Sm2SequenceContainer DbId="c-${seq}"><FieldsBlob/><VideoTrackVec>${video.map((t) => track(seq, t)).join("")}</VideoTrackVec><AudioTrackVec>${audio.map((t) => track(seq, t)).join("")}</AudioTrackVec><SubtitleTrackVec/></Sm2SequenceContainer>`;
}
function mpFolder(timelines) {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<Sm2MpFolder DbId="f"><Name>Master</Name><MediaVec>` + timelines.map((t) => `<Element><Sm2MpTimelineClip DbId="mp-${t.id}"><Name>${t.name}</Name><TimelineSharedHandle><Sm2Timeline DbId="${t.id}"><Name>${t.name}</Name><Sequence><Sm2Sequence DbId="${t.seq}"><MediaExtents>${ext(t.start, 60)}</MediaExtents><FrameRate>${fr(t.fps)}</FrameRate><Resolution>${res(t.w, t.h)}</Resolution></Sm2Sequence></Sequence></Sm2Timeline></TimelineSharedHandle></Sm2MpTimelineClip></Element>`).join("")
    + `<Element><Sm2MpCompoundClip DbId="cc"><Name>Compound Clip 1</Name><Sequence><Sm2Sequence DbId="seq-compound"><FrameRate>${fr(24)}</FrameRate></Sm2Sequence></Sequence></Sm2MpCompoundClip></Element></MediaVec></Sm2MpFolder>`;
}

function zip(files) {
  const locals = [], centrals = [];
  let off = 0;
  for (const [name, text] of Object.entries(files)) {
    const raw = Buffer.from(text), data = deflateRawSync(raw), n = Buffer.from(name);
    const crc = crc32(raw);
    const lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(8, 8); lh.writeUInt32LE(crc, 14); lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(raw.length, 22); lh.writeUInt16LE(n.length, 26);
    const ch = Buffer.alloc(46); ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(8, 10); ch.writeUInt32LE(crc, 16); ch.writeUInt32LE(data.length, 20); ch.writeUInt32LE(raw.length, 24); ch.writeUInt16LE(n.length, 28); ch.writeUInt32LE(off, 42);
    locals.push(lh, n, data); centrals.push(ch, n);
    off += 30 + n.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(centrals.length / 2, 8); end.writeUInt16LE(centrals.length / 2, 10); end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(off, 16);
  return Buffer.concat([...locals, cd, end]);
}

// A cut at 24 fps starting at 01:00:00:00 (86400). `trim` shortens the second shot's head.
function drp({ project = "Клиент Промо", trim = 0 } = {}) {
  const T0 = 86400;
  const v1 = [
    item("VideoClip", { name: "A001_hero.mov", start: T0, dur: 48, cin: "100", path: "/Volumes/Client/A001_hero.mov" }),
    item("VideoClip", { name: "B002_crowd.mov", start: T0 + 48 + trim, dur: 24 - trim, cin: String(10 + trim) + "|" + dbl(0.4), path: "/Volumes/Client/B002_crowd.mov" }),
    // played backwards over a 100-frame source (Xmax 100/24 s): clip frames 0..23 show source 99..76
    item("VideoClip", { name: "C003_orbit.mov", start: T0 + 72, dur: 24, cin: "0", path: "/Volumes/Client/C003_orbit.mov", map: timemap([[0, 100 / 24], [100 / 24, 0]]) }),
  ];
  const v2 = [item("Generator", { name: "Solid Color", start: T0, dur: 24 })];
  const a1 = [item("AudioClip", { name: "music.wav", start: T0, dur: 96, cin: "0", path: "/Volumes/Client/music.wav" })];
  return zip({
    "project.xml": `<?xml version="1.0"?><SM_Project DbId="p"><ProjectName>${project}</ProjectName></SM_Project>`,
    "MediaPool/Master/MpFolder.xml": mpFolder([{ id: "tl-1", seq: "seq-1", name: "Промо v3", fps: 24, w: 1920, h: 1080, start: 3600 }]),
    "SeqContainer/c1.xml": container("seq-1", [v1, v2], [a1]),
    "SeqContainer/c2.xml": container("seq-compound", [[item("VideoClip", { name: "inner.mov", start: 0, dur: 10, cin: "0" })]]),
    "lut/big.cube": "LUT",
  });
}

// ---- readers ----------------------------------------------------------------

test("drp: small readers", () => {
  assert.equal(R.parseIn("5509|80ffffffffffef3f"), 5510);
  assert.equal(R.parseIn("191"), 191);
  assert.equal(R.parseIn(""), null);
  assert.equal(R.doubleLE(fr(23.976)), 23.976);
  assert.deepEqual(R.resolution(res(1080, 1920)), { width: 1080, height: 1920 });
  assert.equal(R.timemapPoints(PLAIN), null);
  assert.deepEqual(R.timemapPoints(timemap([[0, 2], [2, 0]])), [[0, 2], [2, 0]]);
  assert.deepEqual(R.parseDbList("Local Database:/Users/x/Resolve Project Library:*:::DISK\nNet:192.168.0.2:*:u:p:PG"), [{ name: "Local Database", path: "/Users/x/Resolve Project Library" }]);
  assert.equal(R.parseDbList("Local Database:C:\\Users\\x\\Resolve Project Library:*:::DISK")[0].path, "C:\\Users\\x\\Resolve Project Library");
});

test("drp: a .drp reads into the same snapshot shape as a .prproj", () => {
  const dir = mkdtempSync(join(tmpdir(), "drp-"));
  const f = join(dir, "promo.drp");
  writeFileSync(f, drp());
  const s = R.snapshotFromDrp(f, { mtime: new Date("2026-11-15T10:00:00Z") });
  assert.equal(s.project, "Клиент Промо");
  assert.equal(s.source, "resolve-drp");
  assert.equal(s.t, "2026-11-15T10:00:00.000Z");
  assert.equal(s.sequences.length, 1, "a compound clip is not a timeline");
  const q = s.sequences[0];
  assert.equal(q.name, "Промо v3");
  assert.equal(q.fps, 24);
  assert.equal(q.width, 1920);
  assert.equal(q.height, 1080);
  assert.deepEqual(q.video.map((t) => [t.index, t.clipCount]), [[0, 3], [1, 1]]);
  assert.deepEqual(q.audio.map((t) => [t.index, t.clipCount]), [[0, 1]]);
  const [a, b, c] = q.video[0].clips;
  assert.deepEqual([a.start, a.end], [0, 2], "the 01:00:00:00 start is taken off");
  assert.deepEqual([a.inPoint, a.outPoint], [100 / 24, 100 / 24 + 2]);
  assert.equal(a.path, "/Volumes/Client/A001_hero.mov");
  assert.equal(b.inPoint, 10 / 24, "In with a fraction rounds to the frame");
  assert.equal(c.reverse, true);
  assert.ok(Math.abs(c.inPoint - 76 / 24) < 1e-9 && Math.abs(c.outPoint - 100 / 24) < 1e-9, "a reversed clip carries its forward source range");
  assert.equal(q.video[1].clips[0].inPoint, null, "a generator has no source");
  assert.equal(q.end, 4);
});

// ---- Project.db ---------------------------------------------------------------

const SQL = R.sqlite();

function projectDb(file, { project = "Клиент Промо", trim = 0 } = {}) {
  const db = new SQL.DatabaseSync(file);
  db.exec(`CREATE TABLE SM_Project (SM_Project_id TEXT, ProjectName TEXT);
    CREATE TABLE Sm2Timeline (Sm2Timeline_id TEXT, Name TEXT, Sequence TEXT);
    CREATE TABLE Sm2Sequence (Sm2Sequence_id TEXT, FrameRate BLOB, Resolution BLOB, MediaExtents BLOB);
    CREATE TABLE Sm2TiTrack (Sm2TiTrack_id TEXT, Type INTEGER, Sequence TEXT);
    CREATE TABLE Sm2SequenceContainer_Sm2TiTrack (DbOwner TEXT, DbAssociate TEXT, DbPropertyName TEXT, DbIndex INTEGER);
    CREATE TABLE Sm2TiItem (Sm2TiItem_id TEXT, DbType TEXT, Name TEXT, Start TEXT, Duration TEXT, "In" TEXT, MediaFilePath TEXT, MediaTimemapBA BLOB, MediaFrameRate BLOB, Sm2TiTrack_id TEXT);
    CREATE TABLE Sm2TiItem_Sm2TiTrack (DbOwner TEXT, DbAssociate TEXT, DbPropertyName TEXT, DbIndex INTEGER);`);
  const h = (x) => Buffer.from(x, "hex");
  db.prepare("INSERT INTO SM_Project VALUES ('p', ?)").run(project);
  db.prepare("INSERT INTO Sm2Timeline VALUES ('tl-1', 'Промо v3', 'seq-1')").run();
  db.prepare("INSERT INTO Sm2Sequence VALUES ('seq-1', ?, ?, ?)").run(h(fr(24)), h(res(1080, 1920)), h(ext(3600, 60)));
  db.prepare("INSERT INTO Sm2TiTrack VALUES ('v1', 0, 'seq-1'), ('a1', 1, 'seq-1')").run();
  db.prepare("INSERT INTO Sm2SequenceContainer_Sm2TiTrack VALUES ('c', 'v1', 'VideoTrackVec', 0), ('c', 'a1', 'AudioTrackVec', 0)").run();
  const add = db.prepare("INSERT INTO Sm2TiItem VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
  add.run("i1", "Sm2TiVideoClip", "A001_hero.mov", "86400", "48", "100", "/Volumes/Client/A001_hero.mov", h(PLAIN), h(fr(24)), "v1");
  add.run("i2", "Sm2TiVideoClip", "B002_crowd.mov", String(86448 + trim), String(24 - trim), String(10 + trim), "/Volumes/Client/B002_crowd.mov", h(PLAIN), h(fr(24)), "v1");
  add.run("i3", "Sm2TiAudioClip", "music.wav", "86400", "72", "0", "/Volumes/Client/music.wav", h(PLAIN), h(fr(24)), "a1");
  db.close();
}

test("drp: a Project.db reads like the .drp, through a copy", { skip: !SQL && "this Node has no node:sqlite" }, () => {
  const dir = mkdtempSync(join(tmpdir(), "rdb-"));
  const f = join(dir, "Project.db");
  projectDb(f);
  const scratch = join(dir, "scratch");
  const s = R.snapshotFromResolveDb(f, { mtime: new Date("2026-11-15T11:00:00Z"), scratch });
  assert.equal(s.project, "Клиент Промо");
  assert.equal(s.source, "resolve-db");
  const q = s.sequences[0];
  assert.deepEqual([q.name, q.fps, q.width, q.height], ["Промо v3", 24, 1080, 1920]);
  assert.deepEqual(q.video[0].clips.map((c) => [c.start, c.end, c.inPoint]), [[0, 2, 100 / 24], [2, 3, 10 / 24]]);
  assert.equal(q.audio[0].clipCount, 1);
  assert.deepEqual(readdirSync(scratch), [], "the copy is gone");
  writeFileSync(f + "-journal", "");
  assert.throws(() => R.snapshotFromResolveDb(f, { scratch }), (e) => e.retry === true, "a save in progress waits for the next round");
});

// ---- sharing from Resolve -----------------------------------------------------

// Resolve's preferences as Resolve 21 writes them, under the test home.
function resolvePrefs(home, { library, backups = join(home, "backups"), mode = 1, live = true } = {}) {
  const d = join(home, "resolve-prefs");
  mkdirSync(d, { recursive: true });
  process.env.RESOLVE_PREFS_DIR = d;
  writeFileSync(join(d, "config.user.xml"), `<?xml version="1.0" encoding="UTF-8"?>\n<!--DbAppVer="21.0.3.0007" DbPrjVer="17"-->\n<SM_UserPrefs DbId="x">\n <AutoSaveMode>${mode}</AutoSaveMode>\n <AutoSaveDuration>10</AutoSaveDuration>\n <IsProjectLiveSaveEnabled>${live}</IsProjectLiveSaveEnabled>\n <ProjectBackupLocation>${backups}</ProjectBackupLocation>\n</SM_UserPrefs>`);
  writeFileSync(join(d, ".version"), "Resolve.Version=21.0.0\n");
  if (library) writeFileSync(join(d, "dblist.conf"), `Local Database:${library}:*:::DISK\n`);
  mkdirSync(backups, { recursive: true });
  return { prefs: d, backups };
}

const at = (f, iso) => utimesSync(f, new Date(iso), new Date(iso));

test("resolve: an editor who cuts in Resolve is learned from, with nothing named leaving", { skip: !SQL && "this Node has no node:sqlite" }, async () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const lib = join(home, "Resolve Project Library");
  const pdir = join(lib, "Resolve Projects", "Users", "guest", "Projects", "Клиент Промо");
  mkdirSync(pdir, { recursive: true });
  const { backups } = resolvePrefs(home, { library: lib });
  await S.setConsent(true, { home });
  S.updateState((x) => { x.sharingSince = "2026-11-15T09:00:00.000Z"; }, home);

  const old = join(backups, "b-old", "old.drp");
  mkdirSync(join(backups, "b-old"));
  writeFileSync(old, drp({ project: "Старый" }));
  at(old, "2026-11-10T10:00:00Z"); // before the yes: stays home
  writeFileSync(join(backups, "Timeline.20261115100000"), "timeline backup, another format");

  const db = join(pdir, "Project.db");
  projectDb(db);
  at(db, "2026-11-15T10:00:00Z");
  const r1 = await S.importNewAutosaves({ home, root });
  assert.equal(r1.files, 1, "only the project saved after the yes");
  assert.equal(r1.failed, 0);
  assert.deepEqual(readdirSync(root), ["Клиент_Промо.resolve"]);

  projectDb(db + ".tmp", { trim: 6 }); // the editor trims the second shot's head by 6 frames
  const { renameSync } = await import("node:fs");
  renameSync(db + ".tmp", db);
  at(db, "2026-11-15T10:20:00Z");
  const r2 = await S.importNewAutosaves({ home, root });
  assert.equal(r2.files, 1);
  assert.ok(r2.events >= 1, "the trim became a decision");

  const log = [];
  const up = await S.upload({ home, root, fetchImpl: async (url, opts) => { log.push(opts); return { ok: true, status: 200, json: async () => ({}) }; } });
  assert.ok(up.sent >= 2, "decision and shape went");
  const body = gunzipSync(log[0].body).toString();
  for (const secret of ["Клиент", "Промо", "Volumes", "A001", "B002", "Старый", "music"]) assert.ok(!body.includes(secret), "no " + secret + " in what leaves");
  assert.ok(/"type":"trim_head"|"type":"slip"|"type":"trim/.test(body), "the decision type is there");

  const again = await S.importNewAutosaves({ home, root });
  assert.equal(again.files, 0, "the same save is read once");

  const d = S.doctor({ home, root });
  assert.equal(d.resolve.length, 1);
  assert.equal(d.resolve[0].backups, true);
  assert.equal(d.resolve[0].backupFolderFound, true);
  assert.equal(d.resolveProjects, 1);
  assert.equal(d.resolveBackups, 1, "the .drp in the backup folder; the timeline backup is not counted");
  assert.ok(!d.verdict.some((v) => /настройки Premiere/.test(v)), "a Resolve editor is not told Premiere is missing");
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("resolve: a project kept out by name is not even journalled", async () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const { backups } = resolvePrefs(home, {});
  await S.setConsent(true, { home });
  S.updateState((x) => { x.sharingSince = "2026-11-15T09:00:00.000Z"; }, home);
  S.setExcluded("Клиент Промо", true, home);
  const f = join(backups, "promo.drp");
  writeFileSync(f, drp());
  at(f, "2026-11-15T10:00:00Z");
  const r = await S.importNewAutosaves({ home, root });
  assert.equal(r.files, 1);
  assert.equal(readdirSync(root).length, 0, "nothing of it on disk");
  const other = join(backups, "other.drp");
  writeFileSync(other, drp({ project: "Другой" }));
  at(other, "2026-11-15T10:05:00Z");
  await S.importNewAutosaves({ home, root });
  assert.deepEqual(readdirSync(root), ["Другой.resolve"]);
  S.setExcluded("Другой", true, home); // kept out after it was journalled: never sent either
  assert.equal(S.preview({ home, root }).wouldSend, 0);
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("resolve: doctor says when Resolve saves nothing on its own", () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  resolvePrefs(home, { mode: 0, live: false });
  S.updateState((x) => { x.consent = true; }, home);
  const d = S.doctor({ home, root: mkdtempSync(join(tmpdir(), "share-journal-")) });
  assert.equal(d.resolve[0].backups, false);
  assert.equal(d.resolve[0].liveSave, false);
  assert.ok(d.verdict.some((v) => /Live Save.*Project backups/.test(v)));
  assert.ok(d.verdict.some((v) => /ни одного его проекта/.test(v)));
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("resolve: RESOLVE_PREFS_DIR alone decides, and no default folder is added", () => {
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
  assert.deepEqual(R.resolvePrefsDirs(), [NO_PREFS]);
  const src = R.resolveSources();
  assert.deepEqual([src.configs.length, src.projects.length, src.backups.length], [0, 0, 0]);
  assert.ok(!existsSync(join(NO_PREFS, "config.user.xml")));
});

// ---- found on real Resolve 21 projects (review, 16.11.2026) -------------------

// KeyframesBA as Resolve 21 writes it: protobuf (field 1 = keyframe {1: X, 2: Y}),
// behind 0x80, or 0x81 and a zstd frame.
function pbKeyframes(points, zstd) {
  const dblField = (f, v) => { const b = Buffer.alloc(9); b[0] = (f << 3) | 1; b.writeDoubleLE(v, 1); return b; };
  const msgs = points.map(([x, y]) => {
    const inner = Buffer.concat([x ? dblField(1, x) : Buffer.alloc(0), y ? dblField(2, y) : Buffer.alloc(0), Buffer.from([0x1a, 0x02, 0x00, 0x00])]);
    return Buffer.concat([Buffer.from([0x0a, inner.length]), inner]);
  });
  const body = Buffer.concat(msgs);
  return zstd ? Buffer.concat([Buffer.from([0x81]), zstdCompressSync(body)]) : Buffer.concat([Buffer.from([0x80]), body]);
}
const r21map = (points, zstd) => qmapBuf([["YMin", 6, -1], ["KeyframesBA", 12, pbKeyframes(points, zstd)]]).toString("hex");

test("resolve 21: keyframes in protobuf, plain or zstd, are read", { skip: !zstdCompressSync && "this Node has no zstd" }, () => {
  assert.deepEqual(R.timemapPoints(r21map([[0, 0], [60000, 0]], false)), [[0, 0], [60000, 0]]);
  assert.deepEqual(R.timemapPoints(r21map([[0, 85.8], [85.88, 0]], true)), [[0, 85.8], [85.88, 0]]);
  // the reversed shot from the October grading pull: 25 fps, In 191, 72 frames, source 1884-1955
  const r = R.sourceRange(191, 72, { fps: 25, mediaFps: 25, points: R.timemapPoints(r21map([[0, 85.8], [85.88, 0]], true)) });
  assert.equal(r.reverse, true);
  assert.ok(Math.abs(r.inPoint * 25 - 1884) < 1e-6 && Math.abs(r.outPoint * 25 - 1956) < 1e-6);
  assert.deepEqual(R.sourceRange(-2147, 72, { fps: 25, points: null }), { inPoint: null, outPoint: null }, "a negative In gives no range, not a negative one");
  assert.deepEqual(R.sourceRange(10, 24, { fps: 24, points: undefined }), { inPoint: null, outPoint: null }, "an unreadable retime gives no range");
  assert.equal(R.timemapPoints("02" + "00".repeat(40)), null, "0x02 + doubles is not a retime");
});

test("resolve 21: no In, a fractional Duration and a 0x0 timeline still read right", () => {
  const model = { project: "p", timelines: [{ id: "t", name: "T", fps: 24, extStart: 0, tracks: [
    { kind: "video", index: 0, items: [{ type: "VideoClip", name: "a.mov", start: "0", duration: "48", in: null, path: "/x/a.mov", timemap: PLAIN }] },
    { kind: "audio", index: 0, items: [{ type: "AudioClip", name: "m.wav", start: "0", duration: "3766|" + dbl(0.76), in: "", path: "/x/m.wav", timemap: PLAIN }] },
  ] }] };
  const q = R.snapshotFromModel(model).sequences[0];
  assert.deepEqual([q.video[0].clips[0].inPoint, q.video[0].clips[0].outPoint], [0, 2], "a clip from the first frame has In 0, not none");
  assert.equal(q.audio[0].clipCount, 1, "a fractional Duration is not a dropped clip");
  assert.ok(Math.abs(q.audio[0].clips[0].end - 3766.76 / 24) < 1e-9);
  assert.deepEqual(R.resolution("00".repeat(16)), {}, "0x0 = project settings, unknown");
});

test("resolve: work done while a project was kept out never goes after it is let back in", async () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const { backups } = resolvePrefs(home, {});
  await S.setConsent(true, { home });
  S.updateState((x) => { x.sharingSince = "2026-11-15T09:00:00.000Z"; }, home);
  const now = Date.now();
  const save = (name, trim, ms) => { const f = join(backups, name); writeFileSync(f, drp({ trim })); at(f, new Date(ms).toISOString()); };
  save("s1.drp", 0, now - 3600e3);
  await S.importNewAutosaves({ home, root });
  await S.upload({ home, root, fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
  S.setExcluded("Клиент Промо", true, home);
  save("s2.drp", 6, now - 60e3); // NDA work: a trim, done with Claude closed, so no round saw it while it was out
  S.setExcluded("Клиент Промо", false, home);
  save("s3.drp", 6, now + 60e3); // let back in, nothing new done
  const r = await S.importNewAutosaves({ home, root });
  assert.equal(r.events, 0, "the trim done while out is not a decision");
  assert.ok(!S.preview({ home, root }).sample.some((i) => i.item === "decision"));
  save("s4.drp", 10, now + 120e3); // new work after it is back: goes
  const r4 = await S.importNewAutosaves({ home, root });
  assert.ok(r4.events >= 1);
  assert.ok(S.preview({ home, root }).sample.some((i) => i.item === "decision"));
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("resolve: a save read out of order is kept without a diff, so nothing counts twice", async () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const { backups } = resolvePrefs(home, {});
  await S.setConsent(true, { home });
  S.updateState((x) => { x.sharingSince = "2026-11-15T09:00:00.000Z"; }, home);
  const save = (name, trim, iso) => { const f = join(backups, name); writeFileSync(f, drp({ trim })); at(f, iso); };
  save("a.drp", 0, "2026-11-15T10:00:00Z");
  save("c.drp", 10, "2026-11-15T12:00:00Z");
  const r1 = await S.importNewAutosaves({ home, root });
  save("b.drp", 6, "2026-11-15T11:00:00Z"); // an older save that turns up late
  S.updateState((x) => { delete x.autosaveSeen[join(backups, "b.drp")]; }, home);
  utimesSync(join(backups, "b.drp"), new Date("2026-11-15T12:30:00Z"), new Date("2026-11-15T11:00:00Z"));
  const r2 = await S.importNewAutosaves({ home, root, resolveDirs: undefined });
  assert.ok(r1.events >= 1);
  assert.equal(r2.events, 0);
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("anonymize: only a real media extension goes, never the tail of a name", () => {
  assert.equal(S.anonymize({ name: "A001.MOV" }, "s").ext, "mov");
  assert.equal(S.anonymize({ name: "Mr.Smith" }, "s").ext, undefined);
  assert.equal(S.anonymize({ name: "Promo v2.final" }, "s").ext, undefined);
});

test("resolve: a backup with no project name is not read, and a reverse at another speed is a speed change", () => {
  assert.equal(R.sourceRange(0, 24, { fps: 24, mediaFps: 24, points: [[0, 10], [5, 0]] }).reverse, true);
  const r = R.sourceRange(0, 24, { fps: 24, mediaFps: 24, points: [[0, 10], [5, 0]] });
  assert.ok(Math.abs((r.outPoint - r.inPoint) - 2) < 1e-9, "1 s of timeline at -200% covers 2 s of source");
});

test("resolve: a long project name keeps its journal suffix, and its exclusion holds", async () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const { backups } = resolvePrefs(home, {});
  await S.setConsent(true, { home });
  S.updateState((x) => { x.sharingSince = "2026-11-15T09:00:00.000Z"; }, home);
  const long = "Очень длинное название проекта клиента под NDA для промо ролика выставки 2026 версия финальная";
  const f = join(backups, "long.drp");
  writeFileSync(f, drp({ project: long }));
  at(f, "2026-11-15T10:00:00Z");
  await S.importNewAutosaves({ home, root });
  const [dir] = readdirSync(root);
  assert.ok(dir.endsWith(".resolve"));
  assert.ok(S.preview({ home, root }).wouldSend > 0);
  S.setExcluded(long, true, home);
  assert.equal(S.preview({ home, root }).wouldSend, 0, "journalled before the exclusion, still kept out");
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("resolve: backups Resolve has deleted are forgotten", async () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const { backups } = resolvePrefs(home, {});
  await S.setConsent(true, { home });
  const f = join(backups, "gone.drp");
  writeFileSync(f, drp());
  at(f, new Date(Date.now() + 60000).toISOString());
  await S.importNewAutosaves({ home, root });
  assert.ok(S.loadState(home).autosaveSeen[f]);
  unlinkSync(f);
  await S.importNewAutosaves({ home, root });
  assert.equal(S.loadState(home).autosaveSeen[f], undefined);
  process.env.RESOLVE_PREFS_DIR = NO_PREFS;
});

test("resolve: a backup Project.db with no project name is refused, a library one is named by its folder", { skip: !SQL && "this Node has no node:sqlite" }, () => {
  const dir = mkdtempSync(join(tmpdir(), "rdb-"));
  const backup = join(dir, "Project.db");
  projectDb(backup, { project: "" });
  assert.throws(() => R.snapshotFromResolveDb(backup, { source: "resolve-backup", scratch: join(dir, "s") }), /нет имени проекта/);
  const lib = join(dir, "Projects", "Клиент Промо");
  mkdirSync(lib, { recursive: true });
  projectDb(join(lib, "Project.db"), { project: "" });
  assert.equal(R.snapshotFromResolveDb(join(lib, "Project.db"), { scratch: join(dir, "s") }).project, "Клиент Промо");
});
