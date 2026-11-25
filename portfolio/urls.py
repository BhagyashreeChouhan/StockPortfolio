from django.urls import path
from .views import OrderCreateView, ExecutionCreateView, HoldingsView, PortfolioValueView, RealizedPnLView, TopMoversView, MovingAverageView

urlpatterns = [
    path("orders/", OrderCreateView.as_view(), name="create-order"),
    path("orders/<int:order_id>/executions/", ExecutionCreateView.as_view(), name="create-execution"),
    path("portfolios/<int:portfolio_id>/holdings/", HoldingsView.as_view(), name="portfolio-holdings"),
    path("portfolios/<int:portfolio_id>/value/", PortfolioValueView.as_view(), name="portfolio-value"),
    path("portfolios/<int:portfolio_id>/realized-pnl/", RealizedPnLView.as_view(), name="realized-pnl"),
    path("exchanges/<int:exchange_id>/top-movers/", TopMoversView.as_view(), name="top-movers"),
    path("instruments/<int:instrument_id>/moving-average/", MovingAverageView.as_view(), name="moving-average"),
]