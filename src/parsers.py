"""
Multi-language AST parsers using tree-sitter.

One file per language family, shared dataclasses, clean registry.
Falls back to regex for unsupported languages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Language as TSLanguage, Node, Parser

from src.models import Language

# ── Shared data structures ──

@dataclass
class ParsedFunction:
    name: str
    return_type: str = ""
    parameters: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    docstring: str = ""
    start_line: int = 0
    end_line: int = 0

    @property
    def is_public(self) -> bool:
        if "private" in self.modifiers:
            return False
        if "public" in self.modifiers:
            return True
        return not self.name.startswith("_")


@dataclass
class ParsedClass:
    name: str
    package: str = ""
    kind: str = "class"
    decorators: list[str] = field(default_factory=list)
    superclass: str = ""
    interfaces: list[str] = field(default_factory=list)
    docstring: str = ""
    functions: list[ParsedFunction] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0

    @property
    def fqn(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name


# ── Tree-sitter language bindings (loaded once) ──

import tree_sitter_java as _ts_java
import tree_sitter_python as _ts_python
import tree_sitter_go as _ts_go
import tree_sitter_javascript as _ts_js
import tree_sitter_typescript as _ts_ts

_TS_LANGS = {
    "java":       TSLanguage(_ts_java.language()),
    "python":     TSLanguage(_ts_python.language()),
    "go":         TSLanguage(_ts_go.language()),
    "javascript": TSLanguage(_ts_js.language()),
    "typescript": TSLanguage(_ts_ts.language_typescript()),
}


def _make_parser(lang_key: str) -> Parser:
    return Parser(_TS_LANGS[lang_key])


# ── Utility helpers ──

def _text(node: Node) -> str:
    return node.text.decode("utf-8")


def _child(node: Node, kind: str) -> Optional[Node]:
    for c in node.children:
        if c.type == kind:
            return c
    return None


def _child_text(node: Node, kind: str) -> str:
    c = _child(node, kind)
    return _text(c) if c else ""


def _children_of_kind(node: Node, kind: str) -> list[Node]:
    return [c for c in node.children if c.type == kind]


def _modifiers(node: Node) -> list[str]:
    mods = _child(node, "modifiers")
    return [_text(m) for m in mods.children] if mods else []


def _annotations(node: Node) -> list[str]:
    mods = _child(node, "modifiers")
    if not mods:
        return []
    return [_text(m) for m in mods.children if m.type in ("marker_annotation", "annotation")]


def _decorators_before(node: Node, source: str) -> list[str]:
    """Extract decorators/annotations above a declaration."""
    lines = source.split("\n")
    start = node.start_point[0]
    result: list[str] = []
    for i in range(start - 1, max(-1, start - 10), -1):
        line = lines[i].strip()
        if line.startswith("@"):
            result.insert(0, line)
        elif line:
            break
    return result


def _docstring_above(node: Node, source: str) -> str:
    """Extract Javadoc/docstring above a declaration."""
    lines = source.split("\n")
    start = node.start_point[0]
    doc: list[str] = []
    found = False
    for i in range(start - 1, max(-1, start - 20), -1):
        line = lines[i].strip()
        if line.endswith("*/"):
            found = True
            doc.insert(0, line)
        elif found:
            doc.insert(0, line)
            if line.startswith("/**") or line.startswith("/*"):
                break
        elif line.startswith("#") or line.startswith("//"):
            doc.insert(0, line.lstrip("#/ "))
        else:
            break
    return "\n".join(doc).replace("/**", "").replace("*/", "").replace(" * ", " ").strip()[:500]


def _preview(source: str, start: int, end: int) -> str:
    lines = source.split("\n")
    return "\n".join(lines[start - 1 : min(start + 6, end)])[:300]


# ================================================================
# Java
# ================================================================

def parse_java(source: str, package: str = "") -> list[ParsedClass]:
    tree = _make_parser("java").parse(bytes(source, "utf-8"))
    root = tree.root_node

    # Package
    for c in root.children:
        if c.type == "package_declaration":
            pid = _child(c, "scoped_identifier") or _child(c, "identifier")
            if pid:
                package = _text(pid)

    # Imports
    imports = []
    for c in root.children:
        if c.type == "import_declaration":
            imports.append(_text(c).replace("import ", "").replace(";", "").strip())

    classes: list[ParsedClass] = []
    for c in root.children:
        if c.type not in ("class_declaration", "interface_declaration",
                          "enum_declaration", "record_declaration"):
            continue
        kind = {"class_declaration": "class", "interface_declaration": "interface",
                "enum_declaration": "enum", "record_declaration:": "record"}.get(c.type, "class")

        name = _child_text(c, "identifier") or "Unknown"
        superclass = ""
        interfaces: list[str] = []
        for sub in c.children:
            if sub.type == "superclass":
                superclass = _child_text(sub, "type_identifier")
            elif sub.type in ("super_interfaces", "extends_interfaces"):
                interfaces = [_text(t) for t in sub.children if t.type in ("type_identifier", "generic_type")]

        funcs: list[ParsedFunction] = []
        flds: list[dict] = []
        body = _child(c, "class_body") or _child(c, "interface_body")
        if body:
            for m in body.children:
                if m.type in ("method_declaration", "constructor_declaration"):
                    fname = "constructor"
                    ret = "void"
                    for mc in m.children:
                        if mc.type == "identifier" and fname == "constructor":
                            fname = _text(mc)
                        elif mc.type in ("type_identifier", "void_type", "generic_type", "array_type"):
                            ret = _text(mc)
                    params = []
                    fp = _child(m, "formal_parameters")
                    if fp:
                        params = [_text(p) for p in fp.children if p.type == "formal_parameter"]
                    funcs.append(ParsedFunction(
                        name=fname, return_type=ret, parameters=params,
                        decorators=_annotations(m), modifiers=_modifiers(m),
                        start_line=m.start_point[0] + 1, end_line=m.end_point[0] + 1,
                    ))
                elif m.type == "field_declaration":
                    ftype, fname = "", ""
                    for fc in m.children:
                        if fc.type in ("type_identifier", "generic_type", "array_type"):
                            ftype = _text(fc)
                        elif fc.type == "variable_declarator":
                            id_node = _child(fc, "identifier")
                            if id_node:
                                fname = _text(id_node)
                    if fname:
                        flds.append({"name": fname, "type": ftype})

        classes.append(ParsedClass(
            name=name, package=package, kind=kind,
            decorators=_annotations(c), superclass=superclass,
            interfaces=interfaces, docstring=_docstring_above(c, source),
            functions=funcs, fields=flds, imports=imports,
            start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
        ))

    return classes


# ================================================================
# Python
# ================================================================

def parse_python(source: str, package: str = "") -> list[ParsedClass]:
    tree = _make_parser("python").parse(bytes(source, "utf-8"))
    root = tree.root_node

    imports = []
    for c in root.children:
        if c.type in ("import_statement", "import_from_statement"):
            imports.append(_text(c).strip())

    classes: list[ParsedClass] = []
    module_funcs: list[ParsedFunction] = []

    def _parse_fn(node: Node) -> ParsedFunction:
        name = _child_text(node, "identifier") or ""
        params_node = _child(node, "parameters")
        params = []
        if params_node:
            raw = _text(params_node).strip("()")
            params = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
        ret = _child_text(node, "type").lstrip("->").strip() if _child(node, "type") else ""
        mods = []
        if _text(node).strip().startswith("async"):
            mods.append("async")
        mods.append("public" if not name.startswith("_") else "private")
        return ParsedFunction(
            name=name, return_type=ret, parameters=params,
            decorators=_decorators_before(node, source), modifiers=mods,
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        )

    def _parse_docstring(body_node: Node) -> str:
        if body_node and body_node.children:
            first = body_node.children[0]
            if first.type == "expression_statement":
                for sub in first.children:
                    if sub.type == "string":
                        t = _text(sub)
                        for q in ('"""', "'''", '"', "'"):
                            if t.startswith(q) and t.endswith(q):
                                return t[len(q):-len(q)].strip()[:500]
        return ""

    for c in root.children:
        if c.type == "class_definition":
            name = _child_text(c, "identifier") or "Unknown"
            superclass, interfaces = "", []
            arg_list = _child(c, "argument_list")
            if arg_list:
                bases = [b.strip() for b in _text(arg_list).strip("()").split(",") if b.strip()]
                if bases:
                    superclass = bases[0]
                    interfaces = bases[1:]

            body = _child(c, "block")
            funcs = []
            if body:
                for m in body.children:
                    if m.type == "function_definition":
                        funcs.append(_parse_fn(m))

            classes.append(ParsedClass(
                name=name, package=package, kind="class",
                decorators=_decorators_before(c, source),
                superclass=superclass, interfaces=interfaces,
                docstring=_parse_docstring(body), functions=funcs, imports=imports,
                start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
            ))

        elif c.type == "function_definition":
            module_funcs.append(_parse_fn(c))

    if not classes and module_funcs:
        mod_name = package.split(".")[-1] if package else "module"
        classes.append(ParsedClass(
            name=mod_name, package=package, kind="module",
            functions=module_funcs, imports=imports,
            start_line=1, end_line=source.count("\n") + 1,
        ))
    return classes


