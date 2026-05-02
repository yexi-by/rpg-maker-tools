---
name: att-mz
description: Use this skill when operating A.T.T MZ, the RPG Maker MZ autonomous translation toolkit, for project discovery, game registration, Agent workspace analysis, placeholder rules, terminology, plugin rules, event command rules, translation loops, quality checks, manual completion, and write-back gating.
---

# A.T.T MZ Skill

本 Skill 是翻译任务执行协议，不是项目说明书。按阶段执行，遇到阻断就停下报告，不要靠猜。

## 0. 目录边界

- `<项目目录>`：A.T.T MZ 仓库。只允许运行 CLI、读说明、读源码排障。翻译任务中禁止在这里写临时脚本、中间 JSON、抽样报告和补译文件。
- `<游戏目录>`：目标游戏。CLI 可以注册、读取、写回、生成 `data_origin`、复制字体。临时工作区也可以放在这里，但必须集中在一个明确目录里，不能散落到游戏根目录各处。
- `<工作区>`：Agent 临时目录。所有导出文件、规则草稿、临时脚本、中间结果、人工补译 JSON 都放这里。推荐使用 `<外部临时目录>/agent-workspace`；用户允许时也可以使用 `<游戏目录>/<临时工作区名>`。
- 翻译任务中，临时脚本不得直接 `import app...` 操作数据库或游戏数据。业务数据进出必须走本项目 CLI。
- 如果用户要求开发或修改 A.T.T MZ 项目本身，上面“不得 import app...”不适用，但必须明确这是开发任务，不是翻译任务。

## 1. 固定命令习惯

- 进入 `<项目目录>` 后运行命令。
- 默认使用：`uv run python main.py --agent-mode <命令> ...`。
- 需要机器读取结果时加 `--json` 或 `--output <文件>`。
- 全局参数放在子命令前，例如 `uv run python main.py --agent-mode doctor ...`。
- 模型地址和 API Key 只从环境变量或本地配置读取，不写进命令行参数、临时文件、报告和提交。
- 文件型规则一律用 `--input <文件>`，不要用 `--rules "$(cat ...)"`，不要把大 JSON 塞进命令行。

### 编码与 Windows 终端

- 所有工作区 JSON、临时脚本、人工补译文件、规则文件和交付报告都必须按 UTF-8 读写；禁止依赖 Windows 默认编码、ANSI、GBK 或 Shift-JIS。
- 写 JSON 时保持 UTF-8 文本，推荐保留中日文原文可读性，例如 Python 使用 `json.dumps(..., ensure_ascii=False)` 并显式 `encoding="utf-8"`。
- Agent 自写临时脚本时必须显式声明编码：Python 使用 `Path.read_text/write_text(..., encoding="utf-8")` 或 `open(..., encoding="utf-8")`；Node.js 使用 `fs.readFile/writeFile(..., "utf8")`；PowerShell 写文件必须显式 `-Encoding utf8`。
- 在 Windows 终端运行 CLI 时优先使用 `--agent-mode --json` 降低控制台渲染影响；如果 stdout 出现乱码，先在同一 shell 设置 UTF-8 后重跑命令，不要基于乱码内容修改文件。
- PowerShell 推荐先执行：`$OutputEncoding = [System.Text.UTF8Encoding]::new(); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new(); [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()`。
- 如果发现工作区文件、CLI 输出或子代理结果出现乱码，必须先停止当前阶段并修复编码来源；禁止继续导入、翻译或写回乱码数据。

## 2. 启动前必须确认

缺任意一项就先问用户，不启动翻译：

- `<项目目录>` 可进入，并能执行 `uv run python main.py --help`。
- `<游戏目录>` 存在，且是 RPG Maker MZ 标准结构。
- `<工作区>` 已确定，可写，可清理。
- 模型环境变量或本地配置已准备；用户允许时才做模型连通性检查。
- 用户是否允许最终 `write-back` 已明确。

## 3. 新游戏主流程

