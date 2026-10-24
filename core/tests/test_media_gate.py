"""Tests for the media gate. Builds tiny AV1 / VP9 / H.264 files with ffmpeg.

Run:  python -m core.tests.test_media_gate
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from core.media_gate import DISK_RESERVE_BYTES, check_for_premiere, edit_copy_cmd

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def make(path: str, vcodec: list[str], acodec: str = "aac") -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30000/1001:duration=1",
                    "-f", "lavfi", "-i", "sine=duration=1", *vcodec, "-c:a", acodec, "-shortest", path], check=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        av1, vp9, h264 = (os.path.join(d, n) for n in ("a.mp4", "v.webm", "h.mp4"))
        make(av1, ["-c:v", "libsvtav1"])
        make(vp9, ["-c:v", "libvpx-vp9"], "libopus")
        make(h264, ["-c:v", "libx264"])

        r = check_for_premiere(h264)
        check("h264 passes", r.ok and r.video_codec == "h264", str(r))

        r = check_for_premiere(av1, free_bytes=100 * 1024**3)
        check("av1 is refused (the 24.10.2026 podcast)", not r.ok and r.video_codec == "av1", str(r))
        check("av1 reason names Premiere and audio-only", "audio only" in r.reason)
        check("fix is a software x264 copy", "libx264" in r.fix_cmd and "h264_videotoolbox" not in r.fix_cmd)
        check("keyframe every second at 29.97", r.fix_cmd[r.fix_cmd.index("-g") + 1] == "30")
        check("copy goes next to the source as -h264.mp4", r.copy_path.endswith("a-h264.mp4"))
        check("plenty of disk -> disk_ok", r.disk_ok)
        try:
            r.assert_ok()
            check("assert_ok raises on av1", False)
        except RuntimeError:
            check("assert_ok raises on av1", True)

        r = check_for_premiere(vp9, free_bytes=100 * 1024**3)
        check("vp9 webm is refused", not r.ok and r.video_codec == "vp9", str(r))

        # The real case: 65 min 1080p with 2 GB free.
        full = check_for_premiere(av1, free_bytes=2 * 1024**3)
        check("2 GB free is not enough even for a tiny copy plus reserve", not full.disk_ok and "NOT ENOUGH DISK" in full.reason)

        part = check_for_premiere(av1, start=0.2, end=0.8, free_bytes=DISK_RESERVE_BYTES + 10**9)
        check("range copy is named by its range", part.copy_path.endswith("a-0-1s-h264.mp4"), part.copy_path)
        check("range copy cuts with -ss/-to", "-ss" in part.fix_cmd and "-to" in part.fix_cmd)
        check("range copy fits", part.disk_ok, str(part))

        out = os.path.join(d, "out.mp4")
        subprocess.run(edit_copy_cmd(av1, out, 29.97)[:-1] + ["-loglevel", "error", out], check=True)
        check("the fix command produces a file Premiere reads", check_for_premiere(out).video_codec == "h264")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
