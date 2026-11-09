@echo off
rem Sakhi Indic STT (bodhan-ai/indic-transcribe-flex) on http://127.0.0.1:8795
rem Port 8795: 8790 is the local Live Sakhi agent worker.
cd /d "%~dp0.."
"%~dp0.venv\Scripts\python.exe" -m uvicorn stt_service.app:app --host 127.0.0.1 --port 8795
