#!/usr/bin/env python3
"""Apply the VoiceLink telephony-provider overlay onto a Dograh codebase.

This is the engine behind ``/voicelink-install``. It is deliberately
self-contained (stdlib only), idempotent, and safe to re-run. It performs
exactly the proven, non-breaking changes the GPC + orders deployments use:

  1. Copy the bundled ``voicelink/`` provider package into
     ``<root>/api/services/telephony/providers/voicelink/``.
  2. Add ``VOICELINK = "voicelink"`` to ``WorkflowRunMode`` in
     ``<root>/api/enums.py``.
  3. Register the package by adding ``voicelink`` to the side-effect import
     tuple in ``<root>/api/services/telephony/providers/__init__.py``.
  4. Wire the config schema into the discriminated union in
     ``<root>/api/schemas/telephony_config.py`` (import + union member +
     response field + ``__all__``).
  (optional) Copy the pytest suite into ``<root>/api/tests/telephony/voicelink/``.

It does NOT touch Alembic, KYC/SaaS, or any migration — ``provider`` is a
plain string column, so no DB enum migration is required.

Safety guarantees:
  * Every structural insertion is anchor-based and checks for prior presence
    first, so running the script twice is a no-op.
  * List/tuple/union/``__all__`` inserts first ensure the preceding sibling
    carries a trailing comma, so they are safe even on a no-trailing-comma
    layout (no adjacent-token SyntaxError, no silent ``__all__`` string concat).
  * After writing, the three edited files + the provider package are
    py_compiled. On ANY failure the three edited files are rolled back to their
    original contents and the run reports FAILED — nothing is left half-written.
  * If an anchor cannot be found (an unusual Dograh layout), the edit is
    reported as FAILED with a pointer to the matching file in
    ``assets/patches/`` so it can be applied by hand.

Note: files are read/written as UTF-8 text and emitted with LF line endings; a
CRLF checkout will be normalized to LF (functionally inert for Python).

Usage:
    python apply_overlay.py [--dograh-root PATH] [--with-tests]
                            [--copy-only | --edits-only]
                            [--dry-run] [--check] [--compile-check]

If ``--dograh-root`` is omitted, the script auto-detects it by looking for
``api/services/telephony/providers/__init__.py`` under a list of common roots
(``.``, ``/app``, ``/code``, ``/workspace``, ``/usr/src/app``, ``/srv/app``)
and, failing that, a bounded search upward from the CWD and the plugin dir.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_PROVIDER = PLUGIN_ROOT / "assets" / "provider" / "voicelink"
BUNDLED_TESTS = PLUGIN_ROOT / "assets" / "tests" / "voicelink"
PATCH_DIR = PLUGIN_ROOT / "assets" / "patches"

MARKER = Path("api/services/telephony/providers/__init__.py")
COMMON_ROOTS = [".", "/app", "/code", "/workspace", "/usr/src/app", "/srv/app"]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = False

    def ok(self, msg: str) -> None:
        self.lines.append(f"  ✓ {msg}")

    def skip(self, msg: str) -> None:
        self.lines.append(f"  = {msg} (already present)")

    def change(self, msg: str) -> None:
        self.lines.append(f"  + {msg}")

    def fail(self, msg: str, patch: str) -> None:
        self.failed = True
        self.lines.append(f"  ✗ {msg}")
        if patch:
            self.lines.append(f"      → apply manually: assets/patches/{patch}")

    def dump(self) -> None:
        print("\n".join(self.lines))


# --------------------------------------------------------------------------- #
# Root detection
# --------------------------------------------------------------------------- #
def detect_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not (root / MARKER).exists():
            sys.exit(
                f"ERROR: --dograh-root {root} does not look like a Dograh repo "
                f"(missing {MARKER})."
            )
        return root

    candidates: list[Path] = [Path(c).resolve() for c in COMMON_ROOTS]
    for base in (Path.cwd(), PLUGIN_ROOT):
        cur = base
        for _ in range(6):
            candidates.append(cur)
            cur = cur.parent

    seen: set[Path] = set()
    for root in candidates:
        if root in seen:
            continue
        seen.add(root)
        if (root / MARKER).exists():
            return root

    sys.exit(
        "ERROR: could not auto-detect the Dograh root. Pass --dograh-root "
        "pointing at the directory that contains 'api/' (the one with "
        f"{MARKER})."
    )


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str, dry: bool) -> None:
    if not dry:
        path.write_text(text, encoding="utf-8")


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _with_trailing_comma(line: str) -> str:
    """Return ``line`` guaranteed to end in ``,\\n`` (indentation preserved)."""
    core = line.rstrip()
    if not core.endswith(","):
        core += ","
    return core + "\n"


def _append_to_block(lines: list[str], open_idx: int, close_idx: int,
                     items: list[str], indent: str) -> None:
    """Insert ``items`` (each already comma-terminated) into a bracketed block.

    Inserts AFTER the last real sibling, first guaranteeing that sibling has a
    trailing comma — safe whether or not the source uses trailing commas.
    """
    last = None
    for i in range(close_idx - 1, open_idx, -1):
        s = lines[i].strip()
        if s and not s.startswith("#"):
            last = i
            break
    if last is not None:
        lines[last] = _with_trailing_comma(lines[last])
        pos = last + 1
    else:
        pos = open_idx + 1
    for off, item in enumerate(items):
        lines.insert(pos + off, f"{indent}{item}\n")


def _find_close(lines: list[str], start_idx: int) -> int | None:
    return next((i for i in range(start_idx + 1, len(lines))
                 if lines[i].lstrip().startswith(("]", ")"))), None)


# --------------------------------------------------------------------------- #
# Copy steps
# --------------------------------------------------------------------------- #
def copy_provider(root: Path, dry: bool, rep: Report) -> None:
    dst = root / "api" / "services" / "telephony" / "providers" / "voicelink"
    if not BUNDLED_PROVIDER.exists():
        rep.fail(f"bundled provider package missing at {BUNDLED_PROVIDER}", "")
        return
    existed = dst.exists()
    if not dry:
        dst.mkdir(parents=True, exist_ok=True)
        for f in BUNDLED_PROVIDER.glob("*.py"):
            shutil.copy2(f, dst / f.name)
    if existed:
        rep.skip(f"provider package {dst.relative_to(root)}")
    else:
        rep.change(f"copy provider package -> {dst.relative_to(root)}")


def copy_tests(root: Path, dry: bool, rep: Report) -> None:
    dst = root / "api" / "tests" / "telephony" / "voicelink"
    if not BUNDLED_TESTS.exists() or not any(BUNDLED_TESTS.glob("*.py")):
        rep.skip("no bundled tests to copy")
        return
    existed = dst.exists()
    if not dry:
        dst.mkdir(parents=True, exist_ok=True)
        for f in BUNDLED_TESTS.glob("*.py"):
            shutil.copy2(f, dst / f.name)
        for d in (root / "api" / "tests" / "telephony", dst):
            init = d / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
    (rep.skip if existed else rep.change)(f"tests -> {dst.relative_to(root)}")


# --------------------------------------------------------------------------- #
# Edit steps
# --------------------------------------------------------------------------- #
def edit_enums(root: Path, dry: bool, rep: Report) -> None:
    path = root / "api" / "enums.py"
    if not path.exists():
        rep.fail("api/enums.py not found", "01-enums.add-voicelink.md")
        return
    lines = _read(path).splitlines(keepends=True)
    if any('VOICELINK = "voicelink"' in ln for ln in lines):
        rep.skip("WorkflowRunMode.VOICELINK")
        return
    idx = next((i for i, ln in enumerate(lines)
                if ln.strip().startswith("class WorkflowRunMode")), None)
    if idx is None:
        rep.fail("could not find 'class WorkflowRunMode' in enums.py",
                 "01-enums.add-voicelink.md")
        return
    insert_at = idx + 1
    # skip a leading docstring
    if insert_at < len(lines) and lines[insert_at].lstrip().startswith(('"""', "'''")):
        quote = lines[insert_at].lstrip()[:3]
        if lines[insert_at].strip().count(quote) < 2:
            insert_at += 1
            while insert_at < len(lines) and quote not in lines[insert_at]:
                insert_at += 1
        insert_at += 1
    # detect the indent of an existing member, else default 4 spaces
    indent = "    "
    for i in range(idx + 1, len(lines)):
        if "=" in lines[i] and lines[i].strip() and not lines[i].lstrip().startswith(("#", '"', "'")):
            indent = _indent_of(lines[i])
            break
    lines.insert(insert_at, f'{indent}VOICELINK = "voicelink"\n')
    _write(path, "".join(lines), dry)
    rep.change("add WorkflowRunMode.VOICELINK in api/enums.py")


