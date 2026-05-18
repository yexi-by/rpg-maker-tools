# A.T.T MZ

A.T.T MZ 是面向 RPG Maker MV/MZ 日文和英文游戏的 Agent 汉化工具。它负责注册游戏、分析 RPG Maker 数据、保护必须原样保留的游戏控制符、调用模型翻译、检查译文质量，并把通过检查的译文写回游戏目录。

项目优先生成第一版可试玩汉化结果，再通过试玩反馈持续修漏翻、误翻、显示异常和语气问题。

> 推荐用 Codex 搭配 GPT-5.5 这类能力强的模型。强模型更容易理解语境、术语和角色语气，强 Agent 更能稳定执行规则分析、质量检查和迭代修复。两者配合通常能提高第一次翻译完成度，也会减少后续返工次数。

进阶命令、Agent 协议和工作区细节见 [进阶使用技术文档](docs/advanced-usage.md)。数据库结构见 [数据库 Wiki](docs/database-wiki.md)。

## 适合什么

| 场景 | 支持情况 |
|------|----------|
| RPG Maker MV/MZ 标准 JSON 游戏 | 支持 |
| 根目录存在 `data/js` 的工程布局 | 支持 |
| 外层有 `Game.exe`、真实内容在 `www/data` 和 `www/js` 的 MV 部署布局 | 支持 |
| 事件文本、选项、滚动文本、数据库字段、插件文本、Note 标签文本 | 支持规则化提取和写回 |
| 源语言 | 支持 `ja -> zh-Hans` 和 `en -> zh-Hans`；注册游戏时必须显式传入 `--source-language ja` 或 `--source-language en` |
| MV 插件命令 `356`、MZ 插件命令 `357` | 按引擎自动选择默认分析编码 |
| 字体覆盖与还原 | 支持复制字体、同步字体引用、备份和还原 `gamefont.css` |
| 图片汉化 | <small>未来方向，未完成；适合作为独立 Agent 任务处理</small> |
| VX Ace、XP、加密资源解密、非 RPG Maker 引擎 | 不支持 |

## 你需要准备

| 项目 | 说明 |
|------|------|
| Python | 3.14 或更高版本 |
| uv | Python 依赖与环境管理 |
| Rust | MSVC 工具链，建议执行 `rustup default stable-msvc` |
| VS Build Tools | 安装“使用 C++ 的桌面开发”组件 |
| 模型服务 | OpenAI 兼容格式的 API 地址与 Key |
| AI Agent | Codex、Claude Code 等能读取项目文件并运行终端命令的工具 |
| 游戏目录 | RPG Maker MV/MZ 游戏目录 |

建议先复制一份游戏目录作为汉化对象，不要直接在唯一原版上操作。

## 快速开始

```powershell
git clone <项目仓库地址> <项目目录>
cd <项目目录>

uv sync
uv run maturin develop --release

Copy-Item setting.example.toml setting.toml

$env:RPG_MAKER_TOOLS_LLM_BASE_URL = "<模型服务地址>"
$env:RPG_MAKER_TOOLS_LLM_API_KEY = "<API_KEY>"

$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:LANG = "C.UTF-8"
$env:LC_ALL = "C.UTF-8"

uv run python main.py --agent-mode doctor --no-check-llm --json
```

如果自检返回 `status=error`，按错误提示修环境后再继续。

## 交给 Agent

用你熟悉的 Agent 打开 `<项目目录>`，提交下面这段任务说明：

