# Contribuer à Data Plateforme

Ce document décrit les conventions et la discipline de développement réellement appliquées dans ce repo. Ce n'est pas une charte théorique — chaque règle ci-dessous correspond à un pattern observable dans le code existant.

---

## 1. Principe fondateur : construire module par module, vérifier sur infra réelle

C'est la règle la plus structurante du projet.

- **Un module à la fois, dans l'ordre de la roadmap.** On ne démarre pas le module N+1 avant que le module N soit terminé et vérifié.
- **Chaque étape est vérifiée sur infrastructure réelle avant de passer à la suivante** — pas seulement "ça compile" ou "les tests passent" (il n'y a pas de suite de tests automatisés, voir §7). On vérifie en :
  - déclenchant un vrai run Airflow et en lisant son résultat réel,
  - se connectant à une vraie base Oracle/PostgreSQL/MySQL,
  - envoyant un vrai email via le SMTP configuré,
  - ouvrant l'écran dans un navigateur (Playwright) et en regardant le screenshot.
- **Réutiliser l'existant, ne jamais recréer.** Avant d'ajouter une fonctionnalité, vérifier si un service, un pattern ou un composant existant peut être étendu plutôt que dupliqué.
- **Zéro régression.** Une nouvelle fonctionnalité annexe (ex. collecte de métriques qualité, notification) ne doit jamais faire échouer le flux principal qu'elle observe. Pattern systématique : best-effort avec `try/except` qui logue et continue.

---

## 2. Architecture backend (FastAPI / Python 3.13)

Flux en couches strict :

```
api/routes/*.py   → endpoints HTTP, dépendances (auth), validation via schemas
schemas/*.py      → Pydantic v2, contrats d'entrée/sortie
models/*.py       → SQLAlchemy 2.x, typé avec Mapped[]
services/*.py     → logique métier, appels externes (SSH, Docker, API tierces)
templates/*.j2    → artefacts générés (DAGs Airflow, projets dbt, Dockerfiles)
```

### Organisation des routes
- **Un fichier par domaine métier** : `servers.py`, `sources.py`, `medallion.py`, `quality.py`, `ml_templates.py`, `airflow.py`, `stacks.py`, `users.py`, `auth.py`, `dashboard.py`.
- Un routeur imbriqué profondément (ex. `/api/servers/{server_id}/stacks/{sid}/airflow`) obtient **son propre fichier** plutôt que de surcharger le fichier parent — voir `airflow.py` et `quality.py` comme exemples.
- Chaque fichier de routes définit ses propres petits helpers `_get_project()`, `_get_dataset()`, etc. — pas de partage inter-fichiers de ces helpers triviaux (préférence pour la duplication locale plutôt que le couplage).

### Migrations Alembic
- Une migration par changement de schéma, **écrite à la main** (pas d'autogenerate aveugle appliqué tel quel).
- Pièges connus et gérés explicitement :
  - `ALTER TYPE ... ADD VALUE` doit être dans `op.get_context().autocommit_block()` — impossible dans la même transaction que celle qui l'a émis.
  - `DROP TABLE` sur une table avec une vue dépendante nécessite `CASCADE`.
  - Un enum déjà créé par `create_table()` (colonne `Enum`) ne doit pas être re-créé explicitement avant — laisser `create_table()` s'en charger (sinon `DuplicateObject`).

### Secrets
- **Toujours chiffrés au repos** avec Fernet (`APP_SECRET_KEY`, `app/core/security.py::encrypt_secret`/`decrypt_secret`).
- **Jamais renvoyés en clair** au frontend — masqués en `••••••••` à la lecture (voir `services/stack_secrets.py::mask_services`).
- **Jamais loggués.**
- Un champ secret vidé côté frontend lors d'un GET (pour ne pas ré-soumettre le masque comme s'il s'agissait d'une vraie valeur) — voir `StackTab.jsx::blankSecrets`.

### Erreurs et robustesse
- `HTTPException` avec des messages `detail` en français, directement affichables à l'utilisateur.
- Idempotence systématique : contraintes d'unicité en base + patterns upsert (ex. PATCH sur les connexions Airflow existantes, `get_or_create` pour les règles de qualité).
- Timeouts courts sur tout appel réseau externe (SSH, tests de connexion).

---

## 3. Architecture frontend (React 19 / Vite)

```
pages/<domaine>/     → écrans, un dossier par domaine (medallion, servers, sources, settings, auth)
components/ui/       → primitives réutilisables (Button, Modal, Drawer, Input, Switch, Badge)
components/icons.jsx → set d'icônes SVG maison
api/<domaine>.js     → un fichier par domaine, miroir exact des routes backend
context/             → AuthContext, ToastContext
i18n/                → react-i18next, fr/en/nl
styles/               → tokens.css + fichiers par zone (shell.css, ui.css, auth.css...)
```

- Composants fonctionnels + hooks uniquement. Pas de Redux/Zustand — état local (`useState`) et `context` pour l'auth/toast.
- Pas de Tailwind, pas de CSS-in-JS : styles inline (`style={{}}`) pour le layout ponctuel, classes CSS partagées pour les patterns récurrents (`.card`, `.btn-ghost`, `.badge-*`...).
- **Design tokens centralisés** dans `styles/tokens.css` : sidebar sombre (`--ink-900`...) + contenu clair (`--bg`, `--surface`, `--border`), accent `#E57200`, polices Space Grotesk (titres) / Inter (UI) / JetBrains Mono (valeurs, code, IDs techniques).
- Lint : `oxlint` (`npm run lint`), pas d'ESLint classique.
- i18n : `t("domaine.cle")`, un namespace par page/section dans `i18n/locales/{fr,en,nl}.json`. **On ne traduit que le chrome de l'interface, jamais le contenu de la base** (noms de projets, descriptions de templates ML saisis par les utilisateurs).

---

## 4. Style de code

- **Pas de commentaires descriptifs** ("ce code fait X"). Un commentaire n'est écrit que pour expliquer un **pourquoi non-évident** : un piège contourné, une contrainte cachée, une décision qui surprendrait un relecteur.
- Pas de docstrings multi-lignes sauf sur les classes/fonctions dont le contrat n'est pas évident à l'usage.
- Pas d'abstraction prématurée : trois lignes similaires valent mieux qu'une fausse généralisation.
- Noms de variables/fonctions en anglais (code), messages utilisateur et commentaires en français.

---

## 5. Déploiement & versioning

- Images Docker backend/frontend versionnées sémantiquement (`moatezborgi/dataplateforme-backend:1.0.0`), poussées sur Docker Hub.
- `docker-compose.prod.yml` à la racine pour le control plane (backend + frontend + Postgres) — **toujours donner un `name:` explicite** à un fichier compose partageant un répertoire avec un autre (évite les collisions de service entre `docker-compose.dev.yml` et `docker-compose.prod.yml`).
- Le frontend injecte son URL d'API **au démarrage du conteneur** (`docker/runtime-config.sh` → `window.__API_BASE_URL__`), jamais au build — une seule image fonctionne sur n'importe quel environnement.
- `FRONTEND_URL` (utilisé pour `CORS_ORIGINS`) ne doit **jamais** inclure un port par défaut (80 pour http) — les navigateurs ne l'incluent pas dans l'en-tête `Origin`, une correspondance stricte échouerait sinon.

---

## 6. Comment ajouter un module

1. Lire la spec du module en entier avant de coder.
2. Découper en étapes séquentielles (ex. Module 5 : historisation → écran → règles/alertes → notifications).
3. Pour chaque étape : modèle + migration → service → endpoints → frontend → **vérification sur infra réelle** → étape suivante.
4. Ne jamais recréer un service déjà existant (`ssh.py`, `stack_secrets.py`, `email.py`...) — l'étendre si besoin.
5. Mettre à jour `.env.example` si de nouvelles variables sont introduites.

---

## 7. Ce qui n'existe volontairement pas (pour l'instant)

- **Pas de suite de tests automatisés** (`pytest`, Jest...). La validation passe par des vérifications manuelles/scriptées contre l'infrastructure réelle à chaque étape, documentées dans la conversation de build, pas dans un dossier `tests/`.
- **Pas de CI/CD configuré.**
- **Pas de linter/formatter Python** (ni `ruff`, ni `black`, ni `flake8`) — cohérence maintenue à la main.

Si l'un de ces manques devient un problème réel, il vaut mieux l'ajouter explicitement (et documenter pourquoi) que de le contourner silencieusement.
