"""Usage ledger, tariff-aware CNY accounting and bounded session scheduling.

Reservations are scheduling estimates, not provider-side hard token limits.
Unknown interrupted usage is charged at its reservation until a receipt arrives.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from gcms_agent.config import write_json
from gcms_agent.harness.transport import unused_connection_failure

CHINA = timezone(timedelta(hours=8))
UTC = timezone.utc
DEFAULT_CNY = {
    "peak": {"input": 2.0, "cache": 0.04, "output": 8.0},
    "offpeak": {"input": 1.0, "cache": 0.02, "output": 4.0},
}


def tariff_at(when=None):
    """Unknown public holidays are conservatively regular weekdays."""
    when = when or datetime.now(UTC)
    if when.tzinfo is None:
        raise ValueError("Tariff timestamps must be timezone-aware")
    local = when.astimezone(CHINA)
    hour = local.hour + local.minute / 60 + local.second / 3600
    return "peak" if local.weekday() < 5 and (9 <= hour < 12 or 14 <= hour < 18) else "offpeak"


def interval_tariff(start, end):
    """Use the higher tariff if a session crosses any peak window."""
    if end < start:
        raise ValueError("Session end precedes start")
    if tariff_at(start) == "peak" or tariff_at(end) == "peak":
        return "peak"
    day = start.astimezone(CHINA).replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end.astimezone(CHINA):
        if day.weekday() < 5:
            for hour in (9, 14):
                if start <= day + timedelta(hours=hour) <= end:
                    return "peak"
        day += timedelta(days=1)
    return "offpeak"


def token_cost(usage, prices):
    return sum(usage.get(field, 0) * prices[price] for field, price in (
        ("input_tokens", "input"), ("cache_read_input_tokens", "cache"), ("output_tokens", "output")
    )) / 1e6


def output_reservation(runtime):
    """A per-session accounting estimate, independent of API output limits."""
    cap = runtime.get("max_output_tokens")
    if cap is not None:
        if type(cap) is not int or cap < 1:
            raise ValueError("Output cap must be null or a positive integer")
        amount = runtime.get("max_tool_turns", 6) * cap
    else:
        amount = runtime.get("reservation_output_tokens")
    if type(amount) is not int or amount < 1:
        raise ValueError("Output reservation must be a positive integer")
    return amount


class BudgetExhausted(RuntimeError):
    def __init__(self, message, category="budget_exhausted"):
        super().__init__(message)
        self.category = category


class Budget:
    """Single-event-loop ledger. The positional constructor stays USD-compatible."""
    def __init__(self, limit, prices, path, existing=None, *, currency="USD", tariffs=None, offpeak_only=False,
                 case_limit=None, max_sessions_per_case=None, max_transport_failures_per_case=3):
        if currency not in ("CNY", "USD"):
            raise ValueError("Unsupported ledger currency")
        if limit is not None and (type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0):
            raise ValueError("Budget must be null or finite and positive")
        self.limit, self.prices, self.path = float(limit) if limit is not None else None, deepcopy(prices), path
        self.currency, self.tariffs, self.offpeak_only = currency, deepcopy(tariffs or DEFAULT_CNY), offpeak_only
        if case_limit is not None and (type(case_limit) not in (int, float)
                or not math.isfinite(case_limit) or case_limit <= 0):
            raise ValueError("Case budget must be finite and positive")
        if max_sessions_per_case is not None and (type(max_sessions_per_case) is not int or max_sessions_per_case < 1):
            raise ValueError("Case session limit must be a positive integer")
        self.case_limit = float(case_limit) if case_limit is not None else None
        self.max_sessions_per_case = max_sessions_per_case
        if type(max_transport_failures_per_case) is not int or max_transport_failures_per_case < 1:
            raise ValueError("Transport failure limit must be a positive integer")
        self.max_transport_failures_per_case = max_transport_failures_per_case
        self.spent_key, self.cost_key = f"spent_{currency.lower()}", f"cost_{currency.lower()}"
        if existing and existing.get("currency", "USD") != currency:
            raise ValueError("Cannot resume a ledger using a different currency")
        self.ledger = deepcopy(existing) if existing else {self.spent_key: 0.0, "requests": []}
        self.ledger["currency"] = currency
        self.ledger.setdefault(self.spent_key, 0.0)
        self.reserved = dict(self.ledger.pop("pending_reservations", {}))
        self.reservation_metadata = dict(self.ledger.pop("reservation_metadata", {}))
        self.lock = asyncio.Lock()
        self.changed = asyncio.Condition(self.lock)

    @classmethod
    def from_config(cls, config, path, existing=None):
        runtime, model = config["runtime"], config["model"]
        currency = runtime.get("budget_currency", "USD")
        tariffs = model.get("prices_cny", DEFAULT_CNY)
        prices = tariffs["peak"] if currency == "CNY" else model["prices"]
        result = cls(runtime[f"budget_{currency.lower()}"], prices, path, existing,
                     currency=currency, tariffs=tariffs, offpeak_only=runtime.get("offpeak_only", False),
                     case_limit=runtime.get(f"case_budget_{currency.lower()}"),
                     max_sessions_per_case=runtime.get("max_sessions_per_case"),
                     max_transport_failures_per_case=runtime.get("max_transport_failures_per_case", 3))
        for request_id, reservation in list(result.reserved.items()):
            if not any(r["request_id"] == request_id for r in result.ledger["requests"]):
                result.ledger[result.spent_key] += reservation
                result.ledger["requests"].append({"request_id": request_id, "usage": {"known": False},
                    result.cost_key: reservation, "estimated": True, "reservation": reservation,
                    **result.reservation_metadata.get(request_id, {}), "recovered_after_interruption": True})
        result.reserved.clear()
        result.reservation_metadata.clear()
        # Only an explicit increase of the resource reservation may unlock overruns.
        # This changes no molecular search or per-response output limit.
        overruns = [r for r in result.ledger["requests"] if r.get("reservation_exceeded")]
        new_input = runtime.get("reservation_input_tokens", 80000)
        new_usage = {"input_tokens": new_input,
                     "output_tokens": output_reservation(runtime)}
        if result.ledger.get("reservation_exceeded") and overruns and all(
            new_input > r.get("reservation_usage", {}).get("input_tokens", 0)
            and token_cost(new_usage, result.tariffs[r.get("charged_tariff", "peak")] if currency == "CNY" else result.prices) >= r[result.cost_key]
            for r in overruns
        ):
            result.ledger["reservation_exceeded"] = False
            result.ledger.setdefault("reservation_revisions", []).append({
                "input_tokens": new_input, "utc": datetime.now(UTC).isoformat(),
                "reason": "explicit_increase_covers_observed_overruns"})
        return result

    @property
    def spent(self):
        return self.ledger[self.spent_key]

    def cost(self, usage):
        return token_cost(usage, self.prices)

    def case_usage(self, sample_id):
        """Scheduling commitment includes unknown receipts and in-flight requests."""
        requests = [r for r in self.ledger["requests"] if r.get("sample_id") == sample_id]
        pending = [amount for rid, amount in self.reserved.items()
                   if self.reservation_metadata.get(rid, {}).get("sample_id") == sample_id]
        spent = sum(r[self.cost_key] for r in requests)
        reserved = sum(pending)
        excluded = sum(r.get("excluded_transport_failure") is True for r in requests)
        return {"settled_cost": spent, "pending_reservations": reserved,
                "committed_cost": spent + reserved, "settled_sessions": len(requests),
                "pending_sessions": len(pending), "committed_sessions": len(requests) + len(pending),
                "effective_sessions": len(requests) - excluded + len(pending),
                "excluded_transport_failures": excluded,
                "unknown_usage_sessions": sum(not r["usage"].get("known", False) for r in requests)}

    async def reserve_review(self, request_id, payload, runtime):
        if (self.case_limit is not None or self.max_sessions_per_case is not None) and not payload.get("sample_id"):
            raise ValueError("Case-scoped review scheduling requires sample_id")
        async with self.changed:
            while True:
                # A waiting request has not started. Recheck its tariff window and
                # timestamp after settlement wakes it, before committing any cost.
                now = datetime.now(UTC)
                if self.offpeak_only and tariff_at(now) != "offpeak":
                    raise BudgetExhausted("Peak window: resume in an off-peak window", category="offpeak_wait")
                estimated_usage = {"input_tokens": runtime.get("reservation_input_tokens", 80000),
                                   "output_tokens": output_reservation(runtime)}
                # With no session deadline, reserve over the remaining outer
                # candidate limit conservatively rather than a zero-duration window.
                horizon = runtime.get("session_timeout_seconds", 300) or runtime.get("candidate_timeout_seconds", 900)
                tariff = interval_tariff(now, now + timedelta(seconds=horizon))
                prices = self.tariffs[tariff] if self.currency == "CNY" else self.prices
                amount = token_cost(estimated_usage, prices)
                metadata = {"started_utc": now.isoformat(), "reservation_tariff": tariff,
                    "reservation_usage": estimated_usage, "sample_id": payload.get("sample_id"),
                    "candidate_ids": [c.get("candidate_id") for c in payload.get("candidates", [])] or
                                     ([payload["candidate_id"]] if payload.get("candidate_id") else [])}
                if self._reserve_locked(request_id, amount, metadata, wait_for_pending=True):
                    return amount
                await self.changed.wait()

    async def reserve(self, request_id, amount, *, metadata=None):
        """Low-level fail-fast reservation; reviews wait for in-flight settlement."""
        async with self.lock:
            self._reserve_locked(request_id, amount, metadata)

    def _reserve_locked(self, request_id, amount, metadata, *, wait_for_pending=False):
        if not math.isfinite(float(amount)) or amount < 0:
            raise ValueError("Reservation must be finite and nonnegative")
        if (self.limit is not None or self.case_limit is not None) and self.ledger.get("reservation_exceeded"):
            raise BudgetExhausted("Observed usage exceeded a session reservation; review estimates before resuming",
                                  category="reservation_exceeded")
        if any(r["request_id"] == request_id for r in self.ledger["requests"]):
            raise ValueError("Request already settled; replay its stored receipt")
        if request_id in self.reserved:
            if self.reserved[request_id] != amount:
                raise ValueError("Conflicting duplicate reservation")
            if metadata is not None:
                previous = self.reservation_metadata.get(request_id, {})
                if any(previous.get(k) != metadata.get(k) for k in ("sample_id", "candidate_ids", "reservation_usage")):
                    raise ValueError("Conflicting duplicate reservation scope")
            return True
        sample_id = (metadata or {}).get("sample_id")
        pending_blocks = False
        if sample_id is not None:
            current = self.case_usage(sample_id)
            if current["excluded_transport_failures"] >= self.max_transport_failures_per_case:
                raise BudgetExhausted("Case transport failure limit reached", category="case_transport_failure_limit")
            if self.max_sessions_per_case is not None and current["effective_sessions"] >= self.max_sessions_per_case:
                raise BudgetExhausted("Case session scheduling limit reached", category="case_session_limit")
            if self.case_limit is not None and current["committed_cost"] + amount > self.case_limit + 1e-12:
                if not wait_for_pending or current["settled_cost"] + amount > self.case_limit + 1e-12:
                    raise BudgetExhausted("Request reservation exceeds remaining case budget", category="case_budget_exhausted")
                pending_blocks = True
        if self.limit is not None and self.spent + sum(self.reserved.values()) + amount > self.limit:
            if not wait_for_pending or self.spent + amount > self.limit:
                raise BudgetExhausted("Request reservation exceeds remaining batch budget")
            pending_blocks = True
        if pending_blocks:
            return False
        self.reserved[request_id] = amount
        if metadata is not None:
            self.reservation_metadata[request_id] = deepcopy(metadata)
        try:
            self.save()
        except Exception:
            # Nothing was sent yet. Failed persistence must not leave a
            # reservation that no caller can settle and other reviews wait on.
            self.reserved.pop(request_id, None)
            self.reservation_metadata.pop(request_id, None)
            self.changed.notify_all()
            raise
        return True

    async def release(self, request_id):
        """Release an unsent request only; the caller must establish no API call ran.

        This is scheduling cancellation, not a zero-usage provider receipt. It
        must never erase paid/unknown settled usage or consume a case session.
        """
        async with self.lock:
            if any(r["request_id"] == request_id for r in self.ledger["requests"]):
                raise ValueError("Cannot release a settled request")
            if request_id not in self.reserved:
                return False
            self.reserved.pop(request_id)
            self.reservation_metadata.pop(request_id, None)
            try:
                self.save()
            finally:
                self.changed.notify_all()
            return True

    def _known_cost(self, usage, metadata):
        if self.currency == "USD":
            return self.cost(usage), {}
        now = datetime.now(UTC)
        start = datetime.fromisoformat(metadata["started_utc"]) if metadata.get("started_utc") else None
        tariff = metadata.get("charged_tariff") or (interval_tariff(start, now) if start else "peak")
        details = {"charged_tariff": tariff, "settled_utc": metadata.get("settled_utc", now.isoformat()),
                   "peak_equivalent_cny": token_cost(usage, self.tariffs["peak"]),
                   "offpeak_equivalent_cny": token_cost(usage, self.tariffs["offpeak"])}
        return token_cost(usage, self.tariffs[tariff]), details

    async def settle(self, request_id, usage, *, failure=None):
        for field in ("input_tokens", "cache_read_input_tokens", "output_tokens"):
            value = usage.get(field, 0)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid token usage")
        async with self.lock:
            previous = next((r for r in self.ledger["requests"] if r["request_id"] == request_id), None)
            if previous:
                if previous["usage"] == usage:
                    if unused_connection_failure(failure, usage) and not previous.get("excluded_transport_failure"):
                        previous["excluded_transport_failure"] = True
                        try:
                            self.save()
                        finally:
                            self.changed.notify_all()
                    return
                if previous.get("estimated") and usage.get("known"):
                    self.ledger[self.spent_key] -= previous[self.cost_key]
                    cost, details = self._known_cost(usage, previous)
                    self.ledger[self.spent_key] += cost
                    previous.update(usage=usage, estimated=False, **{self.cost_key: cost}, **details)
                    previous["excluded_transport_failure"] = unused_connection_failure(failure, usage)
                    self._check_exceeded(previous)
                    try:
                        self.save()
                    finally:
                        self.changed.notify_all()
                    return
                raise ValueError("Conflicting duplicate usage settlement")
            reservation = self.reserved.pop(request_id, 0)
            metadata = self.reservation_metadata.pop(request_id, {})
            cost, details = self._known_cost(usage, metadata) if usage.get("known") else (reservation, {})
            self.ledger[self.spent_key] += cost
            entry = {"request_id": request_id, "usage": deepcopy(usage), self.cost_key: cost,
                     "estimated": not usage.get("known", False), "reservation": reservation, **metadata, **details,
                     "excluded_transport_failure": unused_connection_failure(failure, usage)}
            self.ledger["requests"].append(entry)
            self._check_exceeded(entry)
            try:
                self.save()
            finally:
                self.changed.notify_all()

    def _check_exceeded(self, entry):
        if entry.get("reservation_usage") and entry[self.cost_key] > entry["reservation"] + 1e-12:
            entry["reservation_exceeded"] = True
            self.ledger["reservation_exceeded"] = True

    def save(self):
        known = [r for r in self.ledger["requests"] if r["usage"].get("known")]
        self.ledger["usage"] = {field: sum(r["usage"].get(field, 0) for r in known)
                                for field in ("input_tokens", "cache_read_input_tokens", "output_tokens")}
        self.ledger["sessions"] = len(self.ledger["requests"])
        self.ledger["unknown_usage_sessions"] = len(self.ledger["requests"]) - len(known)
        write_json(self.path, {**self.ledger, f"limit_{self.currency.lower()}": self.limit,
            f"case_limit_{self.currency.lower()}": self.case_limit,
            "monetary_limits_enabled": self.limit is not None or self.case_limit is not None,
            "max_sessions_per_case": self.max_sessions_per_case,
            "max_transport_failures_per_case": self.max_transport_failures_per_case,
            "prices": self.prices, "prices_cny": self.tariffs if self.currency == "CNY" else None,
            "accounting_policy": "session_max_tariff; holidays conservatively weekdays" if self.currency == "CNY" else "fixed_usd",
            "pending_reservations": self.reserved, "reservation_metadata": self.reservation_metadata})
