"""Pattern redaction for text that leaves this package.

The list of secret shapes lives in scripts/coordination.py, so the server, the
scripts and the Worker's redactText all use the same one. This module only
makes that function reachable from the GitHub clients, which are imported on
their own in tests and do not otherwise load the scripts directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from coordination import redact_secrets  # noqa: E402

__all__ = ["redact_secrets"]
