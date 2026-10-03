# GitHub Issue Reporter

Catches errors from your *other* custom integrations and automatically
files them as GitHub issues in a repo you choose.

**Use a private repo:** reports include unredacted exception messages and
tracebacks (LAN IPs, URLs with keys, webhook IDs). The integration warns
at startup if a target repo is public.

Built for the case where you maintain a handful of personal custom
integrations and don't want to be hunting through `home-assistant.log`
every time one breaks at 2am.

## What it does

- Attaches a `logging.Handler` to the root logger on startup.
- Watches for `ERROR` / `CRITICAL` records with a traceback
  (`_LOGGER.exception(...)` or `_LOGGER.error(..., exc_info=True)`).
- Pulls the integration domain out of the logger name
  (`custom_components.<name>.*`) and maps it to a GitHub repo from your
  config.
- Dedupes against open issues by an `[auto] <ErrorType>: <message>` title
  fingerprint. Match → adds a recurrence comment. No match → opens a new
  issue.
- Rate-limits to at most one GitHub API call per `(repo, fingerprint)`
  every 5 minutes.

It explicitly ignores errors from itself, so no feedback loops.

## Quick config

```yaml
gh_issue_reporter:
  token: !secret github_issue_token
  integrations:
    helldivers2:
      repo: tyler919/ha-error-reports   # private
    lighting:
      repo: tyler919/ha-error-reports   # private
  # default_repo routes errors from EVERY other custom integration
  # (including third-party HACS ones) into the tracker. Leave unset.
```

See the [README](https://github.com/tyler919/ha-gh-issue-reporter#readme)
for setup, PAT instructions, testing, and troubleshooting.
