import os
import requests
import time
import threading
import asyncio
import json
import discord
from discord.ext import commands
from flask import Flask

# === CONFIG ===
SHOP_ID = os.environ.get("SHOP_ID", "181618")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHECK_INTERVAL = 5  # secondes
MESSAGE_MAP_FILE = "message-map.json"
DEFAULT_IMAGE_URL = "https://imagedelivery.net/HL_Fwm__tlvUGLZF2p74xw/ce50fff9-ba1b-4e48-514b-4734633d6f00/public"

# === CHANNELS ===
CHANNELS = {
    "Nitro": 1418965921116065852,
    "Membres": 1418969590251130953,
    "Boost": 1418996481032978643,
    "Deco": 1418968022126821386,
    "Acc": 1420167094888300554,
    "Reactions": 1419054351108018391
}

# === GLOBAL STATE ===
last_stock = {}
message_map = {}
vitrine_active = True

# Load message map from file
if os.path.exists(MESSAGE_MAP_FILE):
    with open(MESSAGE_MAP_FILE, "r") as f:
        message_map = json.load(f)

# === CONFIG FEEDBACK ===
FEEDBACK_URL = "https://zikoshop.mysellauth.com/feedback"
FEEDBACK_WEBHOOK = os.environ.get("FEEDBACK_WEBHOOK")  # webhook Discord pour feedback
CHECK_FEEDBACK_INTERVAL = 60
last_feedback_ids = set()

# === FUNCTIONS ===
def save_message_map():
    with open(MESSAGE_MAP_FILE, "w") as f:
        json.dump(message_map, f)

def get_product_image_url(product_url):
    try:
        r = requests.get(product_url)
        r.raise_for_status()
        html = r.text
        start_index = html.find('<meta property="og:image" content="') + len('<meta property="og:image" content="')
        end_index = html.find('"', start_index)
        return html[start_index:end_index]
    except requests.RequestException:
        return DEFAULT_IMAGE_URL

