@echo off
chcp 65001 >nul
rem 雙擊開啟閱卷網頁。用法：閱卷.bat [出卷ID] [閱卷人]
cd /d "%~dp0"
set "RUN=%~1"
set "REVIEWER=%~2"
if "%REVIEWER%"=="" set /p "REVIEWER=閱卷人名稱（直接按 Enter 為 Richard）："
if "%REVIEWER%"=="" set "REVIEWER=Richard"
echo 正在開啟閱卷網頁，閱卷期間請不要關閉這個視窗；閱完按 Ctrl+C 或直接關閉即可。
".venv\Scripts\python.exe" -X utf8 -m dili.cli review-ui %RUN% --reviewer "%REVIEWER%"
pause
