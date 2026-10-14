import { useTranslation } from "react-i18next";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Icon } from "../../components/icons.jsx";

export const FORMATS = [
  { value: "csv", label: "CSV" },
  { value: "excel", label: "Excel" },
  { value: "json", label: "JSON" },
  { value: "xml", label: "XML" },
];

export const PAYLOAD_FORMATS = new Set(["csv", "excel"]);

// Client-side preview only — the backend re-derives this itself (schema_infer's own
// slugifier) at import time; this mirrors it just closely enough to show the user where
// the file will land before they commit.
export const previewTableName = (n) => {
  const ascii = (n || "").normalize("NFKD").replace(/[̀-ͯ]/g, "");
  const slug = ascii.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return slug ? (/^[0-9]/.test(slug) ? `col_${slug}` : slug) : "col";
};

// Mirrors the backend's own identifier rule (file_import.py's IDENTIFIER_RE) — lowercase
// letters, digits and underscore only, can't start with a digit.
export const IDENTIFIER_RE = /^[a-z_][a-z0-9_]{0,62}$/;

// Shared field block for "how should this one file be imported" — format, typed/payload
// mode, and every format-specific option. Factored out of ImportWizardDrawer so the bulk
// import wizard's per-file step renders *exactly* the same fields/logic instead of a
// reimplementation that could drift from the single-file flow over time. Every value is
// controlled by the caller (ImportWizardDrawer owns one set of useState, the bulk wizard
// owns one set per file) — this component holds no state of its own.
export default function ImportConfigFields({
  allowedFormats = FORMATS.map((f) => f.value),
  format, onFormatChange,
  importMode, onImportModeChange,
  payloadWriteMode, onPayloadWriteModeChange,
  delimiter, onDelimiterChange,
  encoding, onEncodingChange,
  sheet, onSheetChange,
  rootPath, onRootPathChange,
  recordXpath, onRecordXpathChange, xpathCandidates = [], xpathLoading = false,
  sourcePk, onSourcePkChange, pkCandidates = [], pkLoading = false,
  sourceSystem, onSourceSystemChange,
  name, onNameChange,
  fileLabel = "",
}) {
  const { t } = useTranslation();
  const formats = FORMATS.filter((f) => allowedFormats.includes(f.value));
  const isPayload = importMode === "payload" && PAYLOAD_FORMATS.has(format);

  return (
    <>
      {formats.length > 1 && (
        <Field label={t("imports.wizard.format")}>
          <div className="seg" style={{ flexWrap: "wrap" }}>
            {formats.map((f) => (
              <button key={f.value} type="button" className={"seg-opt" + (format === f.value ? " selected" : "")} style={{ flex: "1 1 45%" }} onClick={() => onFormatChange(f.value)}>
                <div className="seg-role">{format === f.value && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{f.label}</div>
              </button>
            ))}
          </div>
        </Field>
      )}

      {PAYLOAD_FORMATS.has(format) && (
        <Field label={t("imports.wizard.importModeLabel")}>
          <div className="seg">
            <button type="button" className={"seg-opt" + (importMode === "typed" ? " selected" : "")} onClick={() => onImportModeChange("typed")}>
              <div className="seg-role">{t("imports.wizard.modeTyped")}</div>
            </button>
            <button type="button" className={"seg-opt" + (importMode === "payload" ? " selected" : "")} onClick={() => onImportModeChange("payload")}>
              <div className="seg-role">{t("imports.wizard.modePayload")}</div>
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>
            {importMode === "typed" ? t("imports.wizard.modeTypedHelp") : t("imports.wizard.modePayloadHelp")}
          </div>
        </Field>
      )}

      {isPayload && (
        <>
          <Field label={t("imports.wizard.writeMode")}>
            <select className="input" value={payloadWriteMode} onChange={(e) => onPayloadWriteModeChange(e.target.value)}>
              <option value="create">{t("imports.modal.writeModeCreate")}</option>
              <option value="replace">{t("imports.modal.writeModeReplace")}</option>
              <option value="append">{t("imports.modal.writeModeAppend")}</option>
            </select>
          </Field>
          <Field label={t("imports.wizard.sourcePk")}>
            {pkLoading && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("imports.wizard.sourcePkAnalyzing")}</div>}
            {pkCandidates.length > 0 && (
              <select
                multiple className="input" style={{ marginBottom: 8, minHeight: 90 }}
                value={sourcePk ? sourcePk.split(",").map((s) => s.trim()).filter(Boolean) : []}
                onChange={(e) => onSourcePkChange(Array.from(e.target.selectedOptions, (o) => o.value).join(","))}
              >
                {pkCandidates.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            <Input placeholder={t("imports.wizard.sourcePkPlaceholder")} value={sourcePk} onChange={(e) => onSourcePkChange(e.target.value)} />
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("imports.wizard.sourcePkHelp")}</div>
          </Field>
          <Field label={t("imports.wizard.sourceSystem")}>
            <Input placeholder={t("imports.wizard.sourceSystemPlaceholder")} value={sourceSystem} onChange={(e) => onSourceSystemChange(e.target.value)} />
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("imports.wizard.sourceSystemHelp")}</div>
          </Field>
          <Field label={t("imports.wizard.bronzeTableName")}>
            <Input
              value={name} onChange={(e) => onNameChange(e.target.value)}
              placeholder={previewTableName(fileLabel)}
              style={{ borderColor: name.trim() && !IDENTIFIER_RE.test(name.trim()) ? "var(--danger)" : undefined }}
            />
            {name.trim() && !IDENTIFIER_RE.test(name.trim()) ? (
              <div style={{ fontSize: 11.5, color: "var(--danger)", marginTop: 6 }}>{t("imports.wizard.tableNameInvalid")}</div>
            ) : (
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6, fontFamily: "var(--font-m)" }}>
                {previewTableName(name.trim() || fileLabel)}
              </div>
            )}
          </Field>
        </>
      )}

      {format === "csv" && (
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Field label={t("imports.wizard.delimiter")}>
              <Input placeholder="," value={delimiter} onChange={(e) => onDelimiterChange(e.target.value)} />
            </Field>
          </div>
          <div style={{ flex: 1 }}>
            <Field label={t("imports.wizard.encoding")}>
              <Input placeholder="utf-8" value={encoding} onChange={(e) => onEncodingChange(e.target.value)} />
            </Field>
          </div>
        </div>
      )}

      {format === "excel" && (
        <Field label={t("imports.wizard.sheet")}>
          <Input placeholder="0" value={sheet} onChange={(e) => onSheetChange(e.target.value)} />
        </Field>
      )}

      {format === "json" && allowedFormats.includes("json") && (
        <Field label={t("imports.wizard.rootPath")}>
          <Input placeholder="data.results" value={rootPath} onChange={(e) => onRootPathChange(e.target.value)} />
        </Field>
      )}

      {format === "xml" && allowedFormats.includes("xml") && (
        <Field label={t("imports.wizard.recordXpath")}>
          {xpathLoading && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("imports.wizard.xpathAnalyzing")}</div>}
          {xpathCandidates.length > 0 && (
            <select className="input" style={{ marginBottom: 8 }} value={recordXpath} onChange={(e) => onRecordXpathChange(e.target.value)}>
              {xpathCandidates.map((c) => (
                <option key={c.xpath} value={c.xpath}>{c.xpath} ({t("imports.wizard.xpathOccurrences", { count: c.count })})</option>
              ))}
            </select>
          )}
          <Input placeholder="//commande" value={recordXpath} onChange={(e) => onRecordXpathChange(e.target.value)} />
        </Field>
      )}

      {!isPayload && (
        <Field label={t("imports.wizard.importName")}>
          <Input
            value={name} onChange={(e) => onNameChange(e.target.value)} placeholder={fileLabel}
            style={{ borderColor: name.trim() && !IDENTIFIER_RE.test(name.trim()) ? "var(--danger)" : undefined }}
          />
          {name.trim() && !IDENTIFIER_RE.test(name.trim()) && (
            <div style={{ fontSize: 11.5, color: "var(--danger)", marginTop: 6 }}>{t("imports.wizard.tableNameInvalid")}</div>
          )}
        </Field>
      )}
    </>
  );
}
