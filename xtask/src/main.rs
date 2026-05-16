//! 项目维护任务入口。
//!
//! `xtask dist` 负责准备官方工具链、构建三端 Rust CLI，并把最终可执行文件复制到
//! 仓库根目录的 `dist/`。脚本只下载 Rust target 和 Zig 官方发布物；如果本机提供
//! Apple 官方 MacOSX.sdk，则会通过 `SDKROOT` 传给 macOS 链接器。没有 SDK 时，
//! 脚本会生成最小 `libiconv` 兼容库，补齐 Rust Darwin 链接元数据需要的系统库名。

use std::env;
use std::ffi::OsStr;
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result, anyhow, bail};
use serde_json::Value;
use sha2::{Digest, Sha256};

const WINDOWS_TARGET: &str = "x86_64-pc-windows-msvc";
const LINUX_TARGET: &str = "x86_64-unknown-linux-gnu";
const MACOS_TARGET: &str = "aarch64-apple-darwin";
const ZIG_INDEX_URL: &str = "https://ziglang.org/download/index.json";
const DEFAULT_ZIG_VERSION: &str = "0.15.2";
const MACOS_ICONV_STUB_SOURCE: &str = r#"
#include <stddef.h>

void *iconv_open(const char *tocode, const char *fromcode) {
    (void)tocode;
    (void)fromcode;
    return (void *)-1;
}

size_t iconv(void *cd, char **inbuf, size_t *inbytesleft, char **outbuf, size_t *outbytesleft) {
    (void)cd;
    (void)inbuf;
    (void)inbytesleft;
    (void)outbuf;
    (void)outbytesleft;
    return (size_t)-1;
}

int iconv_close(void *cd) {
    (void)cd;
    return 0;
}
"#;

const DIST_TARGETS: &[DistTarget] = &[
    DistTarget {
        rust_target: WINDOWS_TARGET,
        binary_name: "att-mz.exe",
        output_dir_name: "att-mz-windows-x86_64",
    },
    DistTarget {
        rust_target: LINUX_TARGET,
        binary_name: "att-mz",
        output_dir_name: "att-mz-linux-x86_64",
    },
    DistTarget {
        rust_target: MACOS_TARGET,
        binary_name: "att-mz",
        output_dir_name: "att-mz-macos-aarch64",
    },
];

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    match args.next().as_deref() {
        Some("dist") => dist(),
        Some("ensure-tools") => ensure_tools_for_targets(DIST_TARGETS).map(|_| ()),
        Some("build-target") => {
            let target = args.next().ok_or_else(|| {
                anyhow!("缺少目标参数，例如: cargo run -p xtask -- build-target {LINUX_TARGET}")
            })?;
            build_single_target(&target)
        }
        Some(command) => bail!("未知 xtask 命令: {command}"),
        None => {
            println!("可用命令:");
            println!("  cargo run -p xtask -- ensure-tools");
            println!("  cargo run -p xtask -- build-target <rust-target>");
            println!("  cargo run -p xtask -- dist");
            Ok(())
        }
    }
}

fn dist() -> Result<()> {
    let tools = ensure_tools_for_targets(DIST_TARGETS)?;
    let dist_dir = workspace_root()?.join("dist");
    if dist_dir.exists() {
        fs::remove_dir_all(&dist_dir).context("清理旧 dist 目录失败")?;
    }
    fs::create_dir_all(&dist_dir).context("创建 dist 目录失败")?;

    for target in DIST_TARGETS {
        build_and_copy(target, &dist_dir, &tools)?;
    }

    println!("三端编译产物已输出到 {}", dist_dir.display());
    Ok(())
}

fn build_single_target(target: &str) -> Result<()> {
    let target = find_dist_target(target)?;
    let tools = ensure_tools_for_targets(&[*target])?;
    let dist_dir = workspace_root()?.join("dist");
    fs::create_dir_all(&dist_dir).context("创建 dist 目录失败")?;
    build_and_copy(target, &dist_dir, &tools)?;
    println!(
        "目标 {} 的编译产物已输出到 {}",
        target.rust_target,
        dist_dir.join(target.output_dir_name).display()
    );
    Ok(())
}

