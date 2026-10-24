import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import * as ops from "../lib/ops.js";

// A fake Premiere: answers each body by what it is for, records the calls.
function fake(answers) {
  const calls = [];
  const run = async (body) => {
    calls.push(body);
    for (const [needle, value] of answers) if (body.includes(needle)) return typeof value === "function" ? value(body) : value;
    return { error: "unexpected body" };
  };
  return { run, calls };
}

test("a Premiere-side error becomes BridgeError with the data", async () => {
  const { run } = fake([["app.project.deleteSequence", { error: "Sequence not found: x" }]]);
  await assert.rejects(ops.deleteSequence(run, { name: "x" }), (e) => e instanceof ops.BridgeError && e.data.error === "Sequence not found: x");
  await assert.rejects(ops.deleteSequence(run, {}), /name is required/);
});

test("activate checks that the sequence really became active", async () => {
  const { run } = fake([["openSequence", { opened: [{ name: "A" }], missing: [], activeSequence: "B" }]]);
  await assert.rejects(ops.activate(run, { name: "A" }), /activeSequence is "B"/);
});

test("markers are sent in chunks of 400, clear only on the first", async () => {
  const { run, calls } = fake([["createMarker", (b) => ({ requested: (b.match(/"t":/g) || []).length, created: (b.match(/"t":/g) || []).length, removed: b.includes("CLEAR = true") ? 7 : 0, markersBefore: 0, markersAfter: 1 })]]);
  const markers = Array.from({ length: 1000 }, (_, i) => ({ t: i, name: "m" + i }));
  const r = await ops.addMarkers(run, { seq: "s", markers, clear: true });
  assert.equal(calls.length, 3);
  assert.equal(r.requested, 1000); assert.equal(r.created, 1000); assert.equal(r.removed, 7);
  assert.ok(calls[0].includes("CLEAR = true") && calls[1].includes("CLEAR = false"));
});

test("getInOut: sequence in/out wins over the clip span", async () => {
  const t = { name: "S", sequenceID: "id", width: 1080, height: 1920, fps: 29.97, end: 30, inPoint: 2, outPoint: 9, workArea: null, clipsRange: [0, 30], renderRange: [2, 9],
    video: [{ index: 0, clips: [{ name: "c", start: 0, end: 30, inPoint: 10, outPoint: 40, path: "/x" }] }], audio: [] };
  const { run } = fake([["getInPointAsTime", t]]);
  const r = await ops.getInOut(run, { seq: "S" });
  assert.deepEqual(r.renderRange, [2, 9]);
  assert.equal(r.video[0].inPoint, 10);
});

const ffmpeg = (() => { try { execFileSync("ffmpeg", ["-version"], { stdio: "ignore" }); return true; } catch { return false; } })();

test("importMedia: VP9 is refused before Premiere, with the ffmpeg command; convert=true imports the copy", { skip: !ffmpeg && "ffmpeg missing" }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "ops-"));
  try {
    const vp9 = join(dir, "web.webm");
    execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=0.5", "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", vp9]);
    const { run, calls } = fake([["importFiles", (b) => ({ name: "web-h264.mp4", nodeId: "n1", type: "clip", path: b.match(/var PATH = "([^"]+)"/)[1], fps: 30, videoDecoded: true, reused: false })]]);
    await assert.rejects(ops.importMedia(run, { paths: [vp9], bin: "B" }), (e) => /sound only/.test(e.message) && /ffmpeg/.test(e.data.fixCmd));
    assert.equal(calls.length, 0, "Premiere was not touched");
    const r = await ops.importMedia(run, { paths: [vp9], bin: "B", convert: true });
    assert.equal(r.imported, 1);
    assert.equal(r.results[0].converted, true);
    assert.match(r.results[0].item.path, /web-h264\.mp4$/);
    assert.equal(calls.length, 1);
    await assert.rejects(ops.importMedia(run, { paths: ["relative.mp4"] }), /absolute/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("build: stops at the first failed sequence, reports the steps done, opens the good ones", async () => {
  const { run, calls } = fake([
    ["createNewSequenceFromClips", (b) => b.includes('"BAD"') ? { error: "boom" } : { name: b.match(/var NAME = "([^"]+)"/)[1], sequenceID: "id", width: 1080, height: 1920, fps: 29.97, replaced: false, check: { v1Clips: 1, a1Clips: 1 } }],
    ["__motion(cl)", (b) => b.includes("KEYS") ? { keysWritten: 2, positionKeysPx: [], scaleKeys: [] } : { scale: 177.778, positionPx: [735, 960] }],
    ["createMarker", { requested: 1, created: 1, removed: 0, markersBefore: 0, markersAfter: 1 }],
    ["openSequence", { opened: [{ name: "OK1" }], missing: [], activeSequence: "OK1" }],
  ]);
  const spec = { bin: "B", width: 1080, height: 1920, fps: 29.97, sequences: [
    { name: "OK1", item: "n1", in: 1, out: 2, motion: { scale: 177.778, x: 735, y: 960 }, motionKeys: [{ t: 0, posX: 1 }], markers: [{ t: 0, name: "m" }] },
    { name: "BAD", item: "n1" }, { name: "NEVER", item: "n1" } ] };
  const r = await ops.build(run, spec);
  assert.equal(r.ok, false);
  assert.deepEqual(r.sequences.map((s) => [s.name, s.ok]), [["OK1", true], ["BAD", false]]);
  assert.deepEqual(r.sequences[0].steps.map((s) => s.step), ["make_sequence", "set_motion", "set_motion_keyframes", "add_markers"]);
  assert.equal(r.sequences[1].error.error, "boom");
  assert.equal(r.opened.activeSequence, "OK1");
  assert.ok(!calls.some((c) => c.includes('"NEVER"')));
  const r2 = await ops.build(run, { ...spec, continueOnError: true, open: false });
  assert.equal(r2.sequences.length, 3);
  assert.equal(r2.opened, null);
});
