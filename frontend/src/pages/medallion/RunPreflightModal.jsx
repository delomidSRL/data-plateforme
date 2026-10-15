import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";
import * as importsApi from "../../api/imports.js";
import useReimportSourcePicker from "../imports/useReimportSourcePicker.js";
import ReimportSourceFields from "../imports/ReimportSourceFields.jsx";

const waitForImportDone = async (importId) => {
  // Mirrors BulkImportWizard's own pollUntilDone — typed-mode same-shape reimports finish in a
  // FastAPI BackgroundTask, so the reimport call's own response is only the "accepted" state.
  for (;;) {
    const s = await importsApi.getImportStatus(importId);
    if (s.status !== "importing") return s;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
};

function StepStatusIcon({ status }) {
  if (status === "running") return <span className="run-spinner" />;
  if (status === "done") return <span style={{ color: "#2f9e6e", display: "flex" }}>{Icon.check({ width: 15, height: 15 })}</span>;
  if (status === "error") return <span style={{ color: "var(--danger)", display: "flex" }}>{Icon.warn({ width: 15, height: 15 })}</span>;
  return null;
}

// One bronze source in the pre-launch popup. File-import sources can be refreshed inline
// (always as Append — see apply_reimport's force_write_mode); DB-table/object-store sources
// are read-only, there is nothing to re-import. Registers its own "is there a pending edit to
// run, and how" with the parent via onRegister, so "Confirmer et lancer" can drive every
// expanded/edited step without the parent reaching into each card's internal picker state.
function PreflightStepCard({ source, onRegister }) {
  const { t } = useTranslation();
  const isFileImport = source.origin_type === "file_import";
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState("idle");
  const [stepError, setStepError] = useState("");
  const picker = useReimportSourcePicker(isFileImport ? source.source_location : null);

  const pending = expanded && picker.canSubmit && status !== "done";

  useEffect(() => {
    if (!isFileImport) return;
    onRegister(source.dataset_id, {
      pending,
      run: async () => {
        setStatus("running");
        setStepError("");
        try {
          const payload = picker.buildPayload();
          let updated = payload.mode === "local"
            ? await importsApi.reimport(source.import_id, payload.file, { forceAppend: true })
            : await importsApi.reimportFromObjectStore(source.import_id, { ...payload, forceAppend: true });
          if (updated.status === "importing") updated = await waitForImportDone(source.import_id);
          if (updated.status === "awaiting_validation") throw new Error(t("medallion.preflight.driftRejected"));
          if (updated.status === "error") throw new Error(updated.last_error || t("medallion.preflight.reimportFailed"));
          setStatus("done");
          return true;
        } catch (err) {
          setStatus("error");
          setStepError(err.message || t("medallion.preflight.reimportFailed"));
          return false;
        }
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFileImport, pending]);

  const sourceBadge = isFileImport
    ? (source.source_location?.transport === "minio" ? t("medallion.preflight.badgeS3") : t("medallion.preflight.badgeLocal"))
    : source.origin_type === "db_table" ? t("medallion.preflight.badgeTable") : t("medallion.preflight.badgeObjectStore");

  const canToggle = isFileImport && status !== "done";

  return (
    <div className="card" style={{ padding: 0, marginBottom: 10, overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => canToggle && setExpanded((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 10, width: "100%", padding: 14,
          background: "transparent", border: "none", textAlign: "left", font: "inherit", color: "inherit",
          cursor: canToggle ? "pointer" : "default",
        }}
      >
        <span style={{ color: "var(--text-muted)", display: "flex" }}>{isFileImport ? Icon.file() : source.origin_type === "db_table" ? Icon.db() : Icon.server()}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{source.dataset_name}</div>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>
            {isFileImport
              ? `${source.import_name || "—"} · ${source.file_name || "—"}`
              : `${source.source_name || "—"}${source.source_object ? " · " + source.source_object : ""}`}
            {status === "done" && <span style={{ color: "#2f9e6e" }}> · {t("medallion.preflight.stepDone")}</span>}
          </div>
        </div>
        <Badge tone={isFileImport ? "accent" : "neutral"}>{sourceBadge}</Badge>
        <StepStatusIcon status={status} />
        {canToggle && (
          <span style={{ display: "flex", color: "var(--text-muted)", transform: expanded ? "rotate(180deg)" : "none", transition: "transform .15s" }}>
            {Icon.chevronDown()}
          </span>
        )}
      </button>

      {canToggle && expanded && (
        <div style={{ borderTop: "1px solid var(--border)", padding: 14 }}>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 8 }}>{t("medallion.preflight.appendNote")}</div>
          <ReimportSourceFields picker={picker} fileInputId={`preflight-file-${source.dataset_id}`} />
          {stepError && <div className="error-banner" style={{ marginTop: 8 }}>{Icon.warn()}<span>{stepError}</span></div>}
        </div>
      )}
    </div>
  );
}

// Popup de pré-lancement — avant un RUN (nœud, sélection, tout le projet) ou l'enregistrement
// d'un schedule : montre chaque source bronze dont ce run dépend, permet de réimporter celles
// qui sont des imports fichier (toujours en Append, voir useReimportSourcePicker/apply_reimport)
// avant de confirmer. `target.items` = [] pour tout le projet, sinon une liste de
// {dataset_id, stage}, même convention que run-selection.
export default function RunPreflightModal({ project, target, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [sources, setSources] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState("");
  const stepsRef = useRef(new Map());

  useEffect(() => {
    medallionApi.getRunPreflight(project.id, target.items || [])
      .then((res) => setSources(res.sources))
      .catch((err) => setLoadError(err.message || t("medallion.preflight.loadFailed")));
  }, [project.id, target, t]);

  const registerStep = (datasetId, step) => { stepsRef.current.set(datasetId, step); };

  const confirmAndLaunch = async () => {
    setLaunching(true);
    setLaunchError("");
    const pendingSteps = [...stepsRef.current.values()].filter((s) => s.pending);
    for (const step of pendingSteps) {
      // eslint-disable-next-line no-await-in-loop
      const ok = await step.run();
      if (!ok) {
        setLaunching(false);
        setLaunchError(t("medallion.preflight.blockedByError"));
        return;
      }
    }
    setLaunching(false);
    onConfirm();
  };

  return (
    <Modal large title={t("medallion.preflight.title")} description={t("medallion.preflight.description")} onClose={onClose}>
      {loadError && <div className="error-banner">{Icon.warn()}<span>{loadError}</span></div>}
      {launchError && <div className="error-banner">{Icon.warn()}<span>{launchError}</span></div>}

      {!loadError && sources === null && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>}
      {sources !== null && sources.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: "20px 0" }}>{t("medallion.preflight.noSources")}</div>
      )}
      {sources !== null && sources.map((s) => (
        <PreflightStepCard key={s.dataset_id} source={s} onRegister={registerStep} />
      ))}

      <div className="modal-actions">
        <Button type="button" variant="ghost" disabled={launching} onClick={onClose}>{t("imports.wizard.cancel")}</Button>
        <Button type="button" disabled={sources === null || launching} onClick={confirmAndLaunch}>
          {launching ? t("medallion.preflight.launching") : t("medallion.preflight.confirmLaunch")}
        </Button>
      </div>
    </Modal>
  );
}