fn ensure_tools_for_targets(targets: &[DistTarget]) -> Result<ToolPaths> {
    for target in targets {
        ensure_rust_target(target.rust_target)?;
    }
    let zig = if targets.iter().any(DistTarget::needs_zig) {
        Some(ensure_zig()?)
    } else {
        None
    };
    Ok(ToolPaths { zig })
}

fn ensure_rust_target(target: &str) -> Result<()> {
    let installed = command_output("rustup", ["target", "list", "--installed"])?;
    if installed.lines().any(|line| line.trim() == target) {
        return Ok(());
    }
    run_command("rustup", ["target", "add", target])
        .with_context(|| format!("安装 Rust target 失败: {target}"))
}

fn ensure_zig() -> Result<PathBuf> {
    let desired_version = desired_zig_version();
    let local_zig = tools_root()?.join("zig").join(exe_name("zig"));
    if local_zig.exists() {
        if zig_version_matches(&local_zig, &desired_version) {
            return Ok(local_zig);
        }
        if let Some(parent) = local_zig.parent() {
            fs::remove_dir_all(parent).context("清理旧 Zig 版本失败")?;
        }
    }
    if command_exists("zig") && zig_version_matches(Path::new("zig"), &desired_version) {
        return Ok(PathBuf::from("zig"));
    }
    if cfg!(windows) {
        download_official_zig_for_windows(&local_zig, &desired_version)?;
        return Ok(local_zig);
    }
    bail!("未找到 Zig。请从 Zig 官方下载并加入 PATH: https://ziglang.org/download/");
}

fn download_official_zig_for_windows(local_zig: &Path, desired_version: &str) -> Result<()> {
    println!("未找到匹配版本的 Zig，正在从 Zig 官方索引下载 Windows x64 版本 {desired_version}...");
    let index: Value = reqwest::blocking::get(ZIG_INDEX_URL)
        .context("请求 Zig 官方下载索引失败")?
        .error_for_status()
        .context("Zig 官方下载索引返回失败状态码")?
        .json()
        .context("解析 Zig 官方下载索引失败")?;
    let release = choose_zig_release(&index, desired_version)?;
    let windows = release
        .get("x86_64-windows")
        .and_then(Value::as_object)
        .ok_or_else(|| anyhow!("Zig 官方索引缺少 x86_64-windows 发布物"))?;
    let tarball = windows
        .get("tarball")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("Zig 官方索引缺少 tarball 字段"))?;
    let expected_shasum = windows
        .get("shasum")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("Zig 官方索引缺少 shasum 字段"))?;

    let tools_dir = tools_root()?;
    fs::create_dir_all(&tools_dir).context("创建 .tools 目录失败")?;
    let archive_path = tools_dir.join("zig-windows-x86_64.zip");
    let bytes = reqwest::blocking::get(tarball)
        .with_context(|| format!("下载 Zig 官方发布包失败: {tarball}"))?
        .error_for_status()
        .context("Zig 官方发布包返回失败状态码")?
        .bytes()
        .context("读取 Zig 官方发布包失败")?;
    let actual_shasum = format!("{:x}", Sha256::digest(&bytes));
    if actual_shasum != expected_shasum {
        bail!("Zig 官方发布包 SHA256 校验失败");
    }
    fs::write(&archive_path, &bytes).context("保存 Zig 发布包失败")?;

    let extract_dir = tools_dir.join("zig-extract");
    if extract_dir.exists() {
        fs::remove_dir_all(&extract_dir).context("清理 Zig 解压目录失败")?;
    }
    fs::create_dir_all(&extract_dir).context("创建 Zig 解压目录失败")?;
    run_command(
        "tar",
        [
            OsStr::new("-xf"),
            archive_path.as_os_str(),
            OsStr::new("-C"),
            extract_dir.as_os_str(),
        ],
    )
    .context("解压 Zig 官方发布包失败")?;

    let extracted_root = fs::read_dir(&extract_dir)
        .context("读取 Zig 解压目录失败")?
        .filter_map(std::result::Result::ok)
        .map(|entry| entry.path())
        .find(|path| path.is_dir())
        .ok_or_else(|| anyhow!("Zig 发布包解压后未找到目录"))?;
    let final_dir = tools_dir.join("zig");
    if final_dir.exists() {
        fs::remove_dir_all(&final_dir).context("清理旧 Zig 目录失败")?;
    }
    fs::rename(extracted_root, &final_dir).context("安装 Zig 到 .tools/zig 失败")?;
    fs::remove_dir_all(&extract_dir).ok();
    if !local_zig.exists() {
        bail!("Zig 安装完成但未找到 {}", local_zig.display());
    }
    Ok(())
}

