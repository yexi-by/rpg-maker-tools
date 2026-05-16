//! RPG Maker MZ 数据读取与外部规则输入导出。
//!
//! 本模块只处理游戏目录中的标准文件结构，不读取项目数据库，也不调用模型。
//! 这些能力是插件规则、事件指令规则和后续正文提取迁移的共同基础。

use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use regex::Regex;
use serde_json::{Map, Value};

use crate::error::{AttMzError, Result};

const DATA_DIRECTORY_NAME: &str = "data";
const DATA_ORIGIN_DIRECTORY_NAME: &str = "data_origin";
const JS_DIRECTORY_NAME: &str = "js";
const PLUGINS_FILE_NAME: &str = "plugins.js";
const PLUGINS_ORIGIN_FILE_NAME: &str = "plugins_origin.js";
const COMMON_EVENTS_FILE_NAME: &str = "CommonEvents.json";
const TROOPS_FILE_NAME: &str = "Troops.json";

/// 当前游戏中一条事件指令的稳定快照。
#[derive(Debug, Clone, PartialEq)]
pub struct EventCommandSnapshot {
    /// 可用于正文定位前缀的事件指令路径。
    pub location_path: String,
    /// 地图显示名；公共事件和敌群使用文件名。
    pub display_name: String,
    /// RPG Maker MZ 事件指令编码。
    pub code: i64,
    /// 原始 `parameters` 数组。
    pub parameters: Value,
}

/// 把当前游戏的 `$plugins` 数组导出为外部 Agent 可直接读取的 JSON 文件。
///
/// 如果游戏已经存在原件留档，函数会优先读取 `js/plugins_origin.js`，保持和
/// Python 版本相同的“基于原始游戏文件分析规则”行为。
pub fn export_plugins_json_file(game_path: &Path, output_path: &Path) -> Result<()> {
    let source_paths = resolve_game_source_paths(game_path)?;
    let plugins = parse_plugins_js_file(&source_paths.plugins_path)?;
    write_json_file(output_path, &plugins)
}

/// 读取当前游戏的 `$plugins` 数组。
///
/// 该函数和导出命令使用同一套来源解析逻辑：若存在原件插件留档，则优先读取
/// `js/plugins_origin.js`，确保规则校验始终针对原始插件结构。
pub fn read_plugins_json(game_path: &Path) -> Result<Vec<Value>> {
    let source_paths = resolve_game_source_paths(game_path)?;
    let plugins = parse_plugins_js_file(&source_paths.plugins_path)?;
    let Some(array) = plugins.as_array() else {
        return Err(AttMzError::InvalidGame(
            "plugins.js 中的 $plugins 必须是数组".to_string(),
        ));
    };
    Ok(array.clone())
}

/// 解析事件指令导出命令的有效编码集合。
///
/// CLI 显式传入编码时覆盖配置数组；否则必须使用配置中的默认编码。空集合会
/// 直接报错，避免生成看似成功但没有业务价值的空文件。
pub fn resolve_event_command_codes(
    command_codes: Option<Vec<i64>>,
    default_command_codes: Option<Vec<i64>>,
) -> Result<BTreeSet<i64>> {
    let raw_codes = match command_codes {
        Some(codes) => codes,
        None => default_command_codes.ok_or_else(|| {
            AttMzError::InvalidConfig("未传入 CLI 编码时必须提供配置文件默认编码数组".to_string())
        })?,
    };
    let codes: BTreeSet<i64> = raw_codes.into_iter().collect();
    if codes.is_empty() {
        return Err(AttMzError::InvalidConfig(
            "事件指令导出编码不能为空".to_string(),
        ));
    }
    Ok(codes)
}

/// 把指定事件指令编码的参数样本导出为 JSON 文件。
///
/// 输出结构为以事件指令编码字符串为键的对象，值为去重后的 `parameters`
/// 数组列表。去重时使用稳定 JSON 表达，避免同一参数对象因键顺序不同重复出现。
pub fn export_event_commands_json_file(
    game_path: &Path,
    output_path: &Path,
    command_codes: &BTreeSet<i64>,
) -> Result<usize> {
    let source_paths = resolve_game_source_paths(game_path)?;
    let mut samples_by_code: Map<String, Value> = Map::new();
    for code in command_codes {
        samples_by_code.insert(code.to_string(), Value::Array(Vec::new()));
    }
    let mut seen_samples = HashSet::new();
    let mut command_count = 0usize;

    for file_name in standard_event_file_names(&source_paths.data_dir)? {
        let source_file = resolve_data_source_file(&source_paths.origin_data_dir, &file_name);
        let value = read_json_file(&source_file)?;
        collect_event_command_samples(
            &value,
            &file_name,
            command_codes,
            &mut samples_by_code,
            &mut seen_samples,
            &mut command_count,
        )?;
    }

    write_json_file(output_path, &Value::Object(samples_by_code))?;
    Ok(command_count)
}

