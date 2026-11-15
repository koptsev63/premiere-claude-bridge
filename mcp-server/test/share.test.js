import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, utimesSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { gunzipSync } from "node:zlib";
import * as S from "../lib/share.js";

// Never the real Premiere preferences: every test points at a temporary folder.
const NO_PREFS = mkdtempSync(join(tmpdir(), "no-prefs-"));
process.env.PREMIERE_PREFS_DIR = NO_PREFS;
// Nor the real DaVinci Resolve: its preferences, library and backups stay out of every test.
process.env.RESOLVE_PREFS_DIR = NO_PREFS;

function fixture() {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const d = join(root, "Client_Film");
  mkdirSync(join(d, "snapshots"), { recursive: true });
  const ev = [
    { t: "2026-11-02T10:00:00.000Z", from: "2026-11-02T09:50:00.000Z", sequence: "Иванов интервью", sequenceID: "abc", type: "trim_head", kind: "video", track: 0, name: "Ivanov_take3.MOV", frames: 4, file: "/Users/denis/Clients/Ivanov/x.prproj" },
    { t: "2026-11-02T10:05:00.000Z", sequence: "Иванов интервью", type: "markers", kind: "sequence", from: [{ name: "секрет", comment: "NDA", t: 1 }], to: [] },
  ];
  writeFileSync(join(d, "events.jsonl"), ev.map((e) => JSON.stringify(e)).join("\n") + "\n");
  writeFileSync(join(d, "snapshots", "20261102T100000Z-aaaa.json"), JSON.stringify({ t: "2026-11-02T10:00:00.000Z", project: "Client Film.prproj", sequences: [{ name: "Иванов интервью", sequenceID: "abc", fps: 25, width: 1920, height: 1080, end: 10, video: [{ index: 0, clips: [{ name: "Ivanov_take3.MOV", start: 0, end: 4, path: "/Users/denis/a.MOV" }, { name: "b.MOV", start: 4, end: 10 }] }], audio: [] }] }));
  return { home, root };
}

// Consent given before the fixture's work was done (the fixture is dated 02.11 10:00).
async function yesBefore(home, opts = {}) {
  await S.setConsent(true, { home, ...opts });
  S.updateState((x) => { x.sharingSince = "2026-11-01T00:00:00.000Z"; }, home);
}

function fakeFetch(log) {
  return async (url, opts) => { log.push({ url, opts }); return { ok: true, status: 200, json: async () => ({ ok: true, deleted: true }) }; };
}

test("anonymize: numbers and decision types stay, names hashed, paths and comments gone", () => {
  const a = S.anonymize({ type: "trim_head", kind: "video", frames: 4, name: "Ivanov_take3.MOV", sequence: "Иванов", file: "/Users/x", comment: "NDA", t: "2026-11-02T10:00:00.000Z" }, "salt");
  assert.equal(a.type, "trim_head");
  assert.equal(a.kind, "video");
  assert.equal(a.frames, 4);
  assert.equal(a.ext, "mov");
  assert.match(a.name, /^h[0-9a-f]{12}$/);
  assert.match(a.sequence, /^h[0-9a-f]{12}$/);
  assert.equal(a.file, undefined);
  assert.equal(a.comment, undefined);
  assert.equal(a.t, "2026-11-02T10:00:00.000Z");
  assert.notEqual(S.anonymize("x", "salt1"), S.anonymize("x", "salt2"));
});

test("an explicit no: nothing leaves, ever", async () => {
  const { home, root } = fixture();
  const log = [];
  await S.setConsent(false, { home, fetchImpl: fakeFetch(log) });
  const r = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r.sent, 0);
  assert.equal(log.length, 0);
  assert.equal(S.consentStatus(home).on, false);
  assert.equal(S.loadState(home).installId ?? null, null);
});

