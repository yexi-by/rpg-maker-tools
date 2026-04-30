# 模块解释图

这份文件同时保存图片版解释图和 Mermaid 可维护图。图片版适合快速理解模块边界，Mermaid 版适合后续随着代码演进直接修改。

## 0. 图片版模块图

这些图片已经复制到 `docs/images/modules/`，文件名按模块固定，避免引用随机生成文件名。

![总流程图](images/modules/00-overview.png)

![CLI 命令入口](images/modules/01-cli.png)

![Application 应用编排](images/modules/02-application.png)

![Config 配置中心](images/modules/03-config.png)

![Observability 可观测性](images/modules/04-observability.png)

![RMMZ 标准数据处理](images/modules/05-rmmz.png)

![Plugin Text 插件文本处理](images/modules/06-plugin-text.png)

![Translation 翻译流水线](images/modules/07-translation.png)

![LLM OpenAI 兼容适配层](images/modules/08-llm.png)

![Persistence SQLite 持久化](images/modules/09-persistence.png)

## 1. 总流程图

```mermaid
flowchart TD
    CLI[CLI 子命令] --> Handler[application.handler]
    Handler --> Config[config + setting.toml]
    Handler --> GameLoad[rmmz.loader 加载标准文件]
    Handler --> PluginExport[export-plugins-json 导出 $plugins JSON]
    Handler --> PluginImport[import-plugin-rules 导入外部插件规则]
    Handler --> NameImport[import-name-context 导入术语表]
    Handler --> DataExtract[rmmz.extraction 提取 data 文本]
    Handler --> PluginExtract[plugin_text.extraction 提取插件文本]
    DataExtract --> Cache[translation.cache 去重与断点续传]
    PluginExtract --> Cache
    NameImport --> Prompt
    Cache --> Prompt[translation.context 组装提示词]
    Prompt --> Retry[translation.retry 业务层可恢复重试]
    Retry --> LLM[llm.handler OpenAI 兼容请求]
    LLM --> Verify[translation.verify 校验译文]
    Verify --> DB[persistence.repository 写入 SQLite]
    DB --> WriteBack[application.file_writer 回写事务]
    WriteBack --> DataFiles[data/*.json]
    WriteBack --> Plugins[js/plugins.js]
    NameImport --> WriteBack
```

## 2. LLM 与重试边界

```mermaid
flowchart LR
    TextWorker[正文翻译 worker] --> Retry[translation.retry]
    Retry -->|单次请求| LLMHandler[llm.handler]
    LLMHandler --> OpenAI[AsyncOpenAI Chat Completions]
    OpenAI --> LLMHandler
    LLMHandler --> Retry
    Retry -->|可恢复错误: 超时/限流/5xx| Retry
    Retry -->|不可恢复错误: 鉴权/参数/模型不存在/空响应| Stop[立即中断流程]
    Retry -->|成功文本| Business[继续校验和落库]
```

## 3. 标准 data 文本提取

```mermaid
flowchart TD
    Loader[rmmz.loader] --> Standard{是否标准 RMMZ 文件?}
    Standard -->|是| Parse[解析为 GameData]
    Standard -->|否| Skip[DEBUG 日志跳过]
    Parse --> Commands[commands 遍历地图/公共事件/敌群]
    Commands --> Dialogue[101+401 对白]
    Commands --> Choices[102 选项]
    Commands --> Scroll[405 滚动文本]
    Commands --> PluginCommand[357 插件命令参数]
    Parse --> System[System.json 系统词汇]
    Parse --> Base[Actors/Items/Skills 等基础数据库]
    Dialogue --> Items[TranslationItem]
    Choices --> Items
    Scroll --> Items
    PluginCommand --> Items
    System --> Items
    Base --> Items
```

## 4. 占位符保护流程

```mermaid
flowchart LR
    Original[原文: こんにちは\\V[1]%12\\G] --> Build[build_placeholders]
    Build --> Masked[送模文本: こんにちは[V_1][P_12][G_0]]
    Masked --> Model[模型翻译]
    Model --> Returned[译文: 你好[V_1][P_12][G_0]]
    Returned --> Verify[verify_placeholders 数量校验]
    Verify --> Restore[restore_placeholders]
    Restore --> Final[最终译文: 你好\\V[1]%12\\G]
```

## 5. 插件文本外部规则导入

```mermaid
flowchart TD
    Plugins[plugins.js] --> Export[export-plugins-json]
    Export --> SourceJSON[$plugins 数组 JSON]
    SourceJSON --> Agent[外部 Agent 判断可翻译字段]
    Agent --> External[外部插件规则 JSON]
    External --> Import[import-plugin-rules]
    Plugins --> Leaves[paths.resolve_plugin_leaves]
    Leaves --> JSONString{字符串是否 JSON 容器?}
    JSONString -->|是| Nested[继续展开嵌套叶子]
    JSONString -->|否| Leaf[记录叶子 JSONPath]
    Nested --> Leaf
    External --> Validate[结构/索引/路径校验]
    Leaf --> Validate
    Validate --> Rules[PluginTextRuleRecord]
    Rules --> DB[SQLite plugin_text_rules]
    DB --> Extract[plugin_text.extraction 按规则提取]
```

## 6. 数据库与回写

```mermaid
flowchart TD
    Verified[校验通过译文] --> TranslationTable[translations 主表]
    Failed[校验失败译文] --> ErrorTable[translation_errors_时间戳]
    PluginRules[外部插件规则 JSON] --> RuleImport[import-plugin-rules]
    RuleImport --> RuleTable[plugin_text_rules]
    NameRegistry[name_registry.json 术语表临时文件] --> NameImport[import-name-context]
    NameImport --> NameTable[name_context_entries]
    TranslationTable --> ReadBack[write-back 读取已完成译文]
    RuleTable --> ReadBack
    NameTable --> ReadBack
    ReadBack --> MemoryCopy[重置 writable_data / writable_plugins_js]
    MemoryCopy --> ApplyData[rmmz.write_back 写 data 内存副本]
    MemoryCopy --> ApplyPlugin[plugin_text.write_back 写插件内存副本]
    MemoryCopy --> ApplyName[name_context.write_back 写 101/displayName]
    ApplyData --> Transaction[file_writer 受影响文件备份与替换]
    ApplyPlugin --> Transaction
    ApplyName --> Transaction
    Transaction --> Active[替换激活版 data 与 plugins.js]
```
