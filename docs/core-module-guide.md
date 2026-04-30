# 核心模块联动说明

这份文档面向后续接手者，解释当前 v2 核心骨架如何从 CLI 命令一路走到文本提取、提示词组装、LLM 请求、缓存续跑、数据库落库和游戏文件回写。

配套的图片版模块解释图见 `docs/module-diagrams.md`，图片资源统一放在 `docs/images/modules/`。

## 1. 总体流水线

当前项目以命令行流水线为入口。`run-all` 的执行顺序固定为：

```text
CLI 参数解析
  -> 应用层加载配置与游戏
  -> 从数据库读取已导入插件规则和术语表
  -> 标准 data 与插件文本提取
  -> 翻译缓存去重
  -> 提示词组装并注入已填写标准名
  -> OpenAI 兼容接口请求
  -> 译文校验与缓存展开
  -> SQLite 落库
  -> 游戏 data/plugins.js 与数据库术语表回写
```

如果正文翻译被阻断，或正文翻译产生错误条目，流水线会停止在失败阶段，修复问题后再执行回写。

## 2. CLI 层

入口文件：`main.py`、`app/cli.py`

CLI 层职责：

- 解析子命令和参数。
- 初始化日志。
- 创建并关闭 `TranslationHandler`。
- 把业务摘要转换成退出码和中文错误信息。

游戏文件读取、数据库访问和模型调用由应用层统一编排。后续如果要把同一套核心能力接到其他入口，例如脚本任务或 Web API，可以复用应用层。

## 3. 应用编排层

目录：`app/application/`

核心文件：

- `handler.py`：负责游戏登记、外部文件导入、正文翻译、回写四个用例的主流程。
- `runtime.py`：加载 `setting.toml`，注册 OpenAI 兼容 LLM 服务。
- `summaries.py`：定义外部导入、正文翻译和写回的任务摘要。
- `file_writer.py`：负责回写时的受影响文件原件备份与激活版文件替换。

应用层是“业务剧本”，它调用 RMMZ、插件文本、持久化、翻译和 LLM 模块，但不把这些模块的内部细节混在一起。

## 4. 配置层

目录：`app/config/`、`app/utils/config_loader_utils.py`

唯一配置入口为项目根目录的 `setting.toml`。

LLM 配置面向 OpenAI 兼容正文服务。插件规则和术语表译名来自外部 Agent 产物的显式导入。

```toml
[llm]
base_url = "https://api.example.com"
api_key = "YOUR_TEXT_API_KEY"
model = "model-name"
timeout = 600
```

文本判断规则集中在 `[text_rules]`，包括：

- RPG Maker 控制符前缀、百分号控制符前缀、控制符名称正则和参数分隔符。
- 简单控制符、复杂控制符、百分号控制符、符号控制符的占位符模板。
- 复杂控制符是否复用相同占位符，以及哪些分隔符按非嵌套方式解析。
- 翻译占位符正则和译文残留控制符清理规则。
- 357 插件命令文本字段关键词。
- 路径、图片、脚本表达式等不可翻译内容过滤规则。
- 日文残留检查规则。
- 长文本切行相关标点、长度限制和计数字符正则。

这些配置存在的目的不是“把默认 RMMZ 规则写成配置”，而是允许不同游戏按自己的文本风格调整占位符策略。例如某些插件可能使用自定义控制符前缀、尖括号脚本参数或特殊 `%` 序列，这些都应该优先通过 `setting.toml` 调整，而不是改业务代码。

## 5. RMMZ 标准数据层

目录：`app/rmmz/`

职责拆分：

- `loader.py`：加载 RPG Maker MZ 官方标准 data 文件和 `js/plugins.js`。
- `game_data.py`：RMMZ 原始数据模型，例如事件命令、地图、公共事件、敌群和系统词汇。
- `schema.py`：业务聚合模型，例如 `GameData`、`TranslationItem`、插件规则快照。
- `extraction.py`：从标准 data 中提取可翻译正文。
- `write_back.py`：把已翻译正文写回 `GameData.writable_data`。
- `text_rules.py`：文本判断门面。
- `json_types.py`：JSON 值类型与边界收窄工具。
- `commands.py`：统一遍历地图、公共事件、敌群里的事件命令。
- `probe.py`：加载后执行对话结构探针，发现结构异常的孤立 401 文本时 fail-fast。

### 标准文件范围

处理这些标准文件：

```text
Actors.json, Animations.json, Armors.json, Classes.json, CommonEvents.json,
Enemies.json, Items.json, MapInfos.json, MapXXX.json, Skills.json,
States.json, System.json, Tilesets.json, Troops.json, Weapons.json
```

