
import json
import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from copy import deepcopy

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


def deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """Recursively update mapping `dst` with values from `src`.

    - If both `dst[k]` and `src[k]` are dicts, recurse.
    - Otherwise `src[k]` replaces `dst[k]` (deep-copied when appropriate).
    """
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            deep_update(dst[k], v)
        else:
            # Use deepcopy to avoid referencing input structures
            dst[k] = deepcopy(v)

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
    # Prepare raw copy before applying any overrides
    schema_raw = deepcopy(schema)

    # Load overrides if available and apply to a copy of schema
    overrides_path = assets_dir / "overrides.json"
    schema_assembly = deepcopy(schema)
    if overrides_path.exists():
        try:
            with overrides_path.open("r", encoding="utf-8") as f:
                overrides = json.load(f)
            # Apply overrides to parameters by matching `name` field
            name_to_param = {p['name']: p for p in schema_assembly.get('availableParameters', [])}
            for ov in overrides:
                target_name = ov.get('name')
                if not target_name:
                    continue
                param = name_to_param.get(target_name)
                if not param:
                    log(f"Warning: override for unknown parameter '{target_name}'")
                    continue
                # Merge override keys (recursive): support partial nested updates
                for k, v in ov.items():
                    if k == 'name':
                        continue
                    if isinstance(v, dict) and isinstance(param.get(k), dict):
                        deep_update(param[k], v)
                    else:
                        param[k] = deepcopy(v)
            log(f"Applied {len(overrides)} overrides from {overrides_path}")
        except Exception as e:
            log(f"Failed to load overrides from {overrides_path}: {e}")
    else:
        log(f"No overrides file found at {overrides_path}")

    # Ensure the dedicated `output` folder contains only raw and assembly files
    output_folder = script_dir / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    raw_output_path = output_folder / "schema_raw.json"
    assembly_output_path = output_folder / "schema_assembly.json"
    with raw_output_path.open("w", encoding="utf-8") as f:
        json.dump(schema_raw, f, indent=4, ensure_ascii=False)
    with assembly_output_path.open("w", encoding="utf-8") as f:
        json.dump(schema_assembly, f, indent=4, ensure_ascii=False)
    log(f"Wrote schema_raw to {raw_output_path}")
    log(f"Wrote schema_assembly to {assembly_output_path}")

    # Per configuration: do not generate any `schema.json` files.
    # `tool/output/` is the only output folder and already contains
    # `schema_raw.json` and `schema_assembly.json`.
    if user_output:
        log(f"User requested output path '{user_output}', but schema.json generation is disabled.")
    else:
        log("Skipping generation of schema.json files (only raw/assembly outputs are written).")

    log(f" - Categories: {len(categories)}")
    log(f" - Parameters: {len(parameters)}")

if __name__ == "__main__":
    main()
