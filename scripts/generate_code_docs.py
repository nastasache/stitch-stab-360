#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Automated Code Documentation Generator for StitchStab 360.

Extracts docstrings, class definitions, function signatures, and type annotations
from repository Python source files using standard library AST parsing, generating
a comprehensive Markdown reference in docs/code_reference.md.
"""

import ast
import argparse
import glob
import re
from pathlib import Path


def extract_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Extract formatted function signature including arguments, defaults, and return type.

    Args:
        node: The AST FunctionDef or AsyncFunctionDef node.

    Returns:
        Formatted function signature string without trailing body block.
    """
    node_cls = ast.AsyncFunctionDef if isinstance(node, ast.AsyncFunctionDef) else ast.FunctionDef
    dummy = node_cls(
        name=node.name,
        args=node.args,
        body=[ast.Pass()],
        decorator_list=[],
        returns=node.returns,
    )
    ast.fix_missing_locations(dummy)
    raw = ast.unparse(dummy).strip()
    if raw.endswith(":\n    pass"):
        return raw[:-10].strip()
    if raw.endswith(": pass"):
        return raw[:-6].strip()
    return raw


def format_docstring(doc: str, indent: str = "") -> str:
    """Format a Google-style docstring into clean GitHub markdown.

    Args:
        doc: Raw docstring text.
        indent: Optional indentation prefix.

    Returns:
        Formatted markdown text.
    """
    if not doc:
        return ""
    lines = doc.strip().split("\n")
    formatted = []
    in_section = False

    for line in lines:
        stripped = line.strip()
        # Highlight section headers (Args:, Returns:, Raises:, Note:)
        if re.match(r"^(Args|Returns|Raises|Note|Yields|Attributes):", stripped, re.IGNORECASE):
            formatted.append(f"\n{indent}**{stripped}**\n")
            in_section = True
        elif in_section and stripped.startswith(("-", "*")):
            formatted.append(f"{indent}  {stripped}")
        elif in_section and ":" in stripped and not stripped.startswith("http"):
            parts = stripped.split(":", 1)
            formatted.append(f"{indent}- `{parts[0].strip()}`: {parts[1].strip()}")
        else:
            formatted.append(f"{indent}{stripped}")

    return "\n".join(formatted).strip()


