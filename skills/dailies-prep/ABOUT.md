# dailies-prep: preparing a shooting day for editing

## What it is in a nutshell
A helper that takes the raw material of the day (cameras separately, recorder sound separately) and prepares it for editing in DaVinci Resolve: it aligns the sound to the picture, returns each clip its own sound, and points out where the picture is worth checking by eye.

## How it works, in parts

**1. Sound sync.**
On set, sound is recorded on a separate recorder, while the camera records its own picture and a quiet "scratch" sound. For the recorder's sound to land exactly under the picture, the offset has to be known. The helper does it like this: it reads from the files which scene and which take it is, finds which cameras were shooting at the same time (by timecode), then compares the camera's scratch sound with the recorder's sound and works out how many frames to shift. For every link it writes how confident it is. Uncertain ones are flagged so you can check them yourself. The result is the `takes.json` file, and the other parts rely on it.

**2. Restoring sound in the timeline.**
If the clips in an edited Resolve timeline have lost their sound or have the wrong one, the helper makes a copy of the timeline and puts under each clip the sound of its take, already shifted. Each recorder channel goes to its own track (Boom1 and Boom2 first). Video and sound are linked. The camera's own sound can be laid enabled, laid disabled, or not laid at all - that is your choice at launch. The camera and the recorder drift apart by milliseconds, so together they can sound like a flanger.

**3. Stepwise frame checking.**
Looking at everything densely is too expensive, so the check goes in steps from cheap to expensive: first numbers without viewing (where the picture changes), then 8 frames for each take of each camera, then the edges of shots. Detailed frames around cuts (one second before and one second after, three frames per second) are built only if the edit is seamless. If the edit is choppy (cutting, cutaways), such frames tell you nothing, and the helper does not make them.

## When it turns on
When you ask to: sync sound, assemble takes, restore sound in the timeline, check the picture of an edit, or when you mention `takes.json`, dailies, or the recorder's MXF.

## What it reads and what it writes
- Reads: the MXF files of the cameras and the recorder (read-only, it changes nothing in them).
- Writes: `meta.json`, `takes.json` and picture sheets into a folder you specify.
- In Resolve it creates a NEW timeline copy. It does not touch the original timeline and deletes nothing.

## What is needed for it to work
Python 3.13 with the packages `numpy`, `av`, `Pillow`; for the Resolve part - Resolve Studio with external scripts enabled (Local mode); sound and cameras in MXF format with the scene and take in the headers.

## What it cannot do and where it can go wrong
- The offset is found from the sound. If a link is flagged as unreliable, it must not be trusted without checking by ear.
- It works with recorder MXF in the Avid OP-Atom format (one file per channel). It has not been tested with other formats.
- The Resolve part was carried over from a working script and has not been run in Resolve since the transfer. Do the first run on a copy of the project.

## How to remove it
Delete the `dailies-prep` folder from `~/.claude/skills/`. The result files stay wherever you saved them.
