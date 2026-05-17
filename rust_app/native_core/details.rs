//! 质量问题明细构造工具。
//!
//! 本模块负责生成 Rust 扫描结果里的通用定位字段，并提供稳定排序规则。

use serde_json::{Map, Value, json};

use super::models::NativeTranslationItem;

pub(crate) fn collect_sorted_details(mut details: Vec<Value>) -> Vec<Value> {
    details.sort_by_key(detail_sort_key);
    details
}

pub(crate) fn detail_sort_key(value: &Value) -> (String, i64) {
    let location_path = value
        .get("location_path")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let line_index = value
        .get("line_index")
        .and_then(Value::as_i64)
        .unwrap_or(-1);
    (location_path, line_index)
}

pub(crate) fn base_detail(item: &NativeTranslationItem) -> Map<String, Value> {
    let mut detail = Map::new();
    detail.insert("location_path".to_string(), json!(item.location_path));
    detail.insert("item_type".to_string(), json!(item.item_type));
    detail.insert("role".to_string(), json!(item.role));
    detail.insert("original_lines".to_string(), json!(item.original_lines));
    detail.insert(
        "translation_lines".to_string(),
        json!(item.translation_lines),
    );
    detail
}