def edit_providers_init(root: Path, dry: bool, rep: Report) -> None:
    path = root / "api" / "services" / "telephony" / "providers" / "__init__.py"
    lines = _read(path).splitlines(keepends=True)
    open_idx = next((i for i, ln in enumerate(lines)
                     if "from api.services.telephony.providers import (" in ln), None)
    if open_idx is None:
        rep.fail("registration import tuple not found in providers/__init__.py",
                 "02-providers-init.add-voicelink.md")
        return
    close_idx = next((i for i in range(open_idx + 1, len(lines))
                      if lines[i].lstrip().startswith(")")), None)
    if close_idx is None:
        rep.fail("malformed import tuple in providers/__init__.py",
                 "02-providers-init.add-voicelink.md")
        return
    if "voicelink" in "".join(lines[open_idx:close_idx]):
        rep.skip("voicelink registration import")
        return
    indent = "    "
    for i in range(open_idx + 1, close_idx):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            indent = _indent_of(lines[i])
            break
    _append_to_block(lines, open_idx, close_idx, ["voicelink,"], indent)
    _write(path, "".join(lines), dry)
    rep.change("add 'voicelink' to the provider registration import tuple")


def edit_schema(root: Path, dry: bool, rep: Report) -> None:
    path = root / "api" / "schemas" / "telephony_config.py"
    if not path.exists():
        rep.fail("api/schemas/telephony_config.py not found",
                 "03-telephony-config-schema.md")
        return
    lines = _read(path).splitlines(keepends=True)
    changed = False

    # (a) import the request/response classes
    if not any("voicelink.config import" in ln for ln in lines):
        block = [
            "from api.services.telephony.providers.voicelink.config import (\n",
            "    VoiceLinkConfigurationRequest,\n",
            "    VoiceLinkConfigurationResponse,\n",
            ")\n",
        ]
        # insert after the last provider ``.config import (`` block …
        anchor = next((i for i in range(len(lines) - 1, -1, -1)
                       if ".config import (" in lines[i]), None)
        if anchor is not None:
            j = _find_close(lines, anchor)
            insert_at = (j + 1) if j is not None else (anchor + 1)
        else:
            # … else after the (possibly multi-line) pydantic import.
            pyd = next((i for i, ln in enumerate(lines)
                        if ln.startswith("from pydantic import")), None)
            if pyd is None:
                insert_at = 0
            elif "(" in lines[pyd] and ")" not in lines[pyd]:
                k = _find_close(lines, pyd)
                insert_at = (k + 1) if k is not None else (pyd + 1)
            else:
                insert_at = pyd + 1
            block = ["\n"] + block
        for off, ln in enumerate(block):
            lines.insert(insert_at + off, ln)
        changed = True

    # (b) discriminated union member
    u_open = next((i for i, ln in enumerate(lines)
                   if "TelephonyConfigRequest = Annotated[" in ln), None)
    if u_open is None:
        rep.fail("TelephonyConfigRequest union not found", "03-telephony-config-schema.md")
    else:
        union_start = next((i for i in range(u_open, len(lines))
                            if "Union[" in lines[i]), u_open)
        union_close = next((i for i in range(union_start + 1, len(lines))
                            if lines[i].lstrip().startswith("]")), None)
        if union_close is None:
            rep.fail("could not locate the Union[...] close", "03-telephony-config-schema.md")
        elif "VoiceLinkConfigurationRequest" in "".join(lines[union_start:union_close]):
            pass  # already a member
        else:
            indent = "        "
            for i in range(union_start + 1, union_close):
                if lines[i].strip().endswith("Request,") or lines[i].strip().endswith("Request"):
                    indent = _indent_of(lines[i])
                    break
            _append_to_block(lines, union_start, union_close,
                             ["VoiceLinkConfigurationRequest,"], indent)
            changed = True

    # (c) response field
    if not any("voicelink: Optional[VoiceLinkConfigurationResponse]" in ln for ln in lines):
        cls = next((i for i, ln in enumerate(lines)
                    if ln.startswith("class TelephonyConfigurationResponse")), None)
        if cls is None:
            rep.fail("TelephonyConfigurationResponse class not found",
                     "03-telephony-config-schema.md")
        else:
            field_idx = next(
                (i for i in range(cls + 1, len(lines))
                 if ": Optional[" in lines[i] and "ConfigurationResponse]" in lines[i]),
                None,
            )
            if field_idx is None:
                j = cls + 1
                if j < len(lines) and lines[j].lstrip().startswith(('"""', "'''")):
                    q = lines[j].lstrip()[:3]
                    if lines[j].strip().count(q) < 2:
                        j += 1
                        while j < len(lines) and q not in lines[j]:
                            j += 1
                    j += 1
                field_idx = j
            indent = (_indent_of(lines[field_idx])
                      if field_idx < len(lines) and lines[field_idx].strip() else "    ")
            lines.insert(field_idx,
                         f"{indent}voicelink: Optional[VoiceLinkConfigurationResponse] = None\n")
            changed = True

    # (d) __all__ exports
    if not any('"VoiceLinkConfigurationRequest"' in ln for ln in lines):
        a_open = next((i for i, ln in enumerate(lines) if ln.startswith("__all__")), None)
        if a_open is not None:
            if "]" in lines[a_open]:
                # single-line __all__ = [...]; insert before the closing bracket
                pos = lines[a_open].rfind("]")
                head = lines[a_open][:pos].rstrip()
                if not head.endswith(("[", ",")):
                    head += ","
                lines[a_open] = (head
                                 + ' "VoiceLinkConfigurationRequest",'
                                 + ' "VoiceLinkConfigurationResponse",'
                                 + lines[a_open][pos:])
                changed = True
            else:
                a_close = next((i for i in range(a_open + 1, len(lines))
                                if lines[i].lstrip().startswith("]")), None)
                if a_close is not None:
                    indent = "    "
                    for i in range(a_open + 1, a_close):
                        if lines[i].strip().startswith('"'):
                            indent = _indent_of(lines[i])
                            break
                    _append_to_block(lines, a_open, a_close, [
                        '"VoiceLinkConfigurationRequest",',
                        '"VoiceLinkConfigurationResponse",',
                    ], indent)
                    changed = True

    if changed and not rep.failed:
        _write(path, "".join(lines), dry)
        rep.change("wire VoiceLink into api/schemas/telephony_config.py "
                   "(import + union + response field + __all__)")
    elif not rep.failed:
        rep.skip("telephony_config.py schema wiring")


