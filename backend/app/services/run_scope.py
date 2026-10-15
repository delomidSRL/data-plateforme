"""Bouton RUN par nœud — résout un clic sur un nœud du canvas en (a) le `conf` du dagRun
Airflow qui borne l'exécution à "ce nœud et tout ce qui est en amont", et (b) une carte
nœud -> task_id Airflow (+ nœud -> nom de modèle dbt, pour les nœuds qui partagent une tâche
Airflow — voir ci-dessous) pour que le canvas puisse suivre le statut de chaque nœud après le
déclenchement. Côté exécution (voir dag.py.j2 / dbt_run.py.j2 / ingest_bronze.py.j2/
ml_transform.py.j2) : un `conf` vide/absent (run complet existant) ne change rien au
comportement d'aujourd'hui — ce module est la seule source de vérité qui le remplit.
"""
from dataclasses import dataclass, field

from app.models.medallion import MedallionDataset, MedallionLayer, TransformType
from app.services.dag_render import _dataset_table_name, _safe_identifier

STAGE_PREFIX = {"unpacked": "01_unpacked", "typed": "02_typed"}


@dataclass
class RunScope:
    target_key: str
    target_layer: str
    target_node_names: list[str]
    silver_models: list[str]
    gold_models: list[str]
    node_task_map: dict[str, str]
    # Only for nodes whose task_id is dbt_run_silver/dbt_run_gold (bronze/ML nodes have their
    # own exact Airflow task already, no model name needed) — lets the node-status endpoint
    # resolve a precise per-model state from dbt's own incremental progress/run_results
    # instead of the shared task's one coarse state (see dbt_run.py.j2's progress_file).
    node_model_map: dict[str, str] = field(default_factory=dict)

    def conf(self) -> dict:
        return {
            "target_layer": self.target_layer,
            "target_node_names": self.target_node_names,
            "silver_models": self.silver_models,
            "gold_models": self.gold_models,
        }


def _upstream_closure(by_id: dict[int, MedallionDataset], start_id: int) -> list[MedallionDataset]:
    visited: set[int] = set()
    stack = [start_id]
    while stack:
        did = stack.pop()
        if did in visited:
            continue
        visited.add(did)
        ds = by_id.get(did)
        if ds:
            stack.extend(ds.upstream_dataset_ids or [])
    return [by_id[i] for i in visited if i in by_id]


def _add_dataset_to_maps(d: MedallionDataset, node_task_map: dict[str, str], node_model_map: dict[str, str]) -> None:
    if d.layer == MedallionLayer.bronze:
        node_task_map[str(d.id)] = f"ingest_bronze_{_safe_identifier(d.name)}"
    elif d.layer == MedallionLayer.gold and d.transform_type == TransformType.python:
        node_task_map[str(d.id)] = f"ml_{_safe_identifier(d.name)}"
    elif d.layer == MedallionLayer.silver:
        node_task_map[str(d.id)] = "dbt_run_silver"
        node_model_map[str(d.id)] = _dataset_table_name(d)
    else:
        node_task_map[str(d.id)] = "dbt_run_gold"
        node_model_map[str(d.id)] = _dataset_table_name(d)


def _add_structuration_stages(
    bronze: MedallionDataset, stages: list[str],
    silver_models: set[str], node_task_map: dict[str, str], node_model_map: dict[str, str],
) -> None:
    for s in stages:
        prefix = STAGE_PREFIX[s]
        model_name = f"{prefix}_{bronze.name}"
        node_key = f"silver-{s}-{bronze.id}"
        silver_models.add(model_name)
        node_task_map[node_key] = "dbt_run_silver"
        node_model_map[node_key] = model_name


def compute_full_scope(datasets: list[MedallionDataset], structured_bronze_ids: set[int] = frozenset()) -> dict[str, dict[str, str]]:
    """node_task_map/node_model_map for an UNSCOPED run ("Exécuter maintenant" / schedule) —
    every real dataset, plus every structured bronze's structuration stages, touches
    everything by definition. UX ask — per-node run history must show these runs too, not
    only RUN-button clicks, so they need a node_plan populated exactly like a targeted one."""
    node_task_map: dict[str, str] = {}
    node_model_map: dict[str, str] = {}
    silver_models: set[str] = set()  # unused by the caller, _add_structuration_stages needs it
    for d in datasets:
        _add_dataset_to_maps(d, node_task_map, node_model_map)
        if d.layer == MedallionLayer.bronze and d.id in structured_bronze_ids:
            _add_structuration_stages(d, ["unpacked", "typed"], silver_models, node_task_map, node_model_map)
    return {"nodes": node_task_map, "models": node_model_map}


