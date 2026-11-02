import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diffSequence, diffSnapshots, saveSnapshot, record, report, reportMarkdown, sequenceShape, handReport, slug } from "../lib/journal.js";

const FPS = 25;
const clip = (name, start, end, inP, outP, extra = {}) => ({ name, path: "/m/" + name, start, end, inPoint: inP, outPoint: outP, duration: end - start, ...extra });
const seq = (clips, audio = null, extra = {}) => ({ name: "S", sequenceID: "sid", fps: FPS, width: 1920, height: 1080, markers: 0, end: Math.max(...clips.map((c) => c.end)),
  video: [{ kind: "video", index: 0, clipCount: clips.length, clips }], audio: audio ? [{ kind: "audio", index: 0, clipCount: audio.length, clips: audio }] : [], ...extra });

const base = [clip("a.mp4", 0, 4, 10, 14), clip("b.mp4", 4, 10, 20, 26), clip("c.mp4", 10, 13, 30, 33)];
const types = (evs) => evs.map((e) => e.type).sort();

test("identical sequences: no decisions", () => {
  assert.deepEqual(diffSequence(seq(base), seq(base)), []);
});

test("head trim: in point and start move together", () => {
  const next = [clip("a.mp4", 0, 4, 10, 14), clip("b.mp4", 4.4, 10, 20.4, 26), clip("c.mp4", 10, 13, 30, 33)];
  const evs = diffSequence(seq(base), seq(next));
  assert.deepEqual(types(evs), ["trim_head"]);
  assert.equal(evs[0].frames, 10); assert.equal(evs[0].shortened, true); assert.equal(evs[0].name, "b.mp4");
});

test("tail trim lengthened by 8 frames, with the rest rippled: tail + moves", () => {
  const next = [clip("a.mp4", 0, 4.32, 10, 14.32), clip("b.mp4", 4.32, 10.32, 20, 26), clip("c.mp4", 10.32, 13.32, 30, 33)];
  const evs = diffSequence(seq(base), seq(next));
  const tail = evs.find((e) => e.type === "trim_tail");
  assert.ok(tail && tail.frames === -8 && tail.shortened === false);
  assert.equal(evs.filter((e) => e.type === "move").length, 2);
});

test("slip keeps the timeline, moves the source window", () => {
  const next = [clip("a.mp4", 0, 4, 10, 14), clip("b.mp4", 4, 10, 21, 27), clip("c.mp4", 10, 13, 30, 33)];
  const evs = diffSequence(seq(base), seq(next));
  assert.deepEqual(types(evs), ["slip"]); assert.equal(evs[0].frames, 25);
});

test("razor: one clip becomes two contiguous parts = split, not add+remove", () => {
  const next = [clip("a.mp4", 0, 4, 10, 14), clip("b.mp4", 4, 7, 20, 23), clip("b.mp4", 7, 10, 23, 26), clip("c.mp4", 10, 13, 30, 33)];
  const evs = diffSequence(seq(base), seq(next));
  assert.deepEqual(types(evs), ["split"]); assert.equal(evs[0].parts, 2); assert.deepEqual(evs[0].cutsAt, [7]);
});

test("removed and added shots, speed change", () => {
  const next = [clip("a.mp4", 0, 4, 10, 14), clip("d.mp4", 4, 6, 0, 2), clip("c.mp4", 6, 12, 30, 33)];
  const evs = diffSequence(seq(base), seq(next));
  assert.deepEqual(types(evs), ["added", "removed", "speed"]);
  const sp = evs.find((e) => e.type === "speed"); assert.equal(sp.fromSpeed, 1); assert.equal(sp.toSpeed, 0.5);
});

test("track move and marker count", () => {
  const next = seq(base.slice(0, 2), null, { markers: 3 });
  next.video.push({ kind: "video", index: 1, clipCount: 1, clips: [clip("c.mp4", 10, 13, 30, 33)] });
  const evs = diffSequence(seq(base), next);
  assert.deepEqual(types(evs), ["markers", "move_track"]);
});

test("snapshots: sequences added and removed, counts", () => {
  const A = { t: "2026-11-02T10:00:00Z", sequences: [seq(base)] };
  const B = { t: "2026-11-02T10:05:00Z", sequences: [{ ...seq(base), sequenceID: "other", name: "T" }] };
  const d = diffSnapshots(A, B);
  assert.equal(d.count, 2);
  assert.deepEqual(d.sequences.map((s) => s.events[0].type).sort(), ["sequence_added", "sequence_removed"]);
});

test("storage: unchanged content is not saved twice; record appends decisions; report reads back", () => {
  const dir = mkdtempSync(join(tmpdir(), "journal-"));
  try {
    const A = { t: "2026-11-02T10:00:00Z", project: "x.prproj", sequences: [seq(base)] };
    assert.equal(saveSnapshot(dir, A).saved, true);
    assert.equal(saveSnapshot(dir, { ...A, t: "2026-11-02T10:01:00Z" }).saved, false, "same content, different time");
    const next = [clip("a.mp4", 0, 4, 10, 14), clip("b.mp4", 4.4, 10, 20.4, 26), clip("c.mp4", 10, 13, 30, 33)];
    const r = record(dir, { t: "2026-11-02T10:10:00Z", project: "x.prproj", sequences: [seq(next)] }, { source: "test" });
    assert.equal(r.saved, true); assert.equal(r.events, 1);
    const rep = report(dir);
    assert.equal(rep.snapshots, 2); assert.equal(rep.hand.events, 1); assert.equal(rep.hand.trimHead.n, 1);
    // an older autosave arriving later diffs against its own earlier neighbour (none here) and not against the newest
    const old = record(dir, { t: "2026-11-02T09:00:00Z", project: "x.prproj", sequences: [seq(base.slice(0, 2))] }, { source: "autosave" });
    assert.equal(old.saved, true); assert.equal(old.events, 0);
    assert.equal(report(dir).snapshots, 3);
    assert.equal(rep.shapes[0].shotsV1, 3);
    assert.match(reportMarkdown(rep), /head trims: 1/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("sequence shape: J/L cuts and gaps", () => {
  const audio = [clip("a.mp4", 0, 4.2, 10, 14.2), clip("b.mp4", 4.2, 9.8, 20.2, 25.8), clip("c.mp4", 9.8, 13, 29.8, 33)];
  const s = sequenceShape(seq([clip("a.mp4", 0, 4, 10, 14), clip("b.mp4", 4, 10, 20, 26), clip("c.mp4", 10.2, 13, 30, 33)], audio));
  assert.equal(s.jlCuts.count, 2); assert.equal(s.jlCuts.lTrailing, 1); assert.equal(s.jlCuts.jLeading, 1);
  assert.equal(s.gapsV1, 1);
  assert.equal(s.histogram["2-4s"], 1); assert.equal(s.histogram["4-8s"], 2);
});

test("hand report: tempo over active minutes", () => {
  const evs = [
    { t: "2026-11-02T10:00:00Z", type: "trim_head", kind: "video", frames: 10, shortened: true, sequence: "S" },
    { t: "2026-11-02T10:10:00Z", type: "trim_tail", kind: "video", frames: -4, shortened: false, sequence: "S" },
    { t: "2026-11-02T12:00:00Z", type: "move", kind: "video", frames: 3, sequence: "S" }, // a 110-min gap is not active time
  ];
  const h = handReport(evs, ["a", "b", "c"]);
  assert.equal(h.period.activeMinutes, 10); assert.equal(h.editsPerActiveHour, 18);
  assert.equal(slug("подкаст.prproj"), "подкаст");
});
