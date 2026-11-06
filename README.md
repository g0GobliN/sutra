## Install

The distribution is `sutra-agent`; the `sutra` name on PyPI belongs to an unrelated package.

```bash
python3 -m venv ~/.sutra-venv
~/.sutra-venv/bin/pip install sutra-agent
~/.sutra-venv/bin/sutra --version        # sutra 0.5.0

~/.sutra-venv/bin/sutra detect           # read-only: what is installed
~/.sutra-venv/bin/sutra install --dry-run
~/.sutra-venv/bin/sutra install          # configure every detected client
~/.sutra-venv/bin/sutra doctor           # prove the installed paths
```

To work from a checkout instead (contributors or unreleased changes):

```bash
git clone https://github.com/g0GobliN/sutra
~/.sutra-venv/bin/pip install ./sutra
```

`sutra install` auto-detects Hermes, Claude Code, OpenCode and Codex. Each change is backed up first and can be removed with `sutra uninstall`; `--dry-run` prints the change and writes nothing. The command ends with a per-client summary (`configured`, `active`, `requires /hooks trust`, `skipped` or `failed`) and the next verification step. Restart the host after installing a plugin or hook. Codex also requires a one-time trust review: run `/hooks` and trust the SUTRA `PostToolUse` entry before it can replace results.

### Adding OpenClaw support

OpenClaw is a Node.js-based adapter. To enable it:

1. Install Node.js 24 via nvm (if not already installed):
   ```bash
   nvm install 24
   nvm use 24
   ```
2. Configure an isolated npm prefix (optional but recommended):
   ```bash
   export npm_config_prefix=$(pwd)/.npm-global   # or any isolated location
   ```
3. Install the OpenClaw client plugin:
   ```bash
   ~/.sutra-venv/bin/sutra install --client openclaw
   ```
   This registers the OpenClaw adapter and backs up any existing configuration.

After installation, restart your host to load the new plugin.