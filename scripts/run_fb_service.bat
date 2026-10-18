@echo off
cd /d "C:\Users\User\Desktop\Car Hunt"
if not exist data\logs mkdir data\logs
set PYTHONUNBUFFERED=1
:loop
echo [%date% %time%] starting facebook service >> data\logs\fb_service.log
python -m carhunt.fb_service --per-cycle 3 --rest 900 --scrolls 30 >> data\logs\fb_service.log 2>&1
echo [%date% %time%] facebook service exited, restarting in 120s >> data\logs\fb_service.log
timeout /t 120 /nobreak > nul
goto loop
