"""Logging handler + dispatcher: catches errors from other custom
integrations and files them as GitHub issues.

Design notes you'd otherwise have to re-derive while reading this:

  * We attach to the **root** logger, not to each `custom_components.*`
    child, because:
      - we don't know up front which sub-loggers other integrations will
        create (many use names like `custom_components.helldivers2.api`);
      - `_LOGGER.exception(...)` propagates to root by default, so the root
        handler is guaranteed to see it.
    The handler filters by logger name inside `emit()`, so this is not as
    broad in practice as it looks.

  * Only records with `exc_info` are considered. A bare
    `_LOGGER.error("oops")` is skipped on purpose: with no traceback there
    isn't enough information to file a useful automated bug.

  * `logging.Handler.emit` runs **synchronously** in whatever thread the
    error was logged from (often a worker thread or executor). We must
    not touch aiohttp from there. Instead we hand off to HA's event loop
    via `hass.loop.call_soon_threadsafe(hass.async_create_task, ...)`.

  * Dedup uses a short "fingerprint" derived from the error type + first
    80 chars of the message. Same fingerprint on the same repo within
    RATE_LIMIT_SECONDS = no API calls. The fingerprint is also embedded
    in the issue title, which is how the search-based dedup finds prior
    open issues.

  * Two ways to identify which integration to file under:
      1. **Logger name** — `custom_components.<name>.*` is the simple,
         direct case. Anything an integration logs via its own `_LOGGER`
         will land here.
      2. **Traceback fallback** — HA core's executor catches exceptions
         from integration callbacks (e.g. a sync lambda passed to
         `async_track_time_interval` that throws) and logs them on
         `homeassistant` itself, not on the integration's logger. To
         still attribute these to the right repo, we walk the traceback
         and look for the deepest `custom_components/<name>/` frame.

  * We must never produce an issue for an error logged by ourselves.
    Doing so would feedback-loop and burn through GitHub rate limits in
    seconds. The handler explicitly drops any record whose logger name
    is inside this integration's namespace, **and** any traceback-based
    attribution that points at this integration.
"""
from __future__ import annotations

import logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from types import TracebackType
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import (
    CUSTOM_COMPONENT_PREFIX,
    DOMAIN,
    RATE_LIMIT_SECONDS,
    TITLE_MESSAGE_MAX,
    TITLE_PREFIX,
)
from .github_client import GitHubAuthError, GitHubClient

_LOGGER = logging.getLogger(__name__)

# Logger-name prefix for anything this integration emits. Records starting
# with this string must never trigger a report.
_OWN_LOGGER_PREFIX = f"{CUSTOM_COMPONENT_PREFIX}{DOMAIN}"

# Path-marker substrings used when scanning tracebacks for the originating
# integration. Cover both unix and windows separators so the fallback works
# on either host.
_PATH_MARKERS = (
    f"{os.sep}custom_components{os.sep}",
    "/custom_components/",
    "\\custom_components\\",
)


@dataclass(frozen=True)
class ErrorEvent:
    """Snapshot of a captured log record.

    Built synchronously inside the logging handler so nothing about the
    underlying `LogRecord` needs to survive into the async world.
    """

    logger_name: str
    integration: str  # The part after `custom_components.`, e.g. "helldivers2".
    level: str
    message: str
    error_type: str
    error_message: str
    traceback_text: str
    timestamp: datetime
    source: str  # "logger" or "traceback" — how integration was attributed.


