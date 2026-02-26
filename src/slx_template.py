from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


@dataclass
class SFunctionSpec:
    function_name: str
    module_name: str
    input_width: int
    output_width: int


@dataclass
class SlxTemplateInfo:
    parameter_symbols: list[str]
    sfunction: SFunctionSpec


def load_template_info(slx_path: Path) -> SlxTemplateInfo:
    with zipfile.ZipFile(slx_path, 'r') as zf:
        system_root = zf.read('simulink/systems/system_root.xml').decode('utf-8')
        root = ET.fromstring(system_root)

        param_symbols = sorted(set(re.findall(r'par\.([A-Za-z_][A-Za-z0-9_]*)', system_root)))

        sfun_block = None
        for block in root.findall('Block'):
            if block.attrib.get('BlockType') == 'S-Function':
                sfun_block = block
                break

        if sfun_block is None:
            return SlxTemplateInfo(
                parameter_symbols=param_symbols,
                sfunction=SFunctionSpec(
                    function_name='control_sfunc',
                    module_name='control_sfunc_wrapper.c',
                    input_width=1,
                    output_width=1,
                ),
            )

        function_name = _get_block_param(sfun_block, 'FunctionName') or 'control_sfunc'
        module_name = _get_block_param(sfun_block, 'SFunctionModules') or 'control_sfunc_wrapper.c'
        module_name = module_name.strip().split()[0]

        input_width = 1
        output_width = 1

        wizard_ref = _get_block_ref(sfun_block, 'WizardData')
        if wizard_ref:
            entry_name = _entry_name_from_bdmx_ref(wizard_ref)
            if entry_name and entry_name in zf.namelist():
                raw = zf.read(entry_name).decode('latin1', errors='ignore').replace('\x00', '')
                widths = _extract_widths(raw, function_name)
                if widths is not None:
                    input_width, output_width = widths

        return SlxTemplateInfo(
            parameter_symbols=param_symbols,
            sfunction=SFunctionSpec(
                function_name=function_name,
                module_name=module_name,
                input_width=input_width,
                output_width=output_width,
            ),
        )


def _extract_widths(raw: str, function_name: str) -> tuple[int, int] | None:
    # In S-Function Builder mxarray blobs, the first two width vectors after
    # the function name correspond to input and output widths.
    pattern = re.escape(function_name) + r'.{0,1200}?\[(\d+),\s*1\].{0,600}?\[(\d+),\s*1\]'
    m = re.search(pattern, raw, re.S)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _entry_name_from_bdmx_ref(ref: str) -> str | None:
    prefix = 'bdmxdata:'
    if not ref.startswith(prefix):
        return None
    return f"simulink/bdmxdata/{ref[len(prefix):]}.mxarray"


def _get_block_param(block: ET.Element, name: str) -> str | None:
    for p in block.findall('P'):
        if p.attrib.get('Name') == name:
            return p.text or ''
    return None


def _get_block_ref(block: ET.Element, name: str) -> str | None:
    for p in block.findall('P'):
        if p.attrib.get('Name') == name:
            return p.attrib.get('Ref')
    return None
