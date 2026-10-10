import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as sourcesApi from "../../api/sources.js";
import * as importsApi from "../../api/imports.js";
import * as medallionApi from "../../api/medallion.js";
import ImportConfigFields, { PAYLOAD_FORMATS, previewTableName } from "../imports/ImportConfigFields.jsx";

const IMPORTABLE_LOCAL_RE = /\.(csv|xlsx?|json|ndjson|xml)$/i;
const IMPORTABLE_S3_RE = /\.(csv|xlsx?)$/i;
const WRITE_MODE_LABEL_KEY = { create: "imports.modal.writeModeCreate", replace: "imports.modal.writeModeReplace", append: "imports.modal.writeModeAppend" };

let seq = 0;
const nextId = () => `bulk-${++seq}`;

const detectFormat = (filename) => {
  if (/\.xlsx?$/i.test(filename)) return "excel";
  if (/\.csv$/i.test(filename)) return "csv";
  if (/\.(json|ndjson)$/i.test(filename)) return "json";
  if (/\.xml$/i.test(filename)) return "xml";
  return "csv";
};

const makeLocalItem = (file) => ({
  id: nextId(), origin: "local", file, filename: file.name,
  format: detectFormat(file.name), importMode: "typed", payloadWriteMode: "create",
  delimiter: "", encoding: "", sheet: "", rootPath: "", recordXpath: "",
  sourcePk: "", sourceSystem: "",
  targetSourceId: "", archiveSourceId: "", name: "",
  targetTable: "", writeMode: "create",
  status: "pending", resultError: "",
});

const makeS3Item = (sourceId, bucket, key) => {
  const filename = key.split("/").pop();
  return {
    id: nextId(), origin: "s3", s3SourceId: sourceId, bucket, key, filename,
    format: /\.(xlsx|xls)$/i.test(filename) ? "excel" : "csv", importMode: "typed", payloadWriteMode: "create",
    delimiter: "", encoding: "", sheet: "", rootPath: "", recordXpath: "",
    sourcePk: "", sourceSystem: "",
    targetSourceId: "", archiveSourceId: "", name: "",
    targetTable: "", writeMode: "create",
    status: "pending", resultError: "",
  };
};

