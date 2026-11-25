# Stock Portfolio Management System

A Django REST Framework-based Stock Portfolio Management System built using PostgreSQL. The project provides portfolio management, order execution, cash ledger tracking, FIFO-based tax lot accounting, corporate action processing, and reporting APIs.

## Tech Stack

- Python 3.11+
- Django 5.x
- Django REST Framework
- PostgreSQL
- Docker and Docker Compose

## Features

- Stock exchanges and instruments management
- Daily OHLCV stock price management
- Stock split and cash dividend processing
- Multiple portfolios per user
- Buy and sell order management
- Partial order executions
- Cash reservation and share availability checks
- Immutable cash ledger
- FIFO-based tax lot tracking
- Execution idempotency
- Portfolio holdings and historical valuation APIs
- Realized P&L reporting
- Top 10 movers reporting
- 20-day moving average calculation
- Bulk CSV price import with upsert support
- Database constraints and transaction-based operations

## Installation and Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd StockPortfolio
```

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```env
DB_NAME=stock_portfolio
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
```

Use the database credentials configured for your PostgreSQL instance.

### 3. Create and Activate Virtual Environment

**Windows:**

```powershell
python -m venv venv
venv\Scripts\activate
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Apply Migrations

```bash
python manage.py migrate
```

### 6. Start the Development Server

```bash
python manage.py runserver
```

The API base URL is:

```text
http://127.0.0.1:8000/api/
```

## Docker Setup

Docker and Docker Compose must be installed.

### Start the Application

```bash
docker compose up --build
```

This starts the PostgreSQL database and Django application. Database migrations are applied automatically before the development server starts.

Application URL:

```text
http://localhost:8000/
```

### Stop the Application

```bash
docker compose down
```

