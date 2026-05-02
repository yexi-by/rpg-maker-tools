# A.T.T MZ 进阶使用技术文档

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

## Agent 自动翻译示范（以 Claude Code 为例）

下面示范面向第一次使用的 Agent 操作者。A.T.T MZ 不绑定某一个 Agent；Codex、Claude Code 或其他能读取项目文件并运行命令的工具都可以使用。核心思路是：先让 Agent 读取本项目 Skill，再由它按 CLI 协议准备工作区、分析规则、小批量翻译、质量检查、补译和写回。

友情提示：Windows 终端容易把中日文和控制符显示成乱码。启动 Agent 前，先在同一个 PowerShell 会话里设置 UTF-8：

```powershell
cd <项目目录>
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:LANG = "C.UTF-8"
$env:LC_ALL = "C.UTF-8"
```

然后用你熟悉的 Agent 打开项目目录。下面仅以 Claude Code 为例：

```powershell
claude --permission-mode bypassPermissions
```

如果使用 Codex 或其他 Agent，按对应工具的 Skill 使用方式加载 `skills/att-mz`，或在任务说明中要求它读取 `<项目目录>/skills/att-mz/SKILL.md`。进入交互界面后，可以提交类似下面的任务说明：

```text
请使用 <项目目录>/skills/att-mz/SKILL.md 执行 RPG Maker MZ 游戏自动翻译。

项目目录：<项目目录>
游戏目录：<游戏目录>
工作区：<工作区>

要求：
1. 全程按 Skill 的黑盒协议工作，只通过 CLI、工作区 JSON 和游戏目录处理业务数据。
2. 先运行 doctor、add-game、prepare-agent-workspace，并确认 <游戏标题>。
3. 分析并校验占位符规则、术语表、插件规则和事件指令规则；通过 CLI validate 后再 import。
4. 先执行 translate --max-batches 1 小批量试跑，再查看 translation-status 和 quality-report。
5. 质量问题优先用 export-quality-fix-template 导出修复骨架，再用 import-manual-translations 导入。
6. pending 需要人工补齐时，使用 export-untranslated-translations 导出完整结构，只填写 translation_lines。
7. 不直接修改数据库，不跳过 validate，不在 quality-report 存在阻断问题时 write-back。
8. 最终写回前先向我确认；我确认后再执行 write-back --json。
```

新手建议先让 Agent 跑到小批量质量报告为止，确认没有占位符、乱码、超宽行和日文残留问题后，再让它继续全量翻译。若 Agent 输出出现乱码，先停止当前阶段，重新设置 UTF-8 后再重跑相关命令，不要基于乱码内容修译文或规则。

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
