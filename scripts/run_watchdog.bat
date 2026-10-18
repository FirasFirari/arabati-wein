@echo off
cd /d "C:\Users\User\Desktop\Car Hunt"
if not exist data\logs mkdir data\logs
set PYTHONUNBUFFERED=1
:loop
python -m carhunt.watchdog --loop --interval 300 >> data\logs\watchdog.log 2>&1
echo [%date% %time%] watchdog exited, restarting in 60s >> data\logs\watchdog.log
timeout /t 60 /nobreak > nul
goto loop
