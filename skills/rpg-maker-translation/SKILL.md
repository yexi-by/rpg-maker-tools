---
name: rpg-maker-translation
description: Use this skill when Codex needs to operate this RPG Maker MZ translation toolkit as an external Agent workflow. Covers project discovery, environment checks, missing LLM configuration, game validation, encrypted or non-RMMZ data handling, custom placeholder scanning, external rule preparation, translation loops, quality-report triage, failure escalation, and write-back gating.
---

# RPG Maker MZ 翻译工具包

## 核心目标

把本项目作为可执行翻译工具包使用。CLI 负责确定性工作，Agent 负责判断、补规则、读报告、处理失败、及时向用户反馈。

## 快速判断

使用本 Skill 的任务通常包含这些关键词或目标：RPG Maker MZ、游戏翻译、日文转简体中文、`data/*.json`、`plugins.js`、术语表、名字框、插件规则、事件指令规则、占位符、写回。

不要把本 Skill 用在：非 RPG Maker MZ 游戏、图片翻译、视频字幕、人工校对纯文本、无法取得可解析 `data/*.json` 的游戏。

## 不可跳过的红线

- 未确认项目目录、游戏目录、模型配置和写回许可前，不启动会消耗模型额度的命令。
- 模型地址和 API Key 只能通过环境变量传入，不写进 CLI 参数、文档、日志说明或临时文件。
- 自定义控制符规则未确认覆盖前，不执行 `translate`。
- 外部临时 JSON 不能作为主流程数据源，必须通过 `import-*` 命令写入数据库。
- `quality-report` 存在阻断错误时，不执行 `write-back`。
- 找不到项目、游戏不可识别、数据不可解析、模型不可用、连续多轮失败时，暂停并向用户反馈。

## 输入清单

开始前收集：

- 项目目录：包含 `main.py`、`pyproject.toml`、`setting.example.toml` 和 `app/`。
- 游戏根目录：RPG Maker MZ 游戏目录。
- 外部临时目录：保存导出文件、Agent 产物和质量报告。
- 模型环境变量：`RPG_MAKER_TOOLS_LLM_BASE_URL`、`RPG_MAKER_TOOLS_LLM_API_KEY`。
- 写回许可：用户是否允许最终修改游戏副本。

输入缺失时：

- 缺项目目录：先检查当前目录；再检查用户给出的候选目录；仍找不到就请求用户提供项目目录。
- 缺游戏目录：请求用户提供游戏根目录，不扫描整盘猜测。
- 缺外部临时目录：在用户允许的位置创建本次任务专用目录。
- 缺模型环境变量：只运行无需模型的检查、导出和扫描；翻译前要求用户配置环境变量。
- 缺写回许可：可以翻译和生成质量报告，停止在 `write-back` 前。
- 当前 Agent 没有文件或命令执行能力：不要假装已经运行；输出用户可手动执行的命令和检查清单。

## 状态机

### State RT0：项目未知

症状：没有明确项目目录，或当前目录不是项目根目录。

行动：

1. 检查当前目录是否同时存在 `main.py`、`pyproject.toml`、`setting.example.toml`、`app/`。
2. 如果用户给了候选目录，只在候选目录中查找上述标记。
3. 找到唯一项目后进入项目目录。
4. 找到多个项目时列出候选，让用户选择。
5. 找不到项目时停止并请求项目目录。

不要做：扫描整盘、猜测隐藏目录、把不完整目录当项目根目录。

### State RT1：项目环境未验证

症状：项目目录已知，但依赖、Python、uv 或 CLI 状态未知。

行动：

```bash
uv sync
uv run python main.py --help
uv run python main.py doctor --no-check-llm
```

处理：

- `setting.toml` 不存在且 `setting.example.toml` 存在：先复制示例配置为本地配置。
- `uv` 不可用：提示用户安装或修复 `uv`。
- Python 版本不满足：提示用户切换到项目要求版本。
- `doctor --no-check-llm` 失败：读取终端摘要和日志线索，修复配置或向用户反馈。

### State RT2：游戏候选未确认

症状：用户提供了游戏目录，但未确认是否可处理。

先检查：

- 存在 `data/`。
- 存在 `data/System.json`。
- 存在 `data/MapInfos.json`。
- 通常存在 `js/plugins.js`。
- 标准 data JSON 可解析。

行动：

```bash
uv run python main.py add-game --path "<游戏根目录>"
uv run python main.py doctor --game "<游戏标题>" --no-check-llm
```

处理：

