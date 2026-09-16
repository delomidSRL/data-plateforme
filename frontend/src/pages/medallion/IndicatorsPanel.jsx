import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import IndicatorEditorList from "../../components/IndicatorEditorList.jsx";

// Module 12 étape 2/3 — Mistral (or the heuristic fallback) proposes; the engineer includes,
// excludes, renames, and reassigns columns; "Générer" persists the contract and builds the
// `dp_` charts + dashboard in Superset. "Régénérer" replays the persisted contract without
// ever calling Mistral again; "Re-suggérer" explicitly calls it again.
export default function IndicatorsPanel({ project, dataset, readOnly = false }) {
  const { t, i18n } = useTranslation();
  const [publication, setPublication] = useState(null);
  const [dashboardStatus, setDashboardStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [suggesting, setSuggesting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [busy, setBusy] = useState(false); // régénérer / supprimer
  const [error, setError] = useState(null);
  const [source, setSource] = useState(null);
  const [columns, setColumns] = useState([]);
  const [indicators, setIndicators] = useState(null); // null = showing the summary/intro screen, not the validation list

  const loadStatus = async () => {
    const [pub, dash] = await Promise.all([
      medallionApi.getDatasetPublication(project.id, dataset.id),
      medallionApi.getDashboardStatus(project.id, dataset.id),
    ]);
    setPublication(pub);
    setDashboardStatus(dash);
    return dash;
  };

  useEffect(() => {
    setLoading(true);
    loadStatus().finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, dataset.id]);

  const suggest = async () => {
    setSuggesting(true);
    setError(null);
    try {
      const res = await medallionApi.suggestIndicators(project.id, dataset.id);
      if (res.status !== "ok") {
        setError(res.message || t(`medallion.suggest.status.${res.status}`));
        setIndicators([]);
        return;
      }
      setSource(res.source);
      setColumns(res.columns || []);
      setIndicators(res.indicators.map((ind) => ({ ...ind, included: true })));
    } catch (err) {
      setError(err.message || t("medallion.suggest.failed"));
      setIndicators([]);
    } finally {
      setSuggesting(false);
    }
  };

  const patch = (i, changes) => setIndicators((list) => list.map((ind, idx) => (idx === i ? { ...ind, ...changes } : ind)));
  const toggleDimension = (i, colName) => setIndicators((list) => list.map((ind, idx) => {
    if (idx !== i) return ind;
    const has = ind.dimension_columns.includes(colName);
    return { ...ind, dimension_columns: has ? ind.dimension_columns.filter((c) => c !== colName) : [...ind.dimension_columns, colName] };
  }));
  const removeIndicator = (i) => setIndicators((list) => list.filter((_, idx) => idx !== i));

  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const included = indicators.filter((i) => i.included).map(({ included: _included, ...rest }) => rest);
      const res = await medallionApi.generateDashboard(project.id, dataset.id, { indicators: included, source: source || "manual" });
      if (res.status !== "ok") {
        setError(res.message || t(`medallion.suggest.generateStatus.${res.status}`));
        return;
      }
      await loadStatus();
      setIndicators(null);
    } catch (err) {
      setError(err.message || t("medallion.suggest.generateFailed"));
    } finally {
      setGenerating(false);
    }
  };

  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await medallionApi.regenerateDashboard(project.id, dataset.id);
      if (res.status !== "ok") {
        setError(res.message || t(`medallion.suggest.generateStatus.${res.status}`));
        return;
      }
      await loadStatus();
    } finally {
      setBusy(false);
    }
  };

  const removeDashboard = async () => {
    setBusy(true);
    try {
      await medallionApi.deleteDashboard(project.id, dataset.id);
      await loadStatus();
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  if (!publication?.published) {
    return <div className="card" style={{ padding: 16, textAlign: "center", fontSize: 13, color: "var(--text-muted)" }}>{t("medallion.suggest.notPublishedYet")}</div>;
  }

  const includedCount = (indicators || []).filter((i) => i.included).length;
  const showSummary = indicators === null && dashboardStatus?.generated;

  return (
    <div>
      {error && (
        <div className="error-banner" style={{ marginBottom: 12 }}>{Icon.warn()}<span>{error}</span></div>
      )}

      {showSummary && (
        <div className="card" style={{ padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <Badge tone="accent">{t("medallion.suggest.generatedBadge", { count: dashboardStatus.charts_count })}</Badge>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {t("medallion.suggest.lastGenerated")}{" "}
            <span style={{ fontFamily: "var(--font-m)" }}>{dashboardStatus.last_generated_at ? new Date(dashboardStatus.last_generated_at).toLocaleString(i18n.language) : "—"}</span>
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn-ghost" style={{ padding: "6px 10px", textDecoration: "none" }} href={dashboardStatus.dashboard_url} target="_blank" rel="noreferrer">
              {Icon.externalLink()} {t("medallion.suggest.openInSuperset")}
            </a>
            {!readOnly && (
              <>
                <button type="button" className="btn-ghost" style={{ padding: "6px 10px" }} disabled={busy} onClick={regenerate}>
                  {Icon.refresh()} {busy ? t("medallion.suggest.regenerating") : t("medallion.suggest.regenerate")}
                </button>
                <button type="button" className="btn-ghost" style={{ padding: "6px 10px" }} disabled={suggesting} onClick={suggest}>
                  {Icon.wand()} {t("medallion.suggest.resuggest")}
                </button>
                <button type="button" className="btn-ghost" style={{ padding: "6px 10px" }} disabled={busy} onClick={removeDashboard}>
                  {Icon.trash()} {t("medallion.suggest.deleteDashboard")}
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {!showSummary && indicators === null && (
        <div className="card" style={{ padding: 16, textAlign: "center" }}>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>{t("medallion.suggest.intro")}</div>
          {!readOnly && (
            <Button disabled={suggesting} onClick={suggest}>
              {Icon.wand()} {suggesting ? t("medallion.suggest.suggesting") : t("medallion.suggest.proposeAction")}
            </Button>
          )}
        </div>
      )}

      {indicators !== null && indicators.length > 0 && (
        <>
          <IndicatorEditorList
            indicators={indicators} columns={columns} source={source} readOnly={readOnly}
            onPatch={patch} onToggleDimension={toggleDimension} onRemove={removeIndicator}
            onResuggest={suggest} resuggesting={suggesting}
          />

          {!readOnly && (
            <div style={{ marginTop: 14 }}>
              <Button disabled={includedCount === 0 || generating} onClick={generate}>
                {generating ? t("medallion.suggest.generating") : t("medallion.suggest.generateAction")}
              </Button>
            </div>
          )}
        </>
      )}

      {indicators !== null && indicators.length === 0 && !error && (
        <div style={{ fontSize: 13, color: "var(--text-muted)", textAlign: "center", padding: 16 }}>{t("medallion.suggest.noIndicators")}</div>
      )}
    </div>
  );
}
