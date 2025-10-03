# bot.py
import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ---------------------------
# CONFIG / LOGGING
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zikoshop")

SHOP_ID = os.environ.get("SHOP_ID", "181618")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")  # peut être "shop|key" ou token Bearer selon ta config
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # webhook alerts restock/oos
FEEDBACK_WEBHOOK_URL = os.environ.get("FEEDBACK_WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 10))  # secondes entre checks
MESSAGE_MAP_FILE = "message-map.json"

if not DISCORD_TOKEN:
    log.critical("DISCORD_TOKEN manquant !")
    raise SystemExit(1)

# Channels mapping (garde les IDs que tu avais)
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
last_feedback_ids = set()

# load/save message map
if os.path.exists(MESSAGE_MAP_FILE):
    try:
        with open(MESSAGE_MAP_FILE, "r", encoding="utf-8") as f:
            message_map = json.load(f)
            log.info("Loaded message-map.json (%d items)", len(message_map))
    except Exception as e:
        log.warning("Impossible de charger message-map.json: %s", e)
        message_map = {}

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

# We'll create an aiohttp session on startup and reuse it
aio_sess: Optional[aiohttp.ClientSession] = None

# ---------------------------
# HELPERS : API / WEBHOOKS
# ---------------------------
async def post_json(url: str, payload: dict, headers: Optional[dict] = None) -> aiohttp.ClientResponse:
    """Helper pour POST JSON"""
    global aio_sess
    if aio_sess is None:
        aio_sess = aiohttp.ClientSession()
    return await aio_sess.post(url, json=payload, headers=headers or {})

async def send_alert_webhook(event_type: str, product_name: str, product_url: str, stock: int, price: Optional[float] = None, diff: int = 0):
    """Envoie une alerte via WEBHOOK_URL (async)"""
    if not WEBHOOK_URL:
        log.warning("WEBHOOK_URL non configuré, alerte non envoyée")
        return

    title = ""
    description = ""
    color = 0x3498db
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

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": [
            {"name": "📦 Stock actuel", "value": str(stock), "inline": True},
            *([{"name": "💰 Prix", "value": f"{price:.2f} €", "inline": True}] if price is not None else []),
            *([{"name": "🛒 Lien d'achat", "value": product_url, "inline": False}] if event_type != "oos" else [])
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

# ---------------------------
# GET PRODUCTS (POST per doc) with fallback to public endpoint
# ---------------------------
async def fetch_products_via_post(session: aiohttp.ClientSession, page: int = 1, per_page: int = 100) -> Optional[dict]:
    url = f"https://api.sellauth.com/v1/shops/{SHOP_ID}/products"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {"page": page, "perPage": per_page}
    try:
        async with session.post(url, json=payload, headers=headers, timeout=15) as r:
            text = await r.text()
            if r.status == 200:
                try:
                    return json.loads(text)
                except Exception as exc:
                    log.error("POST API returned non-JSON (despite 200): %s", exc)
                    log.debug("Response text: %s", text[:500])
                    return None
            else:
                log.warning("POST API status %s: %s", r.status, text[:300])
                return None
    except Exception as e:
        log.exception("Erreur POST API products: %s", e)
        return None

async def fetch_products_via_public(session: aiohttp.ClientSession) -> Optional[dict]:
    # fallback public endpoint pattern used précédemment
    url = f"https://fastshopfrr.mysellauth.com/api/products?auth_token={AUTH_TOKEN}"
    try:
        async with session.get(url, timeout=12) as r:
            text = await r.text()
            if r.status == 200:
                try:
                    return json.loads(text)
                except Exception as exc:
                    log.error("Public API returned non-JSON: %s", exc)
                    log.debug("Response text: %s", text[:500])
                    return None
            else:
                log.warning("Public API status %s: %s", r.status, text[:300])
                return None
    except Exception as e:
        log.exception("Erreur GET public products: %s", e)
        return None

async def get_products() -> List[dict]:
    """Renvoie la liste des produits (async). Essaie POST (doc) puis fallback public GET."""
    global aio_sess
    if aio_sess is None:
        aio_sess = aiohttp.ClientSession()
    # try POST (recommended by doc)
    data = await fetch_products_via_post(aio_sess)
    if data:
        # doc usually returns { data: [...] } or similar
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        # if API returns list directly:
        if isinstance(data, list):
            return data
    # fallback
    data = await fetch_products_via_public(aio_sess)
    if data:
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        if isinstance(data, list):
            return data
    return []

# ---------------------------
# FEEDBACK (uses public endpoint with auth_token)
# ---------------------------
async def fetch_feedback() -> List[dict]:
    global aio_sess
    if aio_sess is None:
        aio_sess = aiohttp.ClientSession()
    url = f"https://fastshopfrr.mysellauth.com/feedback?auth_token={AUTH_TOKEN}"
    try:
        async with aio_sess.get(url, timeout=12) as r:
            text = await r.text()
            if r.status == 200:
                try:
                    parsed = json.loads(text)
                    return parsed if isinstance(parsed, list) else parsed.get("data", []) if isinstance(parsed, dict) else []
                except Exception as e:
                    log.error("Feedback response non-JSON: %s", e)
                    log.debug("Feedback text: %s", text[:400])
                    return []
            else:
                log.warning("Feedback status %s: %s", r.status, text[:200])
                return []
    except Exception as e:
        log.exception("Erreur fetch_feedback: %s", e)
        return []

# ---------------------------
# EMBED BUILDING + View Button
# ---------------------------
def format_price(price: Any) -> str:
    try:
        return f"{float(price):.2f} €"
    except:
        return str(price or "N/A")

def get_price_range_str(product: dict) -> str:
    variants = product.get("variants", []) or []
    if not variants:
        p = product.get("price") or "N/A"
        return format_price(p)
    prices = []
    for v in variants:
        try:
            prices.append(float(v.get("price")))
        except:
            continue
    if not prices:
        return "N/A"
    return f"{min(prices):.2f} € - {max(prices):.2f} €"

class BuyView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Acheter", url=url))

def build_product_embed(product: dict) -> discord.Embed:
    pid = product.get("id") or product.get("product_id") or "Produit"
    name = product.get("name") or product.get("title") or str(pid)
    stock = int(product.get("stock_count", product.get("stock", 0)) or 0)
    price_str = get_price_range_str(product)
    desc = product.get("description") or product.get("short_description") or ""
    url = product.get("url") or f"https://fastshopfrr.mysellauth.com/product/{product.get('path', pid)}"
    image = product.get("image") or product.get("thumbnail") or None

    color = 0x2ecc71 if stock > 0 else 0xe74c3c

    embed = discord.Embed(title=name, description=desc[:200] or None, color=color)
    embed.add_field(name="📦 Stock", value=f"**{stock} unités**", inline=True)
    embed.add_field(name="💰 Prix", value=price_str, inline=True)
    embed.add_field(name="🔗 Lien", value=f"[Voir / Acheter]({url})", inline=False)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text="ZIKO SHOP • Mise à jour automatique")
    return embed

