import requests
from bs4 import BeautifulSoup
import json
import os
import re
import getpass
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
    def __init__(self, base_url=None):
        self.session = requests.Session()
        # Nouveau intranet par défaut. Ancien: https://www.telecom-st-etienne.fr/intranet
        self.base_url = (base_url or CONFIG.get('base_url') or "https://intranet.telecomste.fr").rstrip('/')
        self.is_connected = False
        # Fichier pour persister le cookie "remember_device" (30 jours)
        self.cookie_file = os.path.join(os.path.dirname(__file__), '.edith_cookies.json')

        # Headers pour imiter un navigateur
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3',
        })
        self._load_cookies()

    # ---------- Cookie persistence (trusted device 30j) ----------
    def _load_cookies(self):
        if os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, 'r') as f:
                    data = json.load(f)
                # Restore cookies into session
                for k, v in data.get('cookies', {}).items():
                    self.session.cookies.set(k, v, domain='intranet.telecomste.fr')
                logger.debug(f"Cookies restaurés depuis {self.cookie_file}")
            except Exception as e:
                logger.warning(f"Impossible de charger cookies persistés: {e}")

    def _save_cookies(self):
        try:
            cookies = {c.name: c.value for c in self.session.cookies}
            with open(self.cookie_file, 'w') as f:
                json.dump({'cookies': cookies, 'saved_at': datetime.now().isoformat()}, f)
            logger.debug(f"Cookies sauvegardés dans {self.cookie_file}")
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder cookies: {e}")

    def _is_old_intranet(self):
        return 'telecom-st-etienne.fr' in self.base_url

    def _extract_csrf_token(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        # 1. input _token
        inp = soup.find('input', {'name': '_token'})
        if inp and inp.get('value'):
            return inp.get('value')
        # 2. meta csrf-token
        meta = soup.find('meta', {'name': 'csrf-token'})
        if meta and meta.get('content'):
            return meta.get('content')
        return None

    def _is_authenticated(self, html):
        """Heuristique: si on trouve un indicateur de session authentifiée"""
        if not html:
            return False
        # Après login EDITH, la page d'accueil contient souvent "Déconnexion" ou "/logout" ou tableau de bord
        indicators = ['Déconnexion', 'Se déconnecter', '/logout', 'Mon emploi du temps', 'EDT', 'timetable', 'Mon profil']
        # Si on est encore sur /login ou /2fa, pas authentifié
        if 'Vérification en deux étapes' in html or 'name="password"' in html and 'name="username"' in html:
            return False
        return any(ind in html for ind in indicators)

    # ---------- Login ----------
    def login(self, username, password, totp_code=None, totp_secret=None):
        """Connexion à l'intranet EDITH (ou ancien intranet si base_url ancienne)"""
        if self._is_old_intranet():
            return self._login_old(username, password)

        # Si des cookies persistés semblent valides, tester d'abord
        try:
            test = self.session.get(f"{self.base_url}/", timeout=10, allow_redirects=True)
            if test.url.rstrip('/') != f"{self.base_url}/login" and 'Vérification en deux étapes' not in test.text and self._is_authenticated(test.text):
                logger.info("Session déjà authentifiée via cookie persisté (trusted device).")
                self.is_connected = True
                return True
            # Si redirigé vers /timetable après être connecté
            if '/timetable' in test.url and test.status_code == 200:
                self.is_connected = True
                return True
        except Exception:
            pass

        try:
            # 1. GET login pour récupérer CSRF + cookies XSRF-TOKEN / edith-session
            login_url = f"{self.base_url}/login"
            resp = self.session.get(login_url, timeout=15)
            resp.raise_for_status()
            token = self._extract_csrf_token(resp.text)
            if not token:
                logger.warning("CSRF token non trouvé sur /login - tentative sans token")

            # 2. POST login
            login_data = {
                '_token': token,
                'username': username,
                'password': password,
            }
            # Laravel attend aussi le header X-XSRF-TOKEN si présent (axios le fait)
            # On le met si on a le cookie (décodé)
            xsrf = self.session.cookies.get('XSRF-TOKEN')
            if xsrf:
                # requests cookie est déjà url-encodé, on le décode pour le header comme le fait axios ?
                import urllib.parse
                try:
                    decoded = urllib.parse.unquote(xsrf)
                except Exception:
                    decoded = xsrf
                self.session.headers.update({'X-XSRF-TOKEN': decoded})

            response = self.session.post(login_url, data=login_data, allow_redirects=True, timeout=15)
            response.raise_for_status()

            # Cas 2FA requis
            if 'Vérification en deux étapes' in response.text or '/2fa/verify' in response.url or '2fa/verify' in response.text:
                logger.info("2FA requis - vérification TOTP...")
                code = totp_code or CONFIG.get('totp_code')
                secret = totp_secret or CONFIG.get('totp_secret') or CONFIG.get('totp_secret_key') or CONFIG.get('2fa_secret')

                if not code and secret:
                    try:
                        import pyotp
                        # Nettoyage secret (espaces, tirets)
                        clean_secret = re.sub(r'[^A-Za-z0-9]', '', str(secret)).upper()
                        # pyotp gère base32 sans padding
                        totp = pyotp.TOTP(clean_secret)
                        code = totp.now()
                        logger.info(f"Code TOTP généré automatiquement (valable 30s)")
                    except ImportError:
                        raise Exception("pyotp non installé (pip install pyotp) - impossible de générer le code TOTP")
                    except Exception as e:
                        logger.error(f"Erreur génération TOTP: {e}")
                        raise

                if not code:
                    # Prompt interactif si terminal disponible
                    try:
                        # Ne pas afficher le code en clair dans les logs
                        code = input("Entrez le code 2FA à 6 chiffres: ").strip()
                    except EOFError:
                        raise Exception("2FA requis mais aucun code fourni. Ajoutez 'totp_secret' dans information_connexion.py ou fournissez --totp-code")

                if not code or not re.match(r'^\d{6}$', code):
                    raise Exception(f"Code 2FA invalide: '{code}' (attendu 6 chiffres)")

                # Récupérer fresh CSRF sur la page 2FA
                verify_url = f"{self.base_url}/2fa/verify"
                # Si response.url déjà sur verify, on peut réutiliser son token
                token2 = self._extract_csrf_token(response.text)
                if not token2:
                    r2 = self.session.get(verify_url, timeout=15)
                    r2.raise_for_status()
                    token2 = self._extract_csrf_token(r2.text)
                if not token2:
                    raise Exception("Impossible de récupérer CSRF pour 2FA")

                verify_data = {
                    '_token': token2,
                    'code': code,
                    'remember_device': '1',  # coche "Se souvenir 30 jours" pour éviter de redemander
                }
                # Mettre à jour X-XSRF-TOKEN si changé
                xsrf2 = self.session.cookies.get('XSRF-TOKEN')
                if xsrf2:
                    import urllib.parse
                    try:
                        decoded = urllib.parse.unquote(xsrf2)
                    except Exception:
                        decoded = xsrf2
                    self.session.headers.update({'X-XSRF-TOKEN': decoded})

                resp2 = self.session.post(verify_url, data=verify_data, allow_redirects=True, timeout=15)
                resp2.raise_for_status()

                # Vérifier succès
                if 'Vérification en deux étapes' in resp2.text:
                    # Code invalide reste sur la page
                    if 'incorrect' in resp2.text.lower() or 'invalide' in resp2.text.lower() or 'erreur' in resp2.text.lower():
                        raise Exception('Code 2FA incorrect - vérifiez totp_secret ou heure système')
                    raise Exception('Échec 2FA - code rejeté ou expiré (30s). Réessayez.')

                if 'name="password"' in resp2.text and 'name="username"' in resp2.text:
                    raise Exception('Échec 2FA - retour à la page login (session expirée)')

                # Vérifier qu'on est bien authentifié (redirection vers / ou /timetable)
                self.is_connected = True
                self._save_cookies()
                logger.info("Connexion 2FA réussie ! (trusted device 30j)")
                return True

            # Cas sans 2FA (ou 2FA désactivé / trusted device déjà)
            # Vérifier si on est revenu sur login avec erreur
            if 'Identifiants invalides' in response.text or 'Échec de connexion' in response.text or ('name="password"' in response.text and response.url.endswith('/login')):
                # Parfois EDITH affiche l'erreur en français
                raise Exception('Échec de connexion - Vérifiez vos identifiants (ou 2FA requis)')

            # Heuristique succès
            if self._is_authenticated(response.text) or response.url.rstrip('/') == self.base_url or '/timetable' in response.url or '/dashboard' in response.url:
                self.is_connected = True
                self._save_cookies()
                logger.info("Connexion réussie !")
                return True

            # Fallback: si on a été redirigé vers login sans erreur visible mais qu'on a un cookie edith-session récent, on considère connecté
            # Test final: GET /timetable
            check = self.session.get(f"{self.base_url}/timetable", timeout=10, allow_redirects=True)
            if check.status_code == 200 and 'Vérification en deux étapes' not in check.text and 'name="password"' not in check.text:
                self.is_connected = True
                self._save_cookies()
                logger.info("Connexion réussie (via /timetable) !")
                return True

            raise Exception('Échec de connexion - réponse inattendue après login')

        except Exception as e:
            logger.error(f"Erreur de connexion: {e}")
            raise

    def _login_old(self, username, password):
        """Ancien intranet TSE (compatibilité)"""
        try:
            login_url = f"{self.base_url}/login.php"
            self.session.get(login_url, timeout=10)
            login_data = {
                'username': username,
                'password': password,
                'referer': self.base_url
            }
            response = self.session.post(login_url, data=login_data, timeout=10)
            response.raise_for_status()
            self.is_connected = 'Connecté en tant que' in response.text
            if not self.is_connected:
                raise Exception('Échec de connexion - Vérifiez vos identifiants')
            logger.info("Connexion réussie (ancien intranet) !")
            return True
        except Exception as e:
            logger.error(f"Erreur de connexion (ancien): {e}")
            raise

    def get_agenda_for_week(self, week_number, year):
        """Récupère l'agenda pour une semaine donnée (nouveau + ancien)"""
        if not self.is_connected:
            raise Exception('Vous devez être connecté pour accéder à l\'agenda')

        # Ancien intranet
        if self._is_old_intranet():
            try:
                url = f"{self.base_url}/edtetud.php?sem={week_number}&annee={year}"
                response = self.session.get(url, timeout=10)
                response.raise_for_status()
                return self.parse_agenda(response.text, week_number, year)
            except Exception as e:
                logger.error(f"Erreur lors de l'accès à l'agenda de la semaine {week_number}: {e}")
                raise

        # Nouveau intranet EDITH - endpoint réel: /timetable?year=YYYY&week=W
        candidates = [
            f"{self.base_url}/timetable?year={year}&week={week_number}",
            f"{self.base_url}/timetable?week={week_number}&year={year}",
            f"{self.base_url}/timetable?sem={week_number}&annee={year}",
            f"{self.base_url}/timetable/{year}/{week_number}",
        ]

        last_exc = None
        for url in candidates:
            try:
                logger.debug(f"Tentative agenda: {url}")
                response = self.session.get(url, timeout=15, headers={'Accept': 'text/html,application/json'})
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                ctype = response.headers.get('Content-Type', '')
                text = response.text
                if 'application/json' in ctype or text.strip().startswith('{') or text.strip().startswith('['):
                    try:
                        data = response.json()
                        logger.debug(f"Réponse JSON reçue pour semaine {week_number}: {str(data)[:500]}")
                        agenda = self.parse_agenda_json(data)
                        if agenda:
                            return agenda
                    except Exception:
                        pass
                agenda = self.parse_agenda(text, week_number, year)
                total = sum(len(v) for v in agenda.values())
                if total > 0:
                    logger.debug(f"Agenda trouvé via {url} ({total} cours)")
                    return agenda
                # Semaine vide valide: vérifier qu'on est bien sur la bonne semaine (snapshot contient year/week)
                # Si l'URL était sem/annee mais ignorée, on aurait une mauvaise semaine -> on continue
                if 'weekly-timetable-view' in text or 'Mon emploi du temps' in text:
                    # Vérifier que la semaine affichée correspond à la demande
                    if f'Semaine {week_number}' in text or f'"week":{week_number}' in text or f'&quot;week&quot;:{week_number}' in text:
                        logger.debug(f"Agenda vide mais endpoint valide: {url}")
                        return agenda
                    else:
                        logger.debug(f"Semaine affichée ne correspond pas à {week_number} pour {url}, tentative suivante")
                        continue
                logger.debug(f"Aucun cours trouvé via {url}")
            except Exception as e:
                last_exc = e
                logger.debug(f"Echec {url}: {e}")
                continue

        # Fallback Livewire: POST /livewire/update (non nécessaire si GET year/week fonctionne,
        # mais gardé pour compatibilité)
        try:
            fallback_url = f"{self.base_url}/timetable?year={year}&week={week_number}"
            resp = self.session.get(fallback_url, timeout=15)
            if resp.status_code == 200 and 'weekly-timetable-view' in resp.text:
                with open('/tmp/edith_timetable_debug.html', 'w', encoding='utf-8') as f:
                    f.write(resp.text)
                return self.parse_agenda(resp.text, week_number, year)
        except Exception:
            pass

        msg = f"Aucun endpoint agenda n'a répondu pour semaine {week_number}/{year} sur {self.base_url}. "
        msg += "Vérifiez /tmp/edith_timetable_debug.html."
        if last_exc:
            msg += f" Dernière erreur: {last_exc}"
        logger.error(msg)
        raise Exception(msg)

    def parse_agenda_json(self, data):
        """Parse une réponse JSON (si EDITH expose une API)"""
        # Tentative générique: data peut être dict avec clés jours ou liste d'events
        agenda = {
            'lundi': [], 'mardi': [], 'mercredi': [], 'jeudi': [],
            'vendredi': [], 'samedi': [], 'dimanche': []
        }
        jours_map = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
        # Si data est dict avec 'events' ou 'data'
        events = None
        if isinstance(data, dict):
            for key in ['events', 'data', 'timetable', 'edt', 'cours']:
                if key in data and isinstance(data[key], list):
                    events = data[key]
                    break
            if events is None:
                # Peut-être deja mapping jour -> liste
                for k in agenda.keys():
                    if k in data:
                        agenda[k] = data[k]
                if any(len(v) > 0 for v in agenda.values()):
                    return agenda
        elif isinstance(data, list):
            events = data

        if events is None:
            return None

        # Normaliser chaque event JSON vers format interne
        for ev in events:
            try:
                # Essayer de mapper champs communs
                # Exemples possibles: {date, start, end, title, room, teacher, type}
                date_str = ev.get('date') or ev.get('date_str') or ev.get('day')
                start = ev.get('start') or ev.get('debut') or ev.get('hdeb') or ev.get('start_time')
                end = ev.get('end') or ev.get('fin') or ev.get('hfin') or ev.get('end_time')
                title = ev.get('title') or ev.get('titre') or ev.get('summary') or ''
                # Conversion date
                if date_str and 'T' in str(date_str):
                    # ISO datetime
                    dt = datetime.fromisoformat(str(date_str).replace('Z',''))
                    date_str = dt.strftime('%Y-%m-%d')
                    if not start:
                        start = dt.strftime('%Hh%M')
                # Parsing heures si string
                if isinstance(start, str):
                    debut = self.parse_heure(start) if 'h' in start or ':' in start else {'heure':0,'minutes':0}
                elif isinstance(start, dict):
                    debut = start
                else:
                    debut = {'heure':0,'minutes':0}
                if isinstance(end, str):
                    fin = self.parse_heure(end) if 'h' in end or ':' in end else {'heure':0,'minutes':0}
                elif isinstance(end, dict):
                    fin = end
                else:
                    fin = {'heure':0,'minutes':0}

                cours_data = {
                    'date': self.convert_date(str(date_str)) if date_str else '',
                    'debut': debut,
                    'fin': fin,
                    'titre': self.clean_titre(str(title)),
                    'enseignant': ev.get('enseignant') or ev.get('teacher') or ev.get('prof') or '',
                    'salle': ev.get('salle') or ev.get('room') or ev.get('location') or 'Non spécifiée',
                    'type': ev.get('type') or ev.get('typematiere') or 'Autre',
                    'evaluation': bool(ev.get('evaluation', False))
                }
                # Déterminer jour via date
                try:
                    dt = datetime.strptime(cours_data['date'], '%Y-%m-%d')
                    idx = dt.weekday()  # 0 lundi
                    agenda[jours_map[idx]].append(cours_data)
                except Exception:
                    agenda['lundi'].append(cours_data)
            except Exception as e:
                logger.debug(f"Impossible de parser event JSON: {ev} -> {e}")
                continue
        return agenda

    def parse_agenda(self, html, week_number=None, year=None):
        """Parse le HTML de l'agenda et extrait les cours (ancien + nouveau EDITH)"""
        # Détection nouveau format EDITH (Livewire weekly-timetable-view)
        if 'weekly-timetable-view' in html or ('grid-column:' in html and 'grid-row:' in html):
            return self._parse_agenda_edith(html, week_number, year)

        soup = BeautifulSoup(html, 'html.parser')

        agenda = {
            'lundi': [], 'mardi': [], 'mercredi': [], 'jeudi': [],
            'vendredi': [], 'samedi': [], 'dimanche': []
        }

        # Trouver tous les cours (ancien sélecteur .tt-event + nouveaux possibles)
        cours_elements = soup.find_all(class_='tt-event')
        if not cours_elements:
            # Essayer d'autres sélecteurs potentiels sur EDITH
            cours_elements = soup.find_all(class_=re.compile(r'event|edt|calendar|timetable', re.I))
            # Filtrer pour garder seulement ceux qui ont data-day ou data-id
            cours_elements = [c for c in cours_elements if c.get('data-day') is not None or c.get('data-id') is not None]
        if not cours_elements:
            # Dernier recours: chercher tous les div avec style contenant position absolute (FullCalendar-like)
            # On loggue pour debug
            if 'timetable' in html.lower() or 'edt' in html.lower():
                logger.debug(f"parse_agenda: aucun .tt-event trouvé, HTML taille {len(html)}, extrait: {html[:1000]}")

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

                # Fallback EDITH: peut-être que les infos sont en data-attributes directement
                if not all([date_input, heure_debut_input, heure_fin_input]):
                    # Essayer data-date / data-start / data-end
                    d = cours.get('data-date') or cours.get('data-datef') or cours.get('data-dater')
                    hd = cours.get('data-hdeb') or cours.get('data-hdebr') or cours.get('data-start')
                    hf = cours.get('data-hfin') or cours.get('data-hfinr') or cours.get('data-end')
                    if d and hd and hf:
                        # Créer des objets factices avec .get('value')
                        class FakeInput:
                            def __init__(self, v): self._v=v
                            def get(self, k, d=''): return self._v if k=='value' else d
                        date_input = FakeInput(d)
                        heure_debut_input = FakeInput(hd)
                        heure_fin_input = FakeInput(hf)
                    else:
                        # Autre fallback: extraire depuis texte ou title
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
                    'type': type_input.get('value', 'Autre') if type_input else cours.get('data-type', 'Autre'),
                    'evaluation': evaluation or 'btn-danger' in cours.get('class', [])
                }

                # Ajout au bon jour
                jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
                if 0 <= day < len(jours):
                    agenda[jours[day]].append(cours_data)
                else:
                    # Si day invalide, déduire via date
                    try:
                        dt = datetime.strptime(cours_data['date'], '%Y-%m-%d')
                        agenda[jours[dt.weekday()]].append(cours_data)
                    except Exception:
                        agenda['lundi'].append(cours_data)

            except Exception as e:
                logger.error(f"Erreur lors du parsing d'un cours: {e}")
                continue

        return agenda

    def _parse_agenda_edith(self, html, week_number=None, year=None):
        """Parse le nouveau format EDITH (Livewire grid). week/year nécessaires pour calculer les dates."""
        soup = BeautifulSoup(html, 'html.parser')
        agenda = {
            'lundi': [], 'mardi': [], 'mercredi': [], 'jeudi': [],
            'vendredi': [], 'samedi': [], 'dimanche': []
        }
        jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']

        # Déterminer les dates de la semaine: essayer d'abord depuis le snapshot, sinon via week/year
        monday_date = None
        # 1. Essayer d'extraire depuis le header "07/09" ou depuis wire:snapshot year/week
        # Chercher snapshot data-year/week
        m = re.search(r'&quot;year&quot;:(\d+),&quot;week&quot;:(\d+)', html)
        if m:
            try:
                y = int(m.group(1)); w = int(m.group(2))
                monday_date = datetime.fromisocalendar(y, w, 1)
                # Si week/year fournis diffèrent, préférer les paramètres demandés
                if week_number is not None and year is not None:
                    if y != year or w != week_number:
                        monday_date = datetime.fromisocalendar(year, week_number, 1)
            except Exception:
                pass
        if monday_date is None and week_number is not None and year is not None:
            try:
                monday_date = datetime.fromisocalendar(year, week_number, 1)
            except Exception:
                monday_date = None
        if monday_date is None:
            # Dernier recours: parser les dates en tête de colonne: "07/09"
            # Prendre la première date trouvée après Lundi
            header_dates = re.findall(r'>Lundi<.*?(\d{2})/(\d{2})', html, re.S)
            if header_dates:
                d, mo = header_dates[0]
                # Deviner l'année
                y = year or datetime.now().year
                try:
                    monday_date = datetime(y, int(mo), int(d))
                except Exception:
                    monday_date = None
        if monday_date is None:
            logger.warning("Impossible de déterminer le lundi de la semaine pour EDITH, utilisation de week/year fournis")
            if week_number and year:
                try:
                    monday_date = datetime.fromisocalendar(year, week_number, 1)
                except Exception:
                    monday_date = datetime.now()

        # Trouver tous les cours: div avec style grid-column/grid-row et x-data="...open..."
        cours_divs = []
        for div in soup.find_all('div', attrs={'style': lambda s: s and 'grid-column' in s and 'grid-row' in s}):
            # Filtrer: doit contenir un titre et être une carte de cours (x-data avec toggle)
            if div.get('x-data') and 'open' in div.get('x-data'):
                # Vérifier qu'il a un <p> titre
                if div.find('p', class_=lambda c: c and 'font-semibold' in c):
                    cours_divs.append(div)

        logger.debug(f"EDITH: {len(cours_divs)} cours trouvés (grid) pour semaine {week_number}/{year}")

        for cours in cours_divs:
            try:
                style = cours.get('style', '')
                col_m = re.search(r'grid-column:\s*(\d+)', style)
                if not col_m:
                    continue
                col = int(col_m.group(1))
                day_idx = col - 2  # 2=Lundi
                if not (0 <= day_idx < 7):
                    continue
                jour = jours[day_idx]
                # Date du cours
                cours_date = monday_date + timedelta(days=day_idx) if monday_date else None
                date_str = cours_date.strftime('%Y-%m-%d') if cours_date else ''

                # Titre: premier p truncate font-semibold
                title_p = cours.find('p', class_=lambda c: c and 'font-semibold' in c and 'truncate' in c)
                titre_raw = title_p.get_text(strip=True) if title_p else ''
                titre = self.clean_titre(titre_raw)

                # Bloc flexible avec teacher/salle/heure
                flex_div = cours.find('div', class_=lambda c: c and 'flex' in c and 'flex-col' in c)
                ps = flex_div.find_all('p') if flex_div else cours.find_all('p')
                # ps[0]=titre, ps[1]=teacher, ps[2]=salle, ps[3]=heure
                enseignant = ''
                salle_raw = ''
                time_raw = ''
                if flex_div and len(ps) >= 2:
                    # Filtrer les p vides
                    texts = [p.get_text(strip=True) for p in ps]
                    # texts[0]=titre, texts[1]=teacher, texts[2]=salle, texts[3]=time
                    if len(texts) > 1 and texts[1]:
                        enseignant = texts[1]
                    if len(texts) > 2 and texts[2]:
                        salle_raw = texts[2]
                    if len(texts) > 3 and texts[3]:
                        time_raw = texts[3]
                # Fallback via template tooltip pour salle/enseignant/type
                tmpl = cours.find('template')
                tmpl_soup = None
                if tmpl:
                    tmpl_soup = BeautifulSoup(tmpl.decode_contents(), 'html.parser')
                    # Enseignant depuis "Enseignant : ..."
                    for p in tmpl_soup.find_all('p'):
                        txt = p.get_text(strip=True)
                        if txt.startswith('Enseignant'):
                            enseignant = txt.split(':', 1)[-1].strip()
                        elif txt.startswith('Salle'):
                            salle_raw = txt.split('Salle', 1)[-1].strip().lstrip(':').strip()
                        elif re.search(r'\d{1,2}:\d{2}\s*[—–-]\s*\d{1,2}:\d{2}', txt):
                            time_raw = re.search(r'\d{1,2}:\d{2}\s*[—–-]\s*\d{1,2}:\d{2}', txt).group(0)

                # Type depuis badge dans template
                type_cours = 'Autre'
                if tmpl_soup:
                    badge = tmpl_soup.find('span', class_=lambda c: c and 'rounded-full' in c)
                    if badge:
                        type_cours = badge.get_text(strip=True)
                # Normaliser type
                if type_cours not in ['CM', 'TD', 'TP']:
                    # Déduire depuis titre si contient _CM/_TD etc.
                    if '_CM' in titre_raw: type_cours = 'CM'
                    elif '_TD' in titre_raw: type_cours = 'TD'
                    elif '_TP' in titre_raw: type_cours = 'TP'

                # Heures
                debut = {'heure': 0, 'minutes': 0}
                fin = {'heure': 0, 'minutes': 0}
                if time_raw:
                    # time_raw peut être "11:00–12:30" ou "11:00 — 12:30"
                    time_clean = time_raw.replace('—', '-').replace('–', '-')
                    parts = re.split(r'\s*-\s*', time_clean)
                    if len(parts) == 2:
                        debut = self.parse_heure(parts[0].strip())
                        fin = self.parse_heure(parts[1].strip())

                # Salle nettoyée: essayer extract_salle puis fallback intelligent pour EDITH
                salle = self.extract_salle(salle_raw) if salle_raw else 'Non spécifiée'
                if salle == 'Non spécifiée' and salle_raw:
                    # Nettoyer préfixes EDITH: "TP RESEAU_J202" -> "RESEAU_J202", "TD_A101" -> "A101"
                    cleaned = salle_raw.strip()
                    # Enlever préfixe "TP " / "TD " avec espace
                    if cleaned.startswith('TP '):
                        cleaned = cleaned[3:].strip()
                    elif cleaned.startswith('TD '):
                        cleaned = cleaned[3:].strip()
                    # Garder brut si potable (ex: J016, A018, RESEAU_J202, EXTERIEUR)
                    if cleaned and cleaned not in ['', 'Non spécifiée']:
                        # Si cleaned encore avec TD_ prefix, l'enlever
                        if cleaned.startswith('TD_'):
                            cleaned = cleaned[3:]
                        # Accepter lettres/chiffres/_/espace mais pas trop exotique
                        if re.match(r'^[A-Z0-9_ ]+$', cleaned):
                            salle = cleaned.replace(' ', '_') if ' ' not in salle_raw else cleaned
                        else:
                            salle = cleaned

                # Evaluation: badge rouge ou titre contient evaluation
                evaluation = False
                if tmpl_soup:
                    txt_low = tmpl_soup.get_text().lower()
                    evaluation = 'evaluation' in txt_low or 'examen' in txt_low
                if 'evaluation' in titre_raw.lower() or 'examen' in titre_raw.lower():
                    evaluation = True

                cours_data = {
                    'date': date_str,
                    'debut': debut,
                    'fin': fin,
                    'titre': titre,
                    'enseignant': enseignant,
                    'salle': salle,
                    'type': type_cours,
                    'evaluation': evaluation
                }
                agenda[jour].append(cours_data)
            except Exception as e:
                logger.error(f"Erreur parsing cours EDITH: {e}")
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
        """Parse une heure au format HHhMM ou HH:MM"""
        if not heure_str:
            return {'heure': 0, 'minutes': 0}
        heure_str = str(heure_str).strip()
        if 'h' in heure_str:
            h, m = heure_str.split('h')
            return {'heure': int(h), 'minutes': int(m)}
        if ':' in heure_str:
            h, m = heure_str.split(':')
            return {'heure': int(h), 'minutes': int(m)}
        # Format compact HHMM ?
        if heure_str.isdigit() and len(heure_str) in (3,4):
            if len(heure_str)==3:
                return {'heure': int(heure_str[0]), 'minutes': int(heure_str[1:])}
            return {'heure': int(heure_str[:2]), 'minutes': int(heure_str[2:])}
        return {'heure': 0, 'minutes': 0}

    def convert_date(self, date_str):
        """Convertit DD-MM-YYYY en YYYY-MM-DD, ou conserve YYYY-MM-DD"""
        if not date_str:
            return date_str
        date_str = str(date_str).strip()
        if '-' in date_str:
            parts = date_str.split('-')
            if len(parts) == 3:
                # Détecter format
                if len(parts[0]) == 4:  # déjà YYYY-MM-DD
                    return date_str
                return f"{parts[2]}-{parts[1]}-{parts[0]}"
        if '/' in date_str:
            parts = date_str.split('/')
            if len(parts)==3:
                if len(parts[2])==4:
                    return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        return date_str

