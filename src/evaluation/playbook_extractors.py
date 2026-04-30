"""Topology-aware playbook extractors and pathology matcher.

Each extractor is a small pure function that operates on the existing waveform
payload and the `computed` dict produced by `waveform_harness.evaluate_waveform_files`,
plus an optional args dict from the playbook. They return a single float (or
NaN when the metric cannot be computed for the given waveform shape).

Pathology matching evaluates simple rule trees against the merged metric
namespace (computed + extracted + a few derived constants like `abs_target`).
Rule values that are strings get evaluated as expressions over the namespace
through a tightly restricted evaluator — only attribute access and arithmetic
on numeric metric names are supported, so the rule files cannot smuggle code.
"""

from __future__ import annotations

import ast
import json
import math
import operator
from pathlib import Path
from typing import Any, Callable

from src.agents._topology_meta import power_stage_family

# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

ExtractorFn = Callable[[dict[str, Any], dict[str, float], dict[str, Any]], float]


def _to_float_list(values: object) -> list[float]:
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except Exception:
            continue
    return out


def _ratio(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    num_key = str(args.get('numerator', ''))
    den_key = str(args.get('denominator', ''))
    num = computed.get(num_key)
    den = computed.get(den_key)
    if num is None or den is None:
        return float('nan')
    try:
        n = float(num)
        d = float(den)
    except Exception:
        return float('nan')
    if abs(d) < 1e-12:
        return float('nan')
    return n / d


def _first_peak_to_tail_ratio(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    """Ratio of the absolute first peak (max |x|) to the steady-state tail mean.

    For DC: tail_mean is the steady value; for AC envelopes the harness already
    provides tail_rms_v, which is what we should compare against.
    """
    vout = _to_float_list(payload.get('vout_v'))
    is_ac = bool(computed.get('is_ac_output', False))
    if not vout:
        return float('nan')
    tail_ref = computed.get('tail_rms_v') if is_ac else computed.get('tail_mean_v')
    if tail_ref is None or not math.isfinite(float(tail_ref)) or abs(float(tail_ref)) < 1e-12:
        return float('nan')
    peak = max(abs(v) for v in vout)
    return peak / abs(float(tail_ref))


def _zero_crossings_in_tail(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    vout = _to_float_list(payload.get('vout_v'))
    if len(vout) < 20:
        return 0.0
    is_ac = bool(computed.get('is_ac_output', False))
    if is_ac:
        # For AC, zero-crossings of the line frequency dominate; not informative here.
        # Use the deviation from tail RMS to detect envelope ringing instead.
        tail_ref = float(computed.get('tail_rms_v', 0.0))
        i0 = int(0.6 * len(vout))
        deviations = [abs(v) - tail_ref for v in vout[i0:]]
    else:
        tail_ref = float(computed.get('tail_mean_v', 0.0))
        i0 = int(0.6 * len(vout))
        deviations = [v - tail_ref for v in vout[i0:]]
    if len(deviations) < 2:
        return 0.0
    crossings = 0
    prev = deviations[0]
    for current in deviations[1:]:
        if (prev >= 0.0) != (current >= 0.0):
            crossings += 1
        prev = current
    return float(crossings)


def _dominant_osc_hz(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    """FFT-based dominant tail frequency. Returns NaN if signal is too short or fs unknown."""
    signal_key = str(args.get('signal', 'vout_v'))
    values = _to_float_list(payload.get(signal_key))
    time_s = _to_float_list(payload.get('time_s'))
    if len(values) < 64 or len(time_s) < 64:
        return float('nan')
    n = min(len(values), len(time_s))
    if n < 64:
        return float('nan')
    # Use the tail half so we capture steady-state oscillation, not the initial transient.
    i0 = n // 2
    seg = values[i0:n]
    t = time_s[i0:n]
    if len(seg) < 32:
        return float('nan')
    dt = (t[-1] - t[0]) / max(len(t) - 1, 1)
    if dt <= 0.0:
        return float('nan')
    # Remove DC (or AC fundamental envelope) by subtracting mean
    mean = sum(seg) / len(seg)
    seg = [v - mean for v in seg]
    return _peak_freq_dft(seg, fs=1.0 / dt)


def _fundamental_hz(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    signal_key = str(args.get('signal', 'va_v'))
    values = _to_float_list(payload.get(signal_key))
    time_s = _to_float_list(payload.get('time_s'))
    if len(values) < 64 or len(time_s) < 64:
        return float('nan')
    n = min(len(values), len(time_s))
    dt = (time_s[-1] - time_s[0]) / max(n - 1, 1)
    if dt <= 0.0:
        return float('nan')
    seg = values[:n]
    mean = sum(seg) / len(seg)
    seg = [v - mean for v in seg]
    return _peak_freq_dft(seg, fs=1.0 / dt)


def _thd_fft(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    """Approximate THD (% of fundamental) via DFT of a single phase signal.

    Returns NaN if no clear fundamental can be located.
    """
    signal_key = str(args.get('signal', 'va_v'))
    values = _to_float_list(payload.get(signal_key))
    time_s = _to_float_list(payload.get('time_s'))
    if len(values) < 256 or len(time_s) < 256:
        return float('nan')
    n = min(len(values), len(time_s))
    dt = (time_s[-1] - time_s[0]) / max(n - 1, 1)
    if dt <= 0.0:
        return float('nan')
    seg = values[:n]
    mean = sum(seg) / len(seg)
    seg = [v - mean for v in seg]
    fs = 1.0 / dt
    mags, _m = _dft_magnitudes(seg)
    if not mags:
        return float('nan')
    # Find fundamental bin = bin with max magnitude (excluding DC bin 0).
    f_bin = max(range(1, len(mags)), key=lambda k: mags[k])
    f0_mag = mags[f_bin]
    if f0_mag <= 0.0:
        return float('nan')
    # Sum harmonics 2..10 of the fundamental, snapping to nearest integer bin.
    harmonic_power = 0.0
    for k in range(2, 11):
        bin_idx = f_bin * k
        if bin_idx >= len(mags):
            break
        harmonic_power += mags[bin_idx] ** 2
    if harmonic_power <= 0.0:
        return 0.0
    return math.sqrt(harmonic_power) / f0_mag * 100.0


def _phase_balance_pct(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    keys = list(args.get('signals', ['va_v', 'vb_v', 'vc_v']))
    rms_per_phase: list[float] = []
    for key in keys:
        values = _to_float_list(payload.get(key))
        if len(values) < 32:
            return float('nan')
        # Use steady-state tail (last 30%) for a stable RMS estimate.
        i0 = int(0.7 * len(values))
        seg = values[i0:]
        if not seg:
            return float('nan')
        rms = math.sqrt(sum(v * v for v in seg) / len(seg))
        rms_per_phase.append(rms)
    if not rms_per_phase:
        return float('nan')
    mean_rms = sum(rms_per_phase) / len(rms_per_phase)
    if abs(mean_rms) < 1e-9:
        return float('nan')
    spread = max(rms_per_phase) - min(rms_per_phase)
    return spread / mean_rms * 100.0


def _ratio_to_fsw(payload: dict[str, Any], computed: dict[str, float], args: dict[str, Any]) -> float:
    fsw = float(payload.get('fsw_hz', 0.0) or 0.0)
    if fsw <= 0.0:
        return float('nan')
    freq_metric = str(args.get('freq_metric', 'dominant_osc_hz'))
    f = computed.get(freq_metric)
    if f is None:
        return float('nan')
    try:
        return float(f) / fsw
    except Exception:
        return float('nan')


EXTRACTORS: dict[str, ExtractorFn] = {
    'ratio': _ratio,
    'first_peak_to_tail_ratio': _first_peak_to_tail_ratio,
    'zero_crossings_in_tail': _zero_crossings_in_tail,
    'dominant_osc_hz': _dominant_osc_hz,
    'fundamental_hz': _fundamental_hz,
    'thd_fft': _thd_fft,
    'phase_balance_pct': _phase_balance_pct,
    'ratio_to_fsw': _ratio_to_fsw,
}


# ---------------------------------------------------------------------------
# Tiny self-contained DFT (avoid numpy dep here for portability)
# ---------------------------------------------------------------------------

def _decimate_for_dft(samples: list[float]) -> tuple[list[float], int]:
    """Return a decimated signal suitable for an O(N*K) DFT plus the decimation step.

    Caps the DFT cost by reducing the sample count so the O(m*n_bins) work
    stays bounded. Returns (sig, step).
    """
    n = len(samples)
    bin_target = min(n // 2, 512)
    step = max(1, n // (bin_target * 2))
    return samples[::step], step


def _dft_magnitudes(samples: list[float]) -> tuple[list[float], int]:
    sig, _step = _decimate_for_dft(samples)
    m = len(sig)
    n_bins = min(m // 2, 512)
    if n_bins < 4:
        return [], m
    mags: list[float] = [0.0] * n_bins
    two_pi_over_m = 2.0 * math.pi / m
    for k in range(n_bins):
        re = 0.0
        im = 0.0
        c = two_pi_over_m * k
        for i, x in enumerate(sig):
            re += x * math.cos(c * i)
            im -= x * math.sin(c * i)
        mags[k] = math.sqrt(re * re + im * im) / m
    return mags, m


def _peak_freq_dft(samples: list[float], fs: float) -> float:
    mags, m = _dft_magnitudes(samples)
    if not mags or m <= 0:
        return float('nan')
    _sig, step = _decimate_for_dft(samples)
    fs_eff = fs / step
    if len(mags) < 2:
        return float('nan')
    k_peak = max(range(1, len(mags)), key=lambda k: mags[k])
    # Bin k of an m-point DFT at sample rate fs_eff = k * fs_eff / m.
    return k_peak * fs_eff / m


# ---------------------------------------------------------------------------
# Playbook loading
# ---------------------------------------------------------------------------

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent.parent / 'knowledge' / 'observation_playbooks'


def load_playbook(topology: str) -> dict[str, Any]:
    """Resolve a playbook for *topology*.

    Resolution order: exact topology id -> family-specific fallback if any
    playbook lists the family in `applies_to_families` -> `_default.json`.
    Returns a dict (never None).
    """
    base = PLAYBOOK_DIR
    topology = (topology or '').strip().lower()
    if topology:
        exact = base / f'{topology}.json'
        if exact.exists():
            return json.loads(exact.read_text(encoding='utf-8'))

    fam = power_stage_family(topology) if topology else ''
    if fam:
        for path in base.glob('*.json'):
            if path.name.startswith('_'):
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            families = data.get('applies_to_families') or []
            if isinstance(families, list) and (fam in families or '*' in families):
                return data

    default_path = base / '_default.json'
    if default_path.exists():
        return json.loads(default_path.read_text(encoding='utf-8'))
    return {'topology': '_empty', 'metrics': [], 'pathologies': [], 'key_signals': []}


# ---------------------------------------------------------------------------
# Apply playbook
# ---------------------------------------------------------------------------

def apply_playbook(
    playbook: dict[str, Any],
    payload: dict[str, Any],
    computed: dict[str, float],
    target_v: float,
    settling_time_ms_max: float,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Run extractors and rule-based pathology matchers.

    Returns (extracted_metrics, matched_pathologies). `extracted_metrics` is a
    flat dict that callers can attach to ResponseAnalysisReport.playbook_metrics.
    Each matched pathology is a small dict with id/implies/explanation so the
    diagnoser receives both label and rationale.
    """
    extracted: dict[str, float] = {}
    # Build a running view that exposes both the harness-computed metrics and
    # any metrics produced by earlier playbook extractors. Order in the
    # playbook therefore matters: a metric that depends on another must be
    # listed after its dependency.
    running_view: dict[str, float] = {}
    for k, v in computed.items():
        try:
            running_view[k] = float(v)
        except Exception:
            continue
    metrics_specs = playbook.get('metrics') or []
    if isinstance(metrics_specs, list):
        for spec in metrics_specs:
            if not isinstance(spec, dict):
                continue
            metric_id = str(spec.get('id', '')).strip()
            extractor_id = str(spec.get('extractor', '')).strip()
            if not metric_id or extractor_id not in EXTRACTORS:
                continue
            args = spec.get('args') or {}
            if not isinstance(args, dict):
                args = {}
            try:
                value = EXTRACTORS[extractor_id](payload, running_view, args)
            except Exception:
                value = float('nan')
            extracted[metric_id] = value
            try:
                running_view[metric_id] = float(value)
            except Exception:
                pass

    namespace: dict[str, float] = {}
    for k, v in computed.items():
        try:
            namespace[k] = float(v)
        except Exception:
            continue
    namespace.update({k: v for k, v in extracted.items() if isinstance(v, (int, float))})
    namespace['abs_target'] = abs(float(target_v)) if target_v else 0.0
    namespace['settling_time_ms_max'] = float(settling_time_ms_max)

    matched: list[dict[str, Any]] = []
    for path_spec in playbook.get('pathologies') or []:
        if not isinstance(path_spec, dict):
            continue
        rule = path_spec.get('when')
        if not _evaluate_rule(rule, namespace):
            continue
        matched.append(
            {
                'id': str(path_spec.get('id', 'unknown')),
                'implies': str(path_spec.get('implies', '')),
                'explanation': str(path_spec.get('explanation', '')),
            }
        )
    return extracted, matched


# ---------------------------------------------------------------------------
# Rule evaluator
# ---------------------------------------------------------------------------

_OPS: dict[str, Callable[[float, float], bool]] = {
    '>': operator.gt,
    '>=': operator.ge,
    '<': operator.lt,
    '<=': operator.le,
    '==': operator.eq,
    '!=': operator.ne,
}


def _evaluate_rule(rule: Any, namespace: dict[str, float]) -> bool:
    if rule is None:
        return False
    if not isinstance(rule, dict):
        return False
    if 'all' in rule:
        sub = rule['all']
        if not isinstance(sub, list) or not sub:
            return False
        return all(_evaluate_rule(r, namespace) for r in sub)
    if 'any' in rule:
        sub = rule['any']
        if not isinstance(sub, list) or not sub:
            return False
        return any(_evaluate_rule(r, namespace) for r in sub)

    metric = rule.get('metric')
    op_str = rule.get('op')
    raw_value = rule.get('value')
    if metric is None or op_str not in _OPS:
        return False
    actual = namespace.get(str(metric))
    if actual is None or not isinstance(actual, (int, float)) or not math.isfinite(float(actual)):
        return False
    expected = _resolve_value(raw_value, namespace)
    if expected is None or not math.isfinite(expected):
        return False
    return _OPS[op_str](float(actual), float(expected))


def _resolve_value(raw: Any, namespace: dict[str, float]) -> float | None:
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    expr = raw.strip()
    if not expr:
        return None
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError:
        return None
    return _eval_safe(tree.body, namespace)


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_safe(node: ast.AST, namespace: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        return None
    if isinstance(node, ast.Name):
        if node.id in namespace:
            return float(namespace[node.id])
        return None
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        inner = _eval_safe(node.operand, namespace)
        if inner is None:
            return None
        return _ALLOWED_UNARYOPS[type(node.op)](inner)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_safe(node.left, namespace)
        right = _eval_safe(node.right, namespace)
        if left is None or right is None:
            return None
        try:
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        except ZeroDivisionError:
            return None
    return None
