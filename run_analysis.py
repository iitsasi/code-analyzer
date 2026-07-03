#!/usr/bin/env python3
"""
Codebase Analyzer — Multi-language Map-Reduce LLM Analysis.

Configuration:
    config/default.yaml   Static defaults (model, tokens, chunking, etc.)
    .env                  Environment-specific (CODEBASE_PATH, OPENAI_API_KEY, etc.)

Usage:
    python run_analysis.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import yaml

# ── Load .env ──
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from src.models import AnalysisOutput
from src.reader import scan
from src.preprocessor import preprocess
from src.chunking import ChunkingEngine
from src.analyzer import Config, MapReduceAnalyzer


def load_config() -> dict:
    """Load config/default.yaml, then overlay .env values."""
    yaml_path = Path(__file__).parent / "config" / "default.yaml"
    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # .env overrides for environment-specific values
    env_overrides = {
        "CODEBASE_PATH":       ("codebase_path", str),
        "CODEBASE_REPO_URL":   ("repo_url", str),
        "CODEBASE_MOCK":       ("mock", lambda v: v.lower() in ("true", "1", "yes")),
        "CODEBASE_OUTPUT_DIR": ("output_dir", str),
    }
    for env_key, (cfg_key, cast) in env_overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            cfg[cfg_key] = cast(val)

    return cfg


def main():
    cfg = load_config()

    # ── Resolve settings ──
    codebase_path = cfg.get("codebase_path", "")
    repo_url = cfg.get("repo_url", "")
    mock = cfg.get("mock", True)
    output_dir = Path(cfg.get("output_dir", "output"))

    model = cfg.get("model", "gpt-4o-mini")
    max_cost = float(cfg.get("max_cost_usd", 5.0))
    concurrency = int(cfg.get("max_concurrent", 5))
    chunk_tokens = int(cfg.get("max_chunk_tokens", 4000))

    # Default codebase
    if not codebase_path:
        codebase_path = "/home/user/spring-rest-sakila"
        repo_url = repo_url or "https://github.com/codejsha/spring-rest-sakila"

    if not Path(codebase_path).exists():
        print(f"ERROR: Path not found: {codebase_path}")
        print(f"       Set CODEBASE_PATH in .env")
        sys.exit(1)

    # API key
    api_key = None
    if not mock:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY not set in .env")
            sys.exit(1)

    # ── Print config ──
    print()
    print("=" * 60)
    print("  CODEBASE ANALYZER v2.0")
    print("=" * 60)
    print(f"  Codebase:    {codebase_path}")
    print(f"  Model:       {model}")
    print(f"  Mode:        {'MOCK' if mock else 'REAL (OpenAI)'}")
    print(f"  Concurrency: {concurrency}")
    print(f"  Max cost:    ${max_cost}")
    print(f"  Output:      {output_dir}/")
    print(f"  Config:      config/default.yaml + .env")
    print("=" * 60)
    print()

    t0 = time.time()

    # ── Stage 1: Scan ──
    print("[1/5] Scanning files...")
    all_files = scan(Path(codebase_path))
    print(f"  Discovered {len(all_files)} files")
    print()

    # ── Stage 2: Preprocess ──
    print("[2/5] Preprocessing (filter source files)...")
    pp = preprocess(all_files)
    source_files = pp.source_files
    lang_counts: dict[str, int] = {}
    for f in source_files:
        lang_counts[f.language.value] = lang_counts.get(f.language.value, 0) + 1
    total_lines = sum(f.line_count for f in source_files)
    print(f"  Source files for analysis: {len(source_files)}")
    print(f"  Config files (context):   {len(pp.config_files)}")
    print(f"  Skipped:                  {pp.skipped_count}")
    if pp.skipped_reasons:
        for reason, count in sorted(pp.skipped_reasons.items(), key=lambda x: -x[1]):
            print(f"    - {reason}: {count}")
    print(f"  Languages: {lang_counts}")
    print()

    # ── Stage 3: Chunk ──
    print("[3/5] Chunking code (AST-aware)...")
    chunker = ChunkingEngine(model=model, max_tokens=chunk_tokens)
    chunks = chunker.chunk(source_files)
    avg_tok = sum(c.token_count for c in chunks) // max(len(chunks), 1)
    max_tok = max((c.token_count for c in chunks), default=0)
    print(f"  {len(chunks)} chunks (avg {avg_tok} tok, max {max_tok} tok)")
    print()

    # ── Stage 4: LLM ──
    print("[4/5] LLM analysis (map-reduce)...")
    llm_cfg = Config(
        model=model, mock=mock, api_key=api_key,
        max_cost_usd=max_cost, max_concurrent=concurrency,
        cache_dir=Path(".analyzer_cache"),
    )
    stats = {
        "total_files": len(source_files), "total_lines": total_lines,
        "languages": lang_counts, "repo_url": repo_url,
    }
    analyzer = MapReduceAnalyzer(llm_cfg)
    result = analyzer.run(chunks, stats)
    print()

    # ── Stage 5: Output ──
    print("[5/5] Writing output...")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "codebase_analysis.json"

    elapsed = time.time() - t0
    output_dict = result.model_dump()
    output_dict["_metadata"] = {
        "elapsed_seconds": round(elapsed, 2),
        "total_discovered": len(all_files),
        "source_files": len(source_files),
        "config_files": len(pp.config_files),
        "skipped": pp.skipped_count,
        "total_lines": total_lines,
        "languages": lang_counts,
        "chunks_raw": len(chunks),
        "model": model,
        "cost_report": analyzer.tokens.report(),
        "cache_stats": analyzer.cache.stats(),
    }
    output_dict["_pipeline"] = {
        "codebase": codebase_path,
        "repo_url": repo_url,
        "mock_mode": mock,
    }

    output_path.write_text(
        json.dumps(output_dict, indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )
    print(f"  Saved: {output_path}")
    print()

    # ── Summary ──
    cost = analyzer.tokens.report()
    cache = analyzer.cache.stats()
    print("=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(f"  Discovered:    {len(all_files)} files")
    print(f"  Source files:  {len(source_files)}")
    print(f"  Config files:  {len(pp.config_files)}")
    print(f"  Skipped:       {pp.skipped_count}")
    print(f"  Lines of code: {total_lines:,}")
    print(f"  Languages:     {lang_counts}")
    print(f"  Chunks:        {len(chunks)}")
    print(f"  LLM calls:     {cost['calls']}")
    print(f"  Cache hits:    {cache['hit_rate']}")
    print(f"  Total cost:    ${cost['cost_usd']:.4f}")
    print(f"  Elapsed:       {elapsed:.1f}s")
    print("=" * 60)

    ov = result.project_overview
    print(f"\n  Project:       {ov.name}")
    print(f"  Description:   {ov.description}")
    print(f"  Purpose:       {ov.purpose[:100]}...")
    print(f"  Technologies:  {', '.join(ov.technologies[:6])}")
    print(f"  Total files:   {ov.total_files}")
    print(f"  Total LoC:     {ov.total_lines_of_code:,}")

    if result.key_classes:
        print(f"\n  Key Classes ({len(result.key_classes)}):")
        for kc in result.key_classes[:8]:
            method_count = len(kc.methods)
            print(f"    * {kc.name} ({kc.role}) [{kc.file_path.rsplit('/', 1)[-1]}]")
            print(f"      {kc.summary}")
            print(f"      Methods: {method_count}")

    if result.key_methods:
        print(f"\n  Key Methods ({len(result.key_methods)}):")
        for m in result.key_methods[:8]:
            print(f"    * {m.name} [{m.file_path.rsplit('/', 1)[-1]}]")
            print(f"      {m.description}")
            print(f"      Complexity: {m.complexity}/10")

    if result.complexity_analysis:
        ca = result.complexity_analysis
        print(f"\n  Complexity:    avg {ca.average_complexity}/10")
        if ca.complex_files:
            print(f"  Most Complex Files:")
            for cf in ca.complex_files[:3]:
                print(f"    * {cf.file_path.rsplit('/', 1)[-1]} (score: {cf.score}/10)")

    if result.chunk_analyses:
        print(f"\n  File Analyses ({len(result.chunk_analyses)}):")
        for ca in result.chunk_analyses[:5]:
            print(f"    * {ca.file_path.rsplit('/', 1)[-1]}: {ca.summary[:80]}")

    print(f"\n  Output: {output_path}")
    print()


if __name__ == "__main__":
    main()
