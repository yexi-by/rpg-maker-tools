"""Skill 执行协议回归测试。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agents_requires_external_judgment_for_game_private_semantics() -> None:
    """项目规范必须禁止程序把启发式候选当成游戏私有语义结论。"""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    required_phrases = [
        "程序只能内置引擎公开协议和结构性校验",
        "不能把启发式猜测当作游戏私有语义的最终判断",
        "必须来自用户明确输入、当前游戏文件的人工或外部代理分析、已导入规则、术语表或源文残留例外规则",
        "候选只能用于提示和校验，不能自动升级为已确认规则",
        "必须输出可理解的告警或错误",
        "禁止靠程序自身猜测继续翻译、导入规则或消耗模型额度",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_att_mz_skill_defines_two_round_subagent_protocol() -> None:
    """Skill 必须约束两轮子代理流程和主代理术语表审核责任。"""
    text = (ROOT / "skills" / "att-mz" / "SKILL.md").read_text(encoding="utf-8")

    required_phrases = [
        "必须启用子代理并行处理",
        "才允许串行处理",
        "子代理轮次固定为两轮",
        "### 子代理任务处理方式确认",
        "主代理必须先向用户确认子代理任务处理方式",
        "当前会话完成",
        "外部协作任务包",
        "混合处理",
        "多项候选分析会消耗较多上下文和模型额度",
        "额度有限时建议使用外部协作任务包",
        "任务包只替代子代理执行方式，不替代主代理审核职责",
        "用户返回的内容一律视为候选答案",
        "`references/subtask-package-mode.md`",
        "任务包必须拆成多个独立文件夹",
        "建议用户提示词、结构化上下文数据、答案模板、清单",
        "### 外部协作任务包",
        "外部协作任务包只允许覆盖五个术语候选分组、插件规则、事件指令规则和 Note 标签规则",
        "一个任务包文件夹只对应一个任务",
        "包内 `context/` 目录",
        "不读取项目源码、数据库、程序内部对象或原机器上的 `<工作区>` 路径",
        "占位符规则最终生成、覆盖扫描和导入不得导出为普通任务包",
        "最终术语表合并与正文术语表维护不得导出为普通任务包",
        "正文翻译、重置译文、写进游戏文件、字体覆盖不得导出为普通任务包",
        "只能要求在包内填写 `answer.json` 或返回 `answer.json` 内容",
        "任务包内只能使用相对路径",
        "可压缩、远程分发并在其他机器上完成",
        "### 用户返回答案验收",
        "先检查 JSON 结构和唯一写入边界",
        "防止编造路径、误选资源、脚本、公式或内部字段",
        "术语候选必须由主代理统一风格、去空值、查源文残留和译名冲突",
        "规则类结果必须运行对应 `validate-* --json`",
        "通过后才运行对应 `import-* --json`",
        "大面积错误时要求重做或改由主代理完成，不能直接导入",
        "### 第一轮：术语候选",
        "术语表翻译必须由主代理亲自把关",
        "`terminology/subtasks/sources/speaker_and_actor_terms.json`",
        "`terminology/subtasks/candidates/item_terms.json`",
        "主代理必须等待全部术语候选子代理完成",
        "主代理必须严审信达雅、源文语义、中文自然度、专名统一、跨类别一致性和游戏 UI 语感",
        "主代理必须亲自修改候选译名并合并到 `terminology/field-terms.json`，同时维护 `terminology/glossary.json`",
        "`terminology/field-terms.json` 的 value 是最终写进游戏字段的完整文本",
        "不能指望正文术语表补回来",
        "术语候选子代理任务单",
        "### 第二轮：三类外部规则",
        "`plugin-rules` 子代理",
        "`event-command-rules` 子代理",
        "`note-tag-rules` 子代理",
        "### 编码与 Windows 终端",
        "所有工作区 JSON、临时脚本、手动填写译文表、规则文件和交付报告都必须按 UTF-8 读写",
        "禁止依赖 Windows 默认编码、ANSI、GBK 或 Shift-JIS",
        "json.dumps(..., ensure_ascii=False)",
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
        "禁止继续导入、翻译或写回乱码数据",
        "必须核验 Unicode code point 或原始字节",
        "`--json` 命令的 stdout 只读取最终 JSON",
        "stderr 持续输出无 ANSI 文本进度条",
        "不要把 stderr 进度行当成命令结果 JSON",
        "### 只按任务文件和命令结果工作",
        "`references/rpg-maker-mv-mz-world-knowledge.md`",
        "翻译任务中，只按 CLI 输出、工作区 JSON、游戏目录和用户明确提供的信息判断",
        "所有业务数据进出只走 CLI、`<工作区>` JSON、CLI 已保存到当前游戏状态的规则和游戏目录文件",
        "### 执行前需要知道什么",
        "RPG Maker MV/MZ",
        "不需要知道：源码、程序内部数据、模型提示词怎样生成、占位符怎样恢复",
        "### 输入-逻辑-输出总则",
        "每个阶段开始前，必须先明确“输入是什么、处理逻辑是什么、输出什么”",
        "### 命令 I/O 合约",
        "`doctor --no-check-llm --json`",
        "`add-game --path <游戏目录> --source-language ja --json`",
        "`add-game --path <游戏目录> --source-language en --json`",
        "注册游戏时必须显式传 `--source-language ja` 或 `--source-language en`",
        "`doctor --game <游戏标题> --no-check-llm --json`",
        "`prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json`",
        "`scan-placeholder-candidates --game <游戏标题> --input <规则文件> --json`",
        "`import-placeholder-rules --game <游戏标题> --input <规则文件> --json`",
        "`import-terminology --game <游戏标题> --input <字段译名表> --glossary-input <正文术语表> --json`",
        "`import-plugin-rules --game <游戏标题> --input <规则文件> --json`",
        "`import-event-command-rules --game <游戏标题> --input <规则文件> --json`",
        "`export-note-tag-candidates --game <游戏标题> --output <文件> --json`",
        "`validate-note-tag-rules --game <游戏标题> --input <规则文件> --json`",
        "`import-note-tag-rules --game <游戏标题> --input <规则文件> --json`",
        "`quality-report --game <游戏标题> --json`",
        "`export-quality-fix-template --game <游戏标题> --output <文件> --json`",
        "生成可填写的修复表",
        "`export-untranslated-translations --game <游戏标题> --output <文件> --json`",
        "一次导出全部还没成功保存译文的原文，生成可填写的译文表",
        "不传 `--limit` 时导出全部",
        "`validate-source-residual-rules --game <游戏标题> --input <规则文件> --json`",
        "`import-source-residual-rules --game <游戏标题> --input <规则文件> --json`",
        "日文和英文游戏都使用通用源文残留命令",
        "禁止全局关闭源文残留检测",
        "`write-back --game <游戏标题> --json`",
        "用户是否允许覆盖字体必须单独确认",
        "禁止使用 `--confirm-font-overwrite`",
        "`write-back --game <游戏标题> --confirm-font-overwrite --json`",
        "`restore-font --game <游戏标题> --json`",
        "默认不覆盖字体引用",
        "对比激活版和原件留档",
        "`--replacement-font-path <字体文件>`",
        "第一次写进游戏文件得到的是可试玩汉化结果，不是百分百完成承诺",
        "## 5. 试玩反馈迭代流程",
        "试玩反馈是正式翻译流程的一部分",
        "先向用户收集最小可定位信息",
        "每轮反馈修复都必须走“导出可填写文件或规则文件 -> 修改 -> validate/import -> quality-report -> 用户确认是否再次写进游戏文件”的闭环",
        "用户试玩反馈不是翻译失败本身",
        "把第一版可试玩汉化结果包装成“百分百完成”",
        "### 工作区 JSON 格式契约",
        "`placeholder-rules.json`：顶层必须是对象，格式为 `{正则表达式: 占位符模板}`",
        "禁止写成 `{占位符名: 正则表达式}`",
        "`terminology/field-terms.json`：这是“字段译名表”",
        "MV 的 `speaker_names` 是正文说话人术语：由 CLI 从每个对话块首条非空 `401` 正文识别",
        "MV 的 `speaker_names` 虽然放在字段译名表里，但只表示正文说话人术语，不表示可写回名字框字段",
        "`terminology/glossary.json`：这是“正文术语表”",
        "`source == translated` 是合法术语",
        "正文术语表必须只保留 `terms` 顶层对象",
        "字段包装形式不得写入正文术语表",
        "字段译名表负责写回地图显示名、数据库名称、系统类型，以及 MZ 标准 `101.parameters[4]` 名字框等游戏字段",
        "正文术语表负责正文翻译提示词命中",
        "`plugin-rules.json`：顶层必须是数组，格式为 `[{plugin_index, plugin_name, paths}]`",
        "插件规则允许空数组 `[]` 表示确认无可导入插件文本",
        "必须使用括号路径语法并从 `$['parameters']` 开始",
        "禁止使用 `$.xxx` 点号路径",
        "`event-command-rules.json`：顶层必须是对象，格式为 `{指令编码字符串: [{match, paths}]}`",
        "`note-tag-rules.json`：顶层必须是对象，格式为 `{data文件名或文件模式: [note标签名, ...]}`",
        "`{\"<data文件名>.json\": [\"<玩家可见说明标签>\"], \"<地图文件模式>\": [\"<玩家可见名牌标签>\"]}`",
        "不能把某个标签名当成所有游戏通用答案",
        "`pending-translations.json`：这是“还没成功保存译文的文本表”。顶层是 `{location_path: 条目对象}`",
        "导入前只填写 `translation_lines` 字符串数组",
        "`long_text` 是多行对话，可以按自然语义填写，导入命令会按当前 `[text_rules]` 行宽配置自动拆短",
        "`quality-fix-template.json`：这是“检查没通过译文的修复表”",
        "`manual_fill_note` 是填写提示，`text_for_model_lines` 只供对照",
        "填写 `translation_lines` 时只能使用 `original_lines` 里的游戏原始控制符",
        "`reset-translations.json`：顶层必须是 `{\"location_paths\": [\"<定位路径>\"]}`",
        "`reset-translations --game <游戏标题> --all --json`",
        "完整重译不要手工导出全集路径",
        "已导入规则回填文件",
        "禁止用空 `translation_lines` 当重置信号",
        "`source-residual-rules.json`：这是“允许保留源文的例外表”。顶层是 `{location_path: {allowed_terms, reason}}`",
        "`allowed_terms` 是允许原样保留的源语言片段字符串数组",
        "英文游戏默认允许少量 UI 缩写",
        "禁止在 `pending-translations.json` 内新增例外字段",
        "### 控制符字符级保留",
        "都必须当成不可翻译标记",
        r"原文是 `\F3[66」「` 时，译文也必须保留 `\F3[66」「`",
        r"禁止改成 `\F3[66]「`",
        r"禁止改成 `\F3[60」「`",
        "如果 CLI 报 `疑似控制符不一致`",
        "占位符规则由主代理亲自处理",
        "基于当前会进入正文翻译的完整文本集合生成草稿",
        "`summary.uncovered_count` 不等于 0 时必须修规则，不能导入或翻译",
        "正确示例：输入候选",
        "第二轮子代理任务契约",
        "格式为 `{正则表达式: 占位符模板}`",
        "格式为 `[{plugin_index, plugin_name, paths}]`",
        "主代理必须等待三类规则子代理全部完成",
        "三类外部规则全部导入后，主代理才能重新运行 `build-placeholder-rules`",
        "亲自审查、校验、覆盖扫描并导入占位符规则",
        "任一术语候选或规则子代理未完成、失败或校验未通过，或占位符规则未最终导入，不启动翻译",
        "### 插件依赖文本型字符串的兼容策略",
        "项目层不阻止地图显示名、数据库名称、系统类型，以及 MZ 标准名字框等字段汉化",
        "必须主动检查插件或脚本是否把名字框、地图显示名、数据库名称、系统类型或其他文本型字符串当作功能触发键使用",
        "MV 说话人字段通常要从插件或文本规则里确认，不能默认补写 `101.parameters[4]`",
        "建立“中文显示值 -> 原始触发值”的兼容映射",
        "不得把例外游戏逻辑写进 A.T.T MZ 项目核心",
        "### 子代理上下文包",
        "不要把大 JSON 正文塞进子代理 prompt",
        "只允许写自己负责的输出文件",
        "完成后必须报告：改动文件、是否为空结果、空结果理由、未解决风险、建议主代理运行的校验命令",
        "推荐子代理 prompt 模板",
        "### 三类规则任务单模板",
        "`docs/plugin-rules-agent-prompt.md`",
        "`docs/event-command-rules-agent-prompt.md`",
        "`plugin-rules` 子代理任务单",
        "`event-command-rules` 子代理任务单",
        "`note-tag-rules` 子代理任务单",
        "### 子代理最佳工作示例",
        "必须复制对应任务单和本节示例",
        "错误示例",
        "{\"(?i)\\\\\\\\N\\\\d*\"",
        "小写 `\\n` 是游戏文本中的字面量换行，已由项目内置规则保护",
        "处理裸大写 `\\N` 插件标记时不得使用 `(?i)` 忽略大小写",
        "输入：读取 <工作区>/terminology/subtasks/sources/<术语分组>.json、<工作区>/terminology/contexts/speakers/*.json",
        "不确定项也必须给出当前最合理译名",
        "$['parameters']['entries'][*]['label']",
        "资源路径、脚本、数字、颜色、布尔值和内部标识都排除",
        "code=357 parameters = [插件名, 指令名, 显示名, 参数对象]",
        "MV 默认导出 `356`",
        "$['parameters'][3]['messageText']",
        "$['parameters'][2]",
        "输入片段: {\"file_name\": \"<物品数据文件>.json\", \"tag_name\": \"<玩家可见说明标签>\"",
        "正确输出: {\"<物品数据文件>.json\": [\"<玩家可见说明标签>\"], \"<技能数据文件>.json\": [\"<玩家可见补充说明标签>\"], \"<地图文件模式>\": [\"<玩家可见名牌标签>\"]}",
        "同一个标签名在不同游戏里含义可能不同",
        "脚本、公式、资源名、ID、枚举、装备状态编号、连锁技能编号",
        "要查看最新一轮“模型翻了但项目检查没通过”的全部错误明细",
        "`export-quality-fix-template --game <游戏标题> --output <工作区>/quality-fix-template.json --json`",
        "`reset-translations --game <游戏标题> --input <工作区>/reset-translations.json --json`",
        "## 13. 检查失败后的处理",
        "`validate-* --json` 返回 `error` 时",
        "先把错误映射回对应工作区 JSON",
        "误用了 `$.xxx` 点号路径",
        "`note_tag_rules_invalid`",
        "`source_residual_rules_invalid`",
        "`quality-report --json` 返回 `source_residual_items`",
        "推荐先用 `export-quality-fix-template --json` 生成可填写的修复表",
        "必须用 `reset-translations` 的 `location_paths` 显式文件",
        "write-back --game <游戏标题> --json",
        "禁止绕过 CLI 手改项目数据",
        "直接改游戏 `data/*.json` 的 `note` 字段",
    ]
    for phrase in required_phrases:
        assert phrase in text

    forbidden_sample_phrases = [
        "拡張説明",
        "ExtendDesc",
        "namePop",
        "upgrade",
        "ChainSkill",
        "EquipState",
        "Items.json",
        "Weapons.json",
        "Skills.json",
        "Map*.json",
        "五类子代理",
        "主代理必须亲自修改候选译名并合并到 `terminology/" + "terms" + ".json`",
        "`terminology` 子代理：读取 `terminology/" + "terms" + ".json`",
        "只写 `terminology/" + "terms" + ".json`",
        "aliases",
        "别名",
        "格式为 `{插件名: [JSONPath, ...]}`",
        "对象格式，key 是插件名",
        "### 四类子代理任务契约",
        "主代理必须等待四类子代理全部完成",
        "四类子代理全部导入后",
        "references/rmmz-world-knowledge.md",
        "`placeholder-rules` 子代理",
        "`placeholder-rules` 子代理任务单",
        "主 Agent",
        "外部 Agent",
        "并行任务",
        "主执行者",
        "子任务执行者",
        "你必须",
        "人工处理者",
        "人工填写",
        "validate-japanese-residual-rules",
        "import-japanese-residual-rules",
        "默认 `ja`",
        "默认日文",
    ]
    for phrase in forbidden_sample_phrases:
        assert phrase not in text


def test_rule_agent_prompt_documents_exist_and_define_cli_contracts() -> None:
    """三类规则子代理引用的外部任务契约文档必须可直接执行。"""
    plugin_text = (ROOT / "docs" / "plugin-rules-agent-prompt.md").read_text(encoding="utf-8")
    event_text = (ROOT / "docs" / "event-command-rules-agent-prompt.md").read_text(encoding="utf-8")

    plugin_required_phrases = [
        "不读取项目源码、数据库或程序内部对象",
        "`<工作区>/plugins.json`",
        "唯一可写文件：`<工作区>/plugin-rules.json`",
        "格式为 `[{plugin_index, plugin_name, paths}]`",
        "`plugin_index` 必须是插件在 `plugins.json` 数组中的下标",
        "合法空结果是 `[]`",
        "JSONPath 必须使用括号路径语法",
        "validate-plugin-rules",
        "改动文件",
        "未解决风险",
    ]
    for phrase in plugin_required_phrases:
        assert phrase in plugin_text

    event_required_phrases = [
        "不读取项目源码、数据库或程序内部对象",
        "`<工作区>/event-commands.json`",
        "MV 工作区未显式指定编码时通常导出 `356` 插件命令",
        "MZ 工作区通常导出 `357` 插件命令",
        "唯一可写文件：`<工作区>/event-command-rules.json`",
        "格式为 `{指令编码字符串: [{match, paths}]}`",
        "`match` 的键必须是参数索引字符串",
        "没有过滤条件时，`match` 写 `{}`",
        "JSONPath 必须使用括号路径语法",
        "合法空结果是 `{}`",
        "validate-event-command-rules",
        "未解决风险",
    ]
    for phrase in event_required_phrases:
        assert phrase in event_text

    forbidden_real_context_phrases = [
        "C:\\",
        "D:\\",
        "Users\\",
        "测试样本",
        "Sexual_conflict",
        "生意気",
    ]
    combined_text = plugin_text + event_text
    for phrase in forbidden_real_context_phrases:
        assert phrase not in combined_text


def test_subtask_package_mode_document_defines_portable_contract() -> None:
    """外部协作任务包文档必须说明可带走任务和主代理验收边界。"""
    text = (ROOT / "skills" / "att-mz" / "references" / "subtask-package-mode.md").read_text(encoding="utf-8")

    required_phrases = [
        "# 外部协作任务包模式",
        "它不是新的 CLI 功能",
        "uv run python main.py --agent-mode prepare-agent-workspace",
        ".\\att-mz.exe --agent-mode prepare-agent-workspace",
        "用途",
        "输入",
        "处理逻辑",
        "输出格式",
        "禁止事项",
        "空结果",
        "主代理验收步骤",
        "## 固定目录结构",
        "一个任务包文件夹只对应一个任务",
        "不要把多个任务塞进同一个文件夹",
        "prompt.md",
        "manifest.json",
        "answer-template.json",
        "answer.json",
        "context/",
        "任务包文件夹必须能被压缩后远程分发",
        "禁止写真实本机绝对路径",
        "## 任务包清单",
        "满配导出时，外部协作任务包是多个文件夹",
        "terminology-speaker-and-actor/",
        "terminology-item/",
        "plugin-rules/",
        "event-command-rules/",
        "note-tag-rules/",
        "## manifest.json 格式",
        '"workspace_target"',
        '"context_files"',
        "任务包只能覆盖五个术语候选分组、插件规则、事件指令规则和 Note 标签规则",
        "占位符规则最终生成、覆盖扫描和导入，最终术语表合并与正文术语表维护，正文翻译、重置译文、写进游戏文件和字体覆盖，都不能导出为普通任务包",
        "用户交回任务包文件夹、压缩包或 `answer.json` 内容后",
        "检查 `manifest.json`、`prompt.md`、`answer-template.json`、`answer.json` 和 `context/` 是否存在",
        "把通过审核的 `answer.json` 写回 `<工作区>` 中 `manifest.json` 指定的 `workspace_target`",
        "对规则类结果运行对应 `validate-* --json`；通过后才运行对应 `import-* --json`",
        "不能导入规则，不能启动正文翻译，不能写进游戏文件",
    ]
    for phrase in required_phrases:
        assert phrase in text

    forbidden_real_context_phrases = [
        "C:\\",
        "D:\\",
        "Users\\",
        "测试样本",
        "Sexual_conflict",
        "生意気",
    ]
    for phrase in forbidden_real_context_phrases:
        assert phrase not in text


def test_release_skill_uses_packaged_cli_contract() -> None:
    """发行版 Skill 必须使用随包 exe，不能泄漏源码运行协议。"""
    text = (ROOT / "skills" / "att-mz-release" / "SKILL.md").read_text(encoding="utf-8")

    required_phrases = [
        "att-mz.exe",
        "不要读取项目源码",
        "不要运行 `uv run python main.py`",
        "不得直接 `import app...`",
        "`.\u005catt-mz.exe --agent-mode <命令> ...`",
        "`.\u005catt-mz.exe --agent-mode doctor --no-check-llm --json`",
        "`.\u005catt-mz.exe --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json`",
        "不直接修改数据库",
        "必须启用子代理并行处理",
        "子代理轮次固定为两轮",
        "### 子代理任务处理方式确认",
        "主代理必须先向用户确认子代理任务处理方式",
        "当前会话完成",
        "外部协作任务包",
        "混合处理",
        "多项候选分析会消耗较多上下文和模型额度",
        "额度有限时建议使用外部协作任务包",
        "任务包只替代子代理执行方式，不替代主代理审核职责",
        "用户返回的内容一律视为候选答案",
        "任务包必须拆成多个独立文件夹",
        "建议用户提示词、结构化上下文数据、答案模板、清单",
        "### 外部协作任务包",
        "外部协作任务包只允许覆盖五个术语候选分组、插件规则、事件指令规则和 Note 标签规则",
        "一个任务包文件夹只对应一个任务",
        "包内 `context/` 目录",
        "不读取项目源码、数据库、程序内部对象或原机器上的 `<工作区>` 路径",
        "占位符规则最终生成、覆盖扫描和导入不得导出为普通任务包",
        "最终术语表合并与正文术语表维护不得导出为普通任务包",
        "正文翻译、重置译文、写进游戏文件、字体覆盖不得导出为普通任务包",
        "只能要求在包内填写 `answer.json` 或返回 `answer.json` 内容",
        "任务包内只能使用相对路径",
        "可压缩、远程分发并在其他机器上完成",
        "### 用户返回答案验收",
        "先检查 JSON 结构和唯一写入边界",
        "防止编造路径、误选资源、脚本、公式或内部字段",
        "术语候选必须由主代理统一风格、去空值、查源文残留和译名冲突",
        "规则类结果必须运行对应 `validate-* --json`",
        "通过后才运行对应 `import-* --json`",
        "大面积错误时要求重做或改由主代理完成，不能直接导入",
        "### 第一轮：术语候选",
        "### 第二轮：三类外部规则",
        "### 命令 I/O 合约",
        "### 工作区 JSON 格式契约",
        "MV 的 `speaker_names` 是正文说话人术语：由 CLI 从每个对话块首条非空 `401` 正文识别",
        "MV 的 `speaker_names` 虽然放在字段译名表里，但只表示正文说话人术语，不表示可写回名字框字段",
        "第二轮子代理任务契约",
        "`quality-report --json` 有错误时禁止写进游戏文件",
        "普通写回不会覆盖字体",
        "每轮修复都必须重新运行质量检查报告",
    ]
    for phrase in required_phrases:
        assert phrase in text

    forbidden_phrases = [
        "uv run python main.py --agent-mode",
        "uv sync",
        "maturin develop",
        "必须读取项目源码",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in text


def test_skill_names_match_source_folders() -> None:
    """源码中的 Skill 名称必须和所在文件夹名一致。"""
    for folder_name in ("att-mz", "att-mz-release"):
        skill_text = (ROOT / "skills" / folder_name / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {folder_name}" in skill_text.split("---", 2)[1]


def test_release_skill_keeps_development_skill_structure() -> None:
    """发行版 Skill 必须保留开发版完整章节结构，只替换运行入口。"""
    dev_text = (ROOT / "skills" / "att-mz" / "SKILL.md").read_text(encoding="utf-8")
    release_text = (ROOT / "skills" / "att-mz-release" / "SKILL.md").read_text(encoding="utf-8")
    dev_headings = [line for line in dev_text.splitlines() if line.startswith("#")]
    release_headings = [line for line in release_text.splitlines() if line.startswith("#")]

    assert release_headings[0] == "# A.T.T MZ 发行版 Skill"
    assert release_headings[1:] == dev_headings[1:]


def test_release_skill_directory_contains_required_references() -> None:
    """发行版 Skill 目录必须和开发版一样带上按需参考资料。"""
    dev_reference = ROOT / "skills" / "att-mz" / "references" / "rpg-maker-mv-mz-world-knowledge.md"
    release_reference = ROOT / "skills" / "att-mz-release" / "references" / "rpg-maker-mv-mz-world-knowledge.md"
    assert release_reference.exists()
    assert release_reference.read_text(encoding="utf-8") == dev_reference.read_text(encoding="utf-8")

    package_reference = ROOT / "skills" / "att-mz-release" / "references" / "subtask-package-mode.md"
    assert package_reference.exists()
    package_text = package_reference.read_text(encoding="utf-8")
    assert ".\\att-mz.exe --agent-mode prepare-agent-workspace" in package_text
    assert "uv run python main.py" not in package_text
    assert "任务包文件夹必须能被压缩后远程分发" in package_text


def test_project_rules_require_github_workflow_releases_and_skill_sync() -> None:
    """项目规范必须固定 GitHub 工作流发布和双 Skill 同步更新。"""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    required_phrases = [
        "每次修改开发版 Skill 时，必须同步审查并更新发行版 Skill",
        "打包脚本必须把 `skills/att-mz-release/SKILL.md` 转换为发行包内的 `skills/att-mz/SKILL.md`",
        "每次正式发布发行版必须使用 GitHub Actions `release` 工作流生成并发布 ZIP",
        "禁止在本机手工打包后直接上传 GitHub Release",
        "本机只能提供源码改动、提交和工作流触发",
        "发行版验收必须由 GitHub Actions 发布工作流执行",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_release_packaging_script_uses_release_skill_template() -> None:
    """发布脚本必须把发行版 Skill 作为发行包内的 att-mz Skill。"""
    text = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")

    required_phrases = [
        "RELEASE_SKILL_SOURCE",
        '"att-mz-release" / "SKILL.md"',
        '"att-mz-release" / "references"',
        '"subtask-package-mode.md"',
        "copy_packaged_release_skill",
        '"name: att-mz-release", "name: att-mz"',
        '"skills" / "att-mz" / "SKILL.md"',
        "att-mz-windows-x86_64.zip",
        "ensure_github_actions_environment",
        "GITHUB_ACTIONS",
        "发行版构建只能在 GitHub Actions release 工作流中执行",
        "configure_stdio_encoding",
        'encoding="utf-8"',
        'errors="replace"',
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_text_translation_prompt_keeps_protocol_minimal() -> None:
    """正文翻译提示词只说明可见任务，不解释项目内部保护机制。"""
    text = (ROOT / "prompts" / "text_translation_system.md").read_text(encoding="utf-8")

    required_phrases = [
        "`[[术语表]]`",
        "`short_text`：按一个完整字段翻译，`translation_lines` 必须只包含 1 个字符串",
        "形如 `[RMMZ_...]` 或 `[CUSTOM_...]` 的片段是必须原样保留的文本标记。",
    ]
    for phrase in required_phrases:
        assert phrase in text

    forbidden_phrases = [
        "# 术语表",
        "如果原文内部已有换行",
        "常用于变量",
        "保护",
        "恢复",
        "写回",
        "占位符",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in text
