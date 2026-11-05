"""Runner job: open Resolve project -> .prproj. See `core.adapters.resolve_spec` for the env vars."""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
from core.adapters.resolve_spec import extract  # noqa: E402
from core.prproj_build import build  # noqa: E402

out, template = os.environ["PRPROJ_OUT"], os.environ["PRPROJ_TEMPLATE"]
only = [x for x in os.environ.get("PRPROJ_TIMELINES", "").split("|") if x] or None
spec = extract(project, mp, only, resolve=resolve)
if os.environ.get("PRPROJ_SPEC"):
    json.dump(spec, open(os.environ["PRPROJ_SPEC"], "w", encoding="utf-8"), ensure_ascii=False)
res = build(spec, template, out)
print(json.dumps({k: v for k, v in res.items() if k != "report"}, ensure_ascii=False))
print("NOT TRANSFERRED / NOTES:")
for line in res["report"]:
    print(" -", line)
