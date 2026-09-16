"""add ml_templates table and template_id fk

Revision ID: 76385386004c
Revises: d8be9d685eb4
Create Date: 2026-07-10 23:56:05.480775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '76385386004c'
down_revision: Union[str, None] = 'd8be9d685eb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ml_templates_table = sa.table(
    "ml_templates",
    sa.column("name", sa.String),
    sa.column("ml_objective", postgresql.ENUM('none', 'anomaly', 'scoring', 'forecast', 'clustering', name="medallion_ml_objective", create_type=False)),
    sa.column("description", sa.Text),
    sa.column("python_code", sa.Text),
    sa.column("expected_inputs", postgresql.JSONB),
    sa.column("output_columns", postgresql.JSONB),
)

TABLE_FIELD = lambda key, label: {"key": key, "label": label, "type": "table"}
COLUMN_FIELD = lambda key, label: {"key": key, "label": label, "type": "column"}
COLUMN_LIST_FIELD = lambda key, label: {"key": key, "label": label, "type": "column_list"}
NUMBER_FIELD = lambda key, label, default: {"key": key, "label": label, "type": "number", "default": default}

SEED_TEMPLATES = [
    {
        "name": "Détection d'anomalies (Isolation Forest)",
        "ml_objective": "anomaly",
        "description": "Isolation forest non supervisé — score chaque ligne et marque les anomalies sur un jeu de colonnes numériques.",
        "python_code": (
            "from sklearn.ensemble import IsolationForest\n\n"
            'df = read_table("TABLE_ENTREE")\n'
            "features = FEATURES_LIST\n"
            "contamination = CONTAMINATION\n\n"
            "model = IsolationForest(contamination=contamination, random_state=42)\n"
            'df["score_anomalie"] = model.fit_predict(df[features])\n'
            'df["est_anomalie"] = df["score_anomalie"] == -1\n\n'
            'write_table(df, "TABLE_SORTIE")\n'
        ),
        "expected_inputs": [
            TABLE_FIELD("TABLE_ENTREE", "Table d'entrée"),
            COLUMN_LIST_FIELD("FEATURES_LIST", "Colonnes features (numériques)"),
            NUMBER_FIELD("CONTAMINATION", "Contamination (proportion attendue d'anomalies)", "0.02"),
            TABLE_FIELD("TABLE_SORTIE", "Table de sortie"),
        ],
        "output_columns": ["(colonnes d'origine)", "score_anomalie", "est_anomalie"],
    },
    {
        "name": "Détection d'anomalies simple (z-score / IQR)",
        "ml_objective": "anomaly",
        "description": "Variante sans ML sur une seule métrique — marque anormal tout point au-delà de N écarts-types. Interprétable, bon premier jet.",
        "python_code": (
            'df = read_table("TABLE_ENTREE")\n'
            'colonne = "COLONNE_METRIQUE"\n'
            "seuil = SEUIL_ECART_TYPE\n\n"
            "moyenne = df[colonne].mean()\n"
            "ecart_type = df[colonne].std()\n"
            'df["z_score"] = (df[colonne] - moyenne) / ecart_type\n'
            'df["est_anomalie"] = df["z_score"].abs() > seuil\n\n'
            'write_table(df, "TABLE_SORTIE")\n'
        ),
        "expected_inputs": [
            TABLE_FIELD("TABLE_ENTREE", "Table d'entrée"),
            COLUMN_FIELD("COLONNE_METRIQUE", "Colonne métrique à surveiller"),
            NUMBER_FIELD("SEUIL_ECART_TYPE", "Seuil (nombre d'écarts-types)", "3"),
            TABLE_FIELD("TABLE_SORTIE", "Table de sortie"),
        ],
        "output_columns": ["(colonnes d'origine)", "z_score", "est_anomalie"],
    },
    {
        "name": "Scoring (classifieur)",
        "ml_objective": "scoring",
        "description": "Entraîne un classifieur sur un historique étiqueté, applique-le aux nouvelles lignes : probabilité + priorité.",
        "python_code": (
            "from sklearn.linear_model import LogisticRegression\n\n"
            'historique = read_table("TABLE_HISTORIQUE")\n'
            'a_scorer = read_table("TABLE_A_SCORER")\n\n'
            "features = FEATURES_LIST\n"
            'cible = "COLONNE_CIBLE"\n\n'
            "model = LogisticRegression(max_iter=1000)\n"
            "model.fit(historique[features], historique[cible])\n\n"
            'a_scorer["probabilite"] = model.predict_proba(a_scorer[features])[:, 1]\n'
            'a_scorer["priorite"] = a_scorer["probabilite"].rank(ascending=False, method="min").astype(int)\n\n'
            'write_table(a_scorer, "TABLE_SORTIE")\n'
        ),
        "expected_inputs": [
            TABLE_FIELD("TABLE_HISTORIQUE", "Table d'historique (issue connue)"),
            TABLE_FIELD("TABLE_A_SCORER", "Table à scorer (nouvelles lignes)"),
            COLUMN_LIST_FIELD("FEATURES_LIST", "Colonnes features"),
            COLUMN_FIELD("COLONNE_CIBLE", "Colonne cible (dans l'historique)"),
            TABLE_FIELD("TABLE_SORTIE", "Table de sortie"),
        ],
        "output_columns": ["(identifiant)", "probabilite", "priorite"],
    },
    {
        "name": "Prévision simple",
        "ml_objective": "forecast",
        "description": "Ajuste une tendance simple sur une série temporelle et projette un horizon donné, avec bornes basse/haute.",
        "python_code": (
            "import numpy as np\n\n"
            'df = read_table("TABLE_SERIE_TEMPORELLE")\n'
            'colonne_date = "COLONNE_DATE"\n'
            'colonne_valeur = "COLONNE_VALEUR"\n'
            "horizon = HORIZON_PERIODES\n\n"
            "df = df.sort_values(colonne_date)\n"
            "x = np.arange(len(df))\n"
            "tendance = np.poly1d(np.polyfit(x, df[colonne_valeur], deg=1))\n\n"
            "futur_x = np.arange(len(df), len(df) + horizon)\n"
            "valeurs_prevues = tendance(futur_x)\n"
            "ecart_type = (df[colonne_valeur] - tendance(x)).std()\n\n"
            "resultat = pd.DataFrame({\n"
            '    "periode": futur_x,\n'
            '    "valeur_prevue": valeurs_prevues,\n'
            '    "borne_basse": valeurs_prevues - 1.96 * ecart_type,\n'
            '    "borne_haute": valeurs_prevues + 1.96 * ecart_type,\n'
            "})\n\n"
            'write_table(resultat, "TABLE_SORTIE")\n'
        ),
        "expected_inputs": [
            TABLE_FIELD("TABLE_SERIE_TEMPORELLE", "Table série temporelle"),
            COLUMN_FIELD("COLONNE_DATE", "Colonne date"),
            COLUMN_FIELD("COLONNE_VALEUR", "Colonne valeur"),
            NUMBER_FIELD("HORIZON_PERIODES", "Horizon (nombre de périodes à projeter)", "30"),
            TABLE_FIELD("TABLE_SORTIE", "Table de sortie"),
        ],
        "output_columns": ["periode", "valeur_prevue", "borne_basse", "borne_haute"],
    },
    {
        "name": "Segmentation (k-means)",
        "ml_objective": "clustering",
        "description": "Regroupe les lignes similaires en k segments à partir de colonnes numériques (k-means).",
        "python_code": (
            "from sklearn.cluster import KMeans\n\n"
            'df = read_table("TABLE_ENTREE")\n'
            "features = FEATURES_LIST\n"
            "k = NOMBRE_SEGMENTS\n\n"
            "model = KMeans(n_clusters=k, random_state=42, n_init=10)\n"
            'df["segment"] = model.fit_predict(df[features])\n\n'
            'write_table(df, "TABLE_SORTIE")\n'
        ),
        "expected_inputs": [
            TABLE_FIELD("TABLE_ENTREE", "Table d'entrée"),
            COLUMN_LIST_FIELD("FEATURES_LIST", "Colonnes features (numériques)"),
            NUMBER_FIELD("NOMBRE_SEGMENTS", "Nombre de segments (k)", "4"),
            TABLE_FIELD("TABLE_SORTIE", "Table de sortie"),
        ],
        "output_columns": ["(colonnes d'origine)", "segment"],
    },
]


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('ml_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('ml_objective', postgresql.ENUM('none', 'anomaly', 'scoring', 'forecast', 'clustering', name='medallion_ml_objective', create_type=False), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('python_code', sa.Text(), nullable=False),
    sa.Column('expected_inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('output_columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_foreign_key(None, 'medallion_datasets', 'ml_templates', ['template_id'], ['id'], ondelete='SET NULL')
    # ### end Alembic commands ###

    op.bulk_insert(ml_templates_table, SEED_TEMPLATES)


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'medallion_datasets', type_='foreignkey')
    op.drop_table('ml_templates')
    # ### end Alembic commands ###
