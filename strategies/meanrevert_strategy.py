import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)
# print os.getcwd()
import tabulate
import json
import pandas as pd
import numpy as np
import datetime as dt
import matplotlib.pyplot as plt
import analytics.tears as tears
from IPython.display import display
from Queue import Queue
from trading.strategy import Strategy
import analytics.plotting as plot
from analytics.plotting import plot_holdings

from backtest.data import BacktestDataHandler
from backtest.execution import ExecutionHandler
from backtest.backtest import CMEBacktest

from plotting.plot import plot_backtest, FIGS_DIR

NOT_UPDATING_FEATURES = False

BACKTEST_NAME = None

RUN_TIME = dt.datetime.now()


# TODO - plot the entry and exit thresholds plus the true_price vs mid_price

class MeanrevertStrategy(Strategy):

    def initialize(self,  contract_multiplier={}, transaction_costs={}, slippage=0, starting_cash=100000, granularity=1,
                   min_hold_time=dt.timedelta(minutes=15), max_hold_time=dt.timedelta(hours=2), start_date=None, end_date=None,
                   start_time=dt.time(hour=0), closing_time=dt.time(hour=23, minute=59), order_qty=1):

        self.symbols = self.bars.symbols
        self.contract_multiplier = contract_multiplier
        self.transaction_costs = transaction_costs
        self.slippage = slippage
        self.starting_cash = starting_cash
        self.min_hold_time = min_hold_time
        self.max_hold_time = max_hold_time
        self.start_date = start_date
        self.end_date = end_date
        self.start_time = start_time
        self.closing_time = closing_time
        self.order_qty = order_qty

        self.retrain_period = 0
        self.last_order_time = {sym: None for sym in self.symbols}
        self.pos = {sym: 0 for sym in self.symbols}
        self.implied_pos = {sym: 0 for sym in self.symbols}
        self.entry_price = {sym: None for sym in self.symbols}
        self.cash = self.starting_cash
        self.pnl = []
        self.daily_pnl = [self.starting_cash]
        self.price_series = {sym: [] for sym in self.symbols}
        self.spread = {sym: [] for sym in self.symbols}
        self.time_series = []
        self.orders = {sym: [] for sym in self.symbols}
        self.kill_till = {sym: None for sym in self.symbols}
        self.total_signals = {sym: [] for sym in self.symbols}
        self.total_probs = {sym: [] for sym in self.symbols}
        self.total_pnl = []
        self.total_price_series = {sym: [] for sym in self.symbols}
        self.total_spread = {sym: [] for sym in self.symbols}
        self.total_time_series = []
        self.total_orders = {sym: [] for sym in self.symbols}
        self.total_positions = {sym: [] for sym in self.symbols}
        self.kill_till = {sym: None for sym in self.symbols}
        self.signals = {sym: [] for sym in self.symbols}
        self.probs = {sym: [] for sym in self.symbols}
        self.true_probs = {sym: [] for sym in self.symbols}
        self.positions = {sym: [] for sym in self.symbols}
        self.cur_time = None
        self.granularity = granularity

        self.alphas = []
        self.thetas = [0]

        self.theta_lockdown = 0
        self.jump_lockdown = 0

        self.HL = int(7680/2 / self.granularity)
        self.alpha = 1-np.exp(np.log(0.5)/self.HL)
        self.std_window = 4*self.HL

        self.true_price = []

        self.stds = []

        self.lockdown = [0]

        self.std_queue = []


    def new_tick(self, market_event):

        self.cur_time = market_event.datetime

        bar = self.bars.get_latest_bars(n=1)

        self.update_metrics()

        for sym in self.symbols:
            try:
                if len(self.price_series[sym]) > 0:
                    py = self.true_price[sym][-1]
                    pt = self.thetas[-1]
                    a = self.alpha
                    x = bar['mid_price']
                    self.lockdown.append(0)
                    if self.theta_lockdown > 1:
                        self.theta_lockdown -= 1
                        self.lockdown[-1] = 1
                        a = self.alpha + max(0, abs(pt - 0.1))
                    if self.jump_lockdown > 1:
                        self.jump_lockdown -= 1
                        self.lockdown[-1] = 1
                        a = 1
                    self.true_price.append(a * x + (1-a) * py)
                    self.thetas.append(a * (self.true_price[-1]-x) + (1-a) * pt)
                    if abs(self.true_price[-1]-x) > 1.5 and self.jump_lockdown == 0:
                        self.jump_lockdown = int(60 * 60 / self.granularity)
                    if abs(self.thetas[-1]) > 0.15 and self.jump_lockdown == 0:
                        self.trade_lockdown = int(60 * 60 / self.granularity)
                else:
                    self.true_price.append(bar['mid_price'])

                # omit jump lockdowns from the std measurement
                if a != 1:
                    self.std_queue.append(bar['mid_price'])
                    if len(self.std_window) > self.std_window:
                        self.std_window = self.std_window[:-1]

                self.stds.append(np.std(self.std_queue))

                pos = self.implied_pos[sym]

                # close out
                if self.cur_time.time() >= self.closing_time and pos != 0:
                    self.order(sym, -pos)
                    self.implied_pos[sym] += -pos
                    self.last_order_time[sym] = self.cur_time

                # do not trade before start time or after close
                if self.cur_time < self.start_time or self.cur_time.time() > self.closing_time:
                    continue

                uthresh = self.true_price[-1] + max(0.15, self.stds[-1])
                dthresh = self.true_price[-1] - max(0.15, self.stds[-1])

                uexthresh = self.true_price[-1] + max(0.075, self.stds[-1]/2.)
                dexthresh = self.true_price[-1] - max(0.075, self.stds[-1]/2.)

                signal = bar['mid_price'] - self.true_price

                if self.jump_lockdown > 0 or self.theta_lockdown > 0:
                    self.order(sym, -pos)
                elif signal > uthresh:
                    qty = (1 + abs(signal-uthresh)/0.1/self.stds[-1])
                    if abs(qty) > abs(pos) and np.sign(qty) == np.sign(pos):
                        self.order(sym, qty-pos)
                        self.implied_pos[sym] += qty-pos
                elif signal < dthresh:
                    qty = -1 * (1 + abs(dthresh-signal)/0.1/self.stds[-1])
                    if abs(qty) > abs(pos) and np.sign(qty) == np.sign(pos):
                        self.order(sym, qty-pos)
                        self.implied_pos[sym] += qty-pos
                elif pos > 0 and signal < uexthresh:
                    self.order(sym, -pos)
                    self.implied_pos[sym] += -pos
                elif pos < 0 and signal > dexthresh:
                    self.order(sym, -pos)
                    self.implied_pos[sym] += -pos

                #elif pos != 0:
                #    self.check_stop_loss(sym)

                self.signals[sym].append(signal)
                self.probs[sym].append(self.thetas[-1])
                self.positions[sym].append(pos)

            except Exception as e:
                self.signals[sym].append(0)
                self.probs[sym].append(0)
                self.positions[sym].append(0)
                print e

        if len(self.signals[self.symbols[0]]) != len(self.time_series):
            raise Exception("FUCK YOU")

    def new_day(self, newday_event):
        print "new day", newday_event.next_date

        if len(self.time_series) == 0:
            return

        self.daily_pnl.append(self.pnl[-1])

        self.total_time_series += self.time_series
        self.total_pnl += self.pnl
        for sym in self.symbols:
            self.total_price_series[sym] += self.price_series[sym]
            self.total_orders[sym] += self.orders[sym]
            self.total_signals[sym] += self.signals[sym]
            self.total_probs[sym] += self.probs[sym]
            self.total_positions[sym] += self.positions[sym]

        backtest_dir = os.path.join('backtests',
                            "_".join(self.symbols),
                            "{}_{}".format(self.start_date.strftime("%Y_%m_%d"), self.end_date.strftime("%Y_%m_%d")),
                            RUN_TIME.strftime("%Y_%m_%d_%h_%M_%s")
                            if BACKTEST_NAME is None else BACKTEST_NAME)

        plot_backtest(os.path.join(backtest_dir,
                                   'backtest_results_{}'.format(
                                       (dt.datetime.utcfromtimestamp(newday_event.prev_date.tolist()/1e9)).strftime("%Y_%m_%d"))),
                      self.time_series,
                      self.price_series[self.symbols[0]],
                      self.pnl,
                      self.orders[self.symbols[0]],
                      self.signals[self.symbols[0]],
                      self.probs[self.symbols[0]],
                      self.positions[self.symbols[0]])

        self.time_series = []
        self.pnl = []
        for sym in self.symbols:
            self.price_series[sym] = []
            self.orders[sym] = []
            self.signals[sym] = []
            self.probs[sym] = []
            self.true_probs[sym] = []
            self.positions[sym] = []

    def new_fill(self, fill_event):
        sym = fill_event.symbol
        self.pos[sym] += fill_event.quantity
        if self.pos[sym] == 0:
            self.entry_price[sym] = None
        else:
            self.entry_price[sym] = fill_event.fill_cost / float(fill_event.quantity)
        self.cash -= self.contract_multiplier[sym] * fill_event.fill_cost + \
                     self.transaction_costs[sym] * abs(fill_event.quantity) + \
                     self.contract_multiplier[sym] * abs(fill_event.quantity) * self.slippage
        self.orders[sym].append((self.cur_time, fill_event.quantity, self.pos[sym], abs(fill_event.fill_cost / float(fill_event.quantity))))

    def finished(self):

        log_returns = np.diff(np.log(self.daily_pnl))
        sharpe = np.sqrt(252) * (np.mean(log_returns) / np.std(log_returns))
        max_drawdown = np.min(
            [np.min(np.array(self.daily_pnl)[i:] - np.array(self.daily_pnl)[:-i])
             for i in xrange(1, len(self.daily_pnl))])
        print "Sharpe ratio = {}".format(sharpe)
        print "Max drawdown = {}".format(max_drawdown)

        info_fpath = self._build_backtest_fpath('info.json')

        with open(info_fpath, 'w') as f:
            info = {
                'pred_windows': self.pred_windows,
                'retrain_frequency': str(self.retrain_frequency),
                'prob_thresh': self.prob_thresh,
                'closing_time': str(self.closing_time),
                'min_hold': str(self.min_hold_time),
                'max_hold': str(self.max_hold_time),
                'slippage': self.slippage,
                'standardize': self.standardize,
                'starting_cash': self.starting_cash,
                'sharpe': sharpe,
                'pnl': self.total_pnl[-1],
                'daily_pnl': self.daily_pnl,
                'max_drawdown': max_drawdown,
                'orders': sum([len(self.total_orders[s]) for s in self.symbols]),
                'longs': sum([len(filter(lambda x: (x[1] > 0) and (x[2] != 0), self.total_orders[s])) for s in self.symbols]),
                'shorts': sum([len(filter(lambda x: (x[1] < 0) and (x[2] != 0), self.total_orders[s])) for s in self.symbols])
            }
            f.write(json.dumps(info))

        backtest_dir = os.path.join('backtests',
                                    "_".join(self.symbols),
                                    "{}_{}".format(self.start_date.strftime("%Y_%m_%d"), self.end_date.strftime("%Y_%m_%d")),
                                    RUN_TIME.strftime("%Y_%m_%d_%h_%M_%s")
                                    if BACKTEST_NAME is None else BACKTEST_NAME)

        plot_backtest(os.path.join(backtest_dir, 'backtest_results_full'),
                      self.total_time_series,
                      self.total_price_series[self.symbols[0]],
                      self.total_pnl,
                      self.total_orders[self.symbols[0]],
                      self.total_signals[self.symbols[0]],
                      self.total_probs[self.symbols[0]],
                      self.total_positions[self.symbols[0]])

    def update_metrics(self):
        last_bar = self.bars.get_latest_bars(n=1)
        pnl_ = self.cash + sum([self.pos[sym] * self.contract_multiplier[sym] * last_bar['mid_price'] for sym in self.symbols])
        self.pnl.append(pnl_)
        self.time_series.append(self.cur_time)
        for sym in self.symbols:
            self.price_series[sym].append(last_bar['mid_price'])
            self.spread[sym].append(last_bar['level_1_price_sell'] - last_bar['level_1_price_buy'])

    def check_stop_loss(self, sym):
        pos = self.implied_pos[sym]
        # TODO - improve stop-loss handling
        """
        if (pos != 0 and len(self.pnl) > 600 and self.pnl[-600] - self.pnl[-1] > 250) and \
                (self.kill_till[sym] is None or self.kill_till[sym] <= self.cur_time):
            self.order(sym, -pos)
            self.order(sym, -pos)
            self.implied_pos[sym] += -2*pos
            self.kill_till[sym] = self.cur_time + dt.timedelta(minutes=15)
            self.last_order_time[sym] = self.cur_time
        """

    def _build_backtest_fpath(self, fname):
        backtest_dir = os.path.join('backtests',
                                    "_".join(self.symbols),
                                    "{}_{}".format(self.start_date.strftime("%Y_%m_%d"), self.end_date.strftime("%Y_%m_%d")),
                                    RUN_TIME.strftime("%Y_%m_%d_%h_%M_%s")
                                    if BACKTEST_NAME is None else BACKTEST_NAME)

        fpath = os.path.join(FIGS_DIR, backtest_dir, fname)

        if not os.path.exists(os.path.dirname(fpath)):
            os.makedirs(os.path.dirname(fpath))

        return fpath



