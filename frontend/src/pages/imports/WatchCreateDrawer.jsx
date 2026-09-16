import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as sourcesApi from "../../api/sources.js";
import * as fileWatchApi from "../../api/fileWatch.js";

const CADENCE_PRESETS = [
  { value: 60, label: "1 min" },
  { value: 300, label: "5 min" },
  { value: 900, label: "15 min" },
  { value: 3600, label: "1 h" },
];

export default function WatchCreateDrawer({ fileImport, onClose, onCreated }) {
  const { t } = useTranslation();
  const [minioSources, setMinioSources] = useState([]);
  const [name, setName] = useState(t("watches.drawer.defaultName", { name: fileImport.name }));
  const [transport, setTransport] = useState("minio");
  const [sourceId, setSourceId] = useState("");
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [pattern, setPattern] = useState("");
  const [patternType, setPatternType] = useState("glob");
  const [pollIntervalSeconds, setPollIntervalSeconds] = useState(300);
  const [completeness, setCompleteness] = useState("stable_size");
  const [stableDelay, setStableDelay] = useState(30);
  const [controlSuffix, setControlSuffix] = useState(".done");
  const [slaEnabled, setSlaEnabled] = useState(false);
  const [slaMode, setSlaMode] = useState("daily"); // daily | custom
  const [dailyTime, setDailyTime] = useState("08:00");
  const [customCron, setCustomCron] = useState("");
  const [graceMinutes, setGraceMinutes] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResults, setTestResults] = useState(null);

  useEffect(() => { sourcesApi.listSources().then((all) => setMinioSources(all.filter((s) => s.type === "minio"))); }, []);

  const location = transport === "minio" ? { source_id: sourceId, bucket, prefix } : { path: localPath };
  const valid = name.trim() && pattern.trim() && (transport === "minio" ? sourceId && bucket : localPath.trim())
    && (!slaEnabled || slaMode === "daily" || customCron.trim());

  const effectiveCron = () => {
    if (!slaEnabled) return null;
    if (slaMode === "daily") {
      const [h, m] = dailyTime.split(":").map(Number);
      return `${m} ${h} * * *`;
    }
    return customCron.trim();
  };

  const draft = () => ({
    transport, location, pattern, pattern_type: patternType,
    completeness_strategy: completeness, stable_size_delay_seconds: stableDelay, control_file_suffix: controlSuffix,
  });

  const runTest = async () => {
    setTesting(true);
    setError("");
    setTestResults(null);
    try {
      const out = await fileWatchApi.testWatchDraft(draft());
      setTestResults(out.candidates);
    } catch (err) {
      setError(err.message || t("watches.drawer.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await fileWatchApi.createWatch({
        name: name.trim(), file_import_id: fileImport.id, write_mode: fileImport.write_mode,
        transport, location, pattern, pattern_type: patternType,
        poll_interval_seconds: pollIntervalSeconds,
        completeness_strategy: completeness, stable_size_delay_seconds: stableDelay, control_file_suffix: controlSuffix,
        post_process: "record_only",
        arrival_cron: effectiveCron(), arrival_grace_minutes: slaEnabled ? graceMinutes : null,
      });
      onCreated(created);
    } catch (err) {
      setError(err.message || t("watches.drawer.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={t("watches.drawer.title")} description={t("watches.drawer.description", { target: `${fileImport.target_schema}.${fileImport.target_table}` })} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <Field label={t("watches.drawer.name")}>
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>

      <Field label={t("watches.drawer.transport")}>
        <div className="seg">
          <button type="button" className={"seg-opt" + (transport === "minio" ? " selected" : "")} onClick={() => setTransport("minio")}>
            <div className="seg-role">{transport === "minio" && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}MinIO</div>
          </button>
          <button type="button" className={"seg-opt" + (transport === "local" ? " selected" : "")} onClick={() => setTransport("local")}>
            <div className="seg-role">{transport === "local" && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{t("watches.drawer.localTransport")}</div>
          </button>
        </div>
      </Field>

      {transport === "minio" ? (
        <>
          <Field label={t("watches.drawer.source")}>
            <select className="input" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
              <option value="">{t("imports.wizard.chooseSource")}</option>
              {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Field label={t("watches.drawer.bucket")}>
                <Input value={bucket} onChange={(e) => setBucket(e.target.value)} placeholder="incoming" />
              </Field>
            </div>
            <div style={{ flex: 1 }}>
              <Field label={t("watches.drawer.prefix")}>
                <Input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="admissions/" />
              </Field>
            </div>
          </div>
        </>
      ) : (
        <Field label={t("watches.drawer.localPath")}>
          <Input value={localPath} onChange={(e) => setLocalPath(e.target.value)} placeholder="/data/incoming/admissions" style={{ fontFamily: "var(--font-m)" }} />
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>{t("watches.drawer.localPathHint")}</div>
        </Field>
      )}

      <Field label={t("watches.drawer.pattern")}>
        <div style={{ display: "flex", gap: 8 }}>
          <Input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="admissions_*.csv" style={{ fontFamily: "var(--font-m)", flex: 1 }} />
          <select className="input" style={{ width: 110 }} value={patternType} onChange={(e) => setPatternType(e.target.value)}>
            <option value="glob">glob</option>
            <option value="regex">regex</option>
          </select>
        </div>
      </Field>

      <Field label={t("watches.drawer.cadence")}>
        <div className="seg" style={{ flexWrap: "wrap" }}>
          {CADENCE_PRESETS.map((p) => (
            <button key={p.value} type="button" className={"seg-opt" + (pollIntervalSeconds === p.value ? " selected" : "")} style={{ flex: "1 1 22%" }} onClick={() => setPollIntervalSeconds(p.value)}>
              <div className="seg-role">{pollIntervalSeconds === p.value && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{p.label}</div>
            </button>
          ))}
        </div>
        <Input type="number" min={60} value={pollIntervalSeconds} onChange={(e) => setPollIntervalSeconds(Number(e.target.value))} style={{ marginTop: 8, maxWidth: 160 }} />
      </Field>

      <Field label={t("watches.drawer.completeness")}>
        <select className="input" value={completeness} onChange={(e) => setCompleteness(e.target.value)}>
          <option value="stable_size">{t("watches.drawer.completenessStableSize")}</option>
          <option value="control_file">{t("watches.drawer.completenessControlFile")}</option>
          <option value="none">{t("watches.drawer.completenessNone")}</option>
        </select>
      </Field>
      {completeness === "stable_size" && (
        <Field label={t("watches.drawer.stableDelay")}>
          <Input type="number" min={1} value={stableDelay} onChange={(e) => setStableDelay(Number(e.target.value))} style={{ maxWidth: 160 }} />
        </Field>
      )}
      {completeness === "control_file" && (
        <Field label={t("watches.drawer.controlSuffix")}>
          <Input value={controlSuffix} onChange={(e) => setControlSuffix(e.target.value)} style={{ maxWidth: 160, fontFamily: "var(--font-m)" }} />
        </Field>
      )}

      <Field label={t("watches.drawer.writeMode")}>
        <div style={{ fontFamily: "var(--font-m)", fontSize: 13, color: "var(--text-muted)" }}>{fileImport.write_mode}</div>
      </Field>

      <div className="field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={slaEnabled} onChange={(e) => setSlaEnabled(e.target.checked)} />
        <label style={{ fontSize: 13.5 }}>{t("watches.drawer.slaEnable")}</label>
      </div>
      {slaEnabled && (
        <>
          <Field label={t("watches.drawer.slaMode")}>
            <div className="seg">
              <button type="button" className={"seg-opt" + (slaMode === "daily" ? " selected" : "")} onClick={() => setSlaMode("daily")}>
                <div className="seg-role">{slaMode === "daily" && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{t("watches.drawer.slaDailyPreset")}</div>
              </button>
              <button type="button" className={"seg-opt" + (slaMode === "custom" ? " selected" : "")} onClick={() => setSlaMode("custom")}>
                <div className="seg-role">{slaMode === "custom" && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{t("watches.drawer.slaCustomCron")}</div>
              </button>
            </div>
          </Field>
          {slaMode === "daily" ? (
            <Field label={t("watches.drawer.slaDailyTime")}>
              <input type="time" className="input" value={dailyTime} onChange={(e) => setDailyTime(e.target.value)} style={{ maxWidth: 160 }} />
            </Field>
          ) : (
            <Field label={t("watches.drawer.slaCronLabel")}>
              <Input value={customCron} onChange={(e) => setCustomCron(e.target.value)} placeholder="0 8 * * *" style={{ fontFamily: "var(--font-m)" }} />
            </Field>
          )}
          <Field label={t("watches.drawer.slaGrace")}>
            <Input type="number" min={0} max={1440} value={graceMinutes} onChange={(e) => setGraceMinutes(Number(e.target.value))} style={{ maxWidth: 160 }} />
          </Field>
        </>
      )}

      <div style={{ marginBottom: 14 }}>
        <Button type="button" variant="ghost" disabled={!valid || testing} onClick={runTest}>{testing ? t("watches.drawer.testing") : t("watches.drawer.test")}</Button>
      </div>

      {testResults && (
        <div style={{ marginBottom: 14 }}>
          {testResults.length === 0 ? (
            <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("watches.drawer.noCandidates")}</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {testResults.map((c, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, padding: "6px 10px", borderRadius: 6, background: "var(--bg)" }}>
                  <span className={"badge " + (c.complete ? "badge-accent" : "badge-neutral")}>{c.complete ? t("watches.complete") : t("watches.incomplete")}</span>
                  <span style={{ fontFamily: "var(--font-m)" }}>{c.name}</span>
                  <span style={{ color: "var(--text-muted)", marginLeft: "auto" }}>{c.reason || `${c.size} o`}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="modal-actions">
        <Button type="button" variant="ghost" onClick={onClose}>{t("imports.wizard.cancel")}</Button>
        <Button type="button" disabled={!valid || busy} onClick={submit}>{busy ? t("watches.drawer.creating") : t("watches.drawer.create")}</Button>
      </div>
    </Drawer>
  );
}