class GitHubIssueReporter(logging.Handler):
    """Logging handler that turns ERROR/CRITICAL records with tracebacks
    into GitHub issues (or comments on existing ones)."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: GitHubClient,
        integration_repos: dict[str, str],
        default_repo: str | None,
    ) -> None:
        # Threshold at ERROR. We still re-check inside emit() because other
        # handlers / loggers may lower the effective level.
        super().__init__(level=logging.ERROR)
        self._hass = hass
        self._client = client
        self._integration_repos = integration_repos
        self._default_repo = default_repo

        # (repo, fingerprint) -> last dispatch time (UTC). In-memory only;
        # resetting on restart is fine and intentional.
        self._last_sent: dict[tuple[str, str], datetime] = {}

        # We warn about auth failure exactly once per HA session. Without
        # this guard, every subsequent error would re-warn, which is noisy
        # and (since we filter our own logs) ineffective at producing
        # actionable output anyway.
        self._auth_warned = False

    # ---------------------------------------------------------------
    # Synchronous side: runs on whatever thread emitted the log record.
    # ---------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Capture an eligible record and hand it off to the event loop."""
        try:
            if record.levelno < logging.ERROR:
                return

            # Recursion guard: our own logs must never produce an issue.
            if record.name == _OWN_LOGGER_PREFIX or record.name.startswith(
                f"{_OWN_LOGGER_PREFIX}."
            ):
                return

            # Skip pure messages — no traceback means no useful bug report.
            if not record.exc_info:
                return

            # Two-step attribution: prefer the logger name (cheap, direct),
            # fall back to scanning the traceback for a custom_components
            # frame (handles errors that bubble up to homeassistant core).
            source = "logger"
            integration = self._integration_from_logger(record.name)
            if integration is None:
                integration = self._integration_from_traceback(record.exc_info)
                source = "traceback"
                if integration is None:
                    return

            # Second recursion guard: if the traceback fallback resolves to
            # ourselves, drop the record. Otherwise an error inside this
            # integration that happens to be logged by HA core (not us)
            # would loop back through us.
            if integration == DOMAIN:
                return

            repo = self._integration_repos.get(integration) or self._default_repo
            if not repo:
                return

            event = self._build_event(record, integration, source)

            # call_soon_threadsafe is the documented way to schedule work on
            # the asyncio loop from a non-loop thread. We use it to call
            # async_create_task, which gives the coroutine proper HA lifecycle.
            self._hass.loop.call_soon_threadsafe(
                self._hass.async_create_task,
                self._dispatch(repo, event),
            )
        except Exception:  # noqa: BLE001
            # A logging handler must never raise — that would break the
            # logging subsystem for everyone. Defer to the stdlib's own
            # error-reporting path and move on.
            self.handleError(record)

    @staticmethod
    def _integration_from_logger(logger_name: str) -> str | None:
        """`custom_components.helldivers2.api` -> `helldivers2`. Otherwise None."""
        if not logger_name.startswith(CUSTOM_COMPONENT_PREFIX):
            return None
        tail = logger_name[len(CUSTOM_COMPONENT_PREFIX) :]
        # First dotted segment is the integration's domain.
        first = tail.split(".", 1)[0]
        return first or None

    @staticmethod
    def _integration_from_traceback(
        exc_info: tuple[type[BaseException], BaseException, TracebackType]
        | tuple[None, None, None]
        | bool
        | None,
    ) -> str | None:
        """Walk the traceback. Return the integration name from the deepest
        frame whose file lives in a `custom_components/<name>/` directory.

        Returning the *deepest* match (not the outermost) gives us the
        actual code that raised, not whatever caller happened to wrap it.
        For errors that pass through HA core's executor, the outer frames
        are inside `homeassistant/helpers/frame.py`, and the integration
        frame is the innermost one — that's the one we want to attribute.
        """
        if not exc_info or exc_info is True:
            return None
        # Mypy/practical: when not bool/None, exc_info is a 3-tuple.
        if not isinstance(exc_info, tuple) or len(exc_info) != 3:
            return None
        tb = exc_info[2]
        if tb is None:
            return None

        found: str | None = None
        while tb is not None:
            filename = tb.tb_frame.f_code.co_filename or ""
            marker_idx, marker_len = _find_marker(filename)
            if marker_idx >= 0:
                rest = filename[marker_idx + marker_len:]
                # Take the first path segment as the integration's folder
                # name. Works whether the path uses / or \ next.
                name = _first_path_segment(rest)
                if name:
                    found = name  # keep walking so deepest wins
            tb = tb.tb_next
        return found

    @staticmethod
    def _build_event(
        record: logging.LogRecord, integration: str, source: str
    ) -> ErrorEvent:
        # `exc_info` is normally a `(type, value, tb)` tuple when set via
        # `logger.error(..., exc_info=...)` or `logger.exception(...)`.
        exc_info = record.exc_info
        if exc_info and exc_info is not True:
            err_type = exc_info[0].__name__ if exc_info[0] else "UnknownError"
            err_msg = str(exc_info[1]) if exc_info[1] else ""
            tb_text = "".join(traceback.format_exception(*exc_info)).rstrip()
        else:
            err_type = "UnknownError"
            err_msg = record.getMessage()
            tb_text = "(traceback unavailable)"

        return ErrorEvent(
            logger_name=record.name,
            integration=integration,
            level=record.levelname,
            message=record.getMessage(),
            error_type=err_type,
            error_message=err_msg,
            traceback_text=tb_text,
            timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
            source=source,
        )

    # ---------------------------------------------------------------
    # Async side: runs on HA's event loop.
    # ---------------------------------------------------------------

    async def _dispatch(self, repo: str, event: ErrorEvent) -> None:
        fingerprint = self._fingerprint(event)
        now = datetime.now(timezone.utc)

        # Per (repo, fingerprint) rate limit. Cheap, in-memory, and good
        # enough to keep a misbehaving integration from spamming GitHub.
        last = self._last_sent.get((repo, fingerprint))
        if last is not None and (now - last).total_seconds() < RATE_LIMIT_SECONDS:
            return

        title = self._title(event)
        body = self._body(event)

        try:
            existing = await self._client.search_open_issue(repo, fingerprint)
            if existing is not None:
                comment = (
                    f"Recurrence at `{event.timestamp.isoformat()}` "
                    f"on Home Assistant `{HA_VERSION}` "
                    f"(logger `{event.logger_name}`, source `{event.source}`)."
                )
                await self._client.add_comment(repo, existing, comment)
            else:
                await self._client.create_issue(repo, title, body)
        except GitHubAuthError:
            if not self._auth_warned:
                # Note: this _LOGGER is inside our own namespace, so the
                # warning will be filtered out by emit() above — no loop.
                _LOGGER.warning(
                    "GitHub authentication failed — issue reporting is "
                    "disabled until you fix the `token` in your "
                    "gh_issue_reporter config and restart."
                )
                self._auth_warned = True
            return
        except Exception as err:  # noqa: BLE001
            # Network blip, GitHub 5xx, etc. Never crash HA's event loop.
            _LOGGER.debug("gh_issue_reporter dispatch failed: %s", err)
            return

        # Only mark the fingerprint "sent" after a (probable) success so a
        # transient network error doesn't accidentally mute us for 5 minutes.
        self._last_sent[(repo, fingerprint)] = now

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------

    @staticmethod
    def _fingerprint(event: ErrorEvent) -> str:
        """Short, human-readable signature embedded in the issue title.

        Same fingerprint = same bug, as far as we're concerned. We use the
        exception type plus the first 80 characters of the message. That
        catches the common case (same exception from the same code path)
        without being so loose that genuinely distinct bugs collide.
        """
        head = (
            event.error_message.strip().splitlines()[0]
            if event.error_message
            else ""
        )
        head = head[:TITLE_MESSAGE_MAX]
        return f"{event.error_type}: {head}".strip().rstrip(":")

    def _title(self, event: ErrorEvent) -> str:
        return f"{TITLE_PREFIX} {self._fingerprint(event)}"

    @staticmethod
    def _body(event: ErrorEvent) -> str:
        return (
            f"**Automated report from `gh_issue_reporter`.**\n\n"
            f"- **Logger:** `{event.logger_name}`\n"
            f"- **Integration:** `{event.integration}` (attributed via `{event.source}`)\n"
            f"- **Level:** `{event.level}`\n"
            f"- **Error type:** `{event.error_type}`\n"
            f"- **Timestamp (UTC):** `{event.timestamp.isoformat()}`\n"
            f"- **Home Assistant version:** `{HA_VERSION}`\n\n"
            f"**Message**\n\n"
            f"> {event.error_message or '(no message)'}\n\n"
            f"**Traceback**\n\n"
            f"```pytb\n{event.traceback_text}\n```\n"
        )


