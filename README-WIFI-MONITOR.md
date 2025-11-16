# WiFi Monitor Service - Installatie Handleiding

## 📋 Overzicht

Deze service monitort de WiFi verbinding en beheert automatisch de Flask webhook service (`script.py`). Bij verlies van WiFi connectiviteit wordt het systeem automatisch herstart na 5 minuten.

### Functionaliteit
- ✅ Controleert WiFi status elke 30 seconden
- ✅ Start `script.py` automatisch bij WiFi verbinding
- ✅ Stopt `script.py` bij verlies van WiFi
- ✅ Herstart systeem na 5 minuten zonder WiFi
- ✅ Start automatisch op bij boot
- ✅ Graceful shutdown bij SIGTERM/SIGINT

---

## 🔧 Vereisten

### Hardware
- Raspberry Pi (Zero 2 W of hoger aanbevolen)
- WiFi adapter (ingebouwd of USB)
- SD kaart met minimaal 8GB

### Software
- **Besturingssysteem**: Debian/Raspbian (Raspberry Pi OS)
- **Python 3**: Pre-geïnstalleerd op Raspberry Pi OS
- **Systemd**: Voor service management
- **Root toegang**: Voor systemd service installatie

### Python Packages
Geen extra packages vereist - gebruikt alleen Python standaard libraries:
- `socket` - WiFi connectiviteit testen
- `subprocess` - Process management
- `signal` - Graceful shutdown
- `time` - Sleep functionaliteit
- `logging` - Log output

---

## 📁 Bestandsstructuur

```
/home/pi/wifi/
├── wifi_monitor.py          # Monitoring script
└── wifi_monitor.log          # Log bestand (wordt automatisch aangemaakt)

/home/pi/face/
└── script.py                 # Flask webhook service

/etc/systemd/system/
├── wifi-monitor.service      # Systemd service unit
└── wifi-monitor-restart.timer # (OPTIONEEL - backup restart timer)
```

---

## 🚀 Installatie Stappen

### Stap 1: Bestanden Kopiëren

```bash
# Maak directories aan
sudo mkdir -p /home/pi/wifi
sudo mkdir -p /home/pi/face

# Kopieer wifi_monitor.py naar juiste locatie
sudo cp wifi_monitor.py /home/pi/wifi/
sudo chmod +x /home/pi/wifi/wifi_monitor.py

# Kopieer script.py naar juiste locatie
sudo cp script.py /home/pi/face/
sudo chmod +x /home/pi/face/script.py

# Stel eigenaarschap in
sudo chown -R pi:pi /home/pi/wifi
sudo chown -R pi:pi /home/pi/face
```

### Stap 2: Service Bestand Installeren

```bash
# Kopieer service bestand naar systemd directory
sudo cp wifi-monitor.service /etc/systemd/system/

# Stel correcte permissies in
sudo chmod 644 /etc/systemd/system/wifi-monitor.service

# Herlaad systemd daemon
sudo systemctl daemon-reload
```

### Stap 3: Service Activeren

```bash
# Enable service (start automatisch bij boot)
sudo systemctl enable wifi-monitor.service

# Start service nu
sudo systemctl start wifi-monitor.service
```

### Stap 4: Verificatie

```bash
# Controleer service status
sudo systemctl status wifi-monitor.service

# Bekijk live logs
sudo journalctl -u wifi-monitor.service -f

# Of bekijk logbestand direct
tail -f /home/pi/wifi/wifi_monitor.log
```

**Verwachte output bij correcte werking:**
```
● wifi-monitor.service - WiFi Connection Monitor
     Loaded: loaded (/etc/systemd/system/wifi-monitor.service; enabled)
     Active: active (running) since Sat 2024-11-16 14:30:22 CET; 2min ago
   Main PID: 1065 (python3)
      Tasks: 3 (limit: 416)
     Memory: 15.2M
        CPU: 1.234s
     CGroup: /system.slice/wifi-monitor.service
             ├─1065 /usr/bin/python3 /home/pi/wifi/wifi_monitor.py
             ├─1070 /usr/bin/python3 /home/pi/face/script.py
             └─1073 /usr/bin/python3 /home/pi/face/script.py
```