/// 读取当前游戏全部可遍历事件指令。
///
/// 路径格式保持和 Python 版本一致：地图为
/// `MapXXX.json/<事件ID>/<页面索引>/<指令索引>`，公共事件为
/// `CommonEvents.json/<公共事件ID>/<指令索引>`，敌群为
/// `Troops.json/<敌群ID>/<页面索引>/<指令索引>`。
pub fn read_event_command_snapshots(game_path: &Path) -> Result<Vec<EventCommandSnapshot>> {
    let source_paths = resolve_game_source_paths(game_path)?;
    let mut snapshots = Vec::new();
    for file_name in standard_event_file_names(&source_paths.data_dir)? {
        let source_file = resolve_data_source_file(&source_paths.origin_data_dir, &file_name);
        let value = read_json_file(&source_file)?;
        collect_event_command_snapshots(&value, &file_name, &mut snapshots)?;
    }
    Ok(snapshots)
}

/// 读取当前游戏 `data/*.json` 文件。
///
/// 若存在 `data_origin` 留档，同名文件优先读取留档；返回值按文件名排序，便于
/// 候选导出和规则校验生成稳定报告。
pub fn read_data_json_files(game_path: &Path) -> Result<BTreeMap<String, Value>> {
    let source_paths = resolve_game_source_paths(game_path)?;
    let mut files = BTreeMap::new();
    for entry in fs::read_dir(&source_paths.data_dir).map_err(|error| {
        AttMzError::io(
            format!("扫描数据目录 {}", source_paths.data_dir.display()),
            error,
        )
    })? {
        let entry = entry.map_err(|error| AttMzError::io("读取数据目录项", error))?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(file_name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !file_name.ends_with(".json") {
            continue;
        }
        let source_file = resolve_data_source_file(&source_paths.origin_data_dir, file_name);
        files.insert(file_name.to_string(), read_json_file(&source_file)?);
    }
    Ok(files)
}

fn resolve_game_source_paths(game_root: &Path) -> Result<GameSourcePaths> {
    let active_data_dir = game_root.join(DATA_DIRECTORY_NAME);
    let active_plugins_path = game_root.join(JS_DIRECTORY_NAME).join(PLUGINS_FILE_NAME);
    let origin_data_dir = game_root.join(DATA_ORIGIN_DIRECTORY_NAME);
    let origin_plugins_path = game_root
        .join(JS_DIRECTORY_NAME)
        .join(PLUGINS_ORIGIN_FILE_NAME);
    let plugins_path = if origin_plugins_path.exists() {
        origin_plugins_path
    } else {
        active_plugins_path
    };

    if !active_data_dir.exists() {
        return Err(AttMzError::MissingPath {
            kind: "数据目录",
            path: active_data_dir,
        });
    }
    if !active_data_dir.is_dir() {
        return Err(AttMzError::NotDirectory {
            kind: "数据目录",
            path: active_data_dir,
        });
    }
    if origin_data_dir.exists() && !origin_data_dir.is_dir() {
        return Err(AttMzError::NotDirectory {
            kind: "原件数据留档",
            path: origin_data_dir,
        });
    }
    if !plugins_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "插件配置文件",
            path: plugins_path,
        });
    }

    Ok(GameSourcePaths {
        data_dir: active_data_dir,
        origin_data_dir,
        plugins_path,
    })
}

