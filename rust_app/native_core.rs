//! Rust 原生核心门面。
//!
//! 本模块只声明内部功能域并向 PyO3 绑定层暴露稳定入口，具体扫描逻辑分布在
//! 质量检查、写入协议、Note 标签来源和字体替换等子模块中。

mod controls;
mod details;
mod font_replacement;
mod models;
mod note_sources;
mod placeholders;
mod pool;
mod quality;
mod rules;
mod write_protocol;

pub fn scan_quality_impl(payload_json: &str) -> Result<String, String> {
    quality::scan_quality_impl(payload_json)
}

pub fn scan_write_protocol_impl(payload_json: &str) -> Result<String, String> {
    write_protocol::scan_write_protocol_impl(payload_json)
}

pub fn collect_note_tag_sources_impl(payload_json: &str) -> Result<String, String> {
    note_sources::collect_note_tag_sources_impl(payload_json)
}

pub fn scan_font_replacements_impl(payload_json: &str) -> Result<String, String> {
    font_replacement::scan_font_replacements_impl(payload_json)
}

pub fn read_configured_thread_count() -> Option<usize> {
    pool::read_configured_thread_count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    fn minimal_text_rules() -> Value {
        json!({
            "custom_placeholder_rules": [],
            "source_residual_allowed_chars": [],
            "source_residual_allowed_tail_chars": [],
            "source_residual_segment_pattern": r"[\p{Hiragana}\p{Katakana}\p{Han}ー]+",
            "source_residual_label": "日文",
            "allowed_source_residual_terms": [],
            "source_residual_terms_ignore_case": false,
            "line_width_count_pattern": r"[^\s]",
            "residual_escape_sequence_pattern": r"\\[A-Za-z0-9_]+\[[^\]]*\]",
            "long_text_line_width_limit": 999
        })
    }

    fn english_text_rules() -> Value {
        json!({
            "custom_placeholder_rules": [],
            "source_residual_allowed_chars": [],
            "source_residual_allowed_tail_chars": [],
            "source_residual_segment_pattern": r"[A-Za-z][A-Za-z0-9'’_-]*",
            "source_residual_label": "英文",
            "allowed_source_residual_terms": ["HP", "MP", "TP", "OK"],
            "source_residual_terms_ignore_case": true,
            "line_width_count_pattern": r"[^\s]",
            "residual_escape_sequence_pattern": r"\\[A-Za-z0-9_]+\[[^\]]*\]",
            "long_text_line_width_limit": 999
        })
    }

    #[test]
    fn quality_scan_reports_source_residual_as_segments() {
        let payload = json!({
            "items": [
                {
                    "location_path": "Map001.json/1/0/0",
                    "item_type": "long_text",
                    "role": null,
                    "original_lines": ["Hello Alice"],
                    "translation_lines": ["你好 Alice"]
                }
            ],
            "text_rules": english_text_rules(),
            "source_residual_rules": []
        });
        let output = scan_quality_impl(&payload.to_string()).expect("质检应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        let reason = value["source_residual_items"][0]["reason"]
            .as_str()
            .expect("残留明细应包含原因");
        assert!(reason.contains("Alice"));
        assert!(!reason.contains("'A', 'l'"));
    }

    #[test]
    fn quality_scan_keeps_real_line_breaks_inside_short_text() {
        let payload = json!({
            "items": [
                {
                    "location_path": "Items.json/1/description",
                    "item_type": "short_text",
                    "role": null,
                    "original_lines": ["武器スキル\n\\C[14]敵単体に毒を付与\\C[0]"],
                    "translation_lines": ["武器技能\n\\C[14]对敌方单体施加毒\\C[0]"]
                }
            ],
            "text_rules": minimal_text_rules(),
            "source_residual_rules": []
        });
        let output = scan_quality_impl(&payload.to_string()).expect("质检应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value["text_structure_items"], json!([]));
        assert_eq!(value["placeholder_risk_items"], json!([]));
    }

    #[test]
    fn protocol_scan_skips_empty_entry() {
        let payload = json!({
            "entries": [
                {
                    "item": {
                        "location_path": "plugins.js",
                        "item_type": "short_text",
                        "role": null,
                        "original_lines": ["旧"],
                        "translation_lines": ["新"]
                    },
                    "mode": "none",
                    "current_value": null,
                    "path_parts": [],
                    "note_text": null,
                    "tag_name": null
                }
            ]
        });
        let output = scan_write_protocol_impl(&payload.to_string()).expect("协议检查应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value, json!([]));
    }

    #[test]
    fn protocol_scan_uses_real_plugin_translation_text() {
        let payload = json!({
            "entries": [
                {
                    "item": {
                        "location_path": "plugins.js/0/Message",
                        "item_type": "short_text",
                        "role": null,
                        "original_lines": ["原文"],
                        "translation_lines": [r"\\V[1]"]
                    },
                    "mode": "nested",
                    "current_value": "\"原文\"",
                    "path_parts": [],
                    "note_text": null,
                    "tag_name": null
                }
            ]
        });
        let output = scan_write_protocol_impl(&payload.to_string()).expect("协议检查应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value.as_array().map(Vec::len), Some(1));
        assert_eq!(value[0]["location_path"], json!("plugins.js/0/Message"));
        assert!(
            value[0]["reason"]
                .as_str()
                .is_some_and(|reason| reason.contains("控制符被写成会直接显示的字面量"))
        );
    }

    #[test]
    fn protocol_scan_uses_real_note_translation_text() {
        let payload = json!({
            "entries": [
                {
                    "item": {
                        "location_path": "Items.json/1/note/说明",
                        "item_type": "short_text",
                        "role": null,
                        "original_lines": ["原文"],
                        "translation_lines": [r"\\V[1]"]
                    },
                    "mode": "note",
                    "current_value": null,
                    "path_parts": [],
                    "note_text": r#"<说明:"原文">"#,
                    "tag_name": "说明"
                }
            ]
        });
        let output = scan_write_protocol_impl(&payload.to_string()).expect("协议检查应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value.as_array().map(Vec::len), Some(1));
        assert_eq!(value[0]["location_path"], json!("Items.json/1/note/说明"));
        assert!(
            value[0]["reason"]
                .as_str()
                .is_some_and(|reason| reason.contains("控制符被写成会直接显示的字面量"))
        );
    }

    #[test]
    fn note_source_scan_collects_nested_note_fields() {
        let payload = json!({
            "data": {
                "Items.json": [
                    null,
                    {
                        "id": 1,
                        "note": "<说明:旧文本>",
                        "effects": [
                            {"note": "<效果:旧文本>"}
                        ]
                    }
                ],
                "plugins.js": "var $plugins = [];"
            },
            "file_pattern": null
        });
        let output = collect_note_tag_sources_impl(&payload.to_string()).expect("扫描应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value.as_array().map(Vec::len), Some(2));
        assert_eq!(value[0]["location_prefix"], "Items.json/1");
        assert_eq!(value[1]["location_prefix"], "Items.json/1/effects/0");
    }

    #[test]
    fn font_scan_reports_direct_and_encoded_json_changes() {
        let payload = json!({
            "data": {
                "System.json": {
                    "advanced": {
                        "mainFontFilename": "OldFont.woff",
                        "nested": "{\"font\": \"AnotherFont.woff\", \"text\": \"正文\"}"
                    }
                },
                "plugins.js": "var $plugins = [];"
            },
            "plugins": [
                {
                    "parameters": {
                        "FontFace": "fonts/OldFont",
                        "HelpText": "请选择 OldFont 字体"
                    }
                }
            ],
            "old_font_names": ["AnotherFont.woff", "OldFont.woff", "OldFont"],
            "replacement_font_name": "NotoSansSC-Regular.ttf"
        });
        let output = scan_font_replacements_impl(&payload.to_string()).expect("扫描应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value["replaced_count"], 3);
        assert_eq!(value["data_changes"].as_array().map(Vec::len), Some(2));
        assert_eq!(value["plugin_changes"].as_array().map(Vec::len), Some(1));
        assert_eq!(
            value["plugin_changes"][0]["replaced_text"],
            "fonts/NotoSansSC-Regular.ttf"
        );
    }
}
