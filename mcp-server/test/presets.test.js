import { test } from "node:test";
import assert from "node:assert/strict";
import { exportPresetProblem } from "../lib/presets.js";

const APP = "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents";

test("the proxy preset that squashed 1080x1920 into 1280x720 is flagged", () => {
  const p = exportPresetProblem(`${APP}/Settings/IngestPresets/Proxy/02_H.264 MP4.epr`);
  assert.equal(p.kind, "proxy");
  assert.match(p.message, /Match Source/);
});

test("Match Source presets pass", () => {
  assert.equal(exportPresetProblem(`${APP}/MediaIO/systempresets/4E49434B_48323634/00 - Match Source - High bitrate.epr`), null);
});

test("not an .epr", () => {
  assert.equal(exportPresetProblem("/tmp/x.txt").kind, "not-epr");
});