# ---------------------------------------------------------------------------
# Helpers used by `_integration_from_traceback`. Kept as module-level pure
# functions so they're trivial to test in isolation if we ever add tests.
# ---------------------------------------------------------------------------


def _find_marker(path: str) -> tuple[int, int]:
    """Return (index, marker_length) of the earliest custom_components/
    marker in `path`, or (-1, 0) if none.

    We try multiple markers because tracebacks captured on Windows can mix
    separators depending on how a path was constructed.
    """
    best_idx = -1
    best_len = 0
    for marker in _PATH_MARKERS:
        idx = path.find(marker)
        if idx >= 0 and (best_idx == -1 or idx < best_idx):
            best_idx = idx
            best_len = len(marker)
    return best_idx, best_len


def _first_path_segment(rest: str) -> str:
    """Given a string like `helldivers2/__init__.py`, return `helldivers2`.

    Treats both `/` and `\\` as separators. Returns `""` if `rest` is
    empty or starts with a separator (which would be a malformed match).
    """
    if not rest:
        return ""
    # Find earliest of either separator.
    cuts: list[int] = []
    for sep in ("/", "\\"):
        idx = rest.find(sep)
        if idx >= 0:
            cuts.append(idx)
    if not cuts:
        return rest
    cut = min(cuts)
    return rest[:cut]
