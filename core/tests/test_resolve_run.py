"""Tests for the Resolve runner (stubbed Resolve objects, no live app).

The stubs model just enough of the scripting API to pin the behaviour
that matters: timeline-relative frame numbers, a render queue that is
left the way it was found, and the Gallery fallback.

Run:  python -m core.tests.test_resolve_run
"""

from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from core.adapters import resolve_run as rr
from core.adapters.resolve_run import Ctx, ResolveRunError

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


class Item:
    def __init__(self, name, start, end, src_in=0, props=None):
        self.name, self.start, self.end = name, start, end
        self.src_in, self.props = src_in, props or {}

    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetLeftOffset(self): return self.src_in

    def GetProperty(self, k):
        if k == "Opacity":
            raise RuntimeError("property not available on this item")
        return self.props.get(k)


class Timeline:
    def __init__(self, name, start=86400, tracks=None, still_ok=True):
        self.name, self.start = name, start
        self.tracks = tracks or {"video": [[]], "audio": [[]]}
        self.tc: list[str] = []
        self.still_ok = still_ok

    def GetName(self): return self.name
    def GetStartFrame(self): return self.start

    def GetEndFrame(self):
        ends = [i.end for k in self.tracks.values() for tr in k for i in tr]
        return max(ends) if ends else self.start

    def GetTrackCount(self, kind): return len(self.tracks[kind])
    def GetItemListInTrack(self, kind, idx): return self.tracks[kind][idx - 1]
    def GetTrackName(self, kind, idx): return f"{kind[0].upper()}{idx}"
    def GetIsTrackEnabled(self, kind, idx): return True
    def SetCurrentTimecode(self, tc): self.tc.append(tc); return True
    def GrabStill(self): return object() if self.still_ok else None


class Album:
    def __init__(self, export=True):
        self.export, self.deleted = export, 0

    def ExportStills(self, stills, folder, prefix, fmt):
        if not self.export:
            return False
        Path(folder, f"{prefix}_1.1.1.{fmt}").write_bytes(b"png")
        Path(folder, f"{prefix}_1.1.1.drx").write_bytes(b"drx")
        return True

    def DeleteStills(self, stills): self.deleted += len(stills); return True


class Gallery:
    def __init__(self, album): self.album = album
    def GetCurrentStillAlbum(self): return self.album


