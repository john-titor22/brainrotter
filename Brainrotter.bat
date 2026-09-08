@echo off
rem Double-click to open Brainrotter as an app window.
rem The dashboard server starts with it and stops when you close the window.
title Brainrotter
cd /d "%~dp0"
".venv\Scripts\brainrotter.exe" app
