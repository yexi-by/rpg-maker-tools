# RPG Maker Tools

一个面向 RPG Maker 项目的 CLI 翻译工具。

当前版本围绕四件事组织：

- 提取术语和正文
- 调用 LLM 进行翻译
- 用 SQLite 保存断点状态和术语表
- 将译文回写到游戏目录

当前版本已经彻底移除 GUI。`main.py` 现在只启动交互式 CLI，业务编排仍由 [`TranslationHandler`](/c:/Users/夜袭/Desktop/translation%20tools/rpg-maker-tools/app/core/handler.py) 负责。

## 当前能力

- 术语表构建
  - 提取角色名和地图显示名
  - 分块调用 LLM 翻译术语
  - 术语表统一写入 `translation.db`
- 正文翻译
  - 提取 `data/` 与 `js/plugins.js` 中可翻译文本
  - 读取数据库中的术语表参与翻译
  - 已完成译文按 `location_path` 过滤，支持断点续跑
- 错误重翻译
  - 读取最新错误表
  - 重新构造上下文并再次翻译
- 回写
  - 读取数据库中的术语表和正文译文
  - 统一回写到游戏原目录
- CLI 交互
  - 启动后通过数字菜单选择动作
  - 动作执行完成后返回菜单
  - 配置修改只在重启进程后生效

## 目录结构

```text
rpg-maker-tools/
├─ app/
│  ├─ config/          # 配置模型与加载器
│  ├─ core/            # Handler 与依赖装配
│  ├─ database/        # SQLite 读写
│  ├─ extraction/      # 术语、正文、插件文本提取
│  ├─ models/          # 游戏数据与翻译数据模型
│  ├─ services/llm/    # LLM 服务适配层
│  ├─ translation/     # 术语翻译、正文翻译、校验、上下文构建
│  ├─ utils/           # 日志、进度条、通用工具
│  └─ write_back/      # 术语与正文回写
├─ cli/                # 交互式 CLI 入口
├─ prompts/            # 系统提示词文件
├─ tests/              # 回归测试
├─ main.py             # CLI 入口
└─ setting.toml        # 配置样例
```

## 环境要求

- Python 3.14+
- `uv`

安装依赖：

```bash
uv sync
```

## 配置

项目根目录下的 [`setting.toml`](/c:/Users/夜袭/Desktop/translation%20tools/rpg-maker-tools/setting.toml) 是唯一配置入口。

核心字段包括：

```toml
[project]
file_path = "D:/your-game"
work_path = "data"
db_name = "translation.db"
translation_table_name = "translation_items"

[llm_services.glossary]
provider_type = "openai"
base_url = "https://your-glossary-api-base"
api_key = "your-glossary-api-key"
model = "your_glossary_model"
timeout = 600
```

说明：

- `project.file_path` 指向游戏根目录
- `project.work_path` 必须是相对 `setting.toml` 的路径，运行时会解析到配置文件目录下
- `system_prompt_file` 字段保存 Prompt 文件路径，运行时会读取并注入 Prompt 正文
- 修改 `setting.toml` 后必须重启进程，当前 CLI 会话不会热更新

运行时配置加载入口在 [`app/config/loaders.py`](/c:/Users/夜袭/Desktop/translation%20tools/rpg-maker-tools/app/config/loaders.py)。

## 运行

从项目根目录启动：

```bash
uv run python main.py
```

CLI 会提供以下动作：

- 构建术语表
- 翻译正文
- 重翻错误表
- 回写游戏文件
- 一键全流程
- 退出

## 运行产物

默认会在相对 `setting.toml` 解析后的 `work_path` 下生成或使用：

- `translation.db`
- 若干错误表

其中 `translation.db` 会维护：

- 主翻译表
- `glossary_role_name`
- `glossary_display_name`
- `glossary_state`
- 若干错误表

主翻译表、术语表和错误表统一由 [`TranslationDB`](/c:/Users/夜袭/Desktop/translation%20tools/rpg-maker-tools/app/database/db.py) 管理。

## 测试

运行全部测试：

```bash
uv run python -m unittest
```

当前重点覆盖：

- CLI 入口与动作分发
- 配置加载
- 术语流式构建
- 数据库读写与 Handler 编排
