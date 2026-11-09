# Imagem do Odoo da MaisFibra = imagem oficial + módulos deste repositório.
#
# Easypanel > maisfibra > odoo > Fonte: Github, repo MatCano/ixc-odoo-bridge,
# caminho de build "odoo-addons" (Dockerfile). Manter as montagens atuais.
#
# Os módulos vão para /mnt/maisfibra-addons (e não /mnt/extra-addons) porque
# /mnt/extra-addons é um VOLUME no Easypanel e esconderia o que a imagem copia.
FROM odoo:19.0

USER root
COPY maisfibra_crm /mnt/maisfibra-addons/maisfibra_crm
COPY maisfibra_whatsapp /mnt/maisfibra-addons/maisfibra_whatsapp
COPY maisfibra_auditoria /mnt/maisfibra-addons/maisfibra_auditoria
RUN chown -R odoo:odoo /mnt/maisfibra-addons
USER odoo

# O entrypoint oficial continua injetando host/usuário/senha do banco.
CMD ["odoo", "--addons-path=/mnt/extra-addons,/mnt/maisfibra-addons"]
