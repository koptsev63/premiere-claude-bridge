// "Is Premiere waiting for a click?" Any modal window stops the ExtendScript
// thread: evalScript never answers, the bridge sees a timeout, and on the
// 8088 debug port the page still answers Runtime.evaluate (checked live
// 24.10.2026: `1+1` came back while a script sat in a modal).
//
// Premiere's own UI is not exposed to Accessibility (System Events lists no
// windows for the process, only AXLayoutArea elements), so the window title
// comes from the Quartz window list (CGWindowListCopyWindowInfo via python3):
// a modal dialog is an extra on-screen window with a title, next to the
// project window. The user-facing text is Russian: the UI is Russian.

import { execFile } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";

export const PROCESS_NAMES = ["Adobe Premiere Pro 2026", "Adobe Premiere Pro 2025", "Adobe Premiere Pro"];
const BASELINE_FILE = join(homedir(), ".claude", "state", "premiere-windows-baseline.json");

// What each known dialog wants. Button names are those of the Russian UI.
export const KNOWN_DIALOGS = [
  { match: /Создать эпизод|New Sequence/i, action: "«Отмена»: секвенцию создаст мост без диалога (createNewSequenceFromClips)", key: "escape" },
  { match: /Несоответствие медиаданных|Media Mismatch/i, action: "«Отмена»: иначе Premiere удалит видео-ссылки из секвенции", key: "escape" },
  { match: /Импорт файлов|Import Files|Импорт/i, action: "подождать: идёт импорт; если окно с кнопками - «Отмена»", key: "wait" },
  { match: /Сохранить|Save/i, action: "«Да» сохранить или «Нет»", key: null },
  { match: /Script Alert|Предупреждение сценария|Alert/i, action: "«OK»", key: "return" },
  { match: /Общие сведения|Link Media|Связать медиаданные/i, action: "«Отмена»", key: "escape" },
];

const PY_WINDOWS = `
import json, Quartz
wl = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
out = []
for w in wl:
    if 'Premiere' not in str(w.get('kCGWindowOwnerName', '')): continue
    b = w.get('kCGWindowBounds', {})
    out.append({"id": w.get('kCGWindowNumber'), "name": w.get('kCGWindowName') or "", "layer": w.get('kCGWindowLayer'),
                "w": int(b.get('Width', 0)), "h": int(b.get('Height', 0)), "x": int(b.get('X', 0)), "y": int(b.get('Y', 0))})
print(json.dumps(out, ensure_ascii=False))
`;

export function listPremiereWindows() {
  return new Promise((resolve) => {
    execFile("python3", ["-c", PY_WINDOWS], { timeout: 8000, encoding: "utf8" }, (e, stdout) => {
      if (e) return resolve({ error: e.message, windows: [] });
      try { resolve({ windows: JSON.parse(stdout) }); } catch (x) { resolve({ error: x.message, windows: [] }); }
    });
  });
}

export function premiereRunning() {
  return new Promise((resolve) => {
    execFile("pgrep", ["-f", "Adobe Premiere Pro"], { encoding: "utf8" }, (e, stdout) => resolve(!e && stdout.trim().length > 0));
  });
}

function readBaseline() {
  try { return JSON.parse(readFileSync(BASELINE_FILE, "utf8")); } catch { return null; }
}

// Remember the windows of a responsive Premiere, so a later check can tell
// which window is new. Cheap: called after a successful command.
export async function rememberBaseline(windows) {
  const list = windows || (await listPremiereWindows()).windows;
  try {
    mkdirSync(dirname(BASELINE_FILE), { recursive: true });
    writeFileSync(BASELINE_FILE, JSON.stringify({ t: Date.now(), windows: list }));
  } catch {}
  return list;
}

// Pure: which windows look like a modal dialog, given a baseline.
// Without a baseline only titled windows count: Premiere's floating panels
// have no title, its dialogs do.
export function pickDialogs(windows, baseline) {
  const base = new Set((baseline || []).map((w) => w.id));
  const biggest = Math.max(0, ...windows.map((w) => w.w * w.h));
  return windows.filter((w) => {
    if (w.layer !== 0) return false;
    if (w.w * w.h === biggest && windows.length > 1) return false; // the project window
    if (base.has(w.id)) return false;
    if (!baseline && !w.name) return false;
    return true;
  });
}

export function describeDialog(title) {
  for (const d of KNOWN_DIALOGS) if (d.match.test(title || "")) return d;
  return null;
}

// The human message (Russian) for a blocked Premiere.
export function blockedMessage(diag) {
  if (!diag.running) return "Premiere не запущен.";
  if (!diag.portAlive) return "Premiere запущен, но панель Claude Bridge не отвечает на порту 8088: откройте её (Окно > Расширения > Claude Bridge).";
  if (!diag.blocked) return "Premiere отвечает.";
  const d = diag.dialogs || [];
  if (!d.length) {
    return "Premiere не отвечает на скрипты, а порт жив: скорее всего, открыто модальное окно (или идёт долгая операция). Окно не опознано: посмотрите на экран Premiere и закройте диалог.";
  }
  const lines = d.map((w) => {
    const known = describeDialog(w.name);
    const title = w.name ? `«${w.name}»` : `окно без заголовка ${w.w}x${w.h}`;
    return known ? `${title}: нажмите ${known.action}` : `${title}: закройте его («Отмена» или «OK»)`;
  });
  return "Premiere ждёт клика: " + lines.join("; ") + ".";
}

// Full check. `pingScript` runs a trivial ExtendScript with a short timeout
// and resolves true on answer; `pingPort` resolves true if the CEF page answers.
export async function diagnose({ pingPort, pingScript, timeoutMs = 4000 } = {}) {
  const diag = { running: await premiereRunning(), portAlive: false, scriptAnswers: false, blocked: false, dialogs: [], windows: [] };
  if (!diag.running) return { ...diag, message: blockedMessage(diag) };
  diag.portAlive = pingPort ? await pingPort().catch(() => false) : false;
  if (diag.portAlive && pingScript) {
    diag.scriptAnswers = await Promise.race([
      pingScript().then(() => true).catch(() => false),
      new Promise((r) => setTimeout(() => r(false), timeoutMs)),
    ]);
  }
  diag.blocked = diag.portAlive && !diag.scriptAnswers;
  const wins = await listPremiereWindows();
  diag.windows = wins.windows;
  const baseline = readBaseline();
  if (diag.blocked) diag.dialogs = pickDialogs(wins.windows, baseline ? baseline.windows : null);
  else if (wins.windows.length && diag.scriptAnswers) await rememberBaseline(wins.windows);
  diag.message = blockedMessage(diag);
  return diag;
}
