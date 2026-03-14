#!/usr/bin/env python3
"""
memory-tree.py — Tree-structured memory operations for PhoneDriver.

Subcommands:
  migrate                          Convert v1 memory to v2 schema
  device-key <model> <resolution>  Return canonical device fingerprint
  find-task <description>          Fuzzy match task description, extract params
  get-task <task_id> <device_key>  Return pre-compiled command string
  save-task <task_id> <json>       Write/update a task recipe
  compile-task <task_id> <device_key>  Resolve bounds → generate batchact string
  save-screen <app> <screen> <json>    Write/update a screen definition
  save-element <app> <screen> <element> <device_key> <bounds_json>  Save element with device bounds
  save-transition <app> <screen> <action> <target_screen>  Record screen transition
  identify-screen <app> <xml_path>  Match UI dump against known screens
  get-replay <task_id> <device_key> [param=val ...]  Get ready-to-execute batchact string
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PD_HOME = Path.home() / ".claude" / "phonedriver"
_INSTALLED_MEMORY = PD_HOME / "memory.json"
_REPO_MEMORY = SCRIPT_DIR / ".." / ".claude" / "commands" / "phonedriver-memory.json"
MEMORY_FILE = _INSTALLED_MEMORY if _INSTALLED_MEMORY.exists() else (_REPO_MEMORY if _REPO_MEMORY.exists() else _INSTALLED_MEMORY)


def load_memory():
    if not MEMORY_FILE.exists():
        return {"schema_version": 2, "devices": {}, "apps": {}, "tasks": {}, "settings_paths": {}}
    with open(MEMORY_FILE) as f:
        return json.load(f)


def save_memory(data):
    tmp = str(MEMORY_FILE) + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(MEMORY_FILE))


def make_device_key(model, resolution):
    safe_model = re.sub(r"[^a-zA-Z0-9]", "_", model.strip())
    return f"{safe_model}__{resolution.strip()}"


def parse_bounds_str(bounds_str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if not m:
        return None
    return [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]


def bounds_center(bounds):
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


# ── Migration ──────────────────────────────────────────────────────────

def cmd_migrate():
    data = load_memory()
    if data.get("schema_version", 1) >= 2:
        print("Already at schema v2 or higher")
        return

    backup = str(MEMORY_FILE).replace(".json", "-v1.backup.json")
    with open(backup, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Backed up v1 to {backup}")

    v2 = {
        "schema_version": 2,
        "devices": {},
        "apps": {},
        "tasks": {},
        "settings_paths": data.get("settings_paths", {}),
    }

    old_device = data.get("device", {})
    if old_device and old_device.get("model"):
        key = make_device_key(old_device["model"], old_device.get("resolution", "unknown"))
        v2["devices"][key] = {
            "model": old_device.get("model"),
            "resolution": old_device.get("resolution"),
            "android_version": old_device.get("android_version"),
            "device_id": old_device.get("device_id"),
            "last_seen": old_device.get("last_seen"),
        }

    for name, app in data.get("apps", {}).items():
        v2["apps"][name] = {
            "package": app.get("package"),
            "activity": app.get("activity"),
            "launch_intent": app.get("launch_intent"),
            "discovered_at": app.get("discovered_at"),
            "launch_count": app.get("launch_count", 0),
            "aliases": app.get("aliases", []),
            "screens": {},
        }

    for name, path_info in data.get("navigation_paths", {}).items():
        v2["tasks"][name] = {
            "pattern": name.replace("_", " "),
            "pattern_aliases": [],
            "app": "",
            "parameters": [],
            "steps": [{"action": "raw", "description": s} for s in path_info.get("steps", [])],
            "commands_by_device": {},
            "success_count": path_info.get("success_count", 0),
            "last_used": path_info.get("last_used"),
            "created_at": path_info.get("last_used"),
        }

    save_memory(v2)
    print(f"Migrated to v2: {len(v2['apps'])} apps, {len(v2['tasks'])} tasks, {len(v2['devices'])} devices")


# ── Device Key ─────────────────────────────────────────────────────────

def cmd_device_key(model, resolution):
    print(make_device_key(model, resolution))


# ── Task Operations ────────────────────────────────────────────────────

def normalize_text(text):
    text = text.lower().strip()
    for phrase in ["please ", "can you ", "could you "]:
        text = text.replace(phrase, "")
    text = re.sub(r"\b(the|a|an)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_params_and_score(pattern, text):
    norm_text = normalize_text(text)
    param_names = re.findall(r"\{(\w+)\}", pattern)
    regex_pattern = normalize_text(pattern)
    for p in param_names:
        regex_pattern = regex_pattern.replace("{" + p + "}", r"(.+?)")
    regex_pattern = "^" + regex_pattern + "$"

    m = re.match(regex_pattern, norm_text)
    if m:
        params = {name: m.group(i + 1).strip() for i, name in enumerate(param_names)}
        return 1.0, params

    fixed_words = re.sub(r"\{(\w+)\}", "", normalize_text(pattern)).split()
    fixed_words = [w for w in fixed_words if w]
    if not fixed_words:
        return 0.0, {}

    matched = sum(1 for w in fixed_words if w in norm_text)
    score = matched / len(fixed_words)
    params = {}
    if param_names and score > 0.5:
        remaining = norm_text
        for w in fixed_words:
            remaining = remaining.replace(w, "", 1)
        remaining = remaining.strip()
        if remaining and param_names:
            params[param_names[0]] = remaining

    return score, params


def cmd_find_task(description):
    data = load_memory()
    tasks = data.get("tasks", {})
    if not tasks:
        print("NO_MATCH")
        return

    best_id = None
    best_score = 0.0
    best_params = {}

    for task_id, task in tasks.items():
        all_patterns = [task.get("pattern", "")] + task.get("pattern_aliases", [])
        for pat in all_patterns:
            if not pat:
                continue
            score, params = extract_params_and_score(pat, description)
            bonus = min(task.get("success_count", 0) * 0.02, 0.1)
            total = score + bonus
            if total > best_score:
                best_score = total
                best_id = task_id
                best_params = params

    if best_score < 0.6 or best_id is None:
        print("NO_MATCH")
        return

    result = {
        "task_id": best_id,
        "score": round(best_score, 3),
        "pattern": tasks[best_id].get("pattern"),
        "parameters": best_params,
        "has_steps": len(tasks[best_id].get("steps", [])) > 0,
    }
    print(json.dumps(result))


def cmd_get_task(task_id, device_key):
    data = load_memory()
    task = data.get("tasks", {}).get(task_id)
    if not task:
        print(f"ERROR: Task '{task_id}' not found")
        sys.exit(1)

    compiled = task.get("commands_by_device", {}).get(device_key)
    result = {
        "task_id": task_id,
        "pattern": task.get("pattern"),
        "steps": task.get("steps", []),
        "compiled_commands": compiled,
        "parameters": task.get("parameters", []),
    }
    print(json.dumps(result))


def cmd_save_task(task_id, task_json):
    data = load_memory()
    task_data = json.loads(task_json)
    if "tasks" not in data:
        data["tasks"] = {}
    data["tasks"][task_id] = task_data
    save_memory(data)
    print(f"OK: Saved task '{task_id}'")


def _get_wait_target(data, steps, step_index, app_name):
    """Find a good element text/resource-id to wait for from the NEXT step's screen."""
    next_steps = steps[step_index + 1:] if step_index + 1 < len(steps) else []
    for ns in next_steps:
        if ns.get("action") == "tap":
            scr_name = ns.get("screen", "")
            el_name = ns.get("element", "")
            target_app = ns.get("app", app_name)
            app_data = data.get("apps", {}).get(target_app, {})
            screen = app_data.get("screens", {}).get(scr_name, {})
            element = screen.get("elements", {}).get(el_name, {})
            # Prefer resource_id, then text, then content_desc
            rid = element.get("resource_id", "")
            if rid:
                return rid
            txt = element.get("text", "")
            if txt:
                return txt
            desc = element.get("content_desc", "")
            if desc:
                return desc
        elif ns.get("action") == "type":
            # For type actions, wait for a focused input field
            return "focused=\"true\""
        break
    return None


