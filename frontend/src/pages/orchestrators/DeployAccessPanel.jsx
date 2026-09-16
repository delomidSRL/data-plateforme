import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as airflowInstancesApi from "../../api/airflowInstances.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Field, Input, PasswordInput } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import { Badge } from "../../components/ui/Badge.jsx";

export default function DeployAccessPanel({ instance, onInstanceChanged }) {
  const { t, i18n } = useTranslation();
  const showToast = useToast();

  const [sshHost, setSshHost] = useState("");
  const [sshPort, setSshPort] = useState(22);
  const [sshUser, setSshUser] = useState("");
  const [authMethod, setAuthMethod] = useState("password");
  const [secret, setSecret] = useState("");
  const [dagsPath, setDagsPath] = useState("");
  const [dbtPath, setDbtPath] = useState("");
  const [dbtBin, setDbtBin] = useState("dbt");
  const [execPrefix, setExecPrefix] = useState("");
  const [saving, setSaving] = useState(false);

  const [preflight, setPreflight] = useState(null);
  const [running, setRunning] = useState(false);
  const [loadingPreflight, setLoadingPreflight] = useState(true);

  useEffect(() => {
    const da = instance.deploy_access;
    if (da) {
      setSshHost(da.ssh_host || "");
      setSshPort(da.ssh_port || 22);
      setSshUser(da.ssh_user || "");
      setAuthMethod(da.auth_method || "password");
      setDagsPath(da.dags_path || "");
      setDbtPath(da.dbt_path || "");
      setDbtBin(da.dbt_bin || "dbt");
      setExecPrefix(da.exec_prefix || "");
    }
  }, [instance.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    airflowInstancesApi.getPreflight(instance.id).then(setPreflight).catch(() => {}).finally(() => setLoadingPreflight(false));
  }, [instance.id]);

  const saveDeployAccess = async (e) => {
    e?.preventDefault();
    setSaving(true);
    try {
      const updated = await airflowInstancesApi.setDeployAccess(instance.id, {
        ssh_host: sshHost.trim(),
        ssh_port: Number(sshPort),
        ssh_user: sshUser.trim(),
        auth_method: authMethod,
        secret,
        dags_path: dagsPath.trim(),
        dbt_path: dbtPath.trim(),
        dbt_bin: dbtBin.trim() || "dbt",
        exec_prefix: execPrefix.trim(),
      });
      showToast(t("orchestrators.deployAccess.saved"));
      setPreflight(null);
      onInstanceChanged(updated);
    } catch (err) {
      showToast(err.message || t("orchestrators.deployAccess.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const runPreflight = async () => {
    setRunning(true);
    try {
      const result = await airflowInstancesApi.runPreflight(instance.id);
      setPreflight(result);
      showToast(result.deployable ? t("orchestrators.deployAccess.deployableNow") : t("orchestrators.deployAccess.notDeployableYet"));
      const refreshed = await airflowInstancesApi.getInstance(instance.id);
      onInstanceChanged(refreshed);
    } catch (err) {
      showToast(err.message || t("orchestrators.deployAccess.preflightFailed"));
    } finally {
      setRunning(false);
    }
  };

  const valid = sshHost.trim() && sshUser.trim() && dagsPath.trim() && dbtPath.trim();

  return (
    <>
      <div className="page-head" style={{ marginTop: 24, marginBottom: 12 }}>
        <h2 style={{ fontFamily: "var(--font-d)", fontSize: 16, fontWeight: 600 }}>{t("orchestrators.deployAccess.title")}</h2>
        <p className="page-desc" style={{ margin: 0, fontSize: 12.5 }}>{t("orchestrators.deployAccess.subtitle")}</p>
      </div>

      <form onSubmit={saveDeployAccess} className="card" style={{ padding: 16, marginBottom: 20 }}>
        <div className="service-tile-body">
          <Field label={t("orchestrators.deployAccess.sshHost")}><Input value={sshHost} onChange={(e) => setSshHost(e.target.value)} placeholder="10.0.0.5" /></Field>
          <Field label={t("orchestrators.deployAccess.sshPort")}><Input type="number" value={sshPort} onChange={(e) => setSshPort(e.target.value)} /></Field>
          <Field label={t("orchestrators.deployAccess.sshUser")}><Input value={sshUser} onChange={(e) => setSshUser(e.target.value)} /></Field>
          <Field label={t("orchestrators.deployAccess.authMethod")}>
            <div className="seg">
              <button type="button" className={"seg-opt" + (authMethod === "password" ? " selected" : "")} onClick={() => setAuthMethod("password")}>
                <div className="seg-role">{t("orchestrators.deployAccess.password")}</div>
              </button>
              <button type="button" className={"seg-opt" + (authMethod === "key" ? " selected" : "")} onClick={() => setAuthMethod("key")}>
                <div className="seg-role">{t("orchestrators.deployAccess.sshKey")}</div>
              </button>
            </div>
          </Field>
        </div>

        <Field label={authMethod === "key" ? t("orchestrators.deployAccess.privateKey") : t("orchestrators.deployAccess.password")}>
          {authMethod === "key"
            ? <textarea className="input" rows={4} style={{ fontFamily: "var(--font-m)", fontSize: 12 }} value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            : <PasswordInput value={secret} onChange={(e) => setSecret(e.target.value)} />}
        </Field>

        <div className="service-tile-body">
          <Field label={t("orchestrators.deployAccess.dagsPath")}><Input value={dagsPath} onChange={(e) => setDagsPath(e.target.value)} placeholder="/opt/airflow/dags" /></Field>
          <Field label={t("orchestrators.deployAccess.dbtPath")}><Input value={dbtPath} onChange={(e) => setDbtPath(e.target.value)} placeholder="/opt/airflow/dbt" /></Field>
        </div>
        <Field label={t("orchestrators.deployAccess.dbtBin")}>
          <Input value={dbtBin} onChange={(e) => setDbtBin(e.target.value)} placeholder="dbt" />
        </Field>

        <Field label={t("orchestrators.deployAccess.execPrefix")}>
          <Input
            value={execPrefix}
            onChange={(e) => setExecPrefix(e.target.value)}
            placeholder="docker exec -u airflow airflow-worker-1"
            style={{ fontFamily: "var(--font-m)", fontSize: 12 }}
          />
        </Field>
        <p className="page-desc" style={{ margin: "-8px 0 14px", fontSize: 12 }}>{t("orchestrators.deployAccess.execPrefixHint")}</p>

        <Button type="submit" className="inline" style={{ marginTop: 14 }} disabled={!valid || saving}>
          {saving ? t("orchestrators.deployAccess.saving") : t("orchestrators.deployAccess.save")}
        </Button>
      </form>

      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <div className="field-label" style={{ margin: 0 }}>{t("orchestrators.deployAccess.checklist")}</div>
          <Button type="button" onClick={runPreflight} disabled={running}>
            {running ? t("orchestrators.deployAccess.running") : t("orchestrators.deployAccess.runPreflight")}
          </Button>
        </div>

        {loadingPreflight && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>}

        {!loadingPreflight && (!preflight || preflight.checks.length === 0) && (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("orchestrators.deployAccess.noPreflightYet")}</div>
        )}

        {preflight && preflight.checks.length > 0 && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
              <Badge tone={preflight.deployable ? "accent" : "neutral"}>
                {preflight.deployable ? t("orchestrators.deployable") : t("orchestrators.pilotOnly")}
              </Badge>
              {preflight.checked_at && (
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {t("orchestrators.deployAccess.lastCheck", { date: new Date(preflight.checked_at).toLocaleString(i18n.language) })}
                </span>
              )}
              {preflight.stale && <Badge tone="neutral">{t("orchestrators.deployAccess.stale")}</Badge>}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {preflight.checks.map((c) => (
                <div key={c.key} style={{ display: "flex", gap: 10, padding: "8px 10px", borderRadius: "var(--radius)", background: "var(--bg)", border: "1px solid var(--border)" }}>
                  <span style={{ color: c.passed ? "#2f9e6e" : "#c53d3d", flexShrink: 0, marginTop: 1 }}>
                    {c.passed ? Icon.check({ width: 15, height: 15 }) : Icon.x({ width: 15, height: 15 })}
                  </span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{t(`orchestrators.deployAccess.checks.${c.key}`, { defaultValue: c.label })}</div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                      {t(`orchestrators.deployAccess.messages.${c.message_key}`, { ...c.message_params, defaultValue: c.message_params?.value ?? c.message_params?.error ?? "" })}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}
