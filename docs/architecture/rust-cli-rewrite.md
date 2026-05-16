# Rust CLI 等价重构设计记录

## 目标

把 A.T.T MZ 交付为单文件 Rust CLI。用户可见命令、参数、JSON 报告、日志口径、数据库路径、外部 Agent 工作区协议和游戏文件处理结果必须稳定一致。最终构建产物输出到仓库根目录 `dist/`。

## 当前实现状态

- 分支：`codex/rust-cli-rewrite`。
- Rust workspace：
  - `crates/att-mz-cli`：命令行入口、日志、退出码和 JSON 错误输出。
  - `crates/att-mz-core`：配置、游戏注册表、SQLite schema、诊断报告、正文翻译、质量检查、规则导入导出、游戏文件写回和字体覆盖/还原。
  - `xtask`：官方工具链检查、下载和三端发布构建。
- 已迁移命令：`list`、`doctor`、`add-game`、`export-plugins-json`、`validate-plugin-rules`、`import-plugin-rules`、`export-event-commands-json`、`validate-event-command-rules`、`import-event-command-rules`、`export-note-tag-candidates`、`validate-note-tag-rules`、`import-note-tag-rules`、`scan-placeholder-candidates`、`build-placeholder-rules`、`validate-placeholder-rules`、`import-placeholder-rules`、`prepare-agent-workspace`、`validate-agent-workspace`、`cleanup-agent-workspace`、`translation-status`、`quality-report`、`export-pending-translations`、`export-untranslated-translations`、`export-quality-fix-template`、`import-manual-translations`、`reset-translations`、`validate-japanese-residual-rules`、`import-japanese-residual-rules`、`export-terminology`、`import-terminology`、`write-terminology`、`translate`、`write-back`、`restore-font`、`run-all`。
- `write-back`、`write-terminology` 和 `run-all` 支持用户明确确认后的字体覆盖；`restore-font` 会按原件留档把覆盖后的字体引用恢复到原字体引用。

## 构建产物

`cargo run -p xtask -- dist` 负责输出：

- `dist/att-mz-windows-x86_64/att-mz.exe`
- `dist/att-mz-linux-x86_64/att-mz`
- `dist/att-mz-macos-aarch64/att-mz`

构建脚本会通过 `rustup target add` 安装 Rust 官方 target，并在 Windows 主机缺少匹配版本 Zig 时从 Zig 官方下载索引和发布包。Linux 目标使用 `x86_64-unknown-linux-gnu`，macOS 目标使用 `aarch64-apple-darwin`；两者都通过 `cargo build --target ...` 加 Zig `cc` 链接器包装脚本构建，不依赖 cargo-zigbuild。默认 Zig 版本固定为 `0.15.2`，可通过 `ATT_MZ_ZIG_VERSION` 覆盖。若本机提供 Apple 官方 `MacOSX.sdk`，可通过 `SDKROOT` 传给 macOS 链接器；没有 SDK 时脚本会生成最小 `libiconv` 兼容静态库，让 Darwin 目标在 Windows 主机上完成链接。此时 Rust 仍会提示缺少 SDK 版本信息，但构建产物会继续输出。

单独验证某个目标时使用：

```powershell
cargo run -p xtask -- build-target x86_64-pc-windows-msvc
cargo run -p xtask -- build-target x86_64-unknown-linux-gnu
cargo run -p xtask -- build-target aarch64-apple-darwin
```

## 等价边界

- 保留 `setting.toml` 和模型环境变量 `RPG_MAKER_TOOLS_LLM_BASE_URL`、`RPG_MAKER_TOOLS_LLM_API_KEY`。
- 保留 `data/db/<游戏标题>.db`，现阶段 Rust 已创建完整兼容 schema，并已能写入 `placeholder_rules`。
- 保留 Agent 报告外层结构：`status`、`errors`、`warnings`、`summary`、`details`。
- 保留 `--json` 失败时 stdout 只输出 JSON 的约定。
- 后续修改必须优先补充 Rust 外部行为测试，避免破坏 CLI 协议。

## 交付边界

- Rust CLI 是最终交付入口；`xtask dist` 会把三端可执行文件输出到仓库根目录 `dist/`。
- 当前分支只保留 Rust 源码、配置、运行数据目录、文档、Skill、官方工具缓存和编译产物。

## 验收命令

```powershell
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
cargo doc --workspace --no-deps
cargo run -p xtask -- dist
```