def run_backtest():
    # parameters
    #global BACKTEST_NAME
    #if len(sys.argv) > 1:
    #    BACKTEST_NAME = sys.argv[1]
    pred_windows = [7680]
    #prob_thresh = (0.2, 0.8)
    prob_thresh = (0.4, 0.6)
    order_qty = 1
    retrain_frequency = dt.timedelta(days=77)
    start_time = dt.time(hour=5)
    closing_time = dt.time(hour=18)
    standardize = False
    take_profit_threshold = None
    #start_date = dt.datetime(year=2015, month=11, day=1)
    #end_date = dt.datetime(year=2015, month=11, day=30)
    #symbols = ['GCZ5']
    start_date = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
    end_date = dt.datetime.strptime(sys.argv[3], "%Y-%m-%d")
    symbols = [sys.argv[1]]
    contract_multiplier = {
        symbols[0]: 1000
    }
    transaction_costs = {
        symbols[0]: 1.45
    }


    events = Queue()

    bars = BacktestDataHandler(events, symbols, start_date, end_date,
                                    second_bars=True,
                                    standardize=standardize,
                                    bar_length=60,
                                    start_time=dt.timedelta(hours=3),
                                    end_time=dt.timedelta(hours=22))

    strategy = MeanrevertStrategy(bars, events,
                                  contract_multiplier=contract_multiplier,
                                  transaction_costs=transaction_costs,
                                  slippage=0.00,
                                  granularity=60,
                                  order_qty=order_qty,
                                  min_hold_time=dt.timedelta(minutes=5),
                                  max_hold_time=dt.timedelta(hours=12),
                                  start_date=start_date,
                                  end_date=end_date,
                                  start_time=start_time,
                                  closing_time=closing_time)

    execution = ExecutionHandler(symbols, events, second_bars=True)

    backtest = CMEBacktest(events, bars, strategy, execution, start_date, end_date)

    backtest.run()



if __name__ == "__main__":
    run_backtest()