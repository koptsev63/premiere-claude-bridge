"""Tests for shot levelling + delivery loudness.

Pure math first (no ffmpeg). Then one synthetic job: three "takes" of pink
noise recorded at very different levels, joined hard - the gate has to reject the raw
join and pass what `level_and_normalize` makes of it. The ffmpeg half
skips cleanly when ffmpeg is not installed.

Run:  python -m core.tests.test_loudness
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from core import loudness as ld
from core.loudness import (
    LoudnessFailure,
    gain_keyframes,
    gated_rms_db,
    judge,
    level_plan,
    parse_loudnorm,
    shot_bounds,
)

_p = _f = _s = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def skip(why: str) -> None:
    global _s
    _s += 1
    print(f"  SKIP  ({why})")


def sine(amp: float, n: int = 4800) -> list[float]:
    return [amp * math.sin(2 * math.pi * 440 * i / 48000) for i in range(n)]


def test_rms() -> None:
    print("loudness - gated RMS")
    full = gated_rms_db(sine(1.0))
    check("a full-scale sine reads about -3 dBFS", abs(full + 3.01) < 0.2,
          str(full))
    half = gated_rms_db(sine(0.5))
    check("half amplitude is 6 dB down", abs((full - half) - 6.02) < 0.1,
          f"{full} {half}")
    check("silence has no level (None, not -inf)",
          gated_rms_db([0.0] * 4800) is None)
    padded = gated_rms_db(sine(0.5) + [0.0] * 48000)
    check("pauses do not drag the level down", abs(padded - half) < 0.1,
          f"{padded} vs {half}")
    np_saved = ld._np
    try:
        ld._np = None
        slow = gated_rms_db(sine(0.5))
        step = ld.max_step([0.0, 0.0, 0.5, 0.5], 2)
    finally:
        ld._np = np_saved
    check("pure-python path agrees with the fast one", abs(slow - half) < 0.01,
          f"{slow} vs {half}")
    check("max_step finds the discontinuity", abs(step - 0.5) < 1e-6, str(step))


def test_plan() -> None:
    print("loudness - the levelling plan")
    check("cuts become contiguous shots",
          shot_bounds([0, 2.0, 5.0], 8.0) == [(0, 2.0), (2.0, 5.0), (5.0, 8.0)],
          str(shot_bounds([0, 2.0, 5.0], 8.0)))
    check("a missing first cut at 0 is implied",
          shot_bounds([2.0], 4.0) == [(0.0, 2.0), (2.0, 4.0)])
    check("cuts past the end are ignored",
          shot_bounds([0, 9.0], 4.0) == [(0, 4.0)])
    target, gains = level_plan([-30.0, -20.0, -26.0])
    check("target is the median shot", target == -26.0, str(target))
    check("each shot is moved onto it", gains == [4.0, -6.0, 0.0], str(gains))
    _, clamped = level_plan([-50.0, -20.0, -20.0])
    check("a 30 dB outlier is clamped to +9, not chased",
          clamped[0] == 9.0, str(clamped))
    target, gains = level_plan([None, -20.0, None])
    check("silent shots are left alone", gains == [0.0, 0.0, 0.0], str(gains))
    check("all-silent input has no target", level_plan([None])[0] is None)


def test_keyframes() -> None:
    print("loudness - the gain envelope")
    shots = [(0.0, 2.0), (2.0, 4.0)]
    up, down = 10 ** (6 / 20), 10 ** (-6 / 20)
    kf = gain_keyframes(shots, [6.0, -6.0], ramp_s=0.01, declick_ms=0)
    check("each shot holds its own gain edge to edge",
          kf[0] == (0.0, up) and kf[-1] == (4.0, down), str(kf))
    check("the gain changes at the join, 10 ms wide",
          [round(t, 4) for t, _ in kf[1:3]] == [1.995, 2.005]
          and kf[1][1] == up and kf[2][1] == down, str(kf))
    kf = gain_keyframes(shots, [6.0, -6.0], declick_ms=12)
    at = {round(t, 4): g for t, g in kf}
    check("declick dips the join to zero", at[2.0] == 0.0, str(kf))
    check("the dip is only as wide as asked",
          at[1.994] == up and at[2.006] == down, str(kf))
    check("head and tail of the file are not touched",
          kf[0][1] == up and kf[-1][1] == down)
    tiny = gain_keyframes([(0.0, 0.004), (0.004, 2.0)], [0.0, 3.0],
                          declick_ms=12)
    check("times never go backwards, even on a 4 ms shot",
          all(a[0] < b[0] for a, b in zip(tiny, tiny[1:])), str(tiny))
    check("one shot is one flat gain",
          gain_keyframes([(0.0, 3.0)], [2.0]) ==
          [(0.0, 10 ** 0.1), (3.0, 10 ** 0.1)])
    check("no shots, no envelope", gain_keyframes([], []) == [])


def test_parse_and_judge() -> None:
    print("loudness - parsing and the verdict")
    log = ('[Parsed_loudnorm_0 @ 0x1] \n{\n"input_i" : "-23.50",\n'
           '"input_tp" : "-6.10",\n"input_lra" : "3.00",\n'
           '"input_thresh" : "-33.6"\n}\n')
    m = parse_loudnorm(log)
    check("loudnorm JSON is read", m == {"i": -23.5, "tp": -6.1, "lra": 3.0},
          str(m))
    check("garbage is None, not a crash", parse_loudnorm("no json") is None)

    good = judge(-14.3, -1.2, [-20.0, -21.0, -20.5])
    check("on target, under the ceiling, even shots: pass", good.ok,
          str(good.failures))
    quiet = judge(-23.5, -6.1, [-20.0, -20.5])
    check("a quiet master fails on integrated loudness",
          not quiet.ok and any("integrated" in f for f in quiet.failures))
    hot = judge(-14.0, 0.4, [-20.0, -20.5])
    check("an over is caught", any("true peak" in f for f in hot.failures))
    jumpy = judge(-14.0, -1.5, [-18.0, -25.0])
    check("a 7 dB jump between shots is caught",
          any("one level" in f for f in jumpy.failures), str(jumpy.failures))
    soft = judge(-16.2, -1.6, None, target_lufs=ld.SOFT_LUFS,
                 target_tp=ld.SOFT_TP)
    check("targets are arguments (-16/-1.5 passes when asked for)", soft.ok)
    check("unchecked shot spread is said out loud",
          any("not checked" in n for n in soft.notes), str(soft.notes))
    blind = judge(None, None)
    check("no measurement fails closed",
          not blind.ok and "UNVERIFIED" in blind.failures[0])
    raised = ""
    try:
        quiet.assert_ok()
    except LoudnessFailure as e:
        raised = str(e)
    check("assert_ok() raises with the numbers", "-23.5 LUFS" in raised, raised)
    good.assert_ok()
    check("printable report carries the verdict",
          str(good).startswith("loudness gate: PASS"))


def _make_job(tmp: Path) -> tuple[Path, list[float]]:
    """Three 2 s takes at very different levels, hard-joined."""
    takes = []
    for i, amp in enumerate((0.08, 0.4, 0.18)):
        f = tmp / f"take{i}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             f"anoisesrc=color=pink:amplitude={amp}:duration=2:"
             f"sample_rate=48000:seed={i + 1}", "-ac", "2", str(f)],
            check=True)
        takes.append(f)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{t.name}'\n" for t in takes))
    wav = tmp / "joined.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe",
                    "0", "-i", str(lst), "-c", "copy", str(wav)], check=True)
    return wav, [0.0, 2.0, 4.0]


def test_end_to_end() -> None:
    print("loudness - a synthetic three-take job through ffmpeg")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        skip("ffmpeg/ffprobe not installed")
        return
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        try:
            wav, cuts = _make_job(tmp)
        except subprocess.CalledProcessError as e:
            skip(f"this ffmpeg build cannot make the test signal: {e}")
            return
        raw = ld.check_delivery(wav, cuts)
        check("the raw join is rejected", not raw.ok, str(raw))
        check("...for the jump between takes",
              any("one level" in f for f in raw.failures), str(raw.failures))
        check("spread measured near the 14 dB that was built in",
              11 < ld.spread_db(raw.levels) < 17, str(raw.levels))

        out = tmp / "out.wav"
        info = ld.level_and_normalize(wav, out, cuts)
        check("quiet take raised, loud take lowered",
              info["shots"][0]["gain_db"] > 0 > info["shots"][1]["gain_db"],
              str(info["shots"]))
        fixed = ld.check_delivery(out, cuts)
        check("the levelled file passes the gate", fixed.ok, str(fixed))
        check("integrated landed within 1 LU of -14",
              abs(fixed.integrated + 14) <= 1.0, str(fixed.integrated))
        check("true peak held under -1 dBTP (+slack)",
              fixed.true_peak <= -0.7, str(fixed.true_peak))
        samples = ld.decode_mono(out)
        join = [abs(float(x)) for x in samples[2 * 48000 - 24:2 * 48000 + 24]]
        body = [abs(float(x)) for x in samples[48000 - 2400:48000 + 2400]]
        check("declick: the join itself is near silence",
              max(join) < 0.1 * max(body), f"{max(join)} vs {max(body)}")

        plain = tmp / "plain.wav"
        ld.level_and_normalize(wav, plain, None)
        rep = ld.check_delivery(plain, cuts)
        check("normalising alone hits the target but keeps the jumps",
              any("one level" in f for f in rep.failures)
              and not any("integrated" in f for f in rep.failures),
              str(rep))

        soft = tmp / "soft.wav"
        ld.level_and_normalize(wav, soft, cuts, ld.SOFT_LUFS, ld.SOFT_TP)
        check("the softer pair is honoured",
              ld.check_delivery(soft, cuts, ld.SOFT_LUFS, ld.SOFT_TP).ok)

        mp4 = tmp / "master.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc2=s=160x90:r=25:d=6", "-i", str(wav), "-c:v", "libx264",
             "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", str(mp4)], capture_output=True)
        if r.returncode != 0:
            skip("no libx264/aac in this ffmpeg build")
        else:
            out_mp4 = tmp / "deliver.mp4"
            ld.level_and_normalize(mp4, out_mp4, cuts)
            def vhash(p):
                return subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(p), "-map", "0:v",
                     "-c", "copy", "-f", "md5", "-"],
                    capture_output=True, text=True).stdout.strip()
            check("picture is stream-copied, bit for bit",
                  vhash(mp4) == vhash(out_mp4) != "", vhash(out_mp4))
            check("the delivered mp4 passes",
                  ld.check_delivery(out_mp4, cuts).ok,
                  str(ld.check_delivery(out_mp4, cuts)))

        mute = tmp / "mute.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=gray:s=64x64:d=1", "-c:v", "mpeg4", str(mute)],
            capture_output=True)
        if r.returncode == 0:
            rep = ld.check_delivery(mute)
            check("a file with no audio fails, it is not skipped",
                  not rep.ok and "no audio" in rep.failures[0], str(rep))
            raised = False
            try:
                ld.level_and_normalize(mute, tmp / "x.wav")
            except LoudnessFailure:
                raised = True
            check("and cannot be 'normalised'", raised)
        check("CLI --check exits 1 on the raw join",
              ld._main(["--check", str(wav), "--shots", "0,2,4"]) == 1)


def main() -> int:
    test_rms()
    test_plan()
    test_keyframes()
    test_parse_and_judge()
    test_end_to_end()
    print(f"\nloudness: {_p} passed, {_f} failed, {_s} skipped")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