1. 项目检查：`doctor --no-check-llm --json`。
2. 注册游戏：`add-game --path <游戏目录> --json`，后续使用返回的 `<游戏标题>`。
3. 游戏检查：`doctor --game <游戏标题> --json`。
4. 准备工作区：`prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json`。
5. 分析并导入占位符规则。
6. 分析并导入术语表、插件规则、事件指令规则。
7. `validate-agent-workspace --game <游戏标题> --workspace <工作区> --json`。
8. 小批量翻译：`translate --game <游戏标题> --max-batches 1 --json`。
9. 查看 `translation-status --json` 和 `quality-report --json`。
10. 稳定后继续 `translate --game <游戏标题> --json`，直到 pending 为 0，或只剩适合人工补译的项。
11. 全量 pending 用 `export-untranslated-translations` 导出、填写、`import-manual-translations` 导入；分批补译才使用 `export-pending-translations --limit N`。
12. `quality-report --json` 无阻断问题，并且用户允许写回后，执行 `write-back --game <游戏标题> --json`。
13. 清理 `<工作区>`；如果工作区由 `prepare-agent-workspace` 生成，优先用 `cleanup-agent-workspace --workspace <工作区> --json`。

## 4. 二次翻译主流程

- 不把二次翻译当新游戏重做。
- 先执行 `doctor --game <游戏标题> --json`、`translation-status --game <游戏标题> --json`、`quality-report --game <游戏标题> --json`。
- 已有译文缓存会复用；CLI 只处理当前提取范围内尚未成功入库的条目。
- 游戏文件、插件配置、事件指令结构或自定义控制符发生变化时，重新导出工作区并重新分析对应规则。
- 二次写回由 CLI 直接替换当前激活文件；不要手工移动 `data/` 或 `data_origin/`。

## 5. 工作区产物规则

`prepare-agent-workspace` 常见产物：

- `manifest.json`：清理清单。
- `placeholder-candidates.json`：候选控制符报告，大文件不要整读。
- `placeholder-rules.json`：占位符规则草稿。
- `name-context/name_registry.json`：术语表，只填写 value。
- `name-context/speaker_contexts/*.json`：名字上下文样本。
- `plugins.json`：插件原始 JSON。
- `event-commands.json`：事件指令参数导出。

Agent 可以在 `<工作区>` 内写临时脚本分析这些文件。项目只关心最终是否通过 CLI 校验并导入数据库。

### 黑盒执行原则

- 翻译任务中，把本项目当成闭源黑盒工具使用：禁止依赖源码、数据库表结构或内部 Python 对象来推断规则格式。
- 所有业务数据进出只走 CLI、`<工作区>` JSON、当前游戏数据库中已导入的规则和游戏目录文件。
- `--json` 输出里的 `status` 是机器判断入口：`error` 必须阻断；`warning` 必须阅读并判断是否属于允许的空结果或可续跑状态；`ok` 才能进入下一阶段。
- 每个阶段都必须明确输入、处理逻辑、输出、校验命令和失败恢复动作；缺一项就先补上下文，不把模糊任务交给子代理。
- 外部 Agent 只负责分析和填写工作区文件，最终是否可用由本项目 CLI 校验决定。

### 输入-逻辑-输出总则

主 Agent 执行每个阶段前，必须先明确“输入是什么、处理逻辑是什么、输出什么”。缺任意一项就先补上下文或停下询问，不把模糊任务交给子代理。

| 阶段 | 输入 | 逻辑 | 输出 |
| --- | --- | --- | --- |
| 环境与注册 | `<项目目录>`、`<游戏目录>`、模型配置 | 用 CLI 检查环境、注册游戏、确认 `<游戏标题>` | 已注册游戏标题，或可理解的阻断原因 |
| 工作区准备 | `<游戏标题>`、`<工作区>` | 用 CLI 导出 Agent 工作区产物，不手工拼数据库数据 | `<工作区>` 内的候选文件和规则草稿 |
| 外部分析 | 工作区候选文件、规则草稿 | 由主 Agent 或子代理按本 Skill 的规则筛选可翻译内容 | 术语表、占位符规则、插件规则、事件指令规则 |
| 验收导入 | 四类外部分析产物 | 逐个运行 `validate-* --json`，通过后再 `import-*` | 当前游戏数据库内的有效规则 |
| 翻译与补译 | 当前游戏数据库、模型配置、质量报告 | 小批量试跑、查看状态、修复 pending 和质量错误 | pending 为 0 或只剩已说明的人工处理项 |
| 写回 | 已入库译文、无阻断质量报告、用户许可 | 执行 `write-back --json`，不直接移动 data 目录 | 已写回游戏目录和机器可读摘要 |

