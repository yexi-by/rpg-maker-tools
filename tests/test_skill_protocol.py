"""Skill 执行协议回归测试。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_att_mz_skill_requires_parallel_subagents_when_available() -> None:
    """支持子代理的平台必须并行处理外部分析任务。"""
    text = (ROOT / "skills" / "att-mz" / "SKILL.md").read_text(encoding="utf-8")

    required_phrases = [
        "必须启用子代理并行处理",
        "才允许串行处理",
        "`placeholder-rules` 子代理",
        "`name-context` 子代理",
        "`plugin-rules` 子代理",
        "`event-command-rules` 子代理",
        "主 Agent 必须等待四类子代理全部完成",
        "任一子代理未完成、失败或校验未通过，不启动翻译",
    ]
    for phrase in required_phrases:
        assert phrase in text
