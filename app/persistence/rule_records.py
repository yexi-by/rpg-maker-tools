"""插件、事件指令、Note 标签和文本规则记录会话能力。"""

import json

from app.rmmz.schema import (
    EventCommandParameterFilter,
    EventCommandTextRuleRecord,
    NoteTagTextRuleRecord,
    PlaceholderRuleRecord,
    PluginTextRuleRecord,
    SourceResidualRuleRecord,
)

from .rows import decode_string_list, row_int, row_str
from .session_base import SessionMixinBase
from .session_utils import build_event_command_group_key, parse_source_residual_rule_type
from .sql import (
    DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS,
    DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS,
    DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS,
    DELETE_ALL_NOTE_TAG_TEXT_RULES,
    DELETE_ALL_PLACEHOLDER_RULES,
    DELETE_ALL_PLUGIN_TEXT_RULES,
    DELETE_ALL_SOURCE_RESIDUAL_RULES,
    INSERT_EVENT_COMMAND_TEXT_RULE_FILTER,
    INSERT_EVENT_COMMAND_TEXT_RULE_GROUP,
    INSERT_EVENT_COMMAND_TEXT_RULE_PATH,
    INSERT_NOTE_TAG_TEXT_RULE,
    INSERT_PLACEHOLDER_RULE,
    INSERT_PLUGIN_TEXT_RULE,
    INSERT_SOURCE_RESIDUAL_RULE,
    SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS,
    SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS,
    SELECT_EVENT_COMMAND_TEXT_RULE_PATHS,
    SELECT_NOTE_TAG_TEXT_RULES,
    SELECT_PLACEHOLDER_RULES,
    SELECT_PLUGIN_TEXT_RULES,
    SELECT_SOURCE_RESIDUAL_RULES,
)


