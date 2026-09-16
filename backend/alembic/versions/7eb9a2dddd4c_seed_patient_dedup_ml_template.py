"""seed patient dedup ml template

Revision ID: 7eb9a2dddd4c
Revises: 68efc079b374
Create Date: 2026-07-11 18:15:37.626152

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7eb9a2dddd4c'
down_revision: Union[str, None] = '68efc079b374'
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

DEDUP_CODE = '''"""
=============================================================================
 Template ML — Déduplication de patients (sans identifiant fort)
 Data Plateforme by Delomid IT  ·  ml_objective = record_linkage
=============================================================================
Problème : un même patient réel apparaît sous plusieurs COD_BENEF à cause de
saisies divergentes (nom tronqué, DOB faux, prénom manquant, inversion
nom/prénom, fautes de frappe). Le SQL déterministe (GROUP BY) ne sait pas
rapprocher ces fiches. Ici : score de confiance probabiliste + décision.

Contrainte : AUCUN identifiant fort (pas de CIN). On exploite au maximum les
seules colonnes disponibles : NOM, PRENOM, DATE_NAI, SEXE, GSM.

Sortie (table gold) :
  id_patient_unifie · score_match · statut_match (sûr/à_réviser/distinct)
  · est_reference (fiche pivot du groupe) · taille_groupe · motif_match

Contrat du nœud (§5.4) : read_table / write_table. Aucun credential ici (§2).
Compute : mono-conteneur pandas/scikit-learn (§0). Le BLOCKING garde le
volume gérable — sans lui, n² paires = ingérable.

Dépendances à ajouter aux images Jupyter ET Airflow (§1, §5.4) :
    recordlinkage · jellyfish · networkx
