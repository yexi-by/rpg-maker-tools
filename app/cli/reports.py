"""命令行报告输出工具。

本模块负责把业务摘要转换为稳定 JSON 报告，并按终端模式渲染用户可扫读表格。
"""

from __future__ import annotations

import argparse

from rich.table import Table

from app.agent_toolkit import AgentIssue, AgentReport
from app.agent_toolkit.reports import issue
from app.application.handler import FontRestoreSummary, TextTranslationSummary, WriteBackSummary
from app.cli.arguments import read_bool_arg, read_optional_path_arg
from app.observability import console, logger


def build_translate_summary_report(summary: TextTranslationSummary) -> AgentReport:
    """把正文翻译摘要转换为稳定 JSON 报告。"""
    warnings: list[AgentIssue] = []
    if summary.has_errors:
        warnings.append(
            issue(
                "translation_quality_errors",
                f"本轮翻译有 {summary.error_count} 条模型翻了但项目检查没通过的译文；可以继续运行 translate，或导出手动填写译文表修复",
            )
        )
    return AgentReport.from_parts(
        errors=[],
        warnings=warnings,
        summary={
            "run_id": summary.run_id,
            "total_extracted_items": summary.total_extracted_items,
            "pending_count": summary.pending_count,
            "deduplicated_count": summary.deduplicated_count,
            "batch_count": summary.batch_count,
            "success_count": summary.success_count,
            "quality_error_count": summary.error_count,
            "llm_failure_count": summary.llm_failure_count,
        },
        details={},
    )


def build_write_back_summary_report(summary: WriteBackSummary) -> AgentReport:
    """把游戏文件回写摘要转换为稳定 JSON 报告。"""
    return AgentReport.from_parts(
        errors=[],
        warnings=[],
        summary={
            "data_item_count": summary.data_item_count,
            "plugin_item_count": summary.plugin_item_count,
            "terminology_written_count": summary.terminology_written_count,
            "target_font_name": summary.target_font_name or "",
            "source_font_count": summary.source_font_count,
            "replaced_font_reference_count": summary.replaced_font_reference_count,
            "font_copied": summary.font_copied,
        },
        details={},
    )


def build_font_restore_summary_report(summary: FontRestoreSummary) -> AgentReport:
    """把字体还原摘要转换为稳定 JSON 报告。"""
    warnings: list[AgentIssue] = []
    if summary.target_font_name is None:
        warnings.append(issue("font_restore", "没有候选覆盖字体名称，无法判断需要还原哪个新字体引用"))
    elif summary.restored_reference_count == 0:
        warnings.append(issue("font_restore", "没有找到需要还原的覆盖字体引用"))
    return AgentReport.from_parts(
        errors=[],
        warnings=warnings,
        summary={
            "restored_field_count": summary.restored_field_count,
            "restored_reference_count": summary.restored_reference_count,
            "target_font_name": summary.target_font_name or "",
        },
        details={},
    )


def write_report_outputs(
    *,
    report: AgentReport,
    args: argparse.Namespace,
    title: str,
    write_output_file: bool = True,
) -> None:
    """按用户参数输出 Agent 工具包报告。"""
    output_path = read_optional_path_arg(args, "output") if write_output_file else None
    json_text = report.to_json_text()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _ = output_path.write_text(f"{json_text}\n", encoding="utf-8")

    if read_bool_arg(args, "json_output"):
        print(json_text)
        return

    render_agent_report(report=report, title=title)
    if output_path is not None:
        logger.success(f"[tag.success]JSON 报告已写出[/tag.success] 文件 [tag.path]{output_path}[/tag.path]")


def render_agent_report(*, report: AgentReport, title: str) -> None:
    """用 Rich 表格展示报告摘要和问题列表。"""
    summary_table = Table(title=title)
    summary_table.add_column("字段", style="cyan")
    summary_table.add_column("值", style="magenta")
    summary_table.add_row("状态", report.status)
    for key, value in report.summary.items():
        summary_table.add_row(key, str(value))
    console.print(summary_table)

    if report.errors:
        error_table = Table(title="必须先处理的错误")
        error_table.add_column("代码", style="red")
        error_table.add_column("说明", style="white")
        for item in report.errors:
            error_table.add_row(item.code, item.message)
        console.print(error_table)

    if report.warnings:
        warning_table = Table(title="告警")
        warning_table.add_column("代码", style="yellow")
        warning_table.add_column("说明", style="white")
        for item in report.warnings:
            warning_table.add_row(item.code, item.message)
        console.print(warning_table)

__all__ = [
    "build_font_restore_summary_report",
    "build_translate_summary_report",
    "build_write_back_summary_report",
    "render_agent_report",
    "write_report_outputs",
]
