# Codebase Analyzer

A  Python program that analyzes the codebase, extracts structural knowledge, and synthesizes a **machine-readable JSON** report using **LangChain** and an OpenAI-compatible LLM.

The analyzer supports **multiple languages** (Java, Python, Go, JavaScript, TypeScript), uses **AST-aware chunking** to split code at semantic boundaries, and runs **concurrent map-reduce LLM analysis** to produce a comprehensive structured report — all with configurable cost limits, caching, and guardrails.

---

## Architecture

```
┌──────────────┐   ┌───────────────┐   ┌──────────────────┐   ┌───────────────────┐   ┌──────────────┐
│  1. Reader    │──>│  2. Preprocess │──>│  3. Chunking     │──>│  4. LLM Analyzer  │──>│  5. Output   │
│  reader.py    │   │  preprocessor  │   │  chunking.py     │   │  analyzer.py      │   │  models.py   │
│               │   │  .py           │   │  parsers.py      │   │  mock_data.py     │   │              │
├──────────────┤   ├───────────────┤   ├──────────────────┤   ├───────────────────┤   ├──────────────┤
│ Walk dirs     │   │ Filter source  │   │ AST parsing      │   │ Map: async        │   │ Pydantic     │
│ Detect lang   │   │ Skip generated │   │ Class/method     │   │   parallel chunk  │   │ validation   │
│ Load content  │   │ Skip binary    │   │   boundaries     │   │   analysis        │   │ Structured   │
│ Hash files    │   │ Clean source   │   │ Token counting   │   │ Reduce: synthesize│   │ JSON output  │
│               │   │ Categorize     │   │ Batch small      │   │   unified report  │   │              │
└──────────────┘   └───────────────┘   └──────────────────┘   │ Cache + cost      │   └──────────────┘
                                                               │   tracking        │
                                                               └───────────────────┘
```

The pipeline has five stages, each with a single responsibility. Every stage produces typed Pydantic models that flow into the next stage. The LLM stage uses a **map-reduce** pattern: parallel chunk-level analysis (map) followed by a single synthesis call (reduce). This keeps token usage bounded while enabling analysis of arbitrarily large codebases.

---

## Best Practices

### Multi-Language Support

The analyzer uses **tree-sitter** for AST-aware parsing of five languages, with a regex fallback for everything else:

| Language | Parser | What it extracts |
|----------|--------|------------------|
| Java | tree-sitter-java | classes, interfaces, enums, methods, annotations, fields, javadoc |
| Python | tree-sitter-python | classes, functions, decorators, docstrings, type hints |
| Go | tree-sitter-go | structs, interfaces, methods (with receiver), fields, visibility |
| JavaScript | tree-sitter-javascript | classes, methods, arrow functions, exports, imports |
| TypeScript | tree-sitter-typescript | all JS + interfaces, type annotations, decorators |
| Other | regex fallback | class/function extraction for Ruby, Rust, C#, C/C++, etc. |

AST-aware parsing means chunks split at **class and method boundaries**, not arbitrary line counts. The LLM gets coherent, complete code units — not fragments that lose context.

### Concurrent Parallel Processing with Map-Reduce

The LLM analysis uses a two-phase **map-reduce** pattern:

- **Map phase**: Each chunk is analyzed independently via `asyncio.gather()`. A semaphore limits concurrency (default: 5 parallel calls). Partial failures don't kill the run — failed chunks get error placeholders.
- **Reduce phase**: All chunk-level results are aggregated into a single synthesis prompt. One LLM call produces the final structured report with cross-cutting insights.

This design means:
- Every LLM call stays under the token limit
- Chunks are analyzed in parallel (5x faster than sequential)
- A single bad chunk doesn't crash the entire analysis
- The codebase can scale to any size — just add more chunks

### Configurable Guardrails

All limits are configurable via `config/default.yaml` and `.env`:

| Guardrail | Default | What it does |
|-----------|---------|--------------|
| `max_chunk_tokens` | 4000 | Max tokens per code chunk sent to LLM |
| `max_concurrent` | 5 | Max parallel LLM API calls |
| `max_cost_usd` | 5.0 | Hard cost ceiling — analysis stops if exceeded |
| `max_output_tokens` | 16000 | Max tokens the LLM can generate per call |
| `safety_margin_tokens` | 2000 | Buffer to prevent hitting context limits |
| `temperature` | 0.1 | Low temperature for deterministic output |

