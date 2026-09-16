import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as foldersApi from "../../api/medallionFolders.js";

// Module 15 §3.2/D3 — deleting a folder never deletes a project: made explicit in the copy
// here so the confirmation itself states what actually happens.
export default function DeleteFolderModal({ folder, onClose, onDeleted }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      await foldersApi.deleteFolder(folder.id);
      onDeleted(folder.id);
    } catch (err) {
      setError(err.message || t("medallion.folders.deleteFailed"));
      setBusy(false);
    }
  };

  return (
    <Modal title={t("medallion.folders.deleteTitle", { name: folder.name })} onClose={onClose} maxWidth={440}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 0 }}>
        {t("medallion.folders.deleteWarning", { count: folder.project_count })}
      </p>
      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
        <Button type="button" disabled={busy} onClick={confirm}>{busy ? t("medallion.folders.deleting") : t("medallion.folders.confirmDelete")}</Button>
      </div>
    </Modal>
  );
}
