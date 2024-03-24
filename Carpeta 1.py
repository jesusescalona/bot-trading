import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
import datetime as dt

# Configura tus claves de API de Binance aquí
api_key = "HG6MAJDlnCS3qU2qq2mXx7f9nGCHIuM4hxkZoJZ60jkqKeOZCAcd2QZxPdnNkYQD"
api_secret = "WqKShSrhsX9B6MAaavkouXqcZenhjq38wJG4dEGe0zOMLP7VpdoMMussyhPYWTAC"

client = Client(api_key, api_secret)

start_str = "1 Sep, 2017"
start_ts = int(dt.datetime.strptime(start_str, "%d %b, %Y").timestamp() * 1000)

usdt_pairs=['BTCUSDT','ETHUSDT']

for pair in usdt_pairs:
    print(f"Descargando datos históricos para {pair}")
    klines = []

    # Inicializar el timestamp de inicio para la descarga de datos
    start_ts_temp = start_ts

    while True:
        try:
            # Obtener los datos históricos desde start_ts_temp hasta el momento actual
            temp_data = client.get_historical_klines(pair, Client.KLINE_INTERVAL_4HOUR, start_ts_temp)
            if not temp_data:
                break
            klines += temp_data

            # Actualizar el timestamp de inicio para la siguiente petición
            start_ts_temp = temp_data[-1][0] + 1

        except BinanceAPIException as e:
            print(f"Error obtenido de Binance API: {e}")
            break
        except Exception as e:
            print(f"Error inesperado: {e}")
            break
 
    # Crear DataFrame con los datos descargados
    df = pd.DataFrame(klines, columns=['Open time', 'Open', 'High', 'Low', 'Close', 'Volume',
                                       'Close time', 'Quote asset volume', 'Number of trades',
                                       'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])

    # Convertir el tiempo de apertura de milisegundos a fecha legible
    df['Open time'] = pd.to_datetime(df['Open time'], unit='ms')

    # Guardar los datos en un archivo CSV
    df.to_csv(f"{pair}_data.csv", index=False)
    print(f"Datos históricos para {pair} guardados en {pair}_data.csv")

print("Descarga completada.")