# ================================================================
# Go
# ================================================================

def parse_go(source: str, package: str = "") -> list[ParsedClass]:
    tree = _make_parser("go").parse(bytes(source, "utf-8"))
    root = tree.root_node

    # Package
    for c in root.children:
        if c.type == "package_clause":
            pid = _child(c, "package_identifier")
            if pid:
                package = _text(pid)

    # Imports
    imports = []
    for c in root.children:
        if c.type == "import_declaration":
            for sub in c.children:
                if sub.type == "import_spec_list":
                    for spec in sub.children:
                        if spec.type == "import_spec":
                            imports.append(_text(spec).strip('"'))
                elif sub.type == "import_spec":
                    imports.append(_text(sub).strip('"'))

    type_map: dict[str, ParsedClass] = {}

    for c in root.children:
        if c.type == "type_declaration":
            for sub in c.children:
                if sub.type != "type_spec":
                    continue
                tname = _child_text(sub, "type_identifier")
                if not tname:
                    continue
                kind, flds = "struct", []
                struct_type = _child(sub, "struct_type")
                iface_type = _child(sub, "interface_type")
                if struct_type:
                    fld_list = _child(struct_type, "field_declaration_list")
                    if fld_list:
                        for fd in fld_list.children:
                            if fd.type == "field_declaration":
                                fn = ""
                                ft = ""
                                for fc in fd.children:
                                    if fc.type == "field_identifier":
                                        fn = _text(fc)
                                    elif fc.type in ("type_identifier", "pointer_type",
                                                      "qualified_type", "slice_type",
                                                      "map_type", "primitive_type"):
                                        ft = _text(fc)
                                if fn:
                                    flds.append({"name": fn, "type": ft})
                elif iface_type:
                    kind = "interface"

                type_map[tname] = ParsedClass(
                    name=tname, package=package, kind=kind,
                    fields=flds, imports=imports, docstring=_docstring_above(c, source),
                    start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
                )

    # Methods and functions
    for c in root.children:
        if c.type == "method_declaration":
            receiver_type = ""
            fname = ""
            params = []
            for sub in c.children:
                if sub.type in ("identifier", "field_identifier"):
                    if not fname:
                        fname = _text(sub)
                elif sub.type == "parameter_list":
                    raw = _text(sub).strip("()")
                    if not fname:
                        # Receiver
                        for pd in sub.children:
                            if pd.type == "parameter_declaration":
                                parts = _text(pd).split()
                                if parts:
                                    receiver_type = parts[-1].lstrip("*")
                    else:
                        if raw:
                            params = [p.strip() for p in raw.split(",") if p.strip()]

            if fname and receiver_type in type_map:
                type_map[receiver_type].functions.append(ParsedFunction(
                    name=fname, parameters=params,
                    modifiers=["public" if fname[0].isupper() else "private"],
                    start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
                ))

        elif c.type == "function_declaration":
            fname = _child_text(c, "identifier") or _child_text(c, "field_identifier")
            params = []
            pl = _child(c, "parameter_list")
            if pl:
                raw = _text(pl).strip("()")
                if raw:
                    params = [p.strip() for p in raw.split(",") if p.strip()]
            if fname:
                key = f"_func_{fname}"
                type_map[key] = ParsedClass(
                    name=fname, package=package, kind="function",
                    functions=[ParsedFunction(
                        name=fname, parameters=params,
                        modifiers=["public" if fname[0].isupper() else "private"],
                        start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
                    )],
                    imports=imports,
                    start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
                )

    return list(type_map.values())


