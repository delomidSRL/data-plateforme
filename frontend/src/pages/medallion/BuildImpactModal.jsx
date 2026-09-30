import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";

const SEVERITY_TONE = { column_removed: "danger", column_renamed: "accent", downstream_model: "neutral", superset: "accent", quality_check: "accent", export: "neutral" };

/** Module 19 §7.3 — "Revue des changements de code" : shown when the build's own 409 carries
 * an impact preview. Non-blocking by design (this module invents no new gate) — confirming
 * just replays the same build call with `confirm_impact: true`. */
export default function BuildImpactModal({ impact, onClose, onConfirm, busy }) {
  const { t } = useTranslation();
  return (
    <Modal title={t("medallion.impact.title")} description={t("medallion.impact.description")} onClose={onClose} maxWidth={640}>
      {impact.map((m) => (
        <div key={m.path} className="card" style={{ padding: 12, marginBottom: 10 }}>
          <div style={{ fontFamily: "var(--font-m)", fontWeight: 600, fontSize: 13 }}>{m.dataset_name}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-m)", marginBottom: 8 }}>{m.path}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {m.items.map((it, i) => (
              <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <Badge tone={SEVERITY_TONE[it.severity] || "neutral"}>{t(`medallion.impact.severity.${it.severity}`)}</Badge>
                <span style={{ fontSize: 12.5 }}>{it.message}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t("medallion.impact.cancel")}</Button>
        <Button type="button" onClick={onConfirm} disabled={busy}>{busy ? t("medallion.deploying") : t("medallion.impact.confirmDeploy")}</Button>
      </div>
    </Modal>
  );
}
