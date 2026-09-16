"""add record_linkage ml objective and seed template

Revision ID: 68efc079b374
Revises: 76385386004c
Create Date: 2026-07-11 17:42:24.490001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '68efc079b374'
down_revision: Union[str, None] = '76385386004c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ml_templates_table = sa.table(
    "ml_templates",
    sa.column("name", sa.String),
    sa.column("ml_objective", postgresql.ENUM(
        'none', 'anomaly', 'scoring', 'forecast', 'clustering', 'record_linkage',
        name="medallion_ml_objective", create_type=False,
    )),
    sa.column("description", sa.Text),
    sa.column("python_code", sa.Text),
    sa.column("expected_inputs", postgresql.JSONB),
    sa.column("output_columns", postgresql.JSONB),
)

RECORD_LINKAGE_CODE = '''"""
Template : Rapprochement de patients (record linkage — Fellegi-Sunter)
ml_objective : record_linkage

Identifie les fiches qui désignent LE MÊME patient malgré des saisies
divergentes (DOB erroné, nom tronqué, inversion prénom/nom, fautes de frappe).
Ce que le SQL déterministe (GROUP BY) ne sait pas faire.

Sortie : un identifiant unifié + un score de confiance + un statut de décision
(sûr / à_réviser / distinct). On ne fusionne JAMAIS automatiquement sur un
score douteux — sécurité patient : la zone grise part en file de révision humaine.

Contrat d'exécution fourni par le nœud (§5.4) :
    read_table(name) -> DataFrame
    write_table(df, name)
