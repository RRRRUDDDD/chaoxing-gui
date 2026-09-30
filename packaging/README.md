# 共用打包配置

`spec_common.py` 为根目录的两份 PyInstaller spec 提供 `datas`、`hiddenimports` 和 `excludes`，防止两种发行包的验证码模型、资源与依赖名单漂移。

在仓库根目录使用已安装构建依赖的 Python 运行：

```sh
python -m PyInstaller --clean --noconfirm chaoxing.spec
python -m PyInstaller --clean --noconfirm chaoxing-backend.spec
```

- 独立版：onefile、无控制台、包含 `pystray`。
- 桌面后端：onedir、保留管道控制台、排除 `pystray`。
- 两种包都只带 ddddocr 1.6.1 的 `common_old.onnx`、`charsets.py` 和元数据。
- `fav.jpg` 只打包一次到资源根目录，供独立版托盘使用。

目录刻意不设 `__init__.py`，避免遮蔽构建工具依赖的第三方 `packaging` 包。spec 通过 `SPECPATH/packaging` 导入 `spec_common`。

测试入口：`python -m unittest tests.test_packaging_specs`。实际 EXE 仍需运行 `--check-captcha-ocr`，不能仅凭配置测试声称打包成功。

参见 [设计说明](DESIGN.md)。
