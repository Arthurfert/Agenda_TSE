CONFIG = {
    'identifiant_tse': "nom.prenom",
    'mot_de_passe_tse': "votre-mot-de-passe",

    # Nouveau intranet EDITH - laissez vide pour utiliser le défaut https://intranet.telecomste.fr
    # Pour revenir à l'ancien intranet: "https://www.telecom-st-etienne.fr/intranet"
    'base_url': "https://intranet.telecomste.fr",

    # 2FA TOTP - Secret base32 de votre application d'authentification (Google Authenticator, etc.)
    # Pour l'obtenir: dans EDITH > Mon profil > Sécurité > Activer 2FA > scanner le QR code
    # Le secret est affiché sous le QR code (ex: "JBSWY3DPEHPK3PXP")
    # Si vous ne renseignez pas totp_secret, le script vous demandera le code à chaque exécution
    # ou vous pouvez passer --totp-code en argument.
    'totp_secret': "",  # ex: "JBSWY3DPEHPK3PXP"
    # Alternative: code ponctuel (prioritaire si renseigné, mais expire en 30s)
    # 'totp_code': "123456",

    # optionnel, utilisez "primary" pour le calendrier principal
    'calendar_id': "...@group.calendar.google.com"
}
