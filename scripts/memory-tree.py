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


# ── Screen Identity (structural, ignores dynamic text) ────────────────

def _structural_signature(xml_path):
    """
    Generate a screen fingerprint based on STRUCTURE, not content.
    Uses: resource-ids, class names, element hierarchy.
    Ignores: text content (prices, timestamps change every second).
    """
    import hashlib
    tree = ET.parse(xml_path)
    root = tree.getroot()
    sig_parts = []
    for node in root.iter("node"):
        rid = node.get("resource-id", "")
        cls = node.get("class", "")
        clickable = node.get("clickable", "false")
        desc = node.get("content-desc", "")
        # Use resource-id (most stable), class, clickable, content-desc
        # Skip text entirely — it contains dynamic values
        if rid or (clickable == "true") or desc:
            sig_parts.append(f"{rid}|{cls}|{clickable}|{desc}")
    return hashlib.md5("::".join(sorted(sig_parts)).encode()).hexdigest()[:16]


def _extract_screen_elements(xml_path):
    """Extract all meaningful elements from a UI dump XML."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    elements = {}
    for node in root.iter("node"):
        text = node.get("text", "")
        desc = node.get("content-desc", "")
        rid = node.get("resource-id", "")
        bounds_str = node.get("bounds", "")
        clickable = node.get("clickable", "false")

        label = text or desc or (rid.split("/")[-1] if rid else "")
        if not label:
            continue
        if clickable != "true" and not text and not desc:
            continue

        bounds = parse_bounds_str(bounds_str)
        if not bounds:
            continue

        # Name key: prefer resource-id (stable), fallback to desc, then text
        if rid:
            ename = rid.split("/")[-1] if "/" in rid else rid
        elif desc:
            ename = desc
        else:
            ename = text

        ename = re.sub(r"[^a-zA-Z0-9_]", "_", ename.lower().strip())[:40]
        if not ename or ename == "_":
            continue

        elements[ename] = {
            "resource_id": rid,
            "text": text,
            "content_desc": desc,
            "clickable": clickable == "true",
            "bounds": bounds,
        }
    return elements


def _detect_app_package(xml_path):
    """Detect the foreground app package from UI dump."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for node in root.iter("node"):
        pkg = node.get("package", "")
        if pkg and pkg != "com.android.systemui":
            return pkg
    return ""


def _find_screen_by_signature(app_data, signature):
    """Find an existing screen in memory that matches this signature."""
    for sname, sdata in app_data.get("screens", {}).items():
        if sdata.get("identifiers", {}).get("signature") == signature:
            return sname
    return None


def _auto_name_screen(app_data, elements):
    """Generate a screen name from its distinctive resource-ids."""
    # Use resource-ids first (most stable), then content-descs
    rids = [el.get("resource_id", "").split("/")[-1] for el in elements.values()
            if el.get("resource_id")]
    descs = [el.get("content_desc", "") for el in elements.values()
             if el.get("content_desc")]

    candidates = rids[:3] or descs[:3] or list(elements.keys())[:3]
    name = "_".join(re.sub(r"[^a-zA-Z0-9]", "_", c.lower())[:15] for c in candidates if c)
    if not name:
        name = "screen"

    # Ensure unique
    existing = set(app_data.get("screens", {}).keys())
    if name not in existing:
        return name[:30]
    for i in range(2, 100):
        candidate = f"{name}_{i}"[:30]
        if candidate not in existing:
            return candidate
    return name[:30]


def _merge_screen_elements(screen_data, new_elements, device_key):
    """Merge new elements into an existing screen, updating bounds per device."""
    stored = screen_data.setdefault("elements", {})
    saved = 0
    updated = 0
    for ename, el in new_elements.items():
        if ename not in stored:
            stored[ename] = {
                "resource_id": el["resource_id"],
                "text": el["text"],
                "content_desc": el["content_desc"],
                "clickable": el["clickable"],
                "bounds_by_device": {device_key: el["bounds"]},
            }
            saved += 1
        else:
            stored[ename].setdefault("bounds_by_device", {})[device_key] = el["bounds"]
            updated += 1
    return saved, updated


# ── Tap and Memo (find element, tap, auto-memoize) ────────────────────

