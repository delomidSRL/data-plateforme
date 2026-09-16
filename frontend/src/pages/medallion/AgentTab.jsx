import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import * as pipelineAgentApi from "../../api/pipelineAgent.js";
import * as sourcesApi from "../../api/sources.js";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import AnnotationField from "../../components/AnnotationField.jsx";
import IndicatorEditorList from "../../components/IndicatorEditorList.jsx";
import { useToast } from "../../context/ToastContext.jsx";

const STATUS_TONE = { resolved: "accent", partial: "neutral", unresolved: "neutral" };
const EXECUTION_ENGAGED_STATUSES = ["executing", "waiting_approval", "run_failed", "done"];

// Module 14 §5.2 — evidence chip formatting: pure display helpers, kept outside the
// component since they don't touch any state.
function formatRowCount(n, t) {
  if (n >= 1_000_000) return t("medallion.agent.evidenceRows", { value: `${(n / 1_000_000).toFixed(1)}M` });
  if (n >= 1_000) return t("medallion.agent.evidenceRows", { value: `${(n / 1_000).toFixed(1)}k` });
  return t("medallion.agent.evidenceRows", { value: n });
}

function formatYearRange([lo, hi]) {
  const year = (s) => (s || "").slice(0, 4);
  const loY = year(lo);
  const hiY = year(hi);
  return loY === hiY ? loY : `${loY}–${hiY}`;
}

