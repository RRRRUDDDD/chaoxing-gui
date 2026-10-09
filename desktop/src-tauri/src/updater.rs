//! Auto-update: fetch the latest release metadata from GitHub and compare the
//! version against the compiled-in app version. The install path downloads
//! the NSIS installer to a temp file and relaunches it with `/UPDATE /P` for
//! a silent in-place upgrade.
//!
//! Uses the GitHub REST API directly. The GitHub API is reachable from
//! Mainland China via api.github.com (the download redirector at
//! objects.githubusercontent.com is the main concern, and the NSIS installer
//! itself is fetched by the system browser via ShellExecute in install_update).

use serde::Serialize;
use std::path::PathBuf;

/// The current application version, compiled from Cargo.toml.
const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");

/// GitHub API endpoint for the latest release.
const GITHUB_API_LATEST: &str =
    "https://api.github.com/repos/RRRRUDDDD/chaoxing-gui/releases/latest";

/// A simple semver comparison: parse "major.minor.patch" and compare numerically.
/// Pre-release / build metadata segments are ignored for simplicity.
fn parse_semver(version: &str) -> Option<(u32, u32, u32)> {
    let clean = version.strip_prefix('v').unwrap_or(version);
    let mut parts = clean.split('.');
    let major: u32 = parts.next()?.parse().ok()?;
    let minor: u32 = parts.next()?.parse().ok()?;
    let patch_str = parts.next().unwrap_or("0");
    let patch_raw = patch_str
        .split_once(['-', '+'])
        .map(|(p, _)| p)
        .unwrap_or(patch_str);
    let patch: u32 = patch_raw.parse().ok()?;
    Some((major, minor, patch))
}

/// Returns true if `latest` is strictly newer than `current`.
fn is_newer(latest: &str, current: &str) -> bool {
    let current = parse_semver(current).unwrap_or((0, 0, 0));
    let latest = parse_semver(latest).unwrap_or((0, 0, 0));
    latest > current
}

/// Strip a leading 'v' (e.g. "v1.3.0" -> "1.3.0") used by GitHub release tags.
fn normalize_tag(tag: &str) -> &str {
    tag.strip_prefix('v').unwrap_or(tag)
}

/// Shape of the GitHub releases API response, only the fields we use.
#[derive(serde::Deserialize)]
struct GitHubRelease {
    #[serde(rename = "tag_name")]
    tag: String,
    #[serde(rename = "prerelease")]
    _prerelease: bool,
    #[serde(rename = "draft")]
    _draft: bool,
    body: String,
    assets: Vec<GitHubAsset>,
}

#[derive(serde::Deserialize)]
struct GitHubAsset {
    name: String,
    #[serde(rename = "browser_download_url")]
    browser_download_url: String,
    size: u64,
}

/// What the frontend receives from `check_update`.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    /// The latest version tag without leading 'v', e.g. "1.4.0".
    pub version: String,
    /// The release notes / changelog body.
    pub notes: String,
    /// Download URL for the NSIS installer (chaoxing-gui-setup-*.exe).
    pub download_url: String,
    /// File size in bytes.
    pub size: u64,
}

/// Error kind surfaced to the frontend.
#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum UpdateError {
    /// Could not reach GitHub or parse the response.
    Network { reason: String },
    /// The release was found but no installer asset was present.
    NoInstaller { version: String },
    /// The download or relaunch failed.
    Install { reason: String },
}

/// Fetch the latest release info from GitHub. Returns `Ok(None)` when the
/// current version is already up-to-date.
pub fn fetch_latest(state: &crate::BackendState) -> Result<Option<UpdateInfo>, String> {
    let response = ureq::get(GITHUB_API_LATEST)
        .timeout(std::time::Duration::from_secs(10))
        .call()
        .map_err(|e| format!("无法连接到更新服务器: {e}"))?;

    if response.status() != 200 {
        return Err(format!("更新服务器返回错误: HTTP {}", response.status()));
    }

    let body = response
        .into_string()
        .map_err(|e| format!("读取更新响应失败: {e}"))?;
    let release: GitHubRelease =
        serde_json::from_str(&body).map_err(|e| format!("更新响应格式错误: {e}"))?;

    let latest_version = normalize_tag(&release.tag).to_string();
    state.host_log(&format!("[updater] latest release: {latest_version}"));

    // Already up-to-date.
    if !is_newer(&latest_version, CURRENT_VERSION) {
        state.host_log(&format!(
            "[updater] current version {CURRENT_VERSION} is up-to-date"
        ));
        return Ok(None);
    }

    // Find the NSIS setup installer asset.
    let installer = release
        .assets
        .iter()
        .find(|asset| asset.name.starts_with("chaoxing-gui-setup-") && asset.name.ends_with(".exe"))
        .ok_or_else(|| {
            state.host_log(&format!(
                "[updater] release {latest_version} has no setup installer asset"
            ));
            UpdateError::NoInstaller {
                version: latest_version.clone(),
            }
        })
        .map_err(|e| format!("更新资产缺失: {e:?}"))?;

    Ok(Some(UpdateInfo {
        version: latest_version,
        notes: release.body,
        download_url: installer.browser_download_url.clone(),
        size: installer.size,
    }))
}

