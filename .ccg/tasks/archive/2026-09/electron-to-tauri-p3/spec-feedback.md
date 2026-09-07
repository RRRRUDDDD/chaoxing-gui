# P3 规范回馈

以下经验已追加到本机ignored `.ccg/spec/frontend/index.md`。此档案保存本轮新增段落；既有通用规范保持原位。

- 完整PyInstaller onedir按目录staging与映射；按版本、目录、长度、SHA256检查，保留Flask内web/dist。
- Tauri CLI2.11.4将NSIS的UNK标记改成NSS，并恢复未签名原宿主；两种产物分别保存完整host hash，NSIS签名捕获需匹配独立preSign计算，portable恢复后另签。
- 后端资源清单生成后不再改写第三方依赖签名字节。
- Get-Command可能返回多个安装路径，调用单个程序时选择首项；中文JSON管道必须明确UTF-8编解码并用真实窗口验证。
- release验收只在fresh runner或专用用户/VM，debug覆写不能证明release隔离；supervisor死亡不能代替已捕获Job清理证据。
- NSIS的空资源祖先代表安装根；旧版本独有目录也要预检。预检无法消除之后的并发路径替换时窗。
