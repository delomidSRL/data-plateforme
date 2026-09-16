import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as stacksApi from "../../api/stacks.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";

const STATE_COLOR = { running: "#2f9e6e", exited: "#c53d3d", restarting: "#c98a1c" };

export default function MonitoringTab({ serverId, stack, onStackChanged }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [containers, setContainers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyReport, setVerifyReport] = useState(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setContainers(await stacksApi.stackStatus(serverId, stack.id));
    } catch (err) {
      setError(err.message || t("servers.monitoring.statusFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); setVerifyReport(null); }, [stack?.id]);

  const runAction = async (action, label) => {
    setBusy(true);
    try {
      await action(serverId, stack.id);
      showToast(t("servers.monitoring.actionDone", { label }));
      await load();
    } catch (err) {
      showToast(err.message || t("servers.monitoring.actionFailed", { label }));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setVerifying(true);
    setVerifyReport(null);
    try {
      const report = await stacksApi.verifyStack(serverId, stack.id);
      setVerifyReport(report);
      showToast(report.ok ? t("servers.monitoring.verifySuccess") : t("servers.monitoring.verifyFailed"));
      if (onStackChanged) onStackChanged((s) => (s ? { ...s, status: report.status } : s));
      await load();
    } catch (err) {
      showToast(err.message || t("servers.monitoring.verifyError"));
    } finally {
      setVerifying(false);
    }
  };

  return (
    <>
      <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
        <Button variant="ghost" onClick={load} disabled={loading}>{Icon.refresh()} {t("common.refresh")}</Button>
        <Button variant="ghost" onClick={() => runAction(stacksApi.restartStack, t("servers.monitoring.restarting"))} disabled={busy}>{Icon.refresh()} {t("servers.monitoring.restart")}</Button>
        <Button variant="ghost" onClick={() => runAction(stacksApi.stopStack, t("servers.monitoring.stopping"))} disabled={busy}>{Icon.stop()} {t("servers.monitoring.stop")}</Button>
        <Button onClick={verify} disabled={verifying}>{Icon.check({ width: 14, height: 14 })} {verifying ? t("servers.monitoring.verifying") : t("servers.monitoring.verify")}</Button>
      </div>

      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      {verifyReport && (
        <div
          className="error-banner"
          style={verifyReport.ok
            ? { background: "rgba(47,158,110,.08)", borderColor: "rgba(47,158,110,.3)", color: "#2f9e6e" }
            : { background: "rgba(197,61,61,.08)", borderColor: "rgba(197,61,61,.3)", color: "#c53d3d" }}
        >
          {verifyReport.ok ? Icon.check({ width: 16, height: 16 }) : Icon.warn()}
          <span>
            {verifyReport.ok
              ? t("servers.monitoring.verifySuccess")
              : t("servers.monitoring.verifyFailedDetail", {
                  services: verifyReport.services.filter((s) => !s.ok).map((s) => `${s.name} (${s.detail})`).join(", ") || t("servers.monitoring.noContainers"),
                })}
          </span>
        </div>
      )}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>{t("servers.monitoring.colContainer")}</th><th>{t("servers.monitoring.colImage")}</th><th>{t("servers.monitoring.colState")}</th><th>{t("servers.monitoring.colHealth")}</th><th>{t("servers.monitoring.colPorts")}</th></tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && containers.length === 0 && !error && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("servers.monitoring.noContainers")}</td></tr>
            )}
            {containers.map((c) => (
              <tr key={c.name}>
                <td className="uc-name">{c.name}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{c.image}</td>
                <td><span className="status"><StatusDot color={STATE_COLOR[c.state] || "#5B6774"} />{c.state}</span></td>
                <td style={{ color: "var(--text-muted)" }}>{c.health || "—"}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{c.ports || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