Every LLM call checks the budget before executing. If the estimated cost would exceed `max_cost_usd`, the call is skipped and an error placeholder is returned.

### Cache to Reduce Cost and Time

A **two-tier cache** (in-memory LRU + persistent disk) avoids re-analyzing unchanged code:

- **Cache key**: `SHA256(content_hash + model + prompt_version + chunk_id)`
- **First run**: All chunks are analyzed, results cached to `.analyzer_cache/`
- **Subsequent runs**: 100% cache hit on the map phase — only the reduce/synthesis call runs
- **Changed files**: Only the changed chunks trigger re-analysis; unchanged chunks hit cache

Measured impact on the Sakila project:

| Metric | Cold run | Warm run |
|--------|----------|----------|
| Cache hit rate | 0% | 100% |
| LLM calls | 26 | 1 |
| Cost | $0.026 | $0.006 |
| Elapsed | 0.5s | 0.4s |

### Preprocessing to Remove Non-Source Files

Before any LLM call, the preprocessor filters and cleans the file inventory:

| Category | Action | Examples |
|----------|--------|----------|
| Source code | Clean + analyze | `.java`, `.py`, `.go`, `.js`, `.ts`, `.kt` |
| Config files | Keep as context | `.xml`, `.yaml`, `.json`, `.properties`, `.sql`, `.md` |
| Generated code | Skip | Files with "auto-generated", "do not edit" markers |
| Binary / minified | Skip | `.class`, `.pyc`, `.min.js`, images, archives |
| Directories | Skip | `.git`, `node_modules`, `__pycache__`, `build`, `target`, `.venv` |

Source files are cleaned before chunking:
- Runs of 3+ blank lines collapsed to 1
- Trailing whitespace removed
- This reduces token count without changing line numbers (important for AST parsing)

### Error Handling and Reliability

- **JSON repair**: A cascade of repair strategies handles common LLM output issues — markdown fences, trailing commas, comments, truncated responses
- **Retry with backoff**: LLM calls retry up to 3 times with exponential backoff
- **Partial failure tolerance**: A failed chunk gets an error placeholder; the pipeline continues
- **Fallback output**: If the reduce phase fails, a basic report is built from chunk-level data
- **Pydantic validation**: Every LLM response is validated against strict schemas before passing downstream
- **Content-hash caching**: Any file change invalidates only that file's cache entry

### Performance

| Metric | Value |
|--------|-------|
| Files scanned | 213 |
| Source files analyzed | 200 |
| Chunks after batching | 25 (down from 199 raw) |
| API call reduction | 87% (batching) |
| Concurrent LLM calls | 5 (configurable) |
| Cold run latency | ~0.5s (mock), ~2-5min (real) |
| Warm run latency | ~0.4s (100% cache hit) |
| Cost per run | ~$0.026 (gpt-4o-mini, cold) |
| Cost on re-run | ~$0.006 (cache hit on map phase) |

---

## Workflow Steps

```
Step 1: File Discovery (reader.py)
         Walk directory tree, detect languages by extension,
         load content, compute content hashes, skip hard-excluded dirs
                    |
                    v
Step 2: Preprocessing (preprocessor.py)
         Filter: keep only source code files
         Skip: generated, binary, minified, tiny non-source
         Clean: remove excess blank lines, trailing whitespace
         Categorize: source files vs config files
                    |
                    v
Step 3: Chunking (chunking.py + parsers.py)
         Parse AST using language-specific tree-sitter parser
         Split at class/method boundaries
         If class too large: split by method groups
         If method too large: split by lines
         Batch small chunks together (reduces API calls 87%)
                    |
                    v
Step 4: LLM Analysis (analyzer.py)
         MAP:  Each chunk -> async LLM call -> structured JSON
               (parallel via asyncio.gather, semaphore-limited)
         REDUCE: All chunk results -> synthesis LLM call -> final report
               (single call with aggregated context)
         Cache: content-hash keyed, two-tier (memory + disk)
         Cost: tracked per call, hard ceiling enforced
                    |
                    v
Step 5: Output (models.py)
         Pydantic validation against strict schema
         JSON repair for common LLM output quirks
         Write structured JSON to output/codebase_analysis.json
```

