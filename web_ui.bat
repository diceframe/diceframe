@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   TRPG WebUI - port from data/config.json web_port, see console
echo   首次使用请在浏览器设置页添加 AI 服务商并选择主模型
echo ========================================
echo.

python scripts\start_webui.py
if errorlevel 1 (
    echo.
    echo WebUI 启动失败，请查看上面的错误信息。
    pause
    exit /b 1
)
pause
