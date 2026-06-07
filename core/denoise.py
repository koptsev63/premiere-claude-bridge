"""core.denoise - clean noisy audio before speech-to-text.

Reduces microphone rumble, broadband hiss, and power-line hum so Whisper
sees cleaner audio and mis-hears fewer words. Motivated by field recordings
("Ded" footage) where ambient noise caused Whisper to mishear syllables.

Honest boundary: this is a signal-processing pre-pass, not a magic fix.
It helps most on broadband white/pink noise and consistent electrical hum.
Intermittent noise bursts (doors, wind, sudden handling), reverberation,
or overlapping speech are outside its scope - a mix engineer's ear is
still the correct tool there.

Attribution: filter chain design (tonal/broadband/rumble stages) inspired by
OpenReel Video MIT (packages/core/src/audio/noise-reduction.ts) - spectral
subtraction with a noise-profile approach, FFT-based broadband reduction,
and per-band high-pass + notch structure.

Pure, unit-tested core (no ffmpeg):
  build_denoise_filter(...)  - returns an ffmpeg -af filterchain string

ffmpeg-backed:
  clean_for_asr(in_path, out_path, **kw)  - mono 16kHz WAV for Whisper
  clean(in_path, out_path, **kw)          - cleaned audio preserving format
  estimate_noise_floor(path)              - sample a quiet region, return
                                           suggested afftdn nf value (approx)

CLI:  python -m core.denoise IN OUT [--asr] [--nf -25] [--hum 50] [--no-normalize]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.silence import detect_silence

# ---- defaults ---------------------------------------------------------------
DEF_HIGHPASS_HZ = 80          # cut rumble below 80 Hz
DEF_AFFTDN_NF = -25.0         # noise floor for afftdn (dB, -80..-20)
DEF_AFFTDN_NR = 12.0          # noise reduction strength (0.01..97)
DEF_HUM_FREQ: int | None = None  # None = no hum notches; 50 or 60 Hz
DEF_NORMALIZE = True          # dynaudnorm final pass for consistent levels
DEF_ANLMDN = False            # prefer afftdn (no model); anlmdn optional
DEF_ANLMDN_STRENGTH = 0.0001  # anlmdn strength - conservative default

# Quiet region sampling for estimate_noise_floor
DEF_NOISE_DB_GATE = -35.0     # silencedetect gate for finding a quiet region
DEF_NOISE_SAMPLE_DURATION = 2.0  # seconds of quiet to measure

# ASR output format (what Whisper wants)
ASR_SAMPLE_RATE = 16000
ASR_CHANNELS = 1


# ---- pure functions (no ffmpeg, unit-testable) ------------------------------

def build_denoise_filter(
    *,
    highpass: int | None = DEF_HIGHPASS_HZ,
    afftdn_nf: float = DEF_AFFTDN_NF,
    afftdn_nr: float = DEF_AFFTDN_NR,
    hum: int | None = DEF_HUM_FREQ,
    normalize: bool = DEF_NORMALIZE,
    anlmdn: bool = DEF_ANLMDN,
    anlmdn_strength: float = DEF_ANLMDN_STRENGTH,
) -> str:
    """Return an ffmpeg -af filterchain string for the requested denoise stages.

    Parameters
    ----------
    highpass:
        Cut-off frequency for the rumble high-pass filter (Hz). Set to None
        or 0 to skip this stage entirely.
    afftdn_nf:
        Noise floor passed to afftdn (dB, valid range -80 to -20). Typical
        field recording: -25 to -30. Noisy phone audio: -20 to -22.
    afftdn_nr:
        afftdn noise-reduction strength (0.01..97). Higher = more aggressive;
        12 is the ffmpeg default and a safe starting point.
    hum:
        Power-line fundamental frequency in Hz (50 or 60). When set, notch
        filters are placed at the fundamental and two harmonics (1x, 2x, 3x)
        using the ffmpeg `bandreject` filter. None = no hum notches.
    normalize:
        Append a dynaudnorm pass for consistent loudness. Whisper benefits
        from consistent levels; disable only if you will normalize elsewhere.
    anlmdn:
        Replace afftdn with anlmdn (non-local means). Handles colored noise
        better but is slower and less well-suited to speech+broadband mix.
        Set to True only when afftdn leaves audible tonal residue.
    anlmdn_strength:
        Strength passed to anlmdn (1e-5..10000). Keep conservative (<0.001)
        to avoid over-smoothing speech formants.

    Returns
    -------
    str
        Comma-joined ffmpeg filter string ready for ``-af <string>``.
        Each enabled stage appears in order: highpass -> broadband -> hum
        notches -> normalize.
    """
    stages: list[str] = []

    # Stage 1: rumble - high-pass to remove handling and low-frequency noise
    if highpass and highpass > 0:
        stages.append(f"highpass=f={highpass}")

    # Stage 2: broadband noise reduction
    if anlmdn:
        stages.append(f"anlmdn=s={anlmdn_strength}")
    else:
        stages.append(f"afftdn=nf={afftdn_nf}:nr={afftdn_nr}")

    # Stage 3: hum notches (power-line harmonics)
    # bandreject with a Q of 10 gives a tight notch (~1/10 octave bandwidth)
    if hum and hum > 0:
        for harmonic in (1, 2, 3):
            freq = hum * harmonic
            stages.append(f"bandreject=f={freq}:width_type=q:w=10")

    # Stage 4: normalize for consistent ASR input levels
    if normalize:
        # dynaudnorm with gentle maxgain cap to avoid pumping on quiet clips
        stages.append("dynaudnorm=f=500:g=31:m=3.0")

    return ",".join(stages)


# ---- ffmpeg helpers ---------------------------------------------------------

def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    """Run ffmpeg, raise RuntimeError on non-zero exit."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y"] + args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}):\n"
            + (result.stderr or "")[-2000:]
        )
    return result


