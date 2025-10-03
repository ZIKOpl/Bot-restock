# bot.py
import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask

# ---------------------------
# CONFIG / LOGGING
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zikoshop")

SHOP_ID = os.environ.get("SHOP_ID", "181618")
SELLAUTH_TOKEN = os.environ.get("SELLAUTH_TOKEN")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 10))
MESSAGE_MAP_FILE = "message-map.json"

CHANNELS = {
    "Nitro": 1418965921116065852,
    "Membres": 1418969590251130953,
    "Boost": 1418996481032978643,
    "Deco": 1418968022126821386,
    "Acc": 1420167094888300554,
    "Reactions": 1419054351108018391
}

# ---------------------------
# STATE
# ---------------------------
last_stock: Dict[str, int] = {}
message_map: Dict[str, int] = {}
vitrine_active = True

if os.path.exists(MESSAGE_MAP_FILE):
    try:
        with open(MESSAGE_MAP_FILE, "r", encoding="utf-8") as f:
            message_map = json.load(f)
            log.info("Loaded message-map.json (%d items)", len(message_map))
    except Exception as e:
        log.warning("Impossible de charger message-map.json: %s", e)

def save_message_map():
    try:
        with open(MESSAGE_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(message_map, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("Erreur écriture message-map.json: %s", e)

# ---------------------------
# DISCORD BOT
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix=".", intents=intents)
aio_sess: Optional[aiohttp.ClientSession] = None

# ---------------------------
# HELPERS : API / WEBHOOKS
# ---------------------------
async def send_alert_webhook(event_type: str, product_name: str, product_url: str, stock: int, diff: int = 0):
    if not WEBHOOK_URL:
        log.warning("WEBHOOK_URL non configuré, alerte non envoyée")
        return

    title, description, color = "", "", 0x3498db
    if event_type == "restock":
        title = f"🚀 Restock ! {product_name}"
        description = f"Le produit **{product_name}** est de retour en stock !"
        color = 0x00ff00
    elif event_type == "add":
        title = f"📈 Stock augmenté | {product_name}"
        description = f"➕ {diff} unités ajoutées\n📦 Nouveau stock : **{stock}**"
    elif event_type == "oos":
        title = f"❌ Rupture de stock | {product_name}"
        description = f"Le produit **{product_name}** est maintenant en rupture ! 🛑"
        color = 0xff0000
    else:
        return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": [
            {"name": "📦 Stock actuel", "value": str(stock), "inline": True},
            {"name": "🛒 Lien d'achat", "value": product_url, "inline": False}
        ],
        "footer": {"text": "ZIKO SHOP"}
    }
    payload = {"content": "@everyone", "embeds": [embed]}
    try:
        async with aio_sess.post(WEBHOOK_URL, json=payload) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                log.error("Erreur Webhook: %s - %s", resp.status, text[:200])
            else:
                log.info("Webhook envoyé: %s - %s", event_type, product_name)
    except Exception as e:
        log.exception("Erreur envoi webhook: %s", e)

async def fetch_products() -> List[dict]:
    global aio_sess
    if aio_sess is None:
        aio_sess = aiohttp.ClientSession()
    url = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"
    headers = {"Authorization": f"Bearer {SELLAUTH_TOKEN}"}
    try:
        async with aio_sess.get(url, headers=headers) as r:
            if r.status == 200:
                return (await r.json()).get("data", [])
            else:
                log.warning("fetch_products status %s", r.status)
                return []
    except Exception as e:
        log.exception("Erreur fetch_products: %s", e)
        return []

# ---------------------------
# EMBEDS
# ---------------------------
def build_product_embed(p: dict) -> discord.Embed:
    pid = str(p.get("id") or p.get("product_id") or "Produit")
    name = p.get("name") or "Produit"
    stock = int(p.get("stock_count", p.get("stock", 0) or 0))
    price = p.get("price", "N/A")
    url = p.get("url") or f"https://fastshopfrr.mysellauth.com/product/{pid}"
    color = 0x2ecc71 if stock > 0 else 0xe74c3c
    embed = discord.Embed(
        title=name, 
        url=url,
        color=color,
        description=f"💰 Prix : **{price} €**\n📦 Stock : **{stock}**"
    )
    embed.set_footer(text="ZIKO SHOP")
    return embed

class BuyView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Acheter", style=discord.ButtonStyle.green, url=url))

