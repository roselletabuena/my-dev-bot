@echo off
rem Launched by Windows Task Scheduler (task "dev-bot"). Polls Jira once and processes matching tickets.
cd /d "%~dp0.."
if not exist logs mkdir logs
echo [%date% %time%] poll start >> logs\scheduler.log
".venv\Scripts\dev-bot.exe" --config config.toml run >> logs\scheduler.log 2>&1
set RC=%errorlevel%
echo [%date% %time%] poll end, exit %RC% >> logs\scheduler.log
exit /b %RC%
