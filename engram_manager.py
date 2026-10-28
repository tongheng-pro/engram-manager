#!/usr/bin/env python3
"""Engram Manager: connect projects to Engram memory and browse the memories.

Works on Windows and Ubuntu with Python 3.8+ and no extra packages (Tkinter only).
It drives the `engram` and `claude` command line tools; nothing is written into project folders.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

import engram_mcp

APP_NAME = "Engram Manager"
IS_WINDOWS = os.name == "nt"
HOME = Path.home()
CONFIG_FILE = engram_mcp.CONFIG_FILE
CLAUDE_CONFIG = HOME / ".claude.json"
# Engram's own default store, where memories live when ENGRAM_DATA_DIR is not set
DEFAULT_ENGRAM_DIR = HOME / ".engram"
MEMORY_TYPES = ["decision", "architecture", "bugfix", "config", "discovery", "pattern", "learning", "manual"]
INSTALL_HINT = ("Install Engram first: https://github.com/Gentleman-Programming/engram\n"
                "With Go: go install github.com/Gentleman-Programming/engram/v3/cmd/engram@latest")

GUIDE = """
# How to use Engram Manager
Engram gives your AI coding tools a memory per project: decisions, gotchas, your preferences and setup steps. This app connects projects to it and lets you read and edit the memories. Nothing is written into your project folders.

## 1. First time on this computer (Settings tab)
1. Install Engram (link at the bottom of Settings) and the AI tools you use: Claude Code, Codex, Gemini CLI and/or Antigravity.
2. Click `Detect programs`, then `Test`. Both lines should say OK (claude may be missing if you only use Codex, Gemini or Antigravity).
3. Memory root folder: where every project's memories go. Default `Documents/engram`. Use a synced folder (OneDrive, Google Drive) to share between computers.
4. Click `Save`.
5. Under AI tools:
   - Codex / Gemini CLI / Antigravity: click `Install / update` once. (Antigravity is the `agy` program; it does not use the Gemini CLI row.)
   - Every tool you use: click `Memory instructions on/off` so it says "instructions on". This tells the tool to search memory before a task and save after.

## 2. Connect a project (Projects tab)
1. Click `+ Connect a project…` and `Browse…` to the project folder (click the folder, then Select).
2. The memory folder name defaults to the project folder name:
   - "Old project": a memory folder with that name exists and is reused.
   - "New project": an empty one is created.
3. Tick the tools to use (Claude Code is set per folder; Codex, Gemini and Antigravity use the global setting from step 1) and click `Connect`.
4. Restart the tool in that folder:
   - Claude Code in VS Code: Ctrl+Shift+P → `Developer: Reload Window`, then type `/mcp` → engram should be connected.
   - Codex / Gemini / Antigravity: close and start again in the project folder, then type `/mcp` to see engram.

## 3. Working with memory (in Claude, Codex or Gemini)
- Just work: the tool searches memory before a task and saves what matters after.
- "Remember: we use PostgreSQL 14" → saved.
- "What do you remember about deployment?" → searched.
- "Forget the memory about the old API URL" → deleted.
- "Which Engram project are you using?" → checks the right memory folder is used.

## 4. Old memories (Find old memories tab)
Memories saved before this app are usually in Engram's default folder `~/.engram`. The tab scans it; select a project and click `Copy to its own memory folder`, then connect the project folder with that name.

## 5. Read and clean memories (Memories tab)
Pick the memory folder and project. Search, read, `+ Add memory…`, `Delete` wrong or old ones, `Export…` / `Import…` to move them, or open Engram's terminal view.

## 6. New computer
1. Clone this app and run `run.sh` (Ubuntu) or `run.bat` (Windows).
2. Copy your `Documents/engram` folder over (or use Export / Import, or a synced memory root).
3. Do step 1 and connect each project again. Connections belong to this computer and the folder path.

## Problems
- Tool doesn't show engram: restart it in the project folder; check Settings → AI tools says "Engram installed".
- Codex/Gemini/Antigravity use the wrong memories: they must be started inside the project folder. Each start is logged in `~/.engram-manager-mcp.log` (folder → project).
- Moved or renamed a project folder: connect it again with the same memory folder name.
- Changes to tool settings are backed up next to the file as `.bak`.
"""

# Engram prints an update notice on every command; it is not part of the result
NOISE = re.compile(r"^\s*(Update available|To update:|brew update|or: go install)")
PROJECT_LINE = re.compile(r"^\s+(\S+)\s+(\d+) obs")


class CommandError(Exception):
    pass


# ─── Settings ────────────────────────────────────────────────────────────────

def documents_dir():
    docs = HOME / "Documents"
    return docs if docs.is_dir() else HOME


def find_engram():
    found = shutil.which("engram")
    if found:
        return found
    exe = "engram.exe" if IS_WINDOWS else "engram"
    for candidate in (HOME / "go" / "bin" / exe, HOME / ".local" / "bin" / exe, Path("/usr/local/bin") / exe):
        if candidate.is_file():
            return str(candidate)
    return ""


def load_config():
    config = {
        "memory_root": str(documents_dir() / "engram"),
        "engram_path": find_engram(),
        "claude_path": shutil.which("claude") or "",
        # {project folder: {"name", "data_dir"}}, read by engram_mcp.py for Codex and Gemini
        "projects": {},
    }
    try:
        config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ─── Command line tools ──────────────────────────────────────────────────────

def run(cmd, data_dir=None, cwd=None, timeout=120):
    """Run a command and return its output without Engram's update notice."""
    env = os.environ.copy()
    if data_dir:
        env["ENGRAM_DATA_DIR"] = str(data_dir)
    extra = {"creationflags": 0x08000000} if IS_WINDOWS else {}  # CREATE_NO_WINDOW: no console flash
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, **extra)
    except FileNotFoundError:
        raise CommandError("Program not found: %s" % cmd[0])
    except subprocess.TimeoutExpired:
        raise CommandError("%s took too long" % Path(cmd[0]).name)
    output = "\n".join(line for line in (proc.stdout + "\n" + proc.stderr).splitlines()
                       if not NOISE.match(line)).strip()
    if proc.returncode != 0:
        raise CommandError(output or "%s exited with code %d" % (Path(cmd[0]).name, proc.returncode))
    return output


