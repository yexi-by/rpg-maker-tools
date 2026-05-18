---
name: att-mz-release
description: 使用 A.T.T MZ 发行版执行 RPG Maker MV/MZ 游戏翻译流程：通过随包 att-mz.exe 注册游戏、准备工作区、分析游戏控制符和术语、导入规则、调用模型翻译、检查译文、手动填写失败译文、把第一版译文写进游戏文件，并根据用户试玩反馈持续查缺补漏。
---

# A.T.T MZ 发行版 Skill

本 Skill 是发行版翻译任务执行协议，不是项目说明书。按阶段执行，遇到不能继续的错误就停下报告，不要靠猜。发行版已经带好 `att-mz.exe`、配置模板、提示词、字体和必要参考资料；不要读取项目源码，不要运行 `uv run python main.py`，不要 import `app...`，也不要直接修改数据库。第一次写进游戏文件得到的是可试玩汉化结果，不是百分百完成承诺；真正稳定的汉化版本需要 Agent 根据用户试玩反馈继续查缺补漏。

## 按需参考资料

- RPG Maker MV/MZ 引擎常识、标准控制符、事件指令、数据库字段、插件与 Note 标签的基础知识放在 `references/rpg-maker-mv-mz-world-knowledge.md`。
- 只有在判断控制符是否合法、区分标准引擎协议和插件自定义协议、分析事件指令/Note/插件字段、或排查工具报告是否异常时，才打开该参考文档。
- 不要把参考文档全文复制进模型 prompt、交付报告或子代理任务单；只摘取当前判断所需的最小规则，并继续以 CLI 输出和当前工作区文件为准。
- 遇到裸 `\N`、`\V`、`\C`、`\I`、`\PX`、`\PY`、`\FS` 这类疑似缺参数片段时，先查参考文档再下结论；不要直接当作完整标准 RPG Maker 控制符。

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
- 不要把第一次写进游戏文件说成“已经百分百汉化完成”；说“已经生成第一版可试玩汉化结果，请先实际游玩并把漏翻、误翻、显示异常和语气不自然的地方反馈回来”。
- 收到用户反馈时，先把问题转成可处理的修复清单，再说明下一步是定位、修译文或补规则、重新检查、再次写进游戏文件。

## 1. 目录边界

- `<发行版目录>`：A.T.T MZ 发行版目录，必须包含 `att-mz.exe`、`setting.toml`、`skills/att-mz/SKILL.md`、`data/db`、`logs` 和随包资源。翻译任务只允许运行 CLI、读取 Skill、README 和 CLI 输出；如果怀疑工具本身有缺陷，停止翻译并报告工具问题。翻译任务中禁止在这里写临时脚本、中间 JSON、抽样报告和手动填写译文表。
- `<游戏目录>`：目标游戏。CLI 可以注册、读取、写回、生成 `data_origin`。只有用户明确允许字体覆盖时，CLI 才能复制候选字体，替换游戏数据里的字体引用，并在存在 `fonts/gamefont.css` 时备份和更新字体族入口。临时工作区也可以放在这里，但必须集中在一个明确目录里，不能散落到游戏根目录各处。
- `<工作区>`：任务临时目录。所有导出文件、规则草稿、临时脚本、中间结果、手动填写译文表都放这里。推荐使用 `<外部临时目录>/translation-workspace`；用户允许时也可以使用 `<游戏目录>/<临时工作区名>`。
- 翻译任务中，临时脚本不得直接 `import app...` 操作数据库或游戏数据。业务数据进出必须走本项目 CLI。
- 不直接修改数据库；所有业务数据必须通过随包 CLI、工作区 JSON 和游戏目录文件进出。
- 发行版 Skill 不处理 A.T.T MZ 项目本身的开发任务；如果用户要改工具源码，切换到源码仓库和开发版 Skill。

## 2. 固定命令习惯

- 进入 `<发行版目录>` 后运行命令。发行版目录必须包含 `att-mz.exe`。
- 默认使用：`.\att-mz.exe --agent-mode <命令> ...`。
- 需要机器读取结果时加 `--json` 或 `--output <文件>`。
- `--json` 命令的 stdout 只读取最终 JSON；`translate`、`quality-report`、`write-back`、`write-terminology` 等长任务会在 stderr 持续输出无 ANSI 文本进度条，显示已完成数量、百分比、已用时间、预计剩余时间和当前状态。
- 不要把 stderr 进度行当成命令结果 JSON；长任务运行时必须观察进度行，不能因为 stdout 暂时没有最终 JSON 就判断命令卡死。
- 全局参数放在子命令前，例如 `.\att-mz.exe --agent-mode doctor ...`。
- 模型地址和 API Key 只从环境变量或本地配置读取，不写进命令行参数、临时文件、报告和提交。
- 文件型规则一律用 `--input <文件>`，不要用 `--rules "$(cat ...)"`，不要把大 JSON 塞进命令行。

### 编码与 Windows 终端

