# RPG Maker Tools

一个面向 RPG Maker 项目的终端翻译工具仓库。

当前版本以 **Textual TUI 工作台** 为默认入口，围绕“多游戏管理 + 术语构建 + 正文翻译 + 回写”组织完整流程。每个游戏会在仓库本地维护独立 SQLite 数据库，用于保存术语、正文译文、错误表和元数据，支持断点续跑与多次回写。

## 1. 项目定位

这个仓库解决的是 RPG Maker 项目的批量翻译问题，重点不是“把一段文本送给模型”，而是把下面这些环节稳定串起来：

- 从游戏目录提取可翻译文本
- 使用术语表约束正文翻译
- 使用 SQLite 保存进度、译文和错误记录
- 将失败批次记录到错误表，便于后续排查
- 将最终译文回写到原始游戏目录

当前实现已经移除旧版 GUI/旧版单游戏配置思路，运行方式和文档都以现有 TUI 新栈为准。

## 2. 核心能力

- 多游戏管理
  - 启动时自动扫描仓库下的 `data/db/*.db`
  - 工作台内可继续添加新的游戏目录
  - 每个游戏按 `package.json.window.title` 建立独立数据库
- 术语构建
  - 从事件对白中提取角色名与对话样本
  - 从地图数据中提取 `displayName`
  - 使用独立术语模型服务翻译角色名、地点名
- 正文翻译
  - 提取 `data/` 目录内正文、系统术语、基础数据库文本
  - 提取 `js/plugins.js` 和事件内插件命令参数文本
  - 通过术语表参与上下文构造
  - 使用请求级去重缓存减少重复送模
  - 已翻译条目按 `location_path` 自动跳过，支持断点续跑
- 结果校验
  - 校验模型返回 JSON 结构
  - 校验漏翻
  - 校验占位符与控制符
  - 校验源语言残留（当前支持日文 / 英文）
  - 校验失败条目自动落入错误表
- 回写
  - 术语、正文统一回写到游戏原目录
  - 支持写回 `data/*.json`
  - 支持写回 `js/plugins.js`

## 3. 整体流程

推荐按下面顺序使用：

1. 添加游戏
2. 构建术语
3. 正文翻译
4. 回写

其中有几个关键前提：

- 正文翻译依赖完整术语表；术语表缺失或不完整时，正文流程会直接终止
- 正文翻译启动前会清理当前游戏已有错误表，并为本轮新建一张错误表
- 回写使用的是数据库中的最终结果，而不是内存中的临时状态

## 4. 运行环境

- Python `>=3.14`
- `uv`
- 推荐在 Windows 终端中运行；仓库已提供 `launch_tui.bat`

安装依赖：

```bash
uv sync
```

## 5. 快速开始

### 5.1 配置模型服务

先修改项目根目录的 `setting.toml`，填入你自己的模型服务地址、密钥、模型名和提示词文件路径。

### 5.2 启动工作台

```bash
uv run python main.py
```

或者直接使用：

```bash
uv run python main.py tui
```

Windows 下也可以双击：

```bat
launch_tui.bat
```

### 5.3 在工作台中操作

1. 首页点击“添加游戏”，输入 RPG Maker 游戏根目录
2. 选中游戏后按 `Enter` 进入任务页
3. 依次执行“构建术语”“正文翻译”“回写”

## 6. 游戏目录要求

被添加的游戏目录至少需要满足以下条件：

- 根目录存在 `package.json`
- `package.json` 中存在 `window.title`
- 根目录存在 `data/`
- 若需要翻译插件配置文本，建议存在 `js/plugins.js`

项目通过 `package.json.window.title` 作为：

- 游戏显示标题
- 数据库文件名
- 多游戏管理器中的唯一键

因此要注意：

- 不同游戏如果标题相同，会复用同一个数据库标识，容易串数据
- 标题不能包含 Windows 非法文件名字符，例如 `< > : " / \ | ? *`

## 7. 配置说明

### 7.1 配置入口

当前仓库只有一个配置入口：

- `setting.toml`

配置加载特点如下：

- 每次任务执行时都会重新加载 `setting.toml`
- 设置页修改字段后会立即写回文件
- 运行中的任务不会热更新，下一次任务才会使用新配置
- `system_prompt_file` 支持相对路径，相对基准是 `setting.toml` 所在目录

### 7.2 示例配置

下面是一份不包含真实密钥的示例：