test("yes: sends anonymized decisions and shapes once, without names, paths or the salt", async () => {
  const { home, root } = fixture();
  const log = [];
  await yesBefore(home, { fetchImpl: fakeFetch(log) });
  const r = await S.upload({ home, root, fetchImpl: fakeFetch(log), version: "0.8.0" });
  assert.equal(r.sent, 3); // 2 decisions + 1 sequence shape
  const body = JSON.parse(gunzipSync(log[0].opts.body).toString());
  const text = JSON.stringify(body);
  for (const secret of ["Ivanov", "Иванов", "/Users", "NDA", "секрет", "Client", S.loadState(home).salt]) assert.ok(!text.includes(secret), `leaked: ${secret}`);
  assert.equal(body.installId, S.loadState(home).installId);
  assert.deepEqual(body.batch.map((b) => b.item), ["decision", "decision", "shape"]);
  assert.equal(body.batch[0].frames, 4);
  const shape = body.batch[2];
  assert.equal(shape.fps, 25);
  const again = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(again.sent, 0);
  assert.equal(log.length, 1);
});

test("no after yes: asks the receiver to delete, forgets id and salt", async () => {
  const { home, root } = fixture();
  const log = [];
  await S.setConsent(true, { home, fetchImpl: fakeFetch(log) });
  const id = S.loadState(home).installId;
  await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  const r = await S.setConsent(false, { home, fetchImpl: fakeFetch(log) });
  assert.equal(r.deleted.deleted, true);
  assert.equal(log[log.length - 1].opts.method, "DELETE");
  assert.ok(log[log.length - 1].url.endsWith(id));
  const st = S.loadState(home);
  assert.equal(st.installId, null);
  assert.equal(st.salt, null);
  assert.equal((await S.upload({ home, root, fetchImpl: fakeFetch(log) })).sent, 0);
});

test("a failed send is kept for the next round", async () => {
  const { home, root } = fixture();
  await yesBefore(home);
  const r = await S.upload({ home, root, fetchImpl: async () => ({ ok: false, status: 503 }) });
  assert.match(r.error, /503/);
  const log = [];
  assert.equal((await S.upload({ home, root, fetchImpl: fakeFetch(log) })).sent, 3);
});

test("preview shows what would go, sends nothing", async () => {
  const { home, root } = fixture();
  await yesBefore(home);
  const p = S.preview({ home, root });
  assert.equal(p.wouldSend, 3);
  assert.ok(!JSON.stringify(p.sample).includes("Ivanov"));
});

test("project folders are remembered for their Auto-Save folders", () => {
  const { home } = fixture();
  const proj = mkdtempSync(join(tmpdir(), "proj-"));
  mkdirSync(join(proj, "Adobe Premiere Pro Auto-Save"));
  S.rememberProject(join(proj, "film.prproj"), home);
  assert.deepEqual(S.autosaveFolders(S.loadState(home)), [join(proj, "Adobe Premiere Pro Auto-Save")]);
});

test("a no during a send wins: nothing recorded as sent, the receiver is told to delete", async () => {
  const { home, root } = fixture();
  await yesBefore(home);
  const id = S.loadState(home).installId;
  const log = [];
  const slowFetch = async (url, opts) => {
    log.push({ url, opts });
    if (opts.method === "POST") await S.setConsent(false, { home, fetchImpl: async () => ({ ok: true, json: async () => ({ deleted: true }) }) });
    return { ok: true, status: 200, json: async () => ({ deleted: true }) };
  };
  const r = await S.upload({ home, root, fetchImpl: slowFetch });
  assert.equal(r.withdrawn, true);
  const st = S.loadState(home);
  assert.equal(st.consent, false);
  assert.equal(st.installId, null);
  assert.ok(log.some((x) => x.opts.method === "DELETE" && x.url.endsWith(id)));
});

test("autosaves are read a few per round, and stop on a no", async () => {
  const { home, root } = fixture();
  await S.setConsent(true, { home });
  const proj = mkdtempSync(join(tmpdir(), "proj-"));
  const auto = join(proj, "Adobe Premiere Pro Auto-Save");
  mkdirSync(auto);
  for (let i = 0; i < 9; i++) writeFileSync(join(auto, `film--x-2026-11-02_10-0${i}-00.prproj`), "not a project");
  S.rememberProject(join(proj, "film.prproj"), home);
  const r1 = await S.importNewAutosaves({ home, root, limit: 6 });
  assert.equal(r1.files, 6);
  assert.equal(r1.left, 3);
  const r2 = await S.importNewAutosaves({ home, root, limit: 6 });
  assert.equal(r2.files, 3);
  await S.setConsent(false, { home, fetchImpl: async () => ({ ok: true, json: async () => ({}) }) });
  const r3 = await S.importNewAutosaves({ home, root });
  assert.equal(r3.files, 0);
});

