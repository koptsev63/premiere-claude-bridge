#!/usr/bin/env node
/**
 * pk - the direct path into Premiere, for when the MCP bridge has no panel.
 *
 * Why it exists: the MCP server owns ONE WebSocket port (9876) and the CEP
 * panel connects to whichever server instance won the race for it. Open
 * several Claude Code windows and there are several copies of the server;
 * all but one have no panel, and their pr_* tools answer "panel not
 * connected" while Premiere sits there perfectly healthy.
 *
 * This runner does not use that port at all. It attaches to the panel's own
 * Chromium remote-debugging endpoint (the port declared in
 * cep-extension/.debug, 8088 by default) and calls
 * window.__adobe_cep__.evalScript - the very call the panel makes itself.
 * Any number of sessions can use it at once.
 *
 * Requirements: Premiere running, the Claude Bridge panel open
 * (Window > Extensions > Claude Bridge), PlayerDebugMode enabled (it already
 * is if you followed the install steps). Node 22+ needs nothing else; older
 * Node falls back to the `ws` package the MCP server already installs.
 *
 * Usage (raw ExtendScript):
 *   node pk.js info                  version, project, active sequence
 *   node pk.js eval '<jsx>'          run one ExtendScript expression
 *   node pk.js eval -                run ExtendScript read from stdin
 *   node pk.js file <path.jsx>       run a .jsx file
 *   node pk.js targets               list what the debug port exposes
 *
 * Typed commands (JSON out, exit 1 on a refusal; no dialog is ever opened):
 *   ping | status | dialog           health; status/dialog say whether Premiere waits for a click
 *   probe <file..> [--in S --out S]  codec gate: can Premiere decode it, ffmpeg copy command, disk
 *   import <file..> [--bin B] [--in S --out S] [--convert] [--no-reuse]
 *   make-seq --name N (--file F | --item REF) [--in S --out S] [--w 1080 --h 1920 --fps 29.97]
 *            [--bin B] [--exists error|replace|skip] [--convert]
 *   motion --seq N [--track 0 --clip 0] [--scale 177.78] [--x PX --y PX] [--rot DEG] [--units px|norm]
 *   motion-keys --seq N [--track --clip] --keys '[{"t":0,"posX":540,"scale":177.78}, ...]' [--hold] [--keep]
 *   markers --seq N --markers '[{"t":1.5,"name":"..","comment":".."}]' | --markers @file.json [--clear]
 *   list-markers [--seq N]
 *   open N1 N2 ... [--activate N] | activate N
 *   timeline [--seq N] [--markers] | inout [--seq N]      clip borders after a manual trim
 *   delete-seq N | move --item REF --bin B | ls [--bin B] [-r]
 *   build --spec spec.json           many sequences from one spec (see lib/ops.js build)
 *   save
 * Sequences are named by exact name or sequenceID; items by nodeId, absolute path or name.
 * Bins are paths from the root ("A/B"), created when missing.
 *
 * Environment:
 *   PK_PORT      debug port (default: read from cep-extension/.debug)
 *   PK_HOST      debug host (default 127.0.0.1)
 *   PK_TARGET    substring of the panel URL to attach to (default claude-bridge)
 *   PK_TIMEOUT   milliseconds to wait for ExtendScript (default 120000)
 *
 * Honest boundaries: the result is whatever evalScript hands back, i.e. a
 * string - return JSON.stringify(...) from your script and parse it on your
 * side. ExtendScript errors arrive as the string "EvalScript error." with no
 * detail; wrap your code in try/catch and return the message yourself. The
 * debug port is local and unauthenticated - it is the same exposure the
 * install steps already create, not a new one.
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { wrapBodyForEvalScript } from "./lib/evalcode.js";
import * as ops from "./lib/ops.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEBUG_FILE = path.join(HERE, "..", "cep-extension", ".debug");
const DEFAULT_PORT = 8088;

/** Port declared for the panel in a CEP `.debug` file. */
export function parseDebugPort(xml) {
  const m = /Port="(\d+)"/.exec(xml || "");
  return m ? parseInt(m[1], 10) : null;
}

export function debugPort() {
  if (process.env.PK_PORT) return Number(process.env.PK_PORT);
  try {
    return parseDebugPort(fs.readFileSync(DEBUG_FILE, "utf8")) || DEFAULT_PORT;
  } catch {
    return DEFAULT_PORT;
  }
}

/** The panel's page among the debug targets, or null. */
export function pickTarget(list, needle = "claude-bridge") {
  const pages = (list || []).filter((t) => t.type === "page" && t.webSocketDebuggerUrl);
  return (
    pages.find((t) => (t.url || "").includes(needle)) ||
    pages.find((t) => (t.title || "").toLowerCase().includes(needle.replace(/-/g, " "))) ||
    null
  );
}

