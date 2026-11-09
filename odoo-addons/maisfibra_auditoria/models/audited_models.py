"""Modelos auditados. Para auditar outro modelo, crie mais uma classe igual às de baixo."""
from odoo import api, models

from .audit_log import IGNORAR


def _fmt(record, fname):
    field = record._fields[fname]
    val = record[fname]
    if field.type == "many2one":
        return val.display_name or ""
    if field.type in ("one2many", "many2many"):
        return ", ".join(val.mapped("display_name")[:20])
    if field.type == "selection":
        return dict(field._description_selection(record.env)).get(val, val) or ""
    if field.type in ("binary", "image"):
        return "(arquivo)" if val else ""
    if field.type == "html":
        return (val or "")[:2000]
    if val is False and field.type != "boolean":
        return ""
    return str(val)


def _auditaveis(records, fnames):
    out = []
    for f in fnames:
        field = records._fields.get(f)
        if not field or f in IGNORAR or f.startswith("message_") or f.startswith("activity_"):
            continue
        if not field.store or field.type in ("one2many",):
            continue
        out.append(f)
    return out


class AuditMixin(models.AbstractModel):
    _name = "maisfibra.audit.mixin"
    _description = "Auditoria automática"

    def _mf_audit_log(self, operacao, linhas_por_registro):
        Log = self.env["maisfibra.audit.log"].sudo()
        origem = "bridge" if self.env.context.get("mf_from_bridge") else False
        desc = self.env["ir.model"]._get(self._name).name
        vals = []
        for rec_id, (nome, linhas) in linhas_por_registro.items():
            if operacao == "write" and not linhas:
                continue
            vals.append({
                "user_id": self.env.uid, "model": self._name, "model_desc": desc,
                "res_id": rec_id, "res_name": (nome or "")[:250], "operacao": operacao,
                "origem": origem,
                "linha_ids": [(0, 0, l) for l in linhas],
            })
        if vals:
            Log.create(vals)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        if not self.env.context.get("mf_audit_off"):
            recs._mf_audit_log("create", {r.id: (r.display_name, []) for r in recs})
        return recs

    def write(self, vals):
        if self.env.context.get("mf_audit_off"):
            return super().write(vals)
        campos = _auditaveis(self, list(vals))
        antes = {r.id: {f: _fmt(r, f) for f in campos} for r in self} if campos else {}
        res = super().write(vals)
        if campos:
            linhas = {}
            for r in self:
                difs = []
                for f in campos:
                    novo = _fmt(r, f)
                    if novo != antes[r.id][f]:
                        difs.append({"campo": f, "campo_desc": r._fields[f].string,
                                     "antes": antes[r.id][f], "depois": novo})
                linhas[r.id] = (r.display_name, difs)
            self._mf_audit_log("write", linhas)
        return res

    def unlink(self):
        if not self.env.context.get("mf_audit_off"):
            self._mf_audit_log("unlink", {r.id: (r.display_name, []) for r in self})
        return super().unlink()


class CrmLead(models.Model):
    _name = "crm.lead"
    _inherit = ["crm.lead", "maisfibra.audit.mixin"]


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "maisfibra.audit.mixin"]


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "maisfibra.audit.mixin"]


class CrmStage(models.Model):
    _name = "crm.stage"
    _inherit = ["crm.stage", "maisfibra.audit.mixin"]