未知 `data/*.json` 默认跳过并写入 DEBUG 文件日志，不进入业务流程。

## 6. 外部标准名上下文

目录：`app/name_context/`

这个模块管理 `101` 名字框与 `MapXXX.displayName` 的标准译名。译名由外部 Agent 根据导出上下文填写，例如 Claude Code、Codex 或其他专门审校工具。

导出命令必须指定项目外临时目录：

```bash
uv run python main.py export-name-context --game "游戏标题" --output-dir "D:/tmp/name-context/游戏标题"
```

产物：

```text
D:/tmp/name-context/游戏标题/name_registry.json
D:/tmp/name-context/游戏标题/speaker_contexts/*.json
```

大 JSON `name_registry.json` 保存每个名字框和地图显示名：

```json
{
  "entry_id": "speaker_name_Map001_json_1_0_0_xxx",
  "kind": "speaker_name",
  "source_text": "村人",
  "translated_text": "",
  "locations": [
    {
      "location_path": "Map001.json/1/0/0",
      "file_name": "Map001.json",
      "map_display_name": "始まりの町",
      "event_id": 1,
      "event_name": "村人",
      "page_index": 0,
      "command_index": 0,
      "context_file": "speaker_contexts/speaker_name_Map001_json_1_0_0_xxx.json"
    }
  ],
  "note": ""
}
```

小 JSON 保存该 `101` 后续连续 `401` 的真实对白：

```json
{
  "entry_id": "speaker_name_Map001_json_1_0_0_xxx",
  "source_text": "村人",
  "dialogue_lines": ["マップこんにちは"]
}
```

外部 Agent 填写大 JSON 中的 `translated_text`。填写后必须显式导入数据库：

```bash
uv run python main.py import-name-context --game "游戏标题" --input "D:/tmp/name-context/游戏标题/name_registry.json"
```

导入后，项目会在两个地方读取数据库：

- 正文翻译：`translation/context.py` 按当前地图和当前批次的 `101` 位置挑选相关标准名，注入用户提示词。
- 回写：`name_context/write_back.py` 把数据库译名写回 `101.parameters[4]` 和 `MapXXX.json.displayName`。

单独写回标准名：

```bash
uv run python main.py write-name-context --game "游戏标题"
```

`write-back` 会读取数据库术语表；存在已填写译名时同步回写 `101` 名字框与地图显示名。

## 7. 文本提取层

正文来源分为三类：

- 事件命令文本：101 作为对白块起点和角色上下文、401 对白、102 选项、405 滚动文本、357 插件命令参数。
- `System.json`：游戏标题、系统词汇、命令词、参数名、提示消息。
- 基础数据库：名称、昵称、简介、说明和战斗消息。

每个文本都会变成一个 `TranslationItem`，关键字段包括：

- `location_path`：回写定位路径，例如 `Map001.json/1/0/0`。
- `item_type`：`long_text`、`array` 或 `short_text`。
- `role`：角色名或旁白。
- `original_lines`：原文行。
- `translation_lines`：最终译文行。
- `placeholder_map`：控制符占位符到原始控制符的映射。

## 8. 占位符替换机制

RPG Maker 文本里常见控制符，例如：

```text
\V[1]
\N[3]
\G
%12
```

这些控制符不能交给模型随意改写。翻译前，`TranslationItem.build_placeholders()` 会调用 `TextRules.replace_rm_control_sequences()`，把控制符替换成更稳定的占位符：

```text
こんにちは\V[1]%12\G
```

会变成：

```text
こんにちは[V_1][P_12][G_0]
```

上面的 `[V_1]`、`[P_12]`、`[G_0]` 只是默认配置效果，不是写死在业务里的格式。相关配置位于 `[text_rules]`：

```toml
control_code_prefix = "\\"
percent_control_prefix = "%"
control_code_name_pattern = "[A-Za-z]+"
control_param_delimiters = [["[", "]"], ["<", ">"]]
line_width_count_pattern = "[\\u3400-\\u4DBF\\u4E00-\\u9FFF\\uF900-\\uFAFF]"
simple_control_placeholder_template = "[{code}_{param}]"
percent_placeholder_template = "[P_{param}]"
symbol_placeholder_template = "[S_{index}]"
complex_control_placeholder_template = "[RM_{index}]"
```

如果某个游戏希望模型看到更不容易误改的 XML 风格标记，可以改成：

```toml
simple_control_placeholder_template = "<RM:{code}:{param}>"
percent_placeholder_template = "<PERCENT:{param}>"
symbol_placeholder_template = "<SYMBOL:{index}>"
complex_control_placeholder_template = "<RAW:{index}>"
```

