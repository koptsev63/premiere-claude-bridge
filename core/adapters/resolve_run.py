"""Resolve runner - eyes on the timeline and a script hatch, from a shell.

`ResolveAdapter` speaks the cutlist verb set and nothing else. Real jobs
keep needing two things it does not have:

- **Eyes.** An agent cannot watch a timeline. It can ask Resolve for frame
  N of the *composited* timeline as a PNG and read that. `grab` goes
  through the Deliver page (every track, every grade - the reference);
  `still` goes through the Gallery and takes seconds instead of a render
  (on a two-layer alpha composite the two agreed to PSNR 52.8 dB).
- **An escape hatch.** `exec` runs your Python with `resolve`, `pm`,
  `project`, `timeline`, `mp`, `fps` and `frame_to_tc` already in scope -
  the Resolve counterpart of the bridge's `pr_eval_jsx`.

```bash
python -m core.adapters.resolve_run info
python -m core.adapters.resolve_run timelines
python -m core.adapters.resolve_run open "Rough v2"
python -m core.adapters.resolve_run dump [--json]
python -m core.adapters.resolve_run still 0 120 480 [--out DIR]
python -m core.adapters.resolve_run grab  0 120 480 [--out DIR]
python -m core.adapters.resolve_run exec script.py      # or: exec -  (stdin)
```

Frame numbers are 0-based from the first frame of the timeline, not
Resolve's absolute frames (a timeline starting at 01:00:00:00 begins at
86400 @ 24 fps; the runner adds that for you).

Honest boundaries:

- Resolve **Studio**, running, project open, external scripting = Local;
  interpreter CPython 3.9-3.13 (see `core/adapters/resolve.py`).
- `grab` uses the project's render queue. It removes only the jobs it
  added, but it does leave the render format set to PNG - reload your
  delivery preset before the next real render.
- `still` reads what the viewer shows at the playhead, so it moves the
  playhead. After a long run of timeline duplicate/delete operations
  `ExportStills` can start returning nothing until Resolve is restarted;
  the runner notices the missing files and falls back to `grab` for them.
- `frame_to_tc` is non-drop-frame at the rounded rate. On a 29.97 DF
  timeline the label drifts from Resolve's; frame *numbers* stay exact.
- `exec` runs whatever you give it with full API access. It is your script.

Everything below takes an explicit `Ctx`, so the logic is unit-tested
against stubs; only `connect()` touches a live Resolve.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.adapters.resolve import ResolveUnavailable, _load_resolve

#: Clip transform properties worth seeing in a dump.
PROPS = ("ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle", "Opacity",
         "CropLeft", "CropRight", "CropTop", "CropBottom")
_CROPS = ("CropLeft", "CropRight", "CropTop", "CropBottom")


class ResolveRunError(RuntimeError):
    pass


@dataclass
class Ctx:
    """The handles every command needs, preloaded once."""

    resolve: Any
    pm: Any
    project: Any
    timeline: Any
    mp: Any

    @property
    def fps(self) -> float:
        return fps_of(self.project)

    def need_timeline(self) -> Any:
        if self.timeline is None:
            raise ResolveRunError("no current timeline - open one first")
        return self.timeline


def connect() -> Ctx:
    resolve = _load_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        raise ResolveUnavailable("Resolve is running but no project is open")
    return Ctx(resolve, pm, project, project.GetCurrentTimeline(),
               project.GetMediaPool())


def fps_of(project: Any, default: float = 25.0) -> float:
    try:
        return float(project.GetSetting("timelineFrameRate"))
    except Exception:  # noqa: BLE001 - a stub or an odd setting string
        return default


def frame_to_tc(frame: int, fps: float) -> str:
    """Non-drop-frame timecode at the rounded rate."""
    base = max(1, int(round(fps)))
    f = int(frame)
    h, rem = divmod(f, base * 3600)
    m, rem = divmod(rem, base * 60)
    s, ff = divmod(rem, base)
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def default_out_dir() -> Path:
    return Path(tempfile.gettempdir()) / "resolve_run_frames"


def _tag(prefix: str, timeline: Any) -> str:
    name = "".join(c if c.isalnum() else "_" for c in timeline.GetName())
    return f"{prefix}_{name[:24]}"


def _clear(out_dir: Path, tag: str) -> None:
    for f in out_dir.glob(tag + "*"):
        f.unlink()


# ---- read-only commands ------------------------------------------------ #

def info(ctx: Ctx) -> dict[str, Any]:
    p, t = ctx.project, ctx.timeline
    return {
        "resolve": ctx.resolve.GetVersionString(),
        "page": ctx.resolve.GetCurrentPage(),
        "project": p.GetName(),
        "fps": ctx.fps,
        "resolution": "%sx%s" % (p.GetSetting("timelineResolutionWidth"),
                                 p.GetSetting("timelineResolutionHeight")),
        "timeline": t.GetName() if t else None,
        "timelineFrames": (t.GetEndFrame() - t.GetStartFrame()) if t else None,
        "timelineCount": p.GetTimelineCount(),
    }


def timelines(ctx: Ctx) -> list[dict[str, Any]]:
    cur = ctx.timeline.GetName() if ctx.timeline else None
    out = []
    for i in range(1, ctx.project.GetTimelineCount() + 1):
        tl = ctx.project.GetTimelineByIndex(i)
        out.append({"name": tl.GetName(),
                    "frames": tl.GetEndFrame() - tl.GetStartFrame(),
                    "current": tl.GetName() == cur})
    return out


def open_timeline(ctx: Ctx, name: str) -> None:
    for i in range(1, ctx.project.GetTimelineCount() + 1):
        tl = ctx.project.GetTimelineByIndex(i)
        if tl.GetName() == name:
            ctx.project.SetCurrentTimeline(tl)
            ctx.timeline = tl
            return
    raise ResolveRunError(f"no timeline named {name!r}")


def dump(ctx: Ctx) -> dict[str, Any]:
    """Every clip on every track, in timeline-relative frames."""
    t = ctx.need_timeline()
    start = t.GetStartFrame()
    data: dict[str, Any] = {"timeline": t.GetName(), "startFrame": start,
                            "endFrame": t.GetEndFrame(), "tracks": []}
    for kind in ("video", "audio"):
        for idx in range(1, t.GetTrackCount(kind) + 1):
            clips = []
            for it in t.GetItemListInTrack(kind, idx) or []:
                c: dict[str, Any] = {
                    "name": it.GetName(),
                    "start": it.GetStart() - start,
                    "end": it.GetEnd() - start,
                    "duration": it.GetDuration(),
                    "srcIn": it.GetLeftOffset(),
                }
                if kind == "video":
                    props = {}
                    for k in PROPS:
                        try:
                            v = it.GetProperty(k)
                        except Exception:  # noqa: BLE001
                            v = None
                        if v is not None:
                            props[k] = v
                    c["props"] = props
                clips.append(c)
            data["tracks"].append({
                "kind": kind, "index": idx,
                "name": t.GetTrackName(kind, idx),
                "enabled": t.GetIsTrackEnabled(kind, idx),
                "clips": clips,
            })
    return data


def gaps(data: dict[str, Any], kind: str = "video", index: int = 1
         ) -> list[tuple[int, int]]:
    """Holes on one track of a `dump()` - (from_frame, to_frame) pairs.

    A one-frame hole between two clips is invisible in the UI at normal
    zoom and very visible on screen. Zero tolerance: clip N+1 must start
    on the frame clip N ended.
    """
    for tr in data["tracks"]:
        if tr["kind"] == kind and tr["index"] == index:
            clips = sorted(tr["clips"], key=lambda c: c["start"])
            return [(a["end"], b["start"]) for a, b in zip(clips, clips[1:])
                    if b["start"] != a["end"]]
    raise ResolveRunError(f"no {kind} track {index}")


def format_dump(data: dict[str, Any]) -> str:
    lines = ["=== %s  %d frames" % (data["timeline"],
                                    data["endFrame"] - data["startFrame"])]
    for tr in data["tracks"]:
        lines.append("%s%d (%s) %d clips" % (tr["kind"][0].upper(),
                                             tr["index"], tr["name"],
                                             len(tr["clips"])))
        for c in tr["clips"]:
            shown = {k: v for k, v in (c.get("props") or {}).items()
                     if k not in _CROPS or float(v or 0) != 0}
            extra = "  " + " ".join(f"{k}={v}" for k, v in shown.items()) \
                if shown else ""
            lines.append("   %6d-%-6d %-34s src@%-6d%s" % (
                c["start"], c["end"], c["name"][:34], c["srcIn"], extra))
    return "\n".join(lines)


# ---- eyes --------------------------------------------------------------- #

def grab(ctx: Ctx, frames: list[int], out_dir: str | Path | None = None,
         timeout_s: float = 180.0, poll_s: float = 0.4) -> list[str]:
    """Render timeline frames to PNG through Deliver (full composite)."""
    t = ctx.need_timeline()
    if not frames:
        raise ResolveRunError("give at least one frame number")
    p = ctx.project
    out = Path(out_dir) if out_dir else default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    tag = _tag("rk", t)
    _clear(out, tag)
    start = t.GetStartFrame()
    jobs: list[Any] = []
    try:
        for fr in frames:
            ok = p.SetRenderSettings({
                "SelectAllFrames": False,
                "MarkIn": start + fr,
                "MarkOut": start + fr,
                "TargetDir": str(out),
                "CustomName": f"{tag}_f{fr}",
                "ExportVideo": True,
                "ExportAudio": False,
                "FormatWidth": int(p.GetSetting("timelineResolutionWidth")),
                "FormatHeight": int(p.GetSetting("timelineResolutionHeight")),
            })
            if not ok:
                raise ResolveRunError(
                    f"SetRenderSettings refused frame {fr}")
            p.SetCurrentRenderFormatAndCodec("png", "RGB8")
            jobs.append(p.AddRenderJob())
        # Only our own jobs: the user's queue is not ours to start or clear.
        p.StartRendering(jobs, False)
        t0 = time.time()
        while p.IsRenderingInProgress() and time.time() - t0 < timeout_s:
            time.sleep(poll_s)
        files = sorted(str(f) for f in out.glob(tag + "*"))
        if not files:
            status = [p.GetRenderJobStatus(j) for j in jobs]
            raise ResolveRunError(f"render produced no files: {status}")
        return files
    finally:
        for j in jobs:
            try:
                p.DeleteRenderJob(j)
            except Exception:  # noqa: BLE001
                pass


def still(ctx: Ctx, frames: list[int], out_dir: str | Path | None = None
          ) -> list[str]:
    """Fast eyes: Gallery still of the viewer at each frame.

    Falls back to `grab` for any frame the Gallery did not export.
    """
    t = ctx.need_timeline()
    if not frames:
        raise ResolveRunError("give at least one frame number")
    out = Path(out_dir) if out_dir else default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    tag = _tag("rs", t)
    _clear(out, tag)
    start, fps = t.GetStartFrame(), ctx.fps
    album = ctx.project.GetGallery().GetCurrentStillAlbum()
    taken, done, missing = [], [], []
    for fr in frames:
        t.SetCurrentTimecode(frame_to_tc(start + fr, fps))
        s = t.GrabStill()
        prefix = f"{tag}_f{fr}"
        dst = out / f"{prefix}.png"
        if s:
            album.ExportStills([s], str(out), prefix, "png")
            taken.append(s)
            # The Gallery writes prefix_N.M.K.png plus a .drx sidecar.
            for f in sorted(out.glob(prefix + "*")):
                if f == dst:
                    continue
                if not (f.name.startswith(prefix + "_")
                        or f.name.startswith(prefix + ".")):
                    continue        # f10 must not swallow f100
                if f.suffix == ".png" and not dst.exists():
                    f.replace(dst)
                else:
                    f.unlink()
        if dst.exists():
            done.append(str(dst))
        else:
            missing.append(fr)
    if taken:
        try:
            album.DeleteStills(taken)
        except Exception:  # noqa: BLE001
            pass
    if missing:
        print(f"gallery exported nothing for {missing} - "
              f"falling back to a Deliver render", file=sys.stderr)
        done += grab(ctx, missing, out)
    return done


# ---- the hatch ---------------------------------------------------------- #

def namespace(ctx: Ctx) -> dict[str, Any]:
    return {"resolve": ctx.resolve, "pm": ctx.pm, "project": ctx.project,
            "timeline": ctx.timeline, "mp": ctx.mp, "fps": ctx.fps,
            "frame_to_tc": frame_to_tc, "json": json, "os": os,
            "__name__": "__resolve_run__"}


def run_script(ctx: Ctx, source: str, filename: str = "<resolve-run>"
               ) -> dict[str, Any]:
    """Execute `source` with the Resolve handles in scope; return its globals."""
    g = namespace(ctx)
    exec(compile(source, filename, "exec"), g)  # noqa: S102 - the point
    return g


# ---- CLI ---------------------------------------------------------------- #

def _split(args: list[str]) -> tuple[list[str], str | None, bool]:
    out_dir = None
    rest: list[str] = []
    it = iter(args)
    for a in it:
        if a == "--out":
            out_dir = next(it, None)
        else:
            rest.append(a)
    as_json = "--json" in rest
    return [a for a in rest if a != "--json"], out_dir, as_json


def _main(argv: list[str] | None = None, ctx: Ctx | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmds = ("info", "timelines", "open", "dump", "grab", "still", "exec")
    if not args or args[0] not in cmds:
        print(__doc__)
        return 2
    cmd = args[0]
    rest, out_dir, as_json = _split(args[1:])
    try:
        ctx = ctx or connect()
        if cmd == "info":
            print(json.dumps(info(ctx), ensure_ascii=False, indent=2))
        elif cmd == "timelines":
            for tl in timelines(ctx):
                print("%s %-45s %d frames" % ("*" if tl["current"] else " ",
                                             tl["name"], tl["frames"]))
        elif cmd == "open":
            open_timeline(ctx, rest[0])
            print("current:", rest[0])
        elif cmd == "dump":
            data = dump(ctx)
            print(json.dumps(data, ensure_ascii=False, indent=2) if as_json
                  else format_dump(data))
        elif cmd in ("grab", "still"):
            fn = grab if cmd == "grab" else still
            for f in fn(ctx, [int(a) for a in rest], out_dir):
                print(f)
        elif cmd == "exec":
            if not rest:
                raise ResolveRunError("exec needs a script path, or - for stdin")
            src = sys.stdin.read() if rest[0] == "-" \
                else Path(rest[0]).read_text(encoding="utf-8")
            run_script(ctx, src, rest[0])
    except (ResolveRunError, ResolveUnavailable, IndexError, ValueError) as e:
        print(f"resolve_run: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
