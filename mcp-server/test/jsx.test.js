import { test } from "node:test";
import assert from "node:assert/strict";
import { importOneCaptionJsx, captionTrackJsx, listVideoEffectsJsx, addVideoEffectJsx } from "../lib/jsx.js";
import { hasTopLevelReturn } from "../lib/evalcode.js";

const bodies = {
  importOne: importOneCaptionJsx({ path: "/Users/x/Субтитры/ru \"1\".srt", bin: "Субтитры", reuse: true }),
  captionTrack: captionTrackJsx({ nodeId: "000f4240", startSeconds: 1.5, format: "subtitle" }),
  list: listVideoEffectsJsx(),
  addEffect: addVideoEffectJsx({ name: "Черное и белое", mode: "index", track: 0, clip: 2 }),
};

for (const [name, body] of Object.entries(bodies)) {
  test(`${name}: parses, ASCII only, ES3, explicit return`, () => {
    assert.doesNotThrow(() => new Function(body));
    assert.match(body, /^[\x09\x0a\x0d\x20-\x7e]*$/, "non-ASCII must be \\u-escaped");
    assert.doesNotMatch(body, /=>|\blet\b|\bconst\b|`/, "ES3 only");
    assert.equal(hasTopLevelReturn(body), true);
  });
}

test("captions import one file and allow the UI (suppressUI=false)", () => {
  assert.match(bodies.importOne, /importFiles\(\[want\], false, target, false\)/);
});

test("unknown caption format is refused before Premiere", () => {
  assert.throws(() => captionTrackJsx({ nodeId: "1", startSeconds: 0, format: "nope" }));
});

test("effect add counts components before and after", () => {
  assert.match(bodies.addEffect, /before = dom\.components\.numItems/);
  assert.match(bodies.addEffect, /r\.added = r\.after > before/);
});

test("sequence tools refuse a sequence from another open project", () => {
  for (const b of [bodies.captionTrack, bodies.addEffect]) {
    assert.match(b, /var foreign = __foreignSequence\(seq\);\s*if \(foreign\) return foreign;/);
  }
});
