"""The evaluation's per-language report: documented support, measured result, every miss listed."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("rag_eval", Path(__file__).resolve().parent.parent / "scripts" / "rag_eval.py")
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


def test_same_script():
    assert E.same_script("ur", "تھورنکلف پارک میں 6 حملے رپورٹ ہوئے۔") is True
    assert E.same_script("ur", "There were 6 assaults in September.") is False
    assert E.same_script("ta", "செப்டம்பரில் 6 தாக்குதல்கள்") is True
    assert E.same_script("es", "Se denunciaron 6 agresiones.") is True
    assert E.same_script("es", "There were 6 assaults reported.") is False      # Latin script: English spotted by words


def test_language_table_lists_documented_support_and_misses():
    res = {"engines": {"cohere": {"languages": {
        "ur": {"correct": True, "same_language": False, "q": "سوال", "answer": "There were 6 assaults.", "expect": "6", "held_back": False},
        "es": {"correct": True, "same_language": True, "q": "¿Cuántas?", "answer": "Hubo 6 agresiones.", "expect": "6", "held_back": False},
        "ta": {"correct": True, "same_language": True, "translated": True, "native": False, "q": "?", "answer": "There were 6.", "expect": "6", "held_back": False},
        "ps": {"correct": False, "same_language": None, "q": "پوښتنه", "answer": "I couldn't match every number…", "expect": "6", "held_back": True},
    }}}}
    t = E.lang_table(res)
    assert "| Urdu | yes | **no** | ✓ (wrong alphabet) |" in t
    assert "| Spanish | yes | yes | ✓ |" in t
    assert "| Pashto | **no** | **no** | held back |" in t
    assert "| Tamil | yes | **no** | ✓ (via translation) |" in t
    assert "Correct: cohere: 3/4." in t
    assert "**cohere, Urdu** (right number, wrong language)" in t and "**cohere, Pashto** (held back by the number guard)" in t
    assert "Spanish** (" not in t                                                   # no miss, not in details
