---
name: phone-driver
description: Control an Android device. Launches apps, taps elements by resource-id/text/content-desc, types text, scrolls, with a memory of reusable recipes. Built on the `android` CLI (structured layout JSON) + `adb shell input`.
argument-hint: <task description, e.g. "open Settings and search for battery">
allowed-tools: Bash, Read
---

# Phone Driver

Automate an Android device. You have a **recipe memory**: reusable task macros keyed by natural-language pattern. Reuse when you can, learn new recipes when you can't.

**RUN AUTONOMOUSLY.** Don't ask "should I proceed?" — just execute. Surface to the user only: final results, blocking questions (e.g. multiple devices, payment confirmation), or unrecoverable errors.

Task: $ARGUMENTS

## Rules

1. **All commands go through `pd`** (defined in Phase 0). Never call `adb` or `android` directly.
2. **Tap by selector, not coordinates.** Use `pd tap "<selector>"`. Selectors: `rid:<resource_id>`, `text:<substring>`, `desc:<substring>`, or a bare string (tries all three).
3. **Launch by name.** `pd launch <app>`. Use `pd save-app <name> <package>` to teach new apps.
4. **Read recipes first.** Reuse any that match the task or a prefix of it.
5. **Save every new multi-step task** as a recipe so next time is one command.
6. **Minimize tool calls.** Chain with `&&`. Target ≤1 Bash call per cycle.

## Phase 0: setup

Run this first:

```bash
pd() { /bin/bash "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/phonedriver}/scripts/pd" "$@"; } && pd check && pd recipes && pd apps
```

- `NO_DEVICE:` → tell user to plug in a device, stop.
- `MULTIPLE_DEVICES:` → ask user which serial, then `pd select <serial>`.
- Otherwise a device is auto-locked for the session.

If `pd: not found`, tell user to install:
```
curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
```

## Phase 1: plan

Decompose the task. For each sub-step, decide:
- **Exact recipe match?** → `pd run <name> key=value ...`
- **Prefix match?** → run recipe, continue from its end state
- **No match?** → discover via `pd layout`, then save a new recipe when done

## Phase 2: execute

### Inspect the screen
```bash
pd layout                          # full JSON of visible UI elements (compact)
pd layout --filter=rid:eq          # only elements matching this selector (keeps context tight)
pd layout --filter=text:settings
```

Each element shows `text`, `rid` (resourceId), `desc` (contentDesc), `center`, `bounds`, `i` (interactions), `s` (state).

### Interact
```bash
pd launch settings                     # launch known app by name
pd tap "rid:search_src_text"           # tap by resource-id
pd tap "text:Wi-Fi"                    # tap by text substring
pd tap "desc:Back"                     # tap by content-description
pd tap "Battery"                       # bare: tries rid→text→desc
pd type "hello world"                  # adb input text (spaces auto-escaped)
pd key KEYCODE_ENTER                   # any adb keycode
pd back                                # shortcut
pd home                                # shortcut
pd enter                               # shortcut
pd swipe 540 1600 540 400 400          # swipe up (scroll down), 400ms
pd wait "rid:search_src_text" 10       # poll layout up to 10s until selector matches
pd wait "Loading" 5 --gone             # wait until selector disappears
```

### Recipe replay
```bash
pd run settings_search query=battery
```

### Visual fallback (WebViews, games, canvas)
Only when `pd layout` is empty or the element has no text/rid/desc:
```bash
pd annotate                            # saves annotated PNG, prints path
# Read the PNG, pick a numbered region, then:
pd tap-visual "#34"                    # capture+resolve+tap in one call
# or manually:
pd resolve /path/annot.png "tap #34"   # prints "tap X Y" — pipe to adb if wanted
```

### Save a new app
```bash
pd save-app whatsapp com.whatsapp
pd save-app myapp com.example.myapp com.example.myapp.MainActivity   # with activity
```

## Phase 3: save the recipe

After completing a new multi-step task, save it. Use `{param}` placeholders for anything the user supplied (search queries, names, counts):

```bash
pd save-recipe search_settings '{
  "description": "Search Settings for {query}",
  "params": ["query"],
  "steps": [
    {"op": "launch", "app": "settings"},
    {"op": "wait", "selector": "rid:animated_hint_layout", "timeout": 10},
    {"op": "tap",  "selector": "rid:animated_hint_layout"},
    {"op": "wait", "selector": "rid:search_src_text", "timeout": 5},
    {"op": "type", "value": "{query}"},
    {"op": "enter"}
  ]
}'
```

### Step ops
| op | fields | purpose |
|----|--------|---------|
| `launch` | `app` | launch known app |
| `tap` | `selector` | resolve selector live, tap center |
| `type` | `value` | adb input text |
| `key` | `value` | adb input keyevent (any KEYCODE_*) |
| `wait` | `selector`, `timeout`?, `gone`? | poll until selector appears/disappears |
| `swipe` | `x1,y1,x2,y2,ms`? | absolute swipe |
| `sleep` | `seconds` | avoid if possible — prefer `wait` |
| `back`/`home`/`enter` | — | key shortcuts |

String fields support `{param}` interpolation from `pd run` args.

### Naming
- `recipe`: snake_case verb phrase. `search_settings`, `play_song_on_youtube`, `send_sms`.
- Prefer `rid:` selectors over `text:` — more stable across locales.

## Phase 4: report

```
Task: [summary]
Mode: [replay | partial-replay+learn | learn]
Recipes used: [list]
Recipes saved: [list]
```

## Safety

- **Never** tap payment confirm buttons without explicit user OK.
- **Never** send messages or make calls unless that's the task.
- On destructive confirmation dialogs: stop, surface to user.

## Command reference

| Command | Purpose |
|---------|---------|
| `pd check` | Device health + auto-lock |
| `pd select <serial>` | Lock a specific device |
| `pd release` | Release device lock |
| `pd info` | Device fingerprint (serial, model, API, resolution) |
| `pd layout [--filter=<sel>]` | Compact JSON of UI |
| `pd tap <sel>` | Resolve selector and tap center |
| `pd type <text>` | adb input text |
| `pd key <KEYCODE>` | adb input keyevent |
| `pd swipe x1 y1 x2 y2 [ms]` | adb input swipe |
| `pd back` / `home` / `enter` | Key shortcuts |
| `pd wait <sel> [sec] [--gone]` | Poll layout |
| `pd launch <app>` | Launch known app |
| `pd screenshot [path]` | Save PNG |
| `pd annotate [path]` | Annotated PNG (numbered boxes) |
| `pd resolve <png> <query>` | Substitute `#N` → coords |
| `pd tap-visual <query>` | annotate → resolve → tap |
| `pd run <recipe> [k=v...]` | Replay a recipe |
| `pd save-recipe <name> <json>` | Save a recipe |
| `pd recipes [-v]` | List recipes |
| `pd recipe-get <name>` | Show one recipe |
| `pd recipe-del <name>` | Delete a recipe |
| `pd save-app <name> <package> [activity]` | Teach a new app |
| `pd apps` | List known apps |
