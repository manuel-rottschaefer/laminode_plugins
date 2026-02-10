import json
import os
import re
import argparse
from datetime import datetime

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
    "enum": "option",
}

DEFAULT_COLOR = "blue"

# Rainbow colors in spectral order, matching LamiColor enum
RAINBOW_COLORS = [
    "red", "crimson", "deepOrange", "orange", "amber", "gold", "yellow", 
    "lime", "lightGreen", "green", "emerald", "teal", "cyan", "lightBlue", 
    "blue", "indigo", "deepPurple", "violet", "purple", "pink"
]

def clean_html(text):
    """Removes HTML tags from a string."""
    if not isinstance(text, str):
        return text
    # Remove HTML tags like <html>, <br>, <i>, etc.
    return re.sub(r'<[^>]+>', '', text)

def collect_param_categories(settings_dict, current_category=None):
    """Recursively collects parameter names and their categories."""
    param_to_category = {}
    for key, value in settings_dict.items():
        if value.get('type') == 'category':
            new_category = key
            if 'children' in value:
                param_to_category.update(collect_param_categories(value['children'], new_category))
        else:
            param_to_category[key] = current_category
            if 'children' in value:
                param_to_category.update(collect_param_categories(value['children'], current_category))
    return param_to_category

def get_param_attributes(cura_setting):
    """Determines the LamiNode parameter type to be merged into the parameter."""
    attributes = {}
    
    # 1. Determine Type (semantic quantity name or generic type)
    cura_type = cura_setting.get('type')
    unit = cura_setting.get('unit')
    
    if unit in UNIT_MAPPING:
        attributes['type'] = UNIT_MAPPING[unit]['name']
    elif cura_type == 'bool':
        attributes['type'] = 'boolean'
    elif cura_type == 'enum' or cura_type == 'str':
        attributes['type'] = 'choice'
    elif cura_type == 'int':
        attributes['type'] = 'integer'
    elif cura_type == 'float':
        attributes['type'] = 'float'
    else:
        attributes['type'] = 'numeric' # Default fallback

    # 2. Extract Options for enums
    if cura_type == 'enum' and 'options' in cura_setting:
         options = cura_setting['options']
         if isinstance(options, dict):
             attributes['options'] = list(options.keys())
         elif isinstance(options, list):
             attributes['options'] = options

    return attributes

def _transpile_expression(expr):
    """Transpiles Python expression to JavaScript."""
    if not isinstance(expr, str):
        return str(expr).lower() if isinstance(expr, bool) else str(expr)
    
    # Remove unsupported functions
    # resolveOrValue('setting') -> setting
    expr = re.sub(r"resolveOrValue\('([^']+)'\)", r"\1", expr)
    expr = re.sub(r'resolveOrValue\("([^"]+)"\)', r"\1", expr)
    
    # extruderValue(nr, 'setting') -> setting
    expr = re.sub(r"extruderValue\([^,]+,\s*'([^']+)'\)", r"\1", expr)
    expr = re.sub(r'extruderValue\([^,]+,\s*"([^"]+)"\)', r"\1", expr)
    
    # Replace math module
    expr = expr.replace("math.", "Math.")

    # 1. Transform function calls
    # extruderValues('setting') -> [setting] (Simulate list for single extruder)
    expr = re.sub(r"extruderValues\('([^']+)'\)", r"[\1]", expr)
    expr = re.sub(r'extruderValues\("([^"]+)"\)', r"[\1]", expr)
    
    # len(x) -> (x).length
    # Note: Regex tries to match balanced parens simply
    expr = re.sub(r"\blen\(([^)]+)\)", r"(\1).length", expr)

    # sum(x) -> (x).reduce((a, b) => a + b, 0)
    # This assumes x is an array (like from extruderValues or manual list)
    expr = re.sub(r"\bsum\(([^)]+)\)", r"(\1).reduce((a, b) => a + b, 0)", expr)
    
    # an(x) -> (x).some(e => e)
    expr = re.sub(r"\bany\(([^)]+)\)", r"(\1).some(e => e)", expr)

    # max(iterable) -> Math.max(...iterable)
    # max(a, b) -> Math.max(a, b) 
    # Use simple heuristic: if comma in args, assume distinct args, else spread
    def replace_max(match):
        args = match.group(1)
        if "," in args:
            return f"Math.max({args})"
        return f"Math.max(...{args})"
    expr = re.sub(r"\bmax\(([^)]+)\)", replace_max, expr)

    # min(iterable) -> Math.min(...iterable)
    # min(a, b) -> Math.min(a, b)
    def replace_min(match):
        args = match.group(1)
        if "," in args:
            return f"Math.min({args})"
        return f"Math.min(...{args})"
    expr = re.sub(r"\bmin\(([^)]+)\)", replace_min, expr)

    # Replace Python operators
    # Note: simple replacement, might need more robust parsing for complex cases
    expr = expr.replace(" and ", " && ")
    expr = expr.replace(" or ", " || ")
    expr = expr.replace(" not ", " ! ")
    
    # Replace Python constants
    expr = expr.replace("True", "true")
    expr = expr.replace("False", "false")
    expr = expr.replace("None", "null")

    # Handle ternary: "A if B else C" -> "B ? A : C"
    # This is a basic regex and won't handle nested ternaries correctly without a parser
    ternary_pattern = re.compile(r"(.+?)\s+if\s+(.+?)\s+else\s+(.+)")
    match = ternary_pattern.search(expr)
    if match:
        expr = f"{match.group(2)} ? {match.group(1)} : {match.group(3)}"
        
    return expr

