"""面向用户文档的结构性回归测试。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agent_prompt_fenced_code_blocks_are_balanced() -> None:
    """Agent 提示词文档的 fenced code block 必须成对闭合。"""
    for relative_path in [
        "skills/rpg-maker-translation/SKILL.md",
        "docs/agent-user-guide.md",
        "docs/name-context-agent-prompt.md",
        "docs/plugin-rules-agent-prompt.md",
        "docs/event-command-rules-agent-prompt.md",
        "docs/custom-placeholder-rules.md",
    ]:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert text.count("````") % 2 == 0
        assert text.count("```") % 2 == 0


def test_readme_matches_runtime_configuration_defaults() -> None:
    """README 中的关键运行配置必须和当前默认值一致。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Python 3.14+" in text
    assert "long_text_line_width_limit = 26" in text
    assert 'line_width_count_pattern = "\\\\S"' in text
    assert "uv run python main.py --debug translate --game" in text


def test_translation_skill_covers_blocking_paths() -> None:
    """项目 Skill 必须覆盖外部 Agent 执行时的阻断路径。"""
    text = (ROOT / "skills/rpg-maker-translation/SKILL.md").read_text(encoding="utf-8")

    required_phrases = [
        "State RT0：项目未知",
        "State RT2：游戏候选未确认",
        "State RT3：模型未配置或不可用",
        "State RT4：自定义控制符未确认",
        "State RT7：翻译反复失败",
        "State RT8：写回门禁",
        "data JSON 被加密或不可解析",
        "当前 Agent 无命令执行能力",
        "event-command-rules.json",
        "plugin-rules.json",
        "name_registry.json",
        "反馈模板",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_agent_user_docs_do_not_replace_skill() -> None:
    """用户文档只提供启动说明，Agent 执行规程必须集中在 Skill。"""
    for relative_path in ["docs/agent-user-guide.md", "docs/agent-workflow.md"]:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "skills/rpg-maker-translation/SKILL.md" in text
        assert "uv run python main.py translate" not in text
        assert "uv run python main.py write-back" not in text
        assert "event-command-rules.json 必须是对象" not in text
