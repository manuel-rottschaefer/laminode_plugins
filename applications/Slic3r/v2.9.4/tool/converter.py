# Converter for Slic3r PrintConfig.cpp -> LamiNode schema
# Import necessary libraries
# Define constants and utility functions

import re
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "0.1"
QUIET = False

RAINBOW_COLORS = [
	"red", "crimson", "deepOrange", "orange", "amber", "gold", "yellow",
	"lime", "lightGreen", "green", "emerald", "teal", "cyan", "lightBlue",
	"blue", "indigo", "deepPurple", "violet", "purple", "pink"
]


def log(msg: str) -> None:
	if not QUIET:
		print(msg)


def clean_text(s: Optional[str]) -> str:
	if not s:
		return ""
	# normalize whitespace
	return re.sub(r"\s+", " ", s.strip())


TYPE_MAP = {
	"coString": "string",
	"coFloat": "float",
	"coInt": "integer",
	"coBool": "boolean",
	"coEnum": "choice",
	"coPoints": "points",
}


def parse_cpp_options(content: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
	# Find all occurrences of 'def = this->add("name", coType);'
	add_re = re.compile(r'def\s*=\s*this->add\(\s*"([^"]+)"\s*,\s*(co\w+)\s*\);')
	matches = list(add_re.finditer(content))
	params: List[Dict[str, Any]] = []
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

	for idx, m in enumerate(matches):
		name = m.group(1)
		ctype = m.group(2)
		start = m.end()
		end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
		block = content[start:end]

		title = None
		description = None
		category = None
		unit = None
		minv = None
		maxv = None
		options = None
		default = None

		lbl = re.search(r'def->label\s*=\s*L\("([^"]*)"\)', block)
		if lbl:
			title = lbl.group(1)

		tip = re.search(r'def->tooltip\s*=\s*L\("([^"]*)"\)', block, re.S)
		if tip:
			description = tip.group(1).replace('\\n', '\n')

		cat = re.search(r'def->category\s*=\s*L\("([^"]*)"\)', block)
		if cat:
			category = cat.group(1)

		side = re.search(r'def->sidetext\s*=\s*L\("([^"]*)"\)', block)
		if side:
			unit = side.group(1)

		mn = re.search(r'def->min\s*=\s*([0-9eE\+\-\.]+)', block)
		if mn:
			minv = mn.group(1)

		mx = re.search(r'def->max\s*=\s*([0-9eE\+\-\.]+)', block)
		if mx:
			maxv = mx.group(1)

		enum_call = re.search(r'def->set_enum<[^>]+>\s*\(\s*\{([^}]*)\}\s*\)', block, re.S)
		if enum_call:
			opts_raw = enum_call.group(1)
			options_list = re.findall(r'"([^"]+)"', opts_raw)
			options = {opt: opt for opt in options_list}

		# default value patterns
		d_str = re.search(r'set_default_value\(new\s+ConfigOptionString\("([^"]*)"\)\)', block)
		if d_str:
			default = d_str.group(1)

		d_float = re.search(r'set_default_value\(new\s+ConfigOptionFloat\(\s*([0-9eE\+\-\.]+)\s*\)\)', block)
		if d_float:
			default = d_float.group(1)

		d_int = re.search(r'set_default_value\(new\s+ConfigOptionInt\(\s*([0-9eE\+\-\.]+)\s*\)\)', block)
		if d_int:
			default = d_int.group(1)

		d_bool = re.search(r'set_default_value\(new\s+ConfigOptionBool\(\s*(true|false)\s*\)\)', block)
		if d_bool:
			default = d_bool.group(1)

		d_enum = re.search(r'set_default_value\(new\s+ConfigOptionEnum<[^>]+>\(([^)]+)\)\)', block)
		if d_enum:
			tok = d_enum.group(1).strip()
			# try to map token to option text by stripping common prefixes
			def_tok = tok
			for pfx in ("pt", "gcf", "ht", "bt", "smp", "ip"):
				if def_tok.startswith(pfx):
					def_tok = def_tok[len(pfx):]
					break
			# try match against options
			if options:
				match_opt = None
				for o in options.keys():
					if o.lower() == def_tok.lower() or o.replace('-', '').lower() == def_tok.lower():
						match_opt = o
						break
				default = match_opt if match_opt is not None else (list(options.keys())[0] if options else tok)
			else:
				default = tok

		# Fallback: sometimes code uses set_default_value(new ConfigOptionString());
		d_empty_str = re.search(r'set_default_value\(new\s+ConfigOptionString\(\)\)', block)
		if d_empty_str and default is None:
			default = ""

		# Build parameter dict
		param: Dict[str, Any] = {"name": name}
		if title:
			param["title"] = clean_text(title)
		if description:
			param["description"] = clean_text(description)
		if category:
			param["category"] = category

		qtype = TYPE_MAP.get(ctype, "numeric")
		
		# Define specific quantity for this parameter
		qty_id = "count"
		name_low = name.lower()

		if qtype == "boolean":
			qty_id = "boolean"
		elif qtype == "choice":
			qty_id = "choice"
			if options is None:
				options = {"default": "Default"}
		elif qtype == "string":
			qty_id = "string"
		elif any(k in name_low for k in ["height", "width", "distance", "length", "area", "size", "offset", "clearance", "spacing"]):
			qty_id = "length"
		elif "speed" in name_low:
			qty_id = "speed"
		elif "accel" in name_low:
			qty_id = "acceleration"
		elif "jerk" in name_low:
			qty_id = "jerk"
		elif "temp" in name_low:
			qty_id = "temperature"
		elif "flow" in name_low:
			qty_id = "volumetric_flow"
		elif "angle" in name_low:
			qty_id = "angle"
		elif "percent" in name_low or qtype == "percentage":
			qty_id = "percentage"

		param["quantityIds"] = [qty_id]
		if options:
			param["options"] = options

		if default is not None:
			expr = default
			if expr in ("true", "false"):
				expr = expr.lower()
			param["defaultValue"] = {"target": name, "expression": expr}

		if minv is not None:
			param["minThreshold"] = {"target": name, "expression": minv}
		if maxv is not None:
			param["maxThreshold"] = {"target": name, "expression": maxv}

		params.append(param)

	return params, quantities


def build_categories(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
	seen: Dict[str, int] = {}
	cats: List[Dict[str, Any]] = []
	for p in params:
		cat = p.get("category") or "general"
		if cat not in seen:
			seen[cat] = len(seen)
			cats.append({"name": cat, "title": cat, "role": "buildJob"})

	# Assign colors
	for i, c in enumerate(cats):
		c["color"] = RAINBOW_COLORS[i % len(RAINBOW_COLORS)]
	return cats


def apply_parent_mapping(categories: List[Dict[str, Any]], params: List[Dict[str, Any]]) -> None:
	"""Apply manual or inferred parent relationships to categories.

	This will add a `parent` key to category objects when a mapping is known.
	If a parent category is referenced but doesn't exist, it will be created.
	"""
	# Manual mapping for Slic3r: map certain categories under a higher-level 'Quality' group.
	PARENT_MAP = {
		"Layers and Perimeters": "Quality",
		"Extrusion Width": "Quality",
	}

	name_to_cat = {c["name"]: c for c in categories}

	for child, parent in PARENT_MAP.items():
		# ensure child category exists
		if child not in name_to_cat:
			new_child = {"name": child, "title": child, "role": "buildJob"}
			new_child["color"] = RAINBOW_COLORS[len(categories) % len(RAINBOW_COLORS)]
			categories.append(new_child)
			name_to_cat[child] = new_child

		name_to_cat[child]["parent"] = parent

		if parent not in name_to_cat:
			# create parent category entry
			new_cat = {"name": parent, "title": parent, "role": "buildJob"}
			# assign next color
			new_cat["color"] = RAINBOW_COLORS[len(categories) % len(RAINBOW_COLORS)]
			categories.append(new_cat)
			name_to_cat[parent] = new_cat

	# Optionally, try to infer parent categories from parameter names or patterns
	# (not implemented heuristics here). This function can be extended later.



def main():
	script_dir = Path(__file__).resolve().parent
	input_path = script_dir / "assets" / "PrintConfig.cpp"
	default_output = script_dir.parent / "schemas" / "v0.1a" / "schema.json"

	parser = argparse.ArgumentParser(description="Convert Slic3r PrintConfig.cpp to LamiNode schema")
	parser.add_argument("output", nargs="?", help="Optional output path")
	parser.add_argument("-o", "--output", dest="opt_output", help="Output path")
	parser.add_argument("--quiet", action="store_true", dest="quiet", help="Quiet output")
	parser.add_argument("--version", action="store_true", dest="version", help="Show version")
	args = parser.parse_args()

	if args.version:
		print(VERSION)
		return
	global QUIET
	QUIET = bool(args.quiet)

	output_path = Path(args.opt_output or args.output) if (args.opt_output or args.output) else default_output

	if not input_path.exists():
		print(f"Error: input file not found: {input_path}")
		return

	log(f"Loading {input_path}")
	content = input_path.read_text(encoding="utf-8")

	log("Parsing parameters...")
	parameters, quantities = parse_cpp_options(content)

	log("Building categories...")
	categories = build_categories(parameters)

	# Apply parent mappings (manual or inferred)
	apply_parent_mapping(categories, parameters)

	schema = {
		"name": "Slic3r / PrusaSlicer",
		"manifest": {
			"schemaType": "application",
			"schemaVersion": "0.3",
			"schemaAuthors": ["The Laminode Team"],
			"lastUpdated": datetime.now().strftime("%Y-%m-%d"),
			"targetAppName": "Slic3r"
		},
		"quantities": quantities,
		"categories": categories,
		"availableParameters": parameters
	}

	# Output to both version folders
	script_dir = Path(__file__).resolve().parent
	for version in ["v0.1", "v0.1a"]:
		out_dir = script_dir.parent / "schemas" / version
		out_dir.mkdir(parents=True, exist_ok=True)
		out_file = out_dir / "schema.json"
		out_file.write_text(json.dumps(schema, indent=4, ensure_ascii=False), encoding="utf-8")
		log(f"Wrote schema to {out_file}")

	log(f" - Categories: {len(categories)}")
	log(f" - Parameters: {len(parameters)}")


if __name__ == "__main__":
	main()

