# Guide de déploiement chez le client — d'un serveur vierge à la plateforme en ligne

Ce guide part d'un **serveur Ubuntu Server LTS fraîchement installé** (22.04 ou 24.04) et
couvre chaque étape jusqu'à avoir Data Plateforme by Delomid IT accessible dans un
navigateur. Il ne couvre **pas** la mise en place d'un reverse proxy / HTTPS (le control
plane reste en HTTP simple, voir §13 "Aller plus loin" pour les pistes) ni le provisioning
Airflow/MinIO/Superset des tenants (géré par la plateforme elle-même une fois en ligne,
voir `backend/app/templates/`).

Pour toute commande, remplacez :
- `<user>` par l'utilisateur SSH sur le serveur client
- `<ip>` par l'IP ou le nom de domaine du serveur

---

## 0. Vue d'ensemble

Trois conteneurs Docker, un réseau, un volume nommé :

| Service    | Rôle                                    | Port publié par défaut |
|------------|------------------------------------------|--------------------------|
| `postgres` | Base de données du control plane         | non exposé (interne)     |
| `backend`  | API FastAPI                              | `8000`                    |
| `frontend` | Application React servie par nginx        | `80`                       |

Tout tourne sur une seule machine ; aucune dépendance externe autre que Docker Hub (pour
récupérer les images) et un serveur SMTP (pour les emails — réinitialisation de mot de
passe, invitations, alertes qualité).

---

## 1. Prérequis

- Ubuntu Server 22.04 LTS ou 24.04 LTS, installation minimale, à jour.
- Un accès initial à la **console** du serveur (clavier/écran branché, ou console web du
  fournisseur — IPMI/iLO pour du matériel physique, console cloud/hyperviseur pour une
  VM). Nécessaire une seule fois, uniquement si SSH n'est pas encore actif (§2).
- Un utilisateur disposant des droits `sudo`.
- Une IP ou un nom de domaine que les navigateurs des utilisateurs du client peuvent
  atteindre.
- Ports à ouvrir sur le pare-feu du réseau du client (en plus du serveur lui-même, §5) :
  `22` (SSH), `80` (interface web), `8000` (API backend).

**Dimensionnement recommandé (point de départ)** : 2 vCPU / 4 Go RAM / 20 Go disque. Le
control plane lui-même est léger (Postgres + deux petits conteneurs) ; ce n'est **pas**
sur cette machine que tournent Airflow/dbt/les traitements des tenants — ceux-ci sont
provisionnés par la plateforme sur des serveurs séparés, dimensionnés au cas par cas.

---

## 2. Installation et activation de SSH (si pas déjà actif)

Un serveur « vierge » installé sans le paquet OpenSSH n'est pas encore joignable à
distance : les commandes ci-dessous s'exécutent **en local sur la machine** (clavier/
écran branché, ou console du fournisseur). Si SSH est déjà actif — le cas sur la plupart
des images cloud/VPS — passez directement à l'étape 3.

```bash
# en local, avec l'utilisateur créé pendant l'installation d'Ubuntu :
sudo apt update
sudo apt install -y openssh-server

# activer le service au démarrage et le démarrer immédiatement
sudo systemctl enable --now ssh

# vérifier qu'il tourne
sudo systemctl status ssh --no-pager
```

### Repérer l'adresse IP du serveur

```bash
ip a
# ou, plus direct :
hostname -I
```

Notez l'adresse affichée (interface `eth0`, `ens...` ou équivalent) — c'est `<ip>` pour
le reste de ce guide.

### Se connecter depuis votre poste

Depuis votre machine (pas depuis le serveur), une fois SSH actif côté serveur :

```bash
ssh <user>@<ip>
```

À la première connexion, acceptez l'empreinte de la clé hôte (`yes`) si demandé.

> Le pare-feu `ufw` n'est pas encore configuré à ce stade (§5) : SSH reste donc
> accessible par défaut pour l'instant. Une fois `ufw` activé, la règle
> `sudo ufw allow OpenSSH` (déjà incluse dans ce guide) garde le port 22 ouvert — ne
> l'omettez pas, sous peine de perdre l'accès au serveur.

---

## 3. Connexion et mise à jour du système

```bash
ssh <user>@<ip>
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg
```

---

## 4. Installation de Docker Engine

