import { useTranslation } from "react-i18next";
import { Badge } from "./ui/Badge.jsx";
import { Icon } from "./icons.jsx";

// The original 5, plus every catalogue type whose shape (superset_publish.SIMPLE_FLAT_VIZ_TYPES)
// is exactly one measure slot + at most one dimension slot + at most one temporal slot — the only
// ones the flat metric_column/aggregation/dimension_columns/time_column fields below can build.
// Every other catalogue type (pivot_table_v2's rows/columns, bubble_v2's x/y/size...) needs more
// than the flat contract can express and stays reachable only via an AI proposal (rendered
// read-only further down, see `hasSlots`).
export const VIZ_TYPES = [
  "big_number_total", "echarts_timeseries_line", "echarts_timeseries_bar", "pie", "table",
  "big_number", "pop_kpi", "funnel", "gauge_chart", "treemap_v2", "ag_grid", "sunburst_v2",
  "partition", "word_cloud", "cal_heatmap", "time_pivot", "time_table", "bullet",
  "echarts_area", "echarts_timeseries_smooth", "echarts_timeseries_step", "echarts_timeseries", "echarts_timeseries_scatter",
];
export const AGGREGATIONS = ["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX"];
// Types whose catalogue shape has a temporal-role slot (needs the time_column selector).
const TEMPORAL_VIZ_TYPES = new Set([
  "echarts_timeseries_line", "big_number", "pop_kpi", "cal_heatmap", "time_pivot", "time_table",
  "echarts_area", "echarts_timeseries_smooth", "echarts_timeseries_step", "echarts_timeseries", "echarts_timeseries_scatter",
]);
// Types with NO dimension-role slot at all (the dimension badges below would have no effect).
const NO_DIMENSION_VIZ_TYPES = new Set(["big_number_total", "big_number", "pop_kpi", "cal_heatmap", "time_pivot", "time_table"]);