# ================================================================
# JavaScript / TypeScript
# ================================================================

def _parse_js_ts(source: str, package: str, lang_key: str) -> list[ParsedClass]:
    tree = _make_parser(lang_key).parse(bytes(source, "utf-8"))
    root = tree.root_node

    imports = [_text(c).strip() for c in root.children if c.type == "import_statement"]
    classes: list[ParsedClass] = []
    module_fns: list[ParsedFunction] = []

    def _parse_fn(node: Node) -> ParsedFunction:
        name = ""
        params = []
        for sub in node.children:
            if sub.type == "identifier":
                name = _text(sub)
            elif sub.type == "formal_parameters":
                raw = _text(sub).strip("()")
                if raw:
                    params = [p.strip() for p in raw.split(",") if p.strip()]
        return ParsedFunction(
            name=name, parameters=params,
            decorators=_decorators_before(node, source),
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        )

    def _walk_class(node: Node):
        name = ""
        superclass = ""
        interfaces = []
        for sub in node.children:
            if sub.type == "identifier":
                name = _text(sub)
            elif sub.type == "class_heritage":
                parts = _text(sub).replace("extends ", "").replace("implements ", "").strip()
                items = [p.strip() for p in parts.split(",") if p.strip()]
                if items:
                    superclass = items[0]
                    interfaces = items[1:]

        funcs = []
        body = _child(node, "class_body")
        if body:
            for m in body.children:
                if m.type == "method_definition":
                    mname = ""
                    mparams = []
                    for mc in m.children:
                        if mc.type == "property_identifier":
                            mname = _text(mc)
                        elif mc.type == "formal_parameters":
                            raw = _text(mc).strip("()")
                            if raw:
                                mparams = [p.strip() for p in raw.split(",") if p.strip()]
                    funcs.append(ParsedFunction(
                        name=mname, parameters=mparams,
                        start_line=m.start_point[0] + 1, end_line=m.end_point[0] + 1,
                    ))

        classes.append(ParsedClass(
            name=name or "Unknown", package=package, kind="class",
            decorators=_decorators_before(node, source),
            superclass=superclass, interfaces=interfaces,
            functions=funcs, imports=imports,
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        ))

    for c in root.children:
        if c.type in ("class_declaration", "abstract_class_declaration"):
            _walk_class(c)
        elif c.type == "interface_declaration" and lang_key == "typescript":
            name = _child_text(c, "type_identifier") or "Unknown"
            classes.append(ParsedClass(
                name=name, package=package, kind="interface", imports=imports,
                start_line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
            ))
        elif c.type == "function_declaration":
            module_fns.append(_parse_fn(c))
        elif c.type == "export_statement":
            for sub in c.children:
                if sub.type in ("class_declaration", "abstract_class_declaration"):
                    _walk_class(sub)
                elif sub.type == "function_declaration":
                    module_fns.append(_parse_fn(sub))

    if not classes and module_fns:
        mod = package.split("/")[-1] if package else "module"
        classes.append(ParsedClass(
            name=mod, package=package, kind="module",
            functions=module_fns, imports=imports,
            start_line=1, end_line=source.count("\n") + 1,
        ))
    return classes


