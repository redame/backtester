import datetime as dt
from utils.data_utils.futures_utils import get_contract_specs

class FuturesContract(object):
    def __init__(self, symbol, exp_year=None, exp_month=None, continuous=False):
        self.symbol = symbol
        self.exp_year = exp_year if exp_year is not None else dt.datetime.now().year
        self.exp_month = exp_month if exp_month is not None else dt.datetime.now().month

        specs = get_contract_specs(self.symbol)
        self.name = specs['Name']
        self.exchange = specs['Exchange']
        self.tick_value = specs['Tick Value']
        self.contract_size = specs['Contract Size']
        self.active = specs['active']
        self.deliver_months = specs['Delivery Months']
        self.units = specs['Units']
        self.currency = specs['Currency']
        self.trading_times = specs['Trading Times']
        self.min_tick_value =  specs['Minimum Tick Value']
        self.full_point_value = specs['Full Point Value']
        self.terminal_point_value = ['Terminal Point Value']