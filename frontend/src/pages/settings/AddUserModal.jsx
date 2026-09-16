import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/ui/Modal.jsx";
import { Field, Input, PasswordInput } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import * as usersApi from "../../api/users.js";

export default function AddUserModal({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("engineer");
  const [method, setMethod] = useState("invite");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const valid = name.trim() && /\S+@\S+\.\S+/.test(email) && (method === "invite" || password.length >= 8);

  const submit = async (e) => {
    e?.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const created = await usersApi.createUser({
        name: name.trim(),
        email: email.trim(),
        role,
        method,
        password: method === "password" ? password : undefined,
      });
      onCreated(created);
    } catch (err) {
      setError(err.message || t("settings.users.add.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("settings.users.add.title")} description={t("settings.users.add.description")} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <form onSubmit={submit}>
        <Field label={t("settings.users.add.fullName")}>
          <Input placeholder={t("settings.users.add.fullNamePlaceholder")} value={name} onChange={(e) => setName(e.target.value)} icon={Icon.user()} />
        </Field>

        <Field label={t("settings.users.add.email")}>
          <Input type="email" placeholder="amine@delomid.io" value={email} onChange={(e) => setEmail(e.target.value)} icon={Icon.mail()} />
        </Field>

        <Field label={t("settings.users.add.role")}>
          <div className="seg">
            <button type="button" className={"seg-opt" + (role === "admin" ? " selected" : "")} onClick={() => setRole("admin")}>
              <div className="seg-role">{role === "admin" && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{t("settings.users.admin")}</div>
              <div className="seg-hint">{t("settings.users.add.adminHint")}</div>
            </button>
            <button type="button" className={"seg-opt" + (role === "engineer" ? " selected" : "")} onClick={() => setRole("engineer")}>
              <div className="seg-role">{role === "engineer" && <span style={{ color: "var(--ember)" }}>{Icon.check({ width: 14, height: 14 })}</span>}{t("settings.users.engineer")}</div>
              <div className="seg-hint">{t("settings.users.add.engineerHint")}</div>
            </button>
          </div>
        </Field>

        <Field label={t("settings.users.add.initialAccess")}>
          <div className="seg">
            <button type="button" className={"seg-opt" + (method === "invite" ? " selected" : "")} onClick={() => setMethod("invite")}>
              <div className="seg-role">{t("settings.users.add.sendInvite")}</div>
              <div className="seg-hint">{t("settings.users.add.sendInviteHint")}</div>
            </button>
            <button type="button" className={"seg-opt" + (method === "password" ? " selected" : "")} onClick={() => setMethod("password")}>
              <div className="seg-role">{t("settings.users.add.setPassword")}</div>
              <div className="seg-hint">{t("settings.users.add.setPasswordHint")}</div>
            </button>
          </div>
        </Field>

        {method === "password" && (
          <Field label={t("settings.users.add.tempPassword")}>
            <PasswordInput placeholder={t("settings.users.add.tempPasswordPlaceholder")} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        )}

        <div className="modal-actions">
          <Button type="button" variant="ghost" onClick={onClose}>{t("settings.users.add.cancel")}</Button>
          <Button type="submit" disabled={!valid || busy}>{busy ? t("settings.users.add.creating") : t("settings.users.add.createAccount")}</Button>
        </div>
      </form>
    </Modal>
  );
}
