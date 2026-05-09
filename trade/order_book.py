


class ORDER:
    def __init__(self, 
        order_id: str, 
        ticker: str,
        symbol: str,
        order_date: str,
        order_type: str,
        order_execution_type: str,
        action: str,
        side: str,
        quantity: int,
        remaining_quantity: int,
        entry_price: float,
        expected_exit_price: float,
        price: float,
        created_at: str,
        last_updated_at: str,
        trade_type: str,
        fill_id: str = None,
    ):
        self.order_id = order_id
        self.ticker = ticker
        self.symbol = symbol
        self.order_date = order_date
        self.order_type = order_type
        self.order_execution_type = order_execution_type
        self.action = action
        self.side = side
        self.quantity = quantity
        self.remaining_quantity = remaining_quantity
        self.entry_price = round(float(entry_price), 2) if entry_price is not None else entry_price
        self.price = round(float(price), 2) if price is not None else price
        self.expected_exit_price = round(float(expected_exit_price), 2) if expected_exit_price is not None else expected_exit_price
        self.created_at = created_at
        self.last_updated_at = last_updated_at
        self.trade_type = trade_type
        self.fill_id = fill_id

class ORDER_MANAGER:
    def __init__(self):
        self.open_buy_orders = []
        self.open_sell_orders = []
        self.in_trade = False

    def add_to_buy_orders(self, order: ORDER):
        self.in_trade = True
        self.open_buy_orders.append(order)

    def add_to_sell_orders(self, order: ORDER):
        self.in_trade = True
        self.open_sell_orders.append(order)

    def remove_from_buy_orders(self, order: ORDER):
        self.open_buy_orders.remove(order)

    def remove_from_sell_orders(self, order: ORDER):
        self.open_sell_orders.remove(order)

    def check_buy_orders(self, ticker: str):
        for order in self.open_buy_orders:
            if order.ticker == ticker:
                return True
        return False

    def check_sell_orders(self, ticker: str):
        for order in self.open_sell_orders:
            if order.ticker == ticker:
                return True
        return False

    def check_in_trade(self):
        return self.in_trade
    
    def reset_in_trade(self):
        self.in_trade = False
        self.open_buy_orders = []
        self.open_sell_orders = []