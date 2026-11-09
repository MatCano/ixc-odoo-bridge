"""Teste do fluxo do bridge com IXC e Odoo falsos (sem rede)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SYNC_STATE_PATH"] = os.path.join(tempfile.mkdtemp(), "s.json")

import ixc  # noqa
import odoo as odoo_mod  # noqa

# ---------- IXC falso ----------
DB = {"cliente": {}, "contato": {"7": {"id": "7", "nome": "Ana", "id_cliente": "0"}},
      "cidade": {"3": {"id": "3", "nome": "São Paulo"}}, "crm_negociacoes": {}}
seq = [100]


def f_listar(tabela, campo, valor, oper="=", pagina=1, rp=None, **k):
    regs = list(DB.get(tabela, {}).values())
    if oper == "=":
        regs = [r for r in regs if str(r.get(campo)) == str(valor)]
    elif oper == ">=":
        regs = [r for r in regs if str(r.get(campo, "")) >= str(valor)]
    return regs if pagina == 1 else []


def f_inserir(tabela, reg):
    seq[0] += 1
    rid = str(seq[0])
    DB.setdefault(tabela, {})[rid] = dict(reg, id=rid)
    return rid


def f_alterar(tabela, rid, mud):
    DB[tabela][rid].update(mud)
    return rid


ixc.listar, ixc.inserir, ixc.alterar = f_listar, f_inserir, f_alterar
ixc.buscar = lambda t, i: DB.get(t, {}).get(str(i))

# ---------- Odoo falso ----------
class FakeOdoo:
    def __init__(self):
        self.recs = {"crm.lead": {}, "res.partner": {}, "crm.stage": {
            10: {"mf_code": "aguardando_assinatura", "team_id": False, "sequence": 1},
            11: {"mf_code": "perdemos", "team_id": False, "sequence": 2}}}
        self.n = 1
        self.posts = []

    def _match(self, rec, domain):
        conds = [d for d in domain if isinstance(d, list)]
        ors = "|" in domain
        res = [self._cond(rec, c) for c in conds]
        return any(res) if ors else all(res)

    def _cond(self, rec, c):
        f, op, v = c
        val = rec.get(f)
        if op == "=":
            return val == v
        if op == "ilike":
            return v in (val or "")
        if op == "in":
            return val in v
        return False

    def search(self, model, domain, limit=0, order=None):
        ids = [i for i, r in self.recs.get(model, {}).items() if self._match(r, domain)]
        return ids[:limit] if limit else ids

    def read(self, model, ids, fields):
        out = []
        for i in ids:
            r = self.recs[model][i]
            row = {f: r.get(f, False) for f in fields}
            for f in ("stage_id", "team_id"):
                if f in fields and r.get(f):
                    row[f] = [r[f], "x"]
            out.append(row)
        return out

    def create(self, model, vals):
        self.n += 1
        self.recs.setdefault(model, {})[self.n] = dict(vals)
        return self.n

    def write(self, model, ids, vals):
        for i in ids:
            self.recs[model][i].update(vals)

    def call(self, model, method, args, kw=None):
        self.posts.append((model, method, args, kw))

    def stage_por_codigo(self, codigo, team_id=None):
        return next(i for i, s in self.recs["crm.stage"].items() if s["mf_code"] == codigo)


FAKE = FakeOdoo()
odoo_mod.conexao = lambda: FAKE

import ixc_para_odoo as sync  # noqa
import odoo_para_ixc as fluxo  # noqa

sync.conexao = lambda: FAKE
FALHAS = []


def ok(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        FALHAS.append(msg)

# 1) Lead do IXC chega no Odoo com ids e endereço
assert sync.processar_contato({"id": "7", "nome": "Ana", "cnpj_cpf": "123.456.789-01",
                                "fone_celular": "11999998888", "email": "ana@x.com",
                                "endereco": "Rua A", "cidade": "3", "bloco": "B", "apartamento": "12",
                                "data_cadastro": "2026-11-08 10:00:00", "id_cliente": "0"})
lead_id = FAKE.search("crm.lead", [["mf_ixc_contato_id", "=", "7"]])[0]
lead = FAKE.recs["crm.lead"][lead_id]
ok(lead["mf_cpf_cnpj"] == "12345678901" and lead["mf_cidade"] == "São Paulo" and lead["mf_bloco"] == "B",
   "contato IXC -> lead Odoo com CPF, cidade e bloco")
partner = FAKE.recs["res.partner"][lead["partner_id"]]
ok("mf_ixc_cliente_id" not in partner, "bug antigo corrigido: lead não recebe ID de cliente")

# 2) Reprocessar não duplica e não pisa em edição do vendedor
lead["mf_bloco"] = "C (editado)"
sync.processar_contato({"id": "7", "nome": "Ana", "cnpj_cpf": "12345678901", "email": "ana@x.com",
                        "bloco": "B", "data_cadastro": "2026-11-08 10:00:00", "id_cliente": "0"})
ok(len(FAKE.recs["crm.lead"]) == 1 and lead["mf_bloco"] == "C (editado)", "sem duplicar e sem sobrescrever edição")

# 3) Odoo -> prospect no IXC
payload = {"odoo_lead_id": lead_id, "nome": "Ana", "cpf_cnpj": "12345678901", "email": "ana@x.com",
           "telefone": "11999998888", "ixc_contato_id": "7", "cidade": "São Paulo", "cep": "01000000",
           "endereco": "Rua A", "bloco": "C", "apartamento": "12", "condominio_ixc_id": "5",
           "origem": "Instagram"}
cli_id = fluxo.virar_prospect(payload)
cli = DB["cliente"][cli_id]
ok(cli["status_prospeccao"] == "P" and cli["crm"] == "S" and cli["cidade"] == "3"
   and cli["id_condominio"] == "5" and cli["filial_id"] == "1", "prospect criado no IXC com defaults + cidade por nome")
ok(DB["contato"]["7"]["id_cliente"] == cli_id, "contato do IXC vinculado ao prospect")
ok("Origem: Instagram" in cli["obs"], "origem registrada no IXC")

# 4) Sem e-mail é recusado
try:
    fluxo.virar_prospect(dict(payload, email=""))
    ok(False, "prospect sem e-mail recusado")
except ixc.IXCError:
    ok(True, "prospect sem e-mail recusado")

# 5) Segunda vez reaproveita o mesmo cliente (por CPF)
ok(fluxo.virar_prospect(payload) == cli_id and len(DB["cliente"]) == 1, "não duplica cliente no IXC")

# 6) Negociação
neg_id = fluxo.criar_negociacao(dict(payload, ixc_cliente_id=cli_id, plano_ixc_id="9",
                                     descricao_produto="500 Mega", plano="Plano 500"))
ok(DB["crm_negociacoes"][neg_id]["id_cliente"] == cli_id and DB["cliente"][cli_id].get("crm_data_negociando"),
   "negociação criada e prospect marcado como negociando")

# 7) Ganho no IXC move o lead
lead["mf_ixc_cliente_id"] = cli_id
sync.processar_status(dict(DB["cliente"][cli_id], crm_data_vencemos="2026-11-09"))
ok(lead.get("stage_id") == 10, "vencemos no IXC -> Aguardar Assinatura no Odoo")
sync.processar_status(dict(DB["cliente"][cli_id], crm_data_perdemos="2026-11-09"))
ok(lead.get("stage_id") == 11, "perdemos no IXC -> Perdemos no Odoo")

# 8) Lead externo (OPA/Chatwoot) sem CPF
r = fluxo.lead_externo({"nome": "Bruno", "telefone": "11988887777", "origem": "Google"})
novo = FAKE.recs["crm.lead"][r["odoo_lead_id"]]
ok(novo["mf_origem"] == "Google" and r["ixc_contato_id"] and r["odoo_lead_id"] != lead_id,
   "lead externo clonado no IXC e no Odoo, sem colidir com lead existente")

# 9) Consulta financeiro/serviço
import consulta_ixc  # noqa
DB["cliente_contrato"] = {"1": {"id": "1", "id_cliente": cli_id, "contrato": "500 Mega", "status": "A", "status_internet": "FA"}}
DB["fn_areceber"] = {
    "1": {"id": "1", "id_cliente": cli_id, "data_vencimento": "2026-10-10", "valor": "99.90", "valor_aberto": "99.90", "status": "A"},
    "2": {"id": "2", "id_cliente": cli_id, "data_vencimento": "2099-01-10", "valor": "99.90", "valor_aberto": "99.90", "status": "A"},
    "3": {"id": "3", "id_cliente": cli_id, "data_vencimento": "2026-09-10", "valor": "99.90", "valor_aberto": "0", "status": "R"},
}
r = consulta_ixc.consultar(cpf_cnpj="123.456.789-01")["resumo"]
ok(r["status_servico"] == "Financeiro em atraso" and r["faturas_abertas"] == 2 and r["faturas_vencidas"] == 1
   and r["valor_vencido"] == 99.9 and r["valor_aberto"] == 199.8, "consulta IXC: status do serviço e valores abertos/vencidos")


if FALHAS:
    raise SystemExit(f"{len(FALHAS)} teste(s) falharam")

# 10) Alertas: falha avisa uma vez no canal, normalização avisa de novo
import alertas  # noqa
alertas.conexao = lambda: FAKE
FAKE.posts.clear()
alertas.falha("sincronismo lead", "IXC fora do ar")
alertas.falha("sincronismo lead", "IXC fora do ar")   # repetida: não posta de novo
alertas.ok("sincronismo lead")
posts = [p for p in FAKE.posts if p[1] == "message_post"]
canal = FAKE.search("discuss.channel", [["name", "=", "Integração IXC"]])
ok(len(posts) == 2 and "⚠️" in posts[0][3]["body"] and "✅" in posts[1][3]["body"] and len(canal) == 1,
   "alertas: falha avisada uma vez e normalização avisada no canal Integração IXC")
FAKE.posts.clear()
_orig = ixc.listar
ixc.listar = lambda *a, **k: (_ for _ in ()).throw(ixc.IXCError("timeout"))
sync.sync_leads()
ixc.listar = _orig
ok(any("não consegui ler o IXC" in (p[3] or {}).get("body", "") for p in FAKE.posts if p[1] == "message_post"),
   "alertas: falha ao ler o IXC no ciclo gera alerta")

# 11) Negociação: reaproveita a aberta (status N) e usa os padrões validados
neg2 = fluxo.criar_negociacao(dict(payload, ixc_cliente_id=cli_id, plano_ixc_id="186", canal_venda_ixc_id="2",
                                   descricao_produto="600 Mega", plano="600 Mega"))
n = DB["crm_negociacoes"][neg2]
ok(neg2 == neg_id and len(DB["crm_negociacoes"]) == 1 and n["status"] == "N" and n["id_funil"] == "7"
   and n["id_plano"] == "186" and n["id_canal_origem"] == "2" and n["id_contato"] == "7",
   "negociação: reaproveita a aberta do cliente, com funil, plano e canal validados")

# 12) Status V/D da negociação move o lead
lead["stage_id"] = None
FAKE.recs["crm.lead"][lead_id]["mf_ixc_negociacao_id"] = neg2
sync.processar_status_negociacao({"id": neg2, "id_cliente": cli_id, "status": "V"})
ok(lead.get("stage_id") == 10, "negociação status V -> Aguardar Assinatura")
sync.processar_status_negociacao({"id": neg2, "id_cliente": cli_id, "status": "D"})
ok(lead.get("stage_id") == 11, "negociação status D -> Perdemos")

# 13) Vendedor e funil do Odoo viram IDs no IXC
DB["cliente"].clear(); DB["crm_negociacoes"].clear()
cid = fluxo.virar_prospect(dict(payload, vendedor_ixc_id="12"))
nid = fluxo.criar_negociacao(dict(payload, ixc_cliente_id=cid, funil_ixc_id="5", plano="X", descricao_produto="Y"))
ok(DB["cliente"][cid].get("id_vendedor") == "12" and DB["crm_negociacoes"][nid]["id_funil"] == "5",
   "vendedor -> id_vendedor no prospect; funil do time -> id_funil na negociação")
