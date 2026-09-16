import { useTranslation } from "react-i18next";
import { Field } from "../../components/ui/Input.jsx";

const PRESETS = ["@hourly", "@daily", "@weekly"];

// Same shape as the field bounds services/schedule.py validates against — light client-side
// feedback only; the backend stays the authoritative validator (never trust this alone).
const FIELD_BOUNDS = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]];

function validPart(part, lo, hi) {
  const m = /^(\*|\d+(?:-\d+)?)(?:\/(\d+))?$/.exec(part);
  if (!m) return false;
  const [, base, step] = m;
  if (step !== undefined && Number(step) <= 0) return false;
  if (base === "*") return true;
  if (base.includes("-")) {
    const [start, end] = base.split("-").map(Number);
    return start >= lo && start <= hi && end >= lo && end <= hi && start <= end;
  }
  const n = Number(base);
  return n >= lo && n <= hi;
}

export function isValidSchedule(value) {
  const v = (value || "").trim();
  if (!v) return false;
  if (PRESETS.includes(v)) return true;
  if (v.startsWith("@")) return false;
  const fields = v.split(/\s+/);
  if (fields.length !== 5) return false;
  return fields.every((f, i) => validPart2(f, FIELD_BOUNDS[i]));
}

function validPart2(field, [lo, hi]) {
  return field.split(",").every((p) => validPart(p, lo, hi));
}

// "Traduction en clair" (spec §4.5) — covers the presets and the two most common shapes
// (fixed daily time, fixed weekly time); anything more exotic just shows the raw cron back,
// which is honest rather than guessing at a description.
export function describeSchedule(value, t) {
  const weekdays = t("medallion.schedule.weekdays", { returnObjects: true });
  const v = (value || "").trim();
  if (v === "@hourly") return t("medallion.schedule.descHourly");
  if (v === "@daily") return t("medallion.schedule.descDaily", { time: "00:00" });
  if (v === "@weekly") return t("medallion.schedule.descWeekly", { day: weekdays[0], time: "00:00" });
  if (!isValidSchedule(v)) return null;
  const [min, hour, dom, month, dow] = v.split(/\s+/);
  const time = `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (dom === "*" && month === "*" && dow === "*" && /^\d+$/.test(min) && /^\d+$/.test(hour)) {
    return t("medallion.schedule.descDaily", { time });
  }
  if (dom === "*" && month === "*" && /^\d+$/.test(dow) && /^\d+$/.test(min) && /^\d+$/.test(hour)) {
    return t("medallion.schedule.descWeekly", { day: weekdays[Number(dow) % 7], time });
  }
  return t("medallion.schedule.descCustom", { cron: v });
}

// value: null (manuel) | string (cron ou preset). onChange(nextValueOrNull).
export default function ScheduleField({ value, onChange, disabled = false }) {
  const { t } = useTranslation();
  const scheduled = value != null;
  const description = scheduled ? describeSchedule(value, t) : null;
  const invalid = scheduled && !isValidSchedule(value);

  return (
    <Field label={t("medallion.schedule.label")}>
      <div className="seg" style={{ marginBottom: scheduled ? 10 : 0 }}>
        <button type="button" disabled={disabled} className={"seg-opt" + (!scheduled ? " selected" : "")} onClick={() => onChange(null)}>
          <div className="seg-role">{t("medallion.schedule.manual")}</div>
        </button>
        <button type="button" disabled={disabled} className={"seg-opt" + (scheduled ? " selected" : "")} onClick={() => onChange(value || "@daily")}>
          <div className="seg-role">{t("medallion.schedule.scheduled")}</div>
        </button>
      </div>

      {scheduled && (
        <>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            {PRESETS.map((p) => (
              <button
                key={p} type="button" disabled={disabled}
                className={"badge" + (value === p ? " badge-accent" : " badge-neutral")}
                style={{ cursor: disabled ? "default" : "pointer", border: "none" }}
                onClick={() => onChange(p)}
              >
                {p}
              </button>
            ))}
          </div>
          <input
            className="input" style={{ fontFamily: "var(--font-m)" }} placeholder="0 6 * * *"
            value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}
          />
          <div style={{ fontSize: 11.5, marginTop: 6, color: invalid ? "var(--danger)" : "var(--text-muted)" }}>
            {invalid ? t("medallion.schedule.invalid") : description}
          </div>
        </>
      )}
    </Field>
  );
}
