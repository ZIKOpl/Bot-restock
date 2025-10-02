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
FEEDBACK_WEBHOOK_URL = os.environ.get("FEEDBACK_WEBHOOK_URL")  # webhook pour feedback
CHECK_INTERVAL = 5  # secondes
MESSAGE_MAP_FILE = "message-map.json"

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

# Load message map
if os.path.exists(MESSAGE_MAP_FILE):
    with open(MESSAGE_MAP_FILE, "r") as f:
        message_map = json.load(f)

def save_message_map():
    with open(MESSAGE_MAP_FILE, "w") as f:
        json.dump(message_map, f)

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
    except:
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
        {"name": "📦 Stock actuel", "value": str(stock), "inline": True}
    ]

    if event_type != "oos":
        fields.append({"name": "🛒 Lien d'achat", "value": f"[Clique ici]({product_url})", "inline": True})

    if price:
        fields.append({"name": "💰 Prix", "value": format_price(price), "inline": True})

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": "ZIKO SHOP"}
    }

    payload = {"content": "@everyone", "embeds": [embed]}
    try:
        r = requests.post(WEBHOOK_URL, json=payload)
        if r.status_code not in [200, 204]:
            print(f"❌ Erreur Webhook: {r.status_code} - {r.text}")
    except Exception as e:
        print("❌ Erreur Webhook:", e)

# === FEEDBACK SYSTEM ===
def fetch_feedback():
    try:
        r = requests.get("https://fastshopfrr.mysellauth.com/feedback")
        if r.status_code == 200:
            return r.json()
        else:
            print("❌ Erreur Feedback:", r.status_code)
            return []
    except Exception as e:
        print("❌ Erreur requête Feedback:", e)
        return []

last_feedback_ids = set()

def feedback_loop():
    global last_feedback_ids
    print("💬 Feedback loop démarré...")
    while True:
        feedbacks = fetch_feedback()
        for fb in feedbacks:
            fid = fb.get("id")
            if not fid or fid in last_feedback_ids:
                continue

            rating = "⭐" * int(fb.get("rating", 0))
            text = fb.get("text", "Aucun avis")
            product = fb.get("product", {}).get("name", "Produit inconnu")

            embed = {
                "title": "📝 Nouveau Feedback",
                "description": f"**{rating}**\n{text}",
                "color": 0xFFD700,
                "fields": [
                    {"name": "🎁 Produit", "value": product, "inline": False}
                ],
                "footer": {"text": "ZIKO SHOP • Feedback client"}
            }

            payload = {"embeds": [embed]}
            try:
                requests.post(FEEDBACK_WEBHOOK_URL, json=payload)
                print(f"✅ Feedback envoyé: {fid}")
            except Exception as e:
                print("❌ Erreur envoi feedback:", e)

            last_feedback_ids.add(fid)

        time.sleep(30)

# === DISCORD BOT ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

def build_pro_embed(product):
    stock = product.get("stock_count", 0)
    min_price, max_price = get_product_price_range(product)
    title = product["name"]
    url = product.get("url") or f"https://fastshopfrr.mysellauth.com/product/{product.get('path', product['id'])}"

    dispo = "🟢 En stock" if stock > 0 else "🔴 Rupture"

    embed = discord.Embed(
        title=title,
        url=url,
        description=dispo,
        color=discord.Color.blue()
    )
    embed.add_field(name="📦 Stock", value=f"**{stock} unités**", inline=True)
    embed.add_field(name="💰 Prix", value=f"{min_price} - {max_price}", inline=True)

    if stock > 0:
        embed.add_field(name="🛒 Acheter", value=f"[Clique ici]({url})", inline=False)

    embed.set_footer(text="ZIKO SHOP • Mise à jour en temps réel")
    return embed

async def clear_channels():
    """Supprimer tous les messages des salons vitrines au démarrage"""
    await bot.wait_until_ready()
    for _, channel_id in CHANNELS.items():
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.purge(limit=100)
                print(f"🧹 Salon vidé : {channel.name}")
            except Exception as e:
                print(f"❌ Erreur purge salon {channel_id}: {e}")

async def update_vitrine():
    global message_map, vitrine_active, last_stock
    await bot.wait_until_ready()
    channels = {k: bot.get_channel(v) for k, v in CHANNELS.items()}

    while not bot.is_closed():
        if vitrine_active:
            products = get_products()
            for p in products:
                pid = str(p["id"])
                stock = p.get("stock_count", 0)
                name = p.get("name", "Produit inconnu")
                url = p.get("url") or f"https://fastshopfrr.sellauth.com/product/{p.get('path', pid)}"

                # === Détection des changements de stock ===
                old_stock = last_stock.get(pid, stock)
                if stock != old_stock:
                    if stock == 0 and old_stock > 0:
                        send_embed("oos", name, url, stock)
                    elif old_stock == 0 and stock > 0:
                        send_embed("restock", name, url, stock, diff=stock-old_stock)
                    elif stock > old_stock:
                        send_embed("add", name, url, stock, diff=stock-old_stock)
                last_stock[pid] = stock

                # === Choix du salon ===
                pname = name.lower()
                if "nitro" in pname:
                    channel = channels["Nitro"]
                elif "reaction" in pname:
                    channel = channels["Reactions"]
                elif any(x in pname for x in ["member", "online", "offline"]):
                    channel = channels["Membres"]
                elif any(x in pname for x in ["decoration", "décoration"]):
                    channel = channels["Deco"]
                elif any(x in pname for x in ["discordaccount", "account"]):
                    channel = channels["Acc"]
                elif any(x in pname for x in ["serverboost", "14x"]):
                    channel = channels["Boost"]
                else:
                    channel = channels["Boost"]

                # === Mise à jour de la vitrine ===
                embed = build_pro_embed(p)
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

# === COMMANDS ===
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
    await ctx.send("🔄 Vitrine réinitialisée. Tous les embeds seront recréés.")

# === SLASH COMMAND ===
@bot.tree.command(name="stock", description="Affiche le stock et les prix de tous les produits")
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

# === FLASK ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot en ligne ✅"

def start_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# === MAIN ===
if __name__ == "__main__":
    threading.Thread(target=start_flask).start()
    threading.Thread(target=feedback_loop).start()

    async def main():
        async with bot:
            asyncio.create_task(clear_channels())  # nettoyage au démarrage
            asyncio.create_task(update_vitrine())
            await bot.start(DISCORD_TOKEN)

    @bot.event
    async def on_ready():
        await bot.tree.sync()
        print(f"✅ Bot connecté en tant que {bot.user} et slash commands synchronisées")

    asyncio.run(main())
