"""No network-posting or email code anywhere. Only core/llm.py (local Ollama) and sources.py (read-only GET) touch the network."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NETWORK_MODULES = {"socket", "requests", "httpx", "urllib", "http", "aiohttp", "smtplib", "ftplib", "telnetlib",
                   "email", "paramiko", "tweepy", "praw", "websocket", "websockets"}
ALLOWED = {"core/llm.py", "sources.py"}
SKIP_DIRS = {".venv", "runs", ".git", "node_modules", "__pycache__"}


def _py_files():
    for p in ROOT.rglob("*.py"):
        if not SKIP_DIRS & set(p.relative_to(ROOT).parts):
            yield p


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".")[0]


def test_only_allowlisted_files_use_network_modules():
    offenders = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        used = NETWORK_MODULES & set(_imports(ast.parse(p.read_text(encoding="utf-8"))))
        if used and rel not in ALLOWED:
            offenders.append(f"{rel}: {sorted(used)}")
    assert not offenders, offenders


def test_sources_is_read_only():
    text = (ROOT / "sources.py").read_text(encoding="utf-8")
    assert 'method="GET"' in text and "POST" not in text and "data=" not in text


def test_no_email_or_posting_code():
    for p in _py_files():
        if p.relative_to(ROOT).parts[0] == "tests":   # tests hold deny-lists that name these modules
            continue
        text = p.read_text(encoding="utf-8").lower()
        assert "smtp" not in text and "sendmail" not in text, p