// ---- 0.8.2 -----------------------------------------------------------------

test("work from before the yes never goes: old journal and old autosaves stay home", async () => {
  const { home, root } = fixture(); // journal dated 02.11 10:00, i.e. before "now"
  const log = [];
  await S.setConsent(true, { home, fetchImpl: fakeFetch(log) });
  assert.ok(S.consentStatus(home).since);
  const r = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r.sent, 0);
  assert.equal(log.length, 0);
  // new work after the yes goes
  const d = join(root, "Client_Film");
  writeFileSync(join(d, "events.jsonl"), readFileSync(join(d, "events.jsonl"), "utf8") + JSON.stringify({ t: new Date(Date.now() + 1000).toISOString(), sequence: "s", type: "move", kind: "video", frames: 2 }) + "\n");
  const r2 = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r2.sent, 1);
  // an autosave written before the yes is not read
  const proj = mkdtempSync(join(tmpdir(), "proj-old-"));
  const auto = join(proj, "Adobe Premiere Pro Auto-Save");
  mkdirSync(auto);
  const old = join(auto, "film--old.prproj");
  writeFileSync(old, "not a project");
  utimesSync(old, new Date("2026-10-01"), new Date("2026-10-01"));
  S.rememberProject(join(proj, "film.prproj"), home);
  const a = await S.importNewAutosaves({ home, root });
  assert.equal(a.files, 0);
});

test("a project kept out never goes, not even its work from while it was out", async () => {
  const { home, root } = fixture();
  const log = [];
  await yesBefore(home, { fetchImpl: fakeFetch(log) });
  const x = S.setExcluded("/Users/denis/Clients/Client Film.prproj", true, home);
  assert.equal(x.project, "Client_Film");
  assert.ok(S.isExcluded("Client Film", home));
  assert.deepEqual(S.consentStatus(home).excluded, ["Client_Film"]);
  assert.equal(S.preview({ home, root }).wouldSend, 0);
  const r = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r.sent, 0);
  assert.equal(log.length, 0);
  S.setExcluded("Client Film.prproj", false, home);
  const r2 = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r2.sent, 0, "work from while it was out stays home");
  const d = join(root, "Client_Film");
  writeFileSync(join(d, "events.jsonl"), readFileSync(join(d, "events.jsonl"), "utf8") + JSON.stringify({ t: new Date(Date.now() + 1000).toISOString(), sequence: "s", type: "move", kind: "video", frames: 2 }) + "\n");
  const r3 = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r3.sent, 1);
  assert.throws(() => S.setExcluded("", true, home), /какой проект/);
});

// ---- 0.8.3: on by default ---------------------------------------------------

test("never chose = on by default from the first run: old work stays home, new work goes", async () => {
  const { home, root } = fixture(); // journal dated before the first run
  const log = [];
  const st0 = S.consentStatus(home);
  assert.equal(st0.on, true);
  assert.equal(st0.byDefault, true);
  assert.equal(S.preview({ home, root }).wouldSend, 0, "preview is honest: the old journal would not go");
  const r = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r.sent, 0);
  assert.equal(log.length, 0);
  const st = S.loadState(home);
  assert.ok(st.installId && st.salt && st.sharingSince);
  assert.equal(st.consent ?? null, null, "still not an explicit choice");
  assert.equal(st.consentVia, "default");
  const d = join(root, "Client_Film");
  writeFileSync(join(d, "events.jsonl"), readFileSync(join(d, "events.jsonl"), "utf8") + JSON.stringify({ t: new Date(Date.now() + 1000).toISOString(), sequence: "s", type: "move", kind: "video", frames: 2 }) + "\n");
  const r2 = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r2.sent, 1);
});

test("unticking a default-on bridge deletes what was sent and stays off", async () => {
  const { home, root } = fixture();
  const log = [];
  S.ensureStarted(home);
  const id = S.loadState(home).installId;
  const r = await S.setConsent(false, { home, fetchImpl: fakeFetch(log) });
  assert.equal(log[log.length - 1].opts.method, "DELETE");
  assert.ok(log[log.length - 1].url.endsWith(id));
  assert.equal(r.status.on, false);
  S.ensureStarted(home);
  assert.equal(S.loadState(home).installId, null, "a no is never undone by the default");
  assert.equal((await S.tick({ home, root, fetchImpl: fakeFetch(log) })).skipped, true);
});

