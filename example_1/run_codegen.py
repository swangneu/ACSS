from __future__ import annotations

import os, re, json
from pathlib import Path
import yaml
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
YML  = ROOT / "configs" / "requirement.yml"
SLX_DIGEST = ROOT / "model" / "model_digest.json"

OUT_M = ROOT / "src" / "parameter_1.m"
OUT_C = ROOT / "src" / "control_1.c"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-reasoner"  # or deepseek-chat

def extract_two_blocks(text: str) -> tuple[str, str]:
    blocks = re.findall(r"```(\w+)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if len(blocks) < 2:
        raise ValueError("Need two fenced code blocks (matlab and c).")

    matlab_code = None
    c_code = None
    for lang, body in blocks:
        lang = (lang or "").lower()
        if matlab_code is None and lang in ("matlab", "m"):
            matlab_code = body.strip()
        if c_code is None and lang == "c":
            c_code = body.strip()

    # fallback by order
    if matlab_code is None:
        matlab_code = blocks[0][1].strip()
    if c_code is None:
        c_code = blocks[1][1].strip()
    return matlab_code, c_code

def build_prompt(model_digest: dict, req: dict, extra_text: str) -> str:
    return f"""
You are generating TWO source files for a Simulink project.

INPUTS YOU MUST USE:
(1) Simulink model digest (compiled metadata):
{json.dumps(model_digest, indent=2)}

(2) Structured requirements YAML:
{yaml.safe_dump(req, sort_keys=False)}

(3) Additional requirements:
{extra_text}

OUTPUT REQUIREMENTS (STRICT):
- Output EXACTLY TWO fenced code blocks and nothing else:
  1) ```matlab ...``` = full content of src/parameter_1.m
  2) ```c ...```      = full content of src/control_1.c

parameter_1.m rules:
- Must define `par = struct();`
- Must map YAML values to par fields with comments referencing YAML paths.
- Must also include any sample time if present in model digest or YAML (prefer model digest compiled sample time if available).
- Do NOT invent rated values not in YAML; if a value is missing, add a clearly-marked default.

control_1.c rules:
- Plain C controller core ONLY (no SimStruct, no mex, no S-function boilerplate).
- Must match I/O dimensions inferred from model digest S-function CompiledPortDimensions:
  - Implement ctrl_step(st, p, u, nu, y, ny) with defensive dimension checks.
- No dynamic allocation.
- Include a short header comment describing input/output meaning.
- If the digest implies multiple S-functions, generate code for the FIRST one unless additional requirements specify otherwise.
""".strip()

def main():
    # load inputs
    req = yaml.safe_load(YML.read_text(encoding="utf-8"))
    model_digest = json.loads(SLX_DIGEST.read_text(encoding="utf-8"))

    # your free-text requirement (can be CLI arg / file read)
    extra_text = (ROOT / "configs" / "extra_requirements.txt").read_text(encoding="utf-8") \
        if (ROOT / "configs" / "extra_requirements.txt").exists() \
        else "Implement a discrete-time PI controller with saturation and anti-windup where relevant."

    prompt = build_prompt(model_digest, req, extra_text)

    # call deepseek
    load_dotenv()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not found (use .env or environment variable).")

    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "Follow formatting constraints exactly. No extra commentary."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    text = resp.choices[0].message.content
    m_code, c_code = extract_two_blocks(text)

    OUT_M.parent.mkdir(parents=True, exist_ok=True)
    OUT_C.parent.mkdir(parents=True, exist_ok=True)
    OUT_M.write_text(m_code + "\n", encoding="utf-8")
    OUT_C.write_text(c_code + "\n", encoding="utf-8")

    print(f"Wrote {OUT_M}")
    print(f"Wrote {OUT_C}")

if __name__ == "__main__":
    main()