---

## Initial Setup

### Prerequisites

| Requirement | Version | Check |
|-------------|---------|-------|
| Python | 3.10+ | `python --version` |
| Git | 2.0+ | `git --version` |
| OpenAI API key | — | Only needed for real analysis, not mock mode |

### Linux / macOS

```bash
cd codebase-analyzer
bash setup.sh
```

The setup script:
1. Verifies Python 3.10+
2. Creates a virtual environment at `.venv/`
3. Installs all dependencies from `requirements.txt`
4. Verifies all 5 tree-sitter language parsers work
5. Runs the full 58-test suite

### Windows (Command Prompt)

```cmd
cd codebase-analyzer
setup.bat
```

### Manual Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Two files control behavior:

**`config/default.yaml`** — Static defaults (same across all environments):

```yaml
model: gpt-4o-mini
temperature: 0.1
max_chunk_tokens: 4000
max_concurrent: 5
max_cost_usd: 5.0
cache_enabled: true
# ... file filters, skip dirs, generated markers
```

**`.env`** — Environment-specific (changes per developer/machine):

```env
# Where is the codebase?
CODEBASE_PATH=/home/user/spring-rest-sakila
CODEBASE_REPO_URL=https://github.com/codejsha/spring-rest-sakila

# Mock mode (no API calls) or real analysis?
CODEBASE_MOCK=true

# OpenAI API key (only needed when CODEBASE_MOCK=false)
# OPENAI_API_KEY=sk-proj-...
```

| `.env` Variable | Purpose | Default |
|---|---|---|
| `CODEBASE_PATH` | Path to codebase to analyze | sakila project |
| `CODEBASE_REPO_URL` | GitHub URL for metadata | sakila URL |
| `CODEBASE_MOCK` | `true` = no API calls, `false` = real | `true` |
| `OPENAI_API_KEY` | Required when `CODEBASE_MOCK=false` | — |
| `CODEBASE_OUTPUT_DIR` | Output directory | `output` |

Priority: `.env` values override `default.yaml` values.

---

## Quick Start

```bash
# 1. Setup (one time)
cd codebase-analyzer
bash setup.sh

# 2. Configure (edit .env to point at your codebase)
cp .env.example .env

# 3. Run
python run_analysis.py
```

That's it. No flags, no arguments. Everything is driven by `config/default.yaml` + `.env`.

### To analyze a different project

Edit `.env`:

```env
CODEBASE_PATH=C:\Projects\my-spring-app
CODEBASE_REPO_URL=https://github.com/me/my-app
```

### To use real OpenAI

Edit `.env`:

```env
CODEBASE_MOCK=false
OPENAI_API_KEY=sk-proj-...
```

### To change model or cost limit

Edit `config/default.yaml`:

```yaml
model: gpt-4o
max_cost_usd: 10.0
max_concurrent: 3
```

---

## Explain the Code

### `run_analysis.py` — Entry Point

Loads `config/default.yaml` for static defaults, overlays `.env` for environment-specific values, then orchestrates the five-stage pipeline. No CLI framework — just reads config and runs.

### `src/reader.py` — Stage 1: File Discovery

Walks the directory tree using `pathlib.rglob()`. Detects language by file extension. Skips hard-excluded directories (`.git`, `node_modules`, `build`, `__pycache__`, etc.) at the path-component level. Computes SHA-256 content hashes for cache keys. Returns a sorted list of `SourceFile` objects.

### `src/preprocessor.py` — Stage 2: Filtering and Cleaning

Separates the raw file inventory into three categories:
- **Source files**: `.java`, `.py`, `.go`, `.js`, `.ts` — these get analyzed
- **Config files**: `.xml`, `.yaml`, `.json`, `.properties`, `.sql` — kept as context, not chunked
- **Skipped**: generated code, binary files, minified files, tiny non-source files

