#!/usr/bin/env python3
"""
v4 FINAL: intro + teaser + outro + music + fades.

Structure:
  [0-3s]   intro card "GRAVE STAKES" (fade in from black, hold)
  [3-64s]  teaser_v3_object_leveled body (61s)
  [64-72s] outro card "GRAVE STAKES / a film by Vladimir Koptsev / DIG DEEP. GO FAST."
  Music: Sneaky Snitch by Kevin MacLeod (CC BY 4.0), full length under,
         audio fade in 0-2s, audio fade out 70-72s.
  Video: fade in 0-1s, fade out 71-72s (very subtle on the body, soft on cards).
"""
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("/Users/kopetan_kakao/Desktop/Grave stakes")
TEASER = OUT_DIR / "GRAVE_STAKES_teaser_v3_object_leveled.mp4"
MUSIC = OUT_DIR / "sneaky_snitch_kevin_macleod_CC-BY.mp3"
WORK = Path("/tmp/v4_work")
WORK.mkdir(exist_ok=True)

# ---------- Font selection ----------
def find_font(size, bold=False):
    candidates = [
        ("/System/Library/Fonts/Helvetica.ttc", 0 if not bold else 1),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 0 if not bold else 1),
        ("/Library/Fonts/Arial.ttf", None),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", None),
        ("/System/Library/Fonts/SFNS.ttf", None),
    ]
    for path, idx in candidates:
        try:
            if Path(path).exists():
                if idx is not None:
                    return ImageFont.truetype(path, size, index=idx)
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ---------- Title cards ----------
def make_intro_png(out_path):
    """Black canvas, 'GRAVE STAKES' centered, sub line below."""
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)

    title_font = find_font(180, bold=True)
    sub_font = find_font(40)

    title = "GRAVE STAKES"
    sub = "a short film by Vladimir Koptsev"

    bbox = d.textbbox((0, 0), title, font=title_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((W - tw) / 2, (H - th) / 2 - 30), title, font=title_font, fill=(245, 245, 240))

    bbox2 = d.textbbox((0, 0), sub, font=sub_font)
    sw, sh = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
    d.text(((W - sw) / 2, (H + th) / 2 + 30), sub, font=sub_font, fill=(180, 180, 175))

    img.save(out_path, "PNG")

def make_outro_png(out_path):
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)

    title_font = find_font(220, bold=True)
    tag_font = find_font(56)
    cred_font = find_font(28)

    title = "GRAVE STAKES"
    tag = "DIG DEEP.   GO FAST."
    cred = "Music: «Sneaky Snitch» — Kevin MacLeod (CC BY 4.0)"

    bbox = d.textbbox((0, 0), title, font=title_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((W - tw) / 2, H / 2 - th - 40), title, font=title_font, fill=(245, 245, 240))

    bbox2 = d.textbbox((0, 0), tag, font=tag_font)
    sw, sh = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
    d.text(((W - sw) / 2, H / 2 + 40), tag, font=tag_font, fill=(200, 180, 150))

    bbox3 = d.textbbox((0, 0), cred, font=cred_font)
    cw, ch = bbox3[2] - bbox3[0], bbox3[3] - bbox3[1]
    d.text(((W - cw) / 2, H - ch - 40), cred, font=cred_font, fill=(110, 110, 110))

    img.save(out_path, "PNG")

intro_png = WORK / "intro.png"
outro_png = WORK / "outro.png"
make_intro_png(intro_png)
make_outro_png(outro_png)
print("titles → PNG done")

# ---------- Convert PNG → MP4 segments with internal fades ----------
def png_to_mp4(png, out, dur, fade_in=0.5, fade_out=0.5):
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", str(png),
        "-vf", f"fade=t=in:st=0:d={fade_in},fade=t=out:st={dur - fade_out}:d={fade_out},format=yuv420p",
        "-c:v", "libx264", "-r", "25", "-preset", "medium", "-crf", "18",
        "-an", str(out)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

INTRO_DUR = 3
OUTRO_DUR = 8
intro_mp4 = WORK / "intro.mp4"
outro_mp4 = WORK / "outro.mp4"
png_to_mp4(intro_png, intro_mp4, INTRO_DUR, fade_in=0.8, fade_out=0.5)
png_to_mp4(outro_png, outro_mp4, OUTRO_DUR, fade_in=0.6, fade_out=1.5)
print("intro/outro MP4 → done")

# ---------- Concat intro + teaser + outro ----------
concat_txt = WORK / "concat.txt"
concat_txt.write_text(
    f"file '{intro_mp4}'\n"
    f"file '{TEASER}'\n"
    f"file '{outro_mp4}'\n"
)
silent_cat = WORK / "video_silent.mp4"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-an", "-r", "25", "-pix_fmt", "yuv420p",
    str(silent_cat)
], check=True, capture_output=True)
print("video concat → done")

# ---------- Get final video duration & build music bed ----------
import json
r = subprocess.run([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "json", str(silent_cat)
], capture_output=True, text=True)
total_dur = float(json.loads(r.stdout)["format"]["duration"])
print(f"total video duration = {total_dur:.2f}s")

# Trim music to total_dur, apply audio fade in/out, keep volume ~ -8dB
music_trimmed = WORK / "music.aac"
subprocess.run([
    "ffmpeg", "-y", "-i", str(MUSIC),
    "-t", str(total_dur),
    "-af", f"afade=t=in:st=0:d=2,afade=t=out:st={total_dur - 2.5}:d=2.5,volume=0.55",
    "-c:a", "aac", "-b:a", "256k",
    str(music_trimmed)
], check=True, capture_output=True)
print(f"music trimmed → {music_trimmed.stat().st_size // 1024}KB")

# ---------- Mux video + music ----------
final = OUT_DIR / "GRAVE_STAKES_teaser_v4_FINAL.mp4"
subprocess.run([
    "ffmpeg", "-y",
    "-i", str(silent_cat),
    "-i", str(music_trimmed),
    "-c:v", "copy",
    "-c:a", "copy",
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-shortest",
    str(final)
], check=True, capture_output=True)
print(f"\n✅ FINAL → {final}")
print(f"   size: {final.stat().st_size / 1024 / 1024:.1f} MB")
print(f"   duration: {total_dur:.2f}s")
