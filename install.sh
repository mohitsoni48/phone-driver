#!/bin/bash
# PhoneDriver standalone installer.
# Usage:
#   curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
#   or: git clone https://github.com/mohitsoni48/phone-driver.git && cd phone-driver && ./install.sh

set -euo pipefail

INSTALL_DIR="$HOME/.claude/phonedriver"
SKILL_DIR="$HOME/.claude/skills/phone-driver"
REPO="mohitsoni48/phone-driver"
REPO_URL="https://raw.githubusercontent.com/$REPO/main"

echo "── PhoneDriver installer ──"

# ── Prerequisites ────────────────────────────────────────────────────
missing=()
command -v adb    >/dev/null 2>&1 || missing+=("adb")
command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 || missing+=("python")
command -v android >/dev/null 2>&1 || missing+=("android")

if [ ${#missing[@]} -gt 0 ]; then
    echo "MISSING: ${missing[*]}"
    [[ " ${missing[*]} " =~ " adb "     ]] && echo "  - adb: install Android platform-tools"
    [[ " ${missing[*]} " =~ " python "  ]] && echo "  - python: https://python.org"
    [[ " ${missing[*]} " =~ " android " ]] && echo "  - android CLI: https://developer.android.com/studio/command-line"
    exit 1
fi
echo "[ok] prerequisites"

# ── Install files ────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/scripts" "$SKILL_DIR"

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || true
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/scripts/driver.py" ]; then
    echo "Installing from local clone..."
    cp "$SCRIPT_DIR/scripts/driver.py"        "$INSTALL_DIR/scripts/driver.py"
    cp "$SCRIPT_DIR/scripts/pd"               "$INSTALL_DIR/scripts/pd"
    cp "$SCRIPT_DIR/skills/phone-driver/SKILL.md" "$SKILL_DIR/SKILL.md"
    [ -f "$INSTALL_DIR/memory.json" ] || cp "$SCRIPT_DIR/memory-seed.json" "$INSTALL_DIR/memory.json"
else
    echo "Downloading from GitHub..."
    curl -sfL "$REPO_URL/scripts/driver.py" -o "$INSTALL_DIR/scripts/driver.py"
    curl -sfL "$REPO_URL/scripts/pd"        -o "$INSTALL_DIR/scripts/pd"
    curl -sfL "$REPO_URL/skills/phone-driver/SKILL.md" -o "$SKILL_DIR/SKILL.md"
    [ -f "$INSTALL_DIR/memory.json" ] || curl -sfL "$REPO_URL/memory-seed.json" -o "$INSTALL_DIR/memory.json"
fi

chmod +x "$INSTALL_DIR/scripts/pd" "$INSTALL_DIR/scripts/driver.py"

# ── Verify ───────────────────────────────────────────────────────────
if bash "$INSTALL_DIR/scripts/pd" help >/dev/null 2>&1; then
    echo "[ok] pd works"
else
    echo "[!!] pd failed to run"; exit 1
fi

echo ""
echo "Installed:"
echo "  $INSTALL_DIR/scripts/pd"
echo "  $INSTALL_DIR/scripts/driver.py"
echo "  $SKILL_DIR/SKILL.md"
echo "  $INSTALL_DIR/memory.json   (learned recipes persist here)"
echo ""
echo "Use in Claude Code:"
echo "  /phone-driver \"open Settings and search for battery\""
