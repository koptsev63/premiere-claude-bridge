// The bounded light read around an AI call, run as real ExtendScript text
// against a fake Premiere whose clock moves on every property read (a "hop"
// to Premiere's UI thread), so a deadline can be checked without waiting.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import vm from "node:vm";
import * as J from "../lib/seq.js";
import * as JR from "../lib/journal.js";
import * as O from "../lib/origin.js";
import * as AI from "../lib/aiactions.js";
import { journalSnapshot, BridgeError } from "../lib/ops.js";
import { busyError } from "../lib/panelqueue.js";
import { prepareEvalCode } from "../lib/evalcode.js";

const clip = (name, start, end, i, o) => ({ name, path: "/m/" + name, start, end, inPoint: i, outPoint: o });
const rowsOf = (tag, n) => Array.from({ length: n }, (_, i) => clip(`${tag}${i}.mp4`, i, i + 1, 0, 1));
const ROW_HOPS = 12; // projectItem, nodeId, getMediaPath, name, 4 x (time + .seconds)

// specs: [{ name, id, rows }]; `active` is an index or -1.
function clockPremiere(specs, { active = 0, step = 1 } = {}) {
  const clock = { t: 1000 };
  const hop = () => { clock.t += step; };
  const get = (o, k, f) => Object.defineProperty(o, k, { get() { hop(); return f(); }, enumerable: true, configurable: true });
  const time = (v) => get({}, "seconds", () => v);
  const clips = (rows) => new Proxy({}, {
    get(_, k) {
      if (k === "numItems") { hop(); return rows.length; }
      if (k === "setEnd") return (i, v) => { rows[i] = { ...rows[i], end: v }; };
      const i = Number(k), c = rows[i];
      if (!c) return undefined;
      const o = {};
      get(o, "name", () => c.name);
      get(o, "projectItem", () => { const pi = { getMediaPath: () => { hop(); return c.path; } }; get(pi, "nodeId", () => "n:" + c.path); return pi; });
      for (const t of ["start", "end", "inPoint", "outPoint"]) get(o, t, () => time(c[t]));
      return o;
    },
  });
  const seqObj = (sp) => {
    const s = {};
    get(s, "name", () => sp.name);
    get(s, "sequenceID", () => sp.id);
    get(s, "timebase", () => "10160640000");
    get(s, "markers", () => ({ numMarkers: 0 }));
    get(s, "videoTracks", () => { const v = { 0: { name: "", clips: clips(sp.rows) } }; get(v, "numTracks", () => 1); return v; });
    get(s, "audioTracks", () => { const a = {}; get(a, "numTracks", () => 0); return a; });
    return s;
  };
  const list = specs.slice();
  const project = {
    addSeq: (name, id, rows) => { list.push({ name, id, rows }); },
    dropSeq: (id) => { const i = list.findIndex((x) => x.id === id); if (i >= 0) list.splice(i, 1); },
  };
  get(project, "name", () => "x.prproj");
  get(project, "activeSequence", () => (active >= 0 ? seqObj(specs[active]) : null));
  get(project, "sequences", () => { const o = {}; list.forEach((sp, i) => (o[i] = seqObj(sp))); get(o, "numSequences", () => list.length); return o; });
  const context = { app: { project }, Date: function FakeDate() { return { getTime: () => clock.t }; } };
  vm.createContext(context);
  const raw = (body) => vm.runInContext(`(function(){ try { var __r = (function(){ ${body} })(); return (typeof __r === 'undefined') ? '' : (typeof __r === 'string' ? __r : JSON.stringify(__r)); } catch(e){ return JSON.stringify({error: e.toString()}); } })()`, context);
  return { clock, list, raw, run: (body) => JSON.parse(raw(body)) };
}

const read = (P, o) => P.run(`${J.STATE_READER} var r = __bstRead(${JSON.stringify(o)}); delete r.seqs; delete r.all; return r;`);

