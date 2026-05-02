"""日文残留例外规则公共导出入口。"""

from .rules import (
    JapaneseResidualRuleImportFile,
    JapaneseResidualRuleSet,
    JapaneseResidualRuleSpec,
    build_japanese_residual_rule_records_from_import,
    check_japanese_residual_for_item,
    load_japanese_residual_rule_import_file,
    mask_japanese_residual_allowed_terms,
    parse_japanese_residual_rule_import_text,
)

__all__: list[str] = [
    "JapaneseResidualRuleImportFile",
    "JapaneseResidualRuleSet",
    "JapaneseResidualRuleSpec",
    "build_japanese_residual_rule_records_from_import",
    "check_japanese_residual_for_item",
    "load_japanese_residual_rule_import_file",
    "mask_japanese_residual_allowed_terms",
    "parse_japanese_residual_rule_import_text",
]
