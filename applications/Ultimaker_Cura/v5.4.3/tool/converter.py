import json
import re
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Mapping of Cura units to LamiNode quantity structures
UNIT_MAPPING = {
    "mm": {"name": "length", "unit": "millimeter", "symbol": "mm"},
    "mm/s": {"name": "speed", "unit": "millimeter/second", "symbol": "mm/s"},
    "°C": {"name": "temperature", "unit": "celsius", "symbol": "°C"},
    "%": {"name": "percentage", "unit": "percent", "symbol": "%"},
    "s": {"name": "time", "unit": "second", "symbol": "s"},
    "mm²": {"name": "area", "unit": "square millimeter", "symbol": "mm²"},
    "mm/s²": {"name": "acceleration", "unit": "millimeter/second²", "symbol": "mm/s²"},
    "°": {"name": "angle", "unit": "degree", "symbol": "°"},
    "mm³": {"name": "volume", "unit": "cubic millimeter", "symbol": "mm³"},
    "mm/s³": {"name": "jerk", "unit": "millimeter/second³", "symbol": "mm/s³"},
}

# Mapping of Cura types to quantity names when no unit is present
TYPE_MAPPING = {
    "int": "integer",
    "float": "float",
    "bool": "boolean",
    "str": "string",
    "enum": "choice",
}

DEFAULT_COLOR = "blue"

# Rainbow colors in spectral order, matching LamiColor enum
RAINBOW_COLORS = [
    "red", "crimson", "deepOrange", "orange", "amber", "gold", "yellow", 
    "lime", "lightGreen", "green", "emerald", "teal", "cyan", "lightBlue", 
    "blue", "indigo", "deepPurple", "violet", "purple", "pink"
]

# Precompiled regexes for expression transpilation (performance and clarity)
RESOLVE_SINGLE = re.compile(r"resolveOrValue\('([^']+)'\)")
RESOLVE_DOUBLE = re.compile(r'resolveOrValue\("([^\"]+)"\)')
EXTRUDER_VALUE_SINGLE = re.compile(r"extruderValue\([^,]+,\s*'([^']+)'\)")
EXTRUDER_VALUE_DOUBLE = re.compile(r'extruderValue\([^,]+,\s*"([^\"]+)"\)')
EXTRUDER_VALUES_SINGLE = re.compile(r"extruderValues\('([^']+)'\)")
EXTRUDER_VALUES_DOUBLE = re.compile(r'extruderValues\("([^\"]+)"\)')
LEN_PATTERN = re.compile(r"\blen\(([^)]+)\)")
SUM_PATTERN = re.compile(r"\bsum\(([^)]+)\)")
ANY_PATTERN = re.compile(r"\bany\(([^)]+)\)")
MAX_PATTERN = re.compile(r"\bmax\(([^)]+)\)")
MIN_PATTERN = re.compile(r"\bmin\(([^)]+)\)")
TERNARY_PATTERN = re.compile(r"(.+?)\s+if\s+(.+?)\s+else\s+(.+)")

# Script metadata and runtime flags
VERSION = "0.1"
QUIET: bool = False


def log(msg: str) -> None:
    """Print helper honoring the `QUIET` flag."""
    if not QUIET:
        print(msg)

def clean_html(text: Any) -> Any:
    """Remove HTML tags from `text` when it's a string.

    Non-string values are returned unchanged.
    """
    if not isinstance(text, str):
        return text
    return re.sub(r"<[^>]+>", "", text)

