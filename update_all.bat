@echo off
cd /d "%~dp0"
python update_all.py >> data\update.log 2>&1
