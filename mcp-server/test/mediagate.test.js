import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { readability, summarizeProbe, editCopyCmd, copyPathFor, gate, shellLine, parseFps, MIN_FREE_BYTES } from "../lib/mediagate.js";

test("readability: AV1, VP9, VP8, 4:4:4 refused; H.264 / ProRes / HEVC 10-bit 4:2:0 fine", () => {
  assert.equal(readability({ codec: "av1", pixFmt: "yuv420p" }).ok, false);
  assert.equal(readability({ codec: "vp9", pixFmt: "yuv420p" }).ok, false);
  assert.equal(readability({ codec: "vp8" }).ok, false);
  assert.equal(readability({ codec: "h264", pixFmt: "yuv444p" }).ok, false);
  assert.equal(readability({ codec: "hevc", pixFmt: "yuv420p12le" }).ok, false);
  assert.equal(readability({ codec: "h264", pixFmt: "yuv420p" }).ok, true);
  assert.equal(readability({ codec: "hevc", pixFmt: "yuv420p10le" }).ok, true);
  assert.equal(readability({ codec: "prores", pixFmt: "yuv422p10le" }).ok, true);
  assert.equal(readability({ codec: null }).ok, true, "audio-only file passes");
});

test("summarizeProbe reads the first real video stream, not the cover art", () => {
  const info = { streams: [
    { codec_type: "video", codec_name: "mjpeg", disposition: { attached_pic: 1 } },
    { codec_type: "video", codec_name: "av1", pix_fmt: "yuv420p", width: 1920, height: 1080, avg_frame_rate: "30000/1001" },
    { codec_type: "audio", codec_name: "aac" } ], format: { duration: "3897.33" } };
  const p = summarizeProbe(info, "/x.mp4");
  assert.equal(p.codec, "av1");
  assert.ok(Math.abs(p.fps - 29.97) < 0.001);
  assert.equal(p.hasAudio, true);
  assert.equal(parseFps("0/0"), 0);
});

test("edit copy command: range, x264 CRF 16, keyframe every second, AAC", () => {
  const cmd = editCopyCmd("/a b.mp4", "/a b-h264.mp4", 29.97, 1396.4, 1450);
  assert.deepEqual(cmd.slice(0, 7), ["ffmpeg", "-hide_banner", "-y", "-ss", "1396.400", "-to", "1450.000"]);
  assert.ok(cmd.includes("libx264") && cmd.includes("-g") && cmd[cmd.indexOf("-g") + 1] === "30");
  assert.equal(copyPathFor("/x/talk.mp4", 1396.4, 1450, 3897), "/x/talk-1396-1450s-h264.mp4");
  assert.equal(copyPathFor("/x/talk.mp4", null, null, 3897), "/x/talk-h264.mp4");
  assert.match(shellLine(cmd), /'\/a b.mp4'/);
});

const ffmpeg = (() => { try { execFileSync("ffmpeg", ["-version"], { stdio: "ignore" }); return true; } catch { return false; } })();

test("gate on real files: H.264 passes, VP9 is refused with a copy command, low disk refuses everything", { skip: !ffmpeg && "ffmpeg missing" }, () => {
  const dir = mkdtempSync(join(tmpdir(), "gate-"));
  try {
    const h264 = join(dir, "ok.mp4"), vp9 = join(dir, "web.webm");
    execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", h264]);
    execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=0.5", "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", vp9]);
    const a = gate(h264);
    assert.equal(a.ok, true); assert.equal(a.codec, "h264"); assert.equal(a.fixCmd, null);
    const b = gate(vp9, { start: 0.1, end: 0.4 });
    assert.equal(b.ok, false); assert.equal(b.codec, "vp9");
    assert.match(b.reason, /VP9/);
    assert.equal(b.copyPath, join(dir, "web-0-0s-h264.mp4"));
    assert.equal(b.fixCmd[0], "ffmpeg");
    const c = gate(h264, { free: MIN_FREE_BYTES - 1 });
    assert.equal(c.ok, false); assert.match(c.reason, /GB free/);
    const d = gate(join(dir, "missing.mp4"));
    assert.equal(d.ok, false); assert.match(d.reason, /not found/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
