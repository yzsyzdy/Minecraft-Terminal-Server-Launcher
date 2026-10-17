python -m nuitka --standalone ^
  --assume-yes-for-downloads ^
  --enable-plugin=anti-bloat ^
  --windows-console-mode=force ^
  --windows-icon-from-ico=icon.png ^
  --output-dir=build ^
  --product-name=MSTL ^
  --product-version="1.1.38" ^
  --file-version="1.1.38" ^
  --file-description="Minecraft Terminal Server Launcher" ^
  --copyright="MSTL" ^
  --include-data-dir=locales=locales ^
  --include-data-dir=webui=webui ^
  --output-filename=MSTL.exe ^
  start.py

echo.
echo   finish: build\start.dist\MSTL.exe
pause
