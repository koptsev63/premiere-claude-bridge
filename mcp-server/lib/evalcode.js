// pr_eval_jsx code preparation.
//
// The panel (cep-extension/js/main.js, buildJsx) runs the code as a function
// body: (function(){ CODE })(). Only an explicit `return` came back, so a
// script that ended in an expression returned "" (field case 20.10.2026).
//
// prepareEvalCode keeps both styles working with the panel as it is installed:
//   - code with a top-level `return` (outside any function) stays a function
//     body, exactly as before;
//   - any other code is sent as `return eval("CODE");` - ExtendScript's eval
//     gives back the value of the last expression statement (checked live in
//     Premiere 26.0.2: eval("var x=2; x+3") === 5).
// A `return` the scanner misses makes eval throw before anything runs:
// "SyntaxError: Illegal 'return' outside of a function body".

const REGEX_AFTER_WORD = new Set([
  "return", "typeof", "instanceof", "in", "new", "delete", "void",
  "throw", "case", "do", "else",
]);

// True if `code` has a `return` that is not inside a function body.
export function hasTopLevelReturn(code) {
  const src = String(code || "");
  const stack = []; // one entry per open `{`: true when it opens a function body
  let fnPending = false; // saw `function`, its body `{` has not come yet
  let fnDepth = 0; // how many function bodies are open
  let prev = ""; // last significant token, to tell a regex from a division
  let i = 0;
  const n = src.length;

  while (i < n) {
    const ch = src[i];
    const next = src[i + 1];

    if (/\s/.test(ch)) { i++; continue; }

    if (ch === "/" && next === "/") {
      while (i < n && src[i] !== "\n" && src[i] !== "\r") i++;
      continue;
    }
    if (ch === "/" && next === "*") {
      const end = src.indexOf("*/", i + 2);
      i = end < 0 ? n : end + 2;
      continue;
    }

    if (ch === "'" || ch === '"' || ch === "`") {
      i++;
      while (i < n && src[i] !== ch) {
        if (src[i] === "\\") i++;
        i++;
      }
      i++;
      prev = "str";
      continue;
    }

    if (ch === "/") {
      const regexStart = prev === "" || REGEX_AFTER_WORD.has(prev) ||
        (prev.length === 1 && "([{,;:=!&|?+-*%<>~^}".includes(prev));
      if (regexStart) {
        i++;
        let inClass = false;
        while (i < n) {
          const c = src[i];
          if (c === "\\") { i += 2; continue; }
          if (c === "[") inClass = true;
          else if (c === "]") inClass = false;
          else if (c === "/" && !inClass) break;
          else if (c === "\n") break;
          i++;
        }
        i++;
        while (i < n && /[a-z]/i.test(src[i])) i++;
        prev = "regex";
        continue;
      }
      prev = "/";
      i++;
      continue;
    }

    if (/[A-Za-z_$]/.test(ch)) {
      let j = i + 1;
      while (j < n && /[\w$]/.test(src[j])) j++;
      const word = src.slice(i, j);
      if (prev !== ".") {
        if (word === "function") fnPending = true;
        else if (word === "return" && fnDepth === 0) return true;
      }
      prev = word;
      i = j;
      continue;
    }

    if (/[0-9]/.test(ch)) {
      let j = i + 1;
      while (j < n && /[\w.]/.test(src[j])) j++;
      prev = "num";
      i = j;
      continue;
    }

    if (ch === "{") {
      stack.push(fnPending);
      if (fnPending) fnDepth++;
      fnPending = false;
    } else if (ch === "}") {
      if (stack.pop()) fnDepth--;
    }
    prev = ch;
    i++;
  }
  return false;
}

// A double-quoted ES3 string literal. Everything outside printable ASCII is
// written as \uXXXX, so neither U+2028 nor a Cyrillic effect name can be
// mangled on the way into ExtendScript.
export function jsxString(value) {
  let out = '"';
  for (const ch of String(value)) {
    const code = ch.codePointAt(0);
    if (ch === "\\") out += "\\\\";
    else if (ch === '"') out += '\\"';
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (code >= 0x20 && code < 0x7f) out += ch;
    else if (code > 0xffff) {
      const s = code - 0x10000;
      out += u4(0xd800 + (s >> 10)) + u4(0xdc00 + (s & 0x3ff));
    } else out += u4(code);
  }
  return out + '"';
}

function u4(code) {
  return "\\u" + code.toString(16).padStart(4, "0");
}

// What pr_eval_jsx sends to the panel. The trailing newline matters in both
// modes: the panel appends " })()" right after the code, and a final
// `// comment` would otherwise swallow it.
export function prepareEvalCode(code) {
  const src = String(code || "");
  if (hasTopLevelReturn(src)) return { mode: "function", code: src + "\n" };
  return { mode: "expression", code: "return eval(" + jsxString(src) + ");\n" };
}

// The panel's own wrapper (cep-extension/js/main.js, buildJsx "evalRaw"),
// for callers that talk to evalScript directly (pk.js): a function body with
// a top-level `return` becomes one expression whose value is a string, and
// an ExtendScript error becomes {"error": ..., "line": ...} instead of the
// bare "EvalScript error.".
export function wrapBodyForEvalScript(body) {
  return (
    "(function(){ try { var __r = (function(){ " + body + " })();" +
    " return (typeof __r === 'undefined') ? '' : (typeof __r === 'string' ? __r : JSON.stringify(__r)); }" +
    " catch(e){ return JSON.stringify({error: e.toString(), line: e.line}); } })()"
  );
}