PostgreSQL data is persisted using a Docker named volume.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/orders/` | Create a buy or sell order |
| POST | `/api/orders/{id}/executions/` | Add an order execution |
| GET | `/api/portfolios/{id}/holdings/` | Get portfolio holdings |
| GET | `/api/portfolios/{id}/value/` | Get historical portfolio value |
| GET | `/api/portfolios/{id}/realized-pnl/` | Get realized profit and loss |
| GET | `/api/exchanges/{id}/top-movers/` | Get top 10 movers for an exchange |
| GET | `/api/instruments/{id}/moving-average/` | Get 20-day moving average |

### Query Parameters

**Portfolio Value**

```text
GET /api/portfolios/{id}/value/?as_of=2026-11-25
```

**Realized P&L**

```text
GET /api/portfolios/{id}/realized-pnl/?start_date=2026-11-01&end_date=2026-11-25
```

**Top Movers**

```text
GET /api/exchanges/{id}/top-movers/?start_date=2026-11-01&end_date=2026-11-25
```

## CSV Price Import

Daily stock prices can be imported using the Django management command.

```bash
python manage.py load_prices sample_prices.csv
```

The CSV file requires these columns:

- `exchange_code`
- `ticker`
- `trading_date`
- `open`
- `high`
- `low`
- `close`
- `volume`

The import command:

- Processes records in batches.
- Uses bulk upsert to insert new records and update existing records.
- Avoids duplicate daily price records.
- Skips invalid rows without stopping the entire CSV processing.
- Generates an error report containing invalid rows.

The error report is generated next to the input CSV file with the suffix `_errors.csv`.

## Entity Relationship Diagram

```mermaid
erDiagram
    EXCHANGE ||--o{ INSTRUMENT : has
    EXCHANGE ||--o{ TRADING_CALENDAR : has
    INSTRUMENT ||--o{ DAILY_PRICE : has
    INSTRUMENT ||--o{ STOCK_SPLIT : has
    INSTRUMENT ||--o{ CASH_DIVIDEND : has
    USER ||--o{ PORTFOLIO : owns
    PORTFOLIO ||--o{ ORDER : contains
    INSTRUMENT ||--o{ ORDER : traded
    ORDER ||--o{ EXECUTION : has
    PORTFOLIO ||--o{ CASH_LEDGER : records
    EXECUTION ||--o| TAX_LOT : creates
    PORTFOLIO ||--o{ TAX_LOT : owns
    INSTRUMENT ||--o{ TAX_LOT : tracks
    EXECUTION ||--o{ LOT_CONSUMPTION : records
    TAX_LOT ||--o{ LOT_CONSUMPTION : consumed

    EXCHANGE {
        int id PK
        string code
        string name
        string timezone
        string currency
    }

    INSTRUMENT {
        int id PK
        int exchange_id FK
        string ticker
        string name
        string isin
        string sector
        boolean active
    }

    DAILY_PRICE {
        int id PK
        int instrument_id FK
        date trading_date
        decimal open
        decimal high
        decimal low
        decimal close
        decimal volume
    }

    PORTFOLIO {
        int id PK
        int user_id FK
        string base_currency
    }

    ORDER {
        int id PK
        int portfolio_id FK
        int instrument_id FK
        string side
        string order_type
        decimal quantity
        decimal limit_price
        string status
    }

    EXECUTION {
        int id PK
        int order_id FK
        decimal quantity
        decimal price
        string external_execution_id
        datetime executed_at
    }

    CASH_LEDGER {
        int id PK
        int portfolio_id FK
        string entry_type
        decimal amount
        datetime created_at
    }

    TAX_LOT {
        int id PK
        int buy_execution_id FK
        int portfolio_id FK
        int instrument_id FK
        decimal original_quantity
        decimal remaining_quantity
        decimal cost_per_share
    }

    LOT_CONSUMPTION {
        int id PK
        int execution_id FK
        int tax_lot_id FK
        decimal consumed_quantity
    }

    STOCK_SPLIT {
        int id PK
        int instrument_id FK
        date ex_date
        int ratio_from
        int ratio_to
    }

    CASH_DIVIDEND {
        int id PK
        int instrument_id FK
        date ex_date
        decimal dividend_per_share
    }

    TRADING_CALENDAR {
        int id PK
        int exchange_id FK
        date holiday_date
    }
```

## Design Decisions

### 1. Portfolio Positions

Portfolio holdings are calculated from TaxLot records instead of maintaining a separate positions table.

The remaining quantity of each tax lot is used to calculate current holdings and average cost.

### 2. Cash Balance

Cash balance is derived from the sum of CashLedger entries.

Deposits, withdrawals, buys, sells, dividends, and fees are recorded as separate ledger entries. The balance is not stored as an independently editable field.

### 3. FIFO Tax Lot Accounting

Every BUY execution creates a TaxLot.

When shares are sold, available tax lots are consumed in FIFO order (First In, First Out). Each consumption is recorded in LotConsumption to support realized P&L calculations.

### 4. Order Execution and Concurrency

Order placement and execution operations use database transactions and row-level locking with `select_for_update()`.

This helps prevent concurrent requests from using the same available cash or shares.

### 5. Idempotency

The `external_execution_id` field has a conditional unique constraint for non-null values.

This prevents duplicate external execution records.

### 6. Corporate Actions

Stock splits adjust the quantity and cost per share of applicable tax lots without changing their total cost basis.

Cash dividends generate ledger entries for portfolios holding the applicable instrument on the ex-date.

### 7. Database Constraints

Database-level unique and check constraints enforce data integrity wherever applicable, including:

- Unique ticker within an exchange
- Globally unique ISIN
- One daily price per instrument per trading date
- Valid OHLC values
- Non-negative volume
- Valid order quantities and limit prices
- Valid execution quantities and prices
- Unique external execution identifiers where provided

## Indexes and Query Optimization

Django automatically creates indexes for ForeignKey fields.

The project also uses unique and conditional unique constraints to maintain data integrity and support lookups.

| Model | Fields | Purpose |
|---|---|---|
| Instrument | `ticker`, `exchange` | Unique ticker within an exchange |
| Instrument | `isin` | Globally unique instrument identification |
| DailyPrice | `instrument`, `trading_date` | Unique daily price per instrument |
| TradingCalendar | `exchange`, `holiday_date` | Unique holiday per exchange |
| Execution | `external_execution_id` | Prevent duplicate external execution IDs |
| TaxLot | `buy_execution` | One tax lot per buy execution |
| LotConsumption | `execution`, `tax_lot` | Prevent duplicate consumption records for the same execution and tax lot |

Reporting queries use database aggregation, subqueries, and window functions to avoid N+1 query patterns.

### Index Design Trade-offs

Indexes improve lookup and reporting query performance but require additional storage and increase the cost of insert and update operations.

Unique constraints are used where required to maintain data integrity. ForeignKey indexes support joins and related-object lookups.

Additional indexes should be introduced only when query patterns justify their storage and write-performance overhead.

## Running Tests

Run the project test suite:

```bash
python manage.py test
```

The test suite covers order placement, concurrent order handling, executions, FIFO tax lot consumption, corporate actions, and reporting API calculations.

## Scaling Considerations

If the system grows to handle 10 years of historical data and 5,000 instruments, the following improvements can be considered:

### 1. Database Partitioning

Partition the DailyPrice table by trading date, for example using yearly partitions, to improve historical price queries and simplify data management.

### 2. Archiving Historical Data

Older historical price records can be archived to reduce the size of frequently accessed data.

### 3. Database Read Replicas

Read replicas can handle reporting and historical data queries, reducing the workload on the primary database.

### 4. Query Optimization

Use database indexes, aggregation, subqueries, and window functions to improve reporting query performance.

### 5. Bulk Data Import

Use batched bulk inserts and upserts for large CSV imports instead of inserting records individually.

### 6. Caching

Frequently requested reporting results can be cached where appropriate to reduce repeated database queries.

## Future Improvements

With additional development time, the following areas could be improved:

- Benchmark CSV import performance with approximately one million rows.
- Evaluate database query performance with larger datasets.
- Add further automated tests for edge cases.
