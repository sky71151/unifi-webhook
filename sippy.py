#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
SIP Call Script voor Python 2.7
Werkt alleen met Python 2.7 vanwege pjsua dependency
"""

import pjsua as pj
import time
import threading
from datetime import datetime
import os
import sys
import socket
import logging

# Debug functie - zet dit op True voor uitgebreide logs
debug = True

# Setup logging (Python 2.7 compatibel)
logging.basicConfig(
    level=logging.DEBUG if debug else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATIE - Pas deze waarden aan naar jouw setup
# =============================================================================
password = "################"
user = "1014" 
domain = "192.168.0.36"
selected_extension = "1012"  # Nummer om te bellen
to_domain = "192.168.0.36"

# Call settings
auto_call_on_start = True     # Maak automatisch call bij opstarten
call_duration = 15            # Hoe lang de call moet duren (seconden)
call_delay_on_start = 0.5       # Wacht tijd voor eerste call (seconden)

# Globale variabelen
call_active = False
lib = None
acc = None


# Callback om inkomende oproepen te behandelen
class MyAccountCallback(pj.AccountCallback):
    def __init__(self, account):
        pj.AccountCallback.__init__(self, account)

    def on_incoming_call(self, call):
        global call_active
        if debug:
            logger.debug("Incoming call from %s", call.info().remote_uri)
        call.answer(200)
        call_active = True
        call.hangup()
        logger.debug("Call ended")
        call_active = False

    def on_reg_state(self):
        if debug:
            logger.debug("Registration status: %s (%s)", self.account.info().reg_status, self.account.info().reg_reason)

# Callback om de status van de oproep te behandelen
class MyCallCallback(pj.CallCallback):
    def __init__(self, call=None):
        pj.CallCallback.__init__(self, call)

    def on_state(self):
        global call_active
        if debug:
            logger.debug("Call is %s, last code = %s (%s)", self.call.info().state_text, self.call.info().last_code, self.call.info().last_reason)
        
        # Check voor geweigerde call (486 Busy Here)
        if self.call.info().last_code == 486:
            logger.info("Call werd geweigerd (486 Busy Here)")
            self.hangup_call()
            time.sleep(1)
            os._exit(0)
        
        # Check voor normale disconnection
        if self.call.info().state == pj.CallState.DISCONNECTED:
            logger.info("Call beëindigd - status: %s", self.call.info().last_reason)
            call_active = False
            time.sleep(1)
            os._exit(0)

    def on_media_state(self):
        if self.call.info().media_state == pj.MediaState.ACTIVE:
            call_slot = self.call.info().conf_slot
            # Verbind call slot met null sound device (slot 0)
            try:
                pj.Lib.instance().conf_connect(call_slot, 0)
                pj.Lib.instance().conf_connect(0, call_slot)
                logger.info("Media verbinding succesvol opgezet")
            except Exception as e:
                logger.warning("Media verbinding fout (mogelijk normaal voor null device): %s", e)
            
            # Hang de oproep direct op
            self.hangup_call()
            time.sleep(1)
            os._exit(0)

    def hangup_call(self):
        if self.call:
            if debug:
                logger.debug("Call wordt beëindigd")
            self.call.hangup()
            time.sleep(2)



def make_call(extension=None):
    """
    Maak een SIP call naar het opgegeven extension
    
    Args:
        extension: Telefoonnummer om te bellen (gebruikt selected_extension als None)
    """
    global call_active, acc
    
    if call_active:
        logger.warning("Call al actief, kan geen nieuwe call starten")
        return False
        
    if not acc:
        logger.error("Account niet geregistreerd")
        return False
    
    target_extension = extension or selected_extension
    uri = "sip:{}@{}".format(target_extension, to_domain)
    
    try:
        logger.info("Bel naar: %s", uri)
        call = acc.make_call(uri, MyCallCallback())
        call_active = True
        logger.info("Call gestart naar %s", target_extension)
        return True
        
    except Exception as e:
        logger.error("Fout bij maken call: %s", e)
        call_active = False
        return False

def run():
    """
    Hoofdfunctie die de SIP service opstart
    """
    global call_active, lib, acc

    logger.info("SIP Service wordt gestart...")
    logger.info("Gebruiker: %s@%s", user, domain)
    logger.info("Bel extensie: %s", selected_extension)

    # Maak een bibliotheek instantie aan
    lib = pj.Lib()

    try:
        # Initialiseer pjsua met null sound device voor headless systemen
        media_cfg = pj.MediaConfig()
        media_cfg.no_vad = True  # Disable voice activity detection
        media_cfg.enable_ice = False  # Disable ICE
        media_cfg.snd_auto_close_time = 1  # Auto close sound device
        
        lib.init(log_cfg=pj.LogConfig(level=3, console_level=3), media_cfg=media_cfg)
        
        # Stel null sound device in (geen echte audio hardware nodig)
        lib.set_null_snd_dev()
        
        transport = lib.create_transport(pj.TransportType.UDP, pj.TransportConfig(5060))
        lib.start()

        # Maak en registreer account
        acc_cfg = pj.AccountConfig(domain, user, password)
        acc_cb = MyAccountCallback(None)
        acc = lib.create_account(acc_cfg, cb=acc_cb)
        acc_cb.account = acc

        # Wacht op registratie
        logger.info("Wacht op SIP registratie...")
        time.sleep(2)

        if acc.info().reg_status == 200:
            logger.info("SIP registratie succesvol: %s (%s)", acc.info().reg_status, acc.info().reg_reason)
            
            # Maak automatisch een call bij opstarten als ingeschakeld
            if auto_call_on_start:
                logger.info("Wacht %d seconden voor automatische call...", call_delay_on_start)
                time.sleep(call_delay_on_start)
                
                logger.info("Start automatische call...")
                make_call()
        else:
            logger.error("SIP registratie gefaald: %s (%s)", acc.info().reg_status, acc.info().reg_reason)
            return False

        # Houd de hoofdthread actief
        logger.info("SIP service actief. Gebruik Ctrl+C om te stoppen.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stoppen van service...")
            
    except pj.Error as e:
        logger.error("PJSUA fout: %s", e)
        return False
    except Exception as e:
        logger.error("Onverwachte fout: %s", e)
        return False
    finally:
        # Cleanup
        if lib:
            try:
                lib.destroy()
            except:
                pass
            lib = None
        logger.info("SIP service afgesloten")
        
    return True

def main():
    """
    Main functie voor command-line argumenten en configuratie
    """
    import argparse
    
    parser = argparse.ArgumentParser(description='SIP Call Service met automatische call')
    parser.add_argument('--extension', help='Extensie om te bellen (overschrijft default)')
    parser.add_argument('--duration', type=int, help='Call duur in seconden (overschrijft default)')
    parser.add_argument('--no-auto-call', action='store_true', help='Geen automatische call bij start')
    parser.add_argument('--delay', type=int, help='Wacht tijd voor eerste call in seconden')
    
    args = parser.parse_args()
    
    # Overschrijf configuratie met command-line argumenten
    global selected_extension, call_duration, auto_call_on_start, call_delay_on_start
    
    if args.extension:
        selected_extension = args.extension
        logger.info("Extension overschreven via command-line: %s", selected_extension)
        
    if args.duration:
        call_duration = args.duration
        logger.info("Call duur overschreven via command-line: %d seconden", call_duration)
        
    if args.no_auto_call:
        auto_call_on_start = False
        logger.info("Automatische call uitgeschakeld via command-line")
        
    if args.delay:
        call_delay_on_start = args.delay
        logger.info("Call delay overschreven via command-line: %d seconden", call_delay_on_start)

if __name__ == '__main__':
    try:
        # Parse command-line argumenten
        main()
        
        # Start de SIP service
        success = run()
        
        if not success:
            logger.error("SIP service gefaald")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Programma gestopt door gebruiker")
    except Exception as e:
        logger.error("Onverwachte fout in main: %s", e)
        sys.exit(1)