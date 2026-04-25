# Install

## Prerequisites

- Python 3.9+ (preinstalled on macOS and most Linux distros)
- Claude Code (the CLI) with at least one prior session recorded under `~/.claude/projects/`

No `pip install`, no virtualenv. The tool is a single file.

## Option A — Automated

```bash
./cwatts install claude
```

This:

1. Writes a statusLine entry into `~/.claude/settings.json` pointing at `claudewatts.py statusline`.
2. Backs up any existing `settings.json` to `settings.json.backup-<timestamp>`.
3. Prints the resulting config so you can inspect it.

Restart Claude Code — the meter appears in your status line.

## Option B — Manual statusLine setup

Add this to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /ABSOLUTE/PATH/TO/claudewatts.py statusline"
  }
}
```

Claude Code passes a JSON payload with `transcript_path` and `cwd` to the command on stdin. The tool reads both automatically.

## Option C — Shell alias only

If you just want the CLI without the statusLine:

```bash
alias tu='/ABSOLUTE/PATH/TO/cwatts'
# Then:
tu report --repo .
tu json
```

Or symlink `cwatts` into your PATH:

```bash
sudo ln -s /ABSOLUTE/PATH/TO/cwatts /usr/local/bin/cwatts
```

## Uninstall

Remove the `statusLine` key from `~/.claude/settings.json`, or restore the backup the installer made:

```bash
ls ~/.claude/settings.json.backup-*
# pick the one you want
cp ~/.claude/settings.json.backup-<timestamp> ~/.claude/settings.json
```

Delete the project folder — there is no other state.

## Configuration

All knobs are environment variables. Set them in your shell rc file, or pass them via an `env` wrapper in the statusLine command:

```json
{
  "statusLine": {
    "type": "command",
    "command": "env CPM_WH_OUTPUT=0.002 python3 /path/to/claudewatts.py statusline"
  }
}
```

| Variable                  | Default     | What it does                                      |
|---------------------------|-------------|---------------------------------------------------|
| `CPM_WH_INPUT`            | `0.0003`    | Wh per fresh input token                          |
| `CPM_WH_OUTPUT`           | `0.0015`    | Wh per output token                               |
| `CPM_WH_CACHE_READ`       | `0.00003`   | Wh per cache-read token                           |
| `CPM_WH_CACHE_CREATE`     | `0.0003`    | Wh per cache-create token                         |
| `CPM_ACTIVE_MINUTES`      | `10`        | How recent a message must be to count as "active" |
| `CLAUDE_PROJECTS_DIR`     | `~/.claude/projects` | Where Claude Code stores transcripts      |

## Performance notes

The tool does a full rescan of `~/.claude/projects/` on every invocation. On a machine with hundreds of sessions this takes 100-500ms. For a statusLine that updates continuously this is noticeable. If you have thousands of transcripts and want the statusLine to stay instant, either:

- Narrow the scan with `CLAUDE_PROJECTS_DIR=/path/to/subfolder`, or
- Run the CLI on-demand rather than as a statusLine.

An mtime-keyed aggregate cache is a reasonable future addition.
