import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";
import * as mlTemplatesApi from "../../api/mlTemplates.js";
import * as dbtMacrosApi from "../../api/dbtMacros.js";
import * as sourcesApi from "../../api/sources.js";
import DataPreviewPanel from "./DataPreviewPanel.jsx";
import IndicatorsPanel from "./IndicatorsPanel.jsx";
import PublishPanel from "./PublishPanel.jsx";
import StructurationPanel from "./StructurationPanel.jsx";
import StructurationPopup from "./StructurationPopup.jsx";
import SchemaValidationModal from "../imports/SchemaValidationModal.jsx";
import { BUILTIN_MACROS } from "./builtinMacros.js";

const LAYER_ORDER = { bronze: 0, silver: 1, gold: 2 };
const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Any {{ ref('01_unpacked_<name>') }} / '02_typed_<name>' / '05_validated_<name>' for one
// bronze — the set of refs a "which stage does this SQL read" selector treats as mutually
// exclusive alternatives to the SAME upstream, never several at once.
const stageRefPattern = (bronzeName, flags = "") => new RegExp(`\\{\\{\\s*ref\\(['"](01_unpacked|02_typed|05_validated)_${escapeRegExp(bronzeName)}['"]\\)\\s*\\}\\}`, flags);
const TEST_TYPES = ["not_null", "unique", "accepted_values", "relationships"];
const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };

const BLANK_STARTER = `df = read_table("TABLE_ENTREE")

# votre calcul ici

write_table(df, "NOM_TABLE_SORTIE")
`;

function substitutePlaceholders(code, expectedInputs, values) {
  let result = code;
  for (const field of expectedInputs || []) {
    const raw = (values[field.key] ?? "").trim();
    let replacement;
    if (!raw) {
      replacement = field.key; // rien saisi : on laisse le placeholder visible, pas de code invalide
    } else if (field.type === "column_list") {
      replacement = "[" + raw.split(",").map((s) => s.trim()).filter(Boolean).map((s) => `"${s}"`).join(", ") + "]";
    } else {
      replacement = raw;
    }
    result = result.split(field.key).join(replacement);
  }
  return result;
}

