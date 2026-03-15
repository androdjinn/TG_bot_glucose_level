import telebot
import mysql.connector
from datetime import datetime, timedelta
from mysql.connector import Error
import pandas as pd
import io
import os
from dotenv import load_dotenv
import logging
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Используем бэкенд без GUI для сохранения графиков в буфер

# Настройка логирования (ошибки и выше по умолчанию)
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()  # Загружаем переменные окружения из .env

# Конфигурация бота и БД из переменных окружения
TOKEN = os.getenv('BOT_TOKEN')
DB_CONFIG = {
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME')
}

bot = telebot.TeleBot(TOKEN)


def reconnect_db():
    """Попытка переподключения к базе данных и установка курсора.

    Используется при ошибках вставки/запросов, чтобы восстановить соединение.
    """
    global conn, cursor
    try:
        # Если старое соединение ещё открыто — закрываем его
        if conn.is_connected():
            cursor.close()
            conn.close()
        # Создаём новое соединение и курсор
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
    except Error as e:
        logging.error(f"Database reconnection error: {e}")
        raise


try:
    # Инициализация соединения при запуске скрипта
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    # Создаём таблицу, если её нет — конструкция безопасна для повторного вызова
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS records (
        user_id BIGINT,
        sugar_level FLOAT,
        insulin_dose FLOAT,
        bread_units INT,
        timestamp DATETIME,
        nocturnal TINYINT DEFAULT 0
    )
    ''')
    conn.commit()
except Error as e:
    logging.error(f"Database initialization error: {e}")
    exit(1)


# Словарь с временными данными пользователей между шагами диалога
user_data = {}


def show_menu(chat_id):
    """Показывает пользователю главное меню с кнопками действий.

    chat_id: id чата для отправки меню
    """
    try:
        markup = telebot.types.InlineKeyboardMarkup()
        # Кнопки для дневных/ночных записей и отчётов
        markup.add(telebot.types.InlineKeyboardButton("Внести дневной", callback_data='short'))
        markup.add(telebot.types.InlineKeyboardButton("Внести ночной", callback_data='long'))
        markup.add(telebot.types.InlineKeyboardButton("Отчет за 7 дней", callback_data='report_7'))
        markup.add(telebot.types.InlineKeyboardButton("Отчет на 45 дней", callback_data='report_45'))
        bot.send_message(chat_id, 'Выберите действие:', reply_markup=markup)
    except Exception as e:
        # Любые ошибки при отправке меню логируем
        logging.error(f"Menu sending error: {e}")


@bot.message_handler(commands=['start'])
def start(message):
    """Обработчик команды /start — показывает меню пользователю."""
    show_menu(message.chat.id)


@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    """Обработчик нажатий inline-кнопок.

    В зависимости от callback_data запускает соответствующий поток диалога
    или генерацию отчёта.
    """
    try:
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        if call.data == 'short':
            # Дневная запись — отмечаем nocturnal = 0
            timestamp = datetime.now()
            user_data[user_id] = {'timestamp': timestamp, 'nocturnal': 0}
            bot.send_message(chat_id, 'Введите показания сахара:')
            bot.register_next_step_handler(call.message, get_sugar, user_id)
        elif call.data == 'long':
            # Ночная запись — отмечаем nocturnal = 1
            timestamp = datetime.now()
            user_data[user_id] = {'timestamp': timestamp, 'nocturnal': 1}
            bot.send_message(chat_id, 'Введите показания сахара:')
            bot.register_next_step_handler(call.message, get_sugar, user_id)
        elif call.data == 'report_7':
            # Отчёт за 7 дней
            generate_report(chat_id, user_id, 7)
        elif call.data == 'report_45':
            # Отчёт за 45 дней
            generate_report(chat_id, user_id, 45)
    except Exception as e:
        logging.error(f"Callback error: {e}")
        bot.send_message(chat_id, 'Произошла ошибка. Попробуйте позже.')


def get_sugar(message, user_id):
    """Шаг диалога: ввод уровня сахара. Сохраняет значение и запрашивает инсулин."""
    try:
        if user_id not in user_data:
            # Если нет данных с предыдущего шага — сессия потеряна
            raise KeyError("User data not found")
        # Приводим введённый текст к float — возможна ошибка ValueError
        user_data[user_id]['sugar_level'] = float(message.text)
        bot.send_message(message.chat.id, 'Введите дозу инсулина:')
        bot.register_next_step_handler(message, get_insulin, user_id)
    except ValueError:
        # Некорректный формат ввода — повторяем шаг
        bot.send_message(message.chat.id, 'Неверный формат. Введите число для показаний сахара:')
        bot.register_next_step_handler(message, get_sugar, user_id)
    except KeyError as e:
        logging.error(f"User data error in get_sugar: {e}")
        bot.send_message(message.chat.id, 'Сессия истекла. Начните заново.')
        show_menu(message.chat.id)
    except Exception as e:
        logging.error(f"Get sugar error: {e}")
        bot.send_message(message.chat.id, 'Ошибка ввода. Попробуйте заново.')


def get_insulin(message, user_id):
    """Шаг диалога: ввод дозы инсулина. Для ночных записей сохраняет запись сразу."""
    try:
        if user_id not in user_data:
            raise KeyError("User data not found")
        user_data[user_id]['insulin_dose'] = float(message.text)

        if user_data[user_id]['nocturnal'] == 1:
            # Для ночных записей хлебные единицы равны 0 — и сразу сохраняем
            user_data[user_id]['bread_units'] = 0
            save_to_db(user_id)
            bot.send_message(message.chat.id, 'Данные сохранены.')
            show_menu(message.chat.id)
        else:
            # Для дневных записей просим указать хлебные единицы
            bot.send_message(message.chat.id, 'Введите количество съеденных хлебных единиц:')
            bot.register_next_step_handler(message, get_bread, user_id)

    except ValueError:
        bot.send_message(message.chat.id, 'Неверный формат. Введите число для дозы инсулина:')
        bot.register_next_step_handler(message, get_insulin, user_id)
    except KeyError as e:
        logging.error(f"User data error in get_insulin: {e}")
        bot.send_message(message.chat.id, 'Сессия истекла. Начните заново.')
        show_menu(message.chat.id)
    except Exception as e:
        logging.error(f"Get insulin error: {e}")
        bot.send_message(message.chat.id, 'Ошибка ввода. Попробуйте зановo.')


def get_bread(message, user_id):
    """Шаг диалога: ввод хлебных единиц (целое число)."""
    try:
        if user_id not in user_data:
            raise KeyError("User data not found")
        user_data[user_id]['bread_units'] = int(message.text)
        save_to_db(user_id)
        bot.send_message(message.chat.id, 'Данные сохранены.')
        show_menu(message.chat.id)
    except ValueError:
        # Если введено не целое число — просим повторить ввод
        bot.send_message(message.chat.id, 'Неверный формат. Введите целое число для хлебных единиц:')
        bot.register_next_step_handler(message, get_bread, user_id)
    except KeyError as e:
        logging.error(f"User data error in get_bread: {e}")
        bot.send_message(message.chat.id, 'Сессия истекла. Начните заново.')
        show_menu(message.chat.id)
    except Exception as e:
        logging.error(f"Get bread error: {e}")
        bot.send_message(message.chat.id, 'Ошибка ввода. Попробуйте зановo.')


def save_to_db(user_id):
    """Сохраняет данные пользователя в таблицу records.

    После успешной записи удаляет временные данные из user_data.
    """
    if user_id not in user_data:
        logging.error("User data not found in save_to_db")
        return
    data = user_data[user_id]
    try:
        cursor.execute('''
        INSERT INTO records (user_id, sugar_level, insulin_dose, bread_units, timestamp, nocturnal)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', (user_id, data['sugar_level'], data['insulin_dose'], data['bread_units'], data['timestamp'], data['nocturnal']))
        conn.commit()
    except Error as e:
        # При ошибке пытаемся переподключиться и повторить запись один раз
        logging.error(f"Database insert error: {e}")
        reconnect_db()
        try:
            cursor.execute('''
            INSERT INTO records (user_id, sugar_level, insulin_dose, bread_units, timestamp, nocturnal)
            VALUES (%s, %s, %s, %s, %s, %s)
            ''', (user_id, data['sugar_level'], data['insulin_dose'], data['bread_units'], data['timestamp'], data['nocturnal']))
            conn.commit()
        except Error as retry_e:
            logging.error(f"Database insert retry error: {retry_e}")
    finally:
        # Очистка временных данных сессии независимо от результата
        if user_id in user_data:
            del user_data[user_id]


