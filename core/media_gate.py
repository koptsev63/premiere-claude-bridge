"""Media gate - can Premiere read this file, and is there room for a copy?

24.10.2026: a podcast downloaded with yt-dlp came as AV1 (1920x1080, 65 min).
Premiere 26.0.2 imported its AUDIO ONLY and showed "Не удается импортировать
видео ... сжатие видео неподдерживаемого типа «av01»"; ten short sequences were
built on it with an empty V1. The disk had 2 GB free at the time, so a full
copy of the source would not have fit either.

    from core.media_gate import check_for_premiere
    r = check_for_premiere("talk.mp4")
    if not r.ok:
        print(r.reason); print(" ".join(r.fix_cmd))   # H.264 edit copy
    r.assert_ok()

Cut only the range you need (start/end): one short's source with 10 s
handles is ~60 MB, the whole hour is several GB.

CLI: python -m core.media_gate FILE [--start S --end E]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

# Video codecs Premiere Pro 26 does not decode (it then imports audio only).
PREMIERE_UNREADABLE_VIDEO = {
    "av1": "AV1 (yt-dlp's default for YouTube): Premiere 26 imports audio only (checked 24.10.2026)",
    "vp9": "VP9 / WebM: not readable by Premiere without a third-party plugin",
    "vp8": "VP8 / WebM: not readable by Premiere without a third-party plugin",
}

# Rough size of the libx264 CRF 16 edit copy, bits per second at 1920x1080.
_COPY_BPS_1080 = 20_000_000
# Room left on the disk after the copy: Premiere autosaves and caches need it.
DISK_RESERVE_BYTES = 5 * 1024**3


@dataclass
class GateResult:
    path: str
    ok: bool
    video_codec: str | None
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    reason: str = ""
    fix_cmd: list[str] = field(default_factory=list)
    copy_path: str = ""
    copy_bytes_est: int = 0
    free_bytes: int = 0
    disk_ok: bool = True

    def assert_ok(self) -> None:
        if not self.ok:
            raise RuntimeError(f"{self.path}: {self.reason}")


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _fps(stream: dict) -> float:
    num, _, den = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def edit_copy_cmd(src: str, dst: str, fps: float, start: float | None = None, end: float | None = None) -> list[str]:
    """ffmpeg command for an edit-friendly H.264 copy: software x264, CRF 16,
    one keyframe per second (smooth scrubbing), 8-bit 4:2:0, AAC 320k."""
    gop = str(max(1, round(fps or 30)))
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", src, "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-g", gop, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", dst]
    return cmd


def check_for_premiere(path: str, start: float | None = None, end: float | None = None,
                       copy_path: str | None = None, free_bytes: int | None = None) -> GateResult:
    info = probe(path)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    duration = float(info.get("format", {}).get("duration") or 0)
    if video is None:
        return GateResult(path, True, None, duration=duration, reason="no video stream")

    codec = video.get("codec_name", "")
    w, h, fps = int(video.get("width") or 0), int(video.get("height") or 0), _fps(video)
    r = GateResult(path, True, codec, w, h, fps, duration)
    if codec not in PREMIERE_UNREADABLE_VIDEO:
        return r

    root, _ = os.path.splitext(path)
    suffix = "-h264" if start is None and end is None else f"-{start or 0:.0f}-{end or duration:.0f}s-h264"
    r.copy_path = copy_path or f"{root}{suffix}.mp4"
    r.fix_cmd = edit_copy_cmd(path, r.copy_path, fps, start, end)
    span = (end if end is not None else duration) - (start or 0)
    r.copy_bytes_est = int(max(span, 0) * _COPY_BPS_1080 / 8 * max(w * h, 1) / (1920 * 1080))
    target_dir = os.path.dirname(os.path.abspath(r.copy_path))
    r.free_bytes = free_bytes if free_bytes is not None else shutil.disk_usage(target_dir).free
    r.disk_ok = r.free_bytes - r.copy_bytes_est >= DISK_RESERVE_BYTES
    r.ok = False
    r.reason = PREMIERE_UNREADABLE_VIDEO[codec] + f". Make an H.264 copy (~{r.copy_bytes_est / 1e9:.1f} GB)"
    if not r.disk_ok:
        r.reason += (f"; NOT ENOUGH DISK: {r.free_bytes / 1e9:.1f} GB free, need the copy plus "
                     f"{DISK_RESERVE_BYTES / 1024**3:.0f} GB reserve. Cut only the needed range (start/end) "
                     f"or free space first")
    return r


def _main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    a = ap.parse_args(argv)
    r = check_for_premiere(a.file, a.start, a.end)
    print(f"{'OK' if r.ok else 'NO'}  {r.video_codec} {r.width}x{r.height} {r.fps:.3f} fps {r.duration:.1f} s")
    if not r.ok:
        print(r.reason)
        print(" ".join(f"'{c}'" if " " in c else c for c in r.fix_cmd))
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