# ---------------------------
# VITRINE LOOP (background task)
# ---------------------------
async def update_vitrine_loop():
    await bot.wait_until_ready()
    log.info("Vitrine loop démarrée (interval %ss)", CHECK_INTERVAL)
    while not bot.is_closed():
        try:
            if not vitrine_active:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            products = await get_products()
            if not products:
                log.debug("Aucun produit récupéré cette itération.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # prepare channels (fetch once per loop to ensure updated objects)
            channel_objs = {}
            for k, cid in CHANNELS.items():
                try:
                    ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
                    channel_objs[k] = ch
                except Exception as e:
                    log.warning("Impossible d'obtenir le channel %s (%s): %s", k, cid, e)
                    channel_objs[k] = None

            # iterate products
            for p in products:
                pid = str(p.get("id") or p.get("product_id") or p.get("path", "unknown"))
                stock = int(p.get("stock_count", p.get("stock", 0) or 0))
                name = p.get("name") or p.get("title", "Produit inconnu")
                url = p.get("url") or f"https://fastshopfrr.mysellauth.com/product/{p.get('path', pid)}"

                # detect changes
                old = last_stock.get(pid, None)
                if old is None:
                    # first time seeing it: set but also consider restock if >0
                    if stock > 0:
                        # treat as restock from 0 -> stock
                        asyncio.create_task(send_alert_webhook("restock", name, url, stock, None, diff=stock))
                else:
                    if stock != old:
                        if stock == 0 and old > 0:
                            asyncio.create_task(send_alert_webhook("oos", name, url, stock))
                        elif old == 0 and stock > 0:
                            asyncio.create_task(send_alert_webhook("restock", name, url, stock, None, diff=stock-old))
                        elif stock > old:
                            asyncio.create_task(send_alert_webhook("add", name, url, stock, None, diff=stock-old))
                last_stock[pid] = stock

                # choose channel
                pname = (name or "").lower()
                channel = None
                if "nitro" in pname:
                    channel = channel_objs.get("Nitro")
                elif "reaction" in pname or "reaction" in p.get("tags", []):
                    channel = channel_objs.get("Reactions")
                elif any(x in pname for x in ["member", "online", "offline"]):
                    channel = channel_objs.get("Membres")
                elif any(x in pname for x in ["decoration", "décoration"]):
                    channel = channel_objs.get("Deco")
                elif any(x in pname for x in ["discordaccount", "account"]):
                    channel = channel_objs.get("Acc")
                elif any(x in pname for x in ["serverboost", "14x"]):
                    channel = channel_objs.get("Boost")
                else:
                    channel = channel_objs.get("Boost")

                if channel is None:
                    log.debug("Channel pour %s introuvable, skip.", name)
                    continue

                # build embed and view
                embed = build_product_embed(p)
                view = BuyView(url)

                # post or edit
                if pid in message_map:
                    try:
                        msg_id = int(message_map[pid])
                        try:
                            msg = await channel.fetch_message(msg_id)
                            await msg.edit(embed=embed, view=view)
                        except discord.NotFound:
                            # message was deleted, recreate
                            new_msg = await channel.send(embed=embed, view=view)
                            message_map[pid] = new_msg.id
                            save_message_map()
                        except Exception as e:
                            log.exception("Erreur edit message %s: %s", pid, e)
                    except Exception as e:
                        log.exception("Message id invalide dans map pour %s: %s", pid, e)
                else:
                    try:
                        new_msg = await channel.send(embed=embed, view=view)
                        message_map[pid] = new_msg.id
                        save_message_map()
                    except Exception as e:
                        log.exception("Erreur envoi message vitrine: %s", e)
            # end products loop

        except Exception as e:
            log.exception("Erreur update_vitrine_loop: %s", e)

        await asyncio.sleep(CHECK_INTERVAL)

# ---------------------------
# FEEDBACK LOOP (background task)
# ---------------------------
async def feedback_loop():
    await bot.wait_until_ready()
    log.info("Feedback loop démarré")
    global last_feedback_ids
    while not bot.is_closed():
        try:
            feedbacks = await fetch_feedback()
            if feedbacks:
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
                        "fields": [{"name": "🎁 Produit", "value": product, "inline": False}],
                        "footer": {"text": "ZIKO SHOP • Feedback client"}
                    }
                    try:
                        if FEEDBACK_WEBHOOK_URL:
                            async with aio_sess.post(FEEDBACK_WEBHOOK_URL, json={"embeds": [embed]}) as resp:
                                if resp.status in (200, 204):
                                    log.info("Feedback envoyé: %s", fid)
                                else:
                                    txt = await resp.text()
                                    log.warning("Erreur feedback webhook %s: %s", resp.status, txt[:200])
                        else:
                            log.debug("FEEDBACK_WEBHOOK_URL non configuré - feedback skip")
                    except Exception:
                        log.exception("Erreur envoi feedback")
                    last_feedback_ids.add(fid)
        except Exception as e:
            log.exception("Erreur feedback_loop: %s", e)
        await asyncio.sleep(30)

