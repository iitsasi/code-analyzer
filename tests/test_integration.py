"""End-to-end integration test with mock LLM."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.models import AnalysisOutput
from src.reader import scan
from src.preprocessor import preprocess
from src.chunking import ChunkingEngine
from src.analyzer import Config, MapReduceAnalyzer


SAKILA = Path("/home/user/spring-rest-sakila")


@pytest.fixture
def run_pipeline():
    if not SAKILA.exists():
        pytest.skip("Sakila repo not found")

    cfg = Config(mock=True, max_concurrent=5, max_cost_usd=1.0)
    all_files = scan(SAKILA)
    pp = preprocess(all_files)
    source_files = pp.source_files

    lang_counts = {}
    for f in source_files:
        lang_counts[f.language.value] = lang_counts.get(f.language.value, 0) + 1

    chunker = ChunkingEngine(model=cfg.model, max_tokens=2000)
    chunks = chunker.chunk(source_files)

    stats = {
        "total_files": len(source_files),
        "total_lines": sum(f.line_count for f in source_files),
        "languages": lang_counts,
        "repo_url": "https://github.com/codejsha/spring-rest-sakila",
    }
    analyzer = MapReduceAnalyzer(cfg)
    result = analyzer.run(chunks, stats)
    return result, stats, chunks, analyzer, pp


def test_output_is_valid(run_pipeline):
    result, *_ = run_pipeline
    assert isinstance(result, AnalysisOutput)

def test_project_overview(run_pipeline):
    result, *_ = run_pipeline
    ov = result.project_overview
    assert ov.name
    assert ov.description
    assert ov.purpose
    assert ov.architecture
    assert len(ov.technologies) > 0
    assert ov.total_files > 0
    assert ov.total_lines_of_code > 0

def test_key_classes(run_pipeline):
    result, *_ = run_pipeline
    assert len(result.key_classes) > 0
    kc = result.key_classes[0]
    assert kc.name
    assert kc.file_path
    assert kc.role
    assert kc.summary
    assert len(kc.methods) > 0
    m = kc.methods[0]
    assert m.name
    assert m.signature
    assert m.description
    assert m.estimated_complexity >= 1

def test_key_methods(run_pipeline):
    result, *_ = run_pipeline
    assert len(result.key_methods) > 0
    m = result.key_methods[0]
    assert m.name
    assert m.signature
    assert m.description
    assert m.complexity > 0
    assert m.file_path

def test_complexity_analysis(run_pipeline):
    result, *_ = run_pipeline
    ca = result.complexity_analysis
    assert ca.average_complexity > 0
    assert len(ca.complex_files) > 0
    assert len(ca.dependencies) > 0
    cf = ca.complex_files[0]
    assert cf.file_path
    assert cf.score > 0
    assert cf.notes

def test_chunk_analyses(run_pipeline):
    result, *_ = run_pipeline
    assert len(result.chunk_analyses) > 0
    ca = result.chunk_analyses[0]
    assert ca.file_path
    assert ca.summary
    assert ca.purpose
    assert len(ca.key_methods) > 0
    assert ca.complexity_score > 0
    assert ca.complexity_notes

def test_services(run_pipeline):
    result, *_ = run_pipeline
    assert len(result.services) > 0
    svc = result.services[0]
    assert svc.service_name
    assert svc.description

def test_architectural_insights(run_pipeline):
    result, *_ = run_pipeline
    assert result.architectural_insights is not None
    assert result.architectural_insights.layers
    assert result.architectural_insights.design_patterns
    assert result.architectural_insights.data_flow

def test_json_round_trip(run_pipeline):
    result, *_ = run_pipeline
    dumped = result.model_dump()
    validated = AnalysisOutput(**dumped)
    assert validated.project_overview.name == result.project_overview.name
    assert len(validated.key_methods) == len(result.key_methods)
    assert len(validated.key_classes) == len(result.key_classes)

def test_java_detected(run_pipeline):
    _, stats, *_ = run_pipeline
    assert "java" in stats["languages"]
    assert stats["languages"]["java"] > 0

def test_preprocessor_filters(run_pipeline):
    _, _, _, _, pp = run_pipeline
    for f in pp.source_files:
        assert f.language.value == "java"
    assert len(pp.config_files) > 0
