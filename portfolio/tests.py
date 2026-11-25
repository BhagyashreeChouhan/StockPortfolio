from decimal import Decimal
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from .models import Exchange, Instrument, DailyPrice, Portfolio, CashLedger, Order , TaxLot, Execution, LotConsumption, StockSplit, CashDividend
from rest_framework.exceptions import ValidationError
from .services import place_order, execute_order
from django.db.models import Sum
from datetime import date, timedelta
from django.core.management import call_command
from io import StringIO
from rest_framework.test import APIClient
from django.urls import reverse

# Create your tests here.


class OrderConcurrencyTest(TransactionTestCase):

    def setUp(self):

        User = get_user_model()
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword"
        )

        self.exchange = Exchange.objects.create(
            code="NSE",
            name="National Stock Exchange",
            timezone="Asia/Kolkata",
            currency="INR"
        )

        self.instrument = Instrument.objects.create(
            ticker="TEST",
            name="Test Instrument",
            isin="TEST00000001",
            exchange=self.exchange,
            sector="Test",
            active=True
        )

        DailyPrice.objects.create(
            instrument=self.instrument,
            trading_date="2026-11-25",
            open=10,
            high=10,
            low=10,
            close=10,
            volume=1000
        )

        self.portfolio = Portfolio.objects.create(
            user=self.user,
            base_currency="INR"
        )

        CashLedger.objects.create(
            portfolio=self.portfolio,
            entry_type=CashLedger.EntryType.DEPOSIT,
            amount=Decimal("100.0000")
        )

    def place_concurrent_order(self, barrier):

        close_old_connections()

        try:
            portfolio = Portfolio.objects.get(
                id=self.portfolio.id
            )

            instrument = Instrument.objects.get(
                id=self.instrument.id
            )

            data = {
                "portfolio": portfolio,
                "instrument": instrument,
                "side": Order.Side.BUY,
                "order_type": Order.OrderType.LIMIT,
                "quantity": Decimal("6.0000"),
                "limit_price": Decimal("10.0000"),
            }

            barrier.wait()

            place_order(data)

            return "created"

        except Exception as error:
            from rest_framework.exceptions import ValidationError

            if isinstance(error, ValidationError):
                return "rejected"

            raise

        finally:
            close_old_connections()

    def test_simultaneous_orders_cannot_spend_same_cash(self):

        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:

            futures = [
                executor.submit(
                    self.place_concurrent_order,
                    barrier
                )
                for _ in range(2)
            ]

            results = [
                future.result()
                for future in futures
            ]

        self.assertCountEqual(
            results,
            ["created", "rejected"]
        )

        self.assertEqual(
            Order.objects.filter(
                portfolio=self.portfolio
            ).count(),
            1
        )
        
        
    def test_simultaneous_sell_orders_cannot_oversell_shares(self):

        buy_order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("10.0000"),
            limit_price=Decimal("10.0000"),
            status=Order.Status.FILLED,
        )

        buy_execution = Execution.objects.create(
            order=buy_order,
            quantity=Decimal("10.0000"),
            price=Decimal("10.0000"),
        )

        TaxLot.objects.create(
            buy_execution=buy_execution,
            portfolio=self.portfolio,
            instrument=self.instrument,
            original_quantity=Decimal("10.0000"),
            remaining_quantity=Decimal("10.0000"),
            cost_per_share=Decimal("10.0000"),
        )

        barrier = Barrier(2)

        def place_sell_order():

            close_old_connections()

            try:
                portfolio = Portfolio.objects.get(
                    id=self.portfolio.id
                )

                instrument = Instrument.objects.get(
                    id=self.instrument.id
                )

                data = {
                    "portfolio": portfolio,
                    "instrument": instrument,
                    "side": Order.Side.SELL,
                    "order_type": Order.OrderType.LIMIT,
                    "quantity": Decimal("7.0000"),
                    "limit_price": Decimal("10.0000"),
                }

                barrier.wait()

                place_order(data)

                return "created"

            except ValidationError:
                return "rejected"

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:

            futures = [
                executor.submit(place_sell_order)
                for _ in range(2)
            ]

            results = [
                future.result()
                for future in futures
            ]

        self.assertCountEqual(
            results,
            ["created", "rejected"]
        )

        self.assertEqual(
            Order.objects.filter(
                portfolio=self.portfolio,
                side=Order.Side.SELL,
            ).count(),
            1
        )
        
    def test_buy_execution_creates_ledger_and_tax_lot(self):

        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("5.0000"),
            limit_price=Decimal("10.0000"),
        )

        execution = execute_order(
            order.id,
            {
                "quantity": Decimal("5.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "BUY-TEST-001",
            }
        )

        order.refresh_from_db()

        self.assertEqual(order.status, Order.Status.FILLED)

        self.assertEqual(execution.quantity, Decimal("5.0000"))

        ledger_entry = CashLedger.objects.get(
            portfolio=self.portfolio,
            entry_type=CashLedger.EntryType.BUY,
        )

        self.assertEqual(ledger_entry.amount, Decimal("-50.0000"))

        tax_lot = TaxLot.objects.get(
            buy_execution=execution
        )

        self.assertEqual(
            tax_lot.original_quantity,
            Decimal("5.0000")
        )

        self.assertEqual(
            tax_lot.remaining_quantity,
            Decimal("5.0000")
        )

        self.assertEqual(
            tax_lot.cost_per_share,
            Decimal("10.0000")
        )
        
    def test_partial_execution_updates_order_status(self):

        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("10.0000"),
            limit_price=Decimal("10.0000"),
        )

        execute_order(
            order.id,
            {
                "quantity": Decimal("4.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "PARTIAL-001",
            }
        )

        order.refresh_from_db()

        self.assertEqual(
            order.status,
            Order.Status.PARTIALLY_FILLED
        )

        execute_order(
            order.id,
            {
                "quantity": Decimal("6.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "PARTIAL-002",
            }
        )

        order.refresh_from_db()

        self.assertEqual(
            order.status,
            Order.Status.FILLED
        )

        self.assertEqual(
            order.executions.aggregate(total=Sum("quantity"))["total"],
            Decimal("10.0000")
        )
        
        
    def test_duplicate_external_execution_id(self):
        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("5.0000"),
            limit_price=Decimal("10.0000"),
        )

        execution_data = {
            "quantity": Decimal("5.0000"),
            "price": Decimal("10.0000"),
            "external_execution_id": "DUPLICATE-001",
        }

        execute_order(order.id, execution_data)

        with self.assertRaises(ValidationError):
            execute_order(order.id, execution_data)

        self.assertEqual(
            Execution.objects.filter(
                external_execution_id="DUPLICATE-001"
            ).count(),
            1
        )
        
        
    def test_sell_execution_consumes_tax_lots_fifo(self):
        # First BUY: 4 shares at 10
        first_buy_order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("4.0000"),
            limit_price=Decimal("10.0000"),
            status=Order.Status.FILLED,
        )

        first_execution = Execution.objects.create(
            order=first_buy_order,
            quantity=Decimal("4.0000"),
            price=Decimal("10.0000"),
            external_execution_id="FIFO-BUY-001",
        )

        first_lot = TaxLot.objects.create(
            buy_execution=first_execution,
            portfolio=self.portfolio,
            instrument=self.instrument,
            original_quantity=Decimal("4.0000"),
            remaining_quantity=Decimal("4.0000"),
            cost_per_share=Decimal("10.0000"),
        )

        # Second BUY: 6 shares at 20
        second_buy_order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("6.0000"),
            limit_price=Decimal("20.0000"),
            status=Order.Status.FILLED,
        )

        second_execution = Execution.objects.create(
            order=second_buy_order,
            quantity=Decimal("6.0000"),
            price=Decimal("20.0000"),
            external_execution_id="FIFO-BUY-002",
        )

        second_lot = TaxLot.objects.create(
            buy_execution=second_execution,
            portfolio=self.portfolio,
            instrument=self.instrument,
            original_quantity=Decimal("6.0000"),
            remaining_quantity=Decimal("6.0000"),
            cost_per_share=Decimal("20.0000"),
        )

        # SELL 7 shares
        sell_order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.SELL,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("7.0000"),
            limit_price=Decimal("30.0000"),
        )

        execute_order(sell_order.id, {
            "quantity": Decimal("7.0000"),
            "price": Decimal("30.0000"),
            "external_execution_id": "FIFO-SELL-001",
        })

        first_lot.refresh_from_db()
        second_lot.refresh_from_db()

        self.assertEqual(
            first_lot.remaining_quantity,
            Decimal("0.0000"),
        )

        self.assertEqual(
            second_lot.remaining_quantity,
            Decimal("3.0000"),
        )

        consumptions = LotConsumption.objects.filter(
            execution__order=sell_order
        ).order_by("tax_lot__buy_execution__executed_at")

        self.assertEqual(consumptions.count(), 2)

        self.assertEqual(
            consumptions.get(tax_lot=first_lot).consumed_quantity,
            Decimal("4.0000"),
        )

        self.assertEqual(
            consumptions.get(tax_lot=second_lot).consumed_quantity,
            Decimal("3.0000"),
        )
        
        
    def test_execution_cannot_exceed_remaining_order_quantity(self):
        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("5.0000"),
            limit_price=Decimal("10.0000"),
        )

        with self.assertRaises(ValidationError):
            execute_order(order.id, {
                "quantity": Decimal("6.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "EXCEED-001",
            })

        self.assertEqual(
            Execution.objects.filter(order=order).count(),
            0
        )
        
    def test_stock_split_adjusts_tax_lot(self):
        buy_order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("10.0000"),
            limit_price=Decimal("100.0000"),
            status=Order.Status.FILLED,
        )

        execution = Execution.objects.create(
            order=buy_order,
            quantity=Decimal("10.0000"),
            price=Decimal("100.0000"),
            external_execution_id="SPLIT-BUY-001",
        )

        lot = TaxLot.objects.create(
            buy_execution=execution,
            portfolio=self.portfolio,
            instrument=self.instrument,
            original_quantity=Decimal("10.0000"),
            remaining_quantity=Decimal("10.0000"),
            cost_per_share=Decimal("100.0000"),
        )

        StockSplit.objects.create(
            instrument=self.instrument,
            ex_date=date(2026, 11, 26),
            ratio_from=1,
            ratio_to=5,
        )

        call_command(
            "process_corporate_actions",
            date(2026, 11, 26).isoformat(),
            stdout=StringIO(),
        )

        lot.refresh_from_db()

        self.assertEqual(
            lot.original_quantity,
            Decimal("50.0000"),
        )

        self.assertEqual(
            lot.remaining_quantity,
            Decimal("50.0000"),
        )

        self.assertEqual(
            lot.cost_per_share,
            Decimal("20.0000"),
        )

        self.assertEqual(
            lot.original_quantity * lot.cost_per_share,
            Decimal("1000.0000000000"),
        )
        
        
    def test_cash_dividend_creates_ledger_entry(self):
        buy_order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("10.0000"),
            limit_price=Decimal("100.0000"),
            status=Order.Status.FILLED,
        )

        execution = Execution.objects.create(
            order=buy_order,
            quantity=Decimal("10.0000"),
            price=Decimal("100.0000"),
            external_execution_id="DIVIDEND-BUY-001",
        )

        TaxLot.objects.create(
            buy_execution=execution,
            portfolio=self.portfolio,
            instrument=self.instrument,
            original_quantity=Decimal("10.0000"),
            remaining_quantity=Decimal("10.0000"),
            cost_per_share=Decimal("100.0000"),
        )

        CashDividend.objects.create(
            instrument=self.instrument,
            ex_date=date(2026, 11, 26),
            dividend_per_share=Decimal("5.0000"),
        )

        call_command(
            "process_corporate_actions",
            date(2026, 11, 26).isoformat(),
            stdout=StringIO(),
        )

        dividend_entry = CashLedger.objects.get(
            portfolio=self.portfolio,
            entry_type=CashLedger.EntryType.DIVIDEND,
        )

        self.assertEqual(
            dividend_entry.amount,
            Decimal("50.0000"),
        )
        
        
    def test_portfolio_holdings_api(self):

        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("5.0000"),
            limit_price=Decimal("10.0000"),
        )

        execute_order(
            order.id,
            {
                "quantity": Decimal("5.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "HOLDINGS-TEST-001",
            }
        )

        client = APIClient()
        client.force_authenticate(user=self.user)

        url = reverse(
            "portfolio-holdings",
            kwargs={"portfolio_id": self.portfolio.id}
        )

        response = client.get(url)

        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(response.data), 1)

        holding = response.data[0]

        self.assertEqual(holding["ticker"], "TEST")
        self.assertEqual(Decimal(str(holding["quantity"])), Decimal("5.0000"))
        self.assertEqual(Decimal(str(holding["average_cost"])), Decimal("10.0000"))
        self.assertEqual(Decimal(str(holding["latest_price"])), Decimal("10.0000"))
        self.assertEqual(Decimal(str(holding["market_value"])), Decimal("50.0000"))
        self.assertEqual(Decimal(str(holding["unrealized_pnl"])), Decimal("0.0000"))
        self.assertEqual(Decimal(str(holding["portfolio_percentage"])), Decimal("100.0000"))
        
        
        
    def test_top_movers_api(self):

        DailyPrice.objects.create(
            instrument=self.instrument,
            trading_date="2026-11-26",
            open=15,
            high=15,
            low=15,
            close=15,
            volume=1000,
        )

        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("5.0000"),
            limit_price=Decimal("10.0000"),
        )

        execute_order(
            order.id,
            {
                "quantity": Decimal("5.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "TOP-MOVER-TEST-001",
            }
        )

        client = APIClient()
        client.force_authenticate(user=self.user)

        url = reverse(
            "top-movers",
            kwargs={"exchange_id": self.exchange.id}
        )

        response = client.get(
            url,
            {
                "start_date": "2026-11-25",
                "end_date": "2026-11-26",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        mover = response.data[0]

        self.assertEqual(mover["ticker"], "TEST")
        self.assertEqual(
            Decimal(str(mover["start_price"])),
            Decimal("10.0000")
        )
        self.assertEqual(
            Decimal(str(mover["end_price"])),
            Decimal("15.0000")
        )
        self.assertEqual(
            Decimal(str(mover["percentage_change"])),
            Decimal("50.0000")
        )
        
        
    def test_moving_average_api(self):

        DailyPrice.objects.filter(
            instrument=self.instrument
        ).delete()

        for day in range(1, 21):
            DailyPrice.objects.create(
                instrument=self.instrument,
                trading_date=date(2026, 10, day),
                open=day,
                high=day,
                low=day,
                close=day,
                volume=1000,
            )

        client = APIClient()
        client.force_authenticate(user=self.user)

        url = reverse(
            "moving-average",
            kwargs={"instrument_id": self.instrument.id}
        )

        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 20)

        last_record = response.data[-1]

        self.assertEqual(
            Decimal(str(last_record["moving_average"])),
            Decimal("10.5000")
        )
        
        
        
    def test_portfolio_historical_value_api(self):

        order = Order.objects.create(
            portfolio=self.portfolio,
            instrument=self.instrument,
            side=Order.Side.BUY,
            order_type=Order.OrderType.LIMIT,
            quantity=Decimal("5.0000"),
            limit_price=Decimal("10.0000"),
        )

        execute_order(
            order.id,
            {
                "quantity": Decimal("5.0000"),
                "price": Decimal("10.0000"),
                "external_execution_id": "HISTORICAL-TEST-001",
            }
        )

        today = date.today()

        DailyPrice.objects.filter(
            instrument=self.instrument
        ).delete()

        DailyPrice.objects.create(
            instrument=self.instrument,
            trading_date=today - timedelta(days=1),
            open=10,
            high=10,
            low=10,
            close=10,
            volume=1000,
        )

        client = APIClient()
        client.force_authenticate(user=self.user)

        url = reverse(
            "portfolio-value",
            kwargs={"portfolio_id": self.portfolio.id}
        )

        response = client.get(
            url,
            {"as_of": today.isoformat()}
        )

        self.assertEqual(response.status_code, 200)

        print(response.data)