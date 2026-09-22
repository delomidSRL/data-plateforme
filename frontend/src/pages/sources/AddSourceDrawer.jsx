import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as sourcesApi from "../../api/sources.js";

const TYPES = [
  { value: "postgresql", label: "PostgreSQL", defaultPort: 5432 },
  { value: "mysql", label: "MySQL", defaultPort: 3306 },
  { value: "oracle", label: "Oracle", defaultPort: 1521 },
  { value: "minio", label: "MinIO", defaultPort: 9000 },
];

export default function AddSourceDrawer({ onClose, onCreated, onUpdated, source }) {
  const { t } = useTranslation();
  const isEdit = Boolean(source);
  const [type, setType] = useState(source?.type || "postgresql");
  const [name, setName] = useState(source?.name || "");
  const [host, setHost] = useState(source?.host || "");
  const [port, setPort] = useState(source?.port || 5432);
  const [databaseName, setDatabaseName] = useState(source?.database_name || "");
  const [username, setUsername] = useState(source?.username || "");
  const [secret, setSecret] = useState("");
  const [secure, setSecure] = useState(source?.options?.secure || false);
  const [region, setRegion] = useState(source?.options?.region || "");
  const [bucket, setBucket] = useState(source?.options?.bucket || "");

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const isMinio = type === "minio";
  const isOracle = type === "oracle";

  const changeType = (t) => {
    // Type is fixed once a source exists (not part of SourceUpdate) — this drawer's type
    // selector is create-only; edit mode renders it read-only (see below), so only the
    // create-mode click handler ever reaches here.
    setType(t.value);
    setPort(t.defaultPort);
  };

  const fieldsValid = name.trim() && host.trim() && username.trim() && port > 0 && (isMinio || databaseName.trim());
  const canTest = fieldsValid && secret.trim();
  // Editing: the secret is optional (blank ⇒ keep the currently stored one) — everything else
  // must still be filled in, same as creation.
  const canSave = isEdit ? fieldsValid : fieldsValid && secret.trim();

  const buildPayload = () => ({
    name: name.trim(),
    type,
    host: host.trim(),
    port: Number(port),
    database_name: isMinio ? null : databaseName.trim(),
    username: username.trim(),
    secret,
    options: isMinio ? { secure, region: region.trim() || undefined, bucket: bucket.trim() || undefined } : {},
  });

  const handleTest = async () => {
    if (!canTest) return;
    setTesting(true);
    setTestResult(null);
    setError("");
    try {
      const { name: _n, ...testPayload } = buildPayload();
      const result = await sourcesApi.testSourceAdhoc(testPayload);
      setTestResult(result);
    } catch (err) {
      setError(err.message || t("sources.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const submit = async (e) => {
    e?.preventDefault();
    if (!canSave) return;
    setBusy(true);
    setError("");
    try {
      if (isEdit) {
        const { type: _t, secret: payloadSecret, ...rest } = buildPayload();
        const payload = payloadSecret.trim() ? { ...rest, secret: payloadSecret } : rest;
        const updated = await sourcesApi.updateSource(source.id, payload);
        onUpdated(updated);
      } else {
        const created = await sourcesApi.createSource(buildPayload());
        onCreated(created);
      }
    } catch (err) {
      setError(err.message || t(isEdit ? "sources.edit.editFailed" : "sources.add.addFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={t(isEdit ? "sources.edit.title" : "sources.add.title")} description={t(isEdit ? "sources.edit.description" : "sources.add.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      {testResult && (
        <div className="error-banner" style={testResult.reachable ? { background: "rgba(47,158,110,.1)", borderColor: "rgba(47,158,110,.35)", color: "#2f9e6e" } : {}}>
          {testResult.reachable ? Icon.check({ width: 16, height: 16 }) : Icon.warn()}
          <span>
            {testResult.message}
            {testResult.reachable && testResult.latency_ms != null ? ` — ${testResult.latency_ms} ms` : ""}
            {testResult.version ? ` — ${testResult.version}` : ""}
          </span>
        </div>
      )}

      <form onSubmit={submit}>
        <Field label={t("sources.add.sourceType")}>
          <div className="seg" style={{ flexWrap: "wrap" }}>
            {TYPES.map((ty) => (
              <button
                key={ty.value} type="button" disabled={isEdit}
                className={"seg-opt" + (type === ty.value ? " selected" : "")}
                style={{ flex: "1 1 45%", opacity: isEdit && type !== ty.value ? 0.5 : 1, cursor: isEdit ? "default" : "pointer" }}
                onClick={() => !isEdit && changeType(ty)}
              >
                <div className="seg-role">{type === ty.value && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{ty.label}</div>
              </button>
            ))}
          </div>
          {isEdit && <p className="page-desc" style={{ margin: "6px 0 0", fontSize: 11.5 }}>{t("sources.edit.typeLocked")}</p>}
        </Field>

        <Field label={t("sources.add.name")}>
          <Input placeholder={t("sources.add.namePlaceholder")} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>

        <Field label={isMinio ? t("sources.add.endpoint") : t("sources.add.host")}>
          <Input placeholder={isMinio ? "s3.exemple.com" : "10.0.0.5"} value={host} onChange={(e) => setHost(e.target.value)} />
        </Field>

        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Field label={t("sources.add.port")}>
              <Input type="number" value={port} onChange={(e) => setPort(e.target.value)} />
            </Field>
          </div>
          {!isMinio && (
            <div style={{ flex: 2 }}>
              <Field label={isOracle ? t("sources.add.serviceName") : t("sources.add.database")}>
                <Input value={databaseName} onChange={(e) => setDatabaseName(e.target.value)} />
              </Field>
            </div>
          )}
        </div>

        <Field label={isMinio ? t("sources.add.accessKey") : t("sources.add.user")}>
          <Input value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>

        <Field label={isMinio ? t("sources.add.secretKey") : t("sources.add.password")}>
          <Input
            type="password" value={secret} onChange={(e) => setSecret(e.target.value)} icon={Icon.lock()}
            placeholder={isEdit ? t("sources.edit.secretPlaceholder") : ""}
          />
        </Field>
        {isEdit && <p className="page-desc" style={{ margin: "-8px 0 14px", fontSize: 11.5 }}>{t("sources.edit.secretHint")}</p>}

        {isMinio && (
          <>
            <label className="service-tile-checkline" style={{ marginBottom: 15 }}>
              <input type="checkbox" checked={secure} onChange={(e) => setSecure(e.target.checked)} /> {t("sources.add.secureConnection")}
            </label>
            <Field label={t("sources.add.region")}>
              <Input placeholder="us-east-1" value={region} onChange={(e) => setRegion(e.target.value)} />
            </Field>
            <Field label={t("sources.add.bucket")}>
              <Input placeholder={t("sources.add.bucketPlaceholder")} value={bucket} onChange={(e) => setBucket(e.target.value)} />
            </Field>
            <p className="page-desc" style={{ margin: "-8px 0 14px", fontSize: 12 }}>{t("sources.add.bucketHint")}</p>
          </>
        )}

        <div className="modal-actions">
          <Button type="button" variant="ghost" disabled={!canTest || testing} onClick={handleTest}>
            {testing ? t("sources.testing") : t("sources.add.testConnection")}
          </Button>
          <Button type="submit" disabled={!canSave || busy}>
            {busy ? t(isEdit ? "sources.edit.saving" : "sources.add.adding") : t(isEdit ? "common.save" : "common.add")}
          </Button>
        </div>
      </form>
    </Drawer>
  );
}
