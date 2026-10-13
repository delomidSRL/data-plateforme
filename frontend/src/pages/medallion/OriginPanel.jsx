import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import ReimportModal from "../imports/ReimportModal.jsx";
import SchemaValidationModal from "../imports/SchemaValidationModal.jsx";
import DataPreviewPanel from "./DataPreviewPanel.jsx";

const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };

export default function OriginPanel({ origin, project, previewDatasetId, onClose, readOnly = false }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [showPreview, setShowPreview] = useState(false);
  const [reimporting, setReimporting] = useState(false);
  const [validating, setValidating] = useState(null);

  const isFileImport = origin.provenance?.type === "file_import";

  const handleReimported = (updated) => {
    setReimporting(false);
    if (updated.status === "awaiting_validation") {
      setValidating(updated);
    } else {
      showToast(t("imports.reimportSuccess"));
    }
  };

  return (
    <>
    <Modal large title={origin.name} description={t("medallion.origin.description")} onClose={onClose}>
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
          <Button type="button" onClick={() => setReimporting(true)}>
            {Icon.upload()} {t("imports.reimport")}
          </Button>
        </div>
      )}
    </Modal>

    {reimporting && (
      <ReimportModal importId={origin.provenance.import_id} onClose={() => setReimporting(false)} onReimported={handleReimported} />
    )}

    {validating && (
      <SchemaValidationModal
        fileImport={validating}
        onClose={() => setValidating(null)}
        onValidated={() => { setValidating(null); showToast(t("imports.modal.running")); }}
      />
    )}
    </>
  );
}
