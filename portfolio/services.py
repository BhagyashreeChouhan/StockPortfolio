from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from .models import Portfolio, Order, DailyPrice, CashLedger, TaxLot, Execution, LotConsumption 


@transaction.atomic
def place_order(data):

    portfolio = Portfolio.objects.select_for_update().get(
        id=data["portfolio"].id
    )

    instrument = data["instrument"]
    side = data["side"]
    order_type = data["order_type"]
    quantity = data["quantity"]
    limit_price = data.get("limit_price")

    if side == Order.Side.BUY:

        ledger_balance = CashLedger.objects.filter(
            portfolio=portfolio
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

        reserved_cash = Decimal("0")

        pending_orders = Order.objects.filter(
            portfolio=portfolio,
            side=Order.Side.BUY,
            status__in=[
                Order.Status.PENDING,
                Order.Status.PARTIALLY_FILLED,
            ],
        ).prefetch_related("executions")

        for pending_order in pending_orders:
            filled_quantity = sum(
                (execution.quantity for execution in pending_order.executions.all()),
                Decimal("0")
            )

            remaining_quantity = pending_order.quantity - filled_quantity

            if pending_order.order_type == Order.OrderType.LIMIT:
                price = pending_order.limit_price
            else:
                latest_price = DailyPrice.objects.filter(
                    instrument=pending_order.instrument
                ).order_by("-trading_date").first()

                if not latest_price:
                    raise ValidationError(
                        "Latest price is not available for a pending market order."
                    )

                price = latest_price.close

            reserved_cash += remaining_quantity * price

        if order_type == Order.OrderType.LIMIT:
            required_cash = quantity * limit_price
        else:
            latest_price = DailyPrice.objects.filter(
                instrument=instrument
            ).order_by("-trading_date").first()

            if not latest_price:
                raise ValidationError(
                    "Latest price is not available for this instrument."
                )

            required_cash = quantity * latest_price.close

        available_cash = ledger_balance - reserved_cash

        if available_cash < required_cash:
            raise ValidationError("Insufficient available cash.")

    else:

        total_shares = TaxLot.objects.filter(
            portfolio=portfolio,
            instrument=instrument
        ).aggregate(total=Sum("remaining_quantity"))["total"] or Decimal("0")

        reserved_shares = Decimal("0")

        pending_orders = Order.objects.filter(
            portfolio=portfolio,
            instrument=instrument,
            side=Order.Side.SELL,
            status__in=[
                Order.Status.PENDING,
                Order.Status.PARTIALLY_FILLED,
            ],
        ).prefetch_related("executions")

        for pending_order in pending_orders:
            filled_quantity = sum(
                (execution.quantity for execution in pending_order.executions.all()),
                Decimal("0")
            )

            reserved_shares += pending_order.quantity - filled_quantity

        available_shares = total_shares - reserved_shares

        if available_shares < quantity:
            raise ValidationError("Insufficient available shares.")

    return Order.objects.create(**data)






@transaction.atomic
def execute_order(order_id, data):

    order = Order.objects.get(id=order_id)

    portfolio = Portfolio.objects.select_for_update().get(
        id=order.portfolio_id
    )

    order = Order.objects.select_for_update().get(
        id=order_id
    )

    if order.status not in [
        Order.Status.PENDING,
        Order.Status.PARTIALLY_FILLED,
    ]:
        raise ValidationError("This order cannot be executed.")

    external_id = data.get("external_execution_id")

    if external_id:
        existing_execution = Execution.objects.filter(
            external_execution_id=external_id
        ).first()

        if existing_execution:
            raise ValidationError("This execution already exists.")

    quantity = data["quantity"]
    price = data["price"]

    filled_quantity = order.executions.aggregate(
        total=Sum("quantity")
    )["total"] or Decimal("0")

    remaining_quantity = order.quantity - filled_quantity

    if quantity > remaining_quantity:
        raise ValidationError(
            "Execution quantity exceeds remaining order quantity."
        )

    execution = Execution.objects.create(
        order=order,
        quantity=quantity,
        price=price,
        external_execution_id=external_id,
    )

    total_amount = quantity * price

    if order.side == Order.Side.BUY:

        CashLedger.objects.create(
            portfolio=portfolio,
            entry_type=CashLedger.EntryType.BUY,
            amount=-total_amount,
        )

        TaxLot.objects.create(
            buy_execution=execution,
            portfolio=portfolio,
            instrument=order.instrument,
            original_quantity=quantity,
            remaining_quantity=quantity,
            cost_per_share=price,
        )

    else:

        CashLedger.objects.create(
            portfolio=portfolio,
            entry_type=CashLedger.EntryType.SELL,
            amount=total_amount,
        )

        tax_lots = TaxLot.objects.select_for_update().filter(
            portfolio=portfolio,
            instrument=order.instrument,
            remaining_quantity__gt=0,
        ).order_by("buy_execution__executed_at")

        remaining_to_consume = quantity

        for tax_lot in tax_lots:

            if remaining_to_consume <= 0:
                break

            consumed_quantity = min(
                tax_lot.remaining_quantity,
                remaining_to_consume,
            )

            LotConsumption.objects.create(
                execution=execution,
                tax_lot=tax_lot,
                consumed_quantity=consumed_quantity,
            )

            tax_lot.remaining_quantity -= consumed_quantity
            tax_lot.save(update_fields=["remaining_quantity"])

            remaining_to_consume -= consumed_quantity

        if remaining_to_consume > 0:
            raise ValidationError("Insufficient shares in tax lots.")

    total_filled = filled_quantity + quantity

    if total_filled == order.quantity:
        order.status = Order.Status.FILLED
    else:
        order.status = Order.Status.PARTIALLY_FILLED

    order.save(update_fields=["status"])

    return execution