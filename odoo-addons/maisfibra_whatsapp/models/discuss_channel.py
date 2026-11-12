import logging
import re
from odoo import api, models

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    def message_post(self, **kwargs):
        """Ao postar mensagem no canal de chat de Mensagens Diretas, encaminha para o WhatsApp Web."""
        message = super().message_post(**kwargs)

        if self.env.context.get("wa_skip_outbound"):
            return message

        for channel in self:
            if channel.channel_type != "chat":
                continue

            author_id = kwargs.get("author_id") or self.env.user.partner_id.id
            # Envia apenas mensagens digitadas pelo usuário logado no Odoo
            if author_id != self.env.user.partner_id.id:
                continue

            body = kwargs.get("body") or ""
            clean_body = re.sub(r"<[^>]+>", "", body or "").strip()

            # Identifica o parceiro de destino
            other_partners = channel.channel_member_ids.mapped("partner_id").filtered(
                lambda p: p.id != self.env.user.partner_id.id
            )

            provider = self.env["maisfibra.whatsapp.provider"]
            for partner in other_partners:
                phone = partner.phone
                if not phone:
                    continue

                attachments = message.attachment_ids
                if attachments:
                    for att in attachments:
                        mime = att.mimetype or "application/octet-stream"
                        is_voice = "audio" in mime
                        datas = att.datas.decode() if isinstance(att.datas, bytes) else att.datas
                        try:
                            provider.with_context(wa_skip_outbound=True).send_media(
                                phone,
                                datas,
                                mime,
                                att.name or "anexo",
                                caption=clean_body,
                                is_voice=is_voice,
                                user_id=self.env.user.id
                            )
                            _logger.info("Discuss -> WhatsApp mídia despachada com sucesso para %s (%s)", partner.name, phone)
                        except Exception as ex:
                            _logger.warning("Falha ao despachar mídia do Discuss para o WhatsApp de %s: %s", partner.name, ex)
                elif clean_body:
                    try:
                        provider.with_context(wa_skip_outbound=True).send_text(
                            phone, clean_body, user_id=self.env.user.id
                        )
                        _logger.info("Discuss -> WhatsApp texto despachado com sucesso para %s (%s)", partner.name, phone)
                    except Exception as ex:
                        _logger.warning("Falha ao despachar texto do Discuss para o WhatsApp de %s: %s", partner.name, ex)

        return message
