# RPG Maker Tools

将日文 RPG Maker MZ 游戏翻译为简体中文的命令行工具链。围绕"一个游戏一个数据库"运行——每款游戏的译文、术语表、插件规则和事件指令规则全部存放在 `data/db/<游戏标题>.db` 这一个 SQLite 文件中。

## 核心能力

- **正文翻译**：从 `data/*.json`、`js/plugins.js` 和事件指令深层参数中提取日文文本，分批调用 LLM 翻译为简体中文，支持断点续传和内容去重
- **控制符保护**：内置 RPG Maker MZ 全部标准控制符保护，自定义控制符通过正则表达式规则配置
- **名称术语管理**：导出说话人名字框和地图显示名上下文，交由外部 Agent 统一填写译名后导入数据库，翻译时自动注入提示词
- **插件文本翻译**：通过外部 Agent 分析 `plugins.js` 结构并产出 JSONPath 规则，按规则提取和翻译插件配置中的界面文本
- **事件指令文本翻译**：通过外部 Agent 按指令编码定义深层参数提取规则，翻译指令中嵌入的可显示文本
- **译文回写**：将数据库中的译文写回 `data/*.json` 和 `js/plugins.js`，自动备份原始文件，支持字体替换

## 环境要求

- Python 3.14+
- `uv` 包管理器
- 一份 RPG Maker MZ 日文游戏的完整目录

## 快速开始

```bash
# 安装依赖
uv sync

# 复制示例配置为本地配置，并填入模型服务地址和 API Key
cp setting.example.toml setting.toml

# 注册你的游戏
uv run python main.py add-game --path "<游戏根目录>"

# 查看已注册游戏
uv run python main.py list

# 一键翻译 + 回写
uv run python main.py run-all --game "<游戏标题>"
```

## 文档导航

- [CLI 完整使用指南](docs/cli-project-usage.md)：面向人工操作，说明从注册游戏到翻译写回的完整流程。
- [外部 Agent 使用指南](docs/agent-user-guide.md)：面向用户，说明如何把翻译任务交给外部 Agent。
- [Agent 翻译流程概览](docs/agent-workflow.md)：面向用户，说明 Agent、CLI 和用户的职责边界。
- [项目专用 Skill](skills/rpg-maker-translation/SKILL.md)：面向 Agent，提供完整执行规程、异常处理和写回门禁。
- [自定义占位符规则](docs/custom-placeholder-rules.md)：说明游戏自定义控制符的扫描、配置和还原流程。
- [名字框与地图名 Agent 提示词](docs/name-context-agent-prompt.md)：供外部 Agent 填写术语表时参考。
- [插件规则 Agent 提示词](docs/plugin-rules-agent-prompt.md)：供外部 Agent 识别插件可翻译字段时参考。
- [事件指令规则 Agent 提示词](docs/event-command-rules-agent-prompt.md)：供外部 Agent 识别事件指令深层参数文本时参考。

## 命令一览

```
uv run python main.py list
uv run python main.py doctor [--game <标题>] [--json] [--no-check-llm]
uv run python main.py add-game --path <游戏根目录>
uv run python main.py scan-placeholder-candidates --game <标题> [--output <路径>] [--json]
uv run python main.py export-plugins-json --game <标题> --output <路径>
uv run python main.py import-plugin-rules --game <标题> --input <路径>
uv run python main.py export-event-commands-json --game <标题> --output <路径> [--code ...]
uv run python main.py import-event-command-rules --game <标题> --input <路径>
uv run python main.py export-name-context --game <标题> --output-dir <目录>
uv run python main.py import-name-context --game <标题> --input <路径>
uv run python main.py write-name-context --game <标题>
uv run python main.py translate --game <标题>
uv run python main.py quality-report --game <标题> [--json] [--output <路径>]
uv run python main.py write-back --game <标题>
uv run python main.py run-all --game <标题> [--skip-write-back]
```

全局 `--debug` 参数可加在子命令前：

```bash
uv run python main.py --debug translate --game "<游戏标题>"
```

## 工作流程

```
doctor 检查 → 注册游戏 → scan-placeholder-candidates 识别自定义控制符
           → 准备插件规则 → 准备事件指令规则 → 准备名字术语
           → 正文翻译 → quality-report 质量检查 → 回写游戏文件
```

前几步的规则和术语准备依赖外部 Agent（如 Claude Code）分析导出 JSON 后产出规则文件，再通过对应的 `import-*` 命令导入数据库。准备就绪后，`translate` 从数据库读取规则和术语进行翻译，`write-back` 将译文写回游戏文件。`run-all` 将这两步合并执行。

## 配置

项目提供 `setting.example.toml` 作为示例配置。首次使用时复制为本地配置：

```bash
cp setting.example.toml setting.toml
```

`setting.toml` 是本机配置文件，不进入版本库。主配置结构：

