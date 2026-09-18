import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as dbtMacrosApi from "../../api/dbtMacros.js";

const NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

export default function DbtMacroDrawer({ macro, onClose, onSaved, onDeleted }) {
  const { t } = useTranslation();
  const isEdit = !!macro;

  const [name, setName] = useState(macro?.name || "");
  const [description, setDescription] = useState(macro?.description || "");
  const [parameters, setParameters] = useState(macro?.parameters?.length ? macro.parameters : [{ name: "col", default: "" }]);
  const [sqlBody, setSqlBody] = useState(macro?.sql_body || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const addParam = () => setParameters((ps) => [...ps, { name: "", default: "" }]);
  const updateParam = (i, patch) => setParameters((ps) => ps.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
  const removeParam = (i) => setParameters((ps) => ps.filter((_, idx) => idx !== i));

  const valid = NAME_RE.test(name.trim()) && sqlBody.trim() && parameters.every((p) => NAME_RE.test((p.name || "").trim()));

  const buildPayload = () => ({
    name: name.trim(),
    description: description.trim() || null,
    parameters: parameters.map((p) => ({ name: p.name.trim(), default: (p.default || "").trim() || null })),
    sql_body: sqlBody,
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
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="dedup_key" />
          {name && !NAME_RE.test(name.trim()) && (
            <div style={{ fontSize: 11, color: "#b3261e", marginTop: 4 }}>{t("settings.dbtMacros.drawer.nameInvalid")}</div>
          )}
        </Field>

        <Field label={t("settings.dbtMacros.drawer.descriptionField")}>
          <textarea className="input" style={{ minHeight: 50, resize: "vertical" }} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        <Field label={t("settings.dbtMacros.drawer.parameters")}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {parameters.map((p, i) => (
              <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input className="input" style={{ flex: 1 }} placeholder={t("settings.dbtMacros.drawer.paramName")} value={p.name} onChange={(e) => updateParam(i, { name: e.target.value })} />
                <input className="input" style={{ flex: 1 }} placeholder={t("settings.dbtMacros.drawer.paramDefault")} value={p.default || ""} onChange={(e) => updateParam(i, { default: e.target.value })} />
                <button type="button" className="btn-icon" onClick={() => removeParam(i)}>{Icon.trash()}</button>
              </div>
            ))}
            <Button type="button" variant="ghost" className="inline" onClick={addParam}>{Icon.plus()} {t("settings.dbtMacros.drawer.addParam")}</Button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("settings.dbtMacros.drawer.paramDefaultHint")}</div>
        </Field>

        <Field label={t("settings.dbtMacros.drawer.sqlBody")}>
          <textarea
            className="input" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, minHeight: 140, resize: "vertical" }}
            value={sqlBody} onChange={(e) => setSqlBody(e.target.value)}
            placeholder={`upper(trim(${parameters[0]?.name || "col"}))`}
          />
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("settings.dbtMacros.drawer.sqlBodyHint")}</div>
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