class RuleRecordSessionMixin(SessionMixinBase):
    """负责当前游戏规则记录的替换、读取与数据库值收窄。"""

    async def read_plugin_text_rules(self) -> list[PluginTextRuleRecord]:
        """读取当前游戏保存的全部插件文本规则。"""
        async with self.connection.execute(SELECT_PLUGIN_TEXT_RULES) as cursor:
            rows = await cursor.fetchall()

        grouped_records: dict[int, PluginTextRuleRecord] = {}
        for row in rows:
            plugin_index = row_int(row, "plugin_index", self.db_path)
            record = grouped_records.get(plugin_index)
            if record is None:
                record = PluginTextRuleRecord(
                    plugin_index=plugin_index,
                    plugin_name=row_str(row, "plugin_name", self.db_path),
                    plugin_hash=row_str(row, "plugin_hash", self.db_path),
                    path_templates=[],
                )
                grouped_records[plugin_index] = record
            record.path_templates.append(row_str(row, "path_template", self.db_path))
        return list(grouped_records.values())

    async def replace_plugin_text_rules(
        self,
        rule_records: list[PluginTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的全部插件文本规则。"""
        _ = await self.connection.execute(DELETE_ALL_PLUGIN_TEXT_RULES)
        for rule_record in rule_records:
            for path_template in rule_record.path_templates:
                _ = await self.connection.execute(
                    INSERT_PLUGIN_TEXT_RULE,
                    (
                        rule_record.plugin_index,
                        rule_record.plugin_name,
                        rule_record.plugin_hash,
                        path_template,
                    ),
                )
        await self.connection.commit()

    async def read_note_tag_text_rules(self) -> list[NoteTagTextRuleRecord]:
        """读取当前游戏保存的 Note 标签文本规则。"""
        async with self.connection.execute(SELECT_NOTE_TAG_TEXT_RULES) as cursor:
            rows = await cursor.fetchall()

        grouped_records: dict[str, NoteTagTextRuleRecord] = {}
        for row in rows:
            file_name = row_str(row, "file_name", self.db_path)
            record = grouped_records.get(file_name)
            if record is None:
                record = NoteTagTextRuleRecord(file_name=file_name, tag_names=[])
                grouped_records[file_name] = record
            record.tag_names.append(row_str(row, "tag_name", self.db_path))
        return list(grouped_records.values())

    async def replace_note_tag_text_rules(
        self,
        rule_records: list[NoteTagTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的 Note 标签文本规则。"""
        _ = await self.connection.execute(DELETE_ALL_NOTE_TAG_TEXT_RULES)
        for rule_record in rule_records:
            for tag_name in rule_record.tag_names:
                _ = await self.connection.execute(
                    INSERT_NOTE_TAG_TEXT_RULE,
                    (
                        rule_record.file_name,
                        tag_name,
                    ),
                )
        await self.connection.commit()

    async def read_event_command_text_rules(self) -> list[EventCommandTextRuleRecord]:
        """读取当前游戏保存的事件指令文本规则。"""
        async with self.connection.execute(SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS) as cursor:
            group_rows = await cursor.fetchall()
        async with self.connection.execute(SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS) as cursor:
            filter_rows = await cursor.fetchall()
        async with self.connection.execute(SELECT_EVENT_COMMAND_TEXT_RULE_PATHS) as cursor:
            path_rows = await cursor.fetchall()

        filters_by_group: dict[str, list[EventCommandParameterFilter]] = {}
        for row in filter_rows:
            group_key = row_str(row, "group_key", self.db_path)
            filters_by_group.setdefault(group_key, []).append(
                EventCommandParameterFilter(
                    index=row_int(row, "parameter_index", self.db_path),
                    value=row_str(row, "parameter_value", self.db_path),
                )
            )

        paths_by_group: dict[str, list[str]] = {}
        for row in path_rows:
            group_key = row_str(row, "group_key", self.db_path)
            paths_by_group.setdefault(group_key, []).append(row_str(row, "path_template", self.db_path))

        records: list[EventCommandTextRuleRecord] = []
        for row in group_rows:
            group_key = row_str(row, "group_key", self.db_path)
            records.append(
                EventCommandTextRuleRecord(
                    command_code=row_int(row, "command_code", self.db_path),
                    parameter_filters=filters_by_group.get(group_key, []),
                    path_templates=paths_by_group.get(group_key, []),
                )
            )
        return records

    async def replace_event_command_text_rules(
        self,
        rule_records: list[EventCommandTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的事件指令文本规则。"""
        _ = await self.connection.execute(DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS)
        _ = await self.connection.execute(DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS)
        _ = await self.connection.execute(DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS)
        for rule_record in rule_records:
            group_key = build_event_command_group_key(rule_record)
            _ = await self.connection.execute(
                INSERT_EVENT_COMMAND_TEXT_RULE_GROUP,
                (group_key, rule_record.command_code),
            )
            for parameter_filter in rule_record.parameter_filters:
                _ = await self.connection.execute(
                    INSERT_EVENT_COMMAND_TEXT_RULE_FILTER,
                    (group_key, parameter_filter.index, parameter_filter.value),
                )
            for path_template in rule_record.path_templates:
                _ = await self.connection.execute(
                    INSERT_EVENT_COMMAND_TEXT_RULE_PATH,
                    (group_key, path_template),
                )
        await self.connection.commit()

    async def replace_placeholder_rules(
        self,
        rules: list[PlaceholderRuleRecord],
    ) -> None:
        """用当前游戏专用规则替换数据库中的自定义占位符规则。"""
        _ = await self.connection.execute(DELETE_ALL_PLACEHOLDER_RULES)
        for rule in rules:
            _ = await self.connection.execute(
                INSERT_PLACEHOLDER_RULE,
                (rule.pattern_text, rule.placeholder_template),
            )
        await self.connection.commit()

    async def read_placeholder_rules(self) -> list[PlaceholderRuleRecord]:
        """读取当前游戏专用自定义占位符规则。"""
        async with self.connection.execute(SELECT_PLACEHOLDER_RULES) as cursor:
            rows = await cursor.fetchall()
        return [
            PlaceholderRuleRecord(
                pattern_text=row_str(row, "pattern_text", self.db_path),
                placeholder_template=row_str(row, "placeholder_template", self.db_path),
            )
            for row in rows
        ]

    async def replace_source_residual_rules(
        self,
        rules: list[SourceResidualRuleRecord],
    ) -> None:
        """用当前游戏专用规则替换源文残留例外规则。"""
        _ = await self.connection.execute(DELETE_ALL_SOURCE_RESIDUAL_RULES)
        for rule in rules:
            _ = await self.connection.execute(
                INSERT_SOURCE_RESIDUAL_RULE,
                (
                    rule.rule_id,
                    rule.rule_type,
                    rule.location_path,
                    rule.pattern_text,
                    json.dumps(rule.allowed_terms, ensure_ascii=False),
                    rule.check_group,
                    rule.reason,
                ),
            )
        await self.connection.commit()

    async def read_source_residual_rules(self) -> list[SourceResidualRuleRecord]:
        """读取当前游戏专用源文残留例外规则。"""
        async with self.connection.execute(SELECT_SOURCE_RESIDUAL_RULES) as cursor:
            rows = await cursor.fetchall()
        return [
            SourceResidualRuleRecord(
                rule_id=row_str(row, "rule_id", self.db_path),
                rule_type=parse_source_residual_rule_type(row_str(row, "rule_type", self.db_path), self.db_path),
                location_path=row_str(row, "location_path", self.db_path),
                pattern_text=row_str(row, "pattern_text", self.db_path),
                allowed_terms=decode_string_list(
                    row_str(row, "allowed_terms", self.db_path),
                    "allowed_terms",
                ),
                check_group=row_str(row, "check_group", self.db_path),
                reason=row_str(row, "reason", self.db_path),
            )
            for row in rows
        ]
