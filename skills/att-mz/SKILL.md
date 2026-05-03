---
name: att-mz
description: 执行 A.T.T MZ 的 RPG Maker MZ 游戏翻译流程：注册游戏、准备工作区、分析游戏控制符和术语、导入规则、调用模型翻译、检查译文、手动填写失败译文、把译文写进游戏文件。
---

# A.T.T MZ Skill

本 Skill 是翻译任务执行协议，不是项目说明书。按阶段执行，遇到不能继续的错误就停下报告，不要靠猜。

## 0. 对用户说人话

- 对用户报告时，先说人话结论：哪些文本没成功保存译文，为什么不能写进游戏文件，下一步准备怎么处理。
- 命令名和 JSON 字段名可以保留，但不能代替解释。第一次出现字段名时，必须紧跟中文解释。
- 不要直接对用户说 `pending`；说“还没成功保存译文的文本”。
- 不要直接对用户说 `quality_error`；说“模型翻了，但项目检查没通过的译文”。
- 不要直接对用户说 `overwide_line`；说“某一行太长，游戏窗口放不下”。
- 不要直接对用户说 `placeholder`；说“必须原样保留的游戏控制符”。
- 不要直接对用户说 `write-back`；说“把译文写进游戏文件”。
- 不要直接对用户说 `location_path`；说“文本在游戏里的内部位置”。只有解释 JSON 表格格式时才写字段名。
- 不要直接对用户说 `translation_lines`；说“中文译文行”。只有解释 JSON 表格格式时才写字段名。
- 不要把“入库、缓存、门禁、阻断、产物、收尾、跑批、去重后、导出骨架”当默认文案；分别说“保存到项目数据库、已保存的译文记录、检查没通过所以不能继续、生成的文件、处理剩下的文本、分组发送给模型、相同原文只翻一次、生成可填写的修复表”。

## 1. 目录边界

- `<项目目录>`：A.T.T MZ 仓库。只允许运行 CLI、读说明、读源码排障。翻译任务中禁止在这里写临时脚本、中间 JSON、抽样报告和手动填写译文表。
- `<游戏目录>`：目标游戏。CLI 可以注册、读取、写回、生成 `data_origin`、复制字体。临时工作区也可以放在这里，但必须集中在一个明确目录里，不能散落到游戏根目录各处。
- `<工作区>`：Agent 临时目录。所有导出文件、规则草稿、临时脚本、中间结果、手动填写译文表都放这里。推荐使用 `<外部临时目录>/agent-workspace`；用户允许时也可以使用 `<游戏目录>/<临时工作区名>`。
- 翻译任务中，临时脚本不得直接 `import app...` 操作数据库或游戏数据。业务数据进出必须走本项目 CLI。
- 如果用户要求开发或修改 A.T.T MZ 项目本身，上面“不得 import app...”不适用，但必须明确这是开发任务，不是翻译任务。

## 2. 固定命令习惯

- 进入 `<项目目录>` 后运行命令。
- 默认使用：`uv run python main.py --agent-mode <命令> ...`。
- 需要机器读取结果时加 `--json` 或 `--output <文件>`。
- 全局参数放在子命令前，例如 `uv run python main.py --agent-mode doctor ...`。
- 模型地址和 API Key 只从环境变量或本地配置读取，不写进命令行参数、临时文件、报告和提交。
- 文件型规则一律用 `--input <文件>`，不要用 `--rules "$(cat ...)"`，不要把大 JSON 塞进命令行。

### 编码与 Windows 终端

