import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as dbtMacrosApi from "../../api/dbtMacros.js";

const NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
// Mirrors backend app/services/dbt_macros.py's extract_macro_name — used here only to warn
// early (before submit) if the name typed above doesn't match the { % macro %} declared below.
const MACRO_DECL_RE = /\{%-?\s*macro\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(/;

const DEFINITION_PLACEHOLDER = `{% macro mask_email(col) -%}
regexp_replace(col, '(^.).*(@.*)$', '\\1***\\2')
{%- endmacro %}`;

export default function DbtMacroDrawer({ macro, onClose, onSaved, onDeleted }) {
  const { t } = useTranslation();
  const isEdit = !!macro;

  const [name, setName] = useState(macro?.name || "");
  const [description, setDescription] = useState(macro?.description || "");
  const [definition, setDefinition] = useState(macro?.definition || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const declaredName = definition.trim() ? MACRO_DECL_RE.exec(definition)?.[1] : null;
  const nameMismatch = !!(name.trim() && definition.trim() && declaredName && declaredName !== name.trim());
  const valid = NAME_RE.test(name.trim()) && definition.trim() && !nameMismatch;

  const buildPayload = () => ({
    name: name.trim(),
    description: description.trim() || null,
    definition,
  });

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      if (isEdit) onSaved(await dbtMacrosApi.updateMacro(macro.id, buildPayload()));
      else onSaved(await dbtMacrosApi.createMacro(buildPayload()));
    } catch (err) {
      setError(err.message || t("settings.dbtMacros.drawer.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await dbtMacrosApi.deleteMacro(macro.id);
      onDeleted(macro.id);
    } catch (err) {
      setError(err.message || t("settings.dbtMacros.drawer.deleteFailed"));
      setBusy(false);
    }
  };

  return (
    <Drawer
      title={isEdit ? t("settings.dbtMacros.drawer.editTitle", { name: macro.name }) : t("settings.dbtMacros.drawer.newTitle")}
      description={t("settings.dbtMacros.drawer.description")}
      onClose={onClose}
    >
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      <div style={{ fontSize: 11.5, color: "var(--text-muted)", margin: "-6px 0 14px" }}>{t("settings.dbtMacros.drawer.panelDesc")}</div>

      <form onSubmit={submit}>
        <Field label={t("settings.dbtMacros.drawer.name")}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="mask_email" />
          {name && !NAME_RE.test(name.trim()) && (
            <div style={{ fontSize: 11, color: "#b3261e", marginTop: 4 }}>{t("settings.dbtMacros.drawer.nameInvalid")}</div>
          )}
        </Field>

        <Field label={t("settings.dbtMacros.drawer.descriptionField")}>
          <textarea className="input" style={{ minHeight: 50, resize: "vertical" }} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        <Field label={t("settings.dbtMacros.drawer.definition")}>
          <textarea
            className="input" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, minHeight: 160, resize: "vertical" }}
            value={definition} onChange={(e) => setDefinition(e.target.value)}
            placeholder={DEFINITION_PLACEHOLDER}
          />
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("settings.dbtMacros.drawer.definitionHint")}</div>
          {nameMismatch && (
            <div style={{ fontSize: 11, color: "#b3261e", marginTop: 4 }}>
              {t("settings.dbtMacros.drawer.nameMismatch", { declared: declaredName, name: name.trim() })}
            </div>
          )}
        </Field>

        <div className="modal-actions">
          {isEdit && <button type="button" className="btn-icon" onClick={remove} disabled={busy} title={t("common.delete")}>{Icon.trash()}</button>}
          <Button type="button" variant="ghost" onClick={onClose}>{t("settings.dbtMacros.drawer.cancel")}</Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("settings.dbtMacros.drawer.saving") : t("settings.dbtMacros.drawer.save")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
