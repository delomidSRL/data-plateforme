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

// UX ask — the format field is free text (any strptime-style combination of %Y/%m/%d/%H/%M/%S/%f
// is accepted end to end, e.g. "%Y-%m-%d %H:%M:%S.%f" for microsecond timestamps): these three
// are just quick-fill suggestions for the common cases, not a closed list — a hand-typed format
// this platform has never seen before works exactly the same way.
const DATE_FORMAT_SUGGESTIONS = [
  { format: "%Y-%m-%d", labelKey: "imports.modal.dateFormatISO" },
  { format: "%d/%m/%Y", labelKey: "imports.modal.dateFormatDDMM" },
  { format: "%m/%d/%Y", labelKey: "imports.modal.dateFormatMMDD" },
];

// Module 18 §7 — how far into the 01/02 chain a stage sits, used to progressively reveal
// contract columns instead of dumping everything at once: 01 only knows raw fields exist, 02
// decides their type/name/nullability. Everything past 02 (standardization, quality flags,
// quarantine routing) is authored as real, hand-written dbt SQL via the "+" on the previous
// stage's canvas node — there's no no-code stage left to reveal here.
const STAGE_LEVEL = { unpacked: 1, typed: 2 };
const MAX_LEVEL = 2;
// Which popup block a stage's click should scroll to / highlight.
const STAGE_BLOCK = { unpacked: "fields", typed: "fields" };
// Module 18 §7 UX — the "+" on a payload-backed bronze and "pick this bronze as a new
// silver's upstream" both land a first-timer on a blank contract with nothing decided yet;
// dumping the full editor on them (the canvas-jump behavior) skips explaining what each
// stage even is. `guided` walks a short 2-step version instead — just enough to get
// silver.01_unpacked_<name>/02_typed_<name> created (materialize_unpacked_typed_sync, empty
// shell — real rows land once the project's DAG actually runs) out the door fast;
// standardization/annotation/quarantine stay reachable the normal way, by clicking a real
// 03/04/05 node's own "+" directly on the canvas once this bronze has a chain.
const WIZARD_STAGES = ["unpacked", "typed"];

