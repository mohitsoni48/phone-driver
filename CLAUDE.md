# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PhoneDriver is a Claude Code custom command that automates Android devices via ADB. It uses a tree-structured memory with task replay — known tasks execute instantly in a single batch call, new tasks are learned and saved for future replay. No local ML model or GPU required.

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

### Memory Tree (v2 Schema)

Location: `.claude/commands/phonedriver-memory.json`

```
memory/
├── devices/           ← Device fingerprints (model + resolution)
├── apps/
│   └── <app>/
│       ├── package, activity, intent
│       └── screens/
│           └── <screen>/
│               ├── elements/     ← UI elements with per-device bounds
│               └── transitions/  ← Screen graph edges
├── tasks/             ← Replayable task recipes with compiled commands
└── settings_paths/    ← Direct settings intents
```

**Key design**: Element labels (resource-id, text) are universal across devices. Coordinates (`bounds_by_device`) are keyed per device fingerprint. Same memory works on any device.

### File Structure

- `.claude/commands/phone-driver.md` — The skill prompt (replay/learn modes)
- `.claude/commands/phonedriver-memory.json` — Tree-structured memory (v2)
- `scripts/memory-tree.py` — Python module for tree operations (CRUD, fuzzy task matching, compilation, migration)
- `scripts/adb-helpers.sh` — Shell helpers (ADB path resolution, UI dump, batch actions, memory dispatch)
- `.claude/settings.local.json` — Auto-approve permissions

### Key Operations

| Command | Purpose |
|---------|---------|
| `memory find-task <desc>` | Fuzzy match task, extract parameters |
| `memory get-replay <id> <dev> [p=v]` | Get ready-to-run batchact with params substituted |
| `memory compile-task <id> <dev>` | Resolve element bounds → generate batchact string |
| `memory save-element-full <app> <scr> <el> <json>` | Save element with device-specific bounds |
| `memory save-transition <app> <scr> <act> <target>` | Record screen transition |
| `memory identify-screen <app>` | Match current UI dump against known screens |
| `launch <app>` | Launch app via intent (memory → appinfo → launch → save) |
| `devicekey` | Get device fingerprint (Model__WIDTHxHEIGHT) |
