Chaoxing GUI Tauri Windows x64 便携包

1. 将 ZIP 完整解压到可写文件夹，再运行 Start-Chaoxing.cmd。
   请保留 chaoxing-gui-tauri.exe、backend 整个目录及同目录的启动脚本。
   不要直接在 ZIP 内启动，也不要只复制 exe。
2. 启动入口只检测 WebView2，不会自动下载或安装运行时。
   若提示缺少 Microsoft Edge WebView2 Runtime，请主动运行 Install-WebView2.cmd。
   在线入口会下载微软官方 Evergreen bootstrapper，并在执行前校验微软签名。
3. 离线安装：在联网设备从微软官方页面下载 Evergreen Standalone Installer (x64)：
   https://developer.microsoft.com/microsoft-edge/webview2/#download-section
   将 MicrosoftEdgeWebView2RuntimeInstallerX64.exe 放到本目录，
   然后在目标设备主动运行 Install-WebView2.cmd；检测到该文件时不会下载 bootstrapper。
   也可在 PowerShell 运行：
   .\Install-WebView2.ps1 -InstallerPath "D:\下载\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
   只有有效 Microsoft Authenticode 签名的安装程序才能执行。
   安装完成后重新启动应用；退出码 3010 表示需先重启 Windows。

便携包与 Electron 版可并存。账号、任务及 WebView2 用户数据仍存放在当前
Windows 用户的 AppData 中，不会跟随此文件夹移动。删除便携文件夹不会删除
这些业务数据，也不会卸载原 Electron 版本。不要把含个人数据的 AppData 复制给别人。

backend-manifest.json 保存冻结后端全部文件的相对路径、大小及 SHA256。
package-manifest.json 保存包内文件校验值；ZIP 同目录的 .manifest.json 与 .sha256
可用于校验发行文件。校验脚本不需要启动应用。校验值用于检测内容变化，
发行者身份以数字签名和可信下载来源为准。
