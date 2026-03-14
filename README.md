# Phone Driver

A Claude Code skill that automates Android devices using visual understanding and ADB. Describe a task in natural language and Claude will execute it by analyzing screenshots and performing touch actions.

<p align="center">
  <img src="Images/PhoneDriver.png" width="600" alt="Phone Driver Demo">
</p>

## Features

- **Zero dependencies** — No Python, no GPU, no model downloads. Just ADB.
- **Natural language tasks** — Describe what you want in plain English
- **Visual understanding** — Claude analyzes phone screenshots to understand UI elements
- **ADB integration** — Controls Android devices via tap, swipe, type, and key events
- **Smart app launching** — Uses Android intents to open apps directly
- **Safety built-in** — Refuses destructive actions unless explicitly requested

## Requirements

- [Claude Code](https://claude.ai/code) CLI installed
- ADB (Android Debug Bridge) installed and on PATH
- Android device with USB debugging enabled

### Install ADB

**macOS:**
```bash
brew install android-platform-tools
```

**Linux/Ubuntu:**
```bash
sudo apt update && sudo apt install adb
```

**Windows:**
Download from [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)

## Installation

### Option 1: Per-Project (clone this repo)

```bash
git clone https://github.com/OminousIndustries/PhoneDriver.git
cd PhoneDriver
```

The command is already at `.claude/commands/phone-driver.md` — it works automatically when you run Claude Code from this directory.

### Option 2: Global Install (any project)

```bash
# Download just the command file
mkdir -p ~/.claude/commands
curl -o ~/.claude/commands/phone-driver.md \
  https://raw.githubusercontent.com/OminousIndustries/PhoneDriver/main/.claude/commands/phone-driver.md
```

Now `/phone-driver` works from any project directory.

## Setup

1. Enable USB debugging on your Android device:
   - Go to **Settings → About Phone**
   - Tap **Build Number** 7 times to enable Developer Options
   - Go to **Settings → Developer Options → USB Debugging** and enable it

2. Connect your device via USB

3. Verify connection:
   ```bash
   adb devices
   ```
   You should see your device listed as `device` (not `unauthorized`).

## Usage

In Claude Code, invoke the command with a task description:

```
/phone-driver "open Chrome"
/phone-driver "search for weather in New York on Google"
/phone-driver "open Settings and enable WiFi"
/phone-driver "open the Calculator and compute 123 + 456"
/phone-driver "take a screenshot of the home screen"
/phone-driver "open Camera"
```

### What Happens

1. Claude verifies your device is connected and detects its screen resolution
2. Captures a screenshot from the device
3. Analyzes the screenshot using its built-in vision
4. Determines the best action (tap, swipe, type text, press button)
5. Executes the action via ADB
6. Captures a new screenshot to verify the result
7. Repeats until the task is complete (max 15 cycles)

## How It Works

Unlike traditional phone automation tools that require scripting specific coordinates or element IDs, Phone Driver uses Claude's multimodal vision to understand what's on screen — just like a human would. It sees buttons, text, icons, and menus, then figures out what to tap or type to accomplish your task.

The entire skill is a single markdown file (`.claude/commands/phone-driver.md`) that instructs Claude how to:
- Capture and analyze screenshots
- Estimate tap coordinates from visual element positions
- Execute ADB commands for device interaction
- Track action history to avoid loops
- Handle errors and retry with alternative approaches

## Troubleshooting

**Device not detected:**
- Ensure USB debugging is enabled
- Accept the authorization prompt on the phone
- Try `adb kill-server && adb start-server`

**Wrong tap locations:**
- Claude auto-detects resolution via `adb shell wm size`
- If taps are off, the device may have a different DPI or display scaling

**App not launching:**
- Some apps have non-standard package names
- Claude will try `adb shell pm list packages | grep <keyword>` to find the correct package

## Comparison: Before vs After

| | Original (Python + Qwen3-VL) | New (Claude Code Skill) |
|---|---|---|
| **Dependencies** | Python, torch, transformers, PIL, gradio | Just ADB |
| **GPU** | 24GB+ VRAM required | None |
| **Model download** | ~16GB model weights | None |
| **Setup time** | 30+ minutes | 2 minutes |
| **Interface** | Gradio web UI or CLI | Claude Code (terminal) |
| **Vision model** | Qwen3-VL (local) | Claude (cloud) |

## License

Apache License 2.0 — see [LICENSE](LICENSE) file for details.

## Acknowledgments

- Powered by [Claude Code](https://claude.ai/code) by Anthropic
- Uses [ADB](https://developer.android.com/tools/adb) for device communication
- Original concept inspired by [Qwen3-VL](https://github.com/QwenLM/Qwen-VL) by Alibaba Cloud
