"""Motor puro de cálculo FEBRABAN, geração de PDF (Boleto / DANFE / DANFSe),
Pix EMV, XML fiscal e sanitização/validação de documentos brasileiros.
"""
import io
import re
from datetime import date, datetime

from lxml import etree
import qrcode
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

CHAVES_SENSIVEIS = {
    "token", "bridge_token", "webhook_secret", "password", "senha", "pass",
    "secret", "api_key", "apikey", "authorization", "cookie", "session",
    "pdf_base64", "xml_base64", "boleto_pdf", "xml_conteudo", "datas",
}


def only_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def validar_cpf_cnpj(doc):
    """Valida CPF (11 dígitos) ou CNPJ (14 dígitos) pelos dígitos verificadores."""
    d = only_digits(doc)
    if len(d) == 11:
        if d == d[0] * 11:
            return False
        s1 = sum(int(d[i]) * (10 - i) for i in range(9))
        d1 = (s1 * 10 % 11) % 10
        s2 = sum(int(d[i]) * (11 - i) for i in range(10))
        d2 = (s2 * 10 % 11) % 10
        return d[-2:] == f"{d1}{d2}"
    if len(d) == 14:
        if d == d[0] * 14:
            return False
        w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        w2 = [6] + w1
        r1 = sum(int(d[i]) * w1[i] for i in range(12)) % 11
        d1 = 0 if r1 < 2 else 11 - r1
        r2 = sum(int(d[i]) * w2[i] for i in range(13)) % 11
        d2 = 0 if r2 < 2 else 11 - r2
        return d[-2:] == f"{d1}{d2}"
    return False


def sanitizar_dict(obj, _depth=0):
    """Remove segredos e blobs base64/binários para gravação segura em log."""
    if _depth > 6:
        return "[...]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in CHAVES_SENSIVEIS or any(s in kl for s in ("token", "secret", "password", "senha", "base64", "apikey")):
                out[k] = f"***REDACTED(len={len(v)})***" if isinstance(v, str) and len(v) > 8 else ("***REDACTED***" if v else "")
            else:
                out[k] = sanitizar_dict(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [sanitizar_dict(i, _depth + 1) for i in obj[:50]]
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    if isinstance(obj, str) and len(obj) > 500:
        return obj[:200] + f"...[truncated len={len(obj)}]"
    return obj


# ======================================================================
# Motor FEBRABAN (Mod 10, Mod 11, Fator Vencimento)
# ======================================================================
def calcular_fator_vencimento(data_iso):
    if isinstance(data_iso, datetime):
        dt = data_iso.date()
    elif isinstance(data_iso, date):
        dt = data_iso
    else:
        dt = date.fromisoformat(str(data_iso)[:10])

    data_reset = date(2025, 2, 22)
    if dt < data_reset:
        dias = max(0, (dt - date(1997, 10, 7)).days)
        return f"{dias % 10000:04d}"
    fator = 1000 + ((dt - data_reset).days % 9000)
    return f"{fator:04d}"


def modulo10(num_str):
    soma = 0
    peso = 2
    for ch in reversed(only_digits(num_str)):
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
    for ch in reversed(only_digits(num_str)):
        soma += int(ch) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - (soma % 11)
    return 1 if dv in (0, 10, 11) else dv


def gerar_dados_febraban(
    banco, vencimento, valor, nosso_numero, agencia="0001", conta="0000001", carteira="109"
):
    banco_str = only_digits(banco).zfill(3)[:3] or "756"
    moeda = "9"
    fator = calcular_fator_vencimento(vencimento)
    valor_centavos = f"{max(0, int(round(float(valor or 0) * 100))):010d}"[-10:]

    ag_str = only_digits(agencia).zfill(4)[-4:]
    cart_str = only_digits(carteira).zfill(3)[-3:]
    nn_str = only_digits(nosso_numero).zfill(11)[-11:]
    ct_str = only_digits(conta).zfill(7)[-7:]
    campo_livre = f"{ag_str}{cart_str}{nn_str}{ct_str}"

    sem_dv = f"{banco_str}{moeda}{fator}{valor_centavos}{campo_livre}"
    dv_geral = str(modulo11_boleto(sem_dv))
    codigo_barras = f"{banco_str}{moeda}{dv_geral}{fator}{valor_centavos}{campo_livre}"

    b1 = f"{banco_str}{moeda}{campo_livre[0:5]}"
    b2 = campo_livre[5:15]
    b3 = campo_livre[15:25]
    linha_digitavel = (
        f"{b1[:5]}.{b1[5:]}{modulo10(b1)} "
        f"{b2[:5]}.{b2[5:]}{modulo10(b2)} "
        f"{b3[:5]}.{b3[5:]}{modulo10(b3)} "
        f"{dv_geral} {fator}{valor_centavos}"
    )
    return {
        "codigo_barras": codigo_barras,
        "linha_digitavel": linha_digitavel,
        "nosso_numero": nn_str,
        "fator_vencimento": fator,
    }


def validar_linha_digitavel(linha):
    dig = only_digits(linha)
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
# Pix EMV QR Code Payload (CRC16-CCITT)
# ======================================================================
def _tlv(tag, val):
    return f"{tag}{len(val):02d}{val}"


def _crc16_ccitt(payload):
    crc = 0xFFFF
    for ch in payload.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else ((crc << 1) & 0xFFFF)
    return f"{crc:04X}"


def gerar_pix_emv_payload(chave_pix, nome_beneficiario, cidade, valor, txid="MAISFIBRA"):
    chave = (chave_pix or "00000000000100").strip()[:77]
    nome = re.sub(r"[^A-Za-z0-9 ]", "", (nome_beneficiario or "MAIS FIBRA").upper())[:25] or "MAIS FIBRA"
    cid = re.sub(r"[^A-Za-z0-9 ]", "", (cidade or "SAO PAULO").upper())[:15] or "SAO PAULO"
    tx = re.sub(r"[^A-Za-z0-9]", "", str(txid or "MAISFIBRA"))[:25] or "MAISFIBRA"

    mai = _tlv("00", "br.gov.bcb.pix") + _tlv("01", chave)
    body = (
        _tlv("00", "01")
        + _tlv("26", mai)
        + _tlv("52", "0000")
        + _tlv("53", "986")
        + (_tlv("54", f"{float(valor):.2f}") if float(valor or 0) > 0 else "")
        + _tlv("58", "BR")
        + _tlv("59", nome)
        + _tlv("60", cid)
        + _tlv("62", _tlv("05", tx))
        + "6304"
    )
    return body + _crc16_ccitt(body)


def _gerar_qr_image_reader(texto):
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=2)
    qr.add_data(texto)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