```toml
[llm_services.glossary]
provider_type = "openai"
base_url = "https://your-glossary-endpoint/v1"
api_key = "your-glossary-api-key"
model = "your-glossary-model"
timeout = 600

[llm_services.text]
provider_type = "openai"
base_url = "https://your-text-endpoint/v1"
api_key = "your-text-api-key"
model = "your-text-model"
timeout = 600

[glossary_extraction]
role_chunk_blocks = 10
role_chunk_lines = 3

[glossary_translation.role_name]
chunk_size = 60
retry_count = 3
retry_delay = 1
response_retry_count = 3
system_prompt_file = "prompts/glossary_role_name_system.txt"

[glossary_translation.display_name]
chunk_size = 60
retry_count = 3
retry_delay = 1
response_retry_count = 3
system_prompt_file = "prompts/glossary_display_name_system.txt"

[translation_context]
token_size = 512
factor = 3.5
max_command_items = 5

[text_translation]
worker_count = 60
rpm = 60
retry_count = 3
retry_delay = 2
system_prompt_file = "prompts/text_translation_system.txt"
```

### 7.3 配置段说明

#### `llm_services`

用于定义两个独立模型服务：

| 字段 | 说明 |
| --- | --- |
| `llm_services.glossary` | 构建术语时使用的模型服务 |
| `llm_services.text` | 正文翻译时使用的模型服务 |

单个服务支持以下字段：

| 字段 | 说明 |
| --- | --- |
| `provider_type` | 提供商类型，当前支持 `openai`、`gemini`、`volcengine` |
| `base_url` | 接口基地址，可填官方地址或兼容网关 |
| `api_key` | 鉴权密钥 |
| `model` | 实际调用的模型名 |
| `timeout` | 单次请求超时时间，单位秒 |

#### `glossary_extraction`

控制角色样本采样策略：

| 字段 | 说明 |
| --- | --- |
| `role_chunk_blocks` | 将角色对白按时间线切成多少块 |
| `role_chunk_lines` | 每块保留多少行样本 |

#### `glossary_translation.role_name`

控制角色名翻译：

| 字段 | 说明 |
| --- | --- |
| `chunk_size` | 每批送模的角色条目数 |
| `retry_count` | 网络失败重试次数 |
| `retry_delay` | 网络失败重试间隔秒数 |
| `response_retry_count` | 结构校验失败后允许纠错的轮数 |
| `system_prompt_file` | 角色术语提示词文件 |

#### `glossary_translation.display_name`

控制地图显示名翻译，字段含义与 `role_name` 相同。

#### `translation_context`

控制正文批次切分和上下文大小：

| 字段 | 说明 |
| --- | --- |
| `token_size` | 单批目标 token 上限 |
| `factor` | 字符数到 token 的经验换算系数 |
| `max_command_items` | 同角色连续命令允许强制合并的最大条目数 |

#### `text_translation`

控制正文翻译运行参数：

| 字段 | 说明 |
| --- | --- |
| `worker_count` | 并发 worker 数量 |
| `rpm` | 每分钟请求上限 |
| `retry_count` | 网络失败重试次数 |
| `retry_delay` | 网络失败重试间隔秒数 |
| `system_prompt_file` | 正文翻译提示词文件 |

### 7.4 配置安全建议

- `setting.toml` 当前以明文形式保存密钥
- 不要把真实密钥写进公开仓库
- 如需共享仓库，建议只保留占位值

## 8. 工作台说明

### 8.1 页面结构

工作台有三个主要页面：

- 首页
  - 查看已注册游戏
  - 添加游戏
  - 进入设置页
- 设置页
  - 直接编辑 `setting.toml`
  - 输入校验失败时不会覆盖最后一次合法值
- 任务页
  - 对当前游戏执行构建术语、正文翻译、回写
  - 展示进度条、状态文本和实时日志

日志历史由应用统一维护，切换页面后不会丢失。

### 8.2 常用快捷键

| 按键 | 作用 |
| --- | --- |
| `Up` / `Down` | 列表上下移动 |
| `Enter` | 进入或执行当前项 |
| `Tab` / `Shift+Tab` | 切换焦点 |
| `Esc` | 返回上一页 |
| `g` | 添加游戏 |
| `s` | 打开设置页 |
| `Ctrl+Left` / `Ctrl+Right` | 切换设置页分页 |
| `l` | 聚焦日志区域 |
| `q` | 退出程序 |

### 8.3 推荐操作顺序

首次导入一个游戏时，推荐这样操作：

