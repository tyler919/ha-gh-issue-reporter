"""Recognise Home Assistant's system-wide startup-timeout cancellations.

When HA's bootstrap runs past its stage deadline, the global timeout
manager cancels *every* setup task still in flight with
`asyncio.CancelledError("Global task timeout: Bootstrap stage N timeout")`.
The traceback then shows whichever custom integration happened to be
awaiting HA core (typically `async_forward_entry_setups` ->
`loader.async_get_platforms`) at that instant, so traceback attribution
blames an innocent integration.

These are not integration bugs, so the reporter drops them instead of
filing an issue. Kept free of Home Assistant imports so it can be tested
without HA installed.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

# Messages HA's timeout manager (homeassistant/util/timeout.py) attaches
# to the CancelledError it raises when a global deadline expires.
_GLOBAL_TIMEOUT_RE = re.compile(
    r"Global task timeout|Bootstrap stage \d+ timeout", re.IGNORECASE
)


def _in_integration(filename: str, integration: str) -> bool:
    path = filename.replace("\\", "/")
    return f"/custom_components/{integration}/" in path


def is_global_startup_timeout(
    error_type: str,
    error_message: str,
    frame_filenames: Sequence[str],
    integration: str,
) -> bool:
    """True if this is HA's global startup timeout landing in core code.

    `frame_filenames` is outermost-first, as in a printed traceback. The
    integration may appear further up the stack (it was awaiting core when
    the deadline hit); what matters is that the innermost frame — where
    the cancellation was actually delivered — is not the integration's own
    code. If it is, the integration itself was what was running, and we
    keep reporting it.
    """
    if error_type != "CancelledError":
        return False
    if not _GLOBAL_TIMEOUT_RE.search(error_message or ""):
        return False
    if not frame_filenames:
        return True
    return not _in_integration(frame_filenames[-1], integration)
