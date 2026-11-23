import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PanelQueue } from "../lib/panelqueue.js";

test("a timed-out request keeps the panel busy until its late answer arrives", () => {
  const q = new PanelQueue();
  assert.equal(q.busy(), null);
  assert.doesNotThrow(() => q.check());
  q.timedOut("r1", "evalRaw", 1000);
  const b = q.busy(43_000);
  assert.deepEqual([b.count, b.fn, b.ageSec], [1, "evalRaw", 42]);
  assert.throws(() => q.check({}, 43_000), (e) => e.code === "PANEL_BUSY" && /still running a script the bridge sent 42 s ago and gave up on \(evalRaw\)\. Nothing new was sent\./.test(e.message) && /pr_dialog_check/.test(e.message));
  assert.equal(q.answered("other"), false, "an unknown late id changes nothing");
  assert.ok(q.busy());
  assert.equal(q.answered("r1"), true);
  assert.equal(q.busy(), null);
});

test("a socket that closes or is replaced clears the memory; a probe always passes", () => {
  const q = new PanelQueue();
  q.timedOut("r1", "evalRaw", 1000); q.timedOut("r2", "getStatus", 2000);
  assert.doesNotThrow(() => q.check({ probe: true }));
  assert.equal(q.busy(3000).fn, "evalRaw", "the oldest one is named");
  q.reset();
  assert.equal(q.busy(), null);
});

test("server.js uses the queue: refuses before sending, remembers on timeout, forgets on a late answer or a new socket", () => {
  const src = readFileSync(new URL("../server.js", import.meta.url), "utf8");
  const callPanel = src.slice(src.indexOf("async function callPanel"), src.indexOf("function parseResult"));
  assert.ok(callPanel.indexOf("abandoned.check({ probe })") < callPanel.indexOf("ensurePanel()"));
  assert.match(callPanel, /pending\.delete\(id\);\s*abandoned\.timedOut\(id, fn, sentAt, seq\);/);
  assert.match(src, /abandoned\.answeredAfter\(p\.seq\);/);
  assert.match(src, /isBusy: \(\) => activeTools > 0 \|\| pending\.size > 0 \|\| !!abandoned\.busy\(\)/);
  assert.match(src, /if \(!p\) \{ abandoned\.answered\(msg\.id\); return; \}/);
  assert.match(src, /panelSocket = null; abandoned\.reset\(\);/);
  assert.match(src, /panelSocket\.close\(\); \} catch \{\}\s*abandoned\.reset\(\);/);
  const ops = readFileSync(new URL("../lib/ops.js", import.meta.url), "utf8");
  assert.match(ops, /export async function dialogCheck[\s\S]*?timeoutMs: 3000, probe: true/);
});

test("an answer to a later request proves the earlier abandoned ones have finished", () => {
  const q = new PanelQueue();
  q.timedOut("r1", "evalRaw", 1000, 1); q.timedOut("r2", "evalRaw", 2000, 2); q.timedOut("r5", "evalRaw", 5000, 5);
  q.answeredAfter(3); // request 3 ran: 1 and 2 are done even though their late answers never came
  assert.deepEqual([q.busy(6000).count, q.busy(6000).since], [1, 5000]);
  q.answeredAfter(4);
  assert.ok(q.busy(), "a request sent before the abandoned one says nothing about it");
  q.timedOut("r3", "getStatus", 3000, 3);
  assert.equal(q.answered("r5"), true, "its own late answer clears it and everything sent before it");
  assert.equal(q.busy(), null);
});
