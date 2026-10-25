import logging
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from utils.embeds import BASE, simple
from utils.timeparse import format_duration, parse_duration

log = logging.getLogger("hardbot.moderation")

MAX_TIMEOUT = 28 * 86400  # tope que pone Discord


def esc(user) -> str:
    return discord.utils.escape_markdown(str(user))


def clean_reason(reason) -> str:
    return (reason or "").strip()[:400] or "Sin motivo"


def audit(ctx, text: str) -> str:
    return f"{ctx.author} ({ctx.author.id}) | {text}"[:512]


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def check_target(self, ctx, member: discord.Member) -> Optional[str]:
        """Devuelve el motivo por el que NO se puede actuar sobre el miembro, o None si se puede."""
        if member.id == ctx.author.id:
            return "No puedes hacerte eso a ti mismo."
        if member.id == ctx.guild.me.id:
            return "Conmigo no."
        if member.id == ctx.guild.owner_id:
            return "Al dueño del servidor no se le puede hacer eso."
        if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
            return "Esa persona tiene un rol igual o más alto que el tuyo."
        if member.top_role >= ctx.guild.me.top_role:
            return "Esa persona está por encima de mi rol, no puedo tocarla."
        return None

    # ───────────── canales ─────────────

    @commands.command(name="lock")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx, channel: Optional[discord.TextChannel] = None, *, reason: str = "Sin motivo"):
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send(embed=simple("Eso solo funciona en canales de texto."))
        reason = clean_reason(reason)
        everyone = ctx.guild.default_role

        overwrite = channel.overwrites_for(everyone)
        if overwrite.send_messages is False:
            return await ctx.send(embed=simple(f"{channel.mention} ya estaba bloqueado."))

        # guardamos cómo estaba para dejarlo igual al desbloquear
        await self.bot.db.locks.update_one(
            {"guild_id": ctx.guild.id, "channel_id": channel.id},
            {"$set": {"prev": overwrite.send_messages}},
            upsert=True,
        )
        # el aviso va antes: una vez bloqueado, ni yo podría escribir ahí
        try:
            await channel.send(embed=simple(f"Este canal quedó bloqueado por el staff.\n**Motivo:** {reason}"))
        except discord.HTTPException:
            pass

        overwrite.send_messages = False
        await channel.set_permissions(everyone, overwrite=overwrite, reason=audit(ctx, f"lock: {reason}"))
        if channel != ctx.channel:
            await ctx.send(embed=simple(f"{channel.mention} bloqueado."))

    @commands.command(name="unlock")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx, channel: Optional[discord.TextChannel] = None, *, reason: str = "Sin motivo"):
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send(embed=simple("Eso solo funciona en canales de texto."))
        reason = clean_reason(reason)
        everyone = ctx.guild.default_role

        overwrite = channel.overwrites_for(everyone)
        doc = await self.bot.db.locks.find_one_and_delete(
            {"guild_id": ctx.guild.id, "channel_id": channel.id}
        )
        if overwrite.send_messages is not False and doc is None:
            return await ctx.send(embed=simple(f"{channel.mention} no estaba bloqueado."))

        overwrite.send_messages = doc["prev"] if doc else None
        await channel.set_permissions(
            everyone,
            overwrite=None if overwrite.is_empty() else overwrite,
            reason=audit(ctx, f"unlock: {reason}"),
        )
        try:
            await channel.send(embed=simple("Canal desbloqueado. Ya se puede escribir."))
        except discord.HTTPException:
            pass
        if channel != ctx.channel:
            await ctx.send(embed=simple(f"{channel.mention} desbloqueado."))

    # ───────────── sanciones ─────────────

    @commands.command(name="mute")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, *, details: str = ""):
        """?mute @usuario [tiempo] [motivo]  (sin tiempo: 28d, el máximo de Discord)"""
        problem = self.check_target(ctx, member)
        if problem:
            return await ctx.send(embed=simple(problem))
        if member.guild_permissions.administrator:
            return await ctx.send(embed=simple("A un administrador Discord no deja mutearlo."))

        first, _, rest = details.strip().partition(" ")
        secs = parse_duration(first) if first else None
        if secs is None:
            secs, reason = MAX_TIMEOUT, clean_reason(details)
        else:
            reason = clean_reason(rest)
        if secs > MAX_TIMEOUT:
            return await ctx.send(embed=simple("Discord solo permite mutes de hasta 28 días (`4w`)."))

        until = discord.utils.utcnow() + timedelta(seconds=secs)
        await member.timeout(until, reason=audit(ctx, f"mute: {reason}"))
        await ctx.send(
            embed=simple(f"**{esc(member)}** quedó muteado por {format_duration(secs)}.\n**Motivo:** {reason}")
        )

    @commands.command(name="unmute")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason: str = "Sin motivo"):
        if not member.is_timed_out():
            return await ctx.send(embed=simple(f"**{esc(member)}** no está muteado."))
        await member.timeout(None, reason=audit(ctx, f"unmute: {clean_reason(reason)}"))
        await ctx.send(embed=simple(f"**{esc(member)}** ya puede hablar de nuevo."))

    @commands.command(name="kick")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = "Sin motivo"):
        problem = self.check_target(ctx, member)
        if problem:
            return await ctx.send(embed=simple(problem))
        reason = clean_reason(reason)
        await member.kick(reason=audit(ctx, f"kick: {reason}"))
        await ctx.send(embed=simple(f"**{esc(member)}** fue expulsado.\n**Motivo:** {reason}"))

    @commands.command(name="ban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, user: discord.User, *, reason: str = "Sin motivo"):
        """?ban @usuario|ID [motivo]  (funciona también con gente que ya no está en el servidor)"""
        member = ctx.guild.get_member(user.id)
        if member:
            problem = self.check_target(ctx, member)
            if problem:
                return await ctx.send(embed=simple(problem))
        elif user.id in (ctx.author.id, ctx.guild.me.id):
            return await ctx.send(embed=simple("Eso no."))

        try:
            await ctx.guild.fetch_ban(user)
            return await ctx.send(embed=simple(f"**{esc(user)}** ya estaba baneado."))
        except discord.NotFound:
            pass

        reason = clean_reason(reason)
        await ctx.guild.ban(user, reason=audit(ctx, f"ban: {reason}"), delete_message_seconds=0)
        await ctx.send(embed=simple(f"**{esc(user)}** fue baneado.\n**Motivo:** {reason}"))

    @commands.command(name="unban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user: discord.User, *, reason: str = "Sin motivo"):
        try:
            await ctx.guild.unban(user, reason=audit(ctx, f"unban: {clean_reason(reason)}"))
        except discord.NotFound:
            return await ctx.send(embed=simple(f"**{esc(user)}** no está baneado."))
        await ctx.send(embed=simple(f"**{esc(user)}** fue desbaneado."))

    # ───────────── info ─────────────

    @commands.command(name="userinfo")
    @commands.guild_only()
    async def userinfo(self, ctx, user: Optional[discord.User] = None):
        user = user or ctx.author
        member = ctx.guild.get_member(user.id)
        fmt = discord.utils.format_dt

        embed = discord.Embed(color=member.color if member and member.color.value else BASE)
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="ID", value=f"`{user.id}`")
        embed.add_field(name="Tipo", value="Bot" if user.bot else "Persona")
        embed.add_field(
            name="Cuenta creada",
            value=f"{fmt(user.created_at, 'D')}\n{fmt(user.created_at, 'R')}",
        )

        if member:
            if member.nick:
                embed.add_field(name="Apodo", value=discord.utils.escape_markdown(member.nick))
            if member.joined_at:
                embed.add_field(
                    name="Entró al servidor",
                    value=f"{fmt(member.joined_at, 'D')}\n{fmt(member.joined_at, 'R')}",
                )
            if member.is_timed_out():
                embed.add_field(name="Muteado hasta", value=fmt(member.timed_out_until, "f"))
            roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
            shown = " ".join(roles[:12]) or "*Ninguno*"
            if len(roles) > 12:
                shown += f" y {len(roles) - 12} más"
            embed.add_field(name=f"Roles ({len(roles)})", value=shown, inline=False)
        else:
            embed.set_footer(text="No está en este servidor")

        await ctx.send(embed=embed)

    @commands.command(name="cmds")
    async def cmds(self, ctx):
        embed = discord.Embed(
            title="Comandos de HardBot",
            description="El prefijo es `?` y da igual si lo escribes en mayúsculas o minúsculas.",
            color=BASE,
        )
        embed.add_field(
            name="Moderación",
            value=(
                "`?lock [#canal] [motivo]` bloquea el canal para @everyone\n"
                "`?unlock [#canal]` lo vuelve a abrir\n"
                "`?mute @usuario [tiempo] [motivo]` mute temporal (máx. 28d)\n"
                "`?unmute @usuario`\n"
                "`?kick @usuario [motivo]`\n"
                "`?ban @usuario o ID [motivo]`\n"
                "`?unban ID [motivo]`"
            ),
            inline=False,
        )
        embed.add_field(name="Información", value="`?userinfo [@usuario o ID]`", inline=False)
        embed.add_field(
            name="Configuración (solo admins)",
            value="`/autoroles` roles automáticos\n`/welcomer` mensajes de bienvenida",
            inline=False,
        )
        embed.set_footer(text="Tiempos: 30s, 10m, 2h, 1d, 1w")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
