import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as mlTemplatesApi from "../../api/mlTemplates.js";

const FIELD_TYPES = ["table", "column", "column_list", "number", "text"];

export default function MLTemplateDrawer({ template, onClose, onSaved, onDeleted }) {
  const { t } = useTranslation();
  const OBJECTIVE_LABEL = t("settings.mlTemplates.objectives", { returnObjects: true });
  const OBJECTIVES = ["anomaly", "scoring", "forecast", "clustering", "record_linkage"].map((value) => ({ value, label: OBJECTIVE_LABEL[value] }));

  const isEdit = !!template;
  const [name, setName] = useState(template?.name || "");
  const [mlObjective, setMlObjective] = useState(template?.ml_objective || "anomaly");
  const [description, setDescription] = useState(template?.description || "");
  const [pythonCode, setPythonCode] = useState(template?.python_code || "");
  const [expectedInputs, setExpectedInputs] = useState(template?.expected_inputs || []);
  const [outputColumns, setOutputColumns] = useState((template?.output_columns || []).join(", "));

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const addField = () => setExpectedInputs((f) => [...f, { key: "", label: "", type: "text", default: "" }]);
  const updateField = (i, patch) => setExpectedInputs((f) => f.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  const removeField = (i) => setExpectedInputs((f) => f.filter((_, idx) => idx !== i));

  const valid = name.trim() && pythonCode.trim();

  const buildPayload = () => ({
    name: name.trim(),
    ml_objective: mlObjective,
    description: description.trim() || null,
    python_code: pythonCode,
    expected_inputs: expectedInputs.filter((f) => f.key.trim()),
    output_columns: outputColumns.split(",").map((s) => s.trim()).filter(Boolean),
  });

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      if (isEdit) {
        const updated = await mlTemplatesApi.updateTemplate(template.id, buildPayload());
        onSaved(updated);
      } else {
        const created = await mlTemplatesApi.createTemplate(buildPayload());
        onSaved(created);
      }
    } catch (err) {
      setError(err.message || t("settings.mlTemplates.drawer.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await mlTemplatesApi.deleteTemplate(template.id);
      onDeleted(template.id);
    } catch (err) {
      setError(err.message || t("settings.mlTemplates.drawer.deleteFailed"));
      setBusy(false);
    }
  };

  return (
    <Drawer title={isEdit ? t("settings.mlTemplates.drawer.editTitle", { name: template.name }) : t("settings.mlTemplates.drawer.newTitle")} description={t("settings.mlTemplates.drawer.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <form onSubmit={submit}>
        <Field label={t("settings.mlTemplates.drawer.name")}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("settings.mlTemplates.drawer.namePlaceholder")} />
        </Field>

        <Field label={t("settings.mlTemplates.drawer.objective")}>
          <select className="input" value={mlObjective} onChange={(e) => setMlObjective(e.target.value)}>
            {OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>

        <Field label={t("settings.mlTemplates.drawer.descriptionField")}>
          <textarea className="input" style={{ minHeight: 60, resize: "vertical" }} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        <Field label={t("settings.mlTemplates.drawer.pythonCode")}>
          <textarea
            className="input" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, minHeight: 220, resize: "vertical" }}
            value={pythonCode} onChange={(e) => setPythonCode(e.target.value)}
            placeholder={`df = read_table("TABLE_ENTREE")\n...\nwrite_table(df, "TABLE_SORTIE")`}
          />
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>
            {t("settings.mlTemplates.drawer.placeholderHintPre")} <code>TABLE_ENTREE</code>{t("settings.mlTemplates.drawer.placeholderHintPost")}
          </div>
        </Field>

        <Field label={t("settings.mlTemplates.drawer.expectedFields")}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {expectedInputs.map((f, i) => (
              <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input className="input" style={{ flex: 1.2 }} placeholder={t("settings.mlTemplates.drawer.placeholder")} value={f.key} onChange={(e) => updateField(i, { key: e.target.value.toUpperCase() })} />
                <input className="input" style={{ flex: 1.5 }} placeholder={t("settings.mlTemplates.drawer.label")} value={f.label} onChange={(e) => updateField(i, { label: e.target.value })} />
                <select className="input" style={{ flex: 1 }} value={f.type} onChange={(e) => updateField(i, { type: e.target.value })}>
                  {FIELD_TYPES.map((ft) => <option key={ft} value={ft}>{ft}</option>)}
                </select>
                <input className="input" style={{ flex: 0.8 }} placeholder={t("settings.mlTemplates.drawer.default")} value={f.default || ""} onChange={(e) => updateField(i, { default: e.target.value })} />
                <button type="button" className="btn-icon" onClick={() => removeField(i)}>{Icon.trash()}</button>
              </div>
            ))}
            <Button type="button" variant="ghost" className="inline" onClick={addField}>{Icon.plus()} {t("settings.mlTemplates.drawer.addField")}</Button>
          </div>
        </Field>

        <Field label={t("settings.mlTemplates.drawer.outputColumns")}>
          <Input value={outputColumns} onChange={(e) => setOutputColumns(e.target.value)} placeholder="score_anomalie, est_anomalie" />
        </Field>

        <div className="modal-actions">
          {isEdit && <button type="button" className="btn-icon" onClick={remove} disabled={busy} title={t("common.delete")}>{Icon.trash()}</button>}
          <Button type="button" variant="ghost" onClick={onClose}>{t("settings.mlTemplates.drawer.cancel")}</Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("settings.mlTemplates.drawer.saving") : t("settings.mlTemplates.drawer.save")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
