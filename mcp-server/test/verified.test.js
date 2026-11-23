import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as O from "../lib/origin.js";
import * as ops from "../lib/ops.js";
import * as JR from "../lib/journal.js";

const T = (s) => new Date(Date.UTC(2026, 10, 23, 12, 0, s)).toISOString();
const trim = { sequence: "S", sequenceID: "s", type: "trim_tail", kind: "video", name: "a.mp4", path: "/m/a.mp4", track: 0, start: 12, end: 14.6, inPoint: 0, outPoint: 2.6, fromStart: 12, fromEnd: 15, fromIn: 0, fromOut: 3 };
const aiCall = { v: 1, id: "a_1", t_start: T(5), t: T(6), ok: true, tool: "pr_eval_jsx", scope: "structure", channel: "mcp", sequence: null, sequenceID: null, effects: [trim] };
const row = { t: T(40), from_t: T(0), ...trim };
const opts = { since: T(-60), trackedHost: true, oldSessions: [] };

test("an AI edit undone before an unchanged snapshot does not explain the same edit made later by hand", () => {
  // AI trims at 5 s, a hand undoes it, the snapshot at 20 s finds nothing changed, a hand trims again at 30 s
  assert.equal(O.attributeEvents([row], [aiCall], opts)[0].origin, "via_bridge_ai", "without the check in between the two cannot be told apart");
  const [a] = O.attributeEvents([row], [aiCall], { ...opts, verified: { [T(0)]: T(20) } });
  assert.equal(a.origin, "not_via_bridge");
  assert.deepEqual(a.action_ids, []);
});

test("the check moves the window only for its own snapshot, and never past a call that may still land", () => {
  assert.equal(O.attributeEvents([row], [aiCall], { ...opts, verified: { [T(-30)]: T(20) } })[0].origin, "via_bridge_ai", "a check of another snapshot says nothing");
  assert.equal(O.attributeEvents([row], [aiCall], { ...opts, verified: { [T(0)]: T(3) } })[0].origin, "via_bridge_ai", "the call came after the check");
  assert.equal(O.attributeEvents([row], [aiCall], { ...opts, verified: { [T(0)]: T(50) } })[0].origin, "via_bridge_ai", "a check after the end of the window is ignored");
  const hung = { ...aiCall, id: "a_h", ok: false, hung: true, effects: null, until: Infinity };
  const [h] = O.attributeEvents([row], [hung], { ...opts, verified: { [T(0)]: T(20) } });
  assert.deepEqual([h.origin, h.origin_reason], ["unresolved", "ai_action_effect_unknown"]);
});

test("markVerified keeps the latest moment per snapshot", () => {
  const dir = mkdtempSync(join(tmpdir(), "verified-"));
  try {
    assert.deepEqual(O.readVerified(dir), {});
    O.markVerified(dir, T(0), T(20)); O.markVerified(dir, T(0), T(10)); O.markVerified(dir, T(40), T(45)); O.markVerified(dir, "", T(50));
    assert.deepEqual(O.readVerified(dir), { [T(0)]: T(20), [T(40)]: T(45) });
    assert.equal(O.originContext(dir).verified[T(0)], T(20));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("a live snapshot that finds nothing changed leaves the mark; a changed one does not", async () => {
  const dir = mkdtempSync(join(tmpdir(), "verified-"));
  try {
    const seq = (end) => ({ name: "S", sequenceID: "s", fps: 25, width: 1920, height: 1080, end, markers: 0, video: [{ kind: "video", index: 0, name: "", clipCount: 1, clips: [{ name: "a.mp4", path: "/m/a.mp4", start: 0, end, inPoint: 0, outPoint: end }] }], audio: [] });
    const state = { end: 5 };
    const run = async () => ({ project: "x.prproj", projectPath: "/p/x.prproj", sequences: [seq(state.end)], activeSequence: "S" });
    const first = await ops.journalSnapshot(run, { dir });
    assert.equal(first.saved, true);
    assert.ok(!existsSync(join(dir, O.VERIFIED_FILE)));
    const again = await ops.journalSnapshot(run, { dir });
    assert.equal(again.saved, false);
    const marks = JSON.parse(readFileSync(join(dir, O.VERIFIED_FILE), "utf8"));
    assert.equal(Object.keys(marks).length, 1);
    state.end = 4;
    const changed = await ops.journalSnapshot(run, { dir });
    assert.equal(changed.saved, true);
    assert.equal(Object.keys(O.readVerified(dir)).length, 1, "a saved snapshot adds no mark");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("snapshots taken within one second are still compared in the order they were taken", () => {
  const dir = mkdtempSync(join(tmpdir(), "verified-"));
  try {
    const at = (ms) => new Date(Date.UTC(2026, 10, 23, 13, 16, 48, ms)).toISOString();
    const snap = (end, t) => ({ t, project: "x.prproj", sequences: [{ name: "S", sequenceID: "s", fps: 25, markers: 0, video: [{ kind: "video", index: 0, clipCount: 1, clips: [{ name: "a.mp4", path: "/m/a.mp4", start: 0, end, inPoint: 0, outPoint: end }] }], audio: [] }] });
    assert.equal(JR.record(dir, snap(5, new Date(Date.UTC(2026, 10, 23, 13, 16, 46)).toISOString())).saved, true); // A
    const b = JR.record(dir, snap(4, at(100))); // B: an edit
    assert.deepEqual([b.saved, b.events], [true, 1]);
    const a2 = JR.record(dir, snap(5, at(300))); // back to A in the same second: a change, not "nothing changed"
    assert.deepEqual([a2.saved, a2.events], [true, 1], "compared with B, not with the snapshot before it");
    assert.equal(JR.latestSnapshot(dir).t, at(300), "the latest is the one taken last, whatever its hash sorts like");
    const same = JR.record(dir, snap(5, at(600)));
    assert.equal(same.saved, false);
    const c = JR.record(dir, snap(3, new Date(Date.UTC(2026, 10, 23, 13, 16, 50)).toISOString()));
    assert.equal(c.diff.from, at(300), "the next second diffs against the last one of the second before");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("the unchanged mark is written only when the timeline equals the latest snapshot", async () => {
  const dir = mkdtempSync(join(tmpdir(), "verified-"));
  try {
    const seq = (end) => ({ name: "S", sequenceID: "s", fps: 25, width: 1920, height: 1080, end, markers: 0, video: [{ kind: "video", index: 0, name: "", clipCount: 1, clips: [{ name: "a.mp4", path: "/m/a.mp4", start: 0, end, inPoint: 0, outPoint: end }] }], audio: [] });
    const state = { end: 5 };
    const run = async () => ({ project: "x.prproj", projectPath: "/p/x.prproj", sequences: [seq(state.end)], activeSequence: "S" });
    const pause = () => new Promise((r) => setTimeout(r, 5)); // a live snapshot never takes 0 ms
    await ops.journalSnapshot(run, { dir }); await pause();
    state.end = 4; await ops.journalSnapshot(run, { dir }); await pause();
    state.end = 5; const back = await ops.journalSnapshot(run, { dir }); await pause(); // possibly the same second as the two before
    assert.equal(back.saved, true, "a return to the first state is a change");
    assert.ok(!existsSync(join(dir, O.VERIFIED_FILE)), "no mark: nothing was seen unchanged");
    const again = await ops.journalSnapshot(run, { dir });
    assert.equal(again.saved, false);
    assert.deepEqual(Object.keys(O.readVerified(dir)), [JR.latestSnapshot(dir).t]);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
