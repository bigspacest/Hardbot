import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from keep_alive import keep_alive
from utils.db import Database
from utils.embeds import bad

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("hardbot")

EXTENSIONS = ("cogs.autoroles", "cogs.welcomer", "cogs.moderation")

PERMS_ES = {
    "administrator": "administrador",
    "manage_channels": "gestionar canales",
    "manage_roles": "gestionar roles",
    "ban_members": "banear miembros",
    "kick_members": "expulsar miembros",
    "moderate_members": "aislar temporalmente miembros",
    "send_messages": "enviar mensajes",
    "embed_links": "insertar enlaces",
}


def perms_text(perms):
    return ", ".join(PERMS_ES.get(p, p.replace("_", " ")) for p in perms)


class HardBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # activar "Server Members Intent" en el portal
        intents.message_content = True  # activar "Message Content Intent" en el portal
        super().__init__(
            command_prefix=commands.when_mentioned_or("?"),
            intents=intents,
            case_insensitive=True,      # ?lock = ?LOCK = ?Lock
            help_command=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
            activity=discord.Game(name="?cmds"),
        )
        self.db = Database()

    async def setup_hook(self):
        await self.db.setup_indexes()
        for ext in EXTENSIONS:
            await self.load_extension(ext)

        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild:
            guild = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info("Conectado como %s (%s) en %d servidores", self.user, self.user.id, len(self.guilds))


bot = HardBot()


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    error = getattr(error, "original", error)

    if isinstance(error, commands.MissingPermissions):
        text = f"Te faltan permisos para eso: {perms_text(error.missing_permissions)}."
    elif isinstance(error, commands.BotMissingPermissions):
        text = f"A mí me falta permiso para hacerlo: {perms_text(error.missing_permissions)}."
    elif isinstance(error, commands.MissingRequiredArgument):
        text = f"Te faltó un dato. Se usa así: `?{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
        text = "No encuentro a ese usuario. Si no está en el servidor, pásame su ID."
    elif isinstance(error, commands.ChannelNotFound):
        text = "No encuentro ese canal."
    elif isinstance(error, commands.BadArgument):
        text = f"Algo de lo que escribiste no cuadra. Se usa así: `?{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, commands.NoPrivateMessage):
        text = "Esto solo funciona dentro de un servidor."
    elif isinstance(error, commands.CheckFailure):
        text = "No puedes usar ese comando."
    elif isinstance(error, discord.Forbidden):
        text = "Discord no me deja hacer eso. Revisa que mi rol esté por encima del de la otra persona."
    else:
        log.exception("Error en el comando %s", ctx.command, exc_info=error)
        text = "Algo falló de mi lado. Prueba otra vez en un rato."

    try:
        await ctx.send(embed=bad(text))
    except discord.HTTPException:
        pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        text = "Este comando es solo para administradores."
    elif isinstance(error, (app_commands.NoPrivateMessage, app_commands.CheckFailure)):
        text = "Úsalo dentro de un servidor, siendo administrador."
    else:
        log.exception("Error en un slash command", exc_info=error)
        text = "Algo falló de mi lado. Prueba otra vez en un rato."

    if interaction.response.is_done():
        await interaction.followup.send(embed=bad(text), ephemeral=True)
    else:
        await interaction.response.send_message(embed=bad(text), ephemeral=True)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Falta la variable DISCORD_TOKEN")
    keep_alive()  # servidor Flask para UptimeRobot / puerto de Render
    bot.run(token, log_handler=None)
