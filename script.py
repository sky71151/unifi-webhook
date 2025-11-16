#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    UniFi Protect Webhook Service                             ║
║                                                                              ║
║  Een Flask-gebaseerde webhook service voor UniFi Protect alarms              ║
║  Ondersteunt GET en POST requests met foto/thumbnail doorsturen              ║
║                                                                              ║
║  Features:                                                                   ║
║  • Ontvang alarms van UniFi Protect (beweging, kenteken herkenning, etc.)    ║
║  • Verstuur foto's naar PC Display                                           ║
║  • SIP call integratie                                                       ║
║  • Email notificaties met foto                                               ║
║  • Loxone integratie via UDP                                                 ║
║  • Apparaat logging                                                          ║
║                                                                              ║
║  Auteur: [Van Baelen Rob]                                                    ║
║  Datum: November 2025                                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# =============================================================================
# IMPORTS
# =============================================================================

from flask import Flask, request, jsonify, send_from_directory, render_template_string, send_file
import json
import logging
import glob
import mimetypes
from datetime import datetime
import os
import socket
import subprocess
import sys
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import base64

# =============================================================================
# LOGGING SETUP
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# FLASK APP INITIALISATIE
# =============================================================================

app = Flask(__name__)

# =============================================================================
# CONFIGURATIE - Pas deze waarden aan naar jouw setup
# =============================================================================

# Loxone configuratie
LOXONE_IP = "192.168.1.100"  # IP adres van je Loxone Miniserver
LOXONE_PORT = 1234           # UDP poort

# SIP configuratie (optioneel)
SIP_CONFIG = {
    "server": "192.168.036",    # IP van je SIP server/PBX
    "user": "1014",               # SIP gebruikersnaam/extensie  
    "password": "Bloemenland2431a",       # SIP wachtwoord
    "domain": "192.168.036",      # SIP domein
    "alarm_number": "6200"        # Telefoonnummer om te bellen bij alarm
}

# Email configuratie
EMAIL_CONFIG = {
    "enabled": True,              # Zet op False om email uit te schakelen
    "smtp_server": "smtp.gmail.com",     # SMTP server
    "smtp_port": 587,             # SMTP poort (587 voor TLS, 465 voor SSL)
    "use_tls": True,              # True voor TLS, False voor SSL
    "username": "jouw_email@gmail.com",  # Jouw email adres
    "password": "jouw_app_wachtwoord",   # App wachtwoord (niet gewone wachtwoord!)
    "from_email": "jouw_email@gmail.com", # Afzender email
    "to_emails": ["ontvanger@example.com"], # Lijst van ontvangers
    "subject_prefix": "[UniFi Protect]"  # Onderwerp prefix
}

# PC Display configuratie
PC_DISPLAY_CONFIG = {
    "enabled": True,              # Zet op False om PC display uit te schakelen
    "receiver_url": "http://192.168.0.246:5001/photo",  # URL van pcReceiver.py
    "timeout": 10,                # Timeout voor versturen (seconden)
    "send_all_alarms": True       # True = alle alarms naar PC, False = alleen bewegingsdetectie
}

# =============================================================================
# HELPER FUNCTIES - Payload Sanitization & Verwerking
# =============================================================================

def sanitize_payload(obj):
    """
    🧹 PAYLOAD SANITIZER
    
    Recursief doorlopen van webhook payload en verwijderen/vervangen van grote 
    thumbnail/image velden zodat deze niet gelogd of opgeslagen worden.
    
    Args:
        obj: De payload data (dict, list, of andere types)
        
    Returns:
        Gesaniteerde versie van de payload met placeholders voor afbeeldingen
        
    Voorbeeld:
        Input:  {"thumbnail": "data:image/jpeg;base64,/9j/4AAQ..."}
        Output: {"thumbnail": "<filtered image, len=107475: redacted>"}
    """
    # Avoid importing heavy libraries; implement a small recursive sanitizer
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            key = k.lower()
            # Detect likely thumbnail/image fields by key name
            if 'thumb' in key or 'thumbnail' in key or 'snapshot' in key:
                if isinstance(v, str):
                    # Replace long image/base64 strings with a short placeholder
                    cleaned[k] = f"<filtered image, len={len(v)}: redacted>"
                else:
                    cleaned[k] = "<filtered binary>"
            else:
                cleaned[k] = sanitize_payload(v)
        return cleaned
    elif isinstance(obj, list):
        return [sanitize_payload(i) for i in obj]
    else:
        # Strings that look like data URIs (common for inline thumbnails)
        if isinstance(obj, str) and obj.startswith('data:image') and len(obj) > 100:
            return f"<filtered data:image, len={len(obj)}: redacted>"
        return obj

# =============================================================================
# CORE FUNCTIE - Alarm Verwerking
# =============================================================================

