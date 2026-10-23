@echo off
chcp 65001 >nul
rem 雙擊出一份新考卷。用法：new-exam.bat [藍圖名稱] [考卷名稱]
cd /d "%~dp0"
set "BPNAME=%~1"
set "RUN=%~2"
set "DEFAULT="
for %%f in (blueprints\*.json) do if not defined DEFAULT set "DEFAULT=%%~nf"
if not "%BPNAME%"=="" goto have_blueprint
echo 可用的藍圖：
for %%f in (blueprints\*.json) do echo   %%~nf
set /p "BPNAME=請輸入藍圖名稱（直接按 Enter 用 %DEFAULT%）："
if "%BPNAME%"=="" set "BPNAME=%DEFAULT%"
if "%RUN%"=="" set /p "RUN=考卷名稱（直接按 Enter 自動命名）："
:have_blueprint
if not exist "blueprints\%BPNAME%.json" (echo 找不到藍圖 blueprints\%BPNAME%.json & pause & exit /b 1)
echo.
echo 開始出卷：分配考點、三個 AI 同時出題、交叉審題、修題、檢核，約需 1 小時。
echo 過程中請不要關閉這個視窗。
echo.
if "%RUN%"=="" (
  ".venv\Scripts\python.exe" -u -X utf8 -m dili.cli make "blueprints\%BPNAME%.json"
) else (
  ".venv\Scripts\python.exe" -u -X utf8 -m dili.cli make "blueprints\%BPNAME%.json" --run "%RUN%"
)
pause
