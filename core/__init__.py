"""premiere-claude-bridge — universal NLE core.

The editing brain is NLE-agnostic. A cut is decided once as a `Cutlist`
(formalized in `core.cutlist`), which round-trips losslessly through
OpenTimelineIO. Per-NLE adapters (`core.adapters`) render that one cutlist
into Premiere, DaVinci Resolve, or Final Cut.

See the epic: https://github.com/koptsev63/premiere-claude-bridge/issues/6
"""

__version__ = "0.3.0-dev"