Aucune connexion, aucun credential dans ce code (§2).
"""
import pandas as pd
import recordlinkage as rl
from recordlinkage.preprocessing import clean, phonetic
import networkx as nx

# =========================================================================
# 1. Paramètres — substitués depuis le formulaire (expected_inputs, §6.5)
# =========================================================================
TABLE_ENTREE   = "{{table}}"          # table patients amont (silver/gold)
TABLE_SORTIE   = "{{output_table}}"   # table gold produite
COL_ID         = "{{col_id}}"         # identifiant de la FICHE (pas du patient)

COL_NOM        = "{{col_nom}}"
COL_PRENOM     = "{{col_prenom}}"
COL_DOB        = "{{col_dob}}"        # date de naissance
COL_CIN        = "{{col_cin}}"        # identifiant fort (CIN / passeport), peut être ""

# Seuils — conservateurs par défaut (cf. sécurité patient)
SEUIL_MATCH    = {{seuil_match}}      # ex. 0.90 : au-dessus = même patient (sûr)
SEUIL_REVISION = {{seuil_revision}}   # ex. 0.60 : entre les deux = à réviser

# =========================================================================
# 2. Lecture + normalisation légère
# =========================================================================
df = read_table(TABLE_ENTREE).set_index(COL_ID)

df["_nom"]      = clean(df[COL_NOM].astype(str))
df["_prenom"]   = clean(df[COL_PRENOM].astype(str))
df["_dob"]      = pd.to_datetime(df[COL_DOB], errors="coerce")
# clés de blocage : code phonétique du nom (absorbe les fautes) + année de naissance
df["_nom_phon"] = phonetic(df["_nom"], method="soundex")
df["_annee"]    = df["_dob"].dt.year

# =========================================================================
# 3. Blocking — OBLIGATOIRE (§0 : mono-conteneur, volumes raisonnables)
# =========================================================================
# Sans blocking : n(n-1)/2 paires -> 100k fiches = 5 milliards de comparaisons.
# On ne compare que les fiches partageant le code phonétique du nom OU l'année
# de naissance (union de 2 blocs => on rate moins de vrais doublons qu'avec un
# seul critère strict, tout en gardant le volume gérable en mémoire).
indexer = rl.Index()
indexer.block("_nom_phon")
indexer.block("_annee")
paires = indexer.index(df)

# =========================================================================
# 4. Comparaison champ par champ
# =========================================================================
c = rl.Compare()
c.string("_nom",    "_nom",    method="jarowinkler", label="nom")
c.string("_prenom", "_prenom", method="jarowinkler", label="prenom")
c.date("_dob",      "_dob",                          label="dob")
if COL_CIN:
    c.exact(COL_CIN, COL_CIN, label="cin")
features = c.compute(paires, df)

# =========================================================================
# 5. Scoring Fellegi-Sunter (ECM : poids m/u appris SANS étiquettes)
# =========================================================================
# L'ECM apprend le poids de chaque champ : un accord sur un champ rare (CIN)
# pèse bien plus qu'un accord sur un prénom courant. C'est CE mécanisme qui
# maintient un score élevé quand le DOB est faux mais que le reste concorde.
ecm = rl.ECMClassifier(binarize=0.85)
ecm.fit(features)
proba = ecm.prob(features)                       # score 0–1 par paire
scores = proba.rename("score_match").reset_index()
scores.columns = ["id_a", "id_b", "score_match"]

# =========================================================================
# 6. Regroupement en entités (composantes connexes) — conservateur
# =========================================================================
# On ne relie QUE les paires "sûres" (>= SEUIL_MATCH) pour former les groupes.
# Une paire en zone grise ne fusionne rien automatiquement : elle est signalée.
g = nx.Graph()
g.add_nodes_from(df.index)                        # chaque fiche = 1 nœud (singletons inclus)
srs = scores[scores["score_match"] >= SEUIL_MATCH]
g.add_edges_from(srs[["id_a", "id_b"]].itertuples(index=False, name=None))

id_unifie = {}
for gid, membres in enumerate(nx.connected_components(g)):
    for fiche in membres:
        id_unifie[fiche] = f"P{gid:07d}"

# meilleur score de rapprochement obtenu par chaque fiche (dans les 2 sens)
best = (
    pd.concat([
        scores[["id_a", "score_match"]].rename(columns={"id_a": "id"}),
        scores[["id_b", "score_match"]].rename(columns={"id_b": "id"}),
    ])
    .groupby("id")["score_match"].max()
)

# =========================================================================
# 7. Construction + écriture de la table gold
# =========================================================================
out = df.drop(columns=[col for col in df.columns if col.startswith("_")]).copy()
out["id_patient_unifie"] = out.index.map(id_unifie)
out["score_match"]       = out.index.map(best).fillna(0.0)

def _statut(s):
    if s >= SEUIL_MATCH:    return "sûr"
    if s >= SEUIL_REVISION: return "à_réviser"
    return "distinct"

out["statut_match"] = out["score_match"].map(_statut)

write_table(out.reset_index(), TABLE_SORTIE)
'''

RECORD_LINKAGE_TEMPLATE = {
    "name": "Rapprochement de patients (record linkage — Fellegi-Sunter)",
    "ml_objective": "record_linkage",
    "description": (
        "Identifie via Fellegi-Sunter (recordlinkage) les fiches désignant probablement le même "
        "patient malgré des saisies divergentes (DOB erroné, nom tronqué, fautes de frappe). "
        "Ne fusionne jamais automatiquement en zone grise — statut « à_réviser » envoyé en revue humaine."
    ),
    "python_code": RECORD_LINKAGE_CODE,
    "expected_inputs": [
        {"key": "{{table}}", "label": "Table d'entrée (patients)", "type": "table"},
        {"key": "{{output_table}}", "label": "Table de sortie (gold)", "type": "table", "is_output": True},
        {"key": "{{col_id}}", "label": "Colonne identifiant de fiche", "type": "column"},
        {"key": "{{col_nom}}", "label": "Colonne nom", "type": "column"},
        {"key": "{{col_prenom}}", "label": "Colonne prénom", "type": "column"},
        {"key": "{{col_dob}}", "label": "Colonne date de naissance", "type": "column"},
        {"key": "{{col_cin}}", "label": "Colonne CIN / passeport (optionnel, laisser vide si absent)", "type": "column"},
        {"key": "{{seuil_match}}", "label": "Seuil de correspondance sûre", "type": "number", "default": "0.90"},
        {"key": "{{seuil_revision}}", "label": "Seuil de révision (zone grise)", "type": "number", "default": "0.60"},
    ],
    "output_columns": ["(colonnes d'origine)", "id_patient_unifie", "score_match", "statut_match"],
}


def upgrade() -> None:
    # Postgres forbids using a freshly-added enum value in the same transaction that
    # added it — autocommit_block() commits the ALTER TYPE on its own before the
    # bulk_insert below runs in a fresh transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE medallion_ml_objective ADD VALUE IF NOT EXISTS 'record_linkage'")

    op.bulk_insert(ml_templates_table, [RECORD_LINKAGE_TEMPLATE])


def downgrade() -> None:
    op.execute("DELETE FROM ml_templates WHERE ml_objective = 'record_linkage'")
    # Postgres enum values can't be dropped without rebuilding the type — left in place.