test("the deadline is checked before every clip row: a read stops within one row of its limit", () => {
  const P = clockPremiere([{ name: "Big", id: "big", rows: rowsOf("b", 5000) }]);
  const t0 = P.clock.t;
  const r = P.run(J.timelineStateJsx({ limitMs: 350 }));
  const spent = P.clock.t - t0;
  assert.equal(r.error, "timeline read over the limit");
  assert.equal(r.project, "x.prproj");
  assert.deepEqual(r.over, ["big"]);
  assert.ok(spent >= 350, `ran to its limit (${spent})`);
  assert.ok(spent <= 350 + ROW_HOPS + 2, `no more than one row past the limit (${spent})`);
});

test("the active sequence first, the others only while under half the limit; a cut sequence is dropped whole", () => {
  const seqs = () => [{ name: "A", id: "a", rows: rowsOf("a", 2) }, { name: "B", id: "b", rows: rowsOf("b", 2) }, { name: "C", id: "c", rows: rowsOf("c", 2) }];
  let r = read(clockPremiere(seqs(), { active: 1 }), { limit: 1000 });
  assert.deepEqual(r.read, ["b", "a", "c"]); assert.equal(r.complete, true); assert.equal(r.listed, true); assert.deepEqual(r.over, []);

  const slowActive = seqs(); slowActive[1].rows = rowsOf("b", 6); // about 75 hops: over half of 100
  r = read(clockPremiere(slowActive, { active: 1 }), { limit: 100 });
  assert.deepEqual(r.read, ["b"]); assert.equal(r.complete, false); assert.deepEqual(r.over, [], "not cut, just not started");

  const bigOther = seqs(); bigOther[0].rows = rowsOf("a", 1000);
  r = read(clockPremiere(bigOther, { active: 1 }), { limit: 100 });
  assert.deepEqual(r.read, ["b"], "A was cut and is dropped whole; C is past the deadline");
  assert.deepEqual(r.over, ["a"]); assert.equal(r.complete, false);
});

test("skip and only; the project name is always there", () => {
  const seqs = () => [{ name: "A", id: "a", rows: rowsOf("a", 2) }, { name: "B", id: "b", rows: rowsOf("b", 2) }, { name: "C", id: "c", rows: rowsOf("c", 2) }];
  let r = read(clockPremiere(seqs(), { active: 1 }), { limit: 1000, skip: { a: 1 } });
  assert.deepEqual(r.read, ["b", "c"]); assert.equal(r.complete, false);
  r = read(clockPremiere(seqs(), { active: 1 }), { limit: 1000, skip: { b: 1 } });
  assert.deepEqual(r.read, [], "the active one is paused: nothing else either");
  assert.equal(r.project, "x.prproj"); assert.equal(r.listed, false);
  r = read(clockPremiere(seqs(), { active: 1 }), { limit: 0 });
  assert.equal(r.project, "x.prproj"); assert.deepEqual(r.read, []);

  r = read(clockPremiere(seqs(), { active: 1 }), { limit: 1000, only: ["C", "a"] });
  assert.deepEqual(r.read, ["c", "a"], "in the order asked, by name or id"); assert.equal(r.complete, true, "the others are not missing");
  r = read(clockPremiere(seqs(), { active: 1 }), { limit: 1000, only: [], active: true });
  assert.deepEqual(r.read, ["b"]); assert.equal(r.complete, true);
  r = read(clockPremiere(seqs(), { active: 1 }), { limit: 1000, only: ["New"] });
  assert.deepEqual(r.read, []); assert.equal(r.complete, true, "a target that does not exist is a complete read of absent");
  const st = clockPremiere(seqs(), { active: 1 }).run(J.timelineStateJsx({ limitMs: 1000, only: ["A"] }));
  assert.deepEqual(st.sequences.map((s) => s.name), ["A"]);
});

const envOf = (P, body, opts) => JSON.parse(P.raw(J.trackedEvalJsx(body, opts)));

