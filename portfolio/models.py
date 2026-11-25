
from django.db import models
from django.db.models import Q, F
from django.conf import settings


class Exchange(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=100)
    currency = models.CharField(max_length=3)

    def __str__(self):
        return self.code


class Instrument(models.Model):
    ticker = models.CharField(max_length=10)
    name = models.CharField(max_length=200)
    isin = models.CharField(max_length=12, unique=True)
    exchange = models.ForeignKey(Exchange, on_delete=models.PROTECT, related_name="instruments")
    sector = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticker", "exchange"],
                name="unique_ticker_exchange"
            ),
        ]

    def __str__(self):
        return self.ticker



class DailyPrice(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="daily_prices")
    trading_date = models.DateField()
    open = models.DecimalField(max_digits=15, decimal_places=4)
    high = models.DecimalField(max_digits=15, decimal_places=4)
    low = models.DecimalField(max_digits=15, decimal_places=4)
    close = models.DecimalField(max_digits=15, decimal_places=4)
    volume = models.DecimalField(max_digits=15, decimal_places=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "trading_date"],
                name="unique_instrument_trading_date"
            ),
            models.CheckConstraint(
                condition=Q(low__lte=F("open")),
                name="daily_price_low_lte_open"
            ),
            models.CheckConstraint(
                condition=Q(close__lte=F("high")),
                name="daily_price_close_lte_high"
            ),
            models.CheckConstraint(
                condition=Q(volume__gte=0),
                name="daily_price_volume_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.instrument.ticker} - {self.trading_date}"



class StockSplit(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="stock_splits")
    ex_date = models.DateField()
    ratio_from = models.PositiveIntegerField()
    ratio_to = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ratio_from__gt=0) & Q(ratio_to__gt=0),
                name="stock_split_ratio_positive"
            ),
        ]

    def __str__(self):
        return f"{self.instrument.ticker} - {self.ratio_from}:{self.ratio_to}"



class CashDividend(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="cash_dividends")
    ex_date = models.DateField()
    dividend_per_share = models.DecimalField(max_digits=15, decimal_places=4)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(dividend_per_share__gte=0),
                name="dividend_per_share_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.instrument.ticker} - {self.ex_date}"


class TradingCalendar(models.Model):
    exchange = models.ForeignKey(Exchange, on_delete=models.PROTECT, related_name="trading_calendars")
    holiday_date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange", "holiday_date"],
                name="unique_exchange_holiday_date"
            ),
        ]

    def __str__(self):
        return f"{self.exchange.code} - {self.holiday_date}"



class Portfolio(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="portfolios")
    base_currency = models.CharField(max_length=3)

    def __str__(self):
        return f"{self.user} - {self.base_currency}"



class Order(models.Model):

    class Side(models.TextChoices):
        BUY = "BUY", "Buy"
        SELL = "SELL", "Sell"

    class OrderType(models.TextChoices):
        MARKET = "MARKET", "Market"
        LIMIT = "LIMIT", "Limit"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARTIALLY_FILLED = "PARTIALLY_FILLED", "Partially Filled"
        FILLED = "FILLED", "Filled"
        CANCELLED = "CANCELLED", "Cancelled"
        REJECTED = "REJECTED", "Rejected"

    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name="orders")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="orders")
    side = models.CharField(max_length=4, choices=Side.choices)
    order_type = models.CharField(max_length=6, choices=OrderType.choices)
    quantity = models.DecimalField(max_digits=15, decimal_places=4)
    limit_price = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="order_quantity_positive"
            ),
            models.CheckConstraint(
                condition=(
                    Q(order_type="LIMIT", limit_price__gt=0)
                    | Q(order_type="MARKET", limit_price__isnull=True)
                ),
                name="order_limit_price_condition"
            ),
            models.CheckConstraint(
                condition=Q(side__in=["BUY", "SELL"]),
                name="order_valid_side"
            ),
            models.CheckConstraint(
                condition=Q(order_type__in=["MARKET", "LIMIT"]),
                name="order_valid_type"
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "PENDING",
                        "PARTIALLY_FILLED",
                        "FILLED",
                        "CANCELLED",
                        "REJECTED"
                    ]
                ),
                name="order_valid_status"
            ),
        ]

    def __str__(self):
        return f"{self.side} {self.instrument.ticker} - {self.status}"



class Execution(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="executions")
    quantity = models.DecimalField(max_digits=15, decimal_places=4)
    price = models.DecimalField(max_digits=15, decimal_places=4)
    external_execution_id = models.CharField(max_length=100, null=True, blank=True)
    executed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="execution_quantity_positive"
            ),
            models.CheckConstraint(
                condition=Q(price__gt=0),
                name="execution_price_positive"
            ),
            models.UniqueConstraint(
                fields=["external_execution_id"],
                condition=Q(external_execution_id__isnull=False),
                name="unique_external_execution_id"
            )
        ]

    def __str__(self):
        return f"{self.order} - {self.quantity}"



class CashLedger(models.Model):

    class EntryType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "Deposit"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
        BUY = "BUY", "Buy"
        SELL = "SELL", "Sell"
        DIVIDEND = "DIVIDEND", "Dividend"
        FEE = "FEE", "Fee"

    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name="cash_ledger_entries")
    entry_type = models.CharField(max_length=10, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=15, decimal_places=4)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Cash ledger entries cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Cash ledger entries cannot be deleted.")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(entry_type__in=["DEPOSIT", "SELL", "DIVIDEND"], amount__gt=0)
                    | Q(entry_type__in=["WITHDRAWAL", "BUY", "FEE"], amount__lt=0)
                ),
                name="cash_ledger_amount_sign"
            ),
            models.CheckConstraint(
                condition=Q(
                    entry_type__in=[
                        "DEPOSIT",
                        "WITHDRAWAL",
                        "BUY",
                        "SELL",
                        "DIVIDEND",
                        "FEE"
                    ]
                ),
                name="cash_ledger_valid_entry_type"
            ),
        ]

    def __str__(self):
        return f"{self.entry_type} - {self.amount}"



class TaxLot(models.Model):
    buy_execution = models.OneToOneField(Execution, on_delete=models.PROTECT, related_name="tax_lot")
    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name="tax_lots")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="tax_lots")
    original_quantity = models.DecimalField(max_digits=15, decimal_places=4)
    remaining_quantity = models.DecimalField(max_digits=15, decimal_places=4)
    cost_per_share = models.DecimalField(max_digits=15, decimal_places=4)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(original_quantity__gt=0),
                name="tax_lot_original_quantity_positive"
            ),
            models.CheckConstraint(
                condition=Q(remaining_quantity__gte=0),
                name="tax_lot_remaining_quantity_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(remaining_quantity__lte=F("original_quantity")),
                name="tax_lot_remaining_lte_original"
            ),
            models.CheckConstraint(
                condition=Q(cost_per_share__gt=0),
                name="tax_lot_cost_positive"
            ),
        ]

    def __str__(self):
        return f"{self.instrument.ticker} - {self.remaining_quantity}"




class LotConsumption(models.Model):
    execution = models.ForeignKey(Execution, on_delete=models.PROTECT, related_name="lot_consumptions")
    tax_lot = models.ForeignKey(TaxLot, on_delete=models.PROTECT, related_name="consumptions")
    consumed_quantity = models.DecimalField(max_digits=15, decimal_places=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["execution", "tax_lot"],
                name="unique_execution_tax_lot"
            ),
            models.CheckConstraint(
                condition=Q(consumed_quantity__gt=0),
                name="lot_consumption_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"{self.tax_lot.instrument.ticker} - {self.consumed_quantity}"