/// Download the installer to a temp file and relaunch the NSIS package with
/// `/UPDATE /P /R` (update mode, passive, restart app).
pub fn perform_install(
    state: &crate::BackendState,
    download_url: &str,
    version: &str,
) -> Result<(), String> {
    state.host_log(&format!(
        "[updater] downloading installer from {download_url}"
    ));

    // Create a temp file for the installer.
    let temp_dir = std::env::temp_dir();
    let instance = state
        .instance_id
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let temp_path: PathBuf = temp_dir.join(format!("chaoxing-gui-setup-{version}-{instance}.exe"));

    // Download the installer bytes.
    let resp = ureq::get(download_url)
        .timeout(std::time::Duration::from_secs(30))
        .call()
        .map_err(|e| format!("下载更新文件失败: {e}"))?;

    if resp.status() != 200 {
        return Err(format!("下载更新文件失败: HTTP {}", resp.status()));
    }

    let mut file =
        std::fs::File::create(&temp_path).map_err(|e| format!("无法创建临时文件: {e}"))?;

    let mut reader = resp.into_reader();
    std::io::copy(&mut reader, &mut file).map_err(|e| format!("写入下载文件失败: {e}"))?;
    drop(file);
    state.host_log(&format!(
        "[updater] installer downloaded to {}",
        temp_path.display()
    ));

    let installer_str = temp_path
        .to_str()
        .ok_or_else(|| String::from("临时路径包含非法字符"))?;

    state.host_log(&format!("[updater] launching installer: {installer_str}"));

    // Launch the NSIS installer with /UPDATE /P /R
    // /UPDATE  – suppress the reinstall page, proceed in-place upgrade
    // /P       – passive mode (progress bar, no prompts)
    // /R       – restart the app after install
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows::Win32::System::Threading::CREATE_NO_WINDOW;

        let result = std::process::Command::new(installer_str)
            .args(["/UPDATE", "/P", "/R"])
            .creation_flags(CREATE_NO_WINDOW.0)
            .spawn();

        match result {
            Ok(_) => {
                state.host_log("[updater] installer launched, exiting host");
                Ok(())
            }
            Err(e) => Err(format!("无法启动安装程序: {e}")),
        }
    }
    #[cfg(not(windows))]
    {
        let _ = installer_str;
        Err(String::from("自动更新仅在 Windows 平台上可用"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_semver_extracts_triple() {
        assert_eq!(parse_semver("1.3.0"), Some((1, 3, 0)));
        assert_eq!(parse_semver("v1.3.0"), Some((1, 3, 0)));
        assert_eq!(parse_semver("2.0.1"), Some((2, 0, 1)));
        assert_eq!(parse_semver("1.3.0-beta.1"), Some((1, 3, 0)));
        assert_eq!(parse_semver("invalid"), None);
        assert_eq!(parse_semver(""), None);
    }

    #[test]
    fn is_newer_compares_semver() {
        assert!(is_newer("1.3.1", "1.3.0"));
        assert!(is_newer("1.4.0", "1.3.0"));
        assert!(is_newer("2.0.0", "1.9.9"));
        assert!(!is_newer("1.3.0", "1.3.0"));
        assert!(!is_newer("1.2.9", "1.3.0"));
        assert!(!is_newer("1.3.0-beta.1", "1.3.0"));
    }

    #[test]
    fn normalize_tag_strips_v_prefix() {
        assert_eq!(normalize_tag("v1.3.0"), "1.3.0");
        assert_eq!(normalize_tag("1.3.0"), "1.3.0");
    }
}
