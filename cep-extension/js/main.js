(function () {
  "use strict";

  var cs = new CSInterface();
  var WS_URL = "ws://127.0.0.1:9876";
  var RECONNECT_DELAY = 2000;

  var statusEl = document.getElementById("status");
  var callsEl = document.getElementById("calls");
  var lastEl = document.getElementById("last");
  var logEl = document.getElementById("log");
  var reconnectBtn = document.getElementById("reconnect");
  var pingBtn = document.getElementById("ping");

  var ws = null;
  var callCount = 0;
  var reconnectTimer = null;

  function log(msg) {
    var ts = new Date().toLocaleTimeString();
    logEl.textContent = "[" + ts + "] " + msg + "\n" + logEl.textContent;
    if (logEl.textContent.length > 4000) logEl.textContent = logEl.textContent.slice(0, 4000);
  }

  function setStatus(cls, text) {
    statusEl.className = "status " + cls;
    statusEl.textContent = text;
  }

  function buildJsx(fn, args) {
    if (fn === "evalRaw") {
      var userCode = (args && args[0]) || "";
      return (
        "(function(){ try { var __r = (function(){ " + userCode + " })();" +
        " return (typeof __r === 'undefined') ? '' : (typeof __r === 'string' ? __r : JSON.stringify(__r)); }" +
        " catch(e){ return JSON.stringify({error: e.toString(), line: e.line}); } })()"
      );
    }
    var argList = (args || []).map(function (a) { return JSON.stringify(a); }).join(",");
    return fn + "(" + argList + ")";
  }

  function handleCall(msg) {
    callCount++;
    callsEl.textContent = String(callCount);
    lastEl.textContent = msg.fn;
    log("<- " + msg.fn);

    var jsx = buildJsx(msg.fn, msg.args);

    cs.evalScript(jsx, function (raw) {
      if (raw === "EvalScript error.") {
        log("!! " + msg.fn + ": EvalScript error");
        try {
          ws.send(JSON.stringify({
            id: msg.id, ok: false,
            error: "EvalScript error inside Premiere. The function may not exist in host.jsx, or threw before try/catch."
          }));
        } catch (e) {}
        return;
      }
      try {
        ws.send(JSON.stringify({ id: msg.id, ok: true, result: raw }));
        log("-> " + msg.fn + " ok (" + (raw ? raw.length : 0) + " chars)");
      } catch (e) {
        log("send back failed: " + e.message);
      }
    });
  }

  function connect() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    try { if (ws) ws.close(); } catch (e) {}

    setStatus("connecting", "Connecting…");
    log("Connecting to " + WS_URL);
    try { ws = new WebSocket(WS_URL); }
    catch (e) {
      log("WS construction failed: " + e.message);
      reconnectTimer = setTimeout(connect, RECONNECT_DELAY);
      return;
    }

    ws.onopen = function () {
      setStatus("connected", "Connected to Claude");
      log("WS open");
    };

    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); }
      catch (e) { log("Bad WS message"); return; }
      if (msg && msg.id && msg.fn) handleCall(msg);
    };

    ws.onclose = function () {
      setStatus("disconnected", "Disconnected — retrying");
      log("WS closed, retry in " + (RECONNECT_DELAY / 1000) + "s");
      reconnectTimer = setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = function () { log("WS error"); };
  }

  function loadHostAndConnect() {
    var extPath = cs.getSystemPath(SystemPath.EXTENSION);
    var jsxPath = extPath + "/jsx/host.jsx";
    log("Loading host.jsx from " + jsxPath);
    cs.evalScript("$.evalFile(\"" + jsxPath + "\")", function (res) {
      log("host.jsx load result: " + (res || "(empty)"));
      connect();
    });
  }

  reconnectBtn.addEventListener("click", function () {
    log("Manual reconnect");
    connect();
  });

  pingBtn.addEventListener("click", function () {
    log("Ping host");
    cs.evalScript("getStatus()", function (res) {
      log("ping result: " + (res ? res.slice(0, 200) : "(empty)"));
    });
  });

  loadHostAndConnect();
})();
