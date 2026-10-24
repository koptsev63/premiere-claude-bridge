import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import * as J from "../lib/seq.js";
import { hasTopLevelReturn } from "../lib/evalcode.js";

const bodies = {
  ping: J.pingJsx(),
  listBin: J.listBinJsx({ bin: "ПОДКАСТ шортсы", recursive: true }),
  importMedia: J.importMediaJsx({ path: "/Users/x/Курс ИИ/a \"1\".mp4", bin: "A/B", reuse: true }),
  makeSequence: J.makeSequenceJsx({ name: "ПОДКАСТ 01 - тест (34 с)", item: "000f4244", bin: "ПОДКАСТ шортсы", inSec: 10, outSec: 43.653, width: 1080, height: 1920, fps: 29.97, exists: "replace" }),
  setMotion: J.setMotionJsx({ seq: "ПОДКАСТ 01", track: 0, clip: 0, scale: 177.778, posX: 735, posY: 960, units: "px" }),
  keyframes: J.setMotionKeyframesJsx({ seq: "s", keys: [{ t: 0, posX: 200, scale: 100 }, { t: 2.5, posX: 880 }], hold: true }),
  markers: J.addMarkersJsx({ seq: "s", markers: [{ t: 0, name: "КРОП", comment: "x y", duration: 2 }], clear: true }),
  listMarkers: J.listMarkersJsx({ seq: "s" }),
  open: J.openSequencesJsx({ names: ["ПОДКАСТ 01", "ПОДКАСТ 02"], activate: "ПОДКАСТ 01" }),
  timeline: J.getTimelineJsx({ seq: "s", withMarkers: true }),
  del: J.deleteSequenceJsx({ name: "s" }),
  move: J.moveToBinJsx({ item: "s", bin: "A/B" }),
  save: J.saveProjectJsx(),
};

for (const [name, body] of Object.entries(bodies)) {
  test(`${name}: parses, ASCII only, ES3, explicit return`, () => {
    assert.doesNotThrow(() => new Function(body));
    assert.match(body, /^[\x09\x0a\x0d\x20-\x7e]*$/, "non-ASCII must be \\u-escaped");
    assert.doesNotMatch(body, /=>|\blet\b|\bconst\b|`/, "ES3 only");
    assert.equal(hasTopLevelReturn(body), true);
  });
}

// A small fake of the Premiere DOM, enough to run the shared helpers.
function fakeApp() {
  const BIN = 2, CLIP = 1;
  let n = 0;
  const item = (name, type, extra = {}) => ({ name, type, nodeId: "000f42" + String(++n).padStart(2, "0"), treePath: "\\p\\" + name, children: type === BIN ? list([]) : undefined, ...extra });
  const list = (arr) => { const o = { numItems: arr.length }; arr.forEach((x, i) => (o[i] = x)); return o; };
  const root = item("root", BIN);
  const clipA = item("a.mp4", CLIP, { getMediaPath: () => "/x/a.mp4" });
  const sub = item("B", BIN);
  sub.children = list([clipA]);
  const top = item("A", BIN);
  top.children = list([sub]);
  top.createBin = (n) => { const b = item(n, BIN); top.children = list([sub, b]); };
  root.children = list([top]);
  const seqs = { numSequences: 1, 0: { name: "S", sequenceID: "sid" } };
  return { app: { project: { rootItem: root, sequences: seqs, activeSequence: seqs[0] } }, ProjectItemType: { BIN, CLIP, FILE: 3 }, File: function (p) { this.fsName = p; }, Time: function () {} };
}

function runLib(code) {
  const ctx = fakeApp();
  vm.createContext(ctx);
  return vm.runInContext(J.LIB + "\n" + code, ctx);
}

test("fps to ticks: NTSC rates exact, integer rates by division", () => {
  assert.equal(runLib("__fpsTicks(29.97)"), "8475667200");
  assert.equal(runLib("__fpsTicks(23.976)"), "10594584000");
  assert.equal(runLib("__fpsTicks(59.94)"), "4237833600");
  assert.equal(runLib("__fpsTicks(25)"), "10160640000");
  assert.equal(runLib("__fpsTicks(30)"), "8467200000");
  assert.equal(runLib("__fpsTicks(24)"), "10584000000");
  assert.ok(Math.abs(runLib("__ticksFps('8475667200')") - 29.97003) < 1e-4);
});

test("bin paths: found, created, or null", () => {
  assert.equal(runLib("__binPath('A/B', false).name"), "B");
  assert.equal(runLib("__binPath('A/C', false)"), null);
  assert.equal(runLib("__binPath('A/C', true).name"), "C");
  assert.equal(runLib("__binPath('', false).name"), "root");
});

test("items by nodeId, absolute path, name; sequences by id or name", () => {
  assert.equal(runLib("__item('000f4202').name"), "a.mp4");
  assert.equal(runLib("__item('/x/a.mp4').name"), "a.mp4");
  assert.equal(runLib("__item('a.mp4').name"), "a.mp4");
  assert.equal(runLib("__item('/elsewhere/a.mp4').name"), "a.mp4", "falls back to the basename");
  assert.equal(runLib("__item('nope')"), null);
  assert.equal(runLib("__seq('sid').name"), "S");
  assert.equal(runLib("__seq('S').sequenceID"), "sid");
  assert.equal(runLib("__seq('').name"), "S", "empty ref = active sequence");
});

test("motion names cover the Russian and English UI", () => {
  assert.deepEqual(J.NAMES.motion, ["Движение", "Motion"]);
  assert.deepEqual(J.NAMES.position, ["Положение", "Position"]);
  assert.match(bodies.setMotion, /NAMES\.motion/);
});

test("make_sequence never calls createNewSequence with a preset (the dialog path)", () => {
  assert.doesNotMatch(bodies.makeSequence, /createNewSequence\(/);
  assert.match(bodies.makeSequence, /createNewSequenceFromClips\(NAME, \[item\], binItem\)/);
  assert.match(bodies.makeSequence, /setInPoint\(IN, 4\)/);
  assert.match(bodies.makeSequence, /seq\.setSettings\(st\)/);
  assert.match(bodies.makeSequence, /EXISTS === "skip"/);
});

test("keyframes: placed in media time (inPoint + t), hold only when asked, old keys cleared by default", () => {
  assert.match(bodies.keyframes, /var inP = __sec\(cl\.inPoint\) \|\| 0/);
  assert.match(bodies.keyframes, /var t = inP \+ tClip;/);
  assert.match(bodies.keyframes, /t: __sec\(ks\[i\]\) - inP/);
  assert.match(bodies.keyframes, /var HOLD = true, CLEAR = true/);
  assert.match(bodies.keyframes, /setInterpolationTypeAtKey\(t, 4, 1\)/);
  const noHold = J.setMotionKeyframesJsx({ seq: "s", keys: [{ t: 0, scale: 1 }], clear: false });
  assert.match(noHold, /var HOLD = false, CLEAR = false/);
});

test("markers: duration becomes end = t + duration, count verified", () => {
  assert.match(bodies.markers, /mk\.end = M\[k\]\.t \+ M\[k\]\.duration/);
  assert.match(bodies.markers, /after - before !== M\.length/);
  assert.match(bodies.markers, /\\u041a\\u0420\\u041e\\u041f/, "Cyrillic marker name escaped");
  assert.match(bodies.markers, /\\u2028/, "U+2028 escaped");
});

test("import of media is one file with the UI suppressed", () => {
  assert.match(bodies.importMedia, /importFiles\(\[want\], true, target, false\)/);
});
