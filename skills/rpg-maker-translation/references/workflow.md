# 项目、游戏和模型流程

## State RT0：项目未知

目标是确认当前目录就是工具项目。

```bash
uv sync
uv run python main.py --help
uv run python main.py --agent-mode doctor --no-check-llm --json
```

处理规则：

- `uv` 不可用：提示用户修复环境。
- Python 版本不满足：提示用户切换到项目要求版本。
- 配置不可解析：修复本地配置或报告给用户。
- `setting.toml` 不存在且有示例配置：复制示例为本地配置，但不要提交本地密钥。
- 当前 Agent 无命令执行能力：不要假装已经执行，给用户可手动运行的命令和检查清单。

## State RT2：游戏候选未确认

先确认游戏目录至少包含 `data/`、核心 data JSON，通常还包含 `js/plugins.js`。

```bash
uv run python main.py --agent-mode add-game --path "<游戏根目录>" --json
uv run python main.py --agent-mode doctor --game "<游戏标题>" --no-check-llm --json
```

处理规则：

- 缺 `data/`：目标不是可处理的游戏根目录。
- 核心 JSON 缺失：需要标准 RPG Maker MZ data 文件。
- data JSON 被加密或不可解析：需要先取得可解析 data JSON。
- 只有图片、音频等资源加密，但 data JSON 可读：可以继续。
- 看起来不是 RPG Maker MZ：停止并请用户确认版本和目录。

## State RT3：模型未配置或不可用

```bash
uv run python main.py --agent-mode doctor --game "<游戏标题>" --json
```

处理规则：

- 认证失败：报告认证问题，不打印密钥。
- 模型不存在或服务拒绝：建议用户更换模型或服务。
- 内容审查拒绝：不要盲目重试同一模型，建议换可处理当前文本的模型。
- 网络、限流、超时：建议降低并发、调整 RPM、增加超时或稍后续跑。

无模型时仍可执行：`doctor --no-check-llm`、`add-game`、`prepare-agent-workspace`、`scan-placeholder-candidates`、`build-placeholder-rules`、`validate-*`、`export-*`、`import-*`、`quality-report`。

## State RT7：二次翻译与增量翻译

不把二次翻译当成全新游戏无脑重跑。先诊断已有数据库和缓存。

```bash
uv run python main.py --agent-mode doctor --game "<游戏标题>" --no-check-llm --json
uv run python main.py --agent-mode quality-report --game "<游戏标题>" --json
uv run python main.py --agent-mode translation-status --game "<游戏标题>" --json
uv run python main.py --agent-mode scan-placeholder-candidates --game "<游戏标题>" --json
```

规则：

- 已有译文缓存会被复用；`translate` 处理当前提取范围内未成功入库的条目。
- 二次写回会直接替换当前激活文件；首次写回创建的 `data_origin` 不重复改写。
- 游戏文件、插件配置或事件指令结构变化时，重新导出对应工作区并分析。
- 出现新的未覆盖控制符时，先补占位符规则。
- 当前规则和质量报告通过时，可以直接续跑或写回。