- `data/` 缺失：停止，说明目标不是可处理的游戏根目录。
- 核心 JSON 缺失：停止，说明需要标准 RPG Maker MZ data 文件。
- JSON 无法解析、乱码、打包或加密：停止，说明需要先取得可解析的 data JSON。
- 只有图片、音频或资源文件加密，但 `data/*.json` 可读：可以继续。
- 看起来不是 RPG Maker MZ：停止，请用户确认游戏版本和目录。

### State RT3：模型未配置或不可用

症状：环境变量缺失、模型连通性失败、认证失败、模型拒绝请求。

行动：

```bash
uv run python main.py doctor --game "<游戏标题>"
```

处理：

- 环境变量缺失：暂停翻译，要求用户设置 `RPG_MAKER_TOOLS_LLM_BASE_URL` 和 `RPG_MAKER_TOOLS_LLM_API_KEY`。
- 认证失败：报告认证失败，不打印密钥。
- 模型不存在或服务拒绝：报告模型配置问题，建议用户更换模型或服务。
- 内容审查拒绝：不要盲目重试同一模型；建议用户换可处理当前文本的模型。
- 网络或超时：可建议降低并发、增加超时或稍后重试。

无模型时仍可执行：`doctor --no-check-llm`、`add-game`、`scan-placeholder-candidates`、`export-*`、`import-*`、`quality-report`。

### State RT4：自定义控制符未确认

症状：尚未扫描当前游戏的自定义控制符，或扫描报告存在未覆盖候选。

行动：

```bash
uv run python main.py scan-placeholder-candidates --game "<游戏标题>" --output "<外部临时目录>/placeholder-candidates.json"
```

处理：

- 候选全部已覆盖：继续。
- 存在未覆盖候选：编写自定义占位符规则，再复查。
- 不确定候选含义：结合出现形态、插件语法和上下文判断；仍不确定时询问用户。
- 未覆盖候选仍存在：不执行 `translate`。

复查：

```bash
uv run python main.py scan-placeholder-candidates --game "<游戏标题>" --placeholder-rules '<规则 JSON 字符串>' --json
```

### State RT5：外部规则未准备

症状：术语表、插件规则或事件指令规则尚未导入数据库。

执行外部规则任务前，按任务类型读取对应提示词文档：

- 术语表：`docs/name-context-agent-prompt.md`
- 插件规则：`docs/plugin-rules-agent-prompt.md`
- 事件指令规则：`docs/event-command-rules-agent-prompt.md`

术语表：

```bash
uv run python main.py export-name-context --game "<游戏标题>" --output-dir "<外部临时目录>/name-context"
uv run python main.py import-name-context --game "<游戏标题>" --input "<外部临时目录>/name-context/name_registry.json"
```

`name_registry.json` 必须保持最小结构。顶层只有 `speaker_names` 和 `map_display_names` 两个对象，key 是原文，value 是译名。只填写 value，不新增字段，不删除 key，不改数组。

插件规则：

```bash
uv run python main.py export-plugins-json --game "<游戏标题>" --output "<外部临时目录>/plugins.json"
uv run python main.py import-plugin-rules --game "<游戏标题>" --input "<外部临时目录>/plugin-rules.json"
```

`plugin-rules.json` 必须是对象。key 是插件名，value 是该插件中可翻译字符串叶子的 JSONPath 数组：

```json
{
  "PluginName": [
    "$['parameters']['message']",
    "$['parameters']['items'][*]['text']"
  ]
}
```

事件指令规则：

```bash
uv run python main.py export-event-commands-json --game "<游戏标题>" --output "<外部临时目录>/event-commands.json"
uv run python main.py import-event-command-rules --game "<游戏标题>" --input "<外部临时目录>/event-command-rules.json"
```

`event-command-rules.json` 必须是对象。顶层 key 是事件指令编码字符串，value 是规则对象数组；每个规则对象包含 `match` 和 `paths`：

```json
{
  "357": [
    {
      "match": {
        "0": "PluginName"
      },
      "paths": [
        "$['parameters'][3]['message']"
      ]
    }
  ]
}
```

`match` 用于区分同一事件指令编码下的不同结构，key 是参数索引字符串，value 是固定参数值。`paths` 是可翻译字符串叶子的 JSONPath 数组。

判断标准：

- 术语表只填译名值，不改变原文 key 和结构。
- 插件规则只选玩家可见文本，不选路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和布局参数。
- 事件指令规则只选玩家可见文本叶子；需要覆盖默认编码时显式传入 `--code`。
- 导入失败时先修正规则文件，不绕过校验。
 - 需要机器可读诊断时，优先使用 `--json` 或 `--output`，不要从终端彩色日志里猜状态。

### State RT6：翻译运行中

行动：

```bash
uv run python main.py translate --game "<游戏标题>"
uv run python main.py quality-report --game "<游戏标题>" --output "<外部临时目录>/quality-report.json"
```

读报告顺序：