fn choose_zig_release<'a>(index: &'a Value, desired_version: &str) -> Result<&'a Value> {
    let object = index
        .as_object()
        .ok_or_else(|| anyhow!("Zig 官方索引顶层不是对象"))?;
    object
        .get(desired_version)
        .ok_or_else(|| anyhow!("Zig 官方索引中没有版本 {desired_version}"))
}

fn build_and_copy(target: &DistTarget, dist_dir: &Path, tools: &ToolPaths) -> Result<()> {
    let mut command = cargo_command();
    command.args([
        "build",
        "-p",
        "att-mz",
        "--release",
        "--target",
        target.rust_target,
    ]);
    let rustc = command_output("rustup", ["which", "rustc"])?;
    command.env("RUSTC", rustc.trim());
    if target.needs_zig() {
        let zig = tools
            .zig
            .as_ref()
            .ok_or_else(|| anyhow!("目标 {} 缺少 Zig 链接器", target.rust_target))?;
        let linker = write_zig_linker_wrapper(target.rust_target, zig)?;
        command.env(cargo_linker_env_key(target.rust_target), linker);
        command.env(
            cc_env_key(target.rust_target),
            write_zig_linker_wrapper(target.rust_target, zig)?,
        );
        command.env(
            ar_env_key(target.rust_target),
            write_zig_ar_wrapper(target.rust_target, zig)?,
        );
    }
    run_prepared_command(&mut command)
        .with_context(|| format!("构建目标失败: {}", target.rust_target))?;

    let source = workspace_root()?
        .join("target")
        .join(target.rust_target)
        .join("release")
        .join(target.binary_name);
    if !source.exists() {
        bail!("构建完成但未找到产物: {}", source.display());
    }
    let output_dir = dist_dir.join(target.output_dir_name);
    if output_dir.exists() {
        fs::remove_dir_all(&output_dir).context("清理旧目标产物目录失败")?;
    }
    fs::create_dir_all(&output_dir).context("创建目标产物目录失败")?;
    fs::copy(&source, output_dir.join(target.binary_name))
        .with_context(|| format!("复制产物失败: {}", source.display()))?;
    Ok(())
}

