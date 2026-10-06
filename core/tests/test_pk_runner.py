"""Tests for the direct Premiere runner (`mcp-server/pk.js`).

No Premiere here, so the panel's Chromium debug endpoint is faked: a tiny
HTTP `/json` target list plus a WebSocket that answers `Runtime.evaluate`
by echoing what it was asked. That proves the half we own - target
selection, the evalScript wrapper, exit codes, the error a user sees when
the port is closed. What Premiere does with the script is not tested.

Skips cleanly when node (or the `ws` package for the fake server) is absent.

Run:  python -m core.tests.test_pk_runner
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

_p = _f = _s = 0

SERVER_DIR = Path(__file__).resolve().parents[2] / "mcp-server"
PK = SERVER_DIR / "pk.js"

FAKE_CDP = r"""
import http from "node:http";
import { WebSocketServer } from "ws";
const server = http.createServer((req, res) => {
  const port = server.address().port;
  res.end(JSON.stringify([
    { type: "page", url: "file:///ext/some.other.panel/index.html",
      webSocketDebuggerUrl: `ws://127.0.0.1:${port}/devtools/page/0` },
    { type: "page", url: "file:///ext/com.koptsev.claude-bridge/index.html",
      webSocketDebuggerUrl: `ws://127.0.0.1:${port}/devtools/page/1` },
  ]));
});
const wss = new WebSocketServer({ server });
wss.on("connection", (ws, req) => ws.on("message", (raw) => {
  const m = JSON.parse(raw.toString());
  const value = m.params.expression.includes("BOOM")
    ? "EvalScript error."
    : JSON.stringify({ page: req.url, method: m.method,
                       awaitPromise: m.params.awaitPromise,
                       expression: m.params.expression });
  ws.send(JSON.stringify({ id: m.id, result: { result: { type: "string", value } } }));
}));
server.listen(0, "127.0.0.1", () => console.log(server.address().port));
"""

PURE = r"""
import { parseDebugPort, pickTarget, buildExpression } from "./pk.js";
const t = (url, ws = "ws://x") => ({ type: "page", url, webSocketDebuggerUrl: ws });
console.log(JSON.stringify({
  port: parseDebugPort('<Host Name="PPRO" Port="8088"/>'),
  noPort: parseDebugPort("<Host/>"),
  picked: pickTarget([t("a"), t("file:///com.koptsev.claude-bridge/index.html")]).url,
  none: pickTarget([t("a"), { type: "worker", url: "claude-bridge" }]),
  custom: pickTarget([t("a/my-panel/b")], "my-panel").url,
  expr: buildExpression('alert("hi")'),
}));
"""


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def skip(why: str) -> None:
    global _s
    _s += 1
    print(f"  SKIP  ({why})")


def _node(args, **kw):
    return subprocess.run(["node", *args], capture_output=True, text=True,
                          cwd=SERVER_DIR, timeout=30, **kw)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_syntax_and_pure() -> None:
    print("pk - parses, and the pure helpers behave")
    r = _node(["--check", str(PK)])
    check("node --check passes", r.returncode == 0, r.stderr[:200])
    r = _node(["--input-type=module", "-e", PURE])
    if r.returncode != 0:
        check("helpers importable without side effects", False, r.stderr[:300])
        return
    out = json.loads(r.stdout)
    check("port is read from a .debug file", out["port"] == 8088, str(out))
    check("a .debug without a port yields null", out["noPort"] is None)
    check("the bridge panel is picked among several pages",
          "claude-bridge" in out["picked"], out["picked"])
    check("non-page targets are never picked", out["none"] is None)
    check("PK_TARGET-style needle selects another panel",
          "my-panel" in out["custom"])
    check("script is embedded as a JSON string, quotes intact",
          'evalScript("alert(\\"hi\\")"' in out["expr"], out["expr"])


def test_no_panel() -> None:
    print("pk - a closed port is an explanation, not a stack trace")
    import os
    env = dict(os.environ, PK_PORT=str(_closed_port()), PK_TIMEOUT="3000")
    r = _node([str(PK), "info"], env=env)
    check("exit code 1", r.returncode == 1, str(r.returncode))
    check("says to open the panel",
          "Claude Bridge panel" in r.stderr and "Traceback" not in r.stderr
          and "    at " not in r.stderr, r.stderr[:300])
    r = _node([str(PK)], env=env)
    check("no command prints usage and exits 2",
          r.returncode == 2 and "node pk.js eval" in r.stdout, r.stdout[:120])


def test_against_fake_panel() -> None:
    print("pk - round trip against a fake panel debug endpoint")
    import os
    if not (SERVER_DIR / "node_modules" / "ws").exists():
        skip("mcp-server/node_modules/ws missing - run npm install to test")
        return
    srv = subprocess.Popen(
        ["node", "--input-type=module", "-e", FAKE_CDP], cwd=SERVER_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        port = srv.stdout.readline().strip()
        if not port.isdigit():
            check("fake panel started", False, srv.stderr.read()[:300])
            return
        env = dict(os.environ, PK_PORT=port, PK_TIMEOUT="8000")
        r = _node([str(PK), "eval", "app.version"], env=env)
        check("eval exits 0", r.returncode == 0, r.stderr[:300])
        try:
            got = json.loads(r.stdout)
        except ValueError:
            got = {}
        check("attached to the bridge panel, not the first page",
              got.get("page", "").endswith("/page/1"), str(got.get("page")))
        check("asked the runtime to evaluate and await",
              got.get("method") == "Runtime.evaluate"
              and got.get("awaitPromise") is True, str(got))
        check("the script went through evalScript",
              'evalScript("app.version"' in got.get("expression", ""),
              got.get("expression", "")[:120])
        r = _node([str(PK), "eval", "-"], env=env, input="1+1 // stdin")
        check("eval - reads the script from stdin",
              "1+1 // stdin" in r.stdout, r.stdout[:200])
        r = _node([str(PK), "eval", "BOOM"], env=env)
        check("an ExtendScript failure is a non-zero exit",
              r.returncode == 1 and "EvalScript error." in r.stdout,
              f"{r.returncode} {r.stdout[:80]}")
        r = _node([str(PK), "targets"], env=env)
        check("targets lists what the port exposes",
              r.returncode == 0 and "claude-bridge" in r.stdout, r.stdout[:200])
        r = _node([str(PK), "eval", "1"], env=dict(env, PK_TARGET="absent"))
        check("a missing panel names the menu to open it from",
              r.returncode == 1 and "Window > Extensions" in r.stderr,
              r.stderr[:200])
    finally:
        srv.kill()
        srv.wait(timeout=5)


def main() -> int:
    if shutil.which("node") is None:
        skip("node not installed")
    else:
        test_syntax_and_pure()
        test_no_panel()
        test_against_fake_panel()
    print(f"\npk_runner: {_p} passed, {_f} failed, {_s} skipped")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
