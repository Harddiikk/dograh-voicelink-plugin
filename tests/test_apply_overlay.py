#!/usr/bin/env python3
"""Self-contained regression tests for scripts/apply_overlay.py.

No external deps, no Dograh checkout required: each test synthesizes a minimal
Dograh-shaped fixture in a temp dir, runs the real overlay CLI against it, and
asserts the result with Python's AST. Covers the layouts that previously broke
the engine (paren-in-comment, no-trailing-comma, tab indent) plus rollback.

Run:  python tests/test_apply_overlay.py     (exits non-zero on any failure)
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "apply_overlay.py"

ENUMS = """\
from enum import Enum


class WorkflowRunMode(Enum):
    TWILIO = "twilio"
    PLIVO = "plivo"{trail}
"""

PROVIDERS_INIT = """\
\"\"\"Telephony provider implementations (registration via side-effect import).\"\"\"

from api.services.telephony.providers import (  # noqa: F401  -- side effects (registration)
    plivo,
    twilio{trail}
)
"""

TELE_CONFIG = """\
from typing import Annotated, List, Optional, Union

from pydantic import BaseModel, Field

from api.services.telephony.providers.twilio.config import (
    TwilioConfigurationRequest,
    TwilioConfigurationResponse,
)

TelephonyConfigRequest = Annotated[
    Union[
        TwilioConfigurationRequest{trail}
    ],
    Field(discriminator="provider"),
]


class TelephonyConfigurationResponse(BaseModel):
    \"\"\"Top-level telephony configuration response.\"\"\"

    twilio: Optional[TwilioConfigurationResponse] = None


__all__ = [
    "TwilioConfigurationRequest",
    "TwilioConfigurationResponse"{trail}
]
"""


def write_fixture(root: Path, *, trailing_comma: bool, tab_enum: bool = False) -> None:
    trail = "," if trailing_comma else ""
    (root / "api" / "services" / "telephony" / "providers").mkdir(parents=True, exist_ok=True)
    (root / "api" / "schemas").mkdir(parents=True, exist_ok=True)
    enums = ENUMS.format(trail=trail)
    if tab_enum:
        enums = enums.replace('    TWILIO', '\tTWILIO').replace('    PLIVO', '\tPLIVO')
    (root / "api" / "enums.py").write_text(enums)
    (root / "api" / "services" / "telephony" / "providers" / "__init__.py").write_text(
        PROVIDERS_INIT.format(trail=trail))
    (root / "api" / "schemas" / "telephony_config.py").write_text(
        TELE_CONFIG.format(trail=trail))


def run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dograh-root", str(root), *extra],
        capture_output=True, text=True,
    )


def assert_wired(root: Path) -> list[str]:
    errs: list[str] = []
    # enums
    wf = [n for n in ast.walk(ast.parse((root / "api/enums.py").read_text()))
          if isinstance(n, ast.ClassDef) and n.name == "WorkflowRunMode"][0]
    vals = {t.id: a.value.value for a in wf.body if isinstance(a, ast.Assign)
            for t in a.targets if isinstance(t, ast.Name) and isinstance(a.value, ast.Constant)}
    if vals.get("VOICELINK") != "voicelink":
        errs.append("enums: VOICELINK member missing/wrong")
    # providers import
    ini = ast.parse((root / "api/services/telephony/providers/__init__.py").read_text())
    names = {a.name for n in ast.walk(ini) if isinstance(n, ast.ImportFrom) for a in n.names}
    if "voicelink" not in names:
        errs.append("providers/__init__: voicelink not a real import name")
    # schema
    tc = ast.parse((root / "api/schemas/telephony_config.py").read_text())
    resp = [n for n in ast.walk(tc) if isinstance(n, ast.ClassDef)
            and n.name == "TelephonyConfigurationResponse"][0]
    fields = [a.target.id for a in resp.body
              if isinstance(a, ast.AnnAssign) and isinstance(a.target, ast.Name)]
    if "voicelink" not in fields:
        errs.append("schema: voicelink not a real AnnAssign field")
    alls = [n for n in ast.walk(tc) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)]
    exports = {e.value for e in alls[0].value.elts if isinstance(e, ast.Constant)} if alls else set()
    if not {"VoiceLinkConfigurationRequest", "VoiceLinkConfigurationResponse"} <= exports:
        errs.append("schema: __all__ missing voicelink exports (or string-concatenated!)")
    # the original twilio exports must survive intact (concat would mangle them)
    if not {"TwilioConfigurationRequest", "TwilioConfigurationResponse"} <= exports:
        errs.append("schema: original __all__ exports were mangled")
    src = (root / "api/schemas/telephony_config.py").read_text()
    union_src = src.split("class TelephonyConfigurationResponse")[0].split("Annotated[")[1]
    if "VoiceLinkConfigurationRequest" not in union_src:
        errs.append("schema: VoiceLink not in discriminated union")
    return errs


def case(name: str, fn) -> bool:
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False


def main() -> int:
    results: list[bool] = []

    for tc_name, kwargs in [
        ("standard (trailing commas)", dict(trailing_comma=True)),
        ("no-trailing-comma layout", dict(trailing_comma=False)),
        ("tab-indented enum", dict(trailing_comma=True, tab_enum=True)),
    ]:
        def _t(kwargs=kwargs):
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                write_fixture(root, **kwargs)
                r = run(root)
                assert r.returncode == 0, f"exit {r.returncode}\n{r.stdout}\n{r.stderr}"
                errs = assert_wired(root)
                assert not errs, "; ".join(errs)
                # idempotent: 2nd run makes no structural change
                r2 = run(root)
                assert r2.returncode == 0, f"rerun exit {r2.returncode}\n{r2.stdout}"
                assert "+ add" not in r2.stdout and "+ wire" not in r2.stdout, \
                    f"not idempotent:\n{r2.stdout}"
        results.append(case(tc_name, _t))

    def _rollback():
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_fixture(root, trailing_comma=True)
            # inject a pre-existing syntax error so the post-edit compile fails
            ep = root / "api/enums.py"
            ep.write_text(ep.read_text() + "\ndef broken(:\n    pass\n")
            before = ep.read_text()
            r = run(root)
            assert r.returncode == 3, f"expected exit 3 (rollback), got {r.returncode}\n{r.stdout}"
            assert "COMPILE-CHECK FAILED" in r.stdout, f"no compile-fail message\n{r.stdout}"
            # the edited files must be restored to their (broken) originals
            assert ep.read_text() == before, "enums.py was not rolled back"
            sc = (root / "api/schemas/telephony_config.py").read_text()
            assert "voicelink" not in sc, "telephony_config.py was not rolled back"
    results.append(case("rollback on compile failure", _rollback))

    print()
    if all(results):
        print(f"ALL {len(results)} CASES PASS ✅")
        return 0
    print(f"{results.count(False)}/{len(results)} CASES FAILED ❌")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
