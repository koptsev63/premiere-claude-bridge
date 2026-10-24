import { test } from "node:test";
import assert from "node:assert/strict";
import { pickDialogs, describeDialog, blockedMessage } from "../lib/dialog.js";

const main = { id: 1, name: "/Users/x/подкаст.prproj *", layer: 0, w: 1470, h: 839 };
const panel = { id: 2, name: "", layer: 0, w: 344, h: 286 };
const dlg = { id: 9, name: "Создать эпизод", layer: 0, w: 600, h: 500 };

test("pickDialogs: new titled window is a dialog; baseline windows and the project window are not", () => {
  assert.deepEqual(pickDialogs([main, panel, dlg], [main, panel]).map((w) => w.id), [9]);
  assert.deepEqual(pickDialogs([main, panel, dlg], null).map((w) => w.id), [9], "no baseline: untitled windows ignored");
  assert.deepEqual(pickDialogs([main, panel, { ...panel, id: 3 }], [main, panel]).map((w) => w.id), [3], "with a baseline a new untitled window counts");
  assert.deepEqual(pickDialogs([main, panel], [main, panel]), []);
});

test("known dialogs tell which button to press", () => {
  assert.match(describeDialog("Создать эпизод").action, /Отмена/);
  assert.match(describeDialog("Несоответствие медиаданных").action, /Отмена/);
  assert.equal(describeDialog("Something else"), null);
});

test("messages are Russian and name the window", () => {
  assert.equal(blockedMessage({ running: false }), "Premiere не запущен.");
  assert.match(blockedMessage({ running: true, portAlive: false }), /панель/);
  assert.equal(blockedMessage({ running: true, portAlive: true, blocked: false }), "Premiere отвечает.");
  const m = blockedMessage({ running: true, portAlive: true, blocked: true, dialogs: [dlg] });
  assert.match(m, /ждёт клика/); assert.match(m, /«Создать эпизод»/); assert.match(m, /Отмена/);
  assert.match(blockedMessage({ running: true, portAlive: true, blocked: true, dialogs: [] }), /не опознано/);
});
