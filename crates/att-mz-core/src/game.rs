//! RPG Maker MZ 游戏目录基础校验。
//!
//! 这里先承载注册和诊断命令需要的轻量检查：路径、`package.json` 标题、
//! 标准数据文件和插件配置文件。完整文本提取会在后续迁移阶段放入独立模块。

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::error::{AttMzError, Result};

const PACKAGE_FILE_NAME: &str = "package.json";
const DATA_DIRECTORY_NAME: &str = "data";
const JS_DIRECTORY_NAME: &str = "js";
const PLUGINS_FILE_NAME: &str = "plugins.js";
const REQUIRED_DATA_FILES: &[&str] = &["System.json", "CommonEvents.json", "Troops.json"];

/// 解析并校验游戏根目录路径。
pub fn resolve_game_directory(game_path: impl AsRef<Path>) -> Result<PathBuf> {
    let raw_path = game_path.as_ref();
    let resolved_path = raw_path
        .canonicalize()
        .map_err(|error| AttMzError::io(format!("解析游戏目录 {}", raw_path.display()), error))?;
    if !resolved_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "游戏目录",
            path: resolved_path,
        });
    }
    if !resolved_path.is_dir() {
        return Err(AttMzError::NotDirectory {
            kind: "游戏路径",
            path: resolved_path,
        });
    }
    Ok(resolved_path)
}

/// 从游戏目录的 `package.json` 读取窗口标题。
pub fn read_game_title(game_path: impl AsRef<Path>) -> Result<String> {
    let package_path = game_path.as_ref().join(PACKAGE_FILE_NAME);
    if !package_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "package.json",
            path: package_path,
        });
    }
    let raw_text = fs::read_to_string(&package_path)
        .map_err(|error| AttMzError::io(format!("读取 {}", package_path.display()), error))?;
    let package_data: Value = serde_json::from_str(raw_text.trim_start_matches('\u{feff}'))
        .map_err(|source| AttMzError::Json {
            context: package_path.display().to_string(),
            source,
        })?;
    let title = package_data
        .get("window")
        .and_then(Value::as_object)
        .and_then(|window| window.get("title"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .ok_or_else(|| {
            AttMzError::InvalidGame(format!(
                "package.json 缺少有效的 window.title: {}",
                package_path.display()
            ))
        })?;
    Ok(title.to_string())
}

/// 校验注册和诊断命令依赖的核心游戏文件。
pub fn validate_game_directory(game_path: impl AsRef<Path>) -> Result<String> {
    let game_path = resolve_game_directory(game_path)?;
    let game_title = read_game_title(&game_path)?;
    let data_dir = game_path.join(DATA_DIRECTORY_NAME);
    if !data_dir.exists() {
        return Err(AttMzError::MissingPath {
            kind: "数据目录",
            path: data_dir,
        });
    }
    if !data_dir.is_dir() {
        return Err(AttMzError::NotDirectory {
            kind: "数据目录",
            path: data_dir,
        });
    }

    for file_name in REQUIRED_DATA_FILES {
        let file_path = data_dir.join(file_name);
        validate_json_file(&file_path)?;
    }

    let plugins_path = game_path.join(JS_DIRECTORY_NAME).join(PLUGINS_FILE_NAME);
    if !plugins_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "插件配置文件",
            path: plugins_path,
        });
    }
    Ok(game_title)
}

fn validate_json_file(path: &Path) -> Result<()> {
    if !path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "核心 data 文件",
            path: path.to_path_buf(),
        });
    }
    let raw_text = fs::read_to_string(path)
        .map_err(|error| AttMzError::io(format!("读取 {}", path.display()), error))?;
    let _: Value =
        serde_json::from_str(raw_text.trim_start_matches('\u{feff}')).map_err(|source| {
            AttMzError::Json {
                context: path.display().to_string(),
                source,
            }
        })?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn read_game_title_uses_package_window_title() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        fs::write(
            temp.path().join(PACKAGE_FILE_NAME),
            r#"{"window":{"title":"テストゲーム"}}"#,
        )
        .expect("package.json 应写入成功");
        let title = read_game_title(temp.path()).expect("标题应读取成功");
        assert_eq!(title, "テストゲーム");
    }
}