fn write_zig_linker_wrapper(target: &str, zig: &Path) -> Result<PathBuf> {
    let linkers_dir = tools_root()?.join("linkers");
    fs::create_dir_all(&linkers_dir).context("创建 Zig linker 包装目录失败")?;
    let wrapper = linkers_dir.join(script_name(&format!("zig-linker-{target}")));
    let zig_path = zig.to_string_lossy().replace('\\', "/");
    let content = if cfg!(windows) {
        let target_arg = zig_target_arg(target)?;
        let sdk_part = macos_sdk_arg(target);
        let extra_link_part = macos_extra_link_arg(target, zig)?;
        format!(
            "@echo off\r\nsetlocal enabledelayedexpansion\r\nset ARGS=\r\n:collect\r\nif \"%~1\"==\"\" goto run\r\nset \"ARG=%~1\"\r\nif \"!ARG!\"==\"--target\" (\r\n  shift\r\n  shift\r\n  goto collect\r\n)\r\nif \"!ARG:~0,9!\"==\"--target=\" (\r\n  shift\r\n  goto collect\r\n)\r\nset \"ARGS=!ARGS! \"%~1\"\"\r\nshift\r\ngoto collect\r\n:run\r\n\"{zig_path}\" cc -target {target_arg} -nostartfiles{sdk_part}{extra_link_part} !ARGS!\r\n"
        )
    } else {
        let target_arg = zig_target_arg(target)?;
        let sdk_part = macos_sdk_arg(target);
        let extra_link_part = macos_extra_link_arg(target, zig)?;
        format!(
            "#!/usr/bin/env sh\nargs=\"\"\nskip_next=0\nfor arg in \"$@\"; do\n  if [ \"$skip_next\" = \"1\" ]; then\n    skip_next=0\n    continue\n  fi\n  if [ \"$arg\" = \"--target\" ]; then\n    skip_next=1\n    continue\n  fi\n  case \"$arg\" in\n    --target=*) continue ;;\n  esac\n  args=\"$args '$arg'\"\ndone\neval '\"{zig_path}\" cc -target {target_arg} -nostartfiles{sdk_part}{extra_link_part}' \"$args\"\n"
        )
    };
    fs::write(&wrapper, content).context("写入 Zig linker 包装脚本失败")?;
    #[cfg(unix)]
    {
        let mut permissions = fs::metadata(&wrapper)
            .context("读取 Zig linker 包装脚本权限失败")?
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&wrapper, permissions).context("设置 Zig linker 包装脚本权限失败")?;
    }
    Ok(wrapper)
}

fn write_zig_ar_wrapper(target: &str, zig: &Path) -> Result<PathBuf> {
    let linkers_dir = tools_root()?.join("linkers");
    fs::create_dir_all(&linkers_dir).context("创建 Zig ar 包装目录失败")?;
    let wrapper = linkers_dir.join(script_name(&format!("zig-ar-{target}")));
    let zig_path = zig.to_string_lossy().replace('\\', "/");
    let content = if cfg!(windows) {
        format!("@echo off\r\n\"{zig_path}\" ar %*\r\n")
    } else {
        format!("#!/usr/bin/env sh\n\"{zig_path}\" ar \"$@\"\n")
    };
    fs::write(&wrapper, content).context("写入 Zig ar 包装脚本失败")?;
    #[cfg(unix)]
    {
        let mut permissions = fs::metadata(&wrapper)
            .context("读取 Zig ar 包装脚本权限失败")?
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&wrapper, permissions).context("设置 Zig ar 包装脚本权限失败")?;
    }
    Ok(wrapper)
}

fn zig_target_arg(target: &str) -> Result<&'static str> {
    match target {
        LINUX_TARGET => Ok("x86_64-linux-gnu"),
        MACOS_TARGET => Ok("aarch64-macos"),
        _ => bail!("不需要 Zig linker 的目标: {target}"),
    }
}

fn macos_sdk_arg(target: &str) -> String {
    if target != MACOS_TARGET {
        return String::new();
    }
    match env::var_os("SDKROOT") {
        Some(sdk_root) => {
            let sdk_root = sdk_root.to_string_lossy().replace('\\', "/");
            format!(" --sysroot \"{sdk_root}\"")
        }
        None => String::new(),
    }
}

fn macos_extra_link_arg(target: &str, zig: &Path) -> Result<String> {
    if target != MACOS_TARGET || env::var_os("SDKROOT").is_some() {
        return Ok(String::new());
    }
    let stub_dir = ensure_macos_iconv_stub(zig)?;
    let stub_dir = stub_dir.to_string_lossy().replace('\\', "/");
    Ok(format!(" -L \"{stub_dir}\""))
}

