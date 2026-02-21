
import json
import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# script_dir must be available early for fallback imports
script_dir = Path(__file__).resolve().parent

# Try a package-relative import first; if that fails (script run), load by path
try:
    from .converter_core import (
        collect_param_categories,
        convert_settings,
        match_ancestors_by_title,
        _normalize_title,
    )
except Exception:
    spec = importlib.util.spec_from_file_location("converter_core", str(script_dir / "converter_core.py"))
    converter_core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter_core)
    collect_param_categories = converter_core.collect_param_categories
    convert_settings = converter_core.convert_settings
    match_ancestors_by_title = converter_core.match_ancestors_by_title
    _normalize_title = converter_core._normalize_title

# Assets
assets_dir = script_dir / "assets"
mappings_path = assets_dir / "unit_type_mappings.json"
colors_path = assets_dir / "colors.json"
quantities_path = assets_dir / "quantities.json"
template_path = assets_dir / "schema_template.json"

def _load_assets() -> Dict[str, Any]:
    with mappings_path.open("r", encoding="utf-8") as f:
        mappings = json.load(f)
    with colors_path.open("r", encoding="utf-8") as f:
        colors = json.load(f)
    with quantities_path.open("r", encoding="utf-8") as f:
        quantities = json.load(f)
    with template_path.open("r", encoding="utf-8") as f:
        template = json.load(f)
    return {"mappings": mappings, "colors": colors, "quantities": quantities, "template": template}

VERSION = "0.1"
QUIET = False

def log(msg: str) -> None:
    if not QUIET:
        print(msg)

def match_ancestors_by_title(parameters: List[Dict[str, Any]]) -> None:
    """Post-process parameters to infer parent-child relations from titles.

    If a parameter's title contains (or mostly contains) another parameter's title
    after normalization, treat the latter as an ancestor.
    """
    name_to_param = {p["name"]: p for p in parameters}
    titles = [(p["name"], p.get("title", "")) for p in parameters]

    # Precompute normalized token sets
    norm = {n: set(_normalize_title(t)) for n, t in titles}

    for child_name, child_title in titles:
        child_tokens = norm.get(child_name, set())
        if not child_tokens:
            continue
        # Skip if explicit ancestors already present
        child_param = name_to_param[child_name]
        if child_param.get("ancestors"):
            continue

        # Search for best parent candidate
        best_parent = None
        best_score = 0
        for parent_name, parent_title in titles:
            if parent_name == child_name:
                continue
            parent_tokens = norm.get(parent_name, set())
            if not parent_tokens:
                continue
            # If parent tokens are subset of child tokens and child has only a few extra tokens
            if parent_tokens.issubset(child_tokens):
                extra = child_tokens - parent_tokens
                # allow a small difference (adjectives like 'thin', 'outer', etc.)
                if len(extra) <= 2:
                    score = len(parent_tokens)
                    if score > best_score:
                        best_score = score
                        best_parent = parent_name

        if best_parent:
            # assign as single ancestor
            child_param.setdefault("ancestors", []).append(best_parent)

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Convert Ultimaker Cura settings to schema format")
    parser.add_argument("-o", "--output", dest="opt_output", help="Output file path")
    parser.add_argument("output", nargs="?", default=None, help="Output file path (positional)")
    args = parser.parse_args()

    # Setup paths
    input_path = script_dir / "assets" / "fdmprinter.def.json"
    default_output_path = script_dir / "output" / "schema.json"

    # Load required assets (no fallbacks; assets must be present)
    assets = _load_assets()
    unit_mapping = assets["mappings"].get("unit_mapping", {})
    type_mapping = assets["mappings"].get("type_mapping", {})
    colors = assets["colors"].get("rainbow", [])
    default_color = assets["colors"].get("default", "blue")
    quantities_template = assets["quantities"]
    schema_template = assets["template"]

    # Preference: -o/--output over positional `output`; fallback to default behaviour
    user_output = args.opt_output or args.output

    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        return

    log(f"Loading {input_path}...")
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    settings = data.get('settings', {})
    categories: List[Dict[str, Any]] = []
    parameters: List[Dict[str, Any]] = []
    quantities: Dict[str, Any] = dict(quantities_template)

    log("Collecting parameter categories...")
    param_to_category = collect_param_categories(settings)

    log("Converting settings...")
    convert_settings(settings, categories, parameters, param_to_category, quantities, unit_mapping, type_mapping)
    match_ancestors_by_title(parameters)

    # Sort categories alphabetically by title
    categories.sort(key=lambda c: c['title'])

    # Assign rainbow colors
    for i, category in enumerate(categories):
        category['color'] = colors[i % len(colors)] if colors else default_color

    # Construct the final schema object
    schema = dict(schema_template)
    schema['manifest']['lastUpdated'] = datetime.now().strftime("%Y-%m-%d")
    schema['quantities'] = quantities
    schema['categories'] = categories
    schema['availableParameters'] = parameters

    # If user specified an output path, write only there (file or directory).
    if user_output:
        outp = Path(user_output)
        if outp.suffix:  # treat as a file when extension present
            outp.parent.mkdir(parents=True, exist_ok=True)
            with outp.open("w", encoding="utf-8") as f:
                json.dump(schema, f, indent=4, ensure_ascii=False)
            log(f"Wrote schema to {outp}")
        else:
            outp.mkdir(parents=True, exist_ok=True)
            final_output_path = outp / "schema.json"
            with final_output_path.open("w", encoding="utf-8") as f:
                json.dump(schema, f, indent=4, ensure_ascii=False)
            log(f"Wrote schema to {final_output_path}")
    else:
        # Default: write compatibility versions into ../schemas/<version>/schema.json
        for version in ["v0.1", "v5.4.3"]:
            output_dir = script_dir.parent / "schemas" / version
            output_dir.mkdir(parents=True, exist_ok=True)
            final_output_path = output_dir / "schema.json"
            with final_output_path.open("w", encoding="utf-8") as f:
                json.dump(schema, f, indent=4, ensure_ascii=False)
            log(f"Wrote schema to {final_output_path}")

    log(f" - Categories: {len(categories)}")
    log(f" - Parameters: {len(parameters)}")

if __name__ == "__main__":
    main()
