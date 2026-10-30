@echo off
rem Builds dist\NowPlayingRPC.exe (single file, no console, tray only).
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --noconsole ^
  --name NowPlayingRPC ^
  --icon "%~dp0assets\app.ico" ^
  --collect-all winrt ^
  --hidden-import pystray._win32 ^
  nowplaying_rpc.py
