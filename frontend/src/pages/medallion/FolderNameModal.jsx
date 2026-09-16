import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as foldersApi from "../../api/medallionFolders.js";

// Module 15 §4.2 — shared by "Nouveau dossier" (folder=null) and "Renommer" (folder set):
// same single-field form, same 409 duplicate-name handling either way.
export default function FolderNameModal({ folder, onClose, onSaved }) {
  const { t } = useTranslation();
  const isRename = !!folder;
  const [name, setName] = useState(folder?.name || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const saved = isRename ? await foldersApi.renameFolder(folder.id, name.trim()) : await foldersApi.createFolder(name.trim());
      onSaved(saved);
    } catch (err) {
      setError(err.message || t("medallion.folders.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={isRename ? t("medallion.folders.renameTitle") : t("medallion.folders.newTitle")} onClose={onClose} maxWidth={420}>
      <form onSubmit={submit}>
        {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
        <Field label={t("medallion.folders.nameLabel")}>
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={t("medallion.folders.namePlaceholder")} maxLength={120} />
        </Field>
        <div className="modal-actions">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
          <Button type="submit" disabled={busy || !name.trim()}>
            {busy ? t("common.saving") : isRename ? t("common.save") : t("medallion.folders.create")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
