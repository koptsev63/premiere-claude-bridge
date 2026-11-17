# nle-handoff: edit handoff to Premiere and Avid

## What it is in brief
A helper that turns your DaVinci Resolve project into files that can be opened in Premiere Pro or Avid Media Composer, and checks those files before you send them to the editor.

## How it works, in parts

**1. Premiere project.**
The helper reads the open Resolve project (read-only) and builds a `.prproj` file: folders with clips, all timelines, tracks, clips with their position, in and out points, speed, frame position and scale, and volume. The base is taken from your real Premiere project, which you give as a template: its settings are kept and its content is replaced with yours. Resolve color and effects are not created in Premiere: such a clip is marked with a color, and the final report says what was carried over and what was not.

**2. Avid bin and project settings.**
The bin (`.avb`) is the project itself with the material: sound clips of the takes with columns (scene, take, channel names), camera clips, "sync" clips (camera with sound, already aligned) and "group" clips (a choice between cameras). The project settings are a separate `.avp` file (and `Settings.xml`): frame rate, size, color space. The helper prepares them from your real Avid project: it changes only the project number, the time and the format parameters.

**3. AAF and XML check.**
Before sending, the helper checks the file: how many objects it holds, whether the frame rate is consistent, whether any references point to nothing, whether the files are on disk. Resolve writes all reels (tapes) in the AAF without a name; the helper can fill in the names from the files.

## When it kicks in
When you ask for: a project for Premiere, a bin for Avid, Avid project settings, to check or fix an AAF or XML, or say that a file will not open for the editor.

## What it reads and what it writes
- Reads: the open Resolve project (read-only), the MXF files used to build the bin, your project templates.
- Writes: new `.prproj`, `.avb`, `.avp` and `Settings.xml` files, and a fixed copy of the AAF. Source files and your templates are not changed.

## What it needs to work
Python 3.13; the packages `pyaaf2` (AAF), `pyavb` (Avid bins), `av` (reading MXF headers); Resolve Studio for the Premiere project; your templates: a real `.prproj` (saved by the oldest Premiere in which you need the result) and a real Avid project.

## What it cannot do and where it can be wrong
- None of the output files was opened in its target application: only structure, references and a comparison against the source were checked. The first file the editor opens is the real test.
- Not carried over to Premiere: Resolve grade and effects, volume fades and crossfades, transitions, markers, opacity, compound clips, titles.
- In `.avp` the list of bins stays from the template, and Avid will show them as missing. One ready format is known (1080p, 24 fps); others need a sample of such a project.
- Fixing tape names in the AAF is a best effort, not a guarantee that Avid will open the file.

## How to remove it
Delete the `nle-handoff` folder from `~/.claude/skills/`. Created files stay wherever you saved them.