test("a no given in 0.8.1/0.8.2 stays a no after the update; a yes stays a yes", async () => {
  const noHome = mkdtempSync(join(tmpdir(), "share-no-"));
  writeFileSync(join(noHome, "share.json"), JSON.stringify({ consent: false, consentAt: "2026-11-02T12:00:00.000Z", consentVia: "panel", sent: {} }));
  assert.equal(S.consentStatus(noHome).on, false);
  assert.equal(S.ensureStarted(noHome).installId, undefined);
  const yesHome = mkdtempSync(join(tmpdir(), "share-yes-"));
  writeFileSync(join(yesHome, "share.json"), JSON.stringify({ consent: true, installId: "old-id", salt: "old-salt", sharingSince: "2026-11-02T12:00:00.000Z", sent: {} }));
  const y = S.ensureStarted(yesHome);
  assert.equal(y.installId, "old-id");
  assert.equal(y.sharingSince, "2026-11-02T12:00:00.000Z");
  assert.equal(S.consentStatus(yesHome).byDefault, false);
});

test("the notice is remembered once given", () => {
  const home = mkdtempSync(join(tmpdir(), "share-notice-"));
  assert.equal(S.consentStatus(home).noticeGiven, null);
  S.markNoticeGiven(home);
  assert.ok(S.consentStatus(home).noticeGiven);
});

// ---- 0.8.5: the editor cuts with Claude closed -----------------------------

// A two-clip Premiere 26 project; `cut` moves the second clip's end (seconds * 254016000000).
function projectXml(cut = 3) {
  const T = 254016000000;
  return `<?xml version="1.0"?><PremiereData Version="3">
<Sequence ObjectUID="seq-1" ClassID="x" Version="12"><Node/><TrackGroups Version="1">
 <TrackGroup Version="1" Index="0"><First>228cda18-3625-4d2d-951e-348879e4ed93</First><Second ObjectRef="10"/></TrackGroup>
</TrackGroups><Name>Клиент монтаж</Name></Sequence>
<VideoTrackGroup ObjectID="10" ClassID="x" Version="13"><TrackGroup Version="1"><Tracks Version="1"><Track Index="0" ObjectURef="vt-1"/></Tracks><FrameRate>10160640000</FrameRate></TrackGroup><FrameRect>0,0,1920,1080</FrameRect></VideoTrackGroup>
<VideoClipTrack ObjectUID="vt-1" ClassID="x" Version="1"><ClipTrack Version="2"><ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="100"/><TrackItem Index="1" ObjectRef="101"/></TrackItems></ClipItems></ClipTrack></VideoClipTrack>
<VideoClipTrackItem ObjectID="100" ClassID="x" Version="8"><ClipTrackItem Version="8"><TrackItem Version="4"><End>${T}</End></TrackItem><SubClip ObjectRef="200"/></ClipTrackItem></VideoClipTrackItem>
<VideoClipTrackItem ObjectID="101" ClassID="x" Version="8"><ClipTrackItem Version="8"><TrackItem Version="4"><Start>${T}</Start><End>${cut * T}</End></TrackItem><SubClip ObjectRef="201"/></ClipTrackItem></VideoClipTrackItem>
<SubClip ObjectID="200" ClassID="x" Version="6"><Clip ObjectRef="300"/><Name>a.mp4</Name></SubClip>
<SubClip ObjectID="201" ClassID="x" Version="6"><Clip ObjectRef="301"/><Name>b.mp4</Name></SubClip>
<VideoClip ObjectID="300" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="400"/><InPoint>0</InPoint><OutPoint>${T}</OutPoint></Clip></VideoClip>
<VideoClip ObjectID="301" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="401"/><InPoint>0</InPoint><OutPoint>${5 * T}</OutPoint></Clip></VideoClip>
<VideoMediaSource ObjectID="400" ClassID="x" Version="2"><MediaSource Version="4"><Media ObjectURef="m-1"/></MediaSource></VideoMediaSource>
<VideoMediaSource ObjectID="401" ClassID="x" Version="2"><MediaSource Version="4"><Media ObjectURef="m-2"/></MediaSource></VideoMediaSource>
<Media ObjectUID="m-1" ClassID="x" Version="30"><FilePath>/Volumes/Work/a.mp4</FilePath><Title>a.mp4</Title></Media>
<Media ObjectUID="m-2" ClassID="x" Version="30"><FilePath>/Volumes/Work/b.mp4</FilePath><Title>b.mp4</Title></Media>
</PremiereData>`;
}

