"""core.ducking - automatic audio ducking: music auto-lowers under speech.

Honest boundary: interval-based ducking (preferred path) requires explicit
speech intervals - from Whisper timestamps or core.silence.speech_segments.
The sidechain path works blindly on live amplitude, so it will duck any loud
burst (cough, clap, clattering gear), not just dialog. For narrative work where
you control the Whisper pass, always prefer the interval path.

Algorithm ported from OpenReel Video (MIT) packages/core/src/audio/volume-automation.ts
AudioDucker class - S-curve ramps, configurable reduction/attack/release/hold.

Pure, unit-tested core (no ffmpeg):
  merge_intervals(intervals, hold_s)          - merge nearby speech ranges
  duck_envelope(intervals, total_s, ...)      - gain keyframes with S-curve ramps
  envelope_to_volexpr(keyframes)              - ffmpeg volume= expression string
  sidechain_args(...)                         - sidechaincompress filter snippet

ffmpeg-backed:
  build_ducked_mix(music, out, ...)           - produce a mixed m4a with ducking

CLI:  python -m core.ducking MUSIC OUT [--video V | --intervals a:b,c:d]
      [--reduction 0.5] [--attack 0.1] [--release 0.25] [--hold 0.1]
"""

from __future__ import annotations

import subprocess
import sys

DEF_REDUCTION = 0.5    # fraction of gain removed during speech (0-1)
DEF_ATTACK = 0.10      # ramp-down duration before speech starts (seconds)
DEF_RELEASE = 0.25     # ramp-up duration after speech ends (seconds)
DEF_HOLD = 0.10        # merge gaps between speech ranges shorter than this (seconds)
DEF_FULL = 1.0         # base music gain (linear)

# sidechaincompress defaults (OpenReel-compatible)
_SC_THRESHOLD = 0.06
_SC_RATIO = 4
_SC_ATTACK = 20        # ms
_SC_RELEASE = 250      # ms


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _scurve(t: float) -> float:
    """Smooth S-curve ease-in-out: t*t*(3-2*t), input t in [0,1]."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def merge_intervals(
    intervals: list[tuple[float, float]],
    hold_s: float = DEF_HOLD,
) -> list[tuple[float, float]]:
    """Merge speech intervals separated by gaps shorter than hold_s.

    Prevents music from fluttering during short pauses between words.
    Returns a sorted list of non-overlapping (start, end) pairs.
    PURE - no I/O.
    """
    if not intervals:
        return []
    srt = sorted(intervals)
    merged: list[tuple[float, float]] = [srt[0]]
    for s, e in srt[1:]:
        ps, pe = merged[-1]
        if s - pe <= hold_s:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def duck_envelope(
    intervals: list[tuple[float, float]],
    total_s: float,
    *,
    reduction: float = DEF_REDUCTION,
    attack: float = DEF_ATTACK,
    release: float = DEF_RELEASE,
    hold: float = DEF_HOLD,
    full: float = DEF_FULL,
) -> list[tuple[float, float]]:
    """Build gain keyframes for the ducking envelope.

    Given merged speech intervals and total duration, returns a list of
    (time_s, gain) pairs that implement:
      - full gain outside speech
      - S-curve ramp DOWN over `attack` seconds before each speech range
      - plateau at full*(1-reduction) during speech
      - S-curve ramp UP over `release` seconds after each speech range

    The ramps start/end at the interval boundaries and may be clamped to
    [0, total_s]. Keyframes are always sorted by time. PURE - no I/O.

    Example values (reduction=0.5, full=1.0):
      - far outside speech:  gain = 1.0
      - plateau inside:      gain = 0.5
      - midpoint of attack:  0.5 < gain < 1.0 (smooth S-curve)
    """
    duck_level = round(full * (1.0 - reduction), 6)
    merged = merge_intervals(intervals, hold_s=hold)

    if not merged:
        return [(0.0, round(full, 3)), (round(total_s, 3), round(full, 3))]

    kf: list[tuple[float, float]] = []

    def _add(t: float, g: float) -> None:
        kf.append((round(max(0.0, min(total_s, t)), 3), round(g, 3)))

    _add(0.0, full)

    for seg_s, seg_e in merged:
        att_start = seg_s - attack
        rel_end = seg_e + release

        # attack ramp: full -> duck_level over [att_start, seg_s]
        att_start_clamped = max(0.0, att_start)
        n_ramp = 8  # number of intermediate keyframe steps per ramp
        for i in range(n_ramp + 1):
            t_frac = i / n_ramp
            t = att_start_clamped + t_frac * (seg_s - att_start_clamped)
            # S-curve: starts at 0 (full gain), ends at 1 (duck level)
            s = _scurve(t_frac)
            g = full + s * (duck_level - full)
            _add(t, g)

        # plateau: duck_level holds from seg_s to seg_e
        _add(seg_s, duck_level)
        _add(seg_e, duck_level)

        # release ramp: duck_level -> full over [seg_e, rel_end]
        rel_end_clamped = min(total_s, rel_end)
        for i in range(n_ramp + 1):
            t_frac = i / n_ramp
            t = seg_e + t_frac * (rel_end_clamped - seg_e)
            s = _scurve(t_frac)
            g = duck_level + s * (full - duck_level)
            _add(t, g)

    _add(total_s, full)

    # sort and deduplicate by time (keep last value at each time)
    seen: dict[float, float] = {}
    for t, g in kf:
        seen[t] = g
    return sorted(seen.items())


def envelope_to_volexpr(keyframes: list[tuple[float, float]]) -> str:
    """Convert gain keyframes to an ffmpeg volume= filter expression.

    Uses piecewise-linear interpolation via nested if(between(t,...)) clauses.
    The expression is evaluated per-frame (use with eval=frame).

    The expression lerps linearly between keyframe pairs. The last keyframe
    value is held for t beyond the final keyframe.

    Returns a string suitable for use in: volume='EXPR':eval=frame
    PURE - no I/O.
    """
    if not keyframes:
        return "1.0"
    if len(keyframes) == 1:
        return str(round(keyframes[0][1], 3))

    parts: list[str] = []
    for i in range(len(keyframes) - 1):
        t0, g0 = keyframes[i]
        t1, g1 = keyframes[i + 1]
        if t1 <= t0:
            continue
        dt = t1 - t0
        dg = g1 - g0
        # lerp: g0 + (t - t0) / dt * dg
        if abs(dg) < 1e-9:
            expr = f"{g0:.4f}"
        else:
            slope = dg / dt
            intercept = g0 - slope * t0
            if intercept >= 0:
                expr = f"{slope:.6f}*t+{intercept:.6f}"
            else:
                expr = f"{slope:.6f}*t-{abs(intercept):.6f}"
        # wrap in: if(between(t,t0,t1), expr, ...)
        parts.append((t0, t1, expr))

    # build nested if() from right to left
    # fallback = last gain value
    last_g = keyframes[-1][1]
    result = f"{last_g:.4f}"
    for t0, t1, expr in reversed(parts):
        result = f"if(between(t,{t0:.4f},{t1:.4f}),{expr},{result})"
    return result


def sidechain_args(
    music_label: str = "[music]",
    key_label: str = "[key]",
    out_label: str = "[ducked]",
    *,
    threshold: float = _SC_THRESHOLD,
    ratio: int = _SC_RATIO,
    attack: int = _SC_ATTACK,
    release: int = _SC_RELEASE,
) -> str:
    """Return a sidechaincompress filter snippet for use in -filter_complex.

    The snippet ducks music_label using key_label as the sidechain signal.
    attack and release are in milliseconds (ffmpeg convention for this filter).

    PURE - no I/O. Example:
        args = sidechain_args("[m]", "[k]", "[out]")
        # insert into a larger filter_complex string
    """
    return (
        f"{music_label}{key_label}"
        f"sidechaincompress="
        f"threshold={threshold}:"
        f"ratio={ratio}:"
        f"attack={attack}:"
        f"release={release}"
        f"{out_label}"
    )


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def _probe_duration(path: str) -> float:
    """Return duration in seconds via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Main ffmpeg-backed function
