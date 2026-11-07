REM Nuitka 的 --include-data-dir 会跳过 .py 文件（它把源码当代码处理），
REM 而插件入口必须是 plugin.py，所以下面用 --include-data-files 再显式包一次。
python -m nuitka --standalone ^
  --assume-yes-for-downloads ^
  --enable-plugin=anti-bloat ^
  --windows-console-mode=force ^
  --windows-icon-from-ico=icon.png ^
  --output-dir=build ^
  --product-name=MSTL ^
  --product-version="1.1.39" ^
  --file-version="1.1.39" ^
  --file-description="Minecraft Terminal Server Launcher" ^
  --copyright="MSTL" ^
  --include-data-dir=locales=locales ^
  --include-data-dir=webui=webui ^
  --include-data-dir=plugins=plugins ^
  --include-data-files=plugins/terminal/plugin.py=plugins/terminal/plugin.py ^
  --include-data-files=plugins/terminal/conpty.py=plugins/terminal/conpty.py ^
  --output-filename=MSTL.exe ^
  start.py

echo.
echo   finish: build\start.dist\MSTL.exe
pause