fn standard_event_file_names(data_dir: &Path) -> Result<Vec<String>> {
    let mut file_names = Vec::new();
    for entry in fs::read_dir(data_dir)
        .map_err(|error| AttMzError::io(format!("扫描数据目录 {}", data_dir.display()), error))?
    {
        let entry = entry.map_err(|error| AttMzError::io("读取数据目录项", error))?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(file_name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if file_name == COMMON_EVENTS_FILE_NAME
            || file_name == TROOPS_FILE_NAME
            || is_map_file_name(file_name)
        {
            file_names.push(file_name.to_string());
        }
    }
    file_names.sort();
    Ok(file_names)
}

fn is_map_file_name(file_name: &str) -> bool {
    let Some(number_part) = file_name
        .strip_prefix("Map")
        .and_then(|value| value.strip_suffix(".json"))
    else {
        return false;
    };
    !number_part.is_empty()
        && number_part
            .chars()
            .all(|char_value| char_value.is_ascii_digit())
}

fn resolve_data_source_file(origin_data_dir: &Path, file_name: &str) -> PathBuf {
    let origin_file = origin_data_dir.join(file_name);
    if origin_file.exists() {
        origin_file
    } else {
        origin_data_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(DATA_DIRECTORY_NAME)
            .join(file_name)
    }
}

fn parse_plugins_js_file(path: &Path) -> Result<Value> {
    let content = fs::read_to_string(path)
        .map_err(|error| AttMzError::io(format!("读取插件配置 {}", path.display()), error))?;
    let pattern = Regex::new(r#"(?s)var\s+\$plugins\s*=\s*(\[.*?\])\s*;\s*$"#)
        .map_err(|error| AttMzError::InvalidGame(format!("插件解析正则不可用: {error}")))?;
    let captures = pattern.captures(&content).ok_or_else(|| {
        AttMzError::InvalidGame("plugins.js 中未找到标准 $plugins 数组".to_string())
    })?;
    let plugins_text = captures
        .get(1)
        .map(|matched| matched.as_str())
        .ok_or_else(|| AttMzError::InvalidGame("plugins.js 中的 $plugins 数组为空".to_string()))?;
    let plugins: Value = serde_json::from_str(plugins_text).map_err(|source| AttMzError::Json {
        context: path.display().to_string(),
        source,
    })?;
    if !plugins.is_array() {
        return Err(AttMzError::InvalidGame(
            "plugins.js 中的 $plugins 必须是数组".to_string(),
        ));
    }
    Ok(plugins)
}

fn collect_event_command_samples(
    root: &Value,
    file_name: &str,
    command_codes: &BTreeSet<i64>,
    samples_by_code: &mut Map<String, Value>,
    seen_samples: &mut HashSet<String>,
    command_count: &mut usize,
) -> Result<()> {
    if file_name == COMMON_EVENTS_FILE_NAME {
        let Some(events) = root.as_array() else {
            return Err(AttMzError::InvalidGame(
                "CommonEvents.json 顶层必须是数组".to_string(),
            ));
        };
        for event in events.iter().filter_map(Value::as_object) {
            collect_commands_from_list(
                event.get("list"),
                command_codes,
                samples_by_code,
                seen_samples,
                command_count,
            )?;
        }
        return Ok(());
    }

    if file_name == TROOPS_FILE_NAME {
        let Some(troops) = root.as_array() else {
            return Err(AttMzError::InvalidGame(
                "Troops.json 顶层必须是数组".to_string(),
            ));
        };
        for troop in troops.iter().filter_map(Value::as_object) {
            if let Some(pages) = troop.get("pages").and_then(Value::as_array) {
                for page in pages.iter().filter_map(Value::as_object) {
                    collect_commands_from_list(
                        page.get("list"),
                        command_codes,
                        samples_by_code,
                        seen_samples,
                        command_count,
                    )?;
                }
            }
        }
        return Ok(());
    }

    if is_map_file_name(file_name) {
        let Some(map) = root.as_object() else {
            return Err(AttMzError::InvalidGame(format!(
                "{file_name} 顶层必须是对象"
            )));
        };
        if let Some(events) = map.get("events").and_then(Value::as_array) {
            for event in events.iter().filter_map(Value::as_object) {
                if let Some(pages) = event.get("pages").and_then(Value::as_array) {
                    for page in pages.iter().filter_map(Value::as_object) {
                        collect_commands_from_list(
                            page.get("list"),
                            command_codes,
                            samples_by_code,
                            seen_samples,
                            command_count,
                        )?;
                    }
                }
            }
        }
    }
    Ok(())
}

fn collect_event_command_snapshots(
    root: &Value,
    file_name: &str,
    snapshots: &mut Vec<EventCommandSnapshot>,
) -> Result<()> {
    if file_name == COMMON_EVENTS_FILE_NAME {
        let Some(events) = root.as_array() else {
            return Err(AttMzError::InvalidGame(
                "CommonEvents.json 顶层必须是数组".to_string(),
            ));
        };
        for event in events.iter().filter_map(Value::as_object) {
            let Some(event_id) = event.get("id").and_then(Value::as_i64) else {
                continue;
            };
            collect_command_snapshots_from_list(
                event.get("list"),
                &format!("{COMMON_EVENTS_FILE_NAME}/{event_id}"),
                COMMON_EVENTS_FILE_NAME,
                snapshots,
            );
        }
        return Ok(());
    }

    if file_name == TROOPS_FILE_NAME {
        let Some(troops) = root.as_array() else {
            return Err(AttMzError::InvalidGame(
                "Troops.json 顶层必须是数组".to_string(),
            ));
        };
        for troop in troops.iter().filter_map(Value::as_object) {
            let Some(troop_id) = troop.get("id").and_then(Value::as_i64) else {
                continue;
            };
            if let Some(pages) = troop.get("pages").and_then(Value::as_array) {
                for (page_index, page) in pages.iter().filter_map(Value::as_object).enumerate() {
                    collect_command_snapshots_from_list(
                        page.get("list"),
                        &format!("{TROOPS_FILE_NAME}/{troop_id}/{page_index}"),
                        TROOPS_FILE_NAME,
                        snapshots,
                    );
                }
            }
        }
        return Ok(());
    }

    if is_map_file_name(file_name) {
        let Some(map) = root.as_object() else {
            return Err(AttMzError::InvalidGame(format!(
                "{file_name} 顶层必须是对象"
            )));
        };
        let display_name = map
            .get("displayName")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if let Some(events) = map.get("events").and_then(Value::as_array) {
            for event in events.iter().filter_map(Value::as_object) {
                let Some(event_id) = event.get("id").and_then(Value::as_i64) else {
                    continue;
                };
                if let Some(pages) = event.get("pages").and_then(Value::as_array) {
                    for (page_index, page) in pages.iter().filter_map(Value::as_object).enumerate()
                    {
                        collect_command_snapshots_from_list(
                            page.get("list"),
                            &format!("{file_name}/{event_id}/{page_index}"),
                            display_name,
                            snapshots,
                        );
                    }
                }
            }
        }
    }
    Ok(())
}

fn collect_command_snapshots_from_list(
    raw_list: Option<&Value>,
    parent_location_path: &str,
    display_name: &str,
    snapshots: &mut Vec<EventCommandSnapshot>,
) {
    let Some(commands) = raw_list.and_then(Value::as_array) else {
        return;
    };
    for (command_index, command) in commands.iter().filter_map(Value::as_object).enumerate() {
        let Some(code) = command.get("code").and_then(Value::as_i64) else {
            continue;
        };
        let parameters = command
            .get("parameters")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new()));
        if !parameters.is_array() {
            continue;
        }
        snapshots.push(EventCommandSnapshot {
            location_path: format!("{parent_location_path}/{command_index}"),
            display_name: display_name.to_string(),
            code,
            parameters,
        });
    }
}