def process_alarm(alarm_data, request_type="GET", sanitized_for_logging=None):
    """
    🚨 ALARM PROCESSOR
    
    Hoofdfunctie die alarm data van UniFi Protect verwerkt en parsed.
    Handelt zowel GET als POST requests af.
    
    Args:
        alarm_data (dict): De originele alarm data inclusief foto's/thumbnails
                          - Bij POST: volledige JSON payload
                          - Bij GET: query parameters
        request_type (str): "GET" of "POST" 
        sanitized_for_logging (dict): Optionele gesaniteerde versie zonder foto's
                                     voor veilig loggen
    
    Workflow:
        1. Log alarm informatie (gesaniteerd)
        2. Extract triggers, conditions, timestamps
        3. Roep handle_alarm_actions() aan voor verwerking
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Gebruik gesaniteerde versie voor logging, originele voor verwerking
    log_data = sanitized_for_logging if sanitized_for_logging else alarm_data
    
    logger.info(f"=== UniFi Protect Alarm Ontvangen ({request_type}) ===")
    logger.info(f"Tijdstip: {timestamp}")
    
    # Log volledige JSON (gesaniteerd) voor debugging
    if log_data:
        logger.info("📋 Volledige payload (gesaniteerd):")
        logger.info(json.dumps(log_data, indent=2, ensure_ascii=False))
    
    if request_type == "POST" and alarm_data:
        # Voor POST requests hebben we volledige alarm informatie
        # Gebruik originele data voor verwerking (inclusief foto's)
        alarm_info = alarm_data.get('alarm', {})
        triggers = alarm_data.get('alarm', {}).get('triggers', [])
        conditions = alarm_data.get('alarm', {}).get('conditions', [])
        webhook_timestamp = alarm_data.get('timestamp')
        
        # Voor logging gebruiken we gesaniteerde data (zonder foto's)
        log_alarm_info = log_data.get('alarm', {}) if log_data else alarm_info
        log_triggers = log_alarm_info.get('triggers', [])
        log_conditions = log_alarm_info.get('conditions', [])
        
        alarm_name = alarm_info.get('name', 'Onbekend alarm')
        logger.info(f"Alarm naam: {alarm_name}")
        logger.info(f"Webhook timestamp: {webhook_timestamp}")
        
        # Log triggers (welke camera's/sensoren) - gesaniteerde versie
        for trigger in log_triggers:
            device_id = trigger.get('device', 'Onbekend apparaat')
            trigger_type = trigger.get('key', 'Onbekend type')
            logger.info(f"Trigger: {trigger_type} op apparaat {device_id}")
        
        # Log conditions (wat heeft het alarm getriggerd) - gesaniteerde versie
        for condition in log_conditions:
            cond_info = condition.get('condition', {})
            source = cond_info.get('source', 'Onbekende bron')
            condition_type = cond_info.get('type', 'Onbekend type')
            logger.info(f"Conditie: {source} ({condition_type})")
        
        # Verwerk acties met originele data (inclusief foto's!)
        handle_alarm_actions(alarm_info, triggers, alarm_data)
        
    else:
        # Voor GET requests hebben we beperkte informatie
        logger.info(f"GET request parameters: {dict(request.args)}")
    
    logger.info("=== Einde Alarm Verwerking ===")

# =============================================================================
# ACTIE HANDLER - Custom Alarm Acties
# =============================================================================

def handle_alarm_actions(alarm_info, triggers, full_payload=None):
    """
    ⚡ ACTIE HANDLER
    
    Voert custom acties uit op basis van het ontvangen alarm.
    Dit is waar je bepaalt wat er gebeurt bij elk type alarm.
    
    Args:
        alarm_info (dict): Alarm informatie met naam, type, etc.
        triggers (list): Lijst van triggers die het alarm activeerden
                        Bevat: device ID, trigger type, zones, timestamps
        full_payload (dict): Volledige originele payload inclusief foto's
    
    Acties die uitgevoerd worden:
        1. ✅ Verstuur foto naar PC Display (indien beweging of configured)
        2. ✅ Log apparaat activiteit per camera
        3. ✅ Sla alarm foto op naar disk
        4. ✅ Start SIP call (indien geconfigureerd)
        
    Configureerbaar via:
        - PC_DISPLAY_CONFIG["send_all_alarms"]
        - SIP_CONFIG
    """
    alarm_name = alarm_info.get('name', '').lower()
    
    # Voorbeeld acties - pas deze aan naar je behoeften
    try:
        # Actie 1: Verstuur notificatie voor specifieke alarms (met foto!)
        if PC_DISPLAY_CONFIG["send_all_alarms"] or 'motion' in alarm_name or any('motion' in str(trigger.get('key', '')) for trigger in triggers):
            logger.info("🚨 Alarm gedetecteerd! Verstuur notificatie...")
            
            # Haal thumbnail uit de originele payload
            thumbnail = extract_thumbnail_from_payload(full_payload)
            
            # Bepaal bericht gebaseerd op alarm type
            if 'motion' in alarm_name or any('motion' in str(trigger.get('key', '')) for trigger in triggers):
                message = f"Beweging gedetecteerd: {alarm_info.get('name')}"
            else:
                message = f"Alarm geactiveerd: {alarm_info.get('name')}"
            #send_notification_with_photo(message, thumbnail)
            
            # Extract name from triggers -> group -> name
            trigger_name = None
            if triggers and len(triggers) > 0:
                trigger = triggers[0]
                if 'group' in trigger and 'name' in trigger['group']:   
                    trigger_name = trigger['group']['name']
                    logger.info(f"📝 Trigger naam gevonden: {trigger_name}")
    
            send_photo_to_pc_display(thumbnail, detected_name=trigger_name)

        # Actie 2: Log naar specifiek bestand voor bepaalde camera's
        for trigger in triggers:
            device_id = trigger.get('device')
            if device_id:
                log_device_activity(device_id, alarm_info)
        
        # Actie 3: Sla foto op voor later gebruik
        if full_payload:
            save_alarm_photo(alarm_info, full_payload)
        
        # Actie 4: Voor alle alarms - verstuur altijd UDP naar Loxone
        #device_list = [trigger.get('device', 'Unknown') for trigger in triggers]
        #alarm_type = 'MOTION' if any('motion' in str(trigger.get('key', '')) for trigger in triggers) else 'ALARM'
        
        #loxone_message = f"{alarm_type}:{alarm_info.get('name', 'Unknown')}|DEVICES:{','.join(device_list)}|TIME:{datetime.now().strftime('%H:%M:%S')}"
        #send_udp_to_loxone(loxone_message)
        
        start_sip_call(SIP_CONFIG["alarm_number"])
        
        # Actie 5: Webhook doorsturen naar andere services (optioneel)
        # forward_to_other_service(alarm_info)
        
    except Exception as e:
        logger.error(f"Fout bij uitvoeren van alarm acties: {e}")

# =============================================================================
# FOTO/THUMBNAIL EXTRACTIE
# =============================================================================

def extract_thumbnail_from_payload(payload):
    """
    📸 THUMBNAIL EXTRACTOR
    
    Recursief zoeken naar thumbnail/snapshot afbeelding in de payload.
    Zoekt in geneste objecten en arrays.
    
    Args:
        payload (dict): De volledige UniFi Protect webhook payload
        
    Returns:
        str: Base64 encoded afbeelding (data:image/jpeg;base64,...)
             of None als geen afbeelding gevonden
             
    Zoekt naar velden:
        - thumbnail
        - snapshot  
        - (andere velden met 'thumb' of 'snapshot' in naam)
    """
    if not payload:
        return None
    
    # Zoek recursief naar thumbnail velden
    def find_thumbnail(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = k.lower()
                if 'thumb' in key or 'thumbnail' in key or 'snapshot' in key:
                    if isinstance(v, str) and (v.startswith('data:image') or len(v) > 100):
                        return v
                # Recursief zoeken in geneste objecten
                result = find_thumbnail(v)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = find_thumbnail(item)
                if result:
                    return result
        return None
    
    return find_thumbnail(payload)


# =============================================================================
# EMAIL NOTIFICATIES - Verstuur emails met foto's
# =============================================================================

def send_email_with_thumbnail(subject, message, thumbnail=None):
    """
    📧 EMAIL VERSTUREN MET THUMBNAIL
    
    Stuurt een email notificatie met optioneel een thumbnail bijlage.
    Gebruikt SMTP configuratie uit EMAIL_CONFIG.
    
    Args:
        subject (str): Email onderwerp (wordt gecombineerd met subject_prefix uit config)
        message (str): Hoofdtekst van de email
        thumbnail (str, optional): Base64 encoded afbeelding data om als bijlage mee te sturen
    
    Returns:
        bool: True als email succesvol verstuurd, False bij fout of als email uitgeschakeld is
    
    Email Format:
        Van: EMAIL_CONFIG["from_email"]
        Naar: EMAIL_CONFIG["to_emails"] (lijst)
        Onderwerp: [EMAIL_CONFIG["subject_prefix"]] {subject}
        Body: Geformatteerd bericht met timestamp
        Bijlage: alarm_photo.jpg (als thumbnail beschikbaar)
    
    Configuratie (in EMAIL_CONFIG):
        enabled: True/False - email functionaliteit aan/uit
        smtp_server: SMTP server adres
        smtp_port: SMTP poort (meestal 587 voor TLS)
        from_email: Afzender email adres
        password: SMTP wachtwoord (⚠️ WAARSCHUWING: hardcoded in script!)
        to_emails: Lijst van ontvangers
        subject_prefix: Prefix voor onderwerp (bijv. "[UniFi Alarm]")
    
    Voorbeeld:
        # Email met foto
        send_email_with_thumbnail(
            subject="Beweging gedetecteerd",
            message="Camera Front Door heeft beweging waargenomen",
            thumbnail=base64_image_data
        )
        
        # Email zonder foto
        send_email_with_thumbnail(
            subject="Systeem herstart",
            message="Webhook service is opnieuw gestart"
        )
    
    Let op:
        - SMTP wachtwoord staat hardcoded in EMAIL_CONFIG (beveiligingsrisico!)
        - Gebruikt TLS voor veilige verbinding
        - Alle errors worden gelogd maar stoppen de applicatie niet
    """
    if not EMAIL_CONFIG["enabled"]:
        logger.info("📧 Email is uitgeschakeld in configuratie")
        return False
    
    try:
        # Maak email bericht
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG["from_email"]
        msg['To'] = ", ".join(EMAIL_CONFIG["to_emails"])
        msg['Subject'] = f"{EMAIL_CONFIG['subject_prefix']} {subject}"
        
        # Email body
        body = f"""
