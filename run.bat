@echo off
rem Start Engram Manager on Windows without a console window (Python from python.org includes Tkinter)
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 engram_manager.py) || (start "" pythonw engram_manager.py)
