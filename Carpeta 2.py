import backtrader as bt
import csv

# Crear una clase de estrategia
class DCAStrategy(bt.Strategy):
    params = (
        ('ma_period', 20),
        ('percent_threshold', 6),
        ('stake_percentage', 5),  # Este es el porcentaje del valor total a vender
    )

    def __init__(self):
        self.ma = bt.indicators.SimpleMovingAverage(
            self.data.close, period=self.params.ma_period)
        self.order = None
        # Abrir archivo CSV para escribir
        self.csvfile = open('operaciones.csv', 'w', newline='')
        self.csvwriter = csv.writer(self.csvfile, delimiter=',')
        # Escribir cabecera del CSV
        self.csvwriter.writerow(['Fecha', 'Tipo', 'Precio', 'Costo', 'Comisión', 'Valor de la Cartera', 'Valor de las Posiciones'])

    
    def log(self, txt, dt=None):
        ''' Logging function for this strategy'''
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()} {txt}')

    def notify_order(self, order):
        if order.status in [order.Completed]:
            tipo = 'COMPRA' if order.isbuy() else 'VENTA'
            fecha = self.datas[0].datetime.datetime(0).isoformat()
            precio = order.executed.price
            costo = order.executed.value
            comision = order.executed.comm

            # Obtener el valor total de la cartera (incluye efectivo + posiciones)
            total_value = self.broker.getvalue()
            # Obtener el efectivo disponible
            cash = self.broker.getcash()

            # Escribir en CSV
            self.csvwriter.writerow([fecha, tipo, precio, costo, comision, total_value])

            self.order = None


    def next(self):
        # Verificar si ya se ha realizado una compra y evitar comprar nuevamente sin una venta de por medio
        if self.data.close[0] < self.ma[0] * (1 - self.params.percent_threshold / 100):
            size = (self.broker.getvalue() * self.params.stake_percentage / 100) / self.data.close[0]
            self.order = self.buy(size=size)

        elif self.data.close[0] > self.ma[0] * (1 + self.params.percent_threshold / 100):
            total_value = self.broker.getvalue()
            sell_size = (total_value * self.params.stake_percentage / 100) / self.data.close[0]
            current_position = self.getposition().size
            sell_size = min(sell_size, current_position)

            if sell_size > 0:
                self.order = self.sell(size=sell_size)
    def stop(self):
        self.csvfile.close()

# Configuración inicial
cerebro = bt.Cerebro()
cerebro.addstrategy(DCAStrategy)
data = bt.feeds.GenericCSVData(
    dataname='BTCUSDT_data.csv',
    nullvalue=0.0,
    dtformat=('%Y-%m-%d %H:%M:%S'),  # Asegúrate de que este formato coincida con tu archivo CSV
    datetime=0,
    high=2,
    low=3,
    open=1,
    close=4,
    volume=5,
    openinterest=-1,
    timeframe=bt.TimeFrame.Minutes,
    compression=240  # Configura esto para que coincida con tu intervalo de tiempo (4 horas en este caso)
)
cerebro.adddata(data)
cerebro.broker.setcash(10000)
cerebro.broker.setcommission(commission=0.001)  # Comisión de 0.1%
print(f'Capital inicial: {cerebro.broker.getvalue()}')

# Añadir analizadores para drawdown y rentabilidad
cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')

results = cerebro.run()

final_value = cerebro.broker.getvalue()
print(f'Valor final de la cuenta: {round(final_value,2)}')

drawdown = results[0].analyzers.dd.get_analysis()
print(f'Drawdown máximo: {round(drawdown.max.drawdown,2)}%')

returns = results[0].analyzers.returns.get_analysis()
print(f'Rentabilidad: {round(((final_value/10000)-1)*100,2)}%')

# Imprimir el gráfico
cerebro.plot(style='candle')

