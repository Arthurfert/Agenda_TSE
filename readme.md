# Synchronisation Emploi du Temps TSE → Google Calendar

Ce projet permet de synchroniser automatiquement votre emploi du temps de Télécom Saint-Étienne avec votre calendrier Google.

## 🚀 Fonctionnalités

- **Synchronisation automatique** : Récupère l'emploi du temps des 11 prochaines semaines
- **Nettoyage intelligent** : Supprime les anciens événements avant d'ajouter les nouveaux
- **Couleurs personnalisables** : Différentes couleurs selon le type de cours (CM, TD, TP, évaluations)
- **Détection automatique** : Reconnaissance des salles, enseignants et évaluations
- **Options configurables** : Inclure ou exclure certains types de cours

## 📋 Prérequis

- Python & pip
- Un compte Google
- Vos identifiants TSE

## 🛠️ Installation

1. **Téléchargez ce projet**

(Normalement si vous lisez ceci c'est déjà fait)

2. **Installez les dépendances**

> vous devez avoir installé pip !

```bash
pip install -r requirements.txt
```

## 🔐 Configuration des credentials Google

### Étape 1 : Créer un projet Google Cloud

1. Rendez-vous sur la [Console Google Cloud](https://console.cloud.google.com/)
2. Créez un nouveau projet ou sélectionnez un projet existant
3. Activez l'API Google Calendar :
   - Allez dans "APIs & Services" > "Library"
   - Recherchez "Google Calendar API"
   - Cliquez sur "Enable"

### Étape 2 : Créer les credentials OAuth 2.0

1. Allez dans "APIs & Services" > "Credentials"
2. Cliquez sur "Create Credentials" > "OAuth client ID"
3. Si c'est votre première fois, vous devrez configurer l'écran de consentement OAuth :
   - Choisissez "External" pour les utilisateurs personnels
   - Remplissez les champs obligatoires (nom de l'app, email)
   - Ajoutez votre email dans les "Test users"
4. Revenez à la création des credentials :
   - Type d'application : "Desktop application"
   - Nom : "TSE Calendar Sync" (ou autre c'est pas important)
5. Téléchargez le fichier JSON généré
6. **Renommez ce fichier en `credentials.json`** et placez-le à la racine du projet (avec tous les autres fichiers)

### Étape 3 : Configurer vos informations de connexion

Modifiez le fichier [`information_connexion.js`](information_connexion.js) avec vos données :

```python
CONFIG = {
    'identifiant_tse': "nom.prénom",
    'mot_de_passe_tse': "VotreMotDePasseIntranet",
    
    # ID d'un calendrier spécifique
    'calendar_id': "IDduCalendrier"}
```

### 📅 Utilisation avec un calendrier spécifique (RECOMMANDÉ)

Pour utiliser un calendrier Google spécifique plutôt que le calendrier principal :

1. Créez un nouveau calendrier dans Google Calendar
2. Allez dans les paramètres du calendrier
3. Copiez l'ID du calendrier (format : `xxxxx@group.calendar.google.com`)
4. Mettez cet ID dans [`information_connexion.py`](information_connexion.py) :

```python
CONFIG = {
    # ... autres paramètres
    'calendar_id': "votre-calendar-id@group.calendar.google.com"
}
```

> Utilisez votre calendrier principal va supprimer les autres événements.

## 🎯 Utilisation

### Synchronisation simple
```bash
python emploi-du-temps-tse.py
```

### Options avancées

```bash
# Inclure les cours "1/3 TEMPS"
python emploi-du-temps-tse.py --tier-temps

# Personnaliser les couleurs (IDs de couleurs Google Calendar : 1-11)
python emploi-du-temps-tse.py --couleur-td=2 --couleur-cm=3 --couleur-tp=4
```

#### Couleurs disponibles (Google Calendar)
- `1`  : Lavande (défaut TD)
- `2`  : Sauge
- `3`  : Raisin
- `4`  : Flamant
- `5`  : Banane
- `6`  : Mandarine
- `7`  : Paon
- `8`  : Graphite (défaut autres)
- `9`  : Myrtille
- `10` : Basilic (défaut CM)
- `11` : Tomate (défaut évaluations CM)

#### Options de couleurs complètes
- `--couleur-td` : Couleur des TD (défaut: 1)
- `--couleur-td-eval` : Couleur des évaluations TD (défaut: 6)
- `--couleur-cm` : Couleur des CM (défaut: 10)
- `--couleur-cm-eval` : Couleur des évaluations CM (défaut: 11)
- `--couleur-tp` : Couleur des TP (défaut: 5)
- `--couleur-autre` : Couleur pour les autres cours (défaut: 8)

## 🔄 Automatisation

### Sous Windows (Planificateur de tâches)
1. Ouvrez le Planificateur de tâches
2. Créez une tâche de base
3. Programmez l'exécution (ex: tous les matins ou à chaque démarrage)
4. Action : Démarrer un programme
   Insérer le fichier [`.bat disponible`](Sync_TSE_Agenda.bat)

## 🛡️ Sécurité

- **Ne partagez jamais** vos fichiers `credentials.json`, `token.json` et `information_connexion.js`
- Ces fichiers contiennent des informations sensibles (mots de passe, tokens d'accès)
- Ajoutez-les à votre `.gitignore` si vous versionnez le projet !

## 🐛 Résolution des problèmes

### Erreur d'authentification Google
1. Supprimez le fichier `token.json`
2. Relancez le script
3. Suivez les instructions de connexion qui s'affichent

### Erreur de connexion TSE
- Vérifiez vos identifiants dans [`information_connexion.py`](information_connexion.py)
- Assurez-vous que votre compte TSE est actif

### Événements non créés
- Vérifiez que l'API Google Calendar est bien activée
- Vérifiez les permissions de votre calendrier

## 📝 Structure des fichiers

```
.
├── emploi-du-temps-tse.py    # Script principal
├── information_connexion.py  # Configuration utilisateur
├── credentials.json          # Credentials Google (à créer)
├── token.json                # Token généré automatiquement
├── requirements.txt          # Dépendances du projet
├── Sync_TSE_Agenda.bat       # Exemple type de fichier .bat pour l'automatisation
├── logger
|   └── logger.py             # Système de log colorisé (sans sauvegarde)
└── README.md                 # Ce fichier
```

## 🤝 Contribution

Les contributions sont les bienvenues !

## 🧑‍💻 Auteurs

Ce projet à été quasiment entièrement codé par **Aubin Sionville** en javascript.
Je (*Arthur Fert*) n'ai ajouté que le readme pour plus d'accessibilité, fais quelques légères modifications de détection des salles/partiels,et effectué le portage sur python.