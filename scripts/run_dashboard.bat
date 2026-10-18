@echo off
cd /d "C:\Users\User\Desktop\Car Hunt"
if not exist data\logs mkdir data\logs
set PYTHONUNBUFFERED=1
:loop
python -m carhunt.dashboard --port 8770 >> data\logs\dashboard.log 2>&1
echo [%date% %time%] dashboard exited, restarting in 30s >> data\logs\dashboard.log
timeout /t 30 /nobreak > nul
goto loop
