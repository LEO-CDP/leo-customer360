"""Validate an untrusted QueryBuilder rule tree against the catalog and the Audience Builder.

Never produces SQL: the form's ``rulesSql`` (segments-view.js) stays the only compiler.
Type and operator rules mirror ``QueryBuilder.catalogType`` and ``queryBuilderFilters``;
``test_rule_validator.py`` re-reads segments-view.js to keep them in sync.

Every error carries a user-facing ``question``. Only :class:`UnresolvedValue` means "ask the
user"; the rest mean refuse.
"""

from __future__ import annotations

import difflib
import json
import math
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

__all__ = [
    "RuleValidationError",
    "MalformedRules",
    "UnknownField",
    "UnknownOperator",
    "UnresolvedValue",
    "UnsafeValue",
    "ForbiddenField",
    "ValidatedRules",
    "SUPPORTED_OPERATORS",
    "form_hints",
    "looks_like_sql",
    "validate_rules",
]

# Bounds on the untrusted tree.
MAX_RULES = 64
MAX_DEPTH = 8
MAX_IN_VALUES = 256

_CONDITIONS = {"AND", "OR"}

# --------------------------------------------------------------------- the form's rules
# QueryBuilder.catalogType (jquery.sql.builder.js): data_type -> category.
_INTEGER = {"SMALLINT", "INTEGER", "INT", "INT2", "INT4", "BIGINT", "INT8", "SERIAL", "BIGSERIAL"}
_DECIMAL = {"NUMERIC", "DECIMAL", "REAL", "FLOAT", "FLOAT4", "DOUBLE", "FLOAT8", "DOUBLE PRECISION", "NUMBER"}
_TIMESTAMP = {"TIMESTAMP", "TIMESTAMPTZ", "DATETIME"}

# queryBuilderFilters (segments-view.js): operators per category.
_ORDERED_OPERATORS = (
    "equal", "not_equal", "less", "less_or_equal", "greater", "greater_or_equal",
    "between", "not_between", "in", "not_in", "is_null", "is_not_null",
)
_OPERATORS_BY_CATEGORY: Dict[str, tuple] = {
    "integer": _ORDERED_OPERATORS,
    "number": _ORDERED_OPERATORS,
    "datetime": _ORDERED_OPERATORS,
    "boolean": ("equal", "not_equal", "is_null", "is_not_null"),
    "json": ("equal", "not_equal", "is_null", "is_not_null"),
    "string": (
        "equal", "not_equal", "contains", "begins_with", "ends_with", "is_empty",
        "is_not_empty", "is_null", "is_not_null", "in", "not_in",
    ),
}
SUPPORTED_OPERATORS = frozenset(op for ops in _OPERATORS_BY_CATEGORY.values() for op in ops)

_ARITY = {
    "is_null": 0, "is_not_null": 0, "is_empty": 0, "is_not_empty": 0,
    "between": 2, "not_between": 2,
    "in": "many", "not_in": "many",
}

# Rendered as a date picker (YYYY-MM-DD) regardless of catalog type.
_DATE_INPUT_FIELDS = {"last_activity_at"}

