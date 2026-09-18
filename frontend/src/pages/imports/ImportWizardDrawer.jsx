import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as sourcesApi from "../../api/sources.js";
import * as importsApi from "../../api/imports.js";

const FORMATS = [
  { value: "csv", label: "CSV" },
  { value: "excel", label: "Excel" },
  { value: "json", label: "JSON" },
  { value: "xml", label: "XML" },
];

const PAYLOAD_FORMATS = new Set(["csv", "excel"]);

export default function ImportWizardDrawer({ onClose, onAnalyzed }) {
  const { t } = useTranslation();
  const [sources, setSources] = useState([]);
  const [file, setFile] = useState(null);
  const [format, setFormat] = useState("csv");
  const [importMode, setImportMode] = useState("typed");
  const [payloadWriteMode, setPayloadWriteMode] = useState("create");
  const [delimiter, setDelimiter] = useState("");
  const [encoding, setEncoding] = useState("");
  const [sheet, setSheet] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [recordXpath, setRecordXpath] = useState("");
  const [xpathCandidates, setXpathCandidates] = useState([]);
  const [xpathLoading, setXpathLoading] = useState(false);
  const [sourcePk, setSourcePk] = useState("");
  const [pkCandidates, setPkCandidates] = useState([]);
  const [pkLoading, setPkLoading] = useState(false);
  const [sourceSystem, setSourceSystem] = useState("");
  const [targetSourceId, setTargetSourceId] = useState("");
  const [archiveSourceId, setArchiveSourceId] = useState("");
  const [name, setName] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { sourcesApi.listSources().then(setSources); }, []);

  const pgSources = sources.filter((s) => s.type === "postgresql");
  const minioSources = sources.filter((s) => s.type === "minio");

  const valid = file && targetSourceId && archiveSourceId && (format !== "xml" || recordXpath.trim());
  const isPayload = importMode === "payload" && PAYLOAD_FORMATS.has(format);

  const pickFile = (f) => {
    if (!f) return;
    setFile(f);
    if (/\.xlsx?$/i.test(f.name)) setFormat("excel");
    else if (/\.csv$/i.test(f.name)) setFormat("csv");
    else if (/\.(json|ndjson)$/i.test(f.name)) { setFormat("json"); setImportMode("typed"); }
    else if (/\.xml$/i.test(f.name)) { setFormat("xml"); setImportMode("typed"); }
  };

  // Client-side preview only — the backend re-derives this itself (schema_infer's own
  // slugifier) at import time; this mirrors it just closely enough to show the user where
  // the file will land before they commit.
  const previewTableName = (n) => {
    const ascii = (n || "").normalize("NFKD").replace(/[̀-ͯ]/g, "");
    const slug = ascii.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    return slug ? (/^[0-9]/.test(slug) ? `col_${slug}` : slug) : "col";
  };

  useEffect(() => {
    if (format !== "xml" || !file) {
      setXpathCandidates([]);
      return;
    }
    let cancelled = false;
    setXpathLoading(true);
    importsApi.getXmlCandidates(file)
      .then((res) => {
        if (cancelled) return;
        setXpathCandidates(res.candidates);
        if (!recordXpath && res.candidates.length > 0) setRecordXpath(res.candidates[0].xpath);
      })
      .catch(() => { if (!cancelled) setXpathCandidates([]); })
      .finally(() => { if (!cancelled) setXpathLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [format, file]);

  // Schema-on-Read has no other preview step (direct import, no inference) — this is the
  // wizard's only way to let the user pick a source_pk before commit.
  useEffect(() => {
    if (!isPayload || !file) {
      setPkCandidates([]);
      return;
    }
    let cancelled = false;
    setPkLoading(true);
    const opts = format === "csv" ? { delimiter: delimiter || undefined, encoding: encoding || undefined } : { sheet: sheet || undefined };
    importsApi.getColumns(file, format, opts)
      .then((res) => { if (!cancelled) setPkCandidates(res.columns); })
      .catch(() => { if (!cancelled) setPkCandidates([]); })
      .finally(() => { if (!cancelled) setPkLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPayload, file, format]);

  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const formatOptions = {};
      if (format === "csv") {
        if (delimiter) formatOptions.delimiter = delimiter;
        if (encoding) formatOptions.encoding = encoding;
      } else if (format === "excel") {
        if (sheet) formatOptions.sheet = sheet;
      } else if (format === "json") {
        if (rootPath) formatOptions.root_path = rootPath;
      } else if (format === "xml") {
        formatOptions.record_xpath = recordXpath.trim();
      }
      if (isPayload && sourcePk.trim()) formatOptions.source_pk = sourcePk.trim();
      if (isPayload && sourceSystem.trim()) formatOptions.source_system = sourceSystem.trim();
      const fi = await importsApi.createImport(file, {
        format, formatOptions, targetSourceId: Number(targetSourceId), archiveSourceId: Number(archiveSourceId), name: name.trim() || undefined,
        importMode: isPayload ? "payload" : "typed", writeMode: isPayload ? payloadWriteMode : "create",
      });
      onAnalyzed(fi);
    } catch (err) {
      setError(err.message || t("imports.wizard.analyzeFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={t("imports.wizard.title")} description={t("imports.wizard.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <Field>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); pickFile(e.dataTransfer.files?.[0]); }}
          onClick={() => document.getElementById("import-file-input").click()}
          style={{
            border: `2px dashed ${dragOver ? "var(--ember)" : "var(--border)"}`, borderRadius: 12, padding: "28px 16px",
            textAlign: "center", cursor: "pointer", background: dragOver ? "var(--ember-soft)" : "var(--bg)",
          }}
        >
          <input id="import-file-input" type="file" accept=".csv,.xlsx,.xls,.json,.ndjson,.xml" style={{ display: "none" }}
            onChange={(e) => pickFile(e.target.files?.[0])} />
          {file ? (
            <div style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>{t("imports.wizard.chosenFile")} {file.name}</div>
          ) : (
            <div style={{ color: "var(--text-muted)", fontSize: 13.5 }}>{t("imports.wizard.dropFile")}</div>
          )}
        </div>
      </Field>

      <Field label={t("imports.wizard.format")}>
        <div className="seg" style={{ flexWrap: "wrap" }}>
          {FORMATS.map((f) => (
            <button key={f.value} type="button" className={"seg-opt" + (format === f.value ? " selected" : "")} style={{ flex: "1 1 45%" }} onClick={() => setFormat(f.value)}>
              <div className="seg-role">{format === f.value && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{f.label}</div>
            </button>
          ))}
        </div>
      </Field>

      {PAYLOAD_FORMATS.has(format) && (
        <Field label={t("imports.wizard.importModeLabel")}>
          <div className="seg">
            <button type="button" className={"seg-opt" + (importMode === "typed" ? " selected" : "")} onClick={() => setImportMode("typed")}>
              <div className="seg-role">{t("imports.wizard.modeTyped")}</div>
            </button>
            <button type="button" className={"seg-opt" + (importMode === "payload" ? " selected" : "")} onClick={() => setImportMode("payload")}>
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
            <select className="input" value={payloadWriteMode} onChange={(e) => setPayloadWriteMode(e.target.value)}>
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
                onChange={(e) => setSourcePk(Array.from(e.target.selectedOptions, (o) => o.value).join(","))}
              >
                {pkCandidates.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            <Input placeholder={t("imports.wizard.sourcePkPlaceholder")} value={sourcePk} onChange={(e) => setSourcePk(e.target.value)} />
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("imports.wizard.sourcePkHelp")}</div>
          </Field>
          <Field label={t("imports.wizard.sourceSystem")}>
            <Input placeholder={t("imports.wizard.sourceSystemPlaceholder")} value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)} />
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("imports.wizard.sourceSystemHelp")}</div>
          </Field>
          <Field label={t("imports.wizard.bronzeTableName")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={previewTableName(file?.name || "")} />
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6, fontFamily: "var(--font-m)" }}>
              {previewTableName(name.trim() || file?.name || "")}
            </div>
          </Field>
        </>
      )}

      {format === "csv" && (
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Field label={t("imports.wizard.delimiter")}>
              <Input placeholder="," value={delimiter} onChange={(e) => setDelimiter(e.target.value)} />
            </Field>
          </div>
          <div style={{ flex: 1 }}>
            <Field label={t("imports.wizard.encoding")}>
              <Input placeholder="utf-8" value={encoding} onChange={(e) => setEncoding(e.target.value)} />
            </Field>
          </div>
        </div>
      )}

      {format === "excel" && (
        <Field label={t("imports.wizard.sheet")}>
          <Input placeholder="0" value={sheet} onChange={(e) => setSheet(e.target.value)} />
        </Field>
      )}

      {format === "json" && (
        <Field label={t("imports.wizard.rootPath")}>
          <Input placeholder="data.results" value={rootPath} onChange={(e) => setRootPath(e.target.value)} />
        </Field>
      )}

      {format === "xml" && (
        <Field label={t("imports.wizard.recordXpath")}>
          {xpathLoading && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("imports.wizard.xpathAnalyzing")}</div>}
          {xpathCandidates.length > 0 && (
            <select className="input" style={{ marginBottom: 8 }} value={recordXpath} onChange={(e) => setRecordXpath(e.target.value)}>
              {xpathCandidates.map((c) => (
                <option key={c.xpath} value={c.xpath}>{c.xpath} ({t("imports.wizard.xpathOccurrences", { count: c.count })})</option>
              ))}
            </select>
          )}
          <Input placeholder="//commande" value={recordXpath} onChange={(e) => setRecordXpath(e.target.value)} />
        </Field>
      )}

      <Field label={t("imports.wizard.targetSource")}>
        <select className="input" value={targetSourceId} onChange={(e) => setTargetSourceId(e.target.value)}>
          <option value="">{t("imports.wizard.chooseSource")}</option>
          {pgSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </Field>

      <Field label={t("imports.wizard.archiveSource")}>
        <select className="input" value={archiveSourceId} onChange={(e) => setArchiveSourceId(e.target.value)}>
          <option value="">{t("imports.wizard.chooseSource")}</option>
          {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </Field>

      {!isPayload && (
        <Field label={t("imports.wizard.importName")}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={file?.name || ""} />
        </Field>
      )}

      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose}>{t("imports.wizard.cancel")}</Button>
        <Button type="button" disabled={!valid || busy} onClick={submit}>
          {isPayload
            ? (busy ? t("imports.wizard.importing") : t("imports.wizard.importAction"))
            : (busy ? t("imports.wizard.analyzing") : t("imports.wizard.analyze"))}
        </Button>
      </div>
    </Drawer>
  );
}
