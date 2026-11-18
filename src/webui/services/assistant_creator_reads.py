"""Model-facing Creator source observations and per-run progress tracking."""
from __future__ import annotations

from dataclasses import dataclass, field

INDEX_TOOLS = frozenset({'creator_project_read', 'creator_project_inspect'})
FILE_TOOL = 'creator_project_read_file'
READ_TOOLS = INDEX_TOOLS | {FILE_TOOL}

CREATOR_GUIDANCE = (
    ' Creator Studio workflow is active. For conversational messages, answer without tools. '
    'For project work, inspect the selected project with creator_project_inspect when you need its current '
    'revision or file index. Read only relevant source via creator_project_read_file using the index revision '
    'and exact path. Follow next_offset for missing ranges, not a full project read. Reuse source already '
    'present in the transcript; if content_omitted is set, fetch that exact range only when needed. A '
    'revision mismatch requires a fresh index and current source. '
    'To change project content, use creator_project_update with the revision of the source you actually read. '
    'Its files values replace whole files, not ranges: never submit a clipped, redacted, or reconstructed '
    'partial file. If you cannot safely preserve the complete file, explain that and stop rather than overwrite it. '
    'Summarize the exact files and intent before the approval boundary, and summarize approved changes '
    'in natural language before waiting for further input. Let the client refresh the project and show the diff. '
    'Use validate, preview, build or submission tools only when the user requests that step. '
    'Do not claim a gameplay design is executable unless a registered capability confirms it.'
)


def source_range_key(value: dict) -> tuple:
    return tuple(value.get(key) for key in ('project_id', 'revision', 'path', 'file_hash', 'offset', 'end_offset'))


@dataclass
class CreatorReadProgress:
    seen: set[tuple] = field(default_factory=set)
    coverage: dict[tuple, tuple[int, set[tuple[int, int]]]] = field(default_factory=dict)
    repeated: int = 0

    def require_complete_replacement_source(self, args: dict) -> None:
        """Reject a whole-file replacement if this run only read part of its source."""
        files = args.get('files')
        if not isinstance(files, dict):
            return
        for path in files:
            normalized = path.replace('\\', '/').strip('/')
            tracked = self.coverage.get((args['project_id'].strip(), args['expected_revision'], normalized))
            if tracked is None:
                continue  # New files and intentionally supplied complete replacements keep the existing API.
            total, ranges = tracked
            end = 0
            for start, stop in sorted(ranges):
                if start > end:
                    break
                end = max(end, stop)
            if end < total:
                raise ValueError('Only part of the source was read. Read the missing ranges before replacing the whole file, or stop without overwriting it.')

    def record(self, tool: str, result: dict) -> bool:
        """Pause after three reads with no new range, page or revision.

        Isolated re-fetches remain valid (e.g. after history compaction). A
        successful non-read action breaks the streak; failures don't imply progress.
        """
        if not result.get('ok'):
            return False
        if tool in INDEX_TOOLS:
            project = result.get('project', {})
            key = ('index', *(project.get(name) for name in ('id', 'revision', 'digest', 'offset')))
        elif tool == FILE_TOOL:
            ref = source_range_key(result)
            key = ('range', *ref)
            _, ranges = self.coverage.setdefault(ref[:3], (result['total_chars'], set()))
            ranges.add((result['offset'], result['end_offset']))
        else:
            self.repeated = 0
            return False
        if key not in self.seen:
            self.seen.add(key)
            self.repeated = 0
            return False
        self.repeated += 1
        return self.repeated >= 3


def preserve_source_observations(message: dict, seen_ranges: set[tuple], *, omit_source: bool = False) -> dict:
    """Never shorten source text while still labelling it as an exact range."""
    parts = []
    for part in reversed(message.get('parts', [])):
        value = part.get('content')
        if (part.get('part_kind') == 'tool-return' and part.get('tool_name') == FILE_TOOL
                and isinstance(value, dict) and isinstance(value.get('content'), str)):
            key = source_range_key(value)
            if omit_source or key in seen_ranges:
                value = {**{name: field for name, field in value.items() if name != 'content'},
                         'content_omitted': True}
                part = {**part, 'content': value}
            else:
                seen_ranges.add(key)
        parts.append(part)
    return {**message, 'parts': list(reversed(parts))}


def creator_write_observation(result: dict) -> dict:
    """Keep the UI receipt intact, without feeding the edited project back whole."""
    project = result.get('project')
    if not isinstance(project, dict) or 'files' not in project:
        return result
    return {**{key: value for key, value in result.items() if key != 'project'}, 'project': {
        key: project[key] for key in ('id', 'name', 'revision', 'digest') if key in project}}


def creator_receipt_observation(receipt: dict) -> dict:
    """Project the two existing copies of a receipt only for model context."""
    projected = dict(receipt)
    if isinstance(projected.get('result'), dict):
        projected['result'] = creator_write_observation(projected['result'])
    if isinstance(projected.get('response'), dict):
        projected['response'] = creator_receipt_observation(projected['response'])
    return projected
