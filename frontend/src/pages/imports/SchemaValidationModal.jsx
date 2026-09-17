import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as importsApi from "../../api/imports.js";

const TYPES = ["text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"];
const IDENTIFIER_RE = /^[a-z_][a-z0-9_]{0,62}$/;
const BOOL_TRUE = new Set(["true", "1", "oui", "vrai", "o", "yes", "y"]);
const BOOL_FALSE = new Set(["false", "0", "non", "faux", "n", "no"]);

function previewCast(raw, type, dateFormat) {
  if (raw == null || String(raw).trim() === "") return "NULL";
  const text = String(raw).trim();
  if (type === "text") return text;
  if (type === "integer" || type === "bigint" || type === "numeric") {
    const cleaned = text.replace(/[\s  ]/g, "").replace(",", ".");
    const n = parseFloat(cleaned);
    if (Number.isNaN(n)) return "⚠ NULL";
    return type === "numeric" ? n.toFixed(2) : String(Math.trunc(n));
  }
  if (type === "boolean") {
    const low = text.toLowerCase();
    if (BOOL_TRUE.has(low)) return "true";
    if (BOOL_FALSE.has(low)) return "false";
    return "⚠ NULL";
  }
  if (type === "date" || type === "timestamp") {
    const iso = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (iso) return text;
    const m = text.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})/);
    if (m) {
      const [, a, b, y] = m;
      const dayFirst = !dateFormat || dateFormat.startsWith("%d");
      const day = (dayFirst ? a : b).padStart(2, "0");
      const month = (dayFirst ? b : a).padStart(2, "0");
      const year = y.length === 2 ? `20${y}` : y;
      return `${year}-${month}-${day}`;
    }
    return "⚠ NULL";
  }
  if (type === "jsonb") return text;
  return text;
}

