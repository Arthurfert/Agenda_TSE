@echo off
echo Synchronisation Emploi du Temps TSE
echo ====================================
cd /d "C:\chemin\vers\votre\dossier\Agenda"

echo Installation/mise a jour des dependances... (à supprimer après la première exécution)
pip install -r requirements.txt

echo Lancement du script...
python emploi-du-temps-tse.py