# A.T.T MZ 技术文档

Autonomous Translation Toolkit for RPG Maker MV/MZ.

A.T.T MZ 是面向 RPG Maker MV/MZ 日文游戏的命令行翻译与质量检查工具。项目负责提取游戏文本、管理译文记录、导入规则、生成质量报告和写回游戏文件。语义判断（术语、插件字段、事件指令字段、data Note 标签字段、少量失败项和用户试玩反馈）由 Agent 按 `skills/att-mz/SKILL.md` 协议执行。

## 环境要求

| 组件 | 用途 |
|------|------|
| [Python](https://www.python.org/downloads/) 3.14+ | 主运行环境 |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 依赖管理与运行入口 |
| [Rust](https://rustup.rs/) stable-msvc | PyO3 原生扩展编译 |
| [VS Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) | C++ 桌面开发组件，提供 MSVC 链接器 |
| OpenAI 兼容模型服务 | 翻译后端 |

## 初始化

```powershell
git clone <项目仓库地址> <项目目录>
cd <项目目录>
uv sync
uv run maturin develop --release
Copy-Item setting.example.toml setting.toml
```

质量检查、写入前协议预演和部分 data 扫描通过 [PyO3](https://pyo3.rs/) 原生扩展执行。首次初始化需先安装 Rust 和 MSVC 链接器：

```powershell
rustup default stable-msvc
rustc --version
cargo --version
uv run maturin develop --release
```

若扩展构建失败并提示找不到 C++ 链接器，安装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022)，勾选"使用 C++ 的桌面开发"。

Rust 核心默认使用逻辑 CPU 核心数。限制线程数：

```powershell
$env:ATT_MZ_RUST_THREADS = "<线程数>"
```

## 模型配置

模型地址和 API Key 通过环境变量提供：

```powershell
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"
```

如需透传额外请求参数，在 `setting.toml` 的 `[llm]` 下配置 `request_body_extra` 为 JSON 对象字符串，该对象会原样合并到 OpenAI 兼容 Chat Completions 请求体：

```toml
[llm]
request_body_extra = '''
{
  "reasoning_effort": "high",
  "thinking": {"type": "enabled"}
}
'''
```

当前流程依赖完整模型 JSON 响应来判断译文保存结果，配置 `stream=true` 或 `stream_options` 会直接报错。

## Windows 终端编码

游戏文本通常同时包含日文、中文和 RPG Maker 控制符。终端编码不正确会导致 Agent 看到乱码并误判文本。启动 Agent 前在同一 PowerShell 会话中执行：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:LANG = "C.UTF-8"
$env:LC_ALL = "C.UTF-8"
```

## 基本命令

```powershell
uv run python main.py --help
uv run python main.py --agent-mode doctor --no-check-llm --json
uv run python main.py --agent-mode add-game --path <游戏目录> --json
uv run python main.py --agent-mode list --json
```

## CLI 约定

- 全局参数（`--agent-mode`）放在子命令前。
- `--json` 时 stdout 只输出最终 JSON 对象。`translate`、`quality-report`、`write-back`、`write-terminology` 等长任务在 stderr 持续输出无 ANSI 文本进度条（已完成数量、百分比、已用时间、预计剩余时间、当前状态）。自动化脚本只解析 stdout，不要把 stderr 进度行当成结果 JSON。
- 长任务运行时必须观察 stderr 进度，不能因为 stdout 暂未输出最终 JSON 就判定命令卡死。
- 模型密钥只从环境变量或本地配置读取，不写进命令行参数、临时文件、报告或提交。
- 文件型规则一律 `--input <文件>`，不把大 JSON 塞进命令行。

## Agent 工作流

Agent 执行翻译任务时必须使用项目 Skill：`skills/att-mz/SKILL.md`。

核心 CLI 调用序列与先决条件：

### 环境与注册

```powershell
uv run python main.py --agent-mode doctor --no-check-llm --json
uv run python main.py --agent-mode add-game --path <游戏目录> --json
uv run python main.py --agent-mode doctor --game <游戏标题> --no-check-llm --json
uv run python main.py --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json
```

### 术语表

`prepare-agent-workspace` 在 `terminology/subtasks/sources/` 和 `terminology/subtasks/candidates/` 下生成按术语字段拆分的候选文件。术语表必须先进入第一轮子代理：主代理把候选文件分配给数个术语候选子代理，等待全部交卷后亲自审查信达雅、日文句意、中文自然度和译名统一，再把修订后的字段译名合并到 `terminology/field-terms.json`，并把可用于正文提示词命中的规范术语写入 `terminology/glossary.json`。子代理不能直接写最终术语表，也不能导入数据库。

```powershell
uv run python main.py --agent-mode import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json
```

字段译名表会覆盖地图显示名、数据库名称、系统类型，以及 MZ 标准 `101.parameters[4]` 名字框等游戏字段；MV 的说话人通常来自插件、文本控制符或自定义文本协议，需要通过插件规则、事件指令规则、占位符规则和正文术语表处理。正文术语表只服务正文翻译提示词命中，不要把字段包装形式、定位信息或说明字段写进正文术语表。

### 外部规则

字段译名表和正文术语表保存后，才开启第二轮子代理任务：插件规则、事件指令规则和 Note 标签规则。

```powershell
# 插件规则
uv run python main.py --agent-mode validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
uv run python main.py --agent-mode import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json

# 事件指令规则
uv run python main.py --agent-mode validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
uv run python main.py --agent-mode import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json

# Note 标签规则
uv run python main.py --agent-mode validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
uv run python main.py --agent-mode import-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
```

Note 标签规则用于标准 `data/*.json` 中由插件显示给玩家的 `note` 标签文本（如 `<玩家可见说明标签:...>`、`<玩家可见名牌标签:...>`）。`<地图文件模式>` 可作为规则文件模式一次覆盖同类地图文件。`<机器协议标签:...>`、`<内部编号标签:...>` 等机器协议标签不得加入规则，也不得手工改游戏 `data/*.json`。

### 占位符规则

占位符规则必须在插件、事件指令和 Note 标签规则全部导入后生成——此时 CLI 才能看到当前真正会进入正文翻译的完整文本集合，避免漏掉插件参数、插件命令参数或 Note 标签文本里的自定义控制符。

```powershell
uv run python main.py --agent-mode build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode scan-placeholder-candidates --game <游戏标题> --input <工作区>/placeholder-rules.json --json
uv run python main.py --agent-mode import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
```

最终导入前必须运行 `scan-placeholder-candidates`，确认 `summary.uncovered_count` 等于 0。

### 工作区验收

```powershell
uv run python main.py --agent-mode validate-agent-workspace --game <游戏标题> --workspace <工作区> --json
```

### 正文翻译

```powershell
# 小批量试跑
uv run python main.py --agent-mode translate --game <游戏标题> --max-batches 1 --json

# 查看状态
uv run python main.py --agent-mode translation-status --game <游戏标题> --json
uv run python main.py --agent-mode quality-report --game <游戏标题> --json

# 全量翻译
uv run python main.py --agent-mode translate --game <游戏标题> --json

# 写回
uv run python main.py --agent-mode write-back --game <游戏标题> --json
```

`translate` 返回 0 表示本轮命令正常结束，不代表所有文本都已成功保存译文。`translation-status --json` 的 `pending_count` 表示当前还有多少文本没成功保存译文，`run_pending_count` 表示最近一次运行开始时待处理的文本数。

## 手动填写译文表

### 导出未保存译文

一次导出全部还没成功保存译文的原文结构，只填写 `translation_lines`（中文译文行）：

```powershell
uv run python main.py --agent-mode export-untranslated-translations --game <游戏标题> --output <工作区>/pending-translations.json --json
uv run python main.py --agent-mode import-manual-translations --game <游戏标题> --input <工作区>/pending-translations.json --json
```

分批或抽样填写：

```powershell
uv run python main.py --agent-mode export-pending-translations --game <游戏标题> --limit N --output <文件> --json
```

手动填写的译文导入时会按当前 `[text_rules]` 行宽配置自动拆短 `long_text` 译文。若仍存在无法安全拆分的超长行，`quality-report` 会继续报告错误，阻止写回。

### 导出质量修复表

质量报告已给出可修复明细时，优先导出修复表（只改 `translation_lines`）：

```powershell
uv run python main.py --agent-mode export-quality-fix-template --game <游戏标题> --output <工作区>/quality-fix-template.json --json
uv run python main.py --agent-mode import-manual-translations --game <游戏标题> --input <工作区>/quality-fix-template.json --json
```

修复表中的 `text_for_model_lines` 仅供对照，不能复制进 `translation_lines`。`translation_lines` 必须使用 `original_lines` 里的游戏原始控制符；若仍看到内置游戏控制符占位符或自定义占位符，先对照 `original_lines` 改回反斜杠形式再导入。

### 重置译文

确认坏译文需要删除、重新交给模型翻译时：

```powershell
uv run python main.py --agent-mode reset-translations --game <游戏标题> --input <工作区>/reset-translations.json --json
```

`reset-translations.json` 格式为 `{"location_paths": ["<定位路径>"]}`。数组不能为空，路径必须来自当前提取范围。禁止用空 `translation_lines` 当重置信号。

完整重译当前提取范围（需用户明确选择）：

```powershell
uv run python main.py --agent-mode reset-translations --game <游戏标题> --all --json
```

### 日文残留例外

确认致谢名单、Staff 名、作品名、品牌名或专有名词确实不应翻译时，使用例外规则，禁止全局关闭日文残留检测：

```powershell
uv run python main.py --agent-mode validate-japanese-residual-rules --game <游戏标题> --input <工作区>/japanese-residual-rules.json --json
uv run python main.py --agent-mode import-japanese-residual-rules --game <游戏标题> --input <工作区>/japanese-residual-rules.json --json
```

## 字体管理

普通 `write-back` 只把译文写进游戏文件，不覆盖字体引用。只有用户明确允许字体覆盖时，才可在本轮追加 `--confirm-font-overwrite`。

还原项目曾覆盖过的字体引用（不滚回译文）：

```powershell
uv run python main.py --agent-mode restore-font --game <游戏标题> --json
```

字体还原对比 `data/*.json` 与 `data_origin/*.json`、`js/plugins.js` 与 `js/plugins_origin.js`，以及存在时的 `fonts/gamefont.css` 与 `fonts/gamefont_origin.css`，只把候选覆盖字体名替回原件里的实际旧字体引用。若需临时指定候选覆盖字体名，追加 `--replacement-font-path <字体文件>`。

## Agent 启动示范

以下以 Claude Code 为例，其他 Agent（Codex 等）对应调整即可。核心思路：先让 Agent 读取项目 Skill，再由它按 CLI 协议准备工作区、分析规则、小批量翻译、质量检查、填写失败译文并写回第一版可试玩汉化结果；用户试玩后继续把问题反馈给 Agent 迭代修复。

```powershell
# 1. 设置 UTF-8
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:LANG = "C.UTF-8"
$env:LC_ALL = "C.UTF-8"

# 2. 设置模型
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"

# 3. 启动 Agent
claude --permission-mode bypassPermissions
```

在 Agent 交互界面提交任务说明：

```text
请使用 <项目目录>/skills/att-mz/SKILL.md 执行 RPG Maker MV/MZ 游戏自动翻译。

项目目录：<项目目录>
游戏目录：<游戏目录>
工作区：<工作区>

要求：
1. 全程按 Skill 里写明的输入、输出和校验步骤工作，只通过 CLI、工作区 JSON 和游戏目录处理业务数据。
2. 先运行 doctor、add-game、prepare-agent-workspace，并确认 <游戏标题>。
3. 先由主代理拆分术语字段，派发术语候选子代理，等待全部交卷后亲自审查、统一译名、修订字段译名表和正文术语表，并用 import-terminology 同时导入。
4. 术语表导入后，再派发插件规则、事件指令规则和 data Note 标签规则三类子代理；这些文本来源确认后，再由主代理生成、校验并导入占位符规则。
5. 先执行 translate --max-batches 1 小批量试跑，再查看 translation-status 和 quality-report。
6. 质量问题优先用 export-quality-fix-template 导出可填写的修复表，再用 import-manual-translations 导入。
7. 如果还有没成功保存译文的文本，使用 export-untranslated-translations 导出完整译文表，只填写中文译文行。
8. 不直接修改数据库，不跳过 validate，不在 quality-report 报告错误时执行写回。
9. 本轮写回前先向我确认；我确认后再执行 write-back --json。
10. 除非我单独明确允许覆盖字体，否则不要添加 --confirm-font-overwrite。
11. 写回完成后提醒我先实际游玩，把漏翻、误翻、显示异常和语气不自然的地方反馈回来；收到反馈后先整理成修复清单，再定位问题、修译文或补规则、重新运行质量检查，并在我确认后再次写回。
```

新手建议先让 Agent 跑到小批量质量报告为止，确认没有占位符、乱码、超宽行和明显日文残留后，再继续全量翻译。若 Agent 输出出现乱码，先停止当前阶段，重新设置 UTF-8 后再重跑相关命令，不要基于乱码内容修译文或规则。

## 验收

```powershell
uv run basedpyright
uv run pytest
```
