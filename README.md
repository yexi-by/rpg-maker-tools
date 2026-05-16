# A.T.T MZ

面向 RPG Maker MZ 日文游戏的自动汉化与迭代修复 CLI。当前交付形态是单文件 Rust 可执行程序，三端编译产物输出在仓库根目录 `dist/`。

进阶命令、Agent 协议和工作区细节见 [进阶使用技术文档](docs/advanced-usage.md)。

## 你需要准备

| 项目 | 说明 |
|------|------|
| A.T.T MZ CLI | 从 `dist/` 中选择当前系统对应的可执行文件 |
| 模型服务 | OpenAI 兼容格式的 API 地址与 Key |
| 游戏目录 | RPG Maker MZ 游戏，目录内能看到 `Game.exe`、`data/`、`js/` |
| AI Agent | Claude Code / Codex 等能读取项目文件并运行终端命令的工具 |

> 建议先复制一份游戏目录作为汉化对象，不要直接在唯一原版上操作。

## 可执行文件位置

```text
dist/att-mz-windows-x86_64/att-mz.exe
dist/att-mz-linux-x86_64/att-mz
dist/att-mz-macos-aarch64/att-mz
```

文档中的 `<att-mz>` 表示当前系统对应的可执行文件路径。Windows 示例：

```powershell
$attMz = "<项目目录>/dist/att-mz-windows-x86_64/att-mz.exe"
& $attMz --help
```

开发者也可以从源码运行：

```powershell
cargo run -p att-mz -- --help
```

## 快速开始

```powershell
# 1. 进入项目目录
cd <项目目录>

# 2. 生成本地配置文件
Copy-Item setting.example.toml setting.toml

# 3. 设置模型环境变量
$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"

# 4. 设置 UTF-8 编码（Windows 终端建议执行）
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# 5. 自检环境
& $attMz doctor --no-check-llm --json

# 6. 注册游戏
& $attMz add-game --path <游戏目录> --json
```

如果 `doctor` 返回 `status=error`，按错误提示修环境后再继续。

## 首次交给 Agent 的任务说明

用你熟悉的 Agent 打开 `<项目目录>` 后，提交这份任务说明：

```text
请使用 <项目目录>/skills/att-mz/SKILL.md 自动汉化这个 RPG Maker MZ 游戏。

项目目录：<项目目录>
CLI：<att-mz>
游戏目录：<游戏目录>
工作区：<工作区>

目标：
1. 从注册游戏开始，完成规则分析、正文翻译、质量检查、必要补译、第一版写回和试玩反馈迭代。
2. 全程按 Skill 里写明的输入、输出和校验步骤工作，只通过 CLI、工作区 JSON 和游戏目录处理业务数据。
3. 启动任何翻译前，先完成术语表、插件规则、事件指令规则、Note 标签规则和占位符规则检查。
4. 先小批量翻译并运行 quality-report，确认没有乱码、游戏控制符风险、超宽行和明显日文残留后，再继续全量翻译。
5. 质量问题优先用 export-quality-fix-template 导出可填写的修复表，再用 import-manual-translations 导入。
6. 如果还有没成功保存译文的文本，用 export-untranslated-translations 导出完整译文表，只填写中文译文行。
7. 不直接修改数据库，不跳过 validate，不在 quality-report 报告错误时把译文写进游戏文件。
8. 执行 write-back 前先向我确认；我确认后再写回游戏目录。
9. 除非我单独明确允许覆盖字体，否则不要添加 --confirm-font-overwrite。
10. 写回完成后告诉我如何启动汉化后的游戏，并提醒我先实际游玩，把漏翻、误翻、显示异常和语气不自然的地方反馈回来。
```

## 典型流程

```powershell
& $attMz doctor --no-check-llm --json
& $attMz add-game --path <游戏目录> --json
& $attMz doctor --game <游戏标题> --no-check-llm --json
& $attMz prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json

& $attMz import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json
& $attMz validate-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
& $attMz import-plugin-rules --game <游戏标题> --input <工作区>/plugin-rules.json --json
& $attMz validate-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
& $attMz import-event-command-rules --game <游戏标题> --input <工作区>/event-command-rules.json --json
& $attMz validate-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json
& $attMz import-note-tag-rules --game <游戏标题> --input <工作区>/note-tag-rules.json --json

& $attMz build-placeholder-rules --game <游戏标题> --output <工作区>/placeholder-rules.json --json
& $attMz validate-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json
& $attMz scan-placeholder-candidates --game <游戏标题> --input <工作区>/placeholder-rules.json --json
& $attMz import-placeholder-rules --game <游戏标题> --input <工作区>/placeholder-rules.json --json

& $attMz translate --game <游戏标题> --max-batches 1 --json
& $attMz quality-report --game <游戏标题> --json
& $attMz translate --game <游戏标题> --json
& $attMz quality-report --game <游戏标题> --json
& $attMz write-back --game <游戏标题> --json
```

## 运行汉化游戏

写回完成后，进入 `<游戏目录>`，启动游戏即可：

```powershell
Start-Process -FilePath "<游戏目录>/Game.exe"
```

认真试玩一段流程，重点看对话、菜单、物品技能说明、任务提示、插件界面、按钮文字和窗口换行。遇到漏翻、误翻、称呼不统一、显示不下、语气不自然或仍有日文的地方，把截图、场景、当前译文和你期望的表达反馈给 Agent。

如果游戏启动后仍显示日文，先重新运行：

```powershell
& $attMz quality-report --game <游戏标题> --json
```

## 字体覆盖和还原

普通写回只更新游戏文本，不覆盖字体引用。只有你明确允许时，才使用：

```powershell
& $attMz write-back --game <游戏标题> --confirm-font-overwrite --json
```

如果曾确认覆盖字体、现在想按原件还原：

```powershell
& $attMz restore-font --game <游戏标题> --json
```

字体还原会对比 `data/*.json` 与 `data_origin/*.json`、`js/plugins.js` 与 `js/plugins_origin.js`，只把候选覆盖字体名替回原件里的实际旧字体引用，不回滚已写入的译文。

## 构建

```powershell
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
cargo doc --workspace --no-deps
cargo run -p xtask -- dist
```

`xtask dist` 会准备 Rust target 和 Zig 官方工具，并输出 Windows、Linux、macOS ARM64 三端可执行文件。若 Windows 主机没有 Apple SDK，macOS 构建可能提示缺少 SDK 版本信息，但仍会输出可执行文件。

## 常见提醒

- 不要在乱码状态下修译文或规则，先重设 UTF-8，再重跑相关命令。
- 不要手工改数据库，所有手动填写译文表都走 CLI 导出和导入。
- 不要跳过小批量翻译，它能提前暴露游戏控制符和规则问题。
- 不确定某个日文专有名词是否该保留时，使用日文残留例外规则，不要关闭全局检查。
- 不要把第一版写回当成最终完成，高质量汉化需要试玩反馈和迭代修复。
