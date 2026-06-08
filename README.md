# gh_issue_reporter

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

A [Home Assistant](https://www.home-assistant.io/) custom integration that
catches errors from your **other** custom integrations and automatically
files them as GitHub issues — one repo per integration.

Built for the case where you maintain a handful of personal custom
integrations and don't want to be hunting through `home-assistant.log`
every time one of them dies.

## What it does

- On HA startup, attaches a `logging.Handler` to the root logger.
- Watches for `ERROR` and `CRITICAL` records that carry exception info
  (i.e. someone called `_LOGGER.exception(...)` or
  `_LOGGER.error(..., exc_info=True)`).
- Pulls the integration domain out of the logger name
  (`custom_components.<name>.*`) and looks it up in your config to find
  the target repo.
- Dedupes against open issues by a title fingerprint
  (`[auto] <ErrorType>: <first 80 chars>`). Existing match → adds a
  recurrence comment. No match → opens a new issue.
- Rate-limits to at most one GitHub API call per `(repo, fingerprint)`
  every 5 minutes.

It never tries to report on errors from itself, so no feedback loop.

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS**.
2. Click the three-dot menu in the top right → **Custom repositories**.
3. Add `https://github.com/tyler919/ha-gh-issue-reporter` as a repository
   of type **Integration**.
4. Find **GitHub Issue Reporter** in the HACS integration list and click
   **Download**.
5. Restart Home Assistant.
6. Add the `gh_issue_reporter:` block to `configuration.yaml` (see
   below), then restart again.

### Manual install (fallback)

1. Copy `custom_components/gh_issue_reporter/` from this repo into your
   Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Configure as below.

## Configuration

Store the GitHub PAT in `secrets.yaml`:

```yaml
# secrets.yaml
github_issue_token: ghp_xxxxxxxxxxxxxxxxxxxx
```

Then add to `configuration.yaml`:

```yaml
gh_issue_reporter:
  token: !secret github_issue_token
  integrations:
    helldivers2:
      repo: tyler919/ha-helldivers2
    lighting:
      repo: tyler919/ha-lighting
  default_repo: tyler919/ha-misc  # optional
```

After a restart, you should see this in your log:

```
custom_components.gh_issue_reporter: watching 2 integration(s), default_repo=tyler919/ha-misc
```

### Reference

| Key | Required | Description |
| --- | --- | --- |
| `token` | yes | GitHub PAT. See **Security / PAT setup** below. |
| `integrations` | no | Map of `<integration name>: { repo: owner/name }`. The integration name is the part after `custom_components.` in the logger. |
| `default_repo` | no | `owner/name` used when the logger doesn't match any entry under `integrations`. Omit to skip unmapped integrations. |

## Security / PAT setup

You need a GitHub Personal Access Token. Two options:

**Fine-grained PAT (preferred).** At
<https://github.com/settings/tokens?type=beta>:

- Resource owner: your account.
- Repository access: **only the repos listed in your config**.
- Repository permissions: **Issues → Read and write**. Nothing else.
- Expiration: pick something reasonable (90d–1y). You'll need to rotate.

**Classic PAT (simpler, broader).** At
<https://github.com/settings/tokens>: create a token with the `repo`
scope. Note that this gives access to all your repos, including private
ones, which is more privilege than this integration needs.

Either way, paste it into `secrets.yaml` under `github_issue_token`.

### How the token is handled

- It's read once at startup from `configuration.yaml` and held only on
  the `GitHubClient` instance.
- It's never logged. `GitHubClient.__repr__` is explicitly
  `<GitHubClient token=***>`.
- It's never written to disk.
- The integration exposes no HA service, sensor, or HTTP endpoint that
  could leak it.

If your token is compromised, revoke it at
<https://github.com/settings/tokens> and the integration will start
logging a one-shot `GitHub authentication failed` warning until you
update `secrets.yaml` and restart.

## Testing it works

Easiest way: pick one of your custom integrations that's already
configured in HA, drop a temporary block somewhere that runs (e.g. in
that integration's `async_setup` or a service handler), and force an
error:

```python
import logging
_LOGGER = logging.getLogger(__name__)

try:
    1 / 0
except ZeroDivisionError:
    _LOGGER.exception("forced test failure")
```

Trigger that code path. Within a few seconds, the configured repo should
have a new `[auto] ZeroDivisionError: division by zero` issue.

- Trigger it again within 5 minutes → nothing happens (rate limit).
- Trigger it more than 5 minutes later → the **existing** issue gains a
  "Recurrence at ..." comment.
- Close the issue, trigger again → a brand new issue is opened.

## Troubleshooting

**Nothing appears in GitHub when I trigger an error.**
Check that the failing logger name starts with `custom_components.<name>`
and that `<name>` matches a key under `integrations:` (or that
`default_repo` is set). Logs from `homeassistant.*`, `aiohttp.*`, or
other library loggers are ignored by design.

**An issue gets created but I want one per occurrence.**
That's intentional — we dedupe and comment instead. If you really want
per-occurrence issues, change `_fingerprint()` in `reporter.py` to
include something unique like a timestamp. You'll burn through GitHub's
secondary rate limits fast.

**`GitHub authentication failed — issue reporting is disabled`** in
the log. The PAT is missing, expired, or lacks Issues write on the
target repo(s). Fix `token` in `secrets.yaml` and restart HA. The
warning is one-shot per HA session to avoid log spam.

**HA boot is slow or network calls are blocking.**
They aren't — the handler captures the record synchronously, but the
GitHub API call is dispatched onto HA's event loop via
`call_soon_threadsafe` + `hass.async_create_task`. The handler returns
to the caller immediately.

## Limitations / known gotchas

- Only records with `exc_info` are reported. A bare
  `_LOGGER.error("oops")` is intentionally skipped.
- Rate-limit state is in-memory; HA restart clears it, so the first
  occurrence after restart will always file/comment even if it fired
  seconds before shutdown.
- GitHub's search API is eventually consistent (~30s). If two errors
  fire near-simultaneously and both predate any open issue, you may end
  up with two issues opened. Subsequent occurrences will then comment on
  whichever the search returns first.
- No options flow yet — config is via `configuration.yaml` only.

## License

[MIT](LICENSE)
