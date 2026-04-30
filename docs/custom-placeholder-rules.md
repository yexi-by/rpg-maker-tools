# 自定义占位符规则

自定义占位符规则用于保护游戏或插件里额外出现的特殊标记。RPG Maker MZ 标准文本控制符由项目内置保护，额外规则可以写入项目根目录的 `custom_placeholder_rules.json`，也可以在翻译命令中直接传入 JSON 字符串。

## 文件位置

```text
custom_placeholder_rules.json
```

文件内容为空对象 `{}` 或文件不存在时，项目只使用内置 RMMZ 标准控制符规则。

## 测试前置要求

对真实游戏执行翻译测试、回写测试或质量验证前，先扫描当前游戏文本，整理完整的自定义控制符、脚本标记和特殊占位符。确认规则能覆盖当前游戏后，再启动正文翻译。

推荐流程：

```text
扫描当前游戏文本 -> 汇总自定义标记 -> 编写 JSON 规则 -> 验证替换覆盖率 -> 启动翻译
```

如果未确认自定义规则，不要启动会消耗模型额度的翻译命令。

## CLI 传入

`translate` 和 `run-all` 支持为本次运行直接传入规则 JSON 字符串：

```bash
uv run python main.py translate --game "<游戏标题>" --placeholder-rules "{\"\\\\\\\\F\\\\[[^\\\\]]+\\\\]\":\"[CUSTOM_FACE_{index}]\"}"
uv run python main.py run-all --game "<游戏标题>" --placeholder-rules "{\"\\\\\\\\F\\\\[[^\\\\]]+\\\\]\":\"[CUSTOM_FACE_{index}]\"}"
```

传入 `--placeholder-rules` 时，本次运行只解析该字符串，不读取项目根目录默认文件。

## JSON 格式

顶层必须是 JSON 对象：

```json
{
  "正则表达式字符串": "占位符模板字符串"
}
```

示例：

```json
{
  "\\\\js\\[[^\\]]+\\]": "[CUSTOM_JS_{index}]",
  "@name\\[[^\\]]+\\]": "[CUSTOM_NAME_{index}]"
}
```

## 字段含义

- 键：Python 正则表达式字符串。JSON 里反斜杠需要双重转义，例如匹配 `\js[...]` 要写成 `"\\\\js\\[[^\\]]+\\]"`。
- 值：占位符模板字符串。模板必须生成形如 `[CUSTOM_NAME_1]` 的方括号占位符。
- `{index}`：当前翻译条目内的自定义占位符编号，从 `1` 开始递增。

## 替换效果

以上示例会产生如下效果：

```text
原文：こんにちは\V[1] @name[アリス] \js[this.actorName()]
送模：こんにちは[RMMZ_V_1] [CUSTOM_NAME_1] [CUSTOM_JS_2]
译文：你好[RMMZ_V_1] [CUSTOM_NAME_1] [CUSTOM_JS_2]
写回：你好\V[1] @name[アリス] \js[this.actorName()]
```

其中 `[RMMZ_V_1]` 来自内置 RMMZ 标准控制符规则，`[CUSTOM_NAME_1]` 和 `[CUSTOM_JS_2]` 来自 `custom_placeholder_rules.json`。

## 冲突规则

- 自定义规则与标准 RMMZ 控制符从同一位置开始匹配时，自定义规则优先。
- 不同自定义片段不能生成同一个占位符。
- 正则表达式不能匹配空字符串。
- 占位符模板不能生成 `[RMMZ_...]`，该前缀由项目内置规则使用。

## 推荐写法

推荐给每类规则使用稳定前缀：

```json
{
  "@speaker\\[[^\\]]+\\]": "[CUSTOM_SPEAKER_{index}]",
  "\\$\\{[^}]+\\}": "[CUSTOM_TEMPLATE_VAR_{index}]"
}
```

这样模型看到的标记清晰、稳定，写回阶段也能按映射恢复原文。
