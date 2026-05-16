# A.T.T MZ 进阶使用技术文档

Autonomous Translation Toolkit for RPG Maker MZ.

A.T.T MZ 是面向 RPG Maker MZ 日文游戏的命令行翻译工具。项目负责提取游戏文本、保存译文记录、导入规则、生成质量报告和写回游戏文件；代理流程负责术语、插件字段、事件指令字段、data Note 标签字段、少量失败项和用户试玩反馈的语义判断。第一次写回生成的是可试玩汉化结果，后续需要按用户反馈继续查缺补漏。

## 环境要求

- Python 3.14+
- uv
- Rust stable MSVC 工具链
- Visual Studio Build Tools C++ 构建组件
- RPG Maker MZ 标准游戏目录
- OpenAI 兼容格式的模型服务

## 初始化

```bash
uv sync
uv run maturin develop --release
cp setting.example.toml setting.toml
```

质量检查、写入前协议预演和部分 data 扫描通过 PyO3 原生扩展执行。开发环境需要先安装 Rust 和 MSVC 链接器：

```powershell
rustup default stable-msvc
rustc --version
cargo --version
uv run maturin develop --release
```

如果扩展构建失败并提示找不到 C++ 链接器，安装 Visual Studio Build Tools，并勾选“使用 C++ 的桌面开发”。修改 Rust 源码后，使用 `cargo test` 和 `uv run maturin develop --release` 重新验证扩展。

Rust 原生核心默认使用逻辑 CPU 核心数。需要限制 CPU 占用时，在运行 CLI 前设置：

```powershell
$env:ATT_MZ_RUST_THREADS = "<线程数>"
```

模型地址和 API Key 推荐通过环境变量提供：

```powershell
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"
```

模型服务需要额外请求参数时，在 `<项目目录>/setting.toml` 的 `[llm]` 下写入 JSON 对象字符串。该对象会原样透传到 OpenAI 兼容 Chat Completions 请求体；当前流程需要先拿到完整模型 JSON 再检查并保存译文，因此配置 `stream=true` 或 `stream_options` 会直接报错。

```toml
[llm]
request_body_extra = '''
{
  "reasoning_effort": "high",
  "thinking": {"type": "enabled"}
}
'''
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
uv run python main.py --agent-mode doctor --game <游戏标题> --no-check-llm --json
uv run python main.py --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json
uv run python main.py --agent-mode import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json
uv run python main.py --agent-mode validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
uv run python main.py --agent-mode import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
uv run python main.py --agent-mode validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
uv run python main.py --agent-mode import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
uv run python main.py --agent-mode validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
uv run python main.py --agent-mode import-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
uv run python main.py --agent-mode build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode scan-placeholder-candidates --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode validate-agent-workspace --game <游戏标题> --workspace <工作区> --json
uv run python main.py --agent-mode translate --game <游戏标题> --max-batches 1 --json
uv run python main.py --agent-mode translation-status --game <游戏标题> --json
uv run python main.py --agent-mode quality-report --game <游戏标题> --json
uv run python main.py --agent-mode translate --game <游戏标题> --json
uv run python main.py --agent-mode write-back --game <游戏标题> --json
```

`prepare-agent-workspace` 会在 `terminology/subtasks/sources/` 和 `terminology/subtasks/candidates/` 下生成按术语字段拆分的候选文件。术语表必须先进入第一轮子代理：主代理把候选文件分配给数个术语候选子代理，等待全部交卷后亲自审查信达雅、日文句意、中文自然度和译名统一，再把修订后的字段译名合并到 `terminology/field-terms.json`，并把可用于正文提示词命中的规范术语写入 `terminology/glossary.json`。子代理不能直接写最终术语表，也不能导入数据库。

字段译名表和正文术语表通过 `import-terminology` 保存到项目数据库后，才开始第二轮子代理任务：插件规则、事件指令规则和 Note 标签规则。三类规则通过各自的 `validate-*` 和 `import-*` 命令后，主代理再亲自生成、校验、扫描并导入占位符规则。

`translate` 返回 0 表示本轮命令正常结束，不代表所有文本都已经成功保存译文。没成功保存译文的文本和检查没通过的译文由 `translation-status`、`quality-report` 和手动填写译文表命令处理，禁止绕过 CLI 直接修改数据库。
`translation-status --json` 的 `pending_count` 表示当前还有多少文本没成功保存译文，`run_pending_count` 表示最近一次运行开始时有多少文本需要处理。

`--json` 的 stdout 只输出最终 JSON。长时间运行的 `translate`、`quality-report`、`write-back`、`write-terminology` 会把无 ANSI 文本进度条输出到 stderr，包含已完成数量、百分比、已用时间、预计剩余时间和当前状态。自动化脚本解析 stdout；观察进度时看 stderr。

普通 `write-back` 只把译文写进游戏文件，不覆盖字体引用。只有用户明确允许字体覆盖时，才可以在本轮写回命令里追加 `--confirm-font-overwrite`。如果需要还原曾经由项目覆盖过的字体引用，使用：

```powershell
uv run python main.py --agent-mode restore-font --game <游戏标题> --json
```

字体还原会对比 `data/*.json` 与 `data_origin/*.json`、`js/plugins.js` 与 `js/plugins_origin.js`，只把候选覆盖字体名替回同路径原件里的实际旧字体引用，不回滚已写入的译文。若需要临时指定候选覆盖字体名，可追加 `--replacement-font-path <字体文件>`。

## Agent 自动翻译示范（以 Claude Code 为例）

