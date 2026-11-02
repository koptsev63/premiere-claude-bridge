import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, utimesSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { gunzipSync } from "node:zlib";
import * as S from "../lib/share.js";

function fixture() {
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
