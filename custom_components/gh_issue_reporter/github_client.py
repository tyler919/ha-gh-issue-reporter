"""Minimal async GitHub REST client.

We only need three endpoints:
  * search open issues by title (for dedup)
  * create an issue
  * add a comment to an existing issue

We use aiohttp, which Home Assistant already ships, so there are no external
package requirements.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubAuthError(Exception):
    """Raised when GitHub returns 401/403. Means the PAT is bad or lacks scope."""


class GitHubClient:
    """Thin async wrapper around the few GitHub REST endpoints we use.

    A single instance is shared by the reporter. The token is held only on the
    instance and never logged — __repr__ explicitly masks it.
    """

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    def __repr__(self) -> str:
        # Defensive: if something ever logs the client, the token must not leak.
        return "<GitHubClient token=***>"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ha-gh-issue-reporter",
        }

    async def search_open_issue(
        self, repo: str, title_fingerprint: str
    ) -> int | None:
        """Return the number of the first open issue whose title contains the
        fingerprint, or None if no match exists.

        Notes:
          * GitHub's search API is eventually consistent — a brand-new issue
            may not be findable for ~30s. We accept that: occasionally
            producing a duplicate is better than silently dropping reports.
          * The fingerprint is quoted so its `:` / `[` / `]` characters aren't
            parsed as search qualifiers.
        """
        query = f'"{title_fingerprint}" in:title is:issue is:open repo:{repo}'
        url = f"{GITHUB_API}/search/issues"
        params = {"q": query, "per_page": "1"}

        async with self._session.get(
            url, headers=self._headers(), params=params
        ) as resp:
            if resp.status in (401, 403):
                raise GitHubAuthError(f"GitHub auth failed: {resp.status}")
            if resp.status != 200:
                body = await resp.text()
                _LOGGER.debug(
                    "Issue search returned %s: %s", resp.status, body[:200]
                )
                return None
            data: dict[str, Any] = await resp.json()

        items = data.get("items") or []
        if not items:
            return None
        return int(items[0]["number"])

    async def create_issue(self, repo: str, title: str, body: str) -> int | None:
        """Create an issue and return its number, or None on failure."""
        url = f"{GITHUB_API}/repos/{repo}/issues"
        payload = {"title": title, "body": body}

        async with self._session.post(
            url, headers=self._headers(), json=payload
        ) as resp:
            if resp.status in (401, 403):
                raise GitHubAuthError(f"GitHub auth failed: {resp.status}")
            if resp.status >= 300:
                body_text = await resp.text()
                _LOGGER.warning(
                    "Failed to create issue in %s: %s %s",
                    repo,
                    resp.status,
                    body_text[:200],
                )
                return None
            data: dict[str, Any] = await resp.json()
        return int(data["number"])

    async def add_comment(
        self, repo: str, issue_number: int, body: str
    ) -> bool:
        """Append a comment to an existing issue. Returns True on success."""
        url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
        payload = {"body": body}

        async with self._session.post(
            url, headers=self._headers(), json=payload
        ) as resp:
            if resp.status in (401, 403):
                raise GitHubAuthError(f"GitHub auth failed: {resp.status}")
            if resp.status >= 300:
                body_text = await resp.text()
                _LOGGER.warning(
                    "Failed to comment on %s#%s: %s %s",
                    repo,
                    issue_number,
                    resp.status,
                    body_text[:200],
                )
                return False
        return True