class Engram:
    def __init__(self, config):
        self.config = config

    @property
    def exe(self):
        path = self.config.get("engram_path") or "engram"
        return path

    def version(self):
        return run([self.exe, "version"])

    def projects(self, data_dir):
        """[(project, memory count)] in one store."""
        if not (Path(data_dir) / "engram.db").is_file():
            return []
        output = run([self.exe, "projects", "list"], data_dir)
        return [(m.group(1), int(m.group(2))) for m in map(PROJECT_LINE.match, output.splitlines()) if m]

    def export(self, data_dir, project, path):
        args = ["--project", project] if project else ["--all"]
        run([self.exe, "export", str(path)] + args, data_dir)

    def memories(self, data_dir, project):
        """All memories of a project, newest first (read from a JSON export)."""
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            self.export(data_dir, project, tmp)
            data = json.loads(Path(tmp).read_text(encoding="utf-8"))
        finally:
            os.remove(tmp)
        # The export also contains deleted memories (with deleted_at set)
        items = [o for o in data.get("observations") or [] if not o.get("deleted_at")]
        return sorted(items, key=lambda o: (o.get("updated_at") or o.get("created_at") or "", o.get("id", 0)),
                      reverse=True)

    def save(self, data_dir, project, title, content, kind):
        return run([self.exe, "save", title, content, "--type", kind, "--project", project], data_dir)

    def delete(self, data_dir, memory_id):
        return run([self.exe, "delete", str(memory_id)], data_dir)

    def import_file(self, data_dir, path):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        return run([self.exe, "import", str(path)], data_dir)

    def copy_project(self, source_dir, project, target_dir):
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            self.export(source_dir, project, tmp)
            return self.import_file(target_dir, tmp)
        finally:
            os.remove(tmp)


class Claude:
    """Claude Code's MCP setting for one folder (local scope, kept in ~/.claude.json)."""

    def __init__(self, config):
        self.config = config

    @property
    def exe(self):
        return self.config.get("claude_path") or "claude"

    @staticmethod
    def connected_projects():
        """[{folder, name, data_dir}] for every folder that has the engram MCP server."""
        try:
            projects = json.loads(CLAUDE_CONFIG.read_text(encoding="utf-8")).get("projects") or {}
        except (OSError, ValueError):
            return []
        result = []
        for folder, settings in projects.items():
            server = (settings.get("mcpServers") or {}).get("engram")
            if not server:
                continue
            args = server.get("args") or []
            name = args[args.index("--project") + 1] if "--project" in args[:-1] else Path(folder).name
            data_dir = (server.get("env") or {}).get("ENGRAM_DATA_DIR") or str(DEFAULT_ENGRAM_DIR)
            result.append({"folder": folder, "name": name, "data_dir": data_dir})
        return sorted(result, key=lambda p: p["name"].lower())

    def connect(self, folder, name, data_dir, engram_exe):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.disconnect(folder)
        run([self.exe, "mcp", "add", "--scope", "local", "engram", "-e", "ENGRAM_DATA_DIR=%s" % data_dir,
             "--", engram_exe, "mcp", "--tools=agent", "--project", name], cwd=folder)

    def disconnect(self, folder):
        try:
            run([self.exe, "mcp", "remove", "engram", "--scope", "local"], cwd=folder)
        except CommandError:
            pass  # not connected


# ─── Codex, Gemini CLI and Antigravity (one global setting that starts engram_mcp.py) ─

CODEX_CONFIG = HOME / ".codex" / "config.toml"
GEMINI_CONFIG = HOME / ".gemini" / "settings.json"
# Antigravity (agy) keeps its MCP servers here, not in Gemini CLI's settings.json
ANTIGRAVITY_CONFIG = HOME / ".gemini" / "config" / "mcp_config.json"
# Global instruction files each tool reads in every project (Antigravity shares GEMINI.md with Gemini CLI)
INSTRUCTION_FILES = {
    "claude": HOME / ".claude" / "CLAUDE.md",
    "codex": HOME / ".codex" / "AGENTS.md",
    "gemini": HOME / ".gemini" / "GEMINI.md",
    "antigravity": HOME / ".gemini" / "GEMINI.md",
}
TOOL_LABELS = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "antigravity": "Antigravity"}
ALL_TOOLS = ("claude", "codex", "gemini", "antigravity")
INSTRUCTIONS_START = "<!-- engram-manager:start -->"
INSTRUCTIONS_END = "<!-- engram-manager:end -->"
INSTRUCTIONS = """## Engram memory
If the `engram` MCP server is available:
- At the start of a task, call `mem_context`, then `mem_search` for the area you are working on.
- After a task, `mem_save` what is not obvious from the code: decisions and why, gotchas, user preferences, build/test steps.
- Never save passwords, keys, tokens or patient data.
- When the user says "remember ..." save it; when they say "forget ..." delete it."""
CODEX_SECTION = re.compile(r"^\[mcp_servers\.engram(\.[^\]]*)?\]$")


def launcher_command():
    """How Codex and Gemini start engram_mcp.py: this Python (with a console, not pythonw) and the script path."""
    python = sys.executable
    if IS_WINDOWS and Path(python).name.lower() == "pythonw.exe":
        python = str(Path(python).with_name("python.exe"))
    return [python, str(Path(__file__).resolve().with_name("engram_mcp.py"))]


def backup(path):
    if path.is_file():
        shutil.copy2(path, str(path) + ".bak")


