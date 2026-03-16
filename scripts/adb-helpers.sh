#!/bin/bash
# adb-helpers.sh — Helper functions for PhoneDriver Claude Code skill
# Usage:
#   ./scripts/adb-helpers.sh check              — Verify device connection
#   ./scripts/adb-helpers.sh resolution         — Print WIDTHxHEIGHT
#   ./scripts/adb-helpers.sh screenshot [path]  — Capture screenshot
#   ./scripts/adb-helpers.sh uidump [--compact]  — Dump UI hierarchy XML to stdout
#   ./scripts/adb-helpers.sh appinfo <keyword>  — Find app package and main activity
#   ./scripts/adb-helpers.sh batchact "cmd1; cmd2; ..."  — Execute batched actions
#   ./scripts/adb-helpers.sh memory init        — Initialize memory file
#   ./scripts/adb-helpers.sh memory read [path] — Read memory (optionally a subtree)
#   ./scripts/adb-helpers.sh memory write <path> <json> — Write to memory subtree
#   ./scripts/adb-helpers.sh memory prune       — Remove stale entries

set -euo pipefail

# Resolve ADB path — handle alias or PATH lookup
if [ -x "$HOME/Library/Android/sdk/platform-tools/adb" ]; then
    ADB="$HOME/Library/Android/sdk/platform-tools/adb"
elif command -v adb &>/dev/null; then
    ADB="$(command -v adb)"
else
    echo "ERROR: adb not found. Install Android SDK platform-tools or add adb to PATH."
    exit 1
fi
alias adb="$ADB" 2>/dev/null || true

SCREENSHOT_DEFAULT="/tmp/phonedriver_screen.png"
UIDUMP_PATH="/tmp/phonedriver_ui.xml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PD_HOME="$HOME/.claude/phonedriver"

# Memory file: prefer installed location, fall back to repo location
if [ -f "$PD_HOME/memory.json" ]; then
    MEMORY_FILE="$PD_HOME/memory.json"
elif [ -f "$SCRIPT_DIR/../.claude/commands/phonedriver-memory.json" ]; then
    MEMORY_FILE="$SCRIPT_DIR/../.claude/commands/phonedriver-memory.json"
else
    MEMORY_FILE="$PD_HOME/memory.json"
fi
MEMORY_TREE="$SCRIPT_DIR/memory-tree.py"

# ── JSON helpers (jq preferred, python3 fallback) ──────────────────────

json_read() {
    local file="$1"
    local path="${2:-}"

    if [ -z "$path" ]; then
        cat "$file"
        return
    fi

    if command -v jq &>/dev/null; then
        jq -r ".$path // empty" "$file" 2>/dev/null
    elif command -v python3 &>/dev/null; then
        python3 -c "
import json, sys
with open('$file') as f: data = json.load(f)
keys = '$path'.split('.')
for k in keys:
    if isinstance(data, dict) and k in data:
        data = data[k]
    else:
        sys.exit(0)
print(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else data)
"
    else
        echo "ERROR: Neither jq nor python3 available for JSON parsing"
        exit 1
    fi
}

json_write() {
    local file="$1"
    local path="$2"
    local value="$3"
    local tmp_file="${file}.tmp.$$"

    if command -v jq &>/dev/null; then
        jq --argjson val "$value" ".$path = \$val" "$file" > "$tmp_file" && mv "$tmp_file" "$file"
    elif command -v python3 &>/dev/null; then
        python3 -c "
import json, sys
with open('$file') as f: data = json.load(f)
keys = '$path'.split('.')
obj = data
for k in keys[:-1]:
    if k not in obj: obj[k] = {}
    obj = obj[k]
obj[keys[-1]] = json.loads('$value')
with open('$tmp_file', 'w') as f: json.dump(data, f, indent=2)
" && mv "$tmp_file" "$file"
    else
        echo "ERROR: Neither jq nor python3 available for JSON writing"
        exit 1
    fi
}

# ── Device commands ────────────────────────────────────────────────────

