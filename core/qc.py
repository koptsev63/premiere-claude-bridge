"""Render QC gate — verify the OUTPUT before declaring done.

Vladimir's rule: "выводи обязательно с перепроверкой." A tiny contact
sheet let an anamorphic squish (narrow faces) and shaky stabilized edges
ship unnoticed. This makes verification a hard, automated gate that
*fails loudly* instead of trusting the render.

Checks on the rendered file:

1. **Geometry / no squish.** The killer bug: source `.MTS` is 1440x1080
   SAR 4:3 (anamorphic, displays 16:9). If the SAR un-stretch is skipped,
   the container is still 16:9 but everything inside is horizontally
   compressed. The container DAR alone can't catch that. So QC re-derives
   the *expected* display width from the source (round(w*SAR)) and
   requires the output to carry that geometry at SAR 1:1 — i.e. proof the
   un-squish was actually applied.
2. **Residual shake.** For clips that were stabilized, re-measure shake on
   the rendered segment and require it to have dropped vs the source by a
   real margin — "stabilized" must mean it.
3. A full-resolution inspection frame is written for the human/LLM to read
   (not a 8-up thumbnail).

`qc_geometry()` and `qc_residual_shake()` are pure-ish (probe + measure)
and unit-tested via the math helper `expected_display_size()`.
`assert_ok()` raises `QCFailure` — call it before saying "готово".
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path


class QCFailure(RuntimeError):
    pass


@dataclass
class QCReport:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def text(self) -> str:
        return "\n".join(
            f"  {'PASS' if ok else 'FAIL'}  {n}  {d}"
            for n, ok, d in self.checks
        )

    def assert_ok(self) -> None:
        if not self.ok:
            raise QCFailure("render QC failed:\n" + self.text())


def _probe(path: str | Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    vals = [x for x in r.stdout.strip().splitlines()]
    out = {}
    if len(vals) >= 4:
        out = {
            "w": int(vals[0]), "h": int(vals[1]),
            "sar": vals[2], "dar": vals[3],
        }
    return out


def expected_display_size(w: int, h: int, sar: str) -> tuple[int, int]:
    """True display geometry once the pixel aspect is applied.
    1440x1080 SAR 4:3 -> 1920x1080 (the un-squished frame)."""
    try:
        sar_f = Fraction(sar.replace(":", "/")) if sar not in (
            "", "0:1", "N/A") else Fraction(1)
    except (ZeroDivisionError, ValueError):
        sar_f = Fraction(1)
    disp_w = round(w * float(sar_f))
    return disp_w, h


def qc_geometry(
    out_path: str | Path, src_path: str | Path, rep: QCReport
) -> None:
    src = _probe(src_path)
    out = _probe(out_path)
    if not src or not out:
        rep.add("geometry-probe", False, "ffprobe gave no stream info")
        return
    exp_w, exp_h = expected_display_size(src["w"], src["h"], src["sar"])
    src_dar = round(exp_w / exp_h, 3)
    out_dar = round(out["w"] / out["h"], 3)
    # output must be SAR 1 (square) and match the SOURCE display ratio
    sar_ok = out["sar"] in ("1:1", "1", "")
    dar_ok = abs(src_dar - out_dar) < 0.02
    rep.add(
        "no-squish (DAR matches source display)",
        sar_ok and dar_ok,
        f"src displays {exp_w}x{exp_h} (DAR {src_dar}); "
        f"out {out['w']}x{out['h']} SAR {out['sar']} DAR {out_dar}",
    )


def qc_residual_shake(
    out_path: str | Path,
    stabilized_windows: list[tuple[float, float, float]],
    rep: QCReport,
    min_drop_ratio: float = 0.5,
) -> None:
    """stabilized_windows: (out_start_s, out_end_s, source_shake_score).
    Re-measure shake on each rendered window; require it to drop to at
    most `min_drop_ratio` of the source score."""
    if not stabilized_windows:
        rep.add("residual-shake", True, "no clips were stabilized")
        return
    try:
        import sys
        sys.path.insert(
            0, str(Path(__file__).resolve().parents[1]
                   / "skills" / "film-editing" / "tools"))
        from shake_detect import estimate_shake
    except Exception as e:  # noqa: BLE001
        # A verification gate must fail CLOSED: "can't verify" is not a
        # pass. (Run with the venv interpreter that has opencv.)
        rep.add(
            "residual-shake", False,
            f"UNVERIFIED — shake detector unavailable ({e!r}); "
            f"run QC with the opencv venv",
        )
        return
    for i, (a, b, src_score) in enumerate(stabilized_windows):
        seg = f"/tmp/_qc_seg_{i}.mp4"
        # accurate seek + RE-ENCODE: a stream-copy cut lands on the
        # wrong keyframe and yields an undecodable stub (estimate_shake
        # then returns None — a false "can't verify", not a pass).
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(a), "-i", str(out_path),
             "-t", str(round(b - a, 3)), "-an",
             "-c:v", "libx264", "-preset", "ultrafast", seg],
            capture_output=True,
        )
        sc, _ = estimate_shake(seg, n_samples=16)
        dropped = sc is not None and sc <= src_score * min_drop_ratio
        rep.add(
            f"stabilized[{i}] shake dropped",
            dropped,
            f"src={src_score} -> out={sc} "
            f"(need <= {round(src_score * min_drop_ratio, 2)})",
        )


def write_inspection_frame(
    out_path: str | Path, at_sec: float, dst: str | Path
) -> str:
    """A FULL-RESOLUTION frame for a human/LLM to actually read."""
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(at_sec), "-i", str(out_path),
         "-frames:v", "1", "-q:v", "2", str(dst)],
        capture_output=True,
    )
    return str(dst)
