import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";

const STATUS_TONE = { draft: "neutral", built: "accent", deployed: "accent", paused: "neutral", error: "neutral" };
const RUN_STATE_TONE = { queued: "neutral", running: "accent", success: "accent", failed: "neutral" };

export default function AdminOverview() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  const STATUS_LABEL = t("medallion.status", { returnObjects: true });
  const RUN_STATE_LABEL = t("medallion.runs.state", { returnObjects: true });

  useEffect(() => {
    medallionApi.getAdminOverview().then(setGroups).finally(() => setLoading(false));
  }, []);

  const f = filter.trim().toLowerCase();
  const filteredGroups = f
    ? groups
        .map((g) => ({
          ...g,
          projects: g.owner.name.toLowerCase().includes(f) || g.owner.email.toLowerCase().includes(f)
            ? g.projects
            : g.projects.filter((p) => p.name.toLowerCase().includes(f)),
        }))
        .filter((g) => g.projects.length > 0)
    : groups;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("medallion.overview.badge")}</div>
          <h1 className="page-title">{t("medallion.overview.title")}</h1>
          <p className="page-desc">{t("medallion.overview.subtitle")}</p>
        </div>
      </div>

      <input
        className="input" style={{ maxWidth: 360, marginBottom: 20 }}
        placeholder={t("medallion.overview.filterPlaceholder")}
        value={filter} onChange={(e) => setFilter(e.target.value)}
      />

      {loading && <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>}

      {!loading && filteredGroups.length === 0 && (
        <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
          {t("medallion.overview.noProjects")}
        </div>
      )}

      {!loading && filteredGroups.map((g) => (
        <div key={g.owner.id} className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <div style={{ fontFamily: "var(--font-d)", fontWeight: 600, fontSize: 15 }}>{g.owner.name}</div>
            <span style={{ fontFamily: "var(--font-m)", fontSize: 12, color: "var(--text-muted)" }}>{g.owner.email}</span>
            <Badge tone={g.owner.role === "admin" ? "accent" : "neutral"}>
              {g.owner.role === "admin" ? t("sidebar.admin") : t("sidebar.dataEngineer")}
            </Badge>
          </div>

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("medallion.colProject")}</th>
                  <th>{t("medallion.colSchedule")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("medallion.overview.colLastRun")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {g.projects.map((p) => (
                  <tr key={p.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/medallion/overview/${p.id}`)}>
                    <td className="uc-name">{p.name}</td>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 12.5, color: "var(--text-muted)" }}>{p.schedule || t("medallion.manual")}</td>
                    <td>
                      <Badge tone={STATUS_TONE[p.status]}>{STATUS_LABEL[p.status]}</Badge>
                      {p.has_pending_changes && <span style={{ marginLeft: 8 }}><Badge tone="neutral">{t("medallion.toRedeploy")}</Badge></span>}
                    </td>
                    <td>
                      {p.last_run ? (
                        <>
                          <Badge tone={RUN_STATE_TONE[p.last_run.state]}>{RUN_STATE_LABEL[p.last_run.state]}</Badge>
                          <span style={{ marginLeft: 6, fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)" }}>
                            {p.last_run.started_at ? new Date(p.last_run.started_at).toLocaleString(i18n.language) : "—"}
                          </span>
                        </>
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>{t("medallion.overview.noRunYet")}</span>
                      )}
                    </td>
                    <td style={{ textAlign: "right", color: "var(--text-muted)" }}>{Icon.arrowLeft({ style: { transform: "rotate(180deg)" }, width: 16, height: 16 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  );
}