# ---------------------------
# VITRINE LOOP
# ---------------------------
async def update_vitrine_loop():
    await bot.wait_until_ready()
    log.info("Vitrine loop démarrée")
    global last_stock, message_map
    while not bot.is_closed():
        try:
            if not vitrine_active:
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            products = await fetch_products()
            if not products:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            channel_objs = {}
            for k, cid in CHANNELS.items():
                try:
                    ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
                    channel_objs[k] = ch
                except:
                    channel_objs[k] = None

            for p in products:
                pid = str(p.get("id") or p.get("product_id") or "unknown")
                stock = int(p.get("stock_count", p.get("stock", 0) or 0))
                name = p.get("name") or "Produit"
                url = p.get("url") or f"https://fastshopfrr.mysellauth.com/product/{pid}"
                old_stock = last_stock.get(pid)

                if old_stock is None and stock > 0:
                    asyncio.create_task(send_alert_webhook("restock", name, url, stock, diff=stock))
                elif old_stock is not None and stock != old_stock:
                    if stock == 0:
                        asyncio.create_task(send_alert_webhook("oos", name, url, stock))
                    elif old_stock == 0 and stock > 0:
                        asyncio.create_task(send_alert_webhook("restock", name, url, stock, diff=stock-old_stock))
                    elif stock > old_stock:
                        asyncio.create_task(send_alert_webhook("add", name, url, stock, diff=stock-old_stock))
                last_stock[pid] = stock

                # Choix salon
                pname = name.lower()
                channel = channel_objs.get(
                    "Nitro" if "nitro" in pname else
                    "Reactions" if "reaction" in pname else
                    "Membres" if any(x in pname for x in ["member", "online", "offline"]) else
                    "Deco" if any(x in pname for x in ["decoration", "décoration"]) else
                    "Acc" if any(x in pname for x in ["discordaccount", "account"]) else
                    "Boost"
                )
                if channel is None:
                    continue

                embed = build_product_embed(p)
                view = BuyView(url)
                if pid in message_map:
                    try:
                        msg = await channel.fetch_message(message_map[pid])
                        await msg.edit(embed=embed, view=view)
                    except discord.NotFound:
                        new_msg = await channel.send(embed=embed, view=view)
                        message_map[pid] = new_msg.id
                        save_message_map()
                else:
                    new_msg = await channel.send(embed=embed, view=view)
                    message_map[pid] = new_msg.id
                    save_message_map()

        except Exception as e:
            log.exception("Erreur update_vitrine_loop: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)

# ---------------------------
# COMMANDS
# ---------------------------
@bot.command()
async def stopstock(ctx):
    global vitrine_active
    vitrine_active = False
    await ctx.send("🛑 Vitrines stoppées.")

@bot.command()
async def startstock(ctx):
    global vitrine_active
    vitrine_active = True
    await ctx.send("✅ Vitrines actives.")

@bot.command()
async def resetvitrine(ctx):
    global message_map
    message_map = {}
    if os.path.exists(MESSAGE_MAP_FILE):
        os.remove(MESSAGE_MAP_FILE)
    await ctx.send("🔄 Vitrine réinitialisée.")

# ---------------------------
# STARTUP / SHUTDOWN
# ---------------------------
@bot.event
async def on_ready():
    global aio_sess
    log.info("Bot connecté : %s", bot.user)
    if aio_sess is None:
        aio_sess = aiohttp.ClientSession()

    # --- SUPPRESSION DES MESSAGES DES SALONS VITRINE ---
    for cid in CHANNELS.values():
        try:
            channel = bot.get_channel(cid) or await bot.fetch_channel(cid)
            if channel:
                async for msg in channel.history(limit=None):
                    try:
                        await msg.delete()
                    except Exception as e:
                        log.warning("Impossible de supprimer un message dans %s: %s", channel.id, e)
        except Exception as e:
            log.warning("Erreur récupération du salon %s: %s", cid, e)
    message_map.clear()
    save_message_map()
    log.info("Tous les messages des salons vitrine ont été supprimés.")

    bot.loop.create_task(update_vitrine_loop())
    try:
        await bot.tree.sync()
        log.info("Slash commands synced.")
    except Exception as e:
        log.warning("Impossible de sync slash commands: %s", e)

async def _shutdown():
    log.info("Shutting down...")
    if aio_sess:
        await aio_sess.close()
    await bot.close()

# ---------------------------
# FLASK POUR KEEP-ALIVE
# ---------------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    import threading
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).start()

# ---------------------------
# MAIN
# ---------------------------
if __name__ == "__main__":
    run_flask()
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.info("Stopped by user")
