"""构建 A.T.T MZ Windows 发行版目录和 ZIP 包。

本脚本只负责发布包装，不保存源码数据库，不复制历史日志，也不把开发态
`skills/att-mz/SKILL.md` 放进发行包。发行包内的 `skills/att-mz/SKILL.md`
固定来自 `skills/att-mz-release/SKILL.md`。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "dist"
RELEASE_DIRECTORY_NAME = "att-mz"
DEFAULT_ZIP_NAME = "att-mz-windows-x86_64.zip"
RELEASE_SKILL_SOURCE = ROOT / "skills" / "att-mz-release" / "SKILL.md"
RELEASE_README_SOURCE = ROOT / "docs" / "release-readme.md"


@dataclass(frozen=True)
class BuildOptions:
    """发布构建参数。"""

    output_dir: Path
    zip_name: str


@dataclass(frozen=True)
class CopySpec:
    """发行包资源复制规则。"""

    source: Path
    target_parts: tuple[str, ...]


def parse_args() -> BuildOptions:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="构建 A.T.T MZ Windows 发行版 ZIP")
    _ = parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="发行目录输出位置，默认写入 dist",
    )
    _ = parser.add_argument(
        "--zip-name",
        default=DEFAULT_ZIP_NAME,
        help=f"生成的 ZIP 文件名，默认 {DEFAULT_ZIP_NAME}",
    )
    namespace = parser.parse_args()
    output_dir = cast(str, namespace.output_dir)
    zip_name = cast(str, namespace.zip_name)
    return BuildOptions(
        output_dir=Path(output_dir).resolve(),
        zip_name=zip_name,
    )


def ensure_source_exists(path: Path) -> None:
    """确认发布资源存在。"""
    if not path.exists():
        raise FileNotFoundError(f"发布资源不存在: {path}")


def configure_stdio_encoding() -> None:
    """把发布脚本输出固定为 UTF-8，避免 GitHub Windows runner 使用窄编码。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def ensure_github_actions_environment() -> None:
    """保证发行版只能由 GitHub Actions 构建。"""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("发行版构建只能在 GitHub Actions release 工作流中执行。")


def reset_release_directory(release_dir: Path) -> None:
    """清空并重建发行目录。"""
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)


def build_pex_scie(exe_path: Path) -> None:
    """使用 PEX scie eager 构建 Windows 可执行文件。"""
    pex_output_path = exe_path.with_suffix(".pex")
    if pex_output_path.exists():
        pex_output_path.unlink()
    if exe_path.exists():
        exe_path.unlink()
    command = [
        "uv",
        "run",
        "--with",
        "pex",
        "pex",
        ".",
        "--script",
        "att-mz",
        "--scie",
        "eager",
        "--scie-load-dotenv",
        "--output-file",
        str(pex_output_path),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    ensure_source_exists(exe_path)
    if pex_output_path.exists():
        pex_output_path.unlink()


def copy_file(source: Path, target: Path) -> None:
    """复制单个文件并确保目标目录存在。"""
    ensure_source_exists(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copy2(source, target)


def copy_packaged_release_skill(target: Path) -> None:
    """把发行版 Skill 模板写成发行包内的 `att-mz` Skill。"""
    ensure_source_exists(RELEASE_SKILL_SOURCE)
    skill_text = RELEASE_SKILL_SOURCE.read_text(encoding="utf-8")
    packaged_skill_text = skill_text.replace("name: att-mz-release", "name: att-mz", 1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packaged_skill_text, encoding="utf-8")


def copy_release_resources(release_dir: Path) -> None:
    """复制发行包所需的配置、文档、字体、提示词和 Skill。"""
    copy_specs = [
        CopySpec(RELEASE_README_SOURCE, ("README.md",)),
        CopySpec(ROOT / "LICENSE", ("LICENSE",)),
        CopySpec(ROOT / "setting.example.toml", ("setting.example.toml",)),
        CopySpec(ROOT / "setting.example.toml", ("setting.toml",)),
        CopySpec(ROOT / "custom_placeholder_rules.json", ("custom_placeholder_rules.json",)),
        CopySpec(ROOT / "prompts" / "text_translation_system.md", ("prompts", "text_translation_system.md")),
        CopySpec(ROOT / "fonts" / "NotoSansSC-Regular.ttf", ("fonts", "NotoSansSC-Regular.ttf")),
        CopySpec(
            ROOT / "skills" / "att-mz-release" / "references" / "rpg-maker-mv-mz-world-knowledge.md",
            ("skills", "att-mz", "references", "rpg-maker-mv-mz-world-knowledge.md"),
        ),
    ]
    for spec in copy_specs:
        copy_file(spec.source, release_dir.joinpath(*spec.target_parts))
    copy_packaged_release_skill(release_dir / "skills" / "att-mz" / "SKILL.md")

    for directory_parts in (("data", "db"), ("logs",), ("outputs",)):
        release_dir.joinpath(*directory_parts).mkdir(parents=True, exist_ok=True)


def run_smoke_tests(release_dir: Path) -> None:
    """验证发行版入口能启动并能读取空注册表。"""
    exe_path = release_dir / "att-mz.exe"
    subprocess.run(
        [str(exe_path), "--help"],
        cwd=release_dir,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    subprocess.run(
        [str(exe_path), "list", "--json"],
        cwd=release_dir,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def add_directory_entry(archive: zipfile.ZipFile, arcname: str) -> None:
    """向 ZIP 写入空目录条目。"""
    normalized_name = arcname.replace("\\", "/").rstrip("/") + "/"
    info = zipfile.ZipInfo(normalized_name)
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.external_attr = 0o755 << 16
    archive.writestr(info, b"")


def add_file_entry(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    """向 ZIP 写入单个文件。"""
    info = zipfile.ZipInfo(arcname.replace("\\", "/"))
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, source.read_bytes())


def create_release_zip(release_dir: Path, zip_path: Path) -> None:
    """把发行目录压缩为 ZIP。"""
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        root_arcname = release_dir.name
        add_directory_entry(archive, root_arcname)
        for directory in sorted(path for path in release_dir.rglob("*") if path.is_dir()):
            add_directory_entry(archive, str(Path(root_arcname) / directory.relative_to(release_dir)))
        for file_path in sorted(path for path in release_dir.rglob("*") if path.is_file()):
            add_file_entry(archive, file_path, str(Path(root_arcname) / file_path.relative_to(release_dir)))


def main() -> int:
    """执行发行版构建。"""
    configure_stdio_encoding()
    ensure_github_actions_environment()
    options = parse_args()
    release_dir = options.output_dir / RELEASE_DIRECTORY_NAME
    zip_path = options.output_dir / options.zip_name

    exe_path = release_dir / "att-mz.exe"
    reset_release_directory(release_dir)
    build_pex_scie(exe_path)

    copy_release_resources(release_dir)
    run_smoke_tests(release_dir)
    create_release_zip(release_dir, zip_path)
    print(f"发行版目录: {release_dir}")
    print(f"发行版 ZIP: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
