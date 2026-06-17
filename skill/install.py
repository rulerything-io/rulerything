#!/usr/bin/env python3
"""
Rulerything Skill — Installer for Claude Code.

Installs the skill into a target Claude Code project by:
1. Copying the CLAUDE.md integration into the project's CLAUDE.md
2. Setting up the post-session-start hook
3. Verifying the rulerything server dependency
"""

import argparse
import json
import os
import shutil
import subprocess
import sys


def detect_claude_settings() -> list:
    """Detect possible Claude Code settings.json locations."""
    home = os.path.expanduser("~")
    candidates = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        candidates.append(os.path.join(appdata, "Claude", "settings.json"))
        candidates.append(os.path.join(home, ".claude", "settings.json"))
    else:
        candidates.append(os.path.join(home, ".claude", "settings.json"))
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
        candidates.append(os.path.join(xdg, "claude", "settings.json"))
    return candidates


def find_rulerything_server() -> str | None:
    """Try to find the rulerything server relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Check ../rulerything (default layout)
    candidate = os.path.normpath(os.path.join(script_dir, "..", "rulerything"))
    if os.path.exists(os.path.join(candidate, "main.py")):
        return candidate
    # Check ./rulerything
    candidate2 = os.path.join(script_dir, "rulerything")
    if os.path.exists(os.path.join(candidate2, "main.py")):
        return candidate2
    return None


def install(args):
    project_dir = os.path.abspath(args.project)
    skill_dir = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(project_dir):
        print(f"Error: project directory not found: {project_dir}")
        sys.exit(1)

    # ── Step 0: Check rulerything server ──
    server_dir = find_rulerything_server()
    if server_dir:
        print(f"✅ Rulerything server found at: {server_dir}")
    else:
        print("⚠️  Rulerything server not found in adjacent directories.")
        print("   Clone it with: git clone https://github.com/rulerything-io/rulerything.git")
        print(f"   Expected at: {os.path.normpath(os.path.join(skill_dir, '..', 'rulerything'))}")
        if args.require_server:
            sys.exit(1)

    # ── Step 1: Update project CLAUDE.md ──
    claude_md = os.path.join(project_dir, "CLAUDE.md")
    skill_claude_md = os.path.join(skill_dir, "CLAUDE.md")
    rule_helper_rel = os.path.relpath(
        os.path.join(skill_dir, "rule_helper.py"),
        project_dir
    )

    if not os.path.exists(skill_claude_md):
        print(f"Error: skill CLAUDE.md not found at {skill_claude_md}")
        sys.exit(1)

    with open(skill_claude_md, "r", encoding="utf-8") as f:
        skill_content = f.read()

    # Replace path placeholder with actual relative path
    skill_content = skill_content.replace(
        "/path/to/rulerything-skill/rule_helper.py",
        rule_helper_rel.replace("\\", "/")
    )

    if os.path.exists(claude_md):
        # Check if already installed
        with open(claude_md, "r", encoding="utf-8") as f:
            existing = f.read()
        if "Rulerything" in existing:
            print(f"⚠️  CLAUDE.md already contains Rulerything integration.")
            print(f"   Skipping — merge manually if needed.")
        else:
            # Append skill content
            with open(claude_md, "a", encoding="utf-8") as f:
                f.write("\n\n" + skill_content)
            print(f"✅ Appended Rulerything integration to {claude_md}")
    else:
        # Copy skill CLAUDE.md as project CLAUDE.md
        shutil.copy2(skill_claude_md, claude_md)
        print(f"✅ Created {claude_md} with Rulerything integration")

    # ── Step 2: Set up hook ──
    if args.setup_hook:
        hook_path = None
        if sys.platform == "win32":
            hook_rel = os.path.relpath(
                os.path.join(skill_dir, "hooks", "post-session-start.ps1"),
                project_dir
            )
            hook_cmd = f"powershell -File \"{hook_rel}\""
        else:
            hook_rel = os.path.relpath(
                os.path.join(skill_dir, "hooks", "post-session-start.sh"),
                project_dir
            )
            hook_cmd = f"bash \"{hook_rel}\""

        # Try project-level settings first, then user-level
        for settings_path in [
            os.path.join(project_dir, ".claude", "settings.json"),
            *detect_claude_settings(),
        ]:
            settings_dir = os.path.dirname(settings_path)
            os.makedirs(settings_dir, exist_ok=True)

            settings = {}
            if os.path.exists(settings_path):
                with open(settings_path, "r", encoding="utf-8") as f:
                    try:
                        settings = json.load(f)
                    except json.JSONDecodeError:
                        pass

            hooks = settings.get("hooks", {})
            if "PostSessionStart" in hooks:
                print(f"⚠️  Hook already set in {settings_path}: {hooks['PostSessionStart']}")
            else:
                hooks["PostSessionStart"] = hook_cmd
                settings["hooks"] = hooks
                with open(settings_path, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=2, ensure_ascii=False)
                print(f"✅ PostSessionStart hook added to {settings_path}")
            break  # Only update the first found settings file

    # ── Step 3: Summary ──
    print()
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│  Rulerything skill installed successfully!                 │")
    print("│                                                             │")
    print(f"│  Rule helper: {rule_helper_rel}")
    print(f"│  Project:     {project_dir}")
    if args.setup_hook:
        print("│  Hook:        Enabled (auto-start on session begin)      │")
    else:
        print("│  Hook:        Not configured                             │")
    print("│                                                             │")
    print("│  Next steps:                                                │")
    print("│  1. Start the rulerything server:                           │")
    print(f"│     python {rule_helper_rel} start")
    print("│  2. Test the integration:                                   │")
    print(f"│     python {rule_helper_rel} smart 'Python async'")
    print("└─────────────────────────────────────────────────────────────┘")


def main():
    parser = argparse.ArgumentParser(
        description="Install Rulerything skill into a Claude Code project"
    )
    parser.add_argument(
        "--project", "-p",
        required=True,
        help="Path to your Claude Code project (where CLAUDE.md lives)"
    )
    parser.add_argument(
        "--setup-hook",
        action="store_true",
        help="Configure PostSessionStart hook in settings.json"
    )
    parser.add_argument(
        "--require-server",
        action="store_true",
        help="Fail if rulerything server is not found"
    )
    args = parser.parse_args()
    install(args)


if __name__ == "__main__":
    main()
