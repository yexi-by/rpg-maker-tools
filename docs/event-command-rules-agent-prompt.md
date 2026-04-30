# Event Command Rules Agent Prompt

本文档提供给 Claude Code、Codex 等外部 Agent 处理 `event-commands.json` 时使用。使用方式是在包含 `event-commands.json` 的临时目录启动交互式 Agent 会话，然后粘贴以下提示词。

```text
请处理当前目录下的 event-commands.json。

任务目标：
阅读 event-commands.json，判断事件指令参数里哪些字符串是玩家能在游戏里看到、需要翻译的文本，并产出可被项目导入的事件指令规则 JSON。

需要参考：
1. ./event-commands.json
   这是按事件指令编码分组导出的 parameters 样本。
   顶层 key 是事件指令编码，例如 "357"。
   顶层 value 是该编码下去重后的 parameters 数组样本。

输出文件：
- 请新建或覆盖当前目录下的 event-command-rules.json。
- event-command-rules.json 必须是合法 JSON，UTF-8 编码。

输出格式：
{
  "357": [
    {
      "match": {
        "0": "TestPlugin",
        "1": "Show"
      },
      "paths": [
        "$['parameters'][3]['message']"
      ]
    }
  ]
}

字段含义：
- 顶层 key：事件指令编码，必须来自 event-commands.json 的顶层 key。
- match：用于识别同一类事件指令参数的固定条件。
- paths：这一类事件指令参数中需要翻译的字符串叶子路径。

match 编写要求：
- match 的 key 必须是参数索引字符串，例如 "0"、"1"。
- match 的 value 必须是该参数索引上的字符串原文。
- match 应该选择能稳定区分命令类型的字段，例如插件名、命令名、子命令名。
- 不要把大段玩家可见文本放进 match。
- 不要把会频繁变化的正文内容放进 match。
- 如果某个编码下所有样本结构完全一致，可以使用空对象 {} 作为 match。

paths 编写要求：
- 路径必须从 $['parameters'] 开始。
- 数组索引用 [0]、[1] 等具体索引。
- 对象键使用 ['key']。
- 同一数组下结构一致的文本字段可以使用 [*]。
- 如果参数中的字符串本身是 JSON 容器，需要把容器内部的玩家可见字符串路径写出来。
- 只把玩家可见文本对应的字符串叶子写入 paths。

过滤要求：
- 文件名、路径、URL、图片名、音频名、字体名、脚本表达式、开关名、变量名、枚举值、布尔值、数字、颜色值不要写入规则。
- 明显是内部 key、symbol、id、type、mode、position、x、y、width、height、volume、pitch、pan 的内容不要写入规则。
- 插件名、命令名、参数名通常用于 match，不作为 paths 里的翻译文本。
- 没有可翻译文本的事件指令编码可以写成空数组，也可以不写该编码。

输出要求：
- 不要输出候选字段。
- 不要输出 reason。
- 不要输出 note。
- 不要输出 rule_id。
- 不要输出游戏标题。
- 不要输出 schema_version。
- 不要新增项目无法导入的字段。

完成后请做自检：
1. event-command-rules.json 能被 JSON 解析。
2. 顶层是对象。
3. 顶层 key 全部来自 event-commands.json。
4. 每个 value 都是数组。
5. 每个规则对象只包含 match 和 paths。
6. match 是对象，paths 是字符串数组。
7. 每条路径都以 $['parameters'] 开头。
8. 没有 reason、note、schema_version、game_title、rule_id 等字段。
9. 不包含明显资源路径、文件名、URL、布尔值、数字和枚举值。
```