export default function AgentTab({ project, readOnly, onExecutionUpdate }) {
  const { t } = useTranslation();
  const showToast = useToast();

  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [instruction, setInstruction] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [removedTables, setRemovedTables] = useState({}); // table -> true (local retire)
  const [forcedTables, setForcedTables] = useState([]); // candidate objects force-added locally

  const [annotationsBySource, setAnnotationsBySource] = useState({});
  const [drafts, setDrafts] = useState({}); // `${sourceId}::${table}::${column||""}` -> text
  const [expanded, setExpanded] = useState({});
  const [columnsByKey, setColumnsByKey] = useState({});
  const [savingAnnotations, setSavingAnnotations] = useState(false);

  const [generatingPlan, setGeneratingPlan] = useState(false);
  const [silverDrafts, setSilverDrafts] = useState({}); // silver name -> draft SQL text
  const [savingSilver, setSavingSilver] = useState({}); // silver name -> bool

  const [executing, setExecuting] = useState(false);
  const [approvingDatasetId, setApprovingDatasetId] = useState(null);

  const [recomputingRelations, setRecomputingRelations] = useState(false);
  const [repairingSilver, setRepairingSilver] = useState(null); // silver name currently being repaired
  const [generatingDashboards, setGeneratingDashboards] = useState(false);

  useEffect(() => {
    setLoading(true);
    pipelineAgentApi.getPlan(project.id)
      .then((p) => { setPlan(p); setInstruction(p.instruction); })
      .catch(() => setPlan(null))
      .finally(() => setLoading(false));
  }, [project.id]);

  // While the run step is in flight, re-check periodically — POST /execute is re-entrant and
  // just reports "still running" (no error) until the real Airflow run settles.
  useEffect(() => {
    if (plan?.status !== "executing") return undefined;
    const interval = setInterval(() => {
      pipelineAgentApi.execute(project.id).then((result) => { setPlan(result); onExecutionUpdate?.(); }).catch(() => {});
    }, 8000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, plan?.status]);

  useEffect(() => { setRemovedTables({}); setForcedTables([]); }, [plan?.id, plan?.updated_at]);

  const mapping = plan?.mapping || null;

  const displayedTables = useMemo(() => {
    if (!mapping) return [];
    const kept = mapping.tables.filter((tb) => !removedTables[tb.table]);
    return [...kept, ...forcedTables];
  }, [mapping, removedTables, forcedTables]);

  const candidateList = useMemo(() => {
    if (!mapping?.unresolved) return [];
    const seen = new Set();
    const list = [];
    for (const u of mapping.unresolved) {
      for (const c of u.candidates) {
        const key = `${c.source_id}::${c.table}`;
        if (!seen.has(key) && !removedTables[c.table] && !displayedTables.some((d) => d.table === c.table)) {
          seen.add(key);
          list.push(c);
        }
      }
    }
    return list;
  }, [mapping, removedTables, displayedTables]);

  useEffect(() => {
    const sourceIds = [...new Set(candidateList.map((c) => c.source_id))];
    sourceIds.forEach((sid) => {
      if (!(sid in annotationsBySource)) {
        sourcesApi.listAnnotations(sid).then((rows) => setAnnotationsBySource((m) => ({ ...m, [sid]: rows }))).catch(() => {});
      }
    });
  }, [candidateList]);

  const annotationKey = (sourceId, table, column) => `${sourceId}::${table}::${column || ""}`;
  const annotationByKey = useMemo(() => {
    const map = {};
    for (const rows of Object.values(annotationsBySource)) {
      for (const a of rows) {
        const sid = a.data_source_id;
        map[annotationKey(sid, a.table_name, a.column_name)] = a;
      }
    }
    return map;
  }, [annotationsBySource]);

  const valueFor = (sourceId, table, column) => {
    const key = annotationKey(sourceId, table, column);
    if (key in drafts) return drafts[key];
    return annotationByKey[key]?.description || "";
  };

  const setDraft = (sourceId, table, column, value) => {
    const key = annotationKey(sourceId, table, column);
    const persisted = annotationByKey[key]?.description || "";
    setDrafts((d) => {
      const next = { ...d };
      if (value === persisted) delete next[key];
      else next[key] = value;
      return next;
    });
  };

  const dirtyCount = Object.keys(drafts).length;

  const toggleExpand = async (sourceId, table) => {
    const key = `${sourceId}::${table}`;
    setExpanded((e) => ({ ...e, [key]: !e[key] }));
    if (!columnsByKey[key]) {
      setColumnsByKey((c) => ({ ...c, [key]: "loading" }));
      const [schema, tableOnly] = table.split(".");
      try {
        const cols = await sourcesApi.listTableColumns(sourceId, schema, tableOnly);
        setColumnsByKey((c) => ({ ...c, [key]: cols }));
      } catch {
        setColumnsByKey((c) => ({ ...c, [key]: "error" }));
      }
    }
  };

  const saveAnnotations = async () => {
    const bySource = {};
    for (const [key, value] of Object.entries(drafts)) {
      if (!value.trim()) continue;
      const [sourceId, table, column] = key.split("::");
      (bySource[sourceId] ||= []).push({ table_name: table, column_name: column || null, description: value.trim() });
    }
    if (Object.keys(bySource).length === 0) { setDrafts({}); return; }
    setSavingAnnotations(true);
    try {
      await Promise.all(Object.entries(bySource).map(([sid, items]) => sourcesApi.upsertAnnotations(sid, items)));
      showToast(t("medallion.agent.annotationsSaved"));
      setDrafts({});
      const results = await Promise.all(Object.keys(bySource).map((sid) => sourcesApi.listAnnotations(sid)));
      setAnnotationsBySource((m) => {
        const next = { ...m };
        Object.keys(bySource).forEach((sid, i) => { next[sid] = results[i]; });
        return next;
      });
    } catch (err) {
      showToast(err.message || t("medallion.agent.annotationsSaveFailed"));
    } finally {
      setSavingAnnotations(false);
    }
  };

  const analyze = async () => {
    if (!instruction.trim()) return;
    setAnalyzing(true);
    try {
      const result = await pipelineAgentApi.mapIntent(project.id, instruction.trim());
      setPlan(result);
      showToast(t("medallion.agent.analyzed"));
    } catch (err) {
      showToast(err.message || t("medallion.agent.analyzeFailed"));
    } finally {
      setAnalyzing(false);
    }
  };

  const doRemap = async () => {
    setAnalyzing(true);
    try {
      const result = await pipelineAgentApi.remap(project.id);
      setPlan(result);
      showToast(result.status === "mapping_failed" ? t("medallion.agent.mappingFailed") : t("medallion.agent.analyzed"));
    } catch (err) {
      showToast(err.message || t("medallion.agent.analyzeFailed"));
    } finally {
      setAnalyzing(false);
    }
  };

  const recomputeRelations = async () => {
    setRecomputingRelations(true);
    try {
      const relationships = await pipelineAgentApi.recomputeRelationships(project.id);
      setPlan((p) => ({ ...p, mapping: { ...p.mapping, relationships } }));
      showToast(t("medallion.agent.relationsRecomputed"));
    } catch (err) {
      showToast(err.message || t("medallion.agent.relationsRecomputeFailed"));
    } finally {
      setRecomputingRelations(false);
    }
  };

  const removeTable = (table) => setRemovedTables((r) => ({ ...r, [table]: true }));
  const forceAddCandidate = (candidate) => {
    setForcedTables((f) => [...f, { table: candidate.table, source_id: candidate.source_id, role: "reference", confidence: 1, columns: { measure: [], temporal: [], dimension: [], join_keys: [] } }]);
  };

  const generatePlan = async () => {
    setGeneratingPlan(true);
    try {
      const result = await pipelineAgentApi.generatePlan(project.id);
      setPlan(result);
      showToast(result.status === "plan_failed" ? t("medallion.agent.planFailed") : t("medallion.agent.planGenerated"));
    } catch (err) {
      showToast(err.message || t("medallion.agent.planGenerateFailed"));
    } finally {
      setGeneratingPlan(false);
    }
  };

  const doReplan = async () => {
    if (!window.confirm(t("medallion.agent.replanConfirm"))) return;
    setGeneratingPlan(true);
    try {
      const result = await pipelineAgentApi.replan(project.id);
      setPlan(result);
      showToast(result.status === "plan_failed" ? t("medallion.agent.planFailed") : t("medallion.agent.planGenerated"));
    } catch (err) {
      showToast(err.message || t("medallion.agent.planGenerateFailed"));
    } finally {
      setGeneratingPlan(false);
    }
  };

  const patchPlan = async (payload) => {
    try {
      const result = await pipelineAgentApi.patchPlan(project.id, payload);
      setPlan(result);
      return result;
    } catch (err) {
      showToast(err.message || t("medallion.agent.planPatchFailed"));
      throw err;
    }
  };

  const removeBronze = (name) => patchPlan({ remove_bronze: [name] }).catch(() => {});
  const removeSilver = (name) => patchPlan({ remove_silver: [name] }).catch(() => {});
  const removeGold = (name) => patchPlan({ remove_gold: [name] }).catch(() => {});

  const repairSilver = async (name) => {
    setRepairingSilver(name);
    try {
      const result = await pipelineAgentApi.repairSilver(project.id, name);
      setPlan(result);
    } catch (err) {
      showToast(err.message || t("medallion.agent.repairFailedToast"));
    } finally {
      setRepairingSilver(null);
    }
  };

  const saveSilverSql = async (name) => {
    const sql = silverDrafts[name];
    if (sql === undefined) return;
    setSavingSilver((s) => ({ ...s, [name]: true }));
    try {
      await patchPlan({ silver_edits: [{ name, sql }] });
      setSilverDrafts((d) => { const next = { ...d }; delete next[name]; return next; });
    } catch {
      // error already toasted by patchPlan
    } finally {
      setSavingSilver((s) => ({ ...s, [name]: false }));
    }
  };

  const approveSilver = (name) => patchPlan({ silver_edits: [{ name, approve: true }] }).catch(() => {});
  const unapproveSilver = (name) => patchPlan({ silver_edits: [{ name, approve: false }] }).catch(() => {});

  const toggleTest = (tst, include) => {
    const key = `${tst.dataset}::${tst.column}::${tst.test}`;
    patchPlan({ test_toggles: { [key]: include } }).catch(() => {});
  };

  const startExecute = async () => {
    setExecuting(true);
    try {
      const result = await pipelineAgentApi.execute(project.id);
      setPlan(result);
      onExecutionUpdate?.();
    } catch (err) {
      showToast(err.message || t("medallion.agent.executeFailed"));
    } finally {
      setExecuting(false);
    }
  };

  const approveDuringExecution = async (datasetId) => {
    setApprovingDatasetId(datasetId);
    try {
      const result = await pipelineAgentApi.approveSilverDuringExecution(project.id, datasetId);
      setPlan(result);
      onExecutionUpdate?.();
    } catch (err) {
      showToast(err.message || t("medallion.agent.executeFailed"));
    } finally {
      setApprovingDatasetId(null);
    }
  };

  const generateDashboards = async (payload) => {
    setGeneratingDashboards(true);
    try {
      const result = await pipelineAgentApi.generateDashboards(project.id, payload);
      setPlan(result);
      onExecutionUpdate?.();
    } catch (err) {
      showToast(err.message || t("medallion.agent.dashboardGenerateFailed"));
    } finally {
      setGeneratingDashboards(false);
    }
  };

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  return (
    <>
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.agent.instructionLabel")}</div>
        <textarea
          className="input"
          rows={3}
          style={{ width: "100%", resize: "vertical", fontFamily: "var(--font-b)", fontSize: 13.5 }}
          value={instruction}
          placeholder={t("medallion.agent.instructionPlaceholder")}
          disabled={readOnly}
          onChange={(e) => setInstruction(e.target.value)}
        />
        <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
          <Button onClick={analyze} disabled={readOnly || analyzing || !instruction.trim()}>
            {analyzing ? t("medallion.agent.analyzing") : t("medallion.agent.analyze")}
          </Button>
          {mapping && (
            <Button variant="ghost" onClick={doRemap} disabled={readOnly || analyzing}>
              {Icon.refresh()} {t("medallion.agent.reanalyze")}
            </Button>
          )}
        </div>
      </div>

      {plan?.status === "mapping_failed" && (
        <div className="error-banner" style={{ marginBottom: 16 }}>{Icon.warn()}<span>{t("medallion.agent.mappingFailedDetail")}</span></div>
      )}

      {mapping && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <Badge tone={STATUS_TONE[mapping.status]}>{t(`medallion.agent.mappingStatus.${mapping.status}`)}</Badge>
            <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.tablesFound", { count: displayedTables.length })}</span>
          </div>

          {displayedTables.map((tb) => (
            <div key={tb.table} className="card" style={{ padding: 14, marginBottom: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontFamily: "var(--font-m)", fontSize: 13, fontWeight: 600 }}>{tb.table}</span>
                <Badge tone="neutral">{t(`medallion.agent.role.${tb.role}`)}</Badge>
                <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("medallion.agent.confidence", { value: Math.round(tb.confidence * 100) })}</span>
                {!readOnly && (
                  <button type="button" className="link" style={{ marginLeft: "auto", fontSize: 12 }} onClick={() => removeTable(tb.table)}>
                    {t("medallion.agent.remove")}
                  </button>
                )}
              </div>
              <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                {["measure", "temporal", "dimension", "join_keys"].map((role) =>
                  tb.columns[role].map((col) => (
                    <span key={role + col} className="badge badge-accent" style={{ fontSize: 10.5, padding: "2px 8px" }}>
                      {col} · {t(`medallion.agent.columnRole.${role}`)}
                    </span>
                  ))
                )}
              </div>
              {tb.evidence && (
                <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {tb.evidence.from_profile?.row_count != null && (
                    <span className="badge badge-neutral" style={{ fontSize: 10.5, padding: "2px 8px" }}>{formatRowCount(tb.evidence.from_profile.row_count, t)}</span>
                  )}
                  {tb.evidence.from_profile?.temporal_range && (
                    <span className="badge badge-neutral" style={{ fontSize: 10.5, padding: "2px 8px" }}>{formatYearRange(tb.evidence.from_profile.temporal_range)}</span>
                  )}
                  {tb.evidence.from_profile?.null_rate_key != null && (
                    <span className="badge badge-neutral" style={{ fontSize: 10.5, padding: "2px 8px" }}>
                      {t("medallion.agent.evidenceNullRateKey", { value: Math.round(tb.evidence.from_profile.null_rate_key * 100) })}
                    </span>
                  )}
                  {(tb.evidence.from_model || []).map((e, i) => (
                    <span key={i} className="badge badge-neutral" style={{ fontSize: 10.5, padding: "2px 8px" }}>{e}</span>
                  ))}
                </div>
              )}
            </div>
          ))}

          <div className="card" style={{ padding: 16, marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <div className="field-label" style={{ marginBottom: 0 }}>{t("medallion.agent.relationsTitle")}</div>
              {!readOnly && (
                <button type="button" className="link" style={{ marginLeft: "auto", fontSize: 12 }} disabled={recomputingRelations} onClick={recomputeRelations}>
                  {recomputingRelations ? t("medallion.agent.relationsRecomputing") : t("medallion.agent.relationsRecompute")}
                </button>
              )}
            </div>
            {(mapping.relationships || []).length === 0 ? (
              <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.relationsEmpty")}</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {mapping.relationships.map((r, i) => (
                  <div key={`v${i}`} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                    <span style={{ fontFamily: "var(--font-m)" }}>{r.child}</span>
                    <span style={{ color: "var(--text-muted)" }}>→</span>
                    <span style={{ fontFamily: "var(--font-m)" }}>{r.parent}</span>
                    <span className="badge badge-accent" style={{ fontSize: 10.5, padding: "2px 8px", marginLeft: "auto" }}>
                      {t("medallion.agent.relationsMatchRate", { value: Math.round(r.match_rate * 100) })}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {mapping.unresolved.length > 0 && (
            <div className="card" style={{ padding: 16, marginTop: 4 }}>
              <div className="field-label" style={{ marginBottom: 4 }}>{t("medallion.agent.unresolvedTitle")}</div>
              <p style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 12 }}>{t("medallion.agent.unresolvedDesc")}</p>
              {mapping.unresolved.map((u, i) => (
                <div key={i} style={{ marginBottom: 10, fontSize: 13 }}>{u.need}</div>
              ))}
              {candidateList.map((c) => {
                const key = `${c.source_id}::${c.table}`;
                const isExpanded = !!expanded[key];
                const cols = columnsByKey[key];
                const tableAnnotated = !!annotationByKey[annotationKey(c.source_id, c.table, null)];
                return (
                  <div key={key} style={{ borderTop: "1px solid var(--border)", paddingTop: 8, marginTop: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <button type="button" onClick={() => toggleExpand(c.source_id, c.table)} aria-label={t("sources.explorer.showColumns")} style={{ display: "flex", color: "var(--text-muted)" }}>
                        <span style={{ display: "inline-flex", transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform .15s" }}>{Icon.chevronDown({ width: 12, height: 12 })}</span>
                      </button>
                      <AnnotationField
                        label={c.table}
                        value={valueFor(c.source_id, c.table, null)}
                        placeholder={t("sources.explorer.descriptionPlaceholder")}
                        annotated={tableAnnotated}
                        annotatedLabel={t("sources.explorer.annotated")}
                        onChange={(v) => setDraft(c.source_id, c.table, null, v)}
                      />
                      {!readOnly && (
                        <button type="button" className="link" style={{ fontSize: 12, whiteSpace: "nowrap" }} onClick={() => forceAddCandidate(c)}>
                          {t("medallion.agent.forceAdd")}
                        </button>
                      )}
                    </div>
                    {isExpanded && (
                      <div style={{ marginLeft: 22, marginTop: 6, display: "flex", flexDirection: "column", gap: 5 }}>
                        {cols === "loading" && <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("common.loading")}</div>}
                        {cols === "error" && <div style={{ fontSize: 11.5, color: "#c53d3d" }}>{t("sources.explorer.columnsFailed")}</div>}
                        {Array.isArray(cols) && cols.map((col) => (
                          <AnnotationField
                            key={col.name}
                            compact
                            label={col.name}
                            sublabel={col.type}
                            value={valueFor(c.source_id, c.table, col.name)}
                            placeholder={t("sources.explorer.descriptionPlaceholderColumn")}
                            annotated={!!annotationByKey[annotationKey(c.source_id, c.table, col.name)]}
                            annotatedLabel={t("sources.explorer.annotated")}
                            onChange={(v) => setDraft(c.source_id, c.table, col.name, v)}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}

              {dirtyCount > 0 && (
                <div style={{ marginTop: 14, display: "flex", gap: 10, justifyContent: "flex-end" }}>
                  <Button variant="ghost" onClick={() => setDrafts({})} disabled={savingAnnotations}>{t("sources.explorer.discardChanges")}</Button>
                  <Button onClick={saveAnnotations} disabled={savingAnnotations}>
                    {savingAnnotations ? t("common.saving") : t("sources.explorer.saveDescriptionsCount", { count: dirtyCount })}
                  </Button>
                </div>
              )}
            </div>
          )}

          {displayedTables.length > 0 && (
            <div className="card" style={{ padding: 16, marginTop: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div className="field-label" style={{ margin: 0 }}>{t("medallion.agent.planTitle")}</div>
                <div style={{ display: "flex", gap: 8 }}>
                  {!plan.plan && (
                    <Button onClick={generatePlan} disabled={readOnly || generatingPlan}>
                      {generatingPlan ? t("medallion.agent.planGenerating") : t("medallion.agent.generatePlan")}
                    </Button>
                  )}
                  {plan.plan && !readOnly && (
                    <Button variant="ghost" onClick={doReplan} disabled={generatingPlan}>{Icon.refresh()} {t("medallion.agent.replan")}</Button>
                  )}
                </div>
              </div>

              {plan.status === "plan_failed" && (
                <div className="error-banner" style={{ marginTop: 10 }}>{Icon.warn()}<span>{t("medallion.agent.planFailedDetail")}</span></div>
              )}

              {plan.plan && (
                <>
                  <div style={{ marginTop: 14 }}>
                    <div className="field-label" style={{ fontSize: 11.5, marginBottom: 6, color: "var(--text-muted)" }}>{t("medallion.agent.bronzeSection")}</div>
                    {plan.plan.bronze.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.noBronze")}</div>}
                    {plan.plan.bronze.map((b) => (
                      <div key={b.name} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0", borderTop: "1px solid var(--border)" }}>
                        <span style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{b.name}</span>
                        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{b.table}</span>
                        <Badge tone="neutral">{b.mode === "incremental" ? t("medallion.agent.modeIncremental", { key: b.incremental_key }) : t("medallion.agent.modeFull")}</Badge>
                        {!readOnly && (
                          <button type="button" className="link" style={{ marginLeft: "auto", fontSize: 12 }} onClick={() => removeBronze(b.name)}>{t("medallion.agent.remove")}</button>
                        )}
                      </div>
                    ))}
                  </div>

                  <div style={{ marginTop: 18 }}>
                    <div className="field-label" style={{ fontSize: 11.5, marginBottom: 6, color: "var(--text-muted)" }}>{t("medallion.agent.silverSection")}</div>
                    {plan.plan.silver.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.noSilver")}</div>}
                    {plan.plan.silver.map((s) => {
                      const draftSql = silverDrafts[s.name];
                      const dirty = draftSql !== undefined && draftSql !== s.sql;
                      const repair = (plan.plan.repair_log || []).find((r) => r.name === s.name && r.status === "repaired");
                      return (
                        <div key={s.name} className="card" style={{ padding: 12, marginTop: 8, background: "var(--bg)" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ fontFamily: "var(--font-m)", fontSize: 13, fontWeight: 600 }}>{s.name}</span>
                            <Badge tone={s.status === "approved" ? "accent" : "neutral"}>
                              {s.status === "approved" ? t("medallion.agent.approved") : t("medallion.agent.draftAiApproval")}
                            </Badge>
                            {repair && <Badge tone="neutral">{t("medallion.agent.autoRepaired", { count: repair.attempts })}</Badge>}
                            {!readOnly && (
                              <button type="button" className="link" style={{ marginLeft: "auto", fontSize: 12 }} onClick={() => removeSilver(s.name)}>{t("medallion.agent.remove")}</button>
                            )}
                          </div>
                          {s.rationale && <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "4px 0 0" }}>{s.rationale}</p>}
                          {s.logical_plan && (
                            <div style={{ marginTop: 8, padding: "8px 10px", background: "var(--surface)", borderRadius: 6, fontSize: 11.5, color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 3 }}>
                              {s.logical_plan.joins.map((j, i) => (
                                <div key={`j${i}`}>{t("medallion.agent.logicalJoin", { left: j.left, right: j.right, type: t(`medallion.agent.joinType.${j.type}`) })}</div>
                              ))}
                              {s.logical_plan.filters.map((f, i) => <div key={`f${i}`}>{t("medallion.agent.logicalFilter", { filter: f })}</div>)}
                              {s.logical_plan.normalizations.map((n, i) => <div key={`n${i}`}>{t("medallion.agent.logicalNormalization", { norm: n })}</div>)}
                              {s.logical_plan.output.length > 0 && <div>{t("medallion.agent.logicalOutput", { columns: s.logical_plan.output.join(", ") })}</div>}
                            </div>
                          )}
                          {s.logical_plan_warning && (
                            <div style={{ display: "flex", alignItems: "flex-start", gap: 6, background: "rgba(229,114,0,0.1)", border: "1px solid var(--accent)", borderRadius: 6, padding: "6px 8px", marginTop: 8, fontSize: 11.5, color: "var(--accent)" }}>
                              {Icon.warn({ width: 13, height: 13 })}
                              <span>{s.logical_plan_warning}</span>
                            </div>
                          )}
                          <textarea
                            className="input"
                            rows={5}
                            style={{ width: "100%", fontFamily: "var(--font-m)", fontSize: 12, resize: "vertical", marginTop: 8 }}
                            value={draftSql !== undefined ? draftSql : s.sql}
                            disabled={readOnly}
                            onChange={(e) => setSilverDrafts((d) => ({ ...d, [s.name]: e.target.value }))}
                          />
                          {!readOnly && (
                            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                              {dirty && (
                                <Button onClick={() => saveSilverSql(s.name)} disabled={savingSilver[s.name]}>
                                  {savingSilver[s.name] ? t("common.saving") : t("common.save")}
                                </Button>
                              )}
                              {!dirty && s.status !== "approved" && (
                                <Button onClick={() => approveSilver(s.name)}>{t("medallion.agent.approveSql")}</Button>
                              )}
                              {!dirty && s.status === "approved" && (
                                <Button variant="ghost" onClick={() => unapproveSilver(s.name)}>{t("medallion.agent.unapprove")}</Button>
                              )}
                              {!dirty && (
                                <Button variant="ghost" onClick={() => repairSilver(s.name)} disabled={repairingSilver === s.name}>
                                  {repairingSilver === s.name ? t("medallion.agent.repairing") : t("medallion.agent.repairAction")}
                                </Button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {(plan.plan.repair_log || []).filter((r) => r.status === "failed").map((r) => (
                      <div key={r.name} className="error-banner" style={{ marginTop: 8 }}>
                        {Icon.warn()}<span>{t("medallion.agent.repairFailed", { name: r.name, attempts: r.attempts, error: r.error })}</span>
                      </div>
                    ))}
                  </div>

                  <div style={{ marginTop: 18 }}>
                    <div className="field-label" style={{ fontSize: 11.5, marginBottom: 6, color: "var(--text-muted)" }}>{t("medallion.agent.goldSection")}</div>
                    {(plan.plan.gold_warnings || []).map((w, idx) => (
                      <div key={idx} style={{ display: "flex", alignItems: "flex-start", gap: 6, background: "rgba(229,114,0,0.1)", border: "1px solid var(--accent)", borderRadius: 6, padding: "6px 8px", marginBottom: 8, fontSize: 11.5, color: "var(--accent)" }}>
                        {Icon.warn({ width: 13, height: 13 })}
                        <span>{w}</span>
                      </div>
                    ))}
                    {plan.plan.gold.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.noGold")}</div>}
                    {plan.plan.gold.map((g) => (
                      <div key={g.name} className="card" style={{ padding: 12, marginTop: 8, background: "var(--bg)" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontFamily: "var(--font-m)", fontSize: 13, fontWeight: 600 }}>{g.name}</span>
                          {!readOnly && (
                            <button type="button" className="link" style={{ marginLeft: "auto", fontSize: 12 }} onClick={() => removeGold(g.name)}>{t("medallion.agent.remove")}</button>
                          )}
                        </div>
                        <p style={{ fontSize: 12.5, margin: "6px 0" }}>
                          {t("medallion.agent.goldSummary", {
                            agg: g.aggregation, metric: g.metric_column,
                            dims: g.dimension_columns.join(", ") || "—",
                            grain: g.time_grain ? t(`medallion.agent.grain.${g.time_grain}`) : "—",
                          })}
                        </p>
                        {g.grain && g.grain.length > 0 && (
                          <p style={{ fontSize: 11.5, color: "var(--text-muted)", margin: "0 0 6px" }}>
                            {t("medallion.agent.datasetGrain", { columns: g.grain.join(" × ") })}
                          </p>
                        )}
                        {g.grain_warning && (
                          <div style={{ display: "flex", alignItems: "flex-start", gap: 6, background: "rgba(229,114,0,0.1)", border: "1px solid var(--accent)", borderRadius: 6, padding: "6px 8px", marginBottom: 8, fontSize: 11.5, color: "var(--accent)" }}>
                            {Icon.warn({ width: 13, height: 13 })}
                            <span>{g.grain_warning}</span>
                          </div>
                        )}
                        <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, background: "var(--surface)", padding: 10, borderRadius: 6, overflowX: "auto", margin: 0 }}>{g.sql}</pre>
                      </div>
                    ))}
                  </div>

                  <div style={{ marginTop: 18 }}>
                    <div className="field-label" style={{ fontSize: 11.5, marginBottom: 6, color: "var(--text-muted)" }}>{t("medallion.agent.testsSection")}</div>
                    {plan.plan.tests.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.noTests")}</div>}
                    {plan.plan.tests.map((tst, i) => (
                      <label key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, padding: "5px 0" }}>
                        <input type="checkbox" checked={tst.include} disabled={readOnly} onChange={(e) => toggleTest(tst, e.target.checked)} />
                        <span style={{ fontFamily: "var(--font-m)" }}>{tst.dataset}.{tst.column}</span>
                        <Badge tone="neutral">{t(`medallion.agent.testType.${tst.test}`)}</Badge>
                      </label>
                    ))}
                  </div>

                  {plan.plan.bronze.length > 0 && plan.plan.gold.length > 0 && (
                    <div style={{ marginTop: 22, borderTop: "1px solid var(--border)", paddingTop: 18 }}>
                      {!EXECUTION_ENGAGED_STATUSES.includes(plan.status) ? (
                        <Button onClick={startExecute} disabled={readOnly || executing}>
                          {executing ? t("medallion.agent.executing") : t("medallion.agent.validateAndBuild")}
                        </Button>
                      ) : (
                        <ExecutionStepper
                          plan={plan}
                          readOnly={readOnly}
                          executing={executing}
                          approvingDatasetId={approvingDatasetId}
                          onResume={startExecute}
                          onApprove={approveDuringExecution}
                          generatingDashboards={generatingDashboards}
                          onGenerateDashboards={generateDashboards}
                        />
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </>
      )}
    </>
  );
}

const STEP_KEYS = ["datasets", "silver", "build", "run", "publish", "dashboard"];

function ExecutionStepper({ plan, readOnly, executing, approvingDatasetId, onResume, onApprove, generatingDashboards, onGenerateDashboards }) {
  const { t } = useTranslation();
  const es = plan.execution_state || {};
  const failedStep = plan.status === "run_failed" ? es.failed_step : null;
  const pendingSilver = es.pending_silver || [];

  const statusOf = (key) => {
    if (failedStep === key) return "error";
    switch (key) {
      case "datasets": return es.dataset_ids ? "done" : "pending";
      case "silver": return pendingSilver.length > 0 ? "waiting" : es.dataset_ids ? "done" : "pending";
      case "build": return es.built_at ? "done" : es.dataset_ids && pendingSilver.length === 0 ? "active" : "pending";
      case "run": return es.published_at ? "done" : es.built_at ? "active" : "pending";
      case "publish": return es.published_at ? "done" : "pending";
      case "dashboard":
        if (plan.status === "done") return "done";
        if (es.dashboard_review && !es.dashboard_generated_at) return "waiting";
        return "pending";
      default: return "pending";
    }
  };

  const ICON_BY_STATUS = {
    done: <span style={{ color: "#2f9e6e" }}>{Icon.check({ width: 14, height: 14 })}</span>,
    error: <span style={{ color: "#c53d3d" }}>{Icon.warn({ width: 14, height: 14 })}</span>,
    active: <span style={{ color: "var(--ember)" }}>{Icon.refresh({ width: 13, height: 13 })}</span>,
    waiting: <span style={{ color: "var(--ember)" }}>{Icon.warn({ width: 14, height: 14 })}</span>,
    pending: <span style={{ width: 14, height: 14, display: "inline-block", borderRadius: "50%", border: "2px solid var(--border)" }} />,
  };

  return (
    <div>
      <div className="field-label" style={{ marginBottom: 10 }}>{t("medallion.agent.executionTitle")}</div>
      {STEP_KEYS.map((key) => {
        const st = statusOf(key);
        return (
          <div key={key} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "8px 0", borderTop: "1px solid var(--border)" }}>
            <div style={{ marginTop: 2 }}>{ICON_BY_STATUS[st]}</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {t(`medallion.agent.step.${key}`)}
                {key === "silver" && pendingSilver.length > 0 && ` (${pendingSilver.length})`}
              </div>

              {key === "silver" && st === "waiting" && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 10 }}>
                  {pendingSilver.map((name) => {
                    const entry = plan.plan.silver.find((s) => s.name === name);
                    const datasetId = (es.dataset_ids || {})[name];
                    if (!entry) return null;
                    return (
                      <div key={name} className="card" style={{ padding: 10, background: "var(--bg)" }}>
                        <div style={{ fontFamily: "var(--font-m)", fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>{name}</div>
                        <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, background: "var(--surface)", padding: 8, borderRadius: 6, overflowX: "auto", margin: 0, whiteSpace: "pre-wrap" }}>{entry.sql}</pre>
                        {!readOnly && (
                          <Button onClick={() => onApprove(datasetId)} disabled={approvingDatasetId === datasetId} style={{ marginTop: 8 }}>
                            {approvingDatasetId === datasetId ? t("common.saving") : t("medallion.agent.approveSql")}
                          </Button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {st === "error" && key === failedStep && (
                <div className="error-banner" style={{ marginTop: 8 }}>{Icon.warn()}<span>{es.error}</span></div>
              )}

              {key === "dashboard" && st === "waiting" && (
                <DashboardReviewStep
                  review={es.dashboard_review} readOnly={readOnly}
                  generating={generatingDashboards} onGenerate={onGenerateDashboards}
                />
              )}

              {key === "dashboard" && plan.status === "done" && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                  <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.agent.dashboardReady")}</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {(es.dashboards || []).map((d) => (
                      <a key={d.url} className="btn-ghost" style={{ padding: "6px 10px", textDecoration: "none" }} href={d.url} target="_blank" rel="noreferrer">
                        {Icon.externalLink()} {t("medallion.agent.openDashboard", { count: d.charts_count })}
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {plan.status === "run_failed" && !readOnly && (
        <Button onClick={onResume} disabled={executing} style={{ marginTop: 12 }}>
          {executing ? t("medallion.agent.executing") : t("medallion.agent.resume")}
        </Button>
      )}
    </div>
  );
}

// Embedded, in-chain counterpart to Module 12's IndicatorsPanel — proposes indicators for
// every published gold dataset right where the engineer already is (no detour through the
// Pipeline tab), reusing the same IndicatorEditorList. Owns its own local edit state,
// seeded once from `review` (execution_state.dashboard_review) — safe because the polling
// effect in AgentTab stops the moment plan.status leaves "executing" (§ waiting_approval),
// so `review` never changes out from under an in-progress edit.
function DashboardReviewStep({ review, readOnly, generating, onGenerate }) {
  const { t } = useTranslation();
  const [edits, setEdits] = useState(() =>
    Object.fromEntries(Object.entries(review || {}).map(([did, r]) => [did, r.indicators.map((ind) => ({ ...ind }))]))
  );

  const patch = (did, i, changes) => setEdits((e) => ({ ...e, [did]: e[did].map((ind, idx) => (idx === i ? { ...ind, ...changes } : ind)) }));
  const toggleDimension = (did, i, colName) => setEdits((e) => ({
    ...e,
    [did]: e[did].map((ind, idx) => {
      if (idx !== i) return ind;
      const has = ind.dimension_columns.includes(colName);
      return { ...ind, dimension_columns: has ? ind.dimension_columns.filter((c) => c !== colName) : [...ind.dimension_columns, colName] };
    }),
  }));
  const remove = (did, i) => setEdits((e) => ({ ...e, [did]: e[did].filter((_, idx) => idx !== i) }));

  const totalIncluded = Object.values(edits).reduce((sum, list) => sum + list.filter((i) => i.included).length, 0);

  const submit = () => {
    const datasets = Object.entries(edits)
      .map(([did, list]) => ({ dataset_id: Number(did), indicators: list.filter((i) => i.included).map(({ included: _included, ...rest }) => rest) }))
      .filter((d) => d.indicators.length > 0);
    onGenerate({ datasets });
  };

  if (!review || Object.keys(review).length === 0) {
    return <div style={{ marginTop: 8, fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.suggest.noIndicators")}</div>;
  }

  return (
    <div style={{ marginTop: 10 }}>
      {Object.entries(review).map(([did, r]) => (
        <div key={did} style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8, fontFamily: "var(--font-m)" }}>{r.dataset_name}</div>
          <IndicatorEditorList
            indicators={edits[did] || []} columns={r.columns || []} source={r.source} readOnly={readOnly}
            onPatch={(i, changes) => patch(did, i, changes)}
            onToggleDimension={(i, colName) => toggleDimension(did, i, colName)}
            onRemove={(i) => remove(did, i)}
          />
        </div>
      ))}
      {!readOnly && (
        <Button disabled={totalIncluded === 0 || generating} onClick={submit}>
          {generating ? t("medallion.agent.dashboardGenerating") : t("medallion.agent.dashboardGenerateAction")}
        </Button>
      )}
    </div>
  );
}
