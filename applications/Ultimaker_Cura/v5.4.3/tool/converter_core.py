import re
from typing import Any, Dict, List, Optional

# Expression transpilation helpers
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


def clean_html(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return re.sub(r"<[^>]+>", "", text)


def _transpile_expression(expr: Any) -> str:
    if not isinstance(expr, str):
        return str(expr).lower() if isinstance(expr, bool) else str(expr)

    expr = RESOLVE_SINGLE.sub(r"\1", expr)
    expr = RESOLVE_DOUBLE.sub(r"\1", expr)
    expr = EXTRUDER_VALUE_SINGLE.sub(r"\1", expr)
    expr = EXTRUDER_VALUE_DOUBLE.sub(r"\1", expr)
    expr = expr.replace("math.", "Math.")
    expr = EXTRUDER_VALUES_SINGLE.sub(r"[\1]", expr)
    expr = EXTRUDER_VALUES_DOUBLE.sub(r"[\1]", expr)
    expr = LEN_PATTERN.sub(r"(\1).length", expr)
    expr = SUM_PATTERN.sub(r"(\1).reduce((a, b) => a + b, 0)", expr)
    expr = ANY_PATTERN.sub(r"(\1).some(e => e)", expr)

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

    expr = expr.replace(" and ", " && ")
    expr = expr.replace(" or ", " || ")
    expr = expr.replace(" not ", " ! ")
    expr = expr.replace("True", "true")
    expr = expr.replace("False", "false")
    expr = expr.replace("None", "null")

    match = TERNARY_PATTERN.search(expr)
    if match:
        expr = f"{match.group(2)} ? {match.group(1)} : {match.group(3)}"
    return expr


def _get_expression(target_name: str, value: Any) -> Dict[str, Any]:
    return {"target": target_name, "expression": _transpile_expression(value)}


def collect_param_categories(settings_dict: Dict[str, Any], current_category: Optional[str] = None) -> Dict[str, Optional[str]]:
    param_to_category: Dict[str, Optional[str]] = {}
    for key, value in settings_dict.items():
        if value.get("type") == "category":
            if "children" in value:
                param_to_category.update(collect_param_categories(value["children"], key))
        else:
            param_to_category[key] = current_category
            if "children" in value:
                param_to_category.update(collect_param_categories(value["children"], current_category))
    return param_to_category


def _normalize_title(title: str) -> List[str]:
    stopwords = {"the", "a", "an", "of", "and", "for", "to", "per"}
    t = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    return [tok for tok in t.split() if tok and tok not in stopwords]


def match_ancestors_by_title(parameters: List[Dict[str, Any]]) -> None:
    name_to_param = {p["name"]: p for p in parameters}
    titles = [(p["name"], p.get("title", "")) for p in parameters]
    norm = {n: set(_normalize_title(t)) for n, t in titles}
    # First: infer ancestors from expressions (defaultValue, enabledCondition, thresholds)
    param_names = set(name_to_param.keys())
    ident_re = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
    for p in parameters:
        expr_sources = []
        for k in ("defaultValue", "enabledCondition", "minThreshold", "maxThreshold"):
            v = p.get(k)
            if isinstance(v, dict):
                expr = v.get("expression")
                if isinstance(expr, str):
                    expr_sources.append(expr)
        for expr in expr_sources:
            for ident in ident_re.findall(expr):
                if ident in param_names and ident != p["name"]:
                    p.setdefault("ancestors", [])
                    if ident not in p["ancestors"]:
                        p["ancestors"].append(ident)
    for child_name, _ in titles:
        child_tokens = norm.get(child_name, set())
        if not child_tokens:
            continue
        child_param = name_to_param[child_name]
        # skip title-based matching if explicit ancestors already inferred
        if child_param.get("ancestors"):
            continue
        best_parent = None
        best_score = 0
        for parent_name, _ in titles:
            if parent_name == child_name:
                continue
            parent_tokens = norm.get(parent_name, set())
            if not parent_tokens:
                continue
            # Parent must be fully present in child's title and child must add
            # at least one additional token. This avoids matching on generic
            # measurement words like "height" or "width" alone.
            if parent_tokens.issubset(child_tokens) and len(child_tokens) > len(parent_tokens):
                extra = child_tokens - parent_tokens
                # don't match when parent is only a single generic measurement token
                GENERIC_TOKENS = {"height", "width", "distance", "length", "size", "speed", "temp", "temperature", "time", "angle", "area", "offset", "spacing"}
                # Require parent to have at least two meaningful tokens OR be a non-generic single token
                if len(parent_tokens) == 1:
                    if parent_tokens & GENERIC_TOKENS:
                        continue
                    # if parent is single non-generic (e.g., 'nozzle'), still require child's extra tokens to be descriptive
                    if len(extra) < 1:
                        continue
                # allow only a small number of extra tokens (adjectives or qualifiers)
                if 1 <= len(extra) <= 3:
                    # Ensure extra tokens are not purely generic (e.g., 'height')
                    if all(tok in GENERIC_TOKENS for tok in extra):
                        continue
                    score = len(parent_tokens)
                    if score > best_score:
                        best_score = score
                        best_parent = parent_name
        if best_parent:
            child_param.setdefault("ancestors", []).append(best_parent)


def convert_settings(
    settings_dict: Dict[str, Any],
    categories: List[Dict[str, Any]],
    parameters: List[Dict[str, Any]],
    param_to_category: Dict[str, Optional[str]],
    quantities: Dict[str, Any],
    unit_mapping: Dict[str, Any],
    type_mapping: Dict[str, Any],
    parent_category: Optional[str] = None,
    ancestors: Optional[List[str]] = None,
):
    if ancestors is None:
        ancestors = []

    for key, value in settings_dict.items():
        if value.get("type") == "category":
            category_name = key
            label = value.get("label", key)
            _machine_keywords = [
                "command_line_settings",
                "experimental",
                "machine_settings",
                "meshfix",
                "ppr",
                "blackmagic",
            ]
            role = "machine" if any(k in label.lower() for k in _machine_keywords) else "buildJob"
            categories.append({"name": category_name, "title": label, "role": role, "parent": parent_category})
            if "children" in value:
                convert_settings(value["children"], categories, parameters, param_to_category, quantities, unit_mapping, type_mapping, category_name, ancestors)
        else:
            param_name = key
            param: Dict[str, Any] = {"name": param_name, "title": value.get("label", key), "description": clean_html(value.get("description", "")), "category": parent_category}
            name_low = param_name.lower()
            cura_type = value.get("type", "float")
            qty_id = "count"
            options = None
            # direct handling for Cura boolean declaration
            if isinstance(cura_type, str) and cura_type.lower() in ("bool", "boolean"):
                qty_id = "boolean"
            
            if cura_type in type_mapping:
                mapped = type_mapping.get(cura_type)
                if mapped == "boolean":
                    qty_id = "boolean"
                elif mapped == "choice":
                    qty_id = "choice"
                elif mapped == "string":
                    qty_id = "string"
                elif mapped == "integer":
                    qty_id = "count"
                else:
                    qty_id = mapped or "count"
            if cura_type == "enum" or (cura_type == "str" and "options" in value):
                qty_id = "choice"
                raw_options = value.get("options", {})
                if isinstance(raw_options, dict):
                    options = {str(k): (v if isinstance(v, str) else str(v)) for k, v in raw_options.items()}
                elif isinstance(raw_options, list):
                    options = {str(e): str(e) for e in raw_options}
            if any(k in name_low for k in ["height", "width", "distance", "length", "area", "size", "offset", "clearance", "spacing"]):
                qty_id = "length"
            elif "speed" in name_low:
                qty_id = "speed"
            elif "accel" in name_low:
                qty_id = "acceleration"
            elif "jerk" in name_low:
                qty_id = "jerk"
            elif "temp" in name_low:
                qty_id = "temperature"
            elif "percent" in name_low:
                qty_id = "percentage"
            elif "time" in name_low:
                qty_id = "time"
            elif "angle" in name_low:
                qty_id = "angle"

            def _is_boolean_like(vdict: Dict[str, Any]) -> bool:
                # Look for any common default-value keys
                dv = None
                for k in ("default_value", "value", "default", "defaultValue", "defaultValue"):
                    if k in vdict:
                        dv = vdict.get(k)
                        break
                if isinstance(dv, bool):
                    return True
                if isinstance(dv, str) and dv.strip().lower() in ("true", "false", "on", "off"):
                    return True
                # Check explicit cura 'type' was already handled upstream; inspect options
                opts = vdict.get("options") or vdict.get("enum")
                if isinstance(opts, dict):
                    keys = {str(k).strip().lower() for k in opts.keys()}
                    vals = {str(v).strip().lower() for v in opts.values()}
                    if {"true", "false"}.issubset(keys) or {"true", "false"}.issubset(vals) or {"on","off"}.issubset(keys) or {"on","off"}.issubset(vals):
                        return True
                if isinstance(opts, list):
                    low = {str(e).strip().lower() for e in opts}
                    if {"true", "false"}.issubset(low) or {"on", "off"}.issubset(low):
                        return True
                return False

            if _is_boolean_like(value):
                qty_id = "boolean"

            param["quantityIds"] = [qty_id]
            if options:
                param["options"] = options
            if ancestors:
                param["ancestors"] = ancestors
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
                new_ancestors = list(ancestors) + [param_name]
                convert_settings(value["children"], categories, parameters, param_to_category, quantities, unit_mapping, type_mapping, parent_category, new_ancestors)
