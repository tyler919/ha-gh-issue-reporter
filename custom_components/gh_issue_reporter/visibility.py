"""Warn when a report target repo is public.

Issues carry the raw exception message and full traceback, unredacted.
That can include LAN IPs, URLs with keys in them, webhook IDs and API
responses, so a public target repo means world-readable reports. We don't
block setup over it (the user may have chosen it on purpose); we just say
so loudly, once per repo per HA session.

Kept free of Home Assistant imports so it can be tested without HA
installed. `client` only needs an async `repo_is_private(repo)` that
returns True / False, or None when the repo can't be read.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

# Repos we've already warned about this session. Module-level so a second
# setup in the same process doesn't repeat the warning.
_warned_public: set[str] = set()


async def warn_public_repos(
    client: Any,
    integration_repos: Mapping[str, str],
    default_repo: str | None,
    logger: logging.Logger,
) -> list[str]:
    """Check each distinct target repo once; warn for public ones.

    Returns the repos warned about on this call (for tests).
    """
    targets: dict[str, list[str]] = {}
    for integration, repo in integration_repos.items():
        targets.setdefault(repo, []).append(integration)
    if default_repo:
        targets.setdefault(default_repo, []).append("default_repo")

    warned: list[str] = []
    for repo, sources in targets.items():
        if repo in _warned_public:
            continue
        try:
            private = await client.repo_is_private(repo)
        except Exception as err:  # noqa: BLE001 - best-effort check only
            logger.debug("Could not check visibility of %s: %s", repo, err)
            continue
        if private is None:
            logger.debug("Could not read %s to check its visibility", repo)
            continue
        if private:
            continue

        _warned_public.add(repo)
        warned.append(repo)
        parts = [s for s in sources if s != "default_repo"]
        if "default_repo" in sources:
            parts.append(
                "every custom integration not listed under `integrations:` "
                "(including ones you didn't write)"
            )
        scope = ", ".join(parts)
        logger.warning(
            "gh_issue_reporter: report repo %s is PUBLIC. Error reports for "
            "%s will be world-readable, with unredacted exception messages "
            "and tracebacks (LAN IPs, URLs with keys, webhook IDs). Point "
            "it at a private repo. Reporting continues.",
            repo,
            scope,
        )
    return warned
