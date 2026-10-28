# Engram Manager

A small desktop app (Windows and Ubuntu) for [Engram](https://github.com/Gentleman-Programming/engram), memory for AI coding tools.
It works with **Claude Code**, **Codex** and **Gemini CLI**.

- **Connect a project:** its memories go in their own folder, by default `Documents/engram/<project>`.
  - Old projects reuse their existing memory folder.
- **Find old memories** in any memory folder, for example Engram's default `~/.engram`, and copy a project out to its own folder.
- **Browse, search, add, delete, export and import** memories, or open Engram's terminal view.
- **Turn the memory instructions on or off** for each tool. This adds or removes a marked section in its global `CLAUDE.md`, `AGENTS.md` or `GEMINI.md`.
- **A Guide tab** has step-by-step help, and it opens on the first run.
- **Nothing is written into your projects.**

## How each tool is connected

| Tool | Where | How the project is chosen |
| --- | --- | --- |
| Claude Code | Claude's private "local" setting per folder, in `~/.claude.json` | One setting per project folder |
| Codex | One `[mcp_servers.engram]` entry in `~/.codex/config.toml` | `engram_mcp.py` matches the folder Codex runs in (or a subfolder) to a connected project |
| Gemini CLI | One `mcpServers.engram` entry in `~/.gemini/settings.json` | Same as Codex |

- The app's settings and the list of connected projects are in `~/.engram-manager.json`.
- Tool config files are backed up as `.bak` before each change.
- Each start of the launcher is logged in `~/.engram-manager-mcp.log`.

## Requirements

| | Windows | Ubuntu |
| --- | --- | --- |
| Python 3.8+ | [python.org](https://www.python.org/downloads/) (includes Tkinter) | `sudo apt install python3 python3-tk` (and `zenity`, normally installed) |
| Engram | [Releases](https://github.com/Gentleman-Programming/engram/releases), or `go install github.com/Gentleman-Programming/engram/v3/cmd/engram@latest` | same |
| AI tool | Claude Code, Codex and/or Gemini CLI | same |

No `pip install` is needed.

## Run

```bash
git clone <this repo> engram-manager
cd engram-manager
./run.sh          # Ubuntu
run.bat           # Windows (double-click)
```

Then follow the **Guide** tab:
1. **Settings:** click **Detect programs** → **Test** → **Save**.
2. **AI tools:** for Codex or Gemini, click **Install / update**. For each tool you use, click **Memory instructions on/off**.
3. **Projects:** click **+ Connect a project…**, pick the folder, tick the tools, then click **Connect**.
4. **Restart the tool** in that folder and type `/mcp` to check that `engram` is listed.

The launcher path is saved inside the Codex and Gemini settings. If you move this app's folder, click **Install / update** again.
# engram-manager
# engram-manager
