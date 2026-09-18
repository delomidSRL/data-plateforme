import { useEffect, useRef, useState } from "react";
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
const FLAG_NAME_RE = /^[a-z][a-z0-9_]*$/;

// Module 18 §5.3 — closed catalog of no-code standardization ops (03), text fields only.
const STANDARDIZE_OPS = ["upper", "lower", "title_case", "trim_collapse", "normalize_matching", "clean_vat", "clean_phone", "url_prefix"];
// Module 18 §6.3 — closed catalog of no-code quality-flag rule types (04).
const RULE_TYPES = ["format", "placeholder", "garbage", "date_range"];

// Module 6 extension (payload & structuration) — étapes 2/3/4, §5 rewrite (unpacked/typed
// convention), Module 18 (standardisation + flags qualité no-code). Profile → edit the
// contract (types, names, required/PK, standardisation) → configure quality-flag rules →
// save (renders 01_unpacked..05_validated/05_quarantine at next build) → inspect rows already
// routed to quarantine and repair the contract or a rule from what it shows. No quarantine
// relation before 05: every row reaches 04_annotated, diagnosed, never excluded there.
export default function StructurationPanel({ project, dataset, readOnly = false, focusSection = null }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [state, setState] = useState("loading"); // loading | none | notApplicable | ready
  const [notApplicableReason, setNotApplicableReason] = useState("");
  const [fields, setFields] = useState([]);
  const [qualityFlags, setQualityFlags] = useState([]);
  const [contractHash, setContractHash] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Canvas lineage chain (01..05) — each stage node opens this same popup but jumps to the
  // block that stage actually corresponds to, so the click isn't a no-op: 01/02/03 land on
  // the contract table (they ARE that table, unpacked/typed/standardized are all edited
  // together), 04 lands on the quality-flag rules, and 05 quarantine lands on the rows those
  // rules actually caught. 05 validated has no block of its own — it's just "the contract
  // minus whatever quarantine shows" — so it reuses the contract table too.
  const [highlighted, setHighlighted] = useState(null);
  const fieldsRef = useRef(null);
  const flagsRef = useRef(null);
  const quarantineRef = useRef(null);

  useEffect(() => {
    if (state !== "ready" || !focusSection) return;
    const targetRef = { fields: fieldsRef, flags: flagsRef, quarantine: quarantineRef }[focusSection];
    if (!targetRef?.current) return;
    targetRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    setHighlighted(focusSection);
    const timer = setTimeout(() => setHighlighted(null), 1600);
    return () => clearTimeout(timer);
  }, [state, focusSection, dataset.id]);

  const highlightStyle = (key) => (highlighted === key
    ? { boxShadow: "0 0 0 2px var(--ember)", borderRadius: "var(--radius)", transition: "box-shadow .3s" }
    : { transition: "box-shadow .3s" });

  const load = async () => {
    setState("loading");
    setError("");
    try {
      const c = await structurationApi.getStructuration(project.id, dataset.id);
      setFields(c.column_mapping);
      setQualityFlags(c.quality_flags || []);
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
      setQualityFlags(c.quality_flags || []);
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

  const flagNames = qualityFlags.map((f) => f.name);
  const hasDuplicateFlagName = (name) => flagNames.filter((n) => n === name).length > 1;
  const flagsValid = qualityFlags.every((f) => FLAG_NAME_RE.test(f.name || "") && !hasDuplicateFlagName(f.name) && f.field && (f.rule_type !== "format" || (f.regex || "").trim()));

  const canSave = fields.some((f) => f.include) && fieldsValid && flagsValid;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const c = await structurationApi.saveStructuration(project.id, dataset.id, { column_mapping: fields, quality_flags: qualityFlags });
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

      <div ref={fieldsRef} className="table-wrap" style={{ marginBottom: 16, ...highlightStyle("fields") }}>
        <table className="table">
          <thead>
            <tr>
              <th>{t("imports.modal.colInclude")}</th>
              <th>{t("imports.modal.colSource")}</th>
              <th>{t("imports.modal.colTarget")}</th>
              <th>{t("imports.modal.colType")}</th>
              <th>{t("medallion.structuration.colNullable")}</th>
              <th>{t("medallion.structuration.colStandardize")}</th>
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
                    <select
                      className="input" value={f.target_type} disabled={readOnly || !f.include}
                      onChange={(e) => {
                        const target_type = e.target.value;
                        updateField(idx, target_type === "text" ? { target_type } : { target_type, standardize: null });
                      }}
                    >
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
                  <td style={{ minWidth: 170 }}>
                    <select
                      className="input" value={f.standardize || ""} disabled={readOnly || !f.include || f.target_type !== "text"}
                      title={f.target_type !== "text" ? t("medallion.structuration.standardizeTextOnly") : undefined}
                      onChange={(e) => updateField(idx, { standardize: e.target.value || null })}
                    >
                      <option value="">{t("medallion.structuration.standardizeNone")}</option>
                      {STANDARDIZE_OPS.map((op) => <option key={op} value={op}>{t(`medallion.structuration.standardize_${op}`)}</option>)}
                    </select>
                  </td>
                  <td><Badge tone={(f.confidence ?? 1) >= 0.95 ? "accent" : "danger"}>{Math.round((f.confidence ?? 1) * 100)}%</Badge></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div ref={flagsRef} style={{ ...highlightStyle("flags") }}>
        <QualityFlagsEditor flags={qualityFlags} setFlags={setQualityFlags} fields={fields} readOnly={readOnly} t={t} />
      </div>

      {!readOnly && (
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={busy} onClick={profile}>{Icon.refresh()} {t("medallion.structuration.reprofile")}</button>
          <Button disabled={!canSave || busy} onClick={save}>{busy ? t("medallion.structuration.saving") : t("medallion.structuration.save")}</Button>
        </div>
      )}

      {contractHash && (
        <div ref={quarantineRef} style={{ ...highlightStyle("quarantine") }}>
          <QuarantineSection project={project} dataset={dataset} t={t} onFieldFix={(sourceName, patch) => {
            const idx = fields.findIndex((f) => f.source_name === sourceName);
            if (idx >= 0) updateField(idx, patch);
          }} />
        </div>
      )}
    </div>
  );
}

// Module 18 §6 — 04's flags, configured without SQL: a name, the field it reads, a rule type
// (each backed by a generic macro server-side), rule-specific params, and the category that's
// the ONLY thing 05's routing reads. Referencing an excluded field is caught server-side
// (validate_quality_flags) — the field picker here only ever offers currently-included fields.
function QualityFlagsEditor({ flags, setFlags, fields, readOnly, t }) {
  const includedFields = fields.filter((f) => f.include);

  const addFlag = () => setFlags((fs) => [...fs, {
    name: "", field: includedFields[0]?.target_name || "", rule_type: "format", category: "elimination", regex: "",
  }]);
  const updateFlag = (idx, patch) => setFlags((fs) => fs.map((f, i) => (i === idx ? { ...f, ...patch } : f)));
  const removeFlag = (idx) => setFlags((fs) => fs.filter((_, i) => i !== idx));

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{t("medallion.structuration.qualityFlagsTitle")}</div>
        {!readOnly && <button type="button" className="btn-ghost" style={{ padding: "4px 8px", fontSize: 11.5 }} onClick={addFlag}>+ {t("medallion.structuration.addFlag")}</button>}
      </div>
      {flags.length === 0 && <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}>{t("medallion.structuration.noFlags")}</div>}
      {flags.map((rule, idx) => {
        const invalidName = !FLAG_NAME_RE.test(rule.name || "") || flags.filter((f) => f.name === rule.name).length > 1;
        return (
          <div key={idx} className="card" style={{ padding: 10, marginBottom: 8, display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
            <div style={{ minWidth: 160 }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagName")}</div>
              <Input
                value={rule.name} disabled={readOnly} placeholder="dq_invalid_email"
                style={{ fontFamily: "var(--font-m)", fontSize: 12, borderColor: invalidName ? "var(--danger)" : undefined }}
                onChange={(e) => updateFlag(idx, { name: e.target.value })}
              />
            </div>
            <div style={{ minWidth: 150 }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagField")}</div>
              <select className="input" value={rule.field} disabled={readOnly} onChange={(e) => updateFlag(idx, { field: e.target.value })}>
                {includedFields.map((f) => <option key={f.target_name} value={f.target_name}>{f.target_name}</option>)}
              </select>
            </div>
            <div style={{ minWidth: 150 }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagRuleType")}</div>
              <select className="input" value={rule.rule_type} disabled={readOnly} onChange={(e) => updateFlag(idx, { rule_type: e.target.value })}>
                {RULE_TYPES.map((rt) => <option key={rt} value={rt}>{t(`medallion.structuration.rule_${rt}`)}</option>)}
              </select>
            </div>
            {rule.rule_type === "format" && (
              <div style={{ minWidth: 200 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagRegex")}</div>
                <Input
                  value={rule.regex || ""} disabled={readOnly} placeholder="^[^@]+@[^@]+\.[^@]+$"
                  style={{ fontFamily: "var(--font-m)", fontSize: 12 }}
                  onChange={(e) => updateFlag(idx, { regex: e.target.value })}
                />
              </div>
            )}
            {rule.rule_type === "date_range" && (
              <>
                <div style={{ minWidth: 130 }}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagMinDate")}</div>
                  <Input type="date" value={rule.min_date || ""} disabled={readOnly} onChange={(e) => updateFlag(idx, { min_date: e.target.value || null })} />
                </div>
                <div style={{ minWidth: 130 }}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagMaxDate")}</div>
                  <Input type="date" value={rule.max_date || ""} disabled={readOnly} onChange={(e) => updateFlag(idx, { max_date: e.target.value || null })} />
                </div>
              </>
            )}
            <div style={{ minWidth: 150 }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{t("medallion.structuration.flagCategory")}</div>
              <select className="input" value={rule.category} disabled={readOnly} onChange={(e) => updateFlag(idx, { category: e.target.value })}>
                <option value="elimination">{t("medallion.structuration.categoryElimination")}</option>
                <option value="informative">{t("medallion.structuration.categoryInformative")}</option>
              </select>
            </div>
            {!readOnly && (
              <button type="button" className="btn-ghost" style={{ padding: "6px 8px", fontSize: 13, color: "var(--danger)" }} onClick={() => removeFlag(idx)} title={t("medallion.structuration.removeFlag")}>
                ✕
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Étape 4 — per-column summary (what to fix first) + the rows carrying an issue, with a
// one-click repair shortcut that jumps straight to editing the offending field above.
function QuarantineSection({ project, dataset, t, onFieldFix }) {
  const showToast = useToast();
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState(null);
  const [activeColumn, setActiveColumn] = useState(null);
  const [loading, setLoading] = useState(true);

  // onFieldFix only patches the (possibly off-screen) field row's state above — nothing near
  // this button otherwise confirms the click did anything, since the anomaly panel itself
  // only reflects the last saved+rebuilt contract, not this pending edit.
  const acceptAsText = (column) => {
    onFieldFix(column, { target_type: "text" });
    showToast(t("medallion.structuration.acceptAsTextDone", { column }));
  };

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
                <button className="btn-ghost" style={{ padding: "4px 8px", fontSize: 11.5 }} onClick={() => acceptAsText(c.column)}>{t("medallion.structuration.acceptAsText")}</button>
              </div>
            </div>
            {activeColumn === c.column && (
              <div className="table-wrap" style={{ marginTop: 10 }}>
                <table className="table">
                  <thead><tr><th>{t("medallion.structuration.colRowNumber")}</th><th>{t("medallion.structuration.colSourceFile")}</th><th>{t("medallion.structuration.colMotif")}</th></tr></thead>
                  <tbody>
                    {rows === null && <tr><td colSpan={3} style={{ color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
                    {rows?.map((r, i) => {
                      const issue = r.issues?.find((iss) => iss.startsWith(`${c.column}:`)) || r.issues?.find((iss) => iss === c.column);
                      const motif = issue && issue.includes(":") ? issue.split(":", 2)[1] : (issue ? c.column : "—");
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
