from rest_framework import serializers
from .models import Order, Execution


class OrderSerializer(serializers.ModelSerializer):

    class Meta:
        model = Order
        fields = ["id", "portfolio", "instrument", "side", "order_type", "quantity", "limit_price", "status",]
        read_only_fields = ["id", "status"]
        



class ExecutionSerializer(serializers.ModelSerializer):

    class Meta:
        model = Execution
        fields = ["id", "quantity", "price", "external_execution_id", "executed_at",]
        read_only_fields = ["id", "executed_at"]
        
        
class HoldingSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField()
    ticker = serializers.CharField()
    instrument_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=15, decimal_places=4)
    average_cost = serializers.DecimalField( max_digits=15, decimal_places=4)
    latest_price = serializers.DecimalField( max_digits=15, decimal_places=4)
    market_value = serializers.DecimalField( max_digits=20, decimal_places=4)
    unrealized_pnl = serializers.DecimalField( max_digits=20, decimal_places=4)
    portfolio_percentage = serializers.DecimalField( max_digits=15, decimal_places=4)
    
    
class PortfolioValueSerializer(serializers.Serializer):
    portfolio_id = serializers.IntegerField()
    as_of = serializers.DateField()
    cash_balance = serializers.DecimalField(max_digits=20, decimal_places=4)
    stock_value = serializers.DecimalField( max_digits=20, decimal_places=4)
    total_value = serializers.DecimalField( max_digits=20, decimal_places=4)
    
    
class RealizedPnLSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField()
    ticker = serializers.CharField()
    instrument_name = serializers.CharField()
    realized_pnl = serializers.DecimalField(max_digits=20, decimal_places=4)
    
    
    
class TopMoverSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField()
    ticker = serializers.CharField()
    instrument_name = serializers.CharField()
    start_price = serializers.DecimalField(max_digits=15, decimal_places=4)
    end_price = serializers.DecimalField(max_digits=15, decimal_places=4)
    percentage_change = serializers.DecimalField( max_digits=15, decimal_places=4)
    
    
class MovingAverageSerializer(serializers.Serializer):
    trading_date = serializers.DateField()
    close = serializers.DecimalField(max_digits=15, decimal_places=4)
    moving_average = serializers.DecimalField(max_digits=15, decimal_places=4)