UniFi Protect Alarm Notificatie

{message}

Tijdstip: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Voeg thumbnail toe als bijlage
        if thumbnail:
            try:
                # Verwijder data:image prefix als aanwezig
                if thumbnail.startswith('data:image'):
                    # Split op komma: data:image/jpeg;base64,ACTUALDATA
                    thumbnail_data = thumbnail.split(',', 1)[1]
                else:
                    thumbnail_data = thumbnail
                
                # Decodeer base64
                img_data = base64.b64decode(thumbnail_data)
                
                # Maak image attachment
                img_attachment = MIMEImage(img_data)
                img_attachment.add_header(
                    'Content-Disposition', 
                    f'attachment; filename="alarm_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg"'
                )
                msg.attach(img_attachment)
                
                logger.info(f"📸 Foto toegevoegd als bijlage ({len(img_data)} bytes)")
                
            except Exception as e:
                logger.error(f"Fout bij verwerken thumbnail voor email: {e}")
        
        # Verstuur email
        server = smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"])
        
        if EMAIL_CONFIG["use_tls"]:
            server.starttls()  # TLS encryptie
        
        server.login(EMAIL_CONFIG["username"], EMAIL_CONFIG["password"])
        
        # Verstuur naar alle ontvangers
        for to_email in EMAIL_CONFIG["to_emails"]:
            server.sendmail(EMAIL_CONFIG["from_email"], to_email, msg.as_string())
        
        server.quit()
        
        logger.info(f"📧 Email verstuurd naar: {', '.join(EMAIL_CONFIG['to_emails'])}")
        return True
        
    except Exception as e:
        logger.error(f"Fout bij versturen email: {e}")
        return False


# =============================================================================
# NOTIFICATIE AGGREGATIE - Verstuur via meerdere kanalen
# =============================================================================

def send_notification_with_photo(message, thumbnail=None):
    """
    📣 MULTI-KANAAL NOTIFICATIE
    
    Stuurt notificatie via meerdere kanalen tegelijk: email EN PC display.
    Convenience functie om beide notificatie methoden in één keer aan te roepen.
    
    Args:
        message (str): Bericht om te versturen (gebruikt als email onderwerp én display tekst)
        thumbnail (str, optional): Base64 encoded afbeelding om mee te sturen
    
    Kanalen:
        1. Email: Verstuurt via send_email_with_thumbnail()
        2. PC Display: Verstuurt via send_photo_to_pc_display()
    
    Voorbeeld:
        # Notificatie naar email EN TV scherm
        send_notification_with_photo(
            message="Beweging bij voordeur",
            thumbnail=base64_image_data
        )
    
    Let op:
        - Beide kanalen werken onafhankelijk (als email faalt, gaat PC display door)
        - Geen return value - check individuele logs voor success/failure
    """
    logger.info(f"📧 NOTIFICATIE: {message}")
    
    if thumbnail:
        logger.info(f"📸 Foto bijgevoegd (grootte: {len(thumbnail)} karakters)")
        
        # Verstuur foto naar PC display
        send_photo_to_pc_display(thumbnail, message)
        
        # Hier kun je nog meer notificaties toevoegen:
        # - Slack/Discord webhook met afbeelding
        # - Telegram bot met foto
        # - WhatsApp API
        # - Push notificaties
        
    else:
        logger.info("📸 Geen foto beschikbaar")
        
        # Verstuur email zonder foto
        #send_email_with_thumbnail("Alarm (geen foto)", message, None)

# =============================================================================
# PC DISPLAY COMMUNICATIE
# =============================================================================

def send_photo_to_pc_display(thumbnail, message="UniFi Protect Alarm", detected_name=None):
    """
    🖥️ PC DISPLAY SENDER
    
    Stuurt foto naar de PC Display Receiver applicatie via HTTP POST.
    De foto wordt getoond op een TV/monitor aangesloten op de PC.
    
    Args:
        thumbnail (str): Base64 encoded afbeelding (met of zonder data:image prefix)
        message (str): Logbericht (momenteel niet getoond op scherm)
        detected_name (str, optional): Naam uit trigger (bijv. kenteken/persoonsnaam)
                                       Wordt getoond als overlay op foto
        
    Returns:
        bool: True als succesvol verstuurd, False bij fout
        
    Vereisten:
        - PC_DISPLAY_CONFIG["enabled"] = True
        - pcReceiver.py moet draaien op receiver_url
        - 'requests' library geïnstalleerd
        
    Configuratie:
        PC_DISPLAY_CONFIG["receiver_url"] = "http://192.168.0.246:5001/photo"
        PC_DISPLAY_CONFIG["timeout"] = 10 (seconden)
    """
    if not PC_DISPLAY_CONFIG["enabled"]:
        logger.info("🖥️ PC Display is uitgeschakeld in configuratie")
        return False
    
    if not thumbnail:
        logger.info("🖥️ Geen foto beschikbaar voor PC display")
        return False
    
    try:
        import requests
        
        # Prepareer payload
        payload = {
            "image": thumbnail if thumbnail.startswith('data:image') else f"data:image/jpeg;base64,{thumbnail}",
            "source": "UniFi_Protect_Webhook",
            "message": message,
            "detected_name": detected_name,  # Naam voor overlay op foto
            "timestamp": datetime.now().isoformat()
        }
        
        # Verstuur naar PC receiver
        response = requests.post(
            PC_DISPLAY_CONFIG["receiver_url"],
            json=payload,
            timeout=PC_DISPLAY_CONFIG["timeout"]
        )
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"🖥️ Foto succesvol verstuurd naar PC display: {result.get('message', 'OK')}")
            return True
        else:
            logger.warning(f"🖥️ PC Display antwoordde met status {response.status_code}: {response.text}")
            return False
            
    except ImportError:
        logger.error("🖥️ 'requests' library niet gevonden. Installeer met: pip install requests")
        return False
    except Exception as e:
        logger.error(f"🖥️ Fout bij versturen naar PC display: {e}")
        return False


# =============================================================================
# FOTO OPSLAG - Bewaar alarm foto's lokaal
# =============================================================================

