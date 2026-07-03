"""
Stage 3 — Map-Reduce LLM analyzer.

Map:  Each chunk -> async LLM call -> structured JSON
Reduce: All results -> synthesis LLM call -> final report
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import tiktoken
from pydantic import BaseModel

from src.models import (
    CodeChunk, AnalysisOutput, ProjectOverview, ArchitecturalInsights,
    ServiceInfo, EndpointInfo, KeyClass, ClassMethod, KeyMethod,
    ComplexityAnalysis, ComplexFile, ComplexityMetrics,
    ChunkAnalysis, ChunkMethod,
)


# ── Configuration ──

class Config(BaseModel):
    model: str = "gpt-4o-mini"
    temperature: float = 0.1
    max_output_tokens: int = 16_000
    max_concurrent: int = 5
    max_cost_usd: float = 5.0
    cache_dir: Path = Path(".analyzer_cache")
    mock: bool = False
    api_key: Optional[str] = None
    input_cost_per_1k: float = 0.00015
    output_cost_per_1k: float = 0.0006


# ── Token Manager ──

class TokenManager:
    def __init__(self, model: str, max_cost: float):
        self.max_cost = max_cost
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.cost = 0.0
        try:
            self._enc = tiktoken.encoding_for_model(model)
        except KeyError:
            self._enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        return len(self._enc.encode(text))

    def record(self, inp: int, out: int, cfg: Config):
        self.input_tokens += inp
        self.output_tokens += out
        self.calls += 1
        self.cost += (inp / 1000) * cfg.input_cost_per_1k + (out / 1000) * cfg.output_cost_per_1k

    def can_afford(self, est_input: int, cfg: Config) -> bool:
        return self.cost + (est_input / 1000) * cfg.input_cost_per_1k < self.max_cost

    def report(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "calls": self.calls,
            "cost_usd": round(self.cost, 6),
        }


# ── Cache ──

class Cache:
    def __init__(self, path: Path, enabled: bool = True):
        self.enabled = enabled
        self.path = path
        self.hits = 0
        self.misses = 0
        self._mem: dict[str, Any] = {}

    def _key(self, content_hash: str, chunk_id: str) -> str:
        return hashlib.sha256(f"{content_hash}:{chunk_id}".encode()).hexdigest()[:32]

    def get(self, content_hash: str, chunk_id: str) -> Optional[dict]:
        if not self.enabled:
            return None
        k = self._key(content_hash, chunk_id)
        if k in self._mem:
            self.hits += 1
            return self._mem[k]
        fpath = self.path / f"{k}.json"
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                self._mem[k] = data
                self.hits += 1
                return data
            except Exception:
                pass
        self.misses += 1
        return None

    def put(self, content_hash: str, chunk_id: str, result: dict):
        if not self.enabled:
            return
        k = self._key(content_hash, chunk_id)
        self._mem[k] = result
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            (self.path / f"{k}.json").write_text(
                json.dumps(result, ensure_ascii=True, default=str), encoding="utf-8",
            )
        except OSError:
            pass

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits, "misses": self.misses,
            "hit_rate": f"{self.hits / max(total, 1):.0%}",
        }


# ── JSON Repair ──

def _repair_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    for attempt in range(4):
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        if attempt == 0:
            text = re.sub(r",\s*([\]}])", r"\1", text)
        elif attempt == 1:
            text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
        elif attempt == 2:
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e > s:
                text = text[s:e + 1]
    s = text.find("{")
    if s != -1:
        c = text[s:]
        c += "]" * max(c.count("[") - c.count("]"), 0) + "}" * max(c.count("{") - c.count("}"), 0)
        try:
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ── LLM Client ──

class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._chat = None
        if not cfg.mock:
            from langchain_openai import ChatOpenAI
            kwargs: dict[str, Any] = {
                "model": cfg.model, "temperature": cfg.temperature,
                "max_tokens": cfg.max_output_tokens, "request_timeout": 120,
            }
            if cfg.api_key:
                kwargs["openai_api_key"] = cfg.api_key
            self._chat = ChatOpenAI(**kwargs)

    async def call(self, system: str, user: str) -> str:
        if self.cfg.mock:
            return self._mock(system)
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = await self._chat.ainvoke([
            SystemMessage(content=system), HumanMessage(content=user),
        ])
        return resp.content

    def _mock(self, system: str) -> str:
        if "architect" in system.lower() or "synthesize" in system.lower():
            return json.dumps(MOCK_SYNTHESIS, indent=2)
        return json.dumps(MOCK_CHUNK, indent=2)


# ── Prompts ──

MAP_SYSTEM = """\
You are an expert software engineer analyzing source code.
Extract structured knowledge. Return ONLY valid JSON, no markdown.
The code is in {language}. Be precise and factual.
"""

MAP_USER = """\
Analyze this code chunk and extract structured information.

