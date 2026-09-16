import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import * as qualityApi from "../../api/quality.js";
import { listDatasets, getDatasetColumns } from "../../api/medallion.js";
import { useToast } from "../../context/ToastContext.jsx";
import { useAuth } from "../../context/AuthContext.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import { saveBlob } from "../../utils/download.js";

// Module 16 §7 — the 5 ⟨contrat⟩ check_type values, each with its own parameters shape
// (mirrors quality_contract.validate_check_parameters / quality_intrinsic._collect_check).
const CHECK_TYPES = ["type_conformity", "format_validity", "intra_row_consistency", "plausibility", "conditional_completeness"];
const TYPE_CONFORMITY_TYPES = ["integer", "numeric", "date", "email", "boolean", "phone"];

function useRelativeTime() {
  const { t, i18n } = useTranslation();
  return (iso) => {
    if (!iso) return t("medallion.quality.never");
    const ms = Date.now() - new Date(iso).getTime();
    const h = ms / 3_600_000;
    if (h < 1) return t("medallion.quality.minAgo", { n: Math.max(1, Math.round(ms / 60_000)) });
    if (h < 48) return t("medallion.quality.hAgo", { n: Math.round(h) });
    return t("medallion.quality.dAgo", { n: Math.round(h / 24) });
  };
}

function Sparkline({ points }) {
  const { t } = useTranslation();
  const vals = points.map((p) => p.row_count ?? 0);
  if (vals.length < 2) return <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("medallion.quality.insufficientHistory")}</span>;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const w = 100, h = 28, step = w / (vals.length - 1);
  const path = vals.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - ((v - min) / span) * h).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <path d={path} fill="none" stroke="var(--ember)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const SEVERITY_TONE = { info: "neutral", warning: "accent", critical: "danger" };
// Module 16 §5.2 — same tone convention as SEVERITY_TONE just above (this design system has
// no dedicated "success/green" token, only accent/neutral/danger): ok reads as plain/neutral,
// warning as accent, critical as danger, skipped as muted neutral.
const METRIC_STATUS_TONE = { ok: "neutral", warning: "accent", critical: "danger", skipped: "neutral" };

function scorePct(score) {
  return score == null ? "—" : `${Math.round(score * 100)}%`;
}

function enforcementFor(checksByDataset, datasetId, checkType, cells) {
  // Module 16 extension §5 — "un check matérialisé produit deux signaux... rendu comme badge
  // d'enforcement sur la cellule, pas comme une seconde ligne": the M16 metric (score/
  // defect_rate) stays the cell's own value, unchanged; this only adds a small badge when a
  // materialized check exists for this exact (dataset, indicator, target_column).
  const candidates = (checksByDataset[datasetId] || []).filter((c) => c.materialize_as_dbt_test && c.check_type === checkType);
  if (candidates.length === 0) return null;
  const columns = new Set(cells.map((c) => c.target_column || ""));
  const match = candidates.find((c) => columns.has(c.target_column || "")) || candidates[0];
  return match.dbt_test_severity;
}

function CoherenceCell({ indicator, enforcement, t }) {
  const [expanded, setExpanded] = useState(false);
  const label = t(`medallion.quality.indicator.${indicator.indicator}`, { defaultValue: indicator.indicator });
  const expandable = indicator.status !== "ok" || indicator.cells.length > 1;
  return (
    <td style={{ verticalAlign: "top" }}>
      <button
        type="button"
        className="btn-ghost"
        style={{ display: "flex", alignItems: "center", gap: 6, padding: "3px 8px", cursor: expandable ? "pointer" : "default" }}
        onClick={() => expandable && setExpanded((e) => !e)}
        title={label}
      >
        <Badge tone={METRIC_STATUS_TONE[indicator.status] || "neutral"}>{indicator.status === "skipped" ? t("medallion.quality.skipped") : scorePct(indicator.score)}</Badge>
        {enforcement && (
          <Badge tone={enforcement === "error" ? "danger" : "accent"}>
            {t(`medallion.quality.enforcement.${enforcement}`)}
          </Badge>
        )}
      </button>
      {expanded && (
        <div style={{ marginTop: 6, padding: "8px 10px", borderRadius: 8, background: "var(--bg)", fontFamily: "var(--font-m)", fontSize: 11.5, display: "flex", flexDirection: "column", gap: 4, minWidth: 220 }}>
          {indicator.cells.map((c, i) => (
            <div key={i}>
              {c.target_column && <div style={{ color: "var(--text-muted)" }}>{c.target_column}</div>}
              <div>
                {c.status === "skipped"
                  ? t("medallion.quality.skipped")
                  : `${t("medallion.quality.defectRate")} ${c.defect_rate != null ? (c.defect_rate * 100).toFixed(2) + "%" : "—"}`}
                {" · "}
                {Object.entries(c.raw || {}).map(([k, v]) => `${k}=${v}`).join(", ")}
              </div>
            </div>
          ))}
        </div>
      )}
    </td>
  );
}