export default function SchemaValidationModal({ fileImport, onClose, onValidated }) {
  const { t } = useTranslation();
  const [columns, setColumns] = useState(fileImport.column_mapping.map((c) => ({ ...c })));
  const [targetTable, setTargetTable] = useState(fileImport.target_table || "");
  const [writeMode, setWriteMode] = useState(fileImport.write_mode || "create");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Nested JSON/XML lands as a single `payload JSONB` column by default (scalar keys are
  // opt-in, unchecked) — the full mapping table has nothing to decide in the common case, so
  // it stays collapsed behind "customize" instead of forcing a review of an empty decision.
  const payloadEntry = fileImport.column_mapping.find((c) => c.source_name === "__root__");
  const isNestedPayload = Boolean(payloadEntry);
  const [expanded, setExpanded] = useState(false);
  const showTable = !isNestedPayload || expanded;

  const updateColumn = (idx, patch) => {
    setColumns((cols) => cols.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const includedCount = columns.filter((c) => c.include).length;

  const includedTargetNames = columns.filter((c) => c.include).map((c) => c.target_name);
  const hasDuplicate = (name) => includedTargetNames.filter((n) => n === name).length > 1;

  const columnsValid = columns.every((c) => !c.include || (IDENTIFIER_RE.test(c.target_name) && !hasDuplicate(c.target_name)));
  const tableValid = IDENTIFIER_RE.test(targetTable);
  const canValidate = includedCount > 0 && columnsValid && tableValid;

  const submit = async () => {
    if (!canValidate) return;
    setBusy(true);
    setError("");
    try {
      await importsApi.updateImport(fileImport.id, { column_mapping: columns, target_table: targetTable, write_mode: writeMode });
      const started = await importsApi.triggerRun(fileImport.id);
      onValidated(started);
    } catch (err) {
      setError(err.message || t("imports.modal.validateFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={t("imports.modal.title")}
      description={`${fileImport.source_file_name} → imports.${targetTable || "…"} — ${t("imports.modal.sampledRows", { count: fileImport.column_mapping[0]?.sample?.length ? "≤1000" : 0 })}`}
      onClose={onClose}
      maxWidth={880}
    >
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      {columns.filter((c) => c.ambiguous && c.include).map((c) => (
        <div key={c.source_name} className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>
          {Icon.warn()}<span>{t("imports.modal.ambiguousWarning", { column: c.source_name })}</span>
        </div>
      ))}

      {isNestedPayload && (
        <div className="card" style={{ padding: 14, marginBottom: 16, background: "var(--bg)" }}>
          <div style={{ fontSize: 13, marginBottom: 8 }}>{t("imports.modal.payloadPreviewLabel")}</div>
          <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, background: "var(--surface)", padding: 10, borderRadius: 8, margin: 0, overflowX: "auto" }}>
            {(payloadEntry.sample || []).map((s, i) => <div key={i}>{s}</div>)}
          </pre>
          <div style={{ fontSize: 13, margin: "12px 0 8px" }}>{t("imports.modal.payloadOrientation")}</div>
          <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, background: "var(--surface)", padding: 10, borderRadius: 8, margin: 0, overflowX: "auto" }}>
{`select
  payload->>'some_key' as some_key,
  payload->'nested'->>'field' as nested_field
from {{ source('imports', '${targetTable || "..."}') }}`}
          </pre>
        </div>
      )}

      {isNestedPayload && (
        <button type="button" className="link" style={{ marginBottom: 12 }} onClick={() => setExpanded((v) => !v)}>
          {expanded ? t("imports.modal.hideColumns") : t("imports.modal.customizeColumns")}
        </button>
      )}

      {showTable && (
        <div style={{ overflowX: "auto", marginBottom: 16 }}>
          <table className="table">
            <thead>
              <tr>
                <th>{t("imports.modal.colInclude")}</th>
                <th>{t("imports.modal.colSource")}</th>
                <th>{t("imports.modal.colTarget")}</th>
                <th>{t("imports.modal.colType")}</th>
                <th>{t("imports.modal.colConfidence")}</th>
                <th>{t("imports.modal.preview")}</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((c, idx) => {
                const invalidName = c.include && (!IDENTIFIER_RE.test(c.target_name) || hasDuplicate(c.target_name));
                const showDateFormat = c.include && (c.target_type === "date" || c.target_type === "timestamp");
                return (
                  <tr key={c.source_name} style={{ opacity: c.include ? 1 : 0.5 }}>
                    <td>
                      <input type="checkbox" checked={c.include} onChange={(e) => updateColumn(idx, { include: e.target.checked })} />
                    </td>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{c.source_name}</td>
                    <td style={{ minWidth: 140 }}>
                      <Input
                        className="input"
                        style={{ fontFamily: "var(--font-m)", fontSize: 12, borderColor: invalidName ? "var(--danger)" : undefined }}
                        value={c.target_name}
                        disabled={!c.include}
                        onChange={(e) => updateColumn(idx, { target_name: e.target.value })}
                      />
                      {showDateFormat && (
                        <select
                          className="input" style={{ marginTop: 6, fontSize: 11.5 }}
                          value={c.format || "%d/%m/%Y"}
                          onChange={(e) => updateColumn(idx, { format: e.target.value })}
                        >
                          <option value="%Y-%m-%d">{t("imports.modal.dateFormatISO")}</option>
                          <option value="%d/%m/%Y">{t("imports.modal.dateFormatDDMM")}</option>
                          <option value="%m/%d/%Y">{t("imports.modal.dateFormatMMDD")}</option>
                        </select>
                      )}
                    </td>
                    <td style={{ minWidth: 110 }}>
                      <select className="input" value={c.target_type} disabled={!c.include} onChange={(e) => updateColumn(idx, { target_type: e.target.value })}>
                        {TYPES.map((ty) => <option key={ty} value={ty}>{ty}</option>)}
                      </select>
                    </td>
                    <td>
                      <Badge tone={c.confidence >= 0.95 ? "accent" : "danger"}>{Math.round((c.confidence ?? 0) * 100)}%</Badge>
                    </td>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)" }}>
                      {(c.sample || []).slice(0, 3).map((s, i) => (
                        <div key={i}>{previewCast(s, c.target_type, c.format)}</div>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", marginBottom: 8 }}>
        <div style={{ flex: 2 }}>
          <Field label={t("imports.modal.targetTable")}>
            <Input id="import-target-table" value={targetTable} onChange={(e) => setTargetTable(e.target.value)} style={{ fontFamily: "var(--font-m)" }} />
          </Field>
        </div>
        <div style={{ flex: 1 }}>
          <Field label={t("imports.modal.writeMode")}>
            <select className="input" value={writeMode} onChange={(e) => setWriteMode(e.target.value)}>
              <option value="create">{t("imports.modal.writeModeCreate")}</option>
              <option value="replace">{t("imports.modal.writeModeReplace")}</option>
              <option value="append">{t("imports.modal.writeModeAppend")}</option>
            </select>
          </Field>
        </div>
      </div>

      {showTable && (
        <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 12 }}>
          {t("imports.modal.columnsSelected", { included: includedCount, total: columns.length })}
        </div>
      )}

      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose}>{t("imports.modal.cancel")}</Button>
        <Button type="button" disabled={!canValidate || busy} onClick={submit}>
          {busy ? t("imports.modal.running") : t("imports.modal.validate")}
        </Button>
      </div>
    </Modal>
  );
}
