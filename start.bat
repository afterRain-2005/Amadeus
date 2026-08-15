@echo off
cd /d "%~dp0"
start "GPT-SoVITS" /b /d "GPT-SoVITS" "..\gpt_sovits_venv\Scripts\pythonw.exe" api_v2.py
start "Amadeus" /b D:\anaconda\pythonw.exe main.py