---

## ⚙️ Configuratie Aanpassen

### WiFi Monitor Settings (wifi_monitor.py)

Open het script om deze waarden aan te passen:

```python
# WiFi check interval (in seconden)
WIFI_CHECK_INTERVAL = 30

# WiFi check timeout (seconden)
WIFI_CHECK_TIMEOUT = 5

# Script paden
SCRIPT_PATH = "/home/pi/face/script.py"
SCRIPT_WORKING_DIR = "/home/pi/face"

# Reboot timer (minuten zonder WiFi voordat reboot)
REBOOT_DELAY_MINUTES = 5
```

### Service Settings (wifi-monitor.service)

**User aanpassen:**
```ini
[Service]
User=pi              # Verander naar jouw gebruikersnaam
```

**Script pad aanpassen:**
```ini
[Service]
ExecStart=/usr/bin/python3 /home/pi/wifi/wifi_monitor.py
#                          ↑ Pas aan naar jouw script locatie
```

**Restart policy aanpassen:**
```ini
[Service]
Restart=always          # always | on-failure | no
RestartSec=10          # Wachttijd voor restart (seconden)
```

---

## 🔍 Service Management

### Basis Commando's

```bash
# Service starten
sudo systemctl start wifi-monitor.service

# Service stoppen
sudo systemctl stop wifi-monitor.service

# Service herstarten
sudo systemctl restart wifi-monitor.service

# Service status bekijken
sudo systemctl status wifi-monitor.service

# Auto-start inschakelen
sudo systemctl enable wifi-monitor.service

# Auto-start uitschakelen
sudo systemctl disable wifi-monitor.service
```

### Logs Bekijken

```bash
# Live logs (volg real-time)
sudo journalctl -u wifi-monitor.service -f

# Laatste 50 regels
sudo journalctl -u wifi-monitor.service -n 50

# Logs sinds vandaag
sudo journalctl -u wifi-monitor.service --since today

# Logs tussen tijdstippen
sudo journalctl -u wifi-monitor.service --since "2024-11-16 14:00" --until "2024-11-16 15:00"

# Direct logbestand (buiten systemd)
tail -f /home/pi/wifi/wifi_monitor.log
```

---

## 🐛 Troubleshooting

### Probleem: Service start niet

**Check 1: Bestanden bestaan**
```bash
ls -l /home/pi/wifi/wifi_monitor.py
ls -l /home/pi/face/script.py
ls -l /etc/systemd/system/wifi-monitor.service
```

**Check 2: Python path correct**
```bash
which python3
# Output: /usr/bin/python3
```

**Check 3: Permissies**
```bash
# Scripts moeten executable zijn
chmod +x /home/pi/wifi/wifi_monitor.py
chmod +x /home/pi/face/script.py
```

**Check 4: Service bestand syntax**
```bash
sudo systemd-analyze verify wifi-monitor.service
```

### Probleem: Service crasht constant

**Check error logs:**
```bash
sudo journalctl -u wifi-monitor.service -n 100 --no-pager
```

**Mogelijke oorzaken:**
- Python script heeft syntax errors → Test script handmatig: `python3 /home/pi/wifi/wifi_monitor.py`
- script.py niet gevonden → Controleer pad in wifi_monitor.py
- Permissie problemen → Controleer User= in service bestand

### Probleem: Script.py start niet

**Debug mode:**
```bash
# Stop service
sudo systemctl stop wifi-monitor.service

# Run script handmatig met debug output
cd /home/pi/wifi
python3 wifi_monitor.py
```

**Check script.py dependency:**
```bash
# Test of script.py werkt
cd /home/pi/face
python3 script.py
```

