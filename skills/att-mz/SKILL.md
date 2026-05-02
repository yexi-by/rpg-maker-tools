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
10. 稳定后继续 `translate --game <游戏标题> --json`，直到 pending 为 0，或只剩少量可人工补译项。
11. 少量 pending 用 `export-pending-translations` 导出、填写、`import-manual-translations` 导入。
12. `quality-report --json` 无阻断问题，并且用户允许写回后，执行 `write-back --game <游戏标题>`。
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

主 Agent 职责：

- 主 Agent 必须等待四类子代理全部完成。
- 主 Agent 必须读取每个子代理结果，复核文件结构和空结果理由。
- 主 Agent 必须运行对应 `validate-*` 命令。
- 主 Agent 必须在校验通过后执行对应 `import-*` 命令。
- 任一子代理未完成、失败或校验未通过，不启动翻译。
- 不允许多个子代理同时修改同一个文件。

## 9. 翻译失败处理

- `translate` 返回 0 只表示本轮命令正常结束，不代表所有条目都成功。
- pending 和少量质量错误是可续跑状态。
- 小批量后如果有模型运行故障、译文质量错误、占位符风险，先排查，不继续全量翻译。
- 连续多轮同类失败不下降时，停止盲目重跑，输出 `quality-report`，向用户说明：错误类型、数量、是否可换模型、是否需要改规则、是否适合人工补译。
- 少量 pending 可用 `export-pending-translations --limit N --output <工作区>/pending-translations.json --json` 导出，Agent 填 `translation_lines` 后用 `import-manual-translations --input <文件> --json` 导入。

## 10. 写回门禁

写回前必须满足：

- 用户明确允许写回。
- `quality-report --json` 无阻断错误。
- 占位符规则已覆盖当前游戏候选。
- 术语表、插件规则、事件指令规则已导入，或已确认游戏本身没有对应内容。
- 目标游戏目录可写。

不满足就停下报告，不要写回。

## 11. 反模式

- 在 `<项目目录>` 写临时脚本或中间文件。
- 用临时脚本直接 `import app...` 操作数据库或游戏数据。
- 把没看懂结构当成“没有内容”。
- 为了让规则非空而编造插件规则、事件指令规则或术语。
- 子代理未完成就导入半成品或启动翻译。
- 看到 `translate` 有少量失败项就当作程序崩溃。
- `quality-report` 有阻断错误仍写回。
- 把模型密钥写进命令、文档、日志摘要或临时文件。

