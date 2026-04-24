# Phone Driver

Claude Code plugin for automating Android devices. Describe a task in natural language; Claude drives your phone via the `android` CLI (structured layout JSON) and `adb shell input`. New tasks become reusable recipes — the second run is one command.

## Features

- **Structured UI access** — `android layout` returns JSON with resource IDs, text, content-desc, interactions, and pre-computed element centers. No XML parsing, no heuristics.
- **Device-agnostic recipes** — Recipes store semantic selectors (`rid:search_src_text`, `text:Wi-Fi`), resolved live against the current layout. The same recipe works across devices and UI updates that preserve resource IDs.
- **Visual fallback** — For WebViews and canvas apps, `android screen capture --annotate` + `screen resolve "#N"` gets coordinates from numbered bounding boxes.
- **Tiny surface** — One Python file, one bash wrapper. Memory is a single JSON file you can hand-edit.
- **Safety built-in** — Refuses destructive actions unless explicitly requested.

## Install

### As a Claude Code plugin
```bash
/plugin marketplace add https://github.com/mohitsoni48/phone-driver.git
/plugin install phone-driver@phone-driver-marketplace
```

### Standalone (curl)
```bash
curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
```

### From source
```bash
git clone https://github.com/mohitsoni48/phone-driver.git
cd phone-driver && ./install.sh
```

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- ADB on PATH
- **`android` CLI** on PATH ([Android CLI](https://developer.android.com/studio/command-line))
- Python 3
- Android device with USB debugging enabled

### Connect your device
1. Enable USB debugging (Settings → Developer Options)
2. Connect via USB
3. `adb devices` should show your device as `device`

## Usage

```
/phone-driver "open Settings and search for battery"
/phone-driver "open Calculator and compute 6 times 9"
/phone-driver "open Chrome and search for weather"
```

First time: Claude discovers screens via `pd layout`, taps by `rid:`/`text:`/`desc:` selectors, and saves a reusable recipe.

Next time: `pd run <recipe> query=...` — one call.

## CLI (for direct use)

```bash
pd check                          # auto-lock a device, show recipes + apps
pd layout [--filter=<sel>]        # compact JSON of UI elements
pd tap "rid:search_src_text"      # tap by selector
pd tap "text:Wi-Fi"
pd tap "desc:Back"
pd tap "Settings"                 # bare: tries rid → text → desc
pd type "hello"                   # adb input text (spaces auto-escaped)
pd key KEYCODE_ENTER
pd back / home / enter
pd swipe 540 1600 540 400 400
pd wait "rid:eq" 10               # poll until selector appears
pd wait "Loading" 5 --gone        # poll until it disappears
pd launch settings                # launch known app
pd save-app myapp com.example.app [activity]
pd annotate                       # annotated screenshot (numbered boxes)
pd tap-visual "#34"               # capture → resolve → tap
pd save-recipe <name> '{"steps":[...]}'
pd run <name> key=value ...
pd recipes [-v]                   # list recipes
```

### Selectors
- `rid:<resource_id>` — matches short name (`eq`), fully qualified, or substring
- `text:<str>` — substring, case-insensitive
- `desc:<str>` — substring, case-insensitive
- bare — tries exact match on all three, then substring

### Recipe ops
| op | fields |
|----|--------|
| `launch` | `app` |
| `tap` | `selector` |
| `type` | `value` |
| `key` | `value` (keycode) |
| `wait` | `selector`, `timeout?`, `gone?` |
| `swipe` | `x1`, `y1`, `x2`, `y2`, `ms?` |
| `sleep` | `seconds` |
| `back` / `home` / `enter` | — |

All string values support `{param}` interpolation from `pd run` args.

## Architecture

```
phone-driver/
├── .claude-plugin/plugin.json       ← Plugin manifest
├── skills/phone-driver/SKILL.md     ← Agent prompt
├── scripts/
│   ├── pd                           ← Bash wrapper
│   └── driver.py                    ← All logic (~500 lines)
├── memory-seed.json                 ← App table template
└── install.sh                       ← Standalone installer
```

Memory lives at `~/.claude/phonedriver/memory.json` (auto-seeded from `memory-seed.json` on first run). Never overwritten by updates.

## Uninstall

```bash
rm -rf ~/.claude/phonedriver
# and remove the plugin via /plugin
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