// One file's configuration fields — reuses ImportConfigFields (the exact same fields/logic
// as the standalone single-file wizard) plus the target-table/write-mode pair that flow
// defers to its own post-analysis SchemaValidationModal. Bulk mode has no equivalent
// per-file mapping-review step (the recap IS the review, per the user's own spec: configure
// each file, then one recap, then confirm) — so the table name has to be collected here,
// upfront, same as the "bronze table name" field payload mode already asks for.
function FileConfigStep({ item, onChange, project, pgSources, minioSources, defaults }) {
  const { t } = useTranslation();
  const isLocal = item.origin === "local";
  const isPayload = item.importMode === "payload" && PAYLOAD_FORMATS.has(item.format);

  const [xpathCandidates, setXpathCandidates] = useState([]);
  const [xpathLoading, setXpathLoading] = useState(false);
  const [pkCandidates, setPkCandidates] = useState([]);
  const [pkLoading, setPkLoading] = useState(false);

  useEffect(() => {
    if (isLocal && !item.targetSourceId && defaults?.targetSourceId) onChange({ targetSourceId: defaults.targetSourceId });
    if (isLocal && !item.archiveSourceId && defaults?.archiveSourceId) onChange({ archiveSourceId: defaults.archiveSourceId });
    if (!item.targetTable) onChange({ targetTable: previewTableName(item.name.trim() || item.filename) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (item.format !== "xml" || !isLocal) { setXpathCandidates([]); return; }
    let cancelled = false;
    setXpathLoading(true);
    importsApi.getXmlCandidates(item.file)
      .then((res) => {
        if (cancelled) return;
        setXpathCandidates(res.candidates);
        if (!item.recordXpath && res.candidates.length > 0) onChange({ recordXpath: res.candidates[0].xpath });
      })
      .catch(() => { if (!cancelled) setXpathCandidates([]); })
      .finally(() => { if (!cancelled) setXpathLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.format]);

  useEffect(() => {
    if (!isPayload) { setPkCandidates([]); return; }
    let cancelled = false;
    setPkLoading(true);
    const formatOptions = item.format === "csv"
      ? { delimiter: item.delimiter || undefined, encoding: item.encoding || undefined }
      : { sheet: item.sheet || undefined };
    const request = isLocal
      ? importsApi.getColumns(item.file, item.format, formatOptions)
      : medallionApi.getObjectStoreColumns(project.id, { source_id: item.s3SourceId, bucket: item.bucket, key: item.key, format: item.format, format_options: formatOptions });
    request
      .then((res) => { if (!cancelled) setPkCandidates(res.columns); })
      .catch(() => { if (!cancelled) setPkCandidates([]); })
      .finally(() => { if (!cancelled) setPkLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPayload, item.format, item.delimiter, item.encoding, item.sheet]);

  return (
    <>
      {isLocal && (
        <>
          <Field label={t("imports.wizard.targetSource")}>
            <select className="input" value={item.targetSourceId} onChange={(e) => onChange({ targetSourceId: e.target.value })}>
              <option value="">{t("imports.wizard.chooseSource")}</option>
              {pgSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label={t("imports.wizard.archiveSource")}>
            <select className="input" value={item.archiveSourceId} onChange={(e) => onChange({ archiveSourceId: e.target.value })}>
              <option value="">{t("imports.wizard.chooseSource")}</option>
              {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
        </>
      )}

      <ImportConfigFields
        allowedFormats={isLocal ? undefined : [item.format]}
        format={item.format} onFormatChange={(v) => onChange({ format: v })}
        importMode={item.importMode} onImportModeChange={(v) => onChange({ importMode: v })}
        payloadWriteMode={item.payloadWriteMode} onPayloadWriteModeChange={(v) => onChange({ payloadWriteMode: v })}
        delimiter={item.delimiter} onDelimiterChange={(v) => onChange({ delimiter: v })}
        encoding={item.encoding} onEncodingChange={(v) => onChange({ encoding: v })}
        sheet={item.sheet} onSheetChange={(v) => onChange({ sheet: v })}
        rootPath={item.rootPath} onRootPathChange={(v) => onChange({ rootPath: v })}
        recordXpath={item.recordXpath} onRecordXpathChange={(v) => onChange({ recordXpath: v })} xpathCandidates={xpathCandidates} xpathLoading={xpathLoading}
        sourcePk={item.sourcePk} onSourcePkChange={(v) => onChange({ sourcePk: v })} pkCandidates={pkCandidates} pkLoading={pkLoading}
        sourceSystem={item.sourceSystem} onSourceSystemChange={(v) => onChange({ sourceSystem: v })}
        name={item.name} onNameChange={(v) => onChange({ name: v })}
        fileLabel={item.filename}
      />

      {!isPayload && (
        <>
          <Field label={t("imports.modal.targetTable")}>
            <Input value={item.targetTable} onChange={(e) => onChange({ targetTable: e.target.value })} style={{ fontFamily: "var(--font-m)" }} />
          </Field>
          <Field label={t("imports.modal.writeMode")}>
            <select className="input" value={item.writeMode} onChange={(e) => onChange({ writeMode: e.target.value })}>
              <option value="create">{t("imports.modal.writeModeCreate")}</option>
              <option value="replace">{t("imports.modal.writeModeReplace")}</option>
              <option value="append">{t("imports.modal.writeModeAppend")}</option>
            </select>
          </Field>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: -8, marginBottom: 14 }}>
            {t("medallion.bulkImport.autoMappingHelp")}
          </div>
        </>
      )}
    </>
  );
}

export default function BulkImportWizard({ project, sources, onClose, onImported }) {
  const { t } = useTranslation();
  const [step, setStep] = useState(0); // 0 = selection, 1..N = per-file config, N+1 = recap
  const [items, setItems] = useState([]);
  const [tab, setTab] = useState("local");
  const [dragOver, setDragOver] = useState(false);

  const [s3SourceId, setS3SourceId] = useState("");
  const [s3Buckets, setS3Buckets] = useState([]);
  const [s3Bucket, setS3Bucket] = useState("");
  const [s3Objects, setS3Objects] = useState([]);
  const [s3Browsing, setS3Browsing] = useState(false);
  const [s3Selected, setS3Selected] = useState(new Set());
  const [s3Filter, setS3Filter] = useState("");

  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState(false);

  const minioSources = sources.filter((s) => s.type === "minio");
  const pgSources = sources.filter((s) => s.type === "postgresql");

  const addFiles = (fileList) => {
    const picked = Array.from(fileList || []).filter((f) => IMPORTABLE_LOCAL_RE.test(f.name));
    if (picked.length === 0) return;
    setItems((list) => [...list, ...picked.map(makeLocalItem)]);
  };

  const removeItem = (id) => setItems((list) => list.filter((it) => it.id !== id));

  useEffect(() => {
    setS3Buckets([]); setS3Bucket(""); setS3Objects([]); setS3Selected(new Set()); setS3Filter("");
    if (!s3SourceId) return;
    let cancelled = false;
    setS3Browsing(true);
    sourcesApi.introspectSource(s3SourceId)
      .then((res) => {
        if (cancelled) return;
        if (res.objects && res.objects.length) {
          setS3Objects(res.objects);
          setS3Bucket((res.buckets && res.buckets[0]) || "");
          setS3Browsing(false);
          return;
        }
        if (res.buckets && res.buckets.length === 1) {
          return sourcesApi.introspectSource(s3SourceId, res.buckets[0]).then((res2) => {
            if (cancelled) return;
            setS3Objects(res2.objects || []);
            setS3Bucket(res.buckets[0]);
            setS3Browsing(false);
          });
        }
        setS3Buckets(res.buckets || []);
        setS3Browsing(false);
      })
      .catch(() => { if (!cancelled) { setS3Objects([]); setS3Buckets([]); setS3Browsing(false); } });
    return () => { cancelled = true; };
  }, [s3SourceId]);

  const loadBucket = (bucket) => {
    setS3Browsing(true);
    setS3Selected(new Set());
    sourcesApi.introspectSource(s3SourceId, bucket)
      .then((res) => { setS3Objects(res.objects || []); setS3Bucket(bucket); })
      .catch(() => setS3Objects([]))
      .finally(() => setS3Browsing(false));
  };

  const importableS3Objects = s3Objects.filter((o) => IMPORTABLE_S3_RE.test(o));
  const visibleS3Objects = s3Filter ? importableS3Objects.filter((o) => o.toLowerCase().includes(s3Filter.toLowerCase())) : importableS3Objects;

  const toggleS3Object = (obj) => setS3Selected((sel) => {
    const next = new Set(sel);
    if (next.has(obj)) next.delete(obj); else next.add(obj);
    return next;
  });

  const addSelectedS3Objects = () => {
    const picked = [...s3Selected];
    if (picked.length === 0) return;
    setItems((list) => [...list, ...picked.map((key) => makeS3Item(Number(s3SourceId), s3Bucket, key))]);
    setS3Selected(new Set());
  };

  const patchItem = (id, patch) => setItems((list) => list.map((it) => (it.id === id ? { ...it, ...patch } : it)));

  const totalSteps = items.length;
  const currentItem = step >= 1 && step <= totalSteps ? items[step - 1] : null;

  // Smart default only — each file stays independently editable, this just saves repeat
  // clicks when every file in the batch goes to the same target/archive source.
  const lastLocalDefaults = (() => {
    for (let i = step - 2; i >= 0; i--) {
      if (items[i]?.origin === "local" && items[i].targetSourceId) {
        return { targetSourceId: items[i].targetSourceId, archiveSourceId: items[i].archiveSourceId };
      }
    }
    return null;
  })();

  const stepValid = (() => {
    if (!currentItem) return false;
    const isPayload = currentItem.importMode === "payload" && PAYLOAD_FORMATS.has(currentItem.format);
    if (currentItem.origin === "local" && (!currentItem.targetSourceId || !currentItem.archiveSourceId)) return false;
    if (currentItem.format === "xml" && !currentItem.recordXpath?.trim()) return false;
    if (!isPayload && !currentItem.targetTable?.trim()) return false;
    return true;
  })();

  const goNext = () => setStep((s) => s + 1);
  const goPrev = () => setStep((s) => Math.max(0, s - 1));

  const pollUntilDone = (id) => new Promise((resolve, reject) => {
    const tick = () => {
      importsApi.getImportStatus(id).then((s) => {
        if (s.status === "importing") { setTimeout(tick, 1500); return; }
        resolve(s);
      }).catch(reject);
    };
    tick();
  });

  const runOne = async (item) => {
    const isPayload = item.importMode === "payload" && PAYLOAD_FORMATS.has(item.format);
    const formatOptions = {};
    if (item.format === "csv") {
      if (item.delimiter) formatOptions.delimiter = item.delimiter;
      if (item.encoding) formatOptions.encoding = item.encoding;
    } else if (item.format === "excel") {
      if (item.sheet) formatOptions.sheet = item.sheet;
    } else if (item.format === "json") {
      if (item.rootPath) formatOptions.root_path = item.rootPath;
    } else if (item.format === "xml") {
      formatOptions.record_xpath = item.recordXpath.trim();
    }
    if (isPayload && item.sourcePk.trim()) formatOptions.source_pk = item.sourcePk.trim();
    if (isPayload && item.sourceSystem.trim()) formatOptions.source_system = item.sourceSystem.trim();

    let fi;
    if (item.origin === "local") {
      fi = await importsApi.createImport(item.file, {
        format: item.format, formatOptions,
        targetSourceId: Number(item.targetSourceId), archiveSourceId: Number(item.archiveSourceId),
        name: item.name.trim() || undefined,
        importMode: isPayload ? "payload" : "typed", writeMode: isPayload ? item.payloadWriteMode : "create",
      });
    } else {
      fi = await medallionApi.importFromObjectStore(project.id, {
        source_id: item.s3SourceId, bucket: item.bucket, key: item.key,
        format: item.format, format_options: formatOptions,
        name: item.name.trim() || undefined,
        import_mode: isPayload ? "payload" : "typed", write_mode: isPayload ? item.payloadWriteMode : "create",
      });
    }

    if (fi.status === "awaiting_validation") {
      // Bulk mode has no per-file mapping-review step — the backend's own inferred column
      // mapping is accepted as-is (same inference the standalone wizard proposes as a
      // starting point); fine-tune it later from the Imports page if needed.
      await importsApi.updateImport(fi.id, { column_mapping: fi.column_mapping, target_table: item.targetTable.trim(), write_mode: item.writeMode });
      fi = await importsApi.triggerRun(fi.id);
      const finalStatus = await pollUntilDone(fi.id);
      if (finalStatus.status !== "imported") throw new Error(finalStatus.last_error || t("medallion.bulkImport.importFailedGeneric"));
    } else if (fi.status !== "imported") {
      throw new Error(fi.last_error || t("medallion.bulkImport.importFailedGeneric"));
    }

    await medallionApi.createDataset(project.id, {
      layer: "bronze", name: fi.target_table, source_id: fi.target_source_id,
      source_object: `${fi.target_schema}.${fi.target_table}`, load_mode: "full", upstream_dataset_ids: [],
    });
  };

  const confirmAll = async () => {
    setConfirming(true);
    for (const item of items) {
      patchItem(item.id, { status: "running", resultError: "" });
      try {
        await runOne(item);
        patchItem(item.id, { status: "done" });
      } catch (err) {
        patchItem(item.id, { status: "error", resultError: err.message || t("medallion.bulkImport.importFailedGeneric") });
      }
    }
    setConfirming(false);
    setDone(true);
  };

  const finish = () => {
    if (items.some((it) => it.status === "done")) onImported();
    onClose();
  };

  return (
    <Modal title={t("medallion.bulkImport.title")} description={t("medallion.bulkImport.description")} onClose={done ? finish : onClose} maxWidth={920}>
      <div style={{ marginBottom: 18 }}>
        <Badge tone="accent">
          {step === 0 ? t("medallion.bulkImport.stepSelect")
            : step <= totalSteps ? t("medallion.bulkImport.stepConfig", { current: step, total: totalSteps })
            : t("medallion.bulkImport.stepRecap")}
        </Badge>
      </div>

      {step === 0 && (
        <>
          <div className="seg" style={{ marginBottom: 16 }}>
            <button type="button" className={"seg-opt" + (tab === "local" ? " selected" : "")} onClick={() => setTab("local")}>
              <div className="seg-role">{t("medallion.bulkImport.tabLocal")}</div>
            </button>
            <button type="button" className={"seg-opt" + (tab === "s3" ? " selected" : "")} onClick={() => setTab("s3")}>
              <div className="seg-role">{t("medallion.bulkImport.tabS3")}</div>
            </button>
          </div>

          {tab === "local" && (
            <Field>
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
                onClick={() => document.getElementById("bulk-import-file-input").click()}
                style={{
                  border: `2px dashed ${dragOver ? "var(--ember)" : "var(--border)"}`, borderRadius: 12, padding: "28px 16px",
                  textAlign: "center", cursor: "pointer", background: dragOver ? "var(--ember-soft)" : "var(--bg)",
                }}
              >
                <input id="bulk-import-file-input" type="file" multiple accept=".csv,.xlsx,.xls,.json,.ndjson,.xml" style={{ display: "none" }}
                  onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
                <div style={{ color: "var(--text-muted)", fontSize: 13.5 }}>{t("medallion.bulkImport.dropFiles")}</div>
              </div>
            </Field>
          )}

          {tab === "s3" && (
            <>
              <Field label={t("medallion.bulkImport.chooseSourceS3")}>
                <select className="input" value={s3SourceId} onChange={(e) => setS3SourceId(e.target.value)}>
                  <option value="">{t("imports.wizard.chooseSource")}</option>
                  {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>

              {s3Browsing && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("medallion.bulkImport.browsing")}</div>}

              {!s3Browsing && s3SourceId && s3Buckets.length > 0 && !s3Bucket && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {s3Buckets.map((b) => (
                    <button key={b} type="button" className="seg-opt" style={{ flex: "0 0 auto" }} onClick={() => loadBucket(b)}>{b}</button>
                  ))}
                </div>
              )}

              {!s3Browsing && s3Bucket && (
                <>
                  {s3Buckets.length > 1 && (
                    <button type="button" className="link" style={{ marginBottom: 8 }} onClick={() => { setS3Bucket(""); setS3Objects([]); }}>
                      {t("medallion.bulkImport.backToBuckets")}
                    </button>
                  )}
                  <Input placeholder={t("medallion.bulkImport.filterObjects")} value={s3Filter} onChange={(e) => setS3Filter(e.target.value)} style={{ marginBottom: 10 }} />
                  <div style={{ maxHeight: 240, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 10 }}>
                    {visibleS3Objects.length === 0 && (
                      <div style={{ padding: 14, color: "var(--text-muted)", fontSize: 13 }}>{t("medallion.bulkImport.noObjectsFound")}</div>
                    )}
                    {visibleS3Objects.map((obj) => (
                      <label key={obj} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderBottom: "1px solid var(--border)", fontSize: 12.5, fontFamily: "var(--font-m)", cursor: "pointer" }}>
                        <input type="checkbox" checked={s3Selected.has(obj)} onChange={() => toggleS3Object(obj)} />
                        {obj}
                      </label>
                    ))}
                  </div>
                  <Button type="button" variant="ghost" style={{ flex: "0 0 auto", marginTop: 10 }} disabled={s3Selected.size === 0} onClick={addSelectedS3Objects}>
                    {t("medallion.bulkImport.addSelected", { count: s3Selected.size })}
                  </Button>
                </>
              )}
            </>
          )}

          <div style={{ marginTop: 18, fontSize: 13, fontWeight: 600 }}>{t("medallion.bulkImport.pickedFiles", { count: items.length })}</div>
          {items.length === 0 && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("medallion.bulkImport.noFilesYet")}</div>}
          {items.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {items.map((it) => (
                <div key={it.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
                  {it.origin === "local" ? Icon.file({ width: 14, height: 14 }) : Icon.db({ width: 14, height: 14 })}
                  <span style={{ flex: 1, fontFamily: "var(--font-m)", fontSize: 12.5 }}>{it.filename}</span>
                  <Badge tone="neutral">{it.format.toUpperCase()}</Badge>
                  <button className="btn-icon" onClick={() => removeItem(it.id)} title={t("common.delete")}>{Icon.trash()}</button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {currentItem && (
        <>
          <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 14, fontFamily: "var(--font-m)" }}>{currentItem.filename}</div>
          <FileConfigStep key={currentItem.id} item={currentItem} onChange={(patch) => patchItem(currentItem.id, patch)} project={project} pgSources={pgSources} minioSources={minioSources} defaults={lastLocalDefaults} />
        </>
      )}

      {step === totalSteps + 1 && totalSteps > 0 && !confirming && !done && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("medallion.bulkImport.recapHeaderFile")}</th>
                <th>{t("imports.wizard.format")}</th>
                <th>{t("medallion.bulkImport.recapHeaderTable")}</th>
                <th>{t("imports.modal.writeMode")}</th>
                <th>{t("medallion.bulkImport.recapHeaderSource")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => {
                const isPayload = it.importMode === "payload" && PAYLOAD_FORMATS.has(it.format);
                const targetTable = isPayload ? previewTableName(it.name.trim() || it.filename) : it.targetTable;
                const writeMode = isPayload ? it.payloadWriteMode : it.writeMode;
                const sourceLabel = it.origin === "local" ? (pgSources.find((s) => String(s.id) === String(it.targetSourceId))?.name || "—") : `${it.bucket}/${it.key}`;
                return (
                  <tr key={it.id}>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{it.filename}</td>
                    <td>{it.format.toUpperCase()}{isPayload ? ` · ${t("imports.wizard.modePayload")}` : ""}</td>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{targetTable}</td>
                    <td>{t(WRITE_MODE_LABEL_KEY[writeMode])}</td>
                    <td style={{ fontSize: 12 }}>{sourceLabel}</td>
                    <td style={{ textAlign: "right" }}><button className="link" onClick={() => setStep(idx + 1)}>{t("medallion.bulkImport.edit")}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {step === totalSteps + 1 && (confirming || done) && (
        <div>
          {items.map((it) => (
            <div key={it.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
              <span style={{ flex: 1, fontFamily: "var(--font-m)", fontSize: 12.5 }}>{it.filename}</span>
              {it.status === "pending" && <Badge tone="neutral">{t("medallion.bulkImport.statusPending")}</Badge>}
              {it.status === "running" && <Badge tone="accent">{t("medallion.bulkImport.statusRunning")}</Badge>}
              {it.status === "done" && <Badge tone="accent">{t("medallion.bulkImport.statusDone")}</Badge>}
              {it.status === "error" && <Badge tone="danger">{t("medallion.bulkImport.statusError")}</Badge>}
              {it.status === "error" && it.resultError && <span style={{ fontSize: 11, color: "var(--danger)", maxWidth: 260 }}>{it.resultError}</span>}
            </div>
          ))}
          {done && (
            <div style={{ marginTop: 14, fontSize: 13, fontWeight: 600 }}>
              {t("medallion.bulkImport.doneSummary", {
                success: items.filter((it) => it.status === "done").length,
                failed: items.filter((it) => it.status === "error").length,
              })}
            </div>
          )}
        </div>
      )}

      <div className="modal-actions" style={{ justifyContent: "space-between" }}>
        <Button type="button" variant="ghost" style={{ flex: "0 0 auto" }} onClick={done ? finish : onClose}>
          {done ? t("medallion.bulkImport.finish") : t("imports.wizard.cancel")}
        </Button>
        {!done && (
          <div style={{ display: "flex", gap: 10 }}>
            {step > 0 && !confirming && (
              <Button type="button" variant="ghost" style={{ flex: "0 0 auto" }} onClick={goPrev}>{t("medallion.bulkImport.previous")}</Button>
            )}
            {step <= totalSteps && (
              <Button type="button" style={{ flex: "0 0 auto", width: "auto" }} disabled={step === 0 ? items.length === 0 : !stepValid} onClick={goNext}>
                {t("medallion.bulkImport.next")}
              </Button>
            )}
            {step === totalSteps + 1 && totalSteps > 0 && (
              <Button type="button" style={{ flex: "0 0 auto", width: "auto" }} disabled={confirming} onClick={confirmAll}>
                {confirming ? t("medallion.bulkImport.confirming") : t("medallion.bulkImport.confirmAction")}
              </Button>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