```toml
[llm]
base_url = "https://api.deepseek.com"
api_key = "YOUR_KEY"
model = "deepseek-chat"
timeout = 600

[translation_context]
token_size = 1024
factor = 3.5
max_command_items = 5

[text_translation]
worker_count = 200
rpm = 200
retry_count = 3
retry_delay = 2
system_prompt_file = "prompts/text_translation_system.txt"

[event_command_text]
default_command_codes = [357]

[write_back]
replacement_font_path = "fonts/NotoSansSC-Regular.ttf"

[text_rules]
strip_wrapping_punctuation_pairs = [["「", "」"]]
allowed_japanese_chars = ["っ", "ッ", "ー", "・", "。", "～", "…"]
allowed_japanese_tail_chars = ["あ", "い", "う", "え", "お", "っ", "ッ", "ん", "ー", "よ", "ね", "な", "か"]
line_split_punctuations = ["，", "。", "、", "；", "：", "！", "？", "…", "～", "—", "♪", "♡", "）", "】", "」", "』", ",", ".", ";", ":", "!", "?"]
long_text_line_width_limit = 26
line_width_count_pattern = "\\S"
source_text_required_pattern = "[\\u3040-\\u309F\\u30A0-\\u30FF\\u3400-\\u4DBF\\u4E00-\\u9FFF\\uF900-\\uFAFF]+"
japanese_segment_pattern = "[\\u3040-\\u309F\\u30A0-\\u30FF]+"
residual_escape_sequence_pattern = "\\\\[nrt]"
```

模型地址和密钥可以用环境变量覆盖，便于临时切换服务且避免把密钥写进 CLI 日志：

```powershell
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "https://api.example.com"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"
uv run python main.py translate --game "<游戏标题>"
```

除模型地址和密钥外，其他运行配置可通过 CLI 参数覆盖，详见 [CLI 完整使用指南](docs/cli-project-usage.md) 的配置体系章节。

## 自定义占位符规则

RPG Maker MZ 标准控制符（`\V[n]`、`\C[n]`、`\.`、`\!` 等）由程序内置保护。游戏通过插件引入的自定义控制符（如 `\F[xxx]`、`\AC`）需要在项目根目录 `custom_placeholder_rules.json` 中编写正则规则：

```json
{
  "(?i)\\F\\d*\\[[^\\]]+\\]": "[CUSTOM_FACE_{index}]",
  "(?i)\\AC(?![A-Za-z\\[])": "[CUSTOM_AC_{index}]"
}
```

也可以在翻译时通过 CLI 直接传入规则字符串。详见 [自定义占位符规则](docs/custom-placeholder-rules.md)。

## 外部 Agent 协作

以下流程依赖外部 Agent 分析导出文件并产出规则：

| 导出命令 | 产物 | 外部 Agent 产出 | 导入命令 |
| --- | --- | --- | --- |
| `export-plugins-json` | 插件参数 JSON | `plugin-rules.json` | `import-plugin-rules` |
| `export-event-commands-json` | 事件指令参数 JSON | `event-command-rules.json` | `import-event-command-rules` |
| `export-name-context` | 名字上下文 JSON | `name_registry.json`（填写译名） | `import-name-context` |

每个外部 Agent 任务的参考提示词见 [名字框与地图名 Agent 提示词](docs/name-context-agent-prompt.md)、[插件规则 Agent 提示词](docs/plugin-rules-agent-prompt.md) 和 [事件指令规则 Agent 提示词](docs/event-command-rules-agent-prompt.md)。

## 数据存储

- 游戏数据库：`data/db/<游戏标题>.db`
- 日志文件：`logs/`
- 原始文件备份（首次回写时自动创建）：`data_origin/`、`js/plugins_origin.js`

## 项目结构

```
rpg-maker-tools/
├── app/
│   ├── cli.py                  # argparse 子命令入口
│   ├── application/            # 业务编排、文件回写、字体替换
│   ├── config/                 # setting.toml 配置模型与解析
│   ├── event_command_text/     # 事件指令规则导出、导入与提取
│   ├── llm/                    # OpenAI 兼容异步客户端
│   ├── name_context/           # 名字框/地图名导出、导入、提示词注入
│   ├── observability/          # 日志系统
│   ├── persistence/            # SQLite 仓储层
│   ├── plugin_text/            # 插件规则导入、JSONPath 提取与回写
│   ├── rmmz/                   # RMMZ 数据模型、文件加载、文本提取、控制符
│   ├── translation/            # 翻译引擎、校验、缓存、上下文切批
│   └── utils/                  # 工具函数
├── docs/                       # 使用指南和 Agent 提示词文档
├── prompts/                    # LLM 提示词文件
├── data/db/                    # 游戏数据库目录
├── logs/                       # 运行日志
├── tests/                      # 测试套件
├── main.py                     # CLI 入口
├── setting.example.toml        # 示例配置文件
├── custom_placeholder_rules.json  # 自定义控制符规则
├── pyproject.toml
└── uv.lock
```

## 开发

```bash
uv sync
uv run basedpyright          # 类型检查
uv run pytest                # 运行测试
uv run python main.py --help
```
