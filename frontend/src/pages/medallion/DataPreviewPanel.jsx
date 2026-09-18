import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import { saveBlob } from "../../utils/download.js";

const LIMIT_OPTIONS = [25, 50, 100];

// Module 10 — the data-preview table, embedded read-only in DatasetPanel (Configuration /
// Aperçu tabs) and OriginPanel. `forceSource` is set for origin nodes: always show the
// upstream table, never the project's own materialized output, regardless of build state.
// `dataset` is passed only from DatasetPanel — it unlocks the Module 11 extension "Exporter
// en CSV" action, shown only for a materialized gold table.
export default function DataPreviewPanel({ project, datasetId, dataset = null, forceSource = false, fetchPreview = null }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportsKey, setExportsKey] = useState(0); // bump to refresh the recent-exports list
  const [expandedCell, setExpandedCell] = useState(null); // `${rowIdx}:${colName}` | null

  // Module 18 §7 UX — `fetchPreview` lets a caller with no real MedallionDataset (the instant
  // silver.typed_<name>/silver.structured_<name> preview) reuse this whole panel — same
  // DataSampleOut shape, just a different endpoint — instead of duplicating the table/paging UI.
  const load = async () => {
    setLoading(true);
    try {
      const res = fetchPreview
        ? await fetchPreview({ limit, offset })
        : await medallionApi.getDatasetPreview(project.id, datasetId, { limit, offset, source: forceSource });
      setData(res);
    } catch {
      setData({ status: "unreachable", columns: [], rows: [], truncated: false, target: null });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { setOffset(0); }, [datasetId, forceSource]);
  useEffect(() => { load(); }, [datasetId, forceSource, limit, offset]);

  const seeMore = () => setOffset((o) => o + limit);
  const refresh = () => { setOffset(0); load(); };

  const isGold = dataset?.layer === "gold";
  const canExport = isGold && data?.status === "ok" && data.target?.kind === "materialized";

  const exportCsv = async () => {
    setExporting(true);
    try {
      const blob = await medallionApi.exportDatasetCsv(project.id, dataset.id);
      const iso = new Date().toISOString(); // 2026-09-10T14:30:00.000Z
      const stamp = `${iso.slice(0, 10).replace(/-/g, "")}-${iso.slice(11, 16).replace(":", "")}`;
      saveBlob(blob, `${dataset.name}_${stamp}.csv`);
      showToast(t("medallion.dataPreview.exportStarted"));
      setExportsKey((k) => k + 1);
    } catch {
      showToast(t("medallion.dataPreview.exportFailed"));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
        <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={refresh} disabled={loading}>
          {Icon.refresh()} {t("common.refresh")}
        </button>
        <select className="input" style={{ width: "auto", padding: "5px 8px", fontSize: 12 }} value={limit} onChange={(e) => { setOffset(0); setLimit(Number(e.target.value)); }}>
          {LIMIT_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        {data?.status === "ok" && (
          <>
            <Badge tone={data.target?.kind === "source" ? "neutral" : "accent"}>
              {data.target?.kind === "source" ? t("medallion.dataPreview.badgeSource") : t("medallion.dataPreview.badgeMaterialized")}
            </Badge>
            <span style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)" }}>
              {data.target?.schema_name}.{data.target?.table}
            </span>
          </>
        )}
        {canExport && (
          <button
            className="btn-ghost"
            style={{ padding: "5px 10px", fontSize: 12, marginLeft: "auto" }}
            onClick={exportCsv}
            disabled={exporting}
            title={t("medallion.dataPreview.exportCsvHint")}
          >
            {Icon.download()} {exporting ? t("medallion.dataPreview.exporting") : t("medallion.dataPreview.exportCsv")}
          </button>
        )}
      </div>

      {loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ height: 30, borderRadius: 6, background: "var(--bg)" }} />
          ))}
        </div>
      )}

      {!loading && data?.status === "not_materialized" && (
        <div className="card" style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12.5 }}>
          {t("medallion.dataPreview.notMaterialized")}
        </div>
      )}

      {!loading && data?.status === "not_found" && (
        <div className="card" style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12.5 }}>
          {t("medallion.dataPreview.notFound")}
        </div>
      )}

      {!loading && (data?.status === "unreachable" || data?.status === "timeout") && (
        <div className="error-banner">
          {Icon.warn()}
          <span>{data.status === "timeout" ? t("medallion.dataPreview.timeout") : t("medallion.dataPreview.unreachable")}</span>
        </div>
      )}

      {!loading && data?.status === "ok" && (
        <>
          {data.rows.length === 0 ? (
            <div className="card" style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12.5 }}>
              {t("medallion.dataPreview.empty")}
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th key={c.name}>
                        <div>{c.name}</div>
                        <div style={{ fontFamily: "var(--font-m)", fontWeight: 400, fontSize: 10.5, color: "var(--text-muted)", textTransform: "none" }}>{c.type}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, ri) => (
                    <tr key={ri}>
                      {data.columns.map((c) => {
                        const value = row[c.name];
                        const cellKey = `${ri}:${c.name}`;
                        const isNull = value === null || value === undefined;
                        const text = isNull ? t("medallion.dataPreview.null") : String(value);
                        const expanded = expandedCell === cellKey;
                        return (
                          <td key={c.name} style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>
                            <span
                              style={{
                                color: isNull ? "var(--text-muted)" : "inherit",
                                fontStyle: isNull ? "italic" : "normal",
                                cursor: !isNull && text.length > 60 ? "pointer" : "default",
                                display: "inline-block",
                                maxWidth: expanded ? "none" : 260,
                                overflow: expanded ? "visible" : "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: expanded ? "pre-wrap" : "nowrap",
                                verticalAlign: "top",
                              }}
                              onClick={() => !isNull && text.length > 60 && setExpandedCell(expanded ? null : cellKey)}
                              title={!isNull && text.length > 60 ? t("medallion.dataPreview.clickToExpand") : undefined}
                            >
                              {text}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
            <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("medallion.dataPreview.rowsShown", { count: data.rows.length })}</span>
            {data.truncated && (
              <button className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={seeMore}>
                {t("medallion.dataPreview.seeMore", { n: limit })}
              </button>
            )}
          </div>
        </>
      )}

      {isGold && <RecentExports projectId={project.id} datasetId={dataset.id} refreshKey={exportsKey} />}
    </div>
  );
}

// Module 11 extension — the last few CSV exports of this gold table (who, when, how many
// rows). Purely informational; collapsed by default so it never crowds the preview.
function RecentExports({ projectId, datasetId, refreshKey }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    medallionApi.listProjectExports(projectId, 50)
      .then((all) => { if (alive) setRows(all.filter((r) => r.dataset_id === datasetId).slice(0, 8)); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, [open, projectId, datasetId, refreshKey]);

  return (
    <div style={{ marginTop: 16, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <button
        className="btn-ghost"
        style={{ padding: "4px 6px", fontSize: 12, color: "var(--text-muted)" }}
        onClick={() => setOpen((o) => !o)}
      >
        {Icon.chevronDown({ style: { transform: open ? "none" : "rotate(-90deg)", transition: "transform .15s" } })}
        {" "}{t("medallion.dataPreview.recentExports")}
      </button>
      {open && rows !== null && (
        rows.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "6px 8px" }}>{t("medallion.dataPreview.noExports")}</div>
        ) : (
          <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            {rows.map((r) => (
              <li key={r.id} style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>
                {new Date(r.exported_at).toLocaleString(i18n.language)}
                {r.exported_by ? ` · ${r.exported_by}` : ""}
                {" · "}
                {r.row_count != null ? t("medallion.dataPreview.exportRows", { count: r.row_count }) : t("medallion.dataPreview.exportRowsUnknown")}
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}