function CoherenceLayerSection({ layer, checksByDataset, t, expanded, onToggle }) {
  const indicatorSet = [...new Set(layer.tables.flatMap((tb) => tb.indicators.map((i) => i.indicator)))];
  return (
    <div className="card" style={{ padding: 0, marginBottom: 12, overflow: "hidden" }}>
      <div
        style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", cursor: "pointer", borderBottom: expanded ? "1px solid var(--border)" : "none" }}
        onClick={onToggle}
      >
        <span style={{ display: "inline-flex", color: "var(--text-muted)", transform: expanded ? "none" : "rotate(-90deg)", transition: "transform .15s" }}>
          {Icon.chevronDown ? Icon.chevronDown({ width: 13, height: 13 }) : null}
        </span>
        <div style={{ fontFamily: "var(--font-d)", fontWeight: 600, fontSize: 13.5, textTransform: "capitalize" }}>{layer.layer}</div>
        <Badge tone={METRIC_STATUS_TONE[layer.composite_score == null ? "skipped" : layer.composite_score >= 0.95 ? "ok" : layer.composite_score >= 0.8 ? "warning" : "critical"]}>
          {t("medallion.quality.compositeScore")} {scorePct(layer.composite_score)}
        </Badge>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("medallion.quality.nTables", { n: layer.tables.length })}</span>
      </div>
      {expanded && (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>{t("medallion.colProject")}</th>
                {indicatorSet.map((ind) => <th key={ind}>{t(`medallion.quality.indicator.${ind}`, { defaultValue: ind })}</th>)}
              </tr>
            </thead>
            <tbody>
              {layer.tables.map((tb) => (
                <tr key={tb.dataset_id}>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{tb.dataset_name}</td>
                  {indicatorSet.map((ind) => {
                    const found = tb.indicators.find((i) => i.indicator === ind);
                    if (!found) return <td key={ind} style={{ color: "var(--text-muted)" }}>—</td>;
                    const enforcement = enforcementFor(checksByDataset, tb.dataset_id, ind, found.cells);
                    return <CoherenceCell key={ind} indicator={found} enforcement={enforcement} t={t} />;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CoherenceMatrix({ project, runId }) {
  const { t } = useTranslation();
  const [overview, setOverview] = useState(null);
  const [checksByDataset, setChecksByDataset] = useState({});
  const [collapsed, setCollapsed] = useState({});

  useEffect(() => {
    let cancelled = false;
    qualityApi.getQualityOverview(project.id, runId).then((d) => { if (!cancelled) setOverview(d); }).catch(() => { if (!cancelled) setOverview({ layers: [] }); });
    // Module 16 extension §5 — project-wide (no dataset_id filter), used only to compute the
    // matrix's enforcement badges; the « Contrôles » panel below still fetches its own
    // dataset-scoped list independently.
    qualityApi.listQualityChecks(project.id).then((list) => {
      if (cancelled) return;
      const by = {};
      for (const c of list) (by[c.dataset_id] ||= []).push(c);
      setChecksByDataset(by);
    }).catch(() => { if (!cancelled) setChecksByDataset({}); });
    return () => { cancelled = true; };
  }, [project.id, runId]);

  // §5.4.4 — a project with no intrinsic-quality metric at all shows the tab exactly as
  // before: render nothing here rather than an empty matrix shell.
  if (!overview || overview.layers.length === 0) return null;

  return (
    <div style={{ marginBottom: 20 }}>
      <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.quality.coherenceTitle")}</div>
      {overview.layers.map((layer) => (
        <CoherenceLayerSection
          key={layer.layer}
          layer={layer}
          checksByDataset={checksByDataset}
          t={t}
          expanded={!collapsed[layer.layer]}
          onToggle={() => setCollapsed((c) => ({ ...c, [layer.layer]: !c[layer.layer] }))}
        />
      ))}
    </div>
  );
}

// §8.4 — the 6 coherence alert types (integrity/validity/consistency/reconciliation/
// plausibility/completeness) that back a ⟨baseline⟩ indicator offer the "double chemin": the
// defect might mean the DATA is wrong (fix it) or the BASELINE assertion itself was wrong
// (update it) — reconciliation and completeness are exactly the two families that can stem
// from a baseline indicator (aggregate_reconciliation/end_to_end_conservation and
// ingestion_completeness/dimensional_completeness respectively, quality_rules.py's own
// _INDICATOR_ALERT_TYPE mapping).
const BASELINE_BACKED_ALERT_TYPES = new Set(["reconciliation", "completeness"]);

function AlertRow({ project, alert, onAck, onGoToBaseline, readOnly, t, ALERT_TYPE_LABEL, showToast }) {
  const [explanation, setExplanation] = useState(alert.explanation || null);
  const [explaining, setExplaining] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const explain = async () => {
    if (explanation) { setExpanded((e) => !e); return; }
    setExplaining(true);
    try {
      const out = await qualityApi.explainQualityAlert(project.id, alert.id);
      setExplanation(out.explanation);
      setExpanded(true);
    } catch (err) {
      showToast(err.message || t("medallion.quality.explainFailed"));
    } finally {
      setExplaining(false);
    }
  };

  return (
    <div style={{ padding: "8px 10px", borderRadius: 8, background: "var(--bg)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <Badge tone={SEVERITY_TONE[alert.severity] || "neutral"}>{ALERT_TYPE_LABEL[alert.type] || alert.type}</Badge>
          <span style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{alert.message}</span>
        </div>
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} disabled={explaining} onClick={explain}>
            {explaining ? "…" : t("medallion.quality.explain")}
          </button>
          {!readOnly && BASELINE_BACKED_ALERT_TYPES.has(alert.type) && (
            <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={onGoToBaseline}>{t("medallion.quality.updateBaseline")}</button>
          )}
          {!readOnly && (
            <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={() => onAck(alert.id)}>{t("medallion.quality.acknowledge")}</button>
          )}
        </div>
      </div>
      {expanded && explanation && (
        <div style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "var(--surface)", fontSize: 12.5, color: "var(--text-muted)" }}>{explanation}</div>
      )}
    </div>
  );
}

function AlertsZone({ project, alerts, onAck, onGoToBaseline, readOnly }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const ALERT_TYPE_LABEL = t("medallion.quality.alertType", { returnObjects: true });
  if (alerts.length === 0) return null;
  return (
    <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: "rgba(197,61,61,.3)" }}>
      <div className="field-label" style={{ marginBottom: 10 }}>{t("medallion.quality.openAlerts", { count: alerts.length })}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {alerts.map((a) => (
          <AlertRow key={a.id} project={project} alert={a} onAck={onAck} onGoToBaseline={onGoToBaseline} readOnly={readOnly} t={t} ALERT_TYPE_LABEL={ALERT_TYPE_LABEL} showToast={showToast} />
        ))}
      </div>
    </div>
  );
}

function DatasetQualityCard({ dq, degraded, onRecollect, readOnly }) {
  const { t, i18n } = useTranslation();
  const relativeTime = useRelativeTime();
  const CHANGE_LABEL = { added: t("medallion.quality.changeAdded"), removed: t("medallion.quality.changeRemoved"), retyped: t("medallion.quality.changeRetyped") };
  const { dataset_name, latest, trend, volume_variation_pct, schema_changed, schema_diff } = dq;

  if (!latest) {
    return (
      <div className="card" style={{ padding: 16 }}>
        <div style={{ fontFamily: "var(--font-d)", fontWeight: 600, fontSize: 14.5, marginBottom: 6 }}>{dataset_name}</div>
        <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.quality.noDataCollected")}</div>
      </div>
    );
  }

  const varTone = volume_variation_pct != null && volume_variation_pct < 0 ? "var(--danger)" : "var(--text-muted)";
  const testsFailed = latest.tests_failed > 0;

  return (
    <div className="card" style={{ padding: 16, borderColor: degraded ? "rgba(197,61,61,.35)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div style={{ fontFamily: "var(--font-d)", fontWeight: 600, fontSize: 14.5 }}>{dataset_name}</div>
        {!readOnly && (
          <button className="btn-ghost" style={{ padding: "4px 8px" }} title={t("medallion.quality.recollect")} onClick={onRecollect}>{Icon.refresh()}</button>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div>
          <div className="field-label" style={{ marginBottom: 4 }}>{t("medallion.quality.freshness")}</div>
          <div style={{ fontSize: 13 }}>{relativeTime(latest.loaded_at)}</div>
          <div style={{ fontFamily: "var(--font-m)", fontSize: 11, color: "var(--text-muted)" }}>
            {latest.loaded_at ? new Date(latest.loaded_at).toLocaleString(i18n.language) : "—"}
          </div>
        </div>

        <div>
          <div className="field-label" style={{ marginBottom: 4 }}>{t("medallion.quality.volume")}</div>
          <div style={{ fontFamily: "var(--font-m)", fontSize: 15, fontWeight: 500 }}>{latest.row_count ?? "—"}</div>
          {volume_variation_pct != null && (
            <div style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: varTone }}>
              {volume_variation_pct > 0 ? "+" : ""}{volume_variation_pct}% {t("medallion.quality.vsPreviousRun")}
            </div>
          )}
          <div style={{ marginTop: 4 }}><Sparkline points={trend} /></div>
        </div>

        <div>
          <div className="field-label" style={{ marginBottom: 4 }}>{t("medallion.quality.tests")}</div>
          <Badge tone={testsFailed ? "danger" : "accent"}>
            {latest.tests_passed} ✓{testsFailed ? ` / ${latest.tests_failed} ✗` : ""}
          </Badge>
        </div>

        <div>
          <div className="field-label" style={{ marginBottom: 4 }}>{t("medallion.quality.schema")}</div>
          {schema_changed ? (
            <>
              <Badge tone="danger">{t("medallion.quality.schemaChanged")}</Badge>
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
                {schema_diff.map((d, i) => (
                  <div key={i} style={{ fontFamily: "var(--font-m)", fontSize: 11 }}>
                    <b>{d.column}</b> {CHANGE_LABEL[d.change]}
                    {d.change === "retyped" && <span style={{ color: "var(--text-muted)" }}> ({d.old_type} → {d.new_type})</span>}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Badge tone="neutral">{t("medallion.quality.schemaUnchanged")}</Badge>
          )}
        </div>
      </div>
    </div>
  );
}

function RulesPanel({ project, readOnly }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [rule, setRule] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      setRule(await qualityApi.getQualityRules(project.id));
    } catch (err) {
      showToast(err.message || t("medallion.quality.thresholdsLoadFailed"));
    }
  };

  useEffect(() => { load(); }, [project.id]);

  const save = async () => {
    setSaving(true);
    try {
      const saved = await qualityApi.updateQualityRules(project.id, {
        volume_variation_pct: Number(rule.volume_variation_pct),
        min_rows: Number(rule.min_rows),
        freshness_max_hours: Number(rule.freshness_max_hours),
        alert_on_test_failure: rule.alert_on_test_failure,
        alert_on_schema_change: rule.alert_on_schema_change,
        orphan_rate_max: Number(rule.orphan_rate_max),
        join_loss_max_pct: Number(rule.join_loss_max_pct),
        validity_min_pct: Number(rule.validity_min_pct),
        reconciliation_tolerance_pct: Number(rule.reconciliation_tolerance_pct),
        plausibility_max_pct: Number(rule.plausibility_max_pct),
        layer_weights: rule.layer_weights ?? null,
      });
      setRule(saved);
      showToast(t("medallion.quality.thresholdsSaved"));
    } catch (err) {
      showToast(err.message || t("medallion.quality.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  if (!rule) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  return (
    <div className="card" style={{ padding: 16, maxWidth: 520 }}>
      <div className="field">
        <label className="field-label">{t("medallion.quality.volumeVariationLabel")}</label>
        <input className="input" type="number" min={1} max={100} value={rule.volume_variation_pct} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, volume_variation_pct: e.target.value })} />
      </div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.minRowsLabel")}</label>
        <input className="input" type="number" min={0} value={rule.min_rows} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, min_rows: e.target.value })} />
      </div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.freshnessMaxLabel")}</label>
        <input className="input" type="number" min={1} value={rule.freshness_max_hours} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, freshness_max_hours: e.target.value })} />
      </div>
      <div className="field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={rule.alert_on_test_failure} disabled={readOnly} onChange={(e) => setRule({ ...rule, alert_on_test_failure: e.target.checked })} />
        <label style={{ fontSize: 13.5 }}>{t("medallion.quality.alertOnTestFailure")}</label>
      </div>
      <div className="field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={rule.alert_on_schema_change} disabled={readOnly} onChange={(e) => setRule({ ...rule, alert_on_schema_change: e.target.checked })} />
        <label style={{ fontSize: 13.5 }}>{t("medallion.quality.alertOnSchemaChange")}</label>
      </div>

      <div className="field-label" style={{ marginTop: 16, marginBottom: 4 }}>{t("medallion.quality.coherenceThresholdsTitle")}</div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.orphanRateMaxLabel")}</label>
        <input className="input" type="number" min={0} max={1} step={0.001} value={rule.orphan_rate_max} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, orphan_rate_max: e.target.value })} />
      </div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.joinLossMaxLabel")}</label>
        <input className="input" type="number" min={0} max={100} step={0.1} value={rule.join_loss_max_pct} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, join_loss_max_pct: e.target.value })} />
      </div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.validityMinLabel")}</label>
        <input className="input" type="number" min={0} max={100} step={0.1} value={rule.validity_min_pct} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, validity_min_pct: e.target.value })} />
      </div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.reconciliationToleranceLabel")}</label>
        <input className="input" type="number" min={0} max={100} step={0.1} value={rule.reconciliation_tolerance_pct} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, reconciliation_tolerance_pct: e.target.value })} />
      </div>
      <div className="field">
        <label className="field-label">{t("medallion.quality.plausibilityMaxLabel")}</label>
        <input className="input" type="number" min={0} max={100} step={0.1} value={rule.plausibility_max_pct} disabled={readOnly}
          onChange={(e) => setRule({ ...rule, plausibility_max_pct: e.target.value })} />
      </div>

      {!readOnly && (
        <Button className="inline" disabled={saving} onClick={save}>{saving ? t("medallion.quality.saving") : t("medallion.quality.save")}</Button>
      )}
    </div>
  );
}