class Project:
    def __init__(self, timelines, album=None, render=True, fps="30"):
        self.timelines, self.current = timelines, timelines[0]
        self.album = album or Album()
        self.render, self.fps = render, fps
        self.queue = ["users-own-job"]      # must survive a grab
        self.started: list = []
        self.settings: list[dict] = []
        self.n = 0

    def GetName(self): return "Stub Project"
    def GetCurrentTimeline(self): return self.current
    def GetMediaPool(self): return "media-pool"
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]
    def SetCurrentTimeline(self, tl): self.current = tl; return True
    def GetGallery(self): return Gallery(self.album)

    def GetSetting(self, k):
        return {"timelineFrameRate": self.fps,
                "timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}[k]

    def SetRenderSettings(self, s): self.settings.append(s); return True
    def SetCurrentRenderFormatAndCodec(self, f, c): return True

    def AddRenderJob(self):
        self.n += 1
        jid = f"job{self.n}"
        self.queue.append(jid)
        return jid

    def StartRendering(self, jobs, interactive=False):
        self.started = list(jobs)
        if self.render:
            for s in self.settings:
                Path(s["TargetDir"],
                     s["CustomName"] + "_00086400.png").write_bytes(b"png")
        return True

    def IsRenderingInProgress(self): return False
    def GetRenderJobStatus(self, j): return {"JobStatus": "Failed"}
    def DeleteRenderJob(self, j): self.queue.remove(j); return True


class App:
    def GetVersionString(self): return "21.0.0"
    def GetCurrentPage(self): return "edit"


def make(**kw) -> Ctx:
    tl = Timeline("Rough v2", tracks={
        "video": [[
            Item("a.mov", 86400, 86500, 12, {"ZoomX": 1.2, "CropLeft": 0.0}),
            Item("b.mov", 86500, 86590, 0, {"Pan": -40.0}),
            Item("c.mov", 86591, 86700),          # one-frame hole before it
        ]],
        "audio": [[Item("a.mov", 86400, 86700)]],
    }, still_ok=kw.pop("still_ok", True))
    proj = Project([tl, Timeline("Other")], **kw)
    return Ctx(App(), "pm", proj, tl, proj.GetMediaPool())


def test_timecode() -> None:
    print("resolve_run - frame math")
    check("frame 0 is 00:00:00:00", rr.frame_to_tc(0, 24) == "00:00:00:00")
    check("86400 @ 24 is the one-hour start",
          rr.frame_to_tc(86400, 24) == "01:00:00:00", rr.frame_to_tc(86400, 24))
    check("frames roll into seconds", rr.frame_to_tc(61, 30) == "00:00:02:01")
    check("29.97 labels at the rounded rate (non-drop)",
          rr.frame_to_tc(1800, 29.97) == "00:01:00:00")
    check("unreadable fps falls back instead of crashing",
          rr.fps_of(object()) == 25.0)


def test_read_only() -> None:
    print("resolve_run - info, timelines, open, dump")
    ctx = make()
    i = rr.info(ctx)
    check("info reports timeline length, not absolute frames",
          i["timelineFrames"] == 300 and i["resolution"] == "1080x1920", str(i))
    names = rr.timelines(ctx)
    check("the current timeline is marked",
          [t["current"] for t in names] == [True, False], str(names))
    rr.open_timeline(ctx, "Other")
    check("open switches project and context",
          ctx.project.current.name == "Other" and ctx.timeline.name == "Other")
    raised = False
    try:
        rr.open_timeline(ctx, "nope")
    except ResolveRunError:
        raised = True
    check("open on an unknown name raises", raised)

    ctx = make()
    d = rr.dump(ctx)
    v1 = d["tracks"][0]["clips"]
    check("clip positions are timeline-relative",
          (v1[0]["start"], v1[0]["end"], v1[0]["srcIn"]) == (0, 100, 12),
          str(v1[0]))
    check("a property that raises is skipped, not fatal",
          "Opacity" not in v1[0]["props"] and v1[0]["props"]["ZoomX"] == 1.2)
    check("audio clips carry no transform block",
          "props" not in d["tracks"][1]["clips"][0])
    text = rr.format_dump(d)
    check("zero crops are hidden, real values shown",
          "ZoomX=1.2" in text and "CropLeft" not in text, text)
    check("the one-frame hole is found", rr.gaps(d) == [(190, 191)],
          str(rr.gaps(d)))
    check("a gapless track reports none", rr.gaps(d, "audio", 1) == [])
    ctx.timeline = None
    raised = False
    try:
        rr.dump(ctx)
    except ResolveRunError:
        raised = True
    check("no timeline is an error, not an AttributeError", raised)


def test_grab() -> None:
    print("resolve_run - grab leaves the render queue as it found it")
    ctx = make()
    with tempfile.TemporaryDirectory() as tmp:
        files = rr.grab(ctx, [0, 120], tmp, poll_s=0)
        check("one PNG per requested frame", len(files) == 2, str(files))
        marks = [s["MarkIn"] for s in ctx.project.settings]
        check("frames are offset by the timeline start",
              marks == [86400, 86520], str(marks))
        check("only its own jobs are started",
              ctx.project.started == ["job1", "job2"], str(ctx.project.started))
        check("its jobs are removed, the user's job stays",
              ctx.project.queue == ["users-own-job"], str(ctx.project.queue))
        Path(tmp, "rk_Rough_v2_stale.png").write_bytes(b"old")
        rr.grab(ctx, [5], tmp, poll_s=0)
        check("stale frames from an earlier run are cleared",
              not Path(tmp, "rk_Rough_v2_stale.png").exists())

    ctx = make(render=False)
    with tempfile.TemporaryDirectory() as tmp:
        msg = ""
        try:
            rr.grab(ctx, [0], tmp, poll_s=0)
        except ResolveRunError as e:
            msg = str(e)
        check("a render with no output raises with the job status",
              "Failed" in msg, msg)
        check("and still cleans its job up",
              ctx.project.queue == ["users-own-job"])
    raised = False
    try:
        rr.grab(make(), [])
    except ResolveRunError:
        raised = True
    check("no frames is refused", raised)


def test_still() -> None:
    print("resolve_run - still, and its fallback")
    ctx = make()
    with tempfile.TemporaryDirectory() as tmp:
        files = rr.still(ctx, [10, 100], tmp)
        names = sorted(Path(f).name for f in files)
        check("clean PNG names, one per frame",
              names == ["rs_Rough_v2_f10.png", "rs_Rough_v2_f100.png"],
              str(names))
        check("no Gallery sidecars left behind",
              not list(Path(tmp).glob("*.drx")), str(list(Path(tmp).iterdir())))
        check("f10 did not swallow f100's file", all(Path(f).exists() for f in files))
        check("the playhead went to absolute timecode",
              ctx.timeline.tc[0] == "00:48:00:10", ctx.timeline.tc[0])
        check("the temporary stills were removed from the album",
              ctx.project.album.deleted == 2)

    ctx = make(album=Album(export=False))
    with tempfile.TemporaryDirectory() as tmp:
        err = io.StringIO()
        with redirect_stderr(err):
            files = rr.still(ctx, [7], tmp)
        check("a silent Gallery falls back to a Deliver render",
              len(files) == 1 and "rk_" in files[0], str(files))
        check("and says so", "falling back" in err.getvalue())


def test_exec_and_cli() -> None:
    print("resolve_run - exec namespace and CLI")
    ctx = make()
    g = rr.run_script(ctx, "seen = (project.GetName(), timeline.GetName(), "
                           "mp, fps, frame_to_tc(30, fps))")
    check("handles are preloaded for the script",
          g["seen"] == ("Stub Project", "Rough v2", "media-pool", 30.0,
                        "00:00:01:00"), str(g.get("seen")))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = rr._main(["timelines"], ctx)
    check("timelines prints a marked list",
          rc == 0 and out.getvalue().startswith("* Rough v2"), out.getvalue())
    with redirect_stdout(io.StringIO()):
        check("no command prints usage, exit 2", rr._main([], ctx) == 2)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = rr._main(["open", "missing"], ctx)
    check("a failed command exits 1 with a one-line reason",
          rc == 1 and "no timeline named" in err.getvalue(), err.getvalue())
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp, "s.py")
        script.write_text("print('ran on', timeline.GetName())")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = rr._main(["exec", str(script)], ctx)
        check("exec runs a script file", rc == 0 and "ran on Rough v2" in out.getvalue(),
              out.getvalue())


def main() -> int:
    test_timecode()
    test_read_only()
    test_grab()
    test_still()
    test_exec_and_cli()
    print(f"\nresolve_run: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
