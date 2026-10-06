"""Tests for the ripple plan compiler (core.ripple).

Run:  python -m core.tests.test_ripple
Hermetic — no NLE, no media. Every check is a defect that shipped on
06.10.2026 and was fixed the same evening.
"""

from __future__ import annotations

import sys

from core.ripple import A, D, I, R, compile_variant, final_time, fr, remap_cues, srt_dump, srt_load, time_map

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


SRC = {"c1": "/x/card1.mov", "c2": "/x/card2.mov", "fight": "/x/fight_V.mov", "music": "/x/music.wav", "room": "/x/room.wav"}
BASE = 377.32


def test_order_dels_first_then_inserts_desc() -> None:
    print("ripple — builder order: rmaud, deletions desc, inserts desc, audio last")
    ops = [D(224.24, 241.56), I(224.24, "c1", 0, 5.28), I(224.24, "fight", 0, 8.76), A(224.24, "music"),
           I(369.28, "c2", 0, 3.52), R(294.84, 297.08, 4), D(7.24, 14.32)]
    cv = compile_variant("V", "v", ops, SRC, BASE)
    kinds = [o["op"] for o in cv.ops]
    check("rmaud first", kinds[0] == "rmaud", str(kinds))
    check("then two dels, descending", kinds[1:3] == ["del", "del"] and cv.ops[1]["a"] > cv.ops[2]["a"], str(cv.ops[1:3]))
    check("inserts after dels, descending t", kinds[3:6] == ["ins"] * 3 and cv.ops[3]["t"] >= cv.ops[4]["t"] >= cv.ops[5]["t"])
    check("audio last", kinds[-1] == "aud")


def test_same_anchor_list_order_is_screen_order() -> None:
    print("ripple — several inserts at one anchor: last listed is inserted first")
    ops = [I(100.0, "c1", 0, 2.0), I(100.0, "c2", 0, 3.0), I(100.0, "fight", 0, 4.0)]
    cv = compile_variant("V", "v", ops, SRC, BASE)
    codes = [o["code"] for o in cv.ops if o["op"] == "ins"]
    check("builder gets fight, c2, c1", codes == ["fight", "c2", "c1"], str(codes))
    check("expected length adds all three", abs(cv.expected - (BASE + 9.0)) < 1e-6, str(cv.expected))


def test_insert_coordinates_after_deletions() -> None:
    print("ripple — insert anchors are expressed after the deletions")
    ops = [D(7.24, 14.32), D(21.56, 28.48), I(135.6, "c1", 0, 2.0)]
    cv = compile_variant("V", "v", ops, SRC, BASE)
    ins = [o for o in cv.ops if o["op"] == "ins"][0]
    check("135.6 becomes 135.6 - 7.08 - 6.92", abs(ins["t"] - fr(135.6 - 7.08 - 6.92)) < 1e-6, str(ins["t"]))
    check("original anchor kept as t0", ins["t0"] == 135.6)


def test_audio_final_coordinates() -> None:
    print("ripple — audio placements in FINAL coordinates (the 67-s room-tone defect)")
    ops = [D(224.24, 241.56), I(224.24, "c1", 0, 60.0), I(224.24, "fight", 0, 7.04), A(224.24, "music"),
           I(369.28, "c2", 0, 3.52), A(369.28, "room")]
    cv = compile_variant("V", "v", ops, SRC, BASE)
    aud = {o["code"]: o["t"] for o in cv.ops if o["op"] == "aud"}
    check("music starts with the block, not after it", abs(aud["music"] - 224.24) < 1e-6, str(aud))
    check("room tone: -17.32 deletion, +67.04 block, not +3.52 of its own card", abs(aud["room"] - fr(369.28 - 17.32 + 67.04)) < 1e-6, str(aud))
    check("final_time ignores an insert AT the anchor", final_time(ops, 224.24) == 224.24)


def test_cue_remap_rules() -> None:
    print("ripple — subtitle cues: inside deletion dropped, crossing a cut clamped, starting inside a deletion kept")
    ops = [D(297.08, 306.6), I(297.08, "c1", 0, 67.04), I(369.28, "c2", 0, 7.92)]
    cues = [(278.9, 280.2, "Давай, Лаци!"), (300.0, 303.0, "inside deletion"), (306.6, 311.2, "question"),
            (306.5, 311.2, "starts 0.1 s inside deletion"), (295.0, 299.0, "crosses the cut"), (359.9, 361.3, "Такова жизнь.")]
    out = remap_cues(cues, ops)
    texts = [t for _, _, t in out]
    check("cue inside the deletion is gone", "inside deletion" not in texts, str(texts))
    q = [c for c in out if c[2] == "question"][0]
    check("question lands after the block", abs(q[0] - (306.6 - 9.52 + 67.04)) < 1e-3, str(q))
    s = [c for c in out if c[2].startswith("starts")][0]
    check("cue starting inside the deletion starts at the deletion end", abs(s[0] - q[0]) < 1e-3, str(s))
    x = [c for c in out if c[2] == "crosses the cut"][0]
    check("cue crossing the cut ends 0.04 s before it", abs(x[1] - (297.08 - 0.04 + 0.0)) < 2e-3, str(x))
    z = [c for c in out if c[2] == "Такова жизнь."][0]
    check("late cue shifted by deletion and block only", abs(z[0] - (359.9 - 9.52 + 67.04)) < 1e-3, str(z))


def test_time_map_and_expected() -> None:
    print("ripple — time map and expected length")
    ops = [D(224.24, 241.56), I(224.24, "c1", 0, 67.04)]
    tm = time_map(ops)
    check("point inside deletion -> None", tm(230.0) is None)
    check("point after -> shifted by block minus deletion", abs(tm(300.0) - (300.0 - 17.32 + 67.04)) < 1e-6)
    cv = compile_variant("V", "v", ops, SRC, BASE)
    check("expected 377.32 - 17.32 + 67.04 = 427.04", abs(cv.expected - 427.04) < 1e-6, str(cv.expected))


def test_clamp_and_skip() -> None:
    print("ripple — out beyond media is clamped to the frame grid; unknown source is skipped with a note")
    ops = [I(10.0, "fight", 1.0, 12.0), I(20.0, "ghost", 0, 1.0)]
    cv = compile_variant("V", "v", ops, SRC, BASE, dur_of=lambda p: 9.653)
    ins = [o for o in cv.ops if o["op"] == "ins"]
    check("one insert survives", len(ins) == 1 and ins[0]["code"] == "fight", str(ins))
    check("out clamped to 9.64 (frame grid)", abs(ins[0]["o"] - 9.64) < 1e-6, str(ins[0]["o"]))
    check("notes name both events", any(n.startswith("CLAMP") for n in cv.notes) and any(n.startswith("SKIP") for n in cv.notes), str(cv.notes))


def test_srt_roundtrip() -> None:
    print("ripple — SRT load/dump")
    body = srt_dump([(1.5, 3.25, "a\nb"), (4.0, 5.0, "c")])
    cues = srt_load(body)
    check("two cues back", len(cues) == 2 and cues[0][2] == "a\nb", str(cues))
    check("millisecond timecode", "00:00:01,500 --> 00:00:03,250" in body, body)


def main() -> int:
    for t in (test_order_dels_first_then_inserts_desc, test_same_anchor_list_order_is_screen_order, test_insert_coordinates_after_deletions,
              test_audio_final_coordinates, test_cue_remap_rules, test_time_map_and_expected, test_clamp_and_skip, test_srt_roundtrip):
        t()
    print(f"\nripple: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