def toml_string(value):
    # A JSON string is a valid TOML basic string (escapes backslashes in Windows paths)
    return json.dumps(value)


def without_codex_engram(text):
    """config.toml without the [mcp_servers.engram] tables."""
    kept, skipping = [], False
    for line in text.splitlines():
        if line.strip().startswith("["):
            skipping = bool(CODEX_SECTION.match(line.strip()))
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip() + "\n" if kept else ""


def codex_installed():
    try:
        return any(CODEX_SECTION.match(l.strip()) for l in CODEX_CONFIG.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def install_codex():
    command = launcher_command()
    text = CODEX_CONFIG.read_text(encoding="utf-8") if CODEX_CONFIG.is_file() else ""
    backup(CODEX_CONFIG)
    block = "\n[mcp_servers.engram]\ncommand = %s\nargs = [%s]\n" % (
        toml_string(command[0]), ", ".join(toml_string(a) for a in command[1:]))
    CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG.write_text(without_codex_engram(text) + block, encoding="utf-8")


def remove_codex():
    if CODEX_CONFIG.is_file():
        backup(CODEX_CONFIG)
        CODEX_CONFIG.write_text(without_codex_engram(CODEX_CONFIG.read_text(encoding="utf-8")), encoding="utf-8")


def read_json_settings(path):
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise CommandError("Cannot read %s (not plain JSON). Add the engram server by hand." % path)


def json_installed(path):
    """True only when the engram entry starts engram_mcp.py (a plain `engram mcp` entry ignores projects)."""
    try:
        entry = (read_json_settings(path).get("mcpServers") or {}).get("engram") or {}
    except CommandError:
        return False
    return any(str(arg).endswith("engram_mcp.py") for arg in entry.get("args") or [])


def install_json(path):
    settings = read_json_settings(path)
    command = launcher_command()
    settings.setdefault("mcpServers", {})["engram"] = {"command": command[0], "args": command[1:]}
    backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def remove_json(path):
    settings = read_json_settings(path)
    if (settings.get("mcpServers") or {}).pop("engram", None) is not None:
        backup(path)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def gemini_installed():
    return json_installed(GEMINI_CONFIG)


GLOBAL_TOOLS = {
    "codex": (codex_installed, install_codex, remove_codex, CODEX_CONFIG),
    "gemini": (gemini_installed, lambda: install_json(GEMINI_CONFIG), lambda: remove_json(GEMINI_CONFIG),
               GEMINI_CONFIG),
    "antigravity": (lambda: json_installed(ANTIGRAVITY_CONFIG), lambda: install_json(ANTIGRAVITY_CONFIG),
                    lambda: remove_json(ANTIGRAVITY_CONFIG), ANTIGRAVITY_CONFIG),
}


def instructions_added(tool):
    path = INSTRUCTION_FILES[tool]
    return path.is_file() and INSTRUCTIONS_START in path.read_text(encoding="utf-8")


def set_instructions(tool, enabled):
    """Add or remove the "use Engram" section (between markers) in the tool's global instruction file."""
    path = INSTRUCTION_FILES[tool]
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if INSTRUCTIONS_START in text:
        start = text.index(INSTRUCTIONS_START)
        end = text.find(INSTRUCTIONS_END, start)
        end = len(text) if end < 0 else end + len(INSTRUCTIONS_END)
        text = (text[:start].rstrip() + "\n" + text[end:].lstrip("\n")).strip("\n")
        text = text + "\n" if text else ""
    if enabled:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + \
               "%s\n%s\n%s\n" % (INSTRUCTIONS_START, INSTRUCTIONS, INSTRUCTIONS_END)
    backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ─── Desktop helpers ─────────────────────────────────────────────────────────

def open_folder(path):
    if IS_WINDOWS:
        os.startfile(path)  # noqa: only exists on Windows
    else:
        subprocess.Popen(["xdg-open", str(path)])


def choose_folder(parent, title, initialdir=""):
    """Folder picker. On Linux, Tk's own picker returns the folder you are *in*, not the one clicked,
    so use the desktop's picker (zenity) when available. Windows already gets its native picker."""
    if not IS_WINDOWS and shutil.which("zenity"):
        start = initialdir if initialdir and Path(initialdir).is_dir() else str(HOME)
        proc = subprocess.run(["zenity", "--file-selection", "--directory", "--title", title,
                               "--filename", start.rstrip("/") + "/"],
                              capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    return filedialog.askdirectory(parent=parent, title=title, initialdir=initialdir or str(HOME))


def open_tui(engram_exe, data_dir):
    """Open Engram's terminal browser in a new terminal window."""
    if IS_WINDOWS:
        env = os.environ.copy()
        env["ENGRAM_DATA_DIR"] = str(data_dir)
        subprocess.Popen(["cmd", "/c", "start", "Engram", "cmd", "/k", engram_exe, "tui"], env=env)
        return
    command = "ENGRAM_DATA_DIR=%s exec %s tui" % (shlex.quote(str(data_dir)), shlex.quote(engram_exe))
    for terminal in (["gnome-terminal", "--"], ["konsole", "-e"], ["xfce4-terminal", "-x"],
                     ["x-terminal-emulator", "-e"], ["xterm", "-e"]):
        if shutil.which(terminal[0]):
            subprocess.Popen(terminal + ["bash", "-c", command])
            return
    raise CommandError("No terminal program found")


# ─── Window ──────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        first_run = not CONFIG_FILE.is_file()
        self.config_data = load_config()
        self.engram = Engram(self.config_data)
        self.claude = Claude(self.config_data)
        self.memories = []
        self.sync_registry()

        self.title(APP_NAME)
        self.geometry("1000x640")
        self.minsize(820, 520)
        style = ttk.Style(self)
        if not IS_WINDOWS and "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=26)
        style.configure("Hint.TLabel", foreground="#666")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.build_projects_tab()
        self.build_memories_tab()
        self.build_old_tab()
        self.build_settings_tab()
        self.build_guide_tab()

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, anchor="w", style="Hint.TLabel").pack(fill="x", padx=10, pady=4)

        self.refresh_projects()
        self.refresh_stores()
        if first_run:
            self.notebook.select(4)
        if not self.config_data.get("engram_path"):
            self.notebook.select(3)
            messagebox.showwarning(APP_NAME, "Engram was not found.\n\n" + INSTALL_HINT)

    # ─── helpers ───

    @property
    def memory_root(self):
        return Path(self.config_data["memory_root"])

    def busy(self, action, done_message=""):
        """Run an action with a busy cursor and show errors in a dialog."""
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            result = action()
            if done_message:
                self.status.set(done_message)
            return result
        except CommandError as e:
            messagebox.showerror(APP_NAME, str(e))
        except OSError as e:
            messagebox.showerror(APP_NAME, str(e))
        finally:
            self.config(cursor="")

    @staticmethod
    def make_tree(parent, columns):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=width > 150)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return frame, tree

    # ─── project list shared with engram_mcp.py ───

    def registry(self):
        return self.config_data.setdefault("projects", {})

    def register_project(self, folder, name, data_dir):
        self.unregister_project(folder, save=False)
        self.registry()[folder] = {"name": name, "data_dir": data_dir}
        save_config(self.config_data)

    def unregister_project(self, folder, save=True):
        for key in [k for k in self.registry() if os.path.normcase(k) == os.path.normcase(folder)]:
            del self.registry()[key]
        if save:
            save_config(self.config_data)

    def sync_registry(self):
        """Projects connected for Claude Code also become known to engram_mcp.py (Codex, Gemini)."""
        known = {os.path.normcase(folder) for folder in self.registry()}
        missing = [p for p in Claude.connected_projects() if os.path.normcase(p["folder"]) not in known]
        for project in missing:
            self.registry()[project["folder"]] = {"name": project["name"], "data_dir": project["data_dir"]}
        if missing:
            save_config(self.config_data)

    def all_projects(self):
        """[{folder, name, data_dir, tools}] for every connected project."""
        claude = {os.path.normcase(p["folder"]) for p in Claude.connected_projects()}
        global_tools = [TOOL_LABELS[tool] for tool, (installed, *_rest) in GLOBAL_TOOLS.items() if installed()]
        result = []
        for folder, info in self.registry().items():
            tools = (["Claude Code"] if os.path.normcase(folder) in claude else []) + global_tools
            result.append(dict(info, folder=folder, tools=", ".join(tools) or "none"))
        return sorted(result, key=lambda p: p["name"].lower())

    def known_stores(self):
        """Memory folders: the ones under the memory root, connected ones, and Engram's default store."""
        stores = []
        if self.memory_root.is_dir():
            stores += [p for p in sorted(self.memory_root.iterdir()) if (p / "engram.db").is_file()]
        stores += [Path(info["data_dir"]) for info in self.registry().values()]
        if (DEFAULT_ENGRAM_DIR / "engram.db").is_file():
            stores.append(DEFAULT_ENGRAM_DIR)
        unique = []
        for store in stores:
            if str(store) not in unique:
                unique.append(str(store))
        return unique

    # ─── Projects tab ───

    def build_projects_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Projects  ")

        ttk.Label(tab, text="Projects connected to Engram. Each one keeps its memories in its own folder.",
                  style="Hint.TLabel").pack(anchor="w")
        frame, self.project_tree = self.make_tree(tab, [
            ("name", "Project", 200), ("count", "Memories", 80), ("tools", "AI tools", 200),
            ("folder", "Project folder", 260), ("data", "Memory folder", 260)])
        frame.pack(fill="both", expand=True, pady=8)
        self.project_tree.bind("<Double-1>", lambda e: self.show_project_memories())

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="+ Connect a project…", command=self.connect_dialog).pack(side="left")
        ttk.Button(buttons, text="Show memories", command=self.show_project_memories).pack(side="left", padx=6)
        ttk.Button(buttons, text="Open memory folder", command=self.open_project_memory_folder).pack(side="left")
        ttk.Button(buttons, text="Disconnect", command=self.disconnect_project).pack(side="left", padx=6)
        ttk.Button(buttons, text="Refresh", command=self.refresh_projects).pack(side="right")

    def refresh_projects(self):
        self.project_tree.delete(*self.project_tree.get_children())
        for project in self.all_projects():
            count = "-"
            try:
                count = dict(self.engram.projects(project["data_dir"])).get(project["name"], 0)
            except CommandError:
                pass
            folder = project["folder"] + ("" if Path(project["folder"]).is_dir() else "  (missing)")
            self.project_tree.insert("", "end", values=(project["name"], count, project["tools"], folder,
                                                        project["data_dir"]))
        self.status.set("%d connected project(s)" % len(self.project_tree.get_children()))

    def selected_project(self):
        selection = self.project_tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Select a project first.")
            return None
        name, _count, _tools, folder, data_dir = self.project_tree.item(selection[0], "values")
        return {"name": name, "folder": folder.replace("  (missing)", ""), "data_dir": data_dir}

    def show_project_memories(self):
        project = self.selected_project()
        if project:
            self.refresh_stores()
            self.store_var.set(project["data_dir"])
            self.load_store_projects(select=project["name"])
            self.notebook.select(1)

    def open_project_memory_folder(self):
        project = self.selected_project()
        if project:
            self.busy(lambda: open_folder(project["data_dir"]))

    def disconnect_project(self):
        project = self.selected_project()
        if not project:
            return
        if not messagebox.askyesno(APP_NAME, "Disconnect Engram from %s?\n\nClaude Code, Codex and Gemini stop "
                                   "using these memories in this folder. The memories stay in:\n%s"
                                   % (project["folder"], project["data_dir"])):
            return
        def disconnect():
            self.claude.disconnect(project["folder"])
            self.unregister_project(project["folder"])

        self.busy(disconnect, "Disconnected " + project["name"])
        self.refresh_projects()

    def connect_dialog(self):
        ConnectDialog(self)

    # ─── Memories tab ───

    def build_memories_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Memories  ")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Memory folder").grid(row=0, column=0, sticky="w")
        self.store_var = tk.StringVar()
        self.store_box = ttk.Combobox(top, textvariable=self.store_var)
        self.store_box.grid(row=0, column=1, sticky="ew", padx=6)
        self.store_box.bind("<<ComboboxSelected>>", lambda e: self.load_store_projects())
        self.store_box.bind("<Return>", lambda e: self.load_store_projects())
        ttk.Button(top, text="Browse…", command=self.browse_store).grid(row=0, column=2)
        ttk.Label(top, text="Project").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.store_project_var = tk.StringVar()
        self.store_project_box = ttk.Combobox(top, textvariable=self.store_project_var, state="readonly")
        self.store_project_box.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        self.store_project_box.bind("<<ComboboxSelected>>", lambda e: self.load_memories())
        ttk.Label(top, text="Search").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.fill_memories())
        ttk.Entry(top, textvariable=self.filter_var).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        top.columnconfigure(1, weight=1)

        panes = ttk.PanedWindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True, pady=8)
        frame, self.memory_tree = self.make_tree(panes, [
            ("id", "ID", 60), ("type", "Type", 110), ("title", "Title", 520), ("updated", "Updated", 170)])
        self.memory_tree.bind("<<TreeviewSelect>>", lambda e: self.show_memory())
        panes.add(frame, weight=3)
        self.memory_text = tk.Text(panes, height=8, wrap="word", relief="flat", padx=8, pady=6)
        panes.add(self.memory_text, weight=2)

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="+ Add memory…", command=self.add_memory_dialog).pack(side="left")
        ttk.Button(buttons, text="Delete", command=self.delete_memory).pack(side="left", padx=6)
        ttk.Button(buttons, text="Export…", command=self.export_memories).pack(side="left")
        ttk.Button(buttons, text="Import…", command=self.import_memories).pack(side="left", padx=6)
        ttk.Button(buttons, text="Open in terminal (TUI)", command=self.open_tui).pack(side="left")
        ttk.Button(buttons, text="Reload", command=self.load_memories).pack(side="right")

    def refresh_stores(self):
        stores = self.known_stores()
        self.store_box["values"] = stores
        if hasattr(self, "old_source_box"):
            self.old_source_box["values"] = stores
        if not self.store_var.get() and stores:
            self.store_var.set(stores[0])
            self.load_store_projects()

    def browse_store(self):
        folder = choose_folder(self, "Choose a memory folder (contains engram.db)",
                               self.store_var.get() or str(self.memory_root))
        if folder:
            self.store_var.set(folder)
            self.load_store_projects()

    def load_store_projects(self, select=None):
        projects = self.busy(lambda: self.engram.projects(self.store_var.get())) or []
        names = [name for name, _count in projects]
        self.store_project_box["values"] = names
        self.store_project_var.set(select if select in names else (names[0] if names else ""))
        self.load_memories()

    def load_memories(self):
        project = self.store_project_var.get()
        self.memories = []
        if project:
            self.memories = self.busy(lambda: self.engram.memories(self.store_var.get(), project)) or []
        self.fill_memories()
        self.status.set("%d memories in %s" % (len(self.memories), project or "(no project)"))

    def fill_memories(self):
        words = self.filter_var.get().lower().split()
        self.memory_tree.delete(*self.memory_tree.get_children())
        for memory in self.memories:
            text = " ".join(str(memory.get(k, "")) for k in ("title", "content", "type")).lower()
            if all(word in text for word in words):
                updated = (memory.get("updated_at") or memory.get("created_at") or "")[:16]
                self.memory_tree.insert("", "end", iid=str(memory["id"]),
                                        values=(memory["id"], memory.get("type", ""), memory.get("title", ""), updated))
        self.memory_text.delete("1.0", "end")

    def show_memory(self):
        selection = self.memory_tree.selection()
        memory = next((m for m in self.memories if selection and str(m["id"]) == selection[0]), None)
        self.memory_text.delete("1.0", "end")
        if memory:
            self.memory_text.insert("1.0", "%s\n\n%s" % (memory.get("title", ""), memory.get("content", "")))

    def add_memory_dialog(self):
        if not self.store_var.get():
            messagebox.showinfo(APP_NAME, "Choose a memory folder first.")
            return
        MemoryDialog(self)

    def delete_memory(self):
        selection = self.memory_tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Select a memory first.")
            return
        if messagebox.askyesno(APP_NAME, "Delete memory #%s?" % selection[0]):
            self.busy(lambda: self.engram.delete(self.store_var.get(), selection[0]), "Deleted #" + selection[0])
            self.load_memories()

    def export_memories(self):
        project = self.store_project_var.get()
        path = filedialog.asksaveasfilename(title="Export memories", defaultextension=".json",
                                            initialfile="%s-memories.json" % (project or "engram"),
                                            filetypes=[("JSON", "*.json")])
        if path:
            self.busy(lambda: self.engram.export(self.store_var.get(), project, path), "Exported to " + path)

    def import_memories(self):
        path = filedialog.askopenfilename(title="Import memories", filetypes=[("JSON", "*.json")])
        if path:
            output = self.busy(lambda: self.engram.import_file(self.store_var.get(), path))
            if output is not None:
                messagebox.showinfo(APP_NAME, output)
                self.load_store_projects(select=self.store_project_var.get())

    def open_tui(self):
        self.busy(lambda: open_tui(self.engram.exe, self.store_var.get()))

    # ─── Find old memories tab ───

    def build_old_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Find old memories  ")

        ttk.Label(tab, text="Look inside any memory folder (default: Engram's own %s) and copy a project's "
                            "memories into its own folder under the memory root." % DEFAULT_ENGRAM_DIR,
                  style="Hint.TLabel", wraplength=900).pack(anchor="w")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        ttk.Label(top, text="Look in").pack(side="left")
        self.old_source_var = tk.StringVar(value=str(DEFAULT_ENGRAM_DIR))
        self.old_source_box = ttk.Combobox(top, textvariable=self.old_source_var)
        self.old_source_box.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Browse…", command=self.browse_old_source).pack(side="left")
        ttk.Button(top, text="Scan", command=self.scan_old).pack(side="left", padx=(6, 0))

        frame, self.old_tree = self.make_tree(tab, [("project", "Project", 400), ("count", "Memories", 100)])
        frame.pack(fill="both", expand=True)
        # Scan the default store the first time the tab is opened
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self.notebook.index("current") == 2
                           and not self.old_tree.get_children()
                           and (Path(self.old_source_var.get()) / "engram.db").is_file() and self.scan_old())

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Copy to its own memory folder", command=self.copy_old).pack(side="left")
        ttk.Button(bottom, text="Browse these memories", command=self.browse_old).pack(side="left", padx=6)

    def browse_old_source(self):
        folder = choose_folder(self, "Choose a memory folder (contains engram.db)", self.old_source_var.get())
        if folder:
            self.old_source_var.set(folder)
            self.scan_old()

    def scan_old(self):
        self.old_tree.delete(*self.old_tree.get_children())
        source = self.old_source_var.get()
        if not (Path(source) / "engram.db").is_file():
            messagebox.showinfo(APP_NAME, "No Engram memories (engram.db) in:\n" + source)
            return
        for name, count in self.busy(lambda: self.engram.projects(source)) or []:
            self.old_tree.insert("", "end", iid=name, values=(name, count))
        self.status.set("%d project(s) in %s" % (len(self.old_tree.get_children()), source))

    def selected_old(self):
        selection = self.old_tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Scan, then select a project first.")
        return selection[0] if selection else None

    def copy_old(self):
        project = self.selected_old()
        if not project:
            return
        target = self.memory_root / project
        if str(target) == str(Path(self.old_source_var.get())):
            messagebox.showinfo(APP_NAME, "These memories are already in their own folder.")
            return
        if not messagebox.askyesno(APP_NAME, "Copy the memories of %s to\n%s?\n\nThe originals are kept."
                                   % (project, target)):
            return
        output = self.busy(lambda: self.engram.copy_project(self.old_source_var.get(), project, target))
        if output is not None:
            self.refresh_stores()
            messagebox.showinfo(APP_NAME, output + "\n\nNow connect the project folder in the Projects tab "
                                                   "and choose the memory folder \"%s\"." % project)

    def browse_old(self):
        project = self.selected_old()
        if project:
            self.store_var.set(self.old_source_var.get())
            self.load_store_projects(select=project)
            self.notebook.select(1)

    # ─── Settings tab ───

    def build_settings_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Settings  ")
        ttk.Label(tab, text="Start here: 1. Detect programs   2. Test   3. Save   4. Under AI tools, install the tools "
                            "you use and turn on their memory instructions.   (Full steps: Guide tab)",
                  style="Hint.TLabel", wraplength=940).grid(row=0, column=0, columnspan=3, sticky="w")
        self.setting_vars = {}
        rows = [
            ("memory_root", "Memory root folder", "New projects get a folder here, e.g. Documents/engram/<project>", True),
            ("engram_path", "engram program", "Path to engram (engram.exe on Windows)", False),
            ("claude_path", "claude program", "Path to Claude Code (claude / claude.cmd); empty if you don't use it", False),
        ]
        for row, (key, label, hint, is_folder) in enumerate(rows, start=1):
            ttk.Label(tab, text=label).grid(row=row * 2, column=0, sticky="w", pady=(8, 0))
            var = tk.StringVar(value=self.config_data.get(key, ""))
            self.setting_vars[key] = var
            ttk.Entry(tab, textvariable=var).grid(row=row * 2, column=1, sticky="ew", padx=6, pady=(8, 0))
            ttk.Button(tab, text="Browse…", command=lambda k=key, f=is_folder: self.browse_setting(k, f)) \
                .grid(row=row * 2, column=2, pady=(8, 0))
            ttk.Label(tab, text=hint, style="Hint.TLabel").grid(row=row * 2 + 1, column=1, sticky="w", padx=6)
        tab.columnconfigure(1, weight=1)

        buttons = ttk.Frame(tab)
        buttons.grid(row=8, column=0, columnspan=3, sticky="w", pady=12)
        ttk.Button(buttons, text="Detect programs", command=self.detect_programs).pack(side="left")
        ttk.Button(buttons, text="Test", command=self.test_programs).pack(side="left", padx=6)
        ttk.Button(buttons, text="Save", command=self.save_settings).pack(side="left")
        ttk.Label(buttons, text="Saved in %s (not in any project)" % CONFIG_FILE,
                  style="Hint.TLabel").pack(side="left", padx=12)

        tools = ttk.LabelFrame(tab, text="AI tools", padding=10)
        tools.grid(row=9, column=0, columnspan=3, sticky="ew")
        ttk.Label(tools, text="Claude Code is connected per project (Projects tab). Codex, Gemini CLI and "
                              "Antigravity have one global setting: it starts engram_mcp.py, which picks the memory "
                              "folder from the project folder you open. Memory instructions tell the tool to search "
                              "and save memories (added to its global CLAUDE.md / AGENTS.md / GEMINI.md; Gemini CLI "
                              "and Antigravity share GEMINI.md).",
                  style="Hint.TLabel", wraplength=920).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))
        self.tool_status = {}
        for row, tool in enumerate(ALL_TOOLS, start=1):
            ttk.Label(tools, text=TOOL_LABELS[tool], width=12).grid(row=row, column=0, sticky="w", pady=3)
            self.tool_status[tool] = tk.StringVar()
            ttk.Label(tools, textvariable=self.tool_status[tool]).grid(row=row, column=1, sticky="w")
            if tool in GLOBAL_TOOLS:
                ttk.Button(tools, text="Install / update", command=lambda t=tool: self.install_tool(t)) \
                    .grid(row=row, column=2, padx=3)
                ttk.Button(tools, text="Remove", command=lambda t=tool: self.remove_tool(t)).grid(row=row, column=3)
            ttk.Button(tools, text="Memory instructions on/off", command=lambda t=tool: self.toggle_instructions(t)) \
                .grid(row=row, column=4, padx=3)
        tools.columnconfigure(1, weight=1)
        self.refresh_tool_status()
        ttk.Label(tab, text=INSTALL_HINT, style="Hint.TLabel").grid(row=10, column=0, columnspan=3, sticky="w", pady=8)

    def refresh_tool_status(self):
        for tool, var in self.tool_status.items():
            if tool in GLOBAL_TOOLS:
                state = "Engram installed" if GLOBAL_TOOLS[tool][0]() else "Engram not installed"
            else:
                state = "Connect each project in the Projects tab"
            var.set("%s  ·  instructions %s" % (state, "on" if instructions_added(tool) else "off"))

    def install_tool(self, tool):
        _installed, install, _remove, config_path = GLOBAL_TOOLS[tool]
        if self.busy(lambda: install() or True, "Engram installed for " + TOOL_LABELS[tool]):
            self.refresh_tool_status()
            self.refresh_projects()
            messagebox.showinfo(APP_NAME, "Engram added to %s\n(backup: %s.bak)\n\nRestart %s. Every project in the "
                                          "Projects tab now uses its own memory folder there."
                                % (config_path, config_path, TOOL_LABELS[tool]))

    def remove_tool(self, tool):
        _installed, _install, remove, config_path = GLOBAL_TOOLS[tool]
        if messagebox.askyesno(APP_NAME, "Remove Engram from %s?\n(%s; the memories are kept)"
                               % (TOOL_LABELS[tool], config_path)):
            self.busy(remove, "Engram removed from " + TOOL_LABELS[tool])
            self.refresh_tool_status()
            self.refresh_projects()

    def toggle_instructions(self, tool):
        enable = not instructions_added(tool)
        path = INSTRUCTION_FILES[tool]
        question = ("Add the \"use Engram memory\" instructions to\n%s?\n\nThey apply to every project in %s."
                    if enable else "Remove the Engram memory instructions from\n%s?") % (path, TOOL_LABELS[tool])
        if messagebox.askyesno(APP_NAME, question):
            self.busy(lambda: set_instructions(tool, enable),
                      "Instructions %s for %s" % ("added" if enable else "removed", TOOL_LABELS[tool]))
            self.refresh_tool_status()

    def browse_setting(self, key, is_folder):
        current = self.setting_vars[key].get()
        if is_folder:
            path = choose_folder(self, "Choose the memory root folder", current)
        else:
            path = filedialog.askopenfilename(initialdir=str(Path(current).parent) if current else str(HOME))
        if path:
            self.setting_vars[key].set(path)

    def detect_programs(self):
        self.setting_vars["engram_path"].set(find_engram())
        self.setting_vars["claude_path"].set(shutil.which("claude") or "")

    def save_settings(self):
        for key, var in self.setting_vars.items():
            self.config_data[key] = var.get().strip()
        self.busy(lambda: save_config(self.config_data), "Settings saved")
        self.refresh_stores()
        self.refresh_projects()

    def test_programs(self):
        self.save_settings()
        lines = []
        for name, cmd in (("engram", [self.engram.exe, "version"]), ("claude", [self.claude.exe, "--version"])):
            try:
                lines.append("%s: OK  %s" % (name, run(cmd).splitlines()[0]))
            except (CommandError, IndexError) as e:
                lines.append("%s: NOT WORKING  %s" % (name, e))
        messagebox.showinfo(APP_NAME, "\n".join(lines))

    # ─── Guide tab ───

    def build_guide_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Guide  ")
        text = tk.Text(tab, wrap="word", relief="flat", padx=14, pady=10, spacing1=2, spacing3=2, font="TkDefaultFont")
        scroll = ttk.Scrollbar(tab, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        base = font.nametofont("TkDefaultFont")
        text.tag_configure("h1", font=(base.actual("family"), base.actual("size") + 5, "bold"), spacing1=6, spacing3=6)
        text.tag_configure("h2", font=(base.actual("family"), base.actual("size") + 2, "bold"), spacing1=12, spacing3=4)
        text.tag_configure("code", font="TkFixedFont", background="#eeeeee")
        for line in GUIDE.strip("\n").splitlines():
            tag = "h1" if line.startswith("# ") else "h2" if line.startswith("## ") else ""
            if tag:
                text.insert("end", line.lstrip("# ") + "\n", tag)
                continue
            # `code` parts in a monospace font
            for i, part in enumerate(line.split("`")):
                text.insert("end", part, "code" if i % 2 else "")
            text.insert("end", "\n")
        text.configure(state="disabled")


# ─── Dialogs ─────────────────────────────────────────────────────────────────

class Dialog(tk.Toplevel):
    def __init__(self, app, title):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.transient(app)
        self.resizable(True, False)
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(1, weight=1)

    def show(self):
        self.update_idletasks()
        self.geometry("+%d+%d" % (self.app.winfo_rootx() + 80, self.app.winfo_rooty() + 80))
        self.grab_set()


class ConnectDialog(Dialog):
    """Connect a project folder: new memories, or the existing memories of an old project."""

    def __init__(self, app):
        super().__init__(app, "Connect a project")
        self.minsize(640, 0)
        b = self.body
        self.folder_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.hint_var = tk.StringVar(value="Choose the project folder.")

        ttk.Label(b, text="Project folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(b, textvariable=self.folder_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(b, text="Browse…", command=self.browse).grid(row=0, column=2)
        ttk.Label(b, text="Memory folder").grid(row=1, column=0, sticky="w", pady=(10, 0))
        stores = [p.name for p in sorted(app.memory_root.iterdir())
                  if (p / "engram.db").is_file()] if app.memory_root.is_dir() else []
        self.name_box = ttk.Combobox(b, textvariable=self.name_var, values=stores)
        self.name_box.grid(row=1, column=1, sticky="ew", padx=6, pady=(10, 0))
        ttk.Label(b, text="inside %s" % app.memory_root, style="Hint.TLabel").grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(b, textvariable=self.hint_var, wraplength=560).grid(row=3, column=0, columnspan=3, sticky="w", pady=10)
        self.name_var.trace_add("write", lambda *a: self.update_hint())

        # Tick the tools that look installed on this computer
        ttk.Label(b, text="Use with").grid(row=4, column=0, sticky="nw")
        tools = ttk.Frame(b)
        tools.grid(row=4, column=1, columnspan=2, sticky="w", padx=6)
        defaults = {
            "claude": bool(app.config_data.get("claude_path") or shutil.which("claude")),
            "codex": codex_installed() or CODEX_CONFIG.parent.is_dir(),
            "gemini": gemini_installed() or bool(shutil.which("gemini")),
            "antigravity": GLOBAL_TOOLS["antigravity"][0]() or (HOME / ".gemini" / "antigravity").is_dir(),
        }
        notes = {"claude": "this folder only", "codex": "global setting, picks this folder",
                 "gemini": "global setting, picks this folder", "antigravity": "global setting, picks this folder"}
        self.tool_vars = {}
        for tool in ALL_TOOLS:
            self.tool_vars[tool] = tk.BooleanVar(value=defaults[tool])
            ttk.Checkbutton(tools, text="%s  (%s)" % (TOOL_LABELS[tool], notes[tool]),
                            variable=self.tool_vars[tool]).pack(anchor="w")

        buttons = ttk.Frame(b)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Connect", command=self.connect).pack(side="right", padx=6)
        self.show()

    def browse(self):
        folder = choose_folder(self, "Choose the project folder", self.folder_var.get())
        if folder:
            self.folder_var.set(folder)
            # An old project reuses the memory folder with the same name
            self.name_var.set(Path(folder).name)

    def update_hint(self):
        name = self.name_var.get().strip()
        store = self.app.memory_root / name
        if not name:
            self.hint_var.set("Choose the project folder.")
        elif (store / "engram.db").is_file():
            try:
                count = sum(c for _n, c in self.app.engram.projects(store))
            except CommandError:
                count = "?"
            self.hint_var.set("Old project: will reuse the existing memories in %s (%s memories)." % (store, count))
        else:
            self.hint_var.set("New project: memories will be saved in %s.\n"
                              "Old memories somewhere else? Use the \"Find old memories\" tab first." % store)

    def connect(self):
        folder, name = self.folder_var.get().strip(), self.name_var.get().strip()
        if not Path(folder).is_dir() or not name:
            messagebox.showinfo(APP_NAME, "Choose an existing project folder and a memory folder name.", parent=self)
            return
        chosen = [tool for tool, var in self.tool_vars.items() if var.get()]
        if not chosen:
            messagebox.showinfo(APP_NAME, "Tick at least one AI tool.", parent=self)
            return
        data_dir = str(self.app.memory_root / name)

        def connect():
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            # The project list is what engram_mcp.py reads for Codex and Gemini
            self.app.register_project(folder, name, data_dir)
            if "claude" in chosen:
                self.app.claude.connect(folder, name, data_dir, self.app.engram.exe)
            else:
                self.app.claude.disconnect(folder)
            for tool in chosen:
                if tool in GLOBAL_TOOLS and not GLOBAL_TOOLS[tool][0]():
                    GLOBAL_TOOLS[tool][1]()
            return True

        if self.app.busy(connect, "Connected %s" % name):
            self.destroy()
            self.app.refresh_projects()
            self.app.refresh_stores()
            self.app.refresh_tool_status()
            messagebox.showinfo(APP_NAME, "Connected for %s.\n\nRestart the tool in this folder "
                                          "(VS Code: Developer: Reload Window), then type /mcp to check."
                                % ", ".join(TOOL_LABELS[t] for t in chosen))


class MemoryDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Add memory")
        self.minsize(560, 0)
        b = self.body
        self.project_var = tk.StringVar(value=app.store_project_var.get() or Path(app.store_var.get()).name)
        self.title_var = tk.StringVar()
        self.type_var = tk.StringVar(value="decision")

        ttk.Label(b, text="Project").grid(row=0, column=0, sticky="w")
        ttk.Entry(b, textvariable=self.project_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(b, text="Title").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(b, textvariable=self.title_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(b, text="Type").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(b, textvariable=self.type_var, values=MEMORY_TYPES).grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(b, text="What to remember").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        self.content = tk.Text(b, height=8, wrap="word")
        self.content.grid(row=3, column=1, sticky="ew", padx=6, pady=(8, 0))

        buttons = ttk.Frame(b)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right", padx=6)
        self.show()

    def save(self):
        project, title = self.project_var.get().strip(), self.title_var.get().strip()
        content = self.content.get("1.0", "end").strip()
        if not (project and title and content):
            messagebox.showinfo(APP_NAME, "Fill in project, title and what to remember.", parent=self)
            return
        output = self.app.busy(lambda: self.app.engram.save(self.app.store_var.get(), project, title, content,
                                                            self.type_var.get().strip() or "manual"))
        if output is not None:
            self.destroy()
            self.app.status.set(output)
            self.app.load_store_projects(select=project)


def main():
    if IS_WINDOWS:
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