# ---------------------------------------------------------------------------

def build_ducked_mix(
    music: str,
    out: str,
    *,
    video: str | None = None,
    intervals: list[tuple[float, float]] | None = None,
    ambient_gain: float = 1.0,
    music_full: float = DEF_FULL,
    reduction: float = DEF_REDUCTION,
    attack: float = DEF_ATTACK,
    release: float = DEF_RELEASE,
    hold: float = DEF_HOLD,
    bitrate: str = "256k",
    sample_rate: int = 48000,
) -> dict:
    """Mix music with automatic ducking. Outputs a single m4a (AAC 256k, 48k stereo).

    Two paths:
    1. intervals given - use envelope-based ducking with the provided speech
       intervals (preferred; feed Whisper word timestamps here).
    2. video given - derive intervals via core.silence.speech_segments(video),
       then mix music + video's own ambient at ambient_gain underneath.
       This reproduces the "music leads, ambient audible, music ducks under
       loud dialog" pattern used in Grave Stakes editing.
    3. Both given - intervals override detection; video ambient is still mixed.
    4. Neither given - music pass-through at music_full gain (no ducking).

    Returns a dict with keys: out, total_s, n_intervals, method, cmd.
    """
    from core.silence import speech_segments  # local import - avoids circular dep

    # resolve total duration from music file
    total_s = _probe_duration(music)
    if total_s <= 0:
        raise ValueError(f"Could not determine duration of music file: {music}")

    # resolve speech intervals
    used_intervals: list[tuple[float, float]] = []
    if intervals is not None:
        used_intervals = list(intervals)
    elif video is not None:
        used_intervals = speech_segments(video, out_s=total_s)

    merged = merge_intervals(used_intervals, hold_s=hold)
    method = "envelope" if merged else "passthrough"

    aformat = f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo"

    if video is not None and merged:
        # Path: music (ducked) + video ambient (low, always present)
        envelope = duck_envelope(
            merged, total_s,
            reduction=reduction, attack=attack, release=release,
            hold=hold, full=music_full,
        )
        vol_expr = envelope_to_volexpr(envelope)

        filter_complex = (
            f"[0:a]{aformat}[mfmt];"
            f"[mfmt]volume='{vol_expr}':eval=frame[mducked];"
            f"[1:a]{aformat}[vfmt];"
            f"[vfmt]volume={ambient_gain:.4f}[vamb];"
            f"[mducked][vamb]amix=inputs=2:normalize=0[mix]"
        )
        cmd = [
            "ffmpeg", "-nostdin", "-y",
            "-i", music,
            "-i", video,
            "-filter_complex", filter_complex,
            "-map", "[mix]",
            "-c:a", "aac", "-b:a", bitrate, "-ar", str(sample_rate),
            str(out),
        ]
    elif merged:
        # Path: music only, interval-based envelope ducking
        envelope = duck_envelope(
            merged, total_s,
            reduction=reduction, attack=attack, release=release,
            hold=hold, full=music_full,
        )
        vol_expr = envelope_to_volexpr(envelope)

        filter_complex = (
            f"[0:a]{aformat}[mfmt];"
            f"[mfmt]volume='{vol_expr}':eval=frame[mix]"
        )
        cmd = [
            "ffmpeg", "-nostdin", "-y",
            "-i", music,
            "-filter_complex", filter_complex,
            "-map", "[mix]",
            "-c:a", "aac", "-b:a", bitrate, "-ar", str(sample_rate),
            str(out),
        ]
    else:
        # Path: no speech detected - simple gain pass-through
        cmd = [
            "ffmpeg", "-nostdin", "-y",
            "-i", music,
            "-af", f"volume={music_full:.4f},{aformat}",
            "-c:a", "aac", "-b:a", bitrate, "-ar", str(sample_rate),
            str(out),
        ]

    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {r.returncode}):\n"
            f"CMD: {' '.join(cmd)}\n"
            f"STDERR: {r.stderr[-2000:]}"
        )

    return {
        "out": str(out),
        "total_s": round(total_s, 3),
        "n_intervals": len(merged),
        "method": method,
        "cmd": " ".join(cmd),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_intervals(s: str) -> list[tuple[float, float]]:
    """Parse 'a:b,c:d,...' into [(a,b),(c,d),...] floats."""
    out = []
    for pair in s.split(","):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split(":")
        if len(parts) != 2:
            raise ValueError(f"Bad interval '{pair}' - expected start:end")
        out.append((float(parts[0]), float(parts[1])))
    return out


def _main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="python -m core.ducking",
        description="Mix music with automatic ducking under speech.",
    )
    p.add_argument("music", help="Input music file")
    p.add_argument("out", help="Output m4a path")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--video", "-v", metavar="V",
                     help="Video file: detect speech via silence analysis, "
                          "mix ambient underneath")
    src.add_argument("--intervals", "-i", metavar="a:b,c:d",
                     help="Explicit speech intervals as start:end pairs, "
                          "comma-separated")
    p.add_argument("--reduction", type=float, default=DEF_REDUCTION,
                   help=f"Gain fraction removed during speech (default {DEF_REDUCTION})")
    p.add_argument("--attack", type=float, default=DEF_ATTACK,
                   help=f"Ramp-down duration in seconds (default {DEF_ATTACK})")
    p.add_argument("--release", type=float, default=DEF_RELEASE,
                   help=f"Ramp-up duration in seconds (default {DEF_RELEASE})")
    p.add_argument("--hold", type=float, default=DEF_HOLD,
                   help=f"Merge gaps shorter than this (default {DEF_HOLD})")
    p.add_argument("--ambient-gain", type=float, default=1.0,
                   help="Gain for video ambient track (only with --video, default 1.0)")
    p.add_argument("--music-full", type=float, default=DEF_FULL,
                   help=f"Full music gain outside speech (default {DEF_FULL})")

    args = p.parse_args(argv[1:])

    ivs = _parse_intervals(args.intervals) if args.intervals else None

    print(f"ducking: music={args.music}  out={args.out}")
    if args.video:
        print(f"  source: video ambient + speech detection from {args.video}")
    elif ivs:
        print(f"  source: explicit intervals: {ivs}")
    else:
        print("  source: none - pass-through (no ducking)")

    info = build_ducked_mix(
        args.music, args.out,
        video=args.video,
        intervals=ivs,
        ambient_gain=args.ambient_gain,
        music_full=args.music_full,
        reduction=args.reduction,
        attack=args.attack,
        release=args.release,
        hold=args.hold,
    )

    print(f"  total:     {info['total_s']}s")
    print(f"  intervals: {info['n_intervals']} (after merge)")
    print(f"  method:    {info['method']}")
    print(f"  wrote:     {info['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
