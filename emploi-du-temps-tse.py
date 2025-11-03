import requests
from bs4 import BeautifulSoup
import json
import os
import re
from datetime import datetime, timedelta
import argparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from information_connexion import CONFIG
from logger.logger import logger

# Scopes pour l'API Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']

class TSESession:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://www.telecom-st-etienne.fr/intranet"
        self.is_connected = False
        
        # Headers pour imiter un navigateur
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3',
        })

    def login(self, username, password):
        """Connexion à l'intranet TSE"""
        try:
            # Première requête pour obtenir les cookies
            login_url = f"{self.base_url}/login.php"
            self.session.get(login_url)
            
            # Données de connexion
            login_data = {
                'username': username,
                'password': password,
                'referer': self.base_url
            }
            
            # Requête de connexion
            response = self.session.post(login_url, data=login_data)
            response.raise_for_status()
            
            # Vérifier si la connexion a réussi
            self.is_connected = 'Connecté en tant que' in response.text
            if not self.is_connected:
                raise Exception('Échec de connexion - Vérifiez vos identifiants')
                
            logger.info("Connexion réussie !")
            return True
            
        except Exception as e:
            logger.error(f"Erreur de connexion: {e}")
            raise

    def get_agenda_for_week(self, week_number, year):
        """Récupère l'agenda pour une semaine donnée"""
        if not self.is_connected:
            raise Exception('Vous devez être connecté pour accéder à l\'agenda')
        
        try:
            url = f"{self.base_url}/edtetud.php?sem={week_number}&annee={year}"
            response = self.session.get(url)
            response.raise_for_status()
            
            return self.parse_agenda(response.text)
            
        except Exception as e:
            logger.error(f"Erreur lors de l'accès à l'agenda de la semaine {week_number}: {e}")
            raise

    def parse_agenda(self, html):
        """Parse le HTML de l'agenda et extrait les cours"""
        soup = BeautifulSoup(html, 'html.parser')
        
        agenda = {
            'lundi': [], 'mardi': [], 'mercredi': [], 'jeudi': [], 
            'vendredi': [], 'samedi': [], 'dimanche': []
        }
        
        # Trouver tous les cours
        cours_elements = soup.find_all(class_='tt-event')
        
        for cours in cours_elements:
            try:
                # Déterminer le type de cours
                is_reservation = 'btn-secondary' in cours.get('class', [])
                cours_id = cours.get('data-id')
                day = int(cours.get('data-day', 0))
                
                # Préfixes selon le type
                date_prefix = 'dater' if is_reservation else 'datef'
                heure_debut_prefix = 'hdebr' if is_reservation else 'hdeb'
                heure_fin_prefix = 'hfinr' if is_reservation else 'hfin'
                
                # Récupération des données
                date_input = soup.find('input', id=f'{date_prefix}{cours_id}')
                heure_debut_input = soup.find('input', id=f'{heure_debut_prefix}{cours_id}')
                heure_fin_input = soup.find('input', id=f'{heure_fin_prefix}{cours_id}')
                type_input = soup.find('input', id=f'typematiere{cours_id}') if not is_reservation else None
                
                if not all([date_input, heure_debut_input, heure_fin_input]):
                    continue
                
                # Extraction des informations
                titre = cours.get_text().strip().split('\n')[0] if cours.get_text() else ''
                cours_text = cours.get_text()
                
                # Détection évaluation
                evaluation = 'evaluation' in cours_text.lower()
                
                # Détection salle
                salle = self.extract_salle(cours_text)
                if 'J021' in salle and 'J022' in salle:
                    salle = 'J021/J022'
                    evaluation = True
                
                # Détection enseignant
                enseignant = self.extract_enseignant(cours_text, evaluation)
                
                # Nettoyage du titre
                titre = self.clean_titre(titre)

                # Parsing des heures
                debut = self.parse_heure(heure_debut_input.get('value', ''))
                fin = self.parse_heure(heure_fin_input.get('value', ''))
                
                # Création de l'objet cours
                cours_data = {
                    'date': self.convert_date(date_input.get('value', '')),
                    'debut': debut,
                    'fin': fin,
                    'titre': titre,
                    'enseignant': enseignant,
                    'salle': salle,
                    'type': type_input.get('value', 'Autre') if type_input else 'Autre',
                    'evaluation': evaluation or 'btn-danger' in cours.get('class', [])
                }
                
                # Ajout au bon jour
                jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
                if 0 <= day < len(jours):
                    agenda[jours[day]].append(cours_data)
                    
            except Exception as e:
                logger.error(f"Erreur lors du parsing d'un cours: {e}")
                continue
        
        return agenda

    def extract_salle(self, cours_text):
        """Extrait la salle du texte du cours"""
        # Amphi
        amphi_match = re.findall(r'AMPHI_(J02[012])', cours_text)
        if amphi_match:
            return '/'.join(amphi_match)
        
        # TD, TP, INFO
        salle_match = re.search(r'(?:TD_|TP ELEC_|INFO_)([A-Z0-9_]+)', cours_text)
        if salle_match:
            salle = salle_match.group(1)
            # Enlever le premier "_" s'il existe
            if salle.startswith('_'):
                salle = salle[1:]
            return salle
        
        # FST (Forges)
        fst_match = re.search(r'FST_\s*(L\d{3})', cours_text)
        if fst_match:
            return f"{fst_match.group(1)} (Forges)"
        
        # Cas spéciaux
        if 'newsplex' in cours_text.lower():
            return 'Newsplex'
        if 'visio' in cours_text.lower():
            return 'Visioconférence'
        
        return 'Non spécifiée'

    def extract_enseignant(self, cours_text, evaluation):
        """Extrait l'enseignant du texte du cours"""
        text_for_prof = cours_text
        if evaluation:
            text_for_prof = re.sub(r' - EVALUATION$', '', text_for_prof)
        
        tirets = text_for_prof.split('- ')
        if len(tirets) > 2:
            return tirets[2].strip()
        return ''

    def clean_titre(self, titre):
        """Nettoie le titre du cours"""
        # Supprimer les références de salles
        titre = re.sub(r'AMPHI_[A-Z0-9]+|TD_[A-Z0-9_]+|INFO_[A-Z0-9]+|TP_[A-Z0-9_]+', '', titre)
        
        # Supprimer les suffixes et préfixes courants
        titre = re.sub(r'_CM\b', '', titre)
        titre = re.sub(r'_TD\s*\([^)]*\)', '', titre)
        titre = re.sub(r'_TP\s*\([^)]*\)', '', titre)
        
        # Supprimer les tirets en début/fin et tout ce qui suit un tiret isolé
        titre = titre.split('- ')[0].strip()
        titre = titre.split('_')[0].strip()
        
        # Nettoyer les espaces multiples et les caractères indésirables
        titre = re.sub(r'\s+', ' ', titre)
        titre = re.sub(r'^[-\s]+|[-\s]+$', '', titre)
        
        # Supprimer les horaires au format HHhMM
        titre = re.sub(r'\d{1,2}h\d{2}', '', titre)
        # Supprimer aussi les horaires au format HH:MM
        titre = re.sub(r'\d{1,2}:\d{2}', '', titre)
        
        # Nettoyer les espaces en trop après suppression
        titre = re.sub(r'\s+', ' ', titre).strip()

        return titre.strip()

    def parse_heure(self, heure_str):
        """Parse une heure au format HHhMM"""
        if 'h' in heure_str:
            h, m = heure_str.split('h')
            return {'heure': int(h), 'minutes': int(m)}
        return {'heure': 0, 'minutes': 0}

    def convert_date(self, date_str):
        """Convertit DD-MM-YYYY en YYYY-MM-DD"""
        if '-' in date_str:
            parts = date_str.split('-')
            if len(parts) == 3:
                return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return date_str

