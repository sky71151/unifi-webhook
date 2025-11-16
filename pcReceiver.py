#!/usr/bin/env python3
"""
PC Photo Receiver - Ontvang en toon foto's via POST requests
Voor Windows - toont foto's direct op het beeldscherm
"""

from flask import Flask, request, jsonify, render_template_string
import base64
import io
import os
import logging
from datetime import datetime
import threading
import webbrowser
import tkinter as tk
from tkinter import Label
from PIL import Image, ImageTk, ImageDraw, ImageFont
import requests
import tempfile
import sys

# Audio imports voor MP3 afspelen
try:
    import pygame
    AUDIO_AVAILABLE = True
    logger_audio = logging.getLogger(__name__ + '.audio')
except ImportError:
    AUDIO_AVAILABLE = False
    logger_audio = None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('photo_receiver.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global variabelen voor de display
current_window = None
display_thread = None
latest_image = None
auto_display = True
save_photos = True
bring_to_foreground_enabled = True  # Window automatisch naar voorgrond brengen
photos_dir = "received_photos"

# Audio configuratie voor MP3 afspelen
AUDIO_CONFIG = {
    "enabled": True,                    # Zet op False om geluid uit te schakelen
    "notification_sound": "alarm.mp3",  # MP3 bestand in dezelfde map
    "volume": 1.0,                      # Volume (0.0 tot 1.0)
    "max_duration": 10                  # Max afspeel tijd in seconden
}

# Maak foto directory aan
if save_photos and not os.path.exists(photos_dir):
    os.makedirs(photos_dir)

def initialize_audio():
    """Initialiseer pygame audio systeem"""
    global AUDIO_AVAILABLE
    
    if not AUDIO_AVAILABLE or not AUDIO_CONFIG["enabled"]:
        return False
        
    try:
        pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
        logger.info("🔊 Audio systeem geïnitialiseerd")
        return True
    except Exception as e:
        logger.error(f"🔊 Kon audio systeem niet initialiseren: {e}")
        AUDIO_AVAILABLE = False
        return False

def play_notification_sound():
    """Speel notificatie geluid af"""
    global AUDIO_AVAILABLE
    
    if not AUDIO_AVAILABLE or not AUDIO_CONFIG["enabled"]:
        logger.debug("🔊 Audio is uitgeschakeld of niet beschikbaar")
        return False
        
    try:
        # Zoek naar het geluidsbestand
        sound_file = AUDIO_CONFIG["notification_sound"]
        
        # Probeer verschillende locaties
        possible_paths = [
            sound_file,  # Huidige directory
            os.path.join(os.path.dirname(__file__), sound_file),  # Script directory
            os.path.join(os.getcwd(), sound_file),  # Working directory
            os.path.join("sounds", sound_file)  # Sounds subdirectory
        ]
        
        sound_path = None
        for path in possible_paths:
            if os.path.exists(path):
                sound_path = path
                break
                
        if not sound_path:
            logger.warning(f"🔊 Geluid bestand niet gevonden: {sound_file}")
            logger.info(f"🔊 Gezocht in: {', '.join(possible_paths)}")
            return False
            
        # Laad en speel geluid af
        sound = pygame.mixer.Sound(sound_path)
        sound.set_volume(AUDIO_CONFIG["volume"])
        
        # Speel af in background thread
        def play_sound():
            try:
                sound.play()
                # Wacht maximaal max_duration seconden
                pygame.time.wait(min(int(sound.get_length() * 1000), AUDIO_CONFIG["max_duration"] * 1000))
                logger.info(f"🔊 Notificatie geluid afgespeeld: {os.path.basename(sound_path)}")
            except Exception as e:
                logger.error(f"🔊 Fout bij afspelen geluid: {e}")
                
        # Start in achtergrond thread zodat het de UI niet blokkeert
        audio_thread = threading.Thread(target=play_sound, daemon=True)
        audio_thread.start()
        
        return True
        
    except Exception as e:
        logger.error(f"🔊 Fout bij afspelen notificatie geluid: {e}")
        return False

class PhotoDisplayWindow:
    """
    Tkinter window om foto's full-screen weer te geven
    """
    def __init__(self):
        self.root = None
        self.label = None
        self.current_image = None
        self.is_fullscreen = False
        
    def create_window(self):
        """Maak het display window aan"""
        self.root = tk.Tk()
        self.root.title("UniFi Protect Photo Viewer")
        
        # Krijg scherm afmetingen
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Stel window grootte in
        self.root.geometry(f"{screen_width}x{screen_height}")
        self.root.configure(bg='black')
        
        # Window altijd op voorgrond en focus
        self.bring_to_foreground()
        
        # Label voor de afbeelding
        self.label = Label(self.root, bg='black')
        self.label.pack(expand=True, fill='both')
        
        # Toetsenbord bindings
        self.root.bind('<Escape>', self.exit_fullscreen)
        self.root.bind('<F11>', self.toggle_fullscreen)
        self.root.bind('<q>', self.quit_app)
        
        # Window close event (X button)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        
        logger.info("📺 Photo display window aangemaakt")
    
    def _add_text_overlay(self, image, text):
        """
        Voeg tekst overlay toe in rechteronderhoek
        
        Args:
            image: PIL Image object
            text: Tekst om te tonen
            
        Returns:
            PIL Image met overlay
        """
        try:
            # Maak een kopie van de afbeelding
            img_with_text = image.copy()
            draw = ImageDraw.Draw(img_with_text)
            
            # Probeer een mooie font te laden, fallback naar default
            img_width, img_height = img_with_text.size
            font_size = 72  # Vaste grote voor goede leesbaarheid op TV
            
            try:
                # Probeer Arial of andere system fonts
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", font_size)
                except:
                    # Fallback naar default font
                    font = ImageFont.load_default()
            
            # Bereken tekst positie (rechteronderhoek)
            # Gebruik textbbox voor nauwkeurige tekst afmetingen
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            # Positioneer rechtsonder met wat marge
            margin = 20
            x = img_width - text_width - margin
            y = img_height - text_height - margin
            
            # Teken semi-transparante achtergrond voor leesbaarheid
            padding = 15
            background_bbox = [
                x - padding,
                y - padding,
                x + text_width + padding,
                y + text_height + padding
            ]
            
            # Teken zwarte achtergrond met opacity
            draw.rectangle(background_bbox, fill=(0, 0, 0, 180))
            
            # Teken tekst in wit
            draw.text((x, y), text, fill=(255, 255, 255), font=font)
            
            logger.info(f"✏️ Tekst overlay toegevoegd: '{text}' op positie ({x}, {y})")
            return img_with_text
            
        except Exception as e:
            logger.error(f"Fout bij toevoegen tekst overlay: {e}")
            return image  # Return originele afbeelding bij fout
    
    def bring_to_foreground(self):
        """Breng window naar voorgrond en geef het focus"""
        global bring_to_foreground_enabled
        
        if not bring_to_foreground_enabled:
            logger.debug("📺 Bring to foreground is disabled")
            return
            
        if self.root:
            try:
                # Basis Tkinter methoden
                self.root.lift()           # Breng naar voor
                self.root.focus_force()    # Forceer focus
                self.root.attributes('-topmost', True)   # Tijdelijk altijd op top
                
                # Windows-specifieke focus tricks
                import os
                if os.name == 'nt':  # Windows
                    try:
                        # Probeer Windows API voor echte focus
                        import ctypes
                        from ctypes import wintypes
                        
                        # Haal window handle op
                        hwnd = self.root.winfo_id()
                        
                        # Windows API calls voor focus
                        user32 = ctypes.windll.user32
                        user32.SetForegroundWindow(hwnd)
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetActiveWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        
                        logger.info("📺 Windows API focus succesvol")
                        
                    except Exception as api_error:
                        logger.debug(f"Windows API focus failed, using Tkinter fallback: {api_error}")
                        
                        # Fallback: Tkinter minimize/restore trick
                        self.root.iconify()        # Minimaliseer
                        self.root.after(50, lambda: self.root.deiconify())  # Herstel na 50ms
                        self.root.after(100, lambda: self.root.state('normal'))  # Normale staat
                
                # Na 200ms: reset topmost zodat andere windows er weer overheen kunnen
                self.root.after(200, lambda: self.root.attributes('-topmost', False))
                
                logger.info("📺 Window naar voorgrond gebracht")
                
            except Exception as e:
                logger.warning(f"Kon window niet naar voorgrond brengen: {e}")
        
    def toggle_fullscreen(self, event=None):
        """Schakel tussen fullscreen en window modus"""
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes('-fullscreen', self.is_fullscreen)
        logger.info(f"📺 Fullscreen: {'aan' if self.is_fullscreen else 'uit'}")
        
    def exit_fullscreen(self, event=None):
        """Verlaat fullscreen modus"""
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.root.attributes('-fullscreen', False)
            logger.info("📺 Fullscreen uitgezet")
            
    def quit_app(self, event=None):
        """Sluit de applicatie"""
        global current_window
        logger.info("📺 Display window gesloten door gebruiker")
        
        # Cleanup - reset global window reference
        current_window = None
        
        # Sluit het window
        if self.root:
            self.root.quit()
            self.root.destroy()
            self.root = None
        
    def display_image(self, image_data, detected_name=None):
        """
        Toon afbeelding in het window
        
        Args:
            image_data: Base64 encoded afbeelding of PIL Image object
            detected_name: Optionele naam om als overlay te tonen
        """
        try:
            if isinstance(image_data, str):
                # Base64 string -> PIL Image
                if image_data.startswith('data:image'):
                    # Verwijder data:image prefix
                    image_data = image_data.split(',', 1)[1]
                
                # Decodeer base64
                img_bytes = base64.b64decode(image_data)
                image = Image.open(io.BytesIO(img_bytes))
            else:
                # Al een PIL Image
                image = image_data
            
            # Voeg naam overlay toe indien aanwezig
            if detected_name:
                image = self._add_text_overlay(image, detected_name)
            
            # Krijg window afmetingen
            if self.root:
                window_width = self.root.winfo_width()
                window_height = self.root.winfo_height()
                
                # Als window nog niet gerenderd is, gebruik scherm afmetingen
                if window_width <= 1:
                    window_width = self.root.winfo_screenwidth()
                    window_height = self.root.winfo_screenheight()
            else:
                # Fallback afmetingen
                window_width, window_height = 1920, 1080
            
            # Schaal afbeelding naar window grootte (behoud aspect ratio)
            img_width, img_height = image.size
            
            # Bereken schaal factor
            scale_width = window_width / img_width
            scale_height = window_height / img_height
            scale_factor = min(scale_width, scale_height) * 0.95  # 5% marge
            
            # Nieuwe afmetingen
            new_width = int(img_width * scale_factor)
            new_height = int(img_height * scale_factor)
            
            # Schaal afbeelding
            image_resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Converteer naar Tkinter format
            self.current_image = ImageTk.PhotoImage(image_resized)
            
            # Update label
            if self.label:
                self.label.configure(image=self.current_image)
                self.label.image = self.current_image  # Referentie behouden
                
                # Update window title met afbeelding info
                timestamp = datetime.now().strftime("%H:%M:%S")
                self.root.title(f"UniFi Protect Photo - {timestamp} ({img_width}x{img_height})")
            
            # Breng window naar voorgrond bij nieuwe foto
            self.bring_to_foreground()
            
            logger.info(f"📺 Afbeelding getoond: {img_width}x{img_height} -> {new_width}x{new_height}")
            
        except Exception as e:
            logger.error(f"Fout bij tonen afbeelding: {e}")
    
    def run(self):
        """Start de display loop"""
        if self.root:
            self.root.mainloop()

def save_received_photo(image_data, source="webhook"):
    """
    Sla ontvangen foto op naar bestand
    
    Args:
        image_data: Base64 encoded afbeelding
        source: Bron van de foto (voor bestandsnaam)
        
    Returns:
        str: Pad naar opgeslagen bestand of None
    """
    if not save_photos:
        return None
        
    try:
        # Verwijder data:image prefix als aanwezig
        if isinstance(image_data, str) and image_data.startswith('data:image'):
            image_data = image_data.split(',', 1)[1]
        
        # Decodeer base64
        img_bytes = base64.b64decode(image_data)
        
        # Genereer bestandsnaam
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{source}_{timestamp}.jpg"
        filepath = os.path.join(photos_dir, filename)
        
        # Sla op
        with open(filepath, 'wb') as f:
            f.write(img_bytes)
        
        logger.info(f"📁 Foto opgeslagen: {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"Fout bij opslaan foto: {e}")
        return None

def start_display_window():
    """Start het display window in een aparte thread"""
    global current_window
    
    try:
        current_window = PhotoDisplayWindow()
        current_window.create_window()
        
        # Als er al een afbeelding is, toon deze
        if latest_image:
            # latest_image is nu een tuple (image_data, detected_name)
            if isinstance(latest_image, tuple):
                current_window.display_image(latest_image[0], detected_name=latest_image[1])
            else:
                # Backwards compatibility
                current_window.display_image(latest_image)
            
        # Start de display loop
        logger.info("📺 Starting Tkinter mainloop...")
        current_window.run()
        
    except Exception as e:
        logger.error(f"Fout bij starten display window: {e}")
    finally:
        # Cleanup wanneer window wordt gesloten
        logger.info("📺 Display window thread beëindigd")
        current_window = None

def display_photo(image_data, detected_name=None):
    """
    Toon foto op het scherm
    
    Args:
        image_data: Base64 encoded afbeelding
        detected_name: Optionele naam om als overlay te tonen
    """
    global current_window, display_thread, latest_image
    
    # Sla afbeelding op
    latest_image = (image_data, detected_name)  # Sla beide op
    
    # Speel notificatie geluid af
    logger.info("🔊 Foto ontvangen - speel notificatie geluid af")
    play_notification_sound()
    
    # Sla foto op naar bestand
    save_received_photo(image_data, "received")
    
    if not auto_display:
        logger.info("📺 Auto-display uitgeschakeld")
        return
    
    # Check of window nog geldig is
    window_valid = False
    if current_window and hasattr(current_window, 'root') and current_window.root:
        try:
            # Test of window nog bestaat door een eigenschap te checken
            _ = current_window.root.winfo_exists()
            window_valid = True
        except:
            # Window is gesloten of ongeldig
            logger.info("📺 Display window was closed, will create new one")
            current_window = None
            window_valid = False
    
    # Als er geen geldig window is, start er een nieuw
    if not window_valid:
        logger.info("📺 Start nieuw display window...")
        
        # Start display thread
        display_thread = threading.Thread(target=start_display_window)
        display_thread.daemon = True
        display_thread.start()
        
        # Wacht even tot window is aangemaakt
        import time
        time.sleep(1.5)  # Iets langer wachten voor stabiliteit
    
    # Toon afbeelding in window (nieuw of bestaand)
    if current_window and hasattr(current_window, 'root') and current_window.root:
        try:
            # Check nogmaals of window nog bestaat voordat we proberen te updaten
            if current_window.root.winfo_exists():
                # Update in main thread (Tkinter vereist dit)
                current_window.root.after(0, lambda: current_window.display_image(image_data, detected_name=detected_name))
                logger.info(f"📺 Foto succesvol doorgestuurd naar display window (naam: {detected_name})")
            else:
                logger.warning("📺 Display window niet meer beschikbaar")
        except Exception as e:
            logger.error(f"Fout bij updaten display: {e}")
            logger.info("📺 Will attempt to create new window for next photo")

@app.route('/photo', methods=['POST'])
def receive_photo():
    """
    Ontvang foto via POST request en toon op scherm
    """
    try:
        content_type = request.content_type
        logger.info(f"📷 POST request ontvangen, content-type: {content_type}")
        
        image_data = None
        detected_name = None  # Naam uit trigger
        
        if 'application/json' in content_type:
            # JSON payload met base64 afbeelding
            data = request.get_json()
            
            if not data:
                return jsonify({"error": "Geen JSON data"}), 400
            
            # Haal detected_name op indien aanwezig
            if 'detected_name' in data:
                detected_name = data['detected_name']
                logger.info(f"📝 Gedetecteerde naam: {detected_name}")
            
            # Zoek naar afbeelding in verschillende mogelijke velden
            possible_fields = ['image', 'photo', 'thumbnail', 'data', 'base64']
            
            for field in possible_fields:
                if field in data:
                    image_data = data[field]
                    logger.info(f"📷 Afbeelding gevonden in veld: {field}")
                    break
            
            if not image_data:
                # Zoek recursief in geneste objecten
                def find_image_data(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if isinstance(v, str) and (v.startswith('data:image') or len(v) > 1000):
                                return v
                            result = find_image_data(v)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_image_data(item)
                            if result:
                                return result
                    return None
                
                image_data = find_image_data(data)
                if image_data:
                    logger.info("📷 Afbeelding gevonden via recursieve zoektocht")
        
        elif 'multipart/form-data' in content_type:
            # Multipart form met bestand
            if 'file' in request.files:
                file = request.files['file']
                if file.filename != '':
                    # Lees bestand en converteer naar base64
                    file_data = file.read()
                    image_data = base64.b64encode(file_data).decode('utf-8')
                    logger.info(f"📷 Bestand ontvangen: {file.filename}")
            
            # Ook checken voor base64 velden in form data
            for field in request.form:
                if len(request.form[field]) > 1000:  # Waarschijnlijk base64 afbeelding
                    image_data = request.form[field]
                    logger.info(f"📷 Base64 data gevonden in form veld: {field}")
                    break
        
        elif 'image/' in content_type:
            # Raw image data
            raw_data = request.get_data()
            image_data = base64.b64encode(raw_data).decode('utf-8')
            logger.info("📷 Raw image data ontvangen")
        
        else:
            # Probeer als raw data
            raw_data = request.get_data()
            if raw_data:
                try:
                    # Probeer als base64
                    if raw_data.startswith(b'data:image'):
                        image_data = raw_data.decode('utf-8')
                    else:
                        # Probeer als raw image bytes
                        image_data = base64.b64encode(raw_data).decode('utf-8')
                    logger.info("📷 Data geïnterpreteerd als afbeelding")
                except:
                    pass
        
        if not image_data:
            logger.warning("⚠️ Geen afbeelding gevonden in request")
            return jsonify({"error": "Geen afbeelding gevonden"}), 400
        
        # Valideer dat het een geldige afbeelding is
        try:
            test_data = image_data
            if test_data.startswith('data:image'):
                test_data = test_data.split(',', 1)[1]
            
            img_bytes = base64.b64decode(test_data)
            # Probeer afbeelding te openen
            Image.open(io.BytesIO(img_bytes))
            
        except Exception as e:
            logger.error(f"Ongeldige afbeelding data: {e}")
            return jsonify({"error": "Ongeldige afbeelding data"}), 400
        
        # Toon afbeelding op scherm
        logger.info(f"📷 Afbeelding ontvangen ({len(image_data)} karakters) - Naam: {detected_name}")
        display_photo(image_data, detected_name=detected_name)
        
        return jsonify({
            "status": "success", 
            "message": "Foto ontvangen en getoond",
            "timestamp": datetime.now().isoformat(),
            "size": len(image_data)
        }), 200
        
    except Exception as e:
        logger.error(f"Fout bij verwerken foto: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    """Status check endpoint"""
    global current_window, auto_display, save_photos, bring_to_foreground_enabled
    
    window_active = current_window is not None and hasattr(current_window, 'root') and current_window.root is not None
    
    return jsonify({
        "status": "running",
        "service": "PC Photo Receiver",
        "timestamp": datetime.now().isoformat(),
        "display_window_active": window_active,
        "auto_display": auto_display,
        "bring_to_foreground": bring_to_foreground_enabled,
        "save_photos": save_photos,
        "photos_directory": photos_dir if save_photos else None,
        "audio_enabled": AUDIO_CONFIG["enabled"],
        "audio_available": AUDIO_AVAILABLE,
        "audio_volume": AUDIO_CONFIG["volume"],
        "audio_file_exists": os.path.exists(AUDIO_CONFIG["notification_sound"])
    })

@app.route('/test-audio', methods=['POST'])
def test_audio():
    """Test audio systeem"""
    if not AUDIO_AVAILABLE:
        return jsonify({
            "status": "error", 
            "message": "Audio niet beschikbaar - installeer pygame: pip install pygame"
        }), 400
    
    if not AUDIO_CONFIG["enabled"]:
        return jsonify({
            "status": "error", 
            "message": "Audio is uitgeschakeld in configuratie"
        }), 400
    
    success = play_notification_sound()
    
    if success:
        return jsonify({
            "status": "success", 
            "message": "Audio test succesvol afgespeeld"
        })
    else:
        return jsonify({
            "status": "error", 
            "message": f"Kon audio niet afspelen - controleer of {AUDIO_CONFIG['notification_sound']} bestaat"
        }), 400

@app.route('/config', methods=['GET', 'POST'])
def config():
    """Configuratie endpoint"""
    global auto_display, save_photos, bring_to_foreground_enabled
    
    if request.method == 'POST':
        data = request.get_json()
        if data:
            if 'auto_display' in data:
                auto_display = data['auto_display']
                logger.info(f"Auto-display: {'aan' if auto_display else 'uit'}")
            
            if 'save_photos' in data:
                save_photos = data['save_photos']
                logger.info(f"Foto's opslaan: {'aan' if save_photos else 'uit'}")
            
            if 'bring_to_foreground' in data:
                bring_to_foreground_enabled = data['bring_to_foreground']
                logger.info(f"Bring to foreground: {'aan' if bring_to_foreground_enabled else 'uit'}")
            
            # Audio configuratie
            if 'audio_enabled' in data:
                AUDIO_CONFIG["enabled"] = data['audio_enabled']
                logger.info(f"Audio: {'aan' if AUDIO_CONFIG['enabled'] else 'uit'}")
            
            if 'audio_volume' in data:
                volume = max(0.0, min(1.0, float(data['audio_volume'])))
                AUDIO_CONFIG["volume"] = volume
                logger.info(f"Audio volume: {int(volume * 100)}%")
        
        return jsonify({"status": "updated"})
    
    else:
        return jsonify({
            "auto_display": auto_display,
            "save_photos": save_photos,
            "bring_to_foreground": bring_to_foreground_enabled,
            "photos_directory": photos_dir,
            "audio_enabled": AUDIO_CONFIG["enabled"],
            "audio_volume": AUDIO_CONFIG["volume"],
            "audio_available": AUDIO_AVAILABLE,
            "audio_file": AUDIO_CONFIG["notification_sound"],
            "audio_file_exists": os.path.exists(AUDIO_CONFIG["notification_sound"])
        })

@app.route('/test', methods=['GET'])
def test():
    """Test endpoint met voorbeeld afbeelding"""
    # Maak een eenvoudige test afbeelding
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Maak test afbeelding
        width, height = 800, 600
        image = Image.new('RGB', (width, height), color='darkblue')
        draw = ImageDraw.Draw(image)
        
        # Teken tekst
        try:
            font = ImageFont.truetype("arial.ttf", 48)
        except:
            font = ImageFont.load_default()
        
        text = f"Test Photo\n{datetime.now().strftime('%H:%M:%S')}"
        
        # Centreer tekst
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) // 2
        y = (height - text_height) // 2
        
        draw.text((x, y), text, fill='white', font=font)
        
        # Converteer naar base64
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG')
        image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        # Toon test afbeelding
        display_photo(image_data)
        
        return jsonify({
            "status": "success", 
            "message": "Test foto getoond"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# HTML interface voor testen
HTML_INTERFACE = """
<!DOCTYPE html>
<html>
<head>
    <title>PC Photo Receiver</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .container { max-width: 800px; }
        button { padding: 10px 20px; margin: 5px; }
        .status { background: #f0f0f0; padding: 15px; border-radius: 5px; }
        input[type="file"] { margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📷 PC Photo Receiver</h1>
        
        <div class="status">
            <h3>Status</h3>
            <p id="status">Loading...</p>
            <button onclick="updateStatus()">Refresh Status</button>
        </div>
        
        <h3>Test Functions</h3>
        <button onclick="testPhoto()">Show Test Photo</button>
        <button onclick="toggleDisplay()">Toggle Auto Display</button>
        <button onclick="toggleForeground()">Toggle Bring to Foreground</button>
        <button onclick="checkDisplay()">Check Display Status</button>
        <button onclick="resetDisplay()">Reset Display System</button>
        
        <div id="displayStatus" style="margin: 10px 0; padding: 10px; background: #e8f4fd; border-radius: 5px; display: none;">
            <h4>Display Status:</h4>
            <div id="displayDetails"></div>
        </div>
        
        <h3>Upload Photo</h3>
        <input type="file" id="fileInput" accept="image/*">
        <button onclick="uploadPhoto()">Upload & Display</button>
        
    </div>
    
    <script>
        function updateStatus() {
            fetch('/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('status').innerHTML = 
                        'Service: ' + data.service + '<br>' +
                        'Display Active: ' + data.display_window_active + '<br>' +
                        'Auto Display: ' + data.auto_display + '<br>' +
                        'Bring to Foreground: ' + data.bring_to_foreground + '<br>' +
                        'Save Photos: ' + data.save_photos;
                });
        }
        
        function testPhoto() {
            fetch('/test')
                .then(r => r.json())
                .then(data => alert(data.message || data.error));
        }
        
        function toggleDisplay() {
            fetch('/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({auto_display: !currentAutoDisplay})
            }).then(() => updateStatus());
        }
        
        function toggleForeground() {
            fetch('/config')
                .then(r => r.json())
                .then(config => {
                    fetch('/config', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({bring_to_foreground: !config.bring_to_foreground})
                    }).then(() => updateStatus());
                });
        }
        
        function checkDisplay() {
            fetch('/display-status')
                .then(r => r.json())
                .then(data => {
                    const statusDiv = document.getElementById('displayStatus');
                    const detailsDiv = document.getElementById('displayDetails');
                    
                    statusDiv.style.display = 'block';
                    detailsDiv.innerHTML = 
                        'Window Exists: ' + (data.window_exists ? '✅ Yes' : '❌ No') + '<br>' +
                        'Window Valid: ' + (data.window_valid ? '✅ Yes' : '❌ No') + '<br>' +
                        'Tkinter Available: ' + (data.tkinter_available ? '✅ Yes' : '❌ No') + '<br>' +
                        (data.error ? 'Error: ' + data.error : '');
                })
                .catch(err => alert('Error checking display: ' + err));
        }
        
        function resetDisplay() {
            if (confirm('Reset display system? This will close any open photo windows.')) {
                fetch('/reset-display', {method: 'POST'})
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        checkDisplay(); // Refresh status
                    })
                    .catch(err => alert('Error resetting display: ' + err));
            }
        }
        
        function uploadPhoto() {
            const fileInput = document.getElementById('fileInput');
            if (fileInput.files.length === 0) {
                alert('Select a photo first');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            
            fetch('/photo', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => alert(data.message || data.error));
        }
        
        let currentAutoDisplay = true;
        updateStatus();
        setInterval(updateStatus, 5000);
    </script>
</body>
</html>
"""

@app.route('/display-status', methods=['GET'])
def display_status():
    """Check status van display window"""
    global current_window
    
    status = {
        "window_exists": current_window is not None,
        "window_valid": False,
        "tkinter_available": True
    }
    
    if current_window:
        try:
            if hasattr(current_window, 'root') and current_window.root:
                status["window_valid"] = current_window.root.winfo_exists()
            else:
                status["window_valid"] = False
        except Exception as e:
            status["window_valid"] = False
            status["error"] = str(e)
    
    try:
        import tkinter
        status["tkinter_available"] = True
    except ImportError:
        status["tkinter_available"] = False
    
    return jsonify(status)

@app.route('/reset-display', methods=['POST'])
def reset_display():
    """Reset het display systeem"""
    global current_window
    
    try:
        # Cleanup bestaand window
        if current_window and hasattr(current_window, 'root') and current_window.root:
            try:
                current_window.root.quit()
                current_window.root.destroy()
            except:
                pass
        
        current_window = None
        logger.info("📺 Display systeem gereset")
        
        return jsonify({"status": "success", "message": "Display systeem gereset"})
        
    except Exception as e:
        logger.error(f"Fout bij resetten display: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def web_interface():
    """Web interface voor testen"""
    return HTML_INTERFACE

if __name__ == '__main__':
    print("🖥️ PC Photo Receiver wordt gestart...")
    
    # Initialiseer audio systeem
    if initialize_audio():
        print("🔊 Audio systeem geïnitialiseerd")
        if os.path.exists(AUDIO_CONFIG["notification_sound"]):
            print(f"🎵 Geluid bestand gevonden: {AUDIO_CONFIG['notification_sound']}")
        else:
            print(f"⚠️ Geluid bestand niet gevonden: {AUDIO_CONFIG['notification_sound']}")
            print("   Plaats een MP3 bestand genaamd 'notification.mp3' in deze map voor geluid")
    else:
        print("⚠️ Audio niet beschikbaar")
        if not AUDIO_AVAILABLE:
            print("   Installeer pygame voor MP3 ondersteuning: pip install pygame")
    
    print()
    print("📡 Endpoints:")
    print("   - Foto ontvangen: http://localhost:5001/photo (POST)")
    print("   - Status: http://localhost:5001/status")
    print("   - Configuratie: http://localhost:5001/config")
    print("   - Test foto: http://localhost:5001/test")
    print("   - Web interface: http://localhost:5001/")
    print()
    print("💡 Gebruik:")
    print("   - POST foto's naar /photo endpoint")
    print("   - JSON: {'image': 'base64_data'}")
    print("   - Multipart: file upload")
    print("   - Raw image data")
    print()
    print("⌨️ Toetsen in photo viewer:")
    print("   - F11: Toggle fullscreen")
    print("   - Escape: Exit fullscreen") 
    print("   - Q: Quit viewer")
    print()
    print("🔊 Audio configuratie:")
    print(f"   - Geluid: {'aan' if AUDIO_CONFIG['enabled'] else 'uit'}")
    print(f"   - Volume: {int(AUDIO_CONFIG['volume'] * 100)}%")
    print(f"   - Bestand: {AUDIO_CONFIG['notification_sound']}")
    print()
    print("💡 Gebruik Ctrl+C om te stoppen")
    
    # Start Flask server
    app.run(
        host='0.0.0.0',
        port=5001,
        debug=False,  # Debug uit voor betere threading support
        threaded=True
    )