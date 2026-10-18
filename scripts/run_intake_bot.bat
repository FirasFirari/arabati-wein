@echo off
cd /d "C:\Users\User\Desktop\Car Hunt"
if not exist data\logs mkdir data\logs
set PYTHONUNBUFFERED=1
:loop
echo [%date% %time%] starting intake bot >> data\logs\intake_bot.log
python -m carhunt.intake_bot >> data\logs\intake_bot.log 2>&1
echo [%date% %time%] intake bot exited, restarting in 30s >> data\logs\intake_bot.log
timeout /t 30 /nobreak > nul
goto loop
