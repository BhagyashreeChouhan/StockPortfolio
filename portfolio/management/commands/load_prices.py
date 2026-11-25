
import csv
from pathlib import Path
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portfolio.models import Instrument, DailyPrice


class Command(BaseCommand):
    help = "Load daily OHLCV prices from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str)

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])

        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        required_columns = {
            "exchange_code",
            "ticker",
            "trading_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }

        instruments = {
            (exchange.upper(), ticker.upper()): instrument_id
            for exchange, ticker, instrument_id in
            Instrument.objects.values_list(
                "exchange__code", "ticker", "id"
            )
        }

        batch = []
        batch_size = 5000
        total_loaded = 0
        total_errors = 0

        error_path = csv_path.with_name(
            f"{csv_path.stem}_errors.csv"
        )

        with open(
            csv_path, "r", encoding="utf-8-sig", newline=""
        ) as csv_file, open(
            error_path, "w", encoding="utf-8", newline=""
        ) as error_file:

            reader = csv.DictReader(csv_file)

            if (
                not reader.fieldnames
                or not required_columns.issubset(reader.fieldnames)
            ):
                raise CommandError(
                    "CSV is missing required columns."
                )

            error_writer = csv.writer(error_file)
            error_writer.writerow(
                ["row_number", "error"] + reader.fieldnames
            )

            def save_batch():
                nonlocal batch, total_loaded

                if not batch:
                    return

                with transaction.atomic():
                    DailyPrice.objects.bulk_create(
                        batch,
                        batch_size=1000,
                        update_conflicts=True,
                        update_fields=[
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                        ],
                        unique_fields=[
                            "instrument",
                            "trading_date",
                        ],
                    )

                total_loaded += len(batch)
                batch = []

            for row_number, row in enumerate(reader, start=2):
                try:
                    exchange_code = row["exchange_code"].strip().upper()
                    ticker = row["ticker"].strip().upper()

                    instrument_id = instruments.get(
                        (exchange_code, ticker)
                    )

                    if instrument_id is None:
                        raise ValueError("Unknown ticker or exchange.")

                    trading_date = date.fromisoformat(
                        row["trading_date"].strip()
                    )

                    open_price = Decimal(row["open"].strip())
                    high_price = Decimal(row["high"].strip())
                    low_price = Decimal(row["low"].strip())
                    close_price = Decimal(row["close"].strip())
                    volume = Decimal(row["volume"].strip())

                    values = [
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ]

                    if not all(value.is_finite() for value in values):
                        raise ValueError("Invalid numeric value.")

                    if any(
                        abs(value) >= Decimal("100000000000")
                        for value in values
                    ):
                        raise ValueError("Numeric value exceeds field limit.")

                    if low_price > open_price:
                        raise ValueError("Low price cannot exceed open price.")

                    if close_price > high_price:
                        raise ValueError("Close price cannot exceed high price.")

                    if high_price < low_price:
                        raise ValueError("High price cannot be less than low price.")

                    if volume < 0:
                        raise ValueError("Volume cannot be negative.")

                    batch.append(
                        DailyPrice(
                            instrument_id=instrument_id,
                            trading_date=trading_date,
                            open=open_price,
                            high=high_price,
                            low=low_price,
                            close=close_price,
                            volume=volume,
                        )
                    )

                    if len(batch) >= batch_size:
                        save_batch()

                except (
                    ValueError,
                    InvalidOperation,
                    TypeError,
                    KeyError,
                ) as error:
                    total_errors += 1

                    error_writer.writerow(
                        [row_number, str(error)]
                        + [
                            row.get(column, "")
                            for column in reader.fieldnames
                        ]
                    )

            save_batch()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully processed {total_loaded} price rows."
            )
        )

        self.stdout.write(f"Invalid rows skipped: {total_errors}")
        self.stdout.write(f"Error report: {error_path}")
