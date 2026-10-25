import copy
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import OFF, ON, bad, clip, role_list, simple
from utils.timeparse import format_duration, parse_duration
from utils.ui import CloseButton, OwnerView, Panel

log = logging.getLogger("hardbot.autoroles")

MAX_ROLES = 10              # roles por lista (entrada / bots)
MAX_TIMED = 20              # time-roles por servidor
MIN_DELAY = 10              # segundos (el ciclo revisa cada 10s)
MAX_DELAY = 52 * 7 * 86400  # 52 semanas

DEFAULT = {"enabled": False, "join_roles": [], "bot_roles": [], "time_roles": []}


# ───────────────────────── componentes del panel ─────────────────────────

class RoleListSelect(discord.ui.RoleSelect):
    def __init__(self, panel, key, placeholder, row):
        current = [r for rid in panel.cfg[key] if (r := panel.guild.get_role(rid))]
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=MAX_ROLES,
            default_values=current,
            row=row,
        )
        self.panel = panel
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        valid = [r for r in self.values if r.is_assignable()]
        skipped = [r for r in self.values if r not in valid]
        await self.panel.save(**{self.key: [r.id for r in valid]})
        await interaction.response.defer()
        await self.panel.refresh()
        if skipped:
            names = ", ".join(r.mention for r in skipped)
            await interaction.followup.send(
                embed=bad(
                    f"No puedo repartir {names}: está por encima de mi rol o es un rol de integración. "
                    "Sube mi rol en Ajustes del servidor → Roles y vuelve a elegirlo."
                ),
                ephemeral=True,
            )