# ======================================================================
# Geração de PDF do Boleto (ReportLab)
# ======================================================================
def gerar_pdf_boleto(dados):
    """Gera PDF bancário completo em bytes usando ReportLab + QR Code Pix + Código de Barras."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    numero = str(dados.get("numero") or "COB-0001")
    banco_codigo = str(dados.get("banco_codigo") or "756").zfill(3)
    banco_nome = str(dados.get("banco_nome") or "SICOOB / REDE BANCÁRIA")
    linha_digitavel = str(dados.get("linha_digitavel") or "")
    codigo_barras = only_digits(dados.get("codigo_barras") or "")
    cedente = str(dados.get("cedente_nome") or "Mais Fibra Telecomunicações")
    cedente_doc = str(dados.get("cedente_doc") or "")
    sacado = str(dados.get("sacado_nome") or "Cliente")
    sacado_doc = str(dados.get("sacado_doc") or "")
    sacado_end = str(dados.get("sacado_endereco") or "")
    competencia = str(dados.get("competencia") or "")
    vencimento = str(dados.get("vencimento") or "")
    valor = float(dados.get("valor") or 0.0)
    desconto = float(dados.get("desconto") or 0.0)
    nosso_numero = str(dados.get("nosso_numero") or "")
    agencia = str(dados.get("agencia") or "0001")
    conta = str(dados.get("conta") or "0000001")
    carteira = str(dados.get("carteira") or "109")
    instrucoes = str(dados.get("instrucoes") or "Após vencimento cobrar multa de 2% e juros de 1% ao mês.")
    pix_payload = str(dados.get("pix_copia_cola") or "")

    # Cabeçalho institucional
    c.setFillColorRGB(0.05, 0.25, 0.55)
    c.rect(15 * mm, h - 28 * mm, w - 30 * mm, 14 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, h - 22 * mm, f"{cedente} — Boleto de Cobrança")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 20 * mm, h - 22 * mm, f"Doc: {numero} | Comp: {competencia}")

    # Recibo do Pagador
    y = h - 38 * mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(15 * mm, y, "RECIBO DO PAGADOR")
    y -= 4 * mm
    c.setStrokeColor(colors.grey)
    c.rect(15 * mm, y - 58 * mm, w - 30 * mm, 58 * mm)

    c.setFont("Helvetica", 8.5)
    c.drawString(18 * mm, y - 7 * mm, f"Beneficiário: {cedente}  {('CNPJ/CPF: ' + cedente_doc) if cedente_doc else ''}")
    c.drawString(18 * mm, y - 14 * mm, f"Pagador: {sacado}  {('CPF/CNPJ: ' + sacado_doc) if sacado_doc else ''}")
    if sacado_end:
        c.drawString(18 * mm, y - 21 * mm, f"Endereço: {sacado_end[:90]}")
    c.drawString(18 * mm, y - 29 * mm, f"Contrato / Referência: {dados.get('contrato_nome') or '-'} ({competencia})")
    c.drawString(18 * mm, y - 36 * mm, f"Nosso Número: {nosso_numero}   |   Agência/Conta: {agencia} / {conta}   |   Carteira: {carteira}")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(18 * mm, y - 47 * mm, f"Vencimento: {vencimento}")
    c.drawRightString(w - 18 * mm, y - 47 * mm, f"Valor do Documento: R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    if desconto > 0:
        c.setFont("Helvetica", 9)
        c.drawRightString(w - 18 * mm, y - 53 * mm, f"Desconto pontualidade: R$ {desconto:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    # QR Code Pix opcional ao lado ou abaixo
    if pix_payload:
        qr_img = _gerar_qr_image_reader(pix_payload)
        c.drawImage(qr_img, w - 53 * mm, y - 102 * mm, width=35 * mm, height=35 * mm)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(w - 35.5 * mm, y - 106 * mm, "Pague também via PIX")

    c.setFont("Helvetica-Bold", 9)
    c.drawString(18 * mm, y - 70 * mm, "Instruções de Cobrança:")
    c.setFont("Helvetica", 8.5)
    c.drawString(18 * mm, y - 76 * mm, instrucoes[:82])
    if len(instrucoes) > 82:
        c.drawString(18 * mm, y - 82 * mm, instrucoes[82:164])
    if dados.get("link_pagamento"):
        c.setFillColorRGB(0.05, 0.25, 0.55)
        c.drawString(18 * mm, y - 92 * mm, f"2ª via / Portal online: {str(dados['link_pagamento'])[:75]}")
        c.setFillColor(colors.black)

    # Linha pontilhada de corte
    y_corte = 115 * mm
    c.setDash(3, 3)
    c.line(15 * mm, y_corte, w - 15 * mm, y_corte)
    c.setDash()

    # Ficha de Compensação
    y_fc = y_corte - 8 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15 * mm, y_fc, f"{banco_nome[:24]}  |  {banco_codigo}-X")
    c.setFont("Helvetica-Bold", 10.5)
    c.drawRightString(w - 15 * mm, y_fc, linha_digitavel)

    c.rect(15 * mm, y_fc - 62 * mm, w - 30 * mm, 58 * mm)
    c.setFont("Helvetica", 8)
    c.drawString(18 * mm, y_fc - 10 * mm, "Local de Pagamento: PAGÁVEL EM QUALQUER BANCO OU LOTÉRICA ATÉ O VENCIMENTO")
    c.drawString(18 * mm, y_fc - 18 * mm, f"Beneficiário: {cedente} ({cedente_doc})")
    c.drawString(18 * mm, y_fc - 26 * mm, f"Data Doc: {dados.get('data_emissao') or vencimento}   |   Nº Doc: {numero}   |   Espécie: DM   |   Carteira: {carteira}")
    c.drawString(18 * mm, y_fc - 34 * mm, f"Pagador: {sacado}   |   CPF/CNPJ: {sacado_doc}")
    c.drawString(18 * mm, y_fc - 42 * mm, f"Nosso Número: {nosso_numero}   |   Vencimento: {vencimento}   |   Valor: R$ {valor:.2f}")

    # Código de barras I2of5
    if len(codigo_barras) == 44:
        try:
            barcode = createBarcodeDrawing(
                "I2of5", value=codigo_barras, barWidth=0.28 * mm, barHeight=13 * mm, checksum=0, bearers=0
            )
            barcode.drawOn(c, 15 * mm, y_fc - 80 * mm)
        except Exception:
            c.setFont("Courier", 8)
            c.drawString(15 * mm, y_fc - 72 * mm, f"BARCODE: {codigo_barras}")

    c.showPage()
    c.save()
    return buf.getvalue()


# ======================================================================
# Geração de XML e PDF Fiscal (NFCom / NFS-e)
# ======================================================================
def gerar_xml_nota_fiscal(dados):
    """Gera XML bem-formado (NFCom Modelo 62 ou NFS-e ABRASF) validado pelo lxml."""
    tipo = str(dados.get("tipo_documento") or "nfcom")
    root_tag = "NFCom" if tipo == "nfcom" else "CompNfse"
    root = etree.Element(root_tag, ambiente=str(dados.get("ambiente") or "homologacao"))

    inf = etree.SubElement(root, "InfDocumento", chave=str(dados.get("chave_acesso") or ""))
    etree.SubElement(inf, "Numero").text = str(dados.get("numero") or "1")
    etree.SubElement(inf, "Serie").text = str(dados.get("serie") or "1")
    etree.SubElement(inf, "Modelo").text = "62" if tipo == "nfcom" else "NFSE"
    etree.SubElement(inf, "Protocolo").text = str(dados.get("protocolo") or "")
    etree.SubElement(inf, "DataEmissao").text = str(dados.get("data_emissao") or date.today().isoformat())

    emit = etree.SubElement(inf, "Emitente")
    etree.SubElement(emit, "RazaoSocial").text = str(dados.get("emitente_nome") or "Mais Fibra")
    etree.SubElement(emit, "CNPJ").text = only_digits(dados.get("emitente_doc") or "")

    dest = etree.SubElement(inf, "Destinatario")
    etree.SubElement(dest, "Nome").text = str(dados.get("cliente_nome") or "")
    etree.SubElement(dest, "Documento").text = only_digits(dados.get("cliente_doc") or "")

    serv = etree.SubElement(inf, "Servico")
    etree.SubElement(serv, "Descricao").text = str(dados.get("descricao") or "Serviço de Comunicação Multimídia / Internet")
    etree.SubElement(serv, "ValorTotal").text = f"{float(dados.get('valor_total') or 0):.2f}"
    etree.SubElement(serv, "BaseCalculo").text = f"{float(dados.get('valor_total') or 0):.2f}"
    etree.SubElement(serv, "AliquotaISS").text = f"{float(dados.get('aliquota_iss') or 0):.2f}"
    etree.SubElement(serv, "ValorISS").text = f"{float(dados.get('valor_iss') or 0):.2f}"
    etree.SubElement(serv, "AliquotaICMS").text = f"{float(dados.get('aliquota_icms') or 0):.2f}"
    etree.SubElement(serv, "ValorICMS").text = f"{float(dados.get('valor_icms') or 0):.2f}"

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def gerar_pdf_nota_fiscal(dados):
    """Gera DANFE-COM / DANFSe em PDF com marca d'água em ambiente de homologação."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    ambiente = str(dados.get("ambiente") or "homologacao")
    tipo_label = "DANFE-COM — Nota Fiscal Fatura de Serviços de Comunicação (Mod. 62)" if dados.get("tipo_documento") == "nfcom" else "DANFSe — Nota Fiscal de Serviços Eletrônica"

    c.setFillColorRGB(0.10, 0.35, 0.25)
    c.rect(15 * mm, h - 30 * mm, w - 30 * mm, 16 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(18 * mm, h - 22 * mm, tipo_label)
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, h - 27 * mm, f"Emitente: {dados.get('emitente_nome') or 'Mais Fibra'} | CNPJ: {dados.get('emitente_doc') or '-'}")

    y = h - 42 * mm
    c.setFillColor(colors.black)
    c.rect(15 * mm, y - 75 * mm, w - 30 * mm, 75 * mm)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(18 * mm, y - 8 * mm, f"Número: {dados.get('numero') or '-'}   |   Série: {dados.get('serie') or '1'}   |   Status: {str(dados.get('status') or '').upper()}")
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, y - 16 * mm, f"Protocolo de Autorização: {dados.get('protocolo') or '-'}")
    c.drawString(18 * mm, y - 24 * mm, f"Chave de Acesso / Código Verificação: {dados.get('chave_acesso') or '-'}")
    c.drawString(18 * mm, y - 34 * mm, f"Tomador / Cliente: {dados.get('cliente_nome') or '-'}   |   CPF/CNPJ: {dados.get('cliente_doc') or '-'}")
    c.drawString(18 * mm, y - 42 * mm, f"Data de Emissão: {dados.get('data_emissao') or '-'}   |   Cobrança Ref.: {dados.get('cobranca_ref') or '-'}")
    c.drawString(18 * mm, y - 52 * mm, f"Discriminação dos Serviços: {str(dados.get('descricao') or '')[:80]}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(18 * mm, y - 65 * mm, f"Valor Total dos Serviços: R$ {float(dados.get('valor_total') or 0):.2f}")

    if ambiente == "homologacao":
        c.saveState()
        c.setFillColorRGB(0.85, 0.15, 0.15)
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(w / 2, h - 140 * mm, "AMBIENTE DE HOMOLOGAÇÃO — SEM VALOR FISCAL")
        c.restoreState()

    c.showPage()
    c.save()
    return buf.getvalue()