def generate_report(chat_id, user_id, days):
    """Генерирует Excel-отчёт и график за последние `days` дней и отправляет пользователю.

    Если за указанный период данных нет — отправляет все доступные данные, либо
    сообщает об их отсутствии.
    """
    try:
        logging.info(f"Generating report for user_id: {user_id}, days: {days}")
        cursor.execute('''
        SELECT * FROM records 
        WHERE user_id = %s AND timestamp >= DATE_SUB(NOW(), INTERVAL %s DAY)
        ORDER BY timestamp
        ''', (user_id, days))
        rows = cursor.fetchall()
        logging.info(f"Rows for period: {len(rows)}")

        if not rows:
            # Если за период нет данных, пытаемся получить все записи пользователя
            cursor.execute('SELECT * FROM records WHERE user_id = %s ORDER BY timestamp', (user_id,))
            rows = cursor.fetchall()
            logging.info(f"All rows: {len(rows)}")
            if not rows:
                bot.send_message(chat_id, 'Нет данных.')
                logging.error("No data found for user")
                return
            bot.send_message(chat_id, 'Нет данных за период. Вот все данные.')

        # Формируем DataFrame для Excel-таблицы
        df = pd.DataFrame(rows, columns=['user_id', 'sugar_level', 'insulin_dose', 'bread_units', 'timestamp', 'nocturnal'])
        df = df.drop(columns=['user_id'])
        # Разбиваем метку времени на дату и время для наглядности
        df['Дата'] = df['timestamp'].dt.date
        df['Время'] = df['timestamp'].dt.time
        df = df.drop(columns=['timestamp'])
        df = df[['Дата', 'Время', 'sugar_level', 'insulin_dose', 'bread_units', 'nocturnal']]
        df.columns = ['Дата', 'Время', 'Показания сахара', 'Доза инсулина', 'Хлебные единицы', 'Ночной']

        # Формируем график динамики уровня сахара
        plt.figure(figsize=(10, 6))
        df_chart = pd.DataFrame(rows, columns=['user_id', 'sugar_level', 'insulin_dose', 'bread_units', 'timestamp', 'nocturnal'])
        df_chart = df_chart.sort_values('timestamp')

        plt.plot(df_chart['timestamp'], df_chart['sugar_level'], marker='o', linewidth=2, markersize=4)
        plt.title('Динамика уровня сахара')
        plt.xlabel('Дата и время')
        plt.ylabel('Уровень сахара')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.xticks(rotation=45)
        plt.tight_layout()

        # Сохраняем график в байтовый буфер и затем закроем фигуру
        chart_buffer = io.BytesIO()
        plt.savefig(chart_buffer, format='png', dpi=150, bbox_inches='tight')
        chart_buffer.seek(0)
        plt.close()

        # Пишем Excel в байтовый буфер
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Данные', index=False)
        excel_buffer.seek(0)

        # Отправляем файл Excel и изображение графика пользователю
        bot.send_document(chat_id, excel_buffer, visible_file_name=f'report_{days}_days.xlsx')
        bot.send_photo(chat_id, chart_buffer, caption=f'График изменения уровня сахара за {days} дней')

    except Error as e:
        logging.error(f"Database query error: {e}")
        reconnect_db()
        bot.send_message(chat_id, 'Ошибка базы данных. Попробуйте позже.')
    except Exception as e:
        logging.error(f"Report generation error: {e}")
        print(f"Report error for user_id {user_id}, days {days}: {e}")
        bot.send_message(chat_id, 'Ошибка при генерации отчета.')


if __name__ == '__main__':
    try:
        # Запуск режима polling — бот будет получать обновления от Telegram
        bot.polling()
    except Exception as e:
        logging.error(f"Bot polling error: {e}")
    finally:
        # При завершении корректно закрываем соединение с БД
        if conn.is_connected():
            cursor.close()
            conn.close()