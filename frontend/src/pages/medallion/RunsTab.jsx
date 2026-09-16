import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";

const STATE_TONE = { queued: "neutral", running: "accent", success: "accent", failed: "neutral" };

function duration(start, end) {
  if (!start || !end) return "—";
  const s = Math.round((new Date(end) - new Date(start)) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
}

export default function RunsTab({ project, readOnly = false }) {
  const { t, i18n } = useTranslation();
  const showToast = useToast();
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [showBackfill, setShowBackfill] = useState(false);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const STATE_LABEL = t("medallion.runs.state", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      const list = await medallionApi.listRuns(project.id);
      setRuns(list);
      const active = list.filter((r) => r.state === "queued" || r.state === "running");
      for (const r of active) {
        medallionApi.getRun(project.id, r.id).catch(() => {});
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [project.id]);

  const runNow = async (backfill) => {
    setRunning(true);
    try {
      const payload = backfill ? { backfill_from: from, backfill_to: to } : {};
      await medallionApi.runProject(project.id, payload);
      showToast(backfill ? t("medallion.runs.backfillTriggered") : t("medallion.runs.runTriggered"));
      setShowBackfill(false);
      await load();
    } catch (err) {
      showToast(err.message || t("medallion.runs.triggerFailed"));
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
        {!readOnly && (
          <>
            <Button className="inline" disabled={running || !project.dag_id} onClick={() => runNow(false)}>{Icon.play()} {t("medallion.runs.runNow")}</Button>
            <Button variant="ghost" className="inline" disabled={!project.dag_id} onClick={() => setShowBackfill((s) => !s)}>{t("medallion.runs.backfill")}</Button>
          </>
        )}
        <Button variant="ghost" className="inline" onClick={load}>{Icon.refresh()} {t("common.refresh")}</Button>
        {!readOnly && !project.dag_id && <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.runs.deployToRun")}</span>}
      </div>

      {!readOnly && showBackfill && (
        <div className="card" style={{ padding: 14, marginBottom: 16, display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div>
            <label className="field-label">{t("medallion.runs.from")}</label>
            <input type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="field-label">{t("medallion.runs.to")}</label>
            <input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <Button className="inline" disabled={!from || !to || running} onClick={() => runNow(true)}>
            {running ? t("medallion.runs.reprocessing") : t("medallion.runs.reprocess", { from: from || "…", to: to || "…" })}
          </Button>
        </div>
      )}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>{t("medallion.runs.colStatus")}</th><th>{t("medallion.runs.colStarted")}</th><th>{t("medallion.runs.colDuration")}</th><th>{t("medallion.runs.colRowsPerLayer")}</th><th>{t("medallion.runs.colTests")}</th></tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && runs.length === 0 && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("medallion.runs.noRuns")}</td></tr>}
            {runs.map((r) => (
              <tr key={r.id}>
                <td><Badge tone={STATE_TONE[r.state]}>{STATE_LABEL[r.state]}</Badge></td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{r.started_at ? new Date(r.started_at).toLocaleString(i18n.language) : "—"}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{duration(r.started_at, r.finished_at)}</td>
                <td style={{ fontSize: 12.5 }}>
                  {Object.keys(r.layer_stats || {}).length
                    ? Object.entries(r.layer_stats).map(([k, v]) => `${k}: ${v}`).join(" · ")
                    : "—"}
                </td>
                <td style={{ fontSize: 12.5 }}>
                  {r.tests_summary?.passed != null
                    ? <span>{r.tests_summary.passed} ✓ {r.tests_summary.failed > 0 ? `/ ${r.tests_summary.failed} ✗` : ""}</span>
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
