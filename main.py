import asyncio
import sqlite3
import re
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import TelegramClient, events
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest
from telethon.tl.types import UserStatusOnline, UserStatusOffline, UserStatusRecently, UserStatusLastWeek, \
    UserStatusLastMonth
from telethon.errors import SessionPasswordNeededError, ChatAdminRequiredError, UserAlreadyParticipantError, \
    FloodWaitError
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged

# НАСТРОЙКА
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ⚠️ ТВОИ ДАННЫЕ ⚠️
BOT_TOKEN = '8828137901:AAEZSXnTsYX8IR5-7xDSoLdnGL0_ZmtYVDI'
API_ID = 2040
API_HASH = 'b18441a1ff607e10a989891a5462e627'
ADMIN_ID = 8572689827

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# СОСТОЯНИЯ
class ImportOldNamesState(StatesGroup):
    waiting_for_names = State()


class ImportOldUsernamesState(StatesGroup):
    waiting_for_usernames = State()


class CollectUserDataState(StatesGroup):
    waiting_for_command = State()
    collecting_data = State()


# Прокси настройки
USE_PROXY = False
PROXY = None

if USE_PROXY and PROXY:
    user_client = TelegramClient('user_session', API_ID, API_HASH, connection=ConnectionTcpAbridged, proxy=PROXY)
else:
    user_client = TelegramClient('user_session', API_ID, API_HASH)

DB_NAME = 'user_analysis.db'


