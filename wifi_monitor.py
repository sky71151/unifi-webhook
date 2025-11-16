import time
import socket
import subprocess
import logging
import sys
import os
import signal

# Systemd watchdog support
try:
    from systemd import daemon
    SYSTEMD_AVAILABLE = True
except ImportError:
    SYSTEMD_AVAILABLE = False
    print("⚠️  systemd library niet beschikbaar - watchdog uitgeschakeld")

# Global flag voor graceful shutdown
shutdown_requested = False

# --- Configuratie ---
#LOG_FILE = '/home/arduino/wifi/wifi_monitor.log' # Kiest een standaard loglocatie
LOG_FILE = '/home/pi/wifi/wifi_monitor.log' # Kiest een standaard loglocatie
CHECK_INTERVAL_SECONDS = 30
TIMEOUT_LIMIT_SECONDS = 300
RELIABLE_HOST = "8.8.8.8"

# Script paths
#SCRIPT_DIR = '/home/arduino/face'  # Directory waar script.py en start_script.sh staan
SCRIPT_DIR = '/home/pi/face'  # Directory waar script.py en start_script.sh staan
START_SCRIPT = os.path.join(SCRIPT_DIR, 'start_script.sh')
#SCRIPT_PID_FILE = '/home/arduino/face/script.pid'  # PID file om script.py proces te tracken
SCRIPT_PID_FILE = '/home/pi/face/script.pid'  # PID file om script.py proces te tracken

# Configureer de logging om naar het bestand te schrijven
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=LOG_FILE,      # Schrijf naar dit bestand
    filemode='a'            # Voeg toe aan het bestand (append)
)
logger = logging.getLogger(__name__)

# --- Variabelen ---
verbindingsfout_starttijd = None
script_is_running = False

def check_internet_connection(host=RELIABLE_HOST):
    """Controleert of een externe host bereikbaar is."""
    try:
        socket.create_connection((host, 53), timeout=5)
        return True
    except OSError:
        return False
    except Exception as e:
        logger.warning(f"Onverwachte fout bij verbindingstest: {e}")
        return False

