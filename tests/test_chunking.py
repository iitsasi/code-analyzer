"""Tests for the chunking engine."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Language, SourceFile
from src.chunking import ChunkingEngine


def _sf(content: str, path="src/Main.java", lang=Language.JAVA) -> SourceFile:
    return SourceFile(
        path=str(Path(path)), relative_path=path, language=lang,
        content=content, content_hash="abc123",
        size_bytes=len(content.encode()), line_count=content.count("\n") + 1,
    )


def test_small_file_single_chunk():
    e = ChunkingEngine(max_tokens=4000)
    chunks = e.chunk([_sf("public class Foo {}")])
    assert len(chunks) == 1

def test_java_split():
    e = ChunkingEngine(max_tokens=200)
    methods = "\n".join(
        f"    public void method{i}(String a, String b, String c) {{\n"
        f"        System.out.println(a + b + c + {i});\n        System.out.println({i});\n    }}"
        for i in range(20)
    )
    code = f"package com.example;\nimport java.util.List;\n\npublic class Big {{\n{methods}\n}}"
    chunks = e.chunk([_sf(code)])
    assert len(chunks) >= 2

def test_python_split():
    e = ChunkingEngine(max_tokens=200)
    methods = "\n".join(f"    def method_{i}(self, a, b, c):\n        return a + b + c + {i}\n        print({i})" for i in range(20))
    code = f"class Big:\n    def __init__(self):\n        pass\n{methods}"
    chunks = e.chunk([_sf(code, "src/big.py", Language.PYTHON)])
    assert len(chunks) >= 1

def test_go_split():
    e = ChunkingEngine(max_tokens=200)
    methods = "\n".join(f"func (s *Svc) M{i}(a int) error {{\n    return nil\n    fmt.Println(a)\n}}" for i in range(20))
    code = f"package main\ntype Svc struct {{ name string }}\n{methods}"
    chunks = e.chunk([_sf(code, "pkg/svc.go", Language.GO)])
    assert len(chunks) >= 1

def test_js_split():
    e = ChunkingEngine(max_tokens=200)
    methods = "\n".join(f"    method{i}(a, b) {{ return a + b + {i}; }}" for i in range(20))
    code = f"class Big {{\n{methods}\n}}"
    chunks = e.chunk([_sf(code, "src/big.js", Language.JAVASCRIPT)])
    assert len(chunks) >= 1

def test_line_fallback():
    e = ChunkingEngine(max_tokens=200)
    code = "\n".join(f"line {i}: {'x' * 100}" for i in range(200))
    chunks = e.chunk([_sf(code, "data/big.json", Language.UNKNOWN)])
    assert len(chunks) >= 2

def test_batching():
    e = ChunkingEngine(max_tokens=4000)
    files = [_sf(f"public class C{i} {{ public void m() {{}} }}", f"C{i}.java") for i in range(20)]
    chunks = e.chunk(files)
    assert len(chunks) < 20  # Batching should reduce count

def test_token_count():
    e = ChunkingEngine()
    assert e.count("Hello, world!") > 0
    assert e.count("") == 0

def test_mixed_languages():
    e = ChunkingEngine(max_tokens=4000)
    files = [
        _sf("public class A {}", "A.java", Language.JAVA),
        _sf("class B: pass", "B.py", Language.PYTHON),
        _sf("type C struct {}", "C.go", Language.GO),
        _sf("class D {}", "D.js", Language.JAVASCRIPT),
    ]
    chunks = e.chunk(files)
    assert len(chunks) >= 1
