from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.rag.validator import validate_knowledge_base


def _write(root: Path, rel: str, payload: dict) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


class KnowledgeValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_source(self, source_id: str = 'src_alpha') -> None:
        _write(
            self.root,
            f'sources/{source_id}.json',
            {
                'title': 'Alpha source',
                'topic': 'reference',
                'source_type': 'source',
                'source_id': source_id,
                'sections': [{'heading': 'h', 'text': 'body'}],
            },
        )

    def test_clean_pair_passes(self) -> None:
        self._seed_source()
        _write(
            self.root,
            'strategy/foo.json',
            {
                'title': 'foo',
                'topic': 'strategy',
                'topology': 'buck',
                'architecture': 'pi',
                'source_refs': ['src_alpha'],
                'sections': [{'heading': 'use', 'text': 'use it'}],
            },
        )
        _write(
            self.root,
            'tuning/foo_tuning.json',
            {
                'title': 'foo tuning',
                'topic': 'tuning',
                'topology': 'buck',
                'architecture': 'pi',
                'sections': [{'heading': 'gain', 'text': 'raise kp'}],
            },
        )
        report = validate_knowledge_base(self.root)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])

    def test_missing_tuning_pair_warns(self) -> None:
        _write(
            self.root,
            'strategy/foo.json',
            {
                'title': 'foo',
                'topic': 'strategy',
                'topology': 'buck',
                'architecture': 'pi',
                'sections': [{'heading': 'use', 'text': 'use it'}],
            },
        )
        report = validate_knowledge_base(self.root)
        codes = {i.code for i in report.warnings}
        self.assertIn('missing_tuning', codes)

    def test_orphan_tuning_warns(self) -> None:
        _write(
            self.root,
            'tuning/foo_tuning.json',
            {
                'title': 'foo tuning',
                'topic': 'tuning',
                'topology': 'buck',
                'architecture': 'pi',
                'sections': [{'heading': 'gain', 'text': 'raise kp'}],
            },
        )
        report = validate_knowledge_base(self.root)
        codes = {i.code for i in report.warnings}
        self.assertIn('missing_strategy', codes)

    def test_unknown_revision_trigger_warns(self) -> None:
        _write(
            self.root,
            'revision/odd.json',
            {
                'title': 'odd',
                'topic': 'revision',
                'sections': [{'heading': 'x', 'revision_trigger': 'mystery_trigger', 'text': 'do thing'}],
            },
        )
        report = validate_knowledge_base(self.root)
        codes = {i.code for i in report.warnings}
        self.assertIn('unknown_revision_trigger', codes)

    def test_unknown_source_ref_warns(self) -> None:
        _write(
            self.root,
            'tuning/foo_tuning.json',
            {
                'title': 'foo tuning',
                'topic': 'tuning',
                'topology': 'buck',
                'architecture': 'pi',
                'source_refs': ['does_not_exist'],
                'sections': [{'heading': 'g', 'text': 'tune'}],
            },
        )
        _write(
            self.root,
            'strategy/foo.json',
            {
                'title': 'foo',
                'topic': 'strategy',
                'topology': 'buck',
                'architecture': 'pi',
                'sections': [{'heading': 'use', 'text': 'use it'}],
            },
        )
        report = validate_knowledge_base(self.root)
        codes = {i.code for i in report.warnings}
        self.assertIn('unknown_source_ref', codes)

    def test_invalid_json_is_error(self) -> None:
        path = self.root / 'tuning' / 'broken.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{not valid', encoding='utf-8')
        report = validate_knowledge_base(self.root)
        codes = {i.code for i in report.errors}
        self.assertIn('invalid_json', codes)

    def test_missing_root_is_error(self) -> None:
        report = validate_knowledge_base(self.root / 'does_not_exist')
        codes = {i.code for i in report.errors}
        self.assertIn('missing_root', codes)

    def test_empty_section_warns(self) -> None:
        _write(
            self.root,
            'tuning/foo_tuning.json',
            {
                'title': 'foo tuning',
                'topic': 'tuning',
                'topology': 'buck',
                'architecture': 'pi',
                'sections': [{'heading': 'empty', 'text': '  '}],
            },
        )
        _write(
            self.root,
            'strategy/foo.json',
            {
                'title': 'foo',
                'topic': 'strategy',
                'topology': 'buck',
                'architecture': 'pi',
                'sections': [{'heading': 'use', 'text': 'use it'}],
            },
        )
        report = validate_knowledge_base(self.root)
        codes = {i.code for i in report.warnings}
        self.assertIn('empty_section', codes)


if __name__ == '__main__':
    unittest.main()