模型返回后会执行两步：

1. `verify_placeholders()`：检查译文里的占位符数量是否和原文一致。
2. `restore_placeholders()`：把 `[V_1]`、`[P_12]`、`[G_0]` 恢复成 `\V[1]`、`%12`、`\G`。

这样可以让模型理解上下文，同时最大限度保护游戏运行时控制符。

占位符替换负责保护运行时控制符。`101` 名字框和 `MapXXX.displayName` 的标准译名来自数据库中已导入的术语表，并在提示词组装阶段注入。

## 9. 插件文本模块

目录：`app/plugin_text/`

插件文本分两种来源：

- `plugins.js` 的插件参数树。
- 标准事件命令 357 中的插件命令参数。

### plugins.js 外部规则导入

插件参数树的可翻译字段由外部 Agent 判断并产出插件规则 JSON；项目负责校验路径是否命中当前 `plugins.js` 的字符串叶子，然后写入当前游戏数据库。

插件配置导出命令：

```bash
uv run python main.py export-plugins-json --game "游戏标题" --output "D:/tmp/plugin-rules/游戏标题.plugins.json"
```

导出文件的顶层就是 RPG Maker `$plugins` 数组：

```json
[
  {
    "name": "TestPlugin",
    "status": true,
    "description": "插件说明",
    "parameters": {
      "Message": "こんにちは",
      "Nested": "{\"text\":\"選択肢\"}"
    }
  }
]
```

导入命令：

```bash
uv run python main.py import-plugin-rules --game "游戏标题" --input "D:/tmp/plugin-rules/游戏标题.json"
```

外部文件格式：

```json
{
  "schema_version": 1,
  "game_title": "游戏标题",
  "plugins": [
    {
      "plugin_index": 0,
      "plugin_name": "TestPlugin",
      "plugin_reason": "该插件参数包含玩家可见文本",
      "translate_rules": [
        {
          "path_template": "$['parameters']['Message']",
          "reason": "玩家可见消息"
        },
        {
          "path_template": "$['parameters']['Choices'][*]['text']",
          "reason": "选项文本"
        }
      ]
    }
  ]
}
```

路径展开逻辑在 `paths.py`：

- 普通对象和数组会递归遍历。
- 字符串如果本身是 JSON 容器，也会继续解析。
- 每个叶子都会得到一个受限 JSONPath，例如 `$['parameters']['Message']`。

导入时会做三层校验：

- JSON 结构校验。
- 插件名和插件索引必须匹配当前插件。
- 路径必须命中当前插件真实字符串叶子。

校验通过的规则写入数据库，后续翻译直接复用。插件结构变更后，失效规则会因插件哈希不匹配而被跳过，需要重新导入。

### 插件文本回写

`plugin_text/write_back.py` 根据 `location_path` 写回 `GameData.writable_plugins_js`。如果目标值来自 JSON 字符串，会先解析成容器，替换内部叶子，再序列化回字符串。

## 10. 翻译缓存与断点续传

目录：`app/translation/cache.py`

缓存有两层含义：

- 数据库断点续传：主翻译表中已有译文的 `location_path` 会被跳过。
- 单轮请求去重：同轮内原文、角色、类型相同的条目只送模型一次。

去重键由三部分组成：

```text
original_lines + item_type + role
```

首条进入提示词，重复项暂存在内存里。首条成功时，重复项复用同一译文一起落库；首条失败时，重复项一起写入错误表，保证进度统计一致。

## 11. 提示词组装

目录：`app/translation/context.py`

提示词组装由正文上下文和数据库术语表注入组成。

每个批次包含：

- system 消息：来自 `text_translation.system_prompt_file`。
- user 消息：地图名、可选术语表和待翻译正文块。

正文块会根据类型采用不同模板：

- `long_text`：包含 ID、类型、角色、建议换行数、台词。
- `array`：包含 ID、类型、输出行数、选项列表。
- `short_text`：包含 ID、类型、游戏文本。

批次大小由 `translation_context.token_size`、`factor` 和 `max_command_items` 控制。

用户提示词的术语表片段格式如下：

```text
[[地图名]]
始まりの町

[[术语表]]
以下为本批次必须遵守的标准译名。原文出现左侧词条时，译文必须使用右侧译名，不要自行改译。
[角色名]
- 村人 => 村民
[地图名]
- 始まりの町 => 起始之镇

[[需要翻译的正文]]
[ID]Map001.json/1/0/0
[类型]long_text
[角色]村人
[建议换行数]1

[台词]
マップこんにちは
```

