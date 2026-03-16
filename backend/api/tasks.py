# backend/api/tasks.py

import ast
import os
import tempfile
import shutil
from pathlib import Path

from celery import shared_task
from .models import AnalysisJob


# ---------------------------------------------------------------------------
# Helpers — cloning
# ---------------------------------------------------------------------------

def _clone_repo(repo_url: str, target_dir: str) -> None:
    import subprocess
    result = subprocess.run(
        ['git', 'clone', '--depth=1', repo_url, target_dir],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git clone failed: {result.stderr.strip()}')


# ---------------------------------------------------------------------------
# Helpers — AST parsing
# ---------------------------------------------------------------------------

def _extract_imports(file_path: Path, repo_root: Path) -> list[str]:
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
                if node.level and node.level > 0:
                    rel_parts = file_path.relative_to(repo_root).with_suffix('').parts
                    base_parts = rel_parts[:max(0, len(rel_parts) - node.level)]
                    module = '.'.join(base_parts) + ('.' + node.module if base_parts else node.module)
                else:
                    module = node.module
                imports.append(module)
    return imports


def _build_graph(repo_root: Path) -> dict:
    module_imports: dict[str, set[str]] = {}

    for py_file in repo_root.rglob('*.py'):
        parts = py_file.relative_to(repo_root).parts
        if any(p.startswith('.') or p in ('venv', '.venv', 'env', 'node_modules') for p in parts):
            continue
        rel = py_file.relative_to(repo_root).with_suffix('')
        module_name = '.'.join(rel.parts)
        module_imports[module_name] = set(_extract_imports(py_file, repo_root))

    all_modules = set(module_imports.keys())
    nodes = [{'id': m, 'label': m} for m in sorted(all_modules)]

    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for source, imports in module_imports.items():
        for target_raw in imports:
            for candidate in all_modules:
                if candidate == target_raw or candidate.startswith(target_raw + '.'):
                    edge = (source, candidate)
                    if edge not in seen_edges and source != candidate:
                        edges.append({'source': source, 'target': candidate})
                        seen_edges.add(edge)

    return {'nodes': nodes, 'edges': edges}


# ---------------------------------------------------------------------------
# Helpers — LLM summary
# ---------------------------------------------------------------------------

def _generate_summary(graph_data: dict) -> str:
    """
    Call the OpenAI API to produce a high-level architectural summary of the
    repository based on its dependency graph.

    Requires OPENAI_API_KEY to be set in the environment.
    Falls back gracefully if the call fails.
    """
    from openai import OpenAI

    node_list = ', '.join(n['id'] for n in graph_data['nodes'][:80])  # cap prompt size
    edge_list = '; '.join(
        f"{e['source']} → {e['target']}" for e in graph_data['edges'][:100]
    )

    prompt = (
        "You are a senior software architect. "
        "Given the following Python module dependency graph for a GitHub repository, "
        "write a concise (3–5 paragraph) high-level architectural summary. "
        "Describe the likely purpose of the project, its main layers or components, "
        "key entry points, and any notable patterns you observe.\n\n"
        f"Modules ({len(graph_data['nodes'])} total):\n{node_list}\n\n"
        f"Dependencies ({len(graph_data['edges'])} total):\n{edge_list}"
    )

    client = OpenAI()  # reads OPENAI_API_KEY from environment
    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=600,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@shared_task
def run_analysis(job_id: str) -> None:
    """
    1. Set job → PROCESSING
    2. Clone repo to a temp directory
    3. Parse Python files with ast → build dependency graph
    4. Call LLM → generate architectural summary
    5. Persist graph_data + summary, set job → COMPLETED
    6. On any error → persist error_message, set job → FAILED
    7. Always clean up the temp directory
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

        # Step 3 — LLM summary (non-fatal: if it fails we still save the graph)
        summary = ''
        try:
            summary = _generate_summary(graph_data)
        except Exception as llm_exc:
            summary = f'[Summary unavailable: {llm_exc}]'

        # Step 4 — persist
        job.status = AnalysisJob.Status.COMPLETED
        job.graph_data = graph_data
        job.summary = summary
        job.save(update_fields=['status', 'graph_data', 'summary', 'updated_at'])

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