def _probe_duration(path: str) -> float:
    """Return file duration in seconds via ffprobe."""
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def _measure_rms(path: str) -> float | None:
    """Return the overall RMS level of a file in dBFS using ffmpeg astats.

    Returns None if measurement fails.
    """
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-f", "lavfi",
            f"-i", f"amovie={Path(path).as_posix()},astats=metadata=1:reset=0",
            "-show_entries", "frame_tags=lavfi.astats.Overall.RMS_level",
            "-of", "csv=p=0",
        ],
        capture_output=True,
        text=True,
    )
    # ffprobe astats via lavfi can be awkward - fall back to direct ffmpeg
    if r.returncode != 0 or not r.stdout.strip():
        # direct ffmpeg approach via ebur128
        r2 = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin",
             "-i", str(path),
             "-af", "astats",
             "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
        stderr = r2.stderr or ""
        # parse "RMS level dB:" from astats output
        for line in stderr.splitlines():
            if "RMS level dB:" in line:
                try:
                    return float(line.split(":")[-1].strip())
                except ValueError:
                    pass
        return None
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return round(float(lines[-1]), 3)
    except ValueError:
        return None


# ---- public ffmpeg-backed API -----------------------------------------------

def clean_for_asr(
    in_path: str,
    out_path: str,
    **kw,
) -> str:
    """Denoise audio and write a mono 16kHz WAV - the format Whisper wants.

    All keyword arguments are forwarded to build_denoise_filter. Returns
    out_path on success, raises RuntimeError on failure.

    The output is always mono PCM 16kHz WAV regardless of the input format
    or channel count. This matches the internal format used by both the local
    openai-whisper CLI and the cloud Whisper APIs.
    """
    af = build_denoise_filter(**kw)
    _run_ffmpeg([
        "-i", str(in_path),
        "-af", af,
        "-ac", str(ASR_CHANNELS),
        "-ar", str(ASR_SAMPLE_RATE),
        "-vn",
        str(out_path),
    ])
    return str(out_path)


def clean(
    in_path: str,
    out_path: str,
    **kw,
) -> str:
    """Denoise audio preserving the source sample rate and channel count.

    Use this for mixing or mastering passes. For Whisper transcription
    use clean_for_asr() instead - it down-mixes to mono 16kHz.

    All keyword arguments are forwarded to build_denoise_filter. Returns
    out_path on success, raises RuntimeError on failure.
    """
    af = build_denoise_filter(**kw)
    _run_ffmpeg([
        "-i", str(in_path),
        "-af", af,
        "-vn",
        str(out_path),
    ])
    return str(out_path)