def save_alarm_photo(alarm_info, full_payload):
    """
    💾 FOTO OPSLAAN NAAR DISK
    
    Extraheert thumbnail uit alarm payload en slaat deze op als JPEG bestand.
    Foto's worden opgeslagen in de 'alarm_photos' directory met timestamp in bestandsnaam.
    
    Args:
        alarm_info (dict): Alarm informatie dict met 'name' key (voor bestandsnaam)
        full_payload (dict): Volledige alarm payload waar thumbnail uit gehaald wordt
    
    Bestandsnaam formaat:
        YYYYMMDD_HHMMSS_alarm_naam.jpg
        Voorbeeld: 20241125_143022_Beweging_Front_Door.jpg
    
    Directory structuur:
        alarm_photos/
        ├── 20241125_143022_Beweging_Front_Door.jpg
        ├── 20241125_150130_Kenteken_ABC123.jpg
        └── ...
    
    Proces:
        1. Extract thumbnail uit payload via extract_thumbnail_from_payload()
        2. Maak 'alarm_photos' directory aan (als niet bestaat)
        3. Genereer bestandsnaam met timestamp + alarm naam
        4. Verwijder base64 prefix (data:image/jpeg;base64,)
        5. Decodeer base64 naar binaire data
        6. Schrijf naar .jpg bestand
    
    Voorbeeld:
        alarm_info = {
            "name": "Beweging Front Door",
            "timestamp": "2024-11-25T14:30:22"
        }
        save_alarm_photo(alarm_info, full_payload)
        # → Maakt: alarm_photos/20241125_143022_Beweging_Front_Door.jpg
    
    Let op:
        - Spaties in alarm naam worden vervangen door underscores
        - Bestaande bestanden worden overschreven (geen conflict detectie)
        - Geen limiet op disk gebruik (oude foto's niet automatisch verwijderd)
    """
    thumbnail = extract_thumbnail_from_payload(full_payload)
    if thumbnail:
        import base64
        import os
        
        try:
            # Maak foto directory aan als die niet bestaat
            photo_dir = "alarm_photos"
            os.makedirs(photo_dir, exist_ok=True)
            
            # Genereer bestandsnaam
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            alarm_name = alarm_info.get('name', 'unknown').replace(' ', '_')
            filename = f"{timestamp}_{alarm_name}.jpg"
            filepath = os.path.join(photo_dir, filename)
            
            # Verwijder data:image/jpeg;base64, prefix als die er is
            if thumbnail.startswith('data:image'):
                thumbnail = thumbnail.split(',', 1)[1]
            
            # Decodeer en sla op
            with open(filepath, 'wb') as f:
                f.write(base64.b64decode(thumbnail))
            
            logger.info(f"📁 Foto opgeslagen: {filepath}")
            
        except Exception as e:
            logger.error(f"Fout bij opslaan foto: {e}")

