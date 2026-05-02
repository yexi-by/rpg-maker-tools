# A.T.T MZ

A.T.T MZ 是面向 RPG Maker MZ 日文游戏的自动汉化工具。新手只需要准备一个游戏目录、一个 OpenAI 兼容模型服务，然后让 Claude Code 按本项目 Skill 执行流程：分析规则、翻译、检查质量、写回游戏，最后运行汉化后的游戏。

进阶命令、Agent 协议和人工补译细节见：[进阶使用技术文档](docs/advanced-usage.md)。

## 准备内容

- Windows PowerShell。
- Python 3.14+。
- `uv`。
- Claude Code。
- OpenAI 兼容格式的模型服务地址和 API Key。
- 一个 RPG Maker MZ 游戏目录，目录里通常能看到 `Game.exe`、`data/`、`js/`。

建议先复制一份游戏目录作为汉化对象，避免直接在唯一原版上试跑。

## 1. 拉取项目

```powershell
git clone <本项目仓库地址> <项目目录>
cd <项目目录>
```

安装依赖：

```powershell
uv sync
```

生成本地配置文件：

```powershell
Copy-Item setting.example.toml setting.toml
```

## 2. 设置模型环境变量

在当前 PowerShell 会话里设置模型服务信息：

```powershell
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"
```

如果你的模型服务、模型名称或超时时间需要调整，编辑 `<项目目录>/setting.toml`。

## 3. 启动前设置 UTF-8

友情提示：游戏文本里经常同时包含日文、中文和 RPG Maker 控制符。Windows 终端编码不对时，Claude Code 可能看到乱码并误判文本。启动 Claude Code 前，先在同一个 PowerShell 会话里执行：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:LANG = "C.UTF-8"
$env:LC_ALL = "C.UTF-8"
```

然后做一次项目自检：

```powershell
uv run python main.py --agent-mode doctor --no-check-llm --json
```

如果这一步返回 `status=error`，先按错误提示修环境。

## 4. 启动 Claude Code

仍然停留在 `<项目目录>`，启动 Claude Code：

```powershell
claude --permission-mode bypassPermissions
```

进入 Claude Code 交互界面后，提交下面的任务说明。把 `<游戏目录>` 和 `<工作区>` 换成你自己的目录；`<工作区>` 建议放在游戏目录旁边或游戏目录内的临时文件夹。

```text
请使用本项目的 skills/att-mz/SKILL.md 自动汉化这个 RPG Maker MZ 游戏。

项目目录：<项目目录>
游戏目录：<游戏目录>
工作区：<工作区>

目标：
1. 从注册游戏开始，完成规则分析、正文翻译、质量检查、必要补译和最终写回。
2. 全程按 Skill 的黑盒协议工作，只通过 CLI、工作区 JSON 和游戏目录处理业务数据。
3. 启动任何翻译前，先扫描并校验占位符规则、术语表、插件规则和事件指令规则。
4. 先小批量翻译并运行 quality-report，确认没有乱码、占位符风险、超宽行和明显日文残留后，再继续全量翻译。
5. 质量问题优先用 export-quality-fix-template 导出修复骨架，再用 import-manual-translations 导入。
6. pending 需要人工补齐时，用 export-untranslated-translations 导出完整结构，只填写 translation_lines。
7. 不直接修改数据库，不跳过 validate，不在 quality-report 存在阻断问题时 write-back。
8. write-back 前先向我确认；我确认后再写回游戏目录。
9. 写回完成后告诉我如何启动汉化后的游戏。
```

Claude Code 会在过程中运行 `add-game`，并从游戏数据中识别 `<游戏标题>`。之后它会使用 `<游戏标题>` 调用后续命令。

## 5. 确认写回

当 Claude Code 告诉你 `quality-report --json` 已经没有阻断问题，并询问是否执行 `write-back` 时，确认后再让它继续。

写回完成后，游戏目录会被更新为汉化文本。工具会尽量保留原始数据备份；但新手仍建议使用复制出来的游戏目录操作。

## 6. 运行汉化游戏

写回完成后，进入 `<游戏目录>`，运行游戏启动程序：

```powershell
Start-Process -FilePath "<游戏目录>/Game.exe"
```

如果游戏启动后仍显示日文，先让 Claude Code 重新运行：

```powershell
uv run python main.py --agent-mode quality-report --game <游戏标题> --json
```

如果报告里还有 pending、日文残留、占位符风险或超宽行，按报告继续修复后再写回。

## 常见提醒

- 不要在乱码状态下修译文或规则；先重设 UTF-8，再重跑相关命令。
- 不要手工改数据库，所有补译都走 CLI 导出和导入。
- 不要跳过小批量翻译；它能提前暴露控制符和规则问题。
- 不确定某个日文专有名词是否该保留时，让 Agent 使用日文残留例外规则，不要关闭全局检查。
