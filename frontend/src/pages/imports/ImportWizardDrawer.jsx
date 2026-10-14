import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as sourcesApi from "../../api/sources.js";
import * as importsApi from "../../api/imports.js";
import ImportConfigFields, { PAYLOAD_FORMATS, previewTableName, IDENTIFIER_RE } from "./ImportConfigFields.jsx";

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

  const isPayload = importMode === "payload" && PAYLOAD_FORMATS.has(format);
  const valid = file && targetSourceId && archiveSourceId && (format !== "xml" || recordXpath.trim())
    && (!isPayload || IDENTIFIER_RE.test(name.trim()));

  const pickFile = (f) => {
    if (!f) return;
    setFile(f);
    if (/\.xlsx?$/i.test(f.name)) setFormat("excel");
    else if (/\.csv$/i.test(f.name)) setFormat("csv");
    else if (/\.(json|ndjson)$/i.test(f.name)) { setFormat("json"); setImportMode("typed"); }
    else if (/\.xml$/i.test(f.name)) { setFormat("xml"); setImportMode("typed"); }
  };

  // Payload mode's "name" field doubles as the bronze table name (it has no later mapping
  // step to ask for one) — pre-fill it from the file name as soon as that mode applies, same
  // as typed mode's target_table gets pre-filled once the schema-validation step opens.
  useEffect(() => {
    if (isPayload && file && !name) setName(previewTableName(file.name));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPayload, file]);

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

      <ImportConfigFields
        format={format} onFormatChange={setFormat}
        importMode={importMode} onImportModeChange={setImportMode}
        payloadWriteMode={payloadWriteMode} onPayloadWriteModeChange={setPayloadWriteMode}
        delimiter={delimiter} onDelimiterChange={setDelimiter}
        encoding={encoding} onEncodingChange={setEncoding}
        sheet={sheet} onSheetChange={setSheet}
        rootPath={rootPath} onRootPathChange={setRootPath}
        recordXpath={recordXpath} onRecordXpathChange={setRecordXpath} xpathCandidates={xpathCandidates} xpathLoading={xpathLoading}
        sourcePk={sourcePk} onSourcePkChange={setSourcePk} pkCandidates={pkCandidates} pkLoading={pkLoading}
        sourceSystem={sourceSystem} onSourceSystemChange={setSourceSystem}
        name={name} onNameChange={setName}
        fileLabel={file?.name || ""}
      />

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