def find_python27():
    """
    🔍 PYTHON 2.7 FINDER
    
    Zoekt Python 2.7 installatie op Linux/Debian systemen.
    Speciaal ontworpen om te werken in crontab omgeving met minimale PATH.
    
    Returns:
        str: Volledige pad naar python2.7 executable, of None als niet gevonden
    
    Zoekstrategie:
        1. Probeer 'which python2.7' met extended PATH
        2. Check common absolute paden:
           - /usr/bin/python2.7
           - /usr/local/bin/python2.7
           - /opt/python2.7/bin/python
           - /snap/bin/python2.7
           - ~/.local/bin/python2.7
        3. Test relatieve paden (python2.7, python2)
        4. Valideer versie met --version
    
    Crontab compatibiliteit:
        - Gebruikt absolute paden waar mogelijk
        - Extended PATH: /usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin
        - Geen afhankelijkheid van user environment
    
    Validatie:
        - Elk gevonden pad wordt getest met 'python --version'
        - Alleen paden die "2.7" in version output bevatten worden geaccepteerd
    
    Voorbeeld:
        python_path = find_python27()
        if python_path:
            subprocess.run([python_path, "script.py"])
        else:
            print("Python 2.7 niet gevonden!")
    """
    common_paths = [
        # Eerst absolute paden (werken altijd, ook in crontab)
        "/usr/bin/python2.7",
        "/usr/bin/python2",
        "/usr/local/bin/python2.7", 
        "/usr/local/bin/python2",
        "/opt/python2.7/bin/python",
        "/snap/bin/python2.7",
        "/home/arduino/.local/bin/python2.7",  # Local user install
        "/home/arduino/miniconda2/bin/python",
        "/home/arduino/anaconda2/bin/python",
        # Dan relatieve paden (werken als PATH correct is)
        "python2.7",
        "python2",
        "python27"
    ]
    
    # Op Linux: probeer eerst 'which' command met volledige PATH
    if os.name != 'nt':  # Niet Windows
        try:
            # Stel extended PATH in voor 'which' command (voor crontab)
            env = os.environ.copy()
            env['PATH'] = '/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin:' + env.get('PATH', '')
            
            result = subprocess.run(
                ["/usr/bin/which", "python2.7"],  # Gebruik absolute pad naar 'which'
                capture_output=True,
                text=True,
                timeout=5,
                env=env
            )
            if result.returncode == 0:
                python_path = result.stdout.strip()
                if python_path and os.path.isfile(python_path):
                    logger.info(f"Python 2.7 gevonden via 'which': {python_path}")
                    return python_path
        except Exception as e:
            logger.debug(f"'which' command gefaald: {e}")
            pass
    
    # Probeer alle bekende paden
    for python_path in common_paths:
        try:
            # Expand ~ naar home directory
            if python_path.startswith('~'):
                python_path = os.path.expanduser(python_path)
                
            # Test of dit een werkende Python 2.7 is
            result = subprocess.run(
                [python_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                version_output = result.stderr + result.stdout  # Python 2.7 print version naar stderr
                if "2.7" in version_output:
                    logger.info(f"Python 2.7 gevonden: {python_path}")
                    return python_path
                    
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, OSError):
            continue
    
    return None


# =============================================================================
# SIP TELEFONIE - VoIP bel integratie
# =============================================================================

def start_sip_call(destination, duration=15):
    """
    📞 SIP CALL STARTEN
    
    Start een SIP (Voice over IP) call via externe sip.py script.
    Gebruikt Python 2.7 (vereist voor pjsua library) en draait als achtergrond proces.
    
    Args:
        destination (str): Bestemmingsnummer om te bellen (bijv. "0612345678" of "100")
        duration (int, optional): Gespreksduur in seconden. Default: 15 seconden
    
    Returns:
        bool: True als call proces succesvol gestart, False bij fout
    
    Ondersteunde SIP Scripts:
        • sippy.py: Python 2.7 versie (gebruikt pjsua library)
        • sip.py: Python 3 versie (gebruikt pjsua2 library)
        Prioriteit: sippy.py > sip.py
    
    Vereisten voor sippy.py (Python 2.7):
        - Python 2.7 geïnstalleerd in systeem
        - pjsua library beschikbaar
        - find_python27() moet Python 2.7 kunnen vinden
    
    Vereisten voor sip.py (Python 3):
        - pjsua2 library geïnstalleerd
        - Huidige Python 3 interpreter
    
    SIP Configuratie (in SIP_CONFIG):
        server: SIP server adres
        user: SIP gebruikersnaam
        password: SIP wachtwoord (⚠️ hardcoded!)
        domain: SIP domein
    
    Logging:
        - Call output → sip_calls.log (append mode)
        - Proces monitoring via background thread
        - Exit codes worden gelogd
    
    Voorbeeld:
        # Bel intercom toestel 100 voor 20 seconden
        start_sip_call(destination="100", duration=20)
        
        # Bel extern nummer
        start_sip_call(destination="0612345678", duration=30)
    
    Let op:
        - Proces draait in achtergrond (non-blocking)
        - Monitor thread logt resultaat wanneer call eindigt
        - Extended PATH en LD_LIBRARY_PATH voor crontab compatibiliteit
        - Password staat hardcoded (beveiligingsrisico!)
    
    Troubleshooting:
        - Check sip_calls.log voor gedetailleerde output
        - Voor sippy.py: controleer of Python 2.7 gevonden wordt
        - Controleer of sip.py of sippy.py bestaat in script directory
        duration: Duur van de call in seconden
    
    Returns:
        bool: True als proces succesvol gestart
    """
    try:
        # Zoek naar beschikbare SIP scripts (probeer eerst sippy.py, dan sip.py)
        sippy_script_path = os.path.join(os.path.dirname(__file__), "sippy.py")
        sip_script_path = os.path.join(os.path.dirname(__file__), "sip.py")
        
        if os.path.exists(sippy_script_path):
            sip_script_path = sippy_script_path
            logger.info("Gebruik sippy.py (Python 2.7)")
        elif os.path.exists(sip_script_path):
            logger.info("Gebruik sip.py (Python 3)")
        else:
            logger.error("Geen SIP script gevonden (sip.py of sippy.py)")
            return False
        
        # Maak SIP logfile pad
        sip_log_path = os.path.join(os.path.dirname(__file__), "sip_calls.log")
        
        # Check of we sippy.py gebruiken (Python 2.7) of sip.py (Python 3)
        if "sippy.py" in sip_script_path:
            # Voor sippy.py - gebruik Python 2.7 met andere argumenten
            python27_path = find_python27()
            if not python27_path:
                logger.error("Python 2.7 niet gevonden voor sippy.py")
                logger.error("Controleer of Python 2.7 geïnstalleerd is en toegankelijk is via crontab")
                return False
                
            # Gebruik het volledige pad naar Python 2.7 en sippy.py
            sippy_full_path = os.path.join(os.path.dirname(__file__), "sippy.py")
            cmd = [python27_path, sippy_full_path]
            
            logger.info(f"Python 2.7 pad: {python27_path}")
            logger.info(f"Sippy.py pad: {sippy_full_path}")
            
            # Controleer of sippy.py bestaat
            if not os.path.exists(sippy_full_path):
                logger.error(f"sippy.py niet gevonden op: {sippy_full_path}")
                return False
        else:
            # Voor sip.py - gebruik Python 3 met pjsua2 argumenten  
            cmd = [
                sys.executable,  # python executable
                sip_script_path,
                "--destination", str(destination),
                "--duration", str(duration),
                "--server", SIP_CONFIG["server"],
                "--user", SIP_CONFIG["user"],  
                "--password", SIP_CONFIG["password"],
                "--domain", SIP_CONFIG["domain"]
            ]
        
        # Open logfile voor schrijven (append mode)
        with open(sip_log_path, 'a', encoding='utf-8') as sip_log:
            # Schrijf header naar logfile
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sip_log.write(f"\n=== SIP Call gestart op {timestamp} ===\n")
            sip_log.write(f"Commando: {' '.join(cmd)}\n")
            sip_log.flush()
            
            # Stel omgeving in voor crontab (extended PATH voor libraries)
            env = os.environ.copy()
            env['PATH'] = '/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin:' + env.get('PATH', '')
            
            # Voor Python 2.7 libraries (pjsua)
            if 'LD_LIBRARY_PATH' in env:
                env['LD_LIBRARY_PATH'] = '/usr/local/lib:/usr/lib:' + env['LD_LIBRARY_PATH']
            else:
                env['LD_LIBRARY_PATH'] = '/usr/local/lib:/usr/lib'
            
            # Start proces in de achtergrond met output naar logfile
            # Gebruik volledige omgeving voor crontab compatibiliteit
            process = subprocess.Popen(
                cmd,
                stdout=sip_log,
                stderr=subprocess.STDOUT,  # Redirect stderr naar stdout (dus naar logfile)
                preexec_fn=None if os.name == 'nt' else os.setsid,  # Linux: nieuwe proces groep
                env=env,  # Gebruik extended environment
                cwd=os.path.dirname(__file__)  # Zet working directory naar script directory
            )
        
        logger.info(f"📞 SIP call proces gestart (PID: {process.pid}) naar: {destination}")
        logger.info(f"📄 SIP output wordt geschreven naar: {sip_log_path}")
        
        # Start thread om proces te monitoren (optioneel)
        def monitor_sip_process():
            try:
                # Wacht tot proces klaar is
                return_code = process.wait()
                
                # Log resultaat
                end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(sip_log_path, 'a', encoding='utf-8') as log:
                    log.write(f"=== SIP Call beëindigd op {end_timestamp} (exit code: {return_code}) ===\n\n")
                
                if return_code == 0:
                    logger.info(f"✅ SIP call naar {destination} succesvol beëindigd")
                else:
                    logger.warning(f"⚠️ SIP call naar {destination} beëindigd met foutcode: {return_code}")
                    
            except Exception as e:
                logger.error(f"Fout bij monitoren SIP proces: {e}")
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=monitor_sip_process)
        monitor_thread.daemon = True
        monitor_thread.start()
        
        return True
        
    except Exception as e:
        logger.error(f"Fout bij starten SIP call proces: {e}")
        return False


# =============================================================================
# LOXONE INTEGRATIE - Domotica communicatie via UDP
# =============================================================================

def send_udp_to_loxone(message, loxone_ip=None, loxone_port=None):
    """
    📡 UDP BERICHT NAAR LOXONE
    
    Stuurt UDP bericht naar Loxone Miniserver voor domotica integratie.
    Gebruikt voor triggering van automaties, virtuele inputs, etc.
    
    Args:
        message (str): Het bericht om te versturen (bijv. "beweging_voordeur" of "alarm_actief")
        loxone_ip (str, optional): IP adres van Loxone Miniserver. Default: LOXONE_IP uit config
        loxone_port (int, optional): UDP poort nummer. Default: LOXONE_PORT uit config
    
    Loxone Setup:
        1. Maak Virtual Input (UDP) aan in Loxone Config
        2. Configureer UDP Command: /dev/sps/io/<input_name>/<message>
        3. Stel IP/poort in script.py configuratie in
    
    Voorbeeld gebruik:
        # Verstuur naar standaard Loxone (uit config)
        send_udp_to_loxone("beweging_detected")
        
        # Verstuur naar specifieke Loxone
        send_udp_to_loxone("alarm", loxone_ip="192.168.1.50", loxone_port=7777)
    
    Configuratie (in script header):
        LOXONE_IP = "192.168.1.100"
        LOXONE_PORT = 1234
    
    Let op:
        - UDP is connectionless (geen garantie dat bericht aankomt)
        - Loxone moet op zelfde netwerk zitten
        - Firewall moet UDP poort toestaan
    """
    # Gebruik configuratie als geen specifieke waarden gegeven
    ip = loxone_ip or LOXONE_IP
    port = loxone_port or LOXONE_PORT
    try:
        # Maak UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Verstuur bericht
        message_bytes = message.encode('utf-8')
        sock.sendto(message_bytes, (ip, port))
        sock.close()
        
        logger.info(f"🔄 UDP bericht verzonden naar Loxone ({ip}:{port}): {message}")
        
    except Exception as e:
        logger.error(f"Fout bij versturen UDP naar Loxone: {e}")


