use std::{fs, io, path::PathBuf};

use serde::{Deserialize, Serialize};

use crate::config_io;

const SETTINGS_FILE: &str = "appearance.json";

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ThemeId {
    Aqua,
    #[default]
    Wired,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AppearanceSettings {
    #[serde(default)]
    pub theme: ThemeId,
}

impl Default for AppearanceSettings {
    fn default() -> Self {
        Self {
            theme: ThemeId::Wired,
        }
    }
}

#[derive(Clone)]
pub struct AppearanceStore {
    path: PathBuf,
}

impl AppearanceStore {
    pub fn new(config_dir: PathBuf) -> Self {
        Self {
            path: config_dir.join(SETTINGS_FILE),
        }
    }

    pub fn get(&self) -> Result<AppearanceSettings, String> {
        match fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map_err(|error| format!("外观设置文件已损坏：{error}")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                Ok(AppearanceSettings::default())
            }
            Err(error) => Err(format!("读取外观设置失败：{error}")),
        }
    }

    pub fn save(&self, input: AppearanceSettings) -> Result<AppearanceSettings, String> {
        let bytes = serde_json::to_vec_pretty(&input)
            .map_err(|error| format!("序列化外观设置失败：{error}"))?;
        config_io::write_bytes(&self.path, &bytes)
            .map_err(|error| format!("写入外观设置失败：{error}"))?;
        Ok(input)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_to_wired_and_persists_theme() {
        let directory =
            std::env::temp_dir().join(format!("amadeus-appearance-test-{}", uuid::Uuid::new_v4()));
        let store = AppearanceStore::new(directory.clone());

        assert_eq!(
            store.get().expect("default appearance").theme,
            ThemeId::Wired
        );
        store
            .save(AppearanceSettings {
                theme: ThemeId::Aqua,
            })
            .expect("save appearance");
        assert_eq!(store.get().expect("stored appearance").theme, ThemeId::Aqua);

        let _ = fs::remove_dir_all(directory);
    }
}
