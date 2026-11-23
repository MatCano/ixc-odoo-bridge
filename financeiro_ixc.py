"""Operações financeiras e fiscais no IXC Provedor + Motor FEBRABAN/Logs.

Suporta:
- Listagem de contratos ativos (cliente_contrato)
- Listagem, emissão idempotente, download de PDF e cancelamento de boletos (fn_areceber / get_boleto)
- Consulta e emissão de documentos fiscais (vd_saida / NFCom / NFS-e) sem jamais marcar
  como autorizado um documento sem retorno de protocolo/chave válido
- Cálculo matemático FEBRABAN de código de barras (44d) e linha digitável (47d)
- Sanitização de payloads/respostas para logs sem exposição de dados sensíveis
"""
import base64
import logging
import re
from datetime import date, datetime

import requests

import config
import ixc

logger = logging.getLogger("IXC-Odoo-Bridge.financeiro")

CHAVES_SENSIVEIS = {
    "token", "bridge_token", "webhook_secret", "password", "senha", "pass",
    "secret", "api_key", "apikey", "authorization", "cookie", "session",
    "pdf_base64", "xml_base64", "boleto_pdf", "xml_conteudo", "datas",
}

STATUS_MAP_IXC = {
    "A": "registrada",
    "R": "paga",
    "P": "registrada",
    "C": "cancelada",
}


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _float(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def sanitizar_payload(obj, _depth=0):
    """Redata segredos e blobs binários/base64 antes de registrar em log."""
    if _depth > 6:
        return "[...]"
    if isinstance(obj, dict):
        limpo = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in CHAVES_SENSIVEIS or any(s in kl for s in ("token", "secret", "password", "senha", "base64", "apikey")):
                if isinstance(v, str) and len(v) > 8:
                    limpo[k] = f"***REDACTED(len={len(v)})***"
                elif v:
                    limpo[k] = "***REDACTED***"
                else:
                    limpo[k] = ""
            else:
                limpo[k] = sanitizar_payload(v, _depth + 1)
        return limpo
    if isinstance(obj, list):
        return [sanitizar_payload(item, _depth + 1) for item in obj[:50]]
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    if isinstance(obj, str) and len(obj) > 500:
        return obj[:200] + f"...[truncated len={len(obj)}]"
    return obj


# ======================================================================
# Motor FEBRABAN (Fator de Vencimento, Mod 10, Mod 11, Código Barras / Linha)
# ======================================================================
def calcular_fator_vencimento(data_iso):
    """Calcula fator de vencimento FEBRABAN (4 dígitos), incluindo reset em 22/02/2025."""
    if isinstance(data_iso, datetime):
        dt = data_iso.date()
    elif isinstance(data_iso, date):
        dt = data_iso
    else:
        dt = date.fromisoformat(str(data_iso)[:10])

    data_reset = date(2025, 2, 22)
    if dt < data_reset:
        base = date(1997, 10, 7)
        dias = max(0, (dt - base).days)
        return f"{dias % 10000:04d}"
    dias_pos_reset = (dt - data_reset).days
    fator = 1000 + (dias_pos_reset % 9000)
    return f"{fator:04d}"


def modulo10(num_str):
    soma = 0
    peso = 2
    for ch in reversed(_digits(num_str)):
        prod = int(ch) * peso
        if prod >= 10:
            prod = (prod // 10) + (prod % 10)
        soma += prod
        peso = 1 if peso == 2 else 2
    resto = soma % 10
    return 0 if resto == 0 else 10 - resto


def modulo11_boleto(num_str):
    soma = 0
    peso = 2
    for ch in reversed(_digits(num_str)):
        soma += int(ch) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    dv = 11 - resto
    if dv in (0, 10, 11):
        return 1
    return dv


def gerar_codigo_barras_e_linha_digitavel(
    banco, vencimento, valor, nosso_numero, agencia="0001", conta="0000001", carteira="109"
):
    """Gera código de barras FEBRABAN (44 dígitos) e linha digitável (47 dígitos formatada)."""
    banco_str = _digits(banco).zfill(3)[:3] or "756"
    moeda = "9"
    fator = calcular_fator_vencimento(vencimento)
    valor_centavos = f"{max(0, int(round(_float(valor) * 100))):010d}"[-10:]

    ag_str = _digits(agencia).zfill(4)[-4:]
    cart_str = _digits(carteira).zfill(3)[-3:]
    nn_str = _digits(nosso_numero).zfill(11)[-11:]
    ct_str = _digits(conta).zfill(7)[-7:]
    campo_livre = f"{ag_str}{cart_str}{nn_str}{ct_str}"  # 25 dígitos

    sem_dv = f"{banco_str}{moeda}{fator}{valor_centavos}{campo_livre}"  # 43 dígitos
    dv_geral = str(modulo11_boleto(sem_dv))
    codigo_barras = f"{banco_str}{moeda}{dv_geral}{fator}{valor_centavos}{campo_livre}"

    bloco1 = f"{banco_str}{moeda}{campo_livre[0:5]}"
    dv1 = str(modulo10(bloco1))
    bloco2 = campo_livre[5:15]
    dv2 = str(modulo10(bloco2))
    bloco3 = campo_livre[15:25]
    dv3 = str(modulo10(bloco3))

    linha_digitavel = (
        f"{bloco1[:5]}.{bloco1[5:]}{dv1} "
        f"{bloco2[:5]}.{bloco2[5:]}{dv2} "
        f"{bloco3[:5]}.{bloco3[5:]}{dv3} "
        f"{dv_geral} {fator}{valor_centavos}"
    )
    return {
        "codigo_barras": codigo_barras,
        "linha_digitavel": linha_digitavel,
        "nosso_numero": nn_str,
        "fator_vencimento": fator,
    }


def validar_linha_digitavel(linha):
    """Valida DVs Mod10 (campos 1-3) e DV Mod11 geral de uma linha digitável de 47 dígitos."""
    dig = _digits(linha)
    if len(dig) != 47:
        return False
    c1, dv1 = dig[0:9], dig[9]
    c2, dv2 = dig[10:20], dig[20]
    c3, dv3 = dig[21:31], dig[31]
    dv_geral = dig[32]
    fator_valor = dig[33:47]
    if str(modulo10(c1)) != dv1 or str(modulo10(c2)) != dv2 or str(modulo10(c3)) != dv3:
        return False
    campo_livre = c1[4:9] + c2 + c3
    sem_dv = c1[0:4] + fator_valor + campo_livre
    return str(modulo11_boleto(sem_dv)) == dv_geral


# ======================================================================
# Contratos, Boletos e Notas Fiscais via API IXC
# ======================================================================
def listar_contratos_ativos(ixc_cliente_id="", pagina=1, rp=100):
    if ixc_cliente_id:
        regs = ixc.listar(config.IXC_TB_CONTRATO, "id_cliente", str(ixc_cliente_id), oper="=", pagina=pagina, rp=rp)
    else:
        regs = ixc.listar(config.IXC_TB_CONTRATO, "status", "A", oper="=", pagina=pagina, rp=rp)

    saida = []
    for c in regs:
        if c.get("status") != "A":
            continue
        saida.append({
            "ixc_contrato_id": str(c.get("id") or ""),
            "ixc_cliente_id": str(c.get("id_cliente") or ""),
            "nome": c.get("contrato") or c.get("descricao_aux_plano_venda") or f"Contrato {c.get('id')}",
            "status": c.get("status") or "A",
            "status_internet": c.get("status_internet") or "",
            "valor": _float(c.get("valor_contrato") or c.get("valor") or 0),
            "dia_vencimento": int(_digits(c.get("dia_vencimento")) or 10),
            "data_ativacao": (c.get("data_ativacao") or "")[:10],
        })
    return saida


def _mapear_status_cobranca(reg, hoje_iso=None):
    hoje = hoje_iso or date.today().isoformat()
    raw = str(reg.get("status") or "A").upper()
    if raw == "R":
        return "paga"
    if raw == "C":
        return "cancelada"
    venc = str(reg.get("data_vencimento") or "")[:10]
    if raw in ("A", "P") and venc and venc < hoje:
        return "vencida"
    return STATUS_MAP_IXC.get(raw, "registrada")


def _extrair_competencia(reg):
    obs = str(reg.get("obs") or "")
    m = re.search(r"(\d{4}-\d{2})", obs)
    if m:
        return m.group(1)
    venc = str(reg.get("data_vencimento") or "")[:7]
    return venc if re.match(r"^\d{4}-\d{2}$", venc) else date.today().strftime("%Y-%m")


def listar_cobrancas_ixc(ixc_cliente_id="", data_inicio="", status="", rp=100):
    if ixc_cliente_id:
        regs = ixc.listar(config.IXC_TB_ARECEBER, "id_cliente", str(ixc_cliente_id), oper="=", rp=rp)
    elif data_inicio:
        regs = ixc.listar(config.IXC_TB_ARECEBER, "data_vencimento", str(data_inicio)[:10], oper=">=", rp=rp)
    else:
        regs = ixc.listar(config.IXC_TB_ARECEBER, "id", "0", oper=">", rp=rp)

    hoje = date.today().isoformat()
    out = []
    for f in regs:
        st_canon = _mapear_status_cobranca(f, hoje_iso=hoje)
        if status and st_canon != status:
            continue
        out.append({
            "identificador_externo": str(f.get("id") or ""),
            "ixc_cliente_id": str(f.get("id_cliente") or ""),
            "ixc_contrato_id": str(f.get("id_contrato") or ""),
            "competencia": _extrair_competencia(f),
            "data_vencimento": str(f.get("data_vencimento") or "")[:10],
            "valor": _float(f.get("valor")),
            "valor_aberto": _float(f.get("valor_aberto")) if st_canon not in ("paga", "cancelada") else 0.0,
            "valor_pago": _float(f.get("valor_recebido") or (f.get("valor") if st_canon == "paga" else 0.0)),
            "data_pagamento": str(f.get("data_pagamento") or f.get("baixa_data") or "")[:10],
            "status": st_canon,
            "linha_digitavel": f.get("linha_digitavel") or "",
            "codigo_barras": f.get("codigo_barras") or "",
            "nosso_numero": f.get("nn_boleto") or f.get("nosso_numero") or "",
            "pix_copia_cola": f.get("pix_txid") or f.get("pix_copia_cola") or "",
            "link_pagamento": f.get("url_boleto") or "",
        })
    return out


def obter_pdf_boleto_ixc(ixc_fatura_id):
    """Consulta PDF do boleto em base64 na API do IXC."""
    if not ixc_fatura_id:
        raise ixc.IXCError("Informe o identificador externo do boleto no IXC.")
    url = f"{config.IXC_HOST}/webservice/v1/{config.IXC_BOLETO_ENDPOINT}"
    payload = {
        "boletos": str(ixc_fatura_id),
        "juro": "N",
        "multa": "N",
        "atualiza_boleto": "N",
        "tipo_boleto": "arquivo",
        "base64": "S",
    }
    resp = requests.post(
        url,
        json=payload,
        auth=(config.IXC_USER, config.IXC_PASS),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ixc.IXCError(f"get_boleto: HTTP {resp.status_code} {resp.text[:200]}")

    pdf_b64 = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            if str(data.get("type") or "").lower() == "error":
                raise ixc.IXCError(f"get_boleto: {data.get('message') or data}")
            pdf_b64 = data.get("base64") or data.get("pdf") or ""
    except ValueError:
        raw_text = (resp.text or "").strip()
        if raw_text.startswith("%PDF-"):
            pdf_b64 = base64.b64encode(resp.content).decode()
        else:
            # Pode ser string base64 pura
            try:
                decoded = base64.b64decode(raw_text, validate=True)
                if decoded.startswith(b"%PDF-"):
                    pdf_b64 = raw_text
            except Exception:
                pdf_b64 = raw_text

    return {"identificador_externo": str(ixc_fatura_id), "pdf_base64": pdf_b64}


def emitir_boleto_ixc(payload):
    """Emite título no IXC de forma idempotente por (id_cliente, id_contrato, competencia)."""
    cliente_id = str(payload.get("id_cliente") or "").strip()
    contrato_id = str(payload.get("id_contrato") or "").strip()
    competencia = str(payload.get("competencia") or "")[:7]
    vencimento = str(payload.get("data_vencimento") or "")[:10]
    valor = _float(payload.get("valor"))
    if not cliente_id or not vencimento or valor <= 0:
        raise ixc.IXCError("Dados insuficientes para emitir boleto no IXC (cliente, vencimento e valor > 0).")

    # Verificação estrita de idempotência antes de inserir
    existentes = ixc.listar(config.IXC_TB_ARECEBER, "id_cliente", cliente_id, oper="=", rp=50)
    for reg in existentes:
        if str(reg.get("status") or "").upper() == "C":
            continue
        reg_contrato = str(reg.get("id_contrato") or "")
        reg_comp = _extrair_competencia(reg)
        if (not contrato_id or reg_contrato == contrato_id) and competencia and reg_comp == competencia:
            pdf_b64 = ""
            try:
                pdf_b64 = obter_pdf_boleto_ixc(reg["id"]).get("pdf_base64", "")
            except Exception:
                pass
            return {
                "duplicidade_evitada": True,
                "identificador_externo": str(reg["id"]),
                "status": _mapear_status_cobranca(reg),
                "linha_digitavel": reg.get("linha_digitavel") or "",
                "codigo_barras": reg.get("codigo_barras") or "",
                "nosso_numero": reg.get("nn_boleto") or reg.get("nosso_numero") or str(reg["id"]),
                "link_pagamento": reg.get("url_boleto") or "",
                "pdf_base64": pdf_b64,
            }

    reg_novo = {
        "id_cliente": cliente_id,
        "id_contrato": contrato_id or "0",
        "data_vencimento": vencimento,
        "valor": f"{valor:.2f}",
        "valor_aberto": f"{valor:.2f}",
        "status": "A",
        "obs": f"{payload.get('descricao') or 'Mensalidade'} | Comp: {competencia or vencimento[:7]}",
    }
    rid = ixc.inserir(config.IXC_TB_ARECEBER, reg_novo)
    criado = ixc.buscar(config.IXC_TB_ARECEBER, rid) or reg_novo
    linha = criado.get("linha_digitavel") or ""
    barra = criado.get("codigo_barras") or ""
    nn = criado.get("nn_boleto") or criado.get("nosso_numero") or rid

    if not linha or not barra:
        febraban = gerar_codigo_barras_e_linha_digitavel(
            banco=payload.get("banco_codigo") or "756",
            vencimento=vencimento,
            valor=valor,
            nosso_numero=nn,
        )
        linha = linha or febraban["linha_digitavel"]
        barra = barra or febraban["codigo_barras"]
        nn = nn or febraban["nosso_numero"]

    pdf_b64 = ""
    try:
        pdf_b64 = obter_pdf_boleto_ixc(rid).get("pdf_base64", "")
    except Exception:
        pass

    return {
        "duplicidade_evitada": False,
        "identificador_externo": str(rid),
        "status": "registrada",
        "linha_digitavel": linha,
        "codigo_barras": barra,
        "nosso_numero": str(nn),
        "link_pagamento": criado.get("url_boleto") or "",
        "pdf_base64": pdf_b64,
    }


def cancelar_boleto_ixc(ixc_fatura_id, motivo=""):
    atual = ixc.buscar(config.IXC_TB_ARECEBER, str(ixc_fatura_id))
    if not atual:
        raise ixc.IXCError(f"Título {ixc_fatura_id} não encontrado no IXC.")
    if str(atual.get("status") or "").upper() == "R":
        raise ixc.IXCError("Não é possível cancelar um boleto já pago/recebido no IXC.")
    obs_atual = (atual.get("obs") or "").strip()
    nova_obs = f"{obs_atual} | Cancelado Odoo: {motivo}".strip(" |") if motivo else obs_atual
    ixc.alterar(config.IXC_TB_ARECEBER, str(ixc_fatura_id), {"status": "C", "valor_aberto": "0.00", "obs": nova_obs})
    return {"identificador_externo": str(ixc_fatura_id), "status": "cancelada"}


def _mapear_status_nf(reg):
    raw = str(reg.get("status") or "").upper()
    tem_autorizacao = bool(reg.get("protocolo") and reg.get("chave_nf"))
    if raw == "C":
        return "cancelada"
    if raw in ("R", "E") or (raw == "A" and not tem_autorizacao):
        return "rejeitada"
    if raw == "A" and tem_autorizacao:
        return "autorizada"
    return "em_processamento" if not reg.get("motivo_rejeicao") else "rejeitada"


def consultar_notas_fiscais_ixc(ixc_cliente_id="", ixc_fatura_id="", rp=100):
    if ixc_fatura_id:
        regs = ixc.listar(config.IXC_TB_NF, "id_areceber", str(ixc_fatura_id), oper="=", rp=rp)
    elif ixc_cliente_id:
        regs = ixc.listar(config.IXC_TB_NF, "id_cliente", str(ixc_cliente_id), oper="=", rp=rp)
    else:
        regs = ixc.listar(config.IXC_TB_NF, "id", "0", oper=">", rp=rp)

    saida = []
    for nf in regs:
        st = _mapear_status_nf(nf)
        saida.append({
            "identificador_externo": str(nf.get("id") or ""),
            "ixc_cliente_id": str(nf.get("id_cliente") or ""),
            "ixc_fatura_id": str(nf.get("id_areceber") or ""),
            "numero": str(nf.get("numero_nf") or ""),
            "serie": str(nf.get("serie") or "1"),
            "chave_acesso": str(nf.get("chave_nf") or ""),
            "protocolo": str(nf.get("protocolo") or ""),
            "modelo": str(nf.get("modelo") or "62"),
            "valor_total": _float(nf.get("valor_total")),
            "data_emissao": str(nf.get("data_emissao") or "")[:10],
            "status": st,
            "xml_base64": nf.get("xml_base64") or "",
            "pdf_base64": nf.get("pdf_base64") or "",
        })
    return saida


def emitir_nota_fiscal_ixc(payload):
    """Solicita emissão de documento fiscal no IXC. Nunca marca autorizada sem protocolo+chave."""
    cliente_id = str(payload.get("id_cliente") or "").strip()
    if not cliente_id:
        return {
            "status": "rejeitada",
            "motivo_falha": "ID do cliente no IXC não informado para emissão fiscal.",
        }

    reg_nf = {
        "id_cliente": cliente_id,
        "id_areceber": str(payload.get("id_areceber") or ""),
        "modelo": "62" if payload.get("tipo_documento") == "nfcom" else "NFSE",
        "valor_total": f"{_float(payload.get('valor')):.2f}",
        "data_emissao": date.today().isoformat(),
    }
    try:
        rid = ixc.inserir(config.IXC_TB_NF, reg_nf)
        criado = ixc.buscar(config.IXC_TB_NF, rid) or {}
    except ixc.IXCError as e:
        return {
            "status": "rejeitada",
            "motivo_falha": str(e),
        }
    except Exception as e:
        return {
            "status": "indisponivel",
            "motivo_falha": f"Serviço fiscal indisponível: {e}",
        }

    protocolo = str(criado.get("protocolo") or "").strip()
    chave = str(criado.get("chave_nf") or "").strip()
    if protocolo and chave:
        return {
            "identificador_externo": str(rid),
            "status": "autorizada",
            "numero": str(criado.get("numero_nf") or rid),
            "serie": str(criado.get("serie") or "1"),
            "chave_acesso": chave,
            "protocolo": protocolo,
            "xml_base64": criado.get("xml_base64") or "",
            "pdf_base64": criado.get("pdf_base64") or "",
        }
    return {
        "identificador_externo": str(rid),
        "status": "rejeitada",
        "motivo_falha": criado.get("motivo_rejeicao") or "Autorizador fiscal não retornou protocolo/chave válida.",
    }
