# backend/api/tasks.py

import ast
import os
import tempfile
import shutil
from pathlib import Path

from celery import shared_task
from .models import AnalysisJob


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clone_repo(repo_url: str, target_dir: str) -> None:
    """
    Clone a public GitHub repository using a subprocess git call.
    Raises RuntimeError if git exits with a non-zero code.
    """
    import subprocess
    result = subprocess.run(
        ['git', 'clone', '--depth=1', repo_url, target_dir],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git clone failed: {result.stderr.strip()}')


def _extract_imports(file_path: Path, repo_root: Path) -> list[str]:
    """
    Parse a single Python file with ast and return a list of imported
    module names (absolute, as written in the source).
    """
    try:
        source = file_path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # Resolve relative imports to a dotted path where possible.
                if node.level and node.level > 0:
                    # Convert the file's path to a package prefix.
                    rel_parts = file_path.relative_to(repo_root).with_suffix('').parts
                    # Walk up `level` levels from the current package.
                    base_parts = rel_parts[:max(0, len(rel_parts) - node.level)]
                    module = '.'.join(base_parts) + ('.' + node.module if base_parts else node.module)
                else:
                    module = node.module
                imports.append(module)

    return imports


def _build_graph(repo_root: Path) -> dict:
    """
    Walk every .py file under repo_root, extract imports, and return a
    graph payload shaped as:
        {
            "nodes": [{"id": "pkg.module", "label": "pkg.module"}, ...],
            "edges": [{"source": "pkg.a", "target": "pkg.b"}, ...]
        }

    Only edges whose target is also present as a node (i.e. internal to the
    repo) are included, keeping the graph focused on the project's own
    architecture rather than third-party packages.
    """
    # Map dotted module name → set of imports
    module_imports: dict[str, set[str]] = {}

    for py_file in repo_root.rglob('*.py'):
        # Skip hidden directories and virtual-env folders.
        parts = py_file.relative_to(repo_root).parts
        if any(p.startswith('.') or p in ('venv', '.venv', 'env', 'node_modules') for p in parts):
            continue

        rel = py_file.relative_to(repo_root).with_suffix('')
        module_name = '.'.join(rel.parts)
        imports = _extract_imports(py_file, repo_root)
        module_imports[module_name] = set(imports)

    all_modules = set(module_imports.keys())

    nodes = [{'id': m, 'label': m} for m in sorted(all_modules)]

    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for source, imports in module_imports.items():
        for target_raw in imports:
            # Match on exact name or as a prefix (e.g. "pkg.mod" matches "pkg.mod.util").
            for candidate in all_modules:
                if candidate == target_raw or candidate.startswith(target_raw + '.'):
                    edge = (source, candidate)
                    if edge not in seen_edges and source != candidate:
                        edges.append({'source': source, 'target': candidate})
                        seen_edges.add(edge)

    return {'nodes': nodes, 'edges': edges}


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@shared_task
def run_analysis(job_id: str) -> None:
    """
    1. Set job → PROCESSING
    2. Clone the repo to a temp directory
    3. Parse Python files with ast and build a dependency graph
    4. Persist graph_data, set job → COMPLETED
    5. On any error, persist error_message, set job → FAILED
    6. Always clean up the temp directory
    """
    tmp_dir = None

    try:
        job = AnalysisJob.objects.get(pk=job_id)
        job.status = AnalysisJob.Status.PROCESSING
        job.save(update_fields=['status', 'updated_at'])

        # Step 1 — clone
        tmp_dir = tempfile.mkdtemp(prefix='repoinsight_')
        _clone_repo(job.repo_url, tmp_dir)

        # Step 2 — parse + build graph
        graph_data = _build_graph(Path(tmp_dir))

        # Step 3 — persist results
        job.status = AnalysisJob.Status.COMPLETED
        job.graph_data = graph_data
        job.save(update_fields=['status', 'graph_data', 'updated_at'])

    except AnalysisJob.DoesNotExist:
        return

    except Exception as exc:
        try:
            job.status = AnalysisJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception:
            pass
        raise

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)