```text
请使用 <项目目录>/skills/att-mz/SKILL.md 自动汉化这个 RPG Maker MV/MZ 游戏。

项目目录：<项目目录>
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

这段说明的重点是让 Agent 先把规则和质量检查做扎实，再开始消耗模型额度翻译正文。游戏里的自定义控制符、语音触发标记、插件名牌和特殊脚本标记必须先保护好，否则后续返工会很痛。

## 工作流

A.T.T MZ 推荐按这个顺序跑：

1. `doctor` 检查环境。
2. `add-game` 注册游戏并识别 MV/MZ 布局。
3. `prepare-agent-workspace` 生成给 Agent 分析的工作区。
4. Agent 审查术语、插件文本、事件指令文本和 Note 标签文本。
5. Agent 校验并导入必须原样保留的游戏控制符规则。
6. `translate --max-batches 1` 做小批量翻译。
7. `quality-report` 检查译文质量。
8. 修复质量问题后继续全量 `translate`。
9. 写回前再次运行 `quality-report`。
10. 用户确认后执行 `write-back`，生成第一版可试玩汉化结果。
11. 根据试玩反馈继续修复并再次写回。

## 常用命令

| 目标 | 命令 |
|------|------|
| 检查环境 | `uv run python main.py --agent-mode doctor --no-check-llm --json` |
| 注册日文游戏 | `uv run python main.py --agent-mode add-game --path <游戏目录> --source-language ja --json` |
| 注册英文游戏 | `uv run python main.py --agent-mode add-game --path <游戏目录> --source-language en --json` |
| 准备 Agent 工作区 | `uv run python main.py --agent-mode prepare-agent-workspace --game <游戏标题> --output-dir <工作区> --json` |
| 小批量翻译 | `uv run python main.py --agent-mode translate --game <游戏标题> --max-batches 1 --json` |
| 查看质量报告 | `uv run python main.py --agent-mode quality-report --game <游戏标题> --json` |
| 写进游戏文件 | `uv run python main.py --agent-mode write-back --game <游戏标题> --json` |
| 用户允许后覆盖字体 | `uv run python main.py --agent-mode write-back --game <游戏标题> --confirm-font-overwrite --json` |
| 还原项目覆盖过的字体引用 | `uv run python main.py --agent-mode restore-font --game <游戏标题> --json` |

普通写回只更新游戏文本，不覆盖字体。字体覆盖必须由用户单独确认。

## 字体处理

如果用户允许字体覆盖，项目会把配置中的候选字体复制到真实游戏内容目录的 `fonts` 目录，并同步替换游戏数据和插件配置里的旧字体引用。

对于存在 `fonts/gamefont.css` 的 MV/MZ 游戏，项目还会先备份为 `fonts/gamefont_origin.css`，再把 `GameFont`、`GameFont2`、`GameFont3` 等字体族入口指向候选字体。这样可以避免“字体文件已经复制，但游戏仍然读取旧日文字体”的半生效状态。

需要撤回项目覆盖过的字体引用时，执行：

```powershell
uv run python main.py --agent-mode restore-font --game <游戏标题> --json
```

还原流程只把候选覆盖字体名替回原件里的旧字体引用，不回滚已写入的译文。

## 图片汉化

<small>未来方向，未完成；当前不会随正文翻译自动执行。</small>

图片汉化适合做成独立的自主型 Agent 任务，和正文翻译、字体替换分阶段执行。规划目标是让 Agent 扫描 `<游戏内容目录>/img` 等资源目录，先筛选出可能含有文字的图片，再判断它们属于标题、按钮、菜单背景、教程提示、立绘差分、CG 背景还是纯装饰资源。

这类任务必须可回滚：写回前先保存原图；改图时保持原尺寸、透明通道、文件名、相对路径和图片格式；写回后通过抽样预览或运行游戏检查标题、菜单、按钮、提示图和剧情画面是否显示正常。无法判断文字含义、图片用途或修图风险时，Agent 应先列入待确认清单，不能直接覆盖游戏图片。

理想流程是：扫描图片资产并生成候选清单，按“有文字且影响玩家理解”“有文字但不建议改”“无文字或无需处理”分类，优先处理 UI、标题、教程提示和按钮类图片；修图结果通过人工或模型复核后，再写回游戏目录。

## 试玩反馈

写回完成后，进入 `<游戏目录>` 启动游戏：

```powershell
Start-Process -FilePath "<游戏目录>/Game.exe"
```

请认真试玩一段流程，重点看对话、菜单、物品技能说明、任务提示、插件界面、按钮文字、窗口换行、字体显示和图片文字。遇到漏翻、误翻、称呼不统一、显示不下、语气不自然或仍有源文残留的地方，把截图、场景、当前译文和你期望的表达反馈给 Agent。

如果游戏启动后仍显示大量原始源语言文本，先重新运行质量检查：

```powershell
uv run python main.py --agent-mode quality-report --game <游戏标题> --json
```

如果报告里还有没成功保存译文的文本、源文残留、游戏控制符风险或窗口放不下的行，按报告继续修复后再写回。

## 开发检查

修改 Python 代码后：

```powershell
uv run basedpyright
uv run pytest
```

修改 Rust 扩展后追加：

```powershell
cargo fmt -- --check
cargo clippy --all-targets -- -D warnings
cargo test
uv run maturin develop --release
```

当前流程不支持模型流式返回。配置 `stream=true` 或 `stream_options` 会直接报错，避免用户误以为参数已经生效。
