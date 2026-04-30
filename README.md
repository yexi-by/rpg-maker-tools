# RPG Maker Tools

一个面向 RPG Maker MZ 项目的命令行翻译核心骨架，提供 CLI、多游戏数据库、标准 `data/*.json` 文本提取、外部插件规则导入、翻译缓存与断点续传、提示词组装、外部术语表导入和游戏文本回写。

## 1. 项目定位

这个仓库是一个“干净的半成品”核心框架，聚焦 RPG Maker MZ 日文游戏翻译流水线，方便后续围绕文本提取、上下文、模型调用和回写继续扩展。

核心能力：

- 通过 CLI 子命令调用核心流程。
- 使用 SQLite 保存游戏注册信息、译文、错误表、插件规则、事件指令规则和术语表。
- 处理 RPG Maker MZ 标准 `data` JSON 文件。
- 使用显式导入的 `plugins.js` 规则提取插件配置文本。
- 使用显式导入的事件指令规则提取 `data/*.json` 中的复杂参数文本。
- 使用正文翻译缓存减少重复请求，并支持已完成译文断点续跑。
- 内置保护 RPG Maker MZ 标准控制符，并按配置组装提示词和文本过滤规则。
- 导出 `101` 名字框与 `MapXXX.displayName` 上下文，允许外部 Agent 填写标准译名后显式导入数据库，再注入正文提示词。
- 把已完成译文写回 `data/*.json` 与 `js/plugins.js`。

## 2. 运行环境

- Python `>=3.14`
- `uv`
- 主配置入口：项目根目录的 `setting.toml`
- 可选自定义占位符规则：项目根目录的 `custom_placeholder_rules.json`，或翻译命令直接传入的 `--placeholder-rules` JSON 字符串

安装依赖：

```bash
uv sync
```

## 3. 快速开始

先修改 `setting.toml`，填入正文翻译的模型服务地址、密钥、模型名和提示词文件路径。

注册游戏：

```bash
uv run python main.py add-game --path "<游戏根目录>"
```

查看已注册游戏：

```bash
uv run python main.py list
```

执行完整流程：

```bash
uv run python main.py run-all --game "游戏标题"
```

如果只想翻译但暂不写回：

```bash
uv run python main.py run-all --game "游戏标题" --skip-write-back
```

如果要让外部 Agent 处理人名和地图显示名，可以先导出标准名上下文：

```bash
uv run python main.py export-name-context --game "游戏标题" --output-dir "<外部临时目录>/name-context"
```

会生成：

```text
<外部临时目录>/name-context/name_registry.json
<外部临时目录>/name-context/speaker_contexts/*.json
```

让外部 Agent 根据小 JSON 的对白上下文填写 `name_registry.json` 中的译名值后，必须先导入数据库：

```bash
uv run python main.py import-name-context --game "游戏标题" --input "<外部临时目录>/name-context/name_registry.json"
```

如果需要翻译 `plugins.js` 中的插件参数文本，先导出插件配置 JSON：

```bash
uv run python main.py export-plugins-json --game "游戏标题" --output "<外部临时目录>/plugins.json"
```

外部 Agent 根据这个 JSON 产出插件规则 JSON 后，再导入数据库：

```bash
uv run python main.py import-plugin-rules --game "游戏标题" --input "<外部临时目录>/plugin-rules.json"
```

如果需要翻译 `data/*.json` 中的事件指令参数，先导出事件指令 JSON。未传 `--code` 时使用 `setting.toml` 中的 `event_command_text.default_command_codes` 数组：

```bash
uv run python main.py export-event-commands-json --game "游戏标题" --output "<外部临时目录>/event-commands.json"
```

需要临时覆盖配置数组时，直接把本次要导出的编码传给 `--code`：

```bash
uv run python main.py export-event-commands-json --game "游戏标题" --code 357 999 --output "<外部临时目录>/event-commands.json"
```

外部 Agent 根据事件指令 JSON 产出规则 JSON 后，再导入数据库：

```bash
uv run python main.py import-event-command-rules --game "游戏标题" --input "<外部临时目录>/event-command-rules.json"
```

`translate`、`write-back`、`run-all` 的运行时数据源是当前游戏数据库。

## 4. CLI 命令