def compute_run_scope(
    datasets: list[MedallionDataset], dataset: MedallionDataset, stage: str | None,
    structured_bronze_ids: set[int] = frozenset(),
) -> RunScope:
    """`dataset` is always a real row — for the 01_unpacked/02_typed synthetic canvas nodes
    (never their own MedallionDataset, see workspace_sync.resolve()) the caller passes the
    BRONZE dataset itself plus `stage`, mirroring GET .../structuration/preview's own
    (dataset_id=bronze_id, stage) convention.

    `structured_bronze_ids` (payload_structure.bulk_structured) — any bronze dataset in the
    closure that has a saved structuration contract always has its 01_unpacked_<name>/
    02_typed_<name> built before any REAL downstream silver/gold model that reads it (dbt's
    own ref() graph requires it); UX ask — "depuis le début jusqu'à ce nœud" must show that
    whole chain lighting up too, not just the real registered datasets on top of it."""
    by_id = {d.id: d for d in datasets}
    closure = _upstream_closure(by_id, dataset.id)

    bronze_names = {d.name for d in closure if d.layer == MedallionLayer.bronze}
    ml_names = {_dataset_table_name(d) for d in closure if d.layer == MedallionLayer.gold and d.transform_type == TransformType.python}
    silver_models = {_dataset_table_name(d) for d in closure if d.layer == MedallionLayer.silver}
    gold_models = {_dataset_table_name(d) for d in closure if d.layer == MedallionLayer.gold and d.transform_type != TransformType.python}

    node_task_map: dict[str, str] = {}
    node_model_map: dict[str, str] = {}
    for d in closure:
        _add_dataset_to_maps(d, node_task_map, node_model_map)

    if stage:
        # Direct click on a synthetic stage node itself — only as far as that one stage.
        stages = ["unpacked"] if stage == "unpacked" else ["unpacked", "typed"]
        _add_structuration_stages(dataset, stages, silver_models, node_task_map, node_model_map)
        target_layer = "silver"
        target_key = f"structuration:{dataset.id}:{stage}"
    else:
        # Any real downstream dataset always needs the FULL typed chain of every structured
        # bronze it (transitively) reads — dbt's ref('02_typed_<name>') already requires it.
        for b in closure:
            if b.layer == MedallionLayer.bronze and b.id in structured_bronze_ids:
                _add_structuration_stages(b, ["unpacked", "typed"], silver_models, node_task_map, node_model_map)
        target_layer = dataset.layer.value
        target_key = f"dataset:{dataset.id}"

    return RunScope(
        target_key=target_key,
        target_layer=target_layer,
        target_node_names=sorted(bronze_names | ml_names),
        silver_models=sorted(silver_models),
        gold_models=sorted(gold_models),
        node_task_map=node_task_map,
        node_model_map=node_model_map,
    )


def bronze_closure(datasets: list[MedallionDataset], target_ids: list[int] | None) -> list[MedallionDataset]:
    """Popup de pré-lancement (run-preflight) — les sources bronze dont dépend réellement un
    run donné : `target_ids` vide/None = tout le projet (run complet/schedule), sinon l'union
    des fermetures amont de chaque cible (même notion que compute_run_scope/merge_scopes, mais
    on ne veut ici que les datasets bronze eux-mêmes, pas le `conf` Airflow)."""
    if not target_ids:
        return [d for d in datasets if d.layer == MedallionLayer.bronze]
    by_id = {d.id: d for d in datasets}
    seen: dict[int, MedallionDataset] = {}
    for tid in target_ids:
        for d in _upstream_closure(by_id, tid):
            if d.layer == MedallionLayer.bronze:
                seen[d.id] = d
    return list(seen.values())


def merge_scopes(scopes: list[RunScope]) -> RunScope:
    """Plusieurs pipelines choisies à la fois (sélection multiple sur le canvas) — un seul
    dagRun borné à l'UNION de leurs chaînes amont respectives, pas un run par pipeline.
    `target_layer` n'a plus de sens pour une sélection mélangeant des couches : aucun
    run_dbt()/ingest_bronze/ml_transform ne le lit (seuls target_node_names/silver_models/
    gold_models pilotent le scoping au runtime) — la valeur ici n'est qu'informative."""
    target_node_names: set[str] = set()
    silver_models: set[str] = set()
    gold_models: set[str] = set()
    node_task_map: dict[str, str] = {}
    node_model_map: dict[str, str] = {}
    for s in scopes:
        target_node_names.update(s.target_node_names)
        silver_models.update(s.silver_models)
        gold_models.update(s.gold_models)
        node_task_map.update(s.node_task_map)
        node_model_map.update(s.node_model_map)
    return RunScope(
        target_key="selection:" + ",".join(sorted(s.target_key for s in scopes)),
        target_layer="mixed",
        target_node_names=sorted(target_node_names),
        silver_models=sorted(silver_models),
        gold_models=sorted(gold_models),
        node_task_map=node_task_map,
        node_model_map=node_model_map,
    )