def parse_javascript(source: str, package: str = "") -> list[ParsedClass]:
    return _parse_js_ts(source, package, "javascript")


def parse_typescript(source: str, package: str = "") -> list[ParsedClass]:
    return _parse_js_ts(source, package, "typescript")


# ================================================================
# Regex fallback (Ruby, Rust, C#, C/C++, etc.)
# ================================================================

_CLASS_RE = re.compile(
    r"(?:public|private|protected|internal|abstract|static|sealed|partial|export)?\s*"
    r"(class|interface|struct|enum|type|trait|protocol)\s+(\w+)", re.MULTILINE,
)
_FUNC_RE = re.compile(
    r"(?:public|private|protected|internal|static|async|virtual|override|abstract|fn|func|def|function)?\s*"
    r"(?:<\w+>\s+)?(\w+(?:<[^>]+>)?(?:\[\])?)\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE,
)


def parse_regex(source: str, package: str = "") -> list[ParsedClass]:
    lines = source.split("\n")
    classes: list[ParsedClass] = []

    for m in _CLASS_RE.finditer(source):
        kind, name = m.group(1), m.group(2)
        start = source[:m.start()].count("\n") + 1
        end = source[:m.end()].count("\n") + 1

        # Find functions within this class
        next_cls = _CLASS_RE.search(source, m.end())
        end_pos = next_cls.start() if next_cls else len(source)
        class_src = source[m.start():end_pos]
        funcs = []
        for fm in _FUNC_RE.finditer(class_src):
            fname = fm.group(2)
            if fname in ("if", "for", "while", "switch", "catch", "return", "new"):
                continue
            fline = start + class_src[:fm.start()].count("\n")
            funcs.append(ParsedFunction(name=fname, start_line=fline, end_line=fline))

        classes.append(ParsedClass(
            name=name, package=package, kind=kind, functions=funcs,
            start_line=start, end_line=end,
        ))

    if not classes:
        all_funcs = []
        for fm in _FUNC_RE.finditer(source):
            fname = fm.group(2)
            if fname not in ("if", "for", "while", "switch", "catch", "return", "new"):
                fline = source[:fm.start()].count("\n") + 1
                all_funcs.append(ParsedFunction(name=fname, start_line=fline, end_line=fline))
        if all_funcs:
            mod = package.split(".")[-1] if package else "module"
            classes.append(ParsedClass(
                name=mod, package=package, kind="module",
                functions=all_funcs, start_line=1, end_line=len(lines),
            ))
    return classes


# ================================================================
# Registry
# ================================================================

PARSERS = {
    Language.JAVA: parse_java,
    Language.PYTHON: parse_python,
    Language.GO: parse_go,
    Language.JAVASCRIPT: parse_javascript,
    Language.JSX: parse_javascript,
    Language.TYPESCRIPT: parse_typescript,
    Language.TSX: parse_typescript,
    Language.UNKNOWN: parse_regex,
}


def get_parser(lang: Language):
    return PARSERS.get(lang, parse_regex)