### Probleem: Service herstart elke 10 minuten

Dit is veroorzaakt door de restart timer. **Disable de timer:**

```bash
# Stop timer
sudo systemctl stop wifi-monitor-restart.timer

# Disable timer (permanent)
sudo systemctl disable wifi-monitor-restart.timer

# Verifieer dat timer uit staat
sudo systemctl list-timers --all | grep wifi
```

### Probleem: Service start niet automatisch na reboot

```bash
# Check of service enabled is
sudo systemctl is-enabled wifi-monitor.service
# Moet "enabled" teruggeven

# Indien "disabled", enable het:
sudo systemctl enable wifi-monitor.service
```

---

## 📊 Monitoring & Logs

### Log Locaties

| Type | Locatie | Beschrijving |
|------|---------|--------------|
| Service logs | `journalctl -u wifi-monitor.service` | Systemd logs |
| Script logs | `/home/pi/wifi/wifi_monitor.log` | Direct log bestand |
| Webhook logs | `/home/pi/face/webhook.log` | Flask service logs |
| SIP logs | `/home/pi/face/sip_calls.log` | SIP call logs |

### Log Niveaus

In `wifi_monitor.py`:
```python
logging.basicConfig(
    level=logging.INFO,  # DEBUG | INFO | WARNING | ERROR
    # ...
)
```

**DEBUG** = Uitgebreide details (voor development)  
**INFO** = Normale operatie logs (aanbevolen)  
**WARNING** = Waarschuwingen en fouten  
**ERROR** = Alleen fouten

---

## 🔐 Beveiligingstips

### 1. Run als Non-Root User
Service draait als user `pi` (niet root). Dit is veiliger.

### 2. Beperk File Permissies
```bash
# Service bestand alleen leesbaar door root
sudo chmod 644 /etc/systemd/system/wifi-monitor.service

# Scripts alleen writable door eigenaar
chmod 755 /home/pi/wifi/wifi_monitor.py
chmod 755 /home/pi/face/script.py
```

### 3. Log Rotatie Instellen
Voorkom dat logs oneindig groeien:

```bash
# Maak logrotate config
sudo nano /etc/logrotate.d/wifi-monitor
```

Inhoud:
```
/home/pi/wifi/wifi_monitor.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
```

---

## 🔄 Service Updates

### Script Updaten

```bash
# 1. Stop service
sudo systemctl stop wifi-monitor.service

# 2. Backup oude versie
cp /home/pi/wifi/wifi_monitor.py /home/pi/wifi/wifi_monitor.py.backup

# 3. Kopieer nieuwe versie
sudo cp wifi_monitor.py /home/pi/wifi/

# 4. Test nieuwe versie handmatig
python3 /home/pi/wifi/wifi_monitor.py
# (Druk Ctrl+C om te stoppen)

# 5. Start service weer
sudo systemctl start wifi-monitor.service

# 6. Verificeer
sudo systemctl status wifi-monitor.service
```

### Service Bestand Updaten

```bash
# 1. Stop service
sudo systemctl stop wifi-monitor.service

# 2. Backup oude service
sudo cp /etc/systemd/system/wifi-monitor.service /etc/systemd/system/wifi-monitor.service.backup

# 3. Kopieer nieuwe versie
sudo cp wifi-monitor.service /etc/systemd/system/

# 4. Reload systemd
sudo systemctl daemon-reload

# 5. Start service
sudo systemctl start wifi-monitor.service
```

---

## 🗑️ Service Verwijderen

Als je de service volledig wilt verwijderen:

