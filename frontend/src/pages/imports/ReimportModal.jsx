import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as importsApi from "../../api/imports.js";
import useReimportSourcePicker from "./useReimportSourcePicker.js";
import ReimportSourceFields from "./ReimportSourceFields.jsx";

const STATUS_TONE = { imported: "accent", drift_rejected: "neutral", error: "danger" };

// Tab content only mounts once `fileImport` has loaded, so the picker hook's one-time
// source_location prefill (rules of hooks forbid calling it conditionally inside the parent)
// always sees the real value instead of racing the fetch.
function ReimportTab({ importId, fileImport, onReimported, onClose }) {
  const { t } = useTranslation();
  const picker = useReimportSourcePicker(fileImport.source_location);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!picker.canSubmit || busy) return;
    setBusy(true);
    setError("");
    try {
      const payload = picker.buildPayload();
      const updated = payload.mode === "local"
        ? await importsApi.reimport(importId, payload.file)
        : await importsApi.reimportFromObjectStore(importId, payload);
      onReimported(updated);
    } catch (err) {
      setError(err.message || t("imports.reimportFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      <ReimportSourceFields picker={picker} fileInputId="reimport-file-input" />
      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose}>{t("imports.wizard.cancel")}</Button>
        <Button type="button" disabled={!picker.canSubmit || busy} onClick={submit}>
          {busy ? t("imports.reimporting") : t("imports.reimport")}
        </Button>
      </div>
    </>
  );
}

// Réimport par nœud bronze — remplace le hidden-input "upload local uniquement" d'origine
// (ImportsList.jsx / OriginPanel.jsx) : choix local ou S3/MinIO à chaque réimport, parcours
// ou saisie manuelle (préfixe pré-rempli depuis le DERNIER emplacement connu, voir
// fi.source_location — jamais dérivable après coup depuis archive_path, qui pointe la copie
// d'archive interne, pas la source externe réellement parcourue), et un onglet Historique.
export default function ReimportModal({ importId, onClose, onReimported }) {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState("reimport");
  const [fileImport, setFileImport] = useState(null);
  const [loadError, setLoadError] = useState("");

  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    importsApi.getImport(importId).then(setFileImport).catch((err) => setLoadError(err.message || t("imports.reimportModal.loadFailed")));
  }, [importId, t]);

  useEffect(() => {
    if (tab !== "history" || history !== null) return;
    importsApi.getReimportHistory(importId)
      .then(setHistory)
      .catch((err) => setHistoryError(err.message || t("imports.reimportModal.historyFailed")));
  }, [tab, history, importId, t]);

  return (
    <Modal large title={t("imports.reimportModal.title")} description={fileImport ? `${t("imports.reimportModal.currentTable")} ${fileImport.target_schema}.${fileImport.target_table}` : undefined} onClose={onClose}>
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        <button type="button" className="btn-ghost" style={{ opacity: tab === "reimport" ? 1 : 0.6 }} onClick={() => setTab("reimport")}>
          {Icon.upload()} {t("imports.reimportModal.tabReimport")}
        </button>
        <button type="button" className="btn-ghost" style={{ opacity: tab === "history" ? 1 : 0.6 }} onClick={() => setTab("history")}>
          {Icon.refresh()} {t("imports.reimportModal.tabHistory")}
        </button>
      </div>

      {tab === "reimport" ? (
        loadError ? (
          <div className="error-banner">{Icon.warn()}<span>{loadError}</span></div>
        ) : !fileImport ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>
        ) : (
          <ReimportTab importId={importId} fileImport={fileImport} onReimported={onReimported} onClose={onClose} />
        )
      ) : historyError ? (
        <div className="error-banner">{Icon.warn()}<span>{historyError}</span></div>
      ) : history === null ? (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>
      ) : history.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: "28px 0" }}>{t("imports.reimportModal.historyEmpty")}</div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("imports.reimportModal.historyDate")}</th>
                <th>{t("common.status")}</th>
                <th>{t("imports.reimportModal.historySource")}</th>
                <th>{t("imports.reimportModal.historyRows")}</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{new Date(h.occurred_at).toLocaleString(i18n.language)}</td>
                  <td>
                    <Badge tone={STATUS_TONE[h.outcome] || "neutral"}>{t(`imports.reimportModal.outcome.${h.outcome}`)}</Badge>
                    {h.error && <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, maxWidth: 280 }}>{h.error}</div>}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    <div>{t(`imports.reimportModal.trigger.${h.trigger_source}`)}</div>
                    <div style={{ fontFamily: "var(--font-m)", color: "var(--text-muted)", marginTop: 2 }}>
                      {h.source_location?.transport === "minio" ? `${h.source_location.bucket}/${h.source_location.key}` : h.file_name}
                    </div>
                  </td>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{h.row_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