Source files are cleaned: excess blank lines collapsed, trailing whitespace removed. This reduces token count without changing line numbers (important for AST parsing to stay aligned).

### `src/parsers.py` — AST Parsers (Multi-Language)

One file contains all six parsers:
- `parse_java()` — tree-sitter-java: classes, interfaces, enums, methods, annotations, fields
- `parse_python()` — tree-sitter-python: classes, functions, decorators, docstrings
- `parse_go()` — tree-sitter-go: structs, interfaces, methods with receiver, fields, visibility
- `parse_javascript()` — tree-sitter-javascript: classes, methods, arrow functions, exports
- `parse_typescript()` — tree-sitter-typescript: all JS + interfaces, type annotations
- `parse_regex()` — regex fallback for any language

Each parser returns a list of `ParsedClass` objects with functions, fields, imports, and line numbers. The `get_parser()` registry maps `Language` enum values to parser functions.

### `src/chunking.py` — Stage 3: AST-Aware Chunking

The `ChunkingEngine` splits code at semantic boundaries:

1. If the file fits in one chunk (under `max_chunk_tokens`) → send as-is
2. If it's a supported language → parse AST, split by class boundary
3. If a class is too large → split by method groups
4. If a method is too large → split by lines
5. Fallback for unsupported languages → line-based splitting

After splitting, small chunks are **batched** together to reduce API calls. The Sakila project goes from 199 raw chunks to 25 batches — an 87% reduction in LLM calls.

Token counting uses **tiktoken** with the exact tokenizer for the configured model (not a heuristic like `len(text) // 4`).

### `src/analyzer.py` — Stage 4: Map-Reduce LLM Analysis

The core of the system. Contains:

- **`Config`**: Pydantic model for all settings (model, tokens, cost, concurrency)
- **`TokenManager`**: Counts tokens, tracks cumulative cost, enforces budget ceiling
- **`Cache`**: Two-tier (memory LRU + disk JSON), content-hash keyed
- **`LLMClient`**: LangChain `ChatOpenAI` wrapper with async support, or mock mode
- **`repair_json()`**: Cascade of strategies to fix common LLM JSON output issues
- **Prompts**: `MAP_SYSTEM`/`MAP_USER` for chunk analysis, `REDUCE_SYSTEM`/`REDUCE_USER` for synthesis
- **`MapReduceAnalyzer`**: Orchestrates the async map-reduce pipeline

The map phase runs `asyncio.gather()` with a semaphore limiting concurrency. Each chunk gets its own LLM call. Results are cached by content hash. The reduce phase takes all chunk results and produces the final report in a single synthesis call.

### `src/mock_data.py` — Mock LLM Responses

Contains `MOCK_CHUNK` and `MOCK_SYNTHESIS` — deterministic JSON responses used when `CODEBASE_MOCK=true`. This enables full pipeline testing without an API key. To test with a different codebase, replace the mock data here.

### `src/models.py` — Pydantic Data Models

Defines every data structure flowing through the pipeline:
- `SourceFile`, `CodeChunk` — pipeline internals
- `AnalysisOutput` — the final deliverable with all sections
- `ProjectOverview`, `KeyClass`, `KeyMethod`, `ServiceInfo`, `ArchitecturalInsights`, `ComplexityAnalysis`, `ChunkAnalysis` — output sections

Every LLM response is validated against these models before passing downstream. The models also auto-generate JSON schema for documentation.

### `config/default.yaml` — Static Configuration

All settings that don't change per environment: model name, token limits, chunk size, concurrency, file filters, skip directories, generated file markers. Loaded once at startup.

### `.env` — Environment Configuration

Settings that change per developer or environment: codebase path, API key, mock mode, output directory. Loaded via `python-dotenv` at startup, overrides YAML defaults.

---

## Project Layout

