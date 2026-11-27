# Téléchargeur vidéo

Petite application de bureau Windows (Python + Tkinter) basée sur [yt-dlp](https://github.com/yt-dlp/yt-dlp) : collez un lien, choisissez la qualité voulue dans la liste des formats disponibles, et la vidéo est enregistrée dans le dossier de votre choix.

> ⚠️ **Usage personnel uniquement.** Ne téléchargez que des vidéos qui vous appartiennent ou que vous êtes autorisé(e) à télécharger. Respectez les droits d'auteur et les conditions d'utilisation des plateformes.

## Fonctionnalités

- Liste claire des formats : qualité, résolution, conteneur, codec, IPS, audio et taille estimée.
- Vue simplifiée (une ligne par résolution, H.264 privilégié) ou liste complète de tous les formats vidéo.
- Téléchargements simultanés : sélectionnez plusieurs formats avec Ctrl/Maj, ou analysez un autre lien pendant qu'un téléchargement est en cours.
- File de téléchargements avec progression et état par tâche (jusqu'à trois téléchargements actifs à la fois).
- Fusion automatique vidéo + audio via FFmpeg pour les formats sans piste audio.
- Mémorisation du dossier de téléchargement.
- Utilisation optionnelle des cookies d'un navigateur (Chrome, Edge, Firefox, Brave) pour les vidéos qui renvoient une erreur 403.
- Messages d'erreur explicites (403, vidéo privée ou indisponible, FFmpeg manquant…).
- Exécutable Windows autonome générable avec PyInstaller.

## Prérequis

| Outil | Obligatoire ? | Rôle |
| --- | --- | --- |
| Python 3.10+ | Oui (sauf si vous utilisez l'exécutable) | Lancer l'application |
| [Node.js](https://nodejs.org/) ou Deno / Bun / QuickJS | Recommandé | YouTube exige un moteur JavaScript pour exposer tous ses formats ; sans lui, seul le 360p est proposé |
| [FFmpeg](https://ffmpeg.org/) | Recommandé | Fusionner vidéo et audio pour les formats haute définition |

Installation rapide avec `winget` :

```powershell
winget install OpenJS.NodeJS
winget install Gyan.FFmpeg
```

Le moteur JavaScript et FFmpeg sont détectés automatiquement s'ils sont dans le `PATH`.

## Installation et lancement

```powershell
git clone https://github.com/gambodenis/personal-video-downloader.git
cd personal-video-downloader
py -m pip install -r requirements.txt
py main.py
```

## Utilisation

1. Collez le lien de la vidéo et cliquez sur **Analyser**.
2. Sélectionnez un ou plusieurs formats avec **Ctrl** ou **Maj**.
3. Cliquez sur **Télécharger la sélection** : chaque choix apparaît dans la file et démarre dès qu'une place se libère.
4. Pour une autre vidéo, remplacez le lien et cliquez sur **Analyser** sans attendre les téléchargements en cours.

Le dossier de destination est mémorisé dans `%APPDATA%\VideoDownloader\config.json`. Par défaut, le dossier `Téléchargements` de l'utilisateur est utilisé.

### En cas d'erreur HTTP 403

Si une vidéo accessible dans votre navigateur renvoie une erreur 403 :

1. Sélectionnez ce navigateur dans **Session YouTube**.
2. Fermez complètement le navigateur.
3. Réessayez.

L'application utilise alors les cookies locaux de ce navigateur, pour cette requête uniquement. N'activez cette option qu'en cas de besoin. Elle ne garantit pas le téléchargement : une vidéo privée sans accès, supprimée ou indisponible ne peut pas être récupérée.

## Construire l'exécutable Windows

```powershell
build_windows.bat
```

Le script installe yt-dlp et PyInstaller (connexion Internet requise la première fois), puis génère un fichier autonome dans `dist\VideoDownloader.exe`.

L'exécutable n'embarque **pas** FFmpeg : installez-le séparément et ajoutez-le au `PATH` pour les formats sans audio intégré.

## Tests

```powershell
py -m unittest test_main.py
```

## Structure du projet

```
.
├── main.py                  # Application (logique + interface Tkinter)
├── test_main.py             # Tests unitaires de la logique
├── requirements.txt         # Dépendance d'exécution (yt-dlp)
├── requirements-build.txt   # Dépendances de build (PyInstaller)
├── build_windows.bat        # Script de génération de l'exécutable
└── VideoDownloader.spec     # Configuration PyInstaller
```
