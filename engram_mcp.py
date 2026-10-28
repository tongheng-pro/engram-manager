#!/usr/bin/env python3
"""Engram MCP launcher for AI tools that only have a global MCP setting (Codex, Gemini CLI).

The tool starts this script in the project folder. It looks the folder up in Engram Manager's
project list (~/.engram-manager.json) and starts `engram mcp` with that project's memory folder.
Folders that are not connected use Engram's default store, like plain `engram mcp`.
No Tkinter here, so it also runs where only the command line Python is installed.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_FILE = Path.home() / ".engram-manager.json"
LOG_FILE = Path.home() / ".engram-manager-mcp.log"
LOG_MAX_BYTES = 200 * 1024


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def same_or_inside(folder, parent):
    folder, parent = os.path.normcase(folder), os.path.normcase(parent)
    return folder == parent or folder.startswith(parent.rstrip("\\/") + os.sep)


def find_project(config, folder):
    """The connected project for a folder (or its closest connected parent folder)."""
    folder = os.path.realpath(folder)
    best = None
    for project_folder, info in (config.get("projects") or {}).items():
        if same_or_inside(folder, os.path.realpath(project_folder)):
            if best is None or len(project_folder) > len(best[0]):
                best = (project_folder, info)
    return dict(best[1], folder=best[0]) if best else None


def log(message):
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            LOG_FILE.unlink()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def main():
    config = load_config()
    engram = config.get("engram_path") or shutil.which("engram") or "engram"
    command = [engram, "mcp", "--tools=agent"]
    env = os.environ.copy()

    project = find_project(config, os.getcwd())
    if project:
        env["ENGRAM_DATA_DIR"] = project["data_dir"]
        command += ["--project", project["name"]]
        log("%s -> %s (%s)" % (os.getcwd(), project["name"], project["data_dir"]))
    else:
        log("%s -> not connected, Engram default store" % os.getcwd())

    # stdin/stdout carry the MCP messages, so the server must inherit them as they are
    if os.name == "nt":
        sys.exit(subprocess.call(command, env=env))
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    main()
