import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as structurationApi from "../../api/structuration.js";
import { ApiError } from "../../api/client.js";
import { Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";

const TYPES = ["text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"];
const IDENTIFIER_RE = /^[a-z_][a-z0-9_]{0,62}$/;

// Module 6 extension (payload & structuration) — étapes 2/3/4, §5 rewrite (unpacked/typed
// convention). Profile → edit the contract (types, names, required/PK) → save (renders
// 01_unpacked/02_typed at next build) → inspect rows with a cast issue and repair the
// contract from what it shows. No quarantine relation: every row from bronze reaches
// 02_typed, diagnosed via cast_issues, never excluded.
export default function StructurationPanel({ project, dataset, readOnly = false }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [state, setState] = useState("loading"); // loading | none | notApplicable | ready
  const [notApplicableReason, setNotApplicableReason] = useState("");
  const [fields, setFields] = useState([]);
  const [contractHash, setContractHash] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setState("loading");
    setError("");
    try {
      const c = await structurationApi.getStructuration(project.id, dataset.id);
      setFields(c.column_mapping);
      setContractHash(c.contract_hash);
      setState("ready");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setState("none");
      } else {
        setNotApplicableReason(err.message || "");
        setState("notApplicable");
      }
    }
  };

  useEffect(() => { load(); }, [dataset.id]);

  const profile = async () => {
    setBusy(true);
    setError("");
    try {
      const c = await structurationApi.profileStructuration(project.id, dataset.id);
      setFields(c.column_mapping);
      setContractHash(c.contract_hash);
      setState("ready");
    } catch (err) {
      setNotApplicableReason(err.message || t("medallion.structuration.profileFailed"));
      setState("notApplicable");
    } finally {
      setBusy(false);
    }
  };

  const updateField = (idx, patch) => setFields((fs) => fs.map((f, i) => (i === idx ? { ...f, ...patch } : f)));

  const includedTargetNames = fields.filter((f) => f.include).map((f) => f.target_name);
  const hasDuplicate = (name) => includedTargetNames.filter((n) => n === name).length > 1;
  const fieldsValid = fields.every((f) => !f.include || (IDENTIFIER_RE.test(f.target_name) && !hasDuplicate(f.target_name)));
  const canSave = fields.some((f) => f.include) && fieldsValid;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const c = await structurationApi.saveStructuration(project.id, dataset.id, { column_mapping: fields });
      setContractHash(c.contract_hash);
      showToast(t("medallion.structuration.saved"));
    } catch (err) {
      setError(err.message || t("medallion.structuration.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (state === "loading") return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  if (state === "notApplicable") {
    return (
      <div className="card" style={{ padding: 16, textAlign: "center" }}>
        <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{notApplicableReason || t("medallion.structuration.notApplicable")}</div>
      </div>
    );
  }

  if (state === "none") {
    return (
      <div className="card" style={{ padding: 16, textAlign: "center" }}>
        <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>{t("medallion.structuration.noneYet")}</div>
        {!readOnly && (
          <Button disabled={busy} onClick={profile}>{busy ? t("medallion.structuration.profiling") : t("medallion.structuration.profileAction")}</Button>
        )}
      </div>
    );
  }

  return (
    <div>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      {fields.some((f) => f.ambiguous && f.include) && (
        <div className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>
          {Icon.warn()}<span>{t("medallion.structuration.ambiguousWarning")}</span>
        </div>
      )}

      <div className="table-wrap" style={{ marginBottom: 16 }}>
        <table className="table">
          <thead>
            <tr>
              <th>{t("imports.modal.colInclude")}</th>
              <th>{t("imports.modal.colSource")}</th>
              <th>{t("imports.modal.colTarget")}</th>
              <th>{t("imports.modal.colType")}</th>
              <th>{t("medallion.structuration.colNullable")}</th>
              <th>{t("imports.modal.colConfidence")}</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f, idx) => {
              const invalidName = f.include && (!IDENTIFIER_RE.test(f.target_name) || hasDuplicate(f.target_name));
              const showDateFormat = f.include && (f.target_type === "date" || f.target_type === "timestamp");
              return (
                <tr key={f.source_name} style={{ opacity: f.include ? 1 : 0.5 }}>
                  <td><input type="checkbox" checked={f.include} disabled={readOnly} onChange={(e) => updateField(idx, { include: e.target.checked })} /></td>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>
                    {f.source_name}
                    {f.is_primary_key && (
                      <span style={{ marginLeft: 6 }} title={t("medallion.structuration.primaryKeyHelp")}>
                        <Badge tone="accent">{t("medallion.structuration.primaryKeyBadge")}</Badge>
                      </span>
                    )}
                  </td>
                  <td style={{ minWidth: 140 }}>
                    <Input
                      style={{ fontFamily: "var(--font-m)", fontSize: 12, borderColor: invalidName ? "var(--danger)" : undefined }}
                      value={f.target_name} disabled={readOnly || !f.include}
                      onChange={(e) => updateField(idx, { target_name: e.target.value })}
                    />
                    {showDateFormat && (
                      <select className="input" style={{ marginTop: 6, fontSize: 11.5 }} disabled={readOnly}
                        value={f.format || "%d/%m/%Y"} onChange={(e) => updateField(idx, { format: e.target.value })}>
                        <option value="%Y-%m-%d">{t("imports.modal.dateFormatISO")}</option>
                        <option value="%d/%m/%Y">{t("imports.modal.dateFormatDDMM")}</option>
                        <option value="%m/%d/%Y">{t("imports.modal.dateFormatMMDD")}</option>
                      </select>
                    )}
                  </td>
                  <td style={{ minWidth: 110 }}>
                    <select className="input" value={f.target_type} disabled={readOnly || !f.include} onChange={(e) => updateField(idx, { target_type: e.target.value })}>
                      {TYPES.map((ty) => <option key={ty} value={ty}>{ty}</option>)}
                    </select>
                  </td>
                  <td style={{ textAlign: "center" }}>
                    <input
                      type="checkbox" checked={f.nullable ?? true} disabled={readOnly || !f.include}
                      title={t("medallion.structuration.nullableHelp")}
                      onChange={(e) => updateField(idx, { nullable: e.target.checked })}
                    />
                  </td>
                  <td><Badge tone={(f.confidence ?? 1) >= 0.95 ? "accent" : "danger"}>{Math.round((f.confidence ?? 1) * 100)}%</Badge></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {!readOnly && (
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={busy} onClick={profile}>{Icon.refresh()} {t("medallion.structuration.reprofile")}</button>
          <Button disabled={!canSave || busy} onClick={save}>{busy ? t("medallion.structuration.saving") : t("medallion.structuration.save")}</Button>
        </div>
      )}

      {contractHash && <QuarantineSection project={project} dataset={dataset} t={t} onFieldFix={(sourceName, patch) => {
        const idx = fields.findIndex((f) => f.source_name === sourceName);
        if (idx >= 0) updateField(idx, patch);
      }} />}
    </div>
  );
}

// Étape 4 — per-column summary (what to fix first) + the rows carrying an issue, with a
// one-click repair shortcut that jumps straight to editing the offending field above.
function QuarantineSection({ project, dataset, t, onFieldFix }) {
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState(null);
  const [activeColumn, setActiveColumn] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadSummary = async () => {
    setLoading(true);
    try {
      setSummary(await structurationApi.getQuarantineSummary(project.id, dataset.id));
    } catch {
      setSummary({ total_quarantined: 0, by_column: [] });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadSummary(); }, [project.id, dataset.id]);

  const openColumn = async (col) => {
    setActiveColumn(col);
    setRows(null);
    try {
      setRows(await structurationApi.listQuarantine(project.id, dataset.id, { column: col, limit: 20 }));
    } catch {
      setRows([]);
    }
  };

  if (loading) return null;
  if (!summary || summary.total_quarantined === 0) {
    return (
      <div style={{ marginTop: 8, fontSize: 12.5, color: "var(--text-muted)" }}>
        <Badge tone="accent">{t("medallion.structuration.quarantineNone")}</Badge>
      </div>
    );
  }

  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <Badge tone="danger">{t("medallion.structuration.quarantineBadge", { count: summary.total_quarantined })}</Badge>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {summary.by_column.map((c) => (
          <div key={c.column} className="card" style={{ padding: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <div style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>
                <strong>{c.column}</strong> — {t("medallion.structuration.rejectedCount", { count: c.count })}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn-ghost" style={{ padding: "4px 8px", fontSize: 11.5 }} onClick={() => openColumn(c.column)}>{t("medallion.structuration.viewRows")}</button>
                <button className="btn-ghost" style={{ padding: "4px 8px", fontSize: 11.5 }} onClick={() => onFieldFix(c.column, { target_type: "text" })}>{t("medallion.structuration.acceptAsText")}</button>
              </div>
            </div>
            {activeColumn === c.column && (
              <div className="table-wrap" style={{ marginTop: 10 }}>
                <table className="table">
                  <thead><tr><th>{t("medallion.structuration.colRowNumber")}</th><th>{t("medallion.structuration.colSourceFile")}</th><th>{t("medallion.structuration.colMotif")}</th></tr></thead>
                  <tbody>
                    {rows === null && <tr><td colSpan={3} style={{ color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
                    {rows?.map((r, i) => {
                      const issue = r.issues?.find((iss) => iss.startsWith(`${c.column}:`));
                      const motif = issue ? issue.split(":", 2)[1] : "—";
                      return (
                        <tr key={i}>
                          <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{r.row_number ?? "—"}</td>
                          <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{r.source_file ?? "—"}</td>
                          <td style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{motif}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
