# 自定义占位符规则

RPG Maker MZ 标准控制符（`\V[n]`、`\C[n]`、`\N[n]`、`\.`、`\!`、`\{`、`\}` 等）由程序内置保护，无需手动配置。游戏或插件引入的自定义控制符需要通过 JSON 规则文件或 CLI 参数额外声明，否则会被当作普通日文送入模型。

## 文件位置

项目根目录的 `custom_placeholder_rules.json`。文件不存在或内容为空对象 `{}` 时，只使用内置标准控制符保护。

## 为什么需要手动识别

翻看游戏的 `data/MapXXX.json`、`data/CommonEvents.json` 等文件，搜索反斜杠开头的非标准标记。常见自定义控制符形态：

- `\F[FinF]`、`\FH[xxx]` — 表情/立绘差分
- `\AA[xxx]` — 特定插件的标记
- `\MT[xxx]` — 消息模板标记
- `\AC` — 自动换行指令

如果你不确定当前游戏有哪些自定义控制符，可以 grep 游戏 data 目录：

```bash
grep -roP '\\\\[A-Za-z]+(\[[^\]]*\])?' <游戏根目录>/data/ | sort -u
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
  "(?i)\\F\\d*\\[[^\\]]+\\]": "[CUSTOM_FACE_PORTRAIT_{index}]",
  "(?i)\\FH\\[[^\\]]+\\]": "[CUSTOM_FACE_HIDE_COMMAND_{index}]",
  "(?i)\\AA\\[[^\\]]+\\]": "[CUSTOM_PLUGIN_AA_MARKER_{index}]",
  "(?i)\\MT\\[[^\\]]+\\]": "[CUSTOM_PLUGIN_MT_MARKER_{index}]",
  "(?i)\\AC(?![A-Za-z\\[])": "[CUSTOM_PLUGIN_AC_MARKER_{index}]"
}
```

### 键（正则表达式）

- 必须是合法 Python 正则表达式
- JSON 中反斜杠需要双重转义，匹配 `\F[xxx]` 要写成 `"\\F\\[[^\\]]+\\]"`
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
  "(?i)\\F\\d*\\[[^\\]]+\\]": "[CUSTOM_FACE_PORTRAIT_{index}]",
  "(?i)\\AC(?![A-Za-z\\[])": "[CUSTOM_PLUGIN_AC_MARKER_{index}]"
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

## CLI 直接传入

除了使用文件，也可以在 `translate` 或 `run-all` 命令中直接传入 JSON 字符串：

```bash
uv run python main.py translate --game "<游戏标题>" \
  --placeholder-rules '{"(?i)\\\\F\\\\d*\\\\[[^\\\\]]+\\\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}'
```

传入 `--placeholder-rules` 后，本次运行只解析该字符串，不读取 `custom_placeholder_rules.json` 文件。

## 验证规则

在启动会消耗模型额度的翻译任务前，建议先确认规则能覆盖当前游戏的自定义控制符。如果规则有遗漏，翻译结果可能出现控制符被错误翻译或丢失的问题。
