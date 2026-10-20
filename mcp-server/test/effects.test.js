import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolveEffect, EN_TO_LOCAL } from "../lib/effects.js";

// Real list from Premiere Pro 26.0.2, Russian UI (qe.project.getVideoEffectList()).
const RU = readFileSync(new URL("./fixtures/video-effects-ru-26.0.2.txt", import.meta.url), "utf8")
  .split("\n").filter(Boolean);

test("fixture is the real list", () => {
  assert.equal(RU.length, 131);
  assert.ok(RU.includes("Черное и белое"));
});

test("Black & White resolves to the Russian name (the 20.10.2026 case)", () => {
  assert.deepEqual(resolveEffect("Black & White", RU), { name: "Черное и белое", how: "map:ru" });
  assert.deepEqual(resolveEffect("black & white", RU), { name: "Черное и белое", how: "map:ru" });
});

test("localized names pass through; case and ё do not matter", () => {
  assert.deepEqual(resolveEffect("Черное и белое", RU), { name: "Черное и белое", how: "exact" });
  assert.deepEqual(resolveEffect("чёрное и белое", RU), { name: "Черное и белое", how: "case" });
});

test("English-named effects in a Russian UI are taken as they are", () => {
  assert.deepEqual(resolveEffect("Gaussian Blur", RU), { name: "Gaussian Blur", how: "exact" });
  assert.deepEqual(resolveEffect("vignette", RU), { name: "Vignette", how: "case" });
});

test("legacy names map to the legacy effect, not the new one", () => {
  assert.equal(resolveEffect("Gaussian Blur (Legacy)", RU).name, "Размытие по Гауссу (из прежней версии)");
});

test("an English UI keeps English names", () => {
  const EN = ["Black & White", "Lumetri Color", "Crop"];
  assert.deepEqual(resolveEffect("Black & White", EN), { name: "Black & White", how: "exact" });
  assert.deepEqual(resolveEffect("Black and White", EN), { name: "Black & White", how: "fuzzy" });
});

test("unknown or ambiguous names are refused with candidates, never guessed", () => {
  const r = resolveEffect("Blur", RU);
  assert.ok(r.error);
  assert.ok(r.candidates.includes("Gaussian Blur"));
  const dup = resolveEffect("преобразовать ", ["Преобразовать", "Преобразовать "]);
  assert.ok(dup.name || dup.error); // duplicates in Premiere's own list must not crash
  assert.ok(resolveEffect("Nonexistent Effect", RU).error);
});

test("every Russian name in the map exists in the real list (no typos)", () => {
  const missing = Object.values(EN_TO_LOCAL.ru).filter((v) => !RU.includes(v));
  assert.deepEqual(missing, []);
});
