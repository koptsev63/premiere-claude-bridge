import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { hasTopLevelReturn, prepareEvalCode, jsxString } from "../lib/evalcode.js";

// The panel's own wrapper, taken from cep-extension/js/main.js, so the test
// breaks if the panel changes the way it runs code.
const mainJs = readFileSync(new URL("../../cep-extension/js/main.js", import.meta.url), "utf8");
const start = mainJs.indexOf("function buildJsx(");
const end = mainJs.indexOf("\n  }\n", start) + 4;
const buildJsx = vm.runInNewContext("(" + mainJs.slice(start, end) + ")");

// Run user code the way the bridge does: prepare, wrap like the panel, evaluate.
function runLikePremiere(code) {
  const jsx = buildJsx("evalRaw", [prepareEvalCode(code).code]);
  return vm.runInNewContext(jsx, {});
}

test("top-level return is detected", () => {
  assert.equal(hasTopLevelReturn("return 1"), true);
  assert.equal(hasTopLevelReturn("var s = 2;\nreturn s * 2;"), true);
  assert.equal(hasTopLevelReturn("if (x) { return 1 } else { return 2 }"), true);
  assert.equal(hasTopLevelReturn("for (var i=0;i<3;i++) { if (i) return i; }"), true);
  assert.equal(hasTopLevelReturn("return (function(){ return 1 })();"), true);
});

test("return inside functions, strings, comments, regexes does not count", () => {
  assert.equal(hasTopLevelReturn("app.project.name"), false);
  assert.equal(hasTopLevelReturn("(function(){ return 7 })()"), false);
  assert.equal(hasTopLevelReturn("function f(a){ if (a) { return 1 } return 2 } f(1)"), false);
  assert.equal(hasTopLevelReturn("var s = 'return'; s"), false);
  assert.equal(hasTopLevelReturn('var s = "a\\" return"; s'), false);
  assert.equal(hasTopLevelReturn("// return 1\n5"), false);
  assert.equal(hasTopLevelReturn("/* return */ 5"), false);
  assert.equal(hasTopLevelReturn("var r = /return/.test('x'); r"), false);
  assert.equal(hasTopLevelReturn("var o = {}; o.return = 1; o"), false);
  assert.equal(hasTopLevelReturn("var a = 6 / 2 / 3; a"), false);
  assert.equal(hasTopLevelReturn("[1,2].map(function(x){ return x*2 })"), false);
});

test("last expression comes back (the 20.10.2026 case)", () => {
  assert.equal(runLikePremiere("var x = 2; x + 3"), "5");
  assert.equal(runLikePremiere("'Черное и белое'"), "Черное и белое");
  assert.equal(runLikePremiere("({a: 1, b: [2]})"), '{"a":1,"b":[2]}');
  assert.equal(runLikePremiere("(function(){ return 7 })()"), "7");
  assert.equal(runLikePremiere("[1,2].map(function(x){ return x*2 })"), "[2,4]");
});

test("explicit return still works, as before", () => {
  assert.equal(runLikePremiere("var s = 4; return s * 2;"), "8");
  assert.equal(runLikePremiere("return (function(){ return 'ok' })();"), "ok");
  assert.equal(runLikePremiere("if (true) { return 1 } return 2"), "1");
});

test("a trailing // comment no longer breaks the panel wrapper", () => {
  assert.equal(runLikePremiere("1 + 1 // two"), "2");
  assert.equal(runLikePremiere("return 3 // three"), "3");
});

test("errors come back as {error}", () => {
  const out = JSON.parse(runLikePremiere("undefinedThing.x"));
  assert.match(out.error, /undefinedThing/);
});

test("statements without a value give empty string", () => {
  assert.equal(runLikePremiere("var x = 1;"), "");
});

test("jsxString is ASCII-only and round-trips", () => {
  const nasty = 'a"b\\c\nd e Черное ё 😀 </script>';
  const lit = jsxString(nasty);
  assert.match(lit, /^[\x20-\x7e]*$/);
  assert.equal(vm.runInNewContext(lit), nasty);
});
