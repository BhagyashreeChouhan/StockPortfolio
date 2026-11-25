from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from .serializers import OrderSerializer, ExecutionSerializer, HoldingSerializer, PortfolioValueSerializer, RealizedPnLSerializer, TopMoverSerializer, MovingAverageSerializer
from .services import place_order, execute_order
from django.shortcuts import get_object_or_404
from .models import Order, CashLedger, LotConsumption, StockSplit, Portfolio, TaxLot, DailyPrice, Execution, Instrument
from decimal import Decimal
from django.db.models import Sum, F, Subquery, OuterRef, ExpressionWrapper, DecimalField, Window, Avg
from django.utils.dateparse import parse_date
from django.db.models.functions import RowNumber
from django.db.models.expressions import RowRange

# Create your views here.
class OrderCreateView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        serializer = OrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        portfolio = serializer.validated_data["portfolio"]

        if portfolio.user_id != request.user.id:
            raise PermissionDenied(
                "You do not have permission to use this portfolio."
            )

        order = place_order(serializer.validated_data)

        response_serializer = OrderSerializer(order)

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )
        
        
class ExecutionCreateView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):

        order = get_object_or_404(
            Order,
            id=order_id
        )

        if order.portfolio.user_id != request.user.id:
            raise PermissionDenied(
                "You do not have permission to execute this order."
            )

        serializer = ExecutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        execution = execute_order(
            order_id,
            serializer.validated_data
        )

        response_serializer = ExecutionSerializer(execution)

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )
        
        
        
        
class HoldingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, portfolio_id):
        portfolio = get_object_or_404(
            Portfolio,
            id=portfolio_id,
            user=request.user
        )

        tax_lots = (
            TaxLot.objects
            .filter(
                portfolio=portfolio,
                remaining_quantity__gt=0
            )
            .values(
                "instrument_id",
                "instrument__ticker",
                "instrument__name"
            )
            .annotate(
                quantity=Sum("remaining_quantity"),
                total_cost=Sum(
                    F("remaining_quantity") * F("cost_per_share")
                ),
                latest_price=Subquery(
                    DailyPrice.objects
                    .filter(instrument_id=OuterRef("instrument_id"))
                    .order_by("-trading_date")
                    .values("close")[:1]
                )
            )
        )

        holdings = []
        total_market_value = Decimal("0")

        for lot in tax_lots:
            quantity = lot["quantity"]
            total_cost = lot["total_cost"]
            latest_price = lot["latest_price"]

            if latest_price is None:
                raise NotFound(
                    f"Price not available for {lot['instrument__ticker']}"
                )

            average_cost = total_cost / quantity
            market_value = quantity * latest_price
            unrealized_pnl = market_value - total_cost

            total_market_value += market_value

            holdings.append({
                "instrument_id": lot["instrument_id"],
                "ticker": lot["instrument__ticker"],
                "instrument_name": lot["instrument__name"],
                "quantity": quantity,
                "average_cost": average_cost,
                "latest_price": latest_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl
            })

        for holding in holdings:
            if total_market_value:
                holding["portfolio_percentage"] = (
                    holding["market_value"] / total_market_value
                ) * Decimal("100")
            else:
                holding["portfolio_percentage"] = Decimal("0")

        serializer = HoldingSerializer(holdings, many=True)

        return Response(serializer.data)
    
    
    
class PortfolioValueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, portfolio_id):
        portfolio = get_object_or_404(
            Portfolio,
            id=portfolio_id,
            user=request.user
        )

        as_of = request.query_params.get("as_of")

        if not as_of:
            raise ValidationError({
                "as_of": "This query parameter is required."
            })

        as_of_date = parse_date(as_of)

        if as_of_date is None:
            raise ValidationError({
                "as_of": "Enter date in YYYY-MM-DD format."
            })

        cash_balance = (
            CashLedger.objects
            .filter(
                portfolio=portfolio,
                created_at__date__lte=as_of_date
            )
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0")
        )

        tax_lots = list(
            TaxLot.objects
            .filter(
                portfolio=portfolio,
                buy_execution__executed_at__date__lte=as_of_date
            )
            .select_related("buy_execution", "instrument")
        )

        lot_ids = [lot.id for lot in tax_lots]
        instrument_ids = list({
            lot.instrument_id for lot in tax_lots
        })

        consumptions = list(
            LotConsumption.objects
            .filter(
                tax_lot_id__in=lot_ids,
                execution__executed_at__date__lte=as_of_date
            )
            .select_related("execution")
        )

        splits = list(
            StockSplit.objects
            .filter(
                instrument_id__in=instrument_ids,
                ex_date__lte=as_of_date
            )
            .order_by("ex_date")
        )

        latest_prices = (
            DailyPrice.objects
            .filter(
                instrument_id__in=instrument_ids,
                trading_date__lte=as_of_date
            )
            .order_by("instrument_id", "-trading_date")
            .distinct("instrument_id")
        )

        price_map = {
            price.instrument_id: price.close
            for price in latest_prices
        }

        consumption_map = {}

        for consumption in consumptions:
            consumption_map.setdefault(
                consumption.tax_lot_id, []
            ).append(consumption)

        split_map = {}

        for split in splits:
            split_map.setdefault(
                split.instrument_id, []
            ).append(split)

        stock_value = Decimal("0")

        for lot in tax_lots:
            buy_date = lot.buy_execution.executed_at.date()

            quantity = lot.buy_execution.quantity

            events = []

            for consumption in consumption_map.get(lot.id, []):
                events.append((
                    consumption.execution.executed_at.date(),
                    0,
                    "SELL",
                    consumption
                ))

            for split in split_map.get(lot.instrument_id, []):
                if split.ex_date > buy_date:
                    events.append((
                        split.ex_date,
                        1,
                        "SPLIT",
                        split
                    ))

            events.sort(key=lambda item: (item[0], item[1]))

            for event_date, priority, event_type, event in events:

                if event_type == "SELL":
                    quantity -= event.consumed_quantity

                elif event_type == "SPLIT":
                    ratio = (
                        Decimal(event.ratio_to)
                        / Decimal(event.ratio_from)
                    )
                    quantity *= ratio

            if quantity <= 0:
                continue

            latest_price = price_map.get(lot.instrument_id)

            if latest_price is None:
                raise NotFound(
                    f"Price not available for {lot.instrument.ticker}"
                )

            stock_value += quantity * latest_price

        total_value = cash_balance + stock_value

        response_data = {
            "portfolio_id": portfolio.id,
            "as_of": as_of_date,
            "cash_balance": cash_balance,
            "stock_value": stock_value,
            "total_value": total_value
        }

        serializer = PortfolioValueSerializer(response_data)

        return Response(serializer.data)
    
    
    
