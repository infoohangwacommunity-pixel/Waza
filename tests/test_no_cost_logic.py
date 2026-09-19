from pathlib import Path

FORBIDDEN = [
    "CostProtector",
    "cost_exceeded",
    "can_spend",
    "estimate_llm_cost",
    "budget_cents",
    "JAMBMode",
    "QuizMode",
    "StudentMode",
    "ExamMode",
]

def test_no_forbidden_patterns():
    root = Path("wax")
    hits = []
    for p in root.rglob("*.py"):
        text = p.read_text(errors="ignore")
        for f in FORBIDDEN:
            if f in text:
                hits.append(f"{p}:{f}")
    assert not hits, hits
