"""Module 16 extension §6 — self-contained, runnable-off-platform dbt project export. Pure
serialization: `dbt_test_renderer`/`dbt_project` are replayed (deterministic, no AI, no new
SQL) and the resulting tree is zipped. Never includes `profiles.yml` or any warehouse
credential (§8 "aucun secret nouveau") — the README documents the expected profile shape
instead, for the client to fill in with their own connection."""
import io
import zipfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.models.payload_structuration import PayloadStructuration
from app.services import dbt_project, dbt_test_renderer


def inventory_preview(db: Session, project_id: int, datasets: list[MedallionDataset]) -> dict[str, int]:
    """§6 frontend — "un aperçu de l'inventaire... avant téléchargement": the same count the
    export itself would produce, without building/zipping any file."""
    rendered = dbt_test_renderer.render_for_export(db, project_id, datasets)
    return _inventory(datasets, rendered)


def _inventory(datasets: list[MedallionDataset], rendered) -> dict[str, int]:
    """Per-layer test count — Tier B (native, always in ds.tests) + Tier A/C (rendered,
    active-check-driven). Computed from the SAME data the files themselves come from, never
    by re-parsing the generated YAML."""
    layer_of = {d.id: d.layer.value for d in datasets}
    counts: dict[str, int] = {}
    for ds in datasets:
        n = len(ds.tests or [])
        if n:
            counts[ds.layer.value] = counts.get(ds.layer.value, 0) + n
    for dataset_id, by_column in rendered.schema_by_dataset.items():
        layer = layer_of.get(dataset_id)
        if layer is None:
            continue
        n = sum(len(v) for v in by_column.values())
        counts[layer] = counts.get(layer, 0) + n
    for path in rendered.singular_files:
        # A singular test's dataset isn't in its path — cheap enough to not bother threading
        # it through; these are rare (Tier C) and shown as their own line in the README.
        counts["_singular_tests"] = counts.get("_singular_tests", 0) + 1
    return counts


def _readme(project: MedallionProject, inventory: dict[str, int]) -> str:
    layer_lines = "\n".join(f"- **{layer}** : {n} test(s)" for layer, n in inventory.items() if layer != "_singular_tests")
    singular_n = inventory.get("_singular_tests", 0)
    return f"""# {project.name} — projet dbt exporté

Généré par Data Plateforme (export du projet dbt autoportant). Ce projet est **standard et
autoportant** : il tourne hors plateforme, sans aucune dépendance à Data Plateforme. Aucune
ligne de données n'est incluse — uniquement les modèles, la configuration des sources, et les
tests (config/métadonnée seule).

## Démarrage

1. Créez un `profiles.yml` (non fourni ici — jamais de credential dans cet export) sur ce
   modèle, à adapter avec votre propre connexion Postgres :

```yaml
{project.dbt_project_name}:
  target: {project.target.value}
  outputs:
    {project.target.value}:
      type: postgres
      host: <votre_host>
      port: 5432
      user: <votre_utilisateur>
      password: <votre_mot_de_passe>
      dbname: <votre_base>
      schema: silver
      threads: 4
```

2. `dbt deps` (installe dbt-expectations/dbt-utils, versions épinglées dans `packages.yml`)
3. `dbt build` — ou `dbt test` seul pour ne rejouer que les contrôles qualité.

## Inventaire des tests par couche

{layer_lines or "- aucun test structurel/contrat déclaré."}
{f"- **tests singuliers (Tier C)** : {singular_n}" if singular_n else ""}
"""


def build_export(
    db: Session, project: MedallionProject, datasets: list[MedallionDataset],
    structurations: dict[int, PayloadStructuration] | None = None,
) -> tuple[bytes, dict[str, int]]:
    """Returns (zip_bytes, inventory). `dbt_project.generate_project_files(..., for_export=True)`
    does all the real work (§6 "l'export ne fabrique rien de neuf") — this only adds the
    README and serializes to a .zip."""
    files = dbt_project.generate_project_files(db, project, datasets, warehouse=None, structurations=structurations, for_export=True)
    rendered = dbt_test_renderer.render_for_export(db, project.id, datasets)
    inventory = _inventory(datasets, rendered)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = project.dbt_project_name
        for rel_path, content in files.items():
            zf.writestr(f"{prefix}/{rel_path}", content)
        zf.writestr(f"{prefix}/README.md", _readme(project, inventory))
    return buf.getvalue(), inventory
