import requests
import time

# === CONFIG ===
SHOP_ID = "181618"
AUTH_TOKEN = "5252934|r93BlgqhmK4AZ1YVlZHLCPBNp0wnqVQ3qHWBuhBi1f57fef0"
WEBHOOK_URL = "https://discord.com/api/webhooks/1421520780646285486/jVd04fV_g43ibf2L-5J2755s_aLAdev7iFJJ8ZLt8Hkwk0ju-4Nm5X5wiDHOAYJbxD_U"
CHECK_INTERVAL = 5

# URL API SellAuth
API_URL = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"

# Dictionnaire pour stocker l'état précédent
last_stock = {}

# Photo fixe pour tous les embeds
DEFAULT_IMAGE_URL = "https://imagedelivery.net/HL_Fwm__tlvUGLZF2p74xw/ce50fff9-ba1b-4e48-514b-4734633d6f00/public"

def get_products():
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
    r = requests.get(API_URL, headers=headers)
    if r.status_code == 200:
        return r.json().get("data", [])
    else:
        print("❌ Erreur API:", r.status_code, r.text)
        return []

def format_price(price):
    """Formate le prix pour Discord avec le symbole €."""
    try:
        price_float = float(price)
        return f"{price_float:.2f} €"
    except (ValueError, TypeError):
        return str(price)

def get_product_price(product):
    """Récupère le prix d'un produit, même si l'API a différents champs."""
    # Vérifie plusieurs champs possibles
    price = product.get("price") or product.get("formatted_price")
    if price:
        return price

    # Vérifie dans les variantes si elles existent
    variants = product.get("variants", [])
    if variants:
        price = variants[0].get("price") or variants[0].get("formatted_price")
        if price:
            return price

    # Autres champs possibles
    price = product.get("sale_price") or product.get("regular_price")
    if price:
        return price

    # Si rien n’est trouvé
    return "N/A"

def send_embed(event_type, product_name, product_url, stock, price=None, diff=0):
    """Envoie un embed Discord amélioré avec le prix formaté."""
    if event_type == "restock":
        title = f"🚀 Restock ! {product_name}"
        description = f"Le produit **{product_name}** est de retour en stock ! 🎉"
        color = 0x00ff00
    elif event_type == "add":
        title = f"📈 Stock augmenté | {product_name}"
        description = f"➕ {diff} unités ajoutées\n📦 Nouveau stock : **{stock}**"
        color = 0x3498db
    elif event_type == "oos":
        title = f"❌ Rupture de stock | {product_name}"
        description = f"Le produit **{product_name}** est maintenant en rupture ! 🛑"
        color = 0xff0000

    # Construction des champs
    fields = [
        {"name": "📦 Stock actuel", "value": str(stock), "inline": True},
        {"name": "🛒 Lien d'achat", "value": f"[Clique ici]({product_url})", "inline": True}
    ]

    # Ajouter le prix si disponible
    if price:
        fields.append({"name": "💰 Prix", "value": format_price(price), "inline": True})

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields,
        "image": {"url": DEFAULT_IMAGE_URL},
        "footer": {"text": "⚡ ZIKO Shop"}
    }

    payload = {"embeds": [embed]}
    r = requests.post(WEBHOOK_URL, json=payload)
    if r.status_code == 204:
        print(f"✅ {event_type} envoyé: {product_name}")
    else:
        print(f"❌ Erreur Discord Webhook: {r.status_code} - {r.text}")

def main():
    global last_stock
    print("🤖 Bot de restock démarré...")

    while True:
        products = get_products()

        for p in products:
            pid = str(p.get("id"))
            stock = p.get("stock_count") or 0
            name = p.get("name", "Produit inconnu")
            url = p.get("url") or f"https://zikoshop.mysellauth.com/product/{p.get('path', pid)}"
            price = get_product_price(p)

            old_stock = last_stock.get(pid, 0)

            # Restock
            if old_stock == 0 and stock > 0:
                send_embed("restock", name, url, stock, price)

            # Ajout de stock
            elif old_stock > 0 and stock > old_stock:
                diff = stock - old_stock
                send_embed("add", name, url, stock, price, diff)

            # Rupture
            elif old_stock > 0 and stock == 0:
                send_embed("oos", name, url, stock, price)

            last_stock[pid] = stock

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
