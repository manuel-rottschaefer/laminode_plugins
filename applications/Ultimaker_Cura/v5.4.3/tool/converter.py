import json
import os
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

def get_quantity(cura_setting):
    """Determines the LamiNode quantity based on Cura unit or type."""
    unit = cura_setting.get('unit')
    if unit in UNIT_MAPPING:
        return UNIT_MAPPING[unit]
    
    # Fallback to type-based name if no unit is found
    cura_type = cura_setting.get('type')
    mapped_name = TYPE_MAPPING.get(cura_type, "generic")
    
    return {
        "name": mapped_name,
        "unit": "none",
        "symbol": ""
    }

def convert_settings(settings_dict, categories, parameters, parent_category=None):
    """Recursively traverses Cura settings to extract categories and parameters."""
    for key, value in settings_dict.items():
        if value.get('type') == 'category':
            category_name = key
            categories.append({
                "name": category_name,
                "title": value.get('label', key),
                "color": "blue"
            })
            if 'children' in value:
                convert_settings(value['children'], categories, parameters, category_name)
        else:
            param_name = key
            param = {
                "name": param_name,
                "title": value.get('label', key),
                "description": value.get('description', ""),
                "category": parent_category,
                "quantity": get_quantity(value)
            }
            
            parameters.append(param)
            
            # Recurse into children if they exist (Cura supports nested parameters)
            if 'children' in value:
                convert_settings(value['children'], categories, parameters, parent_category)

def main():
    # Setup paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, 'assets', 'fdmprinter.def.json')
    # Target version v0.2
    output_path = os.path.join(script_dir, '..', 'schemas', 'v0.2', 'schema.json')
    
    if not os.path.exists(input_path):
        print(f"Error: Input file not found at {input_path}")
        return

    print(f"Loading {input_path}...")
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    settings = data.get('settings', {})
    
    categories = []
    parameters = []

    print("Converting settings...")
    convert_settings(settings, categories, parameters)

    # Construct the final schema object according to the new data structure
    schema = {
        "manifest": {
            "schemaType": "application",
            "schemaVersion": "0.2",
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