def parse_module(filepath: str) -> dict:
    """Parse a Python source file using AST and extract its architectural metadata.

    Args:
        filepath: Relative or absolute path to Python script.

    Returns:
        Dictionary containing module docstring, classes, and top-level functions.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        return {"file": filepath, "doc": f"Error reading file: {e}", "classes": [], "functions": []}

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return {"file": filepath, "doc": f"SyntaxError parsing module: {e}", "classes": [], "functions": []}

    module_doc = ast.get_docstring(tree) or ""
    classes = []
    functions = []

    for item in tree.body:
        if isinstance(item, ast.ClassDef):
            class_doc = ast.get_docstring(item) or ""
            methods = []
            bases = [ast.unparse(b) for b in item.bases]
            for elem in item.body:
                if isinstance(elem, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Include public methods and __init__
                    if not elem.name.startswith("_") or elem.name == "__init__":
                        sig = extract_function_signature(elem)
                        m_doc = ast.get_docstring(elem) or ""
                        methods.append({
                            "name": elem.name,
                            "signature": sig,
                            "doc": m_doc,
                            "line": elem.lineno
                        })
            classes.append({
                "name": item.name,
                "bases": bases,
                "doc": class_doc,
                "methods": methods,
                "line": item.lineno
            })
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Include public functions and documented private functions
            f_doc = ast.get_docstring(item) or ""
            if not item.name.startswith("_") or f_doc:
                sig = extract_function_signature(item)
                functions.append({
                    "name": item.name,
                    "signature": sig,
                    "doc": f_doc,
                    "line": item.lineno,
                    "is_async": isinstance(item, ast.AsyncFunctionDef)
                })

    return {
        "file": filepath.replace("\\", "/"),
        "doc": module_doc,
        "classes": classes,
        "functions": functions
    }


def generate_markdown(categories: list[tuple[str, str, list[dict]]]) -> str:
    """Generate Markdown documentation content from categorized module data.

    Args:
        categories: List of tuples (category_title, category_desc, list of parsed module dicts).

    Returns:
        Complete Markdown document content string.
    """
    out = []
    out.append("# 📖 Code Reference & API Documentation\n")
    out.append("> *Auto-generated from source code docstrings via `scripts/generate_code_docs.py`.*\n")
    out.append("This document provides an automated, comprehensive code-level reference for all modules, classes, and functions in StitchStab 360.\n")

    # Table of Contents
    out.append("## 📑 Table of Contents\n")
    for title, _, modules in categories:
        anchor = title.lower().replace(" ", "-").replace("&", "").replace(",", "")
        out.append(f"- [**{title}**](#{anchor})")
        for mod in modules:
            fname = Path(mod["file"]).name
            fanchor = fname.replace(".", "").lower()
            out.append(f"  - [`{fname}`](#{fanchor})")
    out.append("\n---\n")

    # Categories
    for title, desc, modules in categories:
        out.append(f"## {title}\n")
        if desc:
            out.append(f"{desc}\n")

        for mod in modules:
            fname = Path(mod["file"]).name
            fanchor = fname.replace(".", "").lower()
            out.append(f"### <a id=\"{fanchor}\"></a>`{mod['file']}`\n")

            if mod["doc"]:
                out.append(f"{mod['doc'].strip()}\n")

            # Classes
            if mod["classes"]:
                out.append("#### Classes\n")
                for cls in mod["classes"]:
                    bases_str = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
                    out.append(f"##### `class {cls['name']}{bases_str}` (Line {cls['line']})\n")
                    if cls["doc"]:
                        out.append(f"{format_docstring(cls['doc'])}\n")
                    if cls["methods"]:
                        out.append("**Methods:**\n")
                        for m in cls["methods"]:
                            out.append(f"```python\n{m['signature']}\n```")
                            if m["doc"]:
                                out.append(f"{format_docstring(m['doc'], indent='> ')}\n")

            # Functions
            if mod["functions"]:
                out.append("#### Functions\n")
                for fn in mod["functions"]:
                    async_tag = "*(async)* " if fn.get("is_async") else ""
                    out.append(f"##### {async_tag}`{fn['name']}` (Line {fn['line']})\n")
                    out.append(f"```python\n{fn['signature']}\n```")
                    if fn["doc"]:
                        out.append(f"{format_docstring(fn['doc'])}\n")

            out.append("\n---\n")

    return "\n".join(out)


def main():
    """Main CLI entry point for code documentation generation."""
    parser = argparse.ArgumentParser(description="Generate Markdown documentation from code comments and docstrings.")
    parser.add_argument("--output", default="docs/code_reference.md", help="Destination markdown path.")
    parser.add_argument("--check", action="store_true", help="Check if docs are up-to-date without writing.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    # Category definitions
    category_specs = [
        (
            "1. FastAPI Server & Backend Application",
            "REST API routing, WebSocket progress broadcasting, process management, and media streaming endpoints.",
            ["server.py"]
        ),
        (
            "2. Central Configuration",
            "Central application settings, storage directories, baseline constants, and preset defaults.",
            ["config/settings.py"]
        ),
        (
            "3. Pipeline Orchestration",
            "End-to-end multi-stage pipeline coordinator and CLI execution interface.",
            ["scripts/pipeline.py"]
        ),
        (
            "4. Stabilization Engines",
            "6-axis IMU fusion, Kopf feature tracking, Kabsch trajectory alignment, VidSTAB optical smoothing, Cinematic path optimization, and Travel Direction lock.",
            [
                "scripts/stabilize_imu.py",
                "scripts/stabilize_kopf.py",
                "scripts/stabilize_kabsch.py",
                "scripts/stabilize_vidstab.py",
                "scripts/stabilize_cinematic.py",
                "scripts/stabilize_traveldir.py",
                "scripts/stabilize_horizon.py",
            ]
        ),
        (
            "5. Calibration, Telemetry & Geospatial Tools",
            "Optical circle auto-calibration, gyro warmup detection, binary telemetry extraction, Street View GPX sync, and trajectory plotting.",
            [
                "scripts/calibrate.py",
                "scripts/auto_calibrate.py",
                "scripts/detect_warmup.py",
                "scripts/extract_telemetry.py",
                "scripts/convert_telemetry.py",
                "scripts/streetview_gpx.py",
                "scripts/visualize_corrections.py",
                "scripts/visual_odometry.py",
            ]
        ),
        (
            "6. Remapping, Nadir & Video Operations",
            "Dual-fisheye equirectangular warping, alpha mask generation, polar nadir overlay, video cropping, and spatial media injection.",
            [
                "scripts/remap_stitch.py",
                "scripts/patch_nadir.py",
                "scripts/generate_alpha_mask.py",
                "scripts/crop_camera_video.py",
                "scripts/inject_metadata.py",
            ]
        ),
        (
            "7. Utilities & Low-Level Helpers",
            "MP4 ISO-BMFF binary atom slicer, telemetry math, process orchestration, and geometric projection utilities.",
            [
                "utils/mp4_utils.py",
                "utils/imu_fusion.py",
                "utils/calibrate_dual_fisheye.py",
                "utils/global_calibrate.py",
                "utils/inspect_mp4.py",
                "utils/process.py",
                "utils/stabilize_telemetry.py",
                "utils/stitch_opencv.py",
            ]
        ),
        (
            "8. Developer & Maintenance Tools",
            "Git RAG AI memory engine, automated docstring extraction, and developer maintenance utilities.",
            [
                "scripts/git_rag.py",
                "scripts/generate_code_docs.py",
            ]
        ),
    ]

    categories = []
    for cat_title, cat_desc, rel_paths in category_specs:
        parsed_mods = []
        for rel in rel_paths:
            full_path = repo_root / rel
            if full_path.exists():
                parsed_mods.append(parse_module(str(full_path.relative_to(repo_root))))
        if parsed_mods:
            categories.append((cat_title, cat_desc, parsed_mods))

    markdown_doc = generate_markdown(categories)
    out_path = repo_root / args.output

    if args.check:
        if not out_path.exists():
            if not args.quiet:
                print(f"[Error] {out_path} does not exist. Run python -B scripts/generate_code_docs.py")
            sys.exit(1)
        with open(out_path, "r", encoding="utf-8") as f:
            existing = f.read()
        if existing.strip() != markdown_doc.strip():
            if not args.quiet:
                print(f"[Error] {out_path} is out of date. Run python -B scripts/generate_code_docs.py")
            sys.exit(1)
        if not args.quiet:
            print(f"[OK] {out_path} is strictly up to date.")
        sys.exit(0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(markdown_doc)

    if not args.quiet:
        print(f"[Success] Generated code reference documentation: {out_path} ({len(markdown_doc)} bytes)")


if __name__ == "__main__":
    main()