check_device() {
    local devices
    devices=$($ADB devices 2>/dev/null | grep -w "device" | grep -v "List")

    if [ -z "$devices" ]; then
        echo "ERROR: No authorized Android device found."
        echo "Troubleshooting:"
        echo "  1. Enable USB debugging: Settings > Developer Options > USB Debugging"
        echo "  2. Connect device via USB"
        echo "  3. Accept the authorization prompt on the phone"
        echo "  4. Run: adb devices"
        exit 1
    fi

    local count
    count=$(echo "$devices" | wc -l | tr -d ' ')
    if [ "$count" -gt 1 ]; then
        echo "WARNING: Multiple devices connected. Using first device."
    fi

    local device_id
    device_id=$(echo "$devices" | head -1 | awk '{print $1}')
    local model
    model=$($ADB -s "$device_id" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    local android_version
    android_version=$($ADB -s "$device_id" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')

    echo "OK: Connected to $model (Android $android_version) [$device_id]"
}

get_resolution() {
    local output
    output=$($ADB shell wm size 2>/dev/null | tr -d '\r')

    if echo "$output" | grep -q "Physical size:"; then
        echo "$output" | grep "Physical size:" | sed 's/Physical size: //'
    elif echo "$output" | grep -q "Override size:"; then
        echo "$output" | grep "Override size:" | sed 's/Override size: //'
    else
        echo "ERROR: Could not detect screen resolution"
        exit 1
    fi
}

capture_screenshot() {
    local output_path="${1:-$SCREENSHOT_DEFAULT}"
    $ADB shell screencap -p /sdcard/phonedriver_screen.png
    $ADB pull /sdcard/phonedriver_screen.png "$output_path" > /dev/null 2>&1
    $ADB shell rm /sdcard/phonedriver_screen.png
    echo "$output_path"
}

# ── UI Dump ────────────────────────────────────────────────────────────

ui_dump() {
    local compact="${1:-}"

    $ADB shell uiautomator dump /sdcard/phonedriver_ui.xml > /dev/null 2>&1
    $ADB pull /sdcard/phonedriver_ui.xml "$UIDUMP_PATH" > /dev/null 2>&1
    $ADB shell rm /sdcard/phonedriver_ui.xml 2>/dev/null

    if [ ! -f "$UIDUMP_PATH" ]; then
        echo "ERROR: UI dump failed. Screen may be locked or in transition."
        exit 1
    fi

    if [ "$compact" = "--compact" ]; then
        # Strip nodes with no text/content-description, keep only useful attributes
        if command -v python3 &>/dev/null; then
            python3 -c "
import xml.etree.ElementTree as ET, sys
tree = ET.parse('$UIDUMP_PATH')
root = tree.getroot()
def keep(node):
    text = node.get('text', '')
    desc = node.get('content-desc', '')
    clickable = node.get('clickable', 'false')
    return bool(text or desc or clickable == 'true')
def compact_node(node):
    attrs = {}
    for key in ['text', 'content-desc', 'resource-id', 'class', 'bounds', 'clickable', 'focused', 'checked', 'enabled']:
        val = node.get(key, '')
        if val and val != 'false':
            attrs[key] = val
    children = [compact_node(c) for c in node if keep(c) or any(keep(gc) for gc in c.iter())]
    return (attrs, children)
def render(item, indent=0):
    attrs, children = item
    if not attrs and not children:
        return ''
    parts = ' '.join(f'{k}=\"{v}\"' for k, v in attrs.items())
    prefix = '  ' * indent
    lines = [f'{prefix}<node {parts}>']
    for c in children:
        r = render(c, indent + 1)
        if r: lines.append(r)
    lines.append(f'{prefix}</node>')
    return '\n'.join(lines)
result = compact_node(root)
print(render(result))
"
        else
            cat "$UIDUMP_PATH"
        fi
    else
        cat "$UIDUMP_PATH"
    fi
}

# ── App Info ───────────────────────────────────────────────────────────

app_info() {
    local keyword="$1"

    if [ -z "$keyword" ]; then
        echo "Usage: $0 appinfo <keyword>"
        exit 1
    fi

    # Search both third-party and system packages
    local packages
    packages=$($ADB shell pm list packages 2>/dev/null | tr -d '\r' | sed 's/package://' | grep -i "$keyword" || true)

    if [ -z "$packages" ]; then
        echo "NOT_FOUND: No packages matching '$keyword'"
        return
    fi

    while IFS= read -r pkg; do
        # Extract main launcher activity
        local activity
        activity=$($ADB shell cmd package resolve-activity --brief -c android.intent.category.LAUNCHER "$pkg" 2>/dev/null | tr -d '\r' | tail -1 || true)

        if [ -n "$activity" ] && [ "$activity" != "No activity found" ]; then
            echo "FOUND: package=$pkg activity=$activity intent=adb shell am start -n $activity"
        else
            echo "FOUND: package=$pkg activity=UNKNOWN"
        fi
    done <<< "$packages"
}

# ── Launch App (memory → appinfo → launch, all in one) ────────────────

launch_app() {
    local keyword="$1"

    if [ -z "$keyword" ]; then
        echo "Usage: $0 launch <app name or keyword>"
        exit 1
    fi

    local keyword_lower
    keyword_lower=$(echo "$keyword" | tr '[:upper:]' '[:lower:]')

    # Step 1: Check memory for exact name or alias match
    if [ -f "$MEMORY_FILE" ] && command -v python3 &>/dev/null; then
        local intent
        intent=$(python3 -c "
import json, sys
with open('$MEMORY_FILE') as f: data = json.load(f)
apps = data.get('apps', {})
kw = '$keyword_lower'
# Check exact app name match
if kw in apps and apps[kw].get('launch_intent'):
    print(apps[kw]['launch_intent'])
    sys.exit(0)
# Check aliases
for name, info in apps.items():
    aliases = [a.lower() for a in info.get('aliases', [])]
    if kw in aliases or kw in name.lower():
        if info.get('launch_intent'):
            print(info['launch_intent'])
            sys.exit(0)
sys.exit(1)
" 2>/dev/null) || true

        if [ -n "$intent" ]; then
            echo "MEMORY_HIT: Launching from memory"
            # The intent stored is like 'adb shell am start -n ...'
            # Replace 'adb' with our resolved $ADB path
            local cmd
            cmd=$(echo "$intent" | sed "s|^adb |$ADB |")
            eval "$cmd" 2>/dev/null
            local rc=$?
            if [ $rc -eq 0 ]; then
                echo "LAUNCHED: $keyword (from memory)"
                return 0
            else
                echo "MEMORY_STALE: Intent failed, discovering fresh..."
            fi
        fi
    fi

    # Step 2: Discover via appinfo
    local found_line
    found_line=$(app_info "$keyword_lower" | grep "^FOUND:.*intent=" | head -1)

    if [ -z "$found_line" ]; then
        echo "NOT_FOUND: Could not find app matching '$keyword'"
        echo "TIP: Try a different keyword or check installed apps with: $0 appinfo <keyword>"
        return 1
    fi

    # Parse the found line
    local pkg activity full_intent
    pkg=$(echo "$found_line" | sed 's/.*package=\([^ ]*\).*/\1/')
    activity=$(echo "$found_line" | sed 's/.*activity=\([^ ]*\).*/\1/')
    full_intent="$ADB shell am start -n $activity"

    # Step 3: Launch
    eval "$full_intent" 2>/dev/null
    local rc=$?
    if [ $rc -eq 0 ]; then
        echo "LAUNCHED: $keyword (discovered: $activity)"

        # Step 4: Auto-save to memory
        if [ -f "$MEMORY_FILE" ] && command -v python3 &>/dev/null; then
            local today
            today=$(date +%Y-%m-%d)
            local mem_key
            mem_key=$(echo "$keyword_lower" | tr ' ' '_')
            local mem_json="{\"package\":\"$pkg\",\"activity\":\"$activity\",\"launch_intent\":\"adb shell am start -n $activity\",\"discovered_at\":\"$today\",\"launch_count\":1,\"aliases\":[\"$keyword_lower\"]}"
            json_write "$MEMORY_FILE" "apps.$mem_key" "$mem_json" 2>/dev/null
            echo "MEMORY_SAVED: $mem_key → $activity"
        fi
        return 0
    else
        echo "LAUNCH_FAILED: Could not start $activity"
        return 1
    fi
}

# ── Tap on Element (UI dump → find → compute center → tap) ────────────

tap_on() {
    # Usage: tap_on <text_or_rid_or_desc> [--index N]
    # Finds element by text, content-desc, or resource-id, computes center, taps it.
    # Returns: TAPPED: <x> <y> <matched_text> <bounds>
    local query="$1"
    local index="${2:-0}"  # 0 = first match

    if [ -z "$query" ]; then
        echo "Usage: $0 tap-on <text|resource-id|content-desc> [--index N]"
        exit 1
    fi

    # Handle --index flag
    if [ "${2:-}" = "--index" ]; then
        index="${3:-0}"
    fi

    # Capture fresh UI dump
    $ADB shell uiautomator dump /sdcard/phonedriver_tap.xml > /dev/null 2>&1
    $ADB pull /sdcard/phonedriver_tap.xml /tmp/phonedriver_tap.xml > /dev/null 2>&1
    $ADB shell rm /sdcard/phonedriver_tap.xml 2>/dev/null

    if [ ! -f /tmp/phonedriver_tap.xml ]; then
        echo "ERROR: UI dump failed"
        return 1
    fi

    # Find element and tap using Python (exact coordinate math)
    python3 -c "
import xml.etree.ElementTree as ET
import re, sys

tree = ET.parse('/tmp/phonedriver_tap.xml')
root = tree.getroot()
query = '''$query'''.lower()
index = $index
matches = []

for node in root.iter('node'):
    text = node.get('text', '')
    desc = node.get('content-desc', '')
    rid = node.get('resource-id', '')
    bounds_str = node.get('bounds', '')

    if not bounds_str:
        continue

    matched = False
    match_on = ''

    # Exact matches first
    if text.lower() == query:
        matched = True
        match_on = f'text=\"{text}\"'
    elif desc.lower() == query:
        matched = True
        match_on = f'content-desc=\"{desc}\"'
    elif rid.lower() == query or rid.lower().endswith('/' + query) or rid.lower().endswith(':id/' + query):
        matched = True
        match_on = f'resource-id=\"{rid}\"'
    # Partial/contains matches
    elif query in text.lower():
        matched = True
        match_on = f'text=\"{text}\" (partial)'
    elif query in desc.lower():
        matched = True
        match_on = f'content-desc=\"{desc}\" (partial)'
    elif query in rid.lower():
        matched = True
        match_on = f'resource-id=\"{rid}\" (partial)'

    if matched:
        m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if m:
            l, t, r, b = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            cx, cy = (l + r) // 2, (t + b) // 2
            matches.append((cx, cy, match_on, bounds_str, text or desc or rid))

if not matches:
    print(f'NOT_FOUND: No element matching \"{query}\"')
    # Show available clickable elements as hints
    hints = []
    for node in root.iter('node'):
        t = node.get('text', '')
        d = node.get('content-desc', '')
        r = node.get('resource-id', '')
        c = node.get('clickable', 'false')
        label = t or d or r.split('/')[-1] if r else ''
        if label and c == 'true':
            hints.append(label)
    if hints:
        print(f'HINT: Available elements: {\", \".join(hints[:15])}')
    sys.exit(1)

if index >= len(matches):
    index = 0

cx, cy, match_on, bounds, label = matches[index]
print(f'TAPPED: {cx} {cy} ({match_on}) bounds={bounds}')
if len(matches) > 1:
    print(f'NOTE: {len(matches)} matches found, tapped index {index}')
" || return 1

    # Extract coordinates from output and tap
    local tap_line
    tap_line=$(python3 -c "
import xml.etree.ElementTree as ET
import re

tree = ET.parse('/tmp/phonedriver_tap.xml')
root = tree.getroot()
query = '''$query'''.lower()
index = $index
matches = []

for node in root.iter('node'):
    text = node.get('text', '')
    desc = node.get('content-desc', '')
    rid = node.get('resource-id', '')
    bounds_str = node.get('bounds', '')
    if not bounds_str:
        continue
    matched = (text.lower() == query or desc.lower() == query or
               rid.lower() == query or rid.lower().endswith('/' + query) or
               rid.lower().endswith(':id/' + query) or
               query in text.lower() or query in desc.lower() or query in rid.lower())
    if matched:
        m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if m:
            l, t, r, b = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            matches.append(((l+r)//2, (t+b)//2))

if matches and index < len(matches):
    print(f'{matches[index][0]} {matches[index][1]}')
")

    if [ -n "$tap_line" ]; then
        local tx ty
        tx=$(echo "$tap_line" | awk '{print $1}')
        ty=$(echo "$tap_line" | awk '{print $2}')
        $ADB shell input tap "$tx" "$ty"
    fi
}

# ── Find Elements (list all matching elements with bounds) ────────────

find_elements() {
    local query="${1:-}"

    # Capture fresh UI dump
    $ADB shell uiautomator dump /sdcard/phonedriver_find.xml > /dev/null 2>&1
    $ADB pull /sdcard/phonedriver_find.xml /tmp/phonedriver_find.xml > /dev/null 2>&1
    $ADB shell rm /sdcard/phonedriver_find.xml 2>/dev/null

    if [ ! -f /tmp/phonedriver_find.xml ]; then
        echo "ERROR: UI dump failed"
        return 1
    fi

    python3 -c "
import xml.etree.ElementTree as ET
import re

tree = ET.parse('/tmp/phonedriver_find.xml')
root = tree.getroot()
query = '''$query'''.lower() if '''$query''' else ''

for node in root.iter('node'):
    text = node.get('text', '')
    desc = node.get('content-desc', '')
    rid = node.get('resource-id', '')
    bounds_str = node.get('bounds', '')
    clickable = node.get('clickable', 'false')
    focused = node.get('focused', 'false')
    checked = node.get('checked', 'false')

    label = text or desc or (rid.split('/')[-1] if rid else '')
    if not label and clickable != 'true':
        continue

    if query and query not in text.lower() and query not in desc.lower() and query not in rid.lower():
        continue

    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if not m:
        continue

    l, t, r, b = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    cx, cy = (l + r) // 2, (t + b) // 2

    attrs = []
    if text: attrs.append(f'text=\"{text}\"')
    if desc: attrs.append(f'desc=\"{desc}\"')
    if rid: attrs.append(f'rid=\"{rid}\"')
    if clickable == 'true': attrs.append('clickable')
    if focused == 'true': attrs.append('focused')
    if checked == 'true': attrs.append('checked')

    print(f'  [{cx},{cy}] {\" \".join(attrs)} bounds={bounds_str}')
"
}

# ── Run ADB command (exposes $ADB for prompt use) ─────────────────────

run_adb() {
    $ADB "$@"
}

# ── Batch Actions ──────────────────────────────────────────────────────

batch_actions() {
    local commands="$1"

    IFS=';' read -ra CMDS <<< "$commands"
    for cmd in "${CMDS[@]}"; do
        cmd=$(echo "$cmd" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [ -z "$cmd" ] && continue

        local action
        action=$(echo "$cmd" | awk '{print $1}')

        case "$action" in
            tap)
                local x y
                x=$(echo "$cmd" | awk '{print $2}')
                y=$(echo "$cmd" | awk '{print $3}')
                # If first arg is not a number, treat as tap-on (element name)
                if echo "$x" | grep -qE '^[0-9]+$'; then
                    $ADB shell input tap "$x" "$y"
                else
                    # Rest of cmd after "tap " is the element query
                    local tap_query
                    tap_query=$(echo "$cmd" | sed 's/^tap //')
                    tap_on "$tap_query"
                fi
                ;;
            swipe)
                local x1 y1 x2 y2 dur
                x1=$(echo "$cmd" | awk '{print $2}')
                y1=$(echo "$cmd" | awk '{print $3}')
                x2=$(echo "$cmd" | awk '{print $4}')
                y2=$(echo "$cmd" | awk '{print $5}')
                dur=$(echo "$cmd" | awk '{print $6}')
                $ADB shell input swipe "$x1" "$y1" "$x2" "$y2" "${dur:-300}"
                ;;
            text)
                local txt
                txt=$(echo "$cmd" | sed "s/^text //" | sed "s/^'//;s/'$//")
                $ADB shell input text "$txt"
                ;;
            key)
                local keycode
                keycode=$(echo "$cmd" | awk '{print $2}')
                $ADB shell input keyevent "$keycode"
                ;;
            launch)
                # launch <app_name_or_activity>
                local launch_target
                launch_target=$(echo "$cmd" | sed "s/^launch //" | sed "s/^'//;s/'$//")
                # If it looks like a component (has /), use am start directly
                if echo "$launch_target" | grep -q "/"; then
                    $ADB shell am start -n "$launch_target" 2>/dev/null
                else
                    launch_app "$launch_target"
                fi
                ;;
            intent)
                # intent <action> — e.g., intent android.settings.WIFI_SETTINGS
                local intent_action
                intent_action=$(echo "$cmd" | awk '{print $2}')
                $ADB shell am start -a "$intent_action" 2>/dev/null
                ;;
            sleep)
                local secs
                secs=$(echo "$cmd" | awk '{print $2}')
                sleep "${secs:-1}"
                ;;
            uidump)
                ui_dump "--compact"
                ;;
            screenshot)
                capture_screenshot
                ;;
            waitfor)
                # waitfor <text_or_rid> [timeout_secs]
                # Polls UI dump until an element with matching text or resource-id appears
                local target timeout_s elapsed
                target=$(echo "$cmd" | sed 's/^waitfor //' | sed 's/ [0-9]*$//')
                timeout_s=$(echo "$cmd" | awk '{print $NF}')
                # If last word isn't a number, default to 10s
                if ! echo "$timeout_s" | grep -qE '^[0-9]+$'; then
                    timeout_s=10
                fi
                elapsed=0
                while [ "$elapsed" -lt "$timeout_s" ]; do
                    # Quick UI dump check (suppress errors)
                    $ADB shell uiautomator dump /sdcard/phonedriver_waitfor.xml > /dev/null 2>&1
                    local found=""
                    found=$($ADB shell "cat /sdcard/phonedriver_waitfor.xml 2>/dev/null | grep -o 'text=\"[^\"]*${target}[^\"]*\"' || grep -o 'resource-id=\"[^\"]*${target}[^\"]*\"' /sdcard/phonedriver_waitfor.xml 2>/dev/null" 2>/dev/null || true)
                    $ADB shell rm /sdcard/phonedriver_waitfor.xml 2>/dev/null
                    if [ -n "$found" ]; then
                        echo "WAITFOR_OK: Found '$target' after ${elapsed}s"
                        break
                    fi
                    sleep 1
                    elapsed=$((elapsed + 1))
                done
                if [ "$elapsed" -ge "$timeout_s" ]; then
                    echo "WAITFOR_TIMEOUT: '$target' not found after ${timeout_s}s, continuing anyway"
                fi
                ;;
            *)
                echo "WARN: Unknown batch action: $action"
                ;;
        esac
    done
}

