import { useTranslation } from "react-i18next";
import { StatusDot } from "../../components/ui/Badge.jsx";

const LAYER_COLOR = { bronze: "#a9702f", silver: "#5b7a94", gold: "#c98a1c" };
const TEST_COLOR = { passed: "#2f9e6e", failed: "#c53d3d", none: "#c7cdd3" };
const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };

export default function ThreeColumnView({ nodes, onSelect, selectedId, qualityByDataset = {}, onOpenSilverPreview }) {
  const { t } = useTranslation();
  const LAYER_LABEL = { bronze: "Bronze", silver: "Silver", gold: "Gold" };
  const ML_OBJECTIVE_LABEL = t("medallion.mlObjectives", { returnObjects: true });
  const origins = nodes.filter((n) => n.node_type === "origin");

  // Mirrors LineageCanvas's own synthetic 01_unpacked_<name>/02_typed_<name> preview boxes —
  // neither stage is ever a real MedallionDataset (see workspace_sync.resolve()), so they're
  // absent from `nodes` and have to be synthesized here too, or this view silently shows fewer
  // silver boxes than the canvas does for the exact same project.
  const structurationPreviews = nodes
    .filter((n) => n.node_type !== "origin" && n.layer === "bronze" && n.structured)
    .flatMap((n) => [
      { id: `silver-unpacked-${n.id}`, name: `01_unpacked_${n.name}`, stage: "unpacked", bronzeId: n.id },
      { id: `silver-typed-${n.id}`, name: `02_typed_${n.name}`, stage: "typed", bronzeId: n.id },
    ]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "0.8fr repeat(3, 1fr)", gap: 14 }}>
      <div className="card" style={{ padding: 14, minHeight: 420, background: "var(--bg)" }}>
        <div className="field-label" style={{ marginBottom: 12 }}>{t("medallion.origins")}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {origins.map((n) => (
            <div
              key={n.id}
              onClick={() => onSelect(n.id)}
              style={{
                padding: "10px 12px", borderRadius: 10, border: "1.5px dashed var(--text-muted)", cursor: "pointer",
                boxShadow: selectedId === n.id ? "0 0 0 2px var(--ember)" : "none", background: "var(--surface)",
              }}
            >
              <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>
                {SOURCE_TYPE_LABEL[n.source_type] || n.source_type}
              </div>
              <div style={{ fontWeight: 600, fontSize: 13, marginTop: 2, fontFamily: "var(--font-m)" }}>{n.name}</div>
              {n.provenance && (
                <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
                  {n.provenance.file} · {n.provenance.row_count ?? "—"} {t("medallion.rowsWord")}
                </div>
              )}
            </div>
          ))}
          {origins.length === 0 && (
            <div style={{ fontSize: 12.5, color: "var(--text-muted)", textAlign: "center", padding: "20px 0" }}>{t("medallion.noOrigins")}</div>
          )}
        </div>
      </div>
      {["bronze", "silver", "gold"].map((layer) => (
        <div key={layer} className="card" style={{ padding: 14, minHeight: 420 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: LAYER_COLOR[layer] }} />
            <div className="field-label" style={{ margin: 0 }}>{LAYER_LABEL[layer]}</div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {layer === "silver" && structurationPreviews.map((p) => (
              <div
                key={p.id}
                onClick={() => onOpenSilverPreview?.(p.bronzeId, p.stage)}
                style={{
                  padding: "10px 12px", borderRadius: 10, border: "1px dashed var(--text-muted)",
                  borderLeft: `3px dashed ${LAYER_COLOR.silver}`, cursor: onOpenSilverPreview ? "pointer" : "default",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ fontWeight: 600, fontSize: 13.5 }}>{p.name}</div>
                  <span
                    className="badge"
                    title={t("medallion.structuration.instantPreviewHint")}
                    style={{ fontSize: 9.5, padding: "1px 6px", border: "1px solid var(--ember)", color: "var(--ember)", background: "var(--ember-soft)" }}
                  >
                    {p.stage}
                  </span>
                </div>
              </div>
            ))}
            {nodes.filter((n) => n.layer === layer).map((n) => (
              <div
                key={n.id}
                onClick={() => onSelect(n.id)}
                style={{
                  padding: "10px 12px", borderRadius: 10, border: "1px solid var(--border)",
                  borderLeft: `3px solid ${LAYER_COLOR[layer]}`, cursor: "pointer",
                  boxShadow: selectedId === n.id ? "0 0 0 2px var(--ember)" : "none",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ fontWeight: 600, fontSize: 13.5 }}>{n.name}</div>
                  {n.transform_type === "python" && (
                    <span className="badge badge-accent" style={{ fontSize: 9.5, padding: "1px 6px" }}>{ML_OBJECTIVE_LABEL[n.ml_objective] || "ML"}</span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, fontSize: 11.5, color: "var(--text-muted)" }}>
                  <span>{n.last_row_count != null ? t("medallion.rows", { count: n.last_row_count }) : "—"}</span>
                  {layer !== "bronze" && (
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <StatusDot color={TEST_COLOR[n.last_test_status] || TEST_COLOR.none} /> {t("medallion.tests")}
                    </span>
                  )}
                  {layer === "gold" && qualityByDataset[n.id]?.degraded && (
                    <span style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--danger)" }}>
                      <StatusDot color="var(--danger)" /> {t("medallion.quality.degraded")}
                    </span>
                  )}
                </div>
              </div>
            ))}
            {nodes.filter((n) => n.layer === layer).length === 0 && (layer !== "silver" || structurationPreviews.length === 0) && (
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", textAlign: "center", padding: "20px 0" }}>{t("medallion.noDataset")}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