Source: {source_files}
Language: {language}
Classes: {class_names}
Functions: {function_names}

=== CODE ===
{code}
=== END ===

Return JSON with this EXACT structure:
{{
    "file_path": "relative/path/to/file.java",
    "summary": "1-2 sentence summary of what this file does",
    "purpose": "Why this file exists in the architecture",
    "key_methods": [
        {{
            "name": "methodName",
            "signature": "public ReturnType methodName(ParamType param)",
            "description": "What this method does (1 sentence)",
            "complexity": 3
        }}
    ],
    "dependencies": ["ClassName1", "ClassName2"],
    "complexity_score": 5,
    "complexity_notes": "Why this complexity score (1-2 sentences)"
}}

Return ONLY the JSON.
"""

REDUCE_SYSTEM = """\
You are an expert software architect.
Synthesize chunk-level analyses into a comprehensive codebase report.
Return ONLY valid JSON, no markdown fences.
"""

REDUCE_USER = """\
Synthesize these chunk-level analyses into a comprehensive codebase report.

Chunk analyses:
{analyses}

Stats: {total_files} files, {total_lines} lines, languages: {languages}
Repository: {repo_url}

Return JSON with this EXACT structure:
{{
    "project_overview": {{
        "name": "Project Name",
        "description": "2-3 sentence description",
        "purpose": "What the project demonstrates",
        "architecture": "Layered architecture description with bullet points using \\n",
        "domain": "business domain",
        "primary_language": "Java",
        "framework": "Spring Boot",
        "technologies": ["Java 17", "Spring Boot 3.x", ...],
        "modules": ["controller", "service", "repository", ...],
        "total_files": {total_files},
        "total_lines_of_code": {total_lines}
    }},
    "architectural_insights": {{
        "layers": ["Controller -> Service -> Repository -> JPA -> MySQL"],
        "design_patterns": ["Repository Pattern", "DTO Pattern", ...],
        "cross_cutting_concerns": ["JWT Auth", "Exception Handling", ...],
        "data_flow": "HTTP -> Filter -> Controller -> Service -> Repository -> DB",
        "integration_points": ["MySQL", "Redis"],
        "strengths": ["Clean separation of concerns", ...],
        "areas_for_improvement": ["Add tests", ...]
    }},
    "services": [
        {{
            "service_name": "catalog",
            "description": "what it handles",
            "purpose": "business purpose",
            "classes": ["Controller", "Service"],
            "api_endpoints": [{{"method": "GET", "path": "/api/...", "description": "...", "handler": "..."}}],
            "complexity_notes": ["note1"]
        }}
    ],
    "key_classes": [
        {{
            "name": "ActorController",
            "file_path": "src/main/java/.../ActorController.java",
            "role": "controller",
            "summary": "REST controller for Actor APIs.",
            "methods": [
                {{
                    "name": "getActors",
                    "signature": "ResponseEntity<CollectionModel<ActorDto>> getActors(Pageable pageable)",
                    "description": "Retrieves paginated list of actors",
                    "http_method": "GET",
                    "path": "/api/v1/actors",
                    "estimated_complexity": 2
                }}
            ]
        }}
    ],
    "key_methods": [
        {{
            "name": "methodName",
            "signature": "public ReturnType methodName(Param p)",
            "description": "What it does",
            "complexity": 3,
            "file_path": "src/main/java/.../File.java"
        }}
    ],
    "complexity_metrics": {{
        "total_files": {total_files},
        "total_lines": {total_lines},
        "total_classes": 0,
        "total_functions": 0,
        "avg_methods_per_class": 0.0,
        "avg_complexity_score": 0.0,
        "largest_files": [],
        "most_complex_classes": []
    }},
    "cross_cutting_patterns": ["pattern1", "pattern2"],
    "recommendations": ["recommendation1"],
    "complexity_analysis": {{
        "average_complexity": 4.0,
        "complex_files": [{{"file_path": "...", "score": 8, "notes": "..."}}],
        "dependencies": ["org.springframework.boot:spring-boot-starter-web"]
    }},
    "chunk_analyses": [
        {{
            "file_path": "src/main/java/.../File.java",
            "summary": "What the file does",
            "purpose": "Why it exists",
            "key_methods": [{{"name": "...", "signature": "...", "description": "...", "complexity": 3}}],
            "dependencies": ["Dep1"],
            "complexity_score": 4,
            "complexity_notes": "Notes"
        }}
    ]
}}