fn ensure_macos_iconv_stub(zig: &Path) -> Result<PathBuf> {
    let stub_dir = tools_root()?.join("macos-stubs").join("aarch64");
    let stub_library = stub_dir.join("libiconv.a");
    if stub_library.exists() {
        return Ok(stub_dir);
    }
    fs::create_dir_all(&stub_dir).context("创建 macOS 兼容库目录失败")?;
    let source_path = stub_dir.join("iconv_stub.c");
    let object_path = stub_dir.join("iconv_stub.o");
    fs::write(&source_path, MACOS_ICONV_STUB_SOURCE).context("写入 macOS iconv 兼容源码失败")?;

    let mut compile = Command::new(zig);
    compile.args(["cc", "-target", "aarch64-macos", "-c"]);
    compile.arg(&source_path);
    compile.arg("-o");
    compile.arg(&object_path);
    run_prepared_command(&mut compile).context("编译 macOS iconv 兼容对象失败")?;

    let mut archive = Command::new(zig);
    archive.args(["ar", "rcs"]);
    archive.arg(&stub_library);
    archive.arg(&object_path);
    run_prepared_command(&mut archive).context("打包 macOS iconv 兼容库失败")?;
    Ok(stub_dir)
}

fn cargo_linker_env_key(target: &str) -> String {
    format!(
        "CARGO_TARGET_{}_LINKER",
        target.replace('-', "_").to_ascii_uppercase()
    )
}

fn cc_env_key(target: &str) -> String {
    format!("CC_{}", target.replace('-', "_"))
}

fn ar_env_key(target: &str) -> String {
    format!("AR_{}", target.replace('-', "_"))
}

fn command_exists(command: &str) -> bool {
    command_succeeds(command, ["--version"])
}

fn command_succeeds<I, S>(program: &str, args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new(program)
        .args(args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn command_output<I, S>(program: &str, args: I) -> Result<String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let output = Command::new(program)
        .args(args)
        .output()
        .with_context(|| format!("执行命令失败: {program}"))?;
    if !output.status.success() {
        bail!("命令返回失败: {program}");
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn run_command<I, S>(program: &str, args: I) -> Result<()>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut command = Command::new(program);
    command.args(args);
    run_prepared_command(&mut command)
}

fn cargo_command() -> Command {
    let mut command = Command::new("rustup");
    command.args(["run", "stable", "cargo"]);
    command
}

fn run_prepared_command(command: &mut Command) -> Result<()> {
    let status = command
        .status()
        .with_context(|| format!("执行命令失败: {:?}", command))?;
    if !status.success() {
        bail!("命令返回失败: {:?}", command);
    }
    Ok(())
}

fn workspace_root() -> Result<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| anyhow!("无法解析 workspace 根目录"))
}

fn desired_zig_version() -> String {
    env::var("ATT_MZ_ZIG_VERSION").unwrap_or_else(|_| DEFAULT_ZIG_VERSION.to_string())
}

fn zig_version_matches(zig: &Path, desired_version: &str) -> bool {
    Command::new(zig)
        .arg("version")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .is_some_and(|version| version == desired_version)
}

fn tools_root() -> Result<PathBuf> {
    if let Some(path) = env::var_os("ATT_MZ_TOOLS_DIR") {
        return Ok(PathBuf::from(path));
    }
    if cfg!(windows) {
        return Ok(PathBuf::from(r"C:\att-mz-tools"));
    }
    Ok(workspace_root()?.join(".tools"))
}

fn exe_name(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn script_name(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.cmd")
    } else {
        name.to_string()
    }
}

fn find_dist_target(target: &str) -> Result<&'static DistTarget> {
    DIST_TARGETS
        .iter()
        .find(|dist_target| dist_target.rust_target == target)
        .ok_or_else(|| anyhow!("不支持的构建目标: {target}"))
}

#[derive(Clone, Copy)]
struct DistTarget {
    rust_target: &'static str,
    binary_name: &'static str,
    output_dir_name: &'static str,
}

impl DistTarget {
    fn needs_zig(&self) -> bool {
        self.rust_target != WINDOWS_TARGET
    }
}

struct ToolPaths {
    zig: Option<PathBuf>,
}