1. 在首页添加游戏
2. 先进入设置页确认模型配置和提示词路径
3. 进入任务页构建术语
4. 术语成功后再执行正文翻译
5. 确认无误后执行回写

## 9. 支持的提取与回写范围

### 9.1 `data/` 目录

当前正文提取覆盖以下内容：

- 事件对白
- 选项文本
- 滚动文本
- `System.json` 内的系统术语、提示消息、游戏标题等（默认跳过 `variables`、`switches` 名称）
- 基础数据库中的名称、昵称、简介、说明、战斗消息
- 可选 `Quests.json` 中的 `title_cte`、`summaries_cte`、`rewards_cte`、`objectives_cte`
- 事件中的插件命令文本参数

### 9.2 `js/plugins.js`

当前会递归提取插件配置中的可翻译文本，主要特点：

- 只提取看起来像文本字段的参数键
- 会跳过明显的文件名、纯数字、颜色值、布尔值和纯配置字符串
- 会优先保留包含日文的叶子文本

### 9.3 回写行为

回写时会先在内存中重建可写副本，再统一写回：

- `data/*.json`
- `js/plugins.js`

其中 `System.json/variables/*` 与 `System.json/switches/*` 会被显式忽略，
用于规避旧数据库残留译文继续污染游戏逻辑。

数据库中不存在的数据不会被凭空生成，因此回写前应先确保术语或正文结果已经写入数据库。

## 10. 数据存储与产物

### 10.1 数据库位置

每个游戏的数据库固定保存在：

```text
data/db/<游戏标题>.db
```

这是仓库内部固定路径，不走外部配置。

### 10.2 数据库存储内容

每个游戏数据库会保存：

- 主翻译表
- 角色术语表
- 地点术语表
- 术语状态表
- 元数据表
- 按时间戳创建、且当前只保留最新一张的错误表

### 10.3 启动时的恢复行为

程序启动时会自动扫描 `data/db` 并恢复：

- 游戏标题
- 游戏原始路径
- 数据库连接

因此同一仓库下的已添加游戏，下次启动仍会出现在工作台中。

## 11. 目录结构

```text
rpg-maker-tools/
├─ app/
│  ├─ config/          # 配置模型
│  ├─ core/            # 编排器与依赖注入
│  ├─ database/        # SQLite 管理与 SQL
│  ├─ extraction/      # 术语、正文、插件文本提取
│  ├─ models/          # 游戏数据与业务模型
│  ├─ services/llm/    # LLM 服务适配层
│  ├─ translation/     # 术语翻译、正文翻译、校验、上下文、缓存
│  ├─ tui/             # Textual 工作台
│  ├─ utils/           # 配置、日志、路径、探针等工具
│  └─ write_back/      # 回写逻辑
├─ prompts/            # 提示词文件
├─ data/db/            # 运行后自动生成的数据库目录
├─ main.py             # 程序入口
├─ launch_tui.bat      # Windows 快捷启动脚本
├─ setting.toml        # 唯一配置入口
├─ pyproject.toml
└─ uv.lock
```

## 12. 常见问题

### 12.1 为什么正文翻译一启动就终止？

最常见原因是术语表缺失或不完整。当前流程要求先构建术语，再跑正文翻译。

### 12.2 为什么重新启动后游戏列表还在？

因为游戏注册信息保存在 `data/db/*.db` 中，启动时会自动恢复。

### 12.3 为什么添加游戏时报数据库文件名错误？

因为数据库文件名直接来自 `package.json.window.title`。如果标题包含 Windows 非法字符，就无法创建数据库文件。

### 12.4 修改 `setting.toml` 后要不要重启程序？

通常不需要。下一次任务执行时会重新读取配置。但已经在运行中的任务不会中途切换到新配置。

## 13. 开发与冒烟验证

当前仓库没有独立维护自动化测试目录，修改后建议至少做一次最小冒烟：

```bash
uv sync
uv run python main.py
```

建议重点检查：

- 工作台是否能正常启动
- 设置页是否能正常保存 `setting.toml`
- 添加游戏后是否成功生成 `data/db/*.db`
- 术语构建、正文翻译、回写的日志是否符合预期

## 14. 适用范围说明

当前实现面向 RPG Maker MV/MZ 风格项目，重点覆盖：

- `data/` 目录 JSON 数据
- `package.json`
- `js/plugins.js`

如果你的项目目录结构、插件格式或文本承载方式明显偏离这一套约定，建议先阅读提取和回写模块代码，再决定是否直接投入生产使用。