def get_current_school_week():
    """Obtient la semaine scolaire actuelle (Format ISO)"""
    now = datetime.now()
    # Utilisation du calendrier ISO pour être précis
    # isocalendar() retourne un tuple (année, semaine, jour)
    iso_cal = now.isocalendar()

    return {'week': iso_cal[1], 'year': iso_cal[0]}

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
    parser.add_argument('--totp-code', default=None, help='Code 2FA à 6 chiffres (sinon utilise totp_secret du fichier config)')
    parser.add_argument('--base-url', default=None, help='URL de base intranet (défaut: https://intranet.telecomste.fr)')

    args = parser.parse_args()

    try:
        # Connexion à TSE
        logger.info("Connexion à l'intranet TSE...")
        base_url = args.base_url or CONFIG.get('base_url') or "https://intranet.telecomste.fr"
        session = TSESession(base_url=base_url)
        session.login(CONFIG['identifiant_tse'], CONFIG['mot_de_passe_tse'], totp_code=args.totp_code)

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

        # Calculer la date limite (Lundi de la semaine actuelle à 00h00)
        today = datetime.now()
        start_of_current_week = today - timedelta(days=today.weekday())
        start_of_current_week = start_of_current_week.replace(hour=0, minute=0, second=0, microsecond=0)

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
                        # Vérification de la date pour éviter les doublons passés
                        try:
                            cours_date = datetime.strptime(cours['date'], '%Y-%m-%d')
                            if cours_date < start_of_current_week:
                                # On ignore silencieusement les cours antérieurs au début du nettoyage
                                continue
                        except ValueError:
                            pass

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
