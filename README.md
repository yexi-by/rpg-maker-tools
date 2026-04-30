# RPG Maker Tools

一个面向 RPG Maker MZ 项目的命令行翻译核心骨架，提供 CLI、多游戏数据库、标准 `data/*.json` 文本提取、外部插件规则导入、翻译缓存与断点续传、提示词组装、外部术语表导入和游戏文本回写。

## 1. 项目定位

这个仓库是一个“干净的半成品”核心框架，聚焦 RPG Maker MZ 日文游戏翻译流水线，方便后续围绕文本提取、上下文、模型调用和回写继续扩展。

核心能力：

- 通过 CLI 子命令调用核心流程。
- 使用 SQLite 保存游戏注册信息、译文、错误表、插件规则和术语表。
- 处理 RPG Maker MZ 标准 `data` JSON 文件。
- 使用显式导入的 `plugins.js` 规则提取插件配置文本。
- 使用正文翻译缓存减少重复请求，并支持已完成译文断点续跑。
- 按配置组装提示词、控制符占位符和文本过滤规则。
- 导出 `101` 名字框与 `MapXXX.displayName` 上下文，允许外部 Agent 填写标准译名后显式导入数据库，再注入正文提示词。
- 把已完成译文写回 `data/*.json` 与 `js/plugins.js`。

## 2. 运行环境

- Python `>=3.14`
- `uv`
- 唯一配置入口：项目根目录的 `setting.toml`

安装依赖：

```bash
uv sync
```

## 3. 快速开始

先修改 `setting.toml`，填入正文翻译的模型服务地址、密钥、模型名和提示词文件路径。

注册游戏：

```bash
uv run python main.py add-game --path "D:/games/your-project"
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
uv run python main.py export-name-context --game "游戏标题" --output-dir "D:/tmp/name-context/游戏标题"
```

会生成：

```text
D:/tmp/name-context/游戏标题/name_registry.json
D:/tmp/name-context/游戏标题/speaker_contexts/*.json
```

让外部 Agent 根据小 JSON 的对白上下文填写 `name_registry.json` 中的 `translated_text` 后，必须先导入数据库：

```bash
uv run python main.py import-name-context --game "游戏标题" --input "D:/tmp/name-context/游戏标题/name_registry.json"
```

如果需要翻译 `plugins.js` 中的插件参数文本，先导出插件配置 JSON：

```bash
uv run python main.py export-plugins-json --game "游戏标题" --output "D:/tmp/plugin-rules/游戏标题.plugins.json"
```

外部 Agent 根据这个 JSON 产出插件规则 JSON 后，再导入数据库：

```bash
uv run python main.py import-plugin-rules --game "游戏标题" --input "D:/tmp/plugin-rules/游戏标题.json"
```

`translate`、`write-back`、`run-all` 的运行时数据源是当前游戏数据库。

## 4. CLI 命令

```bash
uv run python main.py list
uv run python main.py add-game --path <游戏根目录>
uv run python main.py export-plugins-json --game <游戏标题> --output <plugins.json>
uv run python main.py export-name-context --game <游戏标题> --output-dir <临时目录>
uv run python main.py import-name-context --game <游戏标题> --input <name_registry.json>
uv run python main.py import-plugin-rules --game <游戏标题> --input <plugin_rules.json>
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

项目通过 `package.json.window.title` 作为游戏显示标题、数据库文件名和多游戏管理器中的唯一键。不同游戏如果标题相同，会复用同一个数据库标识，容易串数据。

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
- 滚动文本
- `System.json` 内的系统术语、提示消息和游戏标题
- 基础数据库中的名称、昵称、简介、说明、战斗消息
- 标准事件命令 `357` 中命中文本规则的插件命令参数

未知 `data/*.json` 会被跳过并写入 DEBUG 文件日志，不进入业务流程。

### 6.2 外部标准名上下文

`101` 名字框和 `MapXXX.displayName` 通过导出上下文、外部填写译名、导入数据库的方式管理：

- 大 JSON：`name_registry.json`，保存每个名字框和地图显示名，`translated_text` 默认为空。
- 小 JSON：`speaker_contexts/*.json`，保存每个 `101` 名字框后续的实际对白，方便外部 Agent 判断人物、性别和稳定译名。

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

`path_template` 使用项目支持的受限 JSONPath：以 `$['parameters']` 开头，对象键使用 `['key']`，数组索引用 `[0]`，需要批量匹配数组时可用 `[*]`。没有可翻译文本的插件不要写入 `plugins`。

### 6.4 回写行为

回写时会先在内存中重建可写副本，再统一写回：

- 标准 `data/*.json`
- `js/plugins.js`
- 数据库术语表中的 `101` 名字框与 `MapXXX.displayName`

首次写回会把本轮受影响的原始文件复制到留档位置：

```text
data_origin/<受影响文件名>.json
js/plugins_origin.js（仅当 plugins.js 本轮发生变化时）
```

二次写回直接替换激活版受影响文件，并保持 `data_origin/` 与 `js/plugins_origin.js` 中的原件留档不变。存在留档的文件优先从留档读取；没有留档的文件从当前 `data/` 读取。

## 7. 配置说明

当前仓库只有一个配置入口：

```text
setting.toml
```

主要配置段：

- `llm`：正文翻译模型服务，使用 OpenAI 兼容 Chat Completions 格式。
- `translation_context`：正文切批参数。
- `text_translation`：正文翻译并发、限速、重试和提示词。
- `text_rules`：控制符、占位符、硬编码标点、插件文本过滤和日文残留检查规则。

`text_rules` 同时控制“是否翻译”和“怎么保护游戏运行时标记”。如果某个游戏的控制符风格不同，可以在这里调整控制符前缀、参数分隔符、占位符模板、复杂控制符判定、插件文本语言过滤、长文本计数字符正则、路径键名过滤和脚本表达式判定规则。业务代码承载 RMMZ 事件命令号、标准文件名这类引擎结构常量，文本风格相关规则应优先进入配置。

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
- 外部导入的术语表条目表
- 按时间戳创建的错误表

外部术语表大 JSON 和插件规则 JSON 只是临时导入文件，不写入仓库固定目录。显式导入后，多游戏数据由 `data/db/<游戏标题>.db` 分库管理。

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
├─ setting.toml        # 唯一配置入口
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
uv run python main.py add-game --path "D:/games/your-project"
uv run python main.py run-all --game "游戏标题" --skip-write-back
```

不要默认跑真实模型翻译任务，避免消耗 API。

更详细的模块联动说明见：

- `docs/core-module-guide.md`
- `docs/module-diagrams.md`
