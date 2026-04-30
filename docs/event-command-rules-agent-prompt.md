# Event Command Rules Agent 提示词

本文档供 Claude Code 等外部 Agent 处理 `event-commands.json` 时参考。在包含 `event-commands.json` 的临时目录启动交互式 Agent 会话后粘贴以下提示词。

````text
请处理当前目录下的 event-commands.json。

## 任务目标

分析 event-commands.json 中每个事件指令的参数，找出玩家能在游戏里看到、需要翻译的文本字段，产出 event-command-rules.json。

## 输入文件

`event-commands.json` 按事件指令编码分组，结构如下：

```json
{
  "357": [
    [
      "PluginName",
      "Show",
      0,
      {
        "message": "こんにちは",
        "choices": ["はい", "いいえ"]
      }
    ],
    [
      "PluginName",
      "Hide",
      1,
      {}
    ]
  ],
  "355": [
    [
      " scripts here "
    ]
  ]
}
```

- 顶层每个 key 是事件指令编码（如 `"357"`）
- 每个 value 是该编码下所有去重后的 parameters 数组样本
- parameters 是一个数组，各索引位置的含义取决于具体指令

## 输出文件

在当前目录创建或覆盖 `event-command-rules.json`。格式为：

```json
{
  "357": [
    {
      "match": {
        "0": "PluginName"
      },
      "paths": [
        "$['parameters'][3]['message']",
        "$['parameters'][3]['choices'][*]"
      ]
    }
  ],
  "355": []
}
```

- 顶层 key 必须来自 event-commands.json 的顶层 key
- 每个 value 是规则对象数组，每个规则对象包含 `match` 和 `paths`

## match 编写要求

- `match` 是一个对象，key 是参数索引（如 `"0"`、`"1"`），value 是该索引上的固定字符串值
- `match` 的作用是区分同一编码下的不同指令类型（例如不同插件的指令）
- 应选择能稳定区分的字段作为 match，如插件名、命令名
- 不要把大段玩家可见文本放进 match
- 如果该编码下所有样本结构完全一致，可以使用空对象 `{}`

## paths 编写要求

- 路径必须从 `$['parameters']` 开始
- 对象键使用 `['key']`
- 具体数组成员使用 `[0]`、`[1]` 等索引
- 同一数组下结构一致的文本字段可以使用 `[*]`
- 只写字符串叶子的路径

## 判断标准

**应该写入 paths 的**：玩家在游戏运行时能看到的文本，例如对话框文字、选项、提示信息

**不要写入 paths 的**：
- 文件名、路径、URL、图片名、音频名、字体名
- 脚本表达式、开关名、变量名
- 枚举值、布尔值、数字、颜色值
- 明显是内部标识的内容（id、type、mode、key、symbol）
- 用于 match 的插件名、命令名本身
- 布局和样式参数

## 其他要求

- 没有可翻译文本的编码可以写成空数组 `[]` 或不写该编码
- 不要输出 reason、note、rule_id、schema_version 等额外字段
- 输出必须是合法 JSON，UTF-8 编码

## 自检清单

1. event-command-rules.json 能被 JSON 解析
2. 顶层 key 全部来自 event-commands.json
3. 每个 value 都是数组，数组中每个对象只包含 `match` 和 `paths`
4. `match` 是对象（可能为空对象），`paths` 是字符串数组
5. 每条路径以 `$['parameters']` 开头
6. 没有包含文件名、URL、布尔值、数字、枚举值
7. 没有 reason、note、rule_id 等额外字段
````
