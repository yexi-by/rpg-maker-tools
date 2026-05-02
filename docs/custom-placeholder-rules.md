# 自定义占位符规则

RPG Maker MZ 标准控制符（`\V[n]`、`\C[n]`、`\N[n]`、`\.`、`\!`、`\{`、`\}` 等）由程序内置保护，无需手动配置。游戏或插件引入的自定义控制符需要通过 JSON 规则文件或 CLI 参数额外声明，否则会被当作普通日文送入模型。

## 规则来源

正文翻译默认读取当前游戏数据库中的自定义占位符规则。规则可以通过 `import-placeholder-rules --input` 写入数据库，也可以在 `translate`、`run-all` 和诊断命令中通过 `--placeholder-rules` 传入本次运行专用 JSON 字符串。

项目根目录的 `custom_placeholder_rules.json` 只适合作为人工草稿或示例文件，不是主流程的隐式运行数据源。

## 为什么需要手动识别

翻看游戏的 `data/MapXXX.json`、`data/CommonEvents.json` 等文件，搜索反斜杠开头的非标准标记。常见自定义控制符形态：

- `\F[FinF]`、`\FH[xxx]` — 表情/立绘差分
- `\AA[xxx]` — 特定插件的标记
- `\MT[xxx]` — 消息模板标记
- `\AC` — 自动换行指令

如果你不确定当前游戏有哪些自定义控制符，可以使用扫描命令生成候选报告：

```bash
uv run python main.py scan-placeholder-candidates --game "<游戏标题>" --output "<外部临时目录>/placeholder-candidates.json"
uv run python main.py build-placeholder-rules --game "<游戏标题>" --output "<外部临时目录>/placeholder-rules.json"
```

## JSON 格式

```json
{
  "正则表达式": "占位符模板"
}
```

示例：

```json
{
  "(?i)\\\\F\\d*\\[[^\\]]+\\]": "[CUSTOM_FACE_PORTRAIT_{index}]",
  "(?i)\\\\FH\\[[^\\]]+\\]": "[CUSTOM_FACE_HIDE_COMMAND_{index}]",
  "(?i)\\\\AA\\[[^\\]]+\\]": "[CUSTOM_PLUGIN_AA_MARKER_{index}]",
  "(?i)\\\\MT\\[[^\\]]+\\]": "[CUSTOM_PLUGIN_MT_MARKER_{index}]",
  "(?i)\\\\AC(?![A-Za-z\\[])": "[CUSTOM_PLUGIN_AC_MARKER_{index}]"
}
```

### 键（正则表达式）

- 必须是合法 Python 正则表达式
- JSON 中反斜杠需要按 JSON 和正则两层语义处理，匹配 `\F[xxx]` 要写成 `"\\\\F\\[[^\\]]+\\]"`，JSON 解析后交给 Python 正则的实际模式是 `\\F\[[^\]]+\]`
- 不能匹配空字符串
- 推荐使用 `(?i)` 前缀忽略大小写

### 值（占位符模板）

- 必须生成形如 `[CUSTOM_NAME_数字]` 的方括号占位符
- 必须包含 `{index}`，用于区分同一规则在同一条原文中的多次命中
- 不能生成 `[RMMZ_...]` 前缀（该前缀由内置规则保留）
- 推荐使用能让模型理解粗略用途的完整英文命名，例如 `[CUSTOM_FACE_PORTRAIT_{index}]`
- 如果只知道插件控制符名字、不确定具体语义，使用 `[CUSTOM_PLUGIN_控制符名_MARKER_{index}]`，不要编造含义

### 规则冲突

- 自定义规则与标准 RMMZ 控制符从同一位置开始匹配时，自定义规则优先
- 不同自定义片段不能生成同一个占位符
- 不同原始片段不能被映射到同一个占位符

## 替换效果

假设规则文件内容为：

```json
{
  "(?i)\\\\F\\d*\\[[^\\]]+\\]": "[CUSTOM_FACE_PORTRAIT_{index}]",
  "(?i)\\\\AC(?![A-Za-z\\[])": "[CUSTOM_PLUGIN_AC_MARKER_{index}]"
}
```

翻译流程中的替换过程：

```
原文：  \F[Face01]こんにちは\AC\!
送模：  [CUSTOM_FACE_PORTRAIT_1]こんにちは[CUSTOM_PLUGIN_AC_MARKER_2][RMMZ_WAIT_INPUT]
译文：  [CUSTOM_FACE_PORTRAIT_1]你好[CUSTOM_PLUGIN_AC_MARKER_2][RMMZ_WAIT_INPUT]
写回：  \F[Face01]你好\AC\!
```

其中 `[RMMZ_WAIT_INPUT]` 来自内置标准控制符保护，`[CUSTOM_FACE_PORTRAIT_1]` 和 `[CUSTOM_PLUGIN_AC_MARKER_2]` 来自自定义规则。

## 导入数据库

规则确认后优先从文件写入当前游戏数据库：

```bash
uv run python main.py validate-placeholder-rules --game "<游戏标题>" --input "<外部临时目录>/placeholder-rules.json" --json
uv run python main.py import-placeholder-rules --game "<游戏标题>" --input "<外部临时目录>/placeholder-rules.json"
```

`--rules` 和 `--placeholder-rules` 只适合短规则或一次性排障。规则较长时不要把 JSON 字符串塞进命令行，直接使用 `--input` 文件。

也可以在 `translate` 或 `run-all` 命令中直接传入短 JSON 字符串：

```bash
uv run python main.py translate --game "<游戏标题>" \
  --placeholder-rules '{"(?i)\\\\F\\d*\\[[^\\]]+\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}'
```

传入 `--placeholder-rules` 后，本次运行只解析该字符串，不读取当前游戏数据库中的占位符规则。

## 验证规则

在启动会消耗模型额度的翻译任务前，建议先确认规则能覆盖当前游戏的自定义控制符。如果规则有遗漏，翻译结果可能出现控制符被错误翻译或丢失的问题。

## 校验与预览

启动正文翻译前，先用校验命令确认规则可以被解析，并查看模型最终会看到什么：

```bash
uv run python main.py validate-placeholder-rules --json \
  --game "<游戏标题>" \
  --input "<外部临时目录>/placeholder-rules.json" \
  --sample "\F[FaceId]テキスト\V[1]"
```

也可以直接校验某段 CLI 规则字符串：

```bash
uv run python main.py validate-placeholder-rules --json \
  --placeholder-rules '{"(?i)\\\\F\\d*\\[[^\\]]+\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}' \
  --sample "\F[FaceId]テキスト\V[1]"
```

报告里的关键字段：

- `rules`：每条规则的正则、模板和第一个占位符预览。
- `samples.original_text`：输入样本。
- `samples.text_for_model`：送给模型的占位符文本。
- `samples.restored_text`：按占位符映射还原后的文本。
- `samples.roundtrip_ok`：还原结果是否和原样本完全一致。

`--json` 输出只包含 JSON，适合外部 Agent 直接解析；出现规则错误时也会返回统一 JSON 错误结构。
