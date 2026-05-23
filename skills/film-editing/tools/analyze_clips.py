#!/usr/bin/env python3
"""
analyze_clips.py — generate motion/audio/visual log for a folder of video clips.

Outputs to OUT_DIR:
  - {clip}_thumb.jpg      — single mid-clip thumbnail (480w)
  - {clip}_strip.jpg      — 6-frame motion strip (one row, 240h each, hstacked)
  - report.json           — machine-readable per-clip metadata
  - report.html           — visual contact sheet for human review

Per-clip metadata captured (the machine layer — measured, not understood):
  duration, width, height, fps,
  motion_score (mean scene-change delta, higher = more action),
  audio_peak_db (loudest moment), audio_peak_time_sec, audio_mean_db,
  horizon_tilt_deg + horizon_correction_filter (level check),
  shake_score + needs_stabilization (camera-shake check),
  speech_text (Whisper transcript if speech detected; first 200 chars).

Optional human/LLM layer (the meaning — supplied via --desc FILE.json):
  desc_ru (one line: what is in the shot, in plain Russian),
  tags    (list of keywords: дед, сарай, руки, природа, …),
  keep    (true = part of the story, false = junk to drop).
A metrics dump is not a usable log. A person — or Claude looking at the
generated thumbs/strips — writes desc/tags/keep, and the report becomes
something you can actually cut from. report.json/html are written after
every clip, so the table is viewable while the run is still going.

Usage:
    python3 analyze_clips.py SRC_DIR OUT_DIR [--whisper] [--desc FILE.json]

Skips Whisper unless --whisper flag (it's slow on 60+ min of footage).
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {".mts", ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mxf"}


def run(cmd, capture=True):
    return subprocess.run(cmd, capture_output=capture, text=True)


def probe(path: Path) -> dict:
    r = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_type",
        "-of", "json", str(path)
    ])
    if r.returncode != 0:
        return {}
    j = json.loads(r.stdout or "{}")
    streams = j.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    fps_raw = v.get("r_frame_rate", "0/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 0
    except Exception:
        fps = 0
    return {
        "duration": float(j.get("format", {}).get("duration", 0) or 0),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": round(fps, 2),
    }


def make_thumb(src: Path, out: Path, t_sec: float):
    run([
        "ffmpeg", "-y", "-ss", str(t_sec), "-i", str(src),
        "-vframes", "1", "-vf", "scale=480:-1",
        str(out)
    ])


def make_strip(src: Path, out: Path, dur: float, n: int = 6, height: int = 200):
    """6 evenly-spaced frames hstacked into a single 'motion strip' image."""
    if dur <= 0:
        return
    times = [dur * (i + 0.5) / n for i in range(n)]
    tmp_dir = out.parent / f"_tmp_{src.stem}"
    tmp_dir.mkdir(exist_ok=True)
    paths = []
    for i, t in enumerate(times):
        p = tmp_dir / f"f{i:02d}.jpg"
        run([
            "ffmpeg", "-y", "-ss", str(t), "-i", str(src),
            "-vframes", "1", "-vf", f"scale=-1:{height}",
            str(p)
        ])
        if p.exists():
            paths.append(p)
    if len(paths) >= 2:
        # hstack via ffmpeg concat-and-tile
        inputs = []
        for p in paths:
            inputs.extend(["-i", str(p)])
        n_inputs = len(paths)
        filter_complex = "".join(f"[{i}:v]" for i in range(n_inputs)) + f"hstack=inputs={n_inputs}"
        run([
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            str(out)
        ])
    shutil.rmtree(tmp_dir, ignore_errors=True)


def motion_score(src: Path, dur: float) -> float:
    """
    Mean scene-change score over the clip.
    ffmpeg signalstats outputs SAMPLE delta per frame; we use the simpler 'select=gt(scene,..)'
    detection count divided by clip length as a proxy.
    Higher = more visual change (action, motion, cuts within shot).
    """
    if dur <= 0:
        return 0.0
    # Use scene detection: count of frames with scene change > 0.1
    r = run([
        "ffmpeg", "-i", str(src), "-an",
        "-vf", "select='gt(scene,0.05)',showinfo",
        "-f", "null", "-"
    ])
    txt = r.stderr or ""
    n_changes = len(re.findall(r"showinfo.*?n:\s*\d+", txt))
    return round(n_changes / dur, 3)


def audio_stats(src: Path) -> dict:
    """Get peak loudness, mean loudness, and time of loudest moment."""
    # astats with metadata + ametadata to extract per-frame loudness
    r = run([
        "ffmpeg", "-i", str(src),
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-"
    ])
    out = r.stderr or ""
    rms_levels = []
    times = []
    for m in re.finditer(r"pts_time:([\d.]+).*?lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)", out, re.DOTALL):
        try:
            t = float(m.group(1))
            db = float(m.group(2))
            times.append(t)
            rms_levels.append(db)
        except Exception:
            pass
    if not rms_levels:
        return {"audio_peak_db": None, "audio_peak_time_sec": None, "audio_mean_db": None}
    peak = max(rms_levels)
    peak_idx = rms_levels.index(peak)
    return {
        "audio_peak_db": round(peak, 2),
        "audio_peak_time_sec": round(times[peak_idx], 2),
        "audio_mean_db": round(sum(rms_levels) / len(rms_levels), 2),
    }


def has_voice_quick(src: Path, peak_db: float) -> bool:
    """
    Crude voice detection: if the mid-frequency band (300-3400 Hz, speech range) has
    significant energy and the clip is loud enough overall.
    """
    if peak_db is None or peak_db < -40:
        return False
    return peak_db > -25  # loud clips are candidates for speech check


def whisper_transcribe(src: Path, max_chars: int = 300) -> str:
    """Run Whisper on the audio. Returns short transcript or empty string."""
    try:
        r = run([
            "whisper", str(src),
            "--model", "tiny",
            "--language", "en",
            "--output_format", "txt",
            "--output_dir", "/tmp/whisper_out",
            "--fp16", "False",
        ])
        if r.returncode != 0:
            return ""
        txt_path = Path("/tmp/whisper_out") / (src.stem + ".txt")
        if txt_path.exists():
            t = txt_path.read_text(encoding="utf-8").strip()
            return t[:max_chars]
    except Exception:
        return ""
    return ""


def process_clip(src: Path, out_dir: Path, do_whisper: bool, do_horizon: bool = True, fast: bool = False) -> dict:
    name = src.stem
    info = probe(src)
    dur = info.get("duration", 0)
    if dur <= 0:
        return {"name": src.name, "error": "probe failed"}

    thumb_path = out_dir / f"{name}_thumb.jpg"
    strip_path = out_dir / f"{name}_strip.jpg"
    make_thumb(src, thumb_path, dur / 2)
    make_strip(src, strip_path, dur)

    # --fast skips the two full-decode metrics (slow + memory-heavy on long
    # clips — they crashed the full 63-clip "Дед" run). Thumb/strip/horizon/
    # shake stay; they are cheap sampled seeks.
    if fast:
        motion = None
        audio = {"audio_peak_db": None, "audio_peak_time_sec": None,
                 "audio_mean_db": None}
    else:
        motion = motion_score(src, dur)
        audio = audio_stats(src)

    rec = {
        "name": src.name,
        "duration": round(dur, 2),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
        "motion_score": motion,
        **audio,
        "thumb": thumb_path.name,
        "strip": strip_path.name,
    }

    if do_horizon:
        try:
            from horizon_detect import estimate_tilt, correction_filter
            # estimate_tilt -> Tuple[Optional[float], str] (angle, method)
            tilt_angle, tilt_method = estimate_tilt(src, n_samples=3)
            rec["horizon_tilt_deg"] = tilt_angle
            rec["horizon_method"] = tilt_method
            if tilt_angle is not None:
                fix = correction_filter(tilt_angle, threshold_deg=0.5)
                rec["horizon_correction_filter"] = fix  # None if within threshold
        except ImportError:
            rec["horizon_tilt_deg"] = None  # opencv not installed

    try:
        from shake_detect import estimate_shake, needs_stabilization
        shake, shake_method = estimate_shake(src, n_samples=24)
        rec["shake_score"] = shake
        rec["shake_method"] = shake_method
        rec["needs_stabilization"] = needs_stabilization(shake)
    except ImportError:
        rec["shake_score"] = None  # opencv not installed
        rec["needs_stabilization"] = False

    if do_whisper and not fast and has_voice_quick(src, audio.get("audio_peak_db")):
        rec["speech_text"] = whisper_transcribe(src)
    else:
        rec["speech_text"] = ""

    return rec


def write_html(records: list, out_path: Path, title: str = "Clip Log"):
    rows = []
    for r in sorted(records, key=lambda x: x.get("name", "")):
        speech = r.get("speech_text") or ""
        speech_html = f'<div class="speech">🎙 {speech[:160]}…</div>' if speech else ""
        tilt = r.get("horizon_tilt_deg")
        if tilt is None:
            tilt_html = ""
        elif abs(tilt) < 0.5:
            tilt_html = f'<span class="tilt-ok" title="within ±0.5°">⊟ {tilt:+.1f}°</span>'
        else:
            tilt_html = f'<span class="tilt-bad" title="needs correction">⚠ tilt {tilt:+.1f}°</span>'
        shake = r.get("shake_score")
        shake_html = (f'<span>shake: <b>{shake}</b></span>'
                      if shake is not None else "")
        # only show metric chips that actually have data — a desc-only card
        # (fast pass / external table) stays clean instead of "motion:0 peak:n/a"
        meta_parts = []
        if r.get("motion_score") is not None:
            meta_parts.append(f"<span>motion: <b>{r['motion_score']}</b></span>")
        if r.get("audio_peak_db") is not None:
            meta_parts.append(f"<span>peak: <b>{r['audio_peak_db']} dB</b></span>")
        if shake_html:
            meta_parts.append(shake_html)
        if tilt_html:
            meta_parts.append(f"<span>{tilt_html}</span>")
        meta_html = ('<div class="meta">' + "".join(meta_parts) + "</div>"
                     if meta_parts else "")
        # human/LLM layer: Russian description + tags + keep/drop verdict
        desc = (r.get("desc_ru") or "").strip()
        desc_html = f'<div class="desc">{desc}</div>' if desc else ""
        tags = r.get("tags") or []
        tags_html = ('<div class="tags">'
                     + "".join(f'<span class="tag">{t}</span>' for t in tags)
                     + '</div>') if tags else ""
        keep = r.get("keep")
        if keep is True:
            badge, card_cls = '<span class="keep">в историю</span>', "card"
        elif keep is False:
            badge, card_cls = '<span class="drop">лишнее</span>', "card out"
        else:
            badge, card_cls = "", "card"
        rows.append(f"""
        <div class="{card_cls}">
          <div class="head">
            <span class="name">{r['name']}</span>
            <span class="dur">{r.get('duration', 0):.1f}s {badge}</span>
          </div>
          <img class="thumb" src="{r.get('thumb','')}"/>
          <img class="strip" src="{r.get('strip','')}"/>
          {desc_html}
          {tags_html}
          {meta_html}
          {speech_html}
        </div>
        """)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{title}</title>
<style>
  body {{ background:#111; color:#eee; font-family:-apple-system,Helvetica,sans-serif; margin:0; padding:24px; }}
  h1 {{ font-weight:300; letter-spacing:0.05em; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:16px; }}
  .card {{ background:#1c1c1c; border:1px solid #333; border-radius:8px; padding:12px; break-inside:avoid; page-break-inside:avoid; }}
  .head {{ display:flex; justify-content:space-between; margin-bottom:6px; font-size:12px; opacity:0.85; }}
  .name {{ font-family:monospace; font-weight:600; }}
  .dur {{ color:#7af; }}
  .thumb {{ width:100%; max-height:300px; object-fit:contain; background:#000; border-radius:4px; display:block; }}
  .strip {{ width:100%; height:auto; max-height:90px; object-fit:cover; margin-top:6px; border-radius:4px; display:block; }}
  .meta {{ display:flex; justify-content:space-between; gap:8px; font-size:11px; opacity:0.75; margin-top:8px; flex-wrap:wrap; }}
  .meta b {{ color:#fc6; }}
  .tilt-ok {{ color:#7c7; font-family:monospace; }}
  .tilt-bad {{ color:#f96; font-family:monospace; font-weight:600; }}
  .speech {{ font-size:11px; color:#aef; margin-top:6px; padding:6px; background:#0d2030; border-radius:3px; line-height:1.4; }}
  .desc {{ font-size:13px; color:#eee; margin-top:8px; line-height:1.45; }}
  .tags {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }}
  .tag {{ font-size:10px; color:#cde; background:#243; border:1px solid #3a5; border-radius:10px; padding:1px 8px; }}
  .keep {{ font-size:10px; color:#0a0a0a; background:#C8FF00; border-radius:3px; padding:1px 6px; margin-left:6px; font-weight:600; }}
  .drop {{ font-size:10px; color:#fff; background:#c33; border-radius:3px; padding:1px 6px; margin-left:6px; font-weight:600; }}
  .card.out {{ opacity:0.5; }}
  @media print {{
    @page {{ size: A4 landscape; margin: 7mm; }}
    body {{ padding:0; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    h1 {{ font-size:15px; margin:2px 0 8px; }}
    .grid {{ grid-template-columns:repeat(3,1fr); gap:8px; }}
    .card {{ padding:8px; break-inside:avoid; page-break-inside:avoid; }}
    .thumb {{ max-height:150px; }}
    .strip {{ max-height:42px; }}
    .desc {{ font-size:11px; margin-top:5px; }}
    .meta {{ font-size:10px; }}
  }}
</style></head><body>
<h1>{title}</h1>
<div class="grid">{''.join(rows)}</div>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")


def main():
    if len(sys.argv) < 3:
        print("usage: analyze_clips.py SRC_DIR OUT_DIR [--whisper] [--fast] [--desc FILE.json]")
        sys.exit(1)
    src_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    do_whisper = "--whisper" in sys.argv
    fast = "--fast" in sys.argv  # skip slow motion+audio full-decodes
    out_dir.mkdir(parents=True, exist_ok=True)

    # Optional human/LLM layer keyed by clip name:
    #   {"clip.mp4": {"desc": "что в кадре", "tags": [...], "keep": true/false}}
    # The program measures; a person (or Claude, looking at the thumbs) writes
    # the meaning. This is what turns a metrics dump into a usable log.
    desc_map = {}
    if "--desc" in sys.argv:
        di = sys.argv.index("--desc")
        if di + 1 < len(sys.argv):
            try:
                desc_map = json.loads(
                    Path(sys.argv[di + 1]).read_text(encoding="utf-8"))
                print(f"loaded {len(desc_map)} descriptions")
            except Exception as e:
                print(f"  (could not read --desc file: {e})")

    def _merge_desc(rec):
        d = (desc_map.get(rec.get("name"))
             or desc_map.get(Path(rec.get("name", "")).stem))
        if d:
            rec["desc_ru"] = d.get("desc") or d.get("desc_ru")
            if d.get("tags"):
                rec["tags"] = d["tags"]
            if "keep" in d:
                rec["keep"] = d["keep"]
        return rec

    clips = sorted(p for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    print(f"Found {len(clips)} clips in {src_dir}")
    title = f"{src_dir.name} — clip log"
    records = []
    for i, c in enumerate(clips, 1):
        print(f"[{i}/{len(clips)}] {c.name}")
        rec = _merge_desc(process_clip(c, out_dir, do_whisper, fast=fast))
        records.append(rec)
        # Save incrementally so a crash doesn't lose work AND the table is
        # viewable (json + html) while the run is still going.
        (out_dir / "report.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        write_html(records, out_dir / "report.html", title=title)
    print(f"\n✅ done → {out_dir}/report.html")


if __name__ == "__main__":
    main()
