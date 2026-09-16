import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import AnnotationField from "../../components/AnnotationField.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import * as sourcesApi from "../../api/sources.js";

const annotationKey = (tableName, columnName) => `${tableName}::${columnName || ""}`;

export default function ExplorerDrawer({ source, onClose }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  // Semantic annotations (Module 13 §3) — inline description per table/column, batched save.
  const [annotations, setAnnotations] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [columnsByTable, setColumnsByTable] = useState({});

  const isMinio = source.type === "minio";

  const loadAnnotations = () => sourcesApi.listAnnotations(source.id).then(setAnnotations).catch(() => {});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    sourcesApi
      .introspectSource(source.id)
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err) => { if (!cancelled) setError(err.message || t("sources.explorer.introspectFailed")); })
      .finally(() => { if (!cancelled) setLoading(false); });
    if (!isMinio) loadAnnotations();
    return () => { cancelled = true; };
  }, [source.id]);

  const annotationByKey = useMemo(() => {
    const map = {};
    for (const a of annotations) map[annotationKey(a.table_name, a.column_name)] = a;
    return map;
  }, [annotations]);

  const dirtyCount = Object.keys(drafts).length;

  const valueFor = (tableName, columnName) => {
    const key = annotationKey(tableName, columnName);
    if (key in drafts) return drafts[key];
    return annotationByKey[key]?.description || "";
  };

  const setDraft = (tableName, columnName, value) => {
    const key = annotationKey(tableName, columnName);
    const persisted = annotationByKey[key]?.description || "";
    setDrafts((d) => {
      const next = { ...d };
      if (value === persisted) delete next[key];
      else next[key] = value;
      return next;
    });
  };

  const toggleExpand = async (tableKey, schemaName, tableOnlyName) => {
    setExpanded((e) => ({ ...e, [tableKey]: !e[tableKey] }));
    if (!columnsByTable[tableKey]) {
      setColumnsByTable((c) => ({ ...c, [tableKey]: "loading" }));
      try {
        const cols = await sourcesApi.listTableColumns(source.id, schemaName, tableOnlyName);
        setColumnsByTable((c) => ({ ...c, [tableKey]: cols }));
      } catch {
        setColumnsByTable((c) => ({ ...c, [tableKey]: "error" }));
      }
    }
  };

  const saveDrafts = async () => {
    const items = Object.entries(drafts)
      .filter(([, value]) => value.trim().length > 0)
      .map(([key, value]) => {
        const [tableName, columnName] = key.split("::");
        return { table_name: tableName, column_name: columnName || null, description: value.trim() };
      });
    if (items.length === 0) { setDrafts({}); return; }
    setSaving(true);
    try {
      await sourcesApi.upsertAnnotations(source.id, items);
      showToast(t("sources.explorer.descriptionsSaved"));
      setDrafts({});
      await loadAnnotations();
    } catch (err) {
      showToast(err.message || t("sources.explorer.descriptionsSaveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const removeAnnotation = async (tableName, columnName) => {
    const existing = annotationByKey[annotationKey(tableName, columnName)];
    if (!existing) return;
    try {
      await sourcesApi.deleteAnnotation(source.id, existing.id);
      await loadAnnotations();
    } catch (err) {
      showToast(err.message || t("sources.explorer.deleteAnnotationFailed"));
    }
  };

  const filteredSchemas = useMemo(() => {
    if (!data?.schemas) return [];
    const f = filter.trim().toLowerCase();
    if (!f) return data.schemas;
    return data.schemas
      .map((s) => ({ ...s, tables: s.tables.filter((t) => t.name.toLowerCase().includes(f)) }))
      .filter((s) => s.name.toLowerCase().includes(f) || s.tables.length > 0);
  }, [data, filter]);

  const filteredBuckets = useMemo(() => {
    if (!data?.buckets) return [];
    const f = filter.trim().toLowerCase();
    return f ? data.buckets.filter((b) => b.toLowerCase().includes(f)) : data.buckets;
  }, [data, filter]);

  const filteredObjects = useMemo(() => {
    if (!data?.objects) return [];
    const f = filter.trim().toLowerCase();
    return f ? data.objects.filter((o) => o.toLowerCase().includes(f)) : data.objects;
  }, [data, filter]);

  return (
    <Drawer title={t("sources.explorer.title", { name: source.name })} description={isMinio ? (data?.objects?.length > 0 ? t("sources.explorer.objectsDesc") : t("sources.explorer.bucketsDesc")) : t("sources.explorer.schemasDesc")} onClose={onClose}>
      <Input
        placeholder={isMinio ? (data?.objects?.length > 0 ? t("sources.explorer.filterObjects") : t("sources.explorer.filterBuckets")) : t("sources.explorer.filterSchemas")}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        icon={Icon.db()}
      />

      <div style={{ marginTop: 18 }}>
        {loading && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>}
        {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

        {!loading && !error && isMinio && data?.objects?.length > 0 && (
          filteredObjects.length === 0
            ? <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("sources.explorer.noObjects")}</div>
            : filteredObjects.map((o) => (
                <div key={o} className="card" style={{ padding: "10px 14px", marginBottom: 8, display: "flex", alignItems: "center", gap: 10 }}>
                  {Icon.db({ width: 16, height: 16, style: { color: "var(--text-muted)" } })}
                  <span style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>{data.buckets[0]}/{o}</span>
                </div>
              ))
        )}

        {!loading && !error && isMinio && !(data?.objects?.length > 0) && (
          filteredBuckets.length === 0
            ? <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("sources.explorer.noBuckets")}</div>
            : filteredBuckets.map((b) => (
                <div key={b} className="card" style={{ padding: "10px 14px", marginBottom: 8, display: "flex", alignItems: "center", gap: 10 }}>
                  {Icon.db({ width: 16, height: 16, style: { color: "var(--text-muted)" } })}
                  <span style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>{b}</span>
                </div>
              ))
        )}

        {!loading && !error && !isMinio && (
          filteredSchemas.length === 0
            ? <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("sources.explorer.noSchemas")}</div>
            : filteredSchemas.map((s) => (
                <div key={s.name} className="card" style={{ padding: 14, marginBottom: 10 }}>
                  <div className="field-label" style={{ marginBottom: 8 }}>{s.name} <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>{t("sources.explorer.tablesCount", { count: s.tables.length })}</span></div>
                  {s.tables.length === 0 ? (
                    <div style={{ color: "var(--text-muted)", fontSize: 12.5 }}>{t("sources.explorer.noTables")}</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      {s.tables.map((tb) => {
                        const tableFullName = `${s.name}.${tb.name}`;
                        const isExpanded = !!expanded[tableFullName];
                        const cols = columnsByTable[tableFullName];
                        const tableAnnotated = !!annotationByKey[annotationKey(tableFullName, null)];
                        return (
                          <div key={tb.name} style={{ borderTop: "1px solid var(--border)", paddingTop: 6, marginTop: 2 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <button
                                type="button"
                                onClick={() => toggleExpand(tableFullName, s.name, tb.name)}
                                aria-label={t("sources.explorer.showColumns")}
                                style={{ display: "flex", alignItems: "center", color: "var(--text-muted)" }}
                              >
                                <span style={{ display: "inline-flex", transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform .15s" }}>
                                  {Icon.chevronDown({ width: 12, height: 12 })}
                                </span>
                              </button>
                              {tb.provenance && (
                                <span
                                  className="badge badge-accent"
                                  style={{ fontSize: 10.5, padding: "1px 7px" }}
                                  title={t("sources.explorer.importedRows", { count: tb.provenance.row_count ?? 0 })}
                                >
                                  {t("sources.explorer.importedFrom", { file: tb.provenance.file })}
                                </span>
                              )}
                              <AnnotationField
                                label={tb.name}
                                value={valueFor(tableFullName, null)}
                                placeholder={t("sources.explorer.descriptionPlaceholder")}
                                annotated={tableAnnotated}
                                annotatedLabel={t("sources.explorer.annotated")}
                                deleteTitle={t("sources.explorer.deleteAnnotation")}
                                onChange={(v) => setDraft(tableFullName, null, v)}
                                onDelete={() => removeAnnotation(tableFullName, null)}
                              />
                            </div>

                            {isExpanded && (
                              <div style={{ marginLeft: 22, marginTop: 6, marginBottom: 4, display: "flex", flexDirection: "column", gap: 5 }}>
                                {cols === "loading" && <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("common.loading")}</div>}
                                {cols === "error" && <div style={{ fontSize: 11.5, color: "#c53d3d" }}>{t("sources.explorer.columnsFailed")}</div>}
                                {Array.isArray(cols) && cols.length === 0 && <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("sources.explorer.noColumns")}</div>}
                                {Array.isArray(cols) && cols.map((c) => {
                                  const colAnnotated = !!annotationByKey[annotationKey(tableFullName, c.name)];
                                  return (
                                    <AnnotationField
                                      key={c.name}
                                      compact
                                      label={c.name}
                                      sublabel={c.type}
                                      value={valueFor(tableFullName, c.name)}
                                      placeholder={t("sources.explorer.descriptionPlaceholderColumn")}
                                      annotated={colAnnotated}
                                      annotatedLabel={t("sources.explorer.annotated")}
                                      deleteTitle={t("sources.explorer.deleteAnnotation")}
                                      onChange={(v) => setDraft(tableFullName, c.name, v)}
                                      onDelete={() => removeAnnotation(tableFullName, c.name)}
                                    />
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))
        )}
      </div>

      {!isMinio && dirtyCount > 0 && (
        <div style={{ position: "sticky", bottom: 0, background: "var(--surface)", borderTop: "1px solid var(--border)", padding: "10px 0 2px", marginTop: 4, display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={() => setDrafts({})} disabled={saving}>{t("sources.explorer.discardChanges")}</Button>
          <Button onClick={saveDrafts} disabled={saving}>{saving ? t("common.saving") : t("sources.explorer.saveDescriptionsCount", { count: dirtyCount })}</Button>
        </div>
      )}
    </Drawer>
  );
}