1. 错误表总数和错误类型。
2. 是否存在模型原始返回。
3. 占位符风险数量。
4. 日文残留数量。
5. 长文本超宽行数量。
6. 待翻译数量和可写回数量。

处理：

- 待翻译数量下降、错误数量下降：可以继续下一轮。
- 模型返回非 JSON：查看模型原始返回，优先调提示词、降低并发或换模型。
- 占位符缺失、重复或无法还原：先修正规则，再重跑。
- 日文残留：区分允许字符、专名、拟声词和漏翻；漏翻时重跑或换模型。
- 长文本超宽：调整行宽配置或提示词，再重跑相关条目。

### State RT7：翻译反复失败

触发条件：

- 同一类错误连续三轮没有明显下降。
- 模型持续返回拒绝、空内容、非 JSON 或无关内容。
- 某些条目反复失败，且错误原因相同。
- 占位符规则无法判断，继续翻译可能破坏游戏。

行动：

1. 停止盲目重跑。
2. 生成或读取最新 `quality-report`。
3. 按错误类型分组。
4. 抽取每类错误的模型原始返回摘要，不暴露密钥。
5. 给用户一个可选择的下一步。

可建议的下一步：

- 更换模型。
- 降低并发和 RPM。
- 补充自定义占位符规则。
- 调整系统提示词。
- 将少量失败条目交给人工或专门 Agent 处理。
- 暂时跳过失败条目，不执行写回。

### State RT8：写回门禁

写回前必须满足：

- 用户明确允许写回。
- 最新 `quality-report` 无阻断错误。
- 自定义控制符已覆盖。
- 术语表、插件规则和事件指令规则已按需求导入。
- 目标游戏目录可写。

检查：

```bash
uv run python main.py quality-report --game "<游戏标题>" --json
```

执行：

```bash
uv run python main.py write-back --game "<游戏标题>"
uv run python main.py doctor --game "<游戏标题>" --no-check-llm
```

不满足门禁时，停止并向用户说明差哪一项。

### State RT9：交付报告

完成后向用户报告：

- 注册的游戏标题。
- 已执行的关键命令。
- 翻译数量、失败数量、风险数量。
- 写回是否执行。
- 留档和质量报告位置。
- 建议用户实机抽查的重点区域。

## 反馈模板

遇到阻断时使用这个结构向用户反馈：

```text
当前阶段：<阶段>
已完成：<已执行命令或产物>
阻断原因：<一句话说清楚>
证据：<doctor 或 quality-report 的关键数量/错误类型>
我建议：
1. <下一步选项>
2. <下一步选项>
需要你提供：<缺失输入或确认项>
```

## 常见问题处理表

| 情况 | Agent 动作 |
| --- | --- |
| 用户没给项目目录 | 检查当前目录和候选目录；仍找不到就请求项目目录 |
| Agent 找不到项目 | 停止，报告已检查的位置和缺少的项目标记 |
| 用户没给游戏目录 | 请求游戏根目录，不扫描整盘猜测 |
| 游戏不是 RMMZ | 停止，说明需要标准 RPG Maker MZ 目录 |
| data JSON 被加密或不可解析 | 停止，要求先取得可解析 data JSON |
| 只有资源文件加密 | data JSON 可读时继续 |
| LLM 未配置 | 只跑无需模型的命令；翻译前要求配置环境变量 |
| 当前 Agent 无命令执行能力 | 输出可手动执行的命令和检查清单 |
| 模型连接失败 | 报告连接、认证或模型配置问题，不打印密钥 |
| 模型审查拒绝 | 建议更换模型，不盲目重试 |
| 模型输出非 JSON | 查看原始返回，调提示词、降并发或换模型 |
| 占位符风险 | 先修正规则，不写回 |
| 多轮失败无改善 | 停止重跑，按错误类型向用户汇报 |
| 写回未授权 | 停止在质量报告阶段 |

## 反模式

- 看到报错就无限重跑。
- 没有扫描自定义控制符就开始翻译。
- 把 API Key 写进 CLI 参数。
- 直接读取外部临时 JSON 参与主流程。
- 把非 RMMZ 或加密 data 当作可处理目标。
- quality-report 有阻断错误仍写回。
- 为了完成任务隐藏失败条目。

## 详细文档

- 用户交给 Agent 的任务模板：`docs/agent-user-guide.md`
- Agent 固定工作流：`docs/agent-workflow.md`
- 自定义占位符规则：`docs/custom-placeholder-rules.md`
- 名字框与地图名提示词：`docs/name-context-agent-prompt.md`
- 插件规则提示词：`docs/plugin-rules-agent-prompt.md`
- 事件指令规则提示词：`docs/event-command-rules-agent-prompt.md`
