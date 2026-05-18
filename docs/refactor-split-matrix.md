# 大文件拆分功能对照矩阵

本文档记录本次等价拆分中已经移动的功能边界，供后续继续拆分时核对外部行为是否保持一致。

| 原功能点 | 原位置 | 新位置 | 测试覆盖 | 等价结论 |
| --- | --- | --- | --- | --- |
| 长文本译文行数适配与宽度兜底 | `app.translation.line_wrap` | `app.rmmz.text_layout.service`、`split`、`width`、`wrapping`、`protected` | `tests/test_translation_line_alignment.py` | 公共函数签名保持一致，调用方改为依赖 RMMZ 文本布局包 |
| 写回阶段文本布局依赖 | `app.rmmz.write_back` 直接依赖 `app.translation.line_wrap` | `app.rmmz.write_back` 依赖 `app.rmmz.text_layout` | `tests/test_rmmz_loader_extraction_writeback.py` | 消除了 RMMZ 层对 translation 层的反向依赖 |
| 统一文本范围模型 | `app.text_scope` 单文件 | `app.text_scope.models` | `tests/test_agent_toolkit.py` | JSON 字段和用户可见原因保持不变 |
| 统一文本范围构建 | `app.text_scope.TextScopeService` | `app.text_scope.builder.TextScopeService`，包入口继续导出 | `tests/test_agent_toolkit.py`、`tests/test_cli_json_output.py` | `TranslationHandler` 与 `AgentToolkitService` 仍通过同一服务读取范围 |
| 插件规则新鲜度检查 | `app.text_scope.read_fresh_plugin_text_rules` | `app.text_scope.plugin_rules.read_fresh_plugin_text_rules`，包入口继续导出 | `tests/test_agent_toolkit.py` | 过期规则原因和返回结构保持一致 |
| 写入可行性探针 | `app.text_scope` 内部函数 | `app.text_scope.write_probe` | `tests/test_agent_toolkit.py` | 探针失败策略保持一致，测试 monkeypatch 定位到新子模块 |
| 外部规则命中展开 | `app.text_scope` 内部函数 | `app.text_scope.rule_hits` | `tests/test_agent_toolkit.py` | 插件、事件指令、Note 标签命中展开规则保持一致 |
| 正文翻译运行控制参数 | `app.application.handler` | `app.application.use_cases.translation_run`，`handler` 继续导出 | `tests/test_cli_json_output.py` | CLI 参数构造仍可从原入口导入 |
| 正文翻译批次、去重、缓存展开 | `TranslationHandler` 静态方法 | `app.application.use_cases.translation_run` 纯函数 | `tests/test_translation_cache_context.py`、`tests/test_cli_json_output.py` | 翻译编排入口不变，纯逻辑脱离总编排类 |
| 数据库路径解析 | `app.persistence.repository` | `app.persistence.paths`，`repository` 与 `persistence` 继续导出 | `tests/test_persistence.py`、`tests/test_runtime_paths.py` | 数据库目录、文件名校验和默认路径行为保持一致 |
| 数据库记录模型 | `app.persistence.repository` | `app.persistence.records`，`repository` 与 `persistence` 继续导出 | `tests/test_persistence.py` | 记录字段和读取失败策略保持一致 |
| data 文本写回入口 | `app.rmmz.write_back.py` | `app.rmmz.write_back.service`，包入口导出 `write_data_text` | `tests/test_rmmz_loader_extraction_writeback.py` | 调用路径保持 `app.rmmz.write_back import write_data_text` |
| 字体替换入口 | `app.application.font_replacement.py` | `app.application.font_replacement.service`，包入口导出公共函数 | `tests/test_rmmz_loader_extraction_writeback.py`、`tests/test_runtime_paths.py` | 调用路径保持 `app.application.font_replacement import ...` |
| data 文本写回内部能力 | `app.rmmz.write_back.service` | `app.rmmz.write_back.commands`、`locators`、`note_tags`、`preparation`、`standard` | `tests/test_rmmz_loader_extraction_writeback.py` | 入口分发、事件指令写入、Note 标签写入、标准 data 字段和写入前文本整理分离，写入结果保持一致 |
| 字体替换内部能力 | `app.application.font_replacement.service` | `constants`、`models`、`files`、`css`、`native_changes`、`references`、`restore` | `tests/test_rmmz_loader_extraction_writeback.py`、`tests/test_runtime_paths.py` | 字体复制、CSS 替换、Rust 扫描结果应用、引用替换算法和原件留档还原分离，公共包入口保持一致 |
| 数据库会话方法 | `TargetGameSession` 内部方法 | `app.persistence.translation_records`、`rule_records`、`terminology_records`、`font_records`、`run_records` | `tests/test_persistence.py`、`tests/test_runtime_paths.py` | `TargetGameSession` 继续作为对外入口，表域读写方法按记录类型拆成 mixin，数据库语义保持一致 |
| Agent 工具箱命令族 | `AgentToolkitService` 大类方法 | `app.agent_toolkit.services.doctor`、`placeholder_rules`、`coverage`、`quality`、`manual_translation`、`workspace`、`rule_validation`、`feedback`、`core` | `tests/test_agent_toolkit.py`、`tests/test_cli_json_output.py` | `AgentToolkitService` 保留薄门面，命令族按职责拆分；原测试 monkeypatch 的服务级原生质检替换点继续可用 |
| Rust 质量检查子域 | `rust_app/native_core/quality.rs` | `rust_app/native_core/quality/mod.rs`、`residual.rs`、`structure.rs`、`placeholder.rs`、`line_width.rs` | `cargo test`、`cargo clippy --all-targets -- -D warnings` | PyO3 暴露入口不变，源文残留、文本结构、占位符和行宽检查拆成独立 Rust 子模块 |

## 后续待拆

- 当前矩阵列出的剩余拆分项已处理完成。
- `app.agent_toolkit.services.common` 仍集中承载跨命令族共享的纯辅助函数，后续若继续压低单文件行数，可在不改变服务边界的前提下按质量报告、工作区、反馈反查和规则草稿再做二级拆分。
