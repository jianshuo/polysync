"""polysync — multicam audio sync + director-style auto-edit.

Align N recordings of one event by audio cross-correlation (envelope-based,
robust at low SNR), emit reversible `.sync.json` sidecars (originals are never
re-encoded), then auto-cut / picture-in-picture them into a single MP4.

Public API:
    from polysync import compute_sync, SyncResult, SyncError
    from polysync.sidecar import read_sidecar, write_sidecar
"""
from .sync import compute_sync, SyncResult, SyncError
from .sidecar import read_sidecar, write_sidecar, sidecar_path, SCHEMA_VERSION

__version__ = "0.1.0"
__all__ = [
    "compute_sync", "SyncResult", "SyncError",
    "read_sidecar", "write_sidecar", "sidecar_path", "SCHEMA_VERSION",
    "__version__",
]
