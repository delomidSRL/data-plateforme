import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as mlTemplatesApi from "../../api/mlTemplates.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import MLTemplateDrawer from "./MLTemplateDrawer.jsx";

export default function MLTemplates() {
  const { t } = useTranslation();
  const showToast = useToast();
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState(null); // { template } | { new: true } | null

  const OBJECTIVE_LABEL = t("settings.mlTemplates.objectives", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      setTemplates(await mlTemplatesApi.listTemplates());
      setError("");
    } catch (err) {
      setError(err.message || t("settings.mlTemplates.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onSaved = (tpl) => {
    setTemplates((list) => {
      const exists = list.some((x) => x.id === tpl.id);
      return exists ? list.map((x) => (x.id === tpl.id ? tpl : x)) : [...list, tpl];
    });
    setPanel(null);
    showToast(t("settings.mlTemplates.templateSaved"));
  };

  const onDeleted = (id) => {
    setTemplates((list) => list.filter((x) => x.id !== id));
    setPanel(null);
    showToast(t("settings.mlTemplates.templateDeleted"));
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("settings.badge")}</div>
          <h1 className="page-title">{t("settings.mlTemplates.title")}</h1>
          <p className="page-desc">{t("settings.mlTemplates.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setPanel({ new: true })}>{Icon.plus()}{t("settings.mlTemplates.newTemplate")}</button>
      </div>

      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>{t("settings.mlTemplates.colName")}</th><th>{t("settings.mlTemplates.colObjective")}</th><th>{t("settings.mlTemplates.colDescription")}</th><th>{t("settings.mlTemplates.colFields")}</th></tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && templates.length === 0 && (
              <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("settings.mlTemplates.noTemplates")}</td></tr>
            )}
            {templates.map((tpl) => (
              <tr key={tpl.id} style={{ cursor: "pointer" }} onClick={() => setPanel({ template: tpl })}>
                <td className="uc-name">{tpl.name}</td>
                <td><Badge tone="accent">{OBJECTIVE_LABEL[tpl.ml_objective] || tpl.ml_objective}</Badge></td>
                <td style={{ fontSize: 12.5, color: "var(--text-muted)", maxWidth: 360 }}>{tpl.description || "—"}</td>
                <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{tpl.expected_inputs.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {panel && (
        <MLTemplateDrawer
          template={panel.template}
          onClose={() => setPanel(null)}
          onSaved={onSaved}
          onDeleted={onDeleted}
        />
      )}
    </>
  );
}