// Premiere's preferences as Premiere 26 writes them, under the test home.
function premierePrefs(home, mru, { autoSave = true, folder = "SameAsProject" } = {}) {
  process.env.PREMIERE_PREFS_DIR = join(home, "premiere-prefs");
  const d = join(home, "premiere-prefs", "26.0", "Profile-denis");
  mkdirSync(d, { recursive: true });
  const rows = mru.map((f, i) => `<BE.Prefs.MRU.Document.${i}>${f.replace(/&/g, "&amp;")}</BE.Prefs.MRU.Document.${i}>`).join("\n");
  writeFileSync(join(d, "Adobe Premiere Pro Prefs"), `<?xml version="1.0" encoding="UTF-8"?>\n<PremiereData Version="3"><Preferences ObjectID="1" ClassID="x" Version="30"><Properties Version="1">\n<BE.Prefs.AutoSave.DoSave>${autoSave}</BE.Prefs.AutoSave.DoSave>\n<BE.Prefs.ScratchDisks.FirstAutoSaveFolder>${folder}</BE.Prefs.ScratchDisks.FirstAutoSaveFolder>\n${rows}\n</Properties></Preferences></PremiereData>`);
}

test("a project Claude never saw is found from Premiere's recent list and its saves go", async () => {
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const proj = mkdtempSync(join(tmpdir(), "proj-tw-"));
  const file = join(proj, "TW & Fever.prproj");
  await S.setConsent(true, { home });
  S.updateState((x) => { x.sharingSince = "2026-11-01T00:00:00.000Z"; }, home);
  premierePrefs(home, [file, join(proj, "gone.prproj")]);
  assert.equal(S.loadState(home).projectDirs.length, 0, "the live bridge never saw it");
  writeFileSync(file, projectXml(3));
  utimesSync(file, new Date("2026-11-13T10:00:00Z"), new Date("2026-11-13T10:00:00Z"));
  const r1 = await S.importNewAutosaves({ home, root });
  assert.equal(r1.files, 1);
  writeFileSync(file, projectXml(4)); // the editor trims and saves again
  utimesSync(file, new Date("2026-11-13T11:00:00Z"), new Date("2026-11-13T11:00:00Z"));
  const r2 = await S.importNewAutosaves({ home, root });
  assert.equal(r2.files, 1);
  assert.ok(r2.events >= 1, "the trim became a decision");
  const log = [];
  const up = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.ok(up.sent >= 2, "decision and shape went");
  const body = gunzipSync(log[0].opts.body).toString();
  assert.ok(!body.includes("TW") && !body.includes("Fever") && !body.includes("Volumes"), "no names, no paths");
  const again = await S.importNewAutosaves({ home, root });
  assert.equal(again.files, 0, "the same save is read once");
});

test("autosaves in Premiere's own Documents folder are read too", async () => {
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  await S.setConsent(true, { home });
  premierePrefs(home, [], { folder: "MyDocuments" });
  const auto = join(home, "premiere-prefs", "26.0", "Adobe Premiere Pro Auto-Save");
  mkdirSync(auto);
  writeFileSync(join(auto, "film--996b7c3f-0732-83e7-1285-3939994eac3e-2099-01-01_10-00-00.prproj"), projectXml(3));
  const r = await S.importNewAutosaves({ home, root });
  assert.equal(r.files, 1);
});

test("a diff that starts before the yes stays home", async () => {
  const { home, root } = fixture();
  await yesBefore(home);
  S.updateState((x) => { x.sharingSince = "2026-11-02T09:55:00.000Z"; }, home); // between the fixture's from (09:50) and t (10:00)
  const p = S.preview({ home, root });
  assert.ok(!p.sample.some((i) => i.item === "decision" && i.type === "trim_head"), "the trim spanning the yes is not sent");
});

