import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";
import { useToast } from "../../context/ToastContext.jsx";

export default function RestoreVersionModal({ project, version, onClose, onRestored }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Module 19 §5.4 — restoring wholesale-replaces the workspace (no merge): whatever's
  // currently `modified`/`code` there is discarded, so it's surfaced here before confirming.
  const [modifiedFiles, setModifiedFiles] = useState(null);

  useEffect(() => {
    medallionApi.getModifiedFiles(project.id).then(setModifiedFiles).catch(() => setModifiedFiles([]));
  }, [project.id]);

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      const newVersion = await medallionApi.restoreVersion(project.id, version.id);
      showToast(t("medallion.versions.restoreSuccess", { n: version.version_number }));
      onRestored(newVersion);
    } catch (err) {
      setError(err.message || t("medallion.versions.restoreFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={t("medallion.versions.restoreTitle", { n: version.version_number })}
      description={t("medallion.versions.restoreDescription", { n: version.version_number })}
      onClose={onClose}
    >
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 0 }}>{t("medallion.versions.restoreWarning", { n: version.version_number })}</p>
      {modifiedFiles && modifiedFiles.length > 0 && (
        <div className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>
          {Icon.code()}
          <span>
            {t("medallion.versions.restoreCodeWarning", { count: modifiedFiles.length })}
            {modifiedFiles.map((p) => (
              <span key={p} style={{ display: "block", fontFamily: "var(--font-m)", fontSize: 11.5, marginTop: 4 }}>{p}</span>
            ))}
          </span>
        </div>
      )}
      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t("medallion.versions.cancel")}</Button>
        <Button type="button" disabled={busy} onClick={confirm}>
          {busy ? t("medallion.versions.restoring") : t("medallion.versions.confirmRestore", { n: version.version_number })}
        </Button>
      </div>
    </Modal>
  );
}