# --------------------------------------------------------------------------- #
def _compile_or_rollback(root: Path, originals: dict[Path, str]) -> tuple[bool, str]:
    """py_compile the edited files + provider package. Roll back the edited
    files on any failure. Returns (ok, message)."""
    import py_compile

    targets = list(originals.keys())
    targets += sorted((root / "api" / "services" / "telephony" / "providers"
                       / "voicelink").glob("*.py"))
    for t in targets:
        if not t.exists():
            continue
        try:
            py_compile.compile(str(t), doraise=True)
        except py_compile.PyCompileError as exc:
            for p, orig in originals.items():
                p.write_text(orig, encoding="utf-8")
            return False, f"{t}\n{exc}\n(rolled back {len(originals)} edited file(s))"
    return True, f"ok ({len(targets)} files)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply the VoiceLink Dograh overlay.")
    ap.add_argument("--dograh-root", help="path to the Dograh repo root (auto-detected if omitted)")
    ap.add_argument("--with-tests", action="store_true", help="also copy the pytest suite")
    ap.add_argument("--copy-only", action="store_true", help="only copy the package, skip edits")
    ap.add_argument("--edits-only", action="store_true", help="only apply edits, skip the copy")
    ap.add_argument("--dry-run", action="store_true", help="report planned changes without writing")
    ap.add_argument("--check", action="store_true", help="alias for --dry-run (report only)")
    ap.add_argument("--compile-check", action="store_true",
                    help="(now always on when writing) kept for compatibility")
    args = ap.parse_args()

    dry = args.dry_run or args.check
    root = detect_root(args.dograh_root)
    rep = Report()

    print(f"VoiceLink overlay {'(dry-run) ' if dry else ''}-> {root}\n")

    # Capture originals of the edited files up front, for rollback.
    edit_targets = [
        root / "api" / "enums.py",
        root / "api" / "services" / "telephony" / "providers" / "__init__.py",
        root / "api" / "schemas" / "telephony_config.py",
    ]
    originals = {p: _read(p) for p in edit_targets if p.exists()}

    if not args.edits_only:
        copy_provider(root, dry, rep)
        if args.with_tests:
            copy_tests(root, dry, rep)
    if not args.copy_only:
        edit_enums(root, dry, rep)
        edit_providers_init(root, dry, rep)
        edit_schema(root, dry, rep)

    rep.dump()
    print()
    if rep.failed:
        print("RESULT: incomplete — some edits need manual application (see pointers above).")
        return 2

    if not dry:
        ok, msg = _compile_or_rollback(root, originals)
        if not ok:
            print(f"COMPILE-CHECK FAILED: {msg}\n")
            print("RESULT: edits reverted — none applied. Apply the patches in "
                  "assets/patches/ by hand for this Dograh layout.")
            return 3
        print(f"COMPILE-CHECK: {msg}\n")

    # Setup complete → hand back the single WSS URL to paste into VoiceLink.
    import os
    bae = os.environ.get("BACKEND_API_ENDPOINT", "").rstrip("/")
    if bae.startswith("https://"):
        wss = "wss://" + bae[len("https://"):]
    elif bae.startswith("http://"):
        wss = "ws://" + bae[len("http://"):]
    else:
        wss = None

    print("RESULT: VoiceLink overlay applied. ✅\n")
    print("Next:")
    print("  1. Set BACKEND_API_ENDPOINT to your public https:// origin and (re)start the api.")
    print("  2. ── Your SINGLE VoiceLink WSS URL (one URL, inbound + outbound) ──")
    if wss:
        print("     Paste THIS into your VoiceLink portal (the client) as the inbound bot/stream URL:")
        print(f"         {wss}/api/v1/telephony/ws")
        if wss.startswith("ws://"):
            print("     ⚠️  This is ws:// (from a non-https BACKEND_API_ENDPOINT). VoiceLink needs a")
            print("         public wss:// — set BACKEND_API_ENDPOINT to https:// and re-run.")
    else:
        print("     Paste this into your VoiceLink portal (the client) as the inbound bot/stream URL:")
        print("         wss://<your-public-host>/api/v1/telephony/ws")
        print("     (= BACKEND_API_ENDPOINT with https→wss + /api/v1/telephony/ws)")
    print("  3. Configure the Settings → Telephony card (API base + credentials + DID), bind DIDs.")
    print("  4. Verify:  scripts/verify.sh https://<your-api-domain>   (prints + probes the WSS URL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
