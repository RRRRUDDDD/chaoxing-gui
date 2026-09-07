# 开发环境初始化（本机专用，不进 CI）
# 用法：在会话中先 `. .\desktop\scripts\dev-env.ps1`，再运行 cargo / tauri 命令。
# 本机 rustup 代理缺失（无 rustup.exe、~/.cargo/bin 为空），需要直接使用工具链目录。
# 本机 MSVC 检测缺失（无 vswhere.exe、无 VS 注册表键），需要 vcvars64 提供 link.exe/LIB/INCLUDE。

$ErrorActionPreference = 'Stop'
$toolchainBin = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
if (-not (Test-Path $toolchainBin)) { throw "未找到 Rust 工具链: $toolchainBin" }
$env:PATH = "$toolchainBin;$env:PATH"
$env:CARGO_HOME = "$env:USERPROFILE\.cargo"

# 通过 vcvars64 获取 MSVC 链接环境（子进程 cmd 输出解析，避免改变当前控制台代码页）
$bat = Join-Path $env:TEMP "chaoxing-vcenv-$(Get-Random).bat"
@'
@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 exit /b %errorlevel%
set
'@ | Set-Content $bat -Encoding ascii
try {
  $vcOutput = & cmd.exe /d /c "`"$bat`""
  if ($LASTEXITCODE -ne 0) { throw "vcvars64 failed with exit code $LASTEXITCODE" }
  $envLines = $vcOutput | Select-String -Pattern "^(LIB|INCLUDE|Path)=" -SimpleMatch:$false
} finally {
  Remove-Item -LiteralPath $bat -ErrorAction SilentlyContinue
}
foreach ($line in $envLines) {
  $name, $value = $line.Line -split '=', 2
  switch ($name) {
    'LIB' { $env:LIB = $value }
    'INCLUDE' { $env:INCLUDE = $value }
    'Path' { $env:VCVARS_PATH = $value }
  }
}
# 把 vcvars 的 PATH 中的 MSVC/SDK 目录并入当前 PATH（放在最前，保证 link.exe 可被 cargo 找到）
if ($env:VCVARS_PATH) {
  $vcDirs = ($env:VCVARS_PATH -split ';') | Where-Object { $_ -match 'MSVC|Windows Kits' }
  foreach ($dir in $vcDirs) { if (Test-Path $dir) { $env:PATH = "$dir;$env:PATH" } }
  Remove-Item Env:VCVARS_PATH -ErrorAction SilentlyContinue
}
$cargoVersion = & cargo --version
if ($LASTEXITCODE -ne 0) { throw "cargo failed with exit code $LASTEXITCODE" }
Write-Host "Rust 工具链与 MSVC 链接环境已就绪: $cargoVersion"
