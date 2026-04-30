# Name Context Agent Prompt

本文档提供给 Claude Code、Codex 等外部 Agent 处理 `name-context` 临时目录时使用。使用方式是在目标游戏的 `name-context` 目录启动交互式 Agent 会话，然后粘贴以下提示词。

```text
请处理当前目录下的 name-context。

任务目标：
把 name-context/name_registry.json 里的日文名字框和地图显示名翻译成简体中文。

需要参考：
1. name-context/name_registry.json
   这里有两个字典：
   - speaker_names：角色名、说话人名
   - map_display_names：地图显示名
   字典的 key 是日文原文，value 目前是空字符串。你只需要填写 value。

2. name-context/speaker_contexts/*.json
   每个小 JSON 是某个名字在游戏中的对白样本。
   你需要阅读这些对白，判断这个名字更像人物名、称呼、身份、怪物名、旁白名，给出稳定中文译名。

填写要求：
- 只修改 name-context/name_registry.json。
- 不要修改 speaker_contexts 下的小 JSON。
- 保持 JSON 结构不变。
- 保持所有 key 不变。
- 只填写空字符串 value。
- 不要新增字段。
- 不要删除字段。
- 不要改成数组。
- 不要写 note。
- 不要写解释。
- 不要把文件路径、上下文文件名、来源说明写进 JSON。
- 同一个角色的不同写法要尽量统一，例如带冒号、带“声”、带敬称的名字，要保持译名体系一致。
- 地名翻译要自然，像中文游戏里的地图名。
- 如果是普通身份称呼，例如 “兵士A”“村人”，正常翻译成 “士兵A”“村民”。
- 如果是角色名，按上下文翻译成稳定中文名。
- 如果是无法确定专名但可以直译的内容，正常直译。
- 如果原文里有控制符、变量或符号，必须原样保留这些符号，只翻译可读文本。
- 输出必须是合法 JSON，UTF-8 编码。

完成后请做自检：
1. name_registry.json 能被 JSON 解析。
2. speaker_names 和 map_display_names 两个顶层键仍然存在。
3. 所有原文 key 完全没变。
4. 没有新增任何字段。
5. 没有空译名，除非原文本身确实不应该翻译。
```
