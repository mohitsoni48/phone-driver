# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PhoneDriver is a Claude Code plugin that automates Android devices via ADB. It uses a tree-structured memory with task replay — known tasks execute instantly in a single batch call, new tasks are learned and saved for future replay. No local ML model or GPU required.

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
/phone-driver "open Chrome and search for weather"
/phone-driver "open Settings and enable WiFi"
```

## Prerequisites

- ADB installed (macOS: `~/Library/Android/sdk/platform-tools/adb` or on PATH)
- Android device connected via USB with USB debugging enabled
- `python3` available (for memory-tree operations)

## Architecture

### Two Modes

**Replay Mode**: Task found in memory with compiled commands → execute entire sequence as ONE `batchact` call. Zero UI dumps, zero screenshots.

**Learn Mode**: New task → discover screens via UI dump/vision, save elements and transitions to memory tree, compile task recipe on completion for instant replay next time.

### File Structure

```
phone-driver/
├── .claude-plugin/
│   └── plugin.json              ← Plugin manifest
├── commands/
│   └── phone-driver.md          ← The skill prompt
├── scripts/
│   ├── pd                       ← Bash wrapper entry point
│   ├── adb-helpers.sh           ← ADB helpers, batch actions, memory dispatch
│   └── memory-tree.py           ← Tree operations, task matching, compilation
├── memory-seed.json             ← Initial memory with common apps
├── install.sh                   ← Standalone installer
├── README.md
└── LICENSE
```

### Path Resolution

Scripts are found via `${CLAUDE_PLUGIN_ROOT}` (plugin install) or `$HOME/.claude/phonedriver` (standalone install). The `pd()` function in the skill prompt handles both:
```bash
pd() { /bin/bash "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/phonedriver}/scripts/pd" "$@"; }
```

### Memory

- `memory-seed.json` — Ships with repo. Common apps pre-seeded. Template only.
- `~/.claude/phonedriver/memory.json` — User's personal memory. Never overwritten by updates. Auto-seeded from `memory-seed.json` on first run.

### Key Operations

| Command | Purpose |
|---------|---------|
| `memory find-task <desc>` | Fuzzy match task, extract parameters |
| `memory get-replay <id> <dev> [p=v]` | Get ready-to-run batchact with params substituted |
| `memory compile-task <id> <dev>` | Resolve element bounds → generate batchact string |
| `memory save-element-full <app> <scr> <el> <json>` | Save element with device-specific bounds |
| `memory save-transition <app> <scr> <act> <target>` | Record screen transition |
| `launch <app>` | Launch app via intent (memory → appinfo → launch → save) |
| `devicekey` | Get device fingerprint (Model__WIDTHxHEIGHT) |
