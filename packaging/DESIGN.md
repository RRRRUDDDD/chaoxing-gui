# 共用打包配置设计

## 目标与边界

只合并两种发行形态共有的 Analysis 输入；EXE/COLLECT 的形态、控制台、名称和托盘差异保留在各自 spec 中。不改变 OCR 推理代码或下载模型。

## 设计决策

- PyInstaller 6.21 提供的 `SPECPATH` 用于找到共用模块；模块相对自身路径解析项目资源，不依赖调用进程的工作目录。
- `hiddenimports + ["pystray"]` 与 `excludes + ["pystray"]` 创建新列表，不修改共用列表。
- 只用发行元数据寻找锁定的 ddddocr 资源，不执行上游包入口；真实引擎自检继续校验模型和字符表的哈希。
- 明确排除未使用的题库 SDK、PaddleOCR 和 OpenCV，防止构建机已安装的包被隐式带入。

## 验证

离线测试以伪造 Analysis/PYZ/EXE/COLLECT 执行两份实际 spec，检查资源唯一性、模型版本拒绝和托盘差异。发布构建还必须检查实际 PYZ/资源清单，并对两个实际 EXE 执行 OCR 自检与进程生命周期 smoke。
