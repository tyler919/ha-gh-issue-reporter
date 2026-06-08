"""Constants for the gh_issue_reporter integration."""
from __future__ import annotations

DOMAIN = "gh_issue_reporter"

# Configuration keys.
CONF_TOKEN = "token"
CONF_INTEGRATIONS = "integrations"
CONF_REPO = "repo"
CONF_DEFAULT_REPO = "default_repo"

# Don't file/comment more than once per 5 minutes per (repo, fingerprint).
# In-memory only; resets on HA restart.
RATE_LIMIT_SECONDS = 300

# Marker used in issue titles. Lets humans (and our search) tell auto-filed
# issues apart from hand-written ones at a glance.
TITLE_PREFIX = "[auto]"

# Max length of the error-message excerpt embedded in the title (and the
# fingerprint). Short enough to stay readable, long enough to disambiguate
# most real bugs.
TITLE_MESSAGE_MAX = 80

# Logger prefix Home Assistant uses for all custom integrations.
CUSTOM_COMPONENT_PREFIX = "custom_components."
