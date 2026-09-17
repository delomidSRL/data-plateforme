import { Modal } from "../../components/ui/Modal.jsx";
import StructurationPanel from "./StructurationPanel.jsx";

// Module 6 extension (payload & structuration) — the "popup de profiling" opened from: the
// "+" on a payload-backed bronze node (available as soon as that bronze exists), the
// synthetic "structuration" node spliced into a bronze->silver edge, and as soon as a
// silver-in-progress picks a payload-backed bronze as an upstream (DatasetPanel's
// toggleUpstream) — same panel every time, just not buried behind the bronze dataset's own
// edit drawer. Titled literally "Application data quality" (kept in English on purpose,
// same convention as the import wizard's Schema-on-Read/Schema-on-Write).
export default function StructurationPopup({ project, dataset, onClose }) {
  return (
    <Modal title="Application data quality" description={dataset.name} onClose={onClose} maxWidth={880}>
      <StructurationPanel project={project} dataset={dataset} />
    </Modal>
  );
}
