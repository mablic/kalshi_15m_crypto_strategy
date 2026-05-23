import copy

from lib.trade_log import log_generated


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

    def reduce_remaining_quantity(self, filled_quantity: float):
        self.remaining_quantity = float(self.remaining_quantity) - float(filled_quantity)

    def add_remaining_quantity(self, filled_quantity: float):
        self.remaining_quantity = float(self.remaining_quantity) + float(filled_quantity)

    def get_quantity_difference(self):
        return float(self.quantity) - float(self.remaining_quantity)


class TICKER_ORDER_BOOK:
    def __init__(self, ticker: str):
        self.open_buy_orders = None
        self.open_sell_orders = None
        self.to_be_trade_list = []
        self.ticker = ticker

    def add_to_buy_orders(self, order: ORDER):
        self.open_buy_orders = order
        self.to_be_trade_list.append(order)

    def add_to_sell_orders(self, order: ORDER):
        self.open_sell_orders = order
        self.to_be_trade_list.append(order)

    def is_in_trade(self):
        return self.open_buy_orders is not None or self.open_sell_orders is not None

    def check_trade_completed(self):
        if not self.open_buy_orders or not self.open_sell_orders:
            return False
        if self.open_buy_orders.remaining_quantity == 0 and self.open_sell_orders.remaining_quantity == 0:
            return True
        return False

    def get_to_be_trade_list(self):
        return self.to_be_trade_list

    def clear_to_be_trade_list(self):
        self.to_be_trade_list = []

    def order_decision(self, open_order: list[ORDER], filled_orders: list[ORDER]):
        open_buy_orders = []
        open_sell_orders = []
        filled_buy_orders = []
        filled_sell_orders = []
        for order in open_order or []:
            if order.action == 'buy':
                open_buy_orders.append(order)
            elif order.action == 'sell':
                open_sell_orders.append(order)
        for filled_order in filled_orders or []:
            if filled_order.action == 'buy':
                filled_buy_orders.append(filled_order)
            elif filled_order.action == 'sell':
                filled_sell_orders.append(filled_order)

        # only buy orders are open
        if not open_sell_orders:
            filled_qty = 0
            for filled_buy_order in filled_buy_orders:
                filled_qty += float(filled_buy_order.quantity)
            if filled_qty > 0:
                # open buy order
                order_book_qty = 0
                if open_buy_orders:
                    order_book_qty = float(open_buy_orders[0].quantity) - float(open_buy_orders[0].remaining_quantity)
                else:
                    # filled all buy orders
                    order_book_qty = float(self.open_buy_orders.quantity)
                self.open_buy_orders.remaining_quantity = float(order_book_qty)
                if order_book_qty != filled_qty:
                    log_generated(f"Order book quantity {order_book_qty} is not equal to filled quantity {filled_qty}")
                to_be_trade_order = copy.copy(self.open_buy_orders)
                to_be_trade_order.remaining_quantity = order_book_qty
                to_be_trade_order.quantity = order_book_qty
                to_be_trade_order.entry_price = to_be_trade_order.expected_exit_price
                to_be_trade_order.action = 'sell'
                to_be_trade_order.order_id = None
                self.add_to_sell_orders(to_be_trade_order)
        else:
            # with sell orders open, if not open buy order
            if not open_buy_orders:
                # open sell order
                order_book_qty = float(open_sell_orders[0].quantity)
                if self.open_sell_orders:
                    self.open_sell_orders.remaining_quantity = float(open_sell_orders[0].remaining_quantity)
                # only process if miss match to preventing over sell
                to_be_trade_order = copy.copy(open_sell_orders[0])
                to_be_trade_order.remaining_quantity = order_book_qty
                to_be_trade_order.quantity = order_book_qty
                to_be_trade_order.entry_price = to_be_trade_order.expected_exit_price
                to_be_trade_order.action = 'sell'
                self.add_to_sell_orders(to_be_trade_order)
            else:
                filled_buy_qty = 0
                filled_sell_qty = 0
                for filled_buy_order in filled_buy_orders:
                    filled_buy_qty += float(filled_buy_order.quantity)
                for filled_sell_order in filled_sell_orders:
                    filled_sell_qty += float(filled_sell_order.quantity)
                if filled_buy_qty == 0 and filled_sell_qty == 0:
                    return
                elif filled_buy_qty > 0 and filled_sell_qty == 0:
                    # reduce buy remaining quantity
                    self.open_buy_orders.remaining_quantity = float(self.open_buy_orders.remaining_quantity) - float(filled_buy_qty)
                    # create open sell order
                    to_be_trade_order = copy.copy(open_buy_orders[0])
                    to_be_trade_order.remaining_quantity = float(filled_buy_qty)
                    to_be_trade_order.quantity = float(filled_buy_qty)
                    to_be_trade_order.entry_price = to_be_trade_order.expected_exit_price
                    to_be_trade_order.action = 'sell'
                    to_be_trade_order.order_id = open_sell_orders[0].order_id if open_sell_orders[0].order_id is not None else None
                    self.add_to_sell_orders(to_be_trade_order)
                elif filled_buy_qty == 0 and filled_sell_qty > 0:
                    self.open_sell_orders.remaining_quantity = float(self.open_sell_orders.remaining_quantity) - float(filled_sell_qty)
                else:
                    # open buy filled and sell filled
                    self.open_buy_orders.remaining_quantity = float(self.open_buy_orders.remaining_quantity) - float(filled_buy_qty)
                    net_sell_qty = float(self.open_buy_orders.quantity) - float(self.open_buy_orders.remaining_quantity) - float(filled_sell_qty)
                    if net_sell_qty > 0:
                        # create open sell order
                        to_be_trade_order = copy.copy(open_buy_orders[0])
                        to_be_trade_order.remaining_quantity = float(net_sell_qty)
                        to_be_trade_order.quantity = float(net_sell_qty)
                        to_be_trade_order.entry_price = to_be_trade_order.expected_exit_price
                        to_be_trade_order.action = 'sell'
                        to_be_trade_order.order_id = None
                        self.add_to_sell_orders(to_be_trade_order)


class ORDER_BOOK_MANAGER:
    def __init__(self):
        self.order_book_managers = {}

    def check_ticker_in_order_book_manager(self, ticker: str):
        return ticker in self.order_book_managers.keys()

    def add_order_book_manager(self, ticker_order_book: TICKER_ORDER_BOOK):
        series = ticker_order_book.ticker.split('-')[1]
        for ticker in self.order_book_managers.keys():
            if ticker.split('-')[1] == series:
                del self.order_book_managers[ticker]
        self.order_book_managers[ticker_order_book.ticker] = ticker_order_book

    def get_order_book_manager(self, ticker: str):
        return self.order_book_managers[ticker]

    def clean_order_book_manager(self):
        self.order_book_managers.clear()