function ChannelForm({ project, onSaved, onCancel }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [type, setType] = useState("email");
  const [recipients, setRecipients] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [minSeverity, setMinSeverity] = useState("warning");
  const [saving, setSaving] = useState(false);

  const SEVERITY_OPTIONS = [
    { value: "info", label: t("medallion.quality.severityOptions.info") },
    { value: "warning", label: t("medallion.quality.severityOptions.warning") },
    { value: "critical", label: t("medallion.quality.severityOptions.critical") },
  ];

  const save = async () => {
    setSaving(true);
    try {
      const config = type === "email"
        ? { recipients: recipients.split(",").map((s) => s.trim()).filter(Boolean) }
        : { url, secret };
      const channel = await qualityApi.createChannel(project.id, { type, config, enabled: true, min_severity: minSeverity });
      showToast(t("medallion.quality.channelAdded"));
      onSaved(channel);
    } catch (err) {
      showToast(err.message || t("medallion.quality.addFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 520 }}>
      <div className="field">
        <label className="field-label">{t("medallion.quality.channelType")}</label>
        <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="email">Email</option>
          <option value="webhook">Webhook</option>
        </select>
      </div>
      {type === "email" ? (
        <div className="field">
          <label className="field-label">{t("medallion.quality.recipients")}</label>
          <input className="input" value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="oncall@delomid.io, data@delomid.io" />
        </div>
      ) : (
        <>
          <div className="field">
            <label className="field-label">{t("medallion.quality.url")}</label>
            <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
          </div>
          <div className="field">
            <label className="field-label">{t("medallion.quality.webhookSecret")}</label>
            <input className="input" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} />
          </div>
        </>
      )}
      <div className="field">
        <label className="field-label">{t("medallion.quality.minSeverity")}</label>
        <select className="input" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)}>
          {SEVERITY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <Button className="inline" disabled={saving} onClick={save}>{saving ? t("medallion.quality.adding") : t("medallion.quality.addChannel")}</Button>
        <Button variant="ghost" className="inline" onClick={onCancel}>{t("medallion.quality.cancel")}</Button>
      </div>
    </div>
  );
}

function ChannelRow({ project, channel, onChanged }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [testing, setTesting] = useState(false);

  const toggle = async () => {
    try {
      await qualityApi.updateChannel(project.id, channel.id, { enabled: !channel.enabled });
      await onChanged();
    } catch (err) {
      showToast(err.message || t("medallion.quality.updateFailed"));
    }
  };

  const test = async () => {
    setTesting(true);
    try {
      await qualityApi.testChannel(project.id, channel.id);
      showToast(t("medallion.quality.testSent"));
    } catch (err) {
      showToast(err.message || t("medallion.quality.sendFailed"));
    } finally {
      setTesting(false);
    }
  };

  const remove = async () => {
    try {
      await qualityApi.deleteChannel(project.id, channel.id);
      await onChanged();
    } catch (err) {
      showToast(err.message || t("medallion.quality.deleteFailed"));
    }
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 8, background: "var(--bg)" }}>
      <Badge tone={channel.type === "email" ? "accent" : "neutral"}>{channel.type === "email" ? "Email" : "Webhook"}</Badge>
      <div style={{ fontSize: 13, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {channel.type === "email" ? channel.config.recipients.join(", ") : channel.config.url}
      </div>
      <span style={{ fontSize: 11.5, color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>
        {t("medallion.quality.thresholdLabel")} {channel.min_severity}
      </span>
      <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={toggle}>{channel.enabled ? t("medallion.quality.disable") : t("medallion.quality.enable")}</button>
      <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} disabled={testing} onClick={test}>{testing ? "…" : t("medallion.quality.test")}</button>
      <button className="btn-icon" onClick={remove}>{Icon.trash()}</button>
    </div>
  );
}

function NotificationsPanel({ project }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setChannels(await qualityApi.listChannels(project.id));
    } catch (err) {
      showToast(err.message || t("medallion.quality.channelsLoadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [project.id]);

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  return (
    <div style={{ maxWidth: 640 }}>
      {showForm && <ChannelForm project={project} onSaved={() => { setShowForm(false); load(); }} onCancel={() => setShowForm(false)} />}
      {!showForm && <Button variant="ghost" className="inline" style={{ marginBottom: 14 }} onClick={() => setShowForm(true)}>{Icon.plus()} {t("medallion.quality.addChannelBtn")}</Button>}

      {channels.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>{t("medallion.quality.noChannels")}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {channels.map((c) => <ChannelRow key={c.id} project={project} channel={c} onChanged={load} />)}
        </div>
      )}
    </div>
  );
}

function BaselinePanel({ project, readOnly }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [assertions, setAssertions] = useState(null);
  const [rationale, setRationale] = useState({});
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);

  const load = async () => {
    try {
      const out = await qualityApi.getQualityBaseline(project.id);
      setAssertions(out.assertions);
    } catch (err) {
      showToast(err.message || t("medallion.quality.baselineLoadFailed"));
    }
  };

  useEffect(() => { load(); }, [project.id]);

  const suggest = async () => {
    setSuggesting(true);
    try {
      const draft = await qualityApi.suggestQualityBaseline(project.id);
      setAssertions(draft.assertions);
      setRationale(draft.rationale || {});
      showToast(t("medallion.quality.baselineDraftReady"));
    } catch (err) {
      showToast(err.message || t("medallion.quality.baselineSuggestFailed"));
    } finally {
      setSuggesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const saved = await qualityApi.updateQualityBaseline(project.id, { assertions });
      setAssertions(saved.assertions);
      showToast(t("medallion.quality.thresholdsSaved"));
    } catch (err) {
      showToast(err.message || t("medallion.quality.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  if (!assertions) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  const setVolume = (patch) => setAssertions((a) => ({ ...a, expected_source_volume: a.expected_source_volume ? { ...a.expected_source_volume, ...patch } : { source_ref: "", value: null, ...patch } }));
  const clearVolume = () => setAssertions((a) => ({ ...a, expected_source_volume: null }));

  const setListItem = (key, i, value) => setAssertions((a) => ({ ...a, [key]: a[key].map((row, idx) => (idx === i ? { column: value } : row)) }));
  const addListItem = (key) => setAssertions((a) => ({ ...a, [key]: [...a[key], { column: "" }] }));
  const removeListItem = (key, i) => setAssertions((a) => ({ ...a, [key]: a[key].filter((_, idx) => idx !== i) }));

  return (
    <div style={{ maxWidth: 620 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.quality.baselineSubtitle")}</span>
        {!readOnly && (
          <Button variant="ghost" className="inline" style={{ marginLeft: "auto" }} disabled={suggesting} onClick={suggest}>
            {suggesting ? t("medallion.quality.baselineSuggesting") : t("medallion.quality.baselineSuggestBtn")}
          </Button>
        )}
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <div className="field-label">{t("medallion.quality.baselineVolumeTitle")}</div>
          {!readOnly && assertions.expected_source_volume && (
            <button className="btn-icon" onClick={clearVolume}>{Icon.trash()}</button>
          )}
        </div>
        {assertions.expected_source_volume ? (
          <>
            <div className="field">
              <label className="field-label">{t("medallion.quality.baselineSourceRef")}</label>
              <input className="input" disabled={readOnly} value={assertions.expected_source_volume.source_ref}
                onChange={(e) => setVolume({ source_ref: e.target.value })} placeholder="billing" />
            </div>
            <div className="field">
              <label className="field-label">{t("medallion.quality.baselineExpectedValue")}</label>
              <input className="input" type="number" min={0} disabled={readOnly}
                value={assertions.expected_source_volume.value ?? ""}
                onChange={(e) => setVolume({ value: e.target.value === "" ? null : Number(e.target.value) })} />
            </div>
            {rationale.expected_source_volume && <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{rationale.expected_source_volume}</div>}
          </>
        ) : (
          <div style={{ fontSize: 12.5, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 10 }}>
            {t("medallion.quality.baselineVolumeEmpty")}
            {!readOnly && <button className="btn-ghost" style={{ padding: "3px 8px", fontSize: 12 }} onClick={() => setVolume({})}>{t("medallion.quality.baselineAdd")}</button>}
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.quality.baselineMeasuresTitle")}</div>
        {assertions.conservative_measures.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 8 }}>{t("medallion.quality.baselineMeasuresEmpty")}</div>}
        {assertions.conservative_measures.map((m, i) => (
          <div key={i}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
              <input className="input" disabled={readOnly} value={m.column} onChange={(e) => setListItem("conservative_measures", i, e.target.value)} placeholder="total_amount" />
              {!readOnly && <button className="btn-icon" onClick={() => removeListItem("conservative_measures", i)}>{Icon.trash()}</button>}
            </div>
            {rationale[`conservative_measures.${m.column}`] && (
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 8 }}>{rationale[`conservative_measures.${m.column}`]}</div>
            )}
          </div>
        ))}
        {!readOnly && <button className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => addListItem("conservative_measures")}>{Icon.plus()} {t("medallion.quality.baselineAdd")}</button>}
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.quality.baselineDimensionsTitle")}</div>
        {assertions.required_dimensions.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 8 }}>{t("medallion.quality.baselineDimensionsEmpty")}</div>}
        {assertions.required_dimensions.map((d, i) => (
          <div key={i}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
              <input className="input" disabled={readOnly} value={d.column} onChange={(e) => setListItem("required_dimensions", i, e.target.value)} placeholder="department_name" />
              {!readOnly && <button className="btn-icon" onClick={() => removeListItem("required_dimensions", i)}>{Icon.trash()}</button>}
            </div>
            {rationale[`required_dimensions.${d.column}`] && (
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 8 }}>{rationale[`required_dimensions.${d.column}`]}</div>
            )}
          </div>
        ))}
        {!readOnly && <button className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => addListItem("required_dimensions")}>{Icon.plus()} {t("medallion.quality.baselineAdd")}</button>}
      </div>

      {!readOnly && (
        <Button className="inline" disabled={saving} onClick={save}>{saving ? t("medallion.quality.saving") : t("medallion.quality.save")}</Button>
      )}
    </div>
  );
}

