# Agent 工作区和规则准备

## State RT3.5：Agent 工作区准备

一次性导出 Agent 需要分析的输入。

```bash
uv run python main.py --agent-mode prepare-agent-workspace \
  --game "<游戏标题>" \
  --output-dir "<外部临时目录>/agent-workspace" \
  --json
```

工作区常见文件：

- `manifest.json`：本轮临时产物清单。
- `name-context/name_registry.json`：名字框和地图显示名术语表。
- `name-context/speaker_contexts/*.json`：名字框对应对白样本。
- `plugins.json`：当前游戏 `$plugins` 数组本体。
- `event-commands.json`：配置编码对应的事件指令参数样本。
- `placeholder-candidates.json`：疑似控制符候选报告。
- `placeholder-rules.json`：项目生成的自定义占位符规则草稿。
- `WORKSPACE.md`：当前工作区内的产物格式、校验命令和导入命令速查。

读取策略：先看 `WORKSPACE.md`、`manifest.json` 和报告 `summary`；大文件用搜索、分段读取、抽样和项目校验命令分析。Agent 可以写自己的临时脚本处理导出数据，但导出和导入必须走本项目 CLI。

## State RT4：自定义控制符未确认

优先使用项目生成的草稿文件。

```bash
uv run python main.py --agent-mode build-placeholder-rules \
  --game "<游戏标题>" \
  --output "<外部临时目录>/agent-workspace/placeholder-rules.json" \
  --json
uv run python main.py --agent-mode validate-placeholder-rules \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/placeholder-rules.json" \
  --json
uv run python main.py --agent-mode scan-placeholder-candidates \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/placeholder-rules.json" \
  --json
uv run python main.py --agent-mode import-placeholder-rules \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/placeholder-rules.json"
```

占位符规则格式：JSON 对象，键是正则表达式字符串，值是占位符模板字符串。

```json
{
  "(?i)\\\\X\\d*\\[[^\\]\\r\\n]+\\]": "[CUSTOM_PLUGIN_X_MARKER_{index}]"
}
```

模板要求：

- 必须生成 `[CUSTOM_..._1]` 形态。
- 必须保留 `{index}`，让同一规则多次命中可区分。
- 名称使用大写英文和下划线。
- 名称要表达语义；不确定含义时使用中性但完整的名字，例如 `[CUSTOM_PLUGIN_<控制符名>_MARKER_{index}]`。

红旗：标准 RMMZ 控制符被报告为未覆盖时，先暂停并报告工具异常；校验失败时按错误修规则，不绕过校验。

## State RT5：术语表、插件规则、事件指令规则未准备

三类数据都是翻译前强制检查项。强制的是导出、分析、确认和验收，不是凭空产出非空规则。当前游戏数据库中的术语表、插件规则或事件指令规则为空且尚未确认游戏本身没有对应内容时，不执行 `translate`。必须由当前 Agent 自己分析导出文件；有内容就生成导入 JSON，没有内容就记录确认结论。

如果当前 Agent 平台支持子代理，可以把三类分析拆成互不写同一文件的子任务。当前 Agent 仍负责统筹：等待全部子代理结束、读取子代理结果、执行校验、导入数据库和清理临时文件。不允许让多个子代理同时修改同一个 JSON 文件。

### 术语表

输入：`name-context/name_registry.json` 和 `name-context/speaker_contexts/*.json`。

任务：填写 `speaker_names` 和 `map_display_names` 的 value。

要求：只改 value；保持 key 不变；不新增字段；不写 note、路径、来源说明；普通身份称呼正常翻译；角色名结合上下文给稳定中文名；同一角色、势力、称呼体系保持一致；控制符、变量、符号原样保留。

```bash
uv run python main.py --agent-mode import-name-context \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/name-context/name_registry.json"
```

合法空结果：`speaker_names` 和 `map_display_names` 都为空。确认游戏本身没有对应内容时，不要编造规则或术语，在交付报告记录结论，即使数据库计数仍为 0。

### 插件规则

输入：`plugins.json`，顶层是 RPG Maker `$plugins` 数组本体。

任务：生成 `plugin-rules.json`，选择玩家可见文本字符串叶子。

排除：资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标、布局参数、资源名、内部标识。

```bash
uv run python main.py --agent-mode validate-plugin-rules \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/plugin-rules.json" \
  --json
uv run python main.py --agent-mode import-plugin-rules \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/plugin-rules.json"
```

合法空结果：`plugins.json` 是空数组，或确认插件参数没有玩家可见文本。不要生成空壳规则，在交付报告记录结论，即使数据库计数仍为 0。

### 事件指令规则

输入：`event-commands.json`，按事件指令编码分组。

任务：生成 `event-command-rules.json`，选择复杂参数里的玩家可见文本字符串叶子。

规则：顶层 key 是事件指令编码字符串；`match` 用参数索引字符串匹配固定参数值；`paths` 指向可翻译字符串叶子；排除脚本、资源路径、枚举、布尔值、数字、颜色、坐标和布局参数。

```bash
uv run python main.py --agent-mode validate-event-command-rules \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/event-command-rules.json" \
  --json
uv run python main.py --agent-mode import-event-command-rules \
  --game "<游戏标题>" \
  --input "<外部临时目录>/agent-workspace/event-command-rules.json"
```

合法空结果：所有编码数组都为空，或确认参数结构没有玩家可见文本。不要生成空壳规则，在交付报告记录结论，即使数据库计数仍为 0。把没有看懂结构的情况当成“游戏没有对应内容”是阻断错误。

## State RT6：工作区验收

```bash
uv run python main.py --agent-mode validate-agent-workspace \
  --game "<游戏标题>" \
  --workspace "<外部临时目录>/agent-workspace" \
  --json
uv run python main.py --agent-mode doctor --game "<游戏标题>" --no-check-llm --json
uv run python main.py --agent-mode quality-report --game "<游戏标题>" --json
```

通过条件：占位符规则可解析、可预览、可还原；术语表没有异常空译名；插件规则命中合理；事件指令规则命中合理。允许为空的前提是已经确认游戏本身没有对应内容。