fn collect_commands_from_list(
    raw_list: Option<&Value>,
    command_codes: &BTreeSet<i64>,
    samples_by_code: &mut Map<String, Value>,
    seen_samples: &mut HashSet<String>,
    command_count: &mut usize,
) -> Result<()> {
    let Some(commands) = raw_list.and_then(Value::as_array) else {
        return Ok(());
    };
    for command in commands.iter().filter_map(Value::as_object) {
        let Some(code) = command.get("code").and_then(Value::as_i64) else {
            continue;
        };
        if !command_codes.contains(&code) {
            continue;
        }
        let parameters = command
            .get("parameters")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new()));
        if !parameters.is_array() {
            continue;
        }
        let sample_key = format!(
            "{code}:{}",
            serde_json::to_string(&canonical_json_value(&parameters)).map_err(|source| {
                AttMzError::Json {
                    context: "事件指令参数去重".to_string(),
                    source,
                }
            })?
        );
        if !seen_samples.insert(sample_key) {
            continue;
        }
        let code_key = code.to_string();
        let Some(Value::Array(samples)) = samples_by_code.get_mut(&code_key) else {
            continue;
        };
        samples.push(parameters);
        *command_count += 1;
    }
    Ok(())
}

fn canonical_json_value(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(canonical_json_value).collect()),
        Value::Object(object) => {
            let mut sorted_keys: Vec<&String> = object.keys().collect();
            sorted_keys.sort();
            let mut sorted_object = Map::new();
            for key in sorted_keys {
                if let Some(item) = object.get(key) {
                    sorted_object.insert(key.clone(), canonical_json_value(item));
                }
            }
            Value::Object(sorted_object)
        }
        other => other.clone(),
    }
}