# ---------------------------
# COMMANDS
# ---------------------------
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
        try:
            os.remove(MESSAGE_MAP_FILE)
        except:
            pass
    await ctx.send("🔄 Vitrine réinitialisée. Tous les embeds seront recréés.")

# slash /stock
@bot.tree.command(name="stock", description="Affiche le stock et les prix de tous les produits")
async def slash_stock(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    products = await get_products()
    if not products:
        await interaction.followup.send("❌ Aucun produit trouvé.", ephemeral=True)
        return
    embed = discord.Embed(title="📦 Stocks actuels - ZIKO SHOP", description="Récapitulatif des produits", color=0x3498db)
    for p in products:
        name = p.get("name") or p.get("title", "Produit")
        stock = int(p.get("stock_count", p.get("stock", 0) or 0))
        price_str = get_price_range_str(p)
        dispo = "🟢 En stock" if stock > 0 else "🔴 Rupture"
        embed.add_field(name=name, value=f"{dispo}\n📦 Stock : {stock}\n💰 Prix : {price_str}", inline=False)
    await interaction.followup.send(embed=embed)

# ---------------------------
# STARTUP / SHUTDOWN
# ---------------------------
@bot.event
async def on_ready():
    global aio_sess
    log.info("Bot connecté : %s", bot.user)
    # create aiohttp session if not exists
    if aio_sess is None:
        aio_sess = aiohttp.ClientSession()
    # start background tasks
    bot.loop.create_task(update_vitrine_loop())
    bot.loop.create_task(feedback_loop())
    log.info("Background tasks started. Slash commands syncing...")
    try:
        await bot.tree.sync()
        log.info("Slash commands synced.")
    except Exception as e:
        log.warning("Impossible de sync slash commands: %s", e)

async def _shutdown():
    log.info("Shutting down...")
    try:
        if aio_sess:
            await aio_sess.close()
    except Exception:
        pass
    await bot.close()

# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.info("Stopped by user")
