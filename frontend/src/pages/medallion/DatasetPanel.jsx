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
import { BUILTIN_MACROS } from "./builtinMacros.js";

const LAYER_ORDER = { bronze: 0, silver: 1, gold: 2 };
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
              {upstreamCandidates.length === 0 ? (
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
    </>
  );
}
