import { useTranslation } from "react-i18next";
import { Field, Input } from "../../components/ui/Input.jsx";

// Presentational half of useReimportSourcePicker — shared by ReimportModal and
// RunPreflightModal's per-step editor. `fileInputId` must be unique per instance on the page
// (RunPreflightModal renders one per expanded bronze step).
export default function ReimportSourceFields({ picker, fileInputId }) {
  const { t } = useTranslation();
  const {
    minioSources, sourceType, setSourceType, file, setFile, dragOver, setDragOver,
    s3SourceId, setS3SourceId, s3Mode, setS3Mode,
    browseBuckets, browseBucket, setBrowseBucket, visibleObjects, setBrowseObjects,
    browseFilter, setBrowseFilter, browsing, loadBucket,
    selectedKey, setSelectedKey,
    manualBucket, setManualBucket, manualPrefix, setManualPrefix, manualFileName, setManualFileName,
  } = picker;

  return (
    <>
      <Field label={t("imports.reimportModal.sourceType")}>
        <div className="seg">
          <button type="button" className={"seg-opt" + (sourceType === "local" ? " selected" : "")} onClick={() => setSourceType("local")}>
            <div className="seg-role">{t("imports.reimportModal.sourceLocal")}</div>
          </button>
          <button type="button" className={"seg-opt" + (sourceType === "s3" ? " selected" : "")} onClick={() => setSourceType("s3")} disabled={minioSources.length === 0}>
            <div className="seg-role">{t("imports.reimportModal.sourceS3")}</div>
          </button>
        </div>
        {minioSources.length === 0 && <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("imports.reimportModal.noMinioSource")}</div>}
      </Field>

      {sourceType === "local" ? (
        <Field>
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); setFile(e.dataTransfer.files?.[0] || null); }}
            onClick={() => document.getElementById(fileInputId).click()}
            style={{
              border: `2px dashed ${dragOver ? "var(--ember)" : "var(--border)"}`, borderRadius: 12, padding: "24px 16px",
              textAlign: "center", cursor: "pointer", background: dragOver ? "var(--ember-soft)" : "var(--bg)",
            }}
          >
            <input id={fileInputId} type="file" accept=".csv,.xlsx,.xls,.json,.ndjson,.xml" style={{ display: "none" }}
              onChange={(e) => setFile(e.target.files?.[0] || null)} />
            {file ? (
              <div style={{ fontFamily: "var(--font-m)", fontSize: 13 }}>{t("imports.wizard.chosenFile")} {file.name}</div>
            ) : (
              <div style={{ color: "var(--text-muted)", fontSize: 13.5 }}>{t("imports.wizard.dropFile")}</div>
            )}
          </div>
        </Field>
      ) : (
        <>
          <Field label={t("imports.reimportModal.minioSource")}>
            <select className="input" value={s3SourceId} onChange={(e) => setS3SourceId(e.target.value)}>
              <option value="">{t("imports.wizard.chooseSource")}</option>
              {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>

          {s3SourceId && (
            <>
              <div className="seg" style={{ marginBottom: 12 }}>
                <button type="button" className={"seg-opt" + (s3Mode === "browse" ? " selected" : "")} onClick={() => setS3Mode("browse")}>
                  <div className="seg-role">{t("imports.reimportModal.modeBrowse")}</div>
                </button>
                <button type="button" className={"seg-opt" + (s3Mode === "manual" ? " selected" : "")} onClick={() => setS3Mode("manual")}>
                  <div className="seg-role">{t("imports.reimportModal.modeManual")}</div>
                </button>
              </div>

              {s3Mode === "browse" ? (
                <>
                  {browsing && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("medallion.bulkImport.browsing")}</div>}
                  {!browsing && browseBuckets.length > 0 && !browseBucket && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                      {browseBuckets.map((b) => (
                        <button key={b} type="button" className="seg-opt" style={{ flex: "0 0 auto" }} onClick={() => loadBucket(b)}>{b}</button>
                      ))}
                    </div>
                  )}
                  {!browsing && browseBucket && (
                    <>
                      {browseBuckets.length > 1 && (
                        <button type="button" className="link" style={{ marginBottom: 8 }} onClick={() => { setBrowseBucket(""); setBrowseObjects([]); }}>
                          {t("medallion.bulkImport.backToBuckets")}
                        </button>
                      )}
                      <Input placeholder={t("medallion.bulkImport.filterObjects")} value={browseFilter} onChange={(e) => setBrowseFilter(e.target.value)} style={{ marginBottom: 10 }} />
                      <div style={{ maxHeight: 220, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 10, marginBottom: 12 }}>
                        {visibleObjects.length === 0 && <div style={{ padding: 14, color: "var(--text-muted)", fontSize: 13 }}>{t("medallion.bulkImport.noObjectsFound")}</div>}
                        {visibleObjects.map((obj) => (
                          <label key={obj} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderBottom: "1px solid var(--border)", fontSize: 12.5, fontFamily: "var(--font-m)", cursor: "pointer", background: selectedKey === obj ? "var(--ember-soft)" : "transparent" }}>
                            <input type="radio" name={`${fileInputId}-object`} checked={selectedKey === obj} onChange={() => setSelectedKey(obj)} />
                            {obj}
                          </label>
                        ))}
                      </div>
                    </>
                  )}
                </>
              ) : (
                <>
                  <Field label={t("imports.reimportModal.bucket")}>
                    <Input value={manualBucket} onChange={(e) => setManualBucket(e.target.value)} placeholder="dataplateforme-bronze" />
                  </Field>
                  <Field label={t("imports.reimportModal.prefix")}>
                    <Input value={manualPrefix} onChange={(e) => setManualPrefix(e.target.value)} placeholder="exports/ventes/" />
                    <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("imports.reimportModal.prefixHelp")}</div>
                  </Field>
                  <Field label={t("imports.reimportModal.fileName")}>
                    <Input value={manualFileName} onChange={(e) => setManualFileName(e.target.value)} placeholder="ventes_2026_02.csv" />
                  </Field>
                </>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