def get_products():
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
    r = requests.get(f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products", headers=headers)
    if r.status_code == 200:
        return r.json().get("data", [])
    else:
        print("❌ Erreur API:", r.status_code, r.text)
        return []

def format_price(price):
    try:
        return f"{float(price):.2f} €"
    except (ValueError, TypeError):
        return str(price)

def get_product_price_range(product):
    variants = product.get("variants", [])
    if not variants:
        price = product.get("price") or "N/A"
        return price, price
    prices = [float(v.get("price")) for v in variants if v.get("price")]
    if not prices:
        return "N/A", "N/A"
    return f"{min(prices):.2f} €", f"{max(prices):.2f} €"

# --- RESTOCK WEBHOOK ---
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
        fields.append({"name": "💰 Prix", "value": format_price(price), "inline": True})

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
        if r.status_code in [200, 204]:
            print(f"✅ {event_type} envoyé: {product_name}")
        else:
            print(f"❌ Erreur Webhook: {r.status_code} - {r.text}")
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
            url = p.get("url") or f"https://zikoshop.sellauth.com/product/{p.get('path', pid)}"
            price = p.get("price") or get_product_price_range(p)[0]

            old_stock = last_stock.get(pid, 0)

            if old_stock == 0 and stock > 0:
                send_embed("restock", name, url, stock, price)
            elif old_stock > 0 and stock > old_stock:
                send_embed("add", name, url, stock, price, stock - old_stock)
            elif old_stock > 0 and stock == 0:
                send_embed("oos", name, url, stock, price)

            last_stock[pid] = stock

        time.sleep(CHECK_INTERVAL)

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

def build_pro_embed(product):
    stock = product.get("stock_count", 0)
    min_price, max_price = get_product_price_range(product)
    title = product["name"]
    url = product.get("url") or f"https://zikoshop.sellauth.com/product/{product.get('path', product['id'])}"
    image_url = get_product_image_url(url)

    dispo = "🟢 En stock" if stock > 0 else "🔴 Rupture"
    color = discord.Color.green() if stock > 0 else discord.Color.red()

    embed = discord.Embed(
        title=title,
        url=url,
        description=dispo,
        color=color
    )
    embed.add_field(name="📦 Stock", value=f"**{stock} unités**", inline=True)
    embed.add_field(name="💰 Prix", value=f"{min_price} - {max_price}", inline=True)
    embed.add_field(name="🛒 Acheter", value=f"[Clique ici]({url})", inline=False)
    embed.set_image(url=image_url)
    embed.set_footer(text="ZIKO SHOP • Mise à jour en temps réel")
    return embed

async def update_vitrine():
    global message_map, vitrine_active
    await bot.wait_until_ready()
    channels = {k: bot.get_channel(v) for k, v in CHANNELS.items()}

    while not bot.is_closed():
        if vitrine_active:
            products = get_products()
            for p in products:
                pid = str(p["id"])
                name = p.get("name", "").lower()

                # Routing précis
                if "nitro" in name:
                    channel = channels["Nitro"]
                elif "reaction" in name:
                    channel = channels["Reactions"]
                elif any(x in name for x in ["member", "online", "offline"]):
                    channel = channels["Membres"]
                elif any(x in name for x in ["decoration", "décoration"]):
                    channel = channels["Deco"]
                elif any(x in name for x in ["discordaccount", "account"]):
                    channel = channels["Acc"]
                elif any(x in name for x in ["serverboost", "14x"]):
                    channel = channels["Boost"]
                else:
                    channel = channels["Boost"]

                embed = build_pro_embed(p)

                # Send or edit
                if pid in message_map:
                    try:
                        msg = await channel.fetch_message(message_map[pid])
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        message_map.pop(pid, None)
                        new_msg = await channel.send(embed=embed)
                        message_map[pid] = new_msg.id
                        save_message_map()
                else:
                    new_msg = await channel.send(embed=embed)
                    message_map[pid] = new_msg.id
                    save_message_map()

        await asyncio.sleep(10)

# --- COMMANDS ---
@bot.command()
async def stopstock(ctx):
    global vitrine_active
    vitrine_active = False
    await ctx.send("🛑 Les vitrines ont été stoppées.")

@bot.command()
async def startstock(ctx):
    global vitrine_active
    vitrine_active = True
    await ctx.send("✅ Les vitrines sont maintenant actives.")

@bot.command()
async def resetvitrine(ctx):
    global message_map
    message_map = {}
    if os.path.exists(MESSAGE_MAP_FILE):
        os.remove(MESSAGE_MAP_FILE)
    await ctx.send("🔄 Vitrine réinitialisée. Tous les embeds seront recréés au prochain cycle.")

# --- SLASH COMMAND /stock ---
from discord import app_commands

tree = app_commands.CommandTree(bot)

@tree.command(name="stock", description="Affiche le stock et les prix de tous les produits")
async def stock(interaction: discord.Interaction):
    products = get_products()
    if not products:
        await interaction.response.send_message("❌ Aucun produit trouvé.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📦 Stocks actuels - ZIKO SHOP",
        description="Voici le récapitulatif des produits avec leur stock et prix",
        color=discord.Color.blue()
    )

    for p in products:
        stock_count = p.get("stock_count", 0)
        min_price, max_price = get_product_price_range(p)
        name = p.get("name", "Produit inconnu")
        dispo = "🟢 En stock" if stock_count > 0 else "🔴 Rupture"
        embed.add_field(
            name=name,
            value=f"{dispo}\n📦 Stock : {stock_count}\n💰 Prix : {min_price} - {max_price}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=False)
# === FEEDBACKS ===
def get_feedbacks():
    try:
        r = requests.get(FEEDBACK_URL, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("❌ Erreur récupération feedbacks:", e)
    return []

def build_feedback_embed(feedback):
    rating = int(feedback.get("rating", 0))
    stars = "⭐" * rating
    text = feedback.get("text", "Aucun avis fourni")
    product = feedback.get("product", {}).get("name", "Produit inconnu")
    author = feedback.get("author", "Anonyme")

    embed = {
        "title": "📝 Nouveau Feedback",
        "description": f"**{author}** a laissé un avis sur le shop.",
        "color": 0x2ecc71,
        "fields": [
            {"name": "⭐ Note", "value": stars, "inline": True},
            {"name": "💬 Avis", "value": text, "inline": False},
            {"name": "📦 Produit", "value": product, "inline": False}
        ],
        "footer": {"text": "ZIKO SHOP • Feedback Client"}
    }
    return embed

def feedback_loop():
    global last_feedback_ids
    print("💬 Système de feedback démarré...")
    while True:
        feedbacks = get_feedbacks()
        for fb in feedbacks:
            fid = fb.get("id")
            if fid and fid not in last_feedback_ids:
                embed = build_feedback_embed(fb)
                payload = {"embeds": [embed]}
                try:
                    r = requests.post(FEEDBACK_WEBHOOK, json=payload)
                    if r.status_code in [200, 204]:
                        print(f"✅ Feedback envoyé: {fid}")
                        last_feedback_ids.add(fid)
                except Exception as e:
                    print("❌ Erreur envoi Feedback:", e)
        time.sleep(CHECK_FEEDBACK_INTERVAL)
        
# --- FLASK POUR PING ---
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
    
    async def main():
        async with bot:
            asyncio.create_task(update_vitrine())
            await bot.start(DISCORD_TOKEN)
    
    # Synchronisation des slash commands
    @bot.event
    async def on_ready():
        await tree.sync()
        print(f"✅ Bot connecté en tant que {bot.user} et slash commands synchronisées")

    asyncio.run(main())
