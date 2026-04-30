from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.contracts import RequirementSpec
from src.workflow.contracts import DesignIntent, render_intent_for_prompt
from src.workflow.spec_parser import DesignSpecParser


def _make_req(**overrides) -> RequirementSpec:
    base = dict(
        name='unit_test_design',
        design_prompt='Stable inverter for weak grid with frequent load steps.',
        vin_nominal_v=400.0,
        vout_target_v=120.0,
        pout_w=5000.0,
        fsw_hz=20000.0,
        ripple_v_pp_max=2.0,
        settling_time_ms_max=20.0,
        overshoot_pct_max=10.0,
        efficiency_min_pct=92.0,
        weak_grid_mode=True,
        load_step_pct=50.0,
    )
    base.update(overrides)
    return RequirementSpec(**base)


class DesignIntentRenderingTests(unittest.TestCase):
    def test_empty_intent_renders_to_empty_string(self) -> None:
        self.assertEqual(render_intent_for_prompt(None), '')
        self.assertEqual(render_intent_for_prompt(DesignIntent()), '')

    def test_renders_when_llm_parsed(self) -> None:
        intent = DesignIntent(
            priorities=['stability_margin_first', 'low_thd'],
            operating_scenarios=['weak_grid_LCL_resonance'],
            hard_constraints={'overshoot_pct_max': 10.0},
            soft_preferences={'efficiency_min_pct': 92.0},
            key_signals=['va_v', 'theta_pll'],
            concerns=['instability_under_weak_grid'],
            summary='Grid-forming inverter for weak grid.',
            llm_parsed=True,
        )
        rendered = render_intent_for_prompt(intent)
        self.assertIn('stability_margin_first', rendered)
        self.assertIn('weak_grid_LCL_resonance', rendered)
        self.assertIn('va_v', rendered)
        self.assertIn('overshoot_pct_max=10.0', rendered)


class DesignSpecParserTests(unittest.TestCase):
    def test_disabled_client_returns_empty_intent(self) -> None:
        client = MagicMock()
        client.enabled = False
        parser = DesignSpecParser(client=client)
        spec = parser.parse(_make_req())
        self.assertFalse(spec.intent.llm_parsed)
        self.assertEqual(spec.intent.priorities, [])
        self.assertEqual(spec.intent.operating_scenarios, [])

    def test_llm_response_populates_intent(self) -> None:
        client = MagicMock()
        client.enabled = True
        client.complete_json.return_value = {
            'priorities': ['stability_margin_first', 'low_thd'],
            'operating_scenarios': ['weak_grid_LCL_resonance', 'load_step_50pct'],
            'hard_constraints': {'overshoot_pct_max': 10.0},
            'soft_preferences': {'efficiency_min_pct': 92.0},
            'key_signals': ['va_v', 'ia_a', 'theta_pll'],
            'concerns': ['instability_under_weak_grid'],
            'summary': 'Grid-forming weak-grid inverter.',
        }
        parser = DesignSpecParser(client=client)
        spec = parser.parse(_make_req())
        self.assertTrue(spec.intent.llm_parsed)
        self.assertEqual(spec.intent.priorities[0], 'stability_margin_first')
        self.assertIn('weak_grid_LCL_resonance', spec.intent.operating_scenarios)
        self.assertEqual(spec.intent.hard_constraints['overshoot_pct_max'], 10.0)
        # The rest of ParsedDesignSpec is preserved.
        self.assertEqual(spec.requirements_name, 'unit_test_design')
        self.assertIn('weak_grid', spec.objective_tags)
        self.assertIn('load_step', spec.objective_tags)

    def test_llm_failure_returns_empty_intent(self) -> None:
        client = MagicMock()
        client.enabled = True
        client.complete_json.side_effect = RuntimeError('network down')
        parser = DesignSpecParser(client=client)
        spec = parser.parse(_make_req())
        self.assertFalse(spec.intent.llm_parsed)
        self.assertEqual(spec.intent.priorities, [])

    def test_malformed_llm_response_falls_back_safely(self) -> None:
        client = MagicMock()
        client.enabled = True
        # Wrong types — must not crash; non-list/non-dict fields drop to defaults.
        client.complete_json.return_value = {
            'priorities': 'not a list',
            'operating_scenarios': None,
            'hard_constraints': 'oops',
            'key_signals': ['va_v', 42, ''],
            'summary': 12345,
        }
        parser = DesignSpecParser(client=client)
        spec = parser.parse(_make_req())
        self.assertTrue(spec.intent.llm_parsed)
        self.assertEqual(spec.intent.priorities, [])
        self.assertEqual(spec.intent.operating_scenarios, [])
        self.assertEqual(spec.intent.hard_constraints, {})
        # 42 coerces to '42'; empty strings filter out.
        self.assertEqual(spec.intent.key_signals, ['va_v', '42'])
        self.assertEqual(spec.intent.summary, '12345')


if __name__ == '__main__':
    unittest.main()