```bash
uv run python main.py list
uv run python main.py add-game --path <游戏根目录>
uv run python main.py export-plugins-json --game <游戏标题> --output <plugins.json>
uv run python main.py export-event-commands-json --game <游戏标题> --output <commands.json> [--code <事件指令编码> ...]
uv run python main.py export-name-context --game <游戏标题> --output-dir <临时目录>
uv run python main.py import-name-context --game <游戏标题> --input <name_registry.json>
uv run python main.py import-plugin-rules --game <游戏标题> --input <plugin_rules.json>
uv run python main.py import-event-command-rules --game <游戏标题> --input <event_command_rules.json>
uv run python main.py translate --game <游戏标题>
uv run python main.py write-back --game <游戏标题>
uv run python main.py write-name-context --game <游戏标题>
uv run python main.py run-all --game <游戏标题> [--skip-write-back]
```

调试时可以在子命令前加入全局参数：

```bash
uv run python main.py --debug list
```

`run-all` 的固定顺序是：

```text
正文翻译 -> 回写
```

如果正文翻译被阻断，或正文翻译产生错误条目，`run-all` 会停止在失败阶段，修复问题后再执行回写。

## 5. 游戏目录要求

被添加的游戏目录至少需要满足以下条件：

- 根目录存在 `package.json`
- `package.json` 中存在 `window.title`
- 根目录存在 `data/`
- 根目录存在 `js/plugins.js`
- `data/` 中存在 `System.json`、`CommonEvents.json`、`Troops.json`

项目通过 `package.json.window.title` 作为游戏显示标题、数据库文件名和注册表唯一键。不同游戏如果标题相同，会复用同一个数据库标识，容易串数据。

## 6. 支持的提取范围

### 6.1 标准 `data/` 文件

扫描范围为以下 RPG Maker MZ 标准文件：

```text
Actors.json
Animations.json
Armors.json
Classes.json
CommonEvents.json
Enemies.json
Items.json
MapInfos.json
MapXXX.json
Skills.json
States.json
System.json
Tilesets.json
Troops.json
Weapons.json
```

提取内容包括：

- 事件对白
- `101` 名字框作为角色上下文参与正文提示词，不作为普通正文译文项
- 选项文本
- 连续 405 滚动文本
- `System.json` 内的系统术语、提示消息和游戏标题
- 基础数据库中的名称、昵称、简介、说明、战斗消息

未知 `data/*.json` 会被跳过并写入 DEBUG 文件日志，不进入业务流程。

### 6.2 外部标准名上下文

`101` 名字框和 `MapXXX.displayName` 通过导出上下文、外部填写译名、导入数据库的方式管理：

- 大 JSON：`name_registry.json`，用原文作为键，译名作为值。
- 小 JSON：`speaker_contexts/*.json`，每个文件保存一个名字框对应的对白样本，方便外部 Agent 判断人物、性别和稳定译名。

`name_registry.json` 格式：

```json
{
  "speaker_names": {
    "パティ": "",
    "村人": ""
  },
  "map_display_names": {
    "始まりの町": ""
  }
}
```

`speaker_contexts/*.json` 格式：

```json
{
  "name": "パティ",
  "dialogue_lines": [
    "おはよう。",
    "今日はどこへ行くの？"
  ]
}
```

填写后的大 JSON 有两个用途：

- `import-name-context`：把已填写译名写入当前游戏数据库。
- `translate`：从数据库读取相关标准名并注入正文用户提示词。
- `write-back` / `write-name-context`：从数据库读取译名并写回 `101.parameters[4]` 和 `MapXXX.json.displayName`。

大 JSON 和小 JSON 都是项目外临时文件。显式导入后，主流程从当前游戏数据库读取术语表数据。

### 6.3 `js/plugins.js`

插件文本分两步处理：

- `export-plugins-json` 把当前游戏的 `js/plugins.js` 转成纯 JSON 文件。
- 外部 Agent 根据插件配置 JSON 产出插件规则 JSON。
- `import-plugin-rules` 校验规则是否命中当前插件字符串叶子，并写入当前游戏数据库。
- `translate` 从数据库读取匹配当前插件结构的规则，提取命中字符串。

插件配置 JSON 的顶层就是 RPG Maker `$plugins` 数组，不添加候选字段、说明字段或文本判断结果：

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

插件规则 JSON 格式：

```json
{
  "TestPlugin": [
    "$['parameters']['Message']",
    "$['parameters']['Choices'][*]['text']"
  ]
}
```

键是 `plugins.js` 中的插件名，值是该插件内需要翻译的路径数组。路径使用项目支持的受限 JSONPath：以 `$['parameters']` 开头，对象键使用 `['key']`，数组索引用 `[0]`，需要批量匹配数组时可用 `[*]`。没有可翻译文本的插件不要写入插件名。

### 6.4 `data/*.json` 事件指令参数

