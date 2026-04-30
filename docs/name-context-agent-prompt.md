# Name Context Agent 提示词

本文档供 Claude Code 等外部 Agent 处理 `name-context` 临时目录时参考。在目标游戏的 `name-context` 目录启动交互式 Agent 会话后粘贴以下提示词。

````text
请处理当前目录下的 name-context 任务。

## 任务目标

把 name_registry.json 里的日文名字框和地图显示名翻译成简体中文。

## 参考文件

### name_registry.json

这是你需要修改的唯一文件。结构如下：

```json
{
  "speaker_names": {
    "アリス": "",
    "兵士A": "",
    "???": ""
  },
  "map_display_names": {
    "始まりの町": "",
    "魔王城": ""
  }
}
```

- `speaker_names`：游戏中的说话人名字框（101 事件指令的 `parameters[4]`，也就是第五个参数）
- `map_display_names`：地图显示名（MapXXX.json 的 displayName）
- 你需要把每个空字符串替换为对应的简体中文译名

### speaker_contexts/*.json

这些是每个说话人在游戏中的对白样本，用于判断角色性别、性格和身份。结构如下：

```json
{
  "name": "アリス",
  "dialogue_lines": [
    "おはよう、今日もいい天気だね。",
    "私は冒険者ギルドの受付係よ。"
  ]
}
```

你需要阅读这些对白，判断该名字是人物名、称呼、身份、怪物名还是旁白，给出稳定的中文译名。

## 填写要求

- 只修改 name_registry.json 中值为空字符串的条目
- 保持 JSON 结构不变，不新增字段、不删除字段、不修改 key
- 同一个角色的不同写法（如带冒号、带"声"、带敬称的变体）要统一译名体系
- 地名翻译要自然，像中文 RPG 里的地图名
- 普通身份称呼直接翻译（如"兵士A"→"士兵A"、"村人"→"村民"）
- 角色名按上下文推断性别和性格后给出稳定中文名
- 如果原文包含控制符、变量或特殊符号，原样保留，只翻译可读文本
- 不要在 JSON 里写注释、说明、来源或路径信息
- 输出必须是合法 JSON，UTF-8 编码

## 自检清单

1. name_registry.json 能被 JSON 解析
2. `speaker_names` 和 `map_display_names` 两个顶层键仍然存在
3. 所有原文 key 完全没变
4. 没有新增任何字段
5. 已填写的条目都有合适的译名，未填写的已确认原文确实无需翻译
````
