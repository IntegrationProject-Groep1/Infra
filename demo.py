import time
import os

# Haal de naam van het systeem op uit de omgeving (bijv. crm of frontend)
SYSTEM = os.getenv('SYSTEM_NAME', 'onbekend-systeem')

print(f"--- START DEMO ---")
print(f"Dit is een demo test voor containers van team {SYSTEM}.")
print("De verbinding met de GitHub Registry is geslaagd!")

# Zorg dat de container blijft draaien zodat je hem kunt zien
try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    print("Demo gestopt.")