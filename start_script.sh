#!/bin/bash
# Crontab startup script voor script.py
# Dit script zorgt ervoor dat script.py correct start vanuit crontab

# Stel omgevingsvariabelen in
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin:$PATH"
export LD_LIBRARY_PATH="/usr/local/lib:/usr/lib:$LD_LIBRARY_PATH"

# Ga naar de juiste script directory
#cd "/home/arduino/face"
cd "/home/pi/face"
# Log startup
echo "=== $(date): Starting script.py via crontab ===" >> startup.log

# Controleer of Python 3 beschikbaar is
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found in PATH" >> startup.log
    exit 1
fi

# Controleer of Python 2.7 beschikbaar is (voor SIP)
PYTHON27_PATH=""
for path in /usr/bin/python2.7 /usr/local/bin/python2.7 /usr/bin/python2; do
    if [ -x "$path" ]; then
        PYTHON27_PATH="$path"
        break
    fi
done

if [ -z "$PYTHON27_PATH" ]; then
    echo "WARNING: Python 2.7 not found, SIP calls will not work" >> startup.log
else
    echo "Found Python 2.7 at: $PYTHON27_PATH" >> startup.log
fi

# Controleer of script.py bestaat
if [ ! -f "script.py" ]; then
    echo "ERROR: script.py not found in $(pwd)" >> startup.log
    exit 1
fi

# Start script.py met logging
echo "Starting script.py with python3..." >> startup.log
python3 script.py >> script_output.log 2>&1 &

# Log de PID
echo "Script.py started with PID: $!" >> startup.log
echo "$!" > script.pid

echo "=== $(date): script.py startup complete ===" >> startup.log
