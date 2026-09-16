import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as serversApi from "../../api/servers.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge, StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import AddServerDrawer from "./AddServerDrawer.jsx";

const STATUS_COLOR = { reachable: "#2f9e6e", unreachable: "#c53d3d", unknown: "#c98a1c" };

export default function ServersList() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const showToast = useToast();
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [testingId, setTestingId] = useState(null);

  const STATUS_LABEL = t("servers.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      setServers(await serversApi.listServers());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleTest = async (id) => {
    setTestingId(id);
    try {
      const result = await serversApi.testServer(id);
      showToast(result.reachable ? t("servers.testSuccess", { version: result.docker_version || "?" }) : result.message);
      await load();
    } catch (err) {
      showToast(err.message || t("servers.testFailed"));
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async (id, name) => {
    try {
      await serversApi.deleteServer(id);
      setServers((s) => s.filter((x) => x.id !== id));
      showToast(t("servers.deletedToast", { name }));
    } catch (err) {
      showToast(err.message || t("servers.deleteFailed"));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("servers.badge")}</div>
          <h1 className="page-title">{t("servers.title")}</h1>
          <p className="page-desc">{t("servers.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setDrawerOpen(true)}>{Icon.plus()}{t("servers.addServer")}</button>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("servers.colServer")}</th><th>{t("common.status")}</th><th>{t("servers.colDocker")}</th><th>{t("servers.colLastTest")}</th>
              <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && servers.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("servers.noServers")}</td></tr>
            )}
            {servers.map((s) => (
              <tr key={s.id}>
                <td>
                  <div className="uc-name" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {s.name}
                    <Badge tone={s.environment === "prod" ? "danger" : "neutral"}>
                      {s.environment === "prod" ? t("servers.environmentProd") : t("servers.environmentDev")}
                    </Badge>
                  </div>
                  <div className="uc-mail">{s.ssh_user}@{s.hostname}:{s.ssh_port}</div>
                </td>
                <td>
                  <span className="status">
                    <StatusDot color={STATUS_COLOR[s.status]} />
                    {STATUS_LABEL[s.status]}
                  </span>
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{s.docker_version || "—"}</td>
                <td style={{ color: "var(--text-muted)", fontFamily: "var(--font-m)", fontSize: 12 }}>
                  {s.last_checked_at ? new Date(s.last_checked_at).toLocaleString(i18n.language) : "—"}
                </td>
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={testingId === s.id} onClick={() => handleTest(s.id)}>
                    {Icon.refresh()} {testingId === s.id ? t("servers.testing") : t("common.test")}
                  </button>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => navigate(`/servers/${s.id}`)}>{t("servers.open")}</button>
                  <button className="btn-icon" onClick={() => handleDelete(s.id, s.name)} title={t("common.delete")}>{Icon.trash()}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {drawerOpen && (
        <AddServerDrawer
          onClose={() => setDrawerOpen(false)}
          onCreated={(s) => { setServers((list) => [...list, s]); setDrawerOpen(false); showToast(t("servers.createdToast", { name: s.name })); }}
        />
      )}
    </>
  );
}