// Shared indicator-list editor — the checkbox/title/viz_type/aggregation/metric/dimension
// editing UI, used both by Module 12's standalone IndicatorsPanel.jsx and by Module 13's
// AgentTab.jsx (embedded dashboard-review step). Purely presentational: all state lives in
// the caller, this only renders `indicators` and reports changes via the callbacks.
export default function IndicatorEditorList({ indicators, columns, source, readOnly = false, onPatch, onToggleDimension, onRemove, onResuggest, resuggesting = false }) {
  const { t } = useTranslation();
  const measureColumns = columns.filter((c) => c.role === "measure");
  const dimensionColumns = columns.filter((c) => c.role !== "measure");
  const temporalColumns = columns.filter((c) => c.role === "temporal");
  const includedCount = indicators.filter((i) => i.included).length;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        {source && <Badge tone={source === "ai" ? "accent" : "neutral"}>{source === "ai" ? t("medallion.suggest.sourceAi") : t("medallion.suggest.sourceHeuristic")}</Badge>}
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{t("medallion.suggest.includedCount", { count: includedCount, total: indicators.length })}</span>
        {!readOnly && onResuggest && (
          <button type="button" className="btn-ghost" style={{ padding: "4px 8px", marginLeft: "auto" }} disabled={resuggesting} onClick={onResuggest}>
            {Icon.refresh()} {t("medallion.suggest.resuggest")}
          </button>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {indicators.map((ind, i) => {
          const hasSlots = Boolean(ind.slots && Object.keys(ind.slots).length);
          return (
          <div key={i} className="card" style={{ padding: 12, opacity: ind.included ? 1 : 0.55 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <input type="checkbox" checked={ind.included} disabled={readOnly} onChange={(e) => onPatch(i, { included: e.target.checked })} />
              <input
                className="input" style={{ flex: 1, fontSize: 12.5 }} value={ind.title} disabled={readOnly}
                onChange={(e) => onPatch(i, { title: e.target.value })}
              />
              {!readOnly && (
                <button type="button" className="btn-icon" onClick={() => onRemove(i)} title={t("common.delete")}>{Icon.trash()}</button>
              )}
            </div>

            {hasSlots ? (
              // Annexe catalogue viz §7 — a multi-slot type (beyond the original 5): the flat
              // metric_column/aggregation/dimension_columns editors below don't apply (Superset's
              // params shape for this type is built from `slots`, not those fields), so this is
              // shown read-only rather than as a dropdown that can't represent the real value.
              <div style={{ marginBottom: 8 }}>
                <Badge tone="neutral">{t(`medallion.suggest.vizTypeLabel.${ind.viz_type}`, { defaultValue: ind.viz_type })}</Badge>
                {ind.ranking && (
                  <span style={{ marginLeft: 6 }}>
                    <Badge tone="accent">{ind.ranking.direction === "bottom" ? `Bottom ${ind.ranking.limit}` : `Top ${ind.ranking.limit}`}</Badge>
                  </span>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                  {Object.entries(ind.slots).map(([slotName, fill]) => (
                    <span key={slotName} className="badge badge-neutral" style={{ fontSize: 10.5 }}>
                      {slotName}: {(fill.columns || []).join(", ")}{fill.aggregation ? ` (${fill.aggregation})` : ""}
                    </span>
                  ))}
                </div>
              </div>
            ) : (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
                  <label style={{ fontSize: 11 }}>
                    <div style={{ color: "var(--text-muted)", marginBottom: 2 }}>{t("medallion.suggest.vizType")}</div>
                    <select className="input" value={ind.viz_type} disabled={readOnly} onChange={(e) => onPatch(i, { viz_type: e.target.value })}>
                      {VIZ_TYPES.map((v) => <option key={v} value={v}>{t(`medallion.suggest.vizTypeLabel.${v}`)}</option>)}
                    </select>
                  </label>
                  <label style={{ fontSize: 11 }}>
                    <div style={{ color: "var(--text-muted)", marginBottom: 2 }}>{t("medallion.suggest.aggregation")}</div>
                    <select className="input" value={ind.aggregation} disabled={readOnly} onChange={(e) => onPatch(i, { aggregation: e.target.value })}>
                      {AGGREGATIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </label>
                  <label style={{ fontSize: 11 }}>
                    <div style={{ color: "var(--text-muted)", marginBottom: 2 }}>{t("medallion.suggest.metricColumn")}</div>
                    <select className="input" value={ind.metric_column} disabled={readOnly} onChange={(e) => onPatch(i, { metric_column: e.target.value })}>
                      {(measureColumns.length ? measureColumns : columns).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                    </select>
                  </label>
                  {TEMPORAL_VIZ_TYPES.has(ind.viz_type) && (
                    <label style={{ fontSize: 11 }}>
                      <div style={{ color: "var(--text-muted)", marginBottom: 2 }}>{t("medallion.suggest.timeColumn")}</div>
                      <select className="input" value={ind.time_column || ""} disabled={readOnly} onChange={(e) => onPatch(i, { time_column: e.target.value || null })}>
                        <option value="">—</option>
                        {(temporalColumns.length ? temporalColumns : columns).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                      </select>
                    </label>
                  )}
                </div>

                {!NO_DIMENSION_VIZ_TYPES.has(ind.viz_type) && (
                  <div style={{ fontSize: 11 }}>
                    <div style={{ color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.suggest.dimensionColumns")}</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {dimensionColumns.map((c) => (
                        <button
                          key={c.name} type="button" disabled={readOnly}
                          className={"badge" + (ind.dimension_columns.includes(c.name) ? " badge-accent" : " badge-neutral")}
                          style={{ cursor: readOnly ? "default" : "pointer", border: "none" }}
                          onClick={() => onToggleDimension(i, c.name)}
                        >
                          {c.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
          );
        })}
      </div>
    </>
  );
}