function checkParamsSummary(checkType, targetColumn, parameters) {
  switch (checkType) {
    case "type_conformity":
      return `${targetColumn} → ${parameters.target_type}`;
    case "format_validity":
      return `${targetColumn} ~ ${parameters.pattern}`;
    case "intra_row_consistency":
      return parameters.predicate;
    case "plausibility":
      return `${targetColumn} ∈ [${parameters.min ?? "−∞"}, ${parameters.max ?? "+∞"}]`;
    case "conditional_completeness":
      return `${parameters.condition} ⇒ ${parameters.target} NOT NULL`;
    default:
      return "";
  }
}

function MaterializeControl({ project, check, onChanged, readOnly, t, showToast }) {
  const setMode = async (mode) => {
    try {
      const payload = mode === "observe"
        ? { materialize_as_dbt_test: false }
        : { materialize_as_dbt_test: true, dbt_test_severity: mode };
      await qualityApi.updateQualityCheck(project.id, check.id, payload);
      await onChanged();
    } catch (err) {
      showToast(err.message || t("medallion.quality.updateFailed"));
    }
  };
  const mode = !check.materialize_as_dbt_test ? "observe" : check.dbt_test_severity;
  const options = [
    ["observe", t("medallion.quality.materialize.observe")],
    ["warn", t("medallion.quality.materialize.warn")],
    ["error", t("medallion.quality.materialize.error")],
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div
        style={{ display: "inline-flex", gap: 2, padding: 2, borderRadius: 8, background: "var(--surface)", border: "1px solid var(--border)", opacity: check.materializable ? 1 : .5 }}
        title={check.materializable ? undefined : t("medallion.quality.materialize.unsupported")}
      >
        {options.map(([key, label]) => (
          <button
            key={key}
            className="btn-ghost"
            disabled={readOnly || !check.materializable}
            style={{
              padding: "4px 9px", fontSize: 11.5, borderRadius: 6,
              background: mode === key ? (key === "error" ? "var(--danger-soft, rgba(197,61,61,.15))" : "var(--ember-soft)") : "transparent",
              color: mode === key ? (key === "error" ? "var(--danger, #a83232)" : "var(--ember)") : "var(--text-muted)",
              fontWeight: mode === key ? 600 : 400,
              cursor: check.materializable ? "pointer" : "not-allowed",
            }}
            onClick={() => key !== mode && setMode(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {check.materialize_as_dbt_test && check.dbt_form && (
        <div style={{ fontFamily: "var(--font-m)", fontSize: 11, color: "var(--text-muted)" }} title={check.dbt_form}>
          {check.dbt_form}
        </div>
      )}
    </div>
  );
}

function ExportDbtButton({ project }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [open, setOpen] = useState(false);
  const [inventory, setInventory] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    setLoading(true);
    try {
      const { inventory: inv } = await qualityApi.previewDbtExport(project.id);
      setInventory(inv);
    } catch (err) {
      showToast(err.message || t("medallion.quality.export.previewFailed"));
      setInventory({});
    } finally {
      setLoading(false);
    }
  };

  const download = async () => {
    setExporting(true);
    try {
      const blob = await qualityApi.exportDbtProject(project.id);
      saveBlob(blob, `${project.dbt_project_name || project.name}_dbt_export.zip`);
      showToast(t("medallion.quality.export.started"));
      setOpen(false);
    } catch (err) {
      showToast(err.message || t("medallion.quality.export.failed"));
    } finally {
      setExporting(false);
    }
  };

  const layers = inventory ? Object.entries(inventory).filter(([k]) => k !== "_singular_tests") : [];
  const singularCount = inventory?._singular_tests || 0;
  const totalTests = layers.reduce((sum, [, n]) => sum + n, 0) + singularCount;

  return (
    <div style={{ position: "relative" }}>
      <button className="btn-ghost" style={{ padding: "6px 12px", fontSize: 12.5 }} onClick={toggle}>
        {Icon.download ? Icon.download({ width: 14, height: 14 }) : null} {t("medallion.quality.export.action")}
      </button>
      {open && (
        <div className="card" style={{ position: "absolute", right: 0, top: "calc(100% + 6px)", zIndex: 20, width: 280, padding: 14 }}>
          <div style={{ fontFamily: "var(--font-d)", fontWeight: 600, fontSize: 13, marginBottom: 8 }}>{t("medallion.quality.export.previewTitle")}</div>
          {loading ? (
            <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.quality.export.loading")}</div>
          ) : totalTests === 0 ? (
            <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.quality.export.noTests")}</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 10, fontFamily: "var(--font-m)", fontSize: 12 }}>
              {layers.map(([layer, n]) => (
                <div key={layer} style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ textTransform: "capitalize" }}>{layer}</span>
                  <span>{n}</span>
                </div>
              ))}
              {singularCount > 0 && (
                <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-muted)" }}>
                  <span>{t("medallion.quality.export.singularTests")}</span>
                  <span>{singularCount}</span>
                </div>
              )}
            </div>
          )}
          <Button className="inline" disabled={loading || exporting} onClick={download} style={{ width: "100%" }}>
            {exporting ? t("medallion.quality.export.exporting") : t("medallion.quality.export.download")}
          </Button>
        </div>
      )}
    </div>
  );
}

function CheckRow({ project, check, onChanged, readOnly, t, showToast }) {
  const toggle = async () => {
    try {
      await qualityApi.updateQualityCheck(project.id, check.id, { status: check.status === "active" ? "dismissed" : "active" });
      await onChanged();
    } catch (err) {
      showToast(err.message || t("medallion.quality.updateFailed"));
    }
  };
  const remove = async () => {
    try {
      await qualityApi.deleteQualityCheck(project.id, check.id);
      await onChanged();
    } catch (err) {
      showToast(err.message || t("medallion.quality.deleteFailed"));
    }
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 8, background: "var(--bg)" }}>
      <Badge tone={check.status === "active" ? "accent" : "neutral"}>{t(`medallion.quality.indicator.${check.check_type}`, { defaultValue: check.check_type })}</Badge>
      {check.source === "ai_suggested" && <Badge tone="neutral">{t("medallion.quality.aiSuggested")}</Badge>}
      <div style={{ fontFamily: "var(--font-m)", fontSize: 12.5, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={checkParamsSummary(check.check_type, check.target_column, check.parameters)}>
        {checkParamsSummary(check.check_type, check.target_column, check.parameters)}
      </div>
      <MaterializeControl project={project} check={check} onChanged={onChanged} readOnly={readOnly} t={t} showToast={showToast} />
      {!readOnly && (
        <>
          <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={toggle}>
            {check.status === "active" ? t("medallion.quality.disable") : t("medallion.quality.enable")}
          </button>
          <button className="btn-icon" onClick={remove}>{Icon.trash()}</button>
        </>
      )}
    </div>
  );
}

function SuggestionCard({ project, suggestion, onValidated, t, showToast }) {
  const [saving, setSaving] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const validate = async () => {
    setSaving(true);
    try {
      await qualityApi.createQualityCheck(project.id, {
        dataset_id: suggestion.dataset_id, layer: suggestion.layer, check_type: suggestion.check_type,
        target_column: suggestion.target_column || "", parameters: suggestion.parameters, source: "ai_suggested",
      });
      showToast(t("medallion.quality.checkValidated"));
      onValidated();
    } catch (err) {
      showToast(err.message || t("medallion.quality.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  if (dismissed) return null;
  return (
    <div className="card" style={{ padding: 12, marginBottom: 8, borderColor: "rgba(229,114,0,.3)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <Badge tone="accent">{t(`medallion.quality.indicator.${suggestion.check_type}`, { defaultValue: suggestion.check_type })}</Badge>
        <div style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{checkParamsSummary(suggestion.check_type, suggestion.target_column, suggestion.parameters)}</div>
      </div>
      {suggestion.rationale && <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 8 }}>{suggestion.rationale}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} disabled={saving} onClick={validate}>{saving ? "…" : t("medallion.quality.validate")}</button>
        <button className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => setDismissed(true)}>{t("medallion.quality.dismiss")}</button>
      </div>
    </div>
  );
}

function CheckForm({ project, dataset, columns, onSaved, onCancel, t, showToast }) {
  const [checkType, setCheckType] = useState("intra_row_consistency");
  const [targetColumn, setTargetColumn] = useState("");
  const [targetType, setTargetType] = useState(TYPE_CONFORMITY_TYPES[0]);
  const [pattern, setPattern] = useState("");
  const [predicate, setPredicate] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [condition, setCondition] = useState("");
  const [conditionTarget, setConditionTarget] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    let parameters = {};
    if (checkType === "type_conformity") parameters = { target_type: targetType };
    else if (checkType === "format_validity") parameters = { pattern };
    else if (checkType === "intra_row_consistency") parameters = { predicate };
    else if (checkType === "plausibility") parameters = { min: min === "" ? null : Number(min), max: max === "" ? null : Number(max) };
    else if (checkType === "conditional_completeness") parameters = { condition, target: conditionTarget };

    setSaving(true);
    try {
      await qualityApi.createQualityCheck(project.id, {
        dataset_id: dataset.id, layer: dataset.layer, check_type: checkType,
        target_column: ["type_conformity", "format_validity", "plausibility"].includes(checkType) ? targetColumn : "",
        parameters, source: "engineer",
      });
      showToast(t("medallion.quality.checkAdded"));
      onSaved();
    } catch (err) {
      showToast(err.message || t("medallion.quality.addFailed"));
    } finally {
      setSaving(false);
    }
  };

  const columnHint = columns.length > 0 ? t("medallion.quality.availableColumns", { cols: columns.map((c) => c.column).join(", ") }) : "";

  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div className="field">
        <label className="field-label">{t("medallion.quality.checkType")}</label>
        <select className="input" value={checkType} onChange={(e) => setCheckType(e.target.value)}>
          {CHECK_TYPES.map((ct) => <option key={ct} value={ct}>{t(`medallion.quality.indicator.${ct}`, { defaultValue: ct })}</option>)}
        </select>
      </div>

      {["type_conformity", "format_validity", "plausibility"].includes(checkType) && (
        <div className="field">
          <label className="field-label">{t("medallion.quality.targetColumn")}</label>
          <select className="input" value={targetColumn} onChange={(e) => setTargetColumn(e.target.value)}>
            <option value="">—</option>
            {columns.map((c) => <option key={c.column} value={c.column}>{c.column} ({c.type})</option>)}
          </select>
        </div>
      )}

      {checkType === "type_conformity" && (
        <div className="field">
          <label className="field-label">{t("medallion.quality.targetType")}</label>
          <select className="input" value={targetType} onChange={(e) => setTargetType(e.target.value)}>
            {TYPE_CONFORMITY_TYPES.map((tt) => <option key={tt} value={tt}>{tt}</option>)}
          </select>
        </div>
      )}
      {checkType === "format_validity" && (
        <div className="field">
          <label className="field-label">{t("medallion.quality.pattern")}</label>
          <input className="input" style={{ fontFamily: "var(--font-m)" }} value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="^[A-Z]{2}-[0-9]{6}$" />
        </div>
      )}
      {checkType === "intra_row_consistency" && (
        <div className="field">
          <label className="field-label">{t("medallion.quality.predicate")}</label>
          <input className="input" style={{ fontFamily: "var(--font-m)" }} value={predicate} onChange={(e) => setPredicate(e.target.value)} placeholder="end_date >= start_date" />
          {columnHint && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{columnHint}</div>}
        </div>
      )}
      {checkType === "plausibility" && (
        <div style={{ display: "flex", gap: 10 }}>
          <div className="field" style={{ flex: 1 }}>
            <label className="field-label">min</label>
            <input className="input" type="number" value={min} onChange={(e) => setMin(e.target.value)} />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label className="field-label">max</label>
            <input className="input" type="number" value={max} onChange={(e) => setMax(e.target.value)} />
          </div>
        </div>
      )}
      {checkType === "conditional_completeness" && (
        <>
          <div className="field">
            <label className="field-label">{t("medallion.quality.condition")}</label>
            <input className="input" style={{ fontFamily: "var(--font-m)" }} value={condition} onChange={(e) => setCondition(e.target.value)} placeholder="statut = 'facture'" />
            {columnHint && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{columnHint}</div>}
          </div>
          <div className="field">
            <label className="field-label">{t("medallion.quality.conditionTarget")}</label>
            <select className="input" value={conditionTarget} onChange={(e) => setConditionTarget(e.target.value)}>
              <option value="">—</option>
              {columns.map((c) => <option key={c.column} value={c.column}>{c.column}</option>)}
            </select>
          </div>
        </>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <Button className="inline" disabled={saving} onClick={save}>{saving ? t("medallion.quality.adding") : t("medallion.quality.addCheckBtn")}</Button>
        <Button variant="ghost" className="inline" onClick={onCancel}>{t("medallion.quality.cancel")}</Button>
      </div>
    </div>
  );
}

function ChecksPanel({ project, readOnly }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [datasets, setDatasets] = useState([]);
  const [datasetId, setDatasetId] = useState(null);
  const [columns, setColumns] = useState([]);
  const [checks, setChecks] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [suggesting, setSuggesting] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    listDatasets(project.id).then((ds) => {
      if (cancelled) return;
      const list = Array.isArray(ds) ? ds : ds.datasets || [];
      setDatasets(list);
      if (list.length > 0) setDatasetId(list[0].id);
      else setLoading(false);
    }).catch(() => setLoading(false));
    return () => { cancelled = true; };
  }, [project.id]);

  const dataset = useMemo(() => datasets.find((d) => d.id === datasetId) || null, [datasets, datasetId]);

  const loadChecks = async () => {
    if (!datasetId) return;
    setLoading(true);
    try {
      setChecks(await qualityApi.listQualityChecks(project.id, datasetId));
      const colsOut = await getDatasetColumns(project.id, datasetId);
      setColumns(colsOut.columns || []);
    } catch (err) {
      showToast(err.message || t("medallion.quality.checksLoadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { setSuggestions([]); setShowForm(false); loadChecks(); }, [datasetId]);

  const suggest = async () => {
    setSuggesting(true);
    try {
      const out = await qualityApi.suggestQualityChecks(project.id, datasetId);
      setSuggestions((out.suggestions || []).map((s) => ({ ...s, dataset_id: datasetId, layer: dataset?.layer })));
      if (!out.suggestions || out.suggestions.length === 0) showToast(t("medallion.quality.noSuggestions"));
    } catch (err) {
      showToast(err.message || t("medallion.quality.baselineSuggestFailed"));
    } finally {
      setSuggesting(false);
    }
  };

  if (datasets.length === 0 && !loading) {
    return <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>{t("medallion.quality.noDatasets")}</div>;
  }

  return (
    <div style={{ maxWidth: 680 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <select className="input" style={{ maxWidth: 320 }} value={datasetId || ""} onChange={(e) => setDatasetId(Number(e.target.value))}>
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.layer} · {d.name}</option>)}
        </select>
        {!readOnly && (
          <Button variant="ghost" className="inline" disabled={suggesting || !datasetId} onClick={suggest}>
            {suggesting ? t("medallion.quality.baselineSuggesting") : t("medallion.quality.baselineSuggestBtn")}
          </Button>
        )}
      </div>

      {suggestions.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.quality.aiDrafts")}</div>
          {suggestions.map((s, i) => (
            <SuggestionCard key={i} project={project} suggestion={s} onValidated={() => { setSuggestions((arr) => arr.filter((_, idx) => idx !== i)); loadChecks(); }} t={t} showToast={showToast} />
          ))}
        </div>
      )}

      {!readOnly && !showForm && (
        <Button variant="ghost" className="inline" style={{ marginBottom: 14 }} onClick={() => setShowForm(true)}>{Icon.plus()} {t("medallion.quality.addCheckBtn")}</Button>
      )}
      {showForm && dataset && (
        <CheckForm project={project} dataset={dataset} columns={columns} onSaved={() => { setShowForm(false); loadChecks(); }} onCancel={() => setShowForm(false)} t={t} showToast={showToast} />
      )}

      {loading ? (
        <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>
      ) : checks.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>{t("medallion.quality.noChecks")}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {checks.map((c) => <CheckRow key={c.id} project={project} check={c} onChanged={loadChecks} readOnly={readOnly} t={t} showToast={showToast} />)}
        </div>
      )}
    </div>
  );
}

export default function QualityTab({ project, quality, openAlerts, onReload, readOnly = false }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const { isAdmin } = useAuth();
  const [collecting, setCollecting] = useState(false);
  const [view, setView] = useState("overview"); // overview | rules | notifications

  const collectNow = async () => {
    setCollecting(true);
    try {
      await qualityApi.collectQuality(project.id);
      showToast(t("medallion.quality.collectDone"));
      await onReload();
    } catch (err) {
      showToast(err.message || t("medallion.quality.collectFailed"));
    } finally {
      setCollecting(false);
    }
  };

  const ack = async (alertId) => {
    try {
      await qualityApi.ackQualityAlert(project.id, alertId);
      await onReload();
    } catch (err) {
      showToast(err.message || t("medallion.quality.ackFailed"));
    }
  };

  const datasets = quality?.datasets || [];
  const alerts = openAlerts || [];
  const degradedDatasetIds = new Set(alerts.map((a) => a.dataset_id));

  return (
    <>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "center" }}>
        {!readOnly && (
          <Button variant="ghost" className="inline" disabled={collecting} onClick={collectNow}>
            {Icon.refresh()} {collecting ? t("medallion.quality.collecting") : t("medallion.quality.collectNow")}
          </Button>
        )}
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.quality.subtitle")}</span>
        <ExportDbtButton project={project} />
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button className="btn-ghost" style={{ opacity: view === "overview" ? 1 : 0.6 }} onClick={() => setView("overview")}>{t("medallion.quality.overview")}</button>
          <button className="btn-ghost" style={{ opacity: view === "baseline" ? 1 : 0.6 }} onClick={() => setView("baseline")}>{t("medallion.quality.baseline")}</button>
          <button className="btn-ghost" style={{ opacity: view === "checks" ? 1 : 0.6 }} onClick={() => setView("checks")}>{t("medallion.quality.checks")}</button>
          <button className="btn-ghost" style={{ opacity: view === "rules" ? 1 : 0.6 }} onClick={() => setView("rules")}>{t("medallion.quality.thresholds")}</button>
          {isAdmin && (
            <button className="btn-ghost" style={{ opacity: view === "notifications" ? 1 : 0.6 }} onClick={() => setView("notifications")}>{t("medallion.quality.notifications")}</button>
          )}
        </div>
      </div>

      {view === "overview" && (
        <>
          <CoherenceMatrix project={project} />
          <AlertsZone project={project} alerts={alerts} onAck={ack} onGoToBaseline={() => setView("baseline")} readOnly={readOnly} />

          {datasets.length === 0 && (
            <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
              {t("medallion.quality.noGoldTables")}
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
            {datasets.map((dq) => (
              <DatasetQualityCard key={dq.dataset_id} dq={dq} degraded={degradedDatasetIds.has(dq.dataset_id)} onRecollect={collectNow} readOnly={readOnly} />
            ))}
          </div>
        </>
      )}

      {view === "baseline" && <BaselinePanel project={project} readOnly={readOnly} />}

      {view === "checks" && <ChecksPanel project={project} readOnly={readOnly} />}

      {view === "rules" && <RulesPanel project={project} readOnly={readOnly} />}

      {view === "notifications" && isAdmin && <NotificationsPanel project={project} />}
    </>
  );
}
