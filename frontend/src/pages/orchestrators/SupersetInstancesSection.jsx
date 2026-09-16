import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as supersetApi from "../../api/superset.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge, StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";

const STATUS_COLOR = { reachable: "#2f9e6e", unreachable: "#c53d3d", unknown: "#c98a1c", unsupported_version: "#c53d3d", bad_credentials: "#c53d3d" };

export default function SupersetInstancesSection() {
  const { t } = useTranslation();
  const showToast = useToast();
  const [instances, setInstances] = useState([]);
  const [loading, setLoading] = useState(true);
  const [testingId, setTestingId] = useState(null);

  const STATUS_LABEL = t("superset.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      setInstances(await supersetApi.listInstances());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleTest = async (id) => {
    setTestingId(id);
    try {
      const result = await supersetApi.testInstance(id);
      showToast(result.message);
      await load();
    } catch (err) {
      showToast(err.message || t("superset.testFailed"));
    } finally {
      setTestingId(null);
    }
  };

  return (
    <>
      <div className="field-label" style={{ margin: "28px 0 10px" }}>{t("superset.title")}</div>
      <p className="page-desc" style={{ margin: "0 0 14px" }}>{t("superset.subtitle")}</p>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("orchestrators.colName")}</th><th>{t("orchestrators.colOrigin")}</th><th>{t("orchestrators.colUrl")}</th>
              <th>{t("orchestrators.colVersion")}</th><th>{t("common.status")}</th>
              <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && instances.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("superset.noInstances")}</td></tr>
            )}
            {instances.map((inst) => (
              <tr key={inst.id}>
                <td><div className="uc-name">{inst.name}</div></td>
                <td>
                  {inst.origin === "platform"
                    ? <Badge tone="accent">{t("orchestrators.platformManaged")}</Badge>
                    : <Badge tone="neutral">{t("orchestrators.external")}</Badge>}
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{inst.base_url}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{inst.superset_version || "—"}</td>
                <td>
                  <span className="status">
                    <StatusDot color={STATUS_COLOR[inst.status]} />
                    {STATUS_LABEL[inst.status]}
                  </span>
                </td>
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <a className="btn-ghost" style={{ padding: "6px 10px", textDecoration: "none" }} href={inst.base_url} target="_blank" rel="noreferrer">
                    {Icon.externalLink()} {t("superset.open")}
                  </a>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={testingId === inst.id} onClick={() => handleTest(inst.id)}>
                    {Icon.refresh()} {testingId === inst.id ? t("orchestrators.testing") : t("common.test")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
