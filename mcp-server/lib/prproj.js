// Read a saved .prproj (or an Auto-Save copy) into the same snapshot shape
// the live bridge produces (lib/seq.js snapshotJsx): every sequence, its
// tracks, every clip with start/end on the timeline and in/out in the
// source, the media path. Premiere writes an autosave every few minutes,
// so a folder of them is the editor's day, readable without the panel.
//
// Object graph (Premiere Pro 26, checked on real files 02.11.2026):
//   Sequence(ObjectUID) -> Name, TrackGroups/TrackGroup{First=media type
//   GUID, Second ObjectRef} -> VideoTrackGroup|AudioTrackGroup(ObjectID)
//   -> Tracks/Track{Index, ObjectURef} -> VideoClipTrack|AudioClipTrack
//   (ObjectUID) -> ClipItems/TrackItems/TrackItem{ObjectRef} ->
//   VideoClipTrackItem|AudioClipTrackItem(ObjectID) -> TrackItem{Start?,End},
//   SubClip ObjectRef -> SubClip(ObjectID){Name, Clip ObjectRef} ->
//   VideoClip|AudioClip(ObjectID) -> Clip{InPoint, OutPoint, Source ObjectRef}
//   -> VideoMediaSource|AudioMediaSource(ObjectID) -> Media ObjectURef ->
//   Media(ObjectUID){FilePath|ActualMediaFilePath, Title}.
//   <Start> is omitted when the item sits at 0. Ticks: 254016000000 per second.

import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { basename } from "node:path";

export const TPS = 254016000000;
const VIDEO_TYPE = "228cda18-3625-4d2d-951e-348879e4ed93";
const AUDIO_TYPE = "80b8e3d5-6dca-4195-aefb-cb5f407ab009";

export function readProjectXml(path) {
  const buf = readFileSync(path);
  const xml = buf[0] === 0x1f && buf[1] === 0x8b ? gunzipSync(buf) : buf;
  return xml.toString("utf8");
}

// Index every element that carries ObjectID or ObjectUID: id -> { tag, body }.
export function indexObjects(xml) {
  const byId = new Map(), byUid = new Map();
  const open = /<(\w+)\s+(ObjectID|ObjectUID)="([^"]+)"[^>]*>/g;
  let m;
  while ((m = open.exec(xml))) {
    const [, tag, kind, id] = m;
    const close = xml.indexOf(`</${tag}>`, m.index + m[0].length);
    if (close < 0) continue;
    const body = xml.slice(m.index + m[0].length, close);
    (kind === "ObjectID" ? byId : byUid).set(id, { tag, body });
  }
  return { byId, byUid };
}

