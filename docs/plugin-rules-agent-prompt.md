# Plugin Rules Agent 提示词

本文档供 Claude Code 等外部 Agent 处理 `plugins.json` 时参考。在包含 `plugins.json` 的临时目录启动交互式 Agent 会话后粘贴以下提示词。

````text
请处理当前目录下的 plugins.json。

## 任务目标

分析 plugins.json 中每个插件的参数，找出玩家在游戏里能看到、需要翻译的文本字段，产出 plugin-rules.json。

## 输入文件

`plugins.json` 是 RPG Maker MZ 的 `$plugins` 数组。每个插件对象包含：

- `name`：插件名
- `status`：启用状态（true/false）
- `description`：插件说明
- `parameters`：插件参数，可能是字符串、数字、布尔值、嵌套对象或数组

## 输出文件

在当前目录创建或覆盖 `plugin-rules.json`。格式为：

```json
{
  "PluginNameA": [
    "$['parameters']['message']",
    "$['parameters']['choices'][*]['text']"
  ],
  "PluginNameB": [
    "$['parameters']['title']"
  ]
}
```

- 顶层每个 key 是插件名，必须精确匹配 plugins.json 中的 `name`
- 每个 value 是字符串数组，每条是一个 JSONPath 路径

## 路径格式

- 必须从 `$['parameters']` 开始
- 对象键使用 `['key']`
- 具体数组成员使用 `[0]`、`[1]` 等索引
- 同一数组下结构完全一致的文本字段可以使用 `[*]` 批量匹配
- 只写字符串叶子的路径，不写中间对象或数组

## 判断标准

**应该写入规则的**：玩家在游戏运行时能看到的文本，例如对话框文字、按钮文案、菜单选项、提示信息

**不要写入规则的**：
- 文件名、路径、URL、图片名、音频名、字体名
- 脚本表达式、开关名、变量名
- 枚举值、布尔值、数字、颜色值
- 明显是内部标识的内容（id、type、mode、key、symbol）
- 布局和样式参数（position、x、y、width、height、volume、pitch、pan）
- `description` 字段通常是给开发者看的插件说明，除非明确游戏运行时会显示

## 其他要求

- `status` 为 false 的插件也要分析其 parameters 结构
- 没有可翻译文本的插件不要写入 plugin-rules.json
- 不要输出候选字段、插件索引、原因说明、注释或任何项目无法导入的额外字段
- 输出必须是合法 JSON，UTF-8 编码

## 自检清单

1. plugin-rules.json 能被 JSON 解析，顶层是对象
2. 每个 key 都是 plugins.json 中存在的插件名
3. 每个 value 都是字符串数组，每条路径以 `$['parameters']` 开头
4. 没有包含文件名、URL、布尔值、数字、枚举值
5. 没有 reason、note、plugin_index 等额外字段
````
