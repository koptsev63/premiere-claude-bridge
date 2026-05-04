#!/usr/bin/env python3
"""v5 final: v3 object-leveled body + intro + outro + Bride's Cigar (Suno)."""
import math, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BODY = Path("/Users/kopetan_kakao/Desktop/Grave stakes/GRAVE_STAKES_teaser_v3_object_leveled.mp4")
MUSIC = Path("/Users/kopetan_kakao/Desktop/Grave stakes/music_candidates/B2_balkan_Brides_Cigar.mp3")
OUT_DIR = Path("/Users/kopetan_kakao/Desktop/Grave stakes")
WORK = Path("/tmp/v5_build")
WORK.mkdir(exist_ok=True)

assert BODY.exists() and MUSIC.exists(), "missing inputs"

# Fonts
FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
]
def load_font(size):
    for p in FONT_PATHS:
        try: return ImageFont.truetype(p, size)
        except Exception: pass
    return ImageFont.load_default()

# Title cards
W, H = 1920, 1080

def make_title(out, lines, sizes, colors=None, bottom_text=None):
    img = Image.new("RGB", (W, H), (8, 8, 10))
    d = ImageDraw.Draw(img)
    if colors is None:
        colors = [(245, 240, 232)] * len(lines)
    fonts = [load_font(s) for s in sizes]
    heights = []
    for line, font in zip(lines, fonts):
        bbox = d.textbbox((0, 0), line, font=font)
        heights.append(bbox[3] - bbox[1])
    total_h = sum(heights) + 36 * (len(lines) - 1)
    y = (H - total_h) // 2
    for line, font, h, color in zip(lines, fonts, heights, colors):
        bbox = d.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        d.text(((W - w) // 2, y), line, font=font, fill=color)
        y += h + 36
    if bottom_text:
        sf = load_font(22)
        bb = d.textbbox((0, 0), bottom_text, font=sf)
        bw = bb[2] - bb[0]
        d.text(((W - bw) // 2, H - 56), bottom_text, font=sf, fill=(140, 140, 140))
    img.save(out, "PNG")

intro_png = WORK / "intro.png"
outro_png = WORK / "outro.png"

make_title(intro_png,
           ["GRAVE STAKES", "a short film by Vladimir Koptsev"],
           [220, 44])

make_title(outro_png,
           ["GRAVE STAKES", "DIG DEEP.  GO FAST."],
           [240, 64],
           bottom_text="Music: Bride's Cigar (Suno AI)")

print("titles → PNG")

# Intro/outro MP4 segments (3s and 8s) with fades
def png_to_mp4(png, out, dur, fade_in=0.6, fade_out=0.6):
    fade_out_start = max(0, dur - fade_out)
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(png),
        "-t", str(dur),
        "-vf", f"fade=t=in:st=0:d={fade_in},fade=t=out:st={fade_out_start}:d={fade_out}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-r", "25", "-pix_fmt", "yuv420p", str(out)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

intro_mp4 = WORK / "intro.mp4"
outro_mp4 = WORK / "outro.mp4"
png_to_mp4(intro_png, intro_mp4, 3.0, fade_in=0.8, fade_out=0.5)
png_to_mp4(outro_png, outro_mp4, 8.0, fade_in=0.6, fade_out=1.5)
print("intro/outro MP4 → done")

# Concat video: intro (3s) + body (61s) + outro (8s) = 72s
list_file = WORK / "list.txt"
list_file.write_text("\n".join([
    f"file '{intro_mp4}'",
    f"file '{BODY}'",
    f"file '{outro_mp4}'",
]))

video_concat = WORK / "video_concat.mp4"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", str(list_file),
    "-c", "copy", "-an",
    str(video_concat)
], check=True, capture_output=True)
print("video concat → done")

# Probe final video duration
r = subprocess.run([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=nw=1:nk=1", str(video_concat)
], capture_output=True, text=True)
total_dur = float(r.stdout.strip())
print(f"total video dur = {total_dur:.2f}s")

# Mix music (trim to total_dur, fade in/out, normalize ~-12 dB peak)
music_trimmed = WORK / "music_trim.mp3"
fade_in = 1.5
fade_out = 2.5
fade_out_start = total_dur - fade_out
subprocess.run([
    "ffmpeg", "-y", "-i", str(MUSIC),
    "-t", str(total_dur),
    "-af", f"afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_out_start}:d={fade_out},loudnorm=I=-16:TP=-1.5:LRA=11",
    "-c:a", "aac", "-b:a", "192k",
    str(music_trimmed)
], check=True, capture_output=True)
print(f"music trimmed + normalized → {music_trimmed.stat().st_size // 1024}KB")

# Merge
final = OUT_DIR / "GRAVE_STAKES_teaser_v5_FINAL.mp4"
subprocess.run([
    "ffmpeg", "-y", "-i", str(video_concat), "-i", str(music_trimmed),
    "-c:v", "copy", "-c:a", "copy",
    "-map", "0:v:0", "-map", "1:a:0",
    "-shortest",
    str(final)
], check=True, capture_output=True)

print(f"\n✅ FINAL → {final}")
print(f"   size: {final.stat().st_size / 1024 / 1024:.1f} MB")
print(f"   duration: {total_dur:.2f}s")
