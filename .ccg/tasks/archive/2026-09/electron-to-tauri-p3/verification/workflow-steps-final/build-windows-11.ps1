New-Item -ItemType Directory -Force release/python | Out-Null
Copy-Item -LiteralPath dist/chaoxing-gui.exe -Destination release/python/chaoxing-gui.exe
Copy-Item -LiteralPath README.md -Destination release/python/README.md
@{ version=$env:APP_VERSION; platform='windows-x64'; kind='independent-python' } | ConvertTo-Json | Set-Content release/python/version.json -Encoding utf8
Compress-Archive -Path release/python/* -DestinationPath "release/chaoxing-gui-no-electron-$env:APP_VERSION-windows-x64.zip" -Force
