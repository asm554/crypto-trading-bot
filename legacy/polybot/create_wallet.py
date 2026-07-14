from eth_account import Account
import secrets
import os

# Warnung ignorieren
Account.enable_unaudited_hdwallet_features()

def create_bot_wallet():
    # Generiert einen komplett neuen, sicheren Private Key
    priv = secrets.token_hex(32)
    private_key = "0x" + priv
    account = Account.from_key(private_key)
    
    print("==================================================")
    print("👛 NEUE BOT-WALLET GENERIERT (SICHERHEITS-SETUP)")
    print("==================================================")
    print("Für Trading-Bots solltest du NIEMALS deine Haupt-Wallet")
    print("(wie deine private Trust Wallet) nutzen. Das ist zu riskant!")
    print("Wir haben gerade eine eigene kleine Kasse nur für den Bot generiert.\n")
    
    print(f"🔗 Public Address (Hier schickst du USDC/MATIC hin):")
    print(f"👉 {account.address}")
    print("")
    print("🤫 Dein Private Key (Steht jetzt sauber in deiner .env):")
    print(f"👉 {private_key}")
    
    # In die .env schreiben
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    
    # Lese existierende .env
    content = ""
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            content = f.read()
            
    # Key austauschen
    import re
    if "PRIVATE_KEY=" in content:
        content = re.sub(r'PRIVATE_KEY=.*', f'PRIVATE_KEY={private_key}', content)
    else:
        content += f"\nPRIVATE_KEY={private_key}\n"
        
    with open(env_path, "w") as f:
        f.write(content)
        
    print("\n✅ Private Key wurde direkt in polybot/.env eingetragen!")
    print("Du kannst jetzt von deiner Trust Wallet etwas auf diese neue Adresse überweisen.")

if __name__ == "__main__":
    create_bot_wallet()
