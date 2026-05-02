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
        "### 编码与 Windows 终端",
        "所有工作区 JSON、临时脚本、人工补译文件、规则文件和交付报告都必须按 UTF-8 读写",
        "禁止依赖 Windows 默认编码、ANSI、GBK 或 Shift-JIS",
        "json.dumps(..., ensure_ascii=False)",
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
        "禁止继续导入、翻译或写回乱码数据",
        "### 黑盒执行原则",
        "翻译任务中，把本项目当成闭源黑盒工具使用",
        "所有业务数据进出只走 CLI、`<工作区>` JSON、当前游戏数据库中已导入的规则和游戏目录文件",
        "### 输入-逻辑-输出总则",
        "主 Agent 执行每个阶段前，必须先明确“输入是什么、处理逻辑是什么、输出什么”",
        "### 命令 I/O 合约",
        "`doctor --no-check-llm --json`",
        "`prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json`",
        "`quality-report --game <游戏标题> --json`",
        "`export-untranslated-translations --game <游戏标题> --output <文件> --json`",
        "一键导出全部尚未成功入库的正文原文结构",
        "不传 `--limit` 时导出全部",
        "`write-back --game <游戏标题> --json`",
        "### 工作区 JSON 格式契约",
        "`placeholder-rules.json`：顶层必须是对象，格式为 `{正则表达式: 占位符模板}`",
        "禁止写成 `{占位符名: 正则表达式}`",
        "`name-context/name_registry.json`：顶层只使用 `speaker_names` 和 `map_display_names` 两个对象",
        "`plugin-rules.json`：顶层必须是对象，格式为 `{插件名: [JSONPath, ...]}`",
        "JSONPath 必须使用括号路径语法并从 `$['parameters']` 开始",
        "禁止使用 `$.xxx` 点号路径",
        "`event-command-rules.json`：顶层必须是对象，格式为 `{指令编码字符串: [{match, paths}]}`",
        "`pending-translations.json`：顶层是 `{location_path: 条目对象}`",
        "导入前只填写 `translation_lines` 字符串数组",
        "`long_text` 可以按自然语义填写，导入命令会按当前 `[text_rules]` 行宽配置自动拆短",
        "### 四类子代理任务契约",
        "主 Agent 派发子代理时，必须把对应行的输入、逻辑和输出写进子代理 prompt",
        "格式为 `{正则表达式: 占位符模板}`",
        "格式为 `{插件名: [JSONPath, ...]}`",
        "主 Agent 必须等待四类子代理全部完成",
        "任一子代理未完成、失败或校验未通过，不启动翻译",
        "### 子代理上下文包",
        "不要把大 JSON 正文塞进子代理 prompt",
        "只允许写自己负责的输出文件",
        "完成后必须报告：改动文件、是否为空结果、空结果理由、未解决风险、建议主 Agent 运行的校验命令",
        "推荐子代理 prompt 骨架",
        "### 子代理任务单模板",
        "`placeholder-rules` 子代理任务单",
        "`name-context` 子代理任务单",
        "`plugin-rules` 子代理任务单",
        "`event-command-rules` 子代理任务单",
        "## 11. 校验失败恢复",
        "`validate-* --json` 返回 `error` 时",
        "先把错误映射回对应工作区 JSON",
        "误用了 `$.xxx` 点号路径",
        "write-back --game <游戏标题> --json",
        "禁止直接修改数据库",
    ]
    for phrase in required_phrases:
        assert phrase in text
