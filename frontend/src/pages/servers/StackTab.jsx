import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as stacksApi from "../../api/stacks.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Switch } from "../../components/ui/Switch.jsx";
import { Icon } from "../../components/icons.jsx";

const DEFAULTS = {
  postgres: { enabled: false, port: 5432, user: "dataplateforme", password: "", db_name: "dataplateforme" },
  minio: { enabled: false, api_port: 9000, console_port: 9001, access_key: "dataplateforme", secret_key: "" },
  superset: { enabled: false, port: 8088, admin_user: "admin", admin_password: "", secret_key: "" },
  airflow: { enabled: false, web_port: 8080, admin_user: "airflow", admin_password: "", load_examples: false, enable_flower: false, dbt_enabled: true },
  jupyter: { enabled: false, port: 8888, token: "", mount_dbt: true },
};

// Mirrors the backend's SECRET_FIELDS (app/services/stack_secrets.py). GET returns these
// masked as "••••••••" — every one must be blanked here, or saving the form re-submits the
// mask literal as if it were a real new secret and permanently overwrites the real value.
const SECRET_FIELDS = {
  postgres: ["password"],
  minio: ["secret_key"],
  superset: ["admin_password", "secret_key", "metadata_db_password"],
  airflow: ["admin_password", "fernet_key", "jwt_secret", "webserver_secret_key"],
  jupyter: ["token"],
};

function blankSecrets(service, block) {
  const blanked = { ...block };
  for (const field of SECRET_FIELDS[service] || []) blanked[field] = "";
  return blanked;
}

function buildInitialForm(stack, defaultName) {
  if (!stack) return { name: defaultName, ...structuredClone(DEFAULTS) };
  const s = stack.services || {};
  return {
    name: stack.name,
    postgres: blankSecrets("postgres", { ...DEFAULTS.postgres, ...s.postgres }),
    minio: blankSecrets("minio", { ...DEFAULTS.minio, ...s.minio }),
    superset: blankSecrets("superset", { ...DEFAULTS.superset, ...s.superset }),
    airflow: blankSecrets("airflow", { ...DEFAULTS.airflow, ...s.airflow }),
    jupyter: blankSecrets("jupyter", { ...DEFAULTS.jupyter, ...s.jupyter }),
  };
}

