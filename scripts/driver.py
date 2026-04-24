#!/usr/bin/env python3
"""
Phone Driver — Android automation built on the `android` CLI + `adb shell input`.

Design:
- `android layout` gives structured JSON (text, resourceId, contentDesc, center, bounds, interactions).
- We use selectors, not pixel coords. `rid:<id>` | `text:<str>` | `desc:<str>` | bare (tries all).
- Memory stores device-agnostic recipes: sequences of semantic steps. Replayed by re-resolving
  selectors against the live layout each time. No per-device bounds caching.
- Visual fallback: `android screen capture --annotate` + `android screen resolve "#N"`.

Memory lives at: $PD_MEMORY_DIR/memory.json  (default: ~/.claude/phonedriver/memory.json)
Seed template: <plugin>/memory-seed.json (copied on first run).
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time, shutil, datetime
from pathlib import Path
from typing import Any

# ---------- paths ----------
SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
MEMORY_DIR = Path(os.environ.get("PD_MEMORY_DIR", os.path.expanduser("~/.claude/phonedriver")))
MEMORY_PATH = MEMORY_DIR / "memory.json"
DEVICE_LOCK = MEMORY_DIR / "device.lock"
SCREENSHOT_DIR = Path(os.environ.get("PD_SCREENSHOT_DIR", MEMORY_DIR / "screens"))
SEED_PATH = PLUGIN_ROOT / "memory-seed.json"

# ---------- shell ----------
def sh(cmd: list[str] | str, check: bool = False, capture: bool = True, timeout: int | None = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    s = env.get("ANDROID_SERIAL") or (DEVICE_LOCK.read_text().strip() if DEVICE_LOCK.exists() else "")
    if s: env["ANDROID_SERIAL"] = s
    if isinstance(cmd, str):
        proc = subprocess.run(cmd, shell=True, capture_output=capture, text=True, timeout=timeout, env=env)
    else:
        proc = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed: {cmd}\nstdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc

def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    sys.exit(code)

# ---------- device selection ----------
def list_devices() -> list[tuple[str, str]]:
    out = sh(["adb", "devices"]).stdout
    rows = []
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            rows.append((parts[0], parts[1]))
    return rows

def locked_device() -> str | None:
    if DEVICE_LOCK.exists():
        s = DEVICE_LOCK.read_text().strip()
        return s or None
    return None

def active_device() -> str | None:
    """Return serial we should target, or None if ambiguous."""
    env = os.environ.get("ANDROID_SERIAL")
    if env: return env
    lock = locked_device()
    if lock: return lock
    devs = list_devices()
    if len(devs) == 1: return devs[0][0]
    return None

def adb_args() -> list[str]:
    return ["adb"]  # ANDROID_SERIAL env (set in sh) targets the right device

# ---------- memory ----------
def load_memory() -> dict:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMORY_PATH.exists():
        if SEED_PATH.exists():
            shutil.copyfile(SEED_PATH, MEMORY_PATH)
        else:
            MEMORY_PATH.write_text(json.dumps({"apps": {}, "recipes": {}}, indent=2))
    return json.loads(MEMORY_PATH.read_text() or "{}")

def save_memory(m: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(m, indent=2))

# ---------- layout ----------
def get_layout() -> list[dict]:
    args = ["android", "layout", "-p"]
    proc = sh(args, timeout=30)
    if proc.returncode != 0:
        # Fallback: raw stderr message
        raise RuntimeError(f"android layout failed: {proc.stderr.strip() or proc.stdout.strip()}")
    txt = proc.stdout.strip()
    if not txt: return []
    try:
        data = json.loads(txt)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"layout JSON parse error: {e}; first 200 chars: {txt[:200]}")
    return data if isinstance(data, list) else []

# ---------- selectors ----------
def parse_selector(sel: str) -> tuple[str, str]:
    """Returns (kind, needle). kind in {rid, text, desc, any}."""
    if sel.startswith("rid:"):  return ("rid", sel[4:])
    if sel.startswith("text:"): return ("text", sel[5:])
    if sel.startswith("desc:"): return ("desc", sel[5:])
    return ("any", sel)

def _match_element(el: dict, kind: str, needle: str) -> bool:
    rid = el.get("resource-id") or el.get("resourceId") or ""
    text = el.get("text") or ""
    desc = el.get("contentDesc") or el.get("content-desc") or ""
    n_lower = needle.lower()
    def m_rid(): return bool(rid) and (needle == rid or rid.endswith("/" + needle) or rid.endswith(":id/" + needle) or needle in rid)
    def m_text(): return n_lower in text.lower() if text else False
    def m_desc(): return n_lower in desc.lower() if desc else False
    if kind == "rid":  return m_rid()
    if kind == "text": return m_text()
    if kind == "desc": return m_desc()
    # any: exact rid match > text equality > desc equality > substring text > substring desc > rid substring
    if rid == needle: return True
    if text.lower() == n_lower: return True
    if desc.lower() == n_lower: return True
    if m_text() or m_desc(): return True
    return m_rid()

def find_elements(elements: list[dict], sel: str) -> list[dict]:
    kind, needle = parse_selector(sel)
    matches = [el for el in elements if _match_element(el, kind, needle)]
    # prefer clickable / not off-screen
    def score(el):
        interactions = el.get("interactions") or []
        s = 0
        if "clickable" in interactions: s += 10
        if "focusable" in interactions: s += 1
        if el.get("off-screen"): s -= 20
        return -s
    matches.sort(key=score)
    return matches

def parse_center(el: dict) -> tuple[int, int] | None:
    c = el.get("center")
    if not c: return None
    m = re.match(r"\[(-?\d+),(-?\d+)\]", c.strip())
    if not m: return None
    return int(m.group(1)), int(m.group(2))

def compact_element(el: dict) -> dict:
    """Slim a layout element to the fields the LLM actually needs."""
    out = {}
    for k_src, k_dst in [("text","text"), ("resource-id","rid"), ("resourceId","rid"),
                          ("contentDesc","desc"), ("content-desc","desc"),
                          ("center","center"), ("bounds","bounds"), ("class","class")]:
        if k_src in el and k_dst not in out and el[k_src] not in ("", None):
            out[k_dst] = el[k_src]
    inters = el.get("interactions") or []
    if inters: out["i"] = ",".join(inters)
    state = el.get("state") or []
    if state: out["s"] = ",".join(state)
    if el.get("off-screen"): out["off"] = True
    return out

# ---------- input ----------
def adb_input(args: list[str]) -> None:
    cmd = adb_args() + ["shell", "input"] + args
    proc = sh(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"adb input failed: {proc.stderr.strip()}")

def do_tap(x: int, y: int) -> None:
    adb_input(["tap", str(x), str(y)])

def do_swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
    adb_input(["swipe", str(x1), str(y1), str(x2), str(y2), str(ms)])

def escape_text(s: str) -> str:
    # adb input text: spaces must be escaped; many shells mangle quotes. Use %s for space.
    return s.replace(" ", "%s")

def do_type(text: str) -> None:
    adb_input(["text", escape_text(text)])

def do_key(keycode: str) -> None:
    adb_input(["keyevent", keycode])

# ---------- tap / wait ----------
def tap_selector(sel: str) -> dict:
    elements = get_layout()
    matches = find_elements(elements, sel)
    if not matches:
        # print a short hint of what's on screen
        names = []
        for el in elements[:30]:
            c = compact_element(el)
            label = c.get("text") or c.get("desc") or c.get("rid") or c.get("class", "?")
            if label: names.append(label[:40])
        raise RuntimeError(f"no element matches {sel!r}\nhint: {', '.join(names)[:400]}")
    el = matches[0]
    pt = parse_center(el)
    if not pt: raise RuntimeError(f"element has no center: {el}")
    do_tap(*pt)
    return {"tapped_xy": pt, "element": compact_element(el)}

def wait_selector(sel: str, timeout: float = 10.0, gone: bool = False) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            elements = get_layout()
        except Exception:
            time.sleep(0.4); continue
        present = bool(find_elements(elements, sel))
        if gone and not present: return True
        if not gone and present: return True
        time.sleep(0.4)
    return False

# ---------- app launch ----------
def known_apps(m: dict) -> dict:
    return m.setdefault("apps", {})

def launch_app(name: str) -> dict:
    m = load_memory()
    apps = known_apps(m)
    entry = apps.get(name.lower())
    if not entry:
        # heuristic: maybe user gave package name directly
        if "." in name:
            entry = {"package": name}
        else:
            raise RuntimeError(f"unknown app {name!r}. Known: {', '.join(sorted(apps))}. "
                               f"Use `pd save-app {name} <package> [activity]`.")
    pkg = entry["package"]
    activity = entry.get("activity")
    if activity:
        comp = activity if activity.startswith(pkg) else f"{pkg}/{activity}"
        cmd = adb_args() + ["shell", "am", "start", "-n", comp]
    else:
        cmd = adb_args() + ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"]
    proc = sh(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"launch failed for {pkg}: {proc.stderr.strip() or proc.stdout.strip()}")
    return {"launched": pkg, "activity": activity}

# ---------- recipe replay ----------
def interpolate(val: Any, params: dict) -> Any:
    if isinstance(val, str):
        def repl(m): return str(params.get(m.group(1), m.group(0)))
        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", repl, val)
    return val

def run_step(step: dict, params: dict) -> dict:
    op = step["op"]
    def arg(k, default=None):
        return interpolate(step.get(k, default), params)
    if op == "launch":   return launch_app(arg("app"))
    if op == "tap":      return tap_selector(arg("selector"))
    if op == "type":     do_type(arg("value") or ""); return {"typed": arg("value")}
    if op == "key":      do_key(arg("value")); return {"key": arg("value")}
    if op == "wait":
        ok = wait_selector(arg("selector"), float(step.get("timeout", 10)), bool(step.get("gone", False)))
        if not ok: raise RuntimeError(f"wait timeout: {arg('selector')}")
        return {"waited": arg("selector")}
    if op == "swipe":
        do_swipe(int(step["x1"]), int(step["y1"]), int(step["x2"]), int(step["y2"]), int(step.get("ms", 300)))
        return {"swiped": True}
    if op == "sleep":    time.sleep(float(step["seconds"])); return {"slept": step["seconds"]}
    if op == "back":     do_key("KEYCODE_BACK"); return {"back": True}
    if op == "home":     do_key("KEYCODE_HOME"); return {"home": True}
    if op == "enter":    do_key("KEYCODE_ENTER"); return {"enter": True}
    raise RuntimeError(f"unknown op: {op}")

def run_recipe(name: str, params: dict) -> dict:
    m = load_memory()
    rec = m.get("recipes", {}).get(name)
    if not rec: raise RuntimeError(f"unknown recipe {name!r}. Known: {', '.join(sorted(m.get('recipes',{})))}")
    results = []
    for i, step in enumerate(rec["steps"]):
        try:
            r = run_step(step, params)
            results.append({"i": i, "step": step, **r})
        except Exception as e:
            return {"ok": False, "failed_at": i, "step": step, "error": str(e), "results": results}
    rec["success_count"] = rec.get("success_count", 0) + 1
    rec["last_used"] = datetime.date.today().isoformat()
    save_memory(m)
    return {"ok": True, "recipe": name, "results": results}

# ---------- screenshots ----------
def screenshot(path: str | None = None) -> str:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not path:
        path = str(SCREENSHOT_DIR / f"screen_{int(time.time())}.png")
    args = ["android", "screen", "capture", "-o", path]
    proc = sh(args)
    if proc.returncode != 0:
        raise RuntimeError(f"screenshot failed: {proc.stderr.strip()}")
    return path

def annotate(path: str | None = None) -> str:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not path:
        path = str(SCREENSHOT_DIR / f"annot_{int(time.time())}.png")
    args = ["android", "screen", "capture", "--annotate", "-o", path]
    proc = sh(args)
    if proc.returncode != 0:
        raise RuntimeError(f"annotate failed: {proc.stderr.strip()}")
    return path

def resolve_visual_raw(png_path: str, query: str) -> str:
    args = ["android", "screen", "resolve", "--screenshot", png_path, "--string", query]
    proc = sh(args)
    if proc.returncode != 0:
        raise RuntimeError(f"resolve failed: {proc.stderr.strip()}")
    return proc.stdout.strip()

def resolve_visual(png_path: str, query: str) -> tuple[int, int]:
    raw = resolve_visual_raw(png_path, query)
    m = re.search(r"(-?\d+)\s+(-?\d+)", raw)
    if not m: raise RuntimeError(f"resolve output not parseable: {raw!r}")
    return int(m.group(1)), int(m.group(2))

# ---------- commands ----------
def cmd_check(args):
    devs = list_devices()
    if not devs:
        print("NO_DEVICE: connect an Android device with USB debugging enabled.")
        sys.exit(2)
    lock = locked_device()
    if lock and any(d[0] == lock for d in devs):
        print(f"OK: locked={lock} devices={len(devs)}")
        return
    if len(devs) == 1:
        DEVICE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_LOCK.write_text(devs[0][0])
        print(f"OK: auto-locked {devs[0][0]}")
        return
    print("MULTIPLE_DEVICES:")
    for i, (s, _) in enumerate(devs, 1):
        print(f"  {i}. {s}")
    print("Use `pd select <serial>` to lock one.")
    sys.exit(3)

def cmd_select(args):
    if not args: die("usage: pd select <serial>")
    serial = args[0]
    if not any(d[0] == serial for d in list_devices()):
        die(f"device {serial} not connected")
    DEVICE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_LOCK.write_text(serial)
    print(f"OK: locked {serial}")

def cmd_release(args):
    if DEVICE_LOCK.exists(): DEVICE_LOCK.unlink()
    print("OK: released")

def cmd_info(args):
    s = active_device() or ""
    adb = adb_args()
    model = sh(adb + ["shell", "getprop", "ro.product.model"]).stdout.strip()
    manuf = sh(adb + ["shell", "getprop", "ro.product.manufacturer"]).stdout.strip()
    api   = sh(adb + ["shell", "getprop", "ro.build.version.sdk"]).stdout.strip()
    size  = sh(adb + ["shell", "wm", "size"]).stdout.strip()
    dens  = sh(adb + ["shell", "wm", "density"]).stdout.strip()
    print(json.dumps({"serial": s, "manufacturer": manuf, "model": model, "api": api,
                      "size": size, "density": dens}, indent=2))

def cmd_layout(args):
    flt = None
    for a in args:
        if a.startswith("--filter="): flt = a.split("=",1)[1]
    elements = get_layout()
    if flt:
        kind, needle = parse_selector(flt)
        elements = [el for el in elements if _match_element(el, kind, needle)]
    compact = [compact_element(el) for el in elements]
    # drop empties
    compact = [c for c in compact if c]
    print(json.dumps(compact, indent=2))

def cmd_tap(args):
    if not args: die("usage: pd tap <selector>")
    print(json.dumps(tap_selector(" ".join(args)), indent=2))

def cmd_type(args):
    if not args: die("usage: pd type <text>")
    do_type(" ".join(args))
    print("OK")

def cmd_key(args):
    if not args: die("usage: pd key <KEYCODE>")
    do_key(args[0]); print("OK")

def cmd_swipe(args):
    if len(args) < 4: die("usage: pd swipe <x1> <y1> <x2> <y2> [ms]")
    ms = int(args[4]) if len(args) > 4 else 300
    do_swipe(int(args[0]), int(args[1]), int(args[2]), int(args[3]), ms)
    print("OK")

def cmd_back(args):  do_key("KEYCODE_BACK");  print("OK")
def cmd_home(args):  do_key("KEYCODE_HOME");  print("OK")
def cmd_enter(args): do_key("KEYCODE_ENTER"); print("OK")

def cmd_launch(args):
    if not args: die("usage: pd launch <app>")
    print(json.dumps(launch_app(args[0]), indent=2))

def cmd_wait(args):
    if not args: die("usage: pd wait <selector> [timeout_sec] [--gone]")
    sel = args[0]
    timeout = 10.0
    gone = False
    for a in args[1:]:
        if a == "--gone": gone = True
        else:
            try: timeout = float(a)
            except: pass
    ok = wait_selector(sel, timeout, gone)
    print("OK" if ok else "TIMEOUT"); sys.exit(0 if ok else 4)

def cmd_screenshot(args):
    print(screenshot(args[0] if args else None))

def cmd_annotate(args):
    print(annotate(args[0] if args else None))

def cmd_resolve(args):
    if len(args) < 2: die("usage: pd resolve <png> <string>")
    print(resolve_visual_raw(args[0], " ".join(args[1:])))

def cmd_tap_visual(args):
    """Capture annotated screenshot, resolve query, tap. usage: pd tap-visual "#34" or pd tap-visual "tap #34" """
    if not args: die("usage: pd tap-visual <query>")
    path = annotate()
    x, y = resolve_visual(path, " ".join(args))
    do_tap(x, y)
    print(json.dumps({"tapped_xy": (x,y), "png": path}, indent=2))

def cmd_run(args):
    if not args: die("usage: pd run <recipe> [k=v ...]")
    name = args[0]
    params = {}
    for kv in args[1:]:
        if "=" in kv: k,v = kv.split("=",1); params[k] = v
    res = run_recipe(name, params)
    print(json.dumps(res, indent=2))
    sys.exit(0 if res.get("ok") else 5)

def cmd_save_recipe(args):
    if len(args) < 2: die("usage: pd save-recipe <name> <json>")
    name = args[0]
    payload = " ".join(args[1:])
    try: data = json.loads(payload)
    except Exception as e: die(f"bad JSON: {e}")
    required = {"steps"}
    if not required.issubset(data): die(f"recipe must have keys: {required}")
    data.setdefault("description", "")
    data.setdefault("params", [])
    data.setdefault("created_at", datetime.date.today().isoformat())
    data.setdefault("success_count", 0)
    m = load_memory()
    m.setdefault("recipes", {})[name] = data
    save_memory(m)
    print(f"OK: saved {name}")

def cmd_recipes(args):
    m = load_memory()
    recs = m.get("recipes", {})
    if "--verbose" in args or "-v" in args:
        print(json.dumps(recs, indent=2))
        return
    out = []
    for name, r in recs.items():
        out.append(f"{name}\t{r.get('description','')}\tparams={r.get('params',[])}\tused={r.get('success_count',0)}")
    print("\n".join(out) if out else "(no recipes)")

def cmd_recipe_get(args):
    if not args: die("usage: pd recipe-get <name>")
    m = load_memory()
    r = m.get("recipes", {}).get(args[0])
    if not r: die("not found")
    print(json.dumps(r, indent=2))

def cmd_recipe_del(args):
    if not args: die("usage: pd recipe-del <name>")
    m = load_memory()
    if args[0] in m.get("recipes", {}):
        del m["recipes"][args[0]]
        save_memory(m); print("OK")
    else: die("not found")

def cmd_save_app(args):
    if len(args) < 2: die("usage: pd save-app <name> <package> [activity]")
    name, pkg = args[0].lower(), args[1]
    activity = args[2] if len(args) > 2 else None
    m = load_memory()
    entry = {"package": pkg}
    if activity: entry["activity"] = activity
    m.setdefault("apps", {})[name] = entry
    save_memory(m); print(f"OK: saved app {name} -> {pkg}")

def cmd_apps(args):
    m = load_memory()
    print(json.dumps(m.get("apps", {}), indent=2))

def cmd_help(args):
    print(__doc__)
    print("\nCommands:\n  " + "\n  ".join(sorted(COMMANDS)))

COMMANDS = {
    "check": cmd_check, "select": cmd_select, "release": cmd_release, "info": cmd_info,
    "layout": cmd_layout,
    "tap": cmd_tap, "type": cmd_type, "key": cmd_key, "swipe": cmd_swipe,
    "back": cmd_back, "home": cmd_home, "enter": cmd_enter,
    "launch": cmd_launch, "wait": cmd_wait,
    "screenshot": cmd_screenshot, "annotate": cmd_annotate,
    "resolve": cmd_resolve, "tap-visual": cmd_tap_visual,
    "run": cmd_run, "save-recipe": cmd_save_recipe, "recipes": cmd_recipes,
    "recipe-get": cmd_recipe_get, "recipe-del": cmd_recipe_del,
    "save-app": cmd_save_app, "apps": cmd_apps,
    "help": cmd_help, "--help": cmd_help, "-h": cmd_help,
}

def main():
    if len(sys.argv) < 2:
        cmd_help([]); sys.exit(0)
    name, rest = sys.argv[1], sys.argv[2:]
    fn = COMMANDS.get(name)
    if not fn: die(f"unknown command: {name}. Try `pd help`.")
    try:
        fn(rest)
    except subprocess.TimeoutExpired as e:
        die(f"timeout: {e}")
    except RuntimeError as e:
        die(f"ERR: {e}")

if __name__ == "__main__":
    main()
