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

API_URL = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"
last_stock = {}
DEFAULT_IMAGE_URL = "https://imagedelivery.net/HL_Fwm__tlvUGLZF2p74xw/ce50fff9-ba1b-4e48-514b-4734633d6f00/public"

# === CONFIG SALONS ===
CHANNELS = {
    "Nitro": 1418965921116065852,
    "Membres Online": 1418969590251130953,
    "Membres Offline": 1418969590251130953,
    "Boost": 1418996481032978643,
    "Messages Réactions": 1419054351108018391
}

CHANNEL_KEYWORDS = {
    "Nitro": ["nitro", "nitro boost", "nitro basic"],
    "Membres Online": ["membres online"],
    "Membres Offline": ["membres offline"],
    "Boost": ["boost", "serve boost"],
    "Messages Réactions": ["message réaction"]
}

message_map = {}

# === FONCTIONS API ===
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
        price_float = float(price)
        return f"{price_float:.2f} €"
    except:
        return str(price)

def get_product_price_range(product):
    variants = product.get("variants", [])
    prices = []
    for v in variants:
        p = v.get("price") or v.get("formatted_price")
        if p:
            try:
                prices.append(float(p))
            except:
                continue
    if prices:
        min_price, max_price = min(prices), max(prices)
        price_str = f"{min_price:.2f}€"
        if min_price != max_price:
            price_str += f" → {max_price:.2f}€"
    else:
        price_str = product.get("price") or "N/A"
    return price_str

# === RESTOCK WEBHOOK ===
def send_embed(event_type, product_name, product_url, stock, price=None, diff=0):
    if event_type == "restock":
        title = f"🚀 Restock ! {product_name}"
        description = f"Le produit **{product_name}** est de retour en stock !"
        color = 0x00ff00
    elif event_type == "add":
        title = f"📈 Stock augmenté | {product_name}"
        description = f"➕ {diff} unités ajoutées\n📦 Nouveau stock : **{stock}**"
        color = 0x3498db
    elif event_type == "oos":
        title = f"❌ Rupture de stock | {product_name}"
        description = f"Le produit **{product_name}** est maintenant en rupture ! 🛑"
        color = 0xff0000
    else:
        return

    fields = [
        {"name": "📦 Stock actuel", "value": str(stock), "inline": True},
        {"name": "🛒 Lien d'achat", "value": f"[Clique ici]({product_url})", "inline": True}
    ]

    if price:
        fields.append({"name": "💰 Prix", "value": price, "inline": True})

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields,
        "image": {"url": DEFAULT_IMAGE_URL},
        "footer": {"text": "ZIKO SHOP"}
    }

    payload = {"content": "@everyone", "embeds": [embed]}
    try:
        r = requests.post(WEBHOOK_URL, json=payload)
        if r.status_code == 204:
            print(f"✅ {event_type} envoyé: {product_name}")
        else:
            print(f"❌ Erreur Discord Webhook: {r.status_code} - {r.text}")
    except Exception as e:
        print("❌ Erreur Webhook:", e)

def bot_loop():
    global last_stock
    print("🤖 Bot de restock démarré...")
    while True:
        products = get_products()
        for p in products:
            pid = str(p.get("id"))
            stock = p.get("stock_count") or 0
            name = p.get("name", "Produit inconnu")
            url = p.get("url") or f"https://zikoshop.mysellauth.com/product/{p.get('path', pid)}"
            price = get_product_price_range(p)

            old_stock = last_stock.get(pid, 0)

            if old_stock == 0 and stock > 0:
                send_embed("restock", name, url, stock, price)
            elif old_stock > 0 and stock > old_stock:
                diff = stock - old_stock
                send_embed("add", name, url, stock, price, diff)
            elif old_stock > 0 and stock == 0:
                send_embed("oos", name, url, stock, price)

            last_stock[pid] = stock

        time.sleep(CHECK_INTERVAL)

# === VITRINE DISCORD ===
intents = discord.Intents.default()
intents.messages = True
client = discord.Client(intents=intents)

def get_channel_for_product(product_name, channel_objects):
    name_lower = product_name.lower()
    for key, keywords in CHANNEL_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                return channel_objects[key]
    return None

def build_vitrine_embed(product):
    stock = product.get("stock_count", 0)
    price_str = get_product_price_range(product)
    dispo = "🟢 En stock" if stock > 0 else "🔴 Rupture"
    
    embed = discord.Embed(
        title=product.get("name", "Produit inconnu"),
        description=f"{dispo}\n📦 Stock : **{stock}**\n💰 Prix : {price_str}",
        color=discord.Color.green() if stock > 0 else discord.Color.red()
    )
    embed.set_footer(text="ZIKO SHOP")
    embed.set_thumbnail(url=DEFAULT_IMAGE_URL)
    return embed

async def update_vitrine():
    await client.wait_until_ready()
    channel_objects = {k: client.get_channel(v) for k, v in CHANNELS.items()}

    while not client.is_closed():
        products = get_products()
        for p in products:
            pid = str(p["id"])
            channel = get_channel_for_product(p.get("name", ""), channel_objects)
            if not channel:
                continue

            embed = build_vitrine_embed(p)

            if pid in message_map:
                try:
                    msg = await channel.fetch_message(message_map[pid])
                    await msg.edit(embed=embed)
                except discord.NotFound:
                    new_msg = await channel.send(embed=embed)
                    message_map[pid] = new_msg.id
            else:
                new_msg = await channel.send(embed=embed)
                message_map[pid] = new_msg.id

        await asyncio.sleep(10)

@client.event
async def on_ready():
    print(f"✅ Vitrine connectée en tant que {client.user}")
    client.loop.create_task(update_vitrine())

# === FLASK POUR PING ===
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot en ligne ✅"

def start_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# === MAIN ===
if __name__ == "__main__":
    threading.Thread(target=start_flask).start()
    threading.Thread(target=bot_loop).start()
    client.run(DISCORD_TOKEN)