// Module 6 extension (payload & structuration) — étapes 2/3, §5 rewrite (unpacked/typed
// convention). Profile → edit the contract (types, names, required/PK) → save (renders
// 01_unpacked/02_typed at next build). Standardization/annotation/quarantine/validation are
// all hand-written dbt SQL from here on, authored via the "+" on their own canvas node.
export default function StructurationPanel({ project, dataset, readOnly = false, stage = null, guided = false, onFinish }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [state, setState] = useState("loading"); // loading | none | notApplicable | ready
  const [notApplicableReason, setNotApplicableReason] = useState("");
  const [fields, setFields] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Canvas lineage chain (01/02) — each stage node opens this same popup, but only reveals
  // the columns that stage has actually decided by then (see STAGE_LEVEL): 01 shows only the
  // raw fields, 02 adds naming/typing. "Show full contract" is the escape hatch for anyone who
  // wants the whole picture anyway. In `guided` mode there's no jump target from the canvas —
  // the panel drives its own `wizardStage` through the same 2 names instead, via the
  // Back/Next/Finish row below.
  const [showAll, setShowAll] = useState(false);
  const [wizardStage, setWizardStage] = useState(WIZARD_STAGES[0]);
  const [highlighted, setHighlighted] = useState(null);
  const fieldsRef = useRef(null);

  const effectiveStage = guided ? wizardStage : stage;
  const level = showAll || !effectiveStage ? MAX_LEVEL : (STAGE_LEVEL[effectiveStage] ?? MAX_LEVEL);
  const showTyped = level >= 2;

  useEffect(() => { setShowAll(false); }, [dataset.id, stage]);

  useEffect(() => {
    if (state !== "ready" || !effectiveStage) return;
    const block = STAGE_BLOCK[effectiveStage];
    const targetRef = { fields: fieldsRef }[block];
    if (!targetRef?.current) return;
    targetRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    setHighlighted(block);
    const timer = setTimeout(() => setHighlighted(null), 1600);
    return () => clearTimeout(timer);
  }, [state, effectiveStage, dataset.id]);

  const highlightStyle = (block) => (highlighted === block
    ? { boxShadow: "0 0 0 2px var(--ember)", borderRadius: "var(--radius)", transition: "box-shadow .3s" }
    : { transition: "box-shadow .3s" });

  const load = async () => {
    setState("loading");
    setError("");
    try {
      const c = await structurationApi.getStructuration(project.id, dataset.id);
      setFields(c.column_mapping);
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
      await structurationApi.saveStructuration(project.id, dataset.id, { column_mapping: fields });
      showToast(t("medallion.structuration.saved"));
      return true;
    } catch (err) {
      setError(err.message || t("medallion.structuration.saveFailed"));
      return false;
    } finally {
      setBusy(false);
    }
  };

  // Module 18 §7 UX — wizard navigation (01 unpacked -> 02 typed only, see WIZARD_STAGES).
  // Leaving 01 just needs something kept; 02 (Terminer) needs valid names too, same
  // fieldsValid the contract table itself is built from.
  const wizardStepIndex = WIZARD_STAGES.indexOf(wizardStage);
  const wizardCanAdvance = wizardStepIndex === 0 ? fields.some((f) => f.include) : fieldsValid;
  const wizardGoBack = () => setWizardStage(WIZARD_STAGES[Math.max(wizardStepIndex - 1, 0)]);
  const wizardGoNext = () => setWizardStage(WIZARD_STAGES[Math.min(wizardStepIndex + 1, WIZARD_STAGES.length - 1)]);
  const wizardFinish = async () => {
    if (await save()) onFinish?.();
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

      {guided ? (
        <div className="card" style={{
          padding: "8px 12px", marginBottom: 12, background: "var(--ember-soft)", border: "1px solid var(--ember)",
        }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ember-600)", textTransform: "uppercase", letterSpacing: ".04em" }}>
            {t("medallion.structuration.wizardStep", { current: wizardStepIndex + 1, total: WIZARD_STAGES.length })}
          </div>
          <div style={{ fontSize: 12, color: "var(--ember-600)", marginTop: 2 }}>{t(`medallion.structuration.stageHint_${wizardStage}`)}</div>
        </div>
      ) : stage && level < MAX_LEVEL && (
        <div className="card" style={{
          padding: "8px 12px", marginBottom: 12, display: "flex", justifyContent: "space-between",
          alignItems: "center", gap: 10, background: "var(--ember-soft)", border: "1px solid var(--ember)",
        }}>
          <div style={{ fontSize: 12, color: "var(--ember-600)" }}>{t(`medallion.structuration.stageHint_${stage}`)}</div>
          <button type="button" className="btn-ghost" style={{ padding: "4px 8px", fontSize: 11.5, whiteSpace: "nowrap" }} onClick={() => setShowAll(true)}>
            {t("medallion.structuration.showFullContract")}
          </button>
        </div>
      )}

      {showTyped && fields.some((f) => f.ambiguous && f.include) && (
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
              {showTyped && <th>{t("imports.modal.colTarget")}</th>}
              {showTyped && <th>{t("imports.modal.colType")}</th>}
              {showTyped && <th>{t("medallion.structuration.colNullable")}</th>}
              {/* Confidence column hidden on request — data still flows through (f.confidence), just not displayed here. */}
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
                  {showTyped && (
                    <td style={{ minWidth: 140 }}>
                      <Input
                        style={{ fontFamily: "var(--font-m)", fontSize: 12, borderColor: invalidName ? "var(--danger)" : undefined }}
                        value={f.target_name} disabled={readOnly || !f.include}
                        onChange={(e) => updateField(idx, { target_name: e.target.value })}
                      />
                      {showDateFormat && (
                        <div style={{ marginTop: 6 }}>
                          <Input
                            style={{ fontFamily: "var(--font-m)", fontSize: 11.5 }}
                            disabled={readOnly}
                            value={f.format || "%d/%m/%Y"}
                            onChange={(e) => updateField(idx, { format: e.target.value })}
                            placeholder="%Y-%m-%d %H:%M:%S.%f"
                          />
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                            {DATE_FORMAT_SUGGESTIONS.map((s) => (
                              <button
                                key={s.format} type="button" className="badge badge-neutral"
                                style={{ cursor: "pointer", border: "none", fontSize: 10, fontFamily: "var(--font-m)" }}
                                disabled={readOnly}
                                title={t(s.labelKey)}
                                onClick={() => updateField(idx, { format: s.format })}
                              >
                                {s.format}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </td>
                  )}
                  {showTyped && (
                    <td style={{ minWidth: 110 }}>
                      <select
                        className="input" value={f.target_type} disabled={readOnly || !f.include}
                        onChange={(e) => updateField(idx, { target_type: e.target.value })}
                      >
                        {TYPES.map((ty) => <option key={ty} value={ty}>{ty}</option>)}
                      </select>
                    </td>
                  )}
                  {showTyped && (
                    <td style={{ textAlign: "center" }}>
                      <input
                        type="checkbox" checked={f.nullable ?? true} disabled={readOnly || !f.include}
                        title={t("medallion.structuration.nullableHelp")}
                        onChange={(e) => updateField(idx, { nullable: e.target.checked })}
                      />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {!readOnly && !guided && (
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={busy} onClick={profile}>{Icon.refresh()} {t("medallion.structuration.reprofile")}</button>
          <Button disabled={!canSave || busy} onClick={save}>{busy ? t("medallion.structuration.saving") : t("medallion.structuration.save")}</Button>
        </div>
      )}

      {guided && !readOnly && (
        // UX ask — only one save-ish action ever shows in guided mode, and only on the last
        // step ("Terminer" IS the save, via wizardFinish): no separate Enregistrer button
        // competing with it on step 1. Busy re-labels it while
        // materialize_unpacked_typed_sync runs server-side (a real DROP+CREATE TABLE, not
        // instant) so the click doesn't look like it did nothing.
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 20 }}>
          <button type="button" className="btn-ghost" style={{ padding: "6px 12px" }} disabled={wizardStepIndex === 0 || busy} onClick={wizardGoBack}>
            ← {t("medallion.structuration.wizardBack")}
          </button>
          {wizardStepIndex === WIZARD_STAGES.length - 1 ? (
            <Button disabled={!canSave || busy} onClick={wizardFinish}>{busy ? t("medallion.structuration.saving") : t("medallion.structuration.wizardFinish")}</Button>
          ) : (
            <Button disabled={!wizardCanAdvance || busy} onClick={wizardGoNext}>{t("medallion.structuration.wizardNext")} →</Button>
          )}
        </div>
      )}
    </div>
  );
}