const text = (body, tag) => { const m = new RegExp(`<${tag}>([^<]*)</${tag}>`).exec(body); return m ? m[1] : null; };
const ref = (body, tag) => { const m = new RegExp(`<${tag}\\s+Object(?:U?Ref)="([^"]+)"`).exec(body); return m ? m[1] : null; };
const sec = (ticks) => (ticks == null ? null : Number(ticks) / TPS);
const unescape = (s) => (s == null ? s : s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'"));

function mediaPath(idx, clipBody) {
  const srcId = ref(clipBody, "Source");
  const src = srcId && idx.byId.get(srcId);
  if (!src) return { path: "", title: "" };
  const mediaUid = ref(src.body, "Media");
  const media = mediaUid && idx.byUid.get(mediaUid);
  if (!media) return { path: "", title: "" };
  return {
    path: unescape(text(media.body, "ActualMediaFilePath") || text(media.body, "FilePath") || ""),
    title: unescape(text(media.body, "Title") || ""),
  };
}

function clipRow(idx, itemId) {
  const item = idx.byId.get(itemId);
  if (!item) return null;
  const start = sec(text(item.body, "Start")) ?? 0;
  const end = sec(text(item.body, "End"));
  if (end == null) return null;
  const subId = ref(item.body, "SubClip");
  const sub = subId && idx.byId.get(subId);
  const clipId = sub && ref(sub.body, "Clip");
  const clip = clipId && idx.byId.get(clipId);
  const row = { name: unescape(sub ? text(sub.body, "Name") : "") || "", start, end, duration: end - start, inPoint: null, outPoint: null, path: "" };
  if (clip) {
    row.inPoint = sec(text(clip.body, "InPoint"));
    row.outPoint = sec(text(clip.body, "OutPoint"));
    const mp = mediaPath(idx, clip.body);
    row.path = mp.path;
    if (!row.name) row.name = mp.title || basename(mp.path);
  }
  return row;
}

function tracksOf(idx, groupId, kind) {
  const group = idx.byId.get(groupId);
  if (!group) return [];
  const out = [];
  const re = /<Track\s+Index="(\d+)"\s+ObjectURef="([^"]+)"/g;
  let m;
  while ((m = re.exec(group.body))) {
    const track = idx.byUid.get(m[2]);
    const clips = [];
    if (track) {
      const items = /<TrackItem\s+Index="\d+"\s+ObjectRef="([^"]+)"/g;
      let it;
      while ((it = items.exec(track.body))) { const row = clipRow(idx, it[1]); if (row) clips.push(row); }
    }
    clips.sort((a, b) => a.start - b.start);
    out.push({ kind, index: Number(m[1]), name: "", clipCount: clips.length, clips });
  }
  out.sort((a, b) => a.index - b.index);
  return out;
}

// All sequences of the project in snapshot shape.
export function readSequences(xml) {
  const idx = indexObjects(xml);
  const seqs = [];
  for (const [uid, obj] of idx.byUid) {
    if (obj.tag !== "Sequence") continue;
    const name = unescape(text(obj.body, "Name")) || "";
    const info = { name, sequenceID: uid, video: [], audio: [], markers: null };
    const groups = /<TrackGroup\s+Version="\d+"\s+Index="\d+">\s*<First>([^<]+)<\/First>\s*<Second\s+ObjectRef="([^"]+)"/g;
    let g;
    while ((g = groups.exec(obj.body))) {
      if (g[1] === VIDEO_TYPE) {
        info.video = tracksOf(idx, g[2], "video");
        const vg = idx.byId.get(g[2]);
        if (vg) {
          const fr = text(vg.body, "FrameRect"); // "0,0,1080,1920"
          if (fr) { const p = fr.split(",").map(Number); info.width = p[2]; info.height = p[3]; }
          const fps = text(vg.body, "FrameRate");
          if (fps) { info.fpsTicks = fps; info.fps = TPS / Number(fps); }
        }
      } else if (g[1] === AUDIO_TYPE) info.audio = tracksOf(idx, g[2], "audio");
    }
    let end = 0;
    for (const t of [...info.video, ...info.audio]) for (const c of t.clips) if (c.end > end) end = c.end;
    info.end = end;
    seqs.push(info);
  }
  seqs.sort((a, b) => a.name.localeCompare(b.name));
  return seqs;
}

// Autosave file name: "<project>--<uuid>-YYYY-MM-DD_HH-MM-SS.prproj".
export function autosaveStamp(path) {
  const m = /^(.*?)--[0-9a-f-]{36}-(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.prproj$/i.exec(basename(path));
  if (!m) return null;
  return { project: m[1], t: new Date(`${m[2]}-${m[3]}-${m[4]}T${m[5]}:${m[6]}:${m[7]}`).toISOString() };
}

export function snapshotFromProject(path, { mtime } = {}) {
  const xml = readProjectXml(path);
  const stamp = autosaveStamp(path);
  return {
    t: stamp ? stamp.t : (mtime || new Date()).toISOString(),
    source: stamp ? "autosave" : "project",
    file: path,
    project: stamp ? stamp.project + ".prproj" : basename(path),
    sequences: readSequences(xml),
  };
}
