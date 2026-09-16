import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input, PasswordInput } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as airflowInstancesApi from "../../api/airflowInstances.js";

export default function AddInstanceDrawer({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [verifyTls, setVerifyTls] = useState(true);

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const valid = name.trim() && baseUrl.trim() && username.trim() && password.trim();

  const buildPayload = () => ({
    name: name.trim(),
    base_url: baseUrl.trim(),
    username: username.trim(),
    password,
    verify_tls: verifyTls,
  });

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const created = await airflowInstancesApi.createInstance(buildPayload());
      onCreated(created);
    } catch (err) {
      setError(err.message || t("orchestrators.add.addFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    if (!valid) return;
    setTesting(true);
    setTestResult(null);
    setError("");
    try {
      const result = await airflowInstancesApi.testInstanceAdhoc(buildPayload());
      setTestResult(result);
    } catch (err) {
      setError(err.message || t("orchestrators.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  return (
    <Drawer title={t("orchestrators.add.title")} description={t("orchestrators.add.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      {testResult && (
        <div className="error-banner" style={testResult.reachable ? { background: "rgba(47,158,110,.1)", borderColor: "rgba(47,158,110,.35)", color: "#2f9e6e" } : {}}>
          {testResult.reachable ? Icon.check({ width: 16, height: 16 }) : Icon.warn()}
          <span>{testResult.message}</span>
        </div>
      )}

      <div className="error-banner" style={{ background: "rgba(197,138,28,.08)", borderColor: "rgba(197,138,28,.3)", color: "#c98a1c" }}>
        {Icon.warn()}
        <span>{t("orchestrators.add.serviceAccountNote")}</span>
      </div>

      <form onSubmit={submit}>
        <Field label={t("orchestrators.add.name")}>
          <Input placeholder={t("orchestrators.add.namePlaceholder")} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>

        <Field label={t("orchestrators.add.baseUrl")}>
          <Input placeholder="http://airflow.client.example.com:8080" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </Field>

        <Field label={t("orchestrators.add.username")}>
          <Input value={username} onChange={(e) => setUsername(e.target.value)} icon={Icon.user()} />
        </Field>

        <Field label={t("orchestrators.add.password")}>
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>

        <label className="service-tile-checkline" style={{ marginBottom: 15 }}>
          <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} /> {t("orchestrators.add.verifyTls")}
        </label>

        <div className="modal-actions">
          <Button type="button" variant="ghost" disabled={!valid || testing} onClick={handleTest}>
            {testing ? t("orchestrators.testing") : t("orchestrators.add.testConnection")}
          </Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("orchestrators.add.adding") : t("common.add")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
