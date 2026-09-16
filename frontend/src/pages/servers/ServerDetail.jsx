import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as serversApi from "../../api/servers.js";
import * as stacksApi from "../../api/stacks.js";
import { Icon } from "../../components/icons.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import StackTab from "./StackTab.jsx";
import MonitoringTab from "./MonitoringTab.jsx";
import AirflowTab from "./AirflowTab.jsx";

const STACK_STATUS_TONE = { draft: "neutral", deploying: "accent", running: "accent", stopped: "neutral", error: "neutral" };

export default function ServerDetail() {
  const { t } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const [server, setServer] = useState(null);
  const [stack, setStack] = useState(null);
  const [tab, setTab] = useState("stack");
  const [loading, setLoading] = useState(true);

  const STACK_STATUS_LABEL = t("servers.stackStatus", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      const [srv, stacks] = await Promise.all([serversApi.getServer(id), stacksApi.listStacks(id)]);
      setServer(srv);
      setStack(stacks[0] || null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id]);

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;
  if (!server) return <div style={{ color: "var(--text-muted)" }}>{t("servers.notFound")}</div>;

  const airflowEnabled = stack?.services?.airflow?.enabled;

  return (
    <>
      <div className="page-head">
        <div>
          <button className="link" style={{ display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 10 }} onClick={() => navigate("/servers")}>
            {Icon.arrowLeft({ width: 14, height: 14 })} {t("servers.title")}
          </button>
          <h1 className="page-title">{server.name}</h1>
          <p className="page-desc" style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{server.ssh_user}@{server.hostname}:{server.ssh_port}</p>
        </div>
        {stack && <Badge tone={STACK_STATUS_TONE[stack.status]}>{STACK_STATUS_LABEL[stack.status]}</Badge>}
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 22, borderBottom: "1px solid var(--border)" }}>
        {[
          ["stack", t("servers.tabs.stack")],
          ["monitoring", t("servers.tabs.monitoring")],
          ["airflow", t("servers.tabs.airflow")],
        ].map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            style={{
              padding: "10px 16px", fontSize: 13.5, fontWeight: 500,
              color: tab === key ? "var(--text)" : "var(--text-muted)",
              borderBottom: tab === key ? "2px solid var(--ember)" : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "stack" && <StackTab serverId={id} stack={stack} server={server} onStackChanged={setStack} />}
      {tab === "monitoring" && (
        stack ? <MonitoringTab serverId={id} stack={stack} onStackChanged={setStack} /> : <EmptyStackNotice onGo={() => setTab("stack")} />
      )}
      {tab === "airflow" && (
        airflowEnabled ? <AirflowTab serverId={id} stack={stack} server={server} /> : <EmptyStackNotice onGo={() => setTab("stack")} text={t("servers.enableAirflowDesc")} />
      )}
    </>
  );
}

function EmptyStackNotice({ onGo, text }) {
  const { t } = useTranslation();
  return (
    <div className="placeholder">
      <div className="placeholder-ring">{Icon.flow()}</div>
      <div className="placeholder-title">{t("servers.noStackConfigured")}</div>
      <div className="placeholder-desc">{text || t("servers.noStackDesc")}</div>
      <button className="btn-ghost" style={{ marginTop: 14 }} onClick={onGo}>{t("servers.goToStackTab")}</button>
    </div>
  );
}