def cmd_compile_task(task_id, device_key):
    data = load_memory()
    task = data.get("tasks", {}).get(task_id)
    if not task:
        print(f"ERROR: Task '{task_id}' not found")
        sys.exit(1)

    steps = task.get("steps", [])
    app_name = task.get("app", "")
    commands = []

    for i, step in enumerate(steps):
        action = step.get("action")
        if action == "launch":
            step_app = step.get("app", app_name)
            app_data = data.get("apps", {}).get(step_app, {})
            activity = app_data.get("activity", "")
            launch_intent = app_data.get("launch_intent", "")
            # Use batchact DSL: launch <activity> or launch <app_name>
            if activity and "/" in activity:
                commands.append(f"launch {activity}")
            elif step_app:
                commands.append(f"launch {step_app}")
            # Wait for the next step's target element instead of blind sleep
            wait_target = _get_wait_target(data, steps, i, step_app)
            if wait_target:
                commands.append(f"waitfor {wait_target} 10")
            else:
                commands.append("sleep 2")
        elif action == "tap":
            step_app = step.get("app", app_name)
            screen_name = step.get("screen", "")
            element_name = step.get("element", "")
            app_data = data.get("apps", {}).get(step_app, {})
            screens = app_data.get("screens", {})
            screen = screens.get(screen_name, {})
            element = screen.get("elements", {}).get(element_name, {})
            bounds = element.get("bounds_by_device", {}).get(device_key)
            if bounds:
                cx, cy = bounds_center(bounds)
                commands.append(f"tap {cx} {cy}")
                # Wait for next step's target element
                wait_target = _get_wait_target(data, steps, i, step_app)
                if wait_target:
                    commands.append(f"waitfor {wait_target} 8")
                else:
                    commands.append("sleep 1")
            else:
                print(f"INCOMPLETE: No bounds for element '{element_name}' on device '{device_key}'")
                return
        elif action == "type":
            text = step.get("text", "")
            commands.append(f"text '{text}'")
            commands.append("sleep 0.3")
        elif action == "key":
            keycode = step.get("keycode", "")
            kc = keycode if keycode.startswith("KEYCODE_") else f"KEYCODE_{keycode}"
            commands.append(f"key {kc}")
            commands.append("sleep 0.5")
        elif action == "tap_repeat":
            step_app = step.get("app", app_name)
            screen_name = step.get("screen", "")
            element_name = step.get("element", "")
            repeat_raw = step.get("repeat", "1")
            app_data = data.get("apps", {}).get(step_app, {})
            screens = app_data.get("screens", {})
            screen = screens.get(screen_name, {})
            element = screen.get("elements", {}).get(element_name, {})
            bounds = element.get("bounds_by_device", {}).get(device_key)
            if bounds:
                cx, cy = bounds_center(bounds)
                # repeat may be a param placeholder like {qty_minus_1}
                repeat_str = str(repeat_raw)
                if repeat_str.startswith("{") and repeat_str.endswith("}"):
                    # Leave as template for param substitution at replay time
                    # Generate a loop marker that get-replay will expand
                    commands.append(f"tap_repeat {cx} {cy} {repeat_str} 0.5")
                else:
                    count = int(repeat_str)
                    for _ in range(count):
                        commands.append(f"tap {cx} {cy}")
                        commands.append("sleep 0.5")
            else:
                print(f"INCOMPLETE: No bounds for element '{element_name}' on device '{device_key}'")
                return
        elif action == "swipe":
            coords = step.get("coords", "")
            commands.append(f"swipe {coords}")
            commands.append("sleep 1")
        elif action == "raw":
            desc = step.get("description", "")
            commands.append(f"# raw: {desc}")

    if not commands:
        print("EMPTY: No compilable steps")
        return

    compiled = "; ".join(commands)
    data["tasks"][task_id].setdefault("commands_by_device", {})[device_key] = compiled
    save_memory(data)
    print(f"COMPILED: {compiled}")