**Important** : on installe Docker depuis le dépôt officiel Docker, pas le paquet
`docker.io` d'Ubuntu (souvent une version ancienne). Étapes officielles
([docs.docker.com](https://docs.docker.com/engine/install/ubuntu/)) :

```bash
# 1. Ajouter la clé GPG officielle de Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 2. Ajouter le dépôt Docker aux sources apt
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 3. Installer Docker Engine + le plugin Compose
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### Vérification

```bash
sudo docker run hello-world
docker compose version
```

La première commande doit afficher un message de bienvenue Docker ; la seconde doit
afficher une version (`Docker Compose version v2.x.x`).

### (Recommandé) Utiliser Docker sans `sudo`

```bash
sudo usermod -aG docker $USER
newgrp docker          # applique le nouveau groupe sans avoir à se reconnecter
docker ps               # doit fonctionner sans sudo
```

---

## 5. Pare-feu du serveur (`ufw`)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 8000/tcp
sudo ufw enable
sudo ufw status verbose
```

Si le serveur est déjà derrière un pare-feu réseau côté client (routeur, cloud security
group), ouvrez les mêmes ports là-bas également.

---

## 6. Création de l'arborescence de déploiement

```bash
sudo mkdir -p /opt/dataplateforme
sudo chown $USER:$USER /opt/dataplateforme
cd /opt/dataplateforme
```

Tout le reste de ce guide se passe dans `/opt/dataplateforme`.

---

## 7. Fichier `docker-compose.prod.yml`

Créez le fichier directement sur le serveur avec son contenu exact :

```bash
cat > docker-compose.prod.yml <<'EOF'
name: dataplateforme-prod

services:
  postgres:
    image: postgres:16
    container_name: dataplateforme-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-dataplateforme}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:-dataplateforme}
    volumes:
      - dataplateforme-postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dataplateforme}"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - dataplateforme

  backend:
    image: ${DOCKERHUB_NAMESPACE:?set DOCKERHUB_NAMESPACE in .env}/dataplateforme-backend:${BACKEND_IMAGE_TAG:-latest}
    container_name: dataplateforme-backend
    restart: unless-stopped
    environment:
      ENV: prod
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-dataplateforme}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-dataplateforme}
      APP_SECRET_KEY: ${APP_SECRET_KEY:?set APP_SECRET_KEY in .env}
      JWT_SECRET_KEY: ${JWT_SECRET_KEY:?set JWT_SECRET_KEY in .env}
      JWT_ALGORITHM: HS256
      ACCESS_TOKEN_EXPIRE_MINUTES: ${ACCESS_TOKEN_EXPIRE_MINUTES:-480}
      RESET_TOKEN_EXPIRE_MINUTES: ${RESET_TOKEN_EXPIRE_MINUTES:-30}
      FRONTEND_URL: ${FRONTEND_URL:?set FRONTEND_URL in .env}
      CORS_ORIGINS: ${FRONTEND_URL}
      SMTP_HOST: ${SMTP_HOST:-}
      SMTP_PORT: ${SMTP_PORT:-587}
      SMTP_USER: ${SMTP_USER:-}
      SMTP_PASSWORD: ${SMTP_PASSWORD:-}
      SMTP_FROM: ${SMTP_FROM:-no-reply@delomid.io}
      SMTP_TLS: ${SMTP_TLS:-true}
      ADMIN_NAME: ${ADMIN_NAME:-Admin}
      ADMIN_EMAIL: ${ADMIN_EMAIL:?set ADMIN_EMAIL in .env}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:?set ADMIN_PASSWORD in .env}
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - dataplateforme

  frontend:
    image: ${DOCKERHUB_NAMESPACE:?set DOCKERHUB_NAMESPACE in .env}/dataplateforme-frontend:${FRONTEND_IMAGE_TAG:-latest}
    container_name: dataplateforme-frontend
    restart: unless-stopped
    environment:
      API_BASE_URL: ${BACKEND_PUBLIC_URL:?set BACKEND_PUBLIC_URL in .env}
    ports:
      - "${FRONTEND_PORT:-80}:80"
    depends_on:
      - backend
    networks:
      - dataplateforme

networks:
  dataplateforme:

volumes:
  dataplateforme-postgres-data:
EOF
```

Vérifiez que le fichier est bien là et syntaxiquement valide :

```bash
docker compose -f docker-compose.prod.yml config -q && echo "OK"
```

(`config -q` échoue silencieusement s'il y a une erreur ; l'absence d'erreur + "OK" affiché
confirme que le YAML est valide — les variables `${...:?...}` non définies ne sont pas
encore une erreur à ce stade, elles ne sont vérifiées qu'au `up`.)

---

## 8. Fichier `.env` — création

Créez un `.env` à partir du modèle, avec des **placeholders explicites** à remplacer :

```bash
cat > .env <<'EOF'
# --- images ---
DOCKERHUB_NAMESPACE=delomidit
BACKEND_IMAGE_TAG=1.2.0
FRONTEND_IMAGE_TAG=1.2.0

# --- ports publiés sur ce serveur ---
BACKEND_PORT=8000
FRONTEND_PORT=80

# --- URLs publiques (telles que vues par le navigateur / vérifiées par le CORS du backend) ---
FRONTEND_URL=http://CHANGE_ME_IP_OU_DOMAINE
BACKEND_PUBLIC_URL=http://CHANGE_ME_IP_OU_DOMAINE:8000

# --- base de données du control plane ---
POSTGRES_USER=dataplateforme
POSTGRES_PASSWORD=CHANGE_ME_MOT_DE_PASSE_POSTGRES
POSTGRES_DB=dataplateforme

# --- secrets applicatifs ---
APP_SECRET_KEY=CHANGE_ME_CLE_FERNET
JWT_SECRET_KEY=CHANGE_ME_CLE_JWT

# --- SMTP (réinitialisation mot de passe, invitations, alertes qualité) ---
SMTP_HOST=CHANGE_ME_SMTP_HOST
SMTP_PORT=465
SMTP_USER=CHANGE_ME_SMTP_USER
SMTP_PASSWORD=CHANGE_ME_SMTP_PASSWORD
SMTP_FROM=CHANGE_ME_SMTP_FROM
SMTP_TLS=true

# --- compte admin de démarrage (créé une seule fois, de façon idempotente) ---
ADMIN_NAME=Admin
ADMIN_EMAIL=CHANGE_ME_ADMIN_EMAIL
ADMIN_PASSWORD=CHANGE_ME_ADMIN_PASSWORD
EOF

chmod 600 .env   # ce fichier contient des secrets — lisible uniquement par vous
```

### Ce que signifie chaque variable

| Variable | Notes |
|---|---|
| `DOCKERHUB_NAMESPACE` | Namespace Docker Hub des images (`delomidit`) — ne change pas d'un client à l'autre. |
| `BACKEND_IMAGE_TAG` / `FRONTEND_IMAGE_TAG` | Version à déployer. Vérifiez la dernière version disponible avant de déployer un nouveau client. |
| `BACKEND_PORT` / `FRONTEND_PORT` | Ports exposés sur ce serveur. Ne changez que si `80`/`8000` sont déjà pris. |
| `FRONTEND_URL` | L'URL que les navigateurs utilisent pour atteindre l'interface. **Piège CORS** : si `FRONTEND_PORT=80`, ne mettez **pas** `:80` dans l'URL (un navigateur n'envoie jamais de port par défaut dans l'en-tête `Origin`, donc `:80` ferait échouer la vérification CORS). |
| `BACKEND_PUBLIC_URL` | L'URL que le *navigateur* utilise pour atteindre l'API — pas le nom de service Docker interne. |
| `POSTGRES_USER/PASSWORD/DB` | Identifiants de la base applicative (interne, jamais exposée). |
| `APP_SECRET_KEY` | Clé Fernet qui chiffre les identifiants stockés (mots de passe de sources de données, clés SSH…). **Unique par client**, ne jamais réutiliser d'un déploiement à l'autre. |
| `JWT_SECRET_KEY` | Signe les jetons de session. Unique par client également. |
| `SMTP_*` | Serveur mail utilisé pour les emails sortants de la plateforme. Utilisez un compte dédié à ce client, pas un compte partagé. |
| `ADMIN_*` | Compte administrateur créé au premier démarrage. Changez le mot de passe par défaut avant de laisser le client l'utiliser. |

### Générer les secrets

```bash
# APP_SECRET_KEY et JWT_SECRET_KEY (générez-en un DIFFÉRENT pour chacun)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Si `python3`/`cryptography` n'est pas installé sur ce serveur (il n'a pas besoin de l'être
pour faire tourner les conteneurs), générez ces clés depuis votre machine de dev, ou
temporairement :

```bash
sudo apt install -y python3-pip && pip3 install cryptography
```

Pour un mot de passe Postgres/admin robuste sans dépendance :

```bash
openssl rand -base64 24
```

### Remplir les vraies valeurs

```bash
nano .env
```

Remplacez chaque `CHANGE_ME_...` par la vraie valeur pour ce client. Sauvegardez
(`Ctrl+O`, `Entrée`, `Ctrl+X` dans nano).

---

## 9. Connexion à Docker Hub

Si les images `delomidit/dataplateforme-backend` / `-frontend` sont privées sur Docker
Hub, connectez-vous avant de tirer les images (une seule fois, les identifiants sont
mis en cache) :

```bash
docker login
```

Si les images sont publiques, cette étape peut être ignorée.

---

## 10. Récupération des images et démarrage

```bash
cd /opt/dataplateforme
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Au premier démarrage, le conteneur backend exécute automatiquement, dans l'ordre :
1. `alembic upgrade head` (applique tout le schéma de base de données)
2. la création idempotente du compte admin (`ADMIN_EMAIL` / `ADMIN_PASSWORD`)
3. le démarrage du serveur API

Ce cycle se répète (sans effet) à chaque redémarrage du conteneur — rien à faire de
spécial pour les démarrages suivants.

---

## 11. Vérification du déploiement

```bash
# les 3 conteneurs doivent être "Up" (et postgres "healthy")
docker compose -f docker-compose.prod.yml ps

# API backend
curl http://localhost:8000/api/health

# interface frontend (doit répondre 200 ou 304)
curl -I http://localhost/
```

En cas de souci, regardez les logs d'un service précis :

```bash
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f postgres
```

---

## 12. Première connexion

Ouvrez `http://<ip-ou-domaine>/` dans un navigateur et connectez-vous avec
`ADMIN_EMAIL` / `ADMIN_PASSWORD` tels que définis dans `.env`. Changez le mot de passe
admin dès la première connexion si ce n'est pas déjà un mot de passe définitif.

---

## 13. Aller plus loin (hors périmètre de ce guide)

- **HTTPS / nom de domaine** : ce guide reste en HTTP simple. Pour une exposition en
  dehors d'un réseau de confiance, placez un reverse proxy (Caddy, nginx + certbot,
  Traefik…) devant les ports `80`/`8000` et gérez le certificat TLS à ce niveau — aucun
  changement requis côté `docker-compose.prod.yml` au-delà d'ajuster `FRONTEND_URL`/
  `BACKEND_PUBLIC_URL` en `https://`.
- **Sauvegardes** : le volume `dataplateforme-postgres-data` contient toute la base
  applicative (utilisateurs, sources, projets médaillon, historique de versions…).
  Mettez en place une sauvegarde régulière (`pg_dump` planifié, ou snapshot du volume)
  adaptée à la politique du client.
- **Airflow / MinIO / Superset des tenants** : provisionnés depuis l'interface une fois
  la plateforme en ligne — pas dans ce guide.

---

## 14. Mise à jour vers une nouvelle version

```bash
cd /opt/dataplateforme
nano .env   # mettre à jour BACKEND_IMAGE_TAG et/ou FRONTEND_IMAGE_TAG
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

`up -d` ne recrée que les conteneurs dont l'image a réellement changé — mettre à jour
uniquement le frontend, par exemple, ne redémarre pas `postgres` ni `backend`.

---

## 15. Dépannage courant

| Symptôme | Cause probable | Solution |
|---|---|---|
| Requêtes bloquées par CORS dans la console navigateur | `FRONTEND_URL` contient `:80`/`:443` explicite | Retirer le port de `FRONTEND_URL` dans `.env`, puis `docker compose -f docker-compose.prod.yml up -d` |
| `docker compose pull` échoue avec "unauthorized" | Images privées, pas de session Docker Hub | `docker login` (§9) |
| `backend` redémarre en boucle | Erreur dans `.env` (variable requise manquante) | `docker compose -f docker-compose.prod.yml logs backend` — le message d'erreur nomme la variable manquante |
| Page blanche / erreur réseau sur le frontend | `BACKEND_PUBLIC_URL` injoignable depuis le navigateur du client (pare-feu, mauvaise IP) | Vérifier que le port `8000` est bien ouvert côté serveur *et* réseau client |
| Port déjà utilisé au démarrage | Un autre service écoute déjà sur `80`/`8000` | Changer `FRONTEND_PORT`/`BACKEND_PORT` dans `.env` |

---

## Annexe — adapter ce guide à une autre distribution

Ce guide cible Ubuntu Server LTS. Sur une autre distribution, seules les étapes
d'installation système changent (§2 à §5) :

- **Debian** : identique à Ubuntu (`apt`, `openssh-server`, `systemctl`), le dépôt Docker
  officiel a une URL différente (`https://download.docker.com/linux/debian`) — remplacer
  `ubuntu` par `debian` dans l'étape 4.
- **RHEL / Rocky / AlmaLinux** : utiliser `dnf` au lieu d'`apt` (`dnf install -y
  openssh-server && systemctl enable --now sshd`), le dépôt Docker officiel pour RPM
  (`https://download.docker.com/linux/rhel/docker-ce.repo`), et `firewalld` au lieu
  d'`ufw` pour le pare-feu (`firewall-cmd --add-port=80/tcp --permanent`, etc.).

Tout le reste du guide (§6 et suivants) est identique, Docker Compose se comportant de
la même façon quelle que soit la distribution sous-jacente.
