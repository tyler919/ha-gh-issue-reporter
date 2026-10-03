"""Tests for the public-report-repo startup warning.

Run with: python -m unittest discover -s tests
(No Home Assistant install needed — the module under test is HA-free.)
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import unittest

_MODULE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "gh_issue_reporter"
    / "visibility.py"
)


def _load():
    # Fresh module per test so the once-per-session set starts empty.
    spec = importlib.util.spec_from_file_location("visibility", _MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClient:
    def __init__(self, private: dict[str, bool | None]) -> None:
        self.private = private
        self.calls: list[str] = []

    async def repo_is_private(self, repo: str) -> bool | None:
        self.calls.append(repo)
        value = self.private[repo]
        if isinstance(value, Exception):
            raise value
        return value


class WarnPublicReposTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mod = _load()
        self.logger = logging.getLogger("test.gh_issue_reporter")

    async def test_private_repo_is_silent(self) -> None:
        client = FakeClient({"tyler919/ha-error-reports": True})
        with self.assertNoLogs(self.logger, level="WARNING"):
            warned = await self.mod.warn_public_repos(
                client, {"helldivers2": "tyler919/ha-error-reports"},
                "tyler919/ha-error-reports", self.logger,
            )
        self.assertEqual(warned, [])
        # Same repo used twice is only looked up once.
        self.assertEqual(client.calls, ["tyler919/ha-error-reports"])

    async def test_public_repo_warns_once_per_session(self) -> None:
        client = FakeClient({"tyler919/helldivers2-ha": False})
        repos = {"helldivers2": "tyler919/helldivers2-ha"}
        with self.assertLogs(self.logger, level="WARNING") as logs:
            warned = await self.mod.warn_public_repos(
                client, repos, None, self.logger
            )
        self.assertEqual(warned, ["tyler919/helldivers2-ha"])
        self.assertIn("tyler919/helldivers2-ha is PUBLIC", logs.output[0])
        self.assertIn("world-readable", logs.output[0])
        self.assertIn("helldivers2", logs.output[0])

        with self.assertNoLogs(self.logger, level="WARNING"):
            again = await self.mod.warn_public_repos(
                client, repos, None, self.logger
            )
        self.assertEqual(again, [])

    async def test_public_default_repo_says_it_catches_everything(self) -> None:
        client = FakeClient({"tyler919/ha-misc": False})
        with self.assertLogs(self.logger, level="WARNING") as logs:
            await self.mod.warn_public_repos(
                client, {}, "tyler919/ha-misc", self.logger
            )
        self.assertIn("every custom integration", logs.output[0])

    async def test_unreadable_or_failing_lookup_never_raises(self) -> None:
        client = FakeClient(
            {"a/missing": None, "a/boom": RuntimeError("network down")}
        )
        with self.assertNoLogs(self.logger, level="WARNING"):
            warned = await self.mod.warn_public_repos(
                client, {"x": "a/missing", "y": "a/boom"}, None, self.logger
            )
        self.assertEqual(warned, [])


if __name__ == "__main__":
    unittest.main()
