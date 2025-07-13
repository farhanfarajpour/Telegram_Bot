import telebot
from telebot import types
import jdatetime
import re
import mysql.connector
from datetime import datetime
import time
import logging
import random
import os
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = "Q"
CHANNEL_USERNAME = ""

TYPE_VALUE_MAP = {
    "باگ": "بحرانی",
    "توسعه": "مهم",
    "پیشنهاد": "متوسط",
    "سایر": "کم اهمیت"
}

TEAM_NAME_MAP = {
    "#دیاگ": "مهندس اصغریان",
    "#پروگرامینگ": "مهندس رستمی",
    "#سخت_افزار": "مهندس رستمی"
}

class DatabasePool:
    def __init__(self, max_connections=5):
        self.max_connections = max_connections
        self.connections = []
        self.config = {
            "host": "",
            "user": "negar",
            "password": "",
            "database": "",
            "port": 3306,
            "autocommit": True,
            "ssl_disabled": True,
            "connection_timeout": 30,
            "connect_timeout": 30,
            "pool_reset_session": True
        }

    def get_connection(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.connections:
                    conn = self.connections.pop()
                    if conn.is_connected():
                        return conn
                    else:
                        conn.close()

                conn = mysql.connector.connect(**self.config)
                logger.info("Database connection established successfully")
                return conn
            except mysql.connector.Error as err:
                logger.error(f"Database connection attempt {attempt + 1} failed: {err}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt + random.uniform(0, 1))
                else:
                    logger.error("All database connection attempts failed")
                    return None

    def release_connection(self, conn):
        if conn and conn.is_connected() and len(self.connections) < self.max_connections:
            self.connections.append(conn)
        elif conn:
            conn.close()

db_pool = DatabasePool()

@contextmanager
def get_db_connection():
    conn = db_pool.get_connection()
    try:
        yield conn
    finally:
        if conn:
            db_pool.release_connection(conn)

def ensure_table_schema():
    with get_db_connection() as conn:
        if not conn:
            logger.error("Failed to connect to database for schema check")
            return
        try:
            with conn.cursor() as cursor:
                cursor.execute("DESCRIBE negar_bot_app_tg_msg_feedback_group")
                columns = [column[0] for column in cursor.fetchall()]

                if 'link' not in columns:
                    cursor.execute('''
                                   ALTER TABLE negar_bot_app_tg_msg_feedback_group
                                       ADD COLUMN link TEXT;
                                   ''')
                    logger.info("Added 'link' column to table")

                if 'miladi_date' not in columns:
                    cursor.execute('''
                                   ALTER TABLE negar_bot_app_tg_msg_feedback_group
                                       ADD COLUMN miladi_date DATE;
                                   ''')
                    logger.info("Added 'miladi_date' column to table")

        except mysql.connector.Error as err:
            logger.error(f"Error updating table schema: {err}")

ensure_table_schema()
class TelegramBotHandler:
    def __init__(self, token):
        self.token = token
        self.bot = None
        self.initialize_bot()

    def initialize_bot(self):
        try:
            self.bot = telebot.TeleBot(self.token, parse_mode='HTML')
            self.bot.remove_webhook()
            time.sleep(1)
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise

    def get_bot(self):
        if not self.bot:
            self.initialize_bot()
        return self.bot

bot_handler = TelegramBotHandler(TOKEN)
bot = bot_handler.get_bot()

def safe_bot_operation(func):
    def wrapper(*args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:  # Too Many Requests
                    retry_after = int(e.description.split('retry after ')[1]) if 'retry after' in e.description else 30
                    logger.warning(f"Rate limit exceeded. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                elif e.error_code in [502, 503, 504]:  # Server errors
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"Server error {e.error_code}. Retrying in {wait_time:.1f} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Telegram API error: {e}")
                    break
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    break
        return None
    return wrapper

def save_or_update_message(group_name, message_date, code, team, message_type, status, status_date,
                           message_content, description, link=""):
    with get_db_connection() as conn:
        if not conn:
            logger.error("Failed to save/update message: No database connection")
            return False

        try:
            with conn.cursor() as cursor:

                cursor.execute("SELECT * FROM negar_bot_app_tg_msg_feedback_group WHERE code = %s", (code,))
                existing_record = cursor.fetchone()                # Parse date
                try:
                    shamsi_date = jdatetime.datetime.strptime(message_date, '%Y/%m/%d')
                    miladi_date = shamsi_date.togregorian().date()
                except ValueError:
                    logger.warning(f"Invalid date format for message_date: {message_date}")
                    miladi_date = datetime.now().date()

                if existing_record:
                    current_link = existing_record[-2] if len(existing_record) > 11 else ""
                    new_link = link if link and not current_link else current_link

                    cursor.execute('''
                                   UPDATE negar_bot_app_tg_msg_feedback_group
                                   SET group_name      = %s,
                                       message_date    = %s,
                                       team            = %s,
                                       message_type    = %s,
                                       status          = %s,
                                       status_date     = %s,
                                       message_content = %s,
                                       description     = %s,
                                       link            = %s,
                                       miladi_date     = %s
                                   WHERE code = %s
                                   ''', (group_name, message_date, team, message_type, status, status_date,
                                         message_content, description, new_link, miladi_date, code))
                    logger.info(f"Updated existing record with code: {code}")
                else:
                    cursor.execute('''
                                   INSERT INTO negar_bot_app_tg_msg_feedback_group
                                   (group_name, message_date, code, team, message_type, status, status_date,
                                    message_content, description, link, miladi_date)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                   ''', (group_name, message_date, code, team, message_type, status, status_date,
                                         message_content, description, link, miladi_date))
                    logger.info(f"Inserted new record with code: {code}")
                return True

        except mysql.connector.Error as err:
            logger.error(f"Database error in save_or_update_message: {err}")
            return False
        except Exception as err:
            logger.error(f"Unexpected error in save_or_update_message: {err}")
            return False

def process_channel_post(message, is_edited=False):
    try:
        text = message.text if message.text else ""
        pattern = r"گروه:\s*(.*?)\nتاریخ:\s*(.*?)\nکد:\s*(.*?)\nتیم:\s*(.*?)\nنوع:\s*(.*?)\nوضعیت:\s*(.*?)\n(?:تاریخ:\s*(.*?)\n)?\nپیام:\s*(.*?)\n\nتوضیحات:(.*)"
        match = re.search(pattern, text, re.DOTALL)

        if not match:
            logger.warning("Message format does not match expected pattern")
            return

        group_name = match.group(1).strip()
        message_date = match.group(2).strip()
        code = match.group(3).strip()
        team = match.group(4).strip()
        message_type = match.group(5).strip()
        status = match.group(6).strip()
        status_date = match.group(7).strip() if match.group(7) else jdatetime.date.today().strftime('%Y/%m/%d')
        message_content = match.group(8).strip()
        description = match.group(9).strip() if match.group(9).strip() else ""

        if not code:
            logger.error("Invalid or missing code in channel post")
            return

        message_link = f"https://t.me/{CHANNEL_USERNAME}/{message.message_id}"

        success = save_or_update_message(
            group_name=group_name or "نامشخص",
            message_date=message_date,
            code=code,
            team=team or "نامشخص",
            message_type=message_type,
            status=status,
            status_date=status_date,
            message_content=message_content,
            description=description,
            link=message_link
        )

        if success:
            logger.info(f"✅ {'Edited' if is_edited else 'New'} message {'updated' if is_edited else 'saved'} successfully.")
        else:
            logger.error(f"❌ Failed to {'update' if is_edited else 'save'} message")

    except Exception as e:
        logger.error(f"Error processing channel post: {e}")

@bot.channel_post_handler(content_types=['text'])
def handle_channel_post(message):
    process_channel_post(message, is_edited=False)

@bot.edited_channel_post_handler(content_types=['text'])
def handle_edited_channel_post(message):
    process_channel_post(message, is_edited=True)

@bot.message_handler(commands=['start'])
@safe_bot_operation
def send_welcome(message):
    try:
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton('دریافت اکسل', callback_data='excel')
        markup.add(btn1)
        bot.send_message(message.chat.id,
                         "سلام! من ربات ذخیره‌کننده دیتا هستم. چه کمکی میتونم بکنم؟",
                         reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in send_welcome: {e}")

@bot.callback_query_handler(func=lambda call: True)
@safe_bot_operation
def handle_callback(call):
    try:
        if call.data == 'excel':
            bot.answer_callback_query(call.id, "شما گزینه دریافت اکسل را انتخاب کردید!")
            send_excel(call.message)
    except Exception as e:
        logger.error(f"Error in handle_callback: {e}")

@safe_bot_operation
def send_excel(message):
    try:
        import pandas as pd

        with get_db_connection() as conn:
            if not conn:
                bot.reply_to(message, "خطا در ارتباط با پایگاه داده")
                return

            try:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT * FROM negar_bot_app_tg_msg_feedback_group')
                    records = cursor.fetchall()

                if not records:
                    bot.reply_to(message, "هنوز داده‌ای ذخیره نشده است.")
                    return

                data = []
                for record in records:
                    telegram_group_name = TEAM_NAME_MAP.get(record[4], "مهندس رستمی")
                    type_value = TYPE_VALUE_MAP.get(record[5], "کم اهمیت")

                    message_content = str(record[8]) if record[8] else ''
                    if len(message_content) > 254:
                        message_content = message_content[:254]

                    link = record[10] if len(record) > 10 and record[10] else record[3]

                    data.append({
                        "واحد سازمانی (شناسه)": "31",
                        "درخواست کننده (شناسه)": "227",
                        "نام مشتری (شناسه)": "",
                        "نام نماینده (شناسه)": "",
                        "نام محصول 1 (شناسه)": "",
                        "سریال 1 (عامل اول)": "",
                        "نام محصول 2 (شناسه)": "",
                        "سریال 2 (عامل اول)": "",
                        "نام نرم افزار (شناسه)": "",
                        "نوع عدم انطباق": record[5],
                        "درجه اهمیت عدم انطباق": type_value,
                        "درگاه شناسایی": "تلگرام",
                        "تیم مربوطه": telegram_group_name,
                        "مهلت اقدام": record[2],
                        "نام گروه تلگرام": f"گروه {record[1]}",
                        "شرح عدم انطباق": message_content,
                        "شرح عدم انطباق2": link,
                        "توضیحات": record[9],
                        "وضعیت فیلدبک": record[6],
                    })

                df = pd.DataFrame(data)
                excel_file = 'tg_msg_feedback_group.xlsx'
                df.index = range(1, len(df) + 1)
                df.to_excel(excel_file, engine='xlsxwriter', index=True)

                with open(excel_file, 'rb') as file:
                    bot.send_document(message.chat.id, file)
                try:
                    os.remove(excel_file)
                except:
                    pass

            except mysql.connector.Error as err:
                logger.error(f"Database error in send_excel: {err}")
                bot.reply_to(message, f"خطا در ارتباط با پایگاه داده: {str(err)}")

    except ImportError:
        bot.reply_to(message, "خطا: کتابخانه pandas نصب نیست")
    except Exception as e:
        logger.error(f"Error in send_excel: {e}")
        bot.reply_to(message, f"خطا در تولید اکسل: {str(e)}")

@bot.message_handler(func=lambda message: True)
@safe_bot_operation
def handle_message(message):
    try:
        data = {
            'گروه': None,
            'تاریخ': None,
            'کد': None,
            'تیم': None,
            'نوع': None,
            'وضعیت': None,
            'پیام': None,
            'توضیحات': None,
        }

        lines = message.text.split('\n')
        message_body = []
        capture_message = False
        last_key = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if capture_message:
                message_body.append(line)
            else:
                if ':' in line:
                    key_part, value = line.split(':', 1)
                    key_part = key_part.strip()
                    value = value.strip()

                    if key_part in data:
                        data[key_part] = value
                        last_key = key_part
                    elif key_part == 'پیام':
                        data['پیام'] = value
                        capture_message = True
                elif last_key and data[last_key]:
                    data[last_key] += ' ' + line

        if capture_message and message_body:
            if data['پیام']:
                data['پیام'] += '\n' + '\n'.join(message_body)
            else:
                data['پیام'] = '\n'.join(message_body)
        required_fields = ['کد', 'تیم', 'نوع', 'وضعیت', 'پیام']
        missing_fields = [field for field in required_fields if not data[field]]

        if missing_fields:
            bot.reply_to(message, f"❌ فیلدهای اجباری خالی هستند: {', '.join(missing_fields)}")
            return

        if not data['تاریخ']:
            data['تاریخ'] = jdatetime.date.today().strftime('%Y/%m/%d')

        message_link = f"https://t.me/c/{str(message.chat.id).replace('-', '')}/{message.message_id}"
        success = save_or_update_message(
            group_name=data['گروه'] or "نامشخص",
            message_date=data['تاریخ'],
            code=data['کد'],
            team=data['تیم'] or "نامشخص",
            message_type=data['نوع'],
            status=data['وضعیت'],
            status_date=data['تاریخ'],
            message_content=data['پیام'],
            description=data['توضیحات'] or "",
            link=message_link
        )

        if success:
            bot.reply_to(message, "✅ گزارش با موفقیت ذخیره شد!")
        else:
            bot.reply_to(message, "❌ خطا در ذخیره گزارش. لطفا دوباره تلاش کنید.")

    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        bot.reply_to(message, "❌ خطا در پردازش پیام. لطفا فرمت پیام را بررسی کنید.")

def run_bot():
    max_retries = 10
    base_delay = 5
    max_delay = 300

    for attempt in range(max_retries):
        try:
            logger.info(f"Starting bot (attempt {attempt + 1}/{max_retries})...")
            bot_handler.initialize_bot()


            bot.polling(
                non_stop=True,
                interval=2,
                timeout=30,
                long_polling_timeout=20,
                allowed_updates=None
            )

        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                retry_after = int(e.description.split('retry after ')[1]) if 'retry after' in e.description else 60
                logger.warning(f"Rate limit exceeded. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
            elif e.error_code == 409:
                logger.warning("Bot conflict detected (another instance running?). Waiting 60 seconds...")
                time.sleep(60)
            elif e.error_code in [502, 503, 504]:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 5), max_delay)
                logger.warning(f"Server error {e.error_code}. Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
            else:
                logger.error(f"Telegram API error: {e}")
                delay = min(base_delay * (2 ** attempt), max_delay)
                time.sleep(delay)

        except (ConnectionError, OSError, Exception) as e:
            logger.error(f"Connection error: {e}")
            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 5), max_delay)
                logger.info(f"Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
            else:
                logger.error("Maximum retry attempts reached. Exiting.")
                break

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break

if __name__ == "__main__":
    logger.info("Bot is starting...")
    run_bot()