```bash
# 1. Stop service
sudo systemctl stop wifi-monitor.service

# 2. Disable auto-start
sudo systemctl disable wifi-monitor.service

# 3. Verwijder service bestand
sudo rm /etc/systemd/system/wifi-monitor.service

# 4. Verwijder timer (indien aanwezig)
sudo systemctl stop wifi-monitor-restart.timer
sudo systemctl disable wifi-monitor-restart.timer
sudo rm /etc/systemd/system/wifi-monitor-restart.timer

# 5. Reload systemd
sudo systemctl daemon-reload

# 6. Reset failed state
sudo systemctl reset-failed

# 7. Optioneel: verwijder scripts
rm -rf /home/pi/wifi
```

---

## 📝 Backup Strategie

### Wat te backuppen:

```bash
# Maak backup directory
mkdir -p ~/wifi-monitor-backup

# Backup scripts
cp /home/pi/wifi/wifi_monitor.py ~/wifi-monitor-backup/
cp /home/pi/face/script.py ~/wifi-monitor-backup/

# Backup service bestand
sudo cp /etc/systemd/system/wifi-monitor.service ~/wifi-monitor-backup/

# Backup configs (indien aanwezig)
cp /home/pi/face/*.config ~/wifi-monitor-backup/ 2>/dev/null

# Maak tar archief
cd ~
tar -czf wifi-monitor-backup-$(date +%Y%m%d).tar.gz wifi-monitor-backup/

echo "Backup gemaakt: ~/wifi-monitor-backup-$(date +%Y%m%d).tar.gz"
```

---

## 🆘 Support & Contact

### Logbestanden Voor Support

Wanneer je hulp nodig hebt, verzamel deze informatie:

```bash
# Systeem info
uname -a > system-info.txt
cat /etc/os-release >> system-info.txt

# Service status
sudo systemctl status wifi-monitor.service > service-status.txt

# Recente logs
sudo journalctl -u wifi-monitor.service -n 200 > service-logs.txt

# Script logs
tail -n 100 /home/pi/wifi/wifi_monitor.log > script-logs.txt

# Creëer archief
tar -czf support-logs-$(date +%Y%m%d).tar.gz *-info.txt *-logs.txt *-status.txt
```

---

## 📖 Gerelateerde Scripts

Deze service werkt samen met:

1. **script.py** - Flask webhook service voor UniFi Protect
   - Ontvangt alarm notificaties
   - Verstuurt foto's naar PC display
   - SIP call integratie
   - Email notificaties

2. **sippy.py** / **sip.py** - SIP calling scripts
   - VoIP telefonie integratie
   - Vereist: Python 2.7 (sippy.py) of pjsua2 (sip.py)

---

## ✅ Checklist Snelle Installatie

- [ ] Python 3 geïnstalleerd (`python3 --version`)
- [ ] Scripts gekopieerd naar `/home/pi/wifi/` en `/home/pi/face/`
- [ ] Scripts executable gemaakt (`chmod +x`)
- [ ] Service bestand gekopieerd naar `/etc/systemd/system/`
- [ ] Systemd daemon reload (`sudo systemctl daemon-reload`)
- [ ] Service enabled (`sudo systemctl enable wifi-monitor.service`)
- [ ] Service gestart (`sudo systemctl start wifi-monitor.service`)
- [ ] Status gecontroleerd (moet "active (running)" zijn)
- [ ] Logs bekeken (geen errors zichtbaar)
- [ ] Reboot test (`sudo reboot` → service moet automatisch starten)
- [ ] Timer disabled indien aanwezig (`sudo systemctl disable wifi-monitor-restart.timer`)

---

## 📚 Nuttige Links

- [Systemd Service Documentation](https://www.freedesktop.org/software/systemd/man/systemd.service.html)
- [Raspberry Pi OS Documentation](https://www.raspberrypi.com/documentation/)
- [Python Subprocess Module](https://docs.python.org/3/library/subprocess.html)

---

**Auteur:** Van Baelen Rob  
**Datum:** November 2024  
**Versie:** 1.0  
**Licentie:** Voor persoonlijk gebruik

---

*Voor vragen of problemen, controleer eerst de Troubleshooting sectie hierboven.*
