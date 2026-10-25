import copy
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import BASE, OFF, ON, bad, clip
from utils.ui import CloseButton, Panel

log = logging.getLogger("hardbot.welcomer")

MAX_CHANNELS = 5
TEXT_TYPES = [discord.ChannelType.text, discord.ChannelType.news]
RECOMMENDED_TYPES = TEXT_TYPES + [discord.ChannelType.forum]
VARIABLES = "{user} {username} {server} {count} {channels}"

DEFAULT = {
    "enabled": False,
    "channel_id": None,
    "mode": "embed",          # "embed" | "text"
    "color": 0x5865F2,
    "title": "Bienvenido, {username}",
    "message": "Ya somos {count} en **{server}**.\nEmpieza por {channels}.",
    "image_url": None,
    "channels": [],
}


# ───────────────────────── armado del mensaje ─────────────────────────

def render(text: str, member: discord.Member, cfg: dict) -> str:
    channels = " ".join(f"<#{c}>" for c in cfg["channels"]) or "los canales del servidor"
    for key, value in (
        ("{user}", member.mention),
        ("{username}", member.display_name),
        ("{server}", member.guild.name),
        ("{count}", str(member.guild.member_count)),
        ("{channels}", channels),
    ):
        text = text.replace(key, value)
    return text


def build_message(cfg: dict, member: discord.Member) -> dict:
    body = render(cfg["message"], member, cfg)
    if cfg["mode"] == "text":
        return {"content": clip(body, 2000)}

    embed = discord.Embed(
        title=clip(render(cfg["title"], member, cfg), 256) or None,
        description=clip(body, 4000),
        color=cfg["color"],
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if cfg.get("image_url"):
        embed.set_image(url=cfg["image_url"])
    embed.set_footer(text=f"Miembro n.º {member.guild.member_count}")

    out = {"embed": embed}
    # un @ dentro del embed no avisa a nadie, así que lo mandamos aparte si el texto no lo trae
    if "{user}" not in cfg["message"]:
        out["content"] = member.mention
    return out


def parse_hex(raw: str):
    s = raw.strip()
    if s.startswith("#"):
        s = s[1:]
    elif s.lower().startswith("0x"):
        s = s[2:]
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return int(s, 16)
    return None


# ───────────────────────── componentes del panel ─────────────────────────

class WelcomeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel):
        cid = panel.cfg["channel_id"]
        current = panel.guild.get_channel(cid) if cid else None
        super().__init__(
            placeholder="Canal donde se envían las bienvenidas",
            channel_types=TEXT_TYPES,
            min_values=1,
            max_values=1,
            default_values=[current] if current else [],
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await self.panel.save(channel_id=self.values[0].id)
        await interaction.response.defer()
        await self.panel.refresh()


class RecommendedSelect(discord.ui.ChannelSelect):
    def __init__(self, panel):
        current = [c for cid in panel.cfg["channels"] if (c := panel.guild.get_channel(cid))]
        super().__init__(
            placeholder=f"Canales recomendados (hasta {MAX_CHANNELS})",
            channel_types=RECOMMENDED_TYPES,
            min_values=0,
            max_values=MAX_CHANNELS,
            default_values=current,
            row=1,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await self.panel.save(channels=[c.id for c in self.values])
        await interaction.response.defer()
        await self.panel.refresh()


class ToggleButton(discord.ui.Button):
    def __init__(self, panel):
        on = panel.cfg["enabled"]
        super().__init__(
            label="Apagar" if on else "Activar",
            style=discord.ButtonStyle.secondary if on else discord.ButtonStyle.success,
            row=2,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await self.panel.save(enabled=not self.panel.cfg["enabled"])
        await interaction.response.defer()
        await self.panel.refresh()


class FormatButton(discord.ui.Button):
    def __init__(self, panel):
        label = "Formato: Embed" if panel.cfg["mode"] == "embed" else "Formato: Texto"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=2)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        new = "text" if self.panel.cfg["mode"] == "embed" else "embed"
        await self.panel.save(mode=new)
        await interaction.response.defer()
        await self.panel.refresh()


class MessageButton(discord.ui.Button):
    def __init__(self, panel):
        super().__init__(label="Editar mensaje", style=discord.ButtonStyle.primary, row=2)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MessageModal(self.panel))


class ColorButton(discord.ui.Button):
    def __init__(self, panel):
        super().__init__(
            label="Color",
            style=discord.ButtonStyle.secondary,
            disabled=panel.cfg["mode"] != "embed",
            row=2,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ColorModal(self.panel))


class ImageButton(discord.ui.Button):
    def __init__(self, panel):
        super().__init__(
            label="Imagen",
            style=discord.ButtonStyle.secondary,
            disabled=panel.cfg["mode"] != "embed",
            row=2,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ImageModal(self.panel))


class PreviewButton(discord.ui.Button):
    def __init__(self, panel):
        super().__init__(label="Vista previa", style=discord.ButtonStyle.secondary, row=3)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message(
                **build_message(self.panel.cfg, interaction.user), ephemeral=True
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=bad("Discord no pudo armar ese mensaje. Revisa la URL de la imagen."),
                ephemeral=True,
            )


# ───────────────────────────── modales ─────────────────────────────

class MessageModal(discord.ui.Modal, title="Mensaje de bienvenida"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.title_input = discord.ui.TextInput(
            label="Título (solo en formato embed)",
            default=panel.cfg["title"],
            required=False,
            max_length=100,
        )
        self.body_input = discord.ui.TextInput(
            label="Mensaje",
            style=discord.TextStyle.paragraph,
            default=panel.cfg["message"],
            placeholder="Variables: " + VARIABLES,
            max_length=1500,
        )
        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.panel.save(
            title=self.title_input.value.strip(), message=self.body_input.value.strip()
        )
        await interaction.response.defer()
        await self.panel.refresh()


class ColorModal(discord.ui.Modal, title="Color del embed"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.color_input = discord.ui.TextInput(
            label="Color en hexadecimal",
            default=f"#{panel.cfg['color']:06X}",
            placeholder="#5865F2",
            min_length=6,
            max_length=8,
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = parse_hex(self.color_input.value)
        if value is None:
            return await interaction.response.send_message(
                embed=bad("Ese color no es válido. Usa un hex de 6 dígitos, por ejemplo `#5865F2`."),
                ephemeral=True,
            )
        await self.panel.save(color=value)
        await interaction.response.defer()
        await self.panel.refresh()


class ImageModal(discord.ui.Modal, title="Imagen del embed"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.url_input = discord.ui.TextInput(
            label="URL de la imagen (vacío para quitarla)",
            default=panel.cfg["image_url"] or "",
            required=False,
            max_length=400,
            placeholder="https://...",
        )
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url_input.value.strip()
        if url and not url.startswith(("http://", "https://")):
            return await interaction.response.send_message(
                embed=bad("La URL tiene que empezar por http:// o https://"), ephemeral=True
            )
        await self.panel.save(image_url=url or None)
        await interaction.response.defer()
        await self.panel.refresh()


# ───────────────────────────── panel principal ─────────────────────────────

class WelcomerPanel(Panel):
    def __init__(self, cog, origin, cfg):
        super().__init__(origin)
        self.cog = cog
        self.cfg = cfg
        self.build()

    async def save(self, **fields):
        await self.cog.update(self.guild.id, **fields)
        self.cfg.update(fields)

    def build(self):
        self.clear_items()
        self.add_item(WelcomeChannelSelect(self))
        self.add_item(RecommendedSelect(self))
        self.add_item(ToggleButton(self))
        self.add_item(FormatButton(self))
        self.add_item(MessageButton(self))
        self.add_item(ColorButton(self))
        self.add_item(ImageButton(self))
        self.add_item(PreviewButton(self))
        self.add_item(CloseButton(row=3))

    def make_embed(self):
        cfg, g = self.cfg, self.guild
        is_embed = cfg["mode"] == "embed"
        embed = discord.Embed(
            title="Bienvenidas",
            description="Lo que se publica cuando entra alguien nuevo al servidor.",
            color=cfg["color"] if is_embed else (ON if cfg["enabled"] else OFF),
        )
        channel = g.get_channel(cfg["channel_id"]) if cfg["channel_id"] else None
        embed.add_field(name="Estado", value="Activas" if cfg["enabled"] else "Apagadas")
        embed.add_field(name="Canal", value=channel.mention if channel else "*Sin elegir*")
        embed.add_field(
            name="Formato",
            value=f"Embed · `#{cfg['color']:06X}`" if is_embed else "Texto normal",
        )
        recommended = [f"<#{c}>" for c in cfg["channels"] if g.get_channel(c)]
        embed.add_field(
            name="Canales recomendados",
            value=" ".join(recommended) if recommended else "*Ninguno*",
            inline=False,
        )
        if is_embed:
            embed.add_field(name="Título", value=clip(cfg["title"], 200) or "*Sin título*", inline=False)
        quoted = "\n".join("> " + line for line in cfg["message"].splitlines()) or "> "
        embed.add_field(name="Mensaje", value=clip(quoted, 700), inline=False)
        if is_embed:
            embed.add_field(name="Imagen", value="Puesta" if cfg["image_url"] else "Ninguna")

        alerts = []
        if cfg["enabled"] and not channel:
            alerts.append("Falta elegir el canal, sin eso no se envía nada.")
        if channel:
            perms = channel.permissions_for(g.me)
            if not perms.send_messages or (is_embed and not perms.embed_links):
                alerts.append(f"No puedo escribir o insertar enlaces en {channel.mention}.")
        if alerts:
            embed.add_field(name="Ojo", value="\n".join(alerts), inline=False)

        embed.set_footer(text="Variables: " + VARIABLES)
        return embed


# ─────────────────────────────────── cog ───────────────────────────────────

class Welcomer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_config(self, guild_id):
        doc = await self.bot.db.welcomer.find_one({"guild_id": guild_id}) or {}
        return {**copy.deepcopy(DEFAULT), **{k: v for k, v in doc.items() if k in DEFAULT}}

    async def update(self, guild_id, **fields):
        await self.bot.db.welcomer.update_one({"guild_id": guild_id}, {"$set": fields}, upsert=True)

    @app_commands.command(name="welcomer", description="Configura las bienvenidas del servidor")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def welcomer(self, interaction: discord.Interaction):
        cfg = await self.get_config(interaction.guild_id)
        panel = WelcomerPanel(self, interaction, cfg)
        await interaction.response.send_message(embed=panel.make_embed(), view=panel, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        cfg = await self.get_config(member.guild.id)
        if not cfg["enabled"] or not cfg["channel_id"]:
            return
        channel = member.guild.get_channel(cfg["channel_id"])
        if channel is None:
            return
        perms = channel.permissions_for(member.guild.me)
        if not perms.send_messages or (cfg["mode"] == "embed" and not perms.embed_links):
            return
        try:
            await channel.send(**build_message(cfg, member))
        except discord.HTTPException:
            log.warning("No pude enviar la bienvenida en %s", member.guild.id)


async def setup(bot):
    await bot.add_cog(Welcomer(bot))
