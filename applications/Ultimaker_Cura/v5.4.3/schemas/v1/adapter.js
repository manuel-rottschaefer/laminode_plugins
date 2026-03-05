/**
 * Ultimaker Cura Profile Adapter for LamiNode
 *
 * This adapter enables importing and exporting Cura profiles (.curaprofile)
 * Cura uses an INI-like format with sections: [general], [metadata], [values]
 *
 * Required functions:
 * - validateFile(fileContent): Validate file structure (returns {isValid, error})
 * - importLayer(fileContent): Parse Cura profile and return layer data
 * - exportProfile(profileSnapshot): Generate Cura profile from snapshot
 *
 * Optional functions:
 * - importProfile(fileContent): Not used for Cura (returns null)
 */

/**
 * Parses INI-style content into sections
 * @param {string} content - The INI file content
 * @returns {Object} Section map with section names as keys
 */
function parseIni(content) {
  const sections = {};
  let currentSection = null;

  const lines = content.split(/\r?\n/);

  for (const line of lines) {
    const trimmed = line.trim();

    // Skip empty lines and comments
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) {
      continue;
    }

    // Check for section header
    const sectionMatch = trimmed.match(/^\[(.+)\]$/);
    if (sectionMatch) {
      currentSection = sectionMatch[1];
      sections[currentSection] = {};
      continue;
    }

    // Parse key-value pairs
    if (currentSection) {
      const kvMatch = trimmed.match(/^([^=]+)=(.*)$/);
      if (kvMatch) {
        const key = kvMatch[1].trim();
        const value = kvMatch[2].trim();
        sections[currentSection][key] = value;
      }
    }
  }

  return sections;
}

/**
 * Converts a value string to appropriate JavaScript type
 * @param {string} valueStr - The value as string
 * @returns {*} Parsed value (number, boolean, or string)
 */
function parseValue(valueStr) {
  if (valueStr === "True" || valueStr === "true") return true;
  if (valueStr === "False" || valueStr === "false") return false;

  const numValue = Number(valueStr);
  if (!isNaN(numValue) && valueStr !== "") return numValue;

  return valueStr;
}

/**
 * Formats a value for INI output
 * @param {*} value - The value to format
 * @returns {string} Formatted value string
 */
function formatValue(value) {
  if (typeof value === "boolean") {
    return value ? "True" : "False";
  }
  if (typeof value === "number") {
    return value.toString();
  }
  return String(value);
}

/**
 * Validates that a file has valid Cura profile structure
 * @param {Object} args - Arguments object
 * @param {string} args.fileContent - The file content to validate
 * @returns {Object} Validation result with isValid and optional error message
 */
function validateFile(args) {
  const fileContent = args.fileContent;

  if (!fileContent || typeof fileContent !== "string") {
    return {
      isValid: false,
      error: "File content is empty or invalid",
    };
  }

  if (fileContent.trim().length === 0) {
    return {
      isValid: false,
      error: "File is empty",
    };
  }

  try {
    const sections = parseIni(fileContent);

    // Check for at least one required section
    if (!sections.general && !sections.values && !sections.metadata) {
      return {
        isValid: false,
        error: "File does not contain valid Cura profile sections ([general], [values], or [metadata])",
      };
    }

    // Validate general section if present
    if (sections.general) {
      const general = sections.general;
      // Version should be present and numeric
      if (general.version && isNaN(Number(general.version))) {
        return {
          isValid: false,
          error: "Invalid version in [general] section",
        };
      }
    }

    // Check that values section has at least some parameters
    if (sections.values && Object.keys(sections.values).length === 0) {
      return {
        isValid: false,
        error: "[values] section exists but contains no parameters",
      };
    }

    return {
      isValid: true,
      metadata: {
        hasSections: Object.keys(sections),
        parameterCount: sections.values ? Object.keys(sections.values).length : 0,
        profileName: sections.general?.name || "Unnamed",
      },
    };
  } catch (error) {
    return {
      isValid: false,
      error: `Failed to parse file: ${error.message || String(error)}`,
    };
  }
}

/**
 * Imports a Cura profile as a LamiNode layer
 * @param {Object} args - Arguments object
 * @param {string} args.fileContent - The .curaprofile file content
 * @returns {Object} Layer data with layerName and parameters
 */
function importLayer(args) {
  const fileContent = args.fileContent;

  if (!fileContent || typeof fileContent !== "string") {
    throw new Error("fileContent is required and must be a string");
  }

  const sections = parseIni(fileContent);

  // Extract layer name from general section
  const general = sections.general || {};
  const layerName = general.name || "Imported Cura Profile";

  // Extract parameters from values section
  const values = sections.values || {};
  const parameters = [];

  for (const [paramName, valueStr] of Object.entries(values)) {
    // Skip empty values
    if (!valueStr || valueStr.trim() === "") continue;

    parameters.push({
      paramName: paramName,
      value: parseValue(valueStr),
    });
  }

  return {
    layerName: layerName,
    parameters: parameters,
    description: `Imported from Cura profile: ${layerName}`,
    metadata: {
      source: "Ultimaker Cura",
      importDate: new Date().toISOString(),
      originalVersion: general.version || "4",
      definition: general.definition || "fdmprinter",
    },
  };
}

/**
 * Import profile - not used for Cura (profiles are imported as layers)
 * @returns {null}
 */
function importProfile(args) {
  return null;
}

/**
 * Exports a LamiNode profile snapshot as a Cura profile
 * @param {Object} profileSnapshot - The profile snapshot to export
 * @param {Object} profileSnapshot.modifiedParams - Modified parameters map
 * @param {string} profileSnapshot.profileName - Profile name
 * @param {string} profileSnapshot.schemaId - Schema ID
 * @returns {Object} Object with fileContent property
 */
function exportProfile(profileSnapshot) {
  if (!profileSnapshot || typeof profileSnapshot !== "object") {
    throw new Error("profileSnapshot is required and must be an object");
  }

  const modifiedParams = profileSnapshot.modifiedParams || {};
  const profileName = profileSnapshot.profileName || "LamiNode Export";

  // Build INI sections
  const lines = [];

  // [general] section
  lines.push("[general]");
  lines.push("version = 4");
  lines.push(`name = ${profileName}`);
  lines.push("definition = fdmprinter");
  lines.push("");

  // [metadata] section
  lines.push("[metadata]");
  lines.push("type = quality_changes");
  lines.push("quality_type = normal");
  lines.push("setting_version = 22");
  lines.push("");

  // [values] section - main parameters
  lines.push("[values]");

  // Sort parameters for consistent output
  const sortedParams = Object.keys(modifiedParams).sort();

  for (const paramName of sortedParams) {
    const value = modifiedParams[paramName];

    // Skip null/undefined values
    if (value == null) continue;

    lines.push(`${paramName} = ${formatValue(value)}`);
  }

  lines.push("");

  // Join all lines with newline
  const fileContent = lines.join("\n");

  return {
    fileContent: fileContent,
  };
}
