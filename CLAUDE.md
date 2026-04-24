# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PhoneDriver is a Claude Code plugin that automates Android devices. It drives the phone through the `android` CLI (which returns structured JSON for UI state) and `adb shell input` (for taps/type/swipes).

Recipes are semantic: they store selectors (`rid:`, `text:`, `desc:`), not pixel coordinates. Each replay re-resolves selectors against the live layout — so the same recipe works across devices and minor UI changes that preserve resource IDs.

## Installation

**As a plugin:**
```bash
/plugin install phone-driver
```

**Standalone (curl):**
```bash
curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
```

## Usage

```bash
/phone-driver "open Settings and search for battery"
/phone-driver "open Calculator and compute 6 times 9"
```

## Prerequisites

- `adb` on PATH
- `android` CLI on PATH (Google Android command-line tool)
- Python 3 (invoked as `python` or `python3`)
- Android device with USB debugging enabled

## Architecture

```
phone-driver/
├── .claude-plugin/plugin.json       ← Plugin manifest
├── skills/phone-driver/SKILL.md     ← Agent prompt (what the LLM reads)
├── scripts/
│   ├── pd                           ← Bash wrapper → driver.py
│   └── driver.py                    ← All logic
├── memory-seed.json                 ← App table template (copied on first run)
└── install.sh                       ← Standalone installer
```

### Path resolution

The `pd()` shell function in SKILL.md finds scripts via `${CLAUDE_PLUGIN_ROOT}` (plugin install) or `$HOME/.claude/phonedriver` (standalone install):

```bash
pd() { /bin/bash "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/phonedriver}/scripts/pd" "$@"; }
```

### Memory

- `memory-seed.json` — template, ships with repo. Known app packages (chrome, settings, …).
- `~/.claude/phonedriver/memory.json` — user memory. Auto-seeded on first run, never overwritten by updates.

Schema:
```json
{
  "schema_version": 3,
  "apps": {
    "<name>": {"package": "<pkg>", "activity": "<optional>"}
  },
  "recipes": {
    "<name>": {
      "description": "...",
      "params": ["query"],
      "steps": [{"op": "launch", "app": "settings"}, ...]
    }
  }
}
```

### Device selection

- Single device: `pd check` auto-locks it for the session.
- Multiple devices: `pd check` prints `MULTIPLE_DEVICES:`; agent asks user, then `pd select <serial>`.
- Lock is stored at `~/.claude/phonedriver/device.lock`. `pd release` clears.
- `driver.py` sets `ANDROID_SERIAL` env when spawning `adb` or `android` — both honor it.

### Selectors

- `rid:<id>` — resource-id; matches short name, fully-qualified, or substring
- `text:<str>` — element text, case-insensitive substring
- `desc:<str>` — contentDescription, case-insensitive substring
- bare — exact match on all three, else substring text/desc, else rid match

### Recipe ops

| op | fields | notes |
|----|--------|-------|
| `launch` | `app` | uses `apps` table; falls back to package name if dotted |
| `tap` | `selector` | re-resolves selector live each run |
| `type` | `value` | spaces → `%s` automatically |
| `key` | `value` | any KEYCODE_* |
| `wait` | `selector`, `timeout?`, `gone?` | polls layout until match/disappear |
| `swipe` | `x1`,`y1`,`x2`,`y2`,`ms?` | coordinates are absolute — use sparingly |
| `sleep` | `seconds` | prefer `wait` |
| `back` / `home` / `enter` | — | key shortcuts |

All string fields support `{param}` interpolation from `pd run` args.

### Visual fallback

When `pd layout` is empty (WebView, game, canvas) or the target has no text/rid/desc:

```bash
pd annotate                          # writes annotated PNG, prints path
# Read the PNG, pick a numbered region N, then:
pd tap-visual "#N"                   # capture → resolve → tap in one call
```

`android screen resolve --screenshot <png> --string "tap #3"` substitutes `#3` with coords, emits `tap 540 880`. `pd resolve` returns the raw substituted string; `pd tap-visual` extracts coords and taps.

### Key operations

| Command | Purpose |
|---------|---------|
| `pd check` | Device health + auto-lock |
| `pd layout [--filter=<sel>]` | Compact JSON |
| `pd tap <sel>` | Live resolve + tap |
| `pd run <name> k=v...` | Replay recipe |
| `pd save-recipe <name> <json>` | Save recipe |
| `pd save-app <name> <pkg> [activity]` | Teach a new app |
