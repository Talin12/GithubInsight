# backend/api/tasks.py

import ast
import os
import tempfile
import shutil
from pathlib import Path

import redis
from celery import shared_task
from .models import AnalysisJob

# ---------------------------------------------------------------------------
# Redis client (for SSE streaming)
# ---------------------------------------------------------------------------

def _get_redis():
    return redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

STREAM_TTL_SECONDS = 300  # expire stream keys after 5 minutes

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
# Helpers — AST parsing with edge type differentiation
# ---------------------------------------------------------------------------

def _extract_imports(file_path: Path, repo_root: Path) -> list[dict]:
    """
    Returns a list of dicts: { "module": str, "type": "relative" | "direct" }
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
                imports.append({'module': alias.name, 'type': 'direct'})
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                is_relative = bool(node.level and node.level > 0)
                if is_relative:
                    rel_parts = file_path.relative_to(repo_root).with_suffix('').parts
                    base_parts = rel_parts[:max(0, len(rel_parts) - node.level)]
                    module = '.'.join(base_parts) + ('.' + node.module if base_parts else node.module)
                else:
                    module = node.module
                imports.append({
                    'module': module,
                    'type': 'relative' if is_relative else 'direct',
                })
    return imports


def _build_graph(repo_root: Path) -> dict:
    """
    Returns:
        {
            "nodes": [{"id": str, "label": str}],
            "edges": [{"source": str, "target": str, "type": "direct" | "relative"}]
        }
    """
    module_imports: dict[str, list[dict]] = {}

    for py_file in repo_root.rglob('*.py'):
        parts = py_file.relative_to(repo_root).parts
        if any(p.startswith('.') or p in ('venv', '.venv', 'env', 'node_modules') for p in parts):
            continue
        rel = py_file.relative_to(repo_root).with_suffix('')
        module_name = '.'.join(rel.parts)
        module_imports[module_name] = _extract_imports(py_file, repo_root)

    all_modules = set(module_imports.keys())
    nodes = [{'id': m, 'label': m} for m in sorted(all_modules)]

    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for source, imports in module_imports.items():
        for imp in imports:
            target_raw = imp['module']
            edge_type  = imp['type']
            for candidate in all_modules:
                if candidate == target_raw or candidate.startswith(target_raw + '.'):
                    edge_key = (source, candidate)
                    if edge_key not in seen_edges and source != candidate:
                        edges.append({
                            'source': source,
                            'target': candidate,
                            'type': edge_type,
                        })
                        seen_edges.add(edge_key)

    return {'nodes': nodes, 'edges': edges}

# ---------------------------------------------------------------------------
# Helpers — LLM streaming summary via Redis
# ---------------------------------------------------------------------------

def _stream_summary_to_redis(graph_data: dict, job_id: str) -> str:
    """
    Calls OpenAI with stream=True, pushes each text chunk to a Redis list
    keyed `summary_stream:<job_id>`. Pushes __DONE__ sentinel when finished,
    or __ERROR__:<msg> on failure.
    Returns the full assembled summary string.
    """
    from openai import OpenAI

    r = _get_redis()
    stream_key = f'summary_stream:{job_id}'

    node_list = ', '.join(n['id'] for n in graph_data['nodes'][:80])
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

    client = OpenAI()
    full_summary = ''

    try:
        with client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=600,
            temperature=0.3,
            stream=True,
        ) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_summary += delta
                    r.rpush(stream_key, delta)

        r.rpush(stream_key, '__DONE__')
    except Exception as exc:
        r.rpush(stream_key, f'__ERROR__:{exc}')
        full_summary = f'[Summary unavailable: {exc}]'
    finally:
        r.expire(stream_key, STREAM_TTL_SECONDS)

    return full_summary

# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@shared_task
def run_analysis(job_id: str) -> None:
    tmp_dir = None

    try:
        job = AnalysisJob.objects.get(pk=job_id)
        job.status = AnalysisJob.Status.PROCESSING
        job.save(update_fields=['status', 'updated_at'])

        tmp_dir = tempfile.mkdtemp(prefix='repoinsight_')
        _clone_repo(job.repo_url, tmp_dir)

        graph_data = _build_graph(Path(tmp_dir))

        # Stream summary chunks to Redis; also returns full text for DB storage.
        summary = _stream_summary_to_redis(graph_data, job_id)

        job.status     = AnalysisJob.Status.COMPLETED
        job.graph_data = graph_data
        job.summary    = summary
        job.save(update_fields=['status', 'graph_data', 'summary', 'updated_at'])

    except AnalysisJob.DoesNotExist:
        return

    except Exception as exc:
        try:
            job.status        = AnalysisJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception:
            pass
        raise

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)