# ── Device Key ─────────────────────────────────────────────────────────

get_device_key() {
    local model
    model=$($ADB shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    local resolution
    resolution=$(get_resolution)
    python3 "$MEMORY_TREE" device-key "$model" "$resolution"
}

# ── Memory ─────────────────────────────────────────────────────────────

memory_init() {
    if [ -f "$MEMORY_FILE" ]; then
        echo "Memory file already exists: $MEMORY_FILE"
        return
    fi

    mkdir -p "$(dirname "$MEMORY_FILE")"
    cat > "$MEMORY_FILE" << 'INIT_EOF'
{
  "schema_version": 1,
  "device": {
    "model": null,
    "android_version": null,
    "resolution": null,
    "device_id": null,
    "last_seen": null
  },
  "apps": {},
  "navigation_paths": {},
  "settings_paths": {
    "wifi": "android.settings.WIFI_SETTINGS",
    "bluetooth": "android.settings.BLUETOOTH_SETTINGS",
    "display": "android.settings.DISPLAY_SETTINGS",
    "battery": "android.intent.action.POWER_USAGE_SUMMARY",
    "location": "android.settings.LOCATION_SOURCE_SETTINGS",
    "sound": "android.settings.SOUND_SETTINGS",
    "storage": "android.settings.INTERNAL_STORAGE_SETTINGS",
    "apps": "android.settings.APPLICATION_SETTINGS",
    "accounts": "android.settings.SYNC_SETTINGS",
    "accessibility": "android.settings.ACCESSIBILITY_SETTINGS",
    "developer_options": "android.settings.APPLICATION_DEVELOPMENT_SETTINGS",
    "about_phone": "android.settings.DEVICE_INFO_SETTINGS"
  }
}
INIT_EOF
    echo "OK: Memory initialized at $MEMORY_FILE"
}

memory_read() {
    local path="${1:-}"

    if [ ! -f "$MEMORY_FILE" ]; then
        memory_init > /dev/null
    fi

    json_read "$MEMORY_FILE" "$path"
}

memory_write() {
    local path="$1"
    local value="$2"

    if [ ! -f "$MEMORY_FILE" ]; then
        memory_init > /dev/null
    fi

    json_write "$MEMORY_FILE" "$path" "$value"
    echo "OK: Updated memory at .$path"
}

memory_prune() {
    if [ ! -f "$MEMORY_FILE" ]; then
        echo "No memory file to prune"
        return
    fi

    if command -v python3 &>/dev/null; then
        python3 -c "
import json
from datetime import datetime, timedelta
with open('$MEMORY_FILE') as f: data = json.load(f)
cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
apps = data.get('apps', {})
pruned = []
for name, info in list(apps.items()):
    if info.get('launch_count', 0) <= 1 and info.get('discovered_at', '9999') < cutoff:
        del apps[name]
        pruned.append(name)
data['apps'] = apps
with open('$MEMORY_FILE', 'w') as f: json.dump(data, f, indent=2)
print(f'Pruned {len(pruned)} stale entries: {pruned}' if pruned else 'Nothing to prune')
"
    else
        echo "python3 required for pruning"
    fi
}

# ── Main dispatch ──────────────────────────────────────────────────────

case "${1:-help}" in
    check)
        check_device
        ;;
    resolution)
        get_resolution
        ;;
    screenshot)
        capture_screenshot "${2:-}"
        ;;
    uidump)
        ui_dump "${2:-}"
        ;;
    appinfo)
        app_info "${2:-}"
        ;;
    launch)
        launch_app "${2:-}"
        ;;
    tap-on)
        tap_on "${2:-}" "${3:-}" "${4:-}"
        ;;
    find-elements)
        find_elements "${2:-}"
        ;;
    batchact)
        batch_actions "${2:-}"
        ;;
    adb)
        shift
        run_adb "$@"
        ;;
    devicekey)
        get_device_key
        ;;
    memory)
        case "${2:-}" in
            init)   memory_init ;;
            read)   memory_read "${3:-}" ;;
            write)  memory_write "${3:-}" "${4:-}" ;;
            prune)  memory_prune ;;
            migrate)        python3 "$MEMORY_TREE" migrate ;;
            find-task)      python3 "$MEMORY_TREE" find-task "${@:3}" ;;
            get-task)       python3 "$MEMORY_TREE" get-task "${3:-}" "${4:-}" ;;
            save-task)      python3 "$MEMORY_TREE" save-task "${3:-}" "${4:-}" ;;
            compile-task)   python3 "$MEMORY_TREE" compile-task "${3:-}" "${4:-}" ;;
            get-replay)     python3 "$MEMORY_TREE" get-replay "${@:3}" ;;
            save-screen)    python3 "$MEMORY_TREE" save-screen "${3:-}" "${4:-}" "${5:-}" ;;
            save-element)   python3 "$MEMORY_TREE" save-element "${3:-}" "${4:-}" "${5:-}" "${6:-}" "${7:-}" ;;
            save-element-full) python3 "$MEMORY_TREE" save-element-full "${3:-}" "${4:-}" "${5:-}" "${6:-}" ;;
            save-transition) python3 "$MEMORY_TREE" save-transition "${3:-}" "${4:-}" "${5:-}" "${6:-}" ;;
            identify-screen)
                # Capture UI dump then identify
                ui_dump "--compact" > /dev/null 2>&1
                python3 "$MEMORY_TREE" identify-screen "${3:-}" "$UIDUMP_PATH"
                ;;
            list-skills)    python3 "$MEMORY_TREE" list-skills "${3:-}" ;;
            save-correction) python3 "$MEMORY_TREE" save-correction "${3:-}" "${4:-}" "${5:-}" ;;
            save-device)
                local model res ver did
                model=$($ADB shell getprop ro.product.model 2>/dev/null | tr -d '\r')
                res=$(get_resolution)
                ver=$($ADB shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')
                did=$($ADB devices 2>/dev/null | grep -w "device" | grep -v "List" | head -1 | awk '{print $1}')
                python3 "$MEMORY_TREE" save-device "$model" "$res" "$ver" "$did"
                ;;
            *)      echo "Usage: $0 memory {init|read|write|prune|migrate|find-task|get-task|save-task|compile-task|get-replay|save-screen|save-element|save-element-full|save-transition|identify-screen|save-device}" ;;
        esac
        ;;
    help|*)
        echo "Usage: $0 {check|resolution|screenshot|uidump|appinfo|launch|batchact|adb|devicekey|memory} [args]"
        echo ""
        echo "Commands:"
        echo "  check                    Verify ADB connection and print device info"
        echo "  resolution               Print device resolution (WIDTHxHEIGHT)"
        echo "  screenshot [path]        Capture screenshot (default: $SCREENSHOT_DEFAULT)"
        echo "  uidump [--compact]       Dump UI hierarchy XML to stdout"
        echo "  appinfo <keyword>        Find app package and main activity"
        echo "  launch <app name>        Launch app (checks memory, discovers if needed, saves to memory)"
        echo "  batchact \"cmd1; cmd2\"    Execute batched actions (tap/swipe/text/key/sleep/uidump/screenshot)"
        echo "  adb <args>               Run raw adb command (uses resolved ADB path)"
        echo "  devicekey                Print device fingerprint (model__resolution)"
        echo ""
        echo "Memory (tree) commands:"
        echo "  memory find-task <desc>         Fuzzy match task, extract params"
        echo "  memory get-task <id> <dev>      Get task details + compiled commands"
        echo "  memory get-replay <id> <dev> [p=v ...]  Get ready-to-run batchact string"
        echo "  memory save-task <id> <json>    Save task recipe"
        echo "  memory compile-task <id> <dev>  Compile task steps → batchact string"
        echo "  memory save-screen <app> <scr> <json>   Save screen definition"
        echo "  memory save-element <app> <scr> <el> <dev> <bounds>  Save element bounds"
        echo "  memory save-element-full <app> <scr> <el> <json>     Save full element"
        echo "  memory save-transition <app> <scr> <act> <target>    Save transition"
        echo "  memory identify-screen <app>    Identify current screen from UI dump"
        echo "  memory save-device              Save current device info"
        echo "  memory migrate                  Migrate v1 → v2 schema"
        echo "  memory read [path]              Read memory"
        echo "  memory write <path> <json>      Write to memory"
        echo "  memory prune                    Remove stale entries"
        exit 0
        ;;
esac
