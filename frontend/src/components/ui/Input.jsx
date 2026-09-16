import { useState } from "react";
import { Icon } from "../icons.jsx";

export function Field({ label, children }) {
  return (
    <div className="field">
      {label && <label className="field-label">{label}</label>}
      {children}
    </div>
  );
}

export function Input({ icon, className = "", ...props }) {
  return (
    <div className="input-wrap">
      <input className={`input ${icon ? "has-icon" : ""} ${className}`} {...props} />
      {icon && <span className="input-icon">{icon}</span>}
    </div>
  );
}

export function PasswordInput({ className = "", ...props }) {
  const [show, setShow] = useState(false);
  return (
    <div className="input-wrap">
      <input className={`input has-icon has-trailing ${className}`} type={show ? "text" : "password"} {...props} />
      <span className="input-icon">{Icon.lock()}</span>
      <button
        type="button"
        className="input-trailing"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Masquer le mot de passe" : "Afficher le mot de passe"}
      >
        {show ? Icon.eyeOff() : Icon.eye()}
      </button>
    </div>
  );
}
