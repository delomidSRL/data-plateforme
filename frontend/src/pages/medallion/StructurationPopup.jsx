import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import StructurationPanel from "./StructurationPanel.jsx";

// Module 6 extension (payload & structuration) — the "popup de profiling" the canvas's
// structuration node opens, and the one that pops up as soon as a silver-in-progress picks a
// payload-backed bronze as an upstream (DatasetPanel's toggleUpstream) — same panel either
// way, just not buried behind the bronze dataset's own edit drawer.
export default function StructurationPopup({ project, dataset, onClose }) {
  const { t } = useTranslation();
  return (
    <Modal title={t("medallion.structuration.tab")} description={dataset.name} onClose={onClose} maxWidth={880}>
      <StructurationPanel project={project} dataset={dataset} />
    </Modal>
  );
}