# =============================================================================
# ALGEMENE NOTIFICATIES - Placeholder voor custom notificatie services
# =============================================================================

def send_notification(message):
    """
    🔔 ALGEMENE NOTIFICATIE
    
    Placeholder functie voor custom notificatie services.
    Momenteel: logt alleen naar console/logfile.
    
    Args:
        message (str): Notificatie bericht
    
    Uitbreidingen (voorbeelden):
        • Slack webhook: requests.post(SLACK_WEBHOOK, json={"text": message})
        • Discord webhook: requests.post(DISCORD_WEBHOOK, json={"content": message})
        • Pushover: requests.post(PUSHOVER_API, data={...})
        • Telegram bot: bot.send_message(CHAT_ID, message)
        • MQTT publish: client.publish("alarm/notification", message)
    
    Gebruik:
        send_notification("Alarm geactiveerd bij voordeur")
    """
    # Voorbeeld: Print naar console (vervang door echte notificatie service)
    logger.info(f"📧 NOTIFICATIE: {message}")
    
    # Optioneel: Verstuur naar Slack, Discord, email, etc.
    # Voeg hier je notificatie code toe

# =============================================================================
# LOGGING & STORAGE FUNCTIES
# =============================================================================

def log_device_activity(device_id, alarm_info):
    """
    📝 DEVICE LOGGER
    
    Logt activiteit per individueel apparaat (camera/sensor) naar een apart bestand.
    Handig voor analyse en debugging van specifieke apparaten.
    
    Args:
        device_id (str): Unieke ID van het UniFi apparaat (bijv. "8C3066FE7870")
        alarm_info (dict): Alarm informatie met naam en details
        
    Output:
        Maakt bestand aan: device_{device_id}.log
        Format: ISO timestamp - Alarm naam
        
    Voorbeeld log entry:
        2025-11-13T14:30:25.123456 - Vehicle of interest
    """
    log_file = f"device_{device_id}.log"
    timestamp = datetime.now().isoformat()
    
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{timestamp} - {alarm_info.get('name', 'Onbekend alarm')}\n")

