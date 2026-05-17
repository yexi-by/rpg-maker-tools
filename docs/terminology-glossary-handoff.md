# 术语表职责拆分交接文档

本文原用于新会话继续完成术语表职责拆分任务。本轮已经在现有改动基础上补齐 CLI、工作区、Skill、README、高级用法文档和协议测试，并通过窄范围验收；最终仍以全量 `uv run basedpyright` 和 `uv run pytest` 为交付准线。

## 目标

- 把字段译名表和正文术语表分开。
- 字段译名表负责精确写回游戏字段，例如地图显示名、数据库名称、系统类型，以及 MZ 标准 `101.parameters[4]` 名字框。
- 正文术语表负责正文翻译提示词命中，只把 Agent 规范化后的真正术语发给模型。
- 项目继续正常翻译和写回 101 等文本型字段；如果个别游戏插件依赖这些字符串导致语音、立绘、状态列表等功能失效，由 Skill 引导 Agent 在游戏目录做临时兼容补丁，不把例外游戏逻辑写进项目核心。

## 当前已做

- 新增了 `TerminologyGlossary` 模型，目标格式为：
  ```json
  {
    "terms": {
      "小明": "小明"
    }
  }
  ```
- `app/terminology/files.py` 已开始从 `terms.json` 改成导出 `field-terms.json` 和 `glossary.json`。
- `app/persistence/sql.py` 已新增正文术语表 `terminology_glossary_terms`。
- `app/persistence/repository.py` 已新增正文术语表读写方法。
- `app/application/handler.py` 和 `app/cli.py` 已开始要求 `import-terminology` 同时接收 `--input` 和 `--glossary-input`。
- `app/terminology/prompt.py` 已改为只从 `TerminologyGlossary.terms` 构建正文提示词索引。
- `tests/test_terminology.py`、`tests/test_persistence.py`、`tests/test_agent_toolkit.py` 已开始补对应测试，但还没跑通。

## 本轮处理清单

1. 先跑窄范围检查，拿真实错误：
   ```bash
   uv run basedpyright
   uv run pytest tests/test_terminology.py tests/test_persistence.py tests/test_agent_toolkit.py tests/test_cli_json_output.py
   ```

2. 修 CLI 参数测试：
   - `tests/test_cli_json_output.py` 已改成传 `terminology/field-terms.json` 和 `--glossary-input terminology/glossary.json`。
   - 已增加对 `glossary_input` 的断言。

3. 修所有旧文件名残留：
   - 继续搜索：
     ```bash
     rg -n "terms\\.json|terms_path|TerminologyPromptIndex\\.from_registry|load_terminology_registry\\(terms_path" app tests docs README.md skills
     ```
   - 代码和测试里必须改成 `field-terms.json` / `glossary.json`。
   - 文档和 Skill 里必须明确字段译名表不是正文术语表。

4. 修 Skill 和协议测试：
   - `skills/att-mz/SKILL.md` 与 `tests/test_skill_protocol.py` 已同步为：
     - `terminology/field-terms.json`：字段译名表，只负责写回。
     - `terminology/glossary.json`：正文术语表，只负责提示词命中。
     - 子代理产出候选后，主代理必须亲自把 `/c小明`、`"小明"`、`◆角色名ｔ` 等字段形式理解并整理为真正正文术语，只写入正文术语表的 `terms`。

5. 修 README 和高级用法文档：
   - `README.md` 和 `docs/advanced-usage.md` 仍有旧命令。
   - 新命令应写成：
     ```bash
     uv run python main.py --agent-mode import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json
     ```

6. 完善 Agent 工作区校验：
   - `prepare-agent-workspace` 必须生成两个文件，并在 manifest 里列出。
   - `validate-agent-workspace` 必须同时校验字段译名表和正文术语表。
   - `doctor` 或工作区摘要里应能反映两者是否已导入。

7. 重新检查提示词命中逻辑：
   - 正文出现规范术语本体时，应注入规范术语。
   - 地图名、角色名、数据库条目上下文也应通过正文术语表命中。
   - `source == translated` 的术语不能再被当作噪音过滤，因为人名可能中日同形或用户希望固定不变。

8. 明确插件依赖文本的 Skill 策略：
   - 项目层不阻止 MZ 标准名字框汉化。
   - 如果插件依赖原始字符串触发功能，Agent 应优先在游戏目录新增或修改插件，建立“中文显示值 -> 原始触发值”的兼容映射。
   - 兼容补丁只属于当前游戏临时处理，不写进项目通用逻辑。

## 可能踩坑

- 当前工作树还有别的历史改动，不全是这次术语拆分产生的。不要用 `git reset --hard` 或批量回退。
- `terminology_terms` 表名暂时保留，用来存字段译名表；不要强行重命名旧表，避免扩大迁移面。
- 新增正文术语表为空也是合法状态，表示用户确认没有可用正文术语；但文件结构必须完整。
- 文档示例要使用 `<游戏标题>`、`<工作区>` 这类占位符，不要写真实本机路径或真实样本名。

## 完成标准

- `rg` 搜不到仍在表达旧单文件术语表语义的主流程说明。
- `prepare-agent-workspace` 生成 `terminology/field-terms.json` 和 `terminology/glossary.json`。
- `import-terminology` 必须同时导入字段译名表和正文术语表。
- 正文提示词只从正文术语表注入 `[[术语表]]`。
- 字段译名表仍能写回地图显示名、数据库名称、系统类型，以及 MZ 标准名字框。
- Skill 明确要求 Agent 处理插件依赖文本型字符串的兼容补丁，不让项目核心逻辑背锅。
- 验收命令全部通过：
  ```bash
  uv run basedpyright
  uv run pytest
  ```

## 新会话建议开场

可以直接把下面这段交给新会话：

```text
继续完成 A.T.T MZ 术语表职责拆分。先读取 docs/terminology-glossary-handoff.md，不要重置工作树；当前已有部分实现。请先运行 rg 检查旧 terms.json 残留，再跑 basedpyright 和相关 pytest，按文档补齐 CLI、工作区、Skill、README、协议测试，最后跑 uv run basedpyright 和 uv run pytest。
```
