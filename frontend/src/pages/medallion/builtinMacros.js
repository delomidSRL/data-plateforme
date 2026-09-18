// Mirrors backend/app/services/payload_structure.py's STRUCTURATION_MACROS (signatures only,
// for display + click-to-insert in DatasetPanel/MacrosTab — the real SQL stays server-side,
// same "closed catalog mirrored client-side" convention as StructurationPanel's STANDARDIZE_OPS).
// `insert` is the snippet DatasetPanel's insertMacro() places at the cursor.
export const BUILTIN_MACROS = [
  { name: "clean_string", params: ["expr"], insert: "clean_string(col)", descriptionKey: "clean_string" },
  { name: "safe_cast", params: ["type", "col", "format=none"], insert: "safe_cast('text', col)", descriptionKey: "safe_cast" },
  { name: "cast_issue", params: ["col", "field_name", "pattern", "required"], insert: "cast_issue(col, 'field_name', 'pattern', true)", descriptionKey: "cast_issue" },
  { name: "normalize_for_matching", params: ["col"], insert: "normalize_for_matching(col)", descriptionKey: "normalize_for_matching" },
  { name: "clean_vat", params: ["col"], insert: "clean_vat(col)", descriptionKey: "clean_vat" },
  { name: "clean_phone", params: ["col"], insert: "clean_phone(col)", descriptionKey: "clean_phone" },
  { name: "format_flag", params: ["col", "flag", "regex"], insert: "format_flag(col, 'flag_name', 'regex')", descriptionKey: "format_flag" },
  { name: "placeholder_name_flag", params: ["col", "flag"], insert: "placeholder_name_flag(col, 'flag_name')", descriptionKey: "placeholder_name_flag" },
  { name: "garbage_flag", params: ["col", "flag"], insert: "garbage_flag(col, 'flag_name')", descriptionKey: "garbage_flag" },
  { name: "date_range_flag", params: ["col", "flag", "min_date='1900-01-01'", "max_date=none"], insert: "date_range_flag(col, 'flag_name')", descriptionKey: "date_range_flag" },
  { name: "unmapped_boolean_flag", params: ["cast_issues_col", "boolean_fields", "flag"], insert: "unmapped_boolean_flag(cast_issues, ['field'], 'flag_name')", descriptionKey: "unmapped_boolean_flag" },
];