export default function DatasetPanel({ project, datasets, dataset, defaultLayer, sources, onClose, onSaved, onDeleted, onStructurationSaved, readOnly = false, initialTab, prefill = null }) {
  const { t } = useTranslation();
  const ML_OBJECTIVE_LABEL = t("medallion.mlObjectives", { returnObjects: true });
  const ML_OBJECTIVES = [
    { value: "anomaly", label: ML_OBJECTIVE_LABEL.anomaly },
    { value: "scoring", label: ML_OBJECTIVE_LABEL.scoring },
    { value: "forecast", label: ML_OBJECTIVE_LABEL.forecast },
    { value: "clustering", label: ML_OBJECTIVE_LABEL.clustering },
    { value: "record_linkage", label: ML_OBJECTIVE_LABEL.record_linkage },
    { value: "none", label: t("medallion.panel.blank") },
  ];

  const isEdit = !!dataset;
  const [panelTab, setPanelTab] = useState(initialTab || "config"); // "config" | "preview" (Module 10) | "publish" (Module 11, gold only) | "indicators" (Module 12)
  const [layer, setLayer] = useState(dataset?.layer || defaultLayer || "bronze");
  // UX ask — the "02 typed" node's "+" opens this same panel pre-seeded (name, upstream, a
  // starter SELECT) via `prefill`, so it only ever applies to a brand-new dataset — an
  // existing one's own saved values always win.
  const [name, setName] = useState(dataset?.name || prefill?.name || "");
  const [description, setDescription] = useState(dataset?.description || "");

  // Sources span 4 unrelated types (PostgreSQL/MySQL/Oracle/MinIO) — picking the type first
  // narrows a possibly-long flat list down to only the sources that could actually apply.
  const [sourceType, setSourceType] = useState(() => {
    if (!dataset?.source_id) return "";
    return (sources || []).find((s) => s.id === dataset.source_id)?.type || "";
  });
  const [sourceId, setSourceId] = useState(dataset?.source_id || "");
  const [sourceObject, setSourceObject] = useState(dataset?.source_object || "");
  const [loadMode, setLoadMode] = useState(dataset?.load_mode || "full");
  const [incrementalKey, setIncrementalKey] = useState(dataset?.incremental_key || "");

  const [dbtModelName, setDbtModelName] = useState(dataset?.dbt_model_name || prefill?.dbtModelName || "");
  const [materialization, setMaterialization] = useState(dataset?.materialization || "view");
  // A new silver dataset starts its query pre-filled with "SELECT * " rather than empty —
  // one less thing to type before picking the FROM/upstream reference. `prefill.sql` (the
  // "02 typed" node's "+") overrides that default with a real starter FROM already in place.
  const [sql, setSql] = useState(dataset?.sql || prefill?.sql || ((dataset?.layer || defaultLayer) === "silver" ? "SELECT * " : ""));

  const [transformType, setTransformType] = useState(dataset?.transform_type || "dbt");
  const [mlObjective, setMlObjective] = useState(dataset?.ml_objective || "none");
  const [pythonCode, setPythonCode] = useState(dataset?.python_code || "");
  const [outputTable, setOutputTable] = useState(dataset?.output_table || "");

  const [templates, setTemplates] = useState([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState(dataset?.template_id ?? null);
  const [templateValues, setTemplateValues] = useState({});
  const [lastAutoCode, setLastAutoCode] = useState(dataset?.python_code || "");

  const [upstreamIds, setUpstreamIds] = useState(new Set(dataset?.upstream_dataset_ids || prefill?.upstreamIds || []));
  const [tests, setTests] = useState(dataset?.tests || []);
  const [structurationPopupDataset, setStructurationPopupDataset] = useState(null); // Module 6 extension

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // UX ask — "Valider la syntaxe": resolves ref()/source() project-wide + a real EXPLAIN
  // against the warehouse, without saving anything. null = not checked yet since the last edit.
  const [sqlValidation, setSqlValidation] = useState(null);
  const [validatingSql, setValidatingSql] = useState(false);
  const validateSql = async () => {
    setValidatingSql(true);
    setSqlValidation(null);
    try {
      setSqlValidation(await medallionApi.validateSql(project.id, sql));
    } catch (err) {
      setSqlValidation({ valid: false, message: err.message || t("medallion.panel.sqlValidateFailed") });
    } finally {
      setValidatingSql(false);
    }
  };

  useEffect(() => { mlTemplatesApi.listTemplates().then(setTemplates).catch(() => {}); }, []);

  // New module — the platform's admin-managed macro library (Settings · Macros dbt), offered
  // alongside BUILTIN_MACROS in the SQL editor's "Macros disponibles" section (same
  // click-to-insert idea as columns/tables).
  const [customMacros, setCustomMacros] = useState([]);
  useEffect(() => { dbtMacrosApi.listMacros().then(setCustomMacros).catch(() => {}); }, []);

  // MinIO/S3 sources: browse actual files instead of typing a path blind — picking a
  // file (not a whole prefix) is what keeps each bronze table's schema clean. A source
  // scoped to one bucket (options.bucket) or with only one bucket goes straight to its
  // file list; a source with several buckets shows a bucket picker first.
  const [browseObjects, setBrowseObjects] = useState([]);
  const [browseBucket, setBrowseBucket] = useState("");
  const [browseBuckets, setBrowseBuckets] = useState([]);
  const [browseTables, setBrowseTables] = useState([]); // ["schema.table", ...] for SQL sources
  const [browsing, setBrowsing] = useState(false);
  const [browseFilter, setBrowseFilter] = useState("");

  const loadBucketObjects = (bucket) => {
    setBrowsing(true);
    sourcesApi.introspectSource(sourceId, bucket)
      .then((res) => { setBrowseObjects(res.objects || []); setBrowseBucket(bucket); })
      .catch(() => setBrowseObjects([]))
      .finally(() => setBrowsing(false));
  };

  const changeBrowseBucket = () => { setBrowseBucket(""); setBrowseObjects([]); setBrowseFilter(""); };

  useEffect(() => {
    setBrowseObjects([]); setBrowseBuckets([]); setBrowseBucket(""); setBrowseTables([]); setBrowseFilter("");
    if (readOnly || layer !== "bronze" || !sourceId) return;
    const source = sources.find((s) => s.id === Number(sourceId));
    if (!source) return;
    let cancelled = false;

    if (source.type === "minio") {
      setBrowsing(true);
      sourcesApi.introspectSource(sourceId)
        .then((res) => {
          if (cancelled) return;
          if (res.objects && res.objects.length) {
            setBrowseObjects(res.objects);
            setBrowseBucket((res.buckets && res.buckets[0]) || "");
            setBrowsing(false);
            return;
          }
          if (res.buckets && res.buckets.length === 1) {
            return sourcesApi.introspectSource(sourceId, res.buckets[0]).then((res2) => {
              if (cancelled) return;
              setBrowseObjects(res2.objects || []);
              setBrowseBucket(res.buckets[0]);
              setBrowsing(false);
            });
          }
          setBrowseBuckets(res.buckets || []);
          setBrowsing(false);
        })
        .catch(() => { if (!cancelled) { setBrowseObjects([]); setBrowseBuckets([]); setBrowsing(false); } });
    } else if (["postgresql", "mysql", "oracle"].includes(source.type)) {
      // Same "browse instead of type blind" idea as MinIO, but tables are already flat
      // (no bucket-picker step needed) — one searchable, sorted schema.table list.
      setBrowsing(true);
      sourcesApi.introspectSource(sourceId)
        .then((res) => {
          if (cancelled) return;
          const items = [];
          for (const schema of res.schemas || []) {
            for (const tbl of schema.tables) items.push(`${schema.name}.${tbl.name}`);
          }
          items.sort();
          setBrowseTables(items);
        })
        .catch(() => { if (!cancelled) setBrowseTables([]); })
        .finally(() => { if (!cancelled) setBrowsing(false); });
    }

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layer, sourceId]);

  const pickBrowsedObject = (obj) => {
    setSourceObject(`${browseBucket}/${obj}`);
    if (!name.trim()) {
      const fileName = obj.split("/").pop();
      setName(fileName.replace(/\.[^.]+$/, "").replace(/\W/g, "_"));
    }
  };

  const pickBrowsedTable = (qualified) => {
    setSourceObject(qualified);
    if (!name.trim()) setName(qualified.split(".").pop().replace(/\W/g, "_"));
  };

  const availableSourceTypes = [...new Set(sources.map((s) => s.type))];
  const sourcesOfType = sourceType ? sources.filter((s) => s.type === sourceType) : [];
  const handleSourceTypeChange = (newType) => {
    setSourceType(newType);
    setSourceId(""); // the previous pick may not exist under the new type
  };

  const isBronze = layer === "bronze";
  const isPython = layer === "gold" && transformType === "python";

  // UX ask — a CSV/Excel file already sitting in the bucket just browsed gets the exact same
  // payload/typed choice the standalone Imports wizard offers for an uploaded file, without
  // leaving this panel. Purely additive: leaving this section alone and just saving keeps the
  // dataset pointed at the raw bucket/key exactly as before (ingested by the DAG's generic
  // object-store loader) — nothing here is required.
  const s3ObjectFormat = /\.(xlsx|xls)$/i.test(sourceObject) ? "excel" : "csv";
  const isS3Importable = !isEdit && isBronze && sourceType === "minio" && sourceObject && /\.(csv|xlsx|xls)$/i.test(sourceObject);
  const [s3ImportMode, setS3ImportMode] = useState("typed");
  const [s3WriteMode, setS3WriteMode] = useState("create");
  const [s3Delimiter, setS3Delimiter] = useState("");
  const [s3Encoding, setS3Encoding] = useState("");
  const [s3Sheet, setS3Sheet] = useState("");
  const [s3SourcePk, setS3SourcePk] = useState("");
  const [s3SourceSystem, setS3SourceSystem] = useState("");
  // UX ask — payload mode needs its own bronze table name, same field the standalone Imports
  // wizard offers (not the dataset's own "Nom" field above, which is a separate concept): the
  // FileImport row this creates has its own name, independent of what the MedallionDataset
  // ends up called.
  const [s3BronzeTableName, setS3BronzeTableName] = useState("");
  const [s3Processing, setS3Processing] = useState(false);
  const [s3Error, setS3Error] = useState("");
  const [s3Result, setS3Result] = useState(null);
  const [s3Validating, setS3Validating] = useState(null);
  const [s3PkCandidates, setS3PkCandidates] = useState([]);
  const [s3PkLoading, setS3PkLoading] = useState(false);

  // Client-side preview only — the backend re-derives this itself (schema_infer's own
  // slugifier) at import time; mirrors ImportWizardDrawer's own previewTableName so both entry
  // points show the exact same rule before commit.
  const previewTableName = (n) => {
    const ascii = (n || "").normalize("NFKD").replace(/[̀-ͯ]/g, "");
    const slug = ascii.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    return slug ? (/^[0-9]/.test(slug) ? `col_${slug}` : slug) : "col";
  };

  const s3FormatOptions = () => (s3ObjectFormat === "csv"
    ? { ...(s3Delimiter ? { delimiter: s3Delimiter } : {}), ...(s3Encoding ? { encoding: s3Encoding } : {}) }
    : (s3Sheet ? { sheet: s3Sheet } : {}));

  // Schema-on-Read has no other preview step (direct import, no inference) — this is the only
  // way to offer a source_pk candidate list before commit, same as the standalone wizard's own
  // pkCandidates effect (ImportWizardDrawer.jsx), just reading from the bucket instead of an
  // uploaded File object.
  useEffect(() => {
    if (!isS3Importable || s3ImportMode !== "payload" || !sourceObject) { setS3PkCandidates([]); return; }
    let cancelled = false;
    setS3PkLoading(true);
    const [bucket, ...rest] = sourceObject.split("/");
    medallionApi.getObjectStoreColumns(project.id, { source_id: Number(sourceId), bucket, key: rest.join("/"), format: s3ObjectFormat, format_options: s3FormatOptions() })
      .then((res) => { if (!cancelled) setS3PkCandidates(res.columns); })
      .catch(() => { if (!cancelled) setS3PkCandidates([]); })
      .finally(() => { if (!cancelled) setS3PkLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isS3Importable, s3ImportMode, sourceObject, s3ObjectFormat, s3Delimiter, s3Encoding, s3Sheet]);

  const applyS3ImportResult = (fi) => {
    setS3Result(fi);
    setSourceType("postgresql");
    setSourceId(fi.target_source_id);
    setSourceObject(`${fi.target_schema}.${fi.target_table}`);
  };

  const processS3File = async () => {
    setS3Processing(true);
    setS3Error("");
    try {
      const [bucket, ...rest] = sourceObject.split("/");
      const key = rest.join("/");
      const isPayload = s3ImportMode === "payload";
      const formatOptions = s3FormatOptions();
      if (isPayload && s3SourcePk.trim()) formatOptions.source_pk = s3SourcePk.trim();
      if (isPayload && s3SourceSystem.trim()) formatOptions.source_system = s3SourceSystem.trim();
      const fi = await medallionApi.importFromObjectStore(project.id, {
        source_id: Number(sourceId), bucket, key, format: s3ObjectFormat, format_options: formatOptions,
        name: (isPayload ? s3BronzeTableName.trim() : name.trim()) || undefined,
        import_mode: s3ImportMode, write_mode: isPayload ? s3WriteMode : "create",
      });
      if (fi.status === "awaiting_validation") setS3Validating(fi);
      else applyS3ImportResult(fi);
    } catch (err) {
      setS3Error(err.message || t("medallion.panel.s3ImportFailed"));
    } finally {
      setS3Processing(false);
    }
  };
  const selectedTemplate = templates.find((tp) => tp.id === selectedTemplateId) || null;
  // UX ask — an upstream may be an equal-or-lower layer (never strictly higher), same rule for
  // every transform type (mirrors validate_lineage, medallion_crud.py): a new silver can now
  // also build on another already-existing silver (chained staging→intermediate silvers), not
  // just on bronze — previously only python/ML gold nodes could chain same-layer.
  const upstreamCandidates = datasets.filter((d) => {
    if (d.id === dataset?.id) return false;
    return LAYER_ORDER[d.layer] - LAYER_ORDER[layer] <= 0;
  });

  const setLayerSafe = (l) => {
    setLayer(l);
    if (l !== "gold") setTransformType("dbt");
    if (l === "silver" && !sql.trim()) setSql("SELECT * ");
  };

  const isOutputField = (f) => f.is_output === true || f.key === "TABLE_SORTIE";

  const selectTemplate = (template) => {
    setSelectedTemplateId(template.id);
    setMlObjective(template.ml_objective);
    const defaults = {};
    for (const f of template.expected_inputs) if (f.default) defaults[f.key] = f.default;
    setTemplateValues(defaults);
    const outputField = template.expected_inputs.find(isOutputField);
    setOutputTable(outputField?.default || "");
    const code = substitutePlaceholders(template.python_code, template.expected_inputs, defaults);
    setPythonCode(code);
    setLastAutoCode(code);
  };

  const selectBlank = () => {
    setSelectedTemplateId(null);
    setMlObjective("none");
    setTemplateValues({});
    setOutputTable("");
    setPythonCode(BLANK_STARTER);
    setLastAutoCode(BLANK_STARTER);
  };

  const handleTemplateValueChange = (field, value) => {
    const newValues = { ...templateValues, [field.key]: value };
    setTemplateValues(newValues);
    if (isOutputField(field)) setOutputTable(value); // même champ que "Table de sortie (gold)" — évite la double saisie
    if (!selectedTemplate || pythonCode !== lastAutoCode) return; // édition manuelle déjà commencée : ne pas écraser
    const next = substitutePlaceholders(selectedTemplate.python_code, selectedTemplate.expected_inputs, newValues);
    setPythonCode(next);
    setLastAutoCode(next);
  };

  const toggleUpstream = (id) => {
    const adding = !upstreamIds.has(id);
    setUpstreamIds((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
    // UX: picking a payload-backed bronze as a silver's upstream is exactly the moment
    // structuration matters most — pop the profiling/quarantine panel open immediately
    // instead of making the engineer go find the bronze dataset's own tab afterwards.
    if (adding && layer === "silver") {
      const candidate = upstreamCandidates.find((d) => d.id === id);
      if (candidate?.layer === "bronze" && candidate.payload_backed) setStructurationPopupDataset(candidate);
    }
  };

  // Mirrors the backend's actual dbt referencing convention (dbt_project.py): bronze
  // datasets are declared as a dbt source keyed by their `name`; silver/gold dbt models
  // are files named after `dbt_model_name` and referenced via ref(); gold ML (python)
  // outputs are declared as a separate `gold_ml` source keyed by `output_table`. A payload
  // bronze dataset with a saved structuration contract is the one exception (Module 18 §7.5):
  // its own source table is just the raw audit/payload shape, so downstream SQL should read
  // its `05_validated_<name>` model instead — the clean, routed output of the full
  // 01..05 chain, never 04_annotated nor the bronze directly.
  // columnsByDataset[d.id].structured (set by getDatasetColumns) is what tells us that
  // contract exists.
  const referenceSnippetFor = (d) => {
    if (d.layer === "bronze") {
      if (columnsByDataset[d.id]?.structured) return `{{ ref('05_validated_${d.name}') }}`;
      return `{{ source('bronze', '${d.name}') }}`;
    }
    if (d.transform_type === "python") return `{{ source('gold_ml', '${d.output_table || d.name}') }}`;
    return `{{ ref('${d.dbt_model_name || d.name}') }}`;
  };

  // UX ask — "Upstreams (lignée)" only ever showed the bare bronze checkbox, even when the SQL
  // actually reads a specific stage (02_typed, 01_unpacked) rather than the bronze directly —
  // this reads that back out of the live SQL text so the section shows the real relationship,
  // not just "this bronze is involved somehow". Same detection LineageCanvas.jsx's
  // rerouteThroughStage uses to draw the canvas edge through that same stage node.
  const detectUpstreamStage = (d) => {
    if (d.layer !== "bronze" || !columnsByDataset[d.id]?.structured) return null;
    return stageRefPattern(d.name).exec(sql)?.[1] || null;
  };

  const sqlRef = useRef(null);
  const insertAtCursor = (text) => {
    const el = sqlRef.current;
    const start = el ? el.selectionStart ?? sql.length : sql.length;
    const end = el ? el.selectionEnd ?? sql.length : sql.length;
    setSql((s) => s.slice(0, start) + text + s.slice(end));
    if (el) {
      requestAnimationFrame(() => {
        const pos = start + text.length;
        el.focus();
        el.setSelectionRange(pos, pos);
      });
    }
  };
  const insertReference = (d) => {
    insertAtCursor(referenceSnippetFor(d));
    setUpstreamIds((s) => (s.has(d.id) ? s : new Set(s).add(d.id)));
  };
  // UX ask — a structured bronze's "Tables disponibles" entry only ever offered
  // 05_validated_<name> (the clean, routed output — the right default). Earlier stages
  // (01_unpacked/02_typed, §7's instant preview) are real, useful upstream shapes too — this
  // inserts one of those instead, same "clicking a reference marks the dependency" convention
  // as insertReference, just for a stage instead of the dataset's own default reference.
  const insertStageReference = (d, stageModelName) => {
    insertAtCursor(`{{ ref('${stageModelName}') }}`);
    setUpstreamIds((s) => (s.has(d.id) ? s : new Set(s).add(d.id)));
  };
  // UX ask — the Upstreams (lignée) stage checkboxes (01_unpacked/02_typed) pick ONE stage as
  // this bronze's source, unlike "Tables disponibles"' free insert-at-cursor: blindly inserting
  // a second stage's ref at the cursor left the first one's text still sitting in the SQL
  // (sometimes split apart by the insertion itself), so the SQL matched neither stage cleanly
  // afterward — the clicked checkbox never showed checked, and the canvas edge (which reads
  // this same SQL text) had nothing valid to reroute through either. Replaces any existing
  // 01_unpacked/02_typed/05_validated reference to this bronze with the new one instead;
  // inserts at cursor only when there's nothing to replace yet (a fresh/empty SQL box).
  const selectUpstreamStage = (d, stageModelName) => {
    const newRef = `{{ ref('${stageModelName}') }}`;
    // Global replace — collapses EVERY stage reference to this bronze down to the one just
    // picked, not just the first: self-heals a SQL box that already has more than one (e.g.
    // saved before this replace-based selection existed, when clicking a second stage could
    // leave the first one's ref still sitting in the text instead of removing it).
    const pattern = stageRefPattern(d.name, "g");
    if (pattern.test(sql)) setSql((s) => s.replace(stageRefPattern(d.name, "g"), newRef));
    else insertAtCursor(newRef);
    setUpstreamIds((s) => (s.has(d.id) ? s : new Set(s).add(d.id)));
  };
  const insertColumn = (columnName) => insertAtCursor(columnName);
  // A macro call, unlike a bare reference/column name, needs the {{ }} wrapper itself — the
  // built-in snippet already carries plausible placeholder args; a custom macro's snippet is
  // built from its own declared parameter names, so an author sees exactly what to fill in.
  const insertMacro = (snippet) => insertAtCursor(`{{ ${snippet} }}`);
  // Admin-authored macros carry their whole { % macro name(...) % } block (no separate
  // structured parameters) — pull the call signature straight out of it, same regex as
  // pages/settings/DbtMacros.jsx's own signature() display helper.
  const customMacroSnippet = (m) => /\{%-?\s*macro\s+([a-zA-Z_][a-zA-Z0-9_]*\([^)]*\))/.exec(m.definition || "")?.[1] || `${m.name}()`;

  // Live column lookup per candidate upstream table/model, to help write the SELECT list
  // without leaving the panel. Best-effort: empty for tables never yet loaded by a run.
  const [columnsByDataset, setColumnsByDataset] = useState({});
  const upstreamIdsKey = upstreamCandidates.map((d) => d.id).join(",");
  useEffect(() => {
    const missing = upstreamCandidates.filter((d) => !(d.id in columnsByDataset));
    if (!missing.length) return;
    setColumnsByDataset((prev) => {
      const next = { ...prev };
      for (const d of missing) next[d.id] = { loading: true, columns: [] };
      return next;
    });
    missing.forEach((d) => {
      medallionApi.getDatasetColumns(project.id, d.id)
        .then((res) => setColumnsByDataset((prev) => ({ ...prev, [d.id]: { loading: false, columns: res.columns, structured: res.structured } })))
        .catch(() => setColumnsByDataset((prev) => ({ ...prev, [d.id]: { loading: false, columns: [] } })));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [upstreamIdsKey]);

  // UX ask — 01_unpacked -> 02_typed -> 03_standardized is a fixed workflow (the "+" on a
  // 02_typed canvas node), never a pick-any-upstream situation: once the SQL reads one of
  // these two instant-preview stages for some bronze, that relationship is locked — "Upstreams"
  // shows just that one line (checked, disabled) instead of the full candidate list. 05_validated
  // isn't included here: that's the normal, still-freely-editable default reference every other
  // silver/gold dataset gets when it depends on a structured bronze.
  const lockedStageUpstream = (() => {
    for (const d of upstreamCandidates) {
      const stage = detectUpstreamStage(d);
      if (stage === "01_unpacked" || stage === "02_typed") return { modelName: `${stage}_${d.name}`, bronzeId: d.id };
    }
    return null;
  })();
  // The disabled checkbox above shows checked because the SQL reads this stage — keep
  // upstreamIds actually true to that (e.g. an existing dataset saved before this locked view
  // existed might have the right SQL but never got its bronze recorded as upstream).
  useEffect(() => {
    if (lockedStageUpstream) setUpstreamIds((s) => (s.has(lockedStageUpstream.bronzeId) ? s : new Set(s).add(lockedStageUpstream.bronzeId)));
  }, [lockedStageUpstream?.bronzeId]);

  const addTest = () => setTests((tt) => [...tt, { column: "", test: "not_null" }]);
  const updateTest = (i, patch) => setTests((tt) => tt.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  const removeTest = (i) => setTests((tt) => tt.filter((_, idx) => idx !== i));

  const valid = name.trim() && (isBronze ? sourceId && sourceObject.trim() : isPython ? pythonCode.trim() : dbtModelName.trim() && sql.trim());

  const buildPayload = () => ({
    layer,
    name: name.trim(),
    description: description.trim() || null,
    upstream_dataset_ids: [...upstreamIds],
    ...(isBronze
      ? { source_id: Number(sourceId), source_object: sourceObject.trim(), load_mode: loadMode, incremental_key: loadMode === "incremental" ? incrementalKey.trim() || null : null, tests: [] }
      : isPython
      ? { transform_type: "python", ml_objective: mlObjective, python_code: pythonCode, output_table: outputTable.trim() || null, template_id: selectedTemplateId, tests }
      : { transform_type: "dbt", dbt_model_name: dbtModelName.trim(), materialization, sql: sql.trim(), tests }),
  });

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      if (isEdit) {
        const updated = await medallionApi.updateDataset(project.id, dataset.id, buildPayload());
        onSaved(updated);
      } else {
        const created = await medallionApi.createDataset(project.id, buildPayload());
        onSaved(created);
      }
    } catch (err) {
      setError(err.message || t("medallion.panel.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await medallionApi.deleteDataset(project.id, dataset.id);
      onDeleted(dataset.id);
    } catch (err) {
      setError(err.message || t("medallion.panel.deleteFailed"));
      setBusy(false);
    }
  };

  const title = readOnly
    ? t("medallion.panel.viewTitle", { name: dataset.name })
    : isEdit ? t("medallion.panel.editTitle", { name: dataset.name }) : t("medallion.panel.addTitle");

  return (
    <>
    <Drawer title={title} description={t("medallion.panel.layerDesc", { layer })} onClose={onClose}>
      {isEdit && (
        <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
          <button type="button" className="btn-ghost" style={{ opacity: panelTab === "config" ? 1 : 0.6 }} onClick={() => setPanelTab("config")}>
            {t("medallion.panel.tabConfig")}
          </button>
          <button type="button" className="btn-ghost" style={{ opacity: panelTab === "preview" ? 1 : 0.6 }} onClick={() => setPanelTab("preview")}>
            {Icon.eye()} {t("medallion.panel.tabPreview")}
          </button>
          {dataset.layer === "bronze" && (
            <button type="button" className="btn-ghost" style={{ opacity: panelTab === "structuration" ? 1 : 0.6 }} onClick={() => setPanelTab("structuration")}>
              {Icon.wand()} {t("medallion.structuration.tab")}
            </button>
          )}
          {dataset.layer === "gold" && (
            <button type="button" className="btn-ghost" style={{ opacity: panelTab === "publish" ? 1 : 0.6 }} onClick={() => setPanelTab("publish")}>
              {Icon.upload()} {t("medallion.publish.tab")}
            </button>
          )}
          {dataset.layer === "gold" && (
            <button type="button" className="btn-ghost" style={{ opacity: panelTab === "indicators" ? 1 : 0.6 }} onClick={() => setPanelTab("indicators")}>
              {Icon.wand()} {t("medallion.suggest.tab")}
            </button>
          )}
        </div>
      )}

      {panelTab === "preview" && isEdit ? (
        <DataPreviewPanel project={project} datasetId={dataset.id} dataset={dataset} />
      ) : panelTab === "structuration" && isEdit ? (
        <StructurationPanel project={project} dataset={dataset} readOnly={readOnly} />
      ) : panelTab === "publish" && isEdit ? (
        <PublishPanel project={project} dataset={dataset} readOnly={readOnly} />
      ) : panelTab === "indicators" && isEdit ? (
        <IndicatorsPanel project={project} dataset={dataset} readOnly={readOnly} />
      ) : (
      <>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <form onSubmit={submit}>
      <fieldset disabled={readOnly} style={{ border: "none", margin: 0, padding: 0 }}>
        {!isEdit && (
          <Field label={t("medallion.panel.layer")}>
            <div className="seg">
              {["bronze", "silver", "gold"].map((l) => (
                <button key={l} type="button" className={"seg-opt" + (layer === l ? " selected" : "")} onClick={() => setLayerSafe(l)}>
                  <div className="seg-role">{l}</div>
                </button>
              ))}
            </div>
          </Field>
        )}

        {layer === "gold" && (
          <Field label={t("medallion.panel.transformType")}>
            <div className="seg">
              <button type="button" className={"seg-opt" + (transformType === "dbt" ? " selected" : "")} onClick={() => setTransformType("dbt")}>
                <div className="seg-role">{t("medallion.panel.dbtSql")}</div>
              </button>
              <button type="button" className={"seg-opt" + (transformType === "python" ? " selected" : "")} onClick={() => setTransformType("python")}>
                <div className="seg-role">{t("medallion.panel.mlCalc")}</div>
              </button>
            </div>
          </Field>
        )}

        <Field label={t("medallion.panel.name")}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={isBronze ? "customers" : "stg_customers"} />
        </Field>

        {isBronze ? (
          <>
            <Field label={t("medallion.panel.sourceType")}>
              <select className="input" value={sourceType} onChange={(e) => handleSourceTypeChange(e.target.value)}>
                <option value="">{t("medallion.panel.chooseSourceType")}</option>
                {availableSourceTypes.map((ty) => <option key={ty} value={ty}>{SOURCE_TYPE_LABEL[ty] || ty}</option>)}
              </select>
            </Field>

            <Field label={t("medallion.panel.source")}>
              <select className="input" value={sourceId} onChange={(e) => setSourceId(e.target.value)} disabled={!sourceType}>
                <option value="">{sourceType ? t("medallion.panel.chooseSource") : t("medallion.panel.chooseSourceTypeFirst")}</option>
                {sourcesOfType.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>

            {browsing && (
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", margin: "-8px 0 10px" }}>{t("medallion.panel.browsingObjects")}</div>
            )}
            {!browsing && !browseBucket && browseBuckets.length > 0 && (
              <Field label={t("medallion.panel.chooseBucket")}>
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  {browseBuckets.map((b) => (
                    <button
                      key={b} type="button" className="card"
                      style={{ textAlign: "left", padding: "6px 10px", cursor: "pointer", fontFamily: "var(--font-m)", fontSize: 12.5, border: "1px solid var(--border)" }}
                      onClick={() => loadBucketObjects(b)}
                    >
                      {b}
                    </button>
                  ))}
                </div>
              </Field>
            )}
            {!browsing && browseBucket && browseObjects.length === 0 && (
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", margin: "-8px 0 10px" }}>
                {t("medallion.panel.noBrowsableFiles", { bucket: browseBucket })}
                {browseBuckets.length > 1 && (
                  <> {" · "}<button type="button" className="link" onClick={changeBrowseBucket}>{t("medallion.panel.changeBucket")}</button></>
                )}
              </div>
            )}
            {!browsing && browseObjects.length > 0 && (
              <Field label={t("medallion.panel.browseObjects")}>
                {browseBuckets.length > 1 && (
                  <div style={{ fontSize: 11.5, marginBottom: 6 }}>
                    <span style={{ color: "var(--text-muted)" }}>{browseBucket}</span>
                    {" · "}
                    <button type="button" className="link" onClick={changeBrowseBucket}>{t("medallion.panel.changeBucket")}</button>
                  </div>
                )}
                <input
                  className="input" placeholder={t("medallion.panel.browseFilter")}
                  value={browseFilter} onChange={(e) => setBrowseFilter(e.target.value)}
                />
                <div style={{ marginTop: 6, maxHeight: 160, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
                  {browseObjects
                    .filter((o) => o.toLowerCase().includes(browseFilter.toLowerCase()))
                    .map((o) => (
                      <button
                        key={o} type="button" className="card"
                        style={{
                          textAlign: "left", padding: "5px 10px", cursor: "pointer", fontFamily: "var(--font-m)", fontSize: 12,
                          border: sourceObject === `${browseBucket}/${o}` ? "1.5px solid var(--ember)" : "1px solid var(--border)",
                        }}
                        onClick={() => pickBrowsedObject(o)}
                      >
                        {o}
                      </button>
                    ))}
                </div>
              </Field>
            )}
            {isS3Importable && !s3Result && (
              <Field label={t("medallion.panel.s3ImportTitle")}>
                <div className="card" style={{ padding: 12, border: "1px solid var(--border)" }}>
                  {s3Error && <div className="error-banner" style={{ marginBottom: 10 }}>{Icon.warn()}<span>{s3Error}</span></div>}
                  <div className="seg">
                    <button type="button" className={"seg-opt" + (s3ImportMode === "typed" ? " selected" : "")} onClick={() => setS3ImportMode("typed")}>
                      <div className="seg-role">{t("imports.wizard.modeTyped")}</div>
                    </button>
                    <button type="button" className={"seg-opt" + (s3ImportMode === "payload" ? " selected" : "")} onClick={() => setS3ImportMode("payload")}>
                      <div className="seg-role">{t("imports.wizard.modePayload")}</div>
                    </button>
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", margin: "6px 0 10px" }}>
                    {s3ImportMode === "typed" ? t("imports.wizard.modeTypedHelp") : t("imports.wizard.modePayloadHelp")}
                  </div>

                  {s3ObjectFormat === "csv" ? (
                    <div style={{ display: "flex", gap: 10, marginBottom: 10 }}>
                      <input className="input" style={{ flex: 1 }} placeholder={t("imports.wizard.delimiter") + " (,)"} value={s3Delimiter} onChange={(e) => setS3Delimiter(e.target.value)} />
                      <input className="input" style={{ flex: 1 }} placeholder={t("imports.wizard.encoding") + " (utf-8)"} value={s3Encoding} onChange={(e) => setS3Encoding(e.target.value)} />
                    </div>
                  ) : (
                    <input className="input" style={{ marginBottom: 10 }} placeholder={t("imports.wizard.sheet") + " (0)"} value={s3Sheet} onChange={(e) => setS3Sheet(e.target.value)} />
                  )}

                  {s3ImportMode === "payload" && (
                    <>
                      <select className="input" style={{ marginBottom: 10 }} value={s3WriteMode} onChange={(e) => setS3WriteMode(e.target.value)}>
                        <option value="create">{t("imports.modal.writeModeCreate")}</option>
                        <option value="replace">{t("imports.modal.writeModeReplace")}</option>
                        <option value="append">{t("imports.modal.writeModeAppend")}</option>
                      </select>
                      <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 4 }}>
                        {t("imports.wizard.sourcePk")}
                      </div>
                      {s3PkLoading && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("imports.wizard.sourcePkAnalyzing")}</div>}
                      {s3PkCandidates.length > 0 && (
                        <select
                          multiple className="input" style={{ marginBottom: 8, minHeight: 90 }}
                          value={s3SourcePk ? s3SourcePk.split(",").map((s) => s.trim()).filter(Boolean) : []}
                          onChange={(e) => setS3SourcePk(Array.from(e.target.selectedOptions, (o) => o.value).join(","))}
                        >
                          {s3PkCandidates.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      )}
                      <input className="input" style={{ marginBottom: 4 }} placeholder={t("imports.wizard.sourcePkPlaceholder")} value={s3SourcePk} onChange={(e) => setS3SourcePk(e.target.value)} />
                      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 10 }}>{t("imports.wizard.sourcePkHelp")}</div>
                      <input className="input" style={{ marginBottom: 10 }} placeholder={t("imports.wizard.sourceSystemPlaceholder")} value={s3SourceSystem} onChange={(e) => setS3SourceSystem(e.target.value)} />
                      <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 4 }}>
                        {t("imports.wizard.bronzeTableName")}
                      </div>
                      <input
                        className="input" style={{ marginBottom: 4 }} value={s3BronzeTableName}
                        onChange={(e) => setS3BronzeTableName(e.target.value)}
                        placeholder={previewTableName(sourceObject.split("/").pop() || "")}
                      />
                      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 10, fontFamily: "var(--font-m)" }}>
                        {previewTableName(s3BronzeTableName.trim() || sourceObject.split("/").pop() || "")}
                      </div>
                    </>
                  )}

                  <Button type="button" variant="ghost" className="inline" disabled={s3Processing} onClick={processS3File}>
                    {s3Processing ? t("medallion.panel.s3ImportProcessing") : t("medallion.panel.s3ImportAction")}
                  </Button>
                </div>
              </Field>
            )}
            {s3Result && (
              <div className="error-banner" style={{ background: "rgba(47,158,110,.08)", borderColor: "rgba(47,158,110,.3)", color: "#2f9e6e", marginBottom: 14 }}>
                {Icon.check()}<span>{t("medallion.panel.s3ImportDone", { table: `${s3Result.target_schema}.${s3Result.target_table}` })}</span>
              </div>
            )}
            {!browsing && browseTables.length > 0 && (
              <Field label={t("medallion.panel.browseTables")}>
                <input
                  className="input" placeholder={t("medallion.panel.browseFilterTables")}
                  value={browseFilter} onChange={(e) => setBrowseFilter(e.target.value)}
                />
                <div style={{ marginTop: 6, maxHeight: 160, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
                  {browseTables
                    .filter((tbl) => tbl.toLowerCase().includes(browseFilter.toLowerCase()))
                    .map((tbl) => (
                      <button
                        key={tbl} type="button" className="card"
                        style={{
                          textAlign: "left", padding: "5px 10px", cursor: "pointer", fontFamily: "var(--font-m)", fontSize: 12,
                          border: sourceObject === tbl ? "1.5px solid var(--ember)" : "1px solid var(--border)",
                        }}
                        onClick={() => pickBrowsedTable(tbl)}
                      >
                        {tbl}
                      </button>
                    ))}
                </div>
              </Field>
            )}

            <Field label={t("medallion.panel.sourceTable")}>
              <Input value={sourceObject} onChange={(e) => setSourceObject(e.target.value)} placeholder="schema.table ou bucket/prefix" />
            </Field>
            <Field label={t("medallion.panel.loadMode")}>
              <select className="input" value={loadMode} onChange={(e) => setLoadMode(e.target.value)}>
                <option value="full">{t("medallion.panel.loadModeFull")}</option>
                <option value="incremental">{t("medallion.panel.loadModeIncremental")}</option>
                <option value="append">{t("medallion.panel.loadModeAppend")}</option>
              </select>
            </Field>
            {loadMode === "incremental" && (
              <Field label={t("medallion.panel.incrementalKey")}>
                <Input value={incrementalKey} onChange={(e) => setIncrementalKey(e.target.value)} placeholder="updated_at" />
              </Field>
            )}
          </>
        ) : isPython ? (
          <>
            {!isEdit ? (
              <>
                <Field label={t("medallion.panel.chooseTemplate")}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {ML_OBJECTIVES.filter((o) => o.value !== "none").map((o) => {
                      const group = templates.filter((tp) => tp.ml_objective === o.value);
                      if (!group.length) return null;
                      return (
                        <div key={o.value}>
                          <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 4 }}>{o.label}</div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                            {group.map((tp) => (
                              <button
                                key={tp.id} type="button" className="card"
                                style={{ textAlign: "left", padding: "8px 10px", cursor: "pointer", border: selectedTemplateId === tp.id ? "1.5px solid var(--ember)" : "1px solid var(--border)" }}
                                onClick={() => selectTemplate(tp)}
                              >
                                <div style={{ fontWeight: 600, fontSize: 12.5 }}>{tp.name}</div>
                                {tp.description && <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>{tp.description}</div>}
                              </button>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                    <button
                      type="button" className="card"
                      style={{ textAlign: "left", padding: "8px 10px", cursor: "pointer", border: selectedTemplateId === null ? "1.5px solid var(--ember)" : "1px solid var(--border)" }}
                      onClick={selectBlank}
                    >
                      <div style={{ fontWeight: 600, fontSize: 12.5 }}>{t("medallion.panel.blank")}</div>
                      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>{t("medallion.panel.blankDesc")}</div>
                    </button>
                  </div>
                </Field>

                {selectedTemplate && (
                  <Field label={t("medallion.panel.templateParams")}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {selectedTemplate.expected_inputs.map((f) => (
                        <div key={f.key}>
                          <label style={{ fontSize: 11.5, color: "var(--text-muted)", display: "block", marginBottom: 3 }}>{f.label}</label>
                          <Input
                            value={templateValues[f.key] || ""}
                            placeholder={f.default || (f.type === "column_list" ? "col1, col2" : "")}
                            onChange={(e) => handleTemplateValueChange(f, e.target.value)}
                          />
                        </div>
                      ))}
                    </div>
                  </Field>
                )}
              </>
            ) : (
              <Field label={t("medallion.panel.mlObjective")}>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {ML_OBJECTIVES.map((o) => (
                    <button
                      key={o.value} type="button"
                      className={"badge" + (mlObjective === o.value ? " badge-accent" : " badge-neutral")}
                      style={{ cursor: "pointer", border: "none" }}
                      onClick={() => setMlObjective(o.value)}
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
              </Field>
            )}

            {!selectedTemplate?.expected_inputs.some(isOutputField) && (
              <Field label={t("medallion.panel.outputTable")}>
                <Input value={outputTable} onChange={(e) => setOutputTable(e.target.value)} placeholder={name.trim() || "nom_par_defaut"} />
              </Field>
            )}

            <Field label={t("medallion.panel.pythonCode")}>
              <textarea
                className="input" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, minHeight: 220, resize: "vertical" }}
                value={pythonCode} onChange={(e) => setPythonCode(e.target.value)}
                placeholder={`df = read_table("stg_table")\n...\nwrite_table(df, "nom_table_sortie")`}
              />
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>
                {t("medallion.panel.codeContractPre")} <code>read_table(nom)</code> {t("medallion.panel.codeContractMid")} <code>write_table(df, nom)</code> {t("medallion.panel.codeContractPost")}
              </div>
            </Field>

            <Field label={t("medallion.panel.upstreams")}>
              {upstreamCandidates.length === 0 ? (
                <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.panel.noUpstreamsEqual")}</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {upstreamCandidates.map((d) => (
                    <label key={d.id} className="service-tile-checkline">
                      <input type="checkbox" checked={upstreamIds.has(d.id)} onChange={() => toggleUpstream(d.id)} />
                      <span style={{ fontSize: 12.5 }}>{d.name} <span style={{ color: "var(--text-muted)" }}>({d.layer})</span></span>
                    </label>
                  ))}
                </div>
              )}
            </Field>

            <Field label={t("medallion.panel.testsOnOutput")}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {tests.map((tItem, i) => (
                  <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input className="input" style={{ flex: 1 }} placeholder={t("medallion.panel.column")} value={tItem.column} onChange={(e) => updateTest(i, { column: e.target.value })} />
                    <select className="input" style={{ flex: 1 }} value={tItem.test} onChange={(e) => updateTest(i, { test: e.target.value })}>
                      {TEST_TYPES.map((tt) => <option key={tt} value={tt}>{tt}</option>)}
                    </select>
                    <button type="button" className="btn-icon" onClick={() => removeTest(i)}>{Icon.trash()}</button>
                  </div>
                ))}
                <Button type="button" variant="ghost" className="inline" onClick={addTest}>{Icon.plus()} {t("medallion.panel.addTest")}</Button>
              </div>
            </Field>
          </>
        ) : (
          <>
            <Field label={t("medallion.panel.dbtModelName")}>
              <Input value={dbtModelName} onChange={(e) => setDbtModelName(e.target.value)} />
            </Field>
            <Field label={t("medallion.panel.materialization")}>
              <select className="input" value={materialization} onChange={(e) => setMaterialization(e.target.value)}>
                <option value="view">View</option>
                <option value="table">Table</option>
                <option value="incremental">Incremental</option>
              </select>
            </Field>
            <Field label={t("medallion.panel.selectSql")}>
              {upstreamCandidates.length > 0 && (
                <div style={{ marginBottom: 8, display: "flex", flexDirection: "column", gap: 4, maxHeight: 160, overflowY: "auto" }}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: ".04em" }}>
                    {t("medallion.panel.availableTables")}
                  </div>
                  {upstreamCandidates.map((d) => {
                    const colState = columnsByDataset[d.id];
                    return (
                      <div key={d.id} className="card" style={{ padding: "6px 10px", border: "1px solid var(--border)" }}>
                        <button
                          type="button"
                          style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", textAlign: "left", width: "100%", padding: 0, border: "none", background: "transparent", cursor: "pointer" }}
                          onClick={() => insertReference(d)}
                          title={t("medallion.panel.insertReferenceHint")}
                        >
                          <span style={{ fontSize: 12, whiteSpace: "nowrap" }}>{d.name} <span style={{ color: "var(--text-muted)" }}>({d.layer})</span></span>
                          <code style={{ fontSize: 11, color: "var(--ember)", overflow: "hidden", textOverflow: "ellipsis" }}>{referenceSnippetFor(d)}</code>
                        </button>
                        {colState?.loading ? (
                          <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 4 }}>{t("medallion.panel.columnsLoading")}</div>
                        ) : colState?.columns.length ? (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                            {colState.columns.map((c) => (
                              <button
                                key={c.column} type="button"
                                className="badge badge-neutral"
                                style={{ cursor: "pointer", border: "none", fontSize: 10.5, fontFamily: "var(--font-m)" }}
                                onClick={() => insertColumn(c.column)}
                                title={c.type}
                              >
                                {c.column}
                              </button>
                            ))}
                          </div>
                        ) : colState ? (
                          <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>{t("medallion.panel.columnsUnavailable")}</div>
                        ) : null}
                        {d.layer === "bronze" && colState?.structured && (
                          // UX ask — 05_validated_<name> above is the recommended default, but
                          // the instant-preview stages (§7) are real, buildable upstreams too —
                          // same columns (target_name is stable across 01/02/05), just a
                          // different reference to insert. Reuses colState's columns as-is
                          // rather than a second fetch: names don't change stage to stage.
                          <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)", display: "flex", flexDirection: "column", gap: 6 }}>
                            {[["01_unpacked", "unpacked"], ["02_typed", "typed"]].map(([prefix, stageKey]) => {
                              const modelName = `${prefix}_${d.name}`;
                              return (
                                <div key={stageKey}>
                                  <button
                                    type="button"
                                    style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", textAlign: "left", width: "100%", padding: 0, border: "none", background: "transparent", cursor: "pointer" }}
                                    onClick={() => insertStageReference(d, modelName)}
                                    title={t("medallion.structuration.instantPreviewHint")}
                                  >
                                    <span style={{ fontSize: 11.5, whiteSpace: "nowrap", color: "var(--text-muted)" }}>
                                      {modelName} <span>({t("medallion.panel.instantPreviewBadge")})</span>
                                    </span>
                                    <code style={{ fontSize: 10.5, color: "var(--ember)", overflow: "hidden", textOverflow: "ellipsis" }}>{`{{ ref('${modelName}') }}`}</code>
                                  </button>
                                  {colState.columns.length > 0 && (
                                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                                      {colState.columns.map((c) => (
                                        <button
                                          key={c.column} type="button"
                                          className="badge badge-neutral"
                                          style={{ cursor: "pointer", border: "none", fontSize: 10.5, fontFamily: "var(--font-m)" }}
                                          onClick={() => insertColumn(c.column)}
                                          title={c.type}
                                        >
                                          {c.column}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              <div style={{ marginBottom: 8, display: "flex", flexDirection: "column", gap: 4, maxHeight: 160, overflowY: "auto" }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: ".04em" }}>
                  {t("medallion.panel.availableMacros")}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {customMacros.map((m) => (
                    <button
                      key={`custom-${m.id}`} type="button" className="badge badge-accent"
                      style={{ cursor: "pointer", border: "none", fontSize: 10.5, fontFamily: "var(--font-m)" }}
                      onClick={() => insertMacro(customMacroSnippet(m))}
                      title={m.description || ""}
                    >
                      {m.name}
                    </button>
                  ))}
                  {BUILTIN_MACROS.map((m) => (
                    <button
                      key={`builtin-${m.name}`} type="button" className="badge badge-neutral"
                      style={{ cursor: "pointer", border: "none", fontSize: 10.5, fontFamily: "var(--font-m)" }}
                      onClick={() => insertMacro(m.insert)}
                      title={t(`medallion.macros.builtin_${m.descriptionKey}`)}
                    >
                      {m.name}
                    </button>
                  ))}
                </div>
              </div>
              <textarea
                ref={sqlRef}
                className="input" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, minHeight: 140, resize: "vertical" }}
                value={sql} onChange={(e) => { setSql(e.target.value); setSqlValidation(null); }}
                placeholder={`SELECT * FROM {{ source('bronze', 'table') }}`}
              />
              {!readOnly && (
                <div style={{ marginTop: 8 }}>
                  <button type="button" className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} disabled={validatingSql || !sql.trim()} onClick={validateSql}>
                    {Icon.check({ width: 13, height: 13 })} {validatingSql ? t("medallion.panel.sqlValidating") : t("medallion.panel.sqlValidate")}
                  </button>
                  {sqlValidation && (
                    <div
                      className="error-banner"
                      style={{
                        marginTop: 8, background: sqlValidation.valid ? "rgba(47,158,110,.08)" : undefined,
                        borderColor: sqlValidation.valid ? "rgba(47,158,110,.3)" : undefined, color: sqlValidation.valid ? "#2f9e6e" : undefined,
                      }}
                    >
                      {sqlValidation.valid ? Icon.check() : Icon.warn()}
                      <span>{sqlValidation.valid ? t("medallion.panel.sqlValidateOk") : sqlValidation.message}</span>
                    </div>
                  )}
                </div>
              )}
            </Field>

            <Field label={t("medallion.panel.upstreams")}>
              {lockedStageUpstream ? (
                // UX ask — 01_unpacked -> 02_typed -> 03_standardized is a fixed workflow: once
                // this SQL reads one of those two instant-preview stages for some bronze, THAT
                // is the relationship, full stop — not one candidate among a whole list of
                // unrelated bronzes/silvers to pick from. Shown checked and disabled (the real
                // dependency, the underlying bronze's id, is already in upstreamIds from the
                // "+" prefill or an earlier selectUpstreamStage call — nothing to toggle here);
                // editing the SQL directly is still the way to change it.
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <label className="service-tile-checkline" style={{ opacity: 0.85 }}>
                    <input type="checkbox" checked readOnly disabled />
                    <span style={{ fontSize: 12.5 }}>
                      {lockedStageUpstream.modelName} <span style={{ color: "var(--text-muted)" }}>({t("medallion.panel.instantPreviewBadge")})</span>
                    </span>
                  </label>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 22 }}>{t("medallion.panel.upstreamLockedHint")}</div>
                </div>
              ) : upstreamCandidates.length === 0 ? (
                <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.panel.noUpstreamsLower")}</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {upstreamCandidates.map((d) => (
                    <label key={d.id} className="service-tile-checkline">
                      <input type="checkbox" checked={upstreamIds.has(d.id)} onChange={() => toggleUpstream(d.id)} />
                      <span style={{ fontSize: 12.5 }}>{d.name} <span style={{ color: "var(--text-muted)" }}>({d.layer})</span></span>
                    </label>
                  ))}
                </div>
              )}
            </Field>

            <Field label={t("medallion.panel.tests")}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {tests.map((tItem, i) => (
                  <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input className="input" style={{ flex: 1 }} placeholder={t("medallion.panel.column")} value={tItem.column} onChange={(e) => updateTest(i, { column: e.target.value })} />
                    <select className="input" style={{ flex: 1 }} value={tItem.test} onChange={(e) => updateTest(i, { test: e.target.value })}>
                      {TEST_TYPES.map((tt) => <option key={tt} value={tt}>{tt}</option>)}
                    </select>
                    <button type="button" className="btn-icon" onClick={() => removeTest(i)}>{Icon.trash()}</button>
                  </div>
                ))}
                <Button type="button" variant="ghost" className="inline" onClick={addTest}>{Icon.plus()} {t("medallion.panel.addTest")}</Button>
              </div>
            </Field>
          </>
        )}

        <Field label={t("medallion.panel.description")}>
          <textarea className="input" style={{ minHeight: 60, resize: "vertical" }} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </fieldset>

        <div className="modal-actions">
          {readOnly ? (
            <Button type="button" variant="ghost" onClick={onClose}>{t("common.close")}</Button>
          ) : (
            <>
              {isEdit && <button type="button" className="btn-icon" onClick={remove} disabled={busy} title={t("common.delete")}>{Icon.trash()}</button>}
              <Button type="button" variant="ghost" onClick={onClose}>{t("medallion.panel.cancel")}</Button>
              <Button type="submit" disabled={!valid || busy}>{busy ? t("medallion.panel.saving") : t("medallion.panel.save")}</Button>
            </>
          )}
        </div>
      </form>
      </>
      )}
    </Drawer>
    {structurationPopupDataset && (
      <StructurationPopup
        project={project} dataset={structurationPopupDataset} guided
        onClose={() => setStructurationPopupDataset(null)}
        onFinish={() => {
          // Module 18 §7 UX — the moment the guided contract is saved, hand control straight
          // to the SQL box: a fresh "SELECT * " gets a real FROM (the reference this bronze's
          // structuration always produces — see referenceSnippetFor), an already-edited query
          // just gets the reference inserted at the cursor, same as clicking the table below.
          const d = structurationPopupDataset;
          setStructurationPopupDataset(null);
          onStructurationSaved?.();
          if (!d) return;
          const ref = `{{ ref('05_validated_${d.name}') }}`;
          if (sql.trim() === "SELECT *") setSql(`SELECT *\nFROM ${ref}\n`);
          else insertAtCursor(ref);
          requestAnimationFrame(() => sqlRef.current?.focus());
        }}
      />
    )}
    {s3Validating && (
      <SchemaValidationModal
        fileImport={s3Validating}
        onClose={() => setS3Validating(null)}
        onValidated={(fi) => { setS3Validating(null); applyS3ImportResult(fi); }}
      />
    )}
    </>
  );
}
