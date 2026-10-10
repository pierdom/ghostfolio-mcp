"""Deterministic dry run of an activity import against existing activities.

Pure: no network, no clock, no environment reads. Nothing is imported - the
caller passes the activities it is about to send to ``import_transactions``
and gets each one classified, so duplicates are caught before they are
written.

Conventions (``METHOD_VERSION`` changes whenever one of these does):

- Activities are compared on their date in ``timezone``, the same calendar the
  analytics tools use. An activity's identity is its account, type,
  ``DATASOURCE:SYMBOL`` and that local date.
- ``duplicate``: an existing activity (or an earlier item of the same batch)
  has the same identity, quantity, unit price and fee, compared after
  rounding to 8 decimals.
- ``conflict``: same identity but a different quantity, price or fee - for
  example a corrected re-import. For an account whose interest is expected
  ``daily``, any second interest payment of the same kind on one day is a
  conflict, since such a payer pays once a day.
- ``invalid``: a required field is missing or malformed; it is not compared.
- ``new``: none of the above.
- Warnings never change the status: a local-midnight timestamp (Ghostfolio
  books it on the previous UTC day), a date-only value (read as UTC
  midnight), a date in the future, an unknown account.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from datetime import time
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from ghostfolio_mcp.analytics.common import Activity
from ghostfolio_mcp.analytics.common import InterestRule

METHOD_VERSION = "import-preview/1"

ACTIVITY_TYPES = frozenset(
    {"BUY", "SELL", "DIVIDEND", "FEE", "INTEREST", "ITEM", "LIABILITY"}
)
REQUIRED_FIELDS = (
    "currency",
    "dataSource",
    "date",
    "quantity",
    "symbol",
    "type",
    "unitPrice",
)

_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class PreviewConfig:
    timezone: str = "Europe/Madrid"
    interest_rules: Mapping[str, InterestRule] = field(default_factory=dict)


@dataclass(frozen=True)
class _Entry:
    """What two activities are compared on."""

    account_id: str
    type: str
    security: str
    local_date: date
    quantity: Decimal
    unit_price: Decimal
    fee: Decimal

    @property
    def identity(self) -> tuple:
        return (self.account_id, self.type, self.security, self.local_date)

    @property
    def values(self) -> tuple:
        return (self.quantity, self.unit_price, self.fee)


def _decimal(value: Any) -> Decimal:
    """A number rounded to 8 places; raises ValueError on anything else."""
    if isinstance(value, bool):
        raise ValueError("not a number")
    try:
        number = (
            Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
        )
    except InvalidOperation as exc:
        raise ValueError("not a number") from exc
    if not number.is_finite():
        raise ValueError("not a finite number")
    return number.quantize(_PLACES, rounding=ROUND_HALF_EVEN)


def _parse_date(value: Any) -> tuple[datetime, bool]:
    """(UTC-aware timestamp, was_date_only). Date-only means UTC midnight."""
    text = str(value)
    if len(text) == 10:
        day = date.fromisoformat(text)
        return datetime.combine(day, time(0, 0), ZoneInfo("UTC")), True
    timestamp = datetime.fromisoformat(text)
    if timestamp.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return timestamp, False


def _existing_entry(activity: Activity) -> _Entry:
    return _Entry(
        account_id=activity.account_id,
        type=activity.type,
        security=activity.security,
        local_date=activity.local_date,
        quantity=_decimal(activity.quantity),
        unit_price=_decimal(activity.unit_price),
        fee=_decimal(activity.native_fee),
    )


def preview_import(
    proposed: list[Mapping[str, Any]],
    existing: list[Activity],
    accounts: Mapping[str, str],
    config: PreviewConfig,
    today: date,
) -> dict[str, Any]:
    """Classify each proposed activity as new, duplicate, conflict or invalid.

    Args:
        proposed: Activities in ``import_transactions`` format, in batch order.
        existing: The portfolio's current activities, parsed.
        accounts: Account name per account ID, to validate ``accountId``.
        config: Timezone and per-account interest rules.
        today: Today's date in ``config.timezone``; later dates are flagged.
    """
    local_tz = ZoneInfo(config.timezone)
    by_identity: dict[tuple, list[tuple[str, _Entry]]] = {}
    for activity in sorted(existing, key=lambda a: a.id):
        entry = _existing_entry(activity)
        by_identity.setdefault(entry.identity, []).append((activity.id, entry))

    items = []
    for index, raw in enumerate(proposed):
        item: dict[str, Any] = {
            "index": index,
            "status": "new",
            "type": raw.get("type"),
            "security": f"{raw.get('dataSource')}:{raw.get('symbol')}",
            "account_id": raw.get("accountId"),
            "local_date": None,
            "booked_date": None,
            "matches": [],
            "reasons": [],
            "warnings": [],
        }
        items.append(item)

        missing = [name for name in REQUIRED_FIELDS if raw.get(name) in (None, "")]
        if missing:
            item["status"] = "invalid"
            item["reasons"].append(f"missing {', '.join(missing)}")
            continue
        if raw["type"] not in ACTIVITY_TYPES:
            item["status"] = "invalid"
            item["reasons"].append(f"unknown type {raw['type']!r}")
            continue
        try:
            timestamp, date_only = _parse_date(raw["date"])
            quantity = _decimal(raw["quantity"])
            unit_price = _decimal(raw["unitPrice"])
            fee = _decimal(raw.get("fee") or 0)
        except ValueError as exc:
            item["status"] = "invalid"
            item["reasons"].append(f"malformed value: {exc}")
            continue
        if quantity < 0 or unit_price < 0 or fee < 0:
            item["status"] = "invalid"
            item["reasons"].append("quantity, unitPrice and fee must not be negative")
            continue

        local = timestamp.astimezone(local_tz)
        local_date = local.date()
        booked_date = timestamp.astimezone(ZoneInfo("UTC")).date()
        item["local_date"] = local_date.isoformat()
        item["booked_date"] = booked_date.isoformat()
        if date_only:
            item["warnings"].append("date-only value is read as UTC midnight")
        elif local.time() == time(0, 0) and local_date != booked_date:
            item["warnings"].append(
                "local-midnight timestamp: Ghostfolio books it on "
                f"{booked_date.isoformat()}, the previous UTC day"
            )
        if local_date > today:
            item["warnings"].append("date is in the future")
        account_id = str(raw.get("accountId") or "")
        if account_id and account_id not in accounts:
            item["warnings"].append(f"unknown accountId {account_id!r}")
        if not account_id:
            item["warnings"].append(
                "no accountId: matched against unassigned activities"
            )

        entry = _Entry(
            account_id=account_id,
            type=raw["type"],
            security=item["security"],
            local_date=local_date,
            quantity=quantity,
            unit_price=unit_price,
            fee=fee,
        )
        candidates = by_identity.get(entry.identity, [])
        same = [ref for ref, other in candidates if other.values == entry.values]
        rule = config.interest_rules.get(account_id, InterestRule())
        if same:
            item["status"] = "duplicate"
            item["matches"] = same
            item["reasons"].append(
                "same account, type, asset, date, quantity, price and fee"
            )
        elif candidates:
            item["status"] = "conflict"
            item["matches"] = [ref for ref, _ in candidates]
            if entry.type == "INTEREST" and rule.expected == "daily":
                item["reasons"].append(
                    "a daily interest payer already has a payment that day"
                )
            else:
                item["reasons"].append(
                    "same account, type, asset and date with a different quantity, "
                    "price or fee"
                )
        # Later items of the batch are compared with this one too.
        by_identity.setdefault(entry.identity, []).append((f"batch:{index}", entry))

    counts = dict.fromkeys(("new", "duplicate", "conflict", "invalid"), 0)
    for item in items:
        counts[item["status"]] += 1
    return {
        "method_version": METHOD_VERSION,
        "config": {
            "timezone": config.timezone,
            "interest_rules": {
                account_id: {"attribution": rule.attribution, "expected": rule.expected}
                for account_id, rule in sorted(config.interest_rules.items())
            },
        },
        "today": today.isoformat(),
        "summary": {"total": len(items), **counts},
        "items": items,
    }
