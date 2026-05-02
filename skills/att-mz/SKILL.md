---
name: att-mz
description: Use this skill when an agent operates this RPG Maker MZ translation toolkit end-to-end: project discovery, game validation, placeholder rules, name context, plugin rules, event command rules, translation loops, quality reports, failure triage, manual completion, and write-back gating.
---

# A.T.T MZ Skill

本 Skill 是执行协议。CLI 负责确定性工作；Agent 负责语义判断；用户负责提供输入和写回许可。不要把它当背景文章读完再行动，按阶段加载 reference。

## 先读这个

- 命令统一用：`uv run python main.py --agent-mode <命令> ...`。
- 需要机器读取结果时加 `--json` 或 `--output`。
- 全局参数必须放在子命令前，例如 `main.py --agent-mode doctor ...`。
- API Key 和模型地址只通过环境变量或本地配置读取，不写进 CLI 参数、临时文件或报告。
- 临时工作区必须放在用户允许的位置，不放进游戏根目录。
- 大型 JSON 先看 manifest、summary、校验报告，再搜索和分段读取。
- Agent 中间可以用自己的办法处理导出文件；项目只要求导出和导入必须走本项目 CLI。

## 不可跳过的门禁

- 未确认 `<项目目录>`、`<游戏根目录>`、`<外部临时目录>`、模型配置和写回许可前，不启动消耗模型额度的命令。
- 没有完成 `doctor`、`add-game`、`prepare-agent-workspace` 和占位符检查前，不执行 `translate`。
- name-context、plugins、event-commands 三类分析必须导出、分析、验收。游戏本身没有对应内容时允许为空，但必须先确认。
- 占位符规则优先用文件：`validate-placeholder-rules --input <规则文件>`，`import-placeholder-rules --input <规则文件>`。
- 如果使用子代理并行分析，主 Agent 必须等待所有子代理完成、读取结果、执行校验，再导入数据库。
- `quality-report` 有阻断错误时，不执行 `write-back`。
- 如果 pending 很少，先用 `export-pending-translations` 导出并人工补齐，再用 `import-manual-translations` 入库；补不了再停下反馈。
- 找不到项目、游戏不可识别、data JSON 不可解析、模型不可用、连续多轮失败时，暂停并向用户报告。

## 阶段索引

- 项目发现、环境检查、游戏注册、模型检查：读 `references/workflow.md`。
- Agent 工作区、占位符、术语表、插件规则、事件指令规则、子代理并行：读 `references/rules-and-workspace.md`。
- 翻译循环、少量失败项人工补译、质量报告、写回门禁、清理和交付：读 `references/failure-writeback.md`。

## 最短主流程

1. `doctor --no-check-llm --json` 检查项目。
2. `add-game --path <游戏根目录> --json` 注册游戏，记录返回的 `<游戏标题>`。
3. `doctor --game <游戏标题> --json` 检查目标游戏和模型。
4. `prepare-agent-workspace --game <游戏标题> --output-dir <外部临时目录>/agent-workspace --json` 导出分析工作区。
5. 分析并导入占位符、术语表、插件规则、事件指令规则。
6. `validate-agent-workspace --game <游戏标题> --workspace <外部临时目录>/agent-workspace --json` 验收工作区。
7. 小批量 `translate --game <游戏标题> --max-batches 1`，再看 `quality-report`。
8. 稳定后续跑 `translate --game <游戏标题>`，直到 pending 为 0 或只剩少量可人工补齐。
9. 少量 pending 用人工补译命令补齐。
10. `quality-report --game <游戏标题> --json` 通过并获得用户许可后，执行 `write-back --game <游戏标题>`。

## 阻断反馈模板

```text
当前阶段：<阶段>
已完成：<已执行命令或产物>
阻断原因：<一句话说清楚>
证据：<doctor 或 quality-report 的关键数量/错误类型>
我建议：
1. <下一步选项>
2. <下一步选项>
需要你提供：<缺失输入或确认项>
```

## 反模式

- 看到报错就无限重跑。
- 没有扫描自定义控制符就开始翻译。
- 占位符规则只放临时文件，不导入当前游戏数据库。
- name-context、plugins、event-commands 未分析就开始翻译。
- 为了让计数非空而编造不存在的术语或规则。
- 把没看懂结构当成“游戏没有对应内容”。
- 子代理还没结束就导入半成品或启动翻译。
- `quality-report` 有阻断错误仍写回。

