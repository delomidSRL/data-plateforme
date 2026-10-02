const VARIANT_CLASS = { primary: "btn-primary", ghost: "btn-ghost", danger: "btn-danger" };

export function Button({ variant = "primary", className = "", ...props }) {
  const base = VARIANT_CLASS[variant] || "btn-ghost";
  return <button className={`${base} ${className}`} {...props} />;
}
