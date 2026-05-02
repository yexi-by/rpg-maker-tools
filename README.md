# A.T.T MZ

Autonomous Translation Toolkit for RPG Maker MZ.

A.T.T MZ 是面向 RPG Maker MZ 日文游戏的命令行翻译工具。项目负责确定性提取、缓存、规则导入、质量报告和写回；Agent 或人工负责术语、插件字段、事件指令字段和少量失败项的语义判断。

## 环境要求

- Python 3.14+
- uv
- RPG Maker MZ 标准游戏目录
- OpenAI 兼容格式的模型服务

## 初始化

```bash
uv sync
cp setting.example.toml setting.toml
```

模型地址和 API Key 推荐通过环境变量提供：

```powershell
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"
```

## 基本命令

```bash
uv run python main.py --help
uv run python main.py doctor --no-check-llm --json
uv run python main.py add-game --path <游戏目录> --json
uv run python main.py list --json
```

## Agent 工作流

Agent 执行翻译任务时必须使用项目 Skill：`skills/att-mz/SKILL.md`。

核心顺序：

```bash
uv run python main.py --agent-mode doctor --game <游戏标题> --json
uv run python main.py --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json
uv run python main.py --agent-mode validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json
uv run python main.py --agent-mode validate-agent-workspace --game <游戏标题> --workspace <工作区> --json
uv run python main.py --agent-mode translate --game <游戏标题> --max-batches 1 --json
uv run python main.py --agent-mode translation-status --game <游戏标题> --json
uv run python main.py --agent-mode quality-report --game <游戏标题> --json
uv run python main.py --agent-mode translate --game <游戏标题> --json
uv run python main.py --agent-mode write-back --game <游戏标题> --json
```

`translate` 返回 0 表示本轮命令正常结束，不代表所有条目都成功。失败条目、pending 和质量风险由 `translation-status`、`quality-report` 和人工补译命令处理，禁止绕过 CLI 直接修改数据库。
`translation-status --json` 的 `pending_count` 表示当前数据库实时未翻译数，`run_pending_count` 表示最近一次运行开始时的待处理数。

## 外部分析数据

工作区中的分析结果必须通过 CLI 导入数据库，主翻译流程只读取数据库和本次 CLI 参数。

```bash
uv run python main.py --agent-mode import-name-context --game <游戏标题> --input <工作区>/name-context/name_registry.json
uv run python main.py --agent-mode validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
uv run python main.py --agent-mode import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json
uv run python main.py --agent-mode validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
uv run python main.py --agent-mode import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json
```

## 人工补译

全量导出所有尚未成功入库的正文原文结构，Agent 只填写 `translation_lines` 后再导入：

```bash
uv run python main.py --agent-mode export-untranslated-translations --game <游戏标题> --output <工作区>/pending-translations.json --json
uv run python main.py --agent-mode import-manual-translations --game <游戏标题> --input <工作区>/pending-translations.json --json
```

分批或抽样补译时使用 `export-pending-translations --limit N`；省略 `--limit` 时也会导出全部 pending 条目。
人工补译导入会按当前 `[text_rules]` 行宽配置自动拆短 `long_text` 译文；若仍存在无法安全拆分的超宽行，`quality-report` 会继续阻断写回。

质量报告已经给出可修复明细时，优先导出修复模板，Agent 只改 `translation_lines` 后再导入：

```bash
uv run python main.py --agent-mode export-quality-fix-template --game <游戏标题> --output <工作区>/quality-fix-template.json --json
uv run python main.py --agent-mode import-manual-translations --game <游戏标题> --input <工作区>/quality-fix-template.json --json
```

确认为坏译文需要回到 pending 重新翻译时，使用显式重置文件，不要用空译文绕过导入校验：

```bash
uv run python main.py --agent-mode reset-translations --game <游戏标题> --input <工作区>/reset-translations.json --json
```

确认为致谢名单、Staff 名、作品名、品牌名或专有名词而需要保留日文片段时，使用显式例外规则，不要关闭全局日文残留检测：

```bash
uv run python main.py --agent-mode validate-japanese-residual-rules --game <游戏标题> --input <工作区>/japanese-residual-rules.json --json
uv run python main.py --agent-mode import-japanese-residual-rules --game <游戏标题> --input <工作区>/japanese-residual-rules.json --json
```

## 验收

```bash
uv run basedpyright
uv run pytest
```