test("tracked eval envelope: read, complete, over; added and removed sequences; the body stays on line 1", () => {
  const seqs = () => [{ name: "A", id: "a", rows: rowsOf("a", 3) }, { name: "B", id: "b", rows: rowsOf("b", 3) }];
  let e = envOf(clockPremiere(seqs(), { active: 1 }), "app.project.activeSequence.videoTracks[0].clips.setEnd(1, 1.5); return 'ok';", { limitMs: 1000 });
  assert.equal(e.__bridge_tracked, 1); assert.equal(e.value, "ok"); assert.equal(e.threw, false); assert.equal(e.project, "x.prproj");
  assert.deepEqual(e.read.sort(), ["a", "b"]); assert.equal(e.complete, true); assert.deepEqual(e.over, []);
  assert.deepEqual(e.before.map((s) => s.sequenceID), ["b"], "only the changed sequence is carried");

  const big = seqs(); big[0].rows = rowsOf("a", 1000);
  e = envOf(clockPremiere(big, { active: 1 }), "return 1;", { limitMs: 200 });
  assert.deepEqual(e.read, ["b"]); assert.equal(e.complete, false); assert.deepEqual(e.over, ["a"]);
  e = envOf(clockPremiere(big, { active: 1 }), "return 1;", { limitMs: 200, skip: ["a"] });
  assert.deepEqual(e.read, ["b"]); assert.deepEqual(e.over, []);

  e = envOf(clockPremiere(seqs(), { active: 1 }), "app.project.addSeq('N', 'n', []); app.project.dropSeq('a'); return 1;", { limitMs: 1000 });
  assert.deepEqual(e.read.sort(), ["a", "b", "n"]); assert.equal(e.complete, true);
  const fx = JR.diffSnapshots({ sequences: e.before }, { sequences: e.after }).sequences.flatMap((s) => s.events.map((x) => x.type));
  assert.deepEqual(fx.sort(), ["sequence_added", "sequence_removed"]);

  const lines = J.trackedEvalJsx("BODY_HERE", { skip: ["x"] }).split("\n");
  assert.match(lines[0], /BODY_HERE/);
  assert.match(J.trackedEvalJsx("x"), /^[\x09\x0a\x0d\x20-\x7e]*$/);
  assert.doesNotMatch(J.trackedEvalJsx("x") + J.timelineStateJsx({ only: ["Эпизод"], active: true }), /=>|\blet\b|\bconst\b|`/);
  assert.doesNotThrow(() => new Function(J.trackedEvalJsx("return 1;", { skip: ["a", "b"] })));
});

function tempRoot() { return mkdtempSync(join(tmpdir(), "bounded-")); }
const logged = (root) => readFileSync(join(root, "x", O.ACTIONS_FILE), "utf8").trim().split("\n").map((l) => JSON.parse(l));
const EDIT = "app.project.activeSequence.videoTracks[0].clips.setEnd(1, 1.5); return 'done';";

test("runEval: a partial read logs what it compared; a sequence over the limit is paused and passed as skip", async () => {
  const root = tempRoot();
  try {
    // limit 350, 0.1 ms per hop (Premiere at its fast pace): the active one fits, the other starts under half and is cut
    const P = clockPremiere([{ name: "Other", id: "o", rows: rowsOf("o", 1000) }, { name: "S", id: "s", rows: rowsOf("s", 10) }], { active: 1, step: 0.1 });
    const sent = [];
    const call = async (b) => { sent.push(b); return P.raw(b); };
    assert.equal(await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root }), "done");
    const [a] = logged(root);
    assert.equal(a.partial_read, true); assert.deepEqual(a.seqs_read, ["s"]);
    assert.equal(a.effects.length, 1); assert.equal(a.effects[0].sequenceID, "s");
    assert.equal(await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root }), "done");
    assert.match(sent[1].split("\n")[0], /skip: \{"o": 1\}/);
    assert.equal(logged(root)[1].partial_read, true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("runEval: the active sequence paused, nothing compared: effect unknown, but the row keeps its project", async () => {
  const root = tempRoot();
  try {
    const P = clockPremiere([{ name: "S", id: "s", rows: rowsOf("s", 5000) }], { step: 0.1 });
    const sent = [];
    const call = async (b) => { sent.push(b); return P.raw(b); };
    await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root });
    await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root });
    assert.match(sent[1].split("\n")[0], /skip: \{"s": 1\}/);
    const rows = logged(root);
    assert.equal(rows.length, 2, "both rows land in the project's log");
    for (const r of rows) { assert.equal(r.effects, null); assert.equal(r.project, "x.prproj"); assert.equal(r.partial_read, undefined); }
    assert.ok(!existsSync(join(root, O.UNROUTED_FILE)));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("runEval: a read cut while Premiere is slow does not pause the sequence", async () => {
  const root = tempRoot();
  try {
    // 16 ms per hop, one 60 Hz frame: 350 ms is two clip rows, whatever the size of the sequence
    const P = clockPremiere([{ name: "S", id: "s", rows: rowsOf("s", 40) }], { step: 16 });
    const sent = [];
    const call = async (b) => { sent.push(b); return P.raw(b); };
    await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root });
    await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root });
    assert.match(sent[1].split("\n")[0], /skip: \{\}/, "tried again on the next call");
    for (const r of logged(root)) { assert.equal(r.effects, null); assert.equal(r.project, "x.prproj"); }
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("PANEL_BUSY never reached Premiere: nothing is logged, the error reaches the caller", async () => {
  const root = tempRoot();
  try {
    const busy = busyError({ ageSec: 42, fn: "evalRaw" });
    await assert.rejects(AI.runEval(EDIT, "X", { call: async () => { throw busy; }, root }), /Nothing new was sent/);
    const res = { isError: true, content: [{ type: "text", text: busy.message }] };
    assert.equal(await AI.trackTool("pr_make_sequence", { name: "S" }, { run: async () => { throw busy; }, root }, async () => res), res);
    assert.ok(!existsSync(join(root, O.UNROUTED_FILE)));
    assert.ok(!existsSync(join(root, "x")));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a structure tool reads only its targets; a target that cannot be read whole leaves the effect unknown", async () => {
  const root = tempRoot();
  try {
    const P = clockPremiere([{ name: "Other", id: "o", rows: rowsOf("o", 3) }, { name: "S", id: "s", rows: rowsOf("s", 3) }]);
    const bodies = [];
    const run = async (b) => { bodies.push(b); return P.run(b); };
    await AI.trackTool("pr_make_sequence", { name: "New" }, { run, root }, async () => { P.list.push({ name: "New", id: "n", rows: rowsOf("n", 2) }); return { content: [] }; });
    let [a] = logged(root);
    assert.deepEqual(a.effects.map((e) => e.type), ["sequence_added"]); assert.deepEqual(a.sequences, ["New"]);
    assert.equal(a.partial_read, undefined, "a structure tool touches only its targets by contract");
    assert.match(bodies[0], /only: \["New"\]/);
    await AI.trackTool("pr_import_captions", {}, { run, root }, async () => ({ content: [] }));
    a = logged(root)[1];
    assert.deepEqual(a.effects, []);
    const big = clockPremiere([{ name: "S", id: "s", rows: rowsOf("s", 1000) }]);
    await AI.trackTool("pr_delete_sequence", { name: "S" }, { run: async (b) => big.run(b), root }, async () => ({ content: [] }));
    a = logged(root)[2];
    assert.equal(a.effects, null); assert.equal(a.project, "x.prproj");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

// ---- origin --------------------------------------------------------------

const T = (m, s = 0) => `2026-11-22T10:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.000Z`;
const seqRow = (clips, extra = {}) => ({ name: "S", sequenceID: "sid", fps: 25, markers: 0, video: [{ kind: "video", index: 0, clipCount: clips.length, clips }], audio: [], ...extra });
const A1 = clip("a.mp4", 0, 4, 10, 14), B1 = clip("b.mp4", 4, 10, 20, 26), B2 = clip("b.mp4", 4.4, 10, 20.4, 26);
const edits = () => JR.eventRows(JR.diffSnapshots({ t: T(1), sequences: [seqRow([A1, B1])] }, { t: T(10), sequences: [seqRow([A1, B2])] }));
const act = (id, effects, extra = {}) => ({ v: 1, id, t_start: T(5), t: T(5, 1), tool: "pr_eval_jsx", scope: "structure", channel: "mcp", ok: true, sequence: null, sequenceID: null, effects, ...extra });
const fxOf = () => JR.diffSnapshots({ sequences: [seqRow([A1, B1])] }, { sequences: [seqRow([A1, B2])] }).sequences.flatMap((s) => s.events.map((e) => ({ sequence: s.sequence, sequenceID: s.sequenceID, ...e })));
const attr = (rows, actions) => O.attributeEvents(rows, actions, { since: T(0) });

test("origin: a partial read cannot vouch for a sequence it did not read", () => {
  let [r] = attr(edits(), [act("p", [], { partial_read: true, seqs_read: ["other"] })]);
  assert.equal(r.origin, "unresolved"); assert.equal(r.origin_reason, "ai_action_effect_unknown"); assert.deepEqual(r.action_ids, ["p"]);
  [r] = attr(edits().map((e) => ({ ...e, sequenceID: undefined })), [act("p", [], { partial_read: true, seqs_read: ["sid"] })]);
  assert.equal(r.origin, "unresolved", "a row without an id counts as not read");
  [r] = attr(edits(), [act("p", [], { partial_read: true, seqs_read: ["sid"] })]);
  assert.equal(r.origin, "not_via_bridge", "its sequence was read and the call did not change it");
  [r] = attr(edits(), [act("p", fxOf(), { partial_read: true, seqs_read: ["sid"] }), act("q", [], { partial_read: true, seqs_read: [] })]);
  assert.equal(r.origin, "via_bridge_ai", "an explained edit comes first");
  [r] = attr(edits(), [act("old", [])]);
  assert.equal(r.origin, "not_via_bridge", "an old row without the new fields reads as before");
});

test("origin report counts calls read only in part, and says so only when there are any", () => {
  const root = tempRoot();
  try {
    O.markTrackingStarted(root, new Date(T(0)));
    const dir = join(root, "x");
    O.appendAction(dir, act("p", [], { partial_read: true, seqs_read: ["other"] }));
    O.appendAction(dir, act("q", []));
    const o = O.originReport(dir, edits(), null);
    assert.equal(o.actions.partlyRead, 1);
    assert.match(O.originMarkdown(o), /read only in part 1/);
    assert.doesNotMatch(O.originMarkdown({ ...o, actions: { ...o.actions, partlyRead: 0 } }), /read only in part/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("the read limit: 350 ms by default, an env value is clamped, a bad one is ignored", () => {
  assert.equal(AI.readLimit(undefined), 350); assert.equal(AI.readLimit("abc"), 350); assert.equal(AI.readLimit(""), 350);
  assert.equal(AI.readLimit("10"), 50); assert.equal(AI.readLimit("99999"), 5000); assert.equal(AI.readLimit("700"), 700);
  if (!process.env.PREMIERE_BRIDGE_ORIGIN_READ_MS) assert.equal(AI.EVAL_READ_LIMIT_MS, 350);
});

// ---- the journal snapshot ---------------------------------------------------

test("a journal snapshot over its limit stops by itself, saves nothing and says where else to read", async () => {
  const P = clockPremiere([{ name: "S", id: "s", rows: rowsOf("s", 5000) }]);
  const t0 = P.clock.t;
  const res = P.run(J.snapshotJsx({ limitMs: 2000 }));
  assert.equal(res.error, "snapshot over the limit"); assert.equal(res.complete, false);
  assert.ok(res.rows > 0 && res.rows < 5000);
  assert.ok(P.clock.t - t0 < 2000 + 100, "stopped near its limit");
  const root = tempRoot();
  try {
    let timeout = null;
    const run = async (body, opts) => { timeout = opts.timeoutMs; return P.raw(body); };
    const e = await journalSnapshot(run, { dir: join(root, "j"), limitMs: 2000 }).catch((x) => x);
    assert.ok(e instanceof BridgeError);
    assert.match(e.message, /too big, or Premiere is too slow right now/);
    assert.match(e.message, /Nothing was saved/);
    assert.match(e.message, /node pk\.js journal import <file\.prproj>/);
    assert.ok(timeout > 2000, "the bridge waits longer than the script runs");
    assert.ok(!existsSync(join(root, "j")));
    await journalSnapshot(async (b, o) => { timeout = o.timeoutMs; return P.raw(b); }, { dir: join(root, "j"), limitMs: 120000 }).catch(() => {});
    assert.ok(timeout > 120000);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
