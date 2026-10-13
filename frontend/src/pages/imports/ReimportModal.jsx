import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as importsApi from "../../api/imports.js";
import * as sourcesApi from "../../api/sources.js";

const STATUS_TONE = { imported: "accent", drift_rejected: "neutral", error: "danger" };

// Réimport par nœud bronze — remplace le hidden-input "upload local uniquement" d'origine
// (ImportsList.jsx / OriginPanel.jsx) : choix local ou S3/MinIO à chaque réimport, parcours
// ou saisie manuelle (préfixe pré-rempli depuis le DERNIER emplacement connu, voir
// fi.source_location — jamais dérivable après coup depuis archive_path, qui pointe la copie
// d'archive interne, pas la source externe réellement parcourue), et un onglet Historique.
export default function ReimportModal({ importId, onClose, onReimported }) {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState("reimport");
  const [fileImport, setFileImport] = useState(null);
  const [minioSources, setMinioSources] = useState([]);
  const [sourceType, setSourceType] = useState("local");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  const [s3SourceId, setS3SourceId] = useState("");
  const [s3Mode, setS3Mode] = useState("browse"); // "browse" | "manual"
  const [browseBuckets, setBrowseBuckets] = useState([]);
  const [browseBucket, setBrowseBucket] = useState("");
  const [browseObjects, setBrowseObjects] = useState([]);
  const [browseFilter, setBrowseFilter] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [selectedKey, setSelectedKey] = useState("");
  const [manualBucket, setManualBucket] = useState("");
  const [manualPrefix, setManualPrefix] = useState("");
  const [manualFileName, setManualFileName] = useState("");

  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    importsApi.getImport(importId).then((fi) => {
      setFileImport(fi);
      const loc = fi.source_location;
      if (loc?.transport === "minio") {
        setSourceType("s3");
        setS3SourceId(String(loc.source_id || ""));
        setManualBucket(loc.bucket || "");
        const key = loc.key || "";
        const slash = key.lastIndexOf("/");
        setManualPrefix(slash >= 0 ? key.slice(0, slash + 1) : "");
      }
    }).catch((err) => setError(err.message || t("imports.reimportModal.loadFailed")));
    sourcesApi.listSources().then((list) => setMinioSources(list.filter((s) => s.type === "minio"))).catch(() => {});
  }, [importId, t]);

  useEffect(() => {
    if (tab !== "history" || history !== null) return;
    importsApi.getReimportHistory(importId)
      .then(setHistory)
      .catch((err) => setHistoryError(err.message || t("imports.reimportModal.historyFailed")));
  }, [tab, history, importId, t]);

  useEffect(() => {
    setBrowseBuckets([]); setBrowseBucket(""); setBrowseObjects([]); setSelectedKey(""); setBrowseFilter("");
    if (!s3SourceId || s3Mode !== "browse") return;
    let cancelled = false;
    setBrowsing(true);
    sourcesApi.introspectSource(s3SourceId)
      .then((res) => {
        if (cancelled) return;
        if (res.objects && res.objects.length) { setBrowseObjects(res.objects); setBrowseBucket((res.buckets && res.buckets[0]) || ""); setBrowsing(false); return; }
        if (res.buckets && res.buckets.length === 1) {
          return sourcesApi.introspectSource(s3SourceId, res.buckets[0]).then((res2) => {
            if (cancelled) return;
            setBrowseObjects(res2.objects || []); setBrowseBucket(res.buckets[0]); setBrowsing(false);
          });
        }
        setBrowseBuckets(res.buckets || []); setBrowsing(false);
      })
      .catch(() => { if (!cancelled) { setBrowseObjects([]); setBrowseBuckets([]); setBrowsing(false); } });
    return () => { cancelled = true; };
  }, [s3SourceId, s3Mode]);

  const loadBucket = (bucket) => {
    setBrowsing(true);
    setSelectedKey("");
    sourcesApi.introspectSource(s3SourceId, bucket)
      .then((res) => { setBrowseObjects(res.objects || []); setBrowseBucket(bucket); })
      .catch(() => setBrowseObjects([]))
      .finally(() => setBrowsing(false));
  };

  const visibleObjects = browseFilter ? browseObjects.filter((o) => o.toLowerCase().includes(browseFilter.toLowerCase())) : browseObjects;

  const canSubmit = sourceType === "local"
    ? !!file
    : s3Mode === "browse" ? !!(s3SourceId && browseBucket && selectedKey) : !!(s3SourceId && manualBucket.trim() && manualFileName.trim());

  const submit = async () => {
    if (!canSubmit || busy) return;
    setBusy(true);
    setError("");
    try {
      let updated;
      if (sourceType === "local") {
        updated = await importsApi.reimport(importId, file);
      } else {
        const key = s3Mode === "browse" ? selectedKey : `${manualPrefix}${manualFileName}`.replace(/^\/+/, "");
        const bucket = s3Mode === "browse" ? browseBucket : manualBucket.trim();
        updated = await importsApi.reimportFromObjectStore(importId, { source_id: Number(s3SourceId), bucket, key });
      }
      onReimported(updated);
    } catch (err) {
      setError(err.message || t("imports.reimportFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal large title={t("imports.reimportModal.title")} description={fileImport ? `${t("imports.reimportModal.currentTable")} ${fileImport.target_schema}.${fileImport.target_table}` : undefined} onClose={onClose}>
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        <button type="button" className="btn-ghost" style={{ opacity: tab === "reimport" ? 1 : 0.6 }} onClick={() => setTab("reimport")}>
          {Icon.upload()} {t("imports.reimportModal.tabReimport")}
        </button>
        <button type="button" className="btn-ghost" style={{ opacity: tab === "history" ? 1 : 0.6 }} onClick={() => setTab("history")}>
          {Icon.refresh()} {t("imports.reimportModal.tabHistory")}
        </button>
      </div>

      {tab === "reimport" ? (
        <>
          {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

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
                onClick={() => document.getElementById("reimport-file-input").click()}
                style={{
                  border: `2px dashed ${dragOver ? "var(--ember)" : "var(--border)"}`, borderRadius: 12, padding: "24px 16px",
                  textAlign: "center", cursor: "pointer", background: dragOver ? "var(--ember-soft)" : "var(--bg)",
                }}
              >
                <input id="reimport-file-input" type="file" accept=".csv,.xlsx,.xls,.json,.ndjson,.xml" style={{ display: "none" }}
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
                                <input type="radio" name="reimport-object" checked={selectedKey === obj} onChange={() => setSelectedKey(obj)} />
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

          <div className="modal-actions">
            <Button type="button" variant="ghost" onClick={onClose}>{t("imports.wizard.cancel")}</Button>
            <Button type="button" disabled={!canSubmit || busy} onClick={submit}>
              {busy ? t("imports.reimporting") : t("imports.reimport")}
            </Button>
          </div>
        </>
      ) : historyError ? (
        <div className="error-banner">{Icon.warn()}<span>{historyError}</span></div>
      ) : history === null ? (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>{t("common.loading")}</div>
      ) : history.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: "28px 0" }}>{t("imports.reimportModal.historyEmpty")}</div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("imports.reimportModal.historyDate")}</th>
                <th>{t("common.status")}</th>
                <th>{t("imports.reimportModal.historySource")}</th>
                <th>{t("imports.reimportModal.historyRows")}</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{new Date(h.occurred_at).toLocaleString(i18n.language)}</td>
                  <td>
                    <Badge tone={STATUS_TONE[h.outcome] || "neutral"}>{t(`imports.reimportModal.outcome.${h.outcome}`)}</Badge>
                    {h.error && <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, maxWidth: 280 }}>{h.error}</div>}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    <div>{t(`imports.reimportModal.trigger.${h.trigger_source}`)}</div>
                    <div style={{ fontFamily: "var(--font-m)", color: "var(--text-muted)", marginTop: 2 }}>
                      {h.source_location?.transport === "minio" ? `${h.source_location.bucket}/${h.source_location.key}` : h.file_name}
                    </div>
                  </td>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{h.row_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
