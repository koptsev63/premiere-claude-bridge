#!/usr/bin/env node
// premiere-claude-bridge: MCP server connecting Claude Code <-> Adobe Premiere Pro CEP panel
// Architecture:
//   Claude Code  <--stdio JSON-RPC-->  this server  <--WebSocket-->  CEP panel  <--evalScript-->  Premiere host.jsx

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { WebSocketServer } from "ws";
import { randomUUID } from "node:crypto";
import { z } from "zod";

const WS_HOST = "127.0.0.1";
const WS_PORT = Number(process.env.PREMIERE_BRIDGE_PORT || 9876);
const REQUEST_TIMEOUT_MS = 60_000;

let panelSocket = null;
let wss = null;
let portBound = false;
const pending = new Map();

function log(...args) {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
  process.stderr.write(`[premiere-mcp] ${line}\n`);
}

// Multi-instance safe: when several Claude sessions spawn the bridge concurrently,
// only one can hold port WS_PORT at a time. The others would silently break
// (panel talks to whichever process won the bind race; the others have no clients).
// Solution: if EADDRINUSE, retry every 3s. When the holder dies, we grab the port.
function startWsServer() {
  const candidate = new WebSocketServer({ host: WS_HOST, port: WS_PORT });
  candidate.on("listening", () => {
    portBound = true;
    log(`WS server listening on ${WS_HOST}:${WS_PORT}`);
  });
  candidate.on("error", (e) => {
    if (e.code === "EADDRINUSE") {
      if (portBound) {
        log("WS port lost unexpectedly:", e.message);
      } else {
        log(`Port ${WS_PORT} taken by another bridge instance — retrying in 3s`);
      }
      portBound = false;
      try { candidate.close(); } catch {}
      setTimeout(startWsServer, 3000);
      return;
    }
    log("WS server error:", e.message);
  });
  candidate.on("connection", attachConnectionHandler);
  wss = candidate;
}

function attachConnectionHandler(ws) {
  log("CEP panel connected");
  if (panelSocket && panelSocket !== ws) {
    log("Replacing previous panel socket");
    try { panelSocket.close(); } catch {}
  }
  panelSocket = ws;

  ws.on("message", (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); }
    catch { log("invalid msg from panel:", data.toString().slice(0, 200)); return; }
    if (!msg.id) return;
    const p = pending.get(msg.id);
    if (!p) return;
    clearTimeout(p.timer);
    pending.delete(msg.id);
    if (msg.ok) p.resolve(msg.result);
    else p.reject(new Error(msg.error || "Unknown panel error"));
  });

  ws.on("close", () => {
    log("CEP panel disconnected");
    if (panelSocket === ws) panelSocket = null;
  });

  ws.on("error", (e) => log("WS client error:", e.message));
}

function getActiveSocket() {
  // Self-healing: prefer cached panelSocket if alive, otherwise scan wss.clients
  // for any open socket and adopt it. Avoids stale state after panel reload races.
  if (panelSocket && panelSocket.readyState === 1) return panelSocket;
  if (!wss) return null;
  for (const ws of wss.clients) {
    if (ws.readyState === 1) {
      if (panelSocket !== ws) {
        log("Adopting live socket from wss.clients (stale panelSocket recovered)");
        panelSocket = ws;
      }
      return ws;
    }
  }
  return null;
}

function callPanel(fn, args = []) {
  return new Promise((resolve, reject) => {
    const sock = getActiveSocket();
    if (!sock) {
      return reject(new Error(
        "Premiere panel not connected. In Premiere: Window > Extensions > Claude Bridge"
      ));
    }
    const id = randomUUID();
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Panel request "${fn}" timed out after ${REQUEST_TIMEOUT_MS}ms`));
    }, REQUEST_TIMEOUT_MS);
    pending.set(id, { resolve, reject, timer });
    sock.send(JSON.stringify({ id, fn, args }));
  });
}

function parseResult(raw) {
  if (typeof raw !== "string") return raw;
  try { return JSON.parse(raw); } catch { return raw; }
}

function ok(data) {
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return { content: [{ type: "text", text }] };
}

function err(message) {
  return { isError: true, content: [{ type: "text", text: String(message) }] };
}

const server = new McpServer({
  name: "premiere-claude-bridge",
  version: "0.1.0",
});

server.tool(
  "pr_status",
  "Check connection to Premiere Pro and return basic project info. Use this first to verify the bridge is alive.",
  {},
  async () => {
    if (!getActiveSocket()) {
      return ok({
        connected: false,
        hint: "Open panel in Premiere: Window > Extensions > Claude Bridge",
      });
    }
    try {
      const data = parseResult(await callPanel("getStatus", []));
      return ok({ connected: true, ...data });
    } catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_get_project_info",
  "Get the current Premiere project: name, file path, and a flat list of all project items (bins, clips, sequences) with nodeId for later reference.",
  {},
  async () => {
    try { return ok(parseResult(await callPanel("getProjectInfo", []))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_get_active_sequence",
  "Get info about the active sequence: name, duration in seconds, frame rate, dimensions, track counts.",
  {},
  async () => {
    try { return ok(parseResult(await callPanel("getActiveSequenceInfo", []))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_list_timeline",
  "List all video and audio tracks of active sequence with every clip on each track (name, in/out points, start/end times, duration).",
  {},
  async () => {
    try { return ok(parseResult(await callPanel("listTimeline", []))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_get_selected",
  "Get currently selected clips on the timeline.",
  {},
  async () => {
    try { return ok(parseResult(await callPanel("getSelected", []))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_get_playhead",
  "Get current playhead time (CTI) on the active sequence in seconds and ticks.",
  {},
  async () => {
    try { return ok(parseResult(await callPanel("getCTI", []))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_set_playhead",
  "Move the playhead to a given time on the active sequence.",
  { seconds: z.number().describe("Target time in seconds (e.g. 12.5)") },
  async ({ seconds }) => {
    try { return ok(parseResult(await callPanel("setCTI", [seconds]))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_add_marker",
  "Add a marker on the active sequence at given time.",
  {
    seconds: z.number().describe("Time in seconds where to place the marker"),
    name: z.string().optional().describe("Marker name (label)"),
    comment: z.string().optional().describe("Marker comment"),
  },
  async ({ seconds, name, comment }) => {
    try { return ok(parseResult(await callPanel("addSequenceMarker", [seconds, name || "", comment || ""]))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_export_ame",
  "Queue active sequence export to Adobe Media Encoder (non-blocking). Provide absolute output path and absolute path to a .epr preset file.",
  {
    outPath: z.string().describe("Absolute output path including filename and extension"),
    eprPath: z.string().describe("Absolute path to .epr Adobe Media Encoder preset"),
  },
  async ({ outPath, eprPath }) => {
    try { return ok(parseResult(await callPanel("exportAME", [outPath, eprPath]))); }
    catch (e) { return err(e.message); }
  }
);

server.tool(
  "pr_eval_jsx",
  "ESCAPE HATCH: run arbitrary ExtendScript code inside Premiere when no specific tool fits. Code must return a value (string, number, or object); objects are auto-stringified.",
  {
    code: z.string().describe(
      "ExtendScript code to evaluate. Wrap in IIFE if multi-statement. Last expression is returned."
    ),
  },
  async ({ code }) => {
    try { return ok(parseResult(await callPanel("evalRaw", [code]))); }
    catch (e) { return err(e.message); }
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
log("MCP server connected via stdio (waiting for Claude tool calls and CEP panel WS)");

// Start the WebSocket listener (with port-collision retry)
startWsServer();
