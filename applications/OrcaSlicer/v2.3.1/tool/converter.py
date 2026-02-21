import re
import json
import os

def parse_print_config(cpp_file):
    with open(cpp_file, 'r') as f:
        content = f.read()

    # Regexes
    # def = this->add("OPT_NAME", coTYPE);
    # def->label = L("LABEL");
    # def->category = L("CATEGORY");
    # def->tooltip = L("TOOLTIP");
    # def->set_default_value(new ConfigOptionTYPE(VALUE));

    # We use a state-based parser because category is assigned sequentially
    current_category = "Others"
    parameters = []
    
    # Simple regex to find blocks of definitions
    # Match: def = this->add("key", coType); ... def->label = L("label"); ... def->category = L("cat"); ...
    
    blocks = re.split(r'def\s*=\s*this->add\(', content)
    for block in blocks[1:]:
        # Extract key
        key_match = re.search(r'"([^"]+)",\s*(\w+)\)', block)
        if not key_match: continue
        key, co_type = key_match.groups()
        
        # Extract properties
        label_match = re.search(r'def->label\s*=\s*L\("([^"]+)"\)', block)
        label = label_match.group(1) if label_match else key
        
        cat_match = re.search(r'def->category\s*=\s*L\("([^"]+)"\)', block)
        if cat_match:
            current_category = cat_match.group(1)
            if current_category == "": current_category = "Others"
            
        tooltip_match = re.search(r'def->tooltip\s*=\s*L\("([^"]+)"\)', block)
        tooltip = tooltip_match.group(1).replace('\\n', ' ') if tooltip_match else ""
        
        # Default value
        default_val = "0"
        default_match = re.search(r'def->set_default_value\(new\s+\w+\(([^)]+)\)\)', block)
        if default_match:
            default_val = default_match.group(1).replace('true', 'True').replace('false', 'False')
            # Strip extra quotes and casts
            default_val = re.sub(r'INITIAL_LAYER_HEIGHT|INITIAL_TEMPERATURE|sp\w+', '0', default_val)
            default_val = default_val.strip('"').strip()

        # Type mapping
        q_type = "float"
        if "coBool" in co_type: q_type = "boolean"
        elif "coInt" in co_type: q_type = "integer"
        elif "coEnum" in co_type: q_type = "choice"
        elif "coString" in co_type: q_type = "string"
        elif "Percent" in co_type: q_type = "numeric"
        
        parameters.append({
            "name": key,
            "title": label,
            "description": tooltip,
            "category": current_category,
            "type": q_type,
            "defaultValue": default_val
        })

    return parameters