def estimate_noise_floor(path: str) -> float:
    """Estimate a suitable afftdn nf value by sampling a quiet region.

    Uses core.silence.detect_silence to find a gap in speech, then measures
    the RMS level of that gap with ffmpeg astats. Returns a suggested nf
    value 5 dB above the measured floor (a conservative over-subtraction
    margin). Falls back to DEF_AFFTDN_NF if no quiet region is found or
    measurement fails.

    Approximate: the quiet region may contain intentional quiet rather than
    pure noise. Treat the result as a starting point, not a calibrated value.
    """
    silences = detect_silence(path, noise_db=-20.0, min_silence_s=1.0)
    if not silences:
        return DEF_AFFTDN_NF

    # Pick the first silence region that is at least 1 second long
    region: tuple[float, float] | None = None
    for start, end in silences:
        if end is None:
            # trailing silence - clip at DEF_NOISE_SAMPLE_DURATION
            end = start + DEF_NOISE_SAMPLE_DURATION
        if end - start >= 1.0:
            region = (start, min(end, start + DEF_NOISE_SAMPLE_DURATION))
            break

    if region is None:
        return DEF_AFFTDN_NF

    # Extract the quiet region to a temp file and measure RMS
    tmp = Path(path).parent / f"_denoise_noise_sample_{Path(path).stem}.wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-y",
                "-ss", str(region[0]),
                "-t", str(region[1] - region[0]),
                "-i", str(path),
                "-ac", "1", "-ar", "16000", "-vn",
                str(tmp),
            ],
            capture_output=True, text=True, check=True,
        )
        rms = _measure_rms(str(tmp))
    except (subprocess.CalledProcessError, RuntimeError):
        rms = None
    finally:
        if tmp.exists():
            tmp.unlink()

    if rms is None or rms < -80:
        return DEF_AFFTDN_NF

    # Add 5 dB margin above measured floor; clamp to afftdn valid range
    suggested = round(rms + 5.0, 1)
    suggested = max(-80.0, min(-20.0, suggested))
    return suggested


# ---- CLI --------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m core.denoise",
        description="Denoise audio to improve Whisper transcription accuracy.",
    )
    ap.add_argument("input", help="Input audio or video file")
    ap.add_argument("output", help="Output file path")
    ap.add_argument(
        "--asr",
        action="store_true",
        help="Write mono 16kHz WAV optimized for Whisper (default: preserve format)",
    )
    ap.add_argument(
        "--nf",
        type=float,
        default=DEF_AFFTDN_NF,
        metavar="DB",
        help=f"afftdn noise floor in dB (default {DEF_AFFTDN_NF})",
    )
    ap.add_argument(
        "--hum",
        type=int,
        default=None,
        metavar="HZ",
        help="Power-line fundamental frequency for notch filters (50 or 60)",
    )
    ap.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip the dynaudnorm level-normalization pass",
    )
    ap.add_argument(
        "--no-highpass",
        action="store_true",
        help="Skip the 80 Hz highpass rumble filter",
    )
    ap.add_argument(
        "--anlmdn",
        action="store_true",
        help="Use anlmdn instead of afftdn for broadband reduction",
    )
    ap.add_argument(
        "--estimate-nf",
        action="store_true",
        help="Auto-estimate noise floor from a quiet region before processing",
    )
    args = ap.parse_args(argv[1:])

    nf = args.nf
    if args.estimate_nf:
        nf = estimate_noise_floor(args.input)
        print(f"[denoise] estimated noise floor: {nf} dB", file=sys.stderr)

    kw = dict(
        afftdn_nf=nf,
        hum=args.hum,
        normalize=not args.no_normalize,
        highpass=None if args.no_highpass else DEF_HIGHPASS_HZ,
        anlmdn=args.anlmdn,
    )

    if args.asr:
        out = clean_for_asr(args.input, args.output, **kw)
    else:
        out = clean(args.input, args.output, **kw)

    print(f"[denoise] wrote: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