def is_script_running():
    """Check of script.py draait via PID file of process check."""
    # Methode 1: Check PID file
    if os.path.exists(SCRIPT_PID_FILE):
        try:
            with open(SCRIPT_PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            # Check of proces met deze PID bestaat
            os.kill(pid, 0)  # Signal 0 = check alleen of proces bestaat
            return True
        except (OSError, ValueError):
            # PID bestaat niet meer of invalid PID file
            if os.path.exists(SCRIPT_PID_FILE):
                os.remove(SCRIPT_PID_FILE)
            return False
    
    # Methode 2: Check via pgrep
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'script.py'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except Exception as e:
        logger.warning(f"Fout bij process check: {e}")
        return False

def stop_script():
    """Stop ALLE script.py processen netjes."""
    try:
        stopped_any = False
        
        # Stap 1: Vind alle script.py PIDs
        result = subprocess.run(
            ['pgrep', '-f', 'python.*script.py'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            logger.info(f"🛑 Gevonden {len(pids)} script.py proces(sen): {', '.join(pids)}")
            
            # Stap 2: Stop alle processen netjes (SIGTERM)
            for pid in pids:
                try:
                    pid_int = int(pid)
                    logger.info(f"   Verstuur SIGTERM naar PID {pid_int}...")
                    os.kill(pid_int, 15)  # SIGTERM
                    stopped_any = True
                except (ValueError, OSError) as e:
                    logger.warning(f"   Kon PID {pid} niet stoppen: {e}")
            
            # Wacht even voor graceful shutdown
            time.sleep(3)
            
            # Stap 3: Check welke processen nog draaien en force kill
            result2 = subprocess.run(
                ['pgrep', '-f', 'python.*script.py'],
                capture_output=True,
                text=True
            )
            
            if result2.returncode == 0 and result2.stdout.strip():
                remaining_pids = result2.stdout.strip().split('\n')
                logger.warning(f"⚠️  {len(remaining_pids)} proces(sen) reageren niet, force kill...")
                
                for pid in remaining_pids:
                    try:
                        pid_int = int(pid)
                        logger.info(f"   SIGKILL naar PID {pid_int}...")
                        os.kill(pid_int, 9)  # SIGKILL
                    except (ValueError, OSError) as e:
                        logger.warning(f"   Kon PID {pid} niet force killen: {e}")
                
                time.sleep(1)
        
        # Cleanup PID file
        if os.path.exists(SCRIPT_PID_FILE):
            os.remove(SCRIPT_PID_FILE)
            logger.info("   PID file verwijderd")
        
        # Finale verificatie
        result3 = subprocess.run(
            ['pgrep', '-f', 'python.*script.py'],
            capture_output=True,
            text=True
        )
        
        if result3.returncode != 0:
            if stopped_any:
                logger.info("✅ Alle script.py processen succesvol gestopt")
            else:
                logger.info("ℹ️  Geen script.py processen actief")
            return True
        else:
            remaining = result3.stdout.strip().split('\n')
            logger.error(f"❌ Kon niet alle processen stoppen! Nog {len(remaining)} actief")
            return False
            
    except Exception as e:
        logger.error(f"❌ Fout bij stoppen script.py: {e}")
        return False

def start_script():
    """Start script.py via start_script.sh."""
    try:
        # Check of start script bestaat
        if not os.path.exists(START_SCRIPT):
            logger.error(f"❌ Start script niet gevonden: {START_SCRIPT}")
            return False
        
        # Check of al draait
        if is_script_running():
            logger.info("ℹ️  Script.py draait al, skip start")
            return True
        
        logger.info(f"🚀 Start script.py via {START_SCRIPT}...")
        
        # Start het script via bash
        subprocess.Popen(
            ['bash', START_SCRIPT],
            cwd=SCRIPT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # Detach van parent proces
        )
        
        # Wacht even en check of het gestart is
        time.sleep(3)
        if is_script_running():
            logger.info("✅ Script.py succesvol gestart")
            return True
        else:
            logger.warning("⚠️  Script.py start mogelijk gefaald, check logs")
            return False
            
    except Exception as e:
        logger.error(f"❌ Fout bij starten script.py: {e}")
        return False

def reboot_uno_q():
    """Voert de herstart opdracht uit."""
    logger.critical("🚨 De verbinding is langer dan 5 minuten weg. Herstarten nu...")

    # Zorg ervoor dat de logbuffer geleegd wordt voordat we herstarten!
    logging.shutdown()

    try:
        # Dit commando vereist dat de gebruiker van de service (zie Systemd config) voldoende rechten heeft.
        subprocess.run(['sudo', 'reboot'], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Fout bij het uitvoeren van reboot: {e}. Kon niet herstarten.")
        sys.exit(1)

def signal_handler(signum, frame):
    """Handler voor SIGTERM en SIGINT signalen van systemd."""
    global shutdown_requested
    signal_name = 'SIGTERM' if signum == signal.SIGTERM else 'SIGINT'
    logger.info(f"🛑 {signal_name} ontvangen, start graceful shutdown...")
    shutdown_requested = True

def main_loop():
    global verbindingsfout_starttijd, script_is_running

    # Registreer signal handlers voor systemd stop commando
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("✅ Wi-Fi Monitor gestart. Logging naar bestand: " + LOG_FILE)
    
    # Notify systemd dat we klaar zijn om te starten
    if SYSTEMD_AVAILABLE:
        daemon.notify('READY=1')
        logger.info("📡 Systemd watchdog geactiveerd (30s interval)")
    
    # Check initiële status
    script_is_running = is_script_running()
    logger.info(f"📊 Script.py initiële status: {'RUNNING' if script_is_running else 'STOPPED'}")

    watchdog_counter = 0
    
    while not shutdown_requested:
        # Stuur watchdog heartbeat naar systemd elke iteratie
        if SYSTEMD_AVAILABLE:
            daemon.notify('WATCHDOG=1')
            watchdog_counter += 1
            if watchdog_counter % 10 == 0:  # Log elke 10e keer (elke 5 minuten bij 30s checks)
                logger.debug(f"💓 Watchdog heartbeat #{watchdog_counter} verstuurd")
        
        if check_internet_connection():
            # Wi-Fi is OK
            if verbindingsfout_starttijd is not None:
                logger.info("✅ Wi-Fi hersteld. Timer gereset.")
                verbindingsfout_starttijd = None
            
            # Als WiFi OK is, zorg dat script.py draait
            if not is_script_running():
                logger.warning("⚠️  Script.py draait niet terwijl WiFi OK is - start het op...")
                if start_script():
                    script_is_running = True
                else:
                    logger.error("❌ Kon script.py niet starten")
            
        else:
            # Wi-Fi is DOWN
            
            # Stop script.py direct bij eerste WiFi verlies
            if script_is_running or is_script_running():
                logger.warning("❌ Wi-Fi uitgevallen - stop script.py...")
                if stop_script():
                    script_is_running = False
                    logger.info("✅ Script.py gestopt vanwege WiFi verlies")
            
            # Start/reset timer voor reboot
            if verbindingsfout_starttijd is None:
                verbindingsfout_starttijd = time.time()
                logger.warning("❌ Wi-Fi uitgevallen. Timer gestart voor reboot...")
            else:
                tijd_verlopen = time.time() - verbindingsfout_starttijd

                if tijd_verlopen >= TIMEOUT_LIMIT_SECONDS:
                    reboot_uno_q()
                else:
                    resterende_tijd = int(TIMEOUT_LIMIT_SECONDS - tijd_verlopen)
                    logger.warning(f"❌ Wi-Fi nog steeds weg. Nog {resterende_tijd} seconden tot herstart.")

        # Interruptible sleep - check elke seconde of shutdown gevraagd is
        # EN stuur elke 20 seconden een watchdog heartbeat
        for i in range(CHECK_INTERVAL_SECONDS):
            if shutdown_requested:
                break
            
            # Stuur extra watchdog heartbeat tijdens sleep (elke 20 sec)
            if SYSTEMD_AVAILABLE and i > 0 and i % 20 == 0:
                daemon.notify('WATCHDOG=1')
                logger.debug(f"💓 Extra watchdog heartbeat tijdens sleep (sec {i})")
            
            time.sleep(1)
    
    # Graceful shutdown na signal
    logger.info("🔄 Graceful shutdown gestart...")
    if SYSTEMD_AVAILABLE:
        daemon.notify('STOPPING=1')
    logging.shutdown()
    logger.info("✅ Monitor netjes afgesloten")

if __name__ == "__main__":
    try:
        main_loop()
        sys.exit(0)
    except KeyboardInterrupt:
        # Voor handmatig testen (python3 wifi_monitor.py)
        logger.info("⚠️  Monitor gestopt door gebruiker (Ctrl+C)")
        if SYSTEMD_AVAILABLE:
            daemon.notify('STOPPING=1')
        logging.shutdown()
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Onherstelbare fout in de hoofd lus: {e}")
        # Notify systemd van failure
        if SYSTEMD_AVAILABLE:
            daemon.notify('STATUS=Critical error occurred')
        # Log de fout en stop
        logging.shutdown()
        sys.exit(1)