### 命令 I/O 合约

| 命令 | 输入 | 前置条件 | 输出用途 | 成功判断 | 失败后处理 |
| --- | --- | --- | --- | --- | --- |
| `doctor --no-check-llm --json` | `<项目目录>`、本地配置 | 可进入项目目录 | 检查项目静态环境；缺失 `data/db` 时应自愈创建 | `status` 不是 `error` | 按 `errors` 修环境；不启动翻译 |
| `add-game --path <游戏目录> --json` | RPG Maker MZ 游戏目录 | 游戏目录存在且结构有效 | 创建或更新当前游戏数据库，返回 `<游戏标题>` | `summary.game_title` 可作为后续 `--game` | 修正游戏目录或文件结构后重跑 |
| `doctor --game <游戏标题> --json` | 已注册游戏标题 | `add-game` 已成功 | 检查游戏绑定、规则导入状态和占位符风险 | `status` 不是 `error` | 缺规则是 warning 时继续准备工作区；error 先修注册或游戏文件 |
| `prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json` | 游戏标题、工作区目录 | 游戏已注册 | 导出四类外部分析输入和 `manifest.json` | 工作区文件存在，`summary.workspace` 指向目标目录 | 删除不完整工作区后重跑 |
| `build-placeholder-rules --game <游戏标题> --output <规则文件> --json` | 游戏标题、规则输出文件 | 游戏已注册 | 单独生成占位符规则草稿 | 输出文件存在 | 先看 `errors`，不要手写替代 CLI 导出 |
| `validate-placeholder-rules --game <游戏标题> --input <规则文件> --json` | 占位符规则 JSON | 规则文件存在 | 校验正则、模板和样本文本往返 | `status` 为 `ok` 或只有可接受空结果 warning | 修 `<规则文件>` 后重跑校验 |
| `import-placeholder-rules --game <游戏标题> --input <规则文件>` | 已校验规则文件 | validate 已通过 | 导入当前游戏数据库 | 命令返回 0 | 回到 validate 修规则，不直接改库 |
| `import-name-context --game <游戏标题> --input <术语表>` | 填好的 `name_registry.json` | key 未改、只填 value | 导入名字框和地图名术语 | 命令返回 0 | 修术语表结构或空值策略后重跑 |
| `validate-plugin-rules --game <游戏标题> --input <规则文件> --json` | 插件规则 JSON | `plugins.json` 已分析 | 校验插件名、插件哈希和 JSONPath 命中字符串叶子 | `status` 为 `ok`，或空规则 warning 已确认 | 修 `plugin-rules.json`，不读源码猜路径 |
| `import-plugin-rules --game <游戏标题> --input <规则文件>` | 已校验插件规则 | validate 已通过 | 导入插件可翻译字段规则 | 命令返回 0 | 回到 validate 修规则 |
| `validate-event-command-rules --game <游戏标题> --input <规则文件> --json` | 事件指令规则 JSON | `event-commands.json` 已分析 | 校验指令编码、参数过滤、路径命中和回写预演 | 无 `errors`；warning 需说明原因 | 修 `event-command-rules.json` 后重跑 |
| `import-event-command-rules --game <游戏标题> --input <规则文件>` | 已校验事件指令规则 | validate 无 error | 导入事件指令文本规则 | 命令返回 0 | 回到 validate 修规则 |
| `validate-agent-workspace --game <游戏标题> --workspace <工作区> --json` | 完整工作区 | 四类产物已由主 Agent 复核 | 总体验收工作区可导入性 | 无 `errors` | 逐项修工作区 JSON 后重跑 |
| `translate --game <游戏标题> --max-batches 1 --json` | 游戏标题、模型配置 | 工作区已校验并导入 | 小批量试跑正文翻译 | 命令返回 0 且质量报告无新增阻断 | 看 status 和 quality-report，不盲目全量 |
| `translate --game <游戏标题> --json` | 游戏标题、模型配置 | 小批量稳定 | 继续翻译 pending 项 | 命令返回 0 | 失败项走 status、quality-report 或人工补译 |
| `translation-status --game <游戏标题> --json` | 游戏标题 | 至少跑过翻译或导入 | 判断实时 pending、成功数和故障数量；`pending_count` 是当前数据库未翻译数，`run_pending_count` 是最近运行起始待处理数 | pending 可解释 | pending 适合人工处理时导出补译，大量规则性失败时续跑或修故障 |
| `quality-report --game <游戏标题> --json` | 游戏标题 | 已有译文或翻译运行记录 | 判断写回门禁和修复清单 | `status` 为 `ok` | 按 details 修译文或规则，禁止继续写回 |
| `export-untranslated-translations --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 存在 pending | 一键导出全部尚未成功入库的正文原文结构 | 输出文件存在 | 若 warning 为空结果，说明无需人工补译 |
| `export-pending-translations --game <游戏标题> --limit N --output <文件> --json` | 游戏标题、数量、输出文件 | 存在 pending | 分批或抽样导出人工补译 JSON；不传 `--limit` 时导出全部 | 输出文件存在 | 若 warning 为空结果，说明无需人工补译 |
| `import-manual-translations --game <游戏标题> --input <文件> --json` | 已填写人工补译 JSON | 只填 `translation_lines` | 校验并入库人工译文；`long_text` 会按当前 `[text_rules]` 行宽自动拆短 | `status` 为 `ok` | 修对应条目的 `translation_lines` 后重跑 |
| `write-back --game <游戏标题> --json` | 游戏标题、已入库译文、用户许可 | `quality-report --json` 无 error | 写回游戏目录并输出摘要 | 命令返回 0 且 JSON 摘要可读 | 停止交付，按错误修质量或规则 |
| `cleanup-agent-workspace --workspace <工作区> --json` | 工作区目录 | `manifest.json` 存在 | 清理 CLI 生成的工作区产物 | 命令返回 0 | 缺 manifest 时手工确认后再清理 |

### 工作区 JSON 格式契约

- `placeholder-rules.json`：顶层必须是对象，格式为 `{正则表达式: 占位符模板}`。占位符模板必须生成形如 `[CUSTOM_NAME_1]` 的方括号占位符；推荐使用 `{index}`，例如 `[CUSTOM_PLUGIN_MARK_{index}]`。禁止写成 `{占位符名: 正则表达式}`，禁止把 RMMZ 标准控制符当自定义规则硬写。
- `name-context/name_registry.json`：顶层只使用 `speaker_names` 和 `map_display_names` 两个对象。Agent 只填写已有 key 对应的 value；不改 key，不新增字段，不写 note，不把样本文件路径写入 value。不能确定译名时保留空字符串并在最终报告说明。
- `plugin-rules.json`：顶层必须是对象，格式为 `{插件名: [JSONPath, ...]}`。插件名必须来自 `plugins.json`；JSONPath 必须使用括号路径语法并从 `$['parameters']` 开始，例如 `$['parameters']['message']` 或 `$['parameters']['items'][*]['name']`；禁止使用 `$.xxx` 点号路径。
- `event-command-rules.json`：顶层必须是对象，格式为 `{指令编码字符串: [{match, paths}]}`。`match` 是参数索引字符串到期望字符串值的对象；`paths` 是 `$['parameters']...` 路径数组；路径必须命中字符串叶子。没有过滤条件时 `match` 使用 `{}`。
- `pending-translations.json`：顶层是 `{location_path: 条目对象}`。导入前只填写 `translation_lines` 字符串数组，保留导出的 `item_type`、`role`、`original_lines`、`text_for_model_lines`，禁止改 `location_path`，禁止保留程序占位符。`long_text` 可以按自然语义填写，导入命令会按当前 `[text_rules]` 行宽配置自动拆短；若无法安全拆分，后续 `quality-report` 会以 `overwide_line` 阻断写回。
- 所有规则 JSON 允许空对象 `{}` 表示确认无可导入内容，但必须由负责 Agent 在最终回复说明空结果理由。

## 6. 占位符规则

- 先用 `build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json` 生成草稿。
- 再用 `validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json` 校验。
- 校验通过后：`import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json`。
- 占位符名称必须语义化，例如 `[CUSTOM_PLUGIN_FACE_PORTRAIT_1]`，不要用 `[X_1]` 这种模型看不懂的名字。
- 标准 RMMZ 控制符如果被报告为未覆盖，先停下报告工具异常，不要硬凑规则。

## 7. 三类外部分析

这三类在翻译前都必须导出、分析、确认、验收。强制的是“确认”，不是强制产出非空规则。

### 术语表

- 输入：`name-context/name_registry.json` 和 `speaker_contexts/*.json`。
- 只填写 value，保持 key 不变，不新增字段，不写 note。
- 角色名、势力、称呼、声音变体要统一。
- 原文确实没有名字框和地图名时，允许为空，并在交付报告说明。
- 导入：`import-name-context --game <游戏标题> --input <工作区>/name-context/name_registry.json`。

### 插件规则

- 输入：`plugins.json`。
- 输出：`plugin-rules.json`，对象格式，key 是插件名，value 是 JSONPath 字符串数组。
- 只选玩家可见文本，排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识。
- 插件为空或插件没有玩家可见文本时，允许 `{}`，但必须先确认。
- 校验：`validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json`。
- 导入：`import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json`。

### 事件指令规则

- 输入：`event-commands.json`。
- 输出：`event-command-rules.json`，对象格式，key 是事件指令编码字符串，value 是规则数组。
- 可见文本存在才写规则；所有编码数组为空或参数没有可见文本时，允许 `{}`，但必须先确认。
- 校验：`validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json`。
- 导入：`import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json`。

## 8. 子代理规则

如果平台支持子代理，必须启用子代理并行处理外部分析任务；不支持子代理时，才允许串行处理。

默认拆分四个互不写同一文件的子代理：

- `placeholder-rules` 子代理：读取 `placeholder-candidates.json` 和 `placeholder-rules.json`，只写 `placeholder-rules.json`。
- `name-context` 子代理：读取 `name-context/name_registry.json` 和 `speaker_contexts/*.json`，只写 `name-context/name_registry.json`。
- `plugin-rules` 子代理：读取 `plugins.json`，只写 `plugin-rules.json`。
- `event-command-rules` 子代理：读取 `event-commands.json`，只写 `event-command-rules.json`。

### 四类子代理任务契约

主 Agent 派发子代理时，必须把对应行的输入、逻辑和输出写进子代理 prompt。子代理只完成自己的契约，不导入数据库，不启动翻译，不修改其他文件。

| 子代理 | 输入 | 逻辑 | 输出 |
| --- | --- | --- | --- |
| `placeholder-rules` | `placeholder-candidates.json`、`placeholder-rules.json` 草稿 | 判断哪些候选是游戏自定义控制符或脚本标记；保留对象格式；不把 RMMZ 标准控制符硬写成自定义规则 | 只写 `placeholder-rules.json`，格式为 `{正则表达式: 占位符模板}`；无自定义规则时输出 `{}` |
| `name-context` | `name-context/name_registry.json`、`speaker_contexts/*.json` | 统一角色名、地图名、称呼和声音变体；只填写 value；不新增字段和说明 | 只写 `name-context/name_registry.json`，key 保持不变 |
| `plugin-rules` | `plugins.json` | 只选择插件参数里的玩家可见文本；排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识 | 只写 `plugin-rules.json`，格式为 `{插件名: [JSONPath, ...]}`；无可见文本时输出 `{}` |
| `event-command-rules` | `event-commands.json` | 按事件指令编码判断参数中的玩家可见文本；不为资源、脚本、数字、布尔值和内部标识写规则 | 只写 `event-command-rules.json`，格式按事件指令编码分组；无可见文本时输出 `{}` |

主 Agent 职责：

- 主 Agent 必须等待四类子代理全部完成。
- 主 Agent 必须读取每个子代理结果，复核文件结构和空结果理由。
- 主 Agent 必须运行对应 `validate-*` 命令。
- 主 Agent 必须在校验通过后执行对应 `import-*` 命令。
- 任一子代理未完成、失败或校验未通过，不启动翻译。
- 不允许多个子代理同时修改同一个文件。

### 子代理上下文包

主 Agent 派发每个子代理时，必须提供最小但完整的上下文包，不要让子代理靠猜，也不要把大 JSON 正文塞进子代理 prompt。

每个子代理 prompt 必须包含：

- `<项目目录>`、`<工作区>`、`<游戏标题>`。
- 子代理角色名，例如 `placeholder-rules`、`name-context`、`plugin-rules`、`event-command-rules`。
- 输入文件清单和输出文件路径。
- 只读范围和唯一可写文件；只允许写自己负责的输出文件。
- 当前任务的输出 JSON 格式、禁止新增字段规则、空结果允许条件。
- 需要重点排除的内容，例如资源路径、脚本、数字、布尔值、内部标识、数据库字段和调试路径。
- 完成后必须报告：改动文件、是否为空结果、空结果理由、未解决风险、建议主 Agent 运行的校验命令。

推荐子代理 prompt 骨架：

```text
你是 <角色名> 子代理，工作目录是 <项目目录>。
本次只处理 <工作区> 中的指定文件。
输入：<输入文件列表>。
逻辑：<当前任务的筛选、统一、排除和空结果判断规则>。
输出：<唯一可写文件>，格式为 <目标 JSON 格式>。
排除：不要选择资源路径、脚本、数字、布尔值、内部标识、数据库字段和调试路径。
如果确认没有可写内容，输出允许的空结构，并在最终回复说明空结果理由。
完成后只汇报改动文件、空结果理由、未解决风险和建议校验命令。
```

四类子代理的额外上下文要求：

- `placeholder-rules`：说明 `placeholder-candidates.json` 是候选报告、`placeholder-rules.json` 是规则草稿；必须保留对象格式，键是正则表达式，值是 `[CUSTOM_NAME_{index}]` 形式占位符模板。
- `name-context`：说明只能填写 `name_registry.json` 的 value，不能改 key、不能新增字段、不能写 note；必要时读取 `speaker_contexts/*.json` 判断称呼和角色一致性。
- `plugin-rules`：说明 `plugin-rules.json` 必须是 `{插件名: [JSONPath, ...]}`；只选玩家可见文本，排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识。
- `event-command-rules`：说明 `event-command-rules.json` 必须按事件指令编码分组；只为参数中的玩家可见文本写规则，参数无可见文本时允许空结构。

### 子代理任务单模板

`placeholder-rules` 子代理任务单：

```text
输入：读取 <工作区>/placeholder-candidates.json 和 <工作区>/placeholder-rules.json。
逻辑：识别游戏自定义控制符、插件内联标记和脚本标记；已有草稿可保留或修正；RMMZ 标准控制符不是自定义规则。
输出：只写 <工作区>/placeholder-rules.json，格式为 {正则表达式: 占位符模板}。
空结果：确认没有自定义控制符时输出 {}。
完成报告：说明规则数量、空结果理由、仍不确定的候选、建议运行 validate-placeholder-rules --json。
```

`name-context` 子代理任务单：

```text
输入：读取 <工作区>/name-context/name_registry.json 和 <工作区>/name-context/speaker_contexts/*.json。
逻辑：根据对白样本统一角色名、地图名、称呼和声音变体；只填写已有 key 的 value。
输出：只写 <工作区>/name-context/name_registry.json，不改 key，不新增字段。
空结果：没有名字框和地图名，或无法确定译名时保留空 value 并说明。
完成报告：说明填写数量、保留空值原因、命名一致性风险、建议运行 validate-agent-workspace --json。
```

`plugin-rules` 子代理任务单：

```text
输入：读取 <工作区>/plugins.json。
逻辑：按插件逐项判断 parameters 内玩家可见文本；排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识。
输出：只写 <工作区>/plugin-rules.json，格式为 {插件名: [JSONPath, ...]}；JSONPath 从 $['parameters'] 开始并使用括号路径语法。
空结果：确认插件为空或没有玩家可见文本时输出 {}。
完成报告：说明命中的插件、排除理由、空结果理由、建议运行 validate-plugin-rules --json。
```

`event-command-rules` 子代理任务单：

```text
输入：读取 <工作区>/event-commands.json。
逻辑：按事件指令编码判断参数里的玩家可见文本；必要时用 match 限定参数值；不为资源、脚本、数字、布尔值和内部标识写规则。
输出：只写 <工作区>/event-command-rules.json，格式为 {指令编码字符串: [{match, paths}]}。
空结果：确认导出的事件指令参数没有玩家可见文本时输出 {}。
完成报告：说明编码分组、规则数量、空结果理由、建议运行 validate-event-command-rules --json。
```

## 9. 翻译失败处理

- `translate` 返回 0 只表示本轮命令正常结束，不代表所有条目都成功。
- pending 和少量质量错误是可续跑状态。
- 小批量后如果有模型运行故障、译文质量错误、占位符风险，先排查，不继续全量翻译。
- `quality-report --json` 返回的 `placeholder_risk_items` 和 `overwide_line_items` 是修复定位清单。Agent 必须按 `location_path` 整理成人工补译 JSON，用 `import-manual-translations --input <文件> --json` 导入；禁止直接修改数据库。
- `overwide_line` 是写回阻断问题，必须修复到 `quality-report --json` 无错误后才能写回。
- 连续多轮同类失败不下降时，停止盲目重跑，输出 `quality-report`，向用户说明：错误类型、数量、是否可换模型、是否需要改规则、是否适合人工补译。
- 需要一键交给 Agent 补齐全部未翻译正文时，使用 `export-untranslated-translations --game <游戏标题> --output <工作区>/pending-translations.json --json` 导出完整 pending 结构；Agent 只填 `translation_lines`，再用 `import-manual-translations --game <游戏标题> --input <文件> --json` 导入。
- 只想分批或抽样处理时，使用 `export-pending-translations --game <游戏标题> --limit N --output <工作区>/pending-translations.json --json`；不传 `--limit` 时导出全部 pending 条目。
- 人工补译的 `long_text` 不要求 Agent 手工按固定宽度切行；导入命令会先按 `[text_rules].long_text_line_width_limit` 和 `line_split_punctuations` 自动拆短，再写入数据库。

## 10. 写回门禁

写回前必须满足：

- 用户明确允许写回。
- `quality-report --json` 无阻断错误。
- 占位符规则已覆盖当前游戏候选。
- 术语表、插件规则、事件指令规则已导入，或已确认游戏本身没有对应内容。
- 目标游戏目录可写。

不满足就停下报告，不要写回。

## 11. 校验失败恢复

- `validate-* --json` 返回 `error` 时，先把错误映射回对应工作区 JSON，修文件后重跑同一个 validate 命令。
- `placeholder_rules_invalid`：优先检查是否把 `{正则表达式: 占位符模板}` 写反、模板是否能生成 `[CUSTOM_NAME_1]`、正则是否能编译。
- `plugin_rules_invalid`：优先检查插件名是否来自 `plugins.json`、JSONPath 是否从 `$['parameters']` 开始、是否误用了 `$.xxx` 点号路径、路径是否命中字符串叶子。
- `event_command_rules_invalid`：优先检查指令编码是否是字符串数字、`match` 键是否是参数索引、`paths` 是否从 `$['parameters']` 开始并命中字符串叶子。
- `manual_translation_invalid`：优先检查 `translation_lines` 是否为字符串数组、行数是否匹配条目类型、是否残留程序占位符或日文残留。
- `quality-report --json` 返回 `placeholder_risk_items` 或 `overwide_line_items` 时，按 `location_path` 整理人工补译 JSON，用 `import-manual-translations --json` 导入后重跑质量报告。
- 禁止因为校验失败而直接 UPDATE 数据库、跳过 validate、跳过 `validate-agent-workspace` 或继续写回。
- 只有错误信息无法对应到工作区 JSON，或同一合法文件反复触发无法解释的 CLI 错误时，才停止并报告工具问题。

## 12. 反模式

- 在 `<项目目录>` 写临时脚本或中间文件。
- 用临时脚本直接 `import app...` 操作数据库或游戏数据。
- 把没看懂结构当成“没有内容”。
- 为了让规则非空而编造插件规则、事件指令规则或术语。
- 子代理未完成就导入半成品或启动翻译。
- 看到 `translate` 有少量失败项就当作程序崩溃。
- `quality-report` 有阻断错误仍写回。
- 直接 UPDATE 数据库译文表绕过 `import-manual-translations`。
- 把模型密钥写进命令、文档、日志摘要或临时文件。

