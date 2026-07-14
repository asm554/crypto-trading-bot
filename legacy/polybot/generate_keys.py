import os
import sys
from dotenv import load_dotenv, set_key
from py_clob_client.client import ClobClient

def main():
    # .env Datei laden
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    
    private_key = os.getenv("PRIVATE_KEY")
    
    if not private_key or private_key.startswith("0xYourPrivate"):
        print("❌ FEHLER: Du musst zuerst deinen echten PRIVATE_KEY in die .env Datei eintragen!")
        print("Gehe zu MetaMask -> Account Details -> Private Key exportieren.")
        sys.exit(1)
        
    print("🔑 Erstelle Polymarket CLOB API Keys aus deinem Private Key...")
    
    # Client initialisieren (Ohne Creds, um sie generieren zu können)
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=137 # Polygon
    )
    
    try:
        # Generiere L2 Keys
        creds = client.create_or_derive_api_creds()
        
        # Speichere sie direkt in die .env Datei
        set_key(env_path, "CLOB_API_KEY", creds.api_key)
        set_key(env_path, "CLOB_SECRET", creds.api_secret)
        set_key(env_path, "CLOB_PASSPHRASE", creds.api_passphrase)
        
        print("✅ ERFOLG! API Keys wurden generiert und automatisch in deine .env Datei gespeichert.")
        print(f"API Key:   {creds.api_key[:10]}...")
        
    except Exception as e:
        print(f"❌ Fehler bei der Key-Generierung: {e}")
        print("Tipp: Hast du deinen Private Key korrekt mit einem führenden '0x' in die .env geschrieben?")

if __name__ == "__main__":
    main()
