import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext.jsx";
import { Icon } from "../components/icons.jsx";
import { Badge, StatusDot } from "../components/ui/Badge.jsx";
import * as dashboardApi from "../api/dashboard.js";

const POLL_MS = 10000;

const RUN_STATE_TONE = { queued: "neutral", running: "accent", success: "accent", failed: "neutral" };
const ALERT_COLOR = { error: "#e5484d", warn: "#e5a000", info: "var(--text-muted)" };

function duration(start, end) {
  if (!start || !end) return "—";
  const s = Math.round((new Date(end) - new Date(start)) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
}

export default function Dashboard() {
  const { t } = useTranslation();
  const { user, isAdmin } = useAuth();
  const navigate = useNavigate();
  const first = (user?.name || "").split(" ")[0];

  const RUN_STATE_LABEL = t("dashboard.runState", { returnObjects: true });
  const STACK_STATE_LABEL = t("dashboard.stackState", { returnObjects: true });

  const timeAgo = (iso) => {
    if (!iso) return "—";
    const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 5) return t("dashboard.justNow");
    if (s < 60) return t("dashboard.secondsAgo", { s });
    if (s < 3600) return t("dashboard.minutesAgo", { m: Math.floor(s / 60) });
    return t("dashboard.hoursAgo", { h: Math.floor(s / 3600) });
  };

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const timerRef = useRef(null);

  const load = async () => {
    try {
      const res = await dashboardApi.getOverview();
      setData(res);
      setError("");
    } catch (err) {
      setError(err.message || t("dashboard.loadError"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    timerRef.current = setInterval(load, POLL_MS);
    return () => clearInterval(timerRef.current);
  }, []);

  const servers = data?.servers;
  const stacks = data?.stacks;
  const sources = data?.sources;
  const medallion = data?.medallion;
  const alerts = data?.alerts || [];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("dashboard.overview")}</div>
          <h1 className="page-title">{t("dashboard.hello", { name: first })}</h1>
          <p className="page-desc">
            {t("dashboard.connectedAs")}{" "}
            {isAdmin ? t("dashboard.connectedAsAdmin") : t("dashboard.connectedAsEngineer")}
          </p>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>
          {data?.generated_at ? t("dashboard.refreshedAt", { time: timeAgo(data.generated_at) }) : ""}
        </div>
      </div>

      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      {!loading && alerts.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 18 }}>
          <div className="field-label" style={{ marginBottom: 10 }}>{t("dashboard.alerts")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {alerts.map((a, i) => (
              <div
                key={i}
                style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, cursor: a.path ? "pointer" : "default" }}
                onClick={() => a.path && navigate(a.path)}
              >
                <StatusDot color={ALERT_COLOR[a.severity] || ALERT_COLOR.info} />
                <span>{a.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="stats">
        <div className="stat">
          <div className="stat-k">{servers ? `${servers.reachable}/${servers.total}` : "—"}</div>
          <div className="stat-l">{t("dashboard.reachableServers")}</div>
        </div>
        <div className="stat">
          <div className="stat-k">{sources ? `${sources.reachable}/${sources.total}` : "—"}</div>
          <div className="stat-l">{t("dashboard.connectedSources")}</div>
        </div>
        <div className="stat">
          <div className="stat-k">{medallion ? medallion.projects_total : "—"}</div>
          <div className="stat-l">{t("dashboard.medallionProjects")}</div>
        </div>
        <div className="stat">
          <div className="stat-k">{stacks ? `${stacks.running}/${stacks.total}` : "—"}</div>
          <div className="stat-l">{t("dashboard.runningStacks")}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginTop: 4 }}>
        <div className="card" style={{ padding: 16 }}>
          <div className="field-label" style={{ marginBottom: 12 }}>{t("dashboard.infrastructure")}</div>
          {loading && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("common.loading")}</div>}
          {!loading && servers?.items.length === 0 && (
            <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("dashboard.noServersRegistered")}</div>
          )}
          {!loading && servers?.items.map((s) => (
            <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", fontSize: 12.5, cursor: "pointer" }} onClick={() => navigate(`/servers/${s.id}`)}>
              <StatusDot color={s.status === "reachable" ? "#2fb344" : s.status === "unreachable" ? "#e5484d" : "var(--text-muted)"} />
              <span style={{ flex: 1 }}>{s.name}</span>
              <span style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)" }}>{s.hostname}</span>
            </div>
          ))}
          {!loading && stacks?.items.length > 0 && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
              {stacks.items.map((s) => (
                <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", fontSize: 12.5, cursor: "pointer" }} onClick={() => navigate(`/servers/${s.server_id}`)}>
                  <span style={{ flex: 1 }}>{s.name}</span>
                  <Badge tone={s.status === "running" ? "accent" : "neutral"}>{STACK_STATE_LABEL[s.status] || s.status}</Badge>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card" style={{ padding: 16 }}>
          <div className="field-label" style={{ marginBottom: 12 }}>
            {t("dashboard.pipelines")} {medallion && t("dashboard.pipelinesSummary", { success: medallion.last_24h.success, failed: medallion.last_24h.failed })}
          </div>
          {loading && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("common.loading")}</div>}
          {!loading && medallion?.active_runs.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              {medallion.active_runs.map((r) => (
                <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", fontSize: 12.5, cursor: "pointer" }} onClick={() => navigate(`/medallion/${r.project_id}`)}>
                  <Badge tone={RUN_STATE_TONE[r.state]}>{RUN_STATE_LABEL[r.state]}</Badge>
                  <span style={{ flex: 1 }}>{r.project_name}</span>
                </div>
              ))}
            </div>
          )}
          {!loading && medallion?.recent_runs.length === 0 && (
            <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("dashboard.noCompletedRuns")}</div>
          )}
          {!loading && medallion?.recent_runs.map((r) => (
            <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", fontSize: 12.5, cursor: "pointer" }} onClick={() => navigate(`/medallion/${r.project_id}`)}>
              <Badge tone={RUN_STATE_TONE[r.state]}>{RUN_STATE_LABEL[r.state]}</Badge>
              <span style={{ flex: 1 }}>{r.project_name}</span>
              <span style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)" }}>{duration(r.started_at, r.finished_at)}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
