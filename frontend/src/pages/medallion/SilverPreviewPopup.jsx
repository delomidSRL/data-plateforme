import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import DataPreviewPanel from "./DataPreviewPanel.jsx";
import * as structurationApi from "../../api/structuration.js";

// Module 18 §7 UX — the instant preview of silver.typed_<name>/silver.structured_<name>
// (materialize_typed_structured_sync). Neither table is a MedallionDataset, so this reuses
// DataPreviewPanel with a custom fetcher (structuration/preview) instead of the usual
// medallionApi.getDatasetPreview — same table + paging UI as every other dataset preview.
export default function SilverPreviewPopup({ project, bronzeDatasetId, bronzeName, stage, onClose }) {
  const { t } = useTranslation();
  const hintKey = `medallion.structuration.stageHint_${stage}`;
  return (
    <Modal title={`silver.${stage}_${bronzeName}`} description={t(hintKey)} onClose={onClose} maxWidth={880}>
      <DataPreviewPanel
        project={project}
        fetchPreview={(opts) => structurationApi.previewStructurationTable(project.id, bronzeDatasetId, stage, opts)}
      />
    </Modal>
  );
}
