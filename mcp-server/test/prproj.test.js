import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { readSequences, autosaveStamp, snapshotFromProject } from "../lib/prproj.js";

// A minimal project in the shape Premiere 26 writes (lib/prproj.js header).
const XML = `<?xml version="1.0"?><PremiereData Version="3">
<Sequence ObjectUID="seq-1" ClassID="x" Version="12"><Node/><TrackGroups Version="1">
 <TrackGroup Version="1" Index="0"><First>228cda18-3625-4d2d-951e-348879e4ed93</First><Second ObjectRef="10"/></TrackGroup>
 <TrackGroup Version="1" Index="1"><First>80b8e3d5-6dca-4195-aefb-cb5f407ab009</First><Second ObjectRef="20"/></TrackGroup>
</TrackGroups><Name>ПОДКАСТ 01 &amp; тест</Name></Sequence>
<VideoTrackGroup ObjectID="10" ClassID="x" Version="13"><TrackGroup Version="1"><Tracks Version="1"><Track Index="0" ObjectURef="vt-1"/></Tracks><FrameRate>8475667200</FrameRate></TrackGroup><FrameRect>0,0,1080,1920</FrameRect></VideoTrackGroup>
<AudioTrackGroup ObjectID="20" ClassID="x" Version="13"><TrackGroup Version="1"><Tracks Version="1"><Track Index="0" ObjectURef="at-1"/></Tracks></TrackGroup></AudioTrackGroup>
<VideoClipTrack ObjectUID="vt-1" ClassID="x" Version="1"><ClipTrack Version="2"><ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="100"/><TrackItem Index="1" ObjectRef="101"/></TrackItems></ClipItems></ClipTrack></VideoClipTrack>
<AudioClipTrack ObjectUID="at-1" ClassID="x" Version="1"><ClipTrack Version="2"><ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="102"/></TrackItems></ClipItems></ClipTrack></AudioClipTrack>
<VideoClipTrackItem ObjectID="100" ClassID="x" Version="8"><ClipTrackItem Version="8"><TrackItem Version="4"><End>254016000000</End></TrackItem><SubClip ObjectRef="200"/></ClipTrackItem></VideoClipTrackItem>
<VideoClipTrackItem ObjectID="101" ClassID="x" Version="8"><ClipTrackItem Version="8"><TrackItem Version="4"><Start>254016000000</Start><End>762048000000</End></TrackItem><SubClip ObjectRef="201"/></ClipTrackItem></VideoClipTrackItem>
<AudioClipTrackItem ObjectID="102" ClassID="x" Version="11"><ClipTrackItem Version="8"><TrackItem Version="4"><End>762048000000</End></TrackItem><SubClip ObjectRef="200"/></ClipTrackItem></AudioClipTrackItem>
<SubClip ObjectID="200" ClassID="x" Version="6"><Clip ObjectRef="300"/><Name>a.mp4</Name></SubClip>
<SubClip ObjectID="201" ClassID="x" Version="6"><Clip ObjectRef="301"/><Name>b.mp4</Name></SubClip>
<VideoClip ObjectID="300" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="400"/><InPoint>2540160000000</InPoint><OutPoint>2794176000000</OutPoint></Clip></VideoClip>
<VideoClip ObjectID="301" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="401"/><InPoint>0</InPoint><OutPoint>508032000000</OutPoint></Clip></VideoClip>
<VideoMediaSource ObjectID="400" ClassID="x" Version="2"><MediaSource Version="4"><Media ObjectURef="m-1"/></MediaSource></VideoMediaSource>
<VideoMediaSource ObjectID="401" ClassID="x" Version="2"><MediaSource Version="4"><Media ObjectURef="m-2"/></MediaSource></VideoMediaSource>
<Media ObjectUID="m-1" ClassID="x" Version="30"><FilePath>/Users/x/Курс ИИ/a.mp4</FilePath><Title>a.mp4</Title><ActualMediaFilePath>/Users/x/Курс ИИ/a.mp4</ActualMediaFilePath></Media>
<Media ObjectUID="m-2" ClassID="x" Version="30"><FilePath>/Users/x/b.mp4</FilePath><Title>b.mp4</Title></Media>
</PremiereData>`;

test("sequence, tracks, clips with start/end/in/out and media path, first clip at 0", () => {
  const seqs = readSequences(XML);
  assert.equal(seqs.length, 1);
  const s = seqs[0];
  assert.equal(s.name, "ПОДКАСТ 01 & тест"); assert.equal(s.sequenceID, "seq-1");
  assert.equal(s.width, 1080); assert.equal(s.height, 1920); assert.ok(Math.abs(s.fps - 29.97) < 0.001);
  assert.equal(s.video[0].clips.length, 2); assert.equal(s.audio[0].clips.length, 1);
  const [a, b] = s.video[0].clips;
  assert.deepEqual([a.start, a.end, a.inPoint, a.outPoint, a.path], [0, 1, 10, 11, "/Users/x/Курс ИИ/a.mp4"]);
  assert.deepEqual([b.start, b.end, b.inPoint, b.outPoint, b.path], [1, 3, 0, 2, "/Users/x/b.mp4"]);
  assert.equal(s.end, 3);
});

test("autosave file names carry the project and the time", () => {
  const st = autosaveStamp("/x/подкаст--996b7c3f-0732-83e7-1285-3939994eac3e-2026-10-26_15-09-10.prproj");
  assert.equal(st.project, "подкаст"); assert.match(st.t, /^2026-10-26T/);
  assert.equal(autosaveStamp("/x/подкаст.prproj"), null);
});

const AUTOSAVE = "/Users/kopetan_kakao/Desktop/Grave stakes/Adobe Premiere Pro Auto-Save";
test("a real Premiere 26 autosave reads like the live bridge", { skip: !existsSync(AUTOSAVE) && "no autosave folder here" }, () => {
  const f = readdirSync(AUTOSAVE).filter((x) => x.startsWith("подкаст--") && x.endsWith(".prproj")).sort().pop();
  const snap = snapshotFromProject(`${AUTOSAVE}/${f}`);
  assert.equal(snap.source, "autosave");
  const s = snap.sequences.find((q) => q.name.startsWith("ПОДКАСТ 01"));
  assert.ok(s && s.video[0].clips.length === 1);
  assert.ok(Math.abs(s.video[0].clips[0].inPoint - 9.9766) < 0.001);
  assert.match(s.video[0].clips[0].path, /01-remen-src\.mp4$/);
});