fn read_json_file(path: &Path) -> Result<Value> {
    let content = fs::read_to_string(path)
        .map_err(|error| AttMzError::io(format!("读取 JSON 文件 {}", path.display()), error))?;
    serde_json::from_str(&content).map_err(|source| AttMzError::Json {
        context: path.display().to_string(),
        source,
    })
}

fn write_json_file(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| AttMzError::io(format!("创建输出目录 {}", parent.display()), error))?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|source| AttMzError::Json {
        context: "序列化导出 JSON".to_string(),
        source,
    })?;
    fs::write(path, format!("{text}\n"))
        .map_err(|error| AttMzError::io(format!("写出 JSON 文件 {}", path.display()), error))
}

struct GameSourcePaths {
    data_dir: PathBuf,
    origin_data_dir: PathBuf,
    plugins_path: PathBuf,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn write_json(path: &Path, value: &Value) {
        fs::write(
            path,
            serde_json::to_string_pretty(value).expect("测试 JSON 应序列化成功"),
        )
        .expect("测试 JSON 应写入成功");
    }

    fn create_minimal_game(root: &Path) {
        fs::create_dir_all(root.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(root.join("js")).expect("js 目录应创建成功");
        write_json(
            &root.join("package.json"),
            &json!({"window": {"title": "テストゲーム"}}),
        );
        write_json(&root.join("data/System.json"), &json!({}));
        write_json(
            &root.join("data/CommonEvents.json"),
            &json!([
                null,
                {
                    "id": 1,
                    "list": [
                        {"code": 357, "parameters": ["TestPlugin", "Show", 0, {"message": "プラグイン台詞"}]},
                        {"code": 102, "parameters": [["はい", "いいえ"], 0, 0, 2, 0]},
                        {"code": 0, "parameters": []}
                    ]
                }
            ]),
        );
        write_json(
            &root.join("data/Troops.json"),
            &json!([
                null,
                {
                    "id": 1,
                    "pages": [{"list": [{"code": 357, "parameters": ["TestPlugin", "Show", 0, {"message": "プラグイン台詞"}]}]}]
                }
            ]),
        );
        write_json(
            &root.join("data/Map001.json"),
            &json!({
                "displayName": "始まりの町",
                "events": [
                    null,
                    {"id": 1, "pages": [{"list": [{"code": 357, "parameters": ["ComplexPlugin", "ShowWindow", 0, {"window": {"title": "見出し"}}]}]}]}
                ]
            }),
        );
        fs::write(
            root.join("js/plugins.js"),
            format!(
                "var $plugins = {};\n",
                serde_json::to_string_pretty(&json!([
                    {
                        "name": "TestPlugin",
                        "status": true,
                        "description": "説明",
                        "parameters": {"Message": "プラグイン本文"}
                    }
                ]))
                .expect("插件 JSON 应序列化成功")
            ),
        )
        .expect("plugins.js 应写入成功");
    }

    #[test]
    fn export_plugins_json_writes_raw_plugins_array() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        create_minimal_game(temp.path());
        let output = temp.path().join("plugins.json");

        export_plugins_json_file(temp.path(), &output).expect("插件 JSON 应导出成功");

        let exported = read_json_file(&output).expect("导出 JSON 应可读取");
        assert_eq!(exported[0]["name"], "TestPlugin");
        assert!(exported.is_array());
    }

    #[test]
    fn export_event_commands_deduplicates_parameters_by_code() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        create_minimal_game(temp.path());
        let output = temp.path().join("event-commands.json");
        let codes = resolve_event_command_codes(None, Some(vec![357])).expect("编码应解析成功");

        let count = export_event_commands_json_file(temp.path(), &output, &codes)
            .expect("事件指令应导出成功");

        let exported = read_json_file(&output).expect("导出 JSON 应可读取");
        let commands = exported["357"].as_array().expect("357 应为数组");
        assert_eq!(count, 2);
        assert_eq!(commands.len(), 2);
        assert_eq!(commands[0][0], "TestPlugin");
        assert_eq!(commands[1][0], "ComplexPlugin");
    }

    #[test]
    fn event_command_codes_use_cli_values_before_defaults() {
        let codes = resolve_event_command_codes(Some(vec![102, 357, 102]), Some(vec![999]))
            .expect("编码应解析成功");
        assert_eq!(codes.into_iter().collect::<Vec<_>>(), vec![102, 357]);
    }
}
