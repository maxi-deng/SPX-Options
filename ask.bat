@echo off
rem One-command interface to the multi-agent system, e.g.:
rem   ask "load news to rag"
rem   ask "find me call spreads at least 4%% away"
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*
