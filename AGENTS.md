# 项目局部规则

本文件仅补充 `rpg-maker-tools` 仓库的局部约束；未提及的通用规范继续沿用用户级共享规则。

## 1. 运行环境

- Python 版本以 `pyproject.toml` 为准，当前要求 `>=3.14`。
- IDE、终端和脚本执行都应使用同一套 `uv` 管理的环境。
- 依赖锁文件为 `uv.lock`，不要手工维护 `requirements.txt` 一类平行依赖清单。

## 2. 包管理与命令

- 统一使用 `uv` 作为包管理器和运行入口，不使用 `pip install`、`poetry`、`pipenv`。
- 安装或同步依赖使用 `uv sync`。
- 新增、升级、删除依赖使用 `uv add`、`uv remove`，并同步更新 `uv.lock`。
- 运行脚本统一使用 `uv run python <脚本路径>`。
- 启动主程序使用 `uv run python main.py`。
- 当前仓库暂不维护自动化测试目录；需要验证时优先使用最小范围的冒烟检查。

## 3. 项目约束

- 唯一配置入口是项目根目录的 `setting.toml`，不要再引入分散配置源。
- 这是一个 CLI 工具仓库，新增功能优先接入现有 CLI 和 `TranslationHandler` 编排，不引入 GUI。
- 处理文件、导出、日志、回写逻辑时，必须保持现有输出格式和路径约定兼容。
- 涉及路径处理时优先使用 `pathlib.Path`，并保持相对 `setting.toml` 的解析语义不变。
- 注释、文档字符串、规则文档和终端沟通统一使用中文。