事件指令参数文本统一使用外部规则管理。未传 `--code` 时，导出编码来自 `setting.toml` 的 `event_command_text.default_command_codes` 数组；传入 `--code` 时，本次导出只使用 CLI 编码数组。

导出事件指令参数：

```bash
uv run python main.py export-event-commands-json --game "游戏标题" --output "<外部临时目录>/event-commands.json"
```

`--code` 接收一个或多个事件指令编码。未传 `--code` 时读取配置数组；传入 `--code` 时覆盖配置数组：

```bash
uv run python main.py export-event-commands-json --game "游戏标题" --code 357 999 --output "<外部临时目录>/event-commands.json"
```

导出 JSON 结构：

```json
{
  "357": [
    [
      "TestPlugin",
      "Show",
      0,
      {
        "message": "こんにちは"
      }
    ]
  ]
}
```

事件指令规则 JSON 格式：

```json
{
  "357": [
    {
      "match": {
        "0": "TestPlugin",
        "1": "Show"
      },
      "paths": [
        "$['parameters'][3]['message']"
      ]
    }
  ]
}
```

字段说明：

- 顶层键：事件指令编码。
- `match`：按参数索引匹配具体指令，适合区分同一编码下的不同插件或命令。
- `paths`：以 `$['parameters']` 开头的受限 JSONPath 数组，可使用 `[*]` 批量匹配数组。

导入后，`translate` 从当前游戏数据库读取事件指令规则并提取命中的字符串叶子。`write-back` 根据译文路径写回对应 `parameters` 字符串叶子。

### 6.5 回写行为

回写时会先在内存中重建可写副本，再统一写回：

- 标准 `data/*.json`
- 外部事件指令规则命中的 `data/*.json` 参数文本
- `js/plugins.js`
- 数据库术语表中的 `101` 名字框与 `MapXXX.displayName`

首次写回会把本轮受影响的原始文件复制到留档位置：

```text
data_origin/<受影响文件名>.json
js/plugins_origin.js（仅当 plugins.js 本轮发生变化时）
```

二次写回直接替换激活版受影响文件，并保持 `data_origin/` 与 `js/plugins_origin.js` 中的原件留档不变。存在留档的文件优先从留档读取；没有留档的文件从当前 `data/` 读取。

## 7. 配置说明

当前仓库的主配置入口：

```text
setting.toml
```

自定义占位符规则默认使用项目根目录文件：

```text
custom_placeholder_rules.json
```

主要配置段：

- `llm`：正文翻译模型服务，使用 OpenAI 兼容 Chat Completions 格式。
- `translation_context`：正文切批参数。
- `text_translation`：正文翻译并发、限速、重试和提示词。
- `event_command_text`：事件指令参数默认导出编码数组。
- `write_back`：写回阶段的字体复制与字体引用替换配置。
- `text_rules`：正文进入翻译的字符判定、包裹标点、长文本切行和日文残留检查规则。

事件指令参数默认导出编码写在 `setting.toml`：

```toml
[event_command_text]
default_command_codes = [357]
```

这个数组控制 `export-event-commands-json` 未传 `--code` 时导出的事件指令编码。命令行传入 `--code` 时，本次导出使用命令行编码数组覆盖配置数组。

写回字体路径写在 `setting.toml`：

```toml
[write_back]
replacement_font_path = "fonts/NotoSansSC-Regular.ttf"
```

写回阶段会把该字体复制到游戏 `fonts/` 目录，并把本轮写出的 `data/*.json` 与 `js/plugins.js` 中命中的旧字体文件名、旧字体名替换为该字体文件名。命令行可以用 `--replacement-font-path` 覆盖本次运行使用的路径。

RPG Maker MZ 标准文本控制符由代码内置保护。不同游戏的额外控制符或特殊脚本标记使用 JSON 规则表达，不放进 `setting.toml`。

正文提取默认要求原文包含平假名、片假名或 CJK 汉字。纯英文、纯数字、资源路径和插件默认占位文本不会进入翻译缓存，也不会参与写回。

翻译命令未传 `--placeholder-rules` 时读取项目根目录的 `custom_placeholder_rules.json`。传入 `--placeholder-rules` 时，本次运行只解析命令行传入的 JSON 字符串：

```bash
uv run python main.py translate --game "<游戏标题>" --placeholder-rules "{\"\\\\\\\\F\\\\[[^\\\\]]+\\\\]\":\"[CUSTOM_FACE_{index}]\"}"
uv run python main.py run-all --game "<游戏标题>" --placeholder-rules "{\"\\\\\\\\F\\\\[[^\\\\]]+\\\\]\":\"[CUSTOM_FACE_{index}]\"}"
```

