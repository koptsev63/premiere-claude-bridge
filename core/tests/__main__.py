"""Run every core test module: `python -m core.tests`.

Aggregates per-module main() return codes. No pytest dependency.
"""

from __future__ import annotations

import sys
import types

from core.tests import (
    test_adapters,
    test_asr_verify,
    test_beats,
    test_cleanup,
    test_colorgate,
    test_concrete_adapters,
    test_conform,
    test_cutlist,
    test_defects,
    test_denoise,
    test_drt_audit,
    test_ducking,
    test_highlights,
    test_hits,
    test_library,
    test_loudness,
    test_media_gate,
    test_motion,
    test_overlays,
    test_pk_runner,
    test_probe,
    test_prproj,
    test_qc,
    test_render,
    test_resolve_run,
    test_review_loop,
    test_rhythm_gate,
    test_ripple,
    test_screen_comp,
    test_shotmatch,
    test_silence,
    test_subtitles,
    test_taste,
    test_value,
    test_variants,
    test_xml_handoff,
    test_xmeml,
    # dailies prep and NLE handoff add-ons by Denis Nazarov (November 2026)
    test_prproj_build,
    test_avb_build,
    test_avp_build,
)

MODULES = [
    test_cutlist,
    test_adapters,
    test_concrete_adapters,
    test_review_loop,
    test_conform,
    test_probe,
    test_cleanup,
    test_value,
    test_variants,
    test_qc,
    test_render,
    test_library,
    test_silence,
    test_subtitles,
    test_overlays,
    # OpenReel-inspired audio/edit modules (June 2026)
    test_ducking,
    test_denoise,
    test_highlights,
    test_beats,
    test_asr_verify,
    # colour gate + human-in-the-loop round trip (August 2026)
    test_colorgate,
    test_prproj,
    # direct runners, loudness, screen replacement (October 2026)
    test_pk_runner,
    test_resolve_run,
    test_loudness,
    test_screen_comp,
    # ripple variants of a hand-built sequence (October 2026)
    test_ripple,
    test_xml_handoff,
    # per-shot colour match baked to LUTs (October 2026)
    test_shotmatch,
    # .drt pull audit against the editor's cut (October 2026)
    test_drt_audit,
    # dailies prep and NLE handoff add-ons by Denis Nazarov (November 2026)
    test_prproj_build,
    test_avb_build,
    test_avp_build,
    # editor rules from a live cut, Denis (16.11.2026)
    test_taste,
    # motion at cuts (November 2026)
    test_motion,
    # media gate, rhythm gate, defects a viewer sees (November 2026)
    test_media_gate,
    test_defects,
]


def _rhythm_gate_main() -> int:
    """test_rhythm_gate has no main() of its own (its `main` is the gate CLI): run its test_ functions."""
    try:
        for name in sorted(vars(test_rhythm_gate)):
            fn = getattr(test_rhythm_gate, name)
            if name.startswith("test_") and callable(fn):
                fn()
    except SystemExit as e:
        return 1 if e.code else 0
    print(f"rhythm_gate: {test_rhythm_gate._passed} passed")
    return 0


MODULES.append(types.SimpleNamespace(__name__=test_rhythm_gate.__name__, main=_rhythm_gate_main))


def main() -> int:
    rc = 0
    for mod in MODULES:
        print(f"\n=== {mod.__name__} ===")
        rc |= mod.main()
    print("\n" + ("ALL GREEN" if rc == 0 else "FAILURES PRESENT"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