def cmd_get_replay(task_id, device_key, param_pairs):
    data = load_memory()
    task = data.get("tasks", {}).get(task_id)
    if not task:
        print(f"ERROR: Task '{task_id}' not found")
        sys.exit(1)

    compiled = task.get("commands_by_device", {}).get(device_key)
    if not compiled:
        print("NO_COMPILED")
        return

    params = {}
    for pair in param_pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v

    result = compiled
    # Compute derived params (e.g., qty_minus_1 from qty)
    if "qty" in params:
        params["qty_minus_1"] = str(max(0, int(params["qty"]) - 1))
    for k, v in params.items():
        result = result.replace("{" + k + "}", v)

    # Expand tap_repeat commands: "tap_repeat X Y N delay" → N × "tap X Y; sleep delay"
    expanded_parts = []
    for cmd in result.split("; "):
        cmd = cmd.strip()
        if cmd.startswith("tap_repeat "):
            parts = cmd.split()
            x, y, count_str, delay = parts[1], parts[2], parts[3], parts[4] if len(parts) > 4 else "0.5"
            try:
                count = int(count_str)
            except ValueError:
                count = 0
            for _ in range(count):
                expanded_parts.append(f"tap {x} {y}")
                expanded_parts.append(f"sleep {delay}")
        else:
            expanded_parts.append(cmd)
    result = "; ".join(expanded_parts)

    print(f"REPLAY: {result}")


