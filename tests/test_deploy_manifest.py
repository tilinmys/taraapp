"""deploy_sakhi.sh ships every module the running app actually imports.

Its PY_FILES list is hand-maintained, and it has drifted twice: once
missing clinical_terms.py (the worker would have crashed at import on the
EC2 instance), and again when sakhi_telemetry.py / sakhi_dashboard_store.py
were added. This walks the real import graph from the two entry points that
run on the server and fails if any reachable local module is missing from
the list - so the next new module breaks this test, not production.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = ("agent.py", "status_server.py")


def _deploy_list() -> set[str]:
    script = (ROOT / "deploy" / "aws" / "deploy_sakhi.sh").read_text(encoding="utf-8")
    block = re.search(r"PY_FILES=\((.*?)\)", script, re.S)
    assert block, "PY_FILES array not found in deploy_sakhi.sh"
    return set(block.group(1).split())


def _local_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {n for n in names if (ROOT / f"{n}.py").exists()}


def _reachable() -> set[str]:
    seen: set[str] = set()
    stack = [Path(p).stem for p in ENTRY_POINTS]
    while stack:  # iterative DFS over the local import graph
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        stack.extend(_local_imports(ROOT / f"{module}.py") - seen)
    return seen


def test_every_imported_module_is_deployed():
    missing = sorted(f"{m}.py" for m in _reachable() if f"{m}.py" not in _deploy_list())
    assert missing == [], (
        f"deploy_sakhi.sh PY_FILES is missing {missing} - the deployed worker "
        "would fail to import them. Add them to the list."
    )


def test_postgres_cutover_operational_files_are_deployed():
    script = (ROOT / "deploy" / "aws" / "deploy_sakhi.sh").read_text(encoding="utf-8")
    required = (
        "scripts/migrate_sakhi_sqlite_to_postgres.py",
        "scripts/supabase_keepalive.py",
        "deploy/aws/sakhi-supabase-keepalive.service",
        "deploy/aws/sakhi-supabase-keepalive.timer",
    )
    for path in required:
        assert path in script, f"production deploy omits {path}"


def test_frontend_deploy_excludes_all_next_build_directories():
    script = (ROOT / "deploy" / "aws" / "deploy_sakhi.sh").read_text(encoding="utf-8")
    assert '--exclude ".next*"' in script
    assert "--exclude '.next*'" in script


def test_frontend_build_failure_cannot_be_masked_by_and_list():
    """A failed npm install must fail the SSM deploy, not serve stale .next."""
    script = (ROOT / "deploy" / "aws" / "deploy_sakhi.sh").read_text(encoding="utf-8")
    assert r'\"cd /opt/sakhi/frontend && npm ci && npm run build\"' not in script
    assert r'\"npm ci --no-audit --no-fund\"' in script
    assert r'\"npm run build\"' in script