/** JS evaluated inside the panel: evalScript is callback-style, so wrap it. */
export function buildExpression(jsx) {
  return (
    "new Promise(function(res){ window.__adobe_cep__.evalScript(" +
    JSON.stringify(jsx) +
    ", function(r){ res(r); }); })"
  );
}

export function listTargets(host, port) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host, port, path: "/json", timeout: 3000 }, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (e) {
          reject(new Error("debug port answered, but not with a target list: " + e.message));
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timed out")));
    req.on("error", (e) =>
      reject(
        new Error(
          `nothing is listening on ${host}:${port} (${e.message}). ` +
            "Is Premiere running with the Claude Bridge panel open?"
        )
      )
    );
  });
}

async function webSocketClass() {
  if (typeof globalThis.WebSocket === "function") return globalThis.WebSocket;
  try {
    return (await import("ws")).default;
  } catch {
    throw new Error("no WebSocket available: use Node 22+, or run `npm install` in mcp-server/");
  }
}

export async function evalJSX(jsx, opts = {}) {
  const host = opts.host || process.env.PK_HOST || "127.0.0.1";
  const port = opts.port || debugPort();
  const needle = opts.target || process.env.PK_TARGET || "claude-bridge";
  const timeoutMs = opts.timeoutMs || Number(process.env.PK_TIMEOUT || 120000);

  const page = pickTarget(await listTargets(host, port), needle);
  if (!page) {
    throw new Error(
      `no "${needle}" panel on the debug port. Open it in Premiere: ` +
        "Window > Extensions > Claude Bridge"
    );
  }
  const WS = await webSocketClass();
  const ws = new WS(page.webSocketDebuggerUrl);

  return new Promise((resolve, reject) => {
    const done = (fn, value) => {
      clearTimeout(timer);
      try {
        ws.close();
      } catch {}
      fn(value);
    };
    const timer = setTimeout(
      () => done(reject, new Error(`ExtendScript did not answer in ${timeoutMs} ms`)),
      timeoutMs
    );
    ws.onerror = (e) =>
      done(
        reject,
        new Error(
          "debugger socket failed: " + ((e && e.message) || "connection refused")
        )
      );
    ws.onopen = () =>
      ws.send(
        JSON.stringify({
          id: 1,
          method: "Runtime.evaluate",
          params: { expression: buildExpression(jsx), awaitPromise: true, returnByValue: true },
        })
      );
    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === "string" ? event.data : String(event.data));
      } catch {
        return;
      }
      if (msg.id !== 1) return;
      if (msg.error) return done(reject, new Error(JSON.stringify(msg.error)));
      const r = msg.result || {};
      if (r.exceptionDetails) return done(reject, new Error(JSON.stringify(r.exceptionDetails)));
      done(resolve, r.result ? r.result.value : undefined);
    };
  });
}

const INFO_JSX = `(function(){
  var o = { version: app.version, project: app.project ? app.project.name : null };
  var s = app.project ? app.project.activeSequence : null;
  o.sequence = s ? s.name : null;
  if (s) { o.videoTracks = s.videoTracks.numTracks; o.audioTracks = s.audioTracks.numTracks;
           o.end = s.end; o.timebase = s.timebase; }
  return JSON.stringify(o);
})()`;

function usage() {
  const src = fs.readFileSync(fileURLToPath(import.meta.url), "utf8");
  return src.slice(src.indexOf("/**"), src.indexOf("*/") + 2);
}

// --key value / --flag / positionals. A value starting with @ is read from that file.
export function parseArgs(argv) {
  const opts = {}, positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-r") { opts.recursive = true; continue; }
    if (!a.startsWith("--")) { positional.push(a); continue; }
    const key = a.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) { opts[key] = true; continue; }
    opts[key] = next.startsWith("@") ? fs.readFileSync(next.slice(1), "utf8") : next;
    i++;
  }
  return { opts, positional };
}

const num = (v) => (v === undefined || v === true ? undefined : Number(v));
const json = (v) => (v === undefined ? undefined : typeof v === "string" ? JSON.parse(v) : v);

// Executor for lib/ops: a function body goes through evalScript wrapped like
// the panel wraps it, and comes back parsed.
export async function runBody(body, { timeoutMs } = {}) {
  const raw = await evalJSX(wrapBodyForEvalScript(body), { timeoutMs });
  if (raw === "EvalScript error.") return { error: "EvalScript error (syntax error before the try/catch)" };
  if (typeof raw !== "string" || raw === "") return raw;
  try { return JSON.parse(raw); } catch { return raw; }
}

async function pingPort() {
  try { return !!pickTarget(await listTargets(process.env.PK_HOST || "127.0.0.1", debugPort()), process.env.PK_TARGET || "claude-bridge"); }
  catch { return false; }
}

