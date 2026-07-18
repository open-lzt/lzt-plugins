"""Steam auto-buy nodes: search the market, then buy one account.

**This is the first thing in this engine that spends money outward, on a stranger's listing.**
Every node before it acts on lots the operator already owns — a bad `bump` wastes a bump, a bad
`relist` publishes a lot you can delete. A bad buy is money gone to someone else. That asymmetry
is why this file is longer in its refusals than in its happy path.

Why a pack and not built-ins: these nodes reach `deps.get_client` for the raw marketplace Client,
which is the documented path a plugin uses to talk to pylzt (the same one `pylzt.dynamic_call`
takes). Nothing here needs the engine changed. Being a `kind: python` module is also the point —
buying is owner-only by construction, because the API refuses to install code (see docs/modules.md).

Why not just `pylzt.dynamic_call`: it can already reach `purchasing_fast_buy`, and it carries
`REFLECTIVE`, which is in `FORBIDDEN_CAPABILITIES`. A flow built on it can never be published as a
module. These nodes are the typed, shareable version of that same call — the flow that arranges
them is ordinary data anyone can publish and read.

The three facts this file is built on, read off pylzt's own docstrings rather than assumed
(`pylzt` is an alias of `pylzt` — `pylzt.__file__` resolves into `site-packages/pylzt/`):

- `purchasing_fast_buy(item_id, price, balance_id)` is **"Check and buy account"**. It validates
  before it pays. That is the primitive an auto-buyer wants.
- `purchasing_confirm(...)` says, in its own docs, **"This method doesn't check account for
  validity"**. Using it to save a round trip buys broken accounts. It is not used here.
- Both check and fast_buy: **"If you receive a `retry_request` error, you should repeat the same
  request (up to a maximum of 100 times)."** That is the endpoint's own protocol, and it is a
  different thing from the 429/5xx backoff — see `_RETRY_REQUEST` below.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Final

from pylzt import RetryableUpstream
from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed

# The marketplace's own retry contract for check/fast_buy, quoted in pylzt's docstrings. It is NOT
# the generic 429/5xx loop: this is "the answer is not ready, ask the identical question again",
# so it must not back off exponentially into a timeout, and it must be bounded exactly where the
# endpoint says it is bounded. pylzt surfaces it as the typed `RetryableUpstream`
# (`ErrorCode.RETRY_REQUEST`), which is what this file catches — no string matching.
_MAX_RETRY_REQUEST: Final = 100
_RETRY_REQUEST_PAUSE_S: Final = 0.5

# A search page the operator never sees is a page we paid latency for. The marketplace's own page
# size is the honest cap; asking for more is asking for a second request.
_MAX_CANDIDATES: Final = 100


class SteamSearchInput(BaseSchema):
    """The filter surface, and the reason it is not 126 fields wide.

    `category_steam` really does take 126 parameters. Rendering 126 form fields would produce a
    form nobody can read, in a bot where the operator is on a phone — so the ~20 that decide a buy
    are first-class and typed, and `filters_json` carries the rest verbatim for the cases the
    curated set cannot express.

    `filters_json` is a JSON *string* rather than an object because a node input is a scalar:
    `RunContext.resolve_input` returns `str | int | float | bool | None`. That is a real constraint
    of the engine, not a style choice, and pretending otherwise would fail at run time.
    """

    max_price: float = Field(
        title="Максимальная цена",
        description="Дороже этого — не покупать. Обязательное поле, а не фильтр: см. ниже.",
        json_schema_extra={"ui": "number"},
        gt=0,
    )
    min_price: float | None = Field(
        None,
        title="Минимальная цена",
        description="Подозрительно дешёвый аккаунт обычно чем-то плох.",
        json_schema_extra={"ui": "number"},
        ge=0,
    )
    currency: str = Field(
        "rub",
        title="Валюта",
        description="Цена и max_price считаются в ней.",
        json_schema_extra={"ui": "select"},
    )
    budget_total: float | None = Field(
        None,
        title="Бюджет на всю скупку",
        description="Кандидаты набираются, пока их сумма влезает. Пусто — только max_price за штуку.",
        json_schema_extra={"ui": "number"},
        gt=0,
    )

    title: str | None = Field(
        None, title="Название содержит", json_schema_extra={"ui": "text"}
    )
    game: int | None = Field(
        None,
        title="ID игры",
        description="appid: 730 — CS2, 570 — Dota 2, 252490 — Rust.",
        json_schema_extra={"ui": "number"},
    )
    order_by: str | None = Field(
        None,
        title="Сортировка",
        description="price_to_up — сначала дешёвые.",
        json_schema_extra={"ui": "select"},
    )
    origin: str | None = Field(
        None,
        title="Происхождение",
        description="Откуда аккаунт у продавца: brute, stealer, personal, resale, retrieve.",
        json_schema_extra={"ui": "select"},
    )
    balance_min: int | None = Field(
        None, title="Баланс Steam от", json_schema_extra={"ui": "number"}
    )
    inv_min: float | None = Field(
        None, title="Стоимость инвентаря от", json_schema_extra={"ui": "number"}
    )
    games_min: int | None = Field(
        None,
        title="Игр на аккаунте от",
        description="Маппится в gmin.",
        json_schema_extra={"ui": "number"},
    )
    points_min: int | None = Field(
        None, title="Points от", json_schema_extra={"ui": "number"}
    )
    faceit_lvl_min: int | None = Field(
        None, title="FACEIT уровень от", json_schema_extra={"ui": "number"}
    )
    country: str | None = Field(
        None,
        title="Страна",
        description="ISO-код. Несколько — через запятую.",
        json_schema_extra={"ui": "text"},
    )

    no_vac: bool = Field(
        True,
        title="Без VAC",
        description="По умолчанию включено: VAC необратим.",
        json_schema_extra={"ui": "bool"},
    )
    mafile: bool | None = Field(
        None,
        title="С mafile",
        description="Без mafile Steam Guard остаётся у продавца.",
        json_schema_extra={"ui": "bool"},
    )
    no_trade_ban: bool = Field(
        True, title="Без trade ban", json_schema_extra={"ui": "bool"}
    )
    no_market_limit: bool = Field(
        True,
        title="Без ограничения маркета",
        description="Иначе инвентарь нельзя продать.",
        json_schema_extra={"ui": "bool"},
    )
    daybreak: int | None = Field(
        None,
        title="Не активен дней",
        description="Сколько дней аккаунт не заходил — свежий вход у продавца плохой знак.",
        json_schema_extra={"ui": "number"},
    )
    not_origin: str | None = Field(
        None,
        title="Исключить происхождение",
        description="Через запятую. Например: brute,stealer.",
        json_schema_extra={"ui": "text"},
    )

    filters_json: str | None = Field(
        None,
        title="Доп. фильтры (JSON)",
        description=(
            'Любой параметр category_steam, которого нет выше. Пример: '
            '{"rmin": 100, "email_type": ["autoreg"], "reg_period": "year"}'
        ),
        json_schema_extra={"ui": "text"},
    )
    limit: int = Field(
        10,
        title="Сколько кандидатов вернуть",
        json_schema_extra={"ui": "number"},
        ge=1,
        le=_MAX_CANDIDATES,
    )


class SteamSearchOutput(BaseSchema):
    """`item_id` + `price` travel together on purpose.

    The price is not decoration and it is not for display: it is the price the *buy* node must
    quote back to the marketplace. Splitting them would let a flow wire an item_id from one search
    to a price from somewhere else, which is the exact mistake `max_price` exists to prevent.
    """

    found: int
    item_id: int | None
    price: float | None
    title: str | None
    total_price: float
    candidates_json: str


class SteamFastBuyInput(BaseSchema):
    item_id: int = Field(
        title="Лот", json_schema_extra={"ui": "lot_ref"}, gt=0
    )
    price: float = Field(
        title="Цена из поиска",
        description="Цена, на которой сработал поиск. Уходит в маркет как согласие платить ровно её.",
        json_schema_extra={"ui": "number"},
        gt=0,
    )
    max_price: float = Field(
        title="Потолок цены",
        description="Если цена лота выросла выше — не покупать.",
        json_schema_extra={"ui": "number"},
        gt=0,
    )
    balance_id: int | None = Field(
        None,
        title="ID баланса",
        description="Чем платить. Пусто — маркет выберет сам.",
        json_schema_extra={"ui": "number"},
    )
    dry_run: bool = Field(
        True,
        title="Холостой прогон",
        description="ВКЛЮЧЁН ПО УМОЛЧАНИЮ. Пока не выключите — ничего не купится.",
        json_schema_extra={"ui": "bool"},
    )


class SteamFastBuyOutput(BaseSchema):
    bought: bool
    item_id: int
    price_paid: float | None
    reason: str | None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _csv(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return tuple(part.strip() for part in value.split(",") if part.strip())


class SteamSearchNode(BaseNode):
    """Search only. Reads the market and buys nothing — which is why it is MARKET_READ and why it
    is a separate node from the buy.

    Splitting search from buy is not tidiness. A single "find and buy" node would make the flow
    unable to put anything between the two: no approval step, no price check the operator wrote, no
    Telegram message asking a human. Two nodes mean the graph decides what happens in the gap, and
    the gap is where the money decision lives.
    """

    node_type = "steam.search"
    required_inputs = ("max_price",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        filters = self._build_filters(ctx)
        account_ref = ctx.active_account_id or ctx.node.account_ref

        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            page = await client.market.category_steam(**filters)

        items = _items_of(page)
        max_price = _num(ctx.resolve_input("max_price"))
        if max_price is None:
            raise RunFailed(ctx.run_id, ctx.node.id, "max_price must be numeric")

        # Filter by price HERE as well as in the query. pmax is the marketplace's promise; this is
        # ours. If the two ever disagree, the money moves on our number, not on theirs.
        affordable = [it for it in items if (_num(it.get("price")) or float("inf")) <= max_price]
        limit = _int_or_none(ctx.resolve_optional("limit")) or 10
        candidates, total = self._fit_budget(affordable[:limit], ctx.resolve_optional("budget_total"))

        best = candidates[0] if candidates else None
        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "found": len(candidates),
                "item_id": _int_or_none(best.get("item_id")) if best else None,
                "price": _num(best.get("price")) if best else None,
                "title": str(best.get("title")) if best and best.get("title") else None,
                "total_price": total,
                "candidates_json": json.dumps(candidates, ensure_ascii=False),
            },
        )

    def _fit_budget(
        self, items: list[dict[str, Any]], budget_raw: object
    ) -> tuple[list[dict[str, Any]], float]:
        """Take candidates while their running total still fits the budget.

        **This is the whole budget mechanism, and it needs no ledger.** The obvious design — a
        counter incremented as each purchase lands — needs state that survives a crash and is
        shared across loop iterations, which a node does not have and which would have meant
        changing NodeDeps for the one pack that buys. It also would not have been unbypassable
        anyway: a money node can simply not call a ledger handed to it.

        Enforcing the cap at SELECTION instead makes the arithmetic close by itself. `fast_buy`
        quotes the searched price back to the marketplace, so each item is bought at the price
        counted here or not bought at all. Therefore
        `spent <= sum(selected prices) <= budget_total`, with no counter anywhere.

        What this does not do: reconcile. If a buy fails the budget is not handed back — the cap
        bounds what is *attempted*, which is the direction that matters for money. It also cannot
        stop a second run of the same flow from spending the budget again; this is a per-search
        ceiling, not an account-wide one, and an account-wide one belongs where the money actually
        moves rather than in a search node.

        Skips rather than stops: an item that does not fit is passed over and a cheaper one further
        down the page may still fit. Stopping at the first miss would silently turn "spend up to X"
        into "spend until the first expensive lot".
        """
        budget = _num(budget_raw)
        if budget is None:
            return items, sum(_num(it.get("price")) or 0.0 for it in items)

        picked: list[dict[str, Any]] = []
        total = 0.0
        for item in items:
            price = _num(item.get("price"))
            if price is None or total + price > budget:
                continue
            picked.append(item)
            total += price
        return picked, total

    def _build_filters(self, ctx: RunContext) -> dict[str, Any]:
        """Curated inputs → category_steam kwargs, then the JSON passthrough on top.

        The passthrough is applied last and deliberately wins: an operator who typed a raw
        parameter meant it. It cannot reach `pmax` though — see below.
        """
        max_price = _num(ctx.resolve_input("max_price"))
        filters: dict[str, Any] = {"pmax": max_price}

        simple: dict[str, str] = {
            "min_price": "pmin",
            "title": "title",
            "game": "game",
            "order_by": "order_by",
            "origin": "origin",
            "balance_min": "balance_min",
            "inv_min": "inv_min",
            "games_min": "gmin",
            "points_min": "points_min",
            "faceit_lvl_min": "faceit_lvl_min",
            "daybreak": "daybreak",
            "currency": "currency",
        }
        for port, param in simple.items():
            value = ctx.resolve_optional(port)
            if value is not None:
                filters[param] = value

        if ctx.resolve_optional("game") is not None:
            filters["game"] = (filters["game"],)  # the API takes a tuple of appids

        country = _csv(ctx.resolve_optional("country"))
        if country:
            filters["country"] = country
        not_origin = _csv(ctx.resolve_optional("not_origin"))
        if not_origin:
            filters["not_origin"] = not_origin

        # The negatives. `no_vac`/`no_trade_ban`/`no_market_limit` default TRUE in the schema, so a
        # flow that says nothing gets the cautious search rather than the permissive one. An
        # auto-buyer's defaults are the ones that run at 3am unattended.
        if ctx.resolve_optional("no_vac") is not False:
            filters["no_vac"] = True
        if ctx.resolve_optional("no_trade_ban") is not False:
            filters["trade_ban"] = "no"
        if ctx.resolve_optional("no_market_limit") is not False:
            filters["market"] = "no"
        mafile = ctx.resolve_optional("mafile")
        if mafile is True:
            filters["mafile"] = "yes"

        raw = ctx.resolve_optional("filters_json")
        if isinstance(raw, str) and raw.strip():
            try:
                extra = json.loads(raw)
            except ValueError as exc:
                raise RunFailed(
                    ctx.run_id, ctx.node.id, f"filters_json is not valid JSON: {exc}"
                ) from exc
            if not isinstance(extra, dict):
                raise RunFailed(ctx.run_id, ctx.node.id, "filters_json must be a JSON object")
            # pmax is the one key the passthrough may not touch. It is the price ceiling; letting
            # free-text JSON raise it would turn the operator's own escape hatch into the way the
            # cap gets lost.
            extra.pop("pmax", None)
            filters.update(extra)

        return {k: v for k, v in filters.items() if v is not None}


def _items_of(page: object) -> list[dict[str, Any]]:
    """The response's items, as plain dicts, whatever shape the model arrived in.

    Written defensively on purpose: this is the one place a marketplace response shape change would
    surface, and a wrong guess here must produce "found 0" rather than a traceback inside a flow
    that is about to spend money.
    """
    items = getattr(page, "items", None)
    if items is None and isinstance(page, dict):
        items = page.get("items")
    if not items:
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "model_dump"):
            out.append(item.model_dump())
    return out


class SteamFastBuyNode(BaseNode):
    """Buy one account. MONEY, not idempotent, guarded.

    `purchasing_fast_buy` is "check and buy" — it is used instead of `purchasing_confirm` because
    confirm does not check validity, and an auto-buyer that skips the check buys broken accounts at
    full price while reporting success.
    """

    node_type = "steam.fast_buy"
    required_inputs = ("item_id", "price", "max_price")
    batchable = True

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        item_id = _int_or_none(ctx.resolve_input("item_id"))
        price = _num(ctx.resolve_input("price"))
        max_price = _num(ctx.resolve_input("max_price"))
        if item_id is None or price is None or max_price is None:
            raise RunFailed(
                ctx.run_id, ctx.node.id, "item_id/price/max_price must be numeric and present"
            )

        # Checked before the guard is taken: refusing an over-cap lot is not an attempt, and
        # burning the idempotency key on it would make a later legitimate retry look like a replay.
        if price > max_price:
            return StepResultDTO(
                node_id=ctx.node.id,
                output={
                    "bought": False,
                    "item_id": item_id,
                    "price_paid": None,
                    "reason": f"price {price} is above the cap {max_price}",
                },
            )

        if ctx.resolve_optional("dry_run") is not False:
            # Default-on, and it returns success-shaped output so the rest of the flow can be
            # exercised end to end without buying anything. An example that buys the first time
            # somebody runs it is a trap, not an example.
            return StepResultDTO(
                node_id=ctx.node.id,
                output={
                    "bought": False,
                    "item_id": item_id,
                    "price_paid": None,
                    "reason": "dry_run: не куплено. Выключите dry_run, чтобы покупать по-настоящему.",
                },
            )

        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            # Same rule as market.relist, for the same reason: the purchase may have landed and its
            # result was lost to the crash. There is no honest value to return — the account is
            # bought and paid for, or it is not, and this process cannot tell. Inventing "bought:
            # false" would send a retry to buy a second one.
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                f"steam.fast_buy already attempted a purchase of item {item_id} on this step and "
                f"its outcome was lost to a crash; refusing to pay twice — check the order history "
                f"for this item and reconcile manually",
            )

        account_ref = ctx.active_account_id or ctx.node.account_ref
        balance_id = _int_or_none(ctx.resolve_optional("balance_id"))

        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            result = await self._fast_buy_with_retry(
                ctx, client, item_id=item_id, price=price, balance_id=balance_id
            )

        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "bought": True,
                "item_id": item_id,
                "price_paid": price,
                "reason": _status_of(result),
            },
        )

    async def _fast_buy_with_retry(
        self,
        ctx: RunContext,
        client: Any,
        *,
        item_id: int,
        price: float,
        balance_id: int | None,
    ) -> Any:
        """The endpoint's own retry contract, not the transport's.

        pylzt's docstring: "If you receive a `retry_request` error, you should repeat the same
        request (up to a maximum of 100 times)." So: the identical request, a short flat pause, and
        a hard cap where the docs put it. No exponential backoff — this is not congestion, it is the
        marketplace saying "not ready, ask again", and backing off would just miss the lot.

        The guard is already taken by the caller, so every attempt in here is inside one logical
        purchase. That is deliberate: `retry_request` means the request did not complete, and the
        alternative — guarding per attempt — would refuse our own legitimate second ask.
        """
        last: RetryableUpstream | None = None
        for _ in range(_MAX_RETRY_REQUEST):
            try:
                return await client.market.purchasing_fast_buy(
                    item_id=item_id, price=price, balance_id=balance_id
                )
            except RetryableUpstream as exc:
                # Caught by TYPE. pylzt already raises this for us: `RetryableUpstream.check()`
                # matches `retry_request` in the response body and carries it as `.hint`, so there
                # is nothing to add to the library and nothing to pattern-match here. An earlier
                # draft of this node did `if "retry_request" in str(exc).lower()` — that reads the
                # library's *formatted text* as an API, which breaks the day a message is reworded
                # and, worse, would swallow any unrelated error whose text happens to contain the
                # word while retrying a purchase 100 times.
                last = exc
                await asyncio.sleep(_RETRY_REQUEST_PAUSE_S)
        raise RunFailed(
            ctx.run_id,
            ctx.node.id,
            f"purchasing_fast_buy returned {_RETRY_REQUEST} {_MAX_RETRY_REQUEST} times for item "
            f"{item_id}; the marketplace never settled — last: {last}",
        )


def _status_of(result: object) -> str | None:
    status = getattr(result, "status", None)
    return str(status) if status is not None else None


REGISTRATIONS = [
    NodeRegistration(
        node_type=NodeType(
            key=SteamSearchNode.node_type,
            category=NodeCategory.LOGIC,
            input_schema=SteamSearchInput,
            output_schema=SteamSearchOutput,
            idempotent=True,
            capabilities=frozenset({NodeCapability.MARKET_READ}),
        ),
        impl=SteamSearchNode,
    ),
    NodeRegistration(
        node_type=NodeType(
            key=SteamFastBuyNode.node_type,
            category=NodeCategory.ACTION,
            input_schema=SteamFastBuyInput,
            output_schema=SteamFastBuyOutput,
            # Buying is never idempotent: the same request twice is two accounts and two payments.
            idempotent=False,
            capabilities=frozenset({NodeCapability.MARKET_MUTATE, NodeCapability.MONEY}),
        ),
        impl=SteamFastBuyNode,
    ),
]


# ---- Full-plugin lifecycle (lzt_flow.plugins): register the pack's nodes at PRE_INIT ----
from app.plugin_runtime import PluginLoadContext, PluginLoadedContext  # noqa: E402


def _register(ctx: PluginLoadContext) -> PluginLoadedContext:
    loaded = PluginLoadedContext()
    loaded.nodes.extend(REGISTRATIONS)
    return loaded


PRE_INIT = [_register]
