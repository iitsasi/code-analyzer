"""
Stage 2 — AST-aware chunking: split code at semantic boundaries.
"""

from __future__ import annotations

import tiktoken

from src.models import Language, SourceFile, CodeChunk
from src.parsers import get_parser, ParsedClass


class ChunkingEngine:
    def __init__(self, model: str = "gpt-4o-mini", max_tokens: int = 4000):
        self.max_tokens = max_tokens
        try:
            self._enc = tiktoken.encoding_for_model(model)
        except KeyError:
            self._enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        return len(self._enc.encode(text))

    def chunk(self, files: list[SourceFile]) -> list[CodeChunk]:
        """Chunk all files, then batch small ones together."""
        raw: list[CodeChunk] = []
        for sf in files:
            raw.extend(self._chunk_file(sf))
        return self._batch(raw)

    # ── Per-file chunking ──

    def _chunk_file(self, sf: SourceFile) -> list[CodeChunk]:
        if self.count(sf.content) <= self.max_tokens:
            return [CodeChunk(
                chunk_id=f"{sf.relative_path}:full", content=sf.content,
                token_count=self.count(sf.content), source_files=[sf.relative_path],
                language=sf.language, class_names=_extract_names(sf.content, sf.language),
            )]
        if sf.language != Language.UNKNOWN:
            return self._chunk_ast(sf)
        return self._chunk_lines(sf)

    def _chunk_ast(self, sf: SourceFile) -> list[CodeChunk]:
        parser = get_parser(sf.language)
        pkg = _guess_package(sf.relative_path)
        try:
            classes = parser(sf.content, pkg)
        except Exception:
            return self._chunk_lines(sf)
        if not classes:
            return self._chunk_lines(sf)

        lines = sf.content.split("\n")
        chunks: list[CodeChunk] = []
        for cls in classes:
            src = "\n".join(lines[cls.start_line - 1 : cls.end_line])
            tok = self.count(src)
            if tok <= self.max_tokens:
                chunks.append(CodeChunk(
                    chunk_id=f"{sf.relative_path}:{cls.name}", content=src,
                    token_count=tok, source_files=[sf.relative_path],
                    language=sf.language, class_names=[cls.name],
                    function_names=[f.name for f in cls.functions],
                ))
            else:
                chunks.extend(self._split_class(cls, lines, sf))
        return chunks or self._chunk_lines(sf)

    def _split_class(self, cls: ParsedClass, lines: list[str], sf: SourceFile) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []

        # Header (everything before first function)
        first_fn = cls.functions[0].start_line if cls.functions else cls.end_line
        header = "\n".join(lines[cls.start_line - 1 : first_fn - 1])
        if self.count(header) > 50:
            chunks.append(CodeChunk(
                chunk_id=f"{sf.relative_path}:{cls.name}:header", content=header,
                token_count=self.count(header), source_files=[sf.relative_path],
                language=sf.language, class_names=[cls.name],
            ))

        # Group functions into chunks
        group, group_tok = [], 0
        for fn in cls.functions:
            fn_src = "\n".join(lines[fn.start_line - 1 : fn.end_line])
            fn_tok = self.count(fn_src)

            if fn_tok > self.max_tokens:
                if group:
                    chunks.append(self._make_fn_chunk(group, lines, cls, sf))
                    group, group_tok = [], 0
                chunks.extend(self._split_big_fn(fn, fn_src, cls, sf))
                continue

            if group_tok + fn_tok > self.max_tokens and group:
                chunks.append(self._make_fn_chunk(group, lines, cls, sf))
                group, group_tok = [], 0

            group.append(fn)
            group_tok += fn_tok

        if group:
            chunks.append(self._make_fn_chunk(group, lines, cls, sf))
        return chunks

    def _make_fn_chunk(self, fns, lines, cls, sf) -> CodeChunk:
        src = "\n".join(lines[fns[0].start_line - 1 : fns[-1].end_line])
        return CodeChunk(
            chunk_id=f"{sf.relative_path}:{cls.name}:{fns[0].name}-{fns[-1].name}",
            content=src, token_count=self.count(src),
            source_files=[sf.relative_path], language=sf.language,
            class_names=[cls.name], function_names=[f.name for f in fns],
        )

    def _split_big_fn(self, fn, src: str, cls, sf: SourceFile) -> list[CodeChunk]:
        chunks, cur, cur_tok, idx = [], [], 0, 0
        for line in src.split("\n"):
            lt = self.count(line + "\n")
            if cur_tok + lt > self.max_tokens and cur:
                chunks.append(CodeChunk(
                    chunk_id=f"{sf.relative_path}:{cls.name}:{fn.name}:f{idx}",
                    content="\n".join(cur), token_count=cur_tok,
                    source_files=[sf.relative_path], language=sf.language,
                    class_names=[cls.name], function_names=[fn.name],
                ))
                idx += 1
                cur, cur_tok = [], 0
            cur.append(line)
            cur_tok += lt
        if cur:
            chunks.append(CodeChunk(
                chunk_id=f"{sf.relative_path}:{cls.name}:{fn.name}:f{idx}",
                content="\n".join(cur), token_count=cur_tok,
                source_files=[sf.relative_path], language=sf.language,
                class_names=[cls.name], function_names=[fn.name],
            ))
        return chunks

    def _chunk_lines(self, sf: SourceFile) -> list[CodeChunk]:
        chunks, cur, tok, idx = [], [], 0, 0
        for line in sf.content.split("\n"):
            lt = self.count(line + "\n")
            if tok + lt > self.max_tokens and cur:
                chunks.append(CodeChunk(
                    chunk_id=f"{sf.relative_path}:lc{idx}", content="\n".join(cur),
                    token_count=tok, source_files=[sf.relative_path], language=sf.language,
                ))
                idx += 1
                cur, tok = [], 0
            cur.append(line)
            tok += lt
        if cur:
            chunks.append(CodeChunk(
                chunk_id=f"{sf.relative_path}:lc{idx}", content="\n".join(cur),
                token_count=tok, source_files=[sf.relative_path], language=sf.language,
            ))
        return chunks

    # ── Batching ──

    def _batch(self, chunks: list[CodeChunk]) -> list[CodeChunk]:
        batches: list[CodeChunk] = []
        group: list[CodeChunk] = []
        group_tok = 0

        for ch in chunks:
            if ch.token_count > self.max_tokens // 2:
                if group:
                    batches.append(self._merge(group))
                    group, group_tok = [], 0
                batches.append(ch)
                continue
            if group_tok + ch.token_count > self.max_tokens and group:
                batches.append(self._merge(group))
                group, group_tok = [], 0
            group.append(ch)
            group_tok += ch.token_count

        if group:
            batches.append(self._merge(group))
        return batches

    def _merge(self, chunks: list[CodeChunk]) -> CodeChunk:
        merged = "\n\n".join(c.to_prompt() for c in chunks)
        all_files = list(dict.fromkeys(f for c in chunks for f in c.source_files))
        all_cls = list(dict.fromkeys(n for c in chunks for n in c.class_names))
        all_fn = list(dict.fromkeys(n for c in chunks for n in c.function_names))
        return CodeChunk(
            chunk_id=f"batch_{chunks[0].chunk_id}", content=merged,
            token_count=self.count(merged), source_files=all_files,
            language=chunks[0].language, class_names=all_cls, function_names=all_fn,
        )


# ── Helpers ──

def _guess_package(rel_path: str) -> str:
    parts = rel_path.split("/")
    for marker in ("java", "python", "src", "lib", "pkg", "internal"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts) - 1:
                return ".".join(parts[idx + 1 : -1])
    return ""


def _extract_names(source: str, lang: Language) -> list[str]:
    keywords = {
        Language.JAVA: ("class ", "interface ", "enum "),
        Language.PYTHON: ("class ",),
        Language.GO: ("type ",),
    }.get(lang, ("class ",))
    names = []
    for line in source.split("\n"):
        s = line.strip()
        for kw in keywords:
            if kw in s:
                name = s[s.index(kw) + len(kw):].split()[0].split("{")[0].split("(")[0].split(":")[0]
                if name.isidentifier():
                    names.append(name)
    return names
