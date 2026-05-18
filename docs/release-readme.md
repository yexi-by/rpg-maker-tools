# A.T.T MZ 发行版

A.T.T MZ 是面向 RPG Maker MV/MZ 日文和英文游戏的 Agent 汉化工具。发行版已经带好可执行文件、默认配置、字体、提示词和 Agent Skill，不需要用户安装 Python、Rust 或 uv。

## 第一次使用

1. 解压发行包到 `<发行版目录>`。
2. 打开 `<发行版目录>/setting.toml`，按自己的模型服务填写配置；也可以用环境变量提供模型地址和 API Key。
3. 建议先复制一份游戏目录作为汉化对象，不要直接操作唯一原版。
4. 在 PowerShell 中进入 `<发行版目录>`，运行自检：

```powershell
.\att-mz.exe --agent-mode doctor --no-check-llm --json
```

如果返回 `status=error`，先按错误提示修复配置或目录问题。

## 交给 Agent

用 Codex、Claude Code 或其他 Agent 打开 `<发行版目录>`，提交下面这段任务说明：

```text
请使用 <发行版目录>/skills/att-mz/SKILL.md 自动汉化这个 RPG Maker MV/MZ 游戏。

发行版目录：<发行版目录>
游戏目录：<游戏目录>
工作区：<工作区>

目标：
1. 确认游戏源语言，使用 `--source-language ja` 或 `--source-language en` 注册游戏，再准备工作区，分析术语、插件规则、事件指令规则、Note 标签规则和必须原样保留的游戏控制符。
2. 先小批量翻译并运行质量检查，确认没有乱码、游戏控制符风险、窗口放不下的行和明显源文残留后，再继续全量翻译。
3. 质量问题优先导出可填写的修复表，填写中文译文行后再导入。
4. 不直接修改数据库，不跳过校验，不在质量检查报告仍有错误时把译文写进游戏文件。
5. 执行写回前先向我确认；普通写回不覆盖字体。只有我单独允许时，才执行字体覆盖。
6. 写回完成后提醒我先试玩，并把漏翻、误翻、显示异常、图片文字未处理和语气不自然的地方反馈回来。
7. 收到试玩反馈后，先整理修复清单，再定位问题、修译文或补规则，重新检查并在我确认后再次写回。
```

## 常用命令

| 目标 | 命令 |
|------|------|
| 检查发行版配置 | `.\att-mz.exe --agent-mode doctor --no-check-llm --json` |
| 注册日文游戏 | `.\att-mz.exe --agent-mode add-game --path <游戏目录> --source-language ja --json` |
| 注册英文游戏 | `.\att-mz.exe --agent-mode add-game --path <游戏目录> --source-language en --json` |
| 准备 Agent 工作区 | `.\att-mz.exe --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json` |
| 小批量翻译 | `.\att-mz.exe --agent-mode translate --game <游戏标题> --max-batches 1 --json` |
| 查看质量报告 | `.\att-mz.exe --agent-mode quality-report --game <游戏标题> --json` |
| 写进游戏文件 | `.\att-mz.exe --agent-mode write-back --game <游戏标题> --json` |
| 用户允许后覆盖字体 | `.\att-mz.exe --agent-mode write-back --game <游戏标题> --confirm-font-overwrite --json` |
| 还原项目覆盖过的字体引用 | `.\att-mz.exe --agent-mode restore-font --game <游戏标题> --json` |

普通写回只更新游戏文本，不覆盖字体。字体覆盖必须由用户单独确认。

## 数据位置

- 注册游戏和译文记录保存在 `<发行版目录>/data/db`。
- 日志保存在 `<发行版目录>/logs`。
- 建议把临时分析文件放进 `<工作区>`，不要散落在游戏根目录。

更换新版发行包时，可以把旧版的 `data/db`、`setting.toml` 和必要的工作区文件迁移到新版目录。不要复制旧版 `logs` 作为运行数据。
