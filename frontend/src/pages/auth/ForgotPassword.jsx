import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { forgotPassword } from "../../api/auth.js";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon, Logo, NetBG } from "../../components/icons.jsx";

export default function ForgotPassword() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [devLink, setDevLink] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e?.preventDefault();
    if (!email) return;
    setBusy(true);
    try {
      const res = await forgotPassword(email);
      setDevLink(res?.reset_link || null);
      setSent(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-stage theme-dark">
      <div className="auth-bg">
        <NetBG />
        <div className="auth-glow g1" />
        <div className="auth-glow g2" />
      </div>

      <div className="auth-card">
        <div className="auth-brand">
          <div className="auth-mark"><Logo /></div>
          <div>
            <div className="auth-brand-name">Data&nbsp;Plateforme</div>
            <div className="auth-brand-sub">by Delomid IT</div>
          </div>
        </div>

        {!sent ? (
          <>
            <div className="auth-eyebrow">{t("auth.recovery")}</div>
            <h1 className="auth-h1">{t("auth.forgotPasswordTitle")}</h1>
            <p className="auth-sub">{t("auth.forgotPasswordSub")}</p>
            <form onSubmit={submit}>
              <Field label={t("auth.email")}>
                <Input
                  type="email"
                  placeholder="vous@delomid.io"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  icon={Icon.mail()}
                  required
                />
              </Field>
              <Button type="submit" disabled={!email || busy}>
                {busy ? t("auth.sending") : t("auth.sendResetLink")}
              </Button>
            </form>
            <div style={{ textAlign: "center", marginTop: 18 }}>
              <Link className="link" to="/login">{t("auth.backToLogin")}</Link>
            </div>
          </>
        ) : (
          <>
            <div className="auth-ok">
              <div className="auth-ok-ring">{Icon.check({ width: 26, height: 26 })}</div>
              <h1 className="auth-h1">{t("auth.checkYourInbox")}</h1>
              <p className="auth-sub" style={{ marginBottom: 6 }}>
                {t("auth.resetLinkSentTo")}<br />
                <b style={{ color: "#fff", fontFamily: "var(--font-m)", fontSize: 13 }}>{email}</b>
              </p>
              {devLink && (
                <p className="auth-sub" style={{ fontSize: 11.5, wordBreak: "break-all" }}>
                  {t("auth.devModeLink")} <Link className="link" to={devLink.replace(window.location.origin, "")}>{devLink}</Link>
                </p>
              )}
            </div>
            <Button style={{ marginTop: 8 }} onClick={() => navigate("/login")}>{t("auth.goToLogin")}</Button>
          </>
        )}
      </div>
    </div>
  );
}
