import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";

const NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

export default function MacroPanel({ project, macro, onClose, onSaved, onDeleted }) {
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

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      parameters: parameters.map((p) => ({ name: p.name.trim(), default: (p.default || "").trim() || null })),
      sql_body: sqlBody,
    };
    try {
      if (isEdit) onSaved(await medallionApi.updateMacro(project.id, macro.id, payload));
      else onSaved(await medallionApi.createMacro(project.id, payload));
    } catch (err) {
      setError(err.message || t("medallion.macros.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await medallionApi.deleteMacro(project.id, macro.id);
      onDeleted(macro.id);
    } catch (err) {
      setError(err.message || t("medallion.macros.deleteFailed"));
      setBusy(false);
    }
  };

  return (
    <Drawer
      title={isEdit ? t("medallion.macros.editTitle", { name: macro.name }) : t("medallion.macros.addTitle")}
      description={t("medallion.macros.panelDesc")}
      onClose={onClose}
    >
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <form onSubmit={submit}>
        <Field label={t("medallion.macros.name")}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="dedup_key" />
          {name && !NAME_RE.test(name.trim()) && (
            <div style={{ fontSize: 11, color: "#b3261e", marginTop: 4 }}>{t("medallion.macros.nameInvalid")}</div>
          )}
        </Field>

        <Field label={t("medallion.macros.description")}>
          <textarea className="input" style={{ minHeight: 50, resize: "vertical" }} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        <Field label={t("medallion.macros.parameters")}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {parameters.map((p, i) => (
              <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input className="input" style={{ flex: 1 }} placeholder={t("medallion.macros.paramName")} value={p.name} onChange={(e) => updateParam(i, { name: e.target.value })} />
                <input className="input" style={{ flex: 1 }} placeholder={t("medallion.macros.paramDefault")} value={p.default || ""} onChange={(e) => updateParam(i, { default: e.target.value })} />
                <button type="button" className="btn-icon" onClick={() => removeParam(i)}>{Icon.trash()}</button>
              </div>
            ))}
            <Button type="button" variant="ghost" className="inline" onClick={addParam}>{Icon.plus()} {t("medallion.macros.addParam")}</Button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("medallion.macros.paramDefaultHint")}</div>
        </Field>

        <Field label={t("medallion.macros.sqlBody")}>
          <textarea
            className="input" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, minHeight: 140, resize: "vertical" }}
            value={sqlBody} onChange={(e) => setSqlBody(e.target.value)}
            placeholder={`upper(trim(${parameters[0]?.name || "col"}))`}
          />
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("medallion.macros.sqlBodyHint")}</div>
        </Field>

        <div className="modal-actions">
          {isEdit && <button type="button" className="btn-icon" onClick={remove} disabled={busy} title={t("common.delete")}>{Icon.trash()}</button>}
          <Button type="button" variant="ghost" onClick={onClose}>{t("medallion.panel.cancel")}</Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("medallion.panel.saving") : t("medallion.panel.save")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
