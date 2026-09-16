import { useTranslation } from "react-i18next";
import { Icon } from "./icons.jsx";

const LANGUAGES = [
  { code: "fr", label: "FR" },
  { code: "en", label: "EN" },
  { code: "nl", label: "NL" },
];

export default function LanguageSwitcher() {
  const { t, i18n } = useTranslation();
  const current = i18n.resolvedLanguage || i18n.language || "fr";

  return (
    <div className="lang-switcher">
      <div className="nav-label" style={{ display: "flex", alignItems: "center", gap: 6, padding: "16px 10px 7px" }}>
        {Icon.globe({ width: 12, height: 12 })} {t("sidebar.language")}
      </div>
      <div className="lang-switcher-options">
        {LANGUAGES.map((l) => (
          <button
            key={l.code}
            className={"lang-option" + (current === l.code ? " active" : "")}
            onClick={() => i18n.changeLanguage(l.code)}
            aria-pressed={current === l.code}
          >
            {l.label}
          </button>
        ))}
      </div>
    </div>
  );
}
