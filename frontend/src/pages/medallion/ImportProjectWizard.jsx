import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";
import * as importsApi from "../../api/imports.js";

const REQUIRED_BUNDLE_KEYS = ["project_name", "datasets", "structurations", "macros", "dq_flags", "bronze_hints"];

function sanitizeDbtName(raw) {
  return raw.toLowerCase().replace(/[^a-z0-9_]/g, "_");
}

// supersetInstances isn't used here (the imported bundle never carries a Superset pick — the
// backend's ProjectImportCommit treats it as optional/omitted) but stays in the prop shape so
// ProjectsList.jsx can render this wizard exactly like NewProjectWizard, same props either way.
export default function ImportProjectWizard({ sources, instances, supersetInstances: _supersetInstances = [], onClose, onCreated }) {
  const { t } = useTranslation();
  const [step, setStep] = useState(1);

  // Step 1 — bundle
  const [bundle, setBundle] = useState(null);
  const [bundleError, setBundleError] = useState("");

  // Step 2 — destination
  const [objectStoreId, setObjectStoreId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [dbtProjectName, setDbtProjectName] = useState("");

  // Step 3 — re-upload source files, keyed by target_table
  const [fileByTable, setFileByTable] = useState({});
  const [statusByTable, setStatusByTable] = useState({});
  const [importing, setImporting] = useState(false);

  // Step 4 — commit
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const minioSources = sources.filter((s) => s.type === "minio");
  const pgSources = sources.filter((s) => s.type === "postgresql");
  const platformInstances = instances.filter((i) => i.origin === "platform");
  const externalInstances = instances.filter((i) => i.origin === "external");

  const handleBundleFile = async (file) => {
    setBundleError("");
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (parsed.format_version !== 1) {
        setBundleError(t("medallion.importWizard.bundleUnsupportedVersion"));
        return;
      }
      const missing = REQUIRED_BUNDLE_KEYS.filter((k) => !(k in parsed));
      if (missing.length > 0) {
        setBundleError(t("medallion.importWizard.bundleInvalid"));
        return;
      }
      setBundle(parsed);
      setProjectName(parsed.project_name);
      setDbtProjectName(sanitizeDbtName(parsed.project_name));
    } catch {
      setBundleError(t("medallion.importWizard.bundleUnreadable"));
    }
  };

  const datasetCounts = bundle
    ? {
        bronze: bundle.datasets.filter((d) => d.layer === "bronze").length,
        silver: bundle.datasets.filter((d) => d.layer === "silver").length,
        gold: bundle.datasets.filter((d) => d.layer === "gold").length,
      }
    : null;

  const step2Valid = objectStoreId && warehouseId && instanceId && projectName.trim() && dbtProjectName.trim();

  const allFilesChosen = bundle ? bundle.bronze_hints.every((h) => fileByTable[h.target_table]) : false;
  const allImportsDone = bundle ? bundle.bronze_hints.every((h) => statusByTable[h.target_table]?.state === "done") : false;

  const runImportFor = async (hint) => {
    const file = fileByTable[hint.target_table];
    if (!file) return;
    setStatusByTable((s) => ({ ...s, [hint.target_table]: { state: "uploading" } }));
    try {
      const result = await importsApi.createImport(file, {
        format: "csv",
        targetSourceId: Number(warehouseId),
        archiveSourceId: Number(objectStoreId),
        name: hint.target_table,
        importMode: "payload",
        writeMode: "create",
      });
      // createImport resolves (HTTP 2xx) even when the import itself failed — the row is
      // created either way, with the failure recorded on its own `status`/`last_error`
      // fields rather than as a thrown error. A non-throwing response is NOT success on
      // its own; only status === "imported" is.
      if (result.status === "imported") {
        setStatusByTable((s) => ({ ...s, [hint.target_table]: { state: "done" } }));
      } else {
        setStatusByTable((s) => ({ ...s, [hint.target_table]: { state: "error", error: result.last_error || t("medallion.importWizard.fileImportFailed") } }));
      }
    } catch (err) {
      setStatusByTable((s) => ({ ...s, [hint.target_table]: { state: "error", error: err.message || t("medallion.importWizard.fileImportFailed") } }));
    }
  };

  const runAllImports = async () => {
    setImporting(true);
    for (const hint of bundle.bronze_hints) {
      if (statusByTable[hint.target_table]?.state === "done") continue;
      // eslint-disable-next-line no-await-in-loop -- each row's file import must attribute its own error, not race with siblings
      await runImportFor(hint);
    }
    setImporting(false);
  };

  const commit = async () => {
    setBusy(true);
    setError("");
    try {
      const project = await medallionApi.importProject({
        bundle,
        project_name: projectName.trim(),
        dbt_project_name: dbtProjectName.trim(),
        object_store_source_id: Number(objectStoreId),
        warehouse_source_id: Number(warehouseId),
        airflow_instance_id: Number(instanceId),
      });
      onCreated(project);
    } catch (err) {
      setError(err.message || t("medallion.importWizard.commitFailed"));
    } finally {
      setBusy(false);
    }
  };

  const STATUS_BADGE = {
    idle: <Badge tone="neutral">{t("medallion.importWizard.statusIdle")}</Badge>,
    uploading: <Badge tone="accent">{t("medallion.importWizard.statusUploading")}</Badge>,
    done: <Badge tone="accent">{Icon.check({ width: 12, height: 12 })} {t("medallion.importWizard.statusDone")}</Badge>,
    error: <Badge tone="danger">{t("medallion.importWizard.statusError")}</Badge>,
  };

  return (
    <Drawer title={t("medallion.importWizard.title")} description={t("medallion.importWizard.description")} onClose={onClose}>
      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 14 }}>
        {t("medallion.importWizard.stepCounter", { step, total: 4 })}
      </div>

      {step === 1 && (
        <div>
          <Field label={t("medallion.importWizard.bundleFile")}>
            <input type="file" accept=".json" onChange={(e) => handleBundleFile(e.target.files?.[0])} />
          </Field>
          {bundleError && <div className="error-banner">{Icon.warn()}<span>{bundleError}</span></div>}

          {bundle && datasetCounts && (
            <div className="card" style={{ padding: 14, marginTop: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5, marginBottom: 8 }}>{bundle.project_name}</div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", display: "flex", gap: 14, marginBottom: 10 }}>
                <span>{t("medallion.importWizard.countBronze", { count: datasetCounts.bronze })}</span>
                <span>{t("medallion.importWizard.countSilver", { count: datasetCounts.silver })}</span>
                <span>{t("medallion.importWizard.countGold", { count: datasetCounts.gold })}</span>
              </div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 10 }}>
                {t("medallion.importWizard.countMacrosFlags", { macros: bundle.macros.length, flags: bundle.dq_flags.length })}
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{t("medallion.importWizard.sourceFilesNeeded")}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {bundle.bronze_hints.map((h) => (
                  <div key={h.target_table} style={{ fontSize: 12, fontFamily: "var(--font-m)", color: "var(--text-muted)" }}>
                    {h.target_table}{h.suggested_filename ? ` — ${h.suggested_filename}` : ""}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="modal-actions">
            <Button type="button" variant="ghost" onClick={onClose}>{t("medallion.importWizard.cancel")}</Button>
            <Button type="button" disabled={!bundle} onClick={() => setStep(2)}>{t("medallion.importWizard.next")}</Button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div>
          <Field label={t("medallion.wizard.projectName")}>
            <Input value={projectName} onChange={(e) => setProjectName(e.target.value)} />
          </Field>
          <Field label={t("medallion.wizard.dbtProjectName")}>
            <Input value={dbtProjectName} onChange={(e) => setDbtProjectName(sanitizeDbtName(e.target.value))} />
          </Field>

          <Field label={t("medallion.wizard.objectStore")}>
            <select className="input" value={objectStoreId} onChange={(e) => setObjectStoreId(e.target.value)}>
              <option value="">{t("medallion.wizard.chooseMinioSource")}</option>
              {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>

          <Field label={t("medallion.wizard.warehouse")}>
            <select className="input" value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
              <option value="">{t("medallion.wizard.choosePgSource")}</option>
              {pgSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>

          <Field label={t("medallion.wizard.airflowInstance")}>
            <select className="input" value={instanceId} onChange={(e) => setInstanceId(e.target.value)}>
              <option value="">{t("medallion.wizard.choose")}</option>
              {platformInstances.length > 0 && (
                <optgroup label={t("orchestrators.platformManaged")}>
                  {platformInstances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                </optgroup>
              )}
              {externalInstances.length > 0 && (
                <optgroup label={t("orchestrators.external")}>
                  {externalInstances.map((i) => (
                    <option key={i.id} value={i.id} disabled={!i.capabilities?.deployable}>
                      {i.name}{!i.capabilities?.deployable ? ` — ${t("medallion.wizard.notDeployable")}` : ""}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
          </Field>

          <div className="modal-actions">
            <Button type="button" variant="ghost" onClick={() => setStep(1)}>{t("medallion.importWizard.previous")}</Button>
            <Button type="button" disabled={!step2Valid} onClick={() => setStep(3)}>{t("medallion.importWizard.next")}</Button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div>
          <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 12 }}>{t("medallion.importWizard.reuploadHint")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {bundle.bronze_hints.map((h) => {
              const status = statusByTable[h.target_table]?.state || "idle";
              return (
                <div key={h.target_table} className="card" style={{ padding: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13, fontFamily: "var(--font-m)" }}>{h.target_table}</div>
                      {h.suggested_filename && (
                        <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                          {t("medallion.importWizard.originalFile", { filename: h.suggested_filename })}
                        </div>
                      )}
                    </div>
                    {STATUS_BADGE[status]}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                    <input
                      type="file"
                      disabled={status === "uploading"}
                      onChange={(e) => setFileByTable((f) => ({ ...f, [h.target_table]: e.target.files?.[0] || null }))}
                    />
                    {status === "error" && (
                      <Button type="button" variant="ghost" className="inline" onClick={() => runImportFor(h)}>
                        {t("medallion.importWizard.retry")}
                      </Button>
                    )}
                  </div>
                  {status === "error" && (
                    <div style={{ fontSize: 11.5, color: "var(--danger)", marginTop: 6 }}>{statusByTable[h.target_table]?.error}</div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="modal-actions">
            <Button type="button" variant="ghost" onClick={() => setStep(2)}>{t("medallion.importWizard.previous")}</Button>
            <Button type="button" variant="ghost" className="inline" disabled={!allFilesChosen || importing} onClick={runAllImports}>
              {importing ? t("medallion.importWizard.importing") : t("medallion.importWizard.runImports")}
            </Button>
            <Button type="button" disabled={!allImportsDone} onClick={() => setStep(4)}>{t("medallion.importWizard.next")}</Button>
          </div>
        </div>
      )}

      {step === 4 && (
        <div>
          {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <strong>{t("medallion.wizard.projectName")} :</strong> {projectName}
            </div>
            <div style={{ fontSize: 13 }}>
              <strong>{t("medallion.wizard.dbtProjectName")} :</strong> {dbtProjectName}
            </div>
          </div>
          <div className="modal-actions">
            <Button type="button" variant="ghost" onClick={() => setStep(3)} disabled={busy}>{t("medallion.importWizard.previous")}</Button>
            <Button type="button" disabled={busy} onClick={commit}>
              {busy ? t("medallion.importWizard.creating") : t("medallion.importWizard.createProject")}
            </Button>
          </div>
        </div>
      )}
    </Drawer>
  );
}