def cmd_tap_and_memo(xml_before_path, xml_after_path, query, device_key, index=0):
    """
    Core memoization logic called after a tap.
    - Identifies before/after screens by structural signature
    - Merges elements into existing screens (or creates new ones)
    - Records transitions if screen changed
    """
    data = load_memory()

    before_sig = _structural_signature(xml_before_path)
    after_sig = _structural_signature(xml_after_path)
    screen_changed = before_sig != after_sig

    before_elements = _extract_screen_elements(xml_before_path)
    after_elements = _extract_screen_elements(xml_after_path)

    before_pkg = _detect_app_package(xml_before_path)
    after_pkg = _detect_app_package(xml_after_path)

    # Find app in memory by package
    app_name = ""
    for name, app in data.get("apps", {}).items():
        if app.get("package") in (before_pkg, after_pkg):
            app_name = name
            break

    if not app_name:
        if screen_changed:
            print(f"MEMO_SKIP: app package {before_pkg} not in memory")
        return

    app = data["apps"][app_name]
    screens = app.setdefault("screens", {})

    # Find or create BEFORE screen
    before_screen = _find_screen_by_signature(app, before_sig)
    if not before_screen:
        before_screen = _auto_name_screen(app, before_elements)
    screen_before_data = screens.setdefault(before_screen, {"identifiers": {}, "elements": {}, "transitions": {}})
    screen_before_data["identifiers"]["signature"] = before_sig
    saved_b, updated_b = _merge_screen_elements(screen_before_data, before_elements, device_key)

    if screen_changed:
        # Find or create AFTER screen
        after_screen = _find_screen_by_signature(app, after_sig)
        if not after_screen:
            after_screen = _auto_name_screen(app, after_elements)
        screen_after_data = screens.setdefault(after_screen, {"identifiers": {}, "elements": {}, "transitions": {}})
        screen_after_data["identifiers"]["signature"] = after_sig
        saved_a, updated_a = _merge_screen_elements(screen_after_data, after_elements, device_key)

        # Save transition
        tap_label = re.sub(r"[^a-zA-Z0-9_]", "_", query.lower().strip())[:30]
        screen_before_data.setdefault("transitions", {})[f"tap {tap_label}"] = after_screen

        save_memory(data)
        print(f"MEMO: {app_name}/{before_screen} →tap \"{query}\"→ {app_name}/{after_screen} (before:{saved_b}new+{updated_b}upd, after:{saved_a}new+{updated_a}upd)")
    else:
        save_memory(data)
        if saved_b > 0:
            print(f"MEMO: {saved_b} new elements on {app_name}/{before_screen}")


def cmd_cleanup_screens():
    """
    Deduplicate screens that have the same structural signature.
    Merges elements and transitions from duplicates into the canonical screen.
    """
    data = load_memory()
    total_merged = 0

    for app_name, app in data.get("apps", {}).items():
        screens = app.get("screens", {})
        if not screens:
            continue

        # Group by signature
        sig_groups = {}
        for sname, sdata in screens.items():
            sig = sdata.get("identifiers", {}).get("signature", sname)
            sig_groups.setdefault(sig, []).append(sname)

        # Merge duplicates
        to_delete = []
        for sig, names in sig_groups.items():
            if len(names) <= 1:
                continue

            # Keep the first one (or the shortest name) as canonical
            canonical = min(names, key=len)
            canonical_data = screens[canonical]

            for dup_name in names:
                if dup_name == canonical:
                    continue
                dup_data = screens[dup_name]

                # Merge elements
                for ename, el in dup_data.get("elements", {}).items():
                    canon_els = canonical_data.setdefault("elements", {})
                    if ename not in canon_els:
                        canon_els[ename] = el
                    else:
                        # Merge bounds
                        for dev, bounds in el.get("bounds_by_device", {}).items():
                            canon_els[ename].setdefault("bounds_by_device", {})[dev] = bounds

                # Merge transitions
                for action, target in dup_data.get("transitions", {}).items():
                    canonical_data.setdefault("transitions", {})[action] = target

                # Update any transitions pointing TO this duplicate
                for sn, sd in screens.items():
                    for action, target in sd.get("transitions", {}).items():
                        if target == dup_name:
                            sd["transitions"][action] = canonical

                to_delete.append(dup_name)
                total_merged += 1

        for d in to_delete:
            del screens[d]

    if total_merged > 0:
        save_memory(data)
        print(f"CLEANUP: Merged {total_merged} duplicate screens")
    else:
        print("CLEANUP: No duplicates found")