如果数据库没有导入术语表，或者相关条目没有填写译名，该片段会被省略。

## 12. LLM 模块

目录：`app/llm/`

LLM 模块使用 OpenAI 官方 SDK 的异步客户端：

- `schemas.py`：`ChatMessage`。
- `handler.py`：配置 `AsyncOpenAI`，把项目消息转换成 Chat Completions 消息，发出单次请求。
- `errors.py`：判断 OpenAI SDK 错误是否可恢复。

LLM 底层只发起单次 OpenAI 兼容请求，错误分类由 `errors.py` 提供，业务重试由翻译层处理。

### 错误分类

可恢复错误：

- 连接错误。
- 超时。
- 限流。
- 409 冲突。
- 408、425、429。
- 5xx 服务端错误。

不可恢复错误：

- API Key 错误。
- 权限不足。
- 请求参数错误。
- 模型不存在。
- 4xx 非临时错误。
- 响应成功但没有文本。

## 13. 业务层重试

目录：`app/translation/retry.py`

重试策略已经上移到翻译业务层。正文翻译会调用 `request_with_recoverable_retry()`。

行为规则：

- 首次请求失败后，只要错误可恢复，就按 `retry_count` 和 `retry_delay` 重试。
- `retry_count` 表示失败后的额外重试次数，不包含首次请求。
- 每次等待时间是 `retry_delay * 当前失败次数`。
- 不可恢复错误立即抛出，`TaskGroup` 会停止整个翻译流程。
- 可恢复错误耗尽后抛出 `RuntimeError`，保留原始异常链。

这让底层 LLM 适配保持简单，也让不同业务场景未来可以拥有自己的失败策略。

## 14. 持久化层

目录：`app/persistence/`

持久化层只暴露仓储能力，不泄漏 SQL 细节给应用层。

核心表：

- `metadata`：游戏标题与游戏路径。
- `translations`：主译文表。
- `plugin_text_rules`：外部导入的插件文本路径规则。
- `name_context_entries`：外部导入的术语表条目。
- `translation_errors_时间戳`：正文翻译错误表。

`rows.py` 专门处理 `aiosqlite.Row` 的动态类型边界，把第三方无类型字段收窄到明确类型，避免 `object` 在业务层继续扩散。

外部术语表大 JSON 和插件规则 JSON 都是项目外临时文件。只有显式导入数据库后，主流程才会读取它们的内容。

## 15. 观测层

目录：`app/observability/`

日志分成两条通道：

- 终端：默认 INFO，聚焦阶段进展、关键结果、警告和错误。
- 文件：默认 DEBUG，记录配置摘要、运行首尾、细节和完整 traceback。

未知异常终端只显示中文摘要；完整异常链写入文件日志。

## 16. 回写事务

回写分两层：

- `rmmz/write_back.py` 和 `plugin_text/write_back.py`：只改内存副本。
- `application/file_writer.py`：负责把内存副本安全写回磁盘。

首次回写按文件粒度保存本轮实际受影响文件的原件：

```text
data_origin/<受影响文件名>.json
js/plugins_origin.js（仅当 plugins.js 本轮发生变化时）
```

后续写回会直接替换激活版中本轮受影响的文件，并保持 `data_origin/` 与 `js/plugins_origin.js` 中的原件留档不变。

读取规则按留档状态区分：

- 无留档：直接读取 `data/` 和 `js/plugins.js`。
- 有留档：标准 data 文件按文件级叠加读取；如果 `data_origin/<文件名>` 存在就读原件留档，否则读当前 `data/<文件名>`。插件配置同理，存在 `js/plugins_origin.js` 就读它，否则读 `js/plugins.js`。

标准名写回发生在内存副本阶段，不单独破坏原件布局。`write-name-context` 会先保留数据库中已有正文译文，再应用标准名译文，避免只写人名时把已有翻译覆盖回原文。

## 17. 后续扩展入口

常见扩展点：

- 新增事件命令提取：改 `app/rmmz/extraction.py` 与 `app/rmmz/write_back.py`。
- 新增文本过滤规则：优先加到 `TextRulesSetting` 和 `TextRules`。
- 新增插件路径策略：优先扩展外部规则格式、`app/plugin_text/paths.py` 和 `importer.py`。
- 新增上下文段落：改 `app/translation/context.py`。
- 新增外部标准名字段：改 `app/name_context/schemas.py`、`extraction.py`、`prompt.py` 和 `write_back.py`。
- 新增失败策略：改 `app/translation/retry.py`，LLM 底层维持单次请求职责。
- 新增数据库字段：改 `app/persistence/sql.py`、`repository.py` 和相关测试。
