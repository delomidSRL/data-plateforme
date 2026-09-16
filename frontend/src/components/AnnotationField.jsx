import { Icon } from "./icons.jsx";

// Shared "describe this table/column" row — used by the Sources explorer (Module 13 §3.4)
// and by the pipeline-agent mapping panel's targeted annotation view (§4.5), so an
// annotation entered from either screen is the exact same persisted record.
export default function AnnotationField({ label, sublabel, value, placeholder, annotated, annotatedLabel, deleteTitle, onChange, onDelete, compact = false, style }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0, ...style }}>
      <span style={{ fontFamily: "var(--font-m)", fontSize: compact ? 11.5 : 12.5, color: compact ? "var(--text-muted)" : "var(--text)", minWidth: compact ? 120 : undefined }}>{label}</span>
      {sublabel && <span style={{ fontFamily: "var(--font-m)", fontSize: 10.5, color: "var(--text-muted)" }}>{sublabel}</span>}
      {annotated && <span className="badge badge-accent" style={{ fontSize: compact ? 9.5 : 10, padding: compact ? "1px 5px" : "1px 6px" }}>{annotatedLabel}</span>}
      <input
        className="input"
        style={{ flex: 1, minWidth: compact ? 120 : 140, height: compact ? 26 : 28, fontSize: compact ? 11.5 : 12 }}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {annotated && onDelete && (
        <button type="button" onClick={onDelete} title={deleteTitle} style={{ color: "var(--text-muted)", display: "flex" }}>
          {Icon.trash({ width: compact ? 12 : 13, height: compact ? 12 : 13 })}
        </button>
      )}
    </div>
  );
}
