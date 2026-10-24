import { test } from "node:test";
import assert from "node:assert/strict";
import { parseArgs } from "../pk.js";
import { wrapBodyForEvalScript } from "../lib/evalcode.js";
import vm from "node:vm";

test("parseArgs: --key value, flags, camelCase, positionals, @file", () => {
  const { opts, positional } = parseArgs(["ПОДКАСТ 01", "--seq", "S", "--no-reuse", "--clip", "2", "-r", "x"]);
  assert.deepEqual(positional, ["ПОДКАСТ 01", "x"]);
  assert.equal(opts.seq, "S"); assert.equal(opts.noReuse, true); assert.equal(opts.clip, "2"); assert.equal(opts.recursive, true);
});

test("wrapBodyForEvalScript matches the panel: objects stringified, errors as JSON", () => {
  assert.equal(vm.runInNewContext(wrapBodyForEvalScript("return {a: 1};")), '{"a":1}');
  assert.equal(vm.runInNewContext(wrapBodyForEvalScript("return 'x';")), "x");
  assert.match(vm.runInNewContext(wrapBodyForEvalScript("throw new Error('bad');")), /"error":"Error: bad"/);
});
