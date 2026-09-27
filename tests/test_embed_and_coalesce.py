
"""Empty embed guards + delayed-message relatedness coalescing."""

from pathlib import Path
import ast
import importlib.util


def _load_relatedness():
    """Load relatedness_score without full package deps."""
    src = Path("wax/work/recovery.py").read_text()
    # Extract function body by compiling only the pure helpers
    ns = {}
    # minimal exec of token + relatedness functions from source
    start = src.find("def _token_set")
    end = src.find("async def find_coalescable_works")
    code = src[start:end]
    exec(code, ns)
    return ns["relatedness_score"], ns["_token_set"]


def test_relatedness_prefers_near_related():
    relatedness_score, _ = _load_relatedness()
    close = relatedness_score(
        "help me finish the assignment",
        "the second question too",
        seconds_apart=15,
    )
    far = relatedness_score(
        "what is the weather",
        "derive the quadratic formula carefully",
        seconds_apart=400,
    )
    assert close > far
    assert close >= 0.4


def test_usable_embed_rejects_empty():
    src = Path("wax/memory/embeddings.py").read_text()
    ns = {}
    start = src.find("def _usable_embed_text")
    end = src.find("async def embed_texts")
    exec(src[start:end], ns)
    fn = ns["_usable_embed_text"]
    assert fn("") is None
    assert fn("   ") is None
    assert fn(None) is None
    assert fn("hello world") == "hello world"


def test_embed_texts_filters_empties_in_source():
    src = Path("wax/memory/embeddings.py").read_text()
    assert "embed_skipped_empty_input" in src
    assert "_usable_embed_text" in src


def test_coalesce_wired_in_worker():
    src = Path("wax/workers/main.py").read_text()
    assert "find_coalescable_works" in src
    assert "works_coalesced" in src