const log = (m) => process.stderr.write("pk: " + m + "\n");

export async function typed(cmd, argv) {
  const { opts, positional } = parseArgs(argv);
  const run = runBody;
  switch (cmd) {
    case "ping": return ops.ping(run);
    case "status": return ops.status(run, { pingPort });
    case "dialog": return ops.dialogCheck(run, { pingPort });
    case "probe": return ops.probe(positional.map((p) => path.resolve(p)), { start: num(opts.in), end: num(opts.out) });
    case "import": return ops.importMedia(run, { paths: positional.map((p) => path.resolve(p)), bin: opts.bin, start: num(opts.in), end: num(opts.out), convert: !!opts.convert, reuse: !opts.noReuse, log });
    case "make-seq": return ops.makeSequence(run, { name: opts.name, file: opts.file, item: opts.item, bin: opts.bin, in: num(opts.in), out: num(opts.out), width: num(opts.w ?? opts.width), height: num(opts.h ?? opts.height), fps: num(opts.fps), exists: opts.exists, convert: !!opts.convert, log });
    case "motion": return ops.setMotion(run, { seq: opts.seq, track: num(opts.track), clip: num(opts.clip), scale: num(opts.scale), posX: num(opts.x), posY: num(opts.y), rotation: num(opts.rot), units: opts.units });
    case "motion-keys": return ops.setMotionKeyframes(run, { seq: opts.seq, track: num(opts.track), clip: num(opts.clip), keys: json(opts.keys), units: opts.units, hold: !!opts.hold, clear: !opts.keep });
    case "markers": return ops.addMarkers(run, { seq: opts.seq, markers: json(opts.markers), clear: !!opts.clear });
    case "list-markers": return ops.listMarkers(run, { seq: opts.seq });
    case "open": return ops.openSequences(run, { names: positional, activate: opts.activate });
    case "activate": return ops.activate(run, { name: positional[0] });
    case "timeline": return ops.getTimeline(run, { seq: opts.seq, withMarkers: !!opts.markers });
    case "inout": return ops.getInOut(run, { seq: opts.seq });
    case "delete-seq": return ops.deleteSequence(run, { name: positional[0] });
    case "move": return ops.moveToBin(run, { item: opts.item, bin: opts.bin });
    case "ls": return ops.listBin(run, { bin: opts.bin, recursive: !!opts.recursive });
    case "build": return ops.build(run, json(opts.spec ? (opts.spec.trim().startsWith("{") ? opts.spec : "@" + opts.spec) : undefined) ?? (() => { throw new Error("build: --spec spec.json"); })(), { log });
    case "save": return ops.saveProject(run);
    default: return undefined;
  }
}

const TYPED = new Set(["ping", "status", "dialog", "probe", "import", "make-seq", "motion", "motion-keys", "markers", "list-markers",
  "open", "activate", "timeline", "inout", "delete-seq", "move", "ls", "build", "save"]);

export async function main(argv) {
  const [cmd, arg] = argv;
  if (cmd === "targets") {
    const host = process.env.PK_HOST || "127.0.0.1";
    for (const t of await listTargets(host, debugPort())) console.log(`${t.type}\t${t.url}`);
    return 0;
  }
  if (TYPED.has(cmd)) {
    let rest = argv.slice(1);
    if (cmd === "build" && rest[0] === "--spec" && rest[1] && !rest[1].trim().startsWith("{")) rest = ["--spec", fs.readFileSync(rest[1], "utf8"), ...rest.slice(2)];
    try {
      const out = await typed(cmd, rest);
      console.log(JSON.stringify(out, null, 1));
      return out && (out.ok === false || out.error) ? 1 : 0;
    } catch (e) {
      const data = e.data || { error: e.message };
      if (/timed out|did not answer/i.test(e.message) && cmd !== "dialog") {
        try { data.diagnosis = await ops.dialogCheck(runBody, { pingPort }); data.hint = data.diagnosis.message; } catch {}
      }
      console.log(JSON.stringify(data, null, 1));
      return 1;
    }
  }
  let jsx;
  if (cmd === "info") jsx = INFO_JSX;
  else if (cmd === "file" && arg) jsx = fs.readFileSync(arg, "utf8");
  else if (cmd === "eval" && arg) jsx = arg === "-" ? fs.readFileSync(0, "utf8") : arg;
  else {
    console.log(usage());
    return 2;
  }
  const out = await evalJSX(jsx);
  console.log(typeof out === "string" ? out : JSON.stringify(out));
  // evalScript reports any ExtendScript failure as this literal string.
  return out === "EvalScript error." ? 1 : 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (e) => {
      console.error("pk:", e.message);
      process.exit(1);
    }
  );
}