def _get_expression(target_name, value):
    """Formats an expression object for the schema."""
    expr = _transpile_expression(value)
    
    return {
        "target": target_name,
        "expression": expr
    }

def convert_settings(settings_dict, categories, parameters, param_to_category, parent_category=None, ancestors=None):
    """Recursively traverses Cura settings to extract categories and parameters."""
    if ancestors is None:
        ancestors = []
        
    for key, value in settings_dict.items():
        if value.get('type') == 'category':
            category_name = key
            categories.append({
                "name": category_name,
                "title": value.get('label', key),
            })
            if 'children' in value:
                convert_settings(value['children'], categories, parameters, param_to_category, category_name, ancestors)
        else:
            param_name = key
            param = {
                "name": param_name,
                "title": value.get('label', key),
                "description": clean_html(value.get('description', "")),
                "category": parent_category,
            }
            # Merge quantity attributes directly into param
            qty_attrs = get_param_attributes(value)
            param.update(qty_attrs)
            # Duplicate quantity attributes into a 'quantity' object for Schema Editor compatibility
            param['quantity'] = qty_attrs

            # Detect conditional dependencies to add as ancestors
            dependencies = set()
            for attr in ['value', 'enabled', 'minimum_value', 'maximum_value']:
                if attr in value and isinstance(value[attr], str):
                    # Find words that are known parameter names
                    found = re.findall(r'\b[a-z_][a-z0-9_]*\b', value[attr])
                    for f in found:
                        if f in param_to_category and f != param_name:
                            # Only add as ancestor if it's in the same category
                            if param_to_category[f] == parent_category:
                                dependencies.add(f)
            
            # Combine structural ancestors with logical dependencies
            current_ancestors = list(ancestors)
            for dep in sorted(list(dependencies)):
                if dep not in current_ancestors:
                    current_ancestors.append(dep)

            if current_ancestors:
                param['ancestors'] = current_ancestors

            # Map validation and relations
            if 'value' in value:
                param["defaultValue"] = _get_expression(param_name, value['value'])
            elif 'default_value' in value:
                param["defaultValue"] = _get_expression(param_name, value['default_value'])

            if 'minimum_value' in value:
                param["minThreshold"] = _get_expression(param_name, value['minimum_value'])
            
            if 'maximum_value' in value:
                param["maxThreshold"] = _get_expression(param_name, value['maximum_value'])
            
            if 'enabled' in value:
                param["enabledCondition"] = _get_expression(param_name, value['enabled'])
            
            parameters.append(param)
            
            # Recurse into children if they exist (Cura supports nested parameters)
            if 'children' in value:
                # Pass current hierarchy down
                new_ancestors = ancestors + [param_name]
                convert_settings(value['children'], categories, parameters, param_to_category, parent_category, new_ancestors)

def main():
    # Setup paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_output_path = os.path.join(script_dir, '..', 'schemas', 'v0.1a', 'schema.json')
    
    parser = argparse.ArgumentParser(description="Convert Cura definitions to LamiNode schema.")
    parser.add_argument("output", nargs="?", help="Output path for schema.json", default=default_output_path)
    args = parser.parse_args()

    output_path = args.output
    input_path = os.path.join(script_dir, 'assets', 'fdmprinter.def.json')
    
    if not os.path.exists(input_path):
        print(f"Error: Input file not found at {input_path}")
        return

    print(f"Loading {input_path}...")
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    settings = data.get('settings', {})
    
    categories = []
    parameters = []

    print("Collecting parameter categories...")
    param_to_category = collect_param_categories(settings)

    print("Converting settings...")
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
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(schema, f, indent=4, ensure_ascii=False)

    print(f"Successfully generated {output_path}")
    print(f" - Categories: {len(categories)}")
    print(f" - Parameters: {len(parameters)}")

if __name__ == "__main__":
    main()
