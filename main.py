import os
import requests
import time
import threading
import asyncio
import discord
from flask import Flask
import json

# === CONFIG ===
SHOP_ID = os.environ.get("SHOP_ID", "181618")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHECK_INTERVAL = 10  # secondes

API_URL = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"
DEFAULT_IMAGE_URL = "https://imagedelivery.net/HL_Fwm__tlvUGLZF2p74xw/ce50fff9-ba1b-4e48-514b-4734633d6f00/public"

# === CONFIG SALONS ===
CHANNELS = {
    "Nitro": 1418965921116065852,
    "Membres": 1418969590251130953,
    "Boost": 1418996481032978643,
    "Deco": 1418968022126821386,
    "Reactions": 1419054351108018391
}

# === Message map (sauvegarde persistante) ===
MESSAGE_FILE = "message_map.json"

if os.path.exists(MESSAGE_FILE):
    try:
        with open(MESSAGE_FILE, "r") as f:
            message_map = json.load(f)
    except json.JSONDecodeError:
        message_map = {}
else:
    message_map = {}

def save_message_map():
    with open(MESSAGE_FILE, "w") as f:
        json.dump(message_map, f)

# === API SHOP ===
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
    except (ValueError, TypeError):
        return str(price)

def get_product_price_range(product):
    variants = product.get("variants", [])
    if not variants:
        price = product.get("price") or product.get("formatted_price") or "N/A"
        return price, price
    prices = []
    for v in variants:
        p = v.get("price") or v.get("formatted_price")
        if p:
            prices.append(float(p))
    if not prices:
        return "N/A", "N/A"
    return f"{min(prices):.2f} €", f"{max(prices):.2f} €"

# === BOT DISCORD ===
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Flag pour activer/désactiver la vitrine
vitrine_active = True

def build_vitrine_embed(product):
    stock = product.get("stock_count", 0)
    min_price, max_price = get_product_price_range(product)
    title = product["name"]
    url = product.get("url") or f"https://zikoshop.mysellauth.com/product/{product.get('path', product['id'])}"

    dispo = "🟢 En stock" if stock > 0 else "🔴 Rupture"
    embed = discord.Embed(
        title=title,
        url=url,
        description=f"{dispo}\n📦 Stock : **{stock}**\n💰 Prix : {min_price} - {max_price}",
        color=discord.Color.green() if stock > 0 else discord.Color.red()
    )
    embed.set_footer(text="ZIKO SHOP")
    return embed

async def update_vitrine():
    global vitrine_active
    await client.wait_until_ready()
    channel_objects = {k: client.get_channel(v) for k, v in CHANNELS.items()}

    while not client.is_closed():
        if vitrine_active:
            products = get_products()
            for p in products:
                pid = str(p["id"])

                # Choisir le salon
                name = p.get("name", "").lower()
                if "nitro" in name:
                    channel = channel_objects["Nitro"]
                elif "reaction" in name:
                    channel = channel_objects["Reactions"]
                elif "member" in name or "offline" in name or "online" in name:
                    channel = channel_objects["Membres"]
                elif "decoration" in name or "décoration" in name:
                    channel = channel_objects["Deco"]
                else:
                    channel = channel_objects["Boost"]

                embed = build_vitrine_embed(p)

                # Modification ou création du message
                if pid in message_map:
                    try:
                        msg = await channel.fetch_message(message_map[pid])
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        new_msg = await channel.send(embed=embed)
                        message_map[pid] = new_msg.id
                        save_message_map()
                else:
                    new_msg = await channel.send(embed=embed)
                    message_map[pid] = new_msg.id
                    save_message_map()

        await asyncio.sleep(CHECK_INTERVAL)

@client.event
async def on_message(message):
    global vitrine_active, message_map
    if not message.content.startswith(".") or message.author.bot:
        return

    if message.content == ".stopstock":
        vitrine_active = False
        await message.channel.send("⏸️ Les mises à jour de la vitrine sont arrêtées.")

    elif message.content == ".startstock":
        vitrine_active = True
        await message.channel.send("▶️ Les mises à jour de la vitrine sont réactivées.")

    elif message.content == ".resetvitrine":
        vitrine_active = False
        message_map = {}
        if os.path.exists(MESSAGE_FILE):
            os.remove(MESSAGE_FILE)
        await message.channel.send("♻️ La vitrine a été réinitialisée. Faites `.startstock` pour relancer.")

@client.event
async def on_ready():
    print(f"✅ Vitrine connectée en tant que {client.user}")

# === FLASK POUR LE PING ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot en ligne ✅"

def start_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# === MAIN ===
if __name__ == "__main__":
    threading.Thread(target=start_flask).start()

    async def main():
        async with client:
            asyncio.create_task(update_vitrine())
            await client.start(DISCORD_TOKEN)

    asyncio.run(main())