=============================================================================
"""
import re
import unicodedata
import numpy as np
import pandas as pd
import jellyfish
import recordlinkage as rl
import networkx as nx

# =========================================================================
# 0. Paramètres — substitués depuis le formulaire (expected_inputs, §6.5)
# =========================================================================
TABLE_ENTREE   = "{{table}}"
TABLE_SORTIE   = "{{output_table}}"
COL_ID         = "{{col_id}}"            # clé UNIQUE de ligne (à confirmer)

COL_ETAT       = "{{col_etat}}"          # colonne de statut du dossier (laisser vide si non applicable)
VAL_ETAT_EXCLU = "{{val_etat_exclu}}"    # valeur à exclure (ex: dossiers sortis/supprimés)

# Seuils de décision — conservateurs (un mauvais merge en santé est dangereux)
SEUIL_AUTO     = {{seuil_auto}}                  # >= : fusion automatique (statut "sûr")
SEUIL_REVISION = {{seuil_revision}}              # [rev, auto[ : file de révision humaine
TAILLE_MAX_AUTO = {{taille_max_auto}}            # au-delà, le groupe part en révision (anti-chaînage)
RARE_MAX       = {{rare_max}}                    # un nom vu <= 4 fois est "rare" (pèse plus)

# Dates sentinelles (fausses valeurs par défaut des agents) -> neutralisées
DATES_SENTINELLES = {"1900-01-01", "1970-01-01", "0001-01-01", "9999-12-31"}

# =========================================================================
# 1. Normalisation — le levier n°1 du rappel
# =========================================================================
_PARTICULES = {"ben", "bel", "bou", "el", "al", "abou", "abd"}
_TRANSLIT = {  # variantes de translittération arabe fréquentes -> forme pivot
    "mohammed": "mohamed", "muhammad": "mohamed", "mhamed": "mohamed",
    "hammed": "mohamed", "ahmad": "ahmed", "amed": "ahmed",
    "youssef": "yousef", "yusuf": "yousef", "yousuf": "yousef",
    "fatima": "fatma", "khaled": "khalid", "mahmoud": "mahmod",
}

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))

def normaliser_nom(v) -> str:
    if pd.isna(v):
        return ""
    s = _strip_accents(str(v)).lower()
    s = re.sub(r"[^a-z\\s]", " ", s)          # ne garde que lettres + espaces
    toks = [_TRANSLIT.get(t, t) for t in s.split() if t]
    return " ".join(toks).strip()

def cle_phonetique(nom: str) -> str:
    # code phonétique sur le token le plus long hors particules (blocking)
    toks = [t for t in nom.split() if t not in _PARTICULES] or nom.split()
    if not toks:
        return ""
    principal = max(toks, key=len)
    try:
        return jellyfish.metaphone(principal)
    except Exception:
        return principal[:4]

def tokens_tries(nom_complet: str) -> frozenset:
    # ensemble de tokens : robuste à l'inversion nom<->prénom et au prénom manquant
    return frozenset(t for t in nom_complet.split()
                     if t not in _PARTICULES and len(t) > 1)

def normaliser_gsm(v) -> str:
    if pd.isna(v):
        return ""
    chiffres = re.sub(r"\\D", "", str(v))
    return chiffres[-8:] if len(chiffres) >= 8 else ""   # format TN, absorbe +216/0/espaces

# =========================================================================
# 2. Lecture + préparation
# =========================================================================
df = read_table(TABLE_ENTREE)
if COL_ETAT:
    df = df[df[COL_ETAT] != VAL_ETAT_EXCLU].copy()       # dossiers actifs uniquement
else:
    df = df.copy()
df = df.set_index(COL_ID)

df["_nom"]     = df["{{col_nom}}"].map(normaliser_nom)
df["_prenom"]  = df["{{col_prenom}}"].map(normaliser_nom)
df["_full"]    = (df["_nom"] + " " + df["_prenom"]).str.strip()
df["_tokens"]  = df["_full"].map(tokens_tries)
df["_gsm"]     = df["{{col_gsm}}"].map(normaliser_gsm)
df["_sexe"]    = df["{{col_sexe}}"].astype(str).str.upper().str[0]

_dob = pd.to_datetime(df["{{col_dob}}"], errors="coerce")
_sentinelle = _dob.dt.strftime("%Y-%m-%d").isin(DATES_SENTINELLES)
df["_dob"]   = _dob.mask(_sentinelle)                # sentinelles -> NaT (ne pénalisent pas)
df["_annee"] = df["_dob"].dt.year

# clés de blocage
df["_nom_phon"]    = df["_nom"].map(cle_phonetique)
df["_prenom_phon"] = df["_prenom"].map(cle_phonetique)
df["_dob_key"]     = df["_dob"].dt.strftime("%Y%m%d")
df["_bloc_gsm"]    = df["_gsm"].replace("", np.nan)

# fréquence des noms (pour la pondération par rareté / term-frequency)
freq_nom = df["_nom"].value_counts()

# =========================================================================
# 3. Blocking multi-passes — union de blocs complémentaires (rappel max,
#    volume maîtrisé). Chaque passe rattrape un type d'erreur de saisie.
# =========================================================================
idx = rl.Index()
idx.block(["_nom_phon", "_annee"])            # faute d'orthographe sur le nom
idx.block(["_prenom_phon", "_annee"])         # nom faux / inversion partielle
idx.block("_bloc_gsm")                        # nom illisible mais même téléphone
idx.block(["_dob_key", "_sexe"])              # nom illisible mais DOB exact
idx.sortedneighbourhood("_full", window=3)    # quasi-voisins alphabétiques
paires = idx.index(df)                         # union dédupliquée des passes

# =========================================================================
# 4. Features de comparaison BINAIRES (n'apportent que des preuves d'accord ;
#    l'absence d'accord ne pénalise pas artificiellement une donnée manquante)
# =========================================================================
a = df.loc[paires.get_level_values(0)].reset_index(drop=True)
b = df.loc[paires.get_level_values(1)].reset_index(drop=True)

def jw(x, y):
    if not x or not y:
        return 0.0
    return jellyfish.jaro_winkler_similarity(x, y)

sim_nom    = np.array([jw(x, y) for x, y in zip(a["_nom"], b["_nom"])])
sim_prenom = np.array([jw(x, y) for x, y in zip(a["_prenom"], b["_prenom"])])
# overlap des tokens : gère inversion nom/prénom et prénom manquant
overlap = np.array([
    (len(s & t) / len(s | t)) if (s | t) else 0.0
    for s, t in zip(a["_tokens"], b["_tokens"])
])

dob_exact = (a["_dob"].values == b["_dob"].values) & a["_dob"].notna().values
dob_swap  = ((a["_dob"].dt.day.values == b["_dob"].dt.month.values) &
             (a["_dob"].dt.month.values == b["_dob"].dt.day.values) &
             a["_dob"].notna().values & b["_dob"].notna().values)
dob_annee = (a["_annee"].values == b["_annee"].values) & a["_annee"].notna().values

nom_agree = sim_nom >= 0.88
gsm_agree = (a["_gsm"].values == b["_gsm"].values) & (a["_gsm"].values != "")
# rareté : sur une paire qui matche, le nom partagé est-il rare ? -> poids fort
nom_rare = a["_nom"].map(freq_nom).fillna(1).values <= RARE_MAX

feats = pd.DataFrame({
    "nom":         nom_agree.astype(int),
    "nom_rare":    (nom_agree & nom_rare).astype(int),          # TF-adjustment
    "prenom":      (sim_prenom >= 0.88).astype(int),
    "fullname":    (overlap >= 0.80).astype(int),               # robuste à l'inversion
    "dob":         (dob_exact | dob_swap).astype(int),
    "annee":       dob_annee.astype(int),
    "sexe":        (a["_sexe"].values == b["_sexe"].values).astype(int),
    "gsm":         gsm_agree.astype(int),
}, index=paires)

# =========================================================================
# 5. Fellegi-Sunter via ECM (poids m/u appris SANS étiquettes)
#    Chaque feature reçoit un poids = log(m/u) : gsm/nom_rare pèsent lourd,
#    sexe/prénom peu. La somme -> probabilité par paire.
# =========================================================================
ecm = rl.ECMClassifier()
ecm.fit(feats)
proba = ecm.prob(feats)

# garde-fou contre l'inversion d'étiquettes de l'EM : une paire qui concorde
# sur gsm+dob DOIT scorer haut ; sinon on inverse la probabilité.
ancre = (feats["gsm"] == 1) & (feats["dob"] == 1)
if ancre.sum() >= 5 and proba[ancre].mean() < 0.5:
    proba = 1.0 - proba

scores = proba.rename("p").reset_index()
scores.columns = ["id_a", "id_b", "p"]
scores = scores.join(feats.reset_index(drop=True))

# =========================================================================
# 6. Regroupement anti-chaînage
#    - on ne fusionne QUE les arêtes >= SEUIL_AUTO
#    - un groupe trop gros (chaînage suspect) repart en révision
# =========================================================================
g = nx.Graph()
g.add_nodes_from(df.index)
sûrs = scores[scores["p"] >= SEUIL_AUTO]
g.add_edges_from(sûrs[["id_a", "id_b"]].itertuples(index=False, name=None))

id_unifie, taille = {}, {}
groupes_suspects = set()
for gid, membres in enumerate(nx.connected_components(g)):
    membres = list(membres)
    for f in membres:
        id_unifie[f] = f"P{gid:07d}"
        taille[f] = len(membres)
    if len(membres) > TAILLE_MAX_AUTO:
        groupes_suspects.update(membres)

# meilleur score + motif d'appariement par fiche (pour le réviseur)
def _motif(r):
    parts = []
    if r["gsm"]:  parts.append("gsm")
    if r["dob"]:  parts.append("dob")
    elif r["annee"]: parts.append("année")
    if r["nom"]:  parts.append("nom")
    if r["prenom"]: parts.append("prénom")
    if r["fullname"] and not (r["nom"] and r["prenom"]): parts.append("nom↔prénom")
    if r["sexe"]: parts.append("sexe")
    return "+".join(parts)

scores["motif"] = scores.apply(_motif, axis=1)
long = pd.concat([
    scores.rename(columns={"id_a": "id"})[["id", "p", "motif"]],
    scores.rename(columns={"id_b": "id"})[["id", "p", "motif"]],
])
meilleur = long.sort_values("p").groupby("id").tail(1).set_index("id")

# =========================================================================
# 7. Construction de la table gold
# =========================================================================
out = df.drop(columns=[c for c in df.columns if c.startswith("_")]).copy()
out["id_patient_unifie"] = out.index.map(id_unifie)
out["score_match"]       = out.index.map(meilleur["p"]).fillna(0.0).round(3)
out["motif_match"]       = out.index.map(meilleur["motif"]).fillna("")
out["taille_groupe"]     = out.index.map(taille).fillna(1).astype(int)

def _statut(row):
    if row.name in groupes_suspects:          return "à_réviser"
    if row["score_match"] >= SEUIL_AUTO:      return "sûr"
    if row["score_match"] >= SEUIL_REVISION:  return "à_réviser"
    return "distinct"
out["statut_match"] = out.apply(_statut, axis=1)

# fiche de référence par groupe (survivorship : la plus complète, puis la plus récente)
completude = out[["{{col_nom}}", "{{col_prenom}}", "{{col_dob}}", "{{col_gsm}}"]].notna().sum(axis=1)
out["_completude"] = completude
out["_dcrea"] = pd.to_datetime(out["{{col_date_creation}}"], errors="coerce")
ref_idx = (out.sort_values(["_completude", "_dcrea"], ascending=False)
              .groupby("id_patient_unifie").head(1).index)
out["est_reference"] = out.index.isin(ref_idx)
out = out.drop(columns=["_completude", "_dcrea"])

# =========================================================================
# 8. Métriques de qualité (visibles dans les logs Airflow)
# =========================================================================
n = len(out)
n_groupes = out["id_patient_unifie"].nunique()
n_doublons = n - n_groupes
print(f"[dedup] {n} fiches -> {n_groupes} patients uniques "
      f"| {n_doublons} doublons ({n_doublons/max(n,1):.1%}) "
      f"| à réviser : {(out['statut_match']=='à_réviser').sum()}")

write_table(out.reset_index(), TABLE_SORTIE)
'''

TABLE_FIELD = lambda key, label, default=None: {"key": key, "label": label, "type": "table", **({"default": default} if default else {})}
COLUMN_FIELD = lambda key, label, default=None: {"key": key, "label": label, "type": "column", **({"default": default} if default else {})}
TEXT_FIELD = lambda key, label, default=None: {"key": key, "label": label, "type": "text", **({"default": default} if default else {})}
NUMBER_FIELD = lambda key, label, default: {"key": key, "label": label, "type": "number", "default": default}

DEDUP_TEMPLATE = {
    "name": "Déduplication de patients (sans identifiant fort)",
    "ml_objective": "record_linkage",
    "description": (
        "Version production pour patients SANS identifiant fort (pas de CIN) : normalisation avancée "
        "(translittération arabe, phonétique, GSM), blocking multi-passes, garde-fou anti-inversion EM, "
        "anti-chaînage des groupes, et sélection d'une fiche de référence (survivorship)."
    ),
    "python_code": DEDUP_CODE,
    "expected_inputs": [
        TABLE_FIELD("{{table}}", "Table d'entrée (admissions)", "admission"),
        TABLE_FIELD("{{output_table}}", "Table de sortie (gold)", "gold_patients_unifies"),
        COLUMN_FIELD("{{col_id}}", "Colonne identifiant de ligne (unique)", "NUM_DOSS"),
        COLUMN_FIELD("{{col_etat}}", "Colonne statut du dossier (optionnel, laisser vide si non applicable)", "ETAT_DOSS"),
        TEXT_FIELD("{{val_etat_exclu}}", "Valeur de statut à exclure (ex: dossiers sortis/supprimés)", "S"),
        COLUMN_FIELD("{{col_nom}}", "Colonne nom", "NOM_BENEF"),
        COLUMN_FIELD("{{col_prenom}}", "Colonne prénom", "PREN_BENEF"),
        COLUMN_FIELD("{{col_dob}}", "Colonne date de naissance", "DATE_NAI_BENEF"),
        COLUMN_FIELD("{{col_sexe}}", "Colonne sexe", "SEXE_BENEF"),
        COLUMN_FIELD("{{col_gsm}}", "Colonne téléphone (GSM)", "GSM"),
        COLUMN_FIELD("{{col_date_creation}}", "Colonne date de création du dossier (survivorship)", "DATE_CREATION"),
        NUMBER_FIELD("{{seuil_auto}}", "Seuil de fusion automatique (sûr)", "0.92"),
        NUMBER_FIELD("{{seuil_revision}}", "Seuil de révision (zone grise)", "0.55"),
        NUMBER_FIELD("{{taille_max_auto}}", "Taille de groupe max avant révision (anti-chaînage)", "15"),
        NUMBER_FIELD("{{rare_max}}", "Fréquence max pour considérer un nom 'rare'", "4"),
    ],
    "output_columns": [
        "(colonnes d'origine)", "id_patient_unifie", "score_match", "statut_match",
        "motif_match", "taille_groupe", "est_reference",
    ],
}


def upgrade() -> None:
    op.bulk_insert(ml_templates_table, [DEDUP_TEMPLATE])


def downgrade() -> None:
    op.execute("DELETE FROM ml_templates WHERE name = 'Déduplication de patients (sans identifiant fort)'")