# =============================================================================
# FLASK ROUTES - Webhook Endpoints
# =============================================================================

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """
    🎯 WEBHOOK ENDPOINT
    
    Hoofdroute die webhooks ontvangt van UniFi Protect.
    Ondersteunt zowel GET als POST requests.
    
    POST Request (aanbevolen):
        - Content-Type: application/json
        - Body: Volledige alarm data inclusief foto's
        - Gebruikt voor: Moderne UniFi Protect versies
        
    GET Request (legacy):
        - Query parameters met beperkte data
        - Geen foto's beschikbaar
        - Gebruikt voor: Oudere UniFi versies
        
    Returns:
        - 200 OK: Alarm succesvol verwerkt
        - 400 Bad Request: Ongeldige data
        - 500 Server Error: Verwerkingsfout
        
    Voorbeelden:
        POST: https://jouw-server.com/webhook
        GET:  https://jouw-server.com/webhook?alarm=motion&camera=front
    """
    try:
        if request.method == 'POST':
            # Verwerk POST request met JSON data
            alarm_data = request.get_json()
            if alarm_data:
                # Maak gesaniteerde versie voor logging (zonder foto's)
                sanitized_for_logging = sanitize_payload(alarm_data)
                # Gebruik originele data voor verwerking (inclusief foto's)
                process_alarm(alarm_data, "POST", sanitized_for_logging)
                return jsonify({"status": "success", "message": "Alarm verwerkt"}), 200
            else:
                logger.warning("POST request ontvangen zonder JSON data")
                return jsonify({"status": "error", "message": "Geen JSON data"}), 400
        
        elif request.method == 'GET':
            # Verwerk GET request
            process_alarm(request.args, "GET")
            return "Webhook ontvangen", 200
            
    except Exception as e:
        logger.error(f"Fout bij verwerken webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """
    💚 HEALTH CHECK
    
    Status endpoint om te controleren of de service draait.
    Gebruikt door monitoring tools en load balancers.
    
    Returns:
        JSON met status "healthy" en timestamp
        
    Test:
        curl http://localhost:5000/health
    """
    return jsonify({
        "status": "healthy",
        "service": "UniFi Protect Webhook",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/logs', methods=['GET'])
def view_logs():
    """
    📋 LOG VIEWER
    
    Web endpoint om recente webhook logs te bekijken.
    Toont laatste 50 log entries uit webhook.log.
    
    Returns:
        JSON met logs array en count
        
    Test:
        curl http://localhost:5000/logs
        
    Output voorbeeld:
        {
            "logs": ["2024-11-25 14:30:22 - INFO - Alarm ontvangen", ...],
            "count": 50
        }
    """
    try:
        log_lines = []
        if os.path.exists('webhook.log'):
            with open('webhook.log', 'r', encoding='utf-8') as f:
                log_lines = f.readlines()[-50:]  # Laatste 50 regels
        
        return {
            "logs": log_lines,
            "count": len(log_lines)
        }
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/sip-logs', methods=['GET'])
def view_sip_logs():
    """
    📞 SIP CALL LOG VIEWER
    
    Web endpoint om SIP call logs te bekijken.
    Toont laatste 100 log entries uit sip_calls.log.
    
    Returns:
        JSON met sip_logs array, count en log file pad
        
    Test:
        curl http://localhost:5000/sip-logs
    """
    try:
        log_lines = []
        sip_log_path = os.path.join(os.path.dirname(__file__), "sip_calls.log")
        
        if os.path.exists(sip_log_path):
            with open(sip_log_path, 'r', encoding='utf-8') as f:
                log_lines = f.readlines()[-100:]  # Laatste 100 regels
        
        return {
            "sip_logs": log_lines,
            "count": len(log_lines),
            "log_file": sip_log_path
        }
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/test-email', methods=['POST'])
def test_email():
    """
    ✉️ EMAIL TEST ENDPOINT
    
    Test endpoint om email functionaliteit te controleren.
    Stuurt test email zonder foto naar geconfigureerde ontvangers.
    
    Method: POST
    
    Returns:
        200 OK: Email succesvol verstuurd
        500 Error: Email versturen gefaald
        
    Test:
        curl -X POST http://localhost:5000/test-email
    """
    try:
        # Test email zonder foto
        success = send_email_with_thumbnail(
            "Test Email", 
            "Dit is een test email van de UniFi Protect webhook service.", 
            None
        )
        
        if success:
            return jsonify({"status": "success", "message": "Test email verstuurd"}), 200
        else:
            return jsonify({"status": "error", "message": "Email versturen gefaald"}), 500
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =============================================================================
# FOTO WEERGAVE EN UPLOAD ROUTES
# =============================================================================

@app.route('/photos')
def photo_gallery():
    """
    🖼️ FOTO GALERIJ
    
    Web interface om alle opgeslagen foto's te bekijken.
    Toont alarm foto's EN geüploade foto's van Raspberry Pi.
    
    Directories:
        - /home/arduino/face/alarm_photos: Alarm foto's van UniFi
        - uploaded_photos: Foto's van Raspberry Pi face recognition
    
    Returns:
        HTML pagina met foto galerij (sorteer op datum, nieuwste eerst)
        
    URL:
        http://localhost:5000/photos
    """
    try:
        # Zoek foto's in alarm_photos directory
        alarm_photos = []
        alarm_photo_dir = "/home/arduino/face/alarm_photos"
        if os.path.exists(alarm_photo_dir):
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.gif', '*.bmp']:
                alarm_photos.extend(glob.glob(os.path.join(alarm_photo_dir, ext)))
        
        # Zoek foto's in uploaded directory (van Raspberry Pi)
        uploaded_photos = []
        uploaded_photo_dir = "uploaded_photos"
        if os.path.exists(uploaded_photo_dir):
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.gif', '*.bmp']:
                uploaded_photos.extend(glob.glob(os.path.join(uploaded_photo_dir, ext)))
        
        # Sorteer op modificatiedatum (nieuwste eerst)
        alarm_photos.sort(key=os.path.getmtime, reverse=True)
        uploaded_photos.sort(key=os.path.getmtime, reverse=True)
        
        # Combineer en bereid voor voor template
        all_photos = []
        
        for photo in alarm_photos:
            filename = os.path.basename(photo)
            file_stats = os.stat(photo)
            all_photos.append({
                'filename': filename,
                'path': photo.replace('\\', '/'),
                'url': f'/photo/alarm_photos/{filename}',
                'size': file_stats.st_size,
                'modified': datetime.fromtimestamp(file_stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'type': 'UniFi Alarm'
            })
        
        for photo in uploaded_photos:
            filename = os.path.basename(photo)
            file_stats = os.stat(photo)
            all_photos.append({
                'filename': filename,
                'path': photo.replace('\\', '/'),
                'url': f'/photo/uploaded_photos/{filename}',
                'size': file_stats.st_size,
                'modified': datetime.fromtimestamp(file_stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'type': 'Raspberry Pi'
            })
        
        # Sorteer alle foto's samen op datum
        all_photos.sort(key=lambda x: x['modified'], reverse=True)
        
        # HTML template voor foto galerij
        html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>Alarm Foto's - UniFi Protect & Raspberry Pi</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #361BCEFF 0%, #361BCEFF 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            text-align: center;
        }
        .stats {
            display: flex;
            justify-content: space-around;
            margin-bottom: 20px;
        }
        .stat-box {
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }
        .photo-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        .photo-card {
            background: white;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            overflow: hidden;
            transition: transform 0.2s;
        }
        .photo-card:hover {
            transform: translateY(-2px);
        }
        .photo-card img {
            width: 100%;
            height: 200px;
            object-fit: cover;
        }
        .photo-info {
            padding: 15px;
        }
        .photo-title {
            font-weight: bold;
            margin-bottom: 5px;
            color: #333;
        }
        .photo-meta {
            color: #666;
            font-size: 0.9em;
        }
        .photo-type {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: bold;
            margin-bottom: 8px;
        }
        .type-unifi {
            background-color: #e3f2fd;
            color: #1976d2;
        }
        .type-raspberry {
            background-color: #f3e5f5;
            color: #7b1fa2;
        }
        .no-photos {
            text-align: center;
            color: #666;
            font-style: italic;
            margin-top: 50px;
        }
        .refresh-btn {
            background: #4CAF50;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            margin-bottom: 20px;
        }
        .refresh-btn:hover {
            background: #45a049;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📸 Alarm Foto Galerij</h1>
        <p>UniFi Protect Foto's</p>
    </div>
    
    <button class="refresh-btn" onclick="window.location.reload()">🔄 Ververs</button>
    
    <div class="stats">
        <div class="stat-box">
            <h3>{{ total_photos }}</h3>
            <p>Totaal Foto's</p>
        </div>
        <div class="stat-box">
            <h3>{{ alarm_count }}</h3>
            <p>UniFi Alarms</p>
        </div>
    </div>
    
    {% if photos %}
    <div class="photo-grid">
        {% for photo in photos %}
        <div class="photo-card">
            <img src="{{ photo.url }}" alt="{{ photo.filename }}" onclick="window.open('{{ photo.url }}', '_blank')">
            <div class="photo-info">
                <div class="photo-type {{ 'type-unifi' if photo.type == 'UniFi Alarm' else 'type-raspberry' }}">
                    {{ photo.type }}
                </div>
                <div class="photo-title">{{ photo.filename }}</div>
                <div class="photo-meta">
                    📅 {{ photo.modified }}<br>
                    📏 {{ "%.1f"|format(photo.size / 1024) }} KB
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div class="no-photos">
        <h2>📷 Geen foto's gevonden</h2>
        <p>Er zijn nog geen alarm foto's of Raspberry Pi uploads beschikbaar.</p>
    </div>
    {% endif %}
    
    <script>
        // Auto-refresh elke 30 seconden
        setTimeout(function() {
            window.location.reload();
        }, 30000);
    </script>
</body>
</html>
        """
        
        # Bereken statistieken
        alarm_count = len(alarm_photos)
        upload_count = len(uploaded_photos)
        total_count = len(all_photos)
        
        return render_template_string(html_template, 
                                    photos=all_photos,
                                    total_photos=total_count,
                                    alarm_count=alarm_count,
                                    upload_count=upload_count)
        
    except Exception as e:
        logger.error(f"Fout bij laden foto galerij: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/photo/<path:directory>/<filename>')
def serve_photo(directory, filename):
    """
    📷 FOTO SERVE ENDPOINT
    
    Stuurt een specifieke foto naar de browser.
    Gebruikt door foto galerij voor het tonen van thumbnails/images.
    
    Args (URL parameters):
        directory: Subdirectory (alarm_photos of uploaded_photos)
        filename: Bestandsnaam van de foto
    
    Security:
        - Whitelist check: alleen alarm_photos en uploaded_photos toegestaan
        - Voorkomt directory traversal attacks
    
    Returns:
        - 200: Foto bestand
        - 403: Niet toegestane directory
        - 404: Bestand niet gevonden
        
    Voorbeeld:
        http://localhost:5000/photo/alarm_photos/20241125_143022_Beweging.jpg
    """
    try:
        # Veiligheidscheck - alleen toegestane directories
        if directory not in ['alarm_photos', 'uploaded_photos']:
            return "Niet toegestane directory", 403
        
        photo_dir = directory
        
        if not os.path.exists(photo_dir):
            return "Directory niet gevonden", 404
        
        return send_from_directory(photo_dir, filename)
        
    except Exception as e:
        logger.error(f"Fout bij serveren foto {directory}/{filename}: {e}")
        return "Foto niet gevonden", 404

@app.route('/upload', methods=['POST'])
def upload_photo():
    """
    📤 FOTO UPLOAD ENDPOINT
    
    Ontvangt foto uploads van externe clients (zoals Raspberry Pi face recognition).
    Slaat foto's op met timestamp prefix in uploaded_photos directory.
    
    Method: POST (multipart/form-data)
    
    Form data:
        file: Image bestand (PNG, JPG, JPEG, GIF, BMP)
    
    Returns:
        200 OK: Upload succesvol + bestandsinfo
        400 Bad Request: Geen bestand, lege naam, of ongeldig type
        500 Error: Upload gefaald
    
    Toegestane formaten:
        png, jpg, jpeg, gif, bmp
    
    Bestandsnaam:
        YYYYMMDD_HHMMSS_originele_naam.ext
        
    Voorbeeld cURL:
        curl -X POST -F "file=@photo.jpg" http://localhost:5000/upload
        
    Response voorbeeld:
        {
            "success": true,
            "filename": "20241125_143022_detected_face.jpg",
            "original_filename": "detected_face.jpg",
            "size": 245678,
            "view_url": "/photo/uploaded_photos/20241125_143022_detected_face.jpg"
        }
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Geen bestand gevonden'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'Geen bestandsnaam'}), 400
        
        # Controleer bestandstype
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}
        if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
            return jsonify({'error': 'Ongeldig bestandstype'}), 400
        
        # Maak upload directory
        upload_dir = "uploaded_photos"
        os.makedirs(upload_dir, exist_ok=True)
        
        # Genereer unieke bestandsnaam met timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        original_filename = file.filename
        name, ext = os.path.splitext(original_filename)
        
        new_filename = f"{timestamp}_{name}{ext}"
        filepath = os.path.join(upload_dir, new_filename)
        
        # Sla bestand op
        file.save(filepath)
        
        logger.info(f"📸 Foto geupload van Raspberry Pi: {new_filename}")
        logger.info(f"   Originele naam: {original_filename}")
        logger.info(f"   Pad: {filepath}")
        
        # Krijg bestandsinfo
        file_size = os.path.getsize(filepath)
        
        return jsonify({
            'success': True,
            'message': 'Foto succesvol geupload',
            'filename': new_filename,
            'original_filename': original_filename,
            'size': file_size,
            'path': filepath,
            'view_url': f'/photo/uploaded_photos/{new_filename}'
        }), 200
        
    except Exception as e:
        logger.error(f"Fout bij uploaden foto: {e}")
        return jsonify({'error': f'Server fout: {str(e)}'}), 500

@app.route('/photos/api')
def photos_api():
    """
    📊 FOTO API ENDPOINT
    
    JSON API voor het ophalen van foto metadata.
    Gebruikt door JavaScript applicaties/dashboards.
    
    Returns:
        JSON met alle foto's (alarm_photos + uploaded_photos)
        
    Response structuur:
        {
            "alarm_photos": [
                {
                    "filename": "20241125_143022_Beweging.jpg",
                    "size": 245678,
                    "modified": "2024-11-25 14:30:22",
                    "url": "/photo/alarm_photos/20241125_143022_Beweging.jpg"
                },
                ...
            ],
            "uploaded_photos": [...],
            "count": {
                "alarm": 15,
                "uploaded": 8,
                "total": 23
            }
        }
        
    Test:
        curl http://localhost:5000/photos/api
    """
    try:
        photos_info = {
            'alarm_photos': [],
            'uploaded_photos': []
        }
        
        # Alarm foto's
        alarm_photo_dir = "alarm_photos"
        if os.path.exists(alarm_photo_dir):
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.gif', '*.bmp']:
                for photo in glob.glob(os.path.join(alarm_photo_dir, ext)):
                    filename = os.path.basename(photo)
                    file_stats = os.stat(photo)
                    photos_info['alarm_photos'].append({
                        'filename': filename,
                        'size': file_stats.st_size,
                        'modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                        'url': f'/photo/alarm_photos/{filename}'
                    })
        
        # Uploaded foto's
        uploaded_photo_dir = "uploaded_photos"
        if os.path.exists(uploaded_photo_dir):
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.gif', '*.bmp']:
                for photo in glob.glob(os.path.join(uploaded_photo_dir, ext)):
                    filename = os.path.basename(photo)
                    file_stats = os.stat(photo)
                    photos_info['uploaded_photos'].append({
                        'filename': filename,
                        'size': file_stats.st_size,
                        'modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                        'url': f'/photo/uploaded_photos/{filename}'
                    })
        
        # Sorteer op datum (nieuwste eerst)
        photos_info['alarm_photos'].sort(key=lambda x: x['modified'], reverse=True)
        photos_info['uploaded_photos'].sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify(photos_info)
        
    except Exception as e:
        logger.error(f"Fout bij ophalen foto API: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# MAIN - Script Entry Point
# =============================================================================

if __name__ == '__main__':
    print("🚀 UniFi Protect Webhook Service wordt gestart...")
    print("📡 Endpoints:")
    print("   - Webhook: http://localhost:5000/webhook")
    print("   - Gezondheid: http://localhost:5000/health")
    print("   - Logs: http://localhost:5000/logs")
    print("   - SIP Logs: http://localhost:5000/sip-logs")
    print("   - Test Email: http://localhost:5000/test-email (POST)")
    print("📸 Foto Endpoints:")
    print("   - Foto Galerij: http://localhost:5000/photos")
    print("   - Upload Foto: http://localhost:5000/upload (POST)")
    print("   - Foto API: http://localhost:5000/photos/api")
    
    # Toon email configuratie status
    if EMAIL_CONFIG["enabled"]:
        print(f"📧 Email notificaties ingeschakeld naar: {', '.join(EMAIL_CONFIG['to_emails'])}")
    else:
        print("📧 Email notificaties uitgeschakeld")
    
    # Check welke SIP scripts beschikbaar zijn
    sip_py_path = os.path.join(os.path.dirname(__file__), "sip.py")
    sippy_py_path = os.path.join(os.path.dirname(__file__), "sippy.py")
    
    if os.path.exists(sippy_py_path):
        python27_path = find_python27()
        if python27_path:
            print(f"📞 SIP calls beschikbaar via sippy.py (Python 2.7)")
            print(f"   Python 2.7: {python27_path}")
            print(f"   Alarm calls naar: {SIP_CONFIG['alarm_number']}")
        else:
            print("⚠️  sippy.py gevonden maar Python 2.7 niet geïnstalleerd")
    elif os.path.exists(sip_py_path):
        print(f"📞 SIP calls beschikbaar via sip.py (Python 3 + pjsua2)")
        print(f"   Alarm calls naar: {SIP_CONFIG['alarm_number']}")
    else:
        print("📞 Geen SIP scripts gevonden (sip.py of sippy.py moet in dezelfde map staan)")
    
    print("💡 Gebruik Ctrl+C om te stoppen")
    
    # Start de Flask server
    app.run(
        host='0.0.0.0',  # Luister op alle interfaces
        port=5000,       # Standaard poort
        debug=True       # Debug modus voor ontwikkeling
    )