def collect_param_categories(settings_dict: Dict[str, Any], current_category: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Recursively collect a mapping of parameter -> containing category.

    `settings_dict` is a nested dict of Cura settings.
    """
    param_to_category: Dict[str, Optional[str]] = {}
    for key, value in settings_dict.items():
        if value.get("type") == "category":
            new_category = key
            if "children" in value:
                param_to_category.update(collect_param_categories(value["children"], new_category))
        else:
            param_to_category[key] = current_category
            if "children" in value:
                param_to_category.update(collect_param_categories(value["children"], current_category))
    return param_to_category

def get_param_attributes(cura_setting: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dict of quantity/type attributes for a Cura setting.

    The function preserves previous behavior but is annotated for clarity.
    """
    attributes: Dict[str, Any] = {}
    cura_type = cura_setting.get("type")
    unit = cura_setting.get("unit")

    if unit in UNIT_MAPPING:
        attributes["type"] = UNIT_MAPPING[unit]["name"]
    elif cura_type == "bool":
        attributes["type"] = "boolean"
    elif cura_type == "enum" or (cura_type == "str" and "options" in cura_setting):
        attributes["type"] = "choice"
    elif cura_type == "str":
        attributes["type"] = "string"
    elif cura_type == "int":
        attributes["type"] = "integer"
    elif cura_type == "float":
        attributes["type"] = "float"
    else:
        attributes["type"] = "numeric"

    if cura_type == "enum" and "options" in cura_setting:
        options = cura_setting["options"]
        if isinstance(options, dict):
            attributes["options"] = list(options.keys())
        elif isinstance(options, list):
            attributes["options"] = options

    return attributes

def _transpile_expression(expr: Any) -> str:
    """Transpile a Cura/Python expression into a JavaScript-like expression string.

    This is a heuristic transliteration (keeps original behavior).
    """
    if not isinstance(expr, str):
        return str(expr).lower() if isinstance(expr, bool) else str(expr)
    
    # Remove unsupported functions using precompiled patterns
    expr = RESOLVE_SINGLE.sub(r"\1", expr)
    expr = RESOLVE_DOUBLE.sub(r"\1", expr)

    expr = EXTRUDER_VALUE_SINGLE.sub(r"\1", expr)
    expr = EXTRUDER_VALUE_DOUBLE.sub(r"\1", expr)

    # Replace math module
    expr = expr.replace("math.", "Math.")

    # Transform extruderValues -> array for single extruder
    expr = EXTRUDER_VALUES_SINGLE.sub(r"[\1]", expr)
    expr = EXTRUDER_VALUES_DOUBLE.sub(r"[\1]", expr)

    # len(x) -> (x).length
    expr = LEN_PATTERN.sub(r"(\1).length", expr)

    # sum(x) -> (x).reduce((a, b) => a + b, 0)
    expr = SUM_PATTERN.sub(r"(\1).reduce((a, b) => a + b, 0)", expr)

    # any(x) -> (x).some(e => e)
    expr = ANY_PATTERN.sub(r"(\1).some(e => e)", expr)

    # max/min handling (heuristic as before)
    def replace_max(match: re.Match) -> str:
        args = match.group(1)
        if "," in args:
            return f"Math.max({args})"
        return f"Math.max(...{args})"
    expr = MAX_PATTERN.sub(replace_max, expr)

    def replace_min(match: re.Match) -> str:
        args = match.group(1)
        if "," in args:
            return f"Math.min({args})"
        return f"Math.min(...{args})"
    expr = MIN_PATTERN.sub(replace_min, expr)

    # Replace Python boolean operators and constants
    expr = expr.replace(" and ", " && ")
    expr = expr.replace(" or ", " || ")
    expr = expr.replace(" not ", " ! ")
    expr = expr.replace("True", "true")
    expr = expr.replace("False", "false")
    expr = expr.replace("None", "null")

    # Handle ternary: "A if B else C" -> "B ? A : C"
    match = TERNARY_PATTERN.search(expr)
    if match:
        expr = f"{match.group(2)} ? {match.group(1)} : {match.group(3)}"
        
    return expr

def _get_expression(target_name: str, value: Any) -> Dict[str, Any]:
    """Return a schema expression object mapping `target` to transpiled expression."""
    expr = _transpile_expression(value)
    return {"target": target_name, "expression": expr}

def convert_settings(
    settings_dict: Dict[str, Any],
    categories: List[Dict[str, Any]],
    parameters: List[Dict[str, Any]],
    param_to_category: Dict[str, Optional[str]],
    parent_category: Optional[str] = None,
    ancestors: Optional[List[str]] = None,
) -> None:
    """Traverse Cura settings recursively to fill `categories` and `parameters`.

    This function mutates the `categories` and `parameters` lists passed in.
    """
    if ancestors is None:
        ancestors = []

    for key, value in settings_dict.items():
        if value.get("type") == "category":
            category_name = key
            label = value.get("label", key)

            # Category role is "buildJob" by default. If the label contains any
            # of these keywords, mark it as a "machine" category.
            _machine_keywords = [
                "command_line_settings",
                "experimental",
                "machine_settings",
                "meshfix",
                "ppr",
                "blackmagic",
            ]
            role = "machine" if any(k in label.lower() for k in _machine_keywords) else "buildJob"

            categories.append({"name": category_name, "title": label, "role": role})
            if "children" in value:
                convert_settings(value["children"], categories, parameters, param_to_category, category_name, ancestors)
        else:
            param_name = key
            param: Dict[str, Any] = {
                "name": param_name,
                "title": value.get("label", key),
                "description": clean_html(value.get("description", "")),
                "category": parent_category,
            }

            # Strict contract: Every parameter MUST have a 'quantity' object
            param["quantity"] = get_param_attributes(value)

            if ancestors:
                param["ancestors"] = ancestors

            # Map validation and relations
            if "value" in value:
                param["defaultValue"] = _get_expression(param_name, value["value"])
            elif "default_value" in value:
                param["defaultValue"] = _get_expression(param_name, value["default_value"])

            if "minimum_value" in value:
                param["minThreshold"] = _get_expression(param_name, value["minimum_value"])

            if "maximum_value" in value:
                param["maxThreshold"] = _get_expression(param_name, value["maximum_value"])

            if "enabled" in value:
                param["enabledCondition"] = _get_expression(param_name, value["enabled"])

            parameters.append(param)

            if "children" in value:
                new_ancestors = ancestors + [param_name]
                convert_settings(value["children"], categories, parameters, param_to_category, parent_category, new_ancestors)

def main():
    # Setup paths
    script_dir = Path(__file__).resolve().parent
    input_path = script_dir / "assets" / "fdmprinter.def.json"

    # Default output placed in same directory as input file
    default_output_path = input_path.parent / "generated_schema.json"

    parser = argparse.ArgumentParser(description="Convert Cura definitions to LamiNode schema.")
    parser.add_argument("output", nargs="?", help="Optional positional output path (file name or full path)")
    parser.add_argument("-o", "--output", dest="opt_output", help="Output path for generated schema (overrides positional)")
    parser.add_argument("--version", action="store_true", dest="show_version", help="Show script version and exit")
    parser.add_argument("--quiet", action="store_true", dest="quiet", help="Minimize output")
    args = parser.parse_args()

    if args.show_version:
        print(VERSION)
        return

    global QUIET
    QUIET = bool(args.quiet)

    # Preference: -o/--output over positional `output`; fallback to default
    output_path = Path(args.opt_output or args.output or str(default_output_path))
    
    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        return

    log(f"Loading {input_path}...")
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    settings = data.get('settings', {})
    
    categories = []
    parameters = []

    log("Collecting parameter categories...")
    param_to_category = collect_param_categories(settings)

    log("Converting settings...")
    convert_settings(settings, categories, parameters, param_to_category)

    # Sort categories alphabetically by title
    categories.sort(key=lambda c: c['title'])

    # Assign rainbow colors
    for i, category in enumerate(categories):
        category['color'] = RAINBOW_COLORS[i % len(RAINBOW_COLORS)]

    # Construct the final schema object according to the new data structure
    schema = {
        "name": "Ultimaker Cura",
        "manifest": {
            "schemaType": "application",
            "schemaVersion": "0.3",
            "schemaAuthors": ["The Laminode Team"],
            "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
            "targetAppName": "Ultimaker Cura"
        },
        "categories": categories,
        "availableParameters": parameters
    }

    # Ensure output directory exists and write the file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(schema, f, indent=4, ensure_ascii=False)

    log(f"Successfully generated {output_path}")
    log(f" - Categories: {len(categories)}")
    log(f" - Parameters: {len(parameters)}")

if __name__ == "__main__":
    main()
