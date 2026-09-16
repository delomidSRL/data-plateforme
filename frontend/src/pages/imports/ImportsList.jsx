import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import { useAuth } from "../../context/AuthContext.jsx";
import * as importsApi from "../../api/imports.js";
import ImportWizardDrawer from "./ImportWizardDrawer.jsx";
import SchemaValidationModal from "./SchemaValidationModal.jsx";
import WatchCreateDrawer from "./WatchCreateDrawer.jsx";
import WatchesPanel from "./WatchesPanel.jsx";

const WATCHABLE_WRITE_MODES = new Set(["append", "replace"]);

const STATUS_TONE = { draft: "neutral", awaiting_validation: "accent", importing: "accent", imported: "accent", error: "danger" };
const ACTIVE_POLL_STATUSES = new Set(["importing"]);

export default function ImportsList() {
  const { t, i18n } = useTranslation();
  const showToast = useToast();
  const { isAdmin } = useAuth();
  const [imports, setImports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [validating, setValidating] = useState(null);
  const [watchTarget, setWatchTarget] = useState(null);
  const [view, setView] = useState("imports"); // imports | watches
  const reimportInputRef = useRef(null);
  const reimportTargetId = useRef(null);

  const STATUS_LABEL = t("imports.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      setImports(await importsApi.listImports());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    const active = imports.filter((i) => ACTIVE_POLL_STATUSES.has(i.status));
    if (active.length === 0) return;
    const timer = setInterval(async () => {
      for (const imp of active) {
        try {
          const s = await importsApi.getImportStatus(imp.id);
          setImports((list) => list.map((x) => (x.id === imp.id ? { ...x, status: s.status, row_count: s.row_count, cast_errors: s.cast_errors, last_error: s.last_error } : x)));
        } catch {
          // best-effort polling
        }
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [imports]);

  const handleDelete = async (imp) => {
    const dropTable = isAdmin && imp.target_table
      ? window.confirm(t("imports.dropTableConfirm", { schema: imp.target_schema, table: imp.target_table }))
      : false;
    try {
      await importsApi.deleteImport(imp.id, dropTable);
      setImports((list) => list.filter((x) => x.id !== imp.id));
      showToast(t("imports.deletedToast"));
    } catch (err) {
      showToast(err.message || t("imports.deleteFailed"));
    }
  };

  const openReimport = (id) => {
    reimportTargetId.current = id;
    reimportInputRef.current?.click();
  };

  const handleReimportFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !reimportTargetId.current) return;
    const id = reimportTargetId.current;
    try {
      const updated = await importsApi.reimport(id, file);
      setImports((list) => list.map((x) => (x.id === id ? updated : x)));
      showToast(updated.status === "awaiting_validation" ? t("imports.reimportReopenModal") : t("imports.reimportSuccess"));
      if (updated.status === "awaiting_validation") setValidating(updated);
    } catch (err) {
      showToast(err.message || t("imports.reimportFailed"));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("imports.badge")}</div>
          <h1 className="page-title">{t("imports.title")}</h1>
          <p className="page-desc">{t("imports.subtitle")}</p>
        </div>
        {view === "imports" && <button className="add-btn" onClick={() => setWizardOpen(true)}>{Icon.plus()}{t("imports.addImport")}</button>}
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        <button className="btn-ghost" style={{ opacity: view === "imports" ? 1 : 0.6 }} onClick={() => setView("imports")}>{t("imports.title")}</button>
        <button className="btn-ghost" style={{ opacity: view === "watches" ? 1 : 0.6 }} onClick={() => setView("watches")}>{t("watches.tabLabel")}</button>
      </div>

      <input ref={reimportInputRef} type="file" accept=".csv,.xlsx,.xls" style={{ display: "none" }} onChange={handleReimportFile} />

      {view === "watches" && <WatchesPanel />}

      {view === "imports" && (
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("imports.colName")}</th><th>{t("imports.colFile")}</th><th>{t("imports.colFormat")}</th>
              <th>{t("imports.colTarget")}</th><th>{t("imports.colRows")}</th><th>{t("common.status")}</th>
              <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && imports.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("imports.noImports")}</td></tr>
            )}
            {imports.map((imp) => (
              <tr key={imp.id}>
                <td>
                  <div className="uc-name">{imp.name}</div>
                  <div className="uc-mail">{imp.created_at ? new Date(imp.created_at).toLocaleString(i18n.language) : "—"}</div>
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{imp.source_file_name}</td>
                <td>{imp.format?.toUpperCase()}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>
                  {imp.target_table ? `${imp.target_schema}.${imp.target_table}` : "—"}
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{imp.row_count ?? "—"}</td>
                <td>
                  <Badge tone={STATUS_TONE[imp.status] || "neutral"}>{STATUS_LABEL[imp.status] || imp.status}</Badge>
                  {imp.status === "error" && imp.last_error && (
                    <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, maxWidth: 220 }}>{imp.last_error}</div>
                  )}
                  {imp.cast_errors && Object.keys(imp.cast_errors).length > 0 && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      {Object.entries(imp.cast_errors).map(([col, count]) => (
                        <div key={col}>{t("imports.modal.castErrorsWarning", { count, column: col })}</div>
                      ))}
                    </div>
                  )}
                </td>
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  {imp.status === "awaiting_validation" && (
                    <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => setValidating(imp)}>{t("imports.open")}</button>
                  )}
                  {imp.status === "imported" && (
                    <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => openReimport(imp.id)}>{t("imports.reimport")}</button>
                  )}
                  {imp.status === "imported" && WATCHABLE_WRITE_MODES.has(imp.write_mode) && (
                    <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => setWatchTarget(imp)}>{t("watches.watchAction")}</button>
                  )}
                  <button className="btn-icon" onClick={() => handleDelete(imp)} title={t("common.delete")}>{Icon.trash()}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}

      {watchTarget && (
        <WatchCreateDrawer
          fileImport={watchTarget}
          onClose={() => setWatchTarget(null)}
          onCreated={() => { setWatchTarget(null); showToast(t("watches.createdToast")); setView("watches"); }}
        />
      )}

      {wizardOpen && (
        <ImportWizardDrawer
          onClose={() => setWizardOpen(false)}
          onAnalyzed={(fi) => {
            setWizardOpen(false);
            setImports((list) => [fi, ...list]);
            // Payload mode has no mapping step (§3.5) — it's already `imported`, straight in.
            if (fi.status === "awaiting_validation") setValidating(fi);
            else showToast(fi.status === "imported" ? t("imports.wizard.payloadImportedToast") : t("imports.wizard.analyzeFailed"));
          }}
        />
      )}

      {validating && (
        <SchemaValidationModal
          fileImport={validating}
          onClose={() => setValidating(null)}
          onValidated={(fi) => {
            setImports((list) => list.map((x) => (x.id === fi.id ? fi : x)));
            setValidating(null);
            showToast(t("imports.modal.running"));
          }}
        />
      )}
    </>
  );
}