下面示范面向第一次使用的 Agent 操作者。A.T.T MZ 不绑定某一个 Agent；Codex、Claude Code 或其他能读取项目文件并运行命令的工具都可以使用。核心思路是：先让 Agent 读取本项目 Skill，再由它按 CLI 协议准备工作区、分析规则、小批量翻译、质量检查、手动填写失败译文并写回第一版可试玩汉化结果；用户试玩后继续把问题反馈给 Agent 迭代修复。

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
1. 全程按 Skill 里写明的输入、输出和校验步骤工作，只通过 CLI、工作区 JSON 和游戏目录处理业务数据。
2. 先运行 doctor、add-game、prepare-agent-workspace，并确认 <游戏标题>。
3. 先由主代理拆分术语字段，派发术语候选子代理，等待全部交卷后亲自审查、统一译名、修订字段译名表和正文术语表，并用 `import-terminology` 同时导入。
4. 术语表导入后，再派发插件规则、事件指令规则和 data Note 标签规则三类子代理；这些文本来源确认后，再由主代理生成、校验并导入占位符规则。
5. 先执行 translate --max-batches 1 小批量试跑，再查看 translation-status 和 quality-report。
6. 质量问题优先用 export-quality-fix-template 导出可填写的修复表，再用 import-manual-translations 导入。
7. 如果还有没成功保存译文的文本，使用 export-untranslated-translations 导出完整译文表，只填写 translation_lines，也就是中文译文行。
8. 不直接修改数据库，不跳过 validate，不在 quality-report 报告错误时执行 write-back，也就是把译文写进游戏文件。
9. 本轮写回前先向我确认；我确认后再执行 write-back --json。
10. 除非我单独明确允许覆盖字体，否则不要添加 --confirm-font-overwrite。
11. 写回完成后提醒我先实际游玩，把漏翻、误翻、显示异常和语气不自然的地方反馈回来；收到反馈后先整理成修复清单，再定位问题、修译文或补规则、重新运行质量检查，并在我确认后再次写回。
```

新手建议先让 Agent 跑到小批量质量报告为止，确认没有占位符、乱码、超宽行和明显日文残留后，再让它继续全量翻译。第一次写回后先试玩，不要把第一版当成最终完成；若 Agent 输出出现乱码，先停止当前阶段，重新设置 UTF-8 后再重跑相关命令，不要基于乱码内容修译文或规则。

## 外部分析数据

工作区中的分析结果必须通过 CLI 导入数据库，主翻译流程只读取数据库和本次 CLI 参数。

```bash
uv run python main.py --agent-mode import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json
uv run python main.py --agent-mode validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
uv run python main.py --agent-mode import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
uv run python main.py --agent-mode validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
uv run python main.py --agent-mode import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
uv run python main.py --agent-mode validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
uv run python main.py --agent-mode import-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
uv run python main.py --agent-mode build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode scan-placeholder-candidates --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
```

Note 标签规则用于标准 `data/*.json` 中由插件显示给玩家的 `note` 标签文本，例如 `<玩家可见说明标签:...>`、`<玩家可见名牌标签:...>`。`<地图文件模式>` 可作为规则文件模式一次覆盖同类地图文件。不要把 `<机器协议标签:...>`、`<内部编号标签:...>` 等机器协议标签加入规则，也不要手工改游戏 `data/*.json`。

占位符规则必须在插件规则、事件指令规则和 Note 标签规则导入后生成。此时 CLI 才能看到当前真正会进入正文翻译的完整文本集合，避免漏掉插件参数、插件命令参数或 Note 标签文本里的自定义控制符。最终导入前必须运行 `scan-placeholder-candidates --input <工作区>/placeholder-rules.json --json`，并确认 `summary.uncovered_count` 等于 0。

## 手动填写译文表

全量导出所有还没成功保存译文的正文原文结构，只填写 `translation_lines`，也就是中文译文行，然后再导入：

```bash
uv run python main.py --agent-mode export-untranslated-translations --game <游戏标题> --output <工作区>/pending-translations.json --json
uv run python main.py --agent-mode import-manual-translations --game <游戏标题> --input <工作区>/pending-translations.json --json
```

分批或抽样填写时使用 `export-pending-translations --limit N`；省略 `--limit` 时也会导出全部还没成功保存译文的文本。
手动填写的译文导入时会按当前 `[text_rules]` 行宽配置自动拆短 `long_text` 译文；若仍存在无法安全拆分的太长行，`quality-report` 会继续报告错误，不能写进游戏文件。

质量报告已经给出可修复明细时，优先导出可填写的修复表，只改 `translation_lines`，也就是中文译文行，之后再导入：

```bash
uv run python main.py --agent-mode export-quality-fix-template --game <游戏标题> --output <工作区>/quality-fix-template.json --json
uv run python main.py --agent-mode import-manual-translations --game <游戏标题> --input <工作区>/quality-fix-template.json --json
```

修复表里的 `text_for_model_lines` 只供对照，不能复制进 `translation_lines`。`translation_lines` 必须使用 `original_lines` 里的游戏原始控制符；如果仍看到 `[RMMZ_...]` 或 `[CUSTOM_...]`，先按原文控制符改回反斜杠形式再导入。

确认为坏译文需要删除并重新交给模型翻译时，使用显式重置文件，不要用空译文绕过导入校验：

```bash
uv run python main.py --agent-mode reset-translations --game <游戏标题> --input <工作区>/reset-translations.json --json
```

用户明确要求完整重译已完成游戏时，直接重置当前提取范围内全部已保存译文记录：

```bash
uv run python main.py --agent-mode reset-translations --game <游戏标题> --all --json
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