def generate_schema(params):
    ROOTS = ["Quality", "Strength", "Speed", "Support", "Others", "Advanced"]
    HUBS_CONFIG = {
        "Quality": {
            "Layer height": ["layer_height", "initial_layer_height", "adaptive_layer_height", "min_layer", "max_layer"],
            "Line width": ["line_width"],
            "Seam": ["seam", "scarf", "staggered"],
            "Ironing": ["ironing"],
            "Precision": ["precision", "resolution", "xy_size_compensation", "elefant_foot_compensation", "arc_fitting"],
            "Overhangs": ["overhang", "bridge_speed", "bridge_flow"],
        },
        "Strength": {
            "Walls": ["wall", "perimeter", "shell", "wall_loops", "alternate_extra_wall", "detect_thin_wall"],
            "Infill": ["infill", "bridge_angle", "gap_fill", "solid_infill", "sparse_infill"],
            "Strength": ["strength", "load_infill"],
        },
        "Speed": {
            "Speed": ["speed", "feedrate", "print_speed"],
            "Acceleration": ["accel", "jerk", "limit", "resonance"],
        },
        "Support": {
            "Support structure": ["support", "raft", "skirt", "brim", "raft_layers", "contact_distance"],
            "Support filament": ["support_filament", "support_interface_filament"],
        },
        "Others": {
            "Cooling": ["fan", "cool", "temperature"],
            "Travel": ["travel", "retract", "z_hop", "wipe"],
            "Filament": ["filament", "nozzle"],
        }
    }

    # First, let's pre-generate all categories
    categories = []
    for root in ROOTS:
        categories.append({
            "name": root,
            "title": root,
            "role": "buildJob",
            "color": "blue"
        })
        if root in HUBS_CONFIG:
            for hub_title in HUBS_CONFIG[root]:
                categories.append({
                    "name": f"{root}_{hub_title}",
                    "title": hub_title,
                    "parent": root,
                    "role": "buildJob",
                    "color": "cyan"
                })
        # Add a General Hub for each root to catch leftovers
        categories.append({
            "name": f"{root}_General",
            "title": f"{root} General",
            "parent": root,
            "role": "buildJob",
            "color": "cyan"
        })

    available_params = []
    quantities = {
        "length": {"id": "length", "type": "numeric", "unit": "millimeter", "symbol": "mm", "title": "Length"},
        "speed": {"id": "speed", "type": "numeric", "unit": "millimeter/second", "symbol": "mm/s", "title": "Speed"},
        "acceleration": {"id": "acceleration", "type": "numeric", "unit": "millimeter/second^2", "symbol": "mm/s²", "title": "Acceleration"},
        "jerk": {"id": "jerk", "type": "numeric", "unit": "millimeter/second^3", "symbol": "mm/s³", "title": "Jerk"},
        "temperature": {"id": "temperature", "type": "numeric", "unit": "celsius", "symbol": "°C", "title": "Temperature"},
        "volumetric_flow": {"id": "volumetric_flow", "type": "numeric", "unit": "cubic millimeter/second", "symbol": "mm³/s", "title": "Volumetric Flow"},
        "boolean": {"id": "boolean", "type": "boolean", "title": "Boolean"},
        "choice": {"id": "choice", "type": "choice", "title": "Choice"},
        "string": {"id": "string", "type": "string", "title": "String"},
        "percentage": {"id": "percentage", "type": "percentage", "symbol": "%", "meta": {"requiresReference": True}, "title": "Percentage"},
        "relative": {"id": "relative", "type": "relative", "symbol": "x", "meta": {"requiresReference": True}, "title": "Relative"},
        "time": {"id": "time", "type": "numeric", "unit": "second", "symbol": "s", "title": "Time"},
        "count": {"id": "count", "type": "numeric", "title": "Count"},
        "angle": {"id": "angle", "type": "numeric", "unit": "degree", "symbol": "°", "title": "Angle"},
    }
    
    for p in params:
        orca_cat = p["category"]
        
        # Map some specific Orca categories to our Roots
        if orca_cat == "Machine limits":
            orca_cat = "Others"
        if orca_cat == "Extruders":
            orca_cat = "Support"
            
        if orca_cat not in ROOTS:
            orca_cat = "Others"
            
        target_cat = f"{orca_cat}_General"
        # Try to find a specific hub
        if orca_cat in HUBS_CONFIG:
            for hub_title, keywords in HUBS_CONFIG[orca_cat].items():
                if any(k in p["name"].lower() or k in p["title"].lower() for k in keywords):
                    target_cat = f"{orca_cat}_{hub_title}"
                    break
        
        # Determine specific quantity for this parameter
        p_name = p["name"]
        p_name_low = p_name.lower()
        p_type = p["type"]
        
        qty_id = "count"
        options = None

        if p_type == "boolean":
            qty_id = "boolean"
        elif p_type == "choice":
            qty_id = "choice"
            options = {"default": "Default"}
        elif p_type == "string":
            qty_id = "string"
        elif "percent" in p_name_low or p_type == "percentage":
            qty_id = "percentage"
        elif "layer_height" in p_name_low or "width" in p_name_low or "distance" in p_name_low or "length" in p_name_low or "offset" in p_name_low or "clearance" in p_name_low or "spacing" in p_name_low:
            qty_id = "length"
        elif "speed" in p_name_low:
            qty_id = "speed"
        elif "accel" in p_name_low:
            qty_id = "acceleration"
        elif "jerk" in p_name_low:
            qty_id = "jerk"
        elif "temperature" in p_name_low:
            qty_id = "temperature"
        elif "flow" in p_name_low:
            qty_id = "volumetric_flow"
        elif "angle" in p_name_low:
            qty_id = "angle"
        elif "time" in p_name_low:
            qty_id = "time"

        available_params.append({
            "name": p_name,
            "title": p["title"],
            "description": p["description"],
            "category": target_cat,
            "quantityIds": [qty_id],
            "defaultValue": {
                "target": p_name,
                "expression": str(p["defaultValue"])
            },
            "options": options
        })

    schema = {
        "name": "OrcaSlicer",
        "manifest": {
            "schemaType": "application",
            "schemaVersion": "0.3",
            "schemaAuthors": ["The Laminode AI"],
            "lastUpdated": "2026-02-21",
            "targetAppName": "OrcaSlicer"
        },
        "quantities": quantities,
        "categories": categories,
        "availableParameters": available_params
    }
    return schema

if __name__ == "__main__":
    assets_dir = "/home/manuel/Documents/LamiNode/laminode_plugins/applications/OrcaSlicer/v2.3.1/tool/assets"
    cpp_file = os.path.join(assets_dir, "PrintConfig.cpp")
    
    if os.path.exists(cpp_file):
        params = parse_print_config(cpp_file)
        schema = generate_schema(params)
        
        # Output to BOTH v0.1 and v2.3.1 to ensure backend sees it regardless of manifest state
        for version in ["v0.1", "v2.3.1"]:
            output_dir = f"/home/manuel/Documents/LamiNode/laminode_plugins/applications/OrcaSlicer/v2.3.1/schemas/{version}"
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "schema.json"), "w") as f:
                json.dump(schema, f, indent=4)
        
        print(f"Generated schema with {len(params)} parameters across v0.1 and v2.3.1.")
    else:
        print(f"Error: {cpp_file} not found.")
