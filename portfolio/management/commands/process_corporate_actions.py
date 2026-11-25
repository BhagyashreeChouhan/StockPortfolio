
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, models
from django.utils.dateparse import parse_date

from portfolio.models import (
    StockSplit,
    TaxLot,
    CashDividend,
    Portfolio,
    CashLedger,
)


class Command(BaseCommand):

    help = "Apply corporate actions for a given ex-date"

    def add_arguments(self, parser):
        parser.add_argument("ex_date", type=str)

    def handle(self, *args, **options):

        ex_date = parse_date(options["ex_date"])

        if ex_date is None:
            raise CommandError("Date must be in YYYY-MM-DD format.")

        with transaction.atomic():

            # Process Stock Splits
            splits = StockSplit.objects.filter(
                ex_date=ex_date
            )

            for split in splits:

                ratio = (
                    Decimal(split.ratio_to)
                    / Decimal(split.ratio_from)
                )

                tax_lots = TaxLot.objects.select_for_update().filter(
                    instrument=split.instrument,
                    buy_execution__executed_at__date__lt=ex_date,
                    remaining_quantity__gt=0
                )

                for lot in tax_lots:

                    lot.original_quantity *= ratio
                    lot.remaining_quantity *= ratio
                    lot.cost_per_share /= ratio

                    lot.save(
                        update_fields=[
                            "original_quantity",
                            "remaining_quantity",
                            "cost_per_share"
                        ]
                    )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Applied split for {split.instrument.ticker}"
                    )
                )

            # Process Cash Dividends
            dividends = CashDividend.objects.filter(
                ex_date=ex_date
            )

            for dividend in dividends:

                portfolios = Portfolio.objects.filter(
                    tax_lots__instrument=dividend.instrument,
                    tax_lots__remaining_quantity__gt=0
                ).distinct()

                for portfolio in portfolios:

                    quantity = TaxLot.objects.filter(
                        portfolio=portfolio,
                        instrument=dividend.instrument,
                        remaining_quantity__gt=0
                    ).aggregate(
                        total=models.Sum("remaining_quantity")
                    )["total"] or Decimal("0")

                    dividend_amount = (
                        quantity * dividend.dividend_per_share
                    )

                    if dividend_amount > 0:

                        CashLedger.objects.create(
                            portfolio=portfolio,
                            entry_type=CashLedger.EntryType.DIVIDEND,
                            amount=dividend_amount
                        )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Applied dividend for {dividend.instrument.ticker}"
                    )
                )
