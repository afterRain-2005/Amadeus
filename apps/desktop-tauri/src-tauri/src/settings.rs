use std::{fs, io, path::PathBuf};

use reqwest::Url;
use serde::{Deserialize, Serialize};
use zeroize::{Zeroize, Zeroizing};

use crate::config_io;

const CREDENTIAL_TARGET: &str = "com.wweiyi.amadeus.next/model-api-key";
const SETTINGS_FILE: &str = "model.json";
const DEFAULT_ENDPOINT: &str = "https://api.deepseek.com/v1";
const DEFAULT_MODEL: &str = "deepseek-chat";

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveModelSettings {
    pub endpoint: String,
    pub model: String,
    /// `None` preserves the current key; an empty value removes it.
    pub api_key: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicModelSettings {
    pub endpoint: String,
    pub model: String,
    pub has_api_key: bool,
    pub ready: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ModelMetadata {
    endpoint: String,
    model: String,
}

impl Default for ModelMetadata {
    fn default() -> Self {
        Self {
            endpoint: DEFAULT_ENDPOINT.to_owned(),
            model: DEFAULT_MODEL.to_owned(),
        }
    }
}

pub struct ChatCredentials {
    pub chat_url: Url,
    pub model: String,
    pub api_key: Option<Zeroizing<String>>,
}

#[derive(Clone)]
pub struct SettingsStore {
    path: PathBuf,
}

impl SettingsStore {
    pub fn new(config_dir: PathBuf) -> Self {
        Self {
            path: config_dir.join(SETTINGS_FILE),
        }
    }

    pub fn public(&self) -> Result<PublicModelSettings, String> {
        let metadata = self.load_metadata()?;
        let endpoint = validate_endpoint(&metadata.endpoint)?;
        validate_model(&metadata.model)?;
        let has_api_key = credential::read(CREDENTIAL_TARGET)?.is_some();
        let ready = has_api_key || endpoint_is_loopback(&endpoint);
        Ok(PublicModelSettings {
            endpoint: metadata.endpoint,
            model: metadata.model,
            has_api_key,
            ready,
        })
    }

    pub fn save(&self, mut input: SaveModelSettings) -> Result<PublicModelSettings, String> {
        let endpoint = validate_endpoint(&input.endpoint)?;
        validate_model(&input.model)?;
        input.endpoint = endpoint.as_str().trim_end_matches('/').to_owned();
        input.model = input.model.trim().to_owned();

        update_secret(CREDENTIAL_TARGET, input.api_key.take())?;

        let metadata = ModelMetadata {
            endpoint: input.endpoint,
            model: input.model,
        };
        let json = serde_json::to_vec_pretty(&metadata)
            .map_err(|error| format!("serialize model settings: {error}"))?;
        config_io::write_bytes(&self.path, &json)
            .map_err(|error| format!("write model settings: {error}"))?;
        self.public()
    }

    pub fn credentials(&self) -> Result<ChatCredentials, String> {
        let metadata = self.load_metadata()?;
        let endpoint = validate_endpoint(&metadata.endpoint)?;
        validate_model(&metadata.model)?;
        let api_key = credential::read(CREDENTIAL_TARGET)?;
        if api_key.is_none() && !endpoint_is_loopback(&endpoint) {
            return Err("请先在设置中保存模型 API Key".to_owned());
        }
        let chat_url = Url::parse(&format!(
            "{}/chat/completions",
            endpoint.as_str().trim_end_matches('/')
        ))
        .map_err(|error| format!("build chat URL: {error}"))?;
        Ok(ChatCredentials {
            chat_url,
            model: metadata.model,
            api_key,
        })
    }

    fn load_metadata(&self) -> Result<ModelMetadata, String> {
        match fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map_err(|error| format!("模型设置文件已损坏：{error}")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(ModelMetadata::default()),
            Err(error) => Err(format!("读取模型设置失败：{error}")),
        }
    }
}

fn validate_model(model: &str) -> Result<(), String> {
    let model = model.trim();
    if model.is_empty() || model.len() > 128 || model.chars().any(char::is_control) {
        return Err("模型名称必须是 1–128 个可见字符".to_owned());
    }
    Ok(())
}

pub(crate) fn validate_endpoint(endpoint: &str) -> Result<Url, String> {
    let endpoint = endpoint.trim();
    if endpoint.is_empty() || endpoint.len() > 2048 {
        return Err("模型 Endpoint 长度无效".to_owned());
    }
    let mut url = Url::parse(endpoint).map_err(|_| "模型 Endpoint 不是有效 URL".to_owned())?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err("Endpoint 不得包含用户名或密码".to_owned());
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err("Endpoint 不得包含查询参数或片段".to_owned());
    }
    let secure = url.scheme() == "https";
    if !(secure || url.scheme() == "http" && endpoint_is_loopback(&url)) {
        return Err("远程模型 Endpoint 必须使用 HTTPS；HTTP 仅允许本机地址".to_owned());
    }
    let normalized_path = url.path().trim_end_matches('/').to_owned();
    url.set_path(&normalized_path);
    Ok(url)
}

pub(crate) fn endpoint_is_loopback(url: &Url) -> bool {
    match url.host_str() {
        Some("localhost") => true,
        Some(host) => host
            .parse::<std::net::IpAddr>()
            .is_ok_and(|address| address.is_loopback()),
        None => false,
    }
}

pub(crate) fn read_secret(target: &str) -> Result<Option<Zeroizing<String>>, String> {
    credential::read(target)
}

pub(crate) fn update_secret(target: &str, value: Option<String>) -> Result<(), String> {
    let Some(mut secret) = value else {
        return Ok(());
    };
    let mut trimmed = secret.trim().to_owned();
    secret.zeroize();
    let result = if trimmed.is_empty() {
        credential::delete(target)
    } else {
        credential::write(target, trimmed.as_bytes())
    };
    trimmed.zeroize();
    result
}

#[cfg(windows)]
mod credential {
    use std::{ffi::c_void, io, os::windows::ffi::OsStrExt, ptr, slice};

    use windows_sys::Win32::{
        Foundation::{ERROR_NOT_FOUND, GetLastError},
        Security::Credentials::{
            CRED_MAX_CREDENTIAL_BLOB_SIZE, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
            CREDENTIALW, CredDeleteW, CredFree, CredReadW, CredWriteW,
        },
    };
    use zeroize::{Zeroize, Zeroizing};

    fn wide(value: &str) -> Vec<u16> {
        std::ffi::OsStr::new(value)
            .encode_wide()
            .chain(Some(0))
            .collect()
    }

    pub fn write(target: &str, secret: &[u8]) -> Result<(), String> {
        if secret.len() > CRED_MAX_CREDENTIAL_BLOB_SIZE as usize {
            return Err("API Key 太长，无法保存到 Windows Credential Manager".to_owned());
        }
        let mut target = wide(target);
        let mut username = wide("Amadeus");
        let mut blob = secret.to_vec();
        let credential = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: target.as_mut_ptr(),
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_mut_ptr(),
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            UserName: username.as_mut_ptr(),
            ..CREDENTIALW::default()
        };
        let written = unsafe { CredWriteW(&raw const credential, 0) };
        blob.zeroize();
        if written == 0 {
            return Err(format!(
                "保存 API Key 到 Windows Credential Manager 失败：{}",
                io::Error::last_os_error()
            ));
        }
        Ok(())
    }

    pub fn read(target: &str) -> Result<Option<Zeroizing<String>>, String> {
        let target = wide(target);
        let mut raw: *mut CREDENTIALW = ptr::null_mut();
        let found = unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) };
        if found == 0 {
            let code = unsafe { GetLastError() };
            if code == ERROR_NOT_FOUND {
                return Ok(None);
            }
            return Err(format!(
                "从 Windows Credential Manager 读取 API Key 失败：{}",
                io::Error::from_raw_os_error(code as i32)
            ));
        }

        let result = unsafe {
            let credential = &*raw;
            let bytes = slice::from_raw_parts(
                credential.CredentialBlob,
                credential.CredentialBlobSize as usize,
            );
            let bytes = Zeroizing::new(bytes.to_vec());
            std::str::from_utf8(bytes.as_slice()).map(|value| Zeroizing::new(value.to_owned()))
        };
        unsafe { CredFree(raw.cast::<c_void>()) };
        result
            .map(Some)
            .map_err(|_| "Windows Credential Manager 中的 API Key 编码无效".to_owned())
    }

    pub fn delete(target: &str) -> Result<(), String> {
        let target = wide(target);
        let deleted = unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) };
        if deleted == 0 {
            let code = unsafe { GetLastError() };
            if code != ERROR_NOT_FOUND {
                return Err(format!(
                    "从 Windows Credential Manager 删除 API Key 失败：{}",
                    io::Error::from_raw_os_error(code as i32)
                ));
            }
        }
        Ok(())
    }
}

#[cfg(not(windows))]
mod credential {
    use zeroize::Zeroizing;

    pub fn write(_target: &str, _secret: &[u8]) -> Result<(), String> {
        Err("secure credential storage is currently implemented only on Windows".to_owned())
    }

    pub fn read(_target: &str) -> Result<Option<Zeroizing<String>>, String> {
        Ok(None)
    }

    pub fn delete(_target: &str) -> Result<(), String> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_insecure_remote_endpoint() {
        assert!(validate_endpoint("http://example.com/v1").is_err());
    }

    #[test]
    fn accepts_https_and_loopback_http() {
        assert!(validate_endpoint("https://api.example.com/v1/").is_ok());
        assert!(validate_endpoint("http://127.0.0.1:11434/v1").is_ok());
        assert!(validate_endpoint("http://localhost:11434/v1").is_ok());
    }

    #[test]
    fn rejects_credentials_in_endpoint() {
        assert!(validate_endpoint("https://user:secret@example.com/v1").is_err());
    }
}
