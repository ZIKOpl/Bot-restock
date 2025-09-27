import os
import requests
import time
import threading
import asyncio
import discord
from flask import Flask

# === CONFIG ===
SHOP_ID = os.environ.get("SHOP_ID", "181618")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHECK_INTERVAL = 5  # secondes

# URL API SellAuth
API_URL = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"

# === STOCK & MESSAGES ===
last_stock = {}
message_map = {}  # {product_id: message_id}

# === SALONS DISCORD ===
CHANNELS = {
    "Nitro": 1418965921116065852,
    "Membres Online": 1418969590251130953,
    "Membres Offline": 1418969590251130953,
    "Boost": 1418996481032978643,
    "Messages Réactions": 1418996481032978643
}

# === RESTOCK WEBHOOK ===
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
    try:
        return f"{float(price):.2f} €"
    except (TypeError, ValueError):
        return str(price)

def get_price_range(product):
    variants = product.get("variants", [])
    if not variants:
        price = product.get("price") or "N/A"
        return price, price
    prices = [float(v.get("price") or v.get("formatted_price") or 0) for v in variants]
    return format_price(min(prices)), format_price(max(prices))

def send_webhook(event_type, product):
    name = product.get("name", "Produit inconnu")
    url = product.get("url") or f"https://zikoshop.mysellauth.com/product/{product.get('path', product.get('id'))}"
    stock = product.get("stock_count") or 0
    min_price, max_price = get_price_range(product)

    if event_type == "restock":
        title = f"🚀 Restock ! {name}"
        description = f"Le produit **{name}** est de retour en stock !"
        color = 0x00ff00
    elif event_type == "add":
        title = f"📈 Stock augmenté | {name}"
        description = f"Nouveau stock : **{stock}**"
        color = 0x3498db
    elif event_type == "oos":
        title = f"❌ Rupture de stock | {name}"
        description = f"Le produit **{name}** est maintenant en rupture !"
        color = 0xff0000
    else:
        return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": [
            {"name": "📦 Stock actuel", "value": str(stock), "inline": True},
            {"name": "💰 Prix", "value": f"{min_price} → {max_price}", "inline": True},
            {"name": "🛒 Lien d'achat", "value": f"[Clique ici]({url})", "inline": True}
        ],
        "image": {"url": DEFAULT_IMAGE_URL},
        "footer": {"text": "ZIKO SHOP"}
    }

    payload = {"content": "@everyone", "embeds": [embed]}
    try:
        r = requests.post(WEBHOOK_URL, json=payload)
        if r.status_code == 204:
            print(f"✅ Webhook {event_type}: {name}")
        else:
            print(f"❌ Erreur Webhook: {r.status_code} - {r.text}")
    except Exception as e:
        print("❌ Erreur Webhook:", e)

def bot_loop():
    global last_stock
    print("🤖 Bot restock démarré...")
    while True:
        products = get_products()
        for p in products:
            pid = str(p.get("id"))
            stock = p.get("stock_count") or 0
            old_stock = last_stock.get(pid, 0)

            if old_stock == 0 and stock > 0:
                send_webhook("restock", p)
            elif old_stock > 0 and stock > old_stock:
                send_webhook("add", p)
            elif old_stock > 0 and stock == 0:
                send_webhook("oos", p)

            last_stock[pid] = stock
        time.sleep(CHECK_INTERVAL)

# === DISCORD VITRINE ===
intents = discord.Intents.default()
client = discord.Client(intents=intents)

def build_embed(product):
    name = product.get("name", "Produit inconnu")
    url = product.get("url") or f"https://zikoshop.mysellauth.com/product/{product.get('path', product.get('id'))}"
    stock = product.get("stock_count") or 0
    min_price, max_price = get_price_range(product)
    dispo = "🟢 En stock" if stock > 0 else "🔴 Rupture"

    embed = discord.Embed(
        title=name,
        url=url,
        description=f"{dispo}\n📦 Stock : **{stock}**\n💰 Prix : {min_price} → {max_price}",
        color=discord.Color.green() if stock > 0 else discord.Color.red()
    )
    embed.set_footer(text="ZIKO SHOP")
    return embed

def route_channel(product):
    name = product.get("name", "").lower()
    if "nitro" in name:
        return CHANNELS["Nitro"]
    elif "boost" in name:
        return CHANNELS["Boost"]
    elif "message réaction" in name or "messages réactions" in name:
        return CHANNELS["Messages Réactions"]
    elif "offline" in name:
        return CHANNELS["Membres Offline"]
    elif "online" in name:
        return CHANNELS["Membres Online"]
    else:
        return CHANNELS["Boost"]

async def update_vitrine():
    await client.wait_until_ready()
    while not client.is_closed():
        products = get_products()
        for p in products:
            pid = str(p.get("id"))
            channel_id = route_channel(p)
            channel = client.get_channel(channel_id)
            if not channel:
                continue

            embed = build_embed(p)
            try:
                if pid in message_map:
                    msg = await channel.fetch_message(message_map[pid])
                    await msg.edit(embed=embed)
                else:
                    msg = await channel.send(embed=embed)
                    message_map[pid] = msg.id
            except Exception as e:
                print(f"❌ Erreur Vitrine pour {p.get('name')}: {e}")
        await asyncio.sleep(10)

@client.event
async def on_ready():
    print(f"✅ Vitrine connectée en tant que {client.user}")

# === FLASK POUR PING ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot en ligne ✅"

def start_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# === MAIN ===
if __name__ == "__main__":
    threading.Thread(target=start_flask).start()  # Flask
    threading.Thread(target=bot_loop).start()     # Webhook Restock
    client.loop.create_task(update_vitrine())    # Discord Vitrine
    client.run(DISCORD_TOKEN)
