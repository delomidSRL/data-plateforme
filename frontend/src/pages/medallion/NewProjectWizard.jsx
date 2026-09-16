import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as medallionApi from "../../api/medallion.js";
import * as sourcesApi from "../../api/sources.js";
import ScheduleField, { isValidSchedule } from "./ScheduleField.jsx";

export default function NewProjectWizard({ sources, instances, supersetInstances = [], onClose, onCreated }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [dbtProjectName, setDbtProjectName] = useState("");
  const [objectStoreId, setObjectStoreId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [supersetInstanceId, setSupersetInstanceId] = useState(""); // optional — Module 11, can also be set later at publish time
  const [schedule, setSchedule] = useState(null); // null = manuel ; sinon cron/preset — Module 3 correctif

  const [introspectSourceId, setIntrospectSourceId] = useState("");
  const [introspecting, setIntrospecting] = useState(false);
  const [candidates, setCandidates] = useState([]); // [{key, label, source_object}]
  const [selected, setSelected] = useState(new Set());
  const [candidateFilter, setCandidateFilter] = useState("");

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const minioSources = sources.filter((s) => s.type === "minio");
  const pgSources = sources.filter((s) => s.type === "postgresql");
  const platformInstances = instances.filter((i) => i.origin === "platform");
  const externalInstances = instances.filter((i) => i.origin === "external");
  const activeSupersetInstances = supersetInstances.filter((i) => i.is_active);

  const doIntrospect = async () => {
    if (!introspectSourceId) return;
    setIntrospecting(true);
    setError("");
    try {
      const source = sources.find((s) => s.id === Number(introspectSourceId));
      const result = await sourcesApi.introspectSource(introspectSourceId);
      const items = [];
      if (source.type === "minio") {
        const pushObjectCandidates = (bucket, objects) => {
          for (const o of objects) {
            const fileName = o.split("/").pop();
            const suggestedName = fileName.replace(/\.[^.]+$/, "").replace(/\W/g, "_");
            items.push({ key: `${bucket}/${o}`, label: `${bucket}/${o}`, source_object: `${bucket}/${o}`, suggestedName });
          }
        };
        if (result.objects && result.objects.length) {
          // Bucket-scoped source: already gets its files directly from one call.
          pushObjectCandidates(result.buckets[0], result.objects);
        } else if (result.buckets && result.buckets.length) {
          // Unscoped, possibly several buckets: fetch each bucket's files and merge them
          // into one flat, searchable list — one candidate per file, never per bucket, so
          // selecting several never accidentally merges unrelated files into one table.
          const perBucket = await Promise.all(result.buckets.map((b) => sourcesApi.introspectSource(introspectSourceId, b)));
          result.buckets.forEach((b, i) => pushObjectCandidates(b, perBucket[i].objects || []));
        }
      } else {
        for (const schema of result.schemas) {
          for (const tbl of schema.tables) {
            const qualified = `${schema.name}.${tbl.name}`;
            items.push({ key: qualified, label: qualified, source_object: qualified, suggestedName: tbl.name.replace(/\W/g, "_") });
          }
        }
      }
      setCandidates(items);
      setSelected(new Set()); // opt-in: introspection can surface many tables (incl. system schemas), don't preselect all
    } catch (err) {
      setError(err.message || t("medallion.wizard.introspectFailed"));
    } finally {
      setIntrospecting(false);
    }
  };

  const toggle = (key) => setSelected((s) => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; });

  const valid = name.trim() && dbtProjectName.trim() && objectStoreId && warehouseId && instanceId && (schedule == null || isValidSchedule(schedule));

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const project = await medallionApi.createProject({
        name: name.trim(),
        object_store_source_id: Number(objectStoreId),
        warehouse_source_id: Number(warehouseId),
        airflow_instance_id: Number(instanceId),
        superset_instance_id: supersetInstanceId ? Number(supersetInstanceId) : null,
        dbt_project_name: dbtProjectName.trim(),
        target: "dev",
        schedule,
      });

      const toCreate = candidates.filter((c) => selected.has(c.key));
      for (const c of toCreate) {
        await medallionApi.createDataset(project.id, {
          layer: "bronze",
          name: c.suggestedName,
          source_id: Number(introspectSourceId),
          source_object: c.source_object,
          load_mode: "full",
        });
      }

      onCreated(project);
    } catch (err) {
      setError(err.message || t("medallion.wizard.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={t("medallion.wizard.title")} description={t("medallion.wizard.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <form onSubmit={submit}>
        <Field label={t("medallion.wizard.projectName")}>
          <Input placeholder={t("medallion.wizard.projectNamePlaceholder")} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t("medallion.wizard.dbtProjectName")}>
          <Input placeholder="ex: ventes" value={dbtProjectName} onChange={(e) => setDbtProjectName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} />
        </Field>

        <Field label={t("medallion.wizard.objectStore")}>
          <select className="input" value={objectStoreId} onChange={(e) => setObjectStoreId(e.target.value)}>
            <option value="">{t("medallion.wizard.chooseMinioSource")}</option>
            {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>

        <Field label={t("medallion.wizard.warehouse")}>
          <select className="input" value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
            <option value="">{t("medallion.wizard.choosePgSource")}</option>
            {pgSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>

        <Field label={t("medallion.wizard.airflowInstance")}>
          <select className="input" value={instanceId} onChange={(e) => setInstanceId(e.target.value)}>
            <option value="">{t("medallion.wizard.choose")}</option>
            {platformInstances.length > 0 && (
              <optgroup label={t("orchestrators.platformManaged")}>
                {platformInstances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
              </optgroup>
            )}
            {externalInstances.length > 0 && (
              <optgroup label={t("orchestrators.external")}>
                {externalInstances.map((i) => (
                  <option key={i.id} value={i.id} disabled={!i.capabilities?.deployable}>
                    {i.name}{!i.capabilities?.deployable ? ` — ${t("medallion.wizard.notDeployable")}` : ""}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </Field>

        <Field label={t("medallion.wizard.supersetInstance")}>
          <select className="input" value={supersetInstanceId} onChange={(e) => setSupersetInstanceId(e.target.value)}>
            <option value="">{t("medallion.wizard.supersetInstanceLater")}</option>
            {activeSupersetInstances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>{t("medallion.wizard.supersetInstanceHint")}</div>
        </Field>

        <ScheduleField value={schedule} onChange={setSchedule} />

        <div className="card" style={{ padding: 14, marginTop: 6, marginBottom: 16 }}>
          <div className="field-label" style={{ marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }}>
            {Icon.wand()} {t("medallion.wizard.bronzeAssistant")}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <select className="input" value={introspectSourceId} onChange={(e) => setIntrospectSourceId(e.target.value)}>
              <option value="">{t("medallion.wizard.chooseSource")}</option>
              {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <Button type="button" variant="ghost" className="inline" disabled={!introspectSourceId || introspecting} onClick={doIntrospect}>
              {introspecting ? t("medallion.wizard.introspecting") : t("medallion.wizard.introspect")}
            </Button>
          </div>

          {candidates.length > 0 && (
            <>
              <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
                <input
                  className="input" style={{ flex: 1 }} placeholder={t("medallion.wizard.filter")}
                  value={candidateFilter} onChange={(e) => setCandidateFilter(e.target.value)}
                />
                <button type="button" className="link" onClick={() => setSelected(new Set(candidates.map((c) => c.key)))}>{t("medallion.wizard.checkAll")}</button>
                <button type="button" className="link" onClick={() => setSelected(new Set())}>{t("medallion.wizard.uncheckAll")}</button>
              </div>
              <div style={{ marginTop: 10, maxHeight: 220, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
                {candidates
                  .filter((c) => c.label.toLowerCase().includes(candidateFilter.toLowerCase()))
                  .map((c) => (
                    <label key={c.key} className="service-tile-checkline">
                      <input type="checkbox" checked={selected.has(c.key)} onChange={() => toggle(c.key)} />
                      <span style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{c.label}</span>
                    </label>
                  ))}
              </div>
              <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>{t("medallion.wizard.selectedCount", { count: selected.size })}</div>
            </>
          )}
        </div>

        <div className="modal-actions">
          <Button type="button" variant="ghost" onClick={onClose}>{t("medallion.wizard.cancel")}</Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("medallion.wizard.creating") : t("medallion.wizard.createProject")}</Button>
        </div>
      </form>
    </Drawer>
  );
}
