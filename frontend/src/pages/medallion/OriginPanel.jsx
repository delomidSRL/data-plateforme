import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import * as importsApi from "../../api/imports.js";
import DataPreviewPanel from "./DataPreviewPanel.jsx";

const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };

export default function OriginPanel({ origin, project, previewDatasetId, onClose, readOnly = false }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const fileInputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const isFileImport = origin.provenance?.type === "file_import";

  const handleReimportFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const updated = await importsApi.reimport(origin.provenance.import_id, file);
      showToast(updated.status === "awaiting_validation" ? t("imports.reimportReopenModal") : t("imports.reimportSuccess"));
    } catch (err) {
      showToast(err.message || t("imports.reimportFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal large title={origin.name} description={t("medallion.origin.description")} onClose={onClose}>
      <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls" style={{ display: "none" }} onChange={handleReimportFile} />

      <div className="field">
        <label className="field-label">{t("medallion.origin.sourceType")}</label>
        <Badge tone="neutral">{SOURCE_TYPE_LABEL[origin.source_type] || origin.source_type}</Badge>
      </div>

      {isFileImport ? (
        <>
          <div className="field">
            <label className="field-label">{t("medallion.origin.file")}</label>
            <div style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>{origin.provenance.file}</div>
          </div>
          <div className="field">
            <label className="field-label">{t("medallion.origin.importedAt")}</label>
            <div style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>
              {origin.provenance.imported_at ? new Date(origin.provenance.imported_at).toLocaleString() : "—"}
            </div>
          </div>
          <div className="field">
            <label className="field-label">{t("medallion.origin.rowCount")}</label>
            <div style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>{origin.provenance.row_count ?? "—"}</div>
          </div>
        </>
      ) : (
        <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16 }}>{t("medallion.origin.nativeSource")}</div>
      )}

      {previewDatasetId != null && (
        <div className="field">
          <button type="button" className="btn-ghost" style={{ padding: "5px 10px", fontSize: 12.5 }} onClick={() => setShowPreview((s) => !s)}>
            {Icon.eye()} {showPreview ? t("medallion.dataPreview.hidePreview") : t("medallion.dataPreview.previewSource")}
          </button>
        </div>
      )}

      {showPreview && previewDatasetId != null && (
        <div style={{ marginBottom: 16 }}>
          <DataPreviewPanel project={project} datasetId={previewDatasetId} forceSource />
        </div>
      )}

      {isFileImport && !readOnly && (
        <div className="modal-actions">
          <Button type="button" disabled={busy} onClick={() => fileInputRef.current?.click()}>
            {Icon.upload()} {busy ? t("imports.reimporting") : t("imports.reimport")}
          </Button>
        </div>
      )}
    </Modal>
  );
}