```
codebase-analyzer/
├── run_analysis.py             # Entry point — just run this
├── requirements.txt            # Python dependencies
├── config/
│   └── default.yaml            # Static configuration
├── .env.example                # Copy to .env and configure
├── .env                        # Environment-specific (gitignored)
├── setup.sh                    # Linux/macOS setup
├── setup.bat                   # Windows Command Prompt setup
├── setup.ps1                   # Windows PowerShell setup
├── README.md                   # This file
│
├── src/
│   ├── models.py               # Pydantic data models (pipeline contracts)
│   ├── reader.py               # Stage 1: file discovery
│   ├── preprocessor.py         # Stage 2: filter + clean
│   ├── parsers.py              # AST parsers (Java, Python, Go, JS, TS, regex)
│   ├── chunking.py             # Stage 3: AST-aware chunking + batching
│   ├── analyzer.py             # Stage 4: map-reduce LLM + cache + cost tracking
│   └── mock_data.py            # Mock LLM responses for testing
│
├── tests/
│   ├── test_parsers.py         # 18 tests (Java, Python, Go, JS, TS, regex)
│   ├── test_preprocessor.py    # 21 tests (filtering, cleaning, categorization)
│   ├── test_chunking.py        #  9 tests (multi-lang chunking, batching)
│   └── test_integration.py     # 10 tests (full pipeline end-to-end)
│
└── output/
    └── codebase_analysis.json  # Generated report
```

---

## Example Output

The analyzer produces `output/codebase_analysis.json` with this structure:

```json
{
  "project_overview": {
    "name": "Spring REST Sakila",
    "description": "A Spring Boot REST API that provides CRUD operations for the Sakila sample database, which models a DVD rental store.",
    "purpose": "To demonstrate building RESTful APIs with Spring Boot by implementing a complete backend for a DVD rental management system.",
    "architecture": "Layered architecture following the MVC pattern with clear separation of concerns:\n\n* Controller Layer: REST endpoints...\n* Service Layer: Business logic...\n* Repository Layer: Data access...\n* Entity Layer: JPA entities...",
    "domain": "DVD rental management",
    "primary_language": "Java",
    "framework": "Spring Boot",
    "technologies": ["Java 17", "Spring Boot 3.x", "Spring Data JPA", "Spring Security", "QueryDSL", "MapStruct", "MySQL", "Redis"],
    "modules": ["controller", "service", "repository", "entity", "mapper", "security", "exception_handling"],
    "total_files": 200,
    "total_lines_of_code": 11200
  },

  "architectural_insights": {
    "layers": ["Controller -> Service -> Repository -> JPA -> MySQL"],
    "design_patterns": ["Repository Pattern", "DTO Pattern", "HATEOAS", "MapStruct Mapper"],
    "cross_cutting_concerns": ["JWT Authentication", "Global Exception Handling", "MDC Logging", "Redis Caching"],
    "data_flow": "HTTP -> SecurityFilter -> Controller -> Service -> Repository -> JPA -> MySQL -> Response",
    "integration_points": ["MySQL", "Redis"],
    "strengths": ["Clean separation of concerns", "Consistent DTO mapping with MapStruct"],
    "areas_for_improvement": ["Add integration tests", "Implement rate limiting"]
  },

  "services": [
    {
      "service_name": "catalog",
      "description": "Film and actor catalog management",
      "purpose": "Manage the film/actor catalog including CRUD and search",
      "classes": ["ActorController", "FilmController", "ActorServiceImpl", "FilmServiceImpl"],
      "api_endpoints": [
        {"method": "GET", "path": "/api/v1/actors", "description": "List actors", "handler": "getActorList"},
        {"method": "POST", "path": "/api/v1/actors", "description": "Create actor", "handler": "addActor"}
      ],
      "complexity_notes": ["Uses QueryDSL for dynamic actor search"]
    }
  ],

  "key_classes": [
    {
      "name": "ActorController",
      "file_path": "src/main/java/com/example/app/services/catalog/controller/ActorController.java",
      "role": "controller",
      "summary": "REST controller exposing Sakila Actor APIs with HATEOAS support.",
      "methods": [
        {
          "name": "getActorList",
          "signature": "ResponseEntity<CollectionModel<ActorDto>> getActorList(Pageable pageable)",
          "description": "Retrieves paginated list of all actors",
          "http_method": "GET",
          "path": "/api/v1/actors",
          "estimated_complexity": 2
        },
        {
          "name": "addActor",
          "signature": "ResponseEntity<Void> addActor(@RequestBody ActorDto.ActorRequest model)",
          "description": "Creates a new actor",
          "http_method": "POST",
          "path": "/api/v1/actors",
          "estimated_complexity": 5
        }
      ]
    }
  ],

  "key_methods": [
    {
      "name": "rentDvd",
      "signature": "ResponseEntity<Void> rentDvd(@RequestBody RentalDto.RentalCreateRequest model)",
      "description": "Creates a new rental transaction. Validates inventory availability and sets rental dates.",
      "complexity": 7,
      "file_path": "src/main/java/.../RentalController.java"
    }
  ],

  "complexity_metrics": {
    "total_files": 200,
    "total_lines": 11200,
    "total_classes": 85,
    "total_functions": 320,
    "avg_methods_per_class": 3.8,
    "avg_complexity_score": 4.2,
    "largest_files": ["FilmDto.java", "Country.java", "RentalDto.java"],
    "most_complex_classes": ["CustomActorRepositoryImpl", "FilmEntity", "RentalServiceImpl"]
  },

  "complexity_analysis": {
    "average_complexity": 4.2,
    "complex_files": [
      {
        "file_path": "src/main/java/.../RentalServiceImpl.java",
        "score": 8,
        "notes": "High complexity due to rental business logic including inventory checks, date calculations, and transaction management."
      }
    ],
    "dependencies": ["spring-boot-starter-web", "spring-boot-starter-data-jpa", "spring-boot-starter-security"]
  },

  "cross_cutting_patterns": [
    "All entities use MapStruct for DTO conversion",
    "Consistent use of Spring HATEOAS for REST resources",
    "QueryDSL for type-safe dynamic queries"
  ],

  "recommendations": [
    "Add OpenAPI 3.0 documentation with springdoc-openapi",
    "Implement request rate limiting with bucket4j or resilience4j",
    "Add comprehensive integration tests for each service"
  ],

  "chunk_analyses": [
    {
      "file_path": "src/main/java/.../RentalServiceImpl.java",
      "summary": "Service implementing rental business logic with inventory management.",
      "purpose": "Encapsulate rental business rules and transaction management.",
      "key_methods": [
        {"name": "createRental", "signature": "RentalDto createRental(RentalDto dto)", "description": "Creates rental with inventory validation", "complexity": 7}
      ],
      "dependencies": ["RentalRepository", "InventoryRepository"],
      "complexity_score": 8,
      "complexity_notes": "High complexity from business rules, transactions, and date calculations."
    }
  ]
}
```

