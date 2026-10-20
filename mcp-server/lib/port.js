// The panel port (9876) shared by many bridge servers.
//
// Every Claude Code / Codex session starts its own server.js, the CEP panel
// connects to whichever one listens on the port. Before 0.5.0 the losers
// retried the bind every 3 s, the panel stuck to a random winner and the
// session actually working got "Premiere panel not connected" (20.10.2026).
//
// Now:
//   - at start a server binds only if the port is free, and never polls;
//   - the server that receives a tool call takes the port: it asks the
//     holder over HTTP on the same port to let go (POST /release), binds,
//     and waits for the panel to reconnect (it retries every 2 s);
//   - a holder in the middle of a panel request answers 409 busy and keeps
//     the port until the request is done, so nobody's call is cut in half;
//   - a holder that is not a bridge (or a bridge older than 0.5.0) is never
//     touched: the caller gets an error naming the pid.
// Last caller wins. Two sessions working at once hand the port back and
// forth, costing a couple of seconds per switch, never a wrong answer.

import http from "node:http";
import { execFile } from "node:child_process";
import { WebSocketServer } from "ws";

export const BRIDGE_ID = "premiere-claude-bridge";
const RELEASE_HEADER = "x-premiere-bridge"; // custom header: a web page cannot send it without CORS preflight

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class PanelPort {
  constructor({ host, port, version, log, onConnection, isBusy }) {
    this.host = host;
    this.port = port;
    this.version = version;
    this.log = log || (() => {});
    this.onConnection = onConnection;
    this.isBusy = isBusy || (() => false);
    this.http = null;
    this.wss = null;
    this.bound = false;
    this.acquiring = null;
    this.waiters = [];
  }

  // One bind attempt. Resolves "ok" or "busy" (EADDRINUSE); other errors throw.
  listen() {
    if (this.bound) return Promise.resolve("ok");
    return new Promise((resolve, reject) => {
      const srv = http.createServer((req, res) => this.handleHttp(req, res));
      const onError = (e) => {
        srv.removeAllListeners("listening");
        try { srv.close(); } catch {}
        if (e.code === "EADDRINUSE") resolve("busy");
        else reject(e);
      };
      srv.once("error", onError);
      srv.once("listening", () => {
        srv.removeListener("error", onError);
        srv.on("error", (e) => this.log("HTTP server error:", e.message));
        const wss = new WebSocketServer({ server: srv });
        wss.on("connection", (ws) => {
          this.onConnection(ws);
          const w = this.waiters.splice(0);
          w.forEach((fn) => fn());
        });
        wss.on("error", (e) => this.log("WS server error:", e.message));
        this.http = srv;
        this.wss = wss;
        this.bound = true;
        this.log(`WS server listening on ${this.host}:${this.port} (pid ${process.pid})`);
        resolve("ok");
      });
      srv.listen(this.port, this.host);
    });
  }

  handleHttp(req, res) {
    const send = (code, body, after) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(body), after);
    };
    const info = { bridge: BRIDGE_ID, version: this.version, pid: process.pid, busy: this.isBusy() };
    if (req.method === "GET" && req.url === "/bridge") return send(200, info);
    if (req.method === "POST" && req.url === "/release") {
      if (req.headers[RELEASE_HEADER] !== "1") return send(403, { error: "missing header" });
      if (this.isBusy()) return send(409, info);
      this.log("Port handed over on request of another bridge session");
      return send(200, { ...info, released: true }, () => this.release());
    }
    send(404, { error: "not found" });
  }

  release() {
    if (!this.bound) return;
    this.bound = false;
    const { wss, http: srv } = this;
    this.wss = null;
    this.http = null;
    try { for (const ws of wss.clients) ws.terminate(); } catch {}
    try { wss.close(); } catch {}
    try { srv.close(); srv.closeAllConnections?.(); } catch {}
    this.log("WS port released");
  }

  // Ask whoever holds the port about itself / to let go.
  request(method, path) {
    return new Promise((resolve) => {
      const req = http.request(
        { host: this.host, port: this.port, method, path, timeout: 2000,
          headers: { [RELEASE_HEADER]: "1" } },
        (res) => {
          let body = "";
          res.on("data", (d) => (body += d));
          res.on("end", () => {
            let json = null;
            try { json = JSON.parse(body); } catch {}
            resolve({ status: res.statusCode, json });
          });
        }
      );
      req.on("timeout", () => req.destroy(new Error("timeout")));
      req.on("error", (e) => resolve({ status: 0, error: e.message }));
      req.end();
    });
  }

  holderPid() {
    return new Promise((resolve) => {
      execFile("lsof", ["-nP", `-iTCP:${this.port}`, "-sTCP:LISTEN", "-t"], { timeout: 2000 }, (e, out) => {
        resolve(e ? null : String(out).trim().split("\n")[0] || null);
      });
    });
  }

  // Bind the port, taking it from another bridge if needed.
  acquire({ busyWaitMs = 65_000 } = {}) {
    if (this.bound) return Promise.resolve();
    if (this.acquiring) return this.acquiring;
    this.acquiring = this._acquire(busyWaitMs).finally(() => { this.acquiring = null; });
    return this.acquiring;
  }

  async _acquire(busyWaitMs) {
    if ((await this.listen()) === "ok") return;
    const deadline = Date.now() + busyWaitMs;
    for (;;) {
      const rel = await this.request("POST", "/release");
      if (rel.status === 200 && rel.json?.bridge === BRIDGE_ID) {
        this.log(`Bridge pid ${rel.json.pid} let go of the port`);
        for (let i = 0; i < 40; i++) {
          if ((await this.listen()) === "ok") return;
          await sleep(100);
        }
        throw new Error(`Port ${this.port} was released but someone else bound it first; try again`);
      }
      if (rel.status === 409 && rel.json?.bridge === BRIDGE_ID) {
        if (Date.now() > deadline) {
          throw new Error(`Port ${this.port} is busy: bridge pid ${rel.json.pid} is running a Premiere request from another session`);
        }
        await sleep(500);
        continue;
      }
      if (rel.status === 0 && /ECONNREFUSED/.test(rel.error || "") && Date.now() < deadline) {
        // Holder went away between our bind and our request.
        if ((await this.listen()) === "ok") return;
        await sleep(200);
        continue;
      }
      const pid = await this.holderPid();
      throw new Error(
        `Port ${this.port} is held by ${pid ? `pid ${pid}` : "another process"} that does not hand it over ` +
        `(not a bridge, or a bridge older than 0.5.0). Restart that Claude session or stop that process` +
        (pid ? ` (kill ${pid})` : "") + ". Meanwhile mcp-server/pk.js reaches the panel through its debug port."
      );
    }
  }

  // Resolve when a panel connects, or after `ms`.
  waitForConnection(ms) {
    return new Promise((resolve) => {
      let done = false;
      const fn = () => { if (!done) { done = true; clearTimeout(t); resolve(true); } };
      const t = setTimeout(() => {
        done = true;
        this.waiters = this.waiters.filter((x) => x !== fn);
        resolve(false);
      }, ms);
      this.waiters.push(fn);
    });
  }
}
