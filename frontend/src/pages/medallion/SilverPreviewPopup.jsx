import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Icon } from "../../components/icons.jsx";
import DataPreviewPanel from "./DataPreviewPanel.jsx";
import RunHistoryPanel from "./RunHistoryPanel.jsx";
import * as structurationApi from "../../api/structuration.js";

// Module 18 §7 UX — the instant preview of silver.01_unpacked_<name>/silver.02_typed_<name>
// (materialize_unpacked_typed_sync). Neither table is a MedallionDataset, so this reuses
// DataPreviewPanel with a custom fetcher (structuration/preview) instead of the usual
// medallionApi.getDatasetPreview — same table + paging UI as every other dataset preview.
const STAGE_PREFIX = { unpacked: "01_unpacked", typed: "02_typed" };

export default function SilverPreviewPopup({ project, bronzeDatasetId, bronzeName, stage, onClose }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState("preview");
  const hintKey = `medallion.structuration.stageHint_${stage}`;
  return (
    <Modal title={`silver.${STAGE_PREFIX[stage] || stage}_${bronzeName}`} description={t(hintKey)} onClose={onClose} maxWidth={880}>
      <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
        <button type="button" className="btn-ghost" style={{ opacity: tab === "preview" ? 1 : 0.6 }} onClick={() => setTab("preview")}>
          {Icon.eye()} {t("medallion.panel.tabPreview")}
        </button>
        <button type="button" className="btn-ghost" style={{ opacity: tab === "history" ? 1 : 0.6 }} onClick={() => setTab("history")}>
          {Icon.refresh()} {t("medallion.run.historyTab")}
        </button>
      </div>
      {tab === "preview" ? (
        <DataPreviewPanel
          project={project}
          fetchPreview={(opts) => structurationApi.previewStructurationTable(project.id, bronzeDatasetId, stage, opts)}
        />
      ) : (
        <RunHistoryPanel project={project} datasetId={bronzeDatasetId} stage={stage} />
      )}
    </Modal>
  );
}