test("a project that does not read leaves a reason, and doctor says it in words", async () => {
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  const root = mkdtempSync(join(tmpdir(), "share-journal-"));
  const proj = mkdtempSync(join(tmpdir(), "proj-bad-"));
  const file = join(proj, "broken.prproj");
  await S.setConsent(true, { home });
  premierePrefs(home, [file], { autoSave: false });
  writeFileSync(file, '<?xml version="1.0"?><PremiereData Version="3"><Sequence ObjectUID="s" ClassID="x" Version="99"><Broken/></Sequence><VideoClipTrackItem ObjectID="1" ClassID="x" Version="99"/></PremiereData>');
  const r = await S.importNewAutosaves({ home, root });
  assert.equal(r.failed, 1);
  const st = S.loadState(home);
  assert.equal(st.lastImportError.file, "broken.prproj");
  const d = S.doctor({ home, root });
  assert.equal(d.projectsFound, 1);
  assert.ok(d.verdict.some((v) => /автосохранение/.test(v)));
  assert.ok(d.verdict.some((v) => /broken\.prproj/.test(v)));
  writeFileSync(file, '<?xml version="1.0"?><PremiereData Version="3"><Project ObjectRef="1"/></PremiereData>'); // media only, no sequence yet: not a failure
  utimesSync(file, new Date(Date.now() + 60000), new Date(Date.now() + 60000));
  const r2 = await S.importNewAutosaves({ home, root });
  assert.equal(r2.failed, 0);
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  const empty = S.doctor({ home: mkdtempSync(join(tmpdir(), "share-home-")), root: mkdtempSync(join(tmpdir(), "share-journal-")) });
  assert.ok(empty.verdict.some((v) => /настройки Premiere/.test(v)));
});

test("PREMIERE_PREFS_DIR alone decides where prefs are read", () => {
  process.env.PREMIERE_PREFS_DIR = NO_PREFS;
  assert.deepEqual(S.prefsBases(), [NO_PREFS]);
});

test("a 0.8.1 yes without a start moment starts from now, not from the beginning", () => {
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  S.saveState({ consent: true, installId: "old", salt: "s", sent: {}, autosaveSeen: {}, projectDirs: [] }, home);
  const st = S.ensureStarted(home);
  assert.ok(Date.parse(st.sharingSince) > Date.now() - 60000);
  assert.equal(st.consent, true);
});

test("a half-written share.json is never read as never chose", () => {
  const home = mkdtempSync(join(tmpdir(), "share-home-"));
  writeFileSync(join(home, "share.json"), '{"consent": false, "inst');
  assert.throws(() => S.loadState(home), /не читается/);
  S.saveState({ consent: false, excluded: ["NDA"] }, home);
  assert.equal(S.loadState(home).consent, false);
});

test("Windows paths: an excluded project is kept out", async () => {
  const { home, root } = fixture();
  await yesBefore(home);
  S.setExcluded("C:\\Users\\denis\\Clients\\Client Film.prproj", true, home);
  assert.equal(S.preview({ home, root }).wouldSend, 0);
});

test("two bridges on one machine: one round at a time, a dead bridge's lock expires", async () => {
  const { home, root } = fixture();
  await yesBefore(home);
  const release = S.acquireLock(home);
  assert.ok(release);
  const r = await S.tick({ home, root, fetchImpl: fakeFetch([]) });
  assert.equal(r.busy, true);
  assert.equal(S.acquireLock(home), null);
  release();
  const log = [];
  const r2 = await S.tick({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r2.sent, 3);
  const stale = S.acquireLock(home);
  utimesSync(join(home, "share.lock"), new Date(Date.now() - S.LOCK_STALE_MS - 1000), new Date(Date.now() - S.LOCK_STALE_MS - 1000));
  assert.ok(S.acquireLock(home), "a stale lock is taken over");
  stale();
});

test("the bridge reports the version from package.json", () => {
  const src = readFileSync(new URL("../server.js", import.meta.url), "utf8");
  assert.ok(!/const VERSION = "\d/.test(src), "no hand-typed version in server.js");
});