- 所有工作区 JSON、临时脚本、手动填写译文表、规则文件和交付报告都必须按 UTF-8 读写；禁止依赖 Windows 默认编码、ANSI、GBK 或 Shift-JIS。
- 写 JSON 时保持 UTF-8 文本，推荐保留中日文原文可读性，例如 Python 使用 `json.dumps(..., ensure_ascii=False)` 并显式 `encoding="utf-8"`。
- Agent 自写临时脚本时必须显式声明编码：Python 使用 `Path.read_text/write_text(..., encoding="utf-8")` 或 `open(..., encoding="utf-8")`；Node.js 使用 `fs.readFile/writeFile(..., "utf8")`；PowerShell 写文件必须显式 `-Encoding utf8`。
- 在 Windows 终端运行 CLI 时优先使用 `--agent-mode --json` 降低控制台渲染影响；如果 stdout 出现乱码，先在同一 shell 设置 UTF-8 后重跑命令，不要基于乱码内容修改文件。
- PowerShell 推荐先执行：`$OutputEncoding = [System.Text.UTF8Encoding]::new(); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new(); [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()`。
- 如果发现工作区文件、CLI 输出或子代理结果出现乱码，必须先停止当前阶段并修复编码来源；禁止继续导入、翻译或写回乱码数据。
- 控制符、括号和引号边界不能只看终端显示；遇到乱码或 `\` 控制片段异常时，必须核验 Unicode code point 或原始字节，再决定规则和译文，禁止凭肉眼把非 ASCII 字符猜成 ASCII 字符。

## 3. 启动前必须确认

缺任意一项就先问用户，不启动翻译：

- `<项目目录>` 可进入，并能执行 `uv run python main.py --help`。
- `<游戏目录>` 存在，且是 RPG Maker MZ 标准结构。
- `<工作区>` 已确定，可写，可清理。
- 模型环境变量或本地配置已准备；用户允许时才做模型连通性检查。
- 用户是否允许最终执行 `write-back`（把译文写进游戏文件）已明确。

## 4. 新游戏主流程

1. 项目检查：`doctor --no-check-llm --json`。
2. 注册游戏：`add-game --path <游戏目录> --json`，后续使用返回的 `<游戏标题>`。
3. 游戏检查：`doctor --game <游戏标题> --json`。
4. 准备工作区：`prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json`。
5. 分析并导入占位符规则。
6. 分析并导入术语表、插件规则、事件指令规则、Note 标签规则。
7. `validate-agent-workspace --game <游戏标题> --workspace <工作区> --json`。
8. 小批量翻译：`translate --game <游戏标题> --max-batches 1 --json`。
9. 查看翻译进度报告 `translation-status --json` 和质量检查报告 `quality-report --json`。
10. 稳定后继续 `translate --game <游戏标题> --json`，直到没有“还没成功保存译文的文本”，或只剩必须手动填写译文表的文本。
11. 需要一次导出全部没成功保存的文本时，用 `export-untranslated-translations`；只想抽样或分批时，用 `export-pending-translations --limit N`；填写中文译文行后，用 `import-manual-translations` 交回项目检查并保存。
12. 若模型或人工 Agent 判断某个日文名单、作品名、品牌名、专有名词确实不应翻译，必须写入 `japanese-residual-rules.json`，先 `validate-japanese-residual-rules --json`，再 `import-japanese-residual-rules --json`，禁止全局关闭日文残留检测。
13. `quality-report --json` 没有错误，并且用户允许写回后，执行 `write-back --game <游戏标题> --json`。
14. 清理 `<工作区>`；如果工作区由 `prepare-agent-workspace` 生成，优先用 `cleanup-agent-workspace --workspace <工作区> --json`。

## 5. 二次翻译主流程

- 不把二次翻译当新游戏重做。
- 先执行 `doctor --game <游戏标题> --json`、翻译进度报告 `translation-status --game <游戏标题> --json`、质量检查报告 `quality-report --game <游戏标题> --json`。
- 已保存的译文记录会复用；CLI 只处理当前游戏里还没成功保存译文的文本。
- 如果用户明确要求完整重译已经完成的游戏，先执行 `reset-translations --game <游戏标题> --all --json`，确认 `summary.mode=all` 且 `summary.reset_count` 可解释，再按小批量到全量的正文翻译流程继续。
- 游戏文件、插件配置、事件指令结构或自定义控制符发生变化时，重新导出工作区并重新分析对应规则。
- 二次写回由 CLI 直接替换当前激活文件；不要手工移动 `data/` 或 `data_origin/`。

## 6. 工作区文件规则

`prepare-agent-workspace` 常见文件：

- `manifest.json`：清理清单。
- `placeholder-candidates.json`：候选控制符报告，大文件不要整读。
- `placeholder-rules.json`：占位符规则草稿。
- `name-context/name_registry.json`：术语表，只填写 value。
- `name-context/speaker_contexts/*.json`：名字上下文样本。
- `plugins.json`：插件原始 JSON。
- `plugin-rules.json`：插件规则草稿；如果当前游戏数据库已导入有效插件规则，CLI 会预先回填。
- `event-commands.json`：事件指令参数导出。
- `event-command-rules.json`：事件指令规则草稿；如果当前游戏数据库已导入规则，CLI 会预先回填。
- `note-tag-candidates.json`：基础数据库 `note` 字段的标签候选报告。
- `note-tag-rules.json`：Note 标签规则草稿；如果当前游戏数据库已导入规则，CLI 会预先回填，否则默认 `{}`。

`prepare-agent-workspace` 会优先把当前游戏数据库中已经通过 CLI 导入的术语表、插件规则、事件指令规则、Note 标签规则和占位符规则回填到工作区。新游戏或未导入规则时，工作区仍会给出空对象或候选草稿，供 Agent 分析填写。

Agent 可以在 `<工作区>` 内写临时脚本分析这些文件。项目只关心最终是否通过 CLI 校验并导入数据库。

### 黑盒执行原则

- 翻译任务中，把本项目当成闭源黑盒工具使用：禁止依赖源码、数据库表结构或内部 Python 对象来推断规则格式。
- 所有业务数据进出只走 CLI、`<工作区>` JSON、当前游戏数据库中已导入的规则和游戏目录文件。
- `--json` 输出里的 `status` 是程序给出的阶段结果：`error` 表示当前阶段不能继续；`warning` 必须阅读并判断是否属于允许的空结果或可以继续处理的状态；`ok` 才能进入下一阶段。
- 每个阶段都必须明确输入、处理逻辑、输出、校验命令和失败恢复动作；缺一项就先补上下文，不把模糊任务交给子代理。
- 外部 Agent 只负责分析和填写工作区文件，最终是否可用由本项目 CLI 校验决定。

### 输入-逻辑-输出总则

主 Agent 执行每个阶段前，必须先明确“输入是什么、处理逻辑是什么、输出什么”。缺任意一项就先补上下文或停下询问，不把模糊任务交给子代理。

| 阶段 | 输入 | 逻辑 | 输出 |
| --- | --- | --- | --- |
| 环境与注册 | `<项目目录>`、`<游戏目录>`、模型配置 | 用 CLI 检查环境、注册游戏、确认 `<游戏标题>` | 已注册游戏标题，或可理解的失败原因 |
| 工作区准备 | `<游戏标题>`、`<工作区>` | 用 CLI 导出 Agent 工作区文件，不手工拼数据库数据 | `<工作区>` 内的候选文件和规则草稿 |
| 外部分析 | 工作区候选文件、规则草稿 | 由主 Agent 或子代理按本 Skill 的规则筛选可翻译内容 | 术语表、占位符规则、插件规则、事件指令规则、Note 标签规则 |
| 验收导入 | 五类外部分析文件 | 逐个运行 `validate-* --json`，通过后再 `import-*` | 当前游戏数据库内的有效规则 |
| 翻译与手动填写译文表 | 当前游戏数据库、模型配置、质量检查报告 | 小批量试跑、查看进度、处理没成功保存的文本和检查没通过的译文 | 没有未保存译文，或只剩已向用户说明的手动处理项 |
| 写进游戏文件 | 已保存译文、无错误的质量检查报告、用户许可 | 执行 `write-back --json`，不直接移动 data 目录 | 已把译文写进游戏目录，并输出机器可读摘要 |

### 命令 I/O 合约

| 命令 | 输入 | 前置条件 | 输出用途 | 成功判断 | 失败后处理 |
| --- | --- | --- | --- | --- | --- |
| `doctor --no-check-llm --json` | `<项目目录>`、本地配置 | 可进入项目目录 | 检查项目静态环境；缺失 `data/db` 时应自愈创建 | `status` 不是 `error` | 按 `errors` 修环境；不启动翻译 |
| `add-game --path <游戏目录> --json` | RPG Maker MZ 游戏目录 | 游戏目录存在且结构有效 | 创建或更新当前游戏数据库，返回 `<游戏标题>` | `summary.game_title` 可作为后续 `--game` | 修正游戏目录或文件结构后重跑 |
| `doctor --game <游戏标题> --json` | 已注册游戏标题 | `add-game` 已成功 | 检查游戏绑定、规则导入状态和占位符风险 | `status` 不是 `error` | 缺规则是 warning 时继续准备工作区；error 先修注册或游戏文件 |
| `prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json` | 游戏标题、工作区目录 | 游戏已注册 | 导出五类外部分析输入、已导入规则回填文件和 `manifest.json` | 工作区文件存在，`summary.workspace` 指向目标目录 | 删除不完整工作区后重跑 |
| `build-placeholder-rules --game <游戏标题> --output <规则文件> --json` | 游戏标题、规则输出文件 | 游戏已注册 | 单独生成占位符规则草稿 | 输出文件存在 | 先看 `errors`，不要手写替代 CLI 导出 |
| `validate-placeholder-rules --game <游戏标题> --input <规则文件> --json` | 占位符规则 JSON | 规则文件存在 | 校验正则、模板和样本文本往返 | `status` 为 `ok` 或只有可接受空结果 warning | 修 `<规则文件>` 后重跑校验 |
| `import-placeholder-rules --game <游戏标题> --input <规则文件>` | 已校验规则文件 | validate 已通过 | 导入当前游戏数据库 | 命令返回 0 | 回到 validate 修规则，不直接改库 |
| `import-name-context --game <游戏标题> --input <术语表>` | 填好的 `name_registry.json` | key 未改、只填 value | 导入名字框和地图名术语 | 命令返回 0 | 修术语表结构或空值策略后重跑 |
| `validate-plugin-rules --game <游戏标题> --input <规则文件> --json` | 插件规则 JSON | `plugins.json` 已分析 | 校验插件名、插件哈希和 JSONPath 命中字符串叶子 | `status` 为 `ok`，或空规则 warning 已确认 | 修 `plugin-rules.json`，不读源码猜路径 |
| `import-plugin-rules --game <游戏标题> --input <规则文件>` | 已校验插件规则 | validate 已通过 | 导入插件可翻译字段规则 | 命令返回 0 | 回到 validate 修规则 |
| `validate-event-command-rules --game <游戏标题> --input <规则文件> --json` | 事件指令规则 JSON | `event-commands.json` 已分析 | 校验指令编码、参数过滤、路径命中和回写预演 | 无 `errors`；warning 需说明原因 | 修 `event-command-rules.json` 后重跑 |
| `import-event-command-rules --game <游戏标题> --input <规则文件>` | 已校验事件指令规则 | validate 无 error | 导入事件指令文本规则 | 命令返回 0 | 回到 validate 修规则 |
| `export-note-tag-candidates --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 游戏已注册 | 单独导出基础数据库 `note` 字段标签候选，供 Note 标签子代理分析 | 输出文件存在，`summary.candidate_tag_count` 可解释 | 候选为空时可确认 `{}`；异常先修游戏注册或文件 |
| `validate-note-tag-rules --game <游戏标题> --input <规则文件> --json` | Note 标签规则 JSON | `note-tag-candidates.json` 已分析 | 校验文件名、标签名、命中值、机器协议排除和回写预演 | 无 `errors`；空 `{}` 只允许 warning | 修 `note-tag-rules.json` 后重跑 |
| `import-note-tag-rules --game <游戏标题> --input <规则文件> --json` | 已校验 Note 标签规则 | validate 无 error | 导入基础数据库 `note` 标签文本规则 | `status` 为 `ok`，或空规则 warning 已确认 | 回到 validate 修规则 |
| `validate-agent-workspace --game <游戏标题> --workspace <工作区> --json` | 完整工作区 | 五类文件已由主 Agent 复核 | 总体验收工作区可导入性；缺 `note-tag-rules.json` 是必须修复的错误 | 无 `errors` | 逐项修工作区 JSON 后重跑 |
| `translate --game <游戏标题> --max-batches 1 --json` | 游戏标题、模型配置 | 工作区已校验并导入 | 小批量试跑正文翻译 | 命令返回 0 且质量报告没有新增错误 | 看 status 和 quality-report，不盲目全量 |
| `translate --game <游戏标题> --json` | 游戏标题、模型配置 | 小批量稳定 | 继续翻译还没成功保存译文的文本 | 命令返回 0 | 看翻译进度报告和质量检查报告，决定继续跑、换模型、改规则或手动填写译文表 |
| `translation-status --game <游戏标题> --json` | 游戏标题 | 至少跑过翻译或导入 | 判断当前还有多少文本没成功保存译文、已成功多少、模型接口是否失败；`pending_count` 表示当前没成功保存译文的文本数，`run_pending_count` 表示最近一次 translate 开始时要处理的文本数 | 数量能解释 | 剩余数量少时导出手动填写译文表；大量同类失败时先修规则或换模型 |
| `quality-report --game <游戏标题> --json` | 游戏标题 | 已有译文或翻译运行记录 | 判断是否可以写进游戏文件，并列出需要修的文本 | `status` 为 `ok` | 按报告明细修译文或规则，禁止继续写进游戏文件 |
| `export-quality-fix-template --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 质量检查报告有可修复明细 | 生成可填写的修复表，里面会预填当前译文或模型临时译文 | 输出文件存在，`summary.exported_count` 可解释 | 只改“中文译文行”，再用 `import-manual-translations` 交回项目检查并保存；禁止手拼数据库 UPDATE |
| `export-untranslated-translations --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 存在还没成功保存译文的文本 | 一次导出全部还没成功保存译文的原文，生成可填写的译文表 | 输出文件存在 | 若 warning 为空结果，说明已经没有需要手动填写的文本 |
| `export-pending-translations --game <游戏标题> --limit N --output <文件> --json` | 游戏标题、数量、输出文件 | 存在还没成功保存译文的文本 | 分批或抽样导出可填写的译文表；不传 `--limit` 时导出全部 | 输出文件存在 | 若 warning 为空结果，说明已经没有需要手动填写的文本 |
| `import-manual-translations --game <游戏标题> --input <文件> --json` | 已填写的译文表 JSON | 只填写“中文译文行” | 检查并保存手动填写的译文；多行对话会按当前行宽设置自动拆短 | `status` 为 `ok` | 修对应条目的中文译文行后重跑 |
| `reset-translations --game <游戏标题> --input <文件> --json` | `{"location_paths": [...]}` 文件 | 明确需要删除坏译文，让这些文本重新交给模型翻译 | 精确删除这些路径的已保存译文记录 | `status` 为 `ok`，`summary.mode=input`，`summary.reset_count` 可解释 | 非法路径会让整条命令失败；修输入文件，不用空译文伪造重置 |
| `reset-translations --game <游戏标题> --all --json` | 当前游戏数据库和当前提取范围 | 用户明确要求完整重译已完成游戏 | 删除当前提取范围内全部已保存译文记录，让 `translate` 重新处理 | `status` 不是 `error`，`summary.mode=all`，`summary.requested_count` 与当前提取量可解释 | 如果 `reset_count=0`，先确认是否本来没有已保存译文，不要直接改数据库 |
| `validate-japanese-residual-rules --game <游戏标题> --input <规则文件> --json` | 日文残留例外规则 JSON | 只在确需保留日文片段时使用 | 校验 location_path、allowed_terms 和 reason | `status` 为 `ok`，或空规则 warning 已确认 | 修规则文件；不要关闭全局日文残留检测 |
| `import-japanese-residual-rules --game <游戏标题> --input <规则文件> --json` | 已校验例外规则 | validate 已通过 | 导入当前游戏数据库，供 translate、import-manual-translations 和 quality-report 共用 | `status` 为 `ok` | 回到 validate 修规则，不直接改库 |
| `write-back --game <游戏标题> --json` | 游戏标题、已保存译文、用户许可 | `quality-report --json` 无 error | 写回游戏目录并输出摘要 | 命令返回 0 且 JSON 摘要可读 | 停止交付，按错误修质量或规则 |
| `cleanup-agent-workspace --workspace <工作区> --json` | 工作区目录 | `manifest.json` 存在 | 清理 CLI 生成的工作区文件 | 命令返回 0 | 缺 manifest 时手工确认后再清理 |

### 工作区 JSON 格式契约

- `placeholder-rules.json`：顶层必须是对象，格式为 `{正则表达式: 占位符模板}`。占位符模板必须生成形如 `[CUSTOM_NAME_1]` 的方括号占位符；推荐使用 `{index}`，例如 `[CUSTOM_PLUGIN_MARK_{index}]`。禁止写成 `{占位符名: 正则表达式}`，禁止把 RMMZ 标准控制符当自定义规则硬写。
- `name-context/name_registry.json`：顶层只使用 `speaker_names` 和 `map_display_names` 两个对象。Agent 只填写已有 key 对应的 value；不改 key，不新增字段，不写 note，不把样本文件路径写入 value。不能确定译名时保留空字符串并在最终报告说明。
- `plugin-rules.json`：顶层必须是对象，格式为 `{插件名: [JSONPath, ...]}`。插件名必须来自 `plugins.json`；JSONPath 必须使用括号路径语法并从 `$['parameters']` 开始，例如 `$['parameters']['message']` 或 `$['parameters']['items'][*]['name']`；禁止使用 `$.xxx` 点号路径。
- `event-command-rules.json`：顶层必须是对象，格式为 `{指令编码字符串: [{match, paths}]}`。`match` 是参数索引字符串到期望字符串值的对象；`paths` 是 `$['parameters']...` 路径数组；路径必须命中字符串叶子。没有过滤条件时 `match` 使用 `{}`。RMMZ 插件命令 `code=357` 的 `parameters = [插件名, 指令名, 显示名, 参数对象]`，参数对象里的文本通常从 `$['parameters'][3]` 开始；如果可见文本就是顶层字符串叶子，也允许 `$['parameters'][2]` 这类直接路径。
- `note-tag-rules.json`：顶层必须是对象，格式为 `{基础数据库文件名: [note标签名, ...]}`。合法示例：`{"Items.json": ["拡張説明", "ExtendDesc"], "Weapons.json": ["拡張説明"]}`。只写精确标签名，不支持正则；空结果使用 `{}`，但必须说明已检查候选。禁止选择脚本、公式、资源名、ID、升级材料、布尔/枚举和纯系统标签，例如 `upgrade`、`ChainSkill`、`EquipState`。
- `pending-translations.json`：这是“还没成功保存译文的文本表”。顶层是 `{location_path: 条目对象}`，其中 `location_path` 是文本在游戏里的内部位置。导入前只填写 `translation_lines` 字符串数组，意思是“中文译文行”；保留导出的 `item_type`、`role`、`original_lines`、`text_for_model_lines`，禁止改 `location_path`，禁止保留程序占位符。`long_text` 是多行对话，可以按自然语义填写，导入命令会按当前 `[text_rules]` 行宽配置自动拆短；若无法安全拆分，后续质量检查报告会提示“某一行太长，游戏窗口放不下”，并禁止写进游戏文件。
- `quality-fix-template.json`：这是“检查没通过译文的修复表”，由 `export-quality-fix-template` 生成，顶层同样是 `{location_path: 条目对象}`。Agent 只改 `translation_lines`，也就是中文译文行；已保存但仍有问题的文本会预填当前译文，模型翻过但检查没通过的文本会优先预填模型临时译文，没有临时译文时为空数组。
- `reset-translations.json`：顶层必须是 `{"location_paths": ["<定位路径>"]}`。只用于显式重置坏译文；数组不能为空，路径必须来自当前提取范围，禁止用空 `translation_lines` 当重置信号。完整重译不要手工导出全集路径，直接使用 `reset-translations --game <游戏标题> --all --json`。
- `japanese-residual-rules.json`：这是“允许保留日文的例外表”。顶层是 `{location_path: {allowed_terms, reason}}`。`allowed_terms` 是允许原样保留的日文片段字符串数组；`reason` 必须说明原因，例如 `credits`、`staff_name`、`proper_noun`、`brand_name`。只在模型或人工 Agent 判断该片段确实不应翻译时使用；禁止用它掩盖整句漏翻，禁止在 `pending-translations.json` 内新增例外字段。
- 所有规则 JSON 允许空对象 `{}` 表示确认无可导入内容，但必须由负责 Agent 在最终回复说明空结果理由。

### 控制符字符级保留

- `original_lines`、`text_for_model_lines` 和待填 `translation_lines` 中凡是出现 `\` 开头的 RPG Maker 控制片段，都必须按协议处理：已替换成 `[RMMZ_...]` 或 `[CUSTOM_...]` 的片段按占位符还原；未被替换的裸露片段必须字符级照抄原文。
- 禁止把看起来“不标准”的控制片段自动修成标准格式。例如原文是 `\F3[66」「` 时，译文也必须保留 `\F3[66」「`；禁止改成 `\F3[66]「`，也禁止改成 `\F3[60」「`。
- 禁止为了通过校验改动控制符编号、括号、反斜杠、日文右引号 `」` 或紧邻的控制片段边界。看不懂的控制片段不是正文，不参与翻译。
- 如果 CLI 报 `疑似控制符不一致`、`placeholder_risk` 或 `CUSTOM_UNEXPECTED`，先逐条比较 `original_lines` 与 `translation_lines` 的控制片段，修正手动填写译文表后重跑导入或质量报告；禁止直接改数据库，禁止把失败条目清空。

## 7. 游戏控制符规则

- 先用 `build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json` 生成草稿。
- 再用 `validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json` 校验。
- 校验通过后：`import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json`。
- 占位符名称必须语义化，例如 `[CUSTOM_PLUGIN_FACE_PORTRAIT_1]`，不要用 `[X_1]` 这种模型看不懂的名字。
- 标准 RMMZ 控制符如果被报告为未覆盖，先停下报告工具异常，不要硬凑规则。
- `\N` 类规则必须非常谨慎；禁止使用会匹配裸 `\n`、`\r`、`\t` 的宽规则，例如 `(?i)\\N\d*`。如果确实是自定义数字控制符，优先写成要求至少一个数字的精确规则，例如 `(?i)\\N\d+`。
- 如果报告出现非 ASCII 右引号、全角括号或未闭合控制片段 warning，先用 Unicode code point 确认边界字符，再写精确规则；不要把 `\F3[66」「` 猜成 `\F3[66]「`。

## 8. 四类外部分析

这四类在翻译前都必须导出、分析、确认、验收。强制的是“确认”，不是强制产出非空规则。

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

### Note 标签规则

- 输入：`note-tag-candidates.json`。
- 输出：`note-tag-rules.json`，对象格式，key 是基础数据库文件名，value 是 Note 标签名数组。
- 只选择 `note` 字段里由插件消费且玩家可见的长段说明文本，例如 `<拡張説明:...>` 或 `<ExtendDesc:...>`。
- 排除机器协议：脚本、公式、资源名、ID、升级材料、布尔/枚举、装备状态、连锁技能等标签，例如 `<upgrade:...>`、`<ChainSkill:...>`、`<EquipState:...>`。
- Note 标签为空或没有玩家可见文本时，允许 `{}`，但必须说明已检查候选。
- 校验：`validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json`。
- 导入：`import-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json`。

## 9. 子代理规则

如果平台支持子代理，必须启用子代理并行处理外部分析任务；不支持子代理时，才允许串行处理。

默认拆分五个互不写同一文件的子代理：

- `placeholder-rules` 子代理：读取 `placeholder-candidates.json` 和 `placeholder-rules.json`，只写 `placeholder-rules.json`。
- `name-context` 子代理：读取 `name-context/name_registry.json` 和 `speaker_contexts/*.json`，只写 `name-context/name_registry.json`。
- `plugin-rules` 子代理：读取 `plugins.json`，只写 `plugin-rules.json`。
- `event-command-rules` 子代理：读取 `event-commands.json`，只写 `event-command-rules.json`。
- `note-tag-rules` 子代理：读取 `note-tag-candidates.json` 和 `note-tag-rules.json`，只写 `note-tag-rules.json`。

### 五类子代理任务契约

主 Agent 派发子代理时，必须把对应行的输入、逻辑和输出写进子代理 prompt。子代理只完成自己的契约，不导入数据库，不启动翻译，不修改其他文件。

| 子代理 | 输入 | 逻辑 | 输出 |
| --- | --- | --- | --- |
| `placeholder-rules` | `placeholder-candidates.json`、`placeholder-rules.json` 草稿 | 判断哪些候选是游戏自定义控制符或脚本标记；保留对象格式；不把 RMMZ 标准控制符硬写成自定义规则 | 只写 `placeholder-rules.json`，格式为 `{正则表达式: 占位符模板}`；无自定义规则时输出 `{}` |
| `name-context` | `name-context/name_registry.json`、`speaker_contexts/*.json` | 统一角色名、地图名、称呼和声音变体；只填写 value；不新增字段和说明 | 只写 `name-context/name_registry.json`，key 保持不变 |
| `plugin-rules` | `plugins.json` | 只选择插件参数里的玩家可见文本；排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识 | 只写 `plugin-rules.json`，格式为 `{插件名: [JSONPath, ...]}`；无可见文本时输出 `{}` |
| `event-command-rules` | `event-commands.json` | 按事件指令编码判断参数中的玩家可见文本；不为资源、脚本、数字、布尔值和内部标识写规则 | 只写 `event-command-rules.json`，格式按事件指令编码分组；无可见文本时输出 `{}` |
| `note-tag-rules` | `note-tag-candidates.json`、`note-tag-rules.json` 草稿 | 判断基础数据库 `note` 标签值中哪些是玩家可见说明文本；排除机器协议标签 | 只写 `note-tag-rules.json`，格式为 `{基础数据库文件名: [note标签名, ...]}`；无可见标签时输出 `{}` |

主 Agent 职责：

- 主 Agent 必须等待五类子代理全部完成。
- 主 Agent 必须读取每个子代理结果，复核文件结构和空结果理由。
- 主 Agent 必须运行对应 `validate-*` 命令。
- 主 Agent 必须在校验通过后执行对应 `import-*` 命令。
- 任一子代理未完成、失败或校验未通过，不启动翻译。
- 不允许多个子代理同时修改同一个文件。

### 子代理上下文包

主 Agent 派发每个子代理时，必须提供最小但完整的上下文包，不要让子代理靠猜，也不要把大 JSON 正文塞进子代理 prompt。

每个子代理 prompt 必须包含：

- `<项目目录>`、`<工作区>`、`<游戏标题>`。
- 子代理角色名，例如 `placeholder-rules`、`name-context`、`plugin-rules`、`event-command-rules`、`note-tag-rules`。
- 输入文件清单和输出文件路径。
- 只读范围和唯一可写文件；只允许写自己负责的输出文件。
- 当前任务的输出 JSON 格式、禁止新增字段规则、空结果允许条件。
- 需要重点排除的内容，例如资源路径、脚本、数字、布尔值、内部标识、数据库字段和调试路径。
- 完成后必须报告：改动文件、是否为空结果、空结果理由、未解决风险、建议主 Agent 运行的校验命令。

推荐子代理 prompt 模板：

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

五类子代理的额外上下文要求：

- `placeholder-rules`：说明 `placeholder-candidates.json` 是候选报告、`placeholder-rules.json` 是规则草稿；必须保留对象格式，键是正则表达式，值是 `[CUSTOM_NAME_{index}]` 形式占位符模板。
- `name-context`：说明只能填写 `name_registry.json` 的 value，不能改 key、不能新增字段、不能写 note；必要时读取 `speaker_contexts/*.json` 判断称呼和角色一致性。
- `plugin-rules`：说明 `plugin-rules.json` 必须是 `{插件名: [JSONPath, ...]}`；只选玩家可见文本，排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识。
- `event-command-rules`：说明 `event-command-rules.json` 必须按事件指令编码分组；只为参数中的玩家可见文本写规则，参数无可见文本时允许空结构。
- `note-tag-rules`：说明 `note-tag-rules.json` 必须是 `{基础数据库文件名: [note标签名, ...]}`；只选玩家可见 Note 标签值，排除 `upgrade`、`ChainSkill`、`EquipState` 等机器协议。

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

`note-tag-rules` 子代理任务单：

```text
输入：读取 <工作区>/note-tag-candidates.json 和 <工作区>/note-tag-rules.json。
逻辑：判断基础数据库 note 标签值中哪些是玩家可见文本；长段说明标签可选，机器协议标签必须排除。
输出：只写 <工作区>/note-tag-rules.json，格式为 {基础数据库文件名: [note标签名, ...]}。
空结果：确认候选中没有玩家可见 Note 标签文本时输出 {}。
完成报告：说明检查过的文件、选中标签、排除标签及理由、空结果理由、建议运行 validate-note-tag-rules --json。
```

### 子代理最佳工作示例

主 Agent 派发子代理时，不能只概括“分析规则”。必须复制对应任务单和本节示例，填入 `<项目目录>`、`<工作区>`、`<游戏标题>`、输入文件、唯一可写文件和校验命令。

`placeholder-rules` 示例：

```text
输入片段: {"marker": "\\X[face_a]", "covered": false}
正确输出: {"(?i)\\\\X\\[[^\\]\\r\\n]+\\]": "[CUSTOM_PLUGIN_X_MARK_{index}]"}
错误输出: {"CUSTOM_PLUGIN_X": "(?i)\\\\X\\[[^\\]\\r\\n]+\\]"}
错误输出: {"(?i)\\\\N\\d*": "[CUSTOM_PLUGIN_N_{index}]"}
```

判断逻辑：`\X[face_a]` 不是 RMMZ 标准控制符，应该保护；键是正则表达式，值是能生成 `[CUSTOM_NAME_1]` 的模板。`\N` 宽规则会匹配裸 `\n`，必须收窄。看到 `\F3[66」「` 这类非 ASCII 边界时，先核验 Unicode code point，译文也逐字符保留 `\F3[66」「`，不要猜成 `\F3[66]「`。校验命令：`validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json`。

`name-context` 示例：

```text
输入片段 name_registry: {"speaker_names": {"案内人": "", "案内人の声": ""}, "map_display_names": {"始まりの町": ""}}
参考 speaker_contexts: {"source": "案内人", "samples": ["欢迎台词样例"]}
正确输出: {"speaker_names": {"案内人": "引路人", "案内人の声": "引路人的声音"}, "map_display_names": {"始まりの町": "起始之镇"}}
错误输出: {"speaker_names": {"引路人": "案内人"}, "note": "看起来像 NPC"}
```

判断逻辑：必须读取 `speaker_contexts/*.json`，根据上下文统一性别、称号、声音变体和地图名；只填已有 value，不改 key，不加 note。完成报告必须写明读取了多少个 `speaker_contexts/*.json`、哪些译名由上下文决定、哪些 value 保留空以及原因。校验命令：`validate-agent-workspace --game <游戏标题> --workspace <工作区> --json`。

`plugin-rules` 示例：

```text
输入片段: {"name": "DemoPlugin", "parameters": {"message": "按钮文本", "entries": [{"label": "菜单项"}], "file": "img/picture.png", "count": "12"}}
正确输出: {"DemoPlugin": ["$['parameters']['message']", "$['parameters']['entries'][*]['label']"]}
错误输出: {"DemoPlugin": ["$.parameters.message", "$['parameters']['file']", "$['parameters']['count']"]}
```

判断逻辑：只选玩家可见文本；JSONPath 使用括号语法并从 `$['parameters']` 开始。资源路径、脚本、数字、颜色、布尔值和内部标识都排除。校验命令：`validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json`。

`event-command-rules` 示例：

```text
输入片段: {"code": 357, "parameters": ["DemoPlugin", "ShowMessage", "显示名文本", {"messageText": "提示文本", "file": "Actor.png"}]}
正确输出: {"357": [{"match": {"0": "DemoPlugin", "1": "ShowMessage"}, "paths": ["$['parameters'][3]['messageText']"]}]}
顶层字符串输出: {"357": [{"match": {"0": "DemoPlugin", "1": "ShowMessage"}, "paths": ["$['parameters'][2]"]}]}
错误输出: {"357": [{"match": {"plugin": "DemoPlugin"}, "paths": ["$['parameters']['messageText']"]}]}
```

判断逻辑：`code=357 parameters = [插件名, 指令名, 显示名, 参数对象]`；插件命令参数对象通常从 `$['parameters'][3]` 取可见文本，顶层字符串叶子才用 `$['parameters'][2]`。`match` 的键必须是参数索引字符串。校验命令：`validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json`。

`note-tag-rules` 示例：

```text
输入片段: {"file_name": "Items.json", "tag_name": "拡張説明", "sample_values": ["药草的详细说明文本"]}
输入片段: {"file_name": "Weapons.json", "tag_name": "upgrade", "sample_values": ["1,2,3"]}
输入片段: {"file_name": "Skills.json", "tag_name": "ExtendDesc", "sample_values": ["技能追加说明文本"]}
正确输出: {"Items.json": ["拡張説明"], "Skills.json": ["ExtendDesc"]}
错误输出: {"Items.json": ["upgrade"], "Weapons.json": ["ChainSkill"]}
错误输出: {"note": {"Items.json": ["拡張説明"]}}
```

判断逻辑：`<拡張説明:...>`、`<ExtendDesc:...>` 这类长段说明文本通常会显示给玩家，可以选择；`<upgrade:...>`、`<ChainSkill:...>`、`<EquipState:...>` 是机器协议或系统标签，必须排除。输出只能是 `{基础数据库文件名: [note标签名, ...]}`，不写正则、不写 reason、不改游戏 `data/*.json`。校验命令：`validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json`。

## 10. 翻译失败处理

- `translate` 返回 0 只表示本轮命令正常结束，不代表所有文本都已经成功保存译文。
- 少量“还没成功保存译文的文本”和少量“模型翻了但项目检查没通过的译文”，可以继续跑同一个 `translate` 命令。连续多轮数量不明显下降时，停止盲目重跑。
- 小批量后如果有模型接口失败、译文检查没通过、游戏控制符可能被改坏，先排查，不继续全量翻译。
- 要查看最新一轮“模型翻了但项目检查没通过”的全部错误明细，先运行质量检查报告：`quality-report --game <游戏标题> --json`。
- 如果质量检查报告里有可修复明细，优先运行 `export-quality-fix-template --game <游戏标题> --output <工作区>/quality-fix-template.json --json`。这个命令会导出全部当前错误，生成可填写的修复表。
- 修复表里只改 `translation_lines`，也就是中文译文行；不要改文本内部位置、原文、文本类型、角色名等字段。改完后运行 `import-manual-translations --game <游戏标题> --input <工作区>/quality-fix-template.json --json`，让项目检查并保存。
- 如果质量检查报告只提示还有文本没成功保存译文，但没有可修复明细，使用 `export-untranslated-translations --game <游戏标题> --output <工作区>/pending-translations.json --json` 一次导出全部没成功保存的文本。
- 如果只想抽样或分批查看，使用 `export-pending-translations --game <游戏标题> --limit N --output <工作区>/pending-translations.json --json`；不传 `--limit` 时也会导出全部没成功保存的文本。
- 手动填写译文表时，只填写 `translation_lines`，也就是中文译文行；填写完成后使用 `import-manual-translations --game <游戏标题> --input <文件> --json` 交回项目检查并保存。
- 多行对话不要求 Agent 手工按固定宽度切行；导入命令会按当前行宽设置自动拆短，再保存到项目数据库。
- 如果质量检查报告提示“某一行太长，游戏窗口放不下”，必须修短到质量检查报告无错误后，才能写进游戏文件。
- 如果质量检查报告提示“中文译文里还有疑似没翻的日文”，先判断是不是漏翻；如果是漏翻，修中文译文行后导入。只有致谢名单、Staff 名、作品名、品牌名、游戏内专有名词等确实无需翻译的片段，才写入 `japanese-residual-rules.json` 并走 validate/import 例外流程。
- 如果模型明确认为某个日文片段保留原文比硬翻更准确，可以通过日文保留例外表放行；必须限制到具体文本内部位置和具体允许保留的词，并填写原因。
- 只有确认为坏译文需要重新交给 `translate` 时，才使用 `reset-translations --game <游戏标题> --input <工作区>/reset-translations.json --json`。该文件只接受 `{"location_paths": [...]}`，非法路径会整体停止，不能用空中文译文行伪造重置。
- 用户明确要求完整重译已完成游戏时，使用 `reset-translations --game <游戏标题> --all --json`，不要让 Agent 手工拼当前提取范围全集路径。

## 11. 写进游戏文件前的检查

写回前必须满足：

- 用户明确允许写回。
- `quality-report --json` 没有 `error` 错误。
- 占位符规则已覆盖当前游戏候选。
- 术语表、插件规则、事件指令规则已导入，或已确认游戏本身没有对应内容。
- Note 标签规则已导入，或已确认游戏本身没有玩家可见 Note 标签内容。
- 目标游戏目录可写。

不满足就停下报告，不要写回。

## 12. 检查失败后的处理

- `validate-* --json` 返回 `error` 时，先把错误映射回对应工作区 JSON，修文件后重跑同一个 validate 命令。
- `placeholder_rules_invalid`：优先检查是否把 `{正则表达式: 占位符模板}` 写反、模板是否能生成 `[CUSTOM_NAME_1]`、正则是否能编译。
- `plugin_rules_invalid`：优先检查插件名是否来自 `plugins.json`、JSONPath 是否从 `$['parameters']` 开始、是否误用了 `$.xxx` 点号路径、路径是否命中字符串叶子。
- `event_command_rules_invalid`：优先检查指令编码是否是字符串数字、`match` 键是否是参数索引、`paths` 是否从 `$['parameters']` 开始并命中字符串叶子。
- `note_tag_rules_invalid`：优先检查顶层是否是 `{基础数据库文件名: [note标签名, ...]}`，文件名是否来自基础数据库，标签名是否精确命中 `<标签:值>`，是否误选了 `upgrade`、`ChainSkill`、`EquipState` 等机器协议。
- `manual_translation_invalid`：优先检查 `translation_lines` 是否为字符串数组、行数是否匹配条目类型、是否残留程序占位符或日文残留。
- `japanese_residual_rules_invalid`：优先检查顶层 key 是否是当前还没成功保存译文的文本或已保存文本的 `location_path`，`allowed_terms` 是否为非空字符串数组且片段出现在当前条目原文或译文中，`reason` 是否非空。
- `quality-report --json` 返回 `placeholder_risk_items` 或 `overwide_line_items` 时，按 `location_path` 整理手动填写译文表，用 `import-manual-translations --json` 导入后重跑质量报告。
- `quality-report --json` 返回 `japanese_residual_items` 时，先修漏翻；确认为可保留日文时，再写 `japanese-residual-rules.json`，运行 `validate-japanese-residual-rules --json` 和 `import-japanese-residual-rules --json`。
- `quality-report --json` 返回可修复明细时，推荐先用 `export-quality-fix-template --json` 生成可填写的修复表；只有该命令输出为空或 CLI 行为异常时，才手工整理同格式 JSON。
- 需要删除坏译文、让文本重新交给模型翻译时，必须用 `reset-translations` 的 `location_paths` 显式文件；需要完整重译当前提取范围时，必须用 `reset-translations --all`；禁止把 `translation_lines` 写成空数组来绕过导入校验。
- 禁止因为校验失败而直接 UPDATE 数据库、跳过 validate、跳过 `validate-agent-workspace` 或继续写回。
- 只有错误信息无法对应到工作区 JSON，或同一合法文件反复触发无法解释的 CLI 错误时，才停止并报告工具问题。

## 13. 禁止做法

- 在 `<项目目录>` 写临时脚本或中间文件。
- 用临时脚本直接 `import app...` 操作数据库或游戏数据。
- 把没看懂结构当成“没有内容”。
- 为了让规则非空而编造插件规则、事件指令规则或术语。
- 子代理未完成就导入半成品或启动翻译。
- 看到 `translate` 有少量失败项就当作程序崩溃。
- `quality-report` 有 `error` 错误仍写回。
- 直接 UPDATE 数据库译文表绕过 `import-manual-translations`。
- 直接改游戏 `data/*.json` 的 `note` 字段，绕过 `note-tag-rules`、已保存译文记录和写进游戏文件前的检查。
- 用空 `translation_lines` 当作重置译文手段，绕过 `reset-translations`。
- 用日文残留例外规则掩盖整句漏翻，或用全局开关关闭日文残留检测。
- 把模型密钥写进命令、文档、日志摘要或临时文件。

