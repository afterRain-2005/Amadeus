use std::time::Duration;

use serde::{Deserialize, Serialize};

const LATEST_API: &str = "https://api.github.com/repos/afterRain-2005/Amadeus/releases/latest";
const RELEASE_PAGE: &str = "https://github.com/afterRain-2005/Amadeus/releases/latest";
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub current_version: String,
    pub latest_version: Option<String>,
    pub update_available: bool,
    pub release_url: String,
    pub message: String,
}

#[derive(Deserialize)]
struct GitHubRelease {
    tag_name: String,
    draft: bool,
    prerelease: bool,
}

pub async fn check(current_version: String) -> UpdateInfo {
    match fetch_release().await {
        Ok(release) => {
            let latest = normalize_version(&release.tag_name);
            let comparison = compare_versions(&latest, &current_version);
            let update_available = comparison.is_some_and(|order| order.is_gt());
            let message = match comparison {
                Some(std::cmp::Ordering::Greater) => {
                    format!("发现新版本 {latest}，当前版本 {current_version}")
                }
                Some(std::cmp::Ordering::Less) => {
                    format!("当前版本 {current_version} 高于公开版本 {latest}")
                }
                _ => format!("当前已是最新版本 {current_version}"),
            };
            UpdateInfo {
                current_version,
                latest_version: Some(latest),
                update_available,
                release_url: RELEASE_PAGE.to_owned(),
                message,
            }
        }
        Err(error) => UpdateInfo {
            current_version,
            latest_version: None,
            update_available: false,
            release_url: RELEASE_PAGE.to_owned(),
            message: format!("暂时无法检查版本：{error}"),
        },
    }
}

async fn fetch_release() -> Result<GitHubRelease, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::limited(2))
        .user_agent(concat!("Amadeus/", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|error| format!("创建版本检查请求失败：{error}"))?;
    let response = client
        .get(LATEST_API)
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|error| format!("网络连接失败：{error}"))?;
    if !response.status().is_success() {
        return Err(format!("GitHub 返回 HTTP {}", response.status().as_u16()));
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
    {
        return Err("版本响应过大".to_owned());
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|error| format!("读取版本响应失败：{error}"))?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err("版本响应过大".to_owned());
    }
    let release: GitHubRelease =
        serde_json::from_slice(&bytes).map_err(|error| format!("版本响应格式无效：{error}"))?;
    if release.draft || release.prerelease {
        return Err("最新条目不是稳定版本".to_owned());
    }
    if parse_version(&release.tag_name).is_none() {
        return Err("远程版本号格式无效".to_owned());
    }
    Ok(release)
}

fn normalize_version(value: &str) -> String {
    value.trim().trim_start_matches(['v', 'V']).to_owned()
}

fn parse_version(value: &str) -> Option<(u64, u64, u64)> {
    let normalized = normalize_version(value);
    let core = normalized.split(['-', '+']).next()?;
    let mut parts = core.split('.');
    let version = (
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
    );
    parts.next().is_none().then_some(version)
}

fn compare_versions(left: &str, right: &str) -> Option<std::cmp::Ordering> {
    Some(parse_version(left)?.cmp(&parse_version(right)?))
}

pub fn open_release_page() -> Result<(), String> {
    tauri_plugin_opener::open_url(RELEASE_PAGE, None::<&str>)
        .map_err(|error| format!("打开发布页面失败：{error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_release_tags_and_compares_numerically() {
        assert_eq!(parse_version("v0.10.0"), Some((0, 10, 0)));
        assert_eq!(
            compare_versions("0.10.0", "0.9.1"),
            Some(std::cmp::Ordering::Greater)
        );
        assert!(parse_version("latest").is_none());
    }
}
