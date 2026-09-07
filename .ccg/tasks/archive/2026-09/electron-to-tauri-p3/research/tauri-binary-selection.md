# Tauri CLI 2.11.4 辅助二进制排除规则

真实首轮 NSIS 包含 fake-backend.exe，verify-nsis 失败并阻止后续交付标记；不是只由配置注释推断。

核对固定版本上游源码：
https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-cli/src/interface/rust.rs

通过 firecrawl scrape 获取 raw 源码（原始缓存 .firecrawl/tauri-cli-2.11.4-rust.md 不入 Git）。BinarySettings::required_features_enabled（约694行）要求每个 required-features 出现在 options.features；get_binaries（约952行）记录 disabled_bins，且 src/bin 扫描不会再次加入匹配的已禁用二进制。上游自身有 get_binaries_ignores_src_bin_with_disabled_required_features 测试。

本项目为 fake-backend 设置 required-features=["test-support"]。默认特性包含 test-support，以保留既有 cargo test 接口；P3 正式构建关闭默认特性，Tauri 自动启用其正式 custom-protocol。NSIS/ZIP 内容验证仍要求只含业务宿主/资源，没有测试程序。