---

## Assumptions and Limitations

| Item | Detail |
|------|--------|
| **Language focus** | Primary analysis targets Java, Python, Go, JS, TS via tree-sitter AST. Other languages use regex fallback — may miss edge-case signatures. |
| **Parser accuracy** | Tree-sitter is error-tolerant and handles most code, but complex generics or macros may produce incomplete extractions. |
| **LLM accuracy** | Descriptions and complexity scores are model-generated. The synthesis step should be reviewed for critical use cases. |
| **Cost** | Full LLM run issues one call per chunk plus one merge call (~26 calls for the Sakila repo). Cost is ~$0.026 with gpt-4o-mini. |
| **Provider** | OpenAI API by default via LangChain. Other providers need a compatible `ChatOpenAI` adapter or custom `LLMClient` implementation. |
| **Codebase size** | Designed for small-to-medium codebases (<500 files, <100k LoC). For larger codebases, consider increasing `max_chunk_tokens` or sampling. |
| **Static analysis** | The preprocessor and AST parser are static — they cannot reason about runtime behavior, reflection, or dynamic proxies. |

---

## Requirements

- Python 3.10+
- OpenAI API key (for full LLM mode only — mock mode works without one)

## Assignment Deliverables

| Deliverable | Location |
|-------------|----------|
| Source code | `codebase-analyzer/` |
| Structured JSON | `codebase-analyzer/output/codebase_analysis.json` |
| Documentation | This README |
| Tests | `codebase-analyzer/tests/` (58 tests) |
| Configuration | `config/default.yaml` + `.env.example` |
