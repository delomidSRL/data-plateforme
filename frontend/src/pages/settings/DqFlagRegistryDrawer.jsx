import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as dqFlagRegistryApi from "../../api/dqFlagRegistry.js";

// Mirrors the backend's own rule (app/schemas/dq_flag_registry.py's _FLAG_NAME_RE) — a literal
// tag matched verbatim by hand-written 05 routing, never a SQL identifier.
const NAME_RE = /^[a-z][a-z0-9_]*$/;

export default function DqFlagRegistryDrawer({ entry, onClose, onSaved, onDeleted }) {
  const { t } = useTranslation();
  const isEdit = !!entry;

  const [flagName, setFlagName] = useState(entry?.flag_name || "");
  const [category, setCategory] = useState(entry?.category || "elimination");
  const [sourceRule, setSourceRule] = useState(entry?.source_rule || "");
  const [issueType, setIssueType] = useState(entry?.issue_type || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const valid = NAME_RE.test(flagName.trim());

  const buildPayload = () => ({
    flag_name: flagName.trim(),
    category,
    source_rule: sourceRule.trim() || null,
    issue_type: issueType.trim() || null,
  });

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      if (isEdit) onSaved(await dqFlagRegistryApi.updateEntry(entry.id, buildPayload()));
      else onSaved(await dqFlagRegistryApi.createEntry(buildPayload()));
    } catch (err) {
      setError(err.message || t("settings.dqFlagRegistry.drawer.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await dqFlagRegistryApi.deleteEntry(entry.id);
      onDeleted(entry.id);
    } catch (err) {
      setError(err.message || t("settings.dqFlagRegistry.drawer.deleteFailed"));
      setBusy(false);
    }
  };

  return (
    <Drawer
      title={isEdit ? t("settings.dqFlagRegistry.drawer.editTitle", { name: entry.flag_name }) : t("settings.dqFlagRegistry.drawer.newTitle")}
      description={t("settings.dqFlagRegistry.drawer.description")}
      onClose={onClose}
    >
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <form onSubmit={submit}>
        <Field label={t("settings.dqFlagRegistry.drawer.name")}>
          <Input value={flagName} onChange={(e) => setFlagName(e.target.value)} placeholder="dq_invalid_email" style={{ fontFamily: "var(--font-m)" }} />
          {flagName && !NAME_RE.test(flagName.trim()) && (
            <div style={{ fontSize: 11, color: "#b3261e", marginTop: 4 }}>{t("settings.dqFlagRegistry.drawer.nameInvalid")}</div>
          )}
        </Field>

        <Field label={t("settings.dqFlagRegistry.drawer.category")}>
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="elimination">{t("settings.dqFlagRegistry.categoryElimination")}</option>
            <option value="informative">{t("settings.dqFlagRegistry.categoryInformative")}</option>
          </select>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("settings.dqFlagRegistry.drawer.categoryHint")}</div>
        </Field>

        <Field label={t("settings.dqFlagRegistry.drawer.sourceRule")}>
          <Input value={sourceRule} onChange={(e) => setSourceRule(e.target.value)} placeholder="ORG-NR-083" />
        </Field>

        <Field label={t("settings.dqFlagRegistry.drawer.issueType")}>
          <Input value={issueType} onChange={(e) => setIssueType(e.target.value)} placeholder="FORMAT_MISMATCH" />
        </Field>

        <div className="modal-actions">
          {isEdit && <button type="button" className="btn-icon" onClick={remove} disabled={busy} title={t("common.delete")}>{Icon.trash()}</button>}
          <Button type="button" variant="ghost" onClick={onClose}>{t("settings.dqFlagRegistry.drawer.cancel")}</Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("settings.dqFlagRegistry.drawer.saving") : t("settings.dqFlagRegistry.drawer.save")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
