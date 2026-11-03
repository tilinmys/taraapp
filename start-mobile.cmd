@echo off
rem Start the tara app (UI, Expo Go) from the repo root. Works in PowerShell and cmd.
rem   .\start-mobile.cmd            phone/tablet via Expo Go (scan the QR)
rem   .\start-mobile.cmd --lan      force Wi-Fi (phone and PC on the same network)
rem   .\start-mobile.cmd --web      open in the browser
cd /d "%~dp0app"
npx expo start --go %*
