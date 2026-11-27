"""Segmentação dinâmica e serviço declarativo seguro de avaliação de regras AND/OR."""
import json
import logging
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ALLOWED_MODELS = {"crm.lead", "res.partner", "mail.activity"}
BLOCKED_FIELD_TOKENS = {"password", "secret", "token", "apikey", "api_key", "credit_card", "pin"}
VIRTUAL_FIELDS = {
    "last_activity_date",
    "open_activity_count",
    "tag_names",
    "won_status_label",
}
VALID_OPERATORS = {
    "eq",
    "neq",
    "contains",
    "not_contains",
    "is_set",
    "is_empty",
    "gt",
    "lt",
    "between",
    "before",
    "after",
    "in_last_days",
    "not_in_last_days",
    "in_segment",
    "not_in_segment",
}
MAX_RULE_DEPTH = 4


class AutomationSegment(models.Model):
    _name = "mf.automation.segment"
    _description = "Segmento de Automação CRM"
    _order = "name, id"

    name = fields.Char("Nome do Segmento", required=True, index=True)
    description = fields.Text("Descrição")
    model_name = fields.Selection(
        [("crm.lead", "Leads e Oportunidades"), ("res.partner", "Contatos")],
        string="Modelo de Dados",
        default="crm.lead",
        required=True,
    )
    rules_json = fields.Text(
        "Regras de Segmentação (JSON)",
        default='{"logic": "AND", "rules": []}',
        required=True,
    )
    active = fields.Boolean("Ativo", default=True)
    member_count = fields.Integer("Registros Elegíveis", compute="_compute_member_count")

    def _compute_member_count(self):
        evaluator = self.env["mf.automation.evaluator"]
        for seg in self:
            if not seg.model_name or seg.model_name not in ALLOWED_MODELS:
                seg.member_count = 0
                continue
            Model = self.env[seg.model_name].sudo()
            records = Model.search([], order="id desc", limit=500)
            count = 0
            for rec in records:
                if evaluator.evaluate(rec, seg.rules_json, {"visited_segments": {seg.id}}):
                    count += 1
            seg.member_count = count

    def evaluate_record(self, record, extra_context=None):
        self.ensure_one()
        if not record or record._name != self.model_name:
            return False
        ctx = dict(extra_context or {})
        visited = set(ctx.get("visited_segments") or set())
        if self.id in visited:
            return False
        visited.add(self.id)
        ctx["visited_segments"] = visited
        return self.env["mf.automation.evaluator"].evaluate(record, self.rules_json, ctx)

    def action_preview_members(self):
        self.ensure_one()
        evaluator = self.env["mf.automation.evaluator"]
        Model = self.env[self.model_name].sudo()
        matched_ids = [
            rec.id
            for rec in Model.search([], order="id desc", limit=1000)
            if evaluator.evaluate(rec, self.rules_json, {"visited_segments": {self.id}})
        ]
        return {
            "type": "ir.actions.act_window",
            "name": _("Registros no Segmento: %s", self.name),
            "res_model": self.model_name,
            "view_mode": "list,form",
            "domain": [("id", "in", matched_ids)],
        }