def get_current_school_week():
    """Obtient la semaine scolaire actuelle"""
    now = datetime.now()
    # Calcul simple du numéro de semaine
    start_of_year = datetime(now.year, 1, 1)
    days_since_start = (now - start_of_year).days
    week_number = (days_since_start // 7) + 1
    
    return {'week': week_number, 'year': now.year}

def get_next_school_weeks(current_week, count):
    """Obtient les prochaines semaines scolaires"""
    weeks = []
    week = current_week['week']
    year = current_week['year']
    
    for i in range(count):
        weeks.append({'week': week, 'year': year})
        week += 1
        if week > 52:
            week = 1
            year += 1
    
    return weeks

def authorize():
    """Authentification Google Calendar"""
    creds = None
    
    # Le fichier token.json stocke les tokens d'accès et de rafraîchissement
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # Si pas de credentials valides, faire l'authentification
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                raise FileNotFoundError('Fichier credentials.json manquant. Suivez les instructions dans le README.')
            
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Sauvegarder les credentials
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return creds

def get_color_id(cours, args):
    """Détermine la couleur selon le type de cours"""
    titre = cours['titre'].lower()
    type_cours = cours['type'].upper()
    evaluation = cours['evaluation']
    
    if 'projet' in titre and 'gestion' not in titre:
        return args.couleur_autre
    elif 'examen' in titre or 'evaluation' in titre:
        return args.couleur_cm_eval
    else:
        if type_cours == 'CM':
            return args.couleur_cm_eval if evaluation else args.couleur_cm
        elif type_cours == 'TD':
            return args.couleur_td_eval if evaluation else args.couleur_td
        elif type_cours == 'TP':
            return args.couleur_tp
        else:
            return args.couleur_autre

def add_event(service, cours, calendar_id, args):
    """Ajoute un événement au calendrier Google avec gestion du rate limiting"""
    import time
    
    try:
        # Création de la date/heure de début
        date_str = cours['date']
        start_datetime = datetime.strptime(date_str, '%Y-%m-%d')
        start_datetime = start_datetime.replace(
            hour=cours['debut']['heure'], 
            minute=cours['debut']['minutes']
        )
        
        # Création de la date/heure de fin
        end_datetime = datetime.strptime(date_str, '%Y-%m-%d')
        end_datetime = end_datetime.replace(
            hour=cours['fin']['heure'], 
            minute=cours['fin']['minutes']
        )
        
        # Création de l'événement
        event = {
            'summary': cours['titre'],
            'location': cours['salle'] or '',
            'description': f"Enseignant: {cours['enseignant']}",
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': 'Europe/Paris',
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': 'Europe/Paris',
            },
            'colorId': str(get_color_id(cours, args)),
            'reminders': {
                'useDefault': False,
                'overrides': []
            },
            'extendedProperties': {
                'private': {
                    'origin': f"EDT_TSE_{CONFIG['identifiant_tse']}",
                    'tse_cours_id': f"{cours['date']}_{cours['debut']['heure']:02d}{cours['debut']['minutes']:02d}_{cours['titre'].replace(' ', '_')}"
                }
            }
        }
        
        # Insertion avec retry en cas de rate limit
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                service.events().insert(calendarId=calendar_id, body=event).execute()
                logger.debug(f"Événement créé: {cours['titre']}")
                break
                
            except HttpError as e:
                if e.resp.status == 403 and 'rateLimitExceeded' in str(e):
                    retry_count += 1
                    wait_time = 2 ** retry_count
                    logger.warning(f"Rate limit pour création. Attente de {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Erreur lors de la création de l'événement {cours['titre']}: {e}")
                    break
            except Exception as e:
                logger.error(f"Erreur lors de la création de l'événement {cours['titre']}: {e}")
                break
        
        # Petite pause après chaque création
        time.sleep(0.1)
        
    except Exception as e:
        logger.error(f"Erreur lors de la création de l'événement {cours['titre']}: {e}")