class RemoveTimedSelect(discord.ui.Select):
    def __init__(self, panel):
        options = []
        for t in panel.cfg["time_roles"][:25]:
            role = panel.guild.get_role(t["role_id"])
            name = role.name if role else "rol borrado"
            options.append(
                discord.SelectOption(
                    label=clip(f"{name} · {format_duration(t['delay'])}", 100),
                    value=str(t["role_id"]),
                )
            )
        super().__init__(
            placeholder="Quitar un rol por tiempo", min_values=1, max_values=1, options=options, row=2
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        rid = int(self.values[0])
        left = [t for t in self.panel.cfg["time_roles"] if t["role_id"] != rid]
        await self.panel.save(time_roles=left)
        await self.panel.cog.bot.db.timeroles.delete_many(
            {"guild_id": self.panel.guild.id, "role_id": rid}
        )
        await interaction.response.defer()
        await self.panel.refresh()


class ToggleButton(discord.ui.Button):
    def __init__(self, panel):
        on = panel.cfg["enabled"]
        super().__init__(
            label="Apagar" if on else "Activar",
            style=discord.ButtonStyle.secondary if on else discord.ButtonStyle.success,
            row=3,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await self.panel.save(enabled=not self.panel.cfg["enabled"])
        await interaction.response.defer()
        await self.panel.refresh()


class AddTimedButton(discord.ui.Button):
    def __init__(self, panel):
        super().__init__(label="Añadir por tiempo", style=discord.ButtonStyle.primary, row=3)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        if len(self.panel.cfg["time_roles"]) >= MAX_TIMED:
            return await interaction.response.send_message(
                embed=bad(f"Ya hay {MAX_TIMED} roles por tiempo, que es el máximo. Quita alguno primero."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=simple("Elige el rol. Después te pregunto cuánto tiempo debe pasar."),
            view=TimedPicker(self.panel, interaction),
            ephemeral=True,
        )


class TimedPicker(OwnerView):
    def __init__(self, panel, origin):
        super().__init__(origin, timeout=180)
        self.add_item(TimedRoleSelect(panel))


class TimedRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel):
        super().__init__(placeholder="Rol que se va a entregar", min_values=1, max_values=1)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if not role.is_assignable():
            return await interaction.response.send_message(
                embed=bad(f"No puedo dar {role.mention}: está por encima de mi rol o es un rol especial."),
                ephemeral=True,
            )
        await interaction.response.send_modal(TimedModal(self.panel, role))


class TimedModal(discord.ui.Modal, title="¿Cuánto tiempo después?"):
    def __init__(self, panel, role):
        super().__init__()
        self.panel = panel
        self.role = role
        self.time_input = discord.ui.TextInput(
            label="Tiempo desde que entra",
            placeholder="Ej: 30s, 10m, 2h, 1d, 1w o junto: 1h30m",
            max_length=20,
        )
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        secs = parse_duration(self.time_input.value)
        if secs is None or not (MIN_DELAY <= secs <= MAX_DELAY):
            return await interaction.response.send_message(
                embed=bad(
                    "Ese tiempo no sirve. Escríbelo como `30s`, `10m`, `2h`, `1d` o `1w` "
                    "(o junto, `1h30m`). Mínimo 10s, máximo 52w."
                ),
                ephemeral=True,
            )
        entries = [t for t in self.panel.cfg["time_roles"] if t["role_id"] != self.role.id]
        entries.append({"role_id": self.role.id, "delay": secs})
        await self.panel.save(time_roles=entries)
        await interaction.response.edit_message(
            embed=simple(f"Listo. {self.role.mention} se entrega a los {format_duration(secs)} de entrar."),
            view=None,
        )
        await self.panel.refresh()


# ───────────────────────────── panel principal ─────────────────────────────

class AutoRolesPanel(Panel):
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
        self.add_item(RoleListSelect(self, "join_roles", "Roles que reciben al entrar", 0))
        self.add_item(RoleListSelect(self, "bot_roles", "Roles que reciben los bots", 1))
        if self.cfg["time_roles"]:
            self.add_item(RemoveTimedSelect(self))
        self.add_item(ToggleButton(self))
        self.add_item(AddTimedButton(self))
        self.add_item(CloseButton(row=3))

    def make_embed(self):
        cfg, g = self.cfg, self.guild
        embed = discord.Embed(
            title="Auto-roles",
            description="Aquí decides qué roles recibe la gente al entrar y cuáles llegan pasado un rato.",
            color=ON if cfg["enabled"] else OFF,
        )
        embed.add_field(name="Estado", value="Activo" if cfg["enabled"] else "Apagado", inline=False)
        embed.add_field(name="Al entrar", value=role_list(g, cfg["join_roles"]), inline=False)
        embed.add_field(name="Bots", value=role_list(g, cfg["bot_roles"]), inline=False)

        lines = []
        for t in sorted(cfg["time_roles"], key=lambda t: t["delay"]):
            role = g.get_role(t["role_id"])
            lines.append(f"`{format_duration(t['delay'])}` → {role.mention if role else '*rol borrado*'}")
        embed.add_field(
            name="Después de un tiempo",
            value=clip("\n".join(lines)) if lines else "*Ninguno. Usa «Añadir por tiempo».*",
            inline=False,
        )
        if not g.me.guild_permissions.manage_roles:
            embed.add_field(
                name="Ojo",
                value="No tengo el permiso *Gestionar roles*, así que no podré repartir nada.",
                inline=False,
            )
        embed.set_footer(text="Los cambios se guardan al momento · Tiempos: s, m, h, d, w")
        return embed


# ─────────────────────────────────── cog ───────────────────────────────────

class AutoRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.process_pending.start()

    async def cog_unload(self):
        self.process_pending.cancel()

    async def get_config(self, guild_id):
        doc = await self.bot.db.autoroles.find_one({"guild_id": guild_id}) or {}
        return {**copy.deepcopy(DEFAULT), **{k: v for k, v in doc.items() if k in DEFAULT}}

    async def update(self, guild_id, **fields):
        await self.bot.db.autoroles.update_one({"guild_id": guild_id}, {"$set": fields}, upsert=True)

    @app_commands.command(name="autoroles", description="Configura los roles automáticos del servidor")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def autoroles(self, interaction: discord.Interaction):
        cfg = await self.get_config(interaction.guild_id)
        panel = AutoRolesPanel(self, interaction, cfg)
        await interaction.response.send_message(embed=panel.make_embed(), view=panel, ephemeral=True)

    # ── eventos ──

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = await self.get_config(member.guild.id)
        if not cfg["enabled"]:
            return

        ids = cfg["bot_roles"] if member.bot else cfg["join_roles"]
        roles = [r for rid in ids if (r := member.guild.get_role(rid)) and r.is_assignable()]
        if roles:
            try:
                await member.add_roles(*roles, reason="Auto-roles: ingreso")
            except discord.HTTPException:
                log.warning("No pude dar los roles de ingreso a %s en %s", member.id, member.guild.id)

        if member.bot:
            return
        now = time.time()
        for t in cfg["time_roles"]:
            await self.bot.db.timeroles.update_one(
                {"guild_id": member.guild.id, "user_id": member.id, "role_id": t["role_id"]},
                {"$set": {"due_at": now + t["delay"]}},
                upsert=True,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self.bot.db.timeroles.delete_many({"guild_id": member.guild.id, "user_id": member.id})

    # ── entrega de roles por tiempo (sobrevive a reinicios: todo está en Mongo) ──

    @tasks.loop(seconds=10)
    async def process_pending(self):
        try:
            await self._run_pending()
        except Exception:
            log.exception("Falló el ciclo de roles por tiempo")

    @process_pending.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _run_pending(self):
        db = self.bot.db
        enabled = {}
        async for doc in db.timeroles.find({"due_at": {"$lte": time.time()}}).limit(200):
            gid = doc["guild_id"]
            guild = self.bot.get_guild(gid)
            if guild is None:
                await db.timeroles.delete_one({"_id": doc["_id"]})
                continue
            if gid not in enabled:
                enabled[gid] = (await self.get_config(gid))["enabled"]

            member = guild.get_member(doc["user_id"])
            role = guild.get_role(doc["role_id"])
            if not enabled[gid] or member is None or role is None or role in member.roles:
                await db.timeroles.delete_one({"_id": doc["_id"]})
                continue

            try:
                await member.add_roles(role, reason="Auto-roles: tiempo cumplido")
            except discord.Forbidden:
                log.warning("Sin permisos para dar %s a %s en %s", role.id, member.id, gid)
            except discord.HTTPException:
                continue  # se reintenta en el próximo ciclo
            await db.timeroles.delete_one({"_id": doc["_id"]})


async def setup(bot):
    await bot.add_cog(AutoRoles(bot))
