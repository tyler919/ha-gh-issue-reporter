"""GitHub Issue Reporter — entry point.

Reads `gh_issue_reporter:` from `configuration.yaml`, wires up the logging
handler, and tears it down again on Home Assistant shutdown.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DEFAULT_REPO,
    CONF_INTEGRATIONS,
    CONF_REPO,
    CONF_TOKEN,
    DOMAIN,
)
from .github_client import GitHubClient
from .reporter import GitHubIssueReporter

_LOGGER = logging.getLogger(__name__)

# Per-integration block: just `repo: owner/name` for now. Kept as its own
# schema so that adding e.g. `labels:` later is a one-line change.
_INTEGRATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REPO): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_TOKEN): cv.string,
                vol.Optional(CONF_INTEGRATIONS, default={}): {
                    cv.string: _INTEGRATION_SCHEMA
                },
                vol.Optional(CONF_DEFAULT_REPO): cv.string,
            }
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up gh_issue_reporter from configuration.yaml."""
    conf = config.get(DOMAIN)
    if not conf:
        # Nothing to do — the integration is listed but unconfigured.
        return True

    token: str = conf[CONF_TOKEN]
    if not token:
        # Voluptuous already requires the key; this defends against an
        # empty `!secret` resolving to "". We stay loaded but idle so
        # restart-fixing the secret picks up cleanly.
        _LOGGER.warning(
            "gh_issue_reporter: token is empty; integration will stay idle"
        )
        return True

    integration_repos: dict[str, str] = {
        name: entry[CONF_REPO]
        for name, entry in conf.get(CONF_INTEGRATIONS, {}).items()
    }
    default_repo: str | None = conf.get(CONF_DEFAULT_REPO)

    # Reuse HA's shared aiohttp session — gives us sane connection pooling
    # and lifecycle without us having to manage anything.
    session = async_get_clientsession(hass)
    client = GitHubClient(session=session, token=token)

    handler = GitHubIssueReporter(
        hass=hass,
        client=client,
        integration_repos=integration_repos,
        default_repo=default_repo,
    )

    # Attach to the root logger. The handler filters by logger name itself,
    # so this is the simplest way to catch any `custom_components.*` logger
    # (including sub-loggers like `custom_components.helldivers2.api`)
    # without us having to enumerate them up front.
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    hass.data[DOMAIN] = {"handler": handler}

    async def _remove_handler(_event: Event) -> None:
        # Clean up on shutdown so reloads/restarts don't stack handlers.
        root_logger.removeHandler(handler)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _remove_handler)

    _LOGGER.info(
        "gh_issue_reporter: watching %d integration(s)%s",
        len(integration_repos),
        f", default_repo={default_repo}" if default_repo else "",
    )
    return True
