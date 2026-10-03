"""Regression tests for the global-startup-timeout filter.

Run with: python -m unittest discover -s tests
(No Home Assistant install needed — the module under test is HA-free.)
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import unittest

_MODULE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "gh_issue_reporter"
    / "startup_timeout.py"
)
_spec = importlib.util.spec_from_file_location("startup_timeout", _MODULE)
startup_timeout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(startup_timeout)
is_global_startup_timeout = startup_timeout.is_global_startup_timeout

# Captured verbatim from tyler919/minecraft-ha#2 (HA 2026.6.4, 2026-07-13).
MINECRAFT_HA_2 = '''\
Traceback (most recent call last):
  File "/usr/src/homeassistant/homeassistant/config_entries.py", line 796, in __async_setup_with_context
    result = await component.async_setup_entry(hass, self)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/config/custom_components/minecraft_webhook/__init__.py", line 137, in async_setup_entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  File "/usr/src/homeassistant/homeassistant/config_entries.py", line 2741, in async_forward_entry_setups
    await integration.async_get_platforms(platforms)
  File "/usr/src/homeassistant/homeassistant/loader.py", line 1193, in async_get_platforms
    import_future.result()
    ~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/src/homeassistant/homeassistant/loader.py", line 1193, in async_get_platforms
    import_future.result()
    ~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/src/homeassistant/homeassistant/loader.py", line 1193, in async_get_platforms
    import_future.result()
    ~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/src/homeassistant/homeassistant/loader.py", line 1162, in async_get_platforms
    await self.hass.async_add_import_executor_job(
        self._load_platforms, platform_names
    )
asyncio.exceptions.CancelledError: Global task timeout: Bootstrap stage 2 timeout
'''

# Captured verbatim from tyler919/helldivers2-ha#8 (HA 2026.6.4, 2026-07-15).
HELLDIVERS2_HA_8 = '''\
Traceback (most recent call last):
  File "/usr/src/homeassistant/homeassistant/config_entries.py", line 796, in __async_setup_with_context
    result = await component.async_setup_entry(hass, self)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/config/custom_components/helldivers2/__init__.py", line 72, in async_setup_entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  File "/usr/src/homeassistant/homeassistant/config_entries.py", line 2741, in async_forward_entry_setups
    await integration.async_get_platforms(platforms)
  File "/usr/src/homeassistant/homeassistant/loader.py", line 1193, in async_get_platforms
    import_future.result()
    ~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/src/homeassistant/homeassistant/loader.py", line 1162, in async_get_platforms
    await self.hass.async_add_import_executor_job(
        self._load_platforms, platform_names
    )
asyncio.exceptions.CancelledError: Global task timeout: Bootstrap stage 2 timeout
'''


def _parse(tb_text: str) -> tuple[str, str, list[str]]:
    """Captured traceback text -> (error_type, error_message, filenames)."""
    files = re.findall(r'^  File "([^"]+)"', tb_text, re.MULTILINE)
    last = tb_text.rstrip().splitlines()[-1]
    qualname, _, message = last.partition(": ")
    return qualname.rsplit(".", 1)[-1], message, files


class GlobalStartupTimeoutTest(unittest.TestCase):
    def test_minecraft_ha_2_is_suppressed(self) -> None:
        err_type, msg, files = _parse(MINECRAFT_HA_2)
        self.assertTrue(
            is_global_startup_timeout(err_type, msg, files, "minecraft_webhook")
        )

    def test_helldivers2_ha_8_is_suppressed(self) -> None:
        err_type, msg, files = _parse(HELLDIVERS2_HA_8)
        self.assertTrue(is_global_startup_timeout(err_type, msg, files, "helldivers2"))

    def test_other_stage_numbers_and_bare_global_timeout(self) -> None:
        _, _, files = _parse(MINECRAFT_HA_2)
        for msg in ("Global task timeout: Bootstrap stage 1 timeout",
                    "Global task timeout"):
            self.assertTrue(
                is_global_startup_timeout("CancelledError", msg, files, "minecraft_webhook")
            )

    def test_cancellation_landing_in_integration_code_is_still_reported(self) -> None:
        # If the innermost frame is the integration's own code, it was the
        # thing running when the deadline hit — keep reporting it.
        _, msg, files = _parse(HELLDIVERS2_HA_8)
        files = files + ["/config/custom_components/helldivers2/api.py"]
        self.assertFalse(is_global_startup_timeout("CancelledError", msg, files, "helldivers2"))

    def test_other_cancellations_are_still_reported(self) -> None:
        _, _, files = _parse(MINECRAFT_HA_2)
        self.assertFalse(
            is_global_startup_timeout("CancelledError", "", files, "minecraft_webhook")
        )

    def test_other_exception_types_are_still_reported(self) -> None:
        _, msg, files = _parse(MINECRAFT_HA_2)
        self.assertFalse(
            is_global_startup_timeout("TimeoutError", msg, files, "minecraft_webhook")
        )

    def test_windows_paths(self) -> None:
        files = [r"C:\config\custom_components\helldivers2\api.py"]
        self.assertFalse(
            is_global_startup_timeout(
                "CancelledError", "Global task timeout", files, "helldivers2"
            )
        )


if __name__ == "__main__":
    unittest.main()