# ── Find and Tap (element search in UI dump) ──────────────────────────

def cmd_find_and_tap(xml_path, query, index=0):
    """Find an element in a UI dump by text/desc/rid and return tap coordinates."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    query_lower = query.lower()
    index = int(index)
    matches = []

    for node in root.iter("node"):
        text = node.get("text", "")
        desc = node.get("content-desc", "")
        rid = node.get("resource-id", "")
        bounds_str = node.get("bounds", "")

        if not bounds_str:
            continue

        bounds = parse_bounds_str(bounds_str)
        if not bounds:
            continue

        cx, cy = bounds_center(bounds)

        matched = False
        match_on = ""
        # Exact matches
        if text.lower() == query_lower:
            matched, match_on = True, f'text="{text}"'
        elif desc.lower() == query_lower:
            matched, match_on = True, f'content-desc="{desc}"'
        elif rid.lower() == query_lower or rid.lower().endswith("/" + query_lower) or rid.lower().endswith(":id/" + query_lower):
            matched, match_on = True, f'resource-id="{rid}"'
        # Partial matches
        elif query_lower in text.lower():
            matched, match_on = True, f'text="{text}" (partial)'
        elif query_lower in desc.lower():
            matched, match_on = True, f'content-desc="{desc}" (partial)'
        elif query_lower in rid.lower():
            matched, match_on = True, f'resource-id="{rid}" (partial)'

        if matched:
            matches.append((cx, cy, match_on, bounds_str, text or desc or rid))

    if not matches:
        print(f'NOT_FOUND: No element matching "{query}"')
        hints = []
        for node in root.iter("node"):
            t = node.get("text", "")
            d = node.get("content-desc", "")
            r = node.get("resource-id", "")
            c = node.get("clickable", "false")
            label = t or d or (r.split("/")[-1] if r else "")
            if label and c == "true":
                hints.append(label)
        if hints:
            print(f'HINT: Available elements: {", ".join(hints[:15])}')
        sys.exit(1)

    if index >= len(matches):
        index = 0

    cx, cy, match_on, bounds_str, label = matches[index]
    print(f"TAPPED: {cx} {cy} ({match_on}) bounds={bounds_str}")
    if len(matches) > 1:
        print(f"NOTE: {len(matches)} matches found, tapped index {index}")
    print(f"TAP_COORDS: {cx} {cy}")


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
                for correction in screen.get("corrections", []):
                    wrong = correction.get("wrong", "")
                    right = correction.get("right", "")
                    reason = correction.get("reason", "")
                    lines.append(f"    ⚠ AVOID: {wrong}")
                    if right:
                        lines.append(f"      USE INSTEAD: {right}")
                    if reason:
                        lines.append(f"      REASON: {reason}")

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


# ── Corrections (learn from mistakes) ──────────────────────────────────

def cmd_save_correction(app_name, screen_name, correction_json):
    """Save a correction: what went wrong and what to do instead."""
    data = load_memory()
    app = data.get("apps", {}).get(app_name)
    if not app:
        print(f"ERROR: App '{app_name}' not found")
        sys.exit(1)

    screen = app.setdefault("screens", {}).setdefault(
        screen_name, {"identifiers": {}, "elements": {}, "transitions": {}, "corrections": []}
    )
    corrections = screen.setdefault("corrections", [])
    correction = json.loads(correction_json)
    # Deduplicate: don't save the same correction twice
    for existing in corrections:
        if existing.get("wrong") == correction.get("wrong"):
            existing.update(correction)
            save_memory(data)
            print(f"OK: Updated correction on '{app_name}/{screen_name}'")
            return

    corrections.append(correction)
    save_memory(data)
    print(f"OK: Saved correction on '{app_name}/{screen_name}': avoid \"{correction.get('wrong', '')}\"")


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
    elif cmd == "save-correction":
        cmd_save_correction(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "find-and-tap":
        cmd_find_and_tap(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else 0)
    elif cmd == "tap-memo":
        cmd_tap_and_memo(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
                         int(sys.argv[6]) if len(sys.argv) > 6 else 0)
    elif cmd == "cleanup-screens":
        cmd_cleanup_screens()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
