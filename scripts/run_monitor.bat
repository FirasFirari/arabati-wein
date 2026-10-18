@echo off
cd /d "C:\Users\User\Desktop\Car Hunt"
if not exist data\logs mkdir data\logs
set PYTHONUNBUFFERED=1
:loop
echo [%date% %time%] starting channel monitor >> data\logs\monitor.log
python -m carhunt.telegram_monitor >> data\logs\monitor.log 2>&1
echo [%date% %time%] monitor exited, restarting in 60s >> data\logs\monitor.log
timeout /t 60 /nobreak > nul
goto loop
