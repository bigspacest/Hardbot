import discord

BASE = 0x2B2D31   # mismo gris del tema oscuro de Discord, no destaca
BAD = 0xE5484D
ON = 0x3BA55D
OFF = 0x4E5058


def simple(text, color=BASE):
    return discord.Embed(description=text, color=color)


def bad(text):
    return discord.Embed(description=text, color=BAD)


def clip(text, limit=1000):
    """Recorta para no pasar los límites de Discord."""
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def role_list(guild, ids):
    mentions = [r.mention for rid in ids if (r := guild.get_role(rid))]
    return clip(" ".join(mentions)) if mentions else "*Ninguno*"
