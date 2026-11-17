# What data the bridge sends, and why

In short: by default the bridge sends an anonymized outline of your edit, so the assistant can learn to cut the way working editors cut. Video, audio, file names and project names are never sent. If you do not want this: clear one checkbox, and everything sent is deleted.

## How it is turned on and off

1. Sending is on by default from the first run of version 0.8.3. Only work done after that moment is sent. Everything already on your computer (old projects, their autosaves, the journal) stays here.
2. On the first connection check, Claude tells you once that sending is on and how to turn it off. In the Claude Bridge panel in Premiere the "Help train the assistant" checkbox is ticked, with "On by default" next to it.
3. If you do not want it: clear the checkbox or tell Claude "don't send my journal". Sending stops, and everything already sent is deleted from the server at once. This choice is kept across bridge updates.
4. If you turned sending off in version 0.8.1 or 0.8.2, it stays off.
5. If you want it again later: tick the checkbox. Only work from that moment on will be sent.

## Where the bridge looks for your work

On your computer, without waiting for Claude to ask the editing program. The bridge works while Claude is open. Work saved while Claude is closed is sent the next time it opens. The list of projects, their paths and names stay on the computer. Only what is listed below leaves it, in anonymized form.

Premiere Pro: the list of recent projects from Premiere's preferences, the saved projects themselves and the autosave folders.

DaVinci Resolve (since version 0.8.7):

1. The Resolve project library. Each Resolve project keeps its data in a `Project.db` file inside the library: on Mac it is `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Resolve Project Library`, on Windows `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Resolve Project Library`. If you have connected other libraries on disks in Resolve, the bridge takes their list from `dblist.conf` in Resolve's settings. The bridge copies the project file and reads the copy; it does not touch the Resolve project itself. With Live Save (on by default in Resolve) the file is updated on every edit, and the bridge looks into it once every 10 minutes.
2. The Resolve backups folder: project backups (Preferences > User > Project Save and Load > Project backups) and `.drp` files if they lie in this folder. By default this is `~/Movies/Resolve Project Backups` on Mac and `Videos\Resolve Project Backups` on Windows, or the folder chosen in the Project backup location field. Timeline backups (`Timeline.<date>` files in the same folder) are not read by the bridge.
3. Resolve settings, only to know where everything lives and whether backups are on: `config.user.xml` and `dblist.conf` in `~/Library/Preferences/Blackmagic Design/DaVinci Resolve` on Mac or `%APPDATA%\Blackmagic Design\DaVinci Resolve\Preferences` on Windows.

The bridge does not read projects on a Resolve network database (PostgreSQL). To read the Resolve database the bridge needs Node 22.5 or newer; on an older Node it sees only `.drp` files in the backups folder. To check what the bridge sees on your computer, tell Claude "check journal sending".

## What is sent

- Editing decisions: where the cut is, how many frames were trimmed from the head and tail of a shot, where a clip was moved, where an edit was split, where speed was changed, J and L cuts.
- The shape of the sequence as the project was saved after sending was turned on: shot lengths, cuts per minute, frame size, frame rate.
- The time of the decision (date and hour) and the file extension (mov, mp4), without the name.

## What is not sent

- Frames, video, audio, subtitles.
- Names of files, clips, sequences, projects, folders, text of markers and comments.
- Your name, email, IP address. The server does not record IP.

## How it is anonymized

Every name is replaced by a code like `h3f9a1c0b2d4e`. The code is computed with a secret that lives only on your computer, so on the server it cannot be turned back into a name, even by guessing. Instead of you, the server holds a random install number. If you turn sending off, the number and the secret are erased, and new ones are created when you turn it on again.

You can see what will be sent on the next send at any moment: tell Claude "show what the bridge will send" or run `node mcp-server/pk.js share preview`.

## Who stores it and for how long

- Recipient: Vladimir Koptsev, film director, Barcelona, author of the bridge. Contact: koptsev63@gmail.com, editors' chat https://t.me/+Fw5nSiqUkCswNjBi
- The server is in the European Union (Hetzner, Helsinki).
- Retention: up to 3 years, then it is deleted. Earlier if you turned sending off or asked for deletion.
- The data is not sold and not passed to third parties for training their models.
- Legal basis: the legitimate interest of the bridge author in making the editing assistant better (GDPR Article 6.1.f). The data is anonymized, and you may object at any time (Article 21): one checkbox or one phrase, sending stops, and what was sent is deleted.
- What has already gone into the assistant's trained rules cannot be withdrawn. The records themselves are deleted.

## Other people's material and NDA

Any project can be excluded: in the panel tick "Don't send this project" or tell Claude "don't send this project". A DaVinci Resolve project is excluded by name, as it is called in the Project Manager: "don't send project <name>". The bridge does not even record an excluded Resolve project in the journal on your computer. Nothing from it will be sent, neither now nor later, even if you bring it back afterwards: only new work will be sent again. What was sent before the exclusion stays on the server until you clear the main checkbox (then everything is deleted).

If you are editing a client's project under NDA, exclude it before you start work or turn sending off until you agree with the client. The bridge sends neither pictures nor names, but the decision is yours and the project owner's.

This page is the bridge's privacy policy.

---

# Short summary

On by default from 0.8.3, off with one click. The bridge sends an anonymized outline of your edit: cut points, frames trimmed from heads and tails, moves, splits, speed changes, J/L cuts, shot lengths, cuts per minute, frame size and rate, timestamps and file extensions. It never sends video, audio, captions, or any file, clip, sequence, project or folder name, marker text, your name, email or IP address.

The bridge finds your work on your machine, without waiting for Claude to ask your editing app. Premiere: its recent projects list in its preferences, the saved projects and their auto-save folders. DaVinci Resolve (since 0.8.7): each project's `Project.db` in Resolve's project library (`~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Resolve Project Library` on a Mac, `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Resolve Project Library` on Windows, plus any disk library listed in `dblist.conf`), read from a copy so Resolve is never touched; the project backups and `.drp` files in Resolve's backup folder (`~/Movies/Resolve Project Backups` or `Videos\Resolve Project Backups` by default, or your Project backup location); timeline backups (`Timeline.<date>`) and network (PostgreSQL) libraries are not read; reading Resolve's database needs Node 22.5 or newer. A Resolve project is kept out by its name. That list, its paths and names stay on your machine. It runs while Claude is open; work saved with Claude closed goes on the next start. A sequence's shape is sent as saved after the switch. Only work done after the first run of 0.8.3 (or after you turn it back on) is sent; older projects, autosaves and journal entries stay on your machine. Any project can be kept out for good (panel checkbox "Don't send this project", or tell Claude "don't send this project"); work done while it is out never goes. Names become salted hashes; the salt never leaves your computer. You appear as a random install id. Claude tells you once that it is on and how to turn it off. Turning it off (panel checkbox, or tell Claude "don't send my journal") stops it for good, survives updates and deletes everything sent under that id at once and drops the id and salt. Preview the next send with `node mcp-server/pk.js share preview`.

Controller: Vladimir Koptsev, Barcelona, koptsev63@gmail.com. Server in the EU (Hetzner, Helsinki). Kept up to 3 years. Not sold or shared for third-party training. Lawful basis: legitimate interest in improving the editing assistant (GDPR art. 6(1)(f)), with anonymized data and the right to object at any time (art. 21) by one click. A no given in 0.8.1 or 0.8.2 stays a no. What already shaped trained rules cannot be recalled; the records themselves are deleted. This page is the bridge's privacy policy.