- 所有工作区 JSON、临时脚本、手动填写译文表、规则文件和交付报告都必须按 UTF-8 读写；禁止依赖 Windows 默认编码、ANSI、GBK 或 Shift-JIS。
- 写 JSON 时保持 UTF-8 文本，推荐保留中日英原文可读性，例如 Python 使用 `json.dumps(..., ensure_ascii=False)` 并显式 `encoding="utf-8"`。
- 自写临时脚本时必须显式声明编码：Python 使用 `Path.read_text/write_text(..., encoding="utf-8")` 或 `open(..., encoding="utf-8")`；Node.js 使用 `fs.readFile/writeFile(..., "utf8")`；PowerShell 写文件必须显式 `-Encoding utf8`。
- 在 Windows 终端运行 CLI 时优先使用 `--agent-mode --json` 降低控制台渲染影响；如果 stdout 出现乱码，先在同一 shell 设置 UTF-8 后重跑命令，不要基于乱码内容修改文件。
- PowerShell 推荐先执行：`$OutputEncoding = [System.Text.UTF8Encoding]::new(); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new(); [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()`。
- 如果发现工作区文件、CLI 输出或子代理结果出现乱码，必须先停止当前阶段并修复编码来源；禁止继续导入、翻译或写回乱码数据。
- 控制符、括号和引号边界不能只看终端显示；遇到乱码或 `\` 控制片段异常时，必须核验 Unicode code point 或原始字节，再决定规则和译文，禁止凭肉眼把非 ASCII 字符猜成 ASCII 字符。

## 3. 启动前必须确认

缺任意一项就先问用户，不启动翻译：

- `<发行版目录>` 可进入，并能执行 `.\att-mz.exe --help`。
- `<游戏目录>` 存在，且是 RPG Maker MV/MZ 标准结构；目录内可以是直接存在 `data/js` 的内容目录，也可以是外层有 `Game.exe`、真实内容在 `www/data` 和 `www/js` 的部署目录。
- `<工作区>` 已确定，可写，可清理。
- 当前游戏源语言已确认：注册游戏时必须显式传 `--source-language ja` 或 `--source-language en`，不做语言自动检测。
- 模型环境变量或本地配置已准备；用户允许时才做模型连通性检查。
- 用户是否允许本轮执行 `write-back`（把译文写进游戏文件）已明确。
- 用户是否允许覆盖字体必须单独确认；未明确允许时，禁止使用 `--confirm-font-overwrite`。

## 4. 新游戏主流程

1. 发行版检查：`.\att-mz.exe --agent-mode doctor --no-check-llm --json`。
2. 注册游戏：日文游戏使用 `.\att-mz.exe --agent-mode add-game --path <游戏目录> --source-language ja --json`；英文游戏使用 `.\att-mz.exe --agent-mode add-game --path <游戏目录> --source-language en --json`；后续使用返回的 `<游戏标题>`。
3. 游戏检查：`.\att-mz.exe --agent-mode doctor --game <游戏标题> --no-check-llm --json`。
4. 准备工作区：`.\att-mz.exe --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json`。
5. 子代理任务处理方式确认：主代理说明接下来会处理术语候选、插件规则、事件指令规则和 Note 标签规则，并询问用户选择“当前会话完成”“外部协作任务包”或“混合处理”。多项候选分析会消耗较多上下文和模型额度；额度有限时建议使用外部协作任务包。用户未明确选择前，不要默认消耗大量子代理额度。
6. 第一轮子代理只处理术语候选：主代理按字段译名类别拆分 `field-terms.json`，把独立候选文件交给多个子代理或整理成外部协作任务包；子代理和任务包返回内容都只能作为候选，不能直接写 `terminology/field-terms.json` 或 `terminology/glossary.json`。主代理必须等待全部结果、逐项审查信达雅和译名统一、亲自修改字段译名表，并把 `/c<角色名>`、`"<角色名>"`、`◆<角色名>ｔ` 等字段形式由 Agent 人工判断后规范化为正文术语表的 `terms`，才执行 `import-terminology --json`。
7. 第二轮子代理再处理插件规则、事件指令规则和 Note 标签规则；如果用户选择外部协作任务包，主代理收回候选答案后二次审查，逐项校验并导入。
8. 主代理基于当前会进入正文翻译的完整文本集合，亲自生成、审查、覆盖扫描、校验并导入占位符规则。
9. `validate-agent-workspace --game <游戏标题> --workspace <工作区> --json`。
10. 小批量翻译：`translate --game <游戏标题> --max-batches 1 --json`。
11. 查看翻译进度报告 `translation-status --json` 和质量检查报告 `quality-report --json`。
12. 稳定后继续 `translate --game <游戏标题> --json`，直到没有“还没成功保存译文的文本”，或只剩必须手动填写译文表的文本。
13. 需要一次导出全部没成功保存的文本时，用 `export-untranslated-translations`；只想抽样或分批时，用 `export-pending-translations --limit N`；填写中文译文行后，用 `import-manual-translations` 交回项目检查并保存。
14. 若确认某个名单、作品名、品牌名、专有名词确实应保留源语言原文，必须写入 `source-residual-rules.json`。日文和英文游戏都使用通用源文残留命令，先 `validate-source-residual-rules --json`，再 `import-source-residual-rules --json`，禁止全局关闭源文残留检测。
15. `quality-report --json` 没有错误，并且用户允许写回后，执行 `write-back --game <游戏标题> --json`。普通写回不会覆盖字体；只有用户另外明确允许覆盖字体时，才执行 `write-back --game <游戏标题> --confirm-font-overwrite --json`。
16. 写回完成后，向用户说明这是第一版可试玩汉化结果；请用户运行游戏，重点反馈漏翻、误翻、术语不统一、窗口显示异常、剧情语气不自然、按钮菜单看不懂、插件界面仍是源语言文本、图片文字未处理等问题。
17. 收到试玩反馈后进入“试玩反馈迭代流程”；每轮修复都必须重新运行质量检查报告，必要时再次写进游戏文件。
18. 用户确认本轮试玩反馈已经处理完成，或明确表示暂不继续修复后，才清理 `<工作区>`；如果工作区由 `prepare-agent-workspace` 生成，优先用 `cleanup-agent-workspace --workspace <工作区> --json`。

## 5. 试玩反馈迭代流程

- 试玩反馈是正式翻译流程的一部分。不要把用户反馈当成额外闲聊，也不要承诺静态质量检查能替代实际游玩。
- 先向用户收集最小可定位信息：问题截图或原文片段、游戏场景、玩家看到的现译文、期望表达、是否影响继续游玩、能否稳定复现。
- 如果用户只说“有些地方没翻”或“这个翻译很怪”，先请用户补充截图、具体文本或场景；不能凭空猜测并批量重置。
- 把反馈分成几类处理：
  - 漏翻：先运行 `translation-status --game <游戏标题> --json` 和 `quality-report --game <游戏标题> --json`，确认是否还有没成功保存译文的文本或没导入的文本来源。
  - 明显错译、称呼错、语气不自然、术语不统一：优先导出修复表或手动填写译文表，只改中文译文行后导入。
  - 控制符、名牌、语音触发标记、自动替换标记异常：先按占位符规则风险处理，必要时精确重置受影响文本，未经用户选择不得完整重译。
  - 菜单、插件界面、事件指令参数或 Note 标签仍是源语言文本：重新准备或复用工作区，检查并补充对应外部规则，校验导入后再处理相关译文。
  - 游戏窗口放不下、换行难看、按钮被截断：按质量检查报告和用户截图修短中文译文行，再导入并复查。
  - 图片文字、视频文字或引擎外资源文字：向用户说明当前 CLI 主要处理 RPG Maker 数据文本；除非用户另开资源修图任务，否则只记录为非本流程自动处理项。
- 每轮反馈修复都必须走“导出可填写文件或规则文件 -> 修改 -> validate/import -> quality-report -> 用户确认是否再次写进游戏文件”的闭环；禁止直接改数据库或手工改游戏 `data/*.json`。
- 如果反馈影响范围很小，优先精确修复对应文本；如果反馈显示某类规则整体漏掉，先补规则再处理相关文本；只有用户明确选择完整重译时，才执行 `reset-translations --all`。
- 每轮再次写进游戏文件后，都要告诉用户“这一轮反馈已处理，请继续试玩确认”；直到用户确认主要流程已玩过、关键问题已处理，或明确接受剩余问题。
- 面向用户的阶段总结必须包含：本轮处理了哪些反馈、哪些已经重新写进游戏文件、哪些需要用户继续试玩确认、哪些不属于当前自动文本流程。

## 6. 二次翻译主流程

- 不把二次翻译当新游戏重做。
- 先执行 `doctor --game <游戏标题> --no-check-llm --json`、翻译进度报告 `translation-status --game <游戏标题> --json`、质量检查报告 `quality-report --game <游戏标题> --json`。
- 已保存的译文记录会复用；CLI 只处理当前游戏里还没成功保存译文的文本。
- 如果发现遗漏的角色名牌、语音触发标记、自动替换标记或其他必须原样保留的游戏控制内容，且已有大量译文成功保存，必须先暂停并向用户说明：已保存译文数量、疑似受影响范围、继续补翻的成本、精确重置受影响文本的成本、完整重译的成本。未经用户明确选择，禁止执行 `reset-translations --all`，禁止删除全部已保存译文记录。
- 如果用户明确要求完整重译已经完成的游戏，先执行 `reset-translations --game <游戏标题> --all --json`，确认 `summary.mode=all` 且 `summary.reset_count` 可解释，再按小批量到全量的正文翻译流程继续。
- 游戏文件、插件配置、事件指令结构或自定义控制符发生变化时，重新导出工作区并重新分析对应规则。
- 二次写回由 CLI 直接替换当前激活文件；不要手工移动 `data/` 或 `data_origin/`。二次写回后仍应要求用户继续试玩确认。

## 7. 工作区文件规则

`prepare-agent-workspace` 常见文件：

- `manifest.json`：清理清单。
- `placeholder-candidates.json`：初始候选控制符报告，只供主代理参考；最终占位符规则必须在术语表和三类外部规则导入后重新确认。
- `placeholder-rules.json`：初始占位符规则草稿；不能跳过最终审查直接导入。
- `terminology/field-terms.json`：字段译名表，只填写 value；用于精确写回地图显示名、数据库名称、系统类型，以及 MZ 标准 `101.parameters[4]` 名字框等游戏字段；其中 `speaker_names` 在 MV 中是从正文首行识别出的说话人术语，只服务译名统一和正文翻译提示词命中，不写回游戏文件。
- `terminology/glossary.json`：正文术语表，顶层固定为 `terms`；只用于正文翻译提示词命中。
- `terminology/contexts/speakers/*.json`：说话人对白样本；MZ 来自标准名字框，MV 来自每个对话块首条非空 `401` 正文识别出的说话人。
- `terminology/contexts/database_terms.json`：技能、物品、装备、角色、敌人、状态等术语的只读语义上下文。
- `terminology/subtasks/sources/*.json`：按术语字段拆分的只读子代理输入。
- `terminology/subtasks/candidates/*.json`：术语候选子代理的唯一可写文件；主代理合并前必须审查和修改。
- `plugins.json`：插件原始 JSON。
- `plugin-rules.json`：插件规则草稿；如果 CLI 已保存当前游戏的有效插件规则，会预先回填。
- `event-commands.json`：事件指令参数导出。
- `event-command-rules.json`：事件指令规则草稿；如果 CLI 已保存当前游戏的事件指令规则，会预先回填。
- `note-tag-candidates.json`：标准 `data/*.json` 中全部 `note` 字段的标签候选报告。
- `note-tag-rules.json`：Note 标签规则草稿；如果 CLI 已保存当前游戏的 Note 标签规则，会预先回填，否则默认 `{}`。

`prepare-agent-workspace` 会优先把 CLI 已保存到当前游戏状态里的字段译名表、正文术语表、插件规则、事件指令规则、Note 标签规则和占位符规则回填到工作区。新游戏或未保存规则时，工作区仍会给出空对象或候选草稿，供主代理分析填写。占位符候选和草稿只是前置参考，最终规则必须在字段译名表、正文术语表和三类外部规则保存后重新生成或逐条确认。

主代理可以在 `<工作区>` 内写临时脚本分析这些文件。项目只关心最终是否通过 CLI 校验并保存为当前游戏规则。

### 只按任务文件和命令结果工作

- 翻译任务中，只按 CLI 输出、工作区 JSON、游戏目录和用户明确提供的信息判断；禁止靠项目源码或程序内部数据猜规则格式。
- 所有业务数据进出只走 CLI、`<工作区>` JSON、CLI 已保存到当前游戏状态的规则和游戏目录文件。
- `--json` 输出里的 `status` 是程序给出的阶段结果：`error` 表示当前阶段不能继续；`warning` 必须阅读并判断是否属于允许的空结果或可以继续处理的状态；`ok` 才能进入下一阶段。
- 每个阶段都必须明确输入、处理逻辑、输出、校验命令和失败恢复动作；缺一项就先补上下文，不把模糊任务交给子代理。
- 主代理负责分析和填写工作区文件，最终是否可用由本项目 CLI 校验决定。

### 执行前需要知道什么

- 需要知道：任务目标、输入文件、唯一可写文件、允许的 JSON 形状、筛选或翻译原则、校验命令、失败后该修哪个工作区文件。
- 不需要知道：源码、程序内部数据、模型提示词怎样生成、占位符怎样恢复、译文怎样写进游戏文件、已保存译文记录怎样存放。
- 工作区文件里的定位键、路径、ID、计数字段和报告字段只当作需要原样保留的键；除格式契约要求外，不解释、不改写、不据此猜项目实现。
- 发给模型的提示词只写当前翻译或筛选任务、输出格式、质量要求和原样保留约束；不解释项目背后怎么处理这些数据。

### 输入-逻辑-输出总则

每个阶段开始前，必须先明确“输入是什么、处理逻辑是什么、输出什么”。缺任意一项就先补上下文或停下询问，不把模糊任务交给子代理。

| 阶段 | 输入 | 逻辑 | 输出 |
| --- | --- | --- | --- |
| 环境与注册 | `<发行版目录>`、`<游戏目录>`、模型配置 | 用 CLI 检查环境、注册游戏、确认 `<游戏标题>` | 已注册游戏标题，或可理解的失败原因 |
| 工作区准备 | `<游戏标题>`、`<工作区>` | 用 CLI 导出工作区文件，不手工拼数据库数据 | `<工作区>` 内的候选文件和规则草稿 |
| 术语表工程 | `terminology/field-terms.json`、`terminology/glossary.json`、术语上下文、术语拆分文件 | 主代理拆分字段译名类别并发起第一轮候选子代理任务；主代理严审信达雅、统一译名、亲自修改字段译名表，并维护正文术语表的规范术语 | 已通过 CLI 保存的字段译名表和正文术语表 |
| 外部规则分析 | 工作区候选文件、规则草稿 | 第二轮才发起插件规则、事件指令规则和 Note 标签规则子代理任务；主代理二次审查 | 插件规则、事件指令规则、Note 标签规则 |
| 占位符收束 | CLI 已保存的规则、当前会进入正文翻译的完整文本集合 | 主代理统一扫描、生成、审查、校验占位符规则 | 当前游戏有效占位符规则 |
| 验收导入 | 字段译名表、正文术语表、三类外部规则文件和占位符规则文件 | 逐个运行 `validate-* --json` 或导入命令，通过后再保存到项目数据库 | 当前游戏有效规则 |
| 翻译与手动填写译文表 | 当前游戏状态、模型配置、质量检查报告 | 小批量试跑、查看进度、处理没成功保存的文本和检查没通过的译文 | 没有未保存译文，或只剩已向用户说明的手动处理项 |
| 写进游戏文件 | 已保存译文、无错误的质量检查报告、用户许可、字体覆盖许可 | 执行 `write-back --json`，不直接移动 data 目录；未获得字体覆盖许可时不加 `--confirm-font-overwrite` | 已把译文写进游戏目录，并输出机器可读摘要 |
| 试玩反馈迭代 | 用户截图、场景说明、问题原文或现译文、当前游戏状态 | 分类定位反馈，精确修译文或补规则，重新检查并再次写进游戏文件 | 经过用户试玩确认的改进版汉化结果，或已记录的剩余问题 |

### 命令 I/O 合约

下表命令均省略统一前缀；在发行版中执行时使用 `.\att-mz.exe --agent-mode <命令> ...`。

| 命令 | 输入 | 前置条件 | 输出用途 | 成功判断 | 失败后处理 |
| --- | --- | --- | --- | --- | --- |
| `doctor --no-check-llm --json` | `<发行版目录>`、本地配置 | 可进入发行版目录 | 检查发行版静态环境；缺失 `data/db` 时应自愈创建 | `status` 不是 `error` | 按 `errors` 修环境；不启动翻译 |
| `add-game --path <游戏目录> --source-language ja --json` | RPG Maker MV/MZ 日文游戏目录 | 游戏目录存在且结构有效，且用户确认源语言为日文 | 按日文源语言注册或更新当前游戏状态，返回 `<游戏标题>` | `summary.game_title` 可作为后续 `--game`，`summary.source_language` 为 `ja` | 修正游戏目录、文件结构或源语言参数后重跑 |
| `add-game --path <游戏目录> --source-language en --json` | RPG Maker MV/MZ 英文游戏目录 | 游戏目录存在且结构有效，且用户确认源语言为英文 | 按英文源语言注册或更新当前游戏状态，返回 `<游戏标题>` | `summary.game_title` 可作为后续 `--game`，`summary.source_language` 为 `en` | 修正游戏目录、文件结构或源语言参数后重跑 |
| `doctor --game <游戏标题> --no-check-llm --json` | 已注册游戏标题 | `add-game` 已成功 | 检查游戏绑定、规则导入状态和占位符风险，不请求模型服务 | `status` 不是 `error` | 缺规则是 warning 时继续准备工作区；error 先修注册或游戏文件 |
| `prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json` | 游戏标题、工作区目录 | 游戏已注册 | 导出外部分析输入、已导入规则回填文件、占位符候选草稿和 `manifest.json` | 工作区文件存在，`summary.workspace` 指向目标目录 | 删除不完整工作区后重跑 |
| `build-placeholder-rules --game <游戏标题> --output <规则文件> --json` | 游戏标题、规则输出文件 | 插件规则、事件指令规则和 Note 标签规则已导入，或已确认对应内容为空 | 基于当前会进入正文翻译的完整文本集合生成占位符规则草稿 | 输出文件存在 | 先看 `errors`，不要手写替代 CLI 导出 |
| `validate-placeholder-rules --game <游戏标题> --input <规则文件> --json` | 占位符规则 JSON | 规则文件存在；外部文本规则已导入 | 校验正则、模板和当前正文样本文本往返 | `status` 为 `ok` 或只有可接受空结果 warning | 修 `<规则文件>` 后重跑校验 |
| `scan-placeholder-candidates --game <游戏标题> --input <规则文件> --json` | 占位符规则 JSON 和当前有效正文集合 | validate 已通过 | 证明最终规则覆盖当前会进入正文翻译的全部候选控制符 | `summary.uncovered_count` 必须等于 0 | 未覆盖时修 `<规则文件>`，再 validate 和 scan |
| `import-placeholder-rules --game <游戏标题> --input <规则文件> --json` | 已校验且覆盖扫描通过的规则文件 | validate 与 scan 都已通过 | 保存为当前游戏占位符规则 | `status` 为 `ok` 或只有可接受空结果 warning | 回到 validate/scan 修规则，不绕过 CLI |
| `import-terminology --game <游戏标题> --input <字段译名表> --glossary-input <正文术语表> --json` | 填好的 `field-terms.json` 和 `glossary.json` | 字段译名表 key 未改、只填 value；正文术语表只包含 `terms` | 导入用于写回的字段译名表，并导入用于正文提示词命中的正文术语表 | `status` 为 `ok` | 修字段译名表或正文术语表结构后重跑 |
| `validate-plugin-rules --game <游戏标题> --input <规则文件> --json` | 插件规则 JSON | `plugins.json` 已分析 | 按插件下标定位，校验插件名并检查 JSONPath 命中字符串叶子，同时计算插件哈希供导入后匹配当前配置 | `status` 为 `ok`，或空规则 warning 已确认 | 修 `plugin-rules.json`，不读源码猜路径 |
| `import-plugin-rules --game <游戏标题> --input <规则文件> --json` | 已校验插件规则 | validate 已通过 | 导入插件可翻译字段规则 | `status` 为 `ok` | 回到 validate 修规则 |
| `validate-event-command-rules --game <游戏标题> --input <规则文件> --json` | 事件指令规则 JSON | `event-commands.json` 已分析 | 校验指令编码、参数过滤、路径命中和回写预演 | 无 `errors`；warning 需说明原因 | 修 `event-command-rules.json` 后重跑 |
| `import-event-command-rules --game <游戏标题> --input <规则文件> --json` | 已校验事件指令规则 | validate 无 error | 导入事件指令文本规则 | `status` 为 `ok` | 回到 validate 修规则 |
| `export-note-tag-candidates --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 游戏已注册 | 单独导出标准 `data/*.json` 全部 `note` 字段标签候选，供 Note 标签子代理分析 | 输出文件存在，`summary.candidate_tag_count` 可解释 | 候选为空时可确认 `{}`；异常先修游戏注册或文件 |
| `validate-note-tag-rules --game <游戏标题> --input <规则文件> --json` | Note 标签规则 JSON | `note-tag-candidates.json` 已分析 | 校验文件名、标签名、命中值、机器协议排除和回写预演 | 无 `errors`；空 `{}` 只允许 warning | 修 `note-tag-rules.json` 后重跑 |
| `import-note-tag-rules --game <游戏标题> --input <规则文件> --json` | 已校验 Note 标签规则 | validate 无 error | 导入标准 `data/*.json` 的 `note` 标签文本规则 | `status` 为 `ok`，或空规则 warning 已确认 | 回到 validate 修规则 |
| `validate-agent-workspace --game <游戏标题> --workspace <工作区> --json` | 完整工作区 | 术语表、三类外部规则和占位符规则已复核并导入或确认可导入 | 总体验收工作区可导入性，并阻断未覆盖当前正文控制符的占位符规则 | 无 `errors` | 逐项修工作区 JSON 后重跑 |
| `translate --game <游戏标题> --max-batches 1 --json` | 游戏标题、模型配置 | 工作区已校验并导入 | 小批量试跑正文翻译 | 命令返回 0 且质量报告没有新增错误 | 看 status 和 quality-report，不盲目全量 |
| `translate --game <游戏标题> --json` | 游戏标题、模型配置 | 小批量稳定 | 继续翻译还没成功保存译文的文本 | 命令返回 0 | 看翻译进度报告和质量检查报告，决定继续跑、换模型、改规则或手动填写译文表 |
| `translation-status --game <游戏标题> --json` | 游戏标题 | 至少跑过翻译或导入 | 判断当前还有多少文本没成功保存译文、已成功多少、模型接口是否失败；`pending_count` 表示当前没成功保存译文的文本数，`run_pending_count` 表示最近一次 translate 开始时要处理的文本数 | 数量能解释 | 剩余数量少时导出手动填写译文表；大量同类失败时先修规则或换模型 |
| `quality-report --game <游戏标题> --json` | 游戏标题 | 已有译文或翻译运行记录 | 判断是否可以写进游戏文件，并列出需要修的文本 | `status` 为 `ok` | 按报告明细修译文或规则，禁止继续写进游戏文件 |
| `export-quality-fix-template --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 质量检查报告有可修复明细 | 生成可填写的修复表，里面会预填当前译文或模型临时译文 | 输出文件存在，`summary.exported_count` 可解释 | 只改“中文译文行”，再用 `import-manual-translations` 交回项目检查并保存；禁止绕过 CLI 手改项目数据 |
| `export-untranslated-translations --game <游戏标题> --output <文件> --json` | 游戏标题、输出文件 | 存在还没成功保存译文的文本 | 一次导出全部还没成功保存译文的原文，生成可填写的译文表 | 输出文件存在 | 若 warning 为空结果，说明已经没有需要手动填写的文本 |
| `export-pending-translations --game <游戏标题> --limit N --output <文件> --json` | 游戏标题、数量、输出文件 | 存在还没成功保存译文的文本 | 分批或抽样导出可填写的译文表；不传 `--limit` 时导出全部 | 输出文件存在 | 若 warning 为空结果，说明已经没有需要手动填写的文本 |
| `import-manual-translations --game <游戏标题> --input <文件> --json` | 已填写的译文表 JSON | 只填写“中文译文行” | 检查并保存手动填写的译文；多行对话会按当前行宽设置自动拆短 | `status` 为 `ok` | 修对应条目的中文译文行后重跑 |
| `reset-translations --game <游戏标题> --input <文件> --json` | `{"location_paths": [...]}` 文件 | 明确需要删除坏译文，让这些文本重新交给模型翻译 | 精确删除这些路径的已保存译文记录 | `status` 为 `ok`，`summary.mode=input`，`summary.reset_count` 可解释 | 非法路径会让整条命令失败；修输入文件，不用空译文伪造重置 |
| `reset-translations --game <游戏标题> --all --json` | 当前游戏状态和当前提取范围 | 用户在听到已保存译文数量、疑似受影响范围和重跑成本后，仍明确要求完整重译已完成游戏 | 删除当前提取范围内全部已保存译文记录，让 `translate` 重新处理 | `status` 不是 `error`，`summary.mode=all`，`summary.requested_count` 与当前提取量可解释 | 如果 `reset_count=0`，先确认是否本来没有已保存译文，不要绕过 CLI |
| `validate-source-residual-rules --game <游戏标题> --input <规则文件> --json` | 源文残留例外规则 JSON | 只在确需保留源语言片段时使用 | 校验 location_path、allowed_terms 和 reason | `status` 为 `ok`，或空规则 warning 已确认 | 修规则文件；不要关闭全局源文残留检测 |
| `import-source-residual-rules --game <游戏标题> --input <规则文件> --json` | 已校验例外规则 | validate 已通过 | 保存为当前游戏源文保留例外规则，供 translate、import-manual-translations 和 quality-report 共用 | `status` 为 `ok` | 回到 validate 修规则，不绕过 CLI |
| `write-back --game <游戏标题> --json` | 游戏标题、已保存译文、用户许可 | `quality-report --json` 无 error | 写回游戏目录并输出摘要；默认不覆盖字体引用 | 命令返回 0 且 JSON 摘要可读 | 停止交付，按错误修质量或规则 |
| `write-back --game <游戏标题> --confirm-font-overwrite --json` | 游戏标题、已保存译文、用户明确允许覆盖字体 | `quality-report --json` 无 error，且用户单独确认字体覆盖 | 写回游戏目录，并用配置的候选字体覆盖游戏数据里的字体引用；存在 `fonts/gamefont.css` 时同步备份和更新字体族入口；原件留档用于后续对比还原 | 命令返回 0 且 JSON 摘要里 `font_copied=true` | 停止交付，先确认用户是否真的允许覆盖字体 |
| `restore-font --game <游戏标题> --json` | 游戏标题、`data_origin`、`plugins_origin.js`、`gamefont_origin.css`、候选覆盖字体名 | 用户要求还原项目覆盖过的字体引用 | 对比激活版和原件留档，只把候选覆盖字体名替回同路径原件里的实际旧字体引用，不回滚译文 | 命令返回 0 且摘要可解释 | 若提示没有候选覆盖字体名，要求用户提供 `--replacement-font-path <字体文件>`；若没有原件留档，先停止并说明无法按原件对比 |
| `cleanup-agent-workspace --workspace <工作区> --json` | 工作区目录 | `manifest.json` 存在 | 清理 CLI 生成的工作区文件 | 命令返回 0 | 缺 manifest 时手工确认后再清理 |

### 工作区 JSON 格式契约

- `placeholder-rules.json`：顶层必须是对象，格式为 `{正则表达式: 占位符模板}`。占位符模板必须生成形如 `[CUSTOM_NAME_1]` 的方括号占位符；推荐使用 `{index}`，例如 `[CUSTOM_PLUGIN_MARK_{index}]`。禁止写成 `{占位符名: 正则表达式}`，禁止把 RPG Maker 标准控制符当自定义规则硬写。
- `terminology/field-terms.json`：这是“字段译名表”。顶层固定为术语类别对象，包括 `speaker_names`、`map_display_names`、角色、职业、技能、物品、装备、敌人、状态和系统类型术语。只填写已有 key 对应的 value；不改 key，不新增字段，不写 note，不把样本文件路径写入 value。它负责精确写回地图显示名、数据库名称、系统类型，以及 MZ 标准 `101.parameters[4]` 名字框等游戏字段。MV 的 `speaker_names` 是正文说话人术语：由 CLI 从每个对话块首条非空 `401` 正文识别，只用于译名统一和正文翻译提示词命中，不会写回 `101.parameters[4]`，也不能把译名当作可写回名字框字段处理。不能确定译名时保留空字符串并在最终报告说明。
- `terminology/glossary.json`：这是“正文术语表”。顶层必须是 `{"terms": {...}}`。`terms` 的 key 是 Agent 从字段译名表和上下文中人工规范化后的原文术语，value 是标准中文译名。正文术语表只负责正文翻译提示词命中，不能写字段包装形式、定位信息或说明字段。`source == translated` 是合法术语，不能因为人名中日同形或用户希望固定不变就过滤掉。
- `plugin-rules.json`：顶层必须是数组，格式为 `[{plugin_index, plugin_name, paths}]`。`plugin_index` 必须是插件在 `plugins.json` 数组中的下标；`plugin_name` 必须与该下标插件的 `name` 完全一致；`paths` 是 JSONPath 字符串数组，必须使用括号路径语法并从 `$['parameters']` 开始，例如 `$['parameters']['message']` 或 `$['parameters']['items'][*]['name']`；禁止使用 `$.xxx` 点号路径。没有可导入插件文本规则时使用空数组 `[]`。
- `event-command-rules.json`：顶层必须是对象，格式为 `{指令编码字符串: [{match, paths}]}`。`match` 是参数索引字符串到期望字符串值的对象；`paths` 是 `$['parameters']...` 路径数组；路径必须命中字符串叶子。没有过滤条件时 `match` 使用 `{}`。规则只依据 `event-commands.json` 里可见的数组位置和字符串叶子填写；示例里的插件命令结构只是常见导出形态，不能据此跳过当前文件检查。
- `note-tag-rules.json`：顶层必须是对象，格式为 `{data文件名或文件模式: [note标签名, ...]}`。合法示例：`{"<data文件名>.json": ["<玩家可见说明标签>"], "<地图文件模式>": ["<玩家可见名牌标签>"]}`。只写候选里真实存在的精确标签名，不支持标签正则；文件名可用 `<地图文件模式>` 这类模式覆盖同类地图文件；空结果使用 `{}`，但必须说明已检查候选。禁止选择脚本、公式、资源名、ID、布尔/枚举、数值列表、资源引用、内部关联字段和纯系统标签。
- `pending-translations.json`：这是“还没成功保存译文的文本表”。顶层是 `{location_path: 条目对象}`；`location_path` 只是导入时绑定条目的键，必须原样保留，不解释、不改写，也不要从它推断文件结构。导入前只填写 `translation_lines` 字符串数组，意思是“中文译文行”；其他导出字段只读原样保留，禁止新增字段，禁止保留程序占位符。`long_text` 是多行对话，可以按自然语义填写，导入命令会按当前 `[text_rules]` 行宽配置自动拆短；若无法安全拆分，后续质量检查报告会提示“某一行太长，游戏窗口放不下”，并禁止写进游戏文件。
- `quality-fix-template.json`：这是“检查没通过译文的修复表”，由 `export-quality-fix-template` 生成，顶层同样是 `{location_path: 条目对象}`，定位键仍然只需原样保留。只改 `translation_lines`，也就是中文译文行；已保存但仍有问题的文本会预填当前译文，模型翻过但检查没通过的文本会优先预填模型临时译文，没有临时译文时为空数组。导出命令会尽量把预填译文里的内置游戏控制符占位符或自定义占位符还原成 `original_lines` 里的游戏原始控制符；`manual_fill_note` 是填写提示，`text_for_model_lines` 只供对照，不能复制到 `translation_lines`。
- `reset-translations.json`：顶层必须是 `{"location_paths": ["<定位路径>"]}`。只用于显式重置坏译文；数组不能为空，路径必须来自当前提取范围，禁止用空 `translation_lines` 当重置信号。完整重译必须先取得用户明确选择；完整重译不要手工导出全集路径，直接使用 `reset-translations --game <游戏标题> --all --json`。
- `source-residual-rules.json`：这是“允许保留源文的例外表”。顶层是 `{location_path: {allowed_terms, reason}}`，定位键必须来自导出文件或质量检查报告，不允许自造。`allowed_terms` 是允许原样保留的源语言片段字符串数组；英文游戏默认允许少量 UI 缩写，但专名必须通过术语表或本规则显式放行；`reason` 必须说明原因，例如 `credits`、`staff_name`、`proper_noun`、`brand_name`。只在确认该片段确实不应翻译时使用；禁止用它掩盖整句漏翻，禁止在 `pending-translations.json` 内新增例外字段。
- 插件规则允许空数组 `[]` 表示确认无可导入插件文本；其他规则 JSON 允许空对象 `{}` 表示确认无可导入内容。空结果必须在最终回复说明理由。

### 控制符字符级保留

- `original_lines`、`text_for_model_lines` 和待填 `translation_lines` 中凡是出现 `\` 开头的 RPG Maker 控制片段、内置游戏控制符占位符或自定义占位符，都必须当成不可翻译标记。不解释这些标记的含义，不改写编号、括号和边界；填写译文表时需要保留控制片段的可见效果，并以导入命令校验为准。
- 填写 `translation_lines` 时只能使用 `original_lines` 里的游戏原始控制符，禁止把 `text_for_model_lines` 中的内置游戏控制符占位符或自定义占位符复制进去。如果导出的修复表里仍残留程序占位符，先对照 `original_lines` 改回原始反斜杠控制片段，再导入。
- 禁止把看起来“不标准”的控制片段自动修成标准格式。例如原文是 `\F3[66」「` 时，译文也必须保留 `\F3[66」「`；禁止改成 `\F3[66]「`，也禁止改成 `\F3[60」「`。
- 禁止为了通过校验改动控制符编号、括号、反斜杠、日文右引号 `」` 或紧邻的控制片段边界。看不懂的控制片段不是正文，不参与翻译。
- 如果 CLI 报 `疑似控制符不一致`、`placeholder_risk` 或 `CUSTOM_UNEXPECTED`，先逐条比较 `original_lines` 与 `translation_lines` 的控制片段，修正手动填写译文表后重跑导入或质量报告；禁止绕过 CLI 手改项目数据，禁止把失败条目清空。

## 8. 游戏控制符规则

- 占位符规则由主代理亲自处理，不派发给子代理。
- 必须先完成术语表主代理合并审查与导入，再完成插件规则、事件指令规则和 Note 标签规则的二次审查、校验与导入，最后才收束占位符规则。
- 再用 `build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json` 基于当前会进入正文翻译的完整文本集合生成草稿。
- 再用 `validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json` 校验。
- 再用 `scan-placeholder-candidates --game <游戏标题> --input <工作区>/placeholder-rules.json --json` 做最终覆盖扫描；`summary.uncovered_count` 不等于 0 时必须修规则，不能导入或翻译。
- 校验和覆盖扫描都通过后：`import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json`。
- 占位符模板必须稳定、唯一、便于排障，例如 `[CUSTOM_PLUGIN_FACE_PORTRAIT_{index}]`；不要用 `[X_{index}]` 这种过短且无法区分来源的名字。
- 角色名牌、语音触发标记和自动替换触发标记如果进入正文翻译，必须优先作为“必须原样保留的游戏控制符”处理。例如形如 `◆<角色名>ｔ` 的发声/名牌触发文本，通常不能当普通角色名翻译；必须先写入精确占位符规则并通过 `validate-placeholder-rules` 与 `scan-placeholder-candidates`，再启动会消耗模型额度的正文翻译。
- 如果正文翻译后才发现这类触发标记漏保护，只允许先处理还没成功保存译文的文本，或用 `reset-translations --input <规则文件>` 精确重置受影响文本。是否完整重译必须交给用户决定；主代理不得擅自扩大到全量重置。
- 完整标准 RPG Maker 控制符如果被报告为未覆盖，先停下报告工具异常，不要硬凑规则；如果只是裸 `\N` 这类缺参数片段，先按 `references/rpg-maker-mv-mz-world-knowledge.md` 判断它是否属于不完整控制符、插件自定义协议或源文本异常。
- `\N` 类规则必须非常谨慎；禁止使用会匹配裸 `\n`、`\r`、`\t` 的宽规则，例如 `(?i)\\N\d*`。如果确实是自定义数字控制符，优先写成要求至少一个数字的精确规则，例如 `(?i)\\N\d+`。
- 小写 `\n` 是游戏文本中的字面量换行，已由项目内置规则保护；处理裸大写 `\N` 插件标记时不得使用 `(?i)` 忽略大小写，避免把换行误当成角色名牌或插件标记。
- 裸无参数插件控制符可能直接贴着正文。若真实控制符是 `\FX`，原文形如 `\FXStop this!!!`，工具不会自动猜边界；必须查插件源码或规则说明后，手写只保护 `\FX` 本身的规则，例如 `{"\\\\FX": "[CUSTOM_PLUGIN_FX_MARKER_{index}]"}`。不能写成 `\\FXStop` 或吞掉后面的英文正文。`validate-placeholder-rules` 的预览里，模型可见文本必须保留 `Stop this!!!`。
- 如果报告出现非 ASCII 右引号、全角括号或未闭合控制片段 warning，先用 Unicode code point 确认边界字符，再写精确规则；不要把 `\F3[66」「` 猜成 `\F3[66]「`。
- 正确示例：输入候选 `{"marker": "\\X[face_a]", "covered": false}`，可写 `{"(?i)\\\\X\\[[^\\]\\r\\n]+\\]": "[CUSTOM_PLUGIN_X_MARK_{index}]"}`。
- 错误示例：`{"CUSTOM_PLUGIN_X": "(?i)\\\\X\\[[^\\]\\r\\n]+\\]"}` 把键和值写反；`{"(?i)\\\\N\\d*": "[CUSTOM_PLUGIN_N_{index}]"}` 会误匹配裸转义。

## 9. 术语表与外部规则分析

字段译名表、正文术语表和三类外部规则在翻译前都必须导出、分析、确认、验收。强制的是“确认”，不是强制产出非空规则。

### 字段译名表与正文术语表

- 输入：`terminology/field-terms.json`、`terminology/glossary.json`、`terminology/contexts/speakers/*.json` 和 `terminology/contexts/database_terms.json`。
- 字段译名表负责写回地图显示名、数据库名称、系统类型，以及 MZ 标准 `101.parameters[4]` 名字框等游戏字段；正文术语表负责正文翻译提示词命中。两者不能互相替代。MV 的 `speaker_names` 虽然放在字段译名表里，但只表示正文说话人术语，不表示可写回名字框字段。
- 术语表翻译必须由主代理亲自把关；子代理只能提供分字段候选译名，不能直接把结果写入最终 `terminology/field-terms.json` 或 `terminology/glossary.json`。
- 主代理先按术语字段拆分任务，推荐使用 `terminology/subtasks/sources/*.json` 作为只读输入，要求子代理只写 `terminology/subtasks/candidates/*.json`。
- 主代理必须等待全部术语候选子代理交卷，逐项审查信达雅、中文自然度、源文语义、专名统一和跨类别一致性。
- 主代理必须亲自修改候选结果，合并到 `terminology/field-terms.json`，并同步维护 `terminology/glossary.json`。字段译名表只填写 value，保持 key 不变，不新增字段，不写 note；正文术语表只写 `terms`。
- `terminology/field-terms.json` 的 value 是最终写进游戏字段的完整文本；如果原字段里的 `/c`、引号、`◆...ｔ` 等符号需要在游戏字段中保留，必须在字段译名表 value 里保留或按目标译名重组，不能指望正文术语表补回来。
- 主代理必须把 `/c<角色名>`、`"<角色名>"`、`◆<角色名>ｔ` 等字段形式由 Agent 人工判断后规范化：真正原文术语写进正文术语表 `terms`，字段包装形式不得写入正文术语表。
- 主代理必须做全量检查：空译名、源文残留、机械音译残渣、同一原文跨类别冲突和关键术语口径。
- 角色名、地图名、技能名、物品名、装备名、敌人名、状态名和系统类型术语要统一。
- 原文确实没有某类术语时，对应类别保持空对象；存在术语但无法确定译名时，主代理必须先根据上下文处理，不得保存空译名。
- 导入：`import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json`。

### 插件依赖文本型字符串的兼容策略

- 项目层不阻止地图显示名、数据库名称、系统类型，以及 MZ 标准名字框等字段汉化；这些字段按字段译名表正常写回。
- 第二轮分析插件规则、事件指令规则和 Note 标签规则时，Agent 必须主动检查插件或脚本是否把名字框、地图显示名、数据库名称、系统类型或其他文本型字符串当作功能触发键使用。MV 说话人字段通常要从插件或文本规则里确认，不能默认补写 `101.parameters[4]`；CLI 默认识别出的 MV `speaker_names` 只服务术语统一和正文翻译提示词命中。
- 如果个别游戏插件依赖原始字符串触发语音、立绘、状态列表或其他功能，Agent 应优先在当前游戏目录新增或修改插件，建立“中文显示值 -> 原始触发值”的兼容映射。
- 兼容补丁只属于当前游戏的临时处理，必须放在游戏目录内并说明影响范围；不得把例外游戏逻辑写进 A.T.T MZ 项目核心。
- 做兼容补丁前先说明发生了什么、影响什么、下一步怎么处理；补丁后必须运行相关游戏目录检查或最小可验证流程，确认汉化字段仍能显示，插件触发也能按原始值工作。

### 插件规则

- 输入：`plugins.json`。
- 输出：`plugin-rules.json`，数组格式，每项包含 `plugin_index`、`plugin_name` 和 `paths`。
- 只选玩家可见文本，排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识。
- 插件为空或插件没有玩家可见文本时，允许 `[]`，但必须先确认。
- 校验：`validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json`。
- 导入：`import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json`。

### 事件指令规则

- 输入：`event-commands.json`。
- 输出：`event-command-rules.json`，对象格式，key 是事件指令编码字符串，value 是规则数组。
- 可见文本存在才写规则；所有编码数组为空或参数没有可见文本时，允许 `{}`，但必须先确认。
- 校验：`validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json`。
- 导入：`import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json`。

### Note 标签规则

- 输入：`note-tag-candidates.json`。
- 输出：`note-tag-rules.json`，对象格式，key 是 data 文件名或文件模式，value 是 Note 标签名数组。
- 只选择 `note` 字段里由插件消费且玩家可见的文本，例如物品说明、技能说明、任务说明、地图事件名牌、提示语等。必须根据 `sample_values` 判断，不能把某个标签名当成所有游戏通用答案。
- 排除机器协议：脚本、公式、资源名、ID、布尔/枚举、数值列表、资源引用、内部关联字段、开关名、坐标、文件路径等标签。只要样例值主要服务程序判断而不是给玩家阅读，就不要选。
- Note 标签为空或没有玩家可见文本时，允许 `{}`，但必须说明已检查候选。
- 校验：`validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json`。
- 导入：`import-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json`。

## 10. 子代理规则

### 子代理任务处理方式确认

准备工作区完成后，主代理必须先向用户确认子代理任务处理方式，再启动第一轮术语候选。说明时必须让用户知道接下来会处理术语候选、插件规则、事件指令规则和 Note 标签规则；这些任务主要负责译名统一和玩家可见文本规则判断，不直接写进游戏文件。多项候选分析会消耗较多上下文和模型额度，额度有限时建议使用外部协作任务包。

用户只能选择以下三种方式：

- 当前会话完成：用户希望当前会话处理候选分析；如果平台支持子代理，必须启用子代理并行处理候选分析；不支持子代理时，才允许串行处理。子代理轮次固定为两轮，不能把所有任务混在一轮。
- 外部协作任务包：主代理把原本派发给子代理的任务整理成可复制给用户、网页模型或其他工具处理的任务包；任务包只替代子代理执行方式，不替代主代理审核职责。用户返回的内容一律视为候选答案。
- 混合处理：用户指定部分任务由当前会话完成，部分任务整理成外部协作任务包；主代理仍按两轮顺序收束结果。

用户未明确选择前，不要默认消耗大量子代理额度。外部协作任务包的完整写法可参考 `references/subtask-package-mode.md`；任务包必须拆成多个独立文件夹，每个文件夹都包含建议用户提示词、结构化上下文数据、答案模板、清单、禁止事项、空结果规则和主代理验收步骤。

### 外部协作任务包

外部协作任务包只允许覆盖五个术语候选分组、插件规则、事件指令规则和 Note 标签规则。一个任务包文件夹只对应一个任务，必须复制完成该任务所需的结构化数据到包内 `context/` 目录；任务包完成者只按任务包文件夹内文件和用户明确提供的信息工作，不读取项目源码、数据库、程序内部对象或原机器上的 `<工作区>` 路径。

不得导出为普通任务包的内容：

- 占位符规则最终生成、覆盖扫描和导入不得导出为普通任务包。
- 最终术语表合并与正文术语表维护不得导出为普通任务包。
- 正文翻译、重置译文、写进游戏文件、字体覆盖不得导出为普通任务包。

任务包只能要求在包内填写 `answer.json` 或返回 `answer.json` 内容，不能要求任务包完成者导入数据库、修改最终术语表、执行翻译或写进游戏文件。任务包内只能使用相对路径，保证整个文件夹可压缩、远程分发并在其他机器上完成。任务包为空结果时必须返回允许的空结构，并说明已检查的输入范围和空结果理由。

### 用户返回答案验收

任务包返回内容必须由主代理验收后才能继续。主代理必须先检查 JSON 结构和唯一写入边界，再对照输入文件抽查关键条目，防止编造路径、误选资源、脚本、公式或内部字段。

术语候选必须由主代理统一风格、去空值、查源文残留和译名冲突，再合并到 `terminology/field-terms.json` 并维护 `terminology/glossary.json`。规则类结果必须运行对应 `validate-* --json`，通过后才运行对应 `import-* --json`。大面积错误时要求重做或改由主代理完成，不能直接导入。

### 第一轮：术语候选

术语表翻译必须由主代理亲自把关。第一轮子代理只产出候选译名，不得直接写最终 `terminology/field-terms.json` 或 `terminology/glossary.json`，不得保存为当前游戏术语表。

推荐按 `prepare-agent-workspace` 生成的拆分文件派发候选子代理：

- `terminology/subtasks/sources/speaker_and_actor_terms.json` -> 只写 `terminology/subtasks/candidates/speaker_and_actor_terms.json`。
- `terminology/subtasks/sources/map_and_system_terms.json` -> 只写 `terminology/subtasks/candidates/map_and_system_terms.json`。
- `terminology/subtasks/sources/skill_and_state_terms.json` -> 只写 `terminology/subtasks/candidates/skill_and_state_terms.json`。
- `terminology/subtasks/sources/item_terms.json` -> 只写 `terminology/subtasks/candidates/item_terms.json`。
- `terminology/subtasks/sources/equipment_terms.json` -> 只写 `terminology/subtasks/candidates/equipment_terms.json`。

第一轮主代理职责：

- 主代理必须等待全部术语候选子代理完成。
- 主代理必须逐个读取候选文件，不允许只看子代理完成报告。
- 主代理必须严审信达雅、源文语义、中文自然度、专名统一、跨类别一致性和游戏 UI 语感。
- 主代理必须亲自修改候选译名并合并到 `terminology/field-terms.json`，同时维护 `terminology/glossary.json`。
- 主代理必须全量检查空译名、源文残留、机械音译残渣、同一原文跨类别冲突和关键术语口径。
- 只有主代理审查通过后，才能运行 `import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json`。
- 任一术语候选质量明显不合格，主代理必须退回对应候选子代理重做或亲自重译对应字段；禁止把坏候选修修补补后保存为当前游戏术语表。

术语候选子代理任务单：

```text
输入：读取 <工作区>/terminology/subtasks/sources/<术语分组>.json、<工作区>/terminology/contexts/speakers/*.json 和 <工作区>/terminology/contexts/database_terms.json。
逻辑：按源文含义翻译当前分组的全部术语；专名统一，称号、技能、物品、线索句和系统词要译成自然简体中文；禁止机械转写。
输出：只写 <工作区>/terminology/subtasks/candidates/<术语分组>.json，保持类别和 key 不变，只填写 value。
质量要求：不留空值，不残留平假名/片假名，不出现机械音译残渣；不确定项也必须给出当前最合理译名，并在报告说明风险。
完成报告：说明总条数、空值数、读取的上下文、关键统一译名、疑难项和自检结果。
```

术语候选示例：

```text
输入片段: {"item_names": {"【<组织名>は<对象>を一掃している】": "", "<药品原名>": ""}}
正确候选: {"item_names": {"【<组织名>は<对象>を一掃している】": "【<组织译名>正在清剿<对象译名>】", "<药品原名>": "<自然中文药品名>"}}
错误候选: {"item_names": {"【<组织名>は<对象>を一掃している】": "【<组织音译><对象音译>希特伊鲁】"}, "note": "已处理"}
```

主代理合并字段译名表时必须保留 `terminology/field-terms.json` 的完整顶层类别和 key 集合；候选文件只能作为输入，不能代替最终导入文件。正文术语表必须只保留 `terms` 顶层对象。

### 第二轮：三类外部规则

术语表已通过 CLI 保存后，主代理才能开启第二轮子代理，并行处理三类互不写同一文件的外部规则：

- `plugin-rules` 子代理：读取 `plugins.json`，只写 `plugin-rules.json`。
- `event-command-rules` 子代理：读取 `event-commands.json`，只写 `event-command-rules.json`。
- `note-tag-rules` 子代理：读取 `note-tag-candidates.json` 和 `note-tag-rules.json`，只写 `note-tag-rules.json`。

第二轮子代理任务契约：

| 子代理 | 输入 | 逻辑 | 输出 |
| --- | --- | --- | --- |
| `plugin-rules` | `plugins.json` | 只选择插件参数里的玩家可见文本；排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识 | 只写 `plugin-rules.json`，格式为 `[{plugin_index, plugin_name, paths}]`；无可见文本时输出 `[]` |
| `event-command-rules` | `event-commands.json` | 按事件指令编码判断参数中的玩家可见文本；不为资源、脚本、数字、布尔值和内部标识写规则 | 只写 `event-command-rules.json`，格式按事件指令编码分组；无可见文本时输出 `{}` |
| `note-tag-rules` | `note-tag-candidates.json`、`note-tag-rules.json` 草稿 | 判断标准 `data/*.json` 的 `note` 标签值中哪些是玩家可见文本；排除机器协议标签 | 只写 `note-tag-rules.json`，格式为 `{data文件名或文件模式: [note标签名, ...]}`；无可见标签时输出 `{}` |

第二轮主代理职责：

- 主代理必须等待三类规则子代理全部完成。
- 主代理必须读取每个子代理结果，复核文件结构、可见文本判断和空结果理由。
- 主代理必须运行插件规则、事件指令规则和 Note 标签规则对应的 `validate-* --json` 命令。
- 主代理必须在校验通过后执行对应 `import-* --json` 命令，并读取 `status` 和 `summary`。
- 三类外部规则全部导入后，主代理才能重新运行 `build-placeholder-rules`，亲自审查、校验、覆盖扫描并导入占位符规则。
- 任一术语候选或规则子代理未完成、失败或校验未通过，或占位符规则未最终导入，不启动翻译。
- 不允许多个子代理同时修改同一个文件。

### 子代理上下文包

主代理派发每个子代理时，必须提供最小但完整的上下文包，不要让子代理靠猜，也不要把大 JSON 正文塞进子代理 prompt。

每个子代理 prompt 必须包含：

- `<发行版目录>`、`<工作区>`、`<游戏标题>`。
- 子代理角色名，例如 `terminology-candidate:<术语分组>`、`plugin-rules`、`event-command-rules`、`note-tag-rules`。
- 当前轮次：第一轮术语候选或第二轮外部规则。
- 输入文件清单和输出文件路径。
- 只读范围和唯一可写文件；只允许写自己负责的输出文件。
- 当前任务的输出 JSON 格式、禁止新增字段规则、空结果允许条件。
- 需要重点排除的内容，例如资源路径、脚本、数字、布尔值、内部标识和只服务程序定位或排障的字段。
- 完成后必须报告：改动文件、是否为空结果、空结果理由、未解决风险、建议主代理运行的校验命令。

推荐子代理 prompt 模板：

```text
<角色名> 子代理任务，工作目录是 <发行版目录>。
本次属于第 <轮次> 轮，只处理 <工作区> 中的指定文件。
输入：<输入文件列表>。
逻辑：<当前任务的筛选、翻译、统一、排除和空结果判断规则>。
输出：<唯一可写文件>，格式为 <目标 JSON 格式>。
排除：不要选择资源路径、脚本、数字、布尔值、内部标识和只服务程序定位或排障的字段。
如果确认没有可写内容，输出允许的空结构，并在最终回复说明空结果理由。
完成后只汇报改动文件、空结果理由、未解决风险和建议校验命令。
```

### 三类规则任务单模板

派发三类外部规则子代理时，`plugin-rules` 任务优先复制 `docs/plugin-rules-agent-prompt.md` 的任务契约，`event-command-rules` 任务优先复制 `docs/event-command-rules-agent-prompt.md` 的任务契约；如果内联任务单与参考文档不一致，以参考文档的输入、逻辑、输出和校验边界为准。

`plugin-rules` 子代理任务单：

```text
输入：读取 <工作区>/plugins.json。
逻辑：按插件逐项判断 parameters 内玩家可见文本；排除资源路径、文件名、脚本、枚举、布尔值、数字、颜色、坐标和内部标识。
输出：只写 <工作区>/plugin-rules.json，格式为 [{plugin_index, plugin_name, paths}]；plugin_index 是 plugins.json 数组下标，plugin_name 必须与该下标插件名一致，JSONPath 从 $['parameters'] 开始并使用括号路径语法。
空结果：确认插件为空或没有玩家可见文本时输出 []。
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
逻辑：判断标准 data/*.json 的 note 标签值中哪些是玩家可见文本；长段说明标签、地图事件名牌等玩家可见标签可选，机器协议标签必须排除。
输出：只写 <工作区>/note-tag-rules.json，格式为 {data文件名或文件模式: [note标签名, ...]}。
空结果：确认候选中没有玩家可见 Note 标签文本时输出 {}。
完成报告：说明检查过的文件、选中标签、排除标签及理由、空结果理由、建议运行 validate-note-tag-rules --json。
```

### 子代理最佳工作示例

主代理派发子代理时，不能只概括“分析规则”。必须复制对应任务单和本节示例，填入 `<发行版目录>`、`<工作区>`、`<游戏标题>`、输入文件、唯一可写文件和校验命令。

`plugin-rules` 示例：

```text
输入片段: {"name": "DemoPlugin", "parameters": {"message": "按钮文本", "entries": [{"label": "菜单项"}], "file": "img/picture.png", "count": "12"}}
正确输出: {"DemoPlugin": ["$['parameters']['message']", "$['parameters']['entries'][*]['label']"]}
错误输出: {"DemoPlugin": ["$.parameters.message", "$['parameters']['file']", "$['parameters']['count']"]}
```

判断逻辑：只选玩家可见文本；JSONPath 使用括号语法并从 `$['parameters']` 开始。资源路径、脚本、数字、颜色、布尔值和内部标识都排除。校验命令：`validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json`。

`event-command-rules` 示例：

```text
MZ 输入片段: {"code": 357, "parameters": ["DemoPlugin", "ShowMessage", "显示名文本", {"messageText": "提示文本", "file": "Actor.png"}]}
正确输出: {"357": [{"match": {"0": "DemoPlugin", "1": "ShowMessage"}, "paths": ["$['parameters'][3]['messageText']"]}]}
顶层字符串输出: {"357": [{"match": {"0": "DemoPlugin", "1": "ShowMessage"}, "paths": ["$['parameters'][2]"]}]}
MV 输入片段: {"code": 356, "parameters": ["ShowMessage text:提示文本 file:Actor.png"]}
MV 字符串输出: {"356": [{"match": {}, "paths": ["$['parameters'][0]"]}]}
错误输出: {"357": [{"match": {"plugin": "DemoPlugin"}, "paths": ["$['parameters']['messageText']"]}]}
```

判断逻辑：MZ 默认导出 `357`，常见结构是 `code=357 parameters = [插件名, 指令名, 显示名, 参数对象]`；插件命令参数对象通常从 `$['parameters'][3]` 取可见文本，顶层字符串叶子才用 `$['parameters'][2]`。MV 默认导出 `356`，常见结构是单个插件命令字符串，必须按 `event-commands.json` 里的实际字符串判断，不能照搬 MZ 参数位置。`match` 的键必须是参数索引字符串。校验命令：`validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json`。

`note-tag-rules` 示例：

```text
输入片段: {"file_name": "<物品数据文件>.json", "tag_name": "<玩家可见说明标签>", "sample_values": ["药品的详细说明文本"]}
输入片段: {"file_name": "<地图文件模式>", "tag_name": "<玩家可见名牌标签>", "sample_values": ["向导"]}
输入片段: {"file_name": "<装备数据文件>.json", "tag_name": "<机器协议标签>", "sample_values": ["1,2,3"]}
输入片段: {"file_name": "<技能数据文件>.json", "tag_name": "<玩家可见补充说明标签>", "sample_values": ["技能追加说明文本"]}
正确输出: {"<物品数据文件>.json": ["<玩家可见说明标签>"], "<技能数据文件>.json": ["<玩家可见补充说明标签>"], "<地图文件模式>": ["<玩家可见名牌标签>"]}
错误输出: {"<物品数据文件>.json": ["<机器协议标签>"], "<装备数据文件>.json": ["<内部状态编号标签>"]}
错误输出: {"note": {"<物品数据文件>.json": ["<玩家可见说明标签>"]}}
```

判断逻辑：长段自然语言说明、地图事件名牌、任务提示、状态说明等通常是玩家可见文本，可以选择；数字编号、脚本公式、资源路径、开关枚举、装备状态编号、连锁技能编号等是机器协议或系统标签，必须排除。输出只能是 `{data文件名或文件模式: [note标签名, ...]}`，不写标签正则、不写 reason、不改游戏 `data/*.json`。同一个标签名在不同游戏里含义可能不同，必须以当前候选的 `sample_values` 为准。校验命令：`validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json`。

## 11. 翻译失败处理

- `translate` 返回 0 只表示本轮命令正常结束，不代表所有文本都已经成功保存译文。
- 少量“还没成功保存译文的文本”和少量“模型翻了但项目检查没通过的译文”，可以继续跑同一个 `translate` 命令。连续多轮数量不明显下降时，停止盲目重跑。
- 小批量后如果有模型接口失败、译文检查没通过、游戏控制符可能被改坏，先排查，不继续全量翻译。
- 要查看最新一轮“模型翻了但项目检查没通过”的全部错误明细，先运行质量检查报告：`quality-report --game <游戏标题> --json`。
- 如果质量检查报告里有可修复明细，优先运行 `export-quality-fix-template --game <游戏标题> --output <工作区>/quality-fix-template.json --json`。这个命令会导出全部当前错误，生成可填写的修复表。
- 修复表里只改 `translation_lines`，也就是中文译文行；不要改文本内部位置、原文、文本类型、角色名等字段。改完后运行 `import-manual-translations --game <游戏标题> --input <工作区>/quality-fix-template.json --json`，让项目检查并保存。
- 如果质量检查报告只提示还有文本没成功保存译文，但没有可修复明细，使用 `export-untranslated-translations --game <游戏标题> --output <工作区>/pending-translations.json --json` 一次导出全部没成功保存的文本。
- 如果只想抽样或分批查看，使用 `export-pending-translations --game <游戏标题> --limit N --output <工作区>/pending-translations.json --json`；不传 `--limit` 时也会导出全部没成功保存的文本。
- 手动填写译文表时，只填写 `translation_lines`，也就是中文译文行；填写完成后使用 `import-manual-translations --game <游戏标题> --input <文件> --json` 交回项目检查并保存。
- 多行对话不要求手工按固定宽度切行；导入命令会按当前行宽设置自动拆短，再保存到项目数据库。
- 如果质量检查报告提示“某一行太长，游戏窗口放不下”，必须修短到质量检查报告无错误后，才能写进游戏文件。
- 如果质量检查报告提示“中文译文里还有疑似没翻的源语言文本”，先判断是不是漏翻；如果是漏翻，修中文译文行后导入。只有致谢名单、Staff 名、作品名、品牌名、游戏内专有名词等确实无需翻译的片段，才写入 `source-residual-rules.json` 并走 validate/import 例外流程。
- 如果模型明确认为某个源语言片段保留原文比硬翻更准确，可以通过源文保留例外表放行；必须限制到具体文本内部位置和具体允许保留的词，并填写原因。
- 只有确认为坏译文需要重新交给 `translate` 时，才使用 `reset-translations --game <游戏标题> --input <工作区>/reset-translations.json --json`。该文件只接受 `{"location_paths": [...]}`，非法路径会整体停止，不能用空中文译文行伪造重置。
- 用户明确要求完整重译已完成游戏时，使用 `reset-translations --game <游戏标题> --all --json`，不要手工拼当前提取范围全集路径。
- 用户试玩反馈不是翻译失败本身。先按反馈类型定位；只有确认是坏译文、漏规则或漏文本来源时，才进入对应修复流程。

## 12. 写进游戏文件前的检查

写回前必须满足：

- 用户明确允许写回。
- `quality-report --json` 没有 `error` 错误。
- `quality-report --json` 有错误时禁止写进游戏文件。
- 占位符规则已覆盖当前游戏候选。
- 术语表、插件规则、事件指令规则已导入，或已确认游戏本身没有对应内容。
- Note 标签规则已导入，或已确认游戏本身没有玩家可见 Note 标签内容。
- 目标游戏目录可写。

不满足就停下报告，不要写回。

## 13. 检查失败后的处理

- `validate-* --json` 返回 `error` 时，先把错误映射回对应工作区 JSON，修文件后重跑同一个 validate 命令。
- `placeholder_rules_invalid`：优先检查是否把 `{正则表达式: 占位符模板}` 写反、模板是否能生成 `[CUSTOM_NAME_1]`、正则是否能编译。
- `plugin_rules_invalid`：优先检查顶层是否是数组、`plugin_index` 是否是 `plugins.json` 数组下标、`plugin_name` 是否与该下标插件名一致、JSONPath 是否从 `$['parameters']` 开始、是否误用了 `$.xxx` 点号路径、路径是否命中字符串叶子。
- `event_command_rules_invalid`：优先检查指令编码是否是字符串数字、`match` 键是否是参数索引、`paths` 是否从 `$['parameters']` 开始并命中字符串叶子。
- `note_tag_rules_invalid`：优先检查顶层是否是 `{data文件名或文件模式: [note标签名, ...]}`，文件名或文件模式是否能命中当前游戏的 data JSON，标签名是否精确命中 `<标签:值>`，是否误选了脚本、公式、资源名、ID、枚举、装备状态编号、连锁技能编号等机器协议。
- `manual_translation_invalid`：优先检查 `translation_lines` 是否为字符串数组、行数是否匹配条目类型、是否残留程序占位符或源文残留。
- `source_residual_rules_invalid`：优先检查顶层 key 是否是当前还没成功保存译文的文本或已保存文本的 `location_path`，`allowed_terms` 是否为非空字符串数组且片段出现在当前条目原文或译文中，`reason` 是否非空。
- `quality-report --json` 返回 `placeholder_risk_items` 或 `overwide_line_items` 时，按 `location_path` 整理手动填写译文表，用 `import-manual-translations --json` 导入后重跑质量报告。
- `quality-report --json` 返回 `source_residual_items` 时，先修漏翻；确认为可保留源文时，再写 `source-residual-rules.json`，运行 `validate-source-residual-rules --json` 和 `import-source-residual-rules --json`。
- `quality-report --json` 返回可修复明细时，推荐先用 `export-quality-fix-template --json` 生成可填写的修复表；只有该命令输出为空或 CLI 行为异常时，才手工整理同格式 JSON。
- 需要删除坏译文、让文本重新交给模型翻译时，必须用 `reset-translations` 的 `location_paths` 显式文件；需要完整重译当前提取范围时，必须用 `reset-translations --all`；禁止把 `translation_lines` 写成空数组来绕过导入校验。
- 禁止因为校验失败而绕过 CLI 手改项目数据、跳过 validate、跳过 `validate-agent-workspace` 或继续写回。
- 只有错误信息无法对应到工作区 JSON，或同一合法文件反复触发无法解释的 CLI 错误时，才停止并报告工具问题。

## 14. 禁止做法

- 在 `<发行版目录>` 写临时脚本或中间文件。
- 用临时脚本直接 `import app...` 操作数据库或游戏数据。
- 把没看懂结构当成“没有内容”。
- 为了让规则非空而编造插件规则、事件指令规则或术语。
- 子代理未完成就导入半成品或启动翻译。
- 看到 `translate` 有少量失败项就当作程序崩溃。
- `quality-report` 有 `error` 错误仍写回。
- 绕过 `import-manual-translations` 手改项目数据。
- 直接改游戏 `data/*.json` 的 `note` 字段，绕过 `note-tag-rules`、已保存译文记录和写进游戏文件前的检查。
- 用空 `translation_lines` 当作重置译文手段，绕过 `reset-translations`。
- 用源文残留例外规则掩盖整句漏翻，或用全局开关关闭源文残留检测。
- 把模型密钥写进命令、文档、日志摘要或临时文件。
- 把第一版可试玩汉化结果包装成“百分百完成”，或在用户尚未试玩反馈前关闭任务。
- 收到用户反馈后不定位、不复查，直接全量重译或直接手改游戏文件。
