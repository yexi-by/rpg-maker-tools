"""CLI 配置覆盖测试。"""

from pathlib import Path

import pytest

from app.config import LLM_API_KEY_ENV_NAME, LLM_BASE_URL_ENV_NAME
from app.config import SettingOverrides
from app.utils.config_loader_utils import load_setting


def test_load_setting_applies_cli_overrides_without_reading_prompt_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 覆盖可以用具体值替代 `setting.toml` 中的提示词文件引用。"""
    monkeypatch.delenv(LLM_BASE_URL_ENV_NAME, raising=False)
    monkeypatch.delenv(LLM_API_KEY_ENV_NAME, raising=False)
    setting_path = tmp_path / "setting.toml"
    _ = setting_path.write_text(
        """
[llm]
base_url = "https://example.invalid"
api_key = "from-file"
model = "file-model"
timeout = 10

[translation_context]
token_size = 10
factor = 1.0
max_command_items = 1

[text_translation]
worker_count = 1
rpm = 10
retry_count = 1
retry_delay = 1
system_prompt_file = "missing_prompt.txt"

[event_command_text]
default_command_codes = [357]

[text_rules]
strip_wrapping_punctuation_pairs = [["「", "」"]]
allowed_japanese_chars = ["っ"]
allowed_japanese_tail_chars = ["ね"]
line_split_punctuations = ["。"]
long_text_line_width_limit = 30
line_width_count_pattern = "[\\u4E00-\\u9FFF]"
source_text_required_pattern = "[\\u3040-\\u30FF]+"
japanese_segment_pattern = "[\\u3040-\\u30FF]+"
residual_escape_sequence_pattern = "\\\\[nrt]"
""",
        encoding="utf-8",
    )
    overrides = SettingOverrides(
        llm_model="cli-model",
        llm_timeout=600,
        translation_token_size=2048,
        translation_factor=4.0,
        translation_max_command_items=7,
        text_translation_worker_count=12,
        text_translation_rpm=None,
        text_translation_rpm_is_set=True,
        text_translation_retry_count=5,
        text_translation_retry_delay=3,
        text_translation_system_prompt="直接传入的系统提示词",
        event_command_default_codes=[357, 355],
        strip_wrapping_punctuation_pairs=[("《", "》")],
        preserve_wrapping_punctuation_pairs=[("『", "』")],
        allowed_japanese_chars=["ー"],
        allowed_japanese_tail_chars=["よ"],
        line_split_punctuations=["，", "。"],
        long_text_line_width_limit=42,
        line_width_count_pattern="[a-z]",
        source_text_required_pattern="[ぁ-ん一-龠]+",
        japanese_segment_pattern="[ぁ-ん]+",
        residual_escape_sequence_pattern="\\\\[abc]",
        write_back_replacement_font_path="fonts/Override.ttf",
    )

    setting = load_setting(setting_path=setting_path, overrides=overrides)

    assert setting.llm.base_url == "https://example.invalid"
    assert setting.llm.api_key == "from-file"
    assert setting.llm.model == "cli-model"
    assert setting.llm.timeout == 600
    assert setting.translation_context.token_size == 2048
    assert setting.translation_context.factor == 4.0
    assert setting.translation_context.max_command_items == 7
    assert setting.text_translation.worker_count == 12
    assert setting.text_translation.rpm is None
    assert setting.text_translation.retry_count == 5
    assert setting.text_translation.retry_delay == 3
    assert setting.text_translation.system_prompt_file == "<cli>"
    assert setting.text_translation.system_prompt == "直接传入的系统提示词"
    assert setting.event_command_text.default_command_codes == [357, 355]
    assert setting.text_rules.strip_wrapping_punctuation_pairs == [("《", "》")]
    assert setting.text_rules.preserve_wrapping_punctuation_pairs == [("『", "』")]
    assert setting.text_rules.allowed_japanese_chars == ["ー"]
    assert setting.text_rules.allowed_japanese_tail_chars == ["よ"]
    assert setting.text_rules.line_split_punctuations == ["，", "。"]
    assert setting.text_rules.long_text_line_width_limit == 42
    assert setting.text_rules.line_width_count_pattern == "[a-z]"
    assert setting.text_rules.source_text_required_pattern == "[ぁ-ん一-龠]+"
    assert setting.text_rules.japanese_segment_pattern == "[ぁ-ん]+"
    assert setting.text_rules.residual_escape_sequence_pattern == "\\\\[abc]"
    assert setting.write_back.replacement_font_path == "fonts/Override.ttf"


def test_load_setting_applies_environment_llm_connection_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量优先覆盖模型地址和密钥。"""
    setting_path = tmp_path / "setting.toml"
    _ = setting_path.write_text(
        """
[llm]
base_url = "https://example.invalid"
api_key = "from-file"
model = "file-model"
timeout = 10

[translation_context]
token_size = 10
factor = 1.0
max_command_items = 1

[text_translation]
worker_count = 1
rpm = 10
retry_count = 1
retry_delay = 1
system_prompt_file = "prompt.txt"

[event_command_text]
default_command_codes = [357]
""",
        encoding="utf-8",
    )
    _ = (tmp_path / "prompt.txt").write_text("系统提示词", encoding="utf-8")
    monkeypatch.setenv(LLM_BASE_URL_ENV_NAME, "https://env.example.com")
    monkeypatch.setenv(LLM_API_KEY_ENV_NAME, "env-key")

    setting = load_setting(setting_path=setting_path)

    assert setting.llm.base_url == "https://env.example.com"
    assert setting.llm.api_key == "env-key"
    assert setting.llm.model == "file-model"
