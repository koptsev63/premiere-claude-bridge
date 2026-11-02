import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
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

test("no consent: nothing leaves", async () => {
  const { home, root } = fixture();
  const log = [];
  const r = await S.upload({ home, root, fetchImpl: fakeFetch(log) });
  assert.equal(r.sent, 0);
  assert.equal(log.length, 0);
  assert.equal(S.consentStatus(home).consent, null);
});

test("yes: sends anonymized decisions and shapes once, without names, paths or the salt", async () => {
  const { home, root } = fixture();
  const log = [];
  await S.setConsent(true, { home, fetchImpl: fakeFetch(log) });
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
  await S.setConsent(true, { home });
  const r = await S.upload({ home, root, fetchImpl: async () => ({ ok: false, status: 503 }) });
  assert.match(r.error, /503/);
  const log = [];
  assert.equal((await S.upload({ home, root, fetchImpl: fakeFetch(log) })).sent, 3);
});

test("preview shows what would go, sends nothing", () => {
  const { home, root } = fixture();
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
