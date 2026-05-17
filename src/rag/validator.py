from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

KNOWN_REVISION_TRIGGERS = frozenset({
    'overshoot',
    'slow_settling',
    'excess_ripple',
    'efficiency_shortfall',
    'failed_revision',
})

NON_RAG_FOLDERS = frozenset({'observation_playbooks'})


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    source: str = ''


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == 'warning']

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == 'error']

    def is_clean(self) -> bool:
        return not self.errors and not self.warnings

    def log(self, logger: logging.Logger | None = None) -> None:
        target = logger or _log
        for issue in self.issues:
            line = f'[knowledge:{issue.code}] {issue.message}'
            if issue.source:
                line += f' (in {issue.source})'
            if issue.severity == 'error':
                target.error(line)
            else:
                target.warning(line)


def validate_knowledge_base(knowledge_root: Path) -> ValidationReport:
    report = ValidationReport()
    if not knowledge_root.exists():
        report.issues.append(
            ValidationIssue('error', 'missing_root', f'Knowledge root not found: {knowledge_root}')
        )
        return report

    files: list[tuple[Path, dict]] = []
    for path in sorted(knowledge_root.rglob('*.json')):
        if path.name == 'index.json':
            continue
        rel_parts = path.relative_to(knowledge_root).parts
        if rel_parts and rel_parts[0] in NON_RAG_FOLDERS:
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            report.issues.append(
                ValidationIssue(
                    'error',
                    'invalid_json',
                    str(exc),
                    source=_rel(path, knowledge_root),
                )
            )
            continue
        if not isinstance(data, dict):
            report.issues.append(
                ValidationIssue(
                    'error',
                    'invalid_root_type',
                    'top-level JSON value is not an object',
                    source=_rel(path, knowledge_root),
                )
            )
            continue
        files.append((path, data))

    strategy_pairs: dict[tuple[str, str], list[str]] = {}
    tuning_pairs: dict[tuple[str, str], list[str]] = {}
    source_ids: set[str] = set()

    for path, data in files:
        rel = _rel(path, knowledge_root)
        topic = _lower(data.get('topic'))
        topology = _lower(data.get('topology'))
        architecture = _lower(data.get('architecture'))
        if rel.startswith('sources/'):
            sid = _lower(data.get('source_id'))
            if sid:
                source_ids.add(sid)
            else:
                report.issues.append(
                    ValidationIssue('warning', 'missing_source_id', 'source file has no source_id', source=rel)
                )
        if topic == 'strategy' and topology and architecture:
            strategy_pairs.setdefault((topology, architecture), []).append(rel)
        if topic == 'tuning' and topology and architecture:
            tuning_pairs.setdefault((topology, architecture), []).append(rel)

    for pair, paths in strategy_pairs.items():
        if pair not in tuning_pairs:
            report.issues.append(
                ValidationIssue(
                    'warning',
                    'missing_tuning',
                    f'No tuning entry for topology={pair[0]} architecture={pair[1]} (declared in {paths[0]})',
                )
            )
    for pair, paths in tuning_pairs.items():
        if pair not in strategy_pairs:
            report.issues.append(
                ValidationIssue(
                    'warning',
                    'missing_strategy',
                    f'No controllers/strategy entry for topology={pair[0]} architecture={pair[1]} (declared in {paths[0]})',
                )
            )

    for path, data in files:
        rel = _rel(path, knowledge_root)
        sections = data.get('sections', [])
        if not isinstance(sections, list) or not sections:
            report.issues.append(
                ValidationIssue('warning', 'no_sections', 'file has no sections', source=rel)
            )
            continue
        file_refs = data.get('source_refs') or []
        for idx, section in enumerate(sections):
            if not isinstance(section, dict):
                report.issues.append(
                    ValidationIssue('warning', 'invalid_section_type', f'section {idx} is not an object', source=rel)
                )
                continue
            text = str(section.get('text', '')).strip()
            if not text:
                report.issues.append(
                    ValidationIssue('warning', 'empty_section', f'section {idx} has empty text', source=rel)
                )
            trigger = _lower(section.get('revision_trigger'))
            if trigger and trigger not in KNOWN_REVISION_TRIGGERS:
                report.issues.append(
                    ValidationIssue(
                        'warning',
                        'unknown_revision_trigger',
                        f"section {idx} uses revision_trigger='{trigger}' which is not emitted by the workflow",
                        source=rel,
                    )
                )
            refs = section.get('source_refs', file_refs) or []
            if isinstance(refs, list):
                for ref in refs:
                    ref_s = _lower(ref)
                    if ref_s and ref_s not in source_ids:
                        report.issues.append(
                            ValidationIssue(
                                'warning',
                                'unknown_source_ref',
                                f"section {idx} references unknown source_id='{ref_s}'",
                                source=rel,
                            )
                        )

    return report


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace('\\', '/')


def _lower(value: object) -> str:
    if value is None:
        return ''
    return str(value).strip().lower()


def main() -> int:
    import sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    root = Path(__file__).resolve().parents[2] / 'knowledge'
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    report = validate_knowledge_base(root)
    report.log()
    print(f'Validated {root}: {len(report.warnings)} warning(s), {len(report.errors)} error(s).')
    return 0 if not report.errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
