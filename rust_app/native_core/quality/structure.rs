//! 文本结构检查。
//!
//! 本模块负责校验译文行数量、真实换行、字面量换行和模型解释性输出。

use serde_json::{Value, json};

use super::super::details::base_detail;
use super::super::models::{CompiledRules, NativeTranslationItem};
use super::super::placeholders::{
    LITERAL_LINE_BREAK_MARKER, LITERAL_LINE_BREAK_PLACEHOLDER, REAL_LINE_BREAK_PLACEHOLDER,
    build_placeholders, mask_translation_controls,
};

/// 收集单条译文的文本结构问题明细。
pub(super) fn collect_text_structure_detail(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
) -> Option<Value> {
    match build_placeholders(item, rules).and_then(|placeholder_build| {
        let translation_lines_with_placeholders =
            mask_translation_controls(item, rules, &placeholder_build.placeholder_map);
        collect_text_structure_errors(
            item,
            &item.translation_lines,
            &translation_lines_with_placeholders,
            &placeholder_build.original_lines_with_placeholders,
        )
    }) {
        Ok(errors) if errors.is_empty() => None,
        Ok(errors) => {
            let mut detail = base_detail(item);
            detail.insert("reason".to_string(), json!(errors.join(";\n")));
            Some(Value::Object(detail))
        }
        Err(reason) => {
            let mut detail = base_detail(item);
            detail.insert("reason".to_string(), json!(reason));
            Some(Value::Object(detail))
        }
    }
}

fn collect_text_structure_errors(
    item: &NativeTranslationItem,
    translation_lines: &[String],
    translation_lines_with_placeholders: &[String],
    original_lines_with_placeholders: &[String],
) -> Result<Vec<String>, String> {
    let mut errors = collect_artifact_errors(item, translation_lines);
    if item.item_type != "short_text" {
        return Ok(errors);
    }
    if translation_lines.len() != 1 {
        errors.push(format!(
            "单字段文本必须只提供 1 条中文译文行，当前提供 {} 条",
            translation_lines.len()
        ));
        return Ok(errors);
    }

    let original_real_break_count = count_real_line_breaks(original_lines_with_placeholders);
    let translation_real_break_count = count_real_line_breaks(translation_lines_with_placeholders);
    if original_real_break_count != translation_real_break_count {
        errors.push(format!(
            "译文真实换行数量不一致（原文 {} 个，译文 {} 个）",
            original_real_break_count, translation_real_break_count
        ));
    }

    let original_literal_break_count = count_literal_line_breaks(original_lines_with_placeholders);
    let translation_literal_break_count =
        count_literal_line_breaks(translation_lines_with_placeholders);
    if original_literal_break_count != translation_literal_break_count {
        errors.push(format!(
            "译文字面量换行标记数量不一致（原文 {} 个，译文 {} 个）",
            original_literal_break_count, translation_literal_break_count
        ));
    }
    Ok(errors)
}

fn collect_artifact_errors(
    item: &NativeTranslationItem,
    translation_lines: &[String],
) -> Vec<String> {
    let mut errors = Vec::new();
    let joined_text = translation_lines.join("\n");
    if !item.location_path.is_empty() && joined_text.contains(&item.location_path) {
        errors.push("译文包含文本在游戏里的内部位置，不能写进游戏文件".to_string());
    }

    for line in translation_lines {
        let stripped = line.trim();
        let lowered = stripped.to_lowercase();
        if stripped.starts_with("译文：")
            || stripped.starts_with("译文:")
            || stripped.starts_with("翻译：")
            || stripped.starts_with("翻译:")
        {
            errors.push("译文包含明显解释性前缀，不是可写入游戏的正文".to_string());
            break;
        }
        if stripped.contains("以下是翻译") {
            errors.push("译文包含明显解释性说明，不是可写入游戏的正文".to_string());
            break;
        }
        if lowered.starts_with("id:")
            || lowered.starts_with("id：")
            || lowered.starts_with("\"id\":")
            || lowered.starts_with("source_lines:")
            || lowered.starts_with("source_lines：")
            || lowered.starts_with("\"source_lines\":")
            || lowered.starts_with("translation_lines:")
            || lowered.starts_with("translation_lines：")
            || lowered.starts_with("\"translation_lines\":")
        {
            errors.push("译文包含模型输出协议字段，不是可写入游戏的正文".to_string());
            break;
        }
    }
    errors
}

fn count_real_line_breaks(lines: &[String]) -> usize {
    if lines.is_empty() {
        return 0;
    }
    lines.join("\n").matches('\n').count()
        + lines
            .iter()
            .map(|line| line.matches(REAL_LINE_BREAK_PLACEHOLDER).count())
            .sum::<usize>()
}

fn count_literal_line_breaks(lines: &[String]) -> usize {
    lines
        .iter()
        .map(|line| {
            line.matches(LITERAL_LINE_BREAK_MARKER).count()
                + line.matches(LITERAL_LINE_BREAK_PLACEHOLDER).count()
        })
        .sum()
}
