# CLI 使用指南：从零到翻译注入

本文档走一遍完整流程：拿到一份日文 RPG Maker MZ 游戏后，如何用本工具完成注册、规则准备、术语准备、正文翻译和文件回写。

## 目录

- [1. 项目概览](#1-项目概览)
- [2. 环境准备](#2-环境准备)
- [3. 完整流程速览](#3-完整流程速览)
- [4. 第一步：注册游戏](#4-第一步注册游戏)
- [5. 第二步：识别自定义控制符](#5-第二步识别自定义控制符)
- [6. 第三步：准备插件文本规则](#6-第三步准备插件文本规则)
- [7. 第四步：准备事件指令文本规则](#7-第四步准备事件指令文本规则)
- [8. 第五步：准备名字框与地图名术语](#8-第五步准备名字框与地图名术语)
- [9. 第六步：正文翻译](#9-第六步正文翻译)
- [10. 第七步：回写游戏文件](#10-第七步回写游戏文件)
- [11. 一键流水线](#11-一键流水线)
- [12. 断点续传与增量翻译](#12-断点续传与增量翻译)
- [13. 配置体系](#13-配置体系)
- [14. 命令速查表](#14-命令速查表)
- [15. 常见问题](#15-常见问题)

---

## 1. 项目概览

本工具围绕"一个游戏一个数据库"运行。每款游戏的注册信息、译文缓存、术语表、插件文本规则和事件指令规则都存放在 `data/db/<游戏标题>.db` 这一个 SQLite 文件中。

核心流程分为三个阶段：

1. **准备阶段**：诊断环境 → 注册游戏 → 识别自定义控制符 → 通过外部 Agent 产出插件规则、事件指令规则和术语表 → 导入数据库
2. **翻译阶段**：从游戏文件中提取正文 → 调用 LLM 翻译 → 译文写入数据库 → 生成质量报告
3. **回写阶段**：从数据库读取译文 → 写回游戏 `data/*.json` 和 `js/plugins.js` → 同步字体引用

程序启动入口是项目根目录下的 `main.py`，所有命令通过 `uv run python main.py <命令>` 执行。

```
uv run python main.py <命令> --game "<游戏标题>" [其他参数]
```

---

## 2. 环境准备

### 前置条件

- Python 3.14+
- `uv` 包管理器
- 一份 RPG Maker MZ 游戏的完整目录（包含 `data/`、`js/plugins.js`、`Game.rpgproject` 等）

### 安装依赖

```bash
cd <项目目录>
uv sync
```

### 配置文件

项目根目录的 `setting.toml` 是默认配置文件。首次使用时先从示例文件复制本地配置：

```bash
cp setting.example.toml setting.toml
```

`setting.toml` 只保存本机配置，不进入版本库。开始前至少需要确认以下字段：

```
[llm]
base_url = "https://api.deepseek.com"
api_key = "YOUR_API_KEY"
model = "deepseek-chat"
timeout = 600
```

其他字段有合理默认值，完整说明见 [13. 配置体系](#13-配置体系)。

### 验收环境

```bash
uv run basedpyright
uv run pytest
```

---

## 3. 完整流程速览

下面是从零到翻译注入的全部步骤，按顺序执行：

| 步骤 | 命令 | 说明 |
| --- | --- | --- |
| 1 | `doctor` | 检查项目配置和模型连接 |
| 2 | `add-game` | 注册游戏 |
| 3 | `prepare-agent-workspace` / `scan-placeholder-candidates` | 导出 Agent 工作区并识别游戏自定义控制符候选 |
| 4 | `export-plugins-json` + 外部 Agent + `import-plugin-rules` | 准备插件文本规则 |
| 5 | `export-event-commands-json` + 外部 Agent + `import-event-command-rules` | 准备事件指令文本规则 |
| 6 | `export-name-context` + 外部 Agent + `import-name-context` | 准备名字框和地图名术语 |
| 7 | `translate` | 正文翻译 |
| 8 | `quality-report` | 检查运行故障、译文质量错误、残留、占位符和超宽行 |
| 9 | `write-back` | 回写游戏文件 |

第 3 ~ 6 步的顺序可以灵活调整，但第 7 步翻译必须在规则和术语导入之后执行。第 9 步回写必须在质量报告没有阻断错误后执行。不想逐步执行的话，第 7 + 9 步可以用 `run-all` 一步完成。

---

## 4. 第一步：注册游戏

注册前可以先执行项目级诊断：

```bash
uv run python main.py doctor --no-check-llm
```

需要检查模型连通性时去掉 `--no-check-llm`。

```bash
uv run python main.py add-game --path "<游戏根目录>"
```

`--path` 指向 RPG Maker MZ 游戏根目录，即包含 `Game.rpgproject` 或 `data/` 和 `js/plugins.js` 的目录。程序会自动读取游戏标题，并在 `data/db/` 下创建对应数据库。

注册完成后查看已注册的游戏列表：

```bash
uv run python main.py list
```

检查目标游戏状态：

```bash
uv run python main.py doctor --game "<游戏标题>" --no-check-llm
```

输出示例：

```
          已注册游戏
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ 游戏标题  ┃ 游戏目录          ┃ 数据库              ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ 示例游戏  │ <游戏根目录>  │ data/db/示例游戏.db │
└──────────┴──────────────────┴────────────────────┘
```

同一个游戏标题再次执行 `add-game` 会更新绑定路径，不会丢失已有数据。

---

## 5. 第二步：识别自定义控制符

RPG Maker MZ 的标准控制符（`\V[n]`、`\C[n]`、`\N[n]`、`\.`、`\!`、`\{` 等）由程序内置保护，无需手动配置。

但很多游戏会通过插件引入自定义控制符，例如 `\F[FinF]`（表情差分）、`\AC`（自动换行）、`\MT[xxx]`（自定义标记）等。在启动翻译前，必须把这些自定义控制符写成正则规则，否则它们会被当作普通日文送进模型，导致翻译质量下降甚至译文损坏。

### 扫描候选控制符

```bash
uv run python main.py scan-placeholder-candidates --game "<游戏标题>" --output "<外部临时目录>/placeholder-candidates.json"
```

候选报告会标记每个反斜杠控制符是否已被内置标准规则覆盖，或是否已被当前自定义规则覆盖。需要给 Agent 解析时可加 `--json`。

### 人工确认游戏文本中的自定义控制符

翻看游戏 `data/` 目录下的 JSON 文件（尤其是 `MapXXX.json`、`CommonEvents.json`、`Troops.json`），搜索反斜杠开头的非标准标记。常见形态：

- `\F[xxx]`、`\FH[xxx]` — 表情/立绘差分
- `\AA[xxx]` — 特定插件标记
- `\MT[xxx]` — 消息标记
- `\AC` — 自动换行

### 编写规则文件

推荐先生成规则草稿，再把确认后的规则 JSON 导入当前游戏数据库。键是正则表达式（匹配原始控制符），值是占位符模板（翻译时送给模型的替代文本）：

```json
{
  "(?i)\\\\F\\d*\\[[^\\]]+\\]": "[CUSTOM_FACE_PORTRAIT_{index}]",
  "(?i)\\\\FH\\[[^\\]]+\\]": "[CUSTOM_FACE_HIDE_COMMAND_{index}]",
  "(?i)\\\\AA\\[[^\\]]+\\]": "[CUSTOM_PLUGIN_AA_MARKER_{index}]",
  "(?i)\\\\MT\\[[^\\]]+\\]": "[CUSTOM_PLUGIN_MT_MARKER_{index}]",
  "(?i)\\\\AC(?![A-Za-z\\[])": "[CUSTOM_PLUGIN_AC_MARKER_{index}]"
}
```

规则要点：

- 键必须是合法正则表达式，且不能匹配空字符串
- 值必须包含 `{index}`，用于区分同一规则在同一条原文中的多次命中
- 占位符生成结果必须匹配 `[CUSTOM_NAME_数字]` 格式
- 占位符名称应尽量完整表达用途，例如 `FACE_PORTRAIT`；未知语义时使用 `PLUGIN_<控制符名>_MARKER`

导入数据库：

```bash
uv run python main.py validate-placeholder-rules --game "<游戏标题>" --input "<外部临时目录>/placeholder-rules.json" --json
uv run python main.py import-placeholder-rules --game "<游戏标题>" --input "<外部临时目录>/placeholder-rules.json"
```

如果需要本次命令临时覆盖数据库规则，也可以在翻译时通过 CLI 参数直接传入短 JSON 字符串：

```bash
uv run python main.py translate --game "<游戏标题>" \
  --placeholder-rules '{"(?i)\\\\F\\d*\\[[^\\]]+\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}'
```

翻译前建议先校验并预览规则效果：

```bash
uv run python main.py validate-placeholder-rules --json \
  --game "<游戏标题>" \
  --input "<外部临时目录>/placeholder-rules.json" \
  --sample "\F[FaceId]テキスト\V[1]"
```

报告会展示模型实际看到的占位符文本、占位符映射和还原结果。`--json` 输出为纯 JSON，适合 Agent 直接读取。

---

## 6. 第三步：准备插件文本规则

RPG Maker MZ 的 `js/plugins.js` 中包含所有插件的配置参数。部分插件的参数包含界面文本（按钮文案、提示文字等），这些文本也需要翻译。但 `plugins.js` 结构复杂，程序无法自动判断哪些字段需要翻译，需要外部 Agent 分析后产出规则。

### 6.1 导出插件配置

```bash
uv run python main.py export-plugins-json --game "<游戏标题>" --output "<外部临时目录>/plugins.json"
```

导出的 `plugins.json` 是 `$plugins` 数组的纯 JSON 格式，一个插件一个对象。

### 6.2 外部 Agent 分析并产出规则

把 `plugins.json` 交给外部 Agent（例如 Claude Code），让它分析每个插件参数中的可翻译文本，产出 `plugin-rules.json`：

```json
{
  "PluginNameA": [
    "$['parameters']['message']",
    "$['parameters']['items'][*]['text']"
  ],
  "PluginNameB": [
    "$['parameters']['title']"
  ]
}
```

条目的键是插件名（必须精确匹配 `plugins.js` 中的 `name` 字段），值是该插件中可翻译字段的 JSONPath 模板数组。

参考提示词文档：`docs/plugin-rules-agent-prompt.md`

### 6.3 导入规则到数据库

```bash
uv run python main.py import-plugin-rules --game "<游戏标题>" --input "<外部临时目录>/plugin-rules.json"
```

导入时会校验插件名称、插件哈希和 JSONPath 命中结果，只有校验通过的规则才会写入数据库。如果规则发生变化（插件更新或规则修改），会自动清理对应的失效译文。

---

## 7. 第四步：准备事件指令文本规则

RPG Maker MZ 的事件指令中有部分复杂参数包含可翻译文本（例如"显示文字"之外的插件指令文本），这些文本藏在深层参数中，需要外部 Agent 按指令编码逐一定义提取规则。

### 7.1 导出事件指令参数

使用默认编码（`setting.toml` 中 `event_command_text.default_command_codes` 指定的编码）：

```bash
uv run python main.py export-event-commands-json --game "<游戏标题>" --output "<外部临时目录>/event-commands.json"
```

指定特定编码（覆盖配置文件默认值）：

```bash
uv run python main.py export-event-commands-json --game "<游戏标题>" \
  --output "<外部临时目录>/event-commands.json" --code 357 355
```

### 7.2 外部 Agent 分析并产出规则

把 `event-commands.json` 交给外部 Agent，产出 `event-command-rules.json`：

```json
{
  "357": [
    {
      "match": {
        "0": "PluginName",
        "1": "Show"
      },
      "paths": [
        "$['parameters'][3]['message']"
      ]
    }
  ]
}
```

- 顶层 key：事件指令编码
- `match`：可选，用于缩小匹配范围的参数过滤条件，key 是参数索引字符串，value 是固定参数值
- `paths`：该指令中可翻译文本的 JSONPath 模板数组

参考提示词文档：`docs/event-command-rules-agent-prompt.md`

### 7.3 导入规则到数据库

```bash
uv run python main.py import-event-command-rules --game "<游戏标题>" --input "<外部临时目录>/event-command-rules.json"
```

---

## 8. 第五步：准备名字框与地图名术语

RPG Maker MZ 中，事件指令 101（显示文字）的 `parameters[4]`（第五个参数）指定说话人名字框，MapXXX 的 `displayName` 指定地图显示名。这些名字通常是日文，需要在翻译正文前确定标准译名，以保证全游戏名字一致。

### 8.1 导出名字上下文

```bash
uv run python main.py export-name-context --game "<游戏标题>" --output-dir "<外部临时目录>/name-context"
```

导出产物：

- `<外部临时目录>/name-context/name_registry.json`：大 JSON，包含所有说话人名字和地图显示名
- `<外部临时目录>/name-context/` 下的若干小 JSON：每个说话人的对话片段，供外部 Agent 判断角色性别和性格

### 8.2 外部 Agent 填写译名

外部 Agent 在 `name_registry.json` 中填写每个名字的标准译名。格式为 `原文: 译名` 的映射，未填写的条目保留空字符串。

参考提示词文档：`docs/name-context-agent-prompt.md`

### 8.3 导入术语表到数据库

```bash
uv run python main.py import-name-context --game "<游戏标题>" --input "<外部临时目录>/name-context/name_registry.json"
```

术语表导入后，后续 `translate` 命令会自动把术语注入到翻译提示词中，帮助模型统一人名和地名翻译。

### 8.4 （可选）单独写回名字框和地图名

如果只想写回名字框和地图显示名而不处理正文，可以单独执行：

```bash
uv run python main.py write-name-context --game "<游戏标题>"
```

此命令会根据数据库术语表写回 `101.parameters[4]`（说话人名字框）和 `MapXXX.displayName`（地图显示名），同时保留已有的正文译文。

---

## 9. 第六步：正文翻译

### 9.1 基本用法

```bash
uv run python main.py translate --game "<游戏标题>"
```

此命令执行以下流程：

1. 加载 `setting.toml` 配置和当前游戏数据库中的占位符规则；如果传入 `--placeholder-rules`，则只使用 CLI 字符串
2. 打开游戏数据库，加载游戏数据
3. 读取数据库中的插件规则、事件指令规则和术语表
4. 从游戏 `data/*.json` 和 `js/plugins.js` 中提取所有可翻译文本
5. 用标准控制符和自定义占位符规则替换原文中的控制符
6. 跳过数据库中已有译文的条目（断点续传）
7. 按正文内容去重（同原文、同类型、同角色的条目只送模型一次）
8. 分批送入 LLM 翻译
9. 成功译文写入主翻译表；最终译文问题写入译文质量错误表，模型限流、超时和连接失败写入运行级故障表

### 9.2 CLI 参数说明

```
uv run python main.py translate --game "<游戏标题>" [选项]

选项：
  --placeholder-rules <JSON>     自定义占位符规则 JSON 字符串
  --llm-model <MODEL>            LLM 模型名称
  --llm-timeout <秒>            请求超时
  --translation-token-size <数量> 每批目标 token 上限
  --translation-factor <系数>    字符到 token 的换算系数
  --translation-max-command-items <数量> 同角色连续补充条目上限
  --translation-worker-count <数量> 并发 worker 数
  --translation-rpm <数量>       每分钟请求数限制，传 none 表示不限速
  --translation-retry-count <次数> 可恢复错误重试次数
  --translation-retry-delay <秒> 可恢复错误重试间隔
  --system-prompt <文本>         系统提示词文本
  --event-command-default-code <编码> [<编码> ...] 事件指令默认编码
  --strip-wrapping-punctuation-pair <左> <右> 提取时剥离的成对包裹标点
  --allowed-japanese-char <字符> [<字符> ...] 日文残留检查允许的字符
  --allowed-japanese-tail-char <字符> [<字符> ...] 允许的语气尾音字符
  --line-split-punctuation <标点> [<标点> ...] 长文本优先切行标点
  --long-text-line-width-limit <数量> 长文本单行宽度上限
  --line-width-count-pattern <正则> 宽度计数字符正则
  --source-text-required-pattern <正则> 进入翻译的源语言字符正则
  --japanese-segment-pattern <正则> 日文残留片段识别正则
  --residual-escape-sequence-pattern <正则> 残留检查前剥离的转义序列正则
```

所有 CLI 参数均为可选，未传时使用 `setting.toml` 中的对应值。
全局 `--debug` 需要放在子命令前，例如 `uv run python main.py --debug translate --game "<游戏标题>"`。

### 9.3 翻译范围

正文翻译覆盖以下来源：

- **data 正文**：`MapXXX.json`、`CommonEvents.json`、`Troops.json`、`Actors.json`、`Items.json`、`Skills.json`、`System.json` 等标准 data 文件中满足 `source_text_required_pattern` 的文本字段
- **插件文本**：根据 `import-plugin-rules` 导入的规则从 `js/plugins.js` 中提取
- **事件指令参数文本**：根据 `import-event-command-rules` 导入的规则从事件指令深层参数中提取

### 9.4 进度与日志

翻译过程中终端会显示实时进度条：

```
正文翻译：待翻译 1234 条，去重后 890 条，批次 45 个
⠋ 翻译中 ━━━━━━━━━━━━━━ 234/890
```

详细日志写入 `logs/` 目录，包含每批请求的开始、成功、失败和重试记录。

### 9.5 质量报告

翻译后执行：

```bash
uv run python main.py quality-report --game "<游戏标题>" --output "<外部临时目录>/quality-report.json"
```

报告会统计待翻译数量、最新运行状态、运行级模型故障、译文质量错误、模型原始返回数量、日文残留、占位符风险、超宽行和可写回数量。存在阻断错误时先修正规则、提示词或模型配置，再重新执行 `translate`。

---

## 10. 第七步：回写游戏文件

### 10.1 基本用法

```bash
uv run python main.py write-back --game "<游戏标题>"
```

此命令执行以下流程：

1. 从数据库读取所有成功译文
2. 过滤掉当前提取规则不再覆盖的过期条目
3. 把译文写回 `data/*.json` 中对应位置
4. 把插件译文写回 `js/plugins.js` 中对应位置
5. 如果数据库有术语表，顺便写回 101 名字框和地图显示名
6. 如果配置了替换字体，复制字体文件并替换文件中所有旧字体引用
7. 写出所有修改后的文件

### 10.2 数据保护

回写前会把受影响的原始文件自动备份：

- `data/*.json` → `data_origin/*.json`
- `js/plugins.js` → `js/plugins_origin.js`

### 10.3 字体替换

配置 `setting.toml` 中 `write_back.replacement_font_path` 或在命令行传入 `--replacement-font-path`，回写时会：

1. 把目标字体文件复制到游戏 `fonts/` 目录
2. 在所有即将写出的 `data/*.json` 和 `js/plugins.js` 中搜索旧字体文件名和不带扩展名的字体引用
3. 替换为目标字体文件名

```bash
uv run python main.py write-back --game "<游戏标题>" --replacement-font-path "<字体文件路径>"
```

---

## 11. 一键流水线

如果插件规则、事件指令规则和术语表都已导入数据库，可以用 `run-all` 一次完成翻译和回写：

```bash
uv run python main.py run-all --game "<游戏标题>"
```

此命令等价于 `translate` + `write-back`，支持 `translate` 和 `write-back` 的全部配置覆盖参数。

只翻译写库不写回文件（用于验证翻译质量）：

```bash
uv run python main.py run-all --game "<游戏标题>" --skip-write-back
```

---

## 12. 断点续传与增量翻译

### 12.1 断点续传

`translate` 命令天生支持断点续传。翻译开始前会检查数据库中已有的译文，跳过已完成条目。翻译过程中每批成功结果立即写入数据库，因此即使中途中断（Ctrl+C 或网络故障），下次重新执行 `translate` 时只会处理未完成的条目。

### 12.2 增量翻译

游戏更新后（新增地图、事件或文本），再次执行 `translate` 只会翻译新增和修改过的条目。程序会自动清理不再符合当前提取规则的过期译文。

### 12.3 规则变动后重新翻译

如果修改了插件规则或事件指令规则后重新导入，程序会自动清理对应范围的失效译文。下次 `translate` 会按新规则重新提取并翻译。

---

## 13. 配置体系

### 13.1 配置优先级

模型连接密钥相关配置：

环境变量 > `setting.toml`

其他运行配置：

CLI 参数 > `setting.toml` > 内置默认值

当环境变量 `RPG_MAKER_TOOLS_LLM_BASE_URL` 或 `RPG_MAKER_TOOLS_LLM_API_KEY` 存在时，本次运行优先使用环境变量中的模型地址或密钥。这样可以临时切换服务，同时避免把密钥写进命令行参数日志。

除模型地址和密钥外，当 CLI 传入某参数时，本次运行完全使用 CLI 值，忽略 `setting.toml` 对应字段。未传参数则使用 `setting.toml` 配置。

PowerShell 示例：

```powershell
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "https://api.example.com"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"
uv run python main.py translate --game "<游戏标题>"
```

### 13.2 `setting.toml` 完整说明

项目提交 `setting.example.toml` 作为示例配置。实际运行前复制为 `setting.toml`，并按本机模型服务和游戏需求调整。

```toml
# ── LLM 服务连接 ──
[llm]
base_url = "https://api.deepseek.com"   # 模型服务地址
api_key = "<API_KEY>"                  # API 密钥
model = "deepseek-chat"                  # 模型名称
timeout = 600                            # 请求超时秒数

# ── 正文切批上下文 ──
[translation_context]
token_size = 1024                        # 每批目标 token 上限
factor = 3.5                             # 字符到 token 的换算系数
max_command_items = 5                    # 同角色连续补充条目上限

# ── 正文翻译阶段 ──
[text_translation]
worker_count = 200                       # 并发 worker 数
rpm = 200                                # 每分钟请求数上限
retry_count = 3                          # 可恢复错误重试次数
retry_delay = 2                          # 重试间隔秒数
system_prompt_file = "prompts/text_translation_system.md"  # 提示词文件路径

# ── 事件指令参数 ──
[event_command_text]
default_command_codes = [357]            # 默认导出的事件指令编码

# ── 写回阶段 ──
[write_back]
replacement_font_path = "fonts/NotoSansSC-Regular.ttf"  # 替换字体路径，可选

# ── 文本规则 ──
[text_rules]
strip_wrapping_punctuation_pairs = [["「", "」"]]  # 提取时剥离的成对标点
allowed_japanese_chars = ["っ", "ッ", "ー", "・", "。", "～", "…"]  # 日文残留检查允许的字符
allowed_japanese_tail_chars = ["あ", "い", "う", "え", "お", "っ", "ッ", "ん", "ー", "よ", "ね", "な", "か"]  # 允许作为语气尾音的字符
line_split_punctuations = ["，", "。", "、", "；", "：", "！", "？", "…", "～", "—", "♪", "♡", "）", "】", "」", "』", ",", ".", ";", ":", "!", "?"]  # 长文本优先切行标点
long_text_line_width_limit = 26           # 长文本单行宽度上限
line_width_count_pattern = "\\S"          # 宽度计数字符正则
source_text_required_pattern = "[\\u3040-\\u309F\\u30A0-\\u30FF\\u3400-\\u4DBF\\u4E00-\\u9FFF\\uF900-\\uFAFF]+"  # 源语言字符正则
japanese_segment_pattern = "[\\u3040-\\u309F\\u30A0-\\u30FF]+"  # 日文残留片段识别
residual_escape_sequence_pattern = "\\\\[nrt]"  # 残留检查前剥离的转义序列
```

### 13.3 配置覆盖 CLI 参数速查

| `setting.toml` 字段 | CLI 参数 |
| --- | --- |
| `llm.base_url` | 环境变量 `RPG_MAKER_TOOLS_LLM_BASE_URL` |
| `llm.api_key` | 环境变量 `RPG_MAKER_TOOLS_LLM_API_KEY` |
| `llm.model` | `--llm-model` |
| `llm.timeout` | `--llm-timeout` |
| `translation_context.token_size` | `--translation-token-size` |
| `translation_context.factor` | `--translation-factor` |
| `translation_context.max_command_items` | `--translation-max-command-items` |
| `text_translation.worker_count` | `--translation-worker-count` |
| `text_translation.rpm` | `--translation-rpm`（`none` 表示不限速） |
| `text_translation.retry_count` | `--translation-retry-count` |
| `text_translation.retry_delay` | `--translation-retry-delay` |
| `text_translation.system_prompt_file` | `--system-prompt`（直接传文本，非文件路径） |
| `event_command_text.default_command_codes` | `--event-command-default-code CODE ...` |
| `write_back.replacement_font_path` | `--replacement-font-path` |
| `text_rules.*` 各字段 | 对应 `--strip-wrapping-punctuation-pair`、`--allowed-japanese-char` 等参数 |

---

## 14. 命令速查表

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `list` | 列出已注册游戏 | `uv run python main.py list` |
| `doctor` | 检查项目或目标游戏状态 | `uv run python main.py doctor --game "<标题>" --no-check-llm` |
| `add-game` | 注册新游戏 | `uv run python main.py add-game --path "<游戏根目录>"` |
| `scan-placeholder-candidates` | 扫描疑似自定义控制符 | `uv run python main.py scan-placeholder-candidates --game "<标题>" --output "<临时目录>/placeholder-candidates.json"` |
| `build-placeholder-rules` | 生成占位符规则草稿 | `uv run python main.py build-placeholder-rules --game "<标题>" --output "<临时目录>/placeholder-rules.json"` |
| `validate-placeholder-rules` | 校验并预览占位符规则 | `uv run python main.py validate-placeholder-rules --game "<标题>" --input "<临时目录>/placeholder-rules.json" --json` |
| `import-placeholder-rules` | 导入游戏级占位符规则 | `uv run python main.py import-placeholder-rules --game "<标题>" --input "<临时目录>/placeholder-rules.json"` |
| `prepare-agent-workspace` | 导出 Agent 分析工作区 | `uv run python main.py prepare-agent-workspace --game "<标题>" --output-dir "<临时目录>/agent-workspace"` |
| `validate-agent-workspace` | 校验 Agent 工作区产物 | `uv run python main.py validate-agent-workspace --game "<标题>" --workspace "<临时目录>/agent-workspace" --json` |
| `cleanup-agent-workspace` | 清理 Agent 工作区产物 | `uv run python main.py cleanup-agent-workspace --workspace "<临时目录>/agent-workspace"` |
| `export-plugins-json` | 导出插件配置 | `uv run python main.py export-plugins-json --game "<标题>" --output "<临时目录>/plugins.json"` |
| `validate-plugin-rules` | 校验插件规则字符串 | `uv run python main.py validate-plugin-rules --game "<标题>" --rules '<规则 JSON 字符串>' --json` |
| `import-plugin-rules` | 导入插件规则 | `uv run python main.py import-plugin-rules --game "<标题>" --input "<临时目录>/plugin-rules.json"` |
| `export-event-commands-json` | 导出事件指令参数 | `uv run python main.py export-event-commands-json --game "<标题>" --output "<临时目录>/ec.json" --code 357 355` |
| `validate-event-command-rules` | 校验事件指令规则字符串 | `uv run python main.py validate-event-command-rules --game "<标题>" --rules '<规则 JSON 字符串>' --json` |
| `import-event-command-rules` | 导入事件指令规则 | `uv run python main.py import-event-command-rules --game "<标题>" --input "<临时目录>/ec-rules.json"` |
| `export-name-context` | 导出名字上下文 | `uv run python main.py export-name-context --game "<标题>" --output-dir "<临时目录>/names"` |
| `import-name-context` | 导入术语表 | `uv run python main.py import-name-context --game "<标题>" --input "<临时目录>/names/name_registry.json"` |
| `write-name-context` | 单独写回名字框和地图名 | `uv run python main.py write-name-context --game "<标题>"` |
| `translate` | 正文翻译 | `uv run python main.py translate --game "<标题>"` |
| `translation-status` | 查看最新翻译运行状态 | `uv run python main.py translation-status --game "<标题>" --json` |
| `export-pending-translations` | 导出少量待人工补译条目 | `uv run python main.py export-pending-translations --game "<标题>" --limit 5 --output "<临时目录>/pending.json" --json` |
| `import-manual-translations` | 导入人工补译结果 | `uv run python main.py import-manual-translations --game "<标题>" --input "<临时目录>/pending.json" --json` |
| `quality-report` | 生成翻译质量报告 | `uv run python main.py quality-report --game "<标题>" --output "<临时目录>/quality-report.json"` |
| `write-back` | 回写游戏文件 | `uv run python main.py write-back --game "<标题>"` |
| `run-all` | 翻译 + 回写一步完成 | `uv run python main.py run-all --game "<标题>"` |

---

## 15. 常见问题

### Q: 翻译结果出现乱码或日文残留？

检查当前游戏数据库中的占位符规则或本次 CLI `--placeholder-rules` 是否覆盖了游戏的所有自定义控制符。遗漏的控制符会被当作普通日文送进模型。另外检查 `setting.toml` 中 `text_rules` 的日语残留检测正则是否匹配当前游戏。

### Q: 翻译中途断了怎么办？

直接重新执行 `translate` 命令即可。程序会自动跳过数据库中已有的译文，从断点继续。

### Q: 修改了插件规则后需要重新翻译吗？

重新执行 `import-plugin-rules` 导入新规则后，程序会自动清理失效译文。然后再次执行 `translate` 会按新规则提取并翻译。

### Q: 如何用不同模型翻译不同游戏？

两种方式：

1. 每次执行时用 CLI 参数覆盖模型名，并用环境变量覆盖模型地址或密钥
2. 用 CLI 参数覆盖本次运行配置，或修改本机 `setting.toml` 后再执行

### Q: 只想翻译不写回文件？

```bash
uv run python main.py run-all --game "<游戏标题>" --skip-write-back
```

译文会写入数据库，但不回写到游戏文件。

### Q: 如何验证翻译质量？

1. 先用 `translate --game "<标题>"` 翻译
2. 执行 `quality-report --game "<标题>" --output "<临时目录>/quality-report.json"`
3. 根据报告中的错误类型、模型原始返回、日文残留、占位符风险和超宽行修正规则或提示词
4. 重新执行 `translate` 并再次生成质量报告

### Q: 多个游戏可以共享插件规则吗？

不可以。插件规则绑定到具体游戏的 `plugins.js`（通过插件哈希校验）。不同游戏需要分别导出和导入。

### Q: 日志文件在哪里？

`logs/` 目录下，文件名包含时间戳。终端默认显示 INFO 及以上级别，`--debug` 参数可以显示 DEBUG 级别日志。

### Q: 如何在不联网的情况下测试流程？

可以运行测试套件验证核心逻辑：

```bash
uv run pytest
```

