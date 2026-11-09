# Roteiros de Operação — Mais Fibra (Odoo + IXC)

Versão 1.0 · 09/11/2026 · Procedimentos da Frente 0 (ambientes, backup, restauração, acesso).

---

## 1. Backup

### 1.1 O que já existe
| Volume | Serviço | Agenda | Destino |
|---|---|---|---|
| `odoo-db-data` | odoo-db | diário 02:00 | `/backups/maisfibra/odoo-db` (disco local) |
| `odoo-web-data` | odoo | diário 02:00 | `/backups/maisfibra/odoo-filestore` (disco local) |

### 1.2 Levar o backup para fora do servidor (obrigatório pelo contrato)
1. Criar um bucket (Backblaze B2, AWS S3, Wasabi ou similar) com **retenção de 30 dias** (lifecycle rule).
2. Easypanel > **Configurações** > Provedores de armazenamento > adicionar o bucket.
3. Easypanel > odoo-db > Armazenamento > Backups de Volume > **Editar** > trocar o provedor para o bucket.
   Repetir em odoo.
4. Rodar **Execução Manual** e conferir o arquivo no bucket.

### 1.3 Backup lógico consistente (pg_dump) — recomendado
Backup de volume com o Postgres rodando pode ficar inconsistente. Somar um dump diário:
1. Easypanel > odoo-db > **Console** (ou SSH no servidor).
2. Comando (manual, para validar):
   ```bash
   pg_dump -U "$POSTGRES_USER" -Fc maisfibra-crm > /var/lib/postgresql/data/dump_$(date +%F).dump
   ```
3. Para agendar: cron no servidor (`docker exec <container-odoo-db> pg_dump …`) enviando para o mesmo bucket,
   ou usar o serviço **Postgres** gerenciado do Easypanel numa futura migração (ele tem backup de banco nativo).

---

## 2. Teste de restauração (mensal)

Objetivo: provar que o backup funciona. **Nunca restaurar em cima da produção.**
1. Criar o ambiente de homologação (seção 3) ou um serviço Postgres temporário.
2. Restaurar o dump mais recente:
   ```bash
   createdb -U "$POSTGRES_USER" restore_teste
   pg_restore -U "$POSTGRES_USER" -d restore_teste --no-owner /caminho/dump_AAAA-MM-DD.dump
   ```
3. Conferir: `SELECT count(*) FROM crm_lead; SELECT count(*) FROM res_partner;` — números próximos da produção.
4. Registrar no quadro abaixo e apagar o banco de teste.

| Data | Backup usado | Leads | Contatos | Tempo | Responsável | OK? |
|---|---|---|---|---|---|---|
| | | | | | | |

---

## 3. Ambiente de homologação

Mesmo código, banco **copiado** da produção, URLs diferentes. Usar para testar módulos novos antes de produção.

1. Easypanel > projeto `maisfibra` > **+ Serviço** > App `odoo-db-homolog`:
   imagem `postgres:13`, mesmas variáveis do `odoo-db` (com senha **diferente**), volume `odoo-db-homolog-data`
   em `/var/lib/postgresql/data/pgdata`.
2. Restaurar o último dump da produção nele (seção 2, passo 2, com o nome `maisfibra-crm`).
3. **+ Serviço** > App `odoo-homolog`: fonte Git igual ao `odoo` (repo, branch, `/odoo-addons`),
   variáveis apontando para `odoo-db-homolog`, volumes próprios (`odoo-homolog-web-data` em `/var/lib/odoo`).
4. Domínio: `maisfibra-odoo-homolog…` (HTTPS pelo Easypanel).
5. **Antes de usar**, neutralizar a homologação para não falar com o mundo real:
   - Definições > MaisFibra: URL do bridge **vazia** (ou um bridge de homologação apontando para um IXC de teste);
   - Definições > WhatsApp: desligar automações e chatbot, provedor sem credenciais;
   - Configurações > Técnico > Servidores de e-mail de saída: nenhum;
   - Desativar as ações agendadas de envio.
6. Fluxo de trabalho: branch → deploy em homologação → testes (`docs/PLANO_DE_TESTES.md`) → merge → deploy em produção.

Ambiente de desenvolvimento: local, com `docker compose` (Odoo 19 + Postgres) montando `odoo-addons/`.

---

## 4. Acesso administrativo

| Item | Como |
|---|---|
| Painel Easypanel | Somente pelo domínio com HTTPS; acesso direto pelo IP restrito a IPs da equipe ou VPN |
| Tokens de deploy | Após bloquear a porta, usar "Atualizar Token de Implantação" em cada serviço |
| VPN | WireGuard ou Tailscale no servidor; SSH e painel só pela VPN |
| 2FA no Odoo | Cada administrador/gerente: avatar > Meu perfil > Segurança da conta > **Ativar autenticação em dois fatores** |
| Política de senha | Apps > instalar *Password Policy* (`auth_password_policy`) > Definições: mínimo 12 caracteres |
| Revisão de acessos | Trimestral: Configurações > Usuários — remover quem saiu, conferir perfis (ver política LGPD) |
| Senha mestra do Odoo | Forte e guardada no cofre de senhas da empresa; o gerenciador web fica desligado (`--no-database-list`) |

---

## 5. Deploy

| O quê | Como |
|---|---|
| Bridge | Merge na `main` → Easypanel faz deploy (fonte Git) |
| Módulos Odoo | Easypanel > odoo > **Implantar** → Odoo: Apps > módulo > **Atualizar** (ou instalar, se novo) |
| Antes de qualquer deploy | Execução manual do backup (odoo-db) |
| Se o deploy do Odoo quebrar | Easypanel > odoo > Fonte: voltar para Imagem Docker `odoo:19.0` (os dados ficam nos volumes) |
| Depois do deploy | Conferir logs do serviço e o canal "Integração IXC" no Odoo |

---

## 6. Monitoramento

| O quê | Onde |
|---|---|
| Falhas da integração | Odoo > Discuss > canal **Integração IXC** (alertas automáticos) |
| Saúde do bridge | `GET /health` (estado dos alertas) |
| Fila de WhatsApp | CRM > WhatsApp > Mensagens (filtro Status = Erro) |
| E-mails | Configurações > Técnico > E-mails (falhas) |
| Auditoria | Configurações > Auditoria > Registro de alterações |
| Disponibilidade (recomendado) | Monitor externo (UptimeRobot, BetterStack) no domínio do Odoo e no `/health` do bridge |