class RealizedPnLView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, portfolio_id):
        portfolio = get_object_or_404(
            Portfolio,
            id=portfolio_id,
            user=request.user
        )

        start_date = parse_date(
            request.query_params.get("start_date", "")
        )
        end_date = parse_date(
            request.query_params.get("end_date", "")
        )

        if not start_date or not end_date:
            raise ValidationError({
                "date": "Both start_date and end_date are required in YYYY-MM-DD format."
            })

        if start_date > end_date:
            raise ValidationError({
                "date": "start_date cannot be after end_date."
            })

        realized_data = (
            LotConsumption.objects
            .filter(
                execution__order__portfolio=portfolio,
                execution__executed_at__date__gte=start_date,
                execution__executed_at__date__lte=end_date
            )
            .values(
                "tax_lot__instrument_id",
                "tax_lot__instrument__ticker",
                "tax_lot__instrument__name"
            )
            .annotate(
                total_sell_amount=Sum(
                    F("consumed_quantity") *
                    F("execution__price"),
                    output_field=DecimalField(
                        max_digits=20,
                        decimal_places=4
                    )
                ),
                total_cost = Sum(
                    F("consumed_quantity") *
                    F("tax_lot__cost_per_share"),
                    output_field=DecimalField(
                        max_digits=20,
                        decimal_places=4
                    )
                )
            )
            .order_by("tax_lot__instrument__ticker")
        )

        report = []

        for item in realized_data:
            realized_pnl = (
                item["total_sell_amount"] - item["total_cost"]
            )

            report.append({
                "instrument_id": item["tax_lot__instrument_id"],
                "ticker": item["tax_lot__instrument__ticker"],
                "instrument_name": item["tax_lot__instrument__name"],
                "realized_pnl": realized_pnl
            })

        serializer = RealizedPnLSerializer(report, many=True)

        return Response(serializer.data)
    
    
    
class TopMoversView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exchange_id):
        start_date = parse_date(
            request.query_params.get("start_date", "")
        )
        end_date = parse_date(
            request.query_params.get("end_date", "")
        )

        if not start_date or not end_date:
            raise ValidationError({
                "date": "Both start_date and end_date are required in YYYY-MM-DD format."
            })

        if start_date >= end_date:
            raise ValidationError({
                "date": "end_date must be after start_date."
            })

        instruments = Instrument.objects.filter(
            exchange_id=exchange_id,
            orders__executions__executed_at__date__gte=start_date,
            orders__executions__executed_at__date__lte=end_date
        ).distinct()

        start_price = DailyPrice.objects.filter(
            instrument=OuterRef("pk"),
            trading_date__lte=start_date
        ).order_by("-trading_date").values("close")[:1]

        end_price = DailyPrice.objects.filter(
            instrument=OuterRef("pk"),
            trading_date__lte=end_date
        ).order_by("-trading_date").values("close")[:1]

        movers = (
            instruments
            .annotate(
                start_price=Subquery(start_price),
                end_price=Subquery(end_price)
            )
            .exclude(start_price__isnull=True)
            .exclude(start_price=0)
            .exclude(end_price__isnull=True)
            .annotate(
                percentage_change=ExpressionWrapper(
                    (
                        (F("end_price") - F("start_price"))
                        / F("start_price")
                    ) * 100,
                    output_field=DecimalField(
                        max_digits=15,
                        decimal_places=4
                    )
                )
            )
            .order_by("-percentage_change")[:10]
        )

        report = [
            {
                "instrument_id": item.id,
                "ticker": item.ticker,
                "instrument_name": item.name,
                "start_price": item.start_price,
                "end_price": item.end_price,
                "percentage_change": item.percentage_change
            }
            for item in movers
        ]

        serializer = TopMoverSerializer(report, many=True)

        return Response(serializer.data)
    
    
    
class MovingAverageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, instrument_id):

        prices = (
            DailyPrice.objects
            .filter(instrument_id=instrument_id)
            .annotate(
                moving_average=Window(
                    expression=Avg("close"),
                    order_by=F("trading_date").asc(),
                    frame=RowRange(start=-19, end=0)
                )
            )
            .order_by("trading_date")
        )

        report = [
            {
                "trading_date": price.trading_date,
                "close": price.close,
                "moving_average": price.moving_average
            }
            for price in prices
        ]

        serializer = MovingAverageSerializer(report, many=True)
        return Response(serializer.data)