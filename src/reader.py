"""
Stage 1 — Raw file discovery: walk a directory, load everything.
Filter hard excludes like .git, .svn etc , no smart filtering here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.models import Language, SourceFile

EXT_MAP: dict[str, Language] = {
    ".java": Language.JAVA, ".py": Language.PYTHON, ".go": Language.GO,
    ".js": Language.JAVASCRIPT, ".jsx": Language.JSX,
    ".ts": Language.TYPESCRIPT, ".tsx": Language.TSX,
    ".kt": Language.JAVA,
}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def scan(root: Path, max_size: int = 500_000) -> list[SourceFile]:
    """
    Discover ALL files under *root* (broad discovery).
    The preprocessor will decide which ones to actually analyze.
    """
    # extensions to discover
    include = [
        # Source code
        "*.java", "*.py", "*.go", "*.js", "*.jsx", "*.ts", "*.tsx",
        "*.kt", "*.rs", "*.rb", "*.cs", "*.cpp", "*.c", "*.h", "*.hpp",
        # Config / build (kept for context, filtered by preprocessor)
        "*.xml", "*.yml", "*.yaml", "*.json", "*.toml",
        "*.properties", "*.gradle", "*.kts", "*.cfg", "*.ini",
        # Docs
        "*.md", "*.rst", "*.txt",
        # SQL
        "*.sql",
    ]

    # Hard excludes — never even look at these
    skip_dirs = {
        ".git", ".svn", ".hg",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "node_modules", "vendor", ".venv", "venv", "env",
        "build", "target", "dist", "out", "bin",
        ".gradle", ".idea", ".vscode", ".settings",
        "coverage", ".coverage", ".nyc_output",
        "generated", "gen",
    }

    files: list[SourceFile] = []
    seen: set[str] = set()

    for pattern in include:
        for p in root.rglob(pattern):
            rel = str(p.relative_to(root)).replace("\\", "/")

            if rel in seen or not p.is_file():
                continue

            # Skip if any path component is in skip_dirs
            parts = Path(rel).parts
            if any(part in skip_dirs for part in parts):
                continue

            size = p.stat().st_size
            if size == 0 or size > max_size:
                continue

            try:
                content = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            lang = EXT_MAP.get(p.suffix.lower(), Language.UNKNOWN)
            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

            files.append(SourceFile(
                path=str(p), relative_path=rel, language=lang,
                content=content, content_hash=_hash(content),
                size_bytes=size, line_count=line_count,
            ))
            seen.add(rel)

    files.sort(key=lambda f: f.relative_path)
    return files
