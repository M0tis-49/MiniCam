# MiniCam

MiniCam affiche ta webcam dans une petite fenetre Windows.

## Installation

1. Double-clique sur `install.bat`.
2. Attends la fin de l'installation.
3. Double-clique sur `MiniCam.vbs`.

## Utilisation

- La fenetre affiche directement la camera.
- Double-clique sur l'image, ou appuie sur `F10` / `P`, pour ouvrir les parametres.
- `F11` active ou desactive le plein ecran.
- Le menu permet de regler la camera, la fenetre, l'image et les options prevues pour l'ecran.
- Active `Modification en direct` dans les parametres pour voir les effets image et fenetre pendant les reglages.
- Les parametres incluent le choix de camera, les tailles rapides, la taille personnalisee, le mode mini, le plein ecran, le miroir, la luminosite, le contraste, la saturation, le zoom et les FPS.
- Les reglages sont enregistres dans `minicam_settings.json`.
- `Echap` ferme MiniCam.
- En cas d'erreur, un fichier `minicam.log` est cree dans ce dossier.

Si la camera par defaut ne s'ouvre pas, lance avec un autre numero :

```bat
.venv\Scripts\python.exe minicam.py 1
```

La fonction pour afficher l'ecran sera ajoutee plus tard.
