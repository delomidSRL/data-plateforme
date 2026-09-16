import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";

// Module 15 §4.3 — baseline way to route a project into (or out of) a folder. Purely
// organizational: the API call it fires (PATCH /projects/{pid}/folder) never touches build/
// run/lineage.
export default function MoveProjectModal({ project, folders, onClose, onMoved }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const move = async (folderId) => {
    setBusy(true);
    setError("");
    try {
      const updated = await medallionApi.moveProjectFolder(project.id, folderId);
      onMoved(updated);
    } catch (err) {
      setError(err.message || t("medallion.folders.moveFailed"));
      setBusy(false);
    }
  };

  return (
    <Modal title={t("medallion.folders.moveTitle", { name: project.name })} onClose={onClose} maxWidth={420}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <button
          type="button"
          className="link"
          disabled={busy || project.folder_id == null}
          onClick={() => move(null)}
          style={{ textAlign: "left", padding: "8px 10px", borderRadius: 8, fontWeight: project.folder_id == null ? 600 : 400 }}
        >
          {t("medallion.folders.root")}
        </button>
        {folders.map((f) => (
          <button
            key={f.id}
            type="button"
            className="link"
            disabled={busy || project.folder_id === f.id}
            onClick={() => move(f.id)}
            style={{ textAlign: "left", padding: "8px 10px", borderRadius: 8, fontWeight: project.folder_id === f.id ? 600 : 400 }}
          >
            {f.name}
          </button>
        ))}
        {folders.length === 0 && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{t("medallion.folders.noneYet")}</p>}
      </div>
      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
      </div>
    </Modal>
  );
}
