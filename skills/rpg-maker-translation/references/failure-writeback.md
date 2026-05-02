# 翻译、失败处理和写回门禁

## State RT6.5：翻译运行中

先小批量运行，确认模型输出稳定。

```bash
uv run python main.py --agent-mode translate \
  --game "<游戏标题>" \
  --max-batches 1
uv run python main.py --agent-mode translation-status --game "<游戏标题>" --json
uv run python main.py --agent-mode quality-report \
  --game "<游戏标题>" \
  --output "<外部临时目录>/quality-report.json"
```

稳定后继续跑。

```bash
uv run python main.py --agent-mode translate --game "<游戏标题>"
uv run python main.py --agent-mode quality-report --game "<游戏标题>" --json
```

读取报告顺序：运行状态、成功数、待处理数、停止原因、模型运行故障、译文质量错误、模型原始返回、占位符风险、日文残留、长文本超宽、可写回数量。

## State RT8：翻译反复失败

触发条件：同类错误连续三轮没有明显下降；模型持续拒绝、空回复、非 JSON 或无关内容；某些条目反复失败；占位符含义无法判断。

行动：停止盲目重跑，生成最新 `quality-report`，按错误类型分组，摘要模型原始返回，不暴露密钥，给用户明确选项：换模型、降并发、补规则、调提示词、人工处理少量失败条目、暂缓写回。

## 少量 pending 人工补译

如果 pending 很少，Agent 可以顺手补齐，但必须走项目命令，不直接改数据库。

```bash
uv run python main.py --agent-mode export-pending-translations \
  --game "<游戏标题>" \
  --limit 5 \
  --output "<外部临时目录>/pending-translations.json" \
  --json
```

填写规则：只填写每个条目的 `translation_lines`；保留游戏原始控制符；不要保留程序占位符；`array` 行数必须和原文一致；`short_text` 必须一行；`long_text` 可以多行。

```bash
uv run python main.py --agent-mode import-manual-translations \
  --game "<游戏标题>" \
  --input "<外部临时目录>/pending-translations.json" \
  --json
uv run python main.py --agent-mode quality-report --game "<游戏标题>" --json
```

导入失败时按报告修改文件，不绕过校验。仍补不齐时暂停并反馈用户。

## State RT9：写回门禁

写回前必须满足：用户明确允许写回；最新 `quality-report` 无阻断错误；自定义占位符覆盖当前游戏候选；术语表、插件规则、事件指令规则已导入或已确认游戏本身没有对应内容；目标游戏目录可写。

```bash
uv run python main.py --agent-mode quality-report --game "<游戏标题>" --json
uv run python main.py --agent-mode write-back --game "<游戏标题>"
uv run python main.py --agent-mode doctor --game "<游戏标题>" --no-check-llm --json
```

不满足门禁时，停止并告诉用户差哪一项。

## State RT10：清理与交付

优先用 manifest 清理项目生成的临时工作区。

```bash
uv run python main.py --agent-mode cleanup-agent-workspace \
  --workspace "<外部临时目录>/agent-workspace" \
  --json
```

如果需要手工清理，只删除 `<外部临时目录>` 下的 `name-context`、`plugins.json`、`plugin-rules.json`、`event-commands.json`、`event-command-rules.json`、占位符草稿和 pending 补译文件，不要删除游戏目录内容。

交付报告包含：注册的游戏标题、已执行阶段、术语表/插件规则/事件指令规则/占位符规则检查结论、翻译数量、失败数量、风险数量、是否写回、质量报告位置、建议用户实机抽查的重点区域。
