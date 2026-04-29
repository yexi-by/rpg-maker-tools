# RPG Maker Tools

一个面向 RPG Maker MZ 项目的命令行翻译核心骨架。

当前版本只保留可继续扩展的核心能力：CLI、多游戏数据库、标准 `data/*.json` 文本提取、插件文本规则分析、翻译缓存与断点续传、提示词组装、正文译文回写。TUI、英文游戏兼容、术语表流程和插件衍生 JSON 专用处理已经删除。

## 1. 项目定位

这个仓库现在是一个“干净的半成品”而不是完整产品。它用于保留原项目里真正有价值的翻译流水线骨架，方便后续重新设计和扩展。

保留目标：

- 通过 CLI 子命令调用核心流程。
- 使用 SQLite 保存游戏注册信息、译文、错误表和插件解析规则。
- 只处理 RPG Maker MZ 标准 `data` JSON 文件。
- 使用 `plugins.js` 规则缓存提取插件配置文本。
- 使用正文翻译缓存减少重复请求，并支持已完成译文断点续跑。
- 按配置组装提示词、控制符占位符和文本过滤规则。
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

先修改 `setting.toml`，填入正文翻译和插件解析的模型服务地址、密钥、模型名和提示词文件路径。

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

## 4. CLI 命令

```bash
uv run python main.py list
uv run python main.py add-game --path <游戏根目录>
uv run python main.py analyze-plugin --game <游戏标题>
uv run python main.py translate --game <游戏标题>
uv run python main.py write-back --game <游戏标题>
uv run python main.py run-all --game <游戏标题> [--skip-plugin-analysis] [--skip-write-back]
```

调试时可以在子命令前加入全局参数：

```bash
uv run python main.py --debug list
```

`run-all` 的固定顺序是：

```text
插件解析 -> 正文翻译 -> 回写
```

如果插件解析存在失败插件、正文翻译被阻断，或正文翻译产生错误条目，`run-all` 会停止，不会自动回写部分失败结果。

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

当前只扫描以下 RPG Maker MZ 标准文件：

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
- 选项文本
- 滚动文本
- `System.json` 内的系统术语、提示消息和游戏标题
- 基础数据库中的名称、昵称、简介、说明、战斗消息
- 标准事件命令 `357` 中命中文本规则的插件命令参数

未知 `data/*.json` 会被跳过并写入 DEBUG 文件日志，不进入业务流程。

### 6.2 `js/plugins.js`

插件文本分两步处理：

- `analyze-plugin` 使用模型分析插件参数树，生成可翻译 JSONPath 规则并写入数据库。
- `translate` 只复用仍然匹配当前插件结构和提示词的成功规则，提取命中字符串。

### 6.3 回写行为

回写时会先在内存中重建可写副本，再统一写回：

- 标准 `data/*.json`
- `js/plugins.js`

术语表、非标准任务文件和英文兼容逻辑不会参与回写。

## 7. 配置说明

当前仓库只有一个配置入口：

```text
setting.toml
```

主要配置段：

- `llm_services.text`：正文翻译模型服务。
- `llm_services.plugin_text`：插件文本路径分析模型服务。
- `translation_context`：正文切批参数。
- `plugin_text_analysis`：插件解析并发、限速、重试和提示词。
- `text_translation`：正文翻译并发、限速、重试和提示词。
- `text_rules`：控制符、占位符、硬编码标点、插件文本过滤和日文残留检查规则。

`system_prompt_file` 支持相对路径，相对基准是 `setting.toml` 所在目录。`setting.toml` 当前以明文形式保存密钥，不要提交真实密钥到公开仓库。

## 8. 数据存储与日志

每个游戏的数据库固定保存在：

```text
data/db/<游戏标题>.db
```

每个游戏数据库会保存：

- 游戏元数据
- 主翻译表
- 插件文本路径规则表
- 插件文本分析状态表
- 按时间戳创建的错误表

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
│  ├─ llm/                # LLM 服务适配层
│  ├─ observability/      # 结构化日志、终端与文件日志分层
│  ├─ persistence/        # SQLite 连接、SQL、行对象适配和仓储
│  ├─ plugin_text/        # 插件参数树解析、JSONPath 规则、提取与回写
│  ├─ rmmz/               # RMMZ 数据模型、标准文件加载、文本提取与写回
│  ├─ translation/        # 正文翻译、校验、上下文、缓存
│  └─ utils/              # 仍需保留的通用小工具
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