IMPORTANT:
- key_classes: ALL controllers, services, repositories, security, exception handlers with full method lists
- key_methods: 15-20 MOST IMPORTANT methods across the codebase
- chunk_analyses: one entry per significant file
- estimated_complexity for key_classes methods: 1-10 integer (same scale as complexity elsewhere)
- http_method for key_classes methods: "GET", "POST", "PUT", "DELETE", or "" for non-HTTP methods
- Return ONLY the JSON object
"""


from src.mock_data import MOCK_CHUNK, MOCK_SYNTHESIS


# ── Map-Reduce Analyzer ──

class MapReduceAnalyzer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tokens = TokenManager(cfg.model, cfg.max_cost_usd)
        self.cache = Cache(cfg.cache_dir, enabled=True)
        self.llm = LLMClient(cfg)
        self._sem = asyncio.Semaphore(cfg.max_concurrent)

    def run(self, chunks: list[CodeChunk], stats: dict) -> AnalysisOutput:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._run(chunks, stats))
        finally:
            loop.close()

    async def _run(self, chunks: list[CodeChunk], stats: dict) -> AnalysisOutput:
        t0 = time.time()

        print(f"  [map] Analyzing {len(chunks)} chunks...")
        analyses = await self._map(chunks)
        print(f"  [map] Done in {time.time() - t0:.1f}s")

        print(f"  [reduce] Synthesizing...")
        result = await self._reduce(analyses, stats)
        print(f"  [reduce] Done in {time.time() - t0:.1f}s")
        return result

    async def _map(self, chunks: list[CodeChunk]) -> list[dict]:
        async def _one(ch: CodeChunk) -> dict:
            ch_hash = hashlib.sha256(ch.content.encode()).hexdigest()[:16]
            cached = self.cache.get(ch_hash, ch.chunk_id)
            if cached:
                return cached

            async with self._sem:
                system = MAP_SYSTEM.format(language=ch.language.value)
                user = MAP_USER.format(
                    source_files=", ".join(ch.source_files), language=ch.language.value,
                    class_names=", ".join(ch.class_names) or "N/A",
                    function_names=", ".join(ch.function_names[:15]) or "N/A",
                    code=ch.content,
                )
                est = self.tokens.count(system + user)
                if not self.tokens.can_afford(est, self.cfg):
                    return {"error": "budget exceeded", "chunk_id": ch.chunk_id}

                try:
                    raw = await self.llm.call(system, user)
                    parsed = _repair_json(raw)
                    if parsed:
                        self.cache.put(ch_hash, ch.chunk_id, parsed)
                        self.tokens.record(est, self.tokens.count(raw), self.cfg)
                        return parsed
                except Exception as e:
                    return {"error": str(e), "chunk_id": ch.chunk_id}
                return {"error": "parse failed", "chunk_id": ch.chunk_id}

        tasks = [_one(c) for c in chunks]
        results = await asyncio.gather(*tasks)
        return [r if isinstance(r, dict) else {"error": str(r)} for r in results]

    async def _reduce(self, analyses: list[dict], stats: dict) -> AnalysisOutput:
        analyses_str = json.dumps(analyses, indent=1, default=str)[:80_000]
        system = REDUCE_SYSTEM
        user = REDUCE_USER.format(
            analyses=analyses_str,
            total_files=stats.get("total_files", 0),
            total_lines=stats.get("total_lines", 0),
            languages=json.dumps(stats.get("languages", {})),
            repo_url=stats.get("repo_url", ""),
        )

        try:
            raw = await self.llm.call(system, user)
            parsed = _repair_json(raw)
            if parsed and "project_overview" in parsed:
                self.tokens.record(
                    self.tokens.count(system + user), self.tokens.count(raw), self.cfg,
                )
                return AnalysisOutput(**{k: v for k, v in parsed.items() if k != "_metadata"})
        except Exception as e:
            print(f"  [reduce] Error: {e}")

        # Fallback
        return AnalysisOutput(
            project_overview=ProjectOverview(
                name=stats.get("repo_url", "Unknown").split("/")[-1],
                description="Analysis completed (fallback mode).",
                total_files=stats.get("total_files", 0),
                total_lines_of_code=stats.get("total_lines", 0),
            ),
        )