def clear_calendar(service, calendar_id):
    """Supprime TOUS les événements de la semaine actuelle et futures avec gestion du rate limiting"""
    import time
    
    try:
        # Calculer le début de la semaine actuelle (lundi)
        today = datetime.now()
        days_since_monday = today.weekday()  # 0 = lundi, 6 = dimanche
        start_of_week = today - timedelta(days=days_since_monday)
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        
        logger.info(f"Suppression des événements à partir du {start_of_week.strftime('%d/%m/%Y')} (début de semaine)...")
        
        # Récupération de tous les événements à partir du début de la semaine
        events_result = service.events().list(
            calendarId=calendar_id,
            maxResults=2500,
            timeMin=start_of_week.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            logger.info('Aucun événement à supprimer.')
            return

        logger.info(f'Suppression de {len(events)} événements (semaine actuelle et futures)...')

        # Suppression de tous les événements avec gestion du rate limiting
        deleted_count = 0
        for i, event in enumerate(events):
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    service.events().delete(
                        calendarId=calendar_id,
                        eventId=event['id']
                    ).execute()
                    deleted_count += 1
                    break  # Succès, sortir de la boucle de retry
                    
                except HttpError as e:
                    if e.resp.status == 403 and 'rateLimitExceeded' in str(e):
                        retry_count += 1
                        wait_time = 2 ** retry_count  # Backoff exponentiel: 2s, 4s, 8s
                        logger.warning(f"Rate limit atteint. Attente de {wait_time}s... (tentative {retry_count}/{max_retries})")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Erreur lors de la suppression d'un événement: {e}")
                        break
                except Exception as e:
                    logger.error(f"Erreur lors de la suppression d'un événement: {e}")
                    break
            
            # Pause entre chaque suppression pour éviter le rate limiting
            if (i + 1) % 5 == 0:  # Pause plus longue toutes les 5 suppressions
                time.sleep(1)
            else:
                time.sleep(0.2)  # Pause courte entre chaque suppression
            
            # Afficher le progrès
            if deleted_count % 10 == 0:
                logger.info(f'  Supprimé {deleted_count}/{len(events)} événements...')

        logger.info(f'Nettoyage terminé. {deleted_count} événements supprimés.')

    except Exception as e:
        logger.error(f'Erreur lors du nettoyage: {e}')

def main():
    # Arguments de ligne de commande
    parser = argparse.ArgumentParser(description='Synchronisation emploi du temps TSE vers Google Calendar')
    parser.add_argument('--tier-temps', action='store_true', help='Inclure les cours "1/3 TEMPS"')
    parser.add_argument('--couleur-td', default='1', help='Couleur des TD (défaut: 1)')
    parser.add_argument('--couleur-td-eval', default='6', help='Couleur des évaluations TD (défaut: 6)')
    parser.add_argument('--couleur-cm', default='10', help='Couleur des CM (défaut: 10)')
    parser.add_argument('--couleur-cm-eval', default='11', help='Couleur des évaluations CM (défaut: 11)')
    parser.add_argument('--couleur-tp', default='5', help='Couleur des TP (défaut: 5)')
    parser.add_argument('--couleur-autre', default='8', help='Couleur pour les autres cours (défaut: 8)')
    
    args = parser.parse_args()
    
    try:
        # Connexion à TSE
        logger.info("Connexion à l'intranet TSE...")
        session = TSESession()
        session.login(CONFIG['identifiant_tse'], CONFIG['mot_de_passe_tse'])
        
        # Authentification Google
        logger.info("Authentification Google Calendar...")
        creds = authorize()
        service = build('calendar', 'v3', credentials=creds)
        
        calendar_id = CONFIG.get('calendar_id', 'primary')
        
        # Nettoyage du calendrier
        logger.info("Nettoyage du calendrier...")
        clear_calendar(service, calendar_id)
        
        # Récupération des semaines à traiter
        current_week = get_current_school_week()
        weeks = get_next_school_weeks(current_week, 11)
        
        # Traitement de chaque semaine
        for week_info in weeks:
            week_num = week_info['week']
            year = week_info['year']

            logger.info(f"Récupération semaine {week_num} de {year}...")

            try:
                agenda = session.get_agenda_for_week(week_num, year)
                
                # Ajout des événements
                jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
                for jour in jours:
                    for cours in agenda[jour]:
                        # Filtrage des cours
                        if cours['titre'] == 'LV2' or (not args.tier_temps and '1/3 temps' in cours['titre'].lower()):
                            logger.info(f"Ignoré: {cours['titre']}")
                            continue
                        
                        add_event(service, cours, calendar_id, args)
                
                # Pause plus longue entre les semaines
                import time
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Erreur pour la semaine {week_num}: {e}")
                continue

        logger.info("Synchronisation terminée !")

    except Exception as e:
        logger.error(f"Erreur générale: {e}")

if __name__ == '__main__':
    main()