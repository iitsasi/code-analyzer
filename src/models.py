"""
Data models — the type-safe contract for the output JSON schema.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Language(str, Enum):
    JAVA = "java"
    PYTHON = "python"
    GO = "go"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    JSX = "jsx"
    UNKNOWN = "unknown"


# ── Pipeline internal ──

class SourceFile(BaseModel):
    path: str
    relative_path: str
    language: Language
    content: str = Field(exclude=True)
    content_hash: str
    size_bytes: int
    line_count: int


class CodeChunk(BaseModel):
    chunk_id: str
    content: str = Field(exclude=True)
    token_count: int
    source_files: list[str]
    language: Language
    class_names: list[str] = []
    function_names: list[str] = []

    def to_prompt(self) -> str:
        header = f"### File: {', '.join(self.source_files)}\n"
        header += f"### Language: {self.language.value}\n"
        if self.class_names:
            header += f"### Classes: {', '.join(self.class_names)}\n"
        if self.function_names:
            header += f"### Functions: {', '.join(self.function_names[:15])}\n"
        return header + "---\n" + self.content


# ── Output schema models ──

class ProjectOverview(BaseModel):
    name: str = ""
    description: str = ""
    purpose: str = ""
    architecture: str = ""
    domain: str = ""
    primary_language: str = ""
    framework: str = ""
    technologies: list[str] = []
    modules: list[str] = []
    total_files: int = 0
    total_lines_of_code: int = 0
    model_config = {"extra": "ignore"}


class ArchitecturalInsights(BaseModel):
    layers: list[str] = []
    design_patterns: list[str] = []
    cross_cutting_concerns: list[str] = []
    data_flow: str = ""
    integration_points: list[str] = []
    strengths: list[str] = []
    areas_for_improvement: list[str] = []
    model_config = {"extra": "ignore"}


class EndpointInfo(BaseModel):
    method: str = ""
    path: str = ""
    description: str = ""
    handler: str = ""
    model_config = {"extra": "ignore"}


class ServiceInfo(BaseModel):
    service_name: str = ""
    description: str = ""
    purpose: str = ""
    classes: list[str] = []
    api_endpoints: list[EndpointInfo] = []
    complexity_notes: list[str] = []
    model_config = {"extra": "ignore"}


class ClassMethod(BaseModel):
    name: str = ""
    signature: str = ""
    description: str = ""
    http_method: str = ""
    path: Optional[str] = None
    estimated_complexity: int = 1
    model_config = {"extra": "ignore"}


class KeyClass(BaseModel):
    name: str = ""
    file_path: str = ""
    role: str = ""
    summary: str = ""
    methods: list[ClassMethod] = []
    model_config = {"extra": "ignore"}


class KeyMethod(BaseModel):
    name: str = ""
    signature: str = ""
    description: str = ""
    complexity: int = 0
    file_path: str = ""
    model_config = {"extra": "ignore"}


class ComplexFile(BaseModel):
    file_path: str = ""
    score: int = 0
    notes: str = ""
    model_config = {"extra": "ignore"}


class ComplexityAnalysis(BaseModel):
    average_complexity: float = 0.0
    complex_files: list[ComplexFile] = []
    dependencies: list[str] = []
    model_config = {"extra": "ignore"}


class ComplexityMetrics(BaseModel):
    total_files: int = 0
    total_lines: int = 0
    total_classes: int = 0
    total_functions: int = 0
    avg_methods_per_class: float = 0.0
    avg_complexity_score: float = 0.0
    largest_files: list[str] = []
    most_complex_classes: list[str] = []
    model_config = {"extra": "ignore"}


class ChunkMethod(BaseModel):
    name: str = ""
    signature: str = ""
    description: str = ""
    complexity: int = 0
    model_config = {"extra": "ignore"}


class ChunkAnalysis(BaseModel):
    file_path: str = ""
    summary: str = ""
    purpose: str = ""
    key_methods: list[ChunkMethod] = []
    dependencies: list[str] = []
    complexity_score: int = 0
    complexity_notes: str = ""
    model_config = {"extra": "ignore"}


class AnalysisOutput(BaseModel):
    """Final structured output — matches the required JSON schema."""
    project_overview: ProjectOverview = Field(default_factory=ProjectOverview)
    architectural_insights: Optional[ArchitecturalInsights] = None
    services: list[ServiceInfo] = []
    key_classes: list[KeyClass] = []
    key_methods: list[KeyMethod] = []
    complexity_metrics: Optional[ComplexityMetrics] = None
    cross_cutting_patterns: list[str] = []
    recommendations: list[str] = []
    complexity_analysis: ComplexityAnalysis = Field(default_factory=ComplexityAnalysis)
    chunk_analyses: list[ChunkAnalysis] = []
    model_config = {"extra": "ignore"}