# Same grammar as normalize_relative_intervals (segment_repository.py), which turns '-90 days'
# into now() - INTERVAL '90 days'.
_RELATIVE_OFFSET = re.compile(
    r"^[+-]\s*\d+\s+(milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?)$",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TRUE = {"true", "t", "1", "yes", "y"}
_FALSE = {"false", "f", "0", "no", "n"}

# SQL in a rule value is refused, not escaped. Keywords match only in statement shapes, so
# "Drop Inn" passes.
_SQL_LIKE = re.compile(
    r";|--|/\*|\*/"
    r"|\b(drop|alter|truncate|create)\s+(table|schema|database|index|view|function|role|user)\b"
    r"|\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b"
    r"|\bunion\s+(all\s+)?select\b|\bselect\s+.+\s+from\b"
    r"|\b(or|and)\s+\d+\s*=\s*\d+"
    r"|\bpg_sleep\s*\(|\bexec(ute)?\s*\(",
    re.IGNORECASE,
)

# For whole descriptions (prose): a bare ";" or "--" is punctuation, so only statement shapes
# count.
_SQL_IN_TEXT = re.compile(
    r"(;|--|/\*)\s*(drop|alter|truncate|create|delete|insert|update|select|grant|revoke)\b"
    r"|\b(drop|alter|truncate)\s+(table|schema|database|index|view|function|role|user)\b"
    r"|\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+[\w.]+\s+set\b"
    r"|\bunion\s+(all\s+)?select\b|\bselect\s+[\w*,\s.]+\s+from\s+[\w.]+"
    r"|'\s*(or|and)\s+'?\d+'?\s*=\s*'?\d+|\bpg_sleep\s*\(",
    re.IGNORECASE,
)

_DOMAIN_KEY = re.compile(r"domain_attributes->>'([^']+)'")

# Tenant comes from auth, never from json_rules.
_FORBIDDEN_FIELDS = {"tenant_id"}


# --------------------------------------------------------------------- errors
# Vietnamese for the fixed questions; templated ones build theirs in the error class.
_VI = {
    "No segmentable attributes are configured, so no rule can be built.":
        "Chưa có thuộc tính nào dùng được cho phân khúc, nên không thể tạo quy tắc.",
    "This audience definition is nested too deeply. Could you simplify it?":
        "Định nghĩa đối tượng này lồng nhau quá sâu. Bạn có thể đơn giản hóa không?",
    "This audience definition is not in a shape I can read.":
        "Định nghĩa đối tượng này không ở dạng tôi đọc được.",
    "Should these criteria all apply (AND), or any of them (OR)?":
        "Các tiêu chí này phải thỏa mãn tất cả (AND) hay chỉ cần một (OR)?",
    "This audience definition has too many criteria. Could you narrow it down?":
        "Định nghĩa đối tượng này có quá nhiều tiêu chí. Bạn có thể thu hẹp lại không?",
    "Which customer attribute should this criterion apply to?":
        "Tiêu chí này áp dụng cho thuộc tính khách hàng nào?",
    "That is a very long list of values. Could you use a range instead?":
        "Danh sách giá trị quá dài. Bạn có thể dùng một khoảng giá trị không?",
    "two values (a range)": "hai giá trị (một khoảng)",
}


class RuleValidationError(ValueError):
    """A rule tree that cannot be loaded as-is."""

    def __init__(self, message: str, *, question: str, field: Optional[str] = None,
                 question_vi: Optional[str] = None) -> None:
        super().__init__(message)
        self.question = question
        self.question_vi = question_vi or _VI.get(question)
        self.field = field
        self.suggestions: List[str] = []

    def question_in(self, lang: str) -> str:
        """``question`` in ``lang`` ("en" or "vi"); English when no translation exists."""
        return (self.question_vi if lang == "vi" else None) or self.question


class MalformedRules(RuleValidationError):
    """Wrong shape, too deep, or too many rules."""


class UnknownField(RuleValidationError):
    """The field is not in the segmentable attribute catalog."""

    def __init__(self, name: str, suggestions: Sequence[str]) -> None:
        options = ", ".join(suggestions)
        super().__init__(
            f"Unknown field {name!r}.",
            question=(
                f"Segments can only use customer-profile attributes, and “{name}” is not one of "
                f"them. Did you mean: {options or 'one of the available attributes'}?"
            ),
            question_vi=(
                f"Phân khúc chỉ dùng được thuộc tính hồ sơ khách hàng, và “{name}” không nằm trong "
                f"số đó. Ý bạn là: {options or 'một thuộc tính có sẵn'}?"
            ),
            field=name,
        )
        self.suggestions = list(suggestions)


class UnknownOperator(RuleValidationError):
    def __init__(self, operator: str, label: str, allowed: Sequence[str]) -> None:
        super().__init__(
            f"Operator {operator!r} is not offered for {label!r}.",
            question=f"“{label}” can be compared with: {', '.join(allowed)}. Which did you want?",
            question_vi=f"“{label}” có thể so sánh bằng: {', '.join(allowed)}. Bạn muốn dùng phép nào?",
            field=label,
        )
        self.suggestions = list(allowed)


class UnresolvedValue(RuleValidationError):
    """Value missing, mistyped or ambiguous: ask the user."""

    def __init__(
        self,
        field_name: str,
        value: Any,
        expected: Any,
        label: Optional[str] = None,
        choices: Sequence[str] = (),
    ) -> None:
        """``expected``: the field's ``_Filter`` (described per language) or a fixed text."""
        if isinstance(expected, _Filter):
            expected, expected_vi = _expected(expected), _expected(expected, "vi")
        else:
            expected_vi = _VI.get(expected, expected)
        empty = value is None or value == ""
        shown, shown_vi = ("nothing", "giá trị trống") if empty else (f"“{value}”",) * 2
        pretty = label or field_name
        if choices:
            options = ", ".join(choices)
            question = f"For “{pretty}” I got {shown}. Which did you mean: {options}?"
            question_vi = f"Với “{pretty}”, tôi nhận được {shown_vi}. Ý bạn là: {options}?"
        else:
            question = f"What value should “{pretty}” be compared against? I need {expected} and got {shown}."
            question_vi = f"“{pretty}” cần so sánh với giá trị nào? Tôi cần {expected_vi} nhưng nhận được {shown_vi}."
        super().__init__(f"Cannot use {value!r} as {expected} for {field_name!r}.", question=question,
                         question_vi=question_vi, field=field_name)
        self.value = value
        self.suggestions = list(choices)


class ForbiddenField(RuleValidationError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"Field {name!r} cannot be used in segment rules.",
            question=f"“{name}” cannot be used in a segment: the workspace is applied automatically.",
            question_vi=f"Không thể dùng “{name}” trong phân khúc: không gian làm việc được áp dụng tự động.",
            field=name,
        )


class UnsafeValue(RuleValidationError):
    def __init__(self, field_name: str, label: str) -> None:
        super().__init__(
            f"Value for {field_name!r} looks like SQL.",
            question=(
                f"The value for “{label}” looks like SQL, which segments do not accept. "
                "Please describe the audience in plain words."
            ),
            question_vi=(
                f"Giá trị của “{label}” trông giống SQL, phân khúc không chấp nhận SQL. "
                "Vui lòng mô tả đối tượng bằng lời thường."
            ),
            field=field_name,
        )


# --------------------------------------------------------------------- result
@dataclass
class ValidatedRules:
    """A tree ``setRules`` can load; leaf ``id`` equals the catalog ``field``."""

    json_rules: Dict[str, Any] = dc_field(default_factory=dict)
    fields_used: List[str] = dc_field(default_factory=list)


# --------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class _Filter:
    field: str
    label: str
    category: str
    qb_type: str
    qb_input: str
    date_input: bool
    operators: tuple
    allowed_values: tuple
    known_values: tuple = ()


def _filter_for(entry: Mapping[str, Any]) -> Optional[_Filter]:
    field_name = str(entry.get("field") or "").strip()
    if not field_name:
        return None
    dt = str(entry.get("data_type") or "TEXT").strip().upper()
    if dt in _INTEGER:
        category, qb_type, qb_input = "integer", "integer", "number"
    elif dt in _DECIMAL:
        category, qb_type, qb_input = "number", "double", "number"
    elif dt in {"BOOLEAN", "BOOL"}:
        category, qb_type, qb_input = "boolean", "boolean", "radio"
    elif dt == "DATE":
        category, qb_type, qb_input = "datetime", "date", "date"
    elif dt in _TIMESTAMP:
        category, qb_type, qb_input = "datetime", "datetime", "text"
    elif dt in {"JSON", "JSONB"}:
        category, qb_type, qb_input = "json", "string", "textarea"
    elif dt == "ARRAY":
        return None  # the form drops array attributes from the picker
    else:
        category, qb_type, qb_input = "string", "string", "text"

    operators = _OPERATORS_BY_CATEGORY[category]
    date_input = dt == "DATE" or field_name in _DATE_INPUT_FIELDS
    if date_input:
        qb_type, qb_input = "date", "date"
    if field_name == "status_code":  # rendered as a select: 1 = Active, 0 = Inactive
        category, qb_type, qb_input = "integer", "integer", "select"
        operators = ("equal", "not_equal", "is_null", "is_not_null")

    allowed = tuple(str(v) for v in (entry.get("allowed_values") or ()) if str(v).strip())
    if field_name == "status_code" and not allowed:
        allowed = ("1", "0")
    return _Filter(
        field=field_name,
        label=str(entry.get("name") or field_name),
        category=category,
        qb_type=qb_type,
        qb_input=qb_input,
        date_input=date_input,
        operators=operators,
        allowed_values=allowed,
        known_values=tuple(str(v) for v in (entry.get("known_values") or ())),
    )


class _Catalog:
    """Lookup by canonical field, display name, or bare domain-attribute key."""

    def __init__(self, entries: Iterable[Mapping[str, Any]]) -> None:
        self._by_key: Dict[str, _Filter] = {}
        self._display: List[str] = []
        for entry in entries:
            f = _filter_for(entry)
            if f is None:
                continue
            self._register(f.field, f)
            self._register(f.label, f)
            domain_key = _DOMAIN_KEY.search(f.field)
            if domain_key:
                self._register(domain_key.group(1), f)
            self._display.append(domain_key.group(1) if domain_key else f.field)

    def _register(self, key: str, f: _Filter) -> None:
        self._by_key.setdefault(key.strip().lower(), f)

    def resolve(self, name: str) -> _Filter:
        f = self._by_key.get(str(name).strip().lower())
        if f is None:
            raise UnknownField(str(name), self.suggest(str(name)))
        return f

    def suggest(self, name: str, n: int = 3) -> List[str]:
        pool = sorted(set(self._display))
        # Suggest by column part: "crm_transactions.amount" -> "amount".
        probe = str(name).split(".")[-1].lower()
        by_lower = {p.lower(): p for p in pool}
        close = difflib.get_close_matches(probe, list(by_lower), n=n, cutoff=0.6)
        return [by_lower[c] for c in close]

    def __len__(self) -> int:
        return len(self._by_key)


# --------------------------------------------------------------------- values
def _normalise_value(value: Any, f: _Filter) -> Any:
    """Return ``value`` in the form's representation for ``f``, or raise."""
    if isinstance(value, str) and _SQL_LIKE.search(value):
        raise UnsafeValue(f.field, f.label)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise UnresolvedValue(f.field, value, f, f.label, f.allowed_values)

    if f.allowed_values:
        token = str(value).strip().lower()
        for allowed in f.allowed_values:
            if token == allowed.lower():
                return int(allowed) if f.category == "integer" else allowed
        # No fuzzy fixing ("make" -> "male"): that's the model's job. Ask.
        raise UnresolvedValue(f.field, value, f, f.label, f.allowed_values)

    if f.category in ("integer", "number"):
        try:
            number = float(str(value).strip()) if not isinstance(value, bool) else math.nan
        except ValueError:
            number = math.nan
        if not math.isfinite(number) or (f.category == "integer" and not number.is_integer()):
            raise UnresolvedValue(f.field, value, f, f.label)
        return int(number) if number.is_integer() else number

    if f.category == "boolean":
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
        raise UnresolvedValue(f.field, value, f, f.label, ("true", "false"))

    if f.category == "datetime":
        return _normalise_temporal(value, f)

    if f.category == "json":
        # JSONB is compared with a JSON document; a bare word would fail at run time.
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        try:
            parsed = json.loads(str(value))
        except ValueError:
            parsed = None
        if not isinstance(parsed, (dict, list)):
            raise UnresolvedValue(f.field, value, f, f.label)
        return str(value).strip()

    # Text compares case-sensitively in SQL: "email" would match nobody when the data says "Email".
    token = str(value).strip()
    return next((k for k in f.known_values if k.lower() == token.lower()), token)


def _normalise_temporal(value: Any, f: _Filter) -> str:
    if isinstance(value, datetime):
        token = value.isoformat()
    elif isinstance(value, date):
        token = value.isoformat()
    else:
        token = str(value).strip()

    if f.date_input:
        # A date picker can't hold "last 90 days"; pinning today's date would freeze it. Ask.
        candidate = token[:10] if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", token) else token
        if _ISO_DATE.match(candidate):
            try:
                date.fromisoformat(candidate)
                return candidate
            except ValueError:
                pass
        raise UnresolvedValue(f.field, value, f, f.label)

    if _RELATIVE_OFFSET.match(token):
        return re.sub(r"\s+", " ", token)
    try:
        datetime.fromisoformat(token.replace("Z", "+00:00"))
        return token
    except ValueError:
        raise UnresolvedValue(f.field, value, f, f.label) from None


_EXPECTED = {
    "en": {"allowed": "one of ", "integer": "a whole number", "number": "a number", "boolean": "yes or no",
           "json": "a JSON document (object or array)", "string": "a text value", "date": "a date (YYYY-MM-DD)",
           "datetime": "a date/time or an offset such as -90 days"},
    "vi": {"allowed": "một trong: ", "integer": "một số nguyên", "number": "một số", "boolean": "có hoặc không",
           "json": "một tài liệu JSON (object hoặc array)", "string": "một giá trị văn bản",
           "date": "một ngày (YYYY-MM-DD)", "datetime": "một ngày/giờ hoặc khoảng lệch như -90 days"},
}


def _expected(f: _Filter, lang: str = "en") -> str:
    text = _EXPECTED[lang]
    if f.allowed_values:
        return text["allowed"] + ", ".join(f.allowed_values)
    if f.category == "datetime":
        return text["date"] if f.date_input else text["datetime"]
    return text[f.category]


def form_hints(entry: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """``{"operators", "value_format"[, "allowed_values"]}`` for the prompt, or ``None`` if unusable."""
    f = _filter_for(entry)
    if f is None or f.field in _FORBIDDEN_FIELDS:
        return None
    if f.date_input:
        value_format = "date picker: YYYY-MM-DD only"
    elif f.category == "datetime" and f.qb_type == "datetime":
        value_format = "ISO date/time, or an offset from now such as -90 days"
    elif f.category == "json":
        value_format = "whole JSON document only: one key (e.g. a city) cannot be filtered"
    else:
        value_format = ""
    hints: Dict[str, Any] = {"operators": list(f.operators), "value_format": value_format}
    if f.allowed_values:
        hints["allowed_values"] = list(f.allowed_values)
    return hints


def looks_like_sql(text: str) -> bool:
    """True when text contains SQL statements.

    Run before the model: given "x; DROP TABLE t" it drops the SQL half and returns a clean
    answer, so the validator never sees it.
    """
    return bool(_SQL_IN_TEXT.search(str(text or "")))


# --------------------------------------------------------------------- validator
def validate_rules(json_rules: Mapping[str, Any], catalog: Iterable[Mapping[str, Any]]) -> ValidatedRules:
    """Check ``json_rules`` against ``catalog`` and return it in the form's shape.

    An empty tree returns an empty result; callers needing a filter must reject it.
    Raises :class:`RuleValidationError`.
    """
    cat = _Catalog(catalog)
    if len(cat) == 0:
        raise MalformedRules(
            "Attribute catalog is empty.",
            question="No segmentable attributes are configured, so no rule can be built.",
        )

    state = {"rules": 0}
    used: List[str] = []

    def walk(node: Any, depth: int) -> Optional[Dict[str, Any]]:
        if depth > MAX_DEPTH:
            raise MalformedRules(
                f"Rule tree deeper than {MAX_DEPTH} levels.",
                question="This audience definition is nested too deeply. Could you simplify it?",
            )
        if not isinstance(node, Mapping):
            raise MalformedRules(
                f"Expected a rule object, got {type(node).__name__}.",
                question="This audience definition is not in a shape I can read.",
            )

        if "rules" in node:
            condition = str(node.get("condition") or "AND").upper()
            if condition not in _CONDITIONS:
                raise MalformedRules(
                    f"Unsupported condition {condition!r}.",
                    question="Should these criteria all apply (AND), or any of them (OR)?",
                )
            children = node.get("rules")
            if not isinstance(children, (list, tuple)):
                raise MalformedRules(
                    "`rules` must be a list.",
                    question="This audience definition is not in a shape I can read.",
                )
            kept = [r for r in (walk(child, depth + 1) for child in children) if r is not None]
            if not kept:
                return None
            return {"condition": condition, "rules": kept}

        state["rules"] += 1
        if state["rules"] > MAX_RULES:
            raise MalformedRules(
                f"More than {MAX_RULES} rules.",
                question="This audience definition has too many criteria. Could you narrow it down?",
            )

        raw_field = node.get("field") or node.get("id")
        if not raw_field:
            raise MalformedRules(
                "Rule has no field.",
                question="Which customer attribute should this criterion apply to?",
            )
        f = cat.resolve(str(raw_field))
        if f.field in _FORBIDDEN_FIELDS:
            raise ForbiddenField(f.field)
        used.append(f.field)

        operator = str(node.get("operator") or "equal").strip().lower()
        if operator not in f.operators:
            raise UnknownOperator(operator, f.label, f.operators)

        leaf = {
            "id": f.field,
            "field": f.field,
            "type": f.qb_type,
            "input": f.qb_input,
            "operator": operator,
        }
        arity = _ARITY.get(operator, 1)
        value = node.get("value")

        if arity == 0:
            leaf["value"] = None
            return leaf

        if arity == "many":
            values = value if isinstance(value, (list, tuple)) else (
                [v.strip() for v in value.split(",")] if isinstance(value, str) else [value]
            )
            values = [v for v in values if v is not None and str(v).strip() != ""]
            if not values:
                raise UnresolvedValue(f.field, value, f, f.label, f.allowed_values)
            if len(values) > MAX_IN_VALUES:
                raise MalformedRules(
                    f"More than {MAX_IN_VALUES} values in a single {operator}.",
                    question="That is a very long list of values. Could you use a range instead?",
                )
            leaf["value"] = [_normalise_value(v, f) for v in values]
            return leaf

        if arity == 2:
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            if len(values) != 2:
                raise UnresolvedValue(f.field, value, "two values (a range)", f.label)
            leaf["value"] = [_normalise_value(v, f) for v in values]
            return leaf

        if isinstance(value, (list, tuple)):
            if len(value) != 1:
                raise UnresolvedValue(f.field, value, f, f.label, f.allowed_values)
            value = value[0]
        leaf["value"] = _normalise_value(value, f)
        return leaf

    if not isinstance(json_rules, Mapping):
        raise MalformedRules(
            f"Expected a rule tree, got {type(json_rules).__name__}.",
            question="This audience definition is not in a shape I can read.",
        )
    if "rules" not in json_rules:
        raise MalformedRules(
            "The root must be a group with `condition` and `rules`.",
            question="This audience definition is not in a shape I can read.",
        )
    tree = walk(json_rules, 0)
    return ValidatedRules(json_rules=tree or {}, fields_used=used)