`custom_placeholder_rules.json` 是 JSON 对象，键是正则表达式字符串，值是占位符模板字符串：

```json
{
  "\\\\js\\[[^\\]]+\\]": "[CUSTOM_JS_{index}]",
  "@name\\[[^\\]]+\\]": "[CUSTOM_NAME_{index}]"
}
```

效果示例：

```text
原文：こんにちは\V[1] @name[アリス]
送模：こんにちは[RMMZ_V_1] [CUSTOM_NAME_1]
写回：你好\V[1] @name[アリス]
```

占位符模板支持 `{index}`，推荐固定使用 `[CUSTOM_名称_{index}]` 格式。项目会在翻译前替换命中的片段，在译文校验后恢复原始片段。

`system_prompt_file` 支持相对路径，相对基准是 `setting.toml` 所在目录。`setting.toml` 当前以明文形式保存密钥，不要提交真实密钥到公开仓库。

LLM 底层使用 OpenAI 官方异步 SDK 和 OpenAI 兼容 Chat Completions 格式。底层请求不做自动重试；正文翻译会在业务层只针对连接错误、超时、限流、临时状态码和 5xx 服务端错误重试，鉴权、权限、参数、模型不存在和空响应会立即中断流程。

## 8. 数据存储与日志

每个游戏的数据库固定保存在：

```text
data/db/<游戏标题>.db
```

每个游戏数据库会保存：

- 游戏元数据
- 主翻译表
- 外部导入的插件文本路径规则表
- 外部导入的事件指令文本路径规则表
- 外部导入的术语表条目表
- 按时间戳创建的错误表

外部术语表大 JSON、插件规则 JSON 和事件指令规则 JSON 是临时导入文件。显式导入后，多游戏数据由 `data/db/<游戏标题>.db` 分库管理。

数据库按命令按需打开：

- `list` 扫描 `data/db/*.db`，只读取每个数据库的 `metadata`。
- `add-game` 校验目标游戏目录，创建或更新对应数据库。
- 带 `--game` 的命令只打开目标游戏数据库。
- 需要校验游戏文件结构的命令才加载目标游戏的 `data/` 或 `js/plugins.js`。

日志默认写入：

```text
logs/app.log
```

终端默认只显示 `INFO` 及以上级别；排障时使用 `--debug` 查看更细粒度日志。未知异常的完整 traceback 会写入文件日志，终端只显示中文摘要。

## 9. 目录结构

```text
rpg-maker-tools/
├─ app/
│  ├─ cli.py              # argparse 子命令入口与进度条适配
│  ├─ application/        # 应用用例编排、运行时装配、文件回写事务
│  ├─ config/             # setting.toml 配置模型
│  ├─ event_command_text/ # 外部事件指令规则导出、导入、提取
│  ├─ llm/                # OpenAI 兼容异步客户端与错误分类
│  ├─ name_context/       # 101 名字框与地图显示名导出、导入、提示词注入和写回
│  ├─ observability/      # 结构化日志、终端与文件日志分层
│  ├─ persistence/        # SQLite 连接、SQL、行对象适配和仓储
│  ├─ plugin_text/        # 外部插件规则导入、JSONPath 展开、提取与回写
│  ├─ rmmz/               # RMMZ 数据模型、标准文件加载、文本提取与写回
│  ├─ translation/        # 正文翻译、校验、上下文、缓存
│  └─ utils/              # 通用小工具
├─ docs/               # 模块联动说明文档
├─ prompts/            # 提示词文件
├─ data/db/            # 运行后自动生成的数据库目录
├─ logs/               # 运行日志目录
├─ tests/              # 最小 RMMZ fixture 与核心模块测试
├─ main.py             # CLI 程序入口
├─ setting.toml        # 主配置入口
├─ custom_placeholder_rules.json # 可选自定义占位符规则
├─ pyproject.toml
└─ uv.lock
```

## 10. 开发与冒烟验证

修改后建议至少执行：

```bash
uv sync
uv run basedpyright
uv run pytest
uv run python main.py --help
uv run python main.py list
```

如果本地有可用游戏和模型配置，再执行人工冒烟：

```bash
uv run python main.py add-game --path "<游戏根目录>"
uv run python main.py run-all --game "游戏标题" --skip-write-back
```

不要默认跑真实模型翻译任务，避免消耗 API。

更详细的模块联动说明见：

- `docs/core-module-guide.md`
- `docs/cli-project-usage.md`
- `docs/module-diagrams.md`
- `docs/custom-placeholder-rules.md`