class Database:
    def __init__(self, db_name):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        # ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT,
                last_seen TEXT,
                phone TEXT,
                is_premium BOOLEAN DEFAULT FALSE,
                is_hidden BOOLEAN DEFAULT FALSE,
                hidden_until TEXT,
                premium_start TEXT,
                premium_end TEXT,
                premium_history TEXT,
                search_count INTEGER DEFAULT 0,
                unique_searchers TEXT DEFAULT "",
                birthday TEXT,
                reputation_likes INTEGER DEFAULT 0,
                reputation_dislikes INTEGER DEFAULT 0,
                interests_count INTEGER DEFAULT 0
            )
        ''')

        self.cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in self.cursor.fetchall()]
        if 'search_count' not in columns:
            self.cursor.execute('ALTER TABLE users ADD COLUMN search_count INTEGER DEFAULT 0')
        if 'unique_searchers' not in columns:
            self.cursor.execute('ALTER TABLE users ADD COLUMN unique_searchers TEXT DEFAULT ""')
        if 'birthday' not in columns:
            self.cursor.execute('ALTER TABLE users ADD COLUMN birthday TEXT')
        if 'reputation_likes' not in columns:
            self.cursor.execute('ALTER TABLE users ADD COLUMN reputation_likes INTEGER DEFAULT 0')
        if 'reputation_dislikes' not in columns:
            self.cursor.execute('ALTER TABLE users ADD COLUMN reputation_dislikes INTEGER DEFAULT 0')
        if 'interests_count' not in columns:
            self.cursor.execute('ALTER TABLE users ADD COLUMN interests_count INTEGER DEFAULT 0')

        # ТАБЛИЦА СТАРЫХ ЮЗЕРНЕЙМОВ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS old_usernames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                changed_at TEXT
            )
        ''')

        # ТАБЛИЦА СТАРЫХ ИМЕН
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS old_names (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                first_name TEXT,
                last_name TEXT,
                changed_at TEXT
            )
        ''')

        # ТАБЛИЦА СООБЩЕНИЙ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                chat_name TEXT,
                chat_link TEXT,
                message_text TEXT,
                sent_at TEXT,
                is_media BOOLEAN DEFAULT FALSE,
                media_type TEXT,
                is_reply BOOLEAN DEFAULT FALSE,
                reply_to_id INTEGER,
                message_id INTEGER,
                analyzed_type TEXT DEFAULT 'other'
            )
        ''')

        self.cursor.execute("PRAGMA table_info(messages)")
        msg_columns = [col[1] for col in self.cursor.fetchall()]
        if 'chat_link' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN chat_link TEXT')
        if 'is_media' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN is_media BOOLEAN DEFAULT FALSE')
        if 'media_type' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN media_type TEXT')
        if 'is_reply' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN is_reply BOOLEAN DEFAULT FALSE')
        if 'reply_to_id' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN reply_to_id INTEGER')
        if 'message_id' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN message_id INTEGER')
        if 'analyzed_type' not in msg_columns:
            self.cursor.execute('ALTER TABLE messages ADD COLUMN analyzed_type TEXT DEFAULT "other"')

        # ТАБЛИЦА АНАЛИЗА
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message_id INTEGER,
                analysis_type TEXT,
                value TEXT,
                created_at TEXT
            )
        ''')

        # ТАБЛИЦА СТАТИСТИКИ АНАЛИЗА
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_analysis_stats (
                user_id INTEGER PRIMARY KEY,
                total_messages INTEGER DEFAULT 0,
                total_replies INTEGER DEFAULT 0,
                total_media INTEGER DEFAULT 0,
                total_voice INTEGER DEFAULT 0,
                total_video INTEGER DEFAULT 0,
                total_gifs INTEGER DEFAULT 0,
                total_stickers INTEGER DEFAULT 0,
                total_links INTEGER DEFAULT 0,
                total_files INTEGER DEFAULT 0,
                favorite_chat_id INTEGER,
                favorite_chat_name TEXT,
                avg_words_per_message REAL DEFAULT 0,
                last_updated TEXT
            )
        ''')

        # ТАБЛИЦА РЕАКЦИЙ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                reaction_type TEXT,
                reacted_at TEXT
            )
        ''')

        # ТАБЛИЦА ЛИЧНЫХ СООБЩЕНИЙ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS private_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                message_text TEXT,
                sent_at TEXT,
                is_outgoing BOOLEAN DEFAULT FALSE
            )
        ''')

        # ТАБЛИЦА ЧАТОВ ПОЛЬЗОВАТЕЛЯ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                chat_name TEXT,
                chat_link TEXT,
                chat_type TEXT,
                participants_count INTEGER DEFAULT 0,
                is_admin BOOLEAN DEFAULT FALSE,
                is_owner BOOLEAN DEFAULT FALSE,
                joined_at TEXT
            )
        ''')

        # ТАБЛИЦА ОТСЛЕЖИВАЕМЫХ ЧАТОВ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS tracked_chats (
                chat_id INTEGER PRIMARY KEY,
                chat_name TEXT,
                chat_link TEXT,
                chat_type TEXT,
                participants_count INTEGER DEFAULT 0,
                added_at TEXT,
                added_by INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                is_joined BOOLEAN DEFAULT FALSE,
                join_status TEXT DEFAULT 'joined',
                is_public BOOLEAN DEFAULT FALSE
            )
        ''')

        self.cursor.execute("PRAGMA table_info(tracked_chats)")
        tracked_cols = [col[1] for col in self.cursor.fetchall()]
        if 'is_joined' not in tracked_cols:
            self.cursor.execute('ALTER TABLE tracked_chats ADD COLUMN is_joined BOOLEAN DEFAULT FALSE')
        if 'join_status' not in tracked_cols:
            self.cursor.execute('ALTER TABLE tracked_chats ADD COLUMN join_status TEXT DEFAULT "joined"')
        if 'is_public' not in tracked_cols:
            self.cursor.execute('ALTER TABLE tracked_chats ADD COLUMN is_public BOOLEAN DEFAULT FALSE')

        # ТАБЛИЦА ПОДПИСЧИКОВ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS followers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_user_id INTEGER,
                follower_user_id INTEGER,
                created_at TEXT,
                last_notified_at TEXT,
                notify_count INTEGER DEFAULT 0
            )
        ''')

        # ТАБЛИЦА АКТИВНОСТИ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_activity (
                user_id INTEGER,
                date TEXT,
                hour INTEGER,
                message_count INTEGER,
                PRIMARY KEY (user_id, date, hour)
            )
        ''')

        # ТАБЛИЦА ОНЛАЙНА
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS online_history (
                user_id INTEGER,
                online_time TEXT,
                is_online BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (user_id, online_time)
            )
        ''')

        # ТАБЛИЦА ПИКОВ АКТИВНОСТИ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_peaks (
                user_id INTEGER,
                peak_hour INTEGER,
                day_of_week INTEGER,
                message_count INTEGER,
                date TEXT,
                PRIMARY KEY (user_id, peak_hour, day_of_week)
            )
        ''')

        # ТАБЛИЦА СТАТИСТИКИ ПО ГОРОДАМ
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS city_stats (
                user_id INTEGER,
                city TEXT,
                message_count INTEGER,
                last_mentioned TEXT,
                PRIMARY KEY (user_id, city)
            )
        ''')

        self.conn.commit()

    # ==================== МЕТОДЫ ДЛЯ СТАРЫХ ИМЕН ====================

    def add_old_name_import(self, user_id, first_name, last_name, changed_at):
        try:
            self.cursor.execute('''
                INSERT INTO old_names (user_id, first_name, last_name, changed_at)
                VALUES (?, ?, ?, ?)
            ''', (user_id, first_name, last_name, changed_at))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding old name: {e}")
            return False

    def add_old_username_import(self, user_id, username, changed_at):
        try:
            self.cursor.execute('''
                INSERT INTO old_usernames (user_id, username, changed_at)
                VALUES (?, ?, ?)
            ''', (user_id, username, changed_at))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding old username: {e}")
            return False

    def get_old_names(self, user_id):
        self.cursor.execute('''
            SELECT first_name, last_name, changed_at FROM old_names 
            WHERE user_id = ? ORDER BY changed_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    def get_old_usernames(self, user_id):
        self.cursor.execute('''
            SELECT username, changed_at FROM old_usernames 
            WHERE user_id = ? ORDER BY changed_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    # ==================== ОСТАЛЬНЫЕ МЕТОДЫ ====================

    def add_user(self, user_id, username, first_name, last_name, is_premium=False, phone=None, last_seen=None,
                 birthday=None):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        existing = self.cursor.fetchone()

        if existing:
            if existing[2] != first_name or existing[3] != last_name:
                self.add_old_name(user_id, existing[2], existing[3])

            self.cursor.execute('''
                UPDATE users SET 
                    username = ?, 
                    first_name = ?, 
                    last_name = ?, 
                    is_premium = ?, 
                    phone = ?, 
                    last_seen = ?,
                    birthday = COALESCE(?, birthday)
                WHERE user_id = ?
            ''', (username, first_name, last_name, is_premium, phone, last_seen, birthday, user_id))
        else:
            self.cursor.execute('''
                INSERT INTO users (
                    user_id, username, first_name, last_name, created_at, 
                    is_hidden, is_premium, phone, last_seen, birthday
                ) VALUES (?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, datetime.now().isoformat(),
                  is_premium, phone, last_seen, birthday))

        self.conn.commit()

    def add_old_name(self, user_id, first_name, last_name):
        self.cursor.execute('''
            INSERT INTO old_names (user_id, first_name, last_name, changed_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, first_name, last_name, datetime.now().isoformat()))
        self.conn.commit()

    def add_old_username(self, user_id, username):
        self.cursor.execute('''
            INSERT INTO old_usernames (user_id, username, changed_at)
            VALUES (?, ?, ?)
        ''', (user_id, username, datetime.now().isoformat()))
        self.conn.commit()

    def get_user_info(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()

    def get_user_by_username(self, username):
        if username.startswith('@'):
            username = username[1:]
        self.cursor.execute('SELECT user_id FROM users WHERE username = ?', (username,))
        return self.cursor.fetchone()

    def get_user_by_id(self, user_id):
        self.cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()

    def search_users_by_username(self, query):
        self.cursor.execute('''
            SELECT user_id, username, first_name, last_name, is_hidden, is_premium, COALESCE(search_count, 0)
            FROM users 
            WHERE username LIKE ? OR first_name LIKE ? OR user_id LIKE ?
            LIMIT 20
        ''', (f'%{query}%', f'%{query}%', f'%{query}%'))
        return self.cursor.fetchall()

    def add_searcher(self, target_user_id, searcher_user_id):
        try:
            self.cursor.execute('SELECT unique_searchers FROM users WHERE user_id = ?', (target_user_id,))
            result = self.cursor.fetchone()

            if result and result[0]:
                searchers = result[0].split(',')
                if str(searcher_user_id) not in searchers:
                    searchers.append(str(searcher_user_id))
                    new_searchers = ','.join(searchers)
                    self.cursor.execute('''
                        UPDATE users SET unique_searchers = ?, search_count = COALESCE(search_count, 0) + 1
                        WHERE user_id = ?
                    ''', (new_searchers, target_user_id))
                else:
                    self.cursor.execute('''
                        UPDATE users SET search_count = COALESCE(search_count, 0) + 1
                        WHERE user_id = ?
                    ''', (target_user_id,))
            else:
                self.cursor.execute('''
                    UPDATE users SET unique_searchers = ?, search_count = COALESCE(search_count, 0) + 1
                    WHERE user_id = ?
                ''', (str(searcher_user_id), target_user_id))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error adding searcher: {e}")

    def get_search_count(self, user_id):
        try:
            self.cursor.execute('SELECT search_count FROM users WHERE user_id = ?', (user_id,))
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except:
            return 0

    def get_unique_searchers_count(self, user_id):
        try:
            self.cursor.execute('SELECT unique_searchers FROM users WHERE user_id = ?', (user_id,))
            result = self.cursor.fetchone()
            if result and result[0]:
                return len(result[0].split(','))
            return 0
        except:
            return 0

    def is_user_hidden(self, user_id):
        self.cursor.execute('SELECT is_hidden, hidden_until FROM users WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        if not result:
            return False
        is_hidden, hidden_until = result
        if is_hidden and hidden_until:
            try:
                until = datetime.fromisoformat(hidden_until)
                if datetime.now() > until:
                    self.unhide_user(user_id)
                    return False
            except:
                pass
        return bool(is_hidden)

    def hide_user(self, user_id, days=None):
        if days:
            hidden_until = (datetime.now() + timedelta(days=days)).isoformat()
            self.cursor.execute('''
                UPDATE users SET is_hidden = TRUE, hidden_until = ?
                WHERE user_id = ?
            ''', (hidden_until, user_id))
        else:
            self.cursor.execute('''
                UPDATE users SET is_hidden = TRUE, hidden_until = NULL
                WHERE user_id = ?
            ''', (user_id,))
        self.conn.commit()

    def unhide_user(self, user_id):
        self.cursor.execute('''
            UPDATE users SET is_hidden = FALSE, hidden_until = NULL
            WHERE user_id = ?
        ''', (user_id,))
        self.conn.commit()

    def add_tracked_chat(self, chat_id, chat_name, chat_link, chat_type, participants_count=0,
                         added_by=0, is_joined=True, join_status='joined', is_public=False):
        self.cursor.execute('''
            INSERT OR REPLACE INTO tracked_chats (
                chat_id, chat_name, chat_link, chat_type, participants_count, 
                added_at, added_by, is_active, is_joined, join_status, is_public
            ) VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?, ?)
        ''', (chat_id, chat_name, chat_link, chat_type, participants_count,
              datetime.now().isoformat(), added_by, is_joined, join_status, is_public))
        self.conn.commit()

    def get_tracked_chats(self):
        self.cursor.execute('''
            SELECT chat_id, chat_name, chat_link, chat_type, participants_count, 
                   added_at, added_by, is_active, is_joined, join_status, is_public
            FROM tracked_chats 
            WHERE is_active = TRUE
        ''')
        return self.cursor.fetchall()

    def get_tracked_chats_count(self):
        self.cursor.execute('''
            SELECT 
                COUNT(CASE WHEN chat_type = 'group' OR chat_type = 'supergroup' THEN 1 END) as groups_count,
                COUNT(CASE WHEN chat_type = 'channel' THEN 1 END) as channels_count
            FROM tracked_chats 
            WHERE is_active = TRUE
        ''')
        return self.cursor.fetchone()

    def delete_tracked_chat(self, chat_id):
        self.cursor.execute('DELETE FROM tracked_chats WHERE chat_id = ?', (chat_id,))
        self.conn.commit()

    def add_message(self, user_id, chat_id, chat_name, chat_link, message_text, sent_at,
                    message_id, is_media=False, media_type=None, is_reply=False, reply_to_id=None):
        analyzed_type = self.analyze_message_type(message_text)

        self.cursor.execute('''
            INSERT INTO messages (
                user_id, chat_id, chat_name, chat_link, message_text, sent_at, 
                message_id, is_media, media_type, is_reply, reply_to_id, analyzed_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, chat_name, chat_link, message_text, sent_at,
              message_id, is_media, media_type, is_reply, reply_to_id, analyzed_type))

        message_db_id = self.cursor.lastrowid
        self.conn.commit()

        if analyzed_type != 'other' and message_text:
            self.add_analysis(user_id, message_db_id, analyzed_type, message_text[:100])

        self.update_message_analysis_stats(user_id, message_text, is_reply, is_media, media_type)
        return message_db_id

    def add_user_chat(self, user_id, chat_id, chat_name, chat_link, chat_type,
                      participants_count=0, is_admin=False, is_owner=False):
        self.cursor.execute('''
            INSERT OR REPLACE INTO user_chats (
                user_id, chat_id, chat_name, chat_link, chat_type, 
                participants_count, is_admin, is_owner, joined_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, chat_name, chat_link, chat_type,
              participants_count, is_admin, is_owner, datetime.now().isoformat()))
        self.conn.commit()

    def add_private_message(self, user_id, chat_id, message_text, is_outgoing=False):
        self.cursor.execute('''
            INSERT INTO private_messages (user_id, chat_id, message_text, sent_at, is_outgoing)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, chat_id, message_text, datetime.now().isoformat(), is_outgoing))
        self.conn.commit()

    def add_online_history(self, user_id, is_online):
        self.cursor.execute('''
            INSERT INTO online_history (user_id, online_time, is_online)
            VALUES (?, ?, ?)
        ''', (user_id, datetime.now().isoformat(), is_online))
        self.conn.commit()

    def get_online_history(self, user_id):
        self.cursor.execute('''
            SELECT online_time, is_online FROM online_history 
            WHERE user_id = ? ORDER BY online_time DESC LIMIT 100
        ''', (user_id,))
        return self.cursor.fetchall()

    def get_messages_count_from_chats(self, user_id):
        self.cursor.execute('''
            SELECT COUNT(*)
            FROM messages m
            JOIN tracked_chats t ON m.chat_id = t.chat_id
            WHERE m.user_id = ? AND t.is_active = TRUE
        ''', (user_id,))
        return self.cursor.fetchone()[0]

    def get_messages_from_chats(self, user_id, limit=20, offset=0):
        self.cursor.execute('''
            SELECT m.chat_name, m.chat_link, m.message_text, m.sent_at, 
                   m.is_media, m.media_type, m.is_reply, m.message_id, m.chat_id, m.analyzed_type
            FROM messages m
            JOIN tracked_chats t ON m.chat_id = t.chat_id
            WHERE m.user_id = ? AND t.is_active = TRUE
            ORDER BY m.sent_at DESC
            LIMIT ? OFFSET ?
        ''', (user_id, limit, offset))
        return self.cursor.fetchall()

    def get_messages_by_type_from_chats(self, user_id, msg_type, limit=20, offset=0):
        query = """
            SELECT m.chat_name, m.chat_link, m.message_text, m.sent_at, 
                   m.is_media, m.media_type, m.is_reply, m.message_id, m.chat_id, m.analyzed_type
            FROM messages m
            JOIN tracked_chats t ON m.chat_id = t.chat_id
            WHERE m.user_id = ? AND t.is_active = TRUE
        """
        params = [user_id]

        if msg_type == "voice":
            query += " AND m.media_type = 'voice'"
        elif msg_type == "video":
            query += " AND m.media_type = 'video'"
        elif msg_type == "gif":
            query += " AND m.media_type = 'gif'"
        elif msg_type == "sticker":
            query += " AND m.media_type = 'sticker'"
        elif msg_type == "photo":
            query += " AND m.media_type = 'photo'"
        elif msg_type == "file":
            query += " AND m.media_type = 'document'"
        elif msg_type == "reply":
            query += " AND m.is_reply = TRUE"
        elif msg_type == "link":
            query += " AND m.message_text LIKE '%http%'"

        query += " ORDER BY m.sent_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        self.cursor.execute(query, params)
        return self.cursor.fetchall()

    def get_messages_count_by_type_from_chats(self, user_id, msg_type):
        query = """
            SELECT COUNT(*)
            FROM messages m
            JOIN tracked_chats t ON m.chat_id = t.chat_id
            WHERE m.user_id = ? AND t.is_active = TRUE
        """
        params = [user_id]

        if msg_type == "voice":
            query += " AND m.media_type = 'voice'"
        elif msg_type == "video":
            query += " AND m.media_type = 'video'"
        elif msg_type == "gif":
            query += " AND m.media_type = 'gif'"
        elif msg_type == "sticker":
            query += " AND m.media_type = 'sticker'"
        elif msg_type == "photo":
            query += " AND m.media_type = 'photo'"
        elif msg_type == "file":
            query += " AND m.media_type = 'document'"
        elif msg_type == "reply":
            query += " AND m.is_reply = TRUE"
        elif msg_type == "link":
            query += " AND m.message_text LIKE '%http%'"

        self.cursor.execute(query, params)
        return self.cursor.fetchone()[0]

    def get_user_activity(self, user_id):
        self.cursor.execute('''
            SELECT hour, SUM(message_count) as total 
            FROM user_activity 
            WHERE user_id = ? 
            GROUP BY hour 
            ORDER BY total DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    def get_user_chats(self, user_id):
        self.cursor.execute('''
            SELECT chat_name, chat_link, chat_type, participants_count, is_admin, is_owner 
            FROM user_chats 
            WHERE user_id = ?
        ''', (user_id,))
        return self.cursor.fetchall()

    def get_private_contacts(self, user_id):
        self.cursor.execute('SELECT DISTINCT chat_id FROM private_messages WHERE user_id = ?', (user_id,))
        return self.cursor.fetchall()

    def add_follower(self, target_user_id, follower_user_id):
        self.cursor.execute('''
            INSERT OR REPLACE INTO followers (target_user_id, follower_user_id, created_at)
            VALUES (?, ?, ?)
        ''', (target_user_id, follower_user_id, datetime.now().isoformat()))
        self.conn.commit()

    def remove_follower(self, target_user_id, follower_user_id):
        self.cursor.execute('DELETE FROM followers WHERE target_user_id = ? AND follower_user_id = ?',
                            (target_user_id, follower_user_id))
        self.conn.commit()

    def get_followers(self, target_user_id):
        self.cursor.execute('SELECT follower_user_id FROM followers WHERE target_user_id = ?', (target_user_id,))
        return self.cursor.fetchall()

    def is_following(self, target_user_id, follower_user_id):
        self.cursor.execute('SELECT id FROM followers WHERE target_user_id = ? AND follower_user_id = ?',
                            (target_user_id, follower_user_id))
        return self.cursor.fetchone() is not None

    def get_reactions_count(self, user_id):
        self.cursor.execute('''
            SELECT reaction_type, COUNT(*) as count FROM reactions 
            WHERE user_id = ? GROUP BY reaction_type
        ''', (user_id,))
        return self.cursor.fetchall()

    def get_message_analysis_stats(self, user_id):
        self.cursor.execute('SELECT * FROM message_analysis_stats WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()

    def update_message_analysis_stats(self, user_id, message_text, is_reply, is_media, media_type):
        self.cursor.execute('SELECT * FROM message_analysis_stats WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        word_count = len(message_text.split()) if message_text else 0

        if result:
            updates = {
                'total_messages': result[1] + 1,
                'total_replies': result[2] + (1 if is_reply else 0),
                'total_media': result[3] + (1 if is_media else 0)
            }
            if media_type == 'voice':
                updates['total_voice'] = result[4] + 1
            elif media_type == 'video':
                updates['total_video'] = result[5] + 1
            elif media_type == 'gif':
                updates['total_gifs'] = (result[6] if len(result) > 6 else 0) + 1
            elif media_type == 'sticker':
                updates['total_stickers'] = (result[7] if len(result) > 7 else 0) + 1

            self.cursor.execute('''
                UPDATE message_analysis_stats SET 
                    total_messages = ?,
                    total_replies = ?,
                    total_media = ?,
                    total_voice = ?,
                    total_video = ?,
                    avg_words_per_message = (total_messages * avg_words_per_message + ?) / (total_messages + 1),
                    last_updated = ?
                WHERE user_id = ?
            ''', (updates['total_messages'], updates['total_replies'], updates['total_media'],
                  updates.get('total_voice', result[4] if len(result) > 4 else 0),
                  updates.get('total_video', result[5] if len(result) > 5 else 0),
                  word_count, datetime.now().isoformat(), user_id))
        else:
            self.cursor.execute('''
                INSERT INTO message_analysis_stats (
                    user_id, total_messages, total_replies, total_media, 
                    total_voice, total_video, avg_words_per_message, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, 1, 1 if is_reply else 0, 1 if is_media else 0,
                  1 if media_type == 'voice' else 0,
                  1 if media_type == 'video' else 0,
                  word_count, datetime.now().isoformat()))
        self.conn.commit()

    def update_favorite_chat(self, user_id, chat_id, chat_name):
        self.cursor.execute('SELECT favorite_chat_id, favorite_chat_name FROM message_analysis_stats WHERE user_id = ?',
                            (user_id,))
        result = self.cursor.fetchone()
        if result:
            self.cursor.execute('''
                UPDATE message_analysis_stats SET favorite_chat_id = ?, favorite_chat_name = ?
                WHERE user_id = ?
            ''', (chat_id, chat_name, user_id))
        else:
            self.cursor.execute('''
                INSERT INTO message_analysis_stats (user_id, favorite_chat_id, favorite_chat_name)
                VALUES (?, ?, ?)
            ''', (user_id, chat_id, chat_name))
        self.conn.commit()

    def add_analysis(self, user_id, message_id, analysis_type, value):
        self.cursor.execute('''
            INSERT INTO message_analysis (user_id, message_id, analysis_type, value, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, message_id, analysis_type, value, datetime.now().isoformat()))
        self.conn.commit()
        self.cursor.execute('''
            UPDATE messages SET analyzed_type = ? WHERE id = ?
        ''', (analysis_type, message_id))
        self.conn.commit()

    def get_analysis_by_type(self, user_id, analysis_type):
        self.cursor.execute('''
            SELECT m.chat_name, m.chat_link, m.message_text, m.sent_at, m.message_id, m.chat_id
            FROM messages m
            JOIN message_analysis ma ON m.id = ma.message_id
            WHERE ma.user_id = ? AND ma.analysis_type = ?
            ORDER BY m.sent_at DESC
            LIMIT 50
        ''', (user_id, analysis_type))
        return self.cursor.fetchall()

    def analyze_message_type(self, message_text):
        if not message_text:
            return 'other'

        message_text = message_text.lower()

        age_patterns = [
            r'\b\d{1,3}\s*год[а]?',
            r'\b\d{1,3}\s*лет',
            r'\b\d{1,3}\s*г\.',
            r'\bвозраст\s*\d{1,3}',
            r'\bмне\s*\d{1,3}',
        ]
        for pattern in age_patterns:
            if re.search(pattern, message_text):
                return 'age'

        name_patterns = [
            r'\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+',
            r'\b[А-ЯЁ][а-яё]+\b',
        ]
        for pattern in name_patterns:
            if re.search(pattern, message_text):
                return 'name'

        cities = ['москва', 'спб', 'санкт-петербург', 'киев', 'минск', 'алматы', 'новосибирск',
                  'екатеринбург', 'нижний новгород', 'казань', 'челябинск', 'омск', 'самара',
                  'ростов-на-дону', 'уфа', 'красноярск', 'воронеж', 'пермь', 'волгоград']
        for city in cities:
            if city in message_text:
                return 'city'

        if '@' in message_text:
            return 'mention'

        likes = ['люблю', 'нравится', 'обожаю', 'кайф', 'топ', 'лучший', 'класс', 'супер',
                 'вкусно', 'красиво', 'круто', 'мощно', 'огонь', 'бомба', 'имба']
        for word in likes:
            if word in message_text:
                return 'likes'

        return 'other'

    def get_city_stats(self, user_id):
        self.cursor.execute('''
            SELECT city, message_count, last_mentioned 
            FROM city_stats 
            WHERE user_id = ? 
            ORDER BY message_count DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    def update_city_stats(self, user_id, city):
        self.cursor.execute('''
            INSERT INTO city_stats (user_id, city, message_count, last_mentioned)
            VALUES (?, ?, 1, ?)
            ON CONFLICT (user_id, city) DO UPDATE 
            SET message_count = message_count + 1,
                last_mentioned = ?
        ''', (user_id, city, datetime.now().isoformat(), datetime.now().isoformat()))
        self.conn.commit()

    def get_city_messages_detailed(self, user_id, city):
        self.cursor.execute('''
            SELECT message_text, sent_at, chat_name, chat_link, message_id
            FROM messages 
            WHERE user_id = ? AND message_text LIKE ?
            ORDER BY sent_at DESC
            LIMIT 50
        ''', (user_id, f'%{city}%'))
        return self.cursor.fetchall()

    def get_mention_stats_detailed(self, user_id):
        user = self.get_user_info(user_id)
        if not user:
            return {'mentions': [], 'mentioned_by': []}

        username = user[1]

        self.cursor.execute('''
            SELECT message_text, sent_at, chat_name, chat_link, message_id
            FROM messages 
            WHERE user_id = ? AND message_text LIKE '%@%'
            ORDER BY sent_at DESC
            LIMIT 50
        ''', (user_id,))
        mentions = self.cursor.fetchall()

        if username:
            self.cursor.execute('''
                SELECT m.message_text, m.sent_at, m.chat_name, m.chat_link, m.message_id, m.user_id
                FROM messages m
                WHERE m.message_text LIKE ? AND m.user_id != ?
                ORDER BY m.sent_at DESC
                LIMIT 50
            ''', (f'%@{username}%', user_id))
            mentioned_by = self.cursor.fetchall()
        else:
            mentioned_by = []

        return {'mentions': mentions, 'mentioned_by': mentioned_by}

    def get_activity_peaks(self, user_id):
        self.cursor.execute('''
            SELECT peak_hour, day_of_week, message_count, date 
            FROM activity_peaks 
            WHERE user_id = ? 
            ORDER BY message_count DESC 
            LIMIT 10
        ''', (user_id,))
        return self.cursor.fetchall()

    def update_activity_peaks(self, user_id, hour, day_of_week, count, date):
        self.cursor.execute('''
            INSERT OR REPLACE INTO activity_peaks (user_id, peak_hour, day_of_week, message_count, date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, hour, day_of_week, count, date))
        self.conn.commit()

    def get_premium_history(self, user_id):
        user = self.get_user_info(user_id)
        if not user:
            return []
        premium_history = user[12] if len(user) > 12 else None
        if premium_history:
            try:
                return eval(premium_history)
            except:
                return []
        return []

    def add_premium_record(self, user_id, start_date, end_date):
        history = self.get_premium_history(user_id)
        history.append({'start': start_date, 'end': end_date})
        self.cursor.execute('''
            UPDATE users SET premium_history = ? WHERE user_id = ?
        ''', (str(history), user_id))
        self.conn.commit()


db = Database(DB_NAME)


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def format_last_seen(status):
    if status is None:
        return "Неизвестно"
    if isinstance(status, UserStatusOnline):
        return "🟢 В сети"
    elif isinstance(status, UserStatusOffline):
        try:
            was_online = status.was_online
            if was_online:
                now = datetime.now().astimezone()
                delta = now - was_online
                if delta.days > 0:
                    return f"Был(а) {delta.days} дн. назад"
                elif delta.seconds > 3600:
                    hours = delta.seconds // 3600
                    return f"Был(а) {hours} ч. назад"
                elif delta.seconds > 60:
                    minutes = delta.seconds // 60
                    return f"Был(а) {minutes} мин. назад"
                else:
                    return "Был(а) только что"
            return "Недавно"
        except:
            return "Недавно"
    elif isinstance(status, UserStatusRecently):
        return "🟡 Был(а) недавно"
    elif isinstance(status, UserStatusLastWeek):
        return "🟡 Был(а) на прошлой неделе"
    elif isinstance(status, UserStatusLastMonth):
        return "🟡 Был(а) в прошлом месяце"
    else:
        return f"Статус: {str(status)}"


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С TELEGRAM ====================

async def get_chat_link(chat_id):
    try:
        entity = await user_client.get_entity(chat_id)
        if hasattr(entity, 'username') and entity.username:
            return f"https://t.me/{entity.username}"
        elif hasattr(entity, 'id'):
            return f"https://t.me/c/{str(entity.id)[4:]}"
        return None
    except:
        return None


async def get_chat_participants_count(chat_id):
    try:
        entity = await user_client.get_entity(chat_id)
        if hasattr(entity, 'megagroup') or hasattr(entity, 'group'):
            try:
                full = await user_client(GetFullChannelRequest(entity))
                return full.full_chat.participants_count if hasattr(full.full_chat, 'participants_count') else 0
            except:
                pass
        elif hasattr(entity, 'channel'):
            try:
                full = await user_client(GetFullChannelRequest(entity))
                return full.full_chat.participants_count if hasattr(full.full_chat, 'participants_count') else 0
            except:
                pass
        return 0
    except:
        return 0


async def collect_user_data(user_id):
    try:
        if str(user_id).startswith('-100'):
            return None

        entity = await user_client.get_entity(user_id)
        full_user = await user_client(GetFullUserRequest(entity))
        user = full_user.users[0] if full_user.users else entity

        is_premium = hasattr(user, 'premium') and user.premium
        last_seen = format_last_seen(user.status) if hasattr(user, 'status') else None

        if hasattr(user, 'status'):
            if isinstance(user.status, UserStatusOnline):
                db.add_online_history(user_id, True)
            elif isinstance(user.status, UserStatusOffline):
                db.add_online_history(user_id, False)

        premium_start = None
        premium_end = None
        if is_premium:
            try:
                if hasattr(full_user, 'premium_gifts'):
                    for gift in full_user.premium_gifts:
                        if hasattr(gift, 'date'):
                            premium_start = gift.date.isoformat() if gift.date else None
            except:
                pass

        return {
            'id': user.id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'phone': getattr(user, 'phone', None),
            'is_premium': is_premium,
            'last_seen': last_seen,
            'premium_start': premium_start,
            'premium_end': premium_end
        }
    except Exception as e:
        if 'Cannot cast' not in str(e):
            logger.error(f"Error collecting user data: {e}")
        return None


async def collect_all_chat_messages(chat_id, chat_limit=10000000, user_limit=100000):
    try:
        entity = await user_client.get_entity(chat_id)
        chat_name = entity.title if hasattr(entity, 'title') else "Unknown"
        chat_link = await get_chat_link(chat_id)
        chat_type = 'group' if hasattr(entity, 'megagroup') else 'channel'
        participants_count = await get_chat_participants_count(chat_id)

        try:
            if hasattr(entity, 'megagroup') or hasattr(entity, 'group') or hasattr(entity, 'channel'):
                try:
                    await user_client(JoinChannelRequest(entity))
                except UserAlreadyParticipantError:
                    pass
                except Exception as e:
                    logger.error(f"Join error: {e}")
        except:
            pass

        is_public = True
        if hasattr(entity, 'username') and entity.username:
            is_public = True
        else:
            is_public = False

        db.add_tracked_chat(entity.id, chat_name, chat_link, chat_type, participants_count, 0, True, 'joined',
                            is_public)

        total_messages = 0
        offset_id = 0
        user_message_counts = defaultdict(int)

        print(f"⏳ Начинаю сбор сообщений из {chat_name}...")

        while total_messages < chat_limit:
            try:
                messages = await user_client(GetHistoryRequest(
                    peer=entity,
                    limit=min(100, chat_limit - total_messages),
                    offset_date=None,
                    offset_id=offset_id,
                    max_id=0,
                    min_id=0,
                    add_offset=0,
                    hash=0
                ))

                if not messages.messages:
                    break

                for msg in messages.messages:
                    if msg.sender_id:
                        if user_message_counts[msg.sender_id] >= user_limit:
                            continue

                        try:
                            sender = await user_client.get_entity(msg.sender_id)

                            is_media = bool(msg.media)
                            media_type = None
                            if is_media:
                                if hasattr(msg.media, 'document'):
                                    if hasattr(msg.media.document,
                                               'mime_type') and 'gif' in msg.media.document.mime_type:
                                        media_type = 'gif'
                                    else:
                                        media_type = 'document'
                                elif hasattr(msg.media, 'photo'):
                                    media_type = 'photo'
                                elif hasattr(msg.media, 'voice'):
                                    media_type = 'voice'
                                elif hasattr(msg.media, 'video'):
                                    if hasattr(msg.media.video, 'round') and msg.media.video.round:
                                        media_type = 'video'
                                    else:
                                        media_type = 'video'

                            is_reply = bool(msg.reply_to)

                            user_info = db.get_user_info(sender.id)
                            if not user_info:
                                db.add_user(
                                    sender.id,
                                    sender.username if sender.username else None,
                                    sender.first_name if hasattr(sender, 'first_name') else None,
                                    sender.last_name if hasattr(sender, 'last_name') else None
                                )

                            message_text = msg.text if msg.text else ""
                            if is_media and not message_text:
                                message_text = f"[{media_type or 'медиа'}]"

                            cities = ['москва', 'спб', 'санкт-петербург', 'киев', 'минск', 'алматы', 'новосибирск',
                                      'екатеринбург', 'нижний новгород', 'казань', 'челябинск', 'омск', 'самара',
                                      'ростов-на-дону', 'уфа', 'красноярск', 'воронеж', 'пермь', 'волгоград']
                            if message_text:
                                for city in cities:
                                    if city in message_text.lower():
                                        db.update_city_stats(sender.id, city)

                            msg_date = datetime.fromisoformat(msg.date.isoformat())
                            hour = msg_date.hour
                            day_of_week = msg_date.weekday()

                            db.cursor.execute('''
                                INSERT INTO user_activity (user_id, date, hour, message_count)
                                VALUES (?, ?, ?, 1)
                                ON CONFLICT (user_id, date, hour) DO UPDATE 
                                SET message_count = message_count + 1
                            ''', (sender.id, msg.date.date().isoformat(), hour))
                            db.conn.commit()

                            db.cursor.execute('''
                                SELECT COUNT(*) FROM activity_peaks 
                                WHERE user_id = ? AND peak_hour = ? AND day_of_week = ?
                            ''', (sender.id, hour, day_of_week))
                            existing = db.cursor.fetchone()[0]
                            if existing:
                                db.cursor.execute('''
                                    UPDATE activity_peaks 
                                    SET message_count = message_count + 1,
                                        date = ?
                                    WHERE user_id = ? AND peak_hour = ? AND day_of_week = ?
                                ''', (msg.date.date().isoformat(), sender.id, hour, day_of_week))
                            else:
                                db.cursor.execute('''
                                    INSERT INTO activity_peaks (user_id, peak_hour, day_of_week, message_count, date)
                                    VALUES (?, ?, ?, 1, ?)
                                ''', (sender.id, hour, day_of_week, msg.date.date().isoformat()))
                            db.conn.commit()

                            db.add_message(
                                sender.id,
                                entity.id,
                                chat_name,
                                chat_link,
                                message_text,
                                msg.date.isoformat(),
                                msg.id,
                                is_media,
                                media_type,
                                is_reply,
                                msg.reply_to.reply_to_msg_id if msg.reply_to else None
                            )

                            db.add_user_chat(
                                sender.id,
                                entity.id,
                                chat_name,
                                chat_link,
                                chat_type,
                                participants_count,
                                False,
                                False
                            )

                            total_messages += 1
                            user_message_counts[msg.sender_id] += 1

                            if total_messages % 1000 == 0:
                                print(f"Собрано {total_messages} сообщений из {chat_name}")

                        except Exception as e:
                            logger.error(f"Error processing message: {e}")

                if messages.messages:
                    offset_id = messages.messages[-1].id
                else:
                    break

            except FloodWaitError as e:
                wait = e.seconds
                print(f"⏳ Flood wait {wait} секунд...")
                await asyncio.sleep(min(wait, 60))
            except Exception as e:
                logger.error(f"Error in collect loop: {e}")
                break

        print(f"✅ Собрано {total_messages} сообщений из {chat_name}")
        return total_messages

    except Exception as e:
        logger.error(f"Error collecting chat messages: {e}")
        return 0


async def check_is_user(username: str) -> bool:
    """Проверяет, является ли username пользователем (не каналом/группой)"""
    try:
        # Пробуем получить entity
        entity = await user_client.get_entity(f'@{username}')

        # Проверяем по атрибутам
        # Если есть first_name и нет title - это пользователь
        if hasattr(entity, 'first_name') and entity.first_name:
            return True

        # Если есть username и нет title - это пользователь
        if hasattr(entity, 'username') and not hasattr(entity, 'title'):
            return True

        # Проверяем, является ли это ботом
        if hasattr(entity, 'bot') and entity.bot:
            return True

        # Проверяем, является ли это каналом
        if hasattr(entity, 'channel') and entity.channel:
            return False

        # Проверяем, является ли это группой
        if hasattr(entity, 'megagroup') and entity.megagroup:
            return False
        if hasattr(entity, 'group') and entity.group:
            return False

        # Если есть title и нет first_name - это чат
        if hasattr(entity, 'title') and not hasattr(entity, 'first_name'):
            return False

        # По умолчанию считаем, что это пользователь
        return True

    except ValueError as e:
        # Ошибка "Cannot find any entity" - пользователь не найден
        logger.error(f"User @{username} not found: {e}")
        return False
    except Exception as e:
        logger.error(f"Error checking user @{username}: {e}")
        return False


# ==================== СБОР ДАННЫХ ЧЕРЕЗ БОТА ====================

async def collect_user_data_from_bot(username: str) -> Dict:
    """Собирает данные о пользователе через бота @getSendGiftsProBot"""
    try:
        # ✅ ПРОВЕРЯЕМ, СУЩЕСТВУЕТ ЛИ ПОЛЬЗОВАТЕЛЬ
        try:
            entity = await user_client.get_entity(f'@{username}')
        except ValueError as e:
            return {
                'error': 'not_found',
                'message': f'❌ Пользователь @{username} не найден в Telegram'
            }
        except Exception as e:
            logger.error(f"Error getting entity for @{username}: {e}")
            return {
                'error': 'error',
                'message': f'❌ Ошибка при поиске @{username}: {str(e)}'
            }

        # ✅ ПРОВЕРЯЕМ, ЯВЛЯЕТСЯ ЛИ ЭТО ПОЛЬЗОВАТЕЛЕМ (НЕ КАНАЛ/ГРУППА)
        is_user = await check_is_user(username)

        if not is_user:
            # Это канал или группа
            return {
                'error': 'not_user',
                'message': f'❌ @{username} - это канал или группа, а не пользователь!'
            }

        # ✅ ПРОВЕРЯЕМ, НЕ ЯВЛЯЕТСЯ ЛИ ЭТО БОТОМ
        if hasattr(entity, 'bot') and entity.bot:
            return {
                'error': 'is_bot',
                'message': f'❌ @{username} - это бот, а не пользователь!'
            }

        bot_entity = await user_client.get_entity('getSendGiftsProBot')

        # Проверяем последние сообщения, чтобы не дублировать
        last_messages = await user_client.get_messages(bot_entity, limit=3)
        last_text = last_messages[0].text if last_messages else ""

        if username in last_text:
            logger.info(f"⏭️ Пропускаем дублирующий запрос для @{username}")
        else:
            await user_client.send_message(bot_entity, username)
            logger.info(f"📤 Отправлен запрос данных для @{username}")
            await asyncio.sleep(6)

        messages = await user_client.get_messages(bot_entity, limit=30)

        if not messages:
            return {
                'error': 'no_data',
                'message': f'❌ Не удалось получить данные для @{username}'
            }

        full_text = ""
        for msg in messages:
            if msg.text:
                if msg.reply_markup:
                    continue
                full_text += msg.text + "\n"

        if not full_text:
            for msg in messages:
                if msg.text:
                    full_text += msg.text + "\n"

        if not full_text:
            return {
                'error': 'no_data',
                'message': f'❌ Не удалось получить данные для @{username}'
            }

        # Проверяем, есть ли в ответе признаки того, что пользователь не найден
        if "не найден" in full_text.lower() or "not found" in full_text.lower() or "нет данных" in full_text.lower():
            return {
                'error': 'not_found',
                'message': f'❌ Пользователь @{username} не найден в базе бота @getSendGiftsProBot'
            }

        user_data = {
            'username': username,
            'first_name': username,
            'last_name': '',
            'birthday': None,
            'old_names': [],
            'old_usernames': [],
            'reputation': {'likes': 0, 'dislikes': 0},
            'interests': 0,
            'user_id': None,
            'error': None
        }

        # Парсим имя пользователя
        name_match = re.search(r'\*\*\s*\[([^\]]+)\]', full_text)
        if name_match:
            user_data['first_name'] = name_match.group(1).strip()

        # Парсим ID
        id_match = re.search(r'\|\s*`?(\d+)`?', full_text)
        if id_match:
            user_data['user_id'] = int(id_match.group(1))

        # Парсим день рождения
        birthday_match = re.search(r'День рождения:\s*([^\n]+)', full_text)
        if birthday_match:
            birthday = birthday_match.group(1).strip()
            if birthday != 'неизвестно':
                user_data['birthday'] = birthday

        # Парсим репутацию
        rep_match = re.search(r'Репутация:\s*👍\((\d+)\)\s*👎\((\d+)\)', full_text)
        if not rep_match:
            rep_match = re.search(r'Репутация:\s*🐦\((\d+)\)\s*🐦\((\d+)\)', full_text)
        if rep_match:
            user_data['reputation']['likes'] = int(rep_match.group(1))
            user_data['reputation']['dislikes'] = int(rep_match.group(2))

        # Парсим количество интересовавшихся
        interests_match = re.search(r'Интересовались:\s*(\d+)\s*раз\(а\)', full_text)
        if interests_match:
            user_data['interests'] = int(interests_match.group(1))

        # Парсим старые имена
        name_section = re.search(
            r'Последние 10 изменений имен:(.*?)(?=Последние 10 изменений фамилий|Последние 10 изменений ссылок|Последние 10 NFT|$)',
            full_text, re.DOTALL)
        if name_section:
            names = re.findall(r'(.+?)\s*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\)', name_section.group(1))
            for name, date in names:
                user_data['old_names'].append({
                    'name': name.strip(),
                    'date': date
                })

        # Парсим старые фамилии
        lastname_section = re.search(
            r'Последние 10 изменений фамилий:(.*?)(?=Последние 10 изменений ссылок|Последние 10 NFT|$)', full_text,
            re.DOTALL)
        if lastname_section:
            lastnames = re.findall(r'(.+?)\s*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\)', lastname_section.group(1))
            for lastname, date in lastnames:
                if user_data['old_names']:
                    last_item = user_data['old_names'][-1]
                    if last_item['name']:
                        user_data['old_names'][-1]['name'] = f"{last_item['name']} {lastname.strip()}"

        # Парсим старые юзернеймы
        username_section = re.search(r'Последние 10 изменений ссылок на профиль:(.*?)(?=Последние 10 NFT|$)', full_text,
                                     re.DOTALL)
        if not username_section:
            username_section = re.search(r'Последние 10 изменений ссылок:(.*?)(?=Последние 10 NFT|$)', full_text,
                                         re.DOTALL)
        if username_section:
            usernames = re.findall(r'@(\w+)\s*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\)', username_section.group(1))
            for uname, date in usernames:
                user_data['old_usernames'].append({
                    'username': uname,
                    'date': date
                })

        return user_data

    except Exception as e:
        logger.error(f"Error collecting user data from bot: {e}")
        return {
            'error': 'exception',
            'message': f'❌ Ошибка при сборе данных для @{username}: {str(e)}'
        }


async def save_user_data_from_bot(username: str):
    """Сохраняет данные о пользователе, полученные от бота"""
    try:
        user_data = await collect_user_data_from_bot(username)

        # ✅ ПРОВЕРЯЕМ НА ОШИБКИ
        if not user_data:
            return False, f"❌ Не удалось получить данные для @{username}"

        if user_data.get('error'):
            return False, user_data.get('message', f'❌ Ошибка при сборе данных для @{username}')

        # Проверяем, есть ли данные о пользователе
        if not user_data.get('user_id') and not user_data.get('old_names') and not user_data.get('old_usernames'):
            return False, f"❌ Для @{username} не найдено данных (возможно, это канал или группа)"

        user_info = db.get_user_by_username(username)

        if user_info:
            user_id = user_info[0]
        else:
            user_id = abs(hash(username)) % 1000000000
            db.add_user(
                user_id,
                username,
                user_data.get('first_name', username),
                user_data.get('last_name', ''),
                False,
                None,
                None,
                user_data.get('birthday')
            )

        # Сохраняем старые имена
        name_count = 0
        for name_data in user_data.get('old_names', []):
            try:
                date_str = name_data['date']
                date_match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})', date_str)
                if date_match:
                    day, month, year, hour, minute = date_match.groups()
                    changed_at = datetime(int(year), int(month), int(day), int(hour), int(minute))
                    changed_at_str = changed_at.isoformat()

                    name_parts = name_data['name'].split()
                    first_name = name_parts[0] if name_parts else name_data['name']
                    last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                    db.add_old_name_import(user_id, first_name, last_name, changed_at_str)
                    name_count += 1
            except Exception as e:
                logger.error(f"Error saving old name: {e}")

        # Сохраняем старые юзернеймы
        username_count = 0
        for uname_data in user_data.get('old_usernames', []):
            try:
                date_str = uname_data['date']
                date_match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})', date_str)
                if date_match:
                    day, month, year, hour, minute = date_match.groups()
                    changed_at = datetime(int(year), int(month), int(day), int(hour), int(minute))
                    changed_at_str = changed_at.isoformat()

                    db.add_old_username_import(user_id, uname_data['username'], changed_at_str)
                    username_count += 1
            except Exception as e:
                logger.error(f"Error saving old username: {e}")

        # Проверяем, сохранилось ли хоть что-то
        if name_count == 0 and username_count == 0:
            return False, f"❌ Для @{username} не найдено старых имен или юзернеймов"

        report = f"✅ Данные пользователя @{username} успешно сохранены!\n\n"
        report += f"📊 Статистика:\n"
        report += f"• Старых имен: {name_count}\n"
        report += f"• Старых юзернеймов: {username_count}"

        return True, report

    except Exception as e:
        logger.error(f"Error saving user data: {e}")
        return False, f"❌ Ошибка при сохранении данных для @{username}: {str(e)}"


# ==================== КЛАВИАТУРЫ ====================

def create_profile_keyboard(user_id, is_admin=False):
    keyboard = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data=f"profile_{user_id}"),
         InlineKeyboardButton(text="📊 Анализ", callback_data=f"analysis_{user_id}")],
        [InlineKeyboardButton(text="💬 Сообщения", callback_data=f"msgs_{user_id}"),
         InlineKeyboardButton(text="📛 Имена/Теги", callback_data=f"names_{user_id}")],
        [InlineKeyboardButton(text="⭐ Премиум", callback_data=f"premium_{user_id}"),
         InlineKeyboardButton(text="👥 Знакомые", callback_data=f"contacts_{user_id}")],
        [InlineKeyboardButton(text="❤️ Реакции", callback_data=f"reactions_{user_id}"),
         InlineKeyboardButton(text="🏙 Города", callback_data=f"cities_{user_id}")],
        [InlineKeyboardButton(text="⏰ Пик активности", callback_data=f"peak_{user_id}"),
         InlineKeyboardButton(text="🕐 Время", callback_data=f"time_{user_id}")]
    ]

    if is_admin:
        keyboard.append([
            InlineKeyboardButton(text="📛 Д.стар.имена", callback_data=f"import_names_{user_id}"),
            InlineKeyboardButton(text="🔤 Д.стар.юзернеймы", callback_data=f"import_usernames_{user_id}")
        ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_messages_keyboard(user_id, current_type="all", page=1):
    total = db.get_messages_count_by_type_from_chats(user_id, current_type)
    pages = (total // 20) + 1 if total > 0 else 1

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Все", callback_data=f"msg_type_{user_id}_all_1"),
         InlineKeyboardButton(text="🎤 Голосовые", callback_data=f"msg_type_{user_id}_voice_1")],
        [InlineKeyboardButton(text="📹 Видео", callback_data=f"msg_type_{user_id}_video_1"),
         InlineKeyboardButton(text="🖼️ Картинки", callback_data=f"msg_type_{user_id}_photo_1")],
        [InlineKeyboardButton(text="🎭 GIF/Стикеры", callback_data=f"msg_type_{user_id}_gif_1"),
         InlineKeyboardButton(text="🔗 Ссылки", callback_data=f"msg_type_{user_id}_link_1")],
        [InlineKeyboardButton(text="📄 Файлы", callback_data=f"msg_type_{user_id}_file_1"),
         InlineKeyboardButton(text="💬 Ответы", callback_data=f"msg_type_{user_id}_reply_1")]
    ])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"msg_page_{user_id}_{current_type}_{page - 1}"))
    if page < pages:
        nav_buttons.append(
            InlineKeyboardButton(text="Вперед ▶️", callback_data=f"msg_page_{user_id}_{current_type}_{page + 1}"))
    if nav_buttons:
        keyboard.inline_keyboard.append(nav_buttons)

    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text=f"📊 {total} сообщений | Стр. {page}/{pages}", callback_data="msg_info")
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"profile_{user_id}")
    ])
    return keyboard


def create_main_keyboard(user_id):
    counts = db.get_tracked_chats_count()
    groups_count = counts[0] if counts else 0
    channels_count = counts[1] if counts else 0

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="search"),
         InlineKeyboardButton(text="📊 Мои данные", callback_data=f"my_data_{user_id}")],
        [InlineKeyboardButton(text=f"📋 Группы ({groups_count})", callback_data="groups"),
         InlineKeyboardButton(text=f"📢 Каналы ({channels_count})", callback_data="channels")],
        [InlineKeyboardButton(text="📖 Команды", callback_data="commands")]
    ])

    if user_id == ADMIN_ID:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_panel")
        ])
    return keyboard


def create_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🙈 Скрыть пользователя", callback_data="admin_hide"),
         InlineKeyboardButton(text="👁️ Показать пользователя", callback_data="admin_unhide")],
        [InlineKeyboardButton(text="📊 Все пользователи", callback_data="admin_users"),
         InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📋 Отслеживаемые чаты", callback_data="admin_chats"),
         InlineKeyboardButton(text="🗑️ Удалить чат", callback_data="admin_delete_chat")],
        [InlineKeyboardButton(text="📥 Собрать данные из бота", callback_data="admin_collect")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])


def create_analysis_keyboard(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙 Города", callback_data=f"analysis_cities_{user_id}"),
         InlineKeyboardButton(text="👤 Имена", callback_data=f"analysis_names_{user_id}")],
        [InlineKeyboardButton(text="✍️ Кого отмечает", callback_data=f"analysis_mentions_{user_id}"),
         InlineKeyboardButton(text="📢 Кто отмечает", callback_data=f"analysis_mentioned_{user_id}")],
        [InlineKeyboardButton(text="🎂 Возраст", callback_data=f"analysis_age_{user_id}"),
         InlineKeyboardButton(text="❤️ Любовь", callback_data=f"analysis_love_{user_id}")]
    ])


# ==================== ХЕНДЛЕРЫ КОМАНД ====================

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    db.add_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я собираю ВСЕ сообщения и анализирую профили!\n\n"
        "📌 Нажмите '📖 Команды' чтобы увидеть список команд.\n\n"
        "💡 Просто отправь юзернейм или ссылку:\n"
        "• @username - покажу профиль\n"
        "• @channel - соберу сообщения\n"
        "• t.me/... - ссылка на чат - соберу сообщения",
        parse_mode="HTML",
        reply_markup=create_main_keyboard(user_id)
    )


@dp.message(Command("analyze"))
async def analyze_command(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Укажи юзернейм или ID: /analyze @username или /analyze 123456789")
        return
    query = parts[1].replace('@', '')

    is_user = await check_is_user(query)
    if is_user:
        await show_user_profile(message, query)
    else:
        status_msg = await message.answer(f"⏳ Обнаружен чат @{query}. Собираю сообщения...")
        try:
            entity = await user_client.get_entity(f'@{query}')
            count = await collect_all_chat_messages(entity.id, 10000000, 100000)
            await status_msg.edit_text(
                f"✅ Чат '{entity.title if hasattr(entity, 'title') else query}' добавлен!\n"
                f"📊 Собрано {count} сообщений"
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка: {str(e)}")


@dp.message(Command("search"))
async def search_command(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Укажи запрос для поиска: /search username")
        return
    query = parts[1]
    results = db.search_users_by_username(query)
    if not results:
        await message.answer(f"❌ Ничего не найдено по запросу '{query}'")
        return
    response = f"🔍 <b>Результаты поиска по '{query}':</b>\n\n"
    for user_id, username, first_name, last_name, is_hidden, is_premium, search_count in results:
        if is_hidden:
            response += f"🔒 {first_name or 'Без имени'}\n"
        else:
            premium = "⭐" if is_premium else ""
            response += f"{premium} {first_name or 'Без имени'} - @{username or 'Нет'}\n"
        response += f"   ID: <code>{user_id}</code> | 👀 {search_count or 0} поисков\n\n"
    await message.answer(response, parse_mode="HTML")


@dp.message(Command("groups"))
async def groups_command(message: types.Message):
    chats = db.get_tracked_chats()
    groups = [c for c in chats if c[3] in ['group', 'supergroup']]
    if not groups:
        await message.answer("📭 Нет отслеживаемых групп\n\nОтправьте ссылку на группу.")
        return
    response = f"<b>📋 Отслеживаемые группы ({len(groups)})</b>\n\n"
    for chat_id, chat_name, chat_link, chat_type, participants, added_at, added_by, is_active, is_joined, join_status, is_public in groups:
        if chat_link:
            response += f"• <a href='{chat_link}'>{chat_name}</a>"
        else:
            response += f"• {chat_name}"
        response += f" ({participants} участников)\n"
    await message.answer(response, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(Command("channels"))
async def channels_command(message: types.Message):
    chats = db.get_tracked_chats()
    channels = [c for c in chats if c[3] == 'channel']
    if not channels:
        await message.answer("📭 Нет отслеживаемых каналов\n\nОтправьте ссылку на канал.")
        return
    response = f"<b>📢 Отслеживаемые каналы ({len(channels)})</b>\n\n"
    for chat_id, chat_name, chat_link, chat_type, participants, added_at, added_by, is_active, is_joined, join_status, is_public in channels:
        if chat_link:
            response += f"• <a href='{chat_link}'>{chat_name}</a>"
        else:
            response += f"• {chat_name}"
        response += f" ({participants} подписчиков)\n"
    await message.answer(response, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(Command("hide"))
async def hide_user_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещен")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Укажи ID или @username: /hide @username 30")
        return
    query = parts[1].replace('@', '')
    days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    user_info = db.get_user_by_username(query) if not query.isdigit() else db.get_user_by_id(int(query))
    if not user_info:
        await message.answer(f"❌ Пользователь не найден")
        return
    db.hide_user(user_info[0], days)
    await message.answer(f"✅ Пользователь скрыт{' на ' + str(days) + ' дней' if days else ' навсегда'}")


@dp.message(Command("unhide"))
async def unhide_user_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещен")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Укажи ID или @username: /unhide @username")
        return
    query = parts[1].replace('@', '')
    user_info = db.get_user_by_username(query) if not query.isdigit() else db.get_user_by_id(int(query))
    if not user_info:
        await message.answer(f"❌ Пользователь не найден")
        return
    db.unhide_user(user_info[0])
    await message.answer(f"✅ Пользователь показан")


@dp.message(Command("deletechat"))
async def delete_chat_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещен")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Укажи ID чата: /deletechat 123456789")
        return
    try:
        chat_id = int(parts[1])
        db.delete_tracked_chat(chat_id)
        await message.answer(f"✅ Чат {chat_id} удален из отслеживания")
    except:
        await message.answer("❌ Неверный ID чата")


# ==================== КОМАНДА СБОРА ДАННЫХ ====================

@dp.message(Command("collect"))
async def collect_command(message: types.Message):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("❌ Только для администратора!")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Укажи юзернейм для сбора данных:\n"
            "<code>/collect @username</code>\n\n"
            "Или укажи список юзернеймов через запятую:\n"
            "<code>/collect @user1, @user2, @user3</code>",
            parse_mode="HTML"
        )
        return

    text = ' '.join(parts[1:])
    usernames = re.split(r'[,，\s]+', text)
    usernames = [u.replace('@', '').strip() for u in usernames if u.strip() and not u.startswith('/')]

    if not usernames:
        await message.answer("❌ Не найдено юзернеймов для сбора")
        return

    # ✅ ПРОВЕРЯЕМ КАЖДОГО ПОЛЬЗОВАТЕЛЯ ПЕРЕД НАЧАЛОМ СБОРА
    valid_users = []
    invalid_users = []

    status_msg = await message.answer("⏳ Проверка пользователей...")

    for username in usernames:
        try:
            # Пробуем получить entity
            entity = await user_client.get_entity(f'@{username}')

            # Проверяем, является ли это пользователем
            is_user = await check_is_user(username)

            if is_user:
                # Проверяем, не бот ли это
                if hasattr(entity, 'bot') and entity.bot:
                    invalid_users.append(f"@{username} - это бот")
                else:
                    valid_users.append(username)
            else:
                # Проверяем, что это не канал/группа
                if hasattr(entity, 'channel') and entity.channel:
                    invalid_users.append(f"@{username} - это канал")
                elif hasattr(entity, 'megagroup') and entity.megagroup:
                    invalid_users.append(f"@{username} - это группа")
                elif hasattr(entity, 'group') and entity.group:
                    invalid_users.append(f"@{username} - это группа")
                else:
                    invalid_users.append(f"@{username} - не является пользователем")

        except ValueError as e:
            invalid_users.append(f"@{username} - пользователь не найден в Telegram")
        except Exception as e:
            invalid_users.append(f"@{username} - ошибка: {str(e)}")

    if not valid_users:
        report = "❌ <b>Нет валидных пользователей для сбора!</b>\n\n"
        report += "⚠️ <b>Проблемы:</b>\n"
        for msg in invalid_users:
            report += f"• {msg}\n"
        await status_msg.edit_text(report, parse_mode="HTML")
        return

    await status_msg.edit_text(
        f"✅ Найдено {len(valid_users)} валидных пользователей\n"
        f"❌ Пропущено: {len(invalid_users)}\n\n"
        f"⏳ Начинаю сбор данных...\n"
        f"⚠️ Это может занять некоторое время..."
    )

    success_count = 0
    results = []
    total = len(valid_users)

    for i, username in enumerate(valid_users, 1):
        status_msg2 = await message.answer(f"⏳ [{i}/{total}] Сбор данных для @{username}...")

        try:
            success, result = await save_user_data_from_bot(username)
            if success:
                success_count += 1
                results.append(f"✅ @{username} - успешно")
                await status_msg2.edit_text(f"✅ [{i}/{total}] @{username} - данные сохранены")
            else:
                results.append(f"❌ @{username} - {result}")
                await status_msg2.edit_text(f"❌ [{i}/{total}] @{username} - {result}")
        except Exception as e:
            results.append(f"❌ @{username} - ошибка: {str(e)}")
            await status_msg2.edit_text(f"❌ [{i}/{total}] @{username} - ошибка: {str(e)}")

        await asyncio.sleep(5)

    # ✅ ДОБАВЛЯЕМ СПИСОК ПРОПУЩЕННЫХ В ОТЧЕТ
    report = f"<b>📊 Сбор данных завершен!</b>\n\n"
    report += f"✅ Успешно: {success_count}/{len(valid_users)}\n\n"

    if invalid_users:
        report += "⚠️ <b>Пропущенные пользователи:</b>\n"
        for msg in invalid_users:
            report += f"• {msg}\n"
        report += "\n"

    report += "<b>📋 Результаты:</b>\n"
    for result in results:
        report += f"{result}\n"

    await message.answer(report, parse_mode="HTML")


# ==================== ФУНКЦИЯ ПОКАЗА ПРОФИЛЯ ====================

async def show_user_profile(message: types.Message, query: str, from_callback=False):
    try:
        if query.isdigit():
            user_id = int(query)
            user_info = db.get_user_by_id(user_id)
        else:
            user_info = db.get_user_by_username(query)
            user_id = user_info[0] if user_info else None

        if not user_info:
            try:
                if query.isdigit():
                    user_data = await collect_user_data(int(query))
                else:
                    user_data = await collect_user_data(query)
                if user_data:
                    user_id = user_data['id']
                    db.add_user(
                        user_id,
                        user_data['username'],
                        user_data['first_name'],
                        user_data['last_name'],
                        user_data['is_premium'],
                        user_data.get('phone'),
                        user_data.get('last_seen')
                    )
                    if db.is_user_hidden(user_id) and message.from_user.id != ADMIN_ID:
                        await message.answer("🔒 Пользователь скрыл информацию о себе", parse_mode="HTML")
                        return

                    db.add_searcher(user_id, message.from_user.id)
                    await show_user_profile(message, str(user_id))
                    return
                else:
                    await message.answer("❌ Пользователь не найден")
                    return
            except Exception as e:
                await message.answer(f"❌ Ошибка: {str(e)}")
                return

        user_id = user_info[0]
        if db.is_user_hidden(user_id) and message.from_user.id != ADMIN_ID:
            await message.answer("🔒 Пользователь скрыл информацию о себе", parse_mode="HTML")
            return

        db.add_searcher(user_id, message.from_user.id)
        search_count = db.get_search_count(user_id)
        unique_searchers = db.get_unique_searchers_count(user_id)

        try:
            user_data = await collect_user_data(user_id)
            if user_data:
                db.add_user(
                    user_id,
                    user_data['username'],
                    user_data['first_name'],
                    user_data['last_name'],
                    user_data['is_premium'],
                    user_data.get('phone'),
                    user_data.get('last_seen')
                )
        except Exception as e:
            logger.error(f"Error collecting user data: {e}")

        user_info = db.get_user_info(user_id)
        old_usernames = db.get_old_usernames(user_id)
        old_names = db.get_old_names(user_id)
        activity = db.get_user_activity(user_id)
        chats = db.get_user_chats(user_id)
        analysis_stats = db.get_message_analysis_stats(user_id)
        reactions = db.get_reactions_count(user_id)
        contacts = db.get_private_contacts(user_id)
        online_history = db.get_online_history(user_id)
        activity_peaks = db.get_activity_peaks(user_id)
        city_stats = db.get_city_stats(user_id)

        is_premium = user_info[7] if len(user_info) > 7 else False
        last_seen = user_info[5] if len(user_info) > 5 else None
        birthday = user_info[15] if len(user_info) > 15 else None
        reputation_likes = user_info[16] if len(user_info) > 16 else 0
        reputation_dislikes = user_info[17] if len(user_info) > 17 else 0
        interests = user_info[18] if len(user_info) > 18 else 0

        display_name = user_info[1] or str(user_id)
        first_name = user_info[2] or 'Не указано'
        last_name = user_info[3] or ''
        total_msgs_from_chats = db.get_messages_count_from_chats(user_id)

        response = f"<b>📊 Профиль: @{display_name}</b>\n\n"
        response += f"<b>ID:</b> <code>{user_id}</code>\n"
        response += f"<b>Имя:</b> {first_name} {last_name}\n"
        if birthday:
            response += f"<b>🎂 День рождения:</b> {birthday}\n"
        response += f"<b>⭐ Премиум:</b> {'Да' if is_premium else 'Нет'}\n"
        if interests:
            response += f"<b>👀 Интересовались:</b> {interests} раз\n"
        if reputation_likes or reputation_dislikes:
            response += f"<b>👍 Репутация:</b> 👍({reputation_likes}) 👎({reputation_dislikes})\n"
        response += f"<b>💬 Сообщений в чатах:</b> {total_msgs_from_chats}\n"
        response += f"<b>👀 Поисков (всего):</b> {search_count}\n"
        response += f"<b>👤 Уникальных искателей:</b> {unique_searchers}\n"

        if last_seen:
            response += f"<b>Был в сети:</b> {last_seen}\n"

        if online_history:
            online_count = sum(1 for _, is_online in online_history if is_online)
            response += f"<b>🟢 Онлайн сейчас:</b> {'Да' if online_count > 0 else 'Нет'}\n"
            response += f"<b>⏰ Последний онлайн:</b> {online_history[0][0][:16] if online_history else 'Неизвестно'}\n"

        if analysis_stats:
            total_msgs = analysis_stats[1] if len(analysis_stats) > 1 else 0
            total_replies = analysis_stats[2] if len(analysis_stats) > 2 else 0
            total_media = analysis_stats[3] if len(analysis_stats) > 3 else 0
            favorite_chat = analysis_stats[10] if len(analysis_stats) > 10 else None
            avg_words = analysis_stats[12] if len(analysis_stats) > 12 else 0

            response += f"\n<b>📊 Статистика сообщений:</b>\n"
            response += f"• Всего сообщений: {total_msgs}\n"
            response += f"• Ответов: {total_replies}\n"
            response += f"• Медиа: {total_media}\n"
            if avg_words:
                response += f"• Средняя длина: {avg_words:.1f} слов\n"
            if favorite_chat:
                response += f"• ❤️ Любимый чат: {favorite_chat}\n"

        if activity_peaks:
            response += f"\n<b>⏰ Пики активности:</b>\n"
            days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            for hour, day, count, date in activity_peaks[:3]:
                response += f"• {hour}:00 - {days[day]} - {count} сообщений\n"

        if city_stats:
            response += f"\n<b>🏙 Города в сообщениях:</b>\n"
            for city, count, last_mentioned in city_stats[:5]:
                response += f"• {city.capitalize()}: {count} раз\n"

        if activity:
            response += f"\n<b>📈 Активность по часам:</b>\n"
            for hour, count in activity[:3]:
                response += f"• {hour}:00 - {count} сообщений\n"

        if old_usernames:
            response += f"\n<b>📛 Старые юзернеймы ({len(old_usernames)}):</b>\n"
            for username, changed_at in old_usernames[:5]:
                response += f"• @{username} ({changed_at[:16]})\n"

        if old_names:
            response += f"\n<b>👤 Старые имена ({len(old_names)}):</b>\n"
            for fname, lname, changed_at in old_names[:5]:
                name = f"{fname or ''} {lname or ''}".strip() or 'Без имени'
                response += f"• {name} ({changed_at[:16]})\n"

        if reactions:
            response += f"\n<b>❤️ Реакции:</b>\n"
            total_reactions = sum([r[1] for r in reactions])
            response += f"• Всего реакций: {total_reactions}\n"
            for reaction_type, count in reactions[:5]:
                response += f"• {reaction_type}: {count}\n"

        groups = [c for c in chats if c[2] in ['group', 'supergroup']]
        channels = [c for c in chats if c[2] == 'channel']
        response += f"\n<b>👥 Группы:</b> {len(groups)} | <b>📢 Каналы:</b> {len(channels)}\n"

        if contacts:
            response += f"\n<b>👤 Знакомые в ЛС:</b> {len(contacts)}\n"

        is_admin = message.from_user.id == ADMIN_ID
        keyboard = create_profile_keyboard(user_id, is_admin)

        await message.answer(response, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)

    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")


# ==================== ПОЛНЫЙ ОБРАБОТЧИК CALLBACK (ВСЕ ФУНКЦИИ) ====================

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery, state: FSMContext):
    try:
        data = callback.data
        user_id = callback.from_user.id

        # ============ ИМПОРТ СТАРЫХ ИМЕН ============
        if data.startswith("import_names_"):
            target_id = int(data.split('_')[2])

            if user_id != ADMIN_ID:
                await callback.answer("❌ Только для администратора!", show_alert=True)
                return

            await callback.message.answer(
                "📛 <b>Импорт старых имен</b>\n\n"
                "Отправьте список старых имен пользователя в формате:\n"
                "<code>Имя (DD.MM.YYYY HH:MM)</code>\n\n"
                "Пример:\n"
                "<code>Анфитом (20.07.2026 02:15)\n"
                "FEDOTOV SCREAMINT (21.06.2026 01:30)</code>\n\n"
                "Каждое имя с новой строки.\n"
                "Для отмены отправьте /cancel",
                parse_mode="HTML"
            )
            await state.set_state(ImportOldNamesState.waiting_for_names)
            await state.update_data(target_user_id=target_id)
            await callback.answer()
            return

        # ============ ИМПОРТ СТАРЫХ ЮЗЕРНЕЙМОВ ============
        if data.startswith("import_usernames_"):
            target_id = int(data.split('_')[2])

            if user_id != ADMIN_ID:
                await callback.answer("❌ Только для администратора!", show_alert=True)
                return

            await callback.message.answer(
                "🔤 <b>Импорт старых юзернеймов</b>\n\n"
                "Отправьте список старых юзернеймов пользователя в формате:\n"
                "<code>@username (DD.MM.YYYY HH:MM)</code>\n\n"
                "Пример:\n"
                "<code>@retourx (08.04.2026 04:20)\n"
                "@gmailtrepholly (04.04.2026 00:00)</code>\n\n"
                "Каждый юзернейм с новой строки.\n"
                "Для отмены отправьте /cancel",
                parse_mode="HTML"
            )
            await state.set_state(ImportOldUsernamesState.waiting_for_usernames)
            await state.update_data(target_user_id=target_id)
            await callback.answer()
            return

        # ============ СБОР ДАННЫХ ИЗ БОТА ============
        if data == "admin_collect":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return

            await callback.message.answer(
                "📥 <b>Сбор данных из @getSendGiftsProBot</b>\n\n"
                "Используйте команду:\n"
                "<code>/collect @username</code>\n\n"
                "Или для нескольких пользователей:\n"
                "<code>/collect @user1, @user2, @user3</code>\n\n"
                "Бот автоматически:\n"
                "• Соберет старые имена\n"
                "• Соберет старые юзернеймы\n"
                "• Сохранит все данные в базу\n\n"
                "⚠️ Бот должен быть подписан на @getSendGiftsProBot",
                parse_mode="HTML"
            )
            await callback.answer()
            return

        # ============ ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ============
        if data == "commands":
            is_admin = user_id == ADMIN_ID
            commands_text = "📖 <b>СПИСОК КОМАНД</b>\n\n"
            commands_text += "🔍 <b>Поиск и анализ:</b>\n"
            commands_text += "/analyze @username - анализ профиля\n"
            commands_text += "/analyze 123456789 - анализ по ID\n"
            commands_text += "/search username - поиск пользователей\n\n"
            commands_text += "📊 <b>Информация:</b>\n"
            commands_text += "/groups - список отслеживаемых групп\n"
            commands_text += "/channels - список отслеживаемых каналов\n\n"

            if is_admin:
                commands_text += "⚙️ <b>АДМИН КОМАНДЫ:</b>\n"
                commands_text += "/hide @username [дни] - скрыть пользователя\n"
                commands_text += "/unhide @username - показать пользователя\n"
                commands_text += "/deletechat ID - удалить чат из отслеживания\n"
                commands_text += "/collect @username - собрать данные из @getSendGiftsProBot\n\n"

            commands_text += "💡 <b>Как использовать:</b>\n"
            commands_text += "• @username - бот сам определит пользователь это или канал/группа\n"
            commands_text += "• t.me/... - ссылка на чат - соберу сообщения"

            await callback.message.edit_text(
                commands_text,
                parse_mode="HTML",
                reply_markup=create_main_keyboard(user_id)
            )
            await callback.answer()
            return

        if data == "search":
            await callback.message.answer("🔍 Отправьте юзернейм или ID для поиска", parse_mode="HTML")
            await callback.answer()
            return

        if data == "groups":
            chats = db.get_tracked_chats()
            groups = [c for c in chats if c[3] in ['group', 'supergroup']]
            if not groups:
                await callback.message.edit_text(
                    "📭 Нет отслеживаемых групп\n\nОтправьте ссылку на группу.",
                    parse_mode="HTML",
                    reply_markup=create_main_keyboard(user_id)
                )
                await callback.answer()
                return
            response = f"<b>📋 Отслеживаемые группы ({len(groups)})</b>\n\n"
            for chat_id, chat_name, chat_link, chat_type, participants, added_at, added_by, is_active, is_joined, join_status, is_public in groups[
                :20]:
                if chat_link:
                    response += f"• <a href='{chat_link}'>{chat_name}</a>"
                else:
                    response += f"• {chat_name}"
                response += f" ({participants} участников)\n"
            if len(groups) > 20:
                response += f"\n... и ещё {len(groups) - 20} групп"
            await callback.message.edit_text(
                response,
                parse_mode="HTML",
                reply_markup=create_main_keyboard(user_id),
                disable_web_page_preview=True
            )
            await callback.answer()
            return

        if data == "channels":
            chats = db.get_tracked_chats()
            channels = [c for c in chats if c[3] == 'channel']
            if not channels:
                await callback.message.edit_text(
                    "📭 Нет отслеживаемых каналов\n\nОтправьте ссылку на канал.",
                    parse_mode="HTML",
                    reply_markup=create_main_keyboard(user_id)
                )
                await callback.answer()
                return
            response = f"<b>📢 Отслеживаемые каналы ({len(channels)})</b>\n\n"
            for chat_id, chat_name, chat_link, chat_type, participants, added_at, added_by, is_active, is_joined, join_status, is_public in channels[
                :20]:
                if chat_link:
                    response += f"• <a href='{chat_link}'>{chat_name}</a>"
                else:
                    response += f"• {chat_name}"
                response += f" ({participants} подписчиков)\n"
            if len(channels) > 20:
                response += f"\n... и ещё {len(channels) - 20} каналов"
            await callback.message.edit_text(
                response,
                parse_mode="HTML",
                reply_markup=create_main_keyboard(user_id),
                disable_web_page_preview=True
            )
            await callback.answer()
            return

        if data.startswith("my_data_"):
            target_id = int(data.split('_')[2])
            await show_user_profile(callback.message, str(target_id), True)
            await callback.answer()
            return

        if data == "admin_panel":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            await callback.message.edit_text(
                "⚙️ <b>АДМИН ПАНЕЛЬ</b>\n\nВыберите действие:",
                parse_mode="HTML",
                reply_markup=create_admin_keyboard()
            )
            await callback.answer()
            return

        if data == "admin_back":
            await callback.message.edit_text(
                "👋 <b>Добро пожаловать!</b>\n\nЯ анализирую профили пользователей.",
                parse_mode="HTML",
                reply_markup=create_main_keyboard(user_id)
            )
            await callback.answer()
            return

        if data == "admin_hide":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            await callback.message.answer(
                "🔒 Используйте: /hide @username дни",
                parse_mode="HTML"
            )
            await callback.answer()
            return

        if data == "admin_unhide":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            await callback.message.answer(
                "👁️ Используйте: /unhide @username",
                parse_mode="HTML"
            )
            await callback.answer()
            return

        if data == "admin_delete_chat":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            await callback.message.answer(
                "🗑️ <b>Удалить чат из отслеживания</b>\n\n"
                "Используйте команду:\n"
                "<code>/deletechat ID_чата</code>\n\n"
                "ID чата можно найти в списке отслеживаемых чатов",
                parse_mode="HTML"
            )
            await callback.answer()
            return

        if data == "admin_users":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            users = db.cursor.execute(
                'SELECT user_id, username, first_name, is_hidden, is_premium FROM users LIMIT 50').fetchall()
            if not users:
                await callback.message.edit_text(
                    "📊 Нет пользователей",
                    parse_mode="HTML",
                    reply_markup=create_admin_keyboard()
                )
                await callback.answer()
                return
            response = "📊 <b>Список пользователей (50):</b>\n\n"
            for user_id, username, first_name, is_hidden, is_premium in users:
                status = "🔒 Скрыт" if is_hidden else "✅ Открыт"
                premium = "⭐" if is_premium else ""
                name = first_name or "Без имени"
                uname = f"@{username}" if username else "Нет юзернейма"
                response += f"• {premium} {name} - {uname}\n  ID: <code>{user_id}</code> - {status}\n\n"
            await callback.message.edit_text(
                response,
                parse_mode="HTML",
                reply_markup=create_admin_keyboard()
            )
            await callback.answer()
            return

        if data == "admin_stats":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            total_users = db.cursor.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            hidden_users = db.cursor.execute('SELECT COUNT(*) FROM users WHERE is_hidden = TRUE').fetchone()[0]
            premium_users = db.cursor.execute('SELECT COUNT(*) FROM users WHERE is_premium = TRUE').fetchone()[0]
            total_messages = db.cursor.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
            total_chats = db.cursor.execute('SELECT COUNT(*) FROM tracked_chats WHERE is_active = TRUE').fetchone()[0]
            response = (
                "📈 <b>СТАТИСТИКА</b>\n\n"
                f"👤 Всего пользователей: {total_users}\n"
                f"🔒 Скрытых: {hidden_users}\n"
                f"✅ Открытых: {total_users - hidden_users}\n"
                f"⭐ Премиум: {premium_users}\n"
                f"💬 Сообщений: {total_messages}\n"
                f"📋 Отслеживаемых чатов: {total_chats}"
            )
            await callback.message.edit_text(
                response,
                parse_mode="HTML",
                reply_markup=create_admin_keyboard()
            )
            await callback.answer()
            return

        if data == "admin_chats":
            if user_id != ADMIN_ID:
                await callback.answer("❌ Доступ запрещен", show_alert=True)
                return
            chats = db.get_tracked_chats()
            if not chats:
                await callback.message.edit_text(
                    "📋 Нет отслеживаемых чатов",
                    parse_mode="HTML",
                    reply_markup=create_admin_keyboard()
                )
                await callback.answer()
                return
            response = "📋 <b>Отслеживаемые чаты:</b>\n\n"
            for chat_id, chat_name, chat_link, chat_type, participants, added_at, added_by, is_active, is_joined, join_status, is_public in chats:
                if chat_link:
                    response += f"• <a href='{chat_link}'>{chat_name}</a>"
                else:
                    response += f"• {chat_name}"
                response += f" ({chat_type}, {participants} чел.)\n"
                response += f"  ID: <code>{chat_id}</code>\n\n"
            await callback.message.edit_text(
                response,
                parse_mode="HTML",
                reply_markup=create_admin_keyboard(),
                disable_web_page_preview=True
            )
            await callback.answer()
            return

        # ============ КНОПКИ ПРОФИЛЯ ============

        if data.startswith("analysis_"):
            target_id = int(data.split('_')[1])
            await callback.message.edit_text(
                f"<b>📊 Анализ сообщений пользователя</b>\n\n"
                f"Выберите категорию для анализа:",
                parse_mode="HTML",
                reply_markup=create_analysis_keyboard(target_id)
            )
            await callback.answer()
            return

        if data.startswith("analysis_cities_"):
            target_id = int(data.split('_')[2])
            city_stats = db.get_city_stats(target_id)
            if not city_stats:
                await callback.answer("❌ Нет упоминаний городов")
                return

            response = f"<b>🏙 Города в сообщениях пользователя:</b>\n\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for city, count, last_mentioned in city_stats[:10]:
                response += f"• {city.capitalize()}: {count} раз\n"
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(
                        text=f"📝 {city.capitalize()} ({count})",
                        callback_data=f"city_detail_{target_id}_{city}"
                    )
                ])
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="🔙 Назад", callback_data=f"analysis_{target_id}")
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
            await callback.answer()
            return

        if data.startswith("city_detail_"):
            parts = data.split('_')
            target_id = int(parts[2])
            city = parts[3]
            messages = db.get_city_messages_detailed(target_id, city)
            if not messages:
                await callback.answer("❌ Нет сообщений с этим городом")
                return

            response = f"<b>🏙 Сообщения с городом '{city.capitalize()}':</b>\n\n"
            for text, sent_at, chat_name, chat_link, message_id in messages[:10]:
                if chat_link and message_id:
                    msg_link = f"{chat_link}/{message_id}"
                    response += f"📝 <a href='{msg_link}'>{chat_name}</a>"
                else:
                    response += f"📝 {chat_name}"
                response += f" [{sent_at[:10]}]: {text[:80]}...\n"
            if len(messages) > 10:
                response += f"\n... и ещё {len(messages) - 10} сообщений"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к городам", callback_data=f"analysis_cities_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard,
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        if data.startswith("analysis_mentions_"):
            target_id = int(data.split('_')[2])
            stats = db.get_mention_stats_detailed(target_id)
            response = f"<b>✍️ Кого отмечает пользователь:</b>\n\n"
            if stats['mentions']:
                for text, sent_at, chat_name, chat_link, message_id in stats['mentions'][:10]:
                    if chat_link and message_id:
                        msg_link = f"{chat_link}/{message_id}"
                        response += f"📝 <a href='{msg_link}'>{chat_name}</a>"
                    else:
                        response += f"📝 {chat_name}"
                    response += f" [{sent_at[:10]}]: {text[:80]}...\n"
            else:
                response += "❌ Нет упоминаний\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к анализу", callback_data=f"analysis_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard,
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        if data.startswith("analysis_mentioned_"):
            target_id = int(data.split('_')[2])
            stats = db.get_mention_stats_detailed(target_id)
            response = f"<b>📢 Кто отмечает пользователя:</b>\n\n"
            if stats['mentioned_by']:
                for text, sent_at, chat_name, chat_link, message_id, user_id in stats['mentioned_by'][:10]:
                    if chat_link and message_id:
                        msg_link = f"{chat_link}/{message_id}"
                        response += f"📝 <a href='{msg_link}'>{chat_name}</a>"
                    else:
                        response += f"📝 {chat_name}"
                    response += f" [{sent_at[:10]}]: {text[:80]}...\n"
            else:
                response += "❌ Никто не отмечал пользователя\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к анализу", callback_data=f"analysis_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard,
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        if data.startswith("analysis_names_"):
            target_id = int(data.split('_')[2])
            messages = db.get_analysis_by_type(target_id, 'name')
            response = f"<b>👤 Сообщения с именами:</b>\n\n"
            if messages:
                for chat_name, chat_link, text, sent_at, message_id, chat_id in messages[:10]:
                    if chat_link and message_id:
                        msg_link = f"{chat_link}/{message_id}"
                        response += f"📝 <a href='{msg_link}'>{chat_name}</a>"
                    else:
                        response += f"📝 {chat_name}"
                    response += f" [{sent_at[:10]}]: {text[:80]}...\n"
            else:
                response += "❌ Нет сообщений с именами\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к анализу", callback_data=f"analysis_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard,
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        if data.startswith("analysis_age_"):
            target_id = int(data.split('_')[2])
            messages = db.get_analysis_by_type(target_id, 'age')
            response = f"<b>🎂 Сообщения с возрастом:</b>\n\n"
            if messages:
                for chat_name, chat_link, text, sent_at, message_id, chat_id in messages[:10]:
                    if chat_link and message_id:
                        msg_link = f"{chat_link}/{message_id}"
                        response += f"📝 <a href='{msg_link}'>{chat_name}</a>"
                    else:
                        response += f"📝 {chat_name}"
                    response += f" [{sent_at[:10]}]: {text[:80]}...\n"
            else:
                response += "❌ Нет сообщений с возрастом\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к анализу", callback_data=f"analysis_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard,
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        if data.startswith("analysis_love_"):
            target_id = int(data.split('_')[2])
            messages = db.get_analysis_by_type(target_id, 'likes')
            response = f"<b>❤️ Сообщения с любовью/симпатией:</b>\n\n"
            if messages:
                for chat_name, chat_link, text, sent_at, message_id, chat_id in messages[:10]:
                    if chat_link and message_id:
                        msg_link = f"{chat_link}/{message_id}"
                        response += f"📝 <a href='{msg_link}'>{chat_name}</a>"
                    else:
                        response += f"📝 {chat_name}"
                    response += f" [{sent_at[:10]}]: {text[:80]}...\n"
            else:
                response += "❌ Нет сообщений с любовью/симпатией\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к анализу", callback_data=f"analysis_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard,
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        # ============ СООБЩЕНИЯ ============
        if data.startswith("msgs_"):
            target_id = int(data.split('_')[1])
            total = db.get_messages_count_from_chats(target_id)
            if total == 0:
                await callback.answer("Нет сообщений в отслеживаемых чатах")
                return
            await callback.message.edit_text(
                f"<b>💬 Сообщения в отслеживаемых чатах</b>\n\n"
                f"Выберите тип сообщений для просмотра:\n"
                f"Всего сообщений: {total}",
                parse_mode="HTML",
                reply_markup=create_messages_keyboard(target_id)
            )
            await callback.answer()
            return

        if data.startswith("msg_type_"):
            parts = data.split('_')
            target_id = int(parts[2])
            msg_type = parts[3]
            page = int(parts[4])
            offset = (page - 1) * 20
            messages = db.get_messages_by_type_from_chats(target_id, msg_type, 20, offset)
            total = db.get_messages_count_by_type_from_chats(target_id, msg_type)
            if not messages:
                await callback.answer("Нет сообщений этого типа")
                return
            response = f"<b>💬 Сообщения в отслеживаемых чатах</b>\n\n"
            response += f"Тип: {msg_type.upper()} | Всего: {total}\n"
            response += f"Страница {page} из {((total - 1) // 20) + 1 if total > 0 else 1}\n\n"
            for chat_name, chat_link, text, sent_at, is_media, media_type, is_reply, message_id, chat_id, analyzed_type in messages:
                prefix = "🏠" if chat_name == "Private" else ""
                if is_reply:
                    prefix += " [R]"
                if chat_link and message_id:
                    msg_link = f"{chat_link}/{message_id}"
                    response += f"📝 {prefix} <a href='{msg_link}'>{chat_name[:20]}</a>"
                elif chat_link:
                    response += f"📝 {prefix} <a href='{chat_link}'>{chat_name[:20]}</a>"
                else:
                    response += f"📝 {prefix} {chat_name[:20]}"
                response += f" [{sent_at[:10]}]: "
                if text and text.strip():
                    short_text = text[:100] + "..." if len(text) > 100 else text
                    response += f"{short_text}\n"
                else:
                    response += "[пустое сообщение]\n"
            await callback.message.edit_text(response, parse_mode="HTML",
                                             reply_markup=create_messages_keyboard(target_id, msg_type, page),
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        if data.startswith("msg_page_"):
            parts = data.split('_')
            target_id = int(parts[2])
            msg_type = parts[3]
            page = int(parts[4])
            offset = (page - 1) * 20
            messages = db.get_messages_by_type_from_chats(target_id, msg_type, 20, offset)
            if not messages:
                await callback.answer("Нет сообщений")
                return
            response = f"<b>💬 Сообщения в отслеживаемых чатах</b>\n\n"
            total = db.get_messages_count_by_type_from_chats(target_id, msg_type)
            response += f"Тип: {msg_type.upper()} | Всего: {total}\n"
            response += f"Страница {page} из {((total - 1) // 20) + 1 if total > 0 else 1}\n\n"
            for chat_name, chat_link, text, sent_at, is_media, media_type, is_reply, message_id, chat_id, analyzed_type in messages:
                prefix = "🏠" if chat_name == "Private" else ""
                if is_reply:
                    prefix += " [R]"
                if chat_link and message_id:
                    msg_link = f"{chat_link}/{message_id}"
                    response += f"📝 {prefix} <a href='{msg_link}'>{chat_name[:20]}</a>"
                elif chat_link:
                    response += f"📝 {prefix} <a href='{chat_link}'>{chat_name[:20]}</a>"
                else:
                    response += f"📝 {prefix} {chat_name[:20]}"
                response += f" [{sent_at[:10]}]: "
                if text and text.strip():
                    short_text = text[:100] + "..." if len(text) > 100 else text
                    response += f"{short_text}\n"
                else:
                    response += "[пустое сообщение]\n"
            await callback.message.edit_text(response, parse_mode="HTML",
                                             reply_markup=create_messages_keyboard(target_id, msg_type, page),
                                             disable_web_page_preview=True)
            await callback.answer()
            return

        # ============ ПРОФИЛЬ ============
        if data.startswith("profile_"):
            target_id = int(data.split('_')[1])
            if db.is_user_hidden(target_id) and user_id != ADMIN_ID:
                await callback.answer("🔒 Пользователь скрыл информацию", show_alert=True)
                return
            await show_user_profile(callback.message, str(target_id), True)
            await callback.answer()
            return

        if data.startswith("names_"):
            target_id = int(data.split('_')[1])
            old_usernames = db.get_old_usernames(target_id)
            old_names = db.get_old_names(target_id)
            response = "<b>📛 Имена и теги:</b>\n\n"
            if old_names:
                response += "<b>📛 Старые имена:</b>\n"
                for fname, lname, changed_at in old_names[:10]:
                    name = f"{fname or ''} {lname or ''}".strip() or 'Без имени'
                    response += f"• {name} ({changed_at[:16]})\n"
                response += "\n"
            if old_usernames:
                response += "<b>🔤 Старые юзернеймы:</b>\n"
                for username, changed_at in old_usernames[:10]:
                    response += f"• @{username} ({changed_at[:16]})\n"
                response += "\n"
            if not old_usernames and not old_names:
                response += "Нет истории имен и тегов"
            await callback.message.edit_text(response, parse_mode="HTML",
                                             reply_markup=create_profile_keyboard(target_id))
            await callback.answer()
            return

        if data.startswith("premium_"):
            target_id = int(data.split('_')[1])
            user_info = db.get_user_info(target_id)
            if not user_info:
                await callback.answer("Профиль не найден")
                return
            is_premium = user_info[7] if len(user_info) > 7 else False
            premium_start = user_info[10] if len(user_info) > 10 else None
            premium_end = user_info[11] if len(user_info) > 11 else None
            premium_history = db.get_premium_history(target_id)
            response = f"<b>⭐ Премиум статус</b>\n\n"
            if is_premium:
                response += "✅ <b>Статус:</b> Активен\n"
                if premium_start:
                    response += f"📅 <b>Начало:</b> {premium_start[:10]}\n"
                if premium_end:
                    response += f"📅 <b>Окончание:</b> {premium_end[:10]}\n"
                    try:
                        end_date = datetime.fromisoformat(premium_end)
                        days_left = (end_date - datetime.now()).days
                        if days_left > 0:
                            response += f"⏳ <b>Осталось:</b> {days_left} дней\n"
                    except:
                        pass
            else:
                response += "❌ <b>Статус:</b> Неактивен\n"
                if premium_end:
                    response += f"📅 <b>Закончился:</b> {premium_end[:10]}\n"
                else:
                    response += "📅 <b>Никогда не был активен</b>\n"
            if premium_history:
                response += f"\n📜 <b>История премиума:</b>\n"
                for record in premium_history[-5:]:
                    response += f"• {record.get('start', '?')} - {record.get('end', '?')}\n"
            await callback.message.edit_text(response, parse_mode="HTML",
                                             reply_markup=create_profile_keyboard(target_id))
            await callback.answer()
            return

        if data.startswith("reactions_"):
            target_id = int(data.split('_')[1])
            reactions = db.get_reactions_count(target_id)
            if not reactions:
                await callback.answer("Нет реакций")
                return
            response = "<b>❤️ Реакции:</b>\n\n"
            total = sum([r[1] for r in reactions])
            response += f"Всего реакций: {total}\n\n"
            for reaction_type, count in reactions:
                response += f"• {reaction_type}: {count}\n"
            await callback.message.edit_text(response, parse_mode="HTML",
                                             reply_markup=create_profile_keyboard(target_id))
            await callback.answer()
            return

        if data.startswith("contacts_"):
            target_id = int(data.split('_')[1])
            contacts = db.get_private_contacts(target_id)
            if not contacts:
                await callback.answer("Нет знакомых")
                return
            response = "<b>👤 Знакомые (ЛС):</b>\n\n"
            for contact_id in contacts[:20]:
                contact_info = db.get_user_info(contact_id[0])
                if contact_info:
                    name = contact_info[1] or contact_info[2] or str(contact_id[0])
                    response += f"• {name}\n"
            await callback.message.edit_text(response, parse_mode="HTML",
                                             reply_markup=create_profile_keyboard(target_id))
            await callback.answer()
            return

        if data.startswith("follow_"):
            target_id = int(data.split('_')[1])
            if user_id == target_id:
                await callback.answer("❌ Нельзя следить за собой", show_alert=True)
                return
            if db.is_following(target_id, user_id):
                db.remove_follower(target_id, user_id)
                await callback.answer("🔕 Вы отписались")
            else:
                db.add_follower(target_id, user_id)
                await callback.answer("🔔 Вы подписались")
            await show_user_profile(callback.message, str(target_id), True)
            return

        # ============ НОВЫЕ КНОПКИ ============
        if data.startswith("peak_"):
            target_id = int(data.split('_')[1])
            peaks = db.get_activity_peaks(target_id)
            if not peaks:
                await callback.answer("❌ Нет данных о пиках активности")
                return
            response = "<b>⏰ Пики активности пользователя</b>\n\n"
            days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            for hour, day, count, date in peaks[:10]:
                response += f"• {hour:02d}:00 - {days[day]} - {count} сообщений\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data=f"profile_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
            await callback.answer()
            return

        if data.startswith("time_"):
            target_id = int(data.split('_')[1])
            online_history = db.get_online_history(target_id)
            response = "<b>🕐 Время активности пользователя</b>\n\n"
            if online_history:
                online_count = sum(1 for _, is_online in online_history if is_online)
                total_checks = len(online_history)
                if total_checks > 0:
                    percent = (online_count / total_checks) * 100
                    response += f"📊 Был онлайн в {percent:.1f}% случаев\n"
                response += f"\n<b>Последние проверки:</b>\n"
                for time, is_online in online_history[:10]:
                    status = "🟢 Онлайн" if is_online else "🔴 Офлайн"
                    response += f"• {time[:16]} - {status}\n"
            else:
                response += "❌ Нет данных о времени онлайн"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data=f"profile_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
            await callback.answer()
            return

        if data.startswith("cities_"):
            target_id = int(data.split('_')[1])
            city_stats = db.get_city_stats(target_id)
            if not city_stats:
                await callback.answer("❌ Нет упоминаний городов")
                return
            response = "<b>🏙 Города в сообщениях:</b>\n\n"
            for city, count, last_mentioned in city_stats[:10]:
                response += f"• {city.capitalize()}: {count} раз\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data=f"profile_{target_id}")]
            ])
            await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
            await callback.answer()
            return

    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}")


# ==================== ОБРАБОТЧИК СТАРЫХ ИМЕН ====================

@dp.message(ImportOldNamesState.waiting_for_names)
async def handle_import_names(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("❌ Только для администратора!")
        await state.clear()
        return

    if message.text == "/cancel":
        await message.answer("❌ Импорт отменен")
        await state.clear()
        return

    try:
        data = await state.get_data()
        target_user_id = data.get('target_user_id')

        if not target_user_id:
            await message.answer("❌ Ошибка: пользователь не найден")
            await state.clear()
            return

        text = message.text.strip()
        lines = text.split('\n')

        imported_count = 0
        skipped_count = 0
        names_list = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(r'(.+?)\s*\((\d{2})\.(\d{2})\.(\d{2,4})\s+(\d{2}):(\d{2})\)', line)
            if match:
                name = match.group(1).strip()
                day = int(match.group(2))
                month = int(match.group(3))
                year = int(match.group(4))
                hour = int(match.group(5))
                minute = int(match.group(6))

                if year < 100:
                    year = 2000 + year

                try:
                    changed_at = datetime(year, month, day, hour, minute)
                    changed_at_str = changed_at.isoformat()

                    name_parts = name.split()
                    first_name = name_parts[0] if name_parts else name
                    last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                    success = db.add_old_name_import(target_user_id, first_name, last_name, changed_at_str)
                    if success:
                        imported_count += 1
                        names_list.append(f"{name} ({changed_at.strftime('%d.%m.%Y %H:%M')})")
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.error(f"Error parsing date: {e}")
                    skipped_count += 1
            else:
                if line:
                    try:
                        success = db.add_old_name_import(target_user_id, line, '', datetime.now().isoformat())
                        if success:
                            imported_count += 1
                            names_list.append(f"{line} (сейчас)")
                        else:
                            skipped_count += 1
                    except:
                        skipped_count += 1

        report = f"✅ <b>Импорт старых имен завершен!</b>\n\n"
        report += f"📊 <b>Статистика:</b>\n"
        report += f"• Импортировано: {imported_count}\n"
        report += f"• Пропущено: {skipped_count}\n\n"

        if names_list:
            report += f"<b>📋 Список импортированных имен:</b>\n"
            for i, name in enumerate(names_list[:10], 1):
                report += f"{i}. {name}\n"
            if len(names_list) > 10:
                report += f"\n... и еще {len(names_list) - 10} имен"
        else:
            report += "❌ Не удалось импортировать ни одного имени"

        await message.answer(report, parse_mode="HTML")
        await state.clear()
        await show_user_profile(message, str(target_user_id))

    except Exception as e:
        logger.error(f"Error importing names: {e}")
        await message.answer(f"❌ Ошибка при импорте: {str(e)}")
        await state.clear()


# ==================== ОБРАБОТЧИК СТАРЫХ ЮЗЕРНЕЙМОВ ====================

@dp.message(ImportOldUsernamesState.waiting_for_usernames)
async def handle_import_usernames(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("❌ Только для администратора!")
        await state.clear()
        return

    if message.text == "/cancel":
        await message.answer("❌ Импорт отменен")
        await state.clear()
        return

    try:
        data = await state.get_data()
        target_user_id = data.get('target_user_id')

        if not target_user_id:
            await message.answer("❌ Ошибка: пользователь не найден")
            await state.clear()
            return

        text = message.text.strip()
        lines = text.split('\n')

        imported_count = 0
        skipped_count = 0
        usernames_list = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(r'@(\w+)\s*\((\d{2})\.(\d{2})\.(\d{2,4})\s+(\d{2}):(\d{2})\)', line)
            if match:
                username = match.group(1)
                day = int(match.group(2))
                month = int(match.group(3))
                year = int(match.group(4))
                hour = int(match.group(5))
                minute = int(match.group(6))

                if year < 100:
                    year = 2000 + year

                try:
                    changed_at = datetime(year, month, day, hour, minute)
                    changed_at_str = changed_at.isoformat()

                    success = db.add_old_username_import(target_user_id, username, changed_at_str)
                    if success:
                        imported_count += 1
                        usernames_list.append(f"@{username} ({changed_at.strftime('%d.%m.%Y %H:%M')})")
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.error(f"Error parsing date: {e}")
                    skipped_count += 1
            else:
                username_match = re.search(r'@(\w+)', line)
                if username_match:
                    username = username_match.group(1)
                    try:
                        success = db.add_old_username_import(target_user_id, username, datetime.now().isoformat())
                        if success:
                            imported_count += 1
                            usernames_list.append(f"@{username} (сейчас)")
                        else:
                            skipped_count += 1
                    except:
                        skipped_count += 1

        report = f"✅ <b>Импорт старых юзернеймов завершен!</b>\n\n"
        report += f"📊 <b>Статистика:</b>\n"
        report += f"• Импортировано: {imported_count}\n"
        report += f"• Пропущено: {skipped_count}\n\n"

        if usernames_list:
            report += f"<b>📋 Список импортированных юзернеймов:</b>\n"
            for i, username in enumerate(usernames_list[:10], 1):
                report += f"{i}. {username}\n"
            if len(usernames_list) > 10:
                report += f"\n... и еще {len(usernames_list) - 10} юзернеймов"
        else:
            report += "❌ Не удалось импортировать ни одного юзернейма"

        await message.answer(report, parse_mode="HTML")
        await state.clear()
        await show_user_profile(message, str(target_user_id))

    except Exception as e:
        logger.error(f"Error importing usernames: {e}")
        await message.answer(f"❌ Ошибка при импорте: {str(e)}")
        await state.clear()


# ==================== ОБРАБОТЧИК ТЕКСТА ====================

@dp.message(F.text)
async def handle_text(message: types.Message):
    try:
        user = message.from_user
        text = message.text

        db.add_user(user.id, user.username, user.first_name, user.last_name)

        if text and ('t.me/' in text or 'https://t.me/' in text):
            links = re.findall(r'(?:https?://)?t\.me/([^\s/]+)', text)
            for link in links:
                try:
                    status_msg = await message.answer(f"⏳ Проверяю '{link}' и собираю сообщения...")
                    try:
                        entity = await user_client.get_entity(f'@{link}')
                    except:
                        entity = await user_client.get_entity(link)

                    is_user = await check_is_user(link)

                    if is_user:
                        await status_msg.edit_text(f"👤 '{link}' - это пользователь, а не чат. Показываю профиль...")
                        await show_user_profile(message, link)
                        return

                    if hasattr(entity, 'megagroup') or hasattr(entity, 'group') or hasattr(entity, 'channel'):
                        try:
                            await user_client(JoinChannelRequest(entity))
                        except UserAlreadyParticipantError:
                            pass
                        except Exception as e:
                            logger.error(f"Join error: {e}")

                    count = await collect_all_chat_messages(entity.id, 10000000, 100000)
                    await status_msg.edit_text(
                        f"✅ Чат '{entity.title if hasattr(entity, 'title') else link}' добавлен!\n"
                        f"📊 Собрано {count} сообщений"
                    )
                except FloodWaitError as e:
                    await message.answer(f"⏳ Telegram сказал подождать {e.seconds} секунд...")
                except Exception as e:
                    await message.answer(f"❌ Ошибка при обработке ссылки: {str(e)}")
            return

        if text and '@' in text and 't.me/' not in text:
            usernames = re.findall(r'@(\w+)', text)
            for username in usernames:
                try:
                    is_user = await check_is_user(username)

                    if is_user:
                        await show_user_profile(message, username)
                    else:
                        status_msg = await message.answer(f"⏳ Обнаружен чат @{username}. Собираю сообщения...")
                        try:
                            entity = await user_client.get_entity(f'@{username}')
                            if hasattr(entity, 'megagroup') or hasattr(entity, 'group') or hasattr(entity, 'channel'):
                                try:
                                    await user_client(JoinChannelRequest(entity))
                                except UserAlreadyParticipantError:
                                    pass
                                except Exception as e:
                                    logger.error(f"Join error: {e}")

                            count = await collect_all_chat_messages(entity.id, 10000000, 100000)
                            await status_msg.edit_text(
                                f"✅ Чат '{entity.title if hasattr(entity, 'title') else username}' добавлен!\n"
                                f"📊 Собрано {count} сообщений"
                            )
                        except Exception as e:
                            await status_msg.edit_text(f"❌ Ошибка при обработке @{username}: {str(e)}")
                except Exception as e:
                    await message.answer(f"❌ Ошибка при обработке @{username}: {str(e)}")
            return

        if text and text.isdigit() and len(text) > 5:
            await show_user_profile(message, text)

    except Exception as e:
        logger.error(f"Error in handle_text: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")


# ==================== ЗАПУСК ====================

async def main():
    print("=" * 50)
    print("🤖 БОТ ЗАПУЩЕН!")
    print(f"📊 БАЗА: {DB_NAME}")
    print(f"👑 АДМИН ID: {ADMIN_ID}")
    print("=" * 50)

    print("\n⚠️ Нужно авторизоваться в Telethon.")
    print("📱 Введите номер телефона с кодом страны (например: +79123456789)\n")

    try:
        await user_client.start()
        print("\n✅ Telethon клиент авторизован!")
        me = await user_client.get_me()
        print(f"👤 Пользователь: {me.first_name} (@{me.username})")
        print(f"📱 Телефон: {me.phone if hasattr(me, 'phone') else 'Неизвестно'}")
        print("=" * 50)
    except Exception as e:
        print(f"\n❌ Ошибка авторизации: {e}")
        return

    print("\n🚀 Бот готов к работе!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
    except Exception as e:
        logger.error(f"Fatal error: {e}")