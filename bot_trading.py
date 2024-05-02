import json
import time
from kucoin.client import Market
from kucoin.client import Trade
from kucoin.client import User
import pandas as pd
import talib
import requests
import datetime
import csv

global check_venta, check_compra, available_BTC, opening_time, ordersell, orderbuy, price

url = 'https://api.kucoin.com'

# Load the KuCoin API credentials from the config file
with open('config.json', 'r') as f:
    config = json.load(f)

api_key = config['api_key']
api_secret = config['api_secret']
api_passphrase = config['api_passphrase']

# Conexion
market = Market(url=url)

# Connect to the KuCoin API
trade = Trade(api_key, api_secret, api_passphrase, is_sandbox=False)
user = User(api_key, api_secret, api_passphrase, is_sandbox=False)

# Define the trading symbol and time interval
symbol = 'BTC-USDT'
interval = '1hour'
sma_periodo = 20
ema_periodo = 21

# Define the order variables
buyOpen = False
sellOpen = False
limitCompra = False
limitVenta = False
buySignal = False
sellSignal = False

# Archivo csv
csv_path = 'orders.csv'

# Definir el encabezado
header = ['Order ID', 'Fecha Open', 'Ticker', 'Order Type', 'Order Size', 'Price Open', 'Price Closed', 'Comision',
          'Fecha Cierre',
          'Profit/Loss']

# Esta creado o no el csv
try:
    with open(csv_path, 'r') as f:
        pass
except FileNotFoundError:
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(header)


# Definir la funcion que añade al csv
def add_order_to_csv(order_id, opening_time, ticker, order_type, order_size, price_open, price_closed, comision,
                     closing_time,
                     profit_loss):
    with open(csv_path, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(
            [order_id, opening_time, ticker, order_type, order_size, price_open, price_closed, comision, closing_time,
             profit_loss])

vela_duration = 3600
while True:
    try:
        accounts = user.get_account_list()
        for account in accounts:
            if account['id'] == '644c128b585d420001f6fa02':
                available_BTC = float(account['available'])
                print('_' * 100)
                print(f'Balance en BTC de: {available_BTC} BTC')
                print('_' * 100)
            if account['id'] == '644c11cf333b69000107406e':
                available_USDT = float(account['available'])
                print('_' * 100)
                print(f'Balance en USDT de: {available_USDT} USDT')
                print('_' * 100)

        # Get historical candlestick data from the KuCoin API
        candles = market.get_kline(symbol, interval)

        # Convert the candlestick data to a pandas DataFrame
        df = pd.DataFrame(candles, columns=['time', 'open', 'close', 'high', 'low', 'volume', 'turnover'])
        # Set the DataFrame index to the candlestick time
        df['time'] = pd.to_datetime(df['time'].astype(float), unit='s')

        df.set_index('time', inplace=True)
        df = df[::-1]  # Reverse the DataFrame
        df = df.astype(float)

        # Define the strategy indicators

        df['sma'] = talib.SMA(df['close'], sma_periodo)
        df['ema'] = talib.EMA(df['close'], ema_periodo)

        df['bmsb_mayor'] = df['sma'].where(df['sma'] > df['ema'], other=df['ema'])
        df['bmsb_menor'] = df['ema'].where(df['sma'] >= df['ema'], other=df['sma'])

        last_close = df['close'].iloc[-2]
        bmsb_mayor = df['bmsb_mayor'].iloc[-2]
        bmsb_menor = df['bmsb_menor'].iloc[-2]
        previous_close = df['close'].iloc[-3]
        previous_bmsb_mayor = df['bmsb_mayor'].iloc[-3]
        previous_bmsb_menor = df['bmsb_menor'].iloc[-3]
        ticker = market.get_ticker(symbol)
        price_actual = float(ticker['price'])
        fecha_actual = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if (last_close > bmsb_mayor) and (previous_close < previous_bmsb_mayor):
            print('_' * 100)
            print(
                f'El precio de cierre ha cruzado de abajo hacia arriba la BUll Market Support Band\nEsto es una oportunidad de Compra a las {fecha_actual}')
            print(
                f"precio actual: {price_actual}, precio de cierre: {last_close}, valores de la banda a eliminar:{bmsb_menor},{bmsb_mayor} ")
            print('_' * 100)
            buySignal = True
            sellSignal = False
        elif (last_close < bmsb_menor) and (previous_close > previous_bmsb_menor):
            print('_' * 100)
            print(
                f"El precio de cierre ha cruzado de arriba hacia abajo la BUll Market Support Band\nEsto es una oportunidad de Venta alas {fecha_actual}")
            print(
                f"precio actual: {price_actual}, precio de cierre: {last_close}, valores de la banda a eliminar:{bmsb_menor},{bmsb_mayor}")
            print('_' * 100)
            sellSignal = True
            buySignal = False
        else:
            print('_' * 100)
            print(f"El precio de cierre no ha cruzado la BUll Market Support Band y son las {fecha_actual}")
            print(
                f"precio actual: {price_actual}, precio de cierre: {last_close}, valores de la banda a eliminar:{bmsb_menor},{bmsb_mayor}")
            print('_' * 100)
            buySignal = False
            sellSignal = False

        size_to = round((available_BTC * 30 / 100), 6)

        if buySignal:
            if sellOpen:
                try:
                    ordersellcancelled = trade.get_order_details(check_venta)
                    # Datos a extraer de orden de cancelelada de venta
                    size = ordersellcancelled['size']
                    fee_venta = float(ordersellcancelled['fee'])

                    order_recompra = trade.create_market_order(symbol, 'buy', size=size)
                    # Datos a extraer de orden reventa
                    check_recompa = trade.get_order_details(orderId=order_recompra['orderId'])
                    order_id = check_recompa['id']
                    try:
                        ticker = market.get_ticker(symbol)
                        price_closed = float(ticker['price'])
                    except Exception as e:
                        price_closed = 'Error'
                    fee_compra = float(check_recompa['fee'])

                    # Fecha de cierre
                    closing_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f'Orden de venta cerrada con un size de: {size}')
                    comision = fee_compra + fee_venta

                    profit_or_loss = float(size) * (price - price_closed) - comision

                    sellOpen = False
                    limitVenta = False

                    add_order_to_csv(order_id=order_id, opening_time=opening_time, ticker=symbol, order_type='sell',
                                     order_size=size, price_open=price, price_closed=price_closed,
                                     comision=comision, closing_time=closing_time, profit_loss=profit_or_loss)

                except Exception as e:
                    print(f'Error placing order: {e}')

                try:
                    accounts = user.get_account_list()
                    for account in accounts:
                        if account['id'] == '644c128b585d420001f6fa02':
                            available_BTC = float(account['available'])
                            print(f"El valor disponible para la cuenta en BTC es: {available_BTC}")
                    size_to = round((available_BTC * 30 / 100), 6)
                    print(f'El size es de: {size_to}')
                    orderbuy = trade.create_market_order(symbol, 'buy', size=size_to)

                except Exception as e:
                    print(f'Error placing order: {e}')

                time.sleep(2)

                try:
                    check_compra_total = trade.get_order_details(orderId=orderbuy['orderId'])
                    check_compra = check_compra_total['id']
                    try:
                        ticker = market.get_ticker(symbol)
                        price = float(ticker['price'])
                    except Exception as e:
                        price = 'Error'
                    opening_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print('_' * 100)
                    print(f'El id de la compra es: {check_compra}, con un size de {size_to}, en el precio: {price}')
                    print(f'Fecha de apertura: {opening_time}')
                    print('_' * 100)
                    buyOpen = True
                    limitCompra = True
                except Exception as e:
                    print(f'Error while checking order status: {e}')




            elif not limitCompra:
                try:
                    orderbuy = trade.create_market_order(symbol, 'buy', size=size_to)
                except Exception as e:
                    print(f'Error placing order: {e}')

                time.sleep(2)

                try:
                    check_compra_total = trade.get_order_details(orderId=orderbuy['orderId'])
                    check_compra = check_compra_total['id']
                    try:
                        ticker = market.get_ticker(symbol)
                        price = float(ticker['price'])
                    except Exception as e:
                        price = 'Error'
                    opening_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print('_' * 100)
                    print(f'El id de la compra es: {check_compra}, con un size de {size_to}, en el precio: {price}')
                    print(f'Fecha de apertura: {opening_time}')
                    print('_' * 100)
                    buyOpen = True
                    limitCompra = True
                except Exception as e:
                    print(f'Error while checking order status: {e}')

        if sellSignal:
            if buyOpen:
                try:
                    orderbuycancelled = trade.get_order_details(check_compra)
                    # Datos a extraer de orden de cancelada de compra
                    size = orderbuycancelled['size']
                    fee_compra = float(orderbuycancelled['fee'])

                    order_reventa = trade.create_market_order(symbol, 'sell', size=size)
                    # Datos a extraer de orden de reventa
                    check_reventa = trade.get_order_details(orderId=order_reventa['orderId'])
                    order_id = check_reventa['id']
                    try:
                        ticker = market.get_ticker(symbol)
                        price_closed = float(ticker['price'])
                    except Exception as e:
                        price_closed = 'Error'
                    fee_venta = float(check_reventa['fee'])

                    # Fecha de cierre
                    closing_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f'Orden de compra cerrada con un size de: {size}')
                    comision = fee_compra + fee_venta

                    profit_or_loss = float(size) * (price_closed - price) - comision

                    buyOpen = False
                    limitCompra = False
                    add_order_to_csv(order_id=order_id, opening_time=opening_time, ticker=symbol, order_type='buy',
                                     order_size=size, price_open=price, price_closed=price_closed,
                                     comision=comision, closing_time=closing_time, profit_loss=profit_or_loss)


                except Exception as e:
                    print(f'Error placing order: {e}')
                try:
                    accounts = user.get_account_list()
                    for account in accounts:
                        if account['id'] == '644c128b585d420001f6fa02':
                            available_BTC = float(account['available'])
                            print(f"El valor disponible para la cuenta en BTC es: {available_BTC}")
                    size_to = round((available_BTC * 30 / 100), 6)
                    print(f'El size es de: {size_to}')
                    ordersell = trade.create_market_order(symbol, 'sell', size=size_to)
                except Exception as e:
                    print(f'Error placing order: {e}')

                time.sleep(2)

                try:
                    check_venta_total = trade.get_order_details(orderId=ordersell['orderId'])
                    check_venta = check_venta_total['id']
                    try:
                        ticker = market.get_ticker(symbol)
                        price = float(ticker['price'])
                    except Exception as e:
                        price = 'Error'
                    opening_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print('_' * 100)
                    print(f'El id de la venta es: {check_venta}, con un size de {size_to}, en el precio: {price}')
                    print(f'Fecha de apertura: {opening_time}')
                    print('_' * 100)
                    sellOpen = True
                    limitVenta = True
                except Exception as e:
                    print(f'Error while checking order status: {e}')



            elif not limitVenta:
                try:
                    ordersell = trade.create_market_order(symbol, 'sell', size=size_to)
                except Exception as e:
                    print(f'Error placing order: {e}')

                time.sleep(2)

                try:
                    check_venta_total = trade.get_order_details(orderId=ordersell['orderId'])
                    check_venta = check_venta_total['id']
                    try:
                        ticker = market.get_ticker(symbol)
                        price = float(ticker['price'])
                    except Exception as e:
                        price = 'Error'

                    opening_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print('_' * 100)
                    print(f'El id de la venta es: {check_venta}, con un size de {size_to}, en el precio: {price}')
                    print(f'Fecha de apertura: {opening_time}')
                    print('_' * 100)
                    sellOpen = True
                    limitVenta = True
                except Exception as e:
                    print(f'Error while checking order status: {e}')


    except Exception as e:
        print(f'Error:{e}')

    # Lista de caracteres del símbolo de carga
    carga = ["/", "-", "\\", "|", "_"]

    # Inicializa un contador para alternar entre los caracteres del símbolo de carga
    contador = 0

    # Imprime el símbolo de carga durante 60 segundos
    # Calcula el tiempo actual en segundos desde el epoch
    current_time = time.time()

    # Calcula el tiempo de la próxima vela redondeando al múltiplo de vela_duration
    next_candle_time = int(current_time / vela_duration) * vela_duration + vela_duration

    # Calcula cuánto tiempo falta para la próxima vela
    sleep_time = int((next_candle_time - current_time)/10)
    print(f'Tiempo de espera inicial: ', sleep_time * 10)

    for i in range(sleep_time):
        # Imprime el carácter actual del símbolo de carga
        try:
            ticker = market.get_ticker(symbol)
            price_actual = float(ticker['price'])
        except Exception as e:
            price_actual = 'Error'

        current_time = time.time()

        # Calcula el tiempo de la próxima vela redondeando al múltiplo de vela_duration
        next_candle_time = int(current_time / vela_duration) * vela_duration + vela_duration

        # Calcula cuánto tiempo falta para la próxima vela
        time_restante = int(next_candle_time - current_time)
        # Imprime el carácter actual del símbolo de carga
        print(f'El precio actual es: ', price_actual, 'faltando ', time_restante, 's', '...', carga[contador], end="\r")
        # print(carga[contador], end="\r")

        # Actualiza el contador para alternar al siguiente carácter del símbolo de carga
        contador = (contador + 1) % len(carga)

        # Pausa durante un segundo antes de imprimir el siguiente carácter del símbolo de carga
        time.sleep(10)
    time.sleep(1)
