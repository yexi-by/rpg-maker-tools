# Plugin Rules Agent Prompt

本文档提供给 Claude Code、Codex 等外部 Agent 处理 `plugins.json` 时使用。使用方式是在包含 `plugins.json` 的临时目录启动交互式 Agent 会话，然后粘贴以下提示词。

```text
请处理当前目录下的 plugins.json。

任务目标：
阅读 plugins.json，判断哪些插件参数字符串是玩家能在游戏里看到、需要翻译的文本，并产出可被项目导入的插件规则 JSON。

需要参考：
1. ./plugins.json
   这是 RPG Maker MZ 的 $plugins 数组本体转成的 JSON。
   每个插件通常包含：
   - name：插件名
   - status：启用状态
   - description：插件说明
   - parameters：插件参数树

输出文件：
- 请新建或覆盖当前目录下的 plugin-rules.json。
- plugin-rules.json 必须是合法 JSON，UTF-8 编码。

输出格式：
{
  "插件名": [
    "$['parameters']['Message']",
    "$['parameters']['Choices'][*]['text']"
  ]
}

判断要求：
- 只把玩家可见文本对应的字符串叶子写入路径数组。
- 插件名必须使用 plugins.json 中的 name 原文。
- 路径必须从 $['parameters'] 开始。
- 对象键使用 ['key']。
- 数组索引用 [0]、[1] 等具体索引。
- 同一数组下结构一致的文本字段可以使用 [*]。
- 如果字符串本身是 JSON 容器，需要把容器内部的玩家可见字符串路径写出来。
- 没有可翻译文本的插件不要写入 plugin-rules.json。
- 不要输出候选字段。
- 不要输出 reason。
- 不要输出 note。
- 不要输出插件索引。
- 不要输出游戏标题。
- 不要输出 schema_version。
- 不要新增项目无法导入的字段。

过滤要求：
- 文件名、路径、URL、图片名、音频名、字体名、脚本表达式、开关名、变量名、枚举值、布尔值、数字、颜色值不要写入规则。
- 明显是内部 key、symbol、id、type、mode、position、x、y、width、height、volume、pitch、pan 的内容不要写入规则。
- description 字段通常是插件说明，不作为玩家可见游戏文本导入，除非上下文明确表示游戏运行时会显示它。
- status 为 false 的插件仍可分析参数结构，但规则必须以当前 plugins.json 真实插件名为准。

完成后请做自检：
1. plugin-rules.json 能被 JSON 解析。
2. 顶层是对象。
3. 顶层 key 全部是 plugins.json 中存在的插件名。
4. 每个 value 都是字符串数组。
5. 每条路径都以 $['parameters'] 开头。
6. 没有 reason、note、schema_version、game_title、plugin_index 等字段。
7. 不包含明显资源路径、文件名、URL、布尔值、数字和枚举值。
```
