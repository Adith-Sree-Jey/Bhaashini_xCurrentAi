"""Tests for puriyudha.extract.get_ollama_client -- the gate that makes
CLAUDE.md invariant #6 ("the system's core claims must hold with [the LLM]
disabled") true structurally, not just by convention (part 5.1/5.2).
"""
import subprocess
import sys

from puriyudha.config import PipelineConfig
from puriyudha.extract import get_ollama_client


def test_disabled_returns_none():
    result = get_ollama_client(PipelineConfig(llm_enabled=False))
    assert result is None


def test_disabled_is_the_default():
    result = get_ollama_client(PipelineConfig())  # no llm_enabled= override
    assert result is None


def test_enabled_returns_a_real_ollama_client():
    from puriyudha.clients.ollama import OllamaClient

    result = get_ollama_client(PipelineConfig(llm_enabled=True))
    assert isinstance(result, OllamaClient)


def test_enabled_forwards_constructor_kwargs():
    result = get_ollama_client(PipelineConfig(llm_enabled=True), base_url="http://example.invalid:11434")
    assert result.base_url == "http://example.invalid:11434"


def test_llm_disabled_pipeline_path_never_imports_ollama_client():
    """The literal Part 5 acceptance criterion: 'with llm_enabled=false the
    full pipeline path contains no Ollama import at runtime; prove it with
    a test that fails if ollama is imported on that path.'

    Run in a fresh subprocess deliberately: within THIS test process,
    tests/test_ollama_client.py (collected in the same pytest session)
    already imports puriyudha.clients.ollama directly, so sys.modules here
    is not a meaningful signal either way. A clean interpreter that only
    ever imports puriyudha.config and puriyudha.extract, then calls
    get_ollama_client with llm_enabled=False, is the only way to actually
    prove the import never happens on that path.
    """
    script = (
        "import sys\n"
        "from puriyudha.config import PipelineConfig\n"
        "from puriyudha.extract import get_ollama_client\n"
        "result = get_ollama_client(PipelineConfig(llm_enabled=False))\n"
        "assert result is None, 'expected None with llm_enabled=False'\n"
        "assert 'puriyudha.clients.ollama' not in sys.modules, (\n"
        "    'puriyudha.clients.ollama was imported even though llm_enabled=False: '\n"
        "    + repr(sorted(m for m in sys.modules if m.startswith(\"puriyudha\")))\n"
        ")\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout.strip() == "OK"


def test_llm_enabled_pipeline_path_does_import_ollama_client():
    """The mirror-image check: with llm_enabled=True, the client SHOULD be
    importable and constructed -- proving test_llm_disabled_... above is
    actually discriminating (llm_enabled=False) behaviour, not just
    reporting that Ollama is unreachable or some other unrelated reason
    the import never happens."""
    script = (
        "import sys\n"
        "from puriyudha.config import PipelineConfig\n"
        "from puriyudha.extract import get_ollama_client\n"
        "result = get_ollama_client(PipelineConfig(llm_enabled=True))\n"
        "assert result is not None, 'expected a client with llm_enabled=True'\n"
        "assert 'puriyudha.clients.ollama' in sys.modules\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout.strip() == "OK"
