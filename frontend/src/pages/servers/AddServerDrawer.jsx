import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as serversApi from "../../api/servers.js";

export default function AddServerDrawer({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [sshPort, setSshPort] = useState(22);
  const [sshUser, setSshUser] = useState("");
  const [authMethod, setAuthMethod] = useState("password");
  const [secret, setSecret] = useState("");
  const [environment, setEnvironment] = useState("dev"); // Module 17 — required, "un serveur = un environnement"

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const valid = name.trim() && hostname.trim() && sshUser.trim() && secret.trim() && sshPort > 0;

  const buildPayload = () => ({
    name: name.trim(),
    hostname: hostname.trim(),
    ssh_port: Number(sshPort),
    ssh_user: sshUser.trim(),
    auth_method: authMethod,
    secret,
    environment,
  });

  const handleTest = async () => {
    if (!valid) return;
    setTesting(true);
    setTestResult(null);
    setError("");
    try {
      const result = await serversApi.testServerAdhoc(buildPayload());
      setTestResult(result);
    } catch (err) {
      setError(err.message || t("servers.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const created = await serversApi.createServer(buildPayload());
      onCreated(created);
    } catch (err) {
      setError(err.message || t("servers.add.addFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={t("servers.add.title")} description={t("servers.add.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      {testResult && (
        <div className={testResult.reachable ? "error-banner" : "error-banner"} style={testResult.reachable ? { background: "rgba(47,158,110,.1)", borderColor: "rgba(47,158,110,.35)", color: "#2f9e6e" } : {}}>
          {testResult.reachable ? Icon.check({ width: 16, height: 16 }) : Icon.warn()}
          <span>{testResult.message}{testResult.docker_version ? ` — Docker ${testResult.docker_version}` : ""}</span>
        </div>
      )}

      <form onSubmit={submit}>
        <Field label={t("servers.add.name")}>
          <Input placeholder={t("servers.add.namePlaceholder")} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>

        <Field label={t("servers.add.host")}>
          <Input placeholder={t("servers.add.hostPlaceholder")} value={hostname} onChange={(e) => setHostname(e.target.value)} />
        </Field>

        <Field label={t("servers.add.environment")}>
          <div className="seg">
            <button type="button" className={"seg-opt" + (environment === "dev" ? " selected" : "")} onClick={() => setEnvironment("dev")}>
              <div className="seg-role">{t("servers.environmentDev")}</div>
            </button>
            <button type="button" className={"seg-opt" + (environment === "prod" ? " selected" : "")} onClick={() => setEnvironment("prod")}>
              <div className="seg-role">{t("servers.environmentProd")}</div>
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("servers.add.environmentHint")}</div>
        </Field>

        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Field label={t("servers.add.sshPort")}>
              <Input type="number" value={sshPort} onChange={(e) => setSshPort(e.target.value)} />
            </Field>
          </div>
          <div style={{ flex: 2 }}>
            <Field label={t("servers.add.sshUser")}>
              <Input placeholder={t("servers.add.sshUserPlaceholder")} value={sshUser} onChange={(e) => setSshUser(e.target.value)} />
            </Field>
          </div>
        </div>

        <Field label={t("servers.add.authMethod")}>
          <div className="seg">
            <button type="button" className={"seg-opt" + (authMethod === "password" ? " selected" : "")} onClick={() => setAuthMethod("password")}>
              <div className="seg-role">{t("servers.add.password")}</div>
            </button>
            <button type="button" className={"seg-opt" + (authMethod === "key" ? " selected" : "")} onClick={() => setAuthMethod("key")}>
              <div className="seg-role">{t("servers.add.privateKey")}</div>
            </button>
          </div>
        </Field>

        {authMethod === "password" ? (
          <Field label={t("servers.add.sshPassword")}>
            <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} icon={Icon.lock()} />
          </Field>
        ) : (
          <Field label={t("servers.add.sshPrivateKeyPem")}>
            <textarea
              className="input"
              style={{ fontFamily: "var(--font-m)", fontSize: 12, minHeight: 120, resize: "vertical" }}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </Field>
        )}

        <div className="modal-actions">
          <Button type="button" variant="ghost" disabled={!valid || testing} onClick={handleTest}>
            {testing ? t("servers.testing") : t("servers.add.testConnection")}
          </Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("servers.add.adding") : t("common.add")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