# ── Screen & Element Operations ────────────────────────────────────────

def cmd_save_screen(app_name, screen_name, screen_json):
    data = load_memory()
    app = data.get("apps", {}).get(app_name)
    if not app:
        print(f"ERROR: App '{app_name}' not found in memory")
        sys.exit(1)

    screen_data = json.loads(screen_json)
    app.setdefault("screens", {})[screen_name] = screen_data
    save_memory(data)
    print(f"OK: Saved screen '{app_name}/{screen_name}'")


def cmd_save_element(app_name, screen_name, element_name, device_key, bounds_json):
    data = load_memory()
    app = data.get("apps", {}).get(app_name)
    if not app:
        print(f"ERROR: App '{app_name}' not found")
        sys.exit(1)

    screens = app.setdefault("screens", {})
    screen = screens.setdefault(screen_name, {"identifiers": {}, "elements": {}, "transitions": {}})
    elements = screen.setdefault("elements", {})

    bounds = json.loads(bounds_json)
    if element_name in elements:
        elements[element_name].setdefault("bounds_by_device", {})[device_key] = bounds
    else:
        elements[element_name] = {"bounds_by_device": {device_key: bounds}}

    save_memory(data)
    print(f"OK: Saved element '{app_name}/{screen_name}/{element_name}' bounds for {device_key}")


def cmd_save_element_full(app_name, screen_name, element_name, element_json):
    data = load_memory()
    app = data.get("apps", {}).get(app_name)
    if not app:
        print(f"ERROR: App '{app_name}' not found")
        sys.exit(1)

    screens = app.setdefault("screens", {})
    screen = screens.setdefault(screen_name, {"identifiers": {}, "elements": {}, "transitions": {}})
    elements = screen.setdefault("elements", {})

    new_elem = json.loads(element_json)
    if element_name in elements:
        existing = elements[element_name]
        existing_bounds = existing.get("bounds_by_device", {})
        new_bounds = new_elem.get("bounds_by_device", {})
        existing_bounds.update(new_bounds)
        new_elem["bounds_by_device"] = existing_bounds
    elements[element_name] = new_elem

    save_memory(data)
    print(f"OK: Saved element '{app_name}/{screen_name}/{element_name}'")


def cmd_save_transition(app_name, screen_name, action, target_screen):
    data = load_memory()
    app = data.get("apps", {}).get(app_name)
    if not app:
        print(f"ERROR: App '{app_name}' not found")
        sys.exit(1)

    screen = app.setdefault("screens", {}).setdefault(
        screen_name, {"identifiers": {}, "elements": {}, "transitions": {}}
    )
    screen.setdefault("transitions", {})[action] = target_screen
    save_memory(data)
    print(f"OK: Transition '{app_name}/{screen_name}' --[{action}]--> '{target_screen}'")


def cmd_identify_screen(app_name, xml_path):
    data = load_memory()
    app = data.get("apps", {}).get(app_name)
    if not app:
        print("UNKNOWN: App not in memory")
        return

    screens = app.get("screens", {})
    if not screens:
        print("UNKNOWN: No screens recorded")
        return

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        print(f"ERROR: Cannot parse XML: {e}")
        return

    dump_resource_ids = set()
    dump_texts = set()
    dump_content_descs = set()
    for node in root.iter("node"):
        rid = node.get("resource-id", "")
        if rid:
            dump_resource_ids.add(rid)
        txt = node.get("text", "")
        if txt:
            dump_texts.add(txt.lower())
        desc = node.get("content-desc", "")
        if desc:
            dump_content_descs.add(desc.lower())

    best_screen = None
    best_score = 0

    for screen_name, screen in screens.items():
        score = 0
        total = 0
        for elem_name, elem in screen.get("elements", {}).items():
            total += 1
            rid = elem.get("resource_id", "")
            if rid and rid in dump_resource_ids:
                score += 3
                continue
            txt = elem.get("text", "")
            if txt and txt.lower() in dump_texts:
                score += 2
                continue
            desc = elem.get("content_desc", "")
            if desc and desc.lower() in dump_content_descs:
                score += 2
                continue

        if total > 0:
            normalized = score / (total * 3)
            if normalized > best_score:
                best_score = normalized
                best_screen = screen_name

    if best_screen and best_score >= 0.4:
        print(f"MATCH: {best_screen} (score={best_score:.2f})")
    else:
        print("UNKNOWN")


# ── List Skills (LLM-readable summary) ─────────────────────────────────

