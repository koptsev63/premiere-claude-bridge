// Port hand-over between bridge servers of different sessions (20.10.2026:
// several sessions, the panel stuck to an idle one, the working session got
// "Premiere panel not connected"). Real server.js processes, a fake panel.
import { test, after } from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import http from "node:http";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import WebSocket from "ws";

const SERVER = fileURLToPath(new URL("../server.js", import.meta.url));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cleanups = [];
after(async () => { for (const c of cleanups.reverse()) await c(); });

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer().listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

async function startBridge(port) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER],
    env: { ...process.env, PREMIERE_BRIDGE_PORT: String(port), PREMIERE_BRIDGE_PANEL_WAIT_MS: "3000" },
    stderr: "pipe",
  });
  const client = new Client({ name: "port-test", version: "0" });
  await client.connect(transport);
  cleanups.push(() => client.close().catch(() => {}));
  return { client, transport };
}

async function call(bridge, name, args = {}) {
  const r = await bridge.client.callTool({ name, arguments: args });
  const text = r.content[0].text;
  let json; try { json = JSON.parse(text); } catch { json = text; }
  return { isError: !!r.isError, json };
}

// Reconnects like the real panel (faster). Code containing SLOW answers late.
function fakePanel(port) {
  let ws = null, stopped = false;
  const connect = () => {
    if (stopped) return;
    ws = new WebSocket(`ws://127.0.0.1:${port}`);
    ws.on("message", (data) => {
      const msg = JSON.parse(data.toString());
      const reply = (result) => { try { ws.send(JSON.stringify({ id: msg.id, ok: true, result })); } catch {} };
      if (msg.fn === "getStatus") reply(JSON.stringify({ app: "fake Premiere" }));
      else if (msg.fn === "evalRaw" && /SLOW/.test(msg.args[0])) setTimeout(() => reply('"slow done"'), 1500);
      else reply('"ok"');
    });
    ws.on("close", () => setTimeout(connect, 100));
    ws.on("error", () => {});
  };
  connect();
  const stop = () => { stopped = true; try { ws.terminate(); } catch {} };
  cleanups.push(async () => stop());
  return { stop };
}

function holder(port) {
  return new Promise((resolve) => {
    http.get({ host: "127.0.0.1", port, path: "/bridge" }, (res) => {
      let b = ""; res.on("data", (d) => (b += d)); res.on("end", () => resolve(JSON.parse(b)));
    }).on("error", () => resolve(null));
  });
}

test("the session that calls a tool takes the panel from an idle one", async () => {
  const port = await freePort();
  const a = await startBridge(port);
  await sleep(300);
  const b = await startBridge(port); // port busy: B stays idle, no polling
  fakePanel(port);

  const sa = await call(a, "pr_status");
  assert.equal(sa.json.connected, true);
  assert.equal((await holder(port)).pid, sa.json.bridgePid);

  const sb = await call(b, "pr_status");
  assert.equal(sb.json.connected, true, JSON.stringify(sb.json));
  assert.notEqual(sb.json.bridgePid, sa.json.bridgePid);
  assert.equal((await holder(port)).pid, sb.json.bridgePid);

  // and back
  const sa2 = await call(a, "pr_eval_jsx", { code: "1+1" });
  assert.equal(sa2.isError, false);
  assert.equal((await holder(port)).pid, sa.json.bridgePid);
});

test("a holder in the middle of a request is not cut off", async () => {
  const port = await freePort();
  const a = await startBridge(port);
  await sleep(300);
  const b = await startBridge(port);
  fakePanel(port);
  await call(a, "pr_status");

  const slow = call(a, "pr_eval_jsx", { code: "'SLOW'" });
  await sleep(300);
  const t0 = Date.now();
  const sb = await call(b, "pr_status");
  const sa = await slow;
  assert.equal(sa.isError, false);
  assert.equal(sa.json, "slow done");
  assert.equal(sb.json.connected, true);
  assert.ok(Date.now() - t0 >= 900, "B waited for A's request to finish");
});

test("a port held by something else is never taken, the pid is named", async () => {
  const port = await freePort();
  const alien = http.createServer((req, res) => res.end("hello")).listen(port, "127.0.0.1");
  cleanups.push(() => new Promise((r) => alien.close(r)));
  await sleep(100);
  const c = await startBridge(port);
  const s = await call(c, "pr_status");
  assert.equal(s.json.connected, false);
  assert.match(s.json.reason, /does not hand it over/);
  assert.match(s.json.reason, new RegExp(`pid ${process.pid}`));
});

test("a closed session frees the port at once", async () => {
  const port = await freePort();
  const a = await startBridge(port);
  await sleep(300);
  assert.ok(await holder(port));
  await a.client.close();
  await sleep(500);
  assert.equal(await holder(port), null);
});
