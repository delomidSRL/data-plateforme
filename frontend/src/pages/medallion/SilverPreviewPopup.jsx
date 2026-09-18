import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import DataPreviewPanel from "./DataPreviewPanel.jsx";
import * as structurationApi from "../../api/structuration.js";

// Module 18 §7 UX — the instant preview of silver.01_unpacked_<name>/silver.02_typed_<name>
// (materialize_unpacked_typed_sync). Neither table is a MedallionDataset, so this reuses
// DataPreviewPanel with a custom fetcher (structuration/preview) instead of the usual
// medallionApi.getDatasetPreview — same table + paging UI as every other dataset preview.
const STAGE_PREFIX = { unpacked: "01_unpacked", typed: "02_typed" };

export default function SilverPreviewPopup({ project, bronzeDatasetId, bronzeName, stage, onClose }) {
  const { t } = useTranslation();
  const hintKey = `medallion.structuration.stageHint_${stage}`;
  return (
    <Modal title={`silver.${STAGE_PREFIX[stage] || stage}_${bronzeName}`} description={t(hintKey)} onClose={onClose} maxWidth={880}>
      <DataPreviewPanel
        project={project}
        fetchPreview={(opts) => structurationApi.previewStructurationTable(project.id, bronzeDatasetId, stage, opts)}
      />
    </Modal>
  );
}
