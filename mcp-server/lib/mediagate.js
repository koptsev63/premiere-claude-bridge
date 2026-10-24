// Media gate before an import: can Premiere decode this file, is there disk
// for a copy. Node twin of core/media_gate.py (same rules, same ffmpeg copy).
//
// 24.10.2026: an AV1 podcast from yt-dlp imported "successfully"
// (importFiles returned true) with sound only; ten sequences were built on
// an empty V1. The disk had 2 GB free; Premiere itself dies at ENOSPC and
// takes the panel port with it.

import { execFile, execFileSync } from "node:child_process";
import { statfsSync, existsSync } from "node:fs";
import { dirname, extname, basename, join, resolve } from "node:path";

// Video codecs Premiere Pro 26 does not decode (it then imports audio only).
export const UNREADABLE = {
  av1: "AV1 (yt-dlp's default for YouTube): Premiere 26 imports audio only",
  vp9: "VP9 / WebM: Premiere does not read it without a third-party plugin",
  vp8: "VP8 / WebM: Premiere does not read it without a third-party plugin",
  theora: "Theora: not readable by Premiere",
  ffv1: "FFV1: not readable by Premiere",
};

export const MIN_FREE_BYTES = 2 * 1024 ** 3; // below this nothing is imported or cut
export const COPY_RESERVE_BYTES = 5 * 1024 ** 3; // left after an H.264 copy
const COPY_BPS_1080 = 20_000_000; // libx264 CRF 16 at 1920x1080, rough

export function parseFps(s) {
  const [n, d] = String(s || "0/1").split("/");
  const den = Number(d || 1);
  return den ? Number(n) / den : 0;
}

export function readability({ codec, pixFmt }) {
  if (!codec) return { ok: true, reason: "no video stream" };
  if (UNREADABLE[codec]) return { ok: false, reason: UNREADABLE[codec] };
  const pf = String(pixFmt || "");
  if ((codec === "h264" || codec === "hevc") && /444/.test(pf)) return { ok: false, reason: `${codec} 4:4:4 (${pf}): Premiere decodes no video from it` };
  if (codec === "hevc" && /p12/.test(pf)) return { ok: false, reason: `HEVC 12-bit (${pf}): not readable by Premiere` };
  return { ok: true, reason: "" };
}

export function probeMedia(path) {
  const out = execFileSync("ffprobe", ["-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  return summarizeProbe(JSON.parse(out), path);
}

export function summarizeProbe(info, path) {
  const streams = info.streams || [];
  const v = streams.find((s) => s.codec_type === "video" && !(s.disposition || {}).attached_pic);
  const a = streams.find((s) => s.codec_type === "audio");
  return {
    path,
    codec: v ? v.codec_name : null,
    pixFmt: v ? v.pix_fmt : null,
    width: v ? Number(v.width || 0) : 0,
    height: v ? Number(v.height || 0) : 0,
    fps: v ? parseFps(v.avg_frame_rate || v.r_frame_rate) : 0,
    duration: Number((info.format || {}).duration || 0),
    hasAudio: !!a,
    audioCodec: a ? a.codec_name : null,
  };
}

export function freeBytes(dir) {
  try {
    const st = statfsSync(dir);
    return Number(st.bavail) * Number(st.bsize);
  } catch {
    const out = execFileSync("df", ["-k", dir], { encoding: "utf8" }).trim().split("\n").pop().split(/\s+/);
    return Number(out[3]) * 1024;
  }
}

// H.264 edit copy: software x264 CRF 16, a keyframe every second, AAC 320k.
export function editCopyCmd(src, dst, fps, start, end) {
  const gop = String(Math.max(1, Math.round(fps || 30)));
  const cmd = ["ffmpeg", "-hide_banner", "-y"];
  if (start != null) cmd.push("-ss", start.toFixed(3));
  if (end != null) cmd.push("-to", end.toFixed(3));
  cmd.push("-i", src, "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-g", gop,
    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", dst);
  return cmd;
}

export function copyPathFor(src, start, end, duration) {
  const root = src.slice(0, src.length - extname(src).length);
  const suffix = start == null && end == null ? "-h264" : `-${Math.round(start || 0)}-${Math.round(end == null ? duration : end)}s-h264`;
  return `${root}${suffix}.mp4`;
}

// The gate. `start`/`end` (seconds) keep the copy to the range that is needed.
export function gate(path, { start, end, copyPath, free } = {}) {
  const abs = resolve(path);
  if (!existsSync(abs)) return { path: abs, ok: false, reason: "file not found", fixCmd: null };
  const p = probeMedia(abs);
  const r = readability(p);
  const dir = dirname(abs);
  const freeNow = free != null ? free : freeBytes(dir);
  const out = { ...p, ok: r.ok, reason: r.reason, freeBytes: freeNow, diskOk: freeNow >= MIN_FREE_BYTES, fixCmd: null, copyPath: null, copyBytesEst: 0 };
  if (!out.diskOk) {
    out.ok = false;
    out.reason = (out.reason ? out.reason + "; " : "") + `only ${(freeNow / 1e9).toFixed(1)} GB free on ${dir}, need ${MIN_FREE_BYTES / 1024 ** 3} GB before any import (Premiere crashes at ENOSPC)`;
  }
  if (r.ok) return out;
  const span = Math.max(0, (end != null ? end : p.duration) - (start || 0));
  out.copyPath = copyPath || copyPathFor(abs, start, end, p.duration);
  out.copyBytesEst = Math.round((span * COPY_BPS_1080 / 8) * Math.max(p.width * p.height, 1) / (1920 * 1080));
  out.fixCmd = editCopyCmd(abs, out.copyPath, p.fps, start, end);
  out.copyDiskOk = freeNow - out.copyBytesEst >= COPY_RESERVE_BYTES;
  out.reason += `. Make an H.264 copy (~${(out.copyBytesEst / 1e9).toFixed(2)} GB)`;
  if (!out.copyDiskOk) out.reason += `; NOT ENOUGH DISK for it: ${(freeNow / 1e9).toFixed(1)} GB free, need the copy plus ${COPY_RESERVE_BYTES / 1024 ** 3} GB reserve. Cut only the needed range (start/end) or free space first`;
  return out;
}

export function shellLine(cmd) {
  return cmd.map((c) => (/[^A-Za-z0-9_@%+=:,.\/-]/.test(c) ? `'${c.replace(/'/g, "'\\''")}'` : c)).join(" ");
}

// Run the fix command (ffmpeg). Resolves with the copy path.
export function runFix(cmd, { log } = {}) {
  return new Promise((resolve, reject) => {
    const [bin, ...args] = cmd;
    execFile(bin, args, { maxBuffer: 64 * 1024 * 1024 }, (e, _stdout, stderr) => {
      if (e) return reject(new Error(`${bin} failed: ${String(stderr || e.message).trim().split("\n").slice(-3).join(" | ")}`));
      if (log) log(`ffmpeg done: ${basename(cmd[cmd.length - 1])}`);
      resolve(cmd[cmd.length - 1]);
    });
  });
}

export { join };
