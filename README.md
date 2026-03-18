# Phone Driver

AI-powered Claude Code plugin that automates Android devices. Describe a task in natural language and Claude executes it — learning from each interaction to replay tasks instantly next time.

## Features

- **Zero dependencies** — No Python ML libraries, no GPU, no model downloads. Just ADB + Claude Code.
- **Skill learning** — First run discovers, every run after is instant replay
- **Tree-structured memory** — Learns app screens, element locations, and task recipes
- **Multi-device support** — Same skills work across devices, coordinates adapt per device
- **Natural language** — Describe what you want, Claude figures out how
- **Safety built-in** — Refuses destructive actions unless explicitly requested

## Install

### As a Claude Code plugin

```bash
# Add the marketplace (one-time)
/plugin marketplace add https://github.com/mohitsoni48/phone-driver.git

# Install
/plugin install phone-driver@phone-driver-marketplace
```

### Standalone (curl)

```bash
curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
```

### From source

```bash
git clone https://github.com/mohitsoni48/phone-driver.git
cd phone-driver
./install.sh
```

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- ADB (Android Debug Bridge) on PATH
- Android device with USB debugging enabled
- Python 3 (for memory operations)

### Install ADB

**macOS:**
```bash
brew install android-platform-tools
```

**Linux:**
```bash
sudo apt install adb
```

### Connect your device

1. Enable USB debugging: **Settings > Developer Options > USB Debugging**
2. Connect via USB
3. Verify: `adb devices` (should show your device as `device`)

## Usage

In Claude Code:

```
/phone-driver "open Chrome and search for weather"
/phone-driver "open Settings and enable WiFi"
/phone-driver "open Calculator and compute 123 + 456"
/phone-driver "open YouTube"
```

### How it works

**First time** (Learn Mode):
1. Launches app via intent
2. Discovers screens and elements via UI dump
3. Executes actions (tap, type, swipe)
4. Auto-memoizes every screen and element with bounds
5. Saves task and compiles for instant replay

**Second time** (Replay Mode):
1. Reads skill library, finds matching task
2. Executes entire sequence in ONE batch call
3. No UI dumps, no screenshots, no trial and error

**Partial match**:
If you ask "search for weather in Chrome and click first result" and it only knows "search in Chrome", it replays the known prefix and discovers the rest.

## Architecture

```
phone-driver/
├── .claude-plugin/plugin.json    ← Plugin manifest
├── commands/phone-driver.md      ← The skill prompt
├── scripts/
│   ├── pd                        ← Entry point
│   ├── adb-helpers.sh            ← ADB helpers, batch actions, memory dispatch
│   └── memory-tree.py            ← Tree ops, task matching, compilation
├── memory-seed.json              ← Initial memory with common apps
└── install.sh                    ← Standalone installer
```

### Skill tree

PhoneDriver builds a tree of everything it learns:

```
Apps
├── chrome (package, intent, aliases)
│   └── Screens
│       ├── home → elements: [search_bar, menu_button]
│       └── search_results → elements: [first_result]
│
Tasks (replayable recipes)
├── search_in_chrome: "search for {query} in chrome"
│   compiled: "launch chrome; waitfor search_box 10; scrolltap Search; ..."
│
Settings Shortcuts
├── wifi → android.settings.WIFI_SETTINGS
└── bluetooth → android.settings.BLUETOOTH_SETTINGS
```

- **Element locations** stored per-device (same skill works on any phone)
- **Tasks** are parameterized (`{query}` → reusable with any search term)
- **Memory persists** across updates (install never overwrites learned skills)

### Key commands

| Command | What it does |
|---------|-------------|
| `scrolltap <text>` | Scroll until exact match found, then tap |
| `waitfor <text> <timeout>` | Poll UI until element appears |
| `batchact "<commands>"` | Execute a compiled task sequence |
| `tap-on "<element>"` | Find element by text/desc/rid and tap |
| `find-elements` | List all UI elements on screen |
| `snapshot-screen <app> <name>` | Save all elements to memory |

## Uninstall

```bash
rm ~/.claude/commands/phone-driver.md
rm -rf ~/.claude/phonedriver
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) file.
