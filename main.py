import os
import requests
import threading
import asyncio
import discord
from flask import Flask

# === CONFIG ===
SHOP_ID = os.environ.get("SHOP_ID", "181618")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHECK_INTERVAL = 10  # secondes

API_URL = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"
DEFAULT_IMAGE_URL = "https://imagedelivery.net/HL_Fwm__tlvUGLZF2p74xw/ce50fff9-ba1b-4e48-514b-4734633d6f00/public"

# === SALONS ===
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
    "Messages Réactions": ["message réaction", "messages réaction"]
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

def get_price_range(product):
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
        return f"{min_price:.2f}€ → {max_price:.2f}€" if min_price != max_price else f"{min_price:.2f}€"
    else:
        return product.get("price") or "N/A"

def get_channel_for_product(product_name, channel_objects):
    name_lower = product_name.lower()
    for key, keywords in CHANNEL_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                return channel_objects.get(key)
    return None

# === BOT VITRINE DISCORD ===
intents = discord.Intents.default()
intents.messages = True
client = discord.Client(intents=intents)

def build_embed(product):
    stock = product.get("stock_count", 0)
    price_str = get_price_range(product)
    title = product.get("name", "Produit inconnu")
    url = product.get("url") or f"https://zikoshop.mysellauth.com/product/{product.get('path', product.get('id'))}"

    embed = discord.Embed(
        title=title,
        url=url,
        description=f"📦 Stock : **{stock}**\n💰 Prix : {price_str}",
        color=discord.Color.green() if stock > 0 else discord.Color.red()
    )
    embed.set_footer(text="ZIKO SHOP")
    embed.set_thumbnail(url=DEFAULT_IMAGE_URL)
    return embed

async def update_vitrine():
    await client.wait_until_ready()
    channel_objects = {k: client.get_channel(v) for k, v in CHANNELS.items()}

    # Nettoyer les anciens messages pour éviter doublons
    for key, channel in channel_objects.items():
        if channel:
            async for msg in channel.history(limit=50):
                try:
                    await msg.delete()
                except:
                    continue

    while not client.is_closed():
        products = get_products()
        for p in products:
            pid = str(p["id"])
            channel = get_channel_for_product(p.get("name", ""), channel_objects)
            if not channel:
                continue

            embed = build_embed(p)

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

        await asyncio.sleep(CHECK_INTERVAL)

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
    client.run(DISCORD_TOKEN)