def cmd_list_skills(device_key=None):
    """Output a concise, LLM-readable summary of all known skills."""
    data = load_memory()
    lines = []

    # Apps with launch intents
    lines.append("## Known Apps")
    for name, app in sorted(data.get("apps", {}).items()):
        aliases = ", ".join(app.get("aliases", []))
        has_screens = bool(app.get("screens"))
        screen_count = len(app.get("screens", {}))
        intent = app.get("launch_intent", "")
        status = f"{screen_count} screens mapped" if has_screens else "launch only"
        lines.append(f"- **{name}** ({status}): aliases=[{aliases}]")
        if has_screens:
            for sname, screen in app["screens"].items():
                elements = list(screen.get("elements", {}).keys())
                transitions = screen.get("transitions", {})
                has_bounds = False
                if device_key:
                    has_bounds = any(
                        device_key in el.get("bounds_by_device", {})
                        for el in screen.get("elements", {}).values()
                    )
                bounds_note = " [bounds: this device]" if has_bounds else ""
                lines.append(f"  - screen `{sname}`: elements=[{', '.join(elements)}]{bounds_note}")
                for action, target in transitions.items():
                    lines.append(f"    → {action} → `{target}`")

    # Tasks with replay commands
    lines.append("")
    lines.append("## Known Tasks (replayable skills)")
    tasks = data.get("tasks", {})
    if not tasks:
        lines.append("- (none yet)")
    for tid, task in sorted(tasks.items()):
        pattern = task.get("pattern", tid)
        params = task.get("parameters", [])
        step_count = len(task.get("steps", []))
        has_compiled = bool(task.get("commands_by_device", {}).get(device_key)) if device_key else False
        compiled_note = " ✓ compiled for this device" if has_compiled else ""
        steps_desc = []
        for s in task.get("steps", []):
            action = s.get("action", "?")
            if action == "launch":
                steps_desc.append(f"launch {s.get('app', '?')}")
            elif action == "tap":
                steps_desc.append(f"tap {s.get('element', '?')} on {s.get('screen', '?')}")
            elif action == "type":
                steps_desc.append(f"type {s.get('text', '?')}")
            elif action == "key":
                steps_desc.append(f"press {s.get('keycode', '?')}")
            elif action == "swipe":
                steps_desc.append("swipe")
            else:
                steps_desc.append(action)
        lines.append(f"- **{tid}**: \"{pattern}\" params={params}{compiled_note}")
        lines.append(f"  steps: {' → '.join(steps_desc)}")

    # Settings paths
    settings = data.get("settings_paths", {})
    if settings:
        lines.append("")
        lines.append("## Settings Shortcuts")
        for name, intent in sorted(settings.items()):
            lines.append(f"- {name}: `intent {intent}`")

    print("\n".join(lines))


# ── Save Device ────────────────────────────────────────────────────────

def cmd_save_device(model, resolution, android_version, device_id):
    data = load_memory()
    from datetime import date
    key = make_device_key(model, resolution)
    data.setdefault("devices", {})[key] = {
        "model": model,
        "resolution": resolution,
        "android_version": android_version,
        "device_id": device_id,
        "last_seen": date.today().isoformat(),
    }
    save_memory(data)
    print(f"OK: Device '{key}' saved")


# ── Main Dispatch ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: memory-tree.py <command> [args...]")
        print("Commands: migrate, device-key, find-task, get-task, save-task,")
        print("  compile-task, get-replay, save-screen, save-element,")
        print("  save-element-full, save-transition, identify-screen, save-device")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "migrate":
        cmd_migrate()
    elif cmd == "device-key":
        cmd_device_key(sys.argv[2], sys.argv[3])
    elif cmd == "find-task":
        cmd_find_task(" ".join(sys.argv[2:]))
    elif cmd == "get-task":
        cmd_get_task(sys.argv[2], sys.argv[3])
    elif cmd == "save-task":
        cmd_save_task(sys.argv[2], sys.argv[3])
    elif cmd == "compile-task":
        cmd_compile_task(sys.argv[2], sys.argv[3])
    elif cmd == "get-replay":
        cmd_get_replay(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif cmd == "save-screen":
        cmd_save_screen(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "save-element":
        cmd_save_element(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
    elif cmd == "save-element-full":
        cmd_save_element_full(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "save-transition":
        cmd_save_transition(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "identify-screen":
        cmd_identify_screen(sys.argv[2], sys.argv[3])
    elif cmd == "save-device":
        cmd_save_device(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "list-skills":
        cmd_list_skills(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