class AutomationEvaluator(models.AbstractModel):
    _name = "mf.automation.evaluator"
    _description = "Avaliador Declarativo de Condições e Segmentos"

    @api.model
    def parse_rules(self, rules_input):
        if isinstance(rules_input, dict):
            return rules_input
        if not rules_input or not str(rules_input).strip():
            return {"logic": "AND", "rules": []}
        try:
            parsed = json.loads(rules_input)
        except (TypeError, ValueError) as exc:
            raise UserError(_("JSON de regras inválido: %s", exc)) from exc
        if not isinstance(parsed, dict):
            raise UserError(_("A raiz das regras deve ser um objeto JSON."))
        return parsed

    @api.model
    def _is_safe_field_path(self, field_path):
        if not field_path or not isinstance(field_path, str):
            return False
        path = field_path.strip()
        if path in VIRTUAL_FIELDS:
            return True
        if path.startswith("payload.") or path.startswith("event."):
            key = path.split(".", 1)[1]
            return bool(key) and "__" not in key and not any(tok in key.lower() for tok in BLOCKED_FIELD_TOKENS)
        parts = path.split(".")
        if len(parts) > 2:
            return False
        for part in parts:
            if not part or part.startswith("_") or "__" in part:
                return False
            if any(tok in part.lower() for tok in BLOCKED_FIELD_TOKENS):
                return False
        return True

    @api.model
    def validate_rules(self, model_name, rules_input, max_depth=MAX_RULE_DEPTH):
        errors = []
        if model_name not in ALLOWED_MODELS:
            return [_(f"Modelo '{model_name}' não permitido para segmentação.")]
        try:
            group = self.parse_rules(rules_input)
        except UserError as exc:
            return [str(exc)]

        def _walk(node, depth):
            if depth > max_depth:
                errors.append(_("Profundidade máxima de grupos aninhados (%s) excedida.", max_depth))
                return
            if not isinstance(node, dict):
                errors.append(_("Item de regra inválido."))
                return
            if "rules" in node:
                logic = str(node.get("logic") or "AND").upper()
                if logic not in ("AND", "OR"):
                    errors.append(_("Operador lógico '%s' inválido (use AND ou OR).", logic))
                for child in node.get("rules") or []:
                    _walk(child, depth + 1)
                return

            op = node.get("operator") or ""
            if op not in VALID_OPERATORS:
                errors.append(_("Operador '%s' não suportado.", op))
            if op in ("in_segment", "not_in_segment"):
                if not node.get("segment_id") and not node.get("value"):
                    errors.append(_("Regra de segmento requer seleção do segmento."))
                return

            field_path = node.get("field") or ""
            if not self._is_safe_field_path(field_path):
                errors.append(_("Campo '%s' não é permitido ou possui formato inválido.", field_path))
                return
            if (
                field_path not in VIRTUAL_FIELDS
                and not field_path.startswith("payload.")
                and not field_path.startswith("event.")
            ):
                root_field = field_path.split(".", 1)[0]
                if root_field not in self.env[model_name]._fields:
                    errors.append(_("Campo '%s' não existe no modelo %s.", root_field, model_name))

        _walk(group, 1)
        return errors

    @api.model
    def _resolve_field_value(self, record, field_path, extra_context=None):
        ctx = extra_context or {}
        if not record or not field_path:
            return None
        path = field_path.strip()
        if path.startswith("payload.") or path.startswith("event."):
            key = path.split(".", 1)[1]
            payload = ctx.get("payload") or {}
            return payload.get(key)

        if path == "tag_names":
            if "tag_ids" in record._fields:
                return [t.name for t in record.tag_ids]
            if "category_id" in record._fields:
                return [t.name for t in record.category_id]
            return []

        if path == "open_activity_count":
            return self.env["mail.activity"].sudo().search_count([
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
            ])

        if path == "last_activity_date":
            act = self.env["mail.activity"].sudo().search(
                [("res_model", "=", record._name), ("res_id", "=", record.id)],
                order="write_date desc, id desc",
                limit=1,
            )
            msg = self.env["mail.message"].sudo().search(
                [("model", "=", record._name), ("res_id", "=", record.id), ("message_type", "in", ("comment", "email"))],
                order="date desc, id desc",
                limit=1,
            )
            dates = [d for d in (act.write_date if act else None, msg.date if msg else None) if d]
            return max(dates) if dates else (record.write_date or record.create_date)

        if path == "won_status_label":
            if "won_status" in record._fields:
                return record.won_status
            if "stage_id" in record._fields and record.stage_id:
                return "won" if record.stage_id.is_won else "pending"
            return ""

        if not self._is_safe_field_path(path):
            return None

        parts = path.split(".")
        val = record
        for part in parts:
            if not val or part not in val._fields:
                return None
            val = val[part]
        return val

    @api.model
    def _to_datetime(self, value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        return None

    @api.model
    def _to_float(self, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        try:
            return float(str(value).replace(",", ".").strip())
        except (TypeError, ValueError):
            return None

    @api.model
    def _scalar_candidates(self, val):
        if val is None or val is False:
            return []
        if isinstance(val, models.BaseModel):
            out = []
            for rec in val:
                out.append(str(rec.id))
                if rec.display_name:
                    out.append(rec.display_name)
                if "name" in rec._fields and rec.name:
                    out.append(str(rec.name))
            return out
        if isinstance(val, (list, tuple, set)):
            return [str(x) for x in val if x is not None and x is not False]
        return [str(val)]

    @api.model
    def _eval_leaf(self, record, rule, extra_context=None):
        op = rule.get("operator") or "eq"
        if op in ("in_segment", "not_in_segment"):
            seg_id = int(rule.get("segment_id") or rule.get("value") or 0)
            seg = self.env["mf.automation.segment"].sudo().browse(seg_id).exists()
            res = seg.evaluate_record(record, extra_context=extra_context) if seg else False
            return res if op == "in_segment" else not res

        val = self._resolve_field_value(record, rule.get("field") or "", extra_context=extra_context)
        target = rule.get("value")
        target_to = rule.get("value_to")

        if op == "is_set":
            if isinstance(val, models.BaseModel):
                return bool(val)
            if isinstance(val, (list, tuple, set)):
                return len(val) > 0
            if isinstance(val, bool):
                return val
            return val not in (None, False, "")

        if op == "is_empty":
            return not self._eval_leaf(record, {**rule, "operator": "is_set"}, extra_context=extra_context)

        if op in ("eq", "neq"):
            if isinstance(val, bool):
                target_bool = str(target).strip().lower() in ("true", "1", "sim", "yes")
                matched = val is target_bool
            elif isinstance(val, (int, float)) and self._to_float(target) is not None:
                matched = float(val) == self._to_float(target)
            else:
                t_norm = str(target or "").strip().lower()
                cands = [c.strip().lower() for c in self._scalar_candidates(val)]
                matched = t_norm in cands
            return matched if op == "eq" else not matched

        if op in ("contains", "not_contains"):
            t_norm = str(target or "").strip().lower()
            cands = [c.lower() for c in self._scalar_candidates(val)]
            matched = bool(t_norm) and any(t_norm in c for c in cands)
            return matched if op == "contains" else not matched

        if op in ("gt", "lt"):
            num_val = self._to_float(val)
            num_target = self._to_float(target)
            if num_val is not None and num_target is not None:
                return num_val > num_target if op == "gt" else num_val < num_target
            dt_val = self._to_datetime(val)
            dt_target = self._to_datetime(target)
            if dt_val and dt_target:
                return dt_val > dt_target if op == "gt" else dt_val < dt_target
            return False

        if op == "between":
            num_val = self._to_float(val)
            num_a = self._to_float(target)
            num_b = self._to_float(target_to)
            if num_val is not None and num_a is not None and num_b is not None:
                return min(num_a, num_b) <= num_val <= max(num_a, num_b)
            dt_val = self._to_datetime(val)
            dt_a = self._to_datetime(target)
            dt_b = self._to_datetime(target_to)
            if dt_val and dt_a and dt_b:
                return min(dt_a, dt_b) <= dt_val <= max(dt_a, dt_b)
            return False

        if op in ("before", "after"):
            dt_val = self._to_datetime(val)
            dt_target = self._to_datetime(target)
            if not dt_val or not dt_target:
                return False
            return dt_val < dt_target if op == "before" else dt_val > dt_target

        if op in ("in_last_days", "not_in_last_days"):
            dt_val = self._to_datetime(val)
            days = self._to_float(target)
            if not dt_val or days is None:
                return op == "not_in_last_days"
            cutoff = datetime.utcnow() - timedelta(days=float(days))
            in_window = dt_val >= cutoff
            return in_window if op == "in_last_days" else not in_window

        return False

    @api.model
    def evaluate(self, record, rules_input, extra_context=None, depth=0):
        if not record:
            return False
        if depth > MAX_RULE_DEPTH:
            return False
        group = self.parse_rules(rules_input)
        if "rules" not in group:
            return self._eval_leaf(record, group, extra_context=extra_context)
        rules = group.get("rules") or []
        if not rules:
            return True
        logic = str(group.get("logic") or "AND").upper()
        results = []
        for item in rules:
            if isinstance(item, dict) and "rules" in item:
                results.append(self.evaluate(record, item, extra_context=extra_context, depth=depth + 1))
            elif isinstance(item, dict):
                results.append(self._eval_leaf(record, item, extra_context=extra_context))
        if not results:
            return True
        return any(results) if logic == "OR" else all(results)
