import logging

import discord

from .embeds import bad

log = logging.getLogger("hardbot.ui")


class OwnerView(discord.ui.View):
    """Vista que solo puede tocar quien abrió el panel."""

    def __init__(self, origin: discord.Interaction, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.origin = origin

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.origin.user.id:
            await interaction.response.send_message(
                embed=bad("Este panel lo abrió otra persona."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        try:
            await self.origin.edit_original_response(view=None)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item):
        log.exception("Error en un panel", exc_info=error)
        msg = bad("Algo falló al guardar eso. Prueba otra vez.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)


class Panel(OwnerView):
    """Embed + componentes que se redibujan tras cada cambio."""

    def __init__(self, origin: discord.Interaction):
        super().__init__(origin)
        self.guild = origin.guild

    def build(self):
        raise NotImplementedError

    def make_embed(self) -> discord.Embed:
        raise NotImplementedError

    async def refresh(self):
        self.build()
        await self.origin.edit_original_response(embed=self.make_embed(), view=self)


class CloseButton(discord.ui.Button):
    def __init__(self, row=None):
        super().__init__(label="Cerrar", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=None)
        self.view.stop()