export default function StackTab({ serverId, stack, server, onStackChanged }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [form, setForm] = useState(() => buildInitialForm(stack, t("servers.stack.defaultStackName")));
  const [saving, setSaving] = useState(false);
  const [previewYaml, setPreviewYaml] = useState(null);
  const [previewWarnings, setPreviewWarnings] = useState([]);
  const [previewing, setPreviewing] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [deployReport, setDeployReport] = useState(null);

  useEffect(() => { setForm(buildInitialForm(stack, t("servers.stack.defaultStackName"))); setPreviewYaml(null); setDeployReport(null); }, [stack?.id]);

  const patchService = (service, field, value) => setForm((f) => ({ ...f, [service]: { ...f[service], [field]: value } }));

  const refresh = async () => {
    const stacks = await stacksApi.listStacks(serverId);
    onStackChanged(stacks[0] || null);
    return stacks[0];
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload = { name: form.name, services: { postgres: form.postgres, minio: form.minio, superset: form.superset, airflow: form.airflow, jupyter: form.jupyter } };
      if (stack) {
        await stacksApi.updateStack(serverId, stack.id, payload);
      } else {
        await stacksApi.createStack(serverId, payload);
      }
      await refresh();
      showToast(t("servers.stack.configSaved"));
    } catch (err) {
      showToast(err.message || t("servers.stack.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const preview = async () => {
    if (!stack) { showToast(t("servers.stack.saveFirst")); return; }
    setPreviewing(true);
    try {
      const res = await stacksApi.previewStack(serverId, stack.id);
      setPreviewYaml(res.compose_yaml);
      setPreviewWarnings(res.warnings || []);
    } catch (err) {
      showToast(err.message || t("servers.stack.previewFailed"));
    } finally {
      setPreviewing(false);
    }
  };

  const deployNow = async () => {
    if (!stack) { showToast(t("servers.stack.saveFirst")); return; }
    setDeploying(true);
    setDeployReport(null);
    try {
      const report = await stacksApi.deployStack(serverId, stack.id);
      setDeployReport({ ok: true, ...report });
      await refresh();
      showToast(t("servers.stack.stackDeployed"));
    } catch (err) {
      setDeployReport({ ok: false, error: err.message });
      showToast(err.message || t("servers.stack.deployFailed"));
    } finally {
      setDeploying(false);
    }
  };

  const anyEnabled = form.postgres.enabled || form.minio.enabled || form.superset.enabled || form.airflow.enabled || form.jupyter.enabled;
  const jupyterUrl = server ? `http://${server.hostname}:${form.jupyter.port}` : null;
  const jupyterRunning = stack?.status === "running" && stack?.services?.jupyter?.enabled;

  return (
    <>
      <Field label={t("servers.stack.stackName")}>
        <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
      </Field>

      <ServiceTile title="PostgreSQL" hint={t("servers.stack.postgresHint")} enabled={form.postgres.enabled} onToggle={(v) => patchService("postgres", "enabled", v)}>
        <Field label={t("servers.stack.port")}><Input type="number" value={form.postgres.port} onChange={(e) => patchService("postgres", "port", Number(e.target.value))} /></Field>
        <Field label={t("servers.stack.user")}><Input value={form.postgres.user} onChange={(e) => patchService("postgres", "user", e.target.value)} /></Field>
        <Field label={t("servers.stack.password")}><Input type="password" placeholder={stack ? t("servers.stack.keepCurrent") : ""} value={form.postgres.password} onChange={(e) => patchService("postgres", "password", e.target.value)} /></Field>
        <Field label={t("servers.stack.database")}><Input value={form.postgres.db_name} onChange={(e) => patchService("postgres", "db_name", e.target.value)} /></Field>
      </ServiceTile>

      <ServiceTile title="MinIO" hint={t("servers.stack.minioHint")} enabled={form.minio.enabled} onToggle={(v) => patchService("minio", "enabled", v)}>
        <Field label={t("servers.stack.apiPort")}><Input type="number" value={form.minio.api_port} onChange={(e) => patchService("minio", "api_port", Number(e.target.value))} /></Field>
        <Field label={t("servers.stack.consolePort")}><Input type="number" value={form.minio.console_port} onChange={(e) => patchService("minio", "console_port", Number(e.target.value))} /></Field>
        <Field label={t("servers.stack.accessKey")}><Input value={form.minio.access_key} onChange={(e) => patchService("minio", "access_key", e.target.value)} /></Field>
        <Field label={t("servers.stack.secretKey")}><Input type="password" placeholder={stack ? t("servers.stack.keepCurrent") : ""} value={form.minio.secret_key} onChange={(e) => patchService("minio", "secret_key", e.target.value)} /></Field>
      </ServiceTile>

      <ServiceTile title="Superset" hint={t("servers.stack.supersetHint")} enabled={form.superset.enabled} onToggle={(v) => patchService("superset", "enabled", v)}>
        <Field label={t("servers.stack.port")}><Input type="number" value={form.superset.port} onChange={(e) => patchService("superset", "port", Number(e.target.value))} /></Field>
        <Field label={t("servers.stack.adminUser")}><Input value={form.superset.admin_user} onChange={(e) => patchService("superset", "admin_user", e.target.value)} /></Field>
        <Field label={t("servers.stack.adminPassword")}><Input type="password" placeholder={stack ? t("servers.stack.keepCurrent") : ""} value={form.superset.admin_password} onChange={(e) => patchService("superset", "admin_password", e.target.value)} /></Field>
        <Field label={t("servers.stack.secretKeyOptional")}><Input type="password" placeholder={t("servers.stack.autoGenerated")} value={form.superset.secret_key} onChange={(e) => patchService("superset", "secret_key", e.target.value)} /></Field>
      </ServiceTile>

      <ServiceTile title="Airflow 3.0.6" hint={t("servers.stack.airflowHint")} enabled={form.airflow.enabled} onToggle={(v) => patchService("airflow", "enabled", v)}>
        <Field label={t("servers.stack.webPort")}><Input type="number" value={form.airflow.web_port} onChange={(e) => patchService("airflow", "web_port", Number(e.target.value))} /></Field>
        <Field label={t("servers.stack.adminUser")}><Input value={form.airflow.admin_user} onChange={(e) => patchService("airflow", "admin_user", e.target.value)} /></Field>
        <Field label={t("servers.stack.adminPassword")}><Input type="password" placeholder={stack ? t("servers.stack.keepCurrent") : ""} value={form.airflow.admin_password} onChange={(e) => patchService("airflow", "admin_password", e.target.value)} /></Field>
        <div />
        <label className="service-tile-checkline">
          <input type="checkbox" checked={form.airflow.dbt_enabled} onChange={(e) => patchService("airflow", "dbt_enabled", e.target.checked)} /> {t("servers.stack.installDbt")}
        </label>
        <label className="service-tile-checkline">
          <input type="checkbox" checked={form.airflow.load_examples} onChange={(e) => patchService("airflow", "load_examples", e.target.checked)} /> {t("servers.stack.loadExamples")}
        </label>
      </ServiceTile>

      <ServiceTile title="Jupyter" hint={t("servers.stack.jupyterHint")} enabled={form.jupyter.enabled} onToggle={(v) => patchService("jupyter", "enabled", v)}>
        <Field label={t("servers.stack.port")}><Input type="number" value={form.jupyter.port} onChange={(e) => patchService("jupyter", "port", Number(e.target.value))} /></Field>
        <Field label={t("servers.stack.accessToken")}><Input type="password" placeholder={stack ? t("servers.stack.keepCurrent") : t("servers.stack.autoGenerated")} value={form.jupyter.token} onChange={(e) => patchService("jupyter", "token", e.target.value)} /></Field>
        <label className="service-tile-checkline">
          <input type="checkbox" checked={form.jupyter.mount_dbt} onChange={(e) => patchService("jupyter", "mount_dbt", e.target.checked)} /> {t("servers.stack.mountDbt")}
        </label>
        {jupyterRunning && jupyterUrl && (
          <a className="btn-ghost" href={jupyterUrl} target="_blank" rel="noreferrer" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6, gridColumn: "span 2", width: "fit-content" }}>
            {Icon.externalLink()} {t("servers.stack.openJupyter")}
          </a>
        )}
      </ServiceTile>

      {anyEnabled && (form.airflow.enabled) && (
        <div className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>
          {Icon.warn()}<span>{t("servers.stack.airflowRamWarning")}</span>
        </div>
      )}
      {form.jupyter.enabled && (
        <div className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>
          {Icon.warn()}<span>{t("servers.stack.jupyterDiskWarning")}</span>
        </div>
      )}

      <div className="modal-actions" style={{ marginTop: 4 }}>
        <Button variant="ghost" onClick={preview} disabled={previewing || !stack}>{previewing ? t("servers.stack.generatingPreview") : t("servers.stack.previewCompose")}</Button>
        <Button variant="ghost" onClick={save} disabled={saving}>{saving ? t("servers.stack.saving") : stack ? t("servers.stack.saveChanges") : t("servers.stack.createDraftStack")}</Button>
        <Button onClick={deployNow} disabled={deploying || !stack}>{deploying ? t("servers.stack.deploying") : t("servers.stack.deploy")}</Button>
      </div>

      {previewWarnings.map((w, i) => (
        <div key={i} className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>{Icon.warn()}<span>{w}</span></div>
      ))}

      {previewYaml && (
        <div className="card" style={{ padding: 16, marginTop: 16 }}>
          <div className="field-label" style={{ marginBottom: 10 }}>{t("servers.stack.generatedCompose")}</div>
          <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 420, overflowY: "auto", color: "var(--text)" }}>{previewYaml}</pre>
        </div>
      )}

      {deployReport && (
        <div className="card" style={{ padding: 16, marginTop: 16 }}>
          {deployReport.ok ? (
            <>
              <div className="field-label" style={{ marginBottom: 8, color: "#2f9e6e" }}>{t("servers.stack.deploySuccess")}</div>
              <div style={{ fontSize: 13, marginBottom: 6 }}>{t("servers.stack.containers")} {deployReport.containers?.join(", ") || "—"}</div>
              <div style={{ fontSize: 13 }}>{t("servers.stack.registeredSources")} {deployReport.registered_sources?.join(", ") || t("servers.stack.none")}</div>
            </>
          ) : (
            <div style={{ fontSize: 13, color: "#c53d3d" }}>{deployReport.error}</div>
          )}
        </div>
      )}
    </>
  );
}

function ServiceTile({ title, hint, enabled, onToggle, children }) {
  const { t } = useTranslation();
  return (
    <div className="service-tile">
      <div className="service-tile-head">
        <div>
          <div className="service-tile-title">{title}</div>
          <div className="service-tile-hint">{hint}</div>
        </div>
        <Switch checked={enabled} onChange={onToggle} label={t("servers.stack.enableService", { name: title })} />
      </div>
      {enabled && <div className="service-tile-body">{children}</div>}
    </div>
  );
}
