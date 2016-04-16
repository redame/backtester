import datetime as dt
from ib.ext.ContractDetails import ContractDetails
from ib_live.ib_utils import create_ib_futures_contract
from utils.futures_utils import get_contract_specs, build_contract

class FuturesContract(object):
    def __init__(self, base_symbol, exp_year=None, exp_month=None, continuous=False):
        self.base_symbol = base_symbol
        self.symbol = build_contract(base_symbol, exp_year, exp_month)
        self.exp_year = exp_year if exp_year is not None else dt.datetime.now().year
        self.exp_month = exp_month if exp_month is not None else dt.datetime.now().month

        specs = get_contract_specs(self.base_symbol)
        self.name = specs['Name']
        self.exchange = specs['Exchange']
        self.tick_value = specs['Tick Value']
        self.contract_size = specs['Contract Size']
        self.active = specs['Active']
        self.deliver_months = specs['Delivery Months']
        self.units = specs['Units']
        self.currency = specs['Currency']
        self.trading_times = specs['Trading Times']
        self.min_tick_value =  specs['Minimum Tick Value']
        self.full_point_value = specs['Full Point Value']
        self.terminal_point_value = ['Terminal Point Value']

        self.ib_contract = create_ib_futures_contract(self.base_symbol,
                                                      exp_month=self.exp_month, exp_year=self.exp_year,
                                                      exchange=self.exchange, currency=self.currency)