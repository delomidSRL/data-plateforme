import { useTranslation } from "react-i18next";
import { Modal } from "./ui/Modal.jsx";
import { Button } from "./ui/Button.jsx";
import { Icon } from "./icons.jsx";

export default function IdleTimeoutModal({ secondsLeft, onStay }) {
  const { t } = useTranslation();
  return (
    <Modal title={t("idle.title")} description={t("idle.description")} onClose={onStay}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, padding: "8px 4px 4px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--ember)" }}>
          {Icon.warn()}
          <span style={{ fontSize: 34, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{secondsLeft}s</span>
        </div>
        <div className="modal-actions" style={{ width: "100%" }}>
          <Button onClick={onStay} style={{ width: "100%", justifyContent: "center" }}>{t("idle.stayButton")}</Button>
        </div>
      </div>
    </Modal>
  );
}
