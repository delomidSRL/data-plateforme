import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";

// Bouton RUN par nœud §3 — onglet "Historique" du détail d'un nœud : tous les runs qui
// l'ont touché (clic RUN ciblé, "Exécuter maintenant", planifié ou Agent), le plus récent en
// premier (déjà l'ordre renvoyé par GET .../run-history). Partagé par DatasetPanel (un vrai
// dataset) et SilverPreviewPopup (les étapes 01_unpacked/02_typed, jamais une MedallionDataset
// — voir workspace_sync.resolve() — d'où `datasetId` = le bronze lui-même + `stage`).
const STATUS_TONE = { success: "accent", failed: "danger", running: "accent", pending: "neutral" };

export default function RunHistoryPanel({ project, datasetId, stage }) {
  const { t, i18n } = useTranslation();
  const [history, setHistory] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setError("");
    medallionApi.getDatasetRunHistory(project.id, datasetId, stage)
      .then((res) => { if (!cancelled) setHistory(res); })
      .catch((err) => { if (!cancelled) setError(err.message || t("medallion.run.historyFailed")); });
    return () => { cancelled = true; };
  }, [project.id, datasetId, stage]);

  if (error) return <div className="error-banner">{Icon.warn()}<span>{error}</span></div>;
  if (history === null) return <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>;
  if (history.length === 0) {
    return <div style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: "28px 0" }}>{t("medallion.run.historyEmpty")}</div>;
  }

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{t("medallion.run.historyDate")}</th>
            <th>{t("common.status")}</th>
            <th>{t("medallion.run.historyDuration")}</th>
            <th>{t("medallion.run.historyScope")}</th>
          </tr>
        </thead>
        <tbody>
          {history.map((h) => (
            <tr key={h.run_id}>
              <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>
                {h.started_at ? new Date(h.started_at).toLocaleString(i18n.language) : "—"}
              </td>
              <td>
                <Badge tone={STATUS_TONE[h.status] || "neutral"}>{t(`medallion.run.status.${h.status}`)}</Badge>
                {h.status === "failed" && h.error && (
                  <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, maxWidth: 320 }}>{h.error}</div>
                )}
              </td>
              <td style={{ fontSize: 12 }}>
                {h.finished_at && h.started_at ? `${Math.max(0, Math.round((new Date(h.finished_at) - new Date(h.started_at)) / 1000))}s` : "—"}
              </td>
              <td style={{ fontSize: 12 }}>{h.full_run ? t("medallion.run.historyFullRun") : t("medallion.run.historyTargeted")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
