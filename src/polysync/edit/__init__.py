"""Director-style multicam auto-edit on top of polysync sidecars.

autoedit  — build an EDL (which cam is on screen each second) from synced inputs
render_cuts — render the EDL to one MP4 (hard cuts)
render_pip  — render the EDL with a picture-in-picture inset
"""
from .autoedit import build_edl

__all__ = ["build_edl"]
