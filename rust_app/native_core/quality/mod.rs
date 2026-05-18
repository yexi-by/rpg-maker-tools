//! 翻译质量检查编排。
//!
//! 本模块负责解析质量检查输入、并行调度各类检查，并保持 PyO3 门面的输出协议稳定。

mod line_width;
mod placeholder;
mod residual;
mod structure;

use rayon::prelude::*;
use std::sync::Arc;

use super::details::collect_sorted_details;
use super::models::{QualityPayload, QualityScanOutput};
use super::pool::run_with_optional_pool;
use super::rules::compile_rules;
use line_width::collect_overwide_details;
use placeholder::collect_placeholder_detail;
use residual::{collect_residual_detail, index_residual_rules};
use structure::collect_text_structure_detail;

/// 扫描翻译质量问题并返回稳定 JSON 字符串。
pub fn scan_quality_impl(payload_json: &str) -> Result<String, String> {
    let payload: QualityPayload = serde_json::from_str(payload_json)
        .map_err(|error| format!("Rust 质检输入 JSON 解析失败: {error}"))?;
    let rules = Arc::new(compile_rules(payload.text_rules)?);
    let residual_rules = Arc::new(index_residual_rules(payload.source_residual_rules)?);
    let items = Arc::new(payload.items);

    let output = run_with_optional_pool(|| {
        let source_residual_items = collect_sorted_details(
            items
                .par_iter()
                .filter_map(|item| collect_residual_detail(item, &rules, &residual_rules))
                .collect(),
        );
        let text_structure_items = collect_sorted_details(
            items
                .par_iter()
                .filter_map(|item| collect_text_structure_detail(item, &rules))
                .collect(),
        );
        let placeholder_risk_items = collect_sorted_details(
            items
                .par_iter()
                .filter_map(|item| collect_placeholder_detail(item, &rules))
                .collect(),
        );
        let overwide_line_items = collect_sorted_details(
            items
                .par_iter()
                .flat_map(|item| collect_overwide_details(item, &rules))
                .collect(),
        );

        QualityScanOutput {
            source_residual_items,
            text_structure_items,
            placeholder_risk_items,
            overwide_line_items,
        }
    });

    serde_json::to_string(&output)
        .map_err(|error| format!("Rust 质检输出 JSON 序列化失败: {error}"))
}
