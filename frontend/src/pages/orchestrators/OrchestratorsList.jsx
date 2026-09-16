import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as airflowInstancesApi from "../../api/airflowInstances.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge, StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import AddInstanceDrawer from "./AddInstanceDrawer.jsx";
import SupersetInstancesSection from "./SupersetInstancesSection.jsx";

const STATUS_COLOR = { reachable: "#2f9e6e", unreachable: "#c53d3d", unknown: "#c98a1c", unsupported_version: "#c53d3d" };

export default function OrchestratorsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const showToast = useToast();
  const [instances, setInstances] = useState([]);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [testingId, setTestingId] = useState(null);

  const STATUS_LABEL = t("orchestrators.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      setInstances(await airflowInstancesApi.listInstances());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleTest = async (id) => {
    setTestingId(id);
    try {
      const result = await airflowInstancesApi.testInstance(id);
      showToast(result.message);
      await load();
    } catch (err) {
      showToast(err.message || t("orchestrators.testFailed"));
    } finally {
      setTestingId(null);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("orchestrators.badge")}</div>
          <h1 className="page-title">{t("orchestrators.title")}</h1>
          <p className="page-desc">{t("orchestrators.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setDrawerOpen(true)}>{Icon.plus()}{t("orchestrators.addInstance")}</button>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("orchestrators.colName")}</th><th>{t("orchestrators.colOrigin")}</th><th>{t("orchestrators.colUrl")}</th>
              <th>{t("orchestrators.colVersion")}</th><th>{t("common.status")}</th><th>{t("orchestrators.colDeployable")}</th>
              <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && instances.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("orchestrators.noInstances")}</td></tr>
            )}
            {instances.map((inst) => (
              <tr key={inst.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/orchestrators/${inst.id}`)}>
                <td>
                  <div className="uc-name">{inst.name}</div>
                </td>
                <td>
                  {inst.origin === "platform"
                    ? <Badge tone="accent">{t("orchestrators.platformManaged")}</Badge>
                    : <Badge tone="neutral">{t("orchestrators.external")}</Badge>}
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{inst.base_url}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{inst.airflow_version || "—"}</td>
                <td>
                  <span className="status">
                    <StatusDot color={STATUS_COLOR[inst.status]} />
                    {STATUS_LABEL[inst.status]}
                  </span>
                </td>
                <td>
                  {inst.capabilities?.deployable
                    ? <Badge tone="accent">{t("orchestrators.deployable")}</Badge>
                    : <Badge tone="neutral">{t("orchestrators.pilotOnly")}</Badge>}
                </td>
                <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={testingId === inst.id} onClick={() => handleTest(inst.id)}>
                    {Icon.refresh()} {testingId === inst.id ? t("orchestrators.testing") : t("common.test")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {drawerOpen && (
        <AddInstanceDrawer
          onClose={() => setDrawerOpen(false)}
          onCreated={(inst) => { setInstances((list) => [...list, inst]); setDrawerOpen(false); showToast(t("orchestrators.createdToast", { name: inst.name })); }}
        />
      )}

      <SupersetInstancesSection />
    </>
  );
}
