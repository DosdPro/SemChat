import os
import json
import uuid
import random
import time
import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__, static_folder='.', static_url_path='')
socketio = SocketIO(app, cors_allowed_origins="*")

# ================== ФАЙЛЫ ДАННЫХ ==================
USERS_FILE = 'users.json'
MESSAGES_FILE = 'messages.json'
CHANNELS_FILE = 'channels.json'
PROMOS_FILE = 'promos.json'
POSTS_FILE = 'posts.json'
TOURNEY_FILE = 'tourney.json'
GROUPS_FILE = 'groups.json'

DB = {
    'users': {},
    'messages': {},
    'channels': {},
    'promos_used': {},
    'posts': {},
    'tourney': {'scores': {}},
    'groups': {},
}

# ================== НАСТРОЙКИ ==================
PROMO_CODES = {'dravsem': 500}
RESERVED_NAMES = {'bot', 'system', 'admin', 'dezeyt'}

BOT_NAME = 'bot'
OWNER_NAME = 'dezeyt'
OWNER_PASSWORD = 'ermolovo4'

CHANNEL_LIMIT_FREE = 10
CHANNEL_LIMIT_PREMIUM = 20
PREMIUM_PRICE = 500
GIFT_BUY_STARS = 500
POST_MAX_LEN = 2000

REACTION_EMOJI = ['👍', '❤️', '😂', '🔥', '🥺', '🐱', '🚀', '🎉', '😀', '😍', '😎', '😢', '😡', '😱', '🤔', '💯']

# ================== ИГРА «3 В РЯД» (match-3) ==================
GAME_TIME_LIMIT = 90        # секунд на партию
GAME_PRIZES = [100, 50, 10] # места 1/2/3
GEM_COLORS = ['🔴', '🟢', '🟡', '🟠', '🔵', '🟣']  # 6 видов кристаллов

GAME_STATE = {}  # username -> {'board', 'score', 'started'}

# ================== БОТ ==================
BOT_GREETINGS = ['Привет! 👋 Как дела?', 'Привет-привет! Чем занят?', 'Здравствуй! Рады видеть!']
BOT_SMALLTALK = [
    'Интересно! Расскажи подробнее 🙂',
    'Понял тебя. А в «3 в ряд» сыграть хочешь? 🎮',
    'Ага. Кстати, топ-3 турнира получают звёзды!',
    'Ха, люблю такие истории. Что дальше было?',
    'Согласен! Иногда лучше просто сыграть партию 🎮',
]


# ================== ЗАГРУЗКА / СОХРАНЕНИЕ ==================
def load_db():
    for fname, key in [(USERS_FILE, 'users'), (MESSAGES_FILE, 'messages'),
                       (CHANNELS_FILE, 'channels'), (PROMOS_FILE, 'promos_used'),
                       (POSTS_FILE, 'posts'), (TOURNEY_FILE, 'tourney'),
                       (GROUPS_FILE, 'groups')]:
        if os.path.exists(fname):
            try:
                with open(fname, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    DB[key] = data
            except Exception as e:
                print(f'Ошибка чтения {fname}: {e}')
    DB['tourney'].setdefault('scores', {})
    migrate_channels()
    migrate_groups()
    ensure_accounts()


def migrate_channels():
    for owner, chs in DB['channels'].items():
        for ch in chs:
            if 'id' not in ch:
                ch['id'] = uuid.uuid4().hex[:12]
            ch.setdefault('title', 'Без названия')
            ch.setdefault('subscribers', [])


def migrate_groups():
    for gid, g in DB['groups'].items():
        g.setdefault('id', gid)
        g.setdefault('title', 'Без названия')
        g.setdefault('owner', '')
        g.setdefault('members', [])
        g.setdefault('messages', [])


def ensure_accounts():
    """Бот и владелец существуют всегда. Бот есть в контактах у всех."""
    if BOT_NAME not in DB['users']:
        DB['users'][BOT_NAME] = {
            'password': uuid.uuid4().hex,
            'bio': 'SemBot 🎮 Напиши мне что-нибудь или сыграй в «3 в ряд»!',
            'avatar': '', 'stars': 0, 'premium': False,
            'contacts': [], 'blocked': [],
        }
    if OWNER_NAME not in DB['users'] or DB['users'][OWNER_NAME].get('password') != OWNER_PASSWORD:
        DB['users'][OWNER_NAME] = {
            'password': OWNER_PASSWORD,
            'bio': 'Владелец SemChat',
            'avatar': '', 'stars': 100000, 'premium': True,
            'contacts': [], 'blocked': [],
        }
    # Бот в контактах у всех существующих пользователей
    for name, u in DB['users'].items():
        if name == BOT_NAME:
            continue
        contacts = u.setdefault('contacts', [])
        if BOT_NAME not in contacts:
            contacts.append(BOT_NAME)
    save_db()


def save_db():
    for fname, key in [(USERS_FILE, 'users'), (MESSAGES_FILE, 'messages'),
                       (CHANNELS_FILE, 'channels'), (PROMOS_FILE, 'promos_used'),
                       (POSTS_FILE, 'posts'), (TOURNEY_FILE, 'tourney'),
                       (GROUPS_FILE, 'groups')]:
        try:
            with open(fname, 'w', encoding='utf-8') as f:
                json.dump(DB[key], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'Ошибка записи {fname}: {e}')


def now_str():
    return datetime.datetime.now().isoformat(timespec='seconds')


def chat_key(a, b):
    return '|'.join(sorted([a, b]))


def get_chat(a, b):
    return DB['messages'].setdefault(chat_key(a, b), [])


def find_message(msg_id):
    for msgs in DB['messages'].values():
        for m in msgs:
            if m.get('id') == msg_id:
                return m
    return None


def find_channel(cid):
    for owner, chs in DB['channels'].items():
        for ch in chs:
            if ch.get('id') == cid:
                return owner, ch
    return None, None


def user_public(u):
    info = DB['users'].get(u, {})
    return {
        'username': u,
        'bio': info.get('bio', ''),
        'premium': info.get('premium', False),
        'avatar': info.get('avatar', ''),
        'online': is_online(u),
    }


# ================== БОТ ==================
def add_bot_message(to, text):
    """Сообщение от бота в личный чат с пользователем."""
    if to not in DB['users'] or to == BOT_NAME:
        return
    get_chat(BOT_NAME, to).append({
        'sender': BOT_NAME,
        'text': text,
        'type': 'text',
        'time': now_str(),
        'id': uuid.uuid4().hex,
        'reactions': {},
    })
    socketio.emit('new_message', {'from': BOT_NAME, 'to': to}, room=to)


def bot_reply_text(text):
    t = (text or '').strip().lower()
    if not t:
        return 'Круто, но я лучше понимаю текст 🙂'
    if any(k in t for k in ['привет', 'хай', 'здравств', 'hello', 'hi', 'хеллоу']):
        return random.choice(BOT_GREETINGS)
    if any(k in t for k in ['как дела', 'как ты', 'чё как', 'что делаешь', 'чем занят']):
        return random.choice([
            'Отлично! Кручу логику и жду соперника в «3 в ряд» 🎮',
            'Всё чётко! Сам как?',
        ])
    if any(k in t for k in ['игра', '3 в ряд', 'три в ряд', 'кристал', 'сыгра', 'сыграть', 'турнир']):
        return 'Жми на 💎 вверху чата и вперёд! Меняй кристаллы местами, собирай 3+ в линию. Топ-3 турнира получают звёзды: 🥇 100 · 🥈 50 · 🥉 10. У тебя 90 секунд на партию!'
    if any(k in t for k in ['звёзд', 'звезд', 'stars', 'star']):
        return 'Звёзды за турнир выдаёт владелец SemChat. Играй в «3 в ряд», набирай очки и попадай в топ-3!'
    if any(k in t for k in ['помощь', 'help', 'что умеешь', 'команды']):
        return 'Я умею: болтать 🗨 и играть с тобой в «3 в ряд» 🎮 (кнопка 💎 вверху чата). Призы турнира: 🥇 100 · 🥈 50 · 🥉 10 ⭐.'
    if any(k in t for k in ['спасибо', 'благодар']):
        return random.choice(['Всегда пожалуйста! 😊', 'Обращайся!'])
    if any(k in t for k in ['пока', 'до встречи', 'бай', 'прощай']):
        return random.choice(['Пока-пока! 👋', 'До связи! Заходи играть 🎮'])
    return random.choice(BOT_SMALLTALK)


# ================== ОНЛАЙН-СТАТУС ==================
online = {}


def is_online(username):
    return username in set(online.values())


def broadcast_presence(username, status):
    socketio.emit('presence', {'username': username, 'online': status})


# ================== СТАТИКА ==================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


# ================== АВТОРИЗАЦИЯ ==================
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    if not username or not password:
        return jsonify({'ok': False, 'error': 'Заполните все поля'})
    if len(username) < 3 or len(username) > 20:
        return jsonify({'ok': False, 'error': 'Логин от 3 до 20 символов'})
    if not username.isalnum():
        return jsonify({'ok': False, 'error': 'Только буквы и цифры без пробелов'})
    if username.lower() in RESERVED_NAMES:
        return jsonify({'ok': False, 'error': 'Это имя зарезервировано'})
    if username in DB['users']:
        return jsonify({'ok': False, 'error': 'Пользователь уже существует'})
    DB['users'][username] = {
        'password': password,
        'bio': '', 'avatar': '', 'stars': 0, 'premium': False,
        'contacts': [BOT_NAME],          # бот в контактах сразу, как в Telegram
        'blocked': [],
    }
    save_db()
    return jsonify({'ok': True})


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    if username not in DB['users'] or DB['users'][username].get('password') != password:
        return jsonify({'ok': False, 'error': 'Неверный логин или пароль'})
    return jsonify({'ok': True})


# ================== ПРОФИЛЬ ==================
@app.route('/profile/<username>', methods=['GET'])
def get_profile(username):
    if username not in DB['users']:
        return jsonify({'stars': 0, 'premium': False, 'avatar': '', 'bio': ''})
    u = DB['users'][username]
    return jsonify({
        'stars': u.get('stars', 0),
        'premium': u.get('premium', False),
        'avatar': u.get('avatar', ''),
        'bio': u.get('bio', ''),
    })


@app.route('/profile/<username>', methods=['POST'])
def set_profile(username):
    if username not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет такого пользователя'})
    data = request.get_json(silent=True) or {}
    u = DB['users'][username]
    if 'avatar' in data:
        u['avatar'] = data['avatar']
    if 'bio' in data:
        u['bio'] = str(data['bio'])[:200]
    if data.get('new_password'):
        u['password'] = data['new_password']
    save_db()
    return jsonify({'ok': True})


# ================== КОНТАКТЫ И ПОИСК ==================
@app.route('/users/<me>')
def list_users(me):
    if me not in DB['users']:
        return jsonify([])
    names = DB['users'][me].get('contacts', [])
    return jsonify([user_public(n) for n in names if n in DB['users']])


@app.route('/online')
def online_list():
    return jsonify(sorted(set(online.values())))


@app.route('/search_users/<me>/<query>')
def search_users(me, query):
    q = (query or '').lower()
    my_contacts = DB['users'][me].get('contacts', []) if me in DB['users'] else []
    results = [user_public(n) for n in DB['users']
               if q in n.lower() and n != me and n not in my_contacts]
    return jsonify(results)


@app.route('/add_contact/<me>/<other>', methods=['POST'])
def add_contact(me, other):
    if me not in DB['users'] or other not in DB['users'] or me == other:
        return jsonify({'ok': False})
    contacts = DB['users'][me].setdefault('contacts', [])
    if other not in contacts:
        contacts.append(other)
        save_db()
    return jsonify({'ok': True})


# ================== БЛОКИРОВКИ ==================
@app.route('/block/<me>/<other>', methods=['POST'])
def block(me, other):
    if me in DB['users'] and other in DB['users']:
        blocked = DB['users'][me].setdefault('blocked', [])
        if other not in blocked:
            blocked.append(other)
            save_db()
    return jsonify({'ok': True})


@app.route('/unblock/<me>/<other>', methods=['POST'])
def unblock(me, other):
    if me in DB['users']:
        blocked = DB['users'][me].setdefault('blocked', [])
        DB['users'][me]['blocked'] = [b for b in blocked if b != other]
        save_db()
    return jsonify({'ok': True})


def is_blocked(sender, receiver):
    return sender in DB['users'].get(receiver, {}).get('blocked', [])


# ================== ГРУППЫ (пишут все участники) ==================
GROUP_LIMIT = 10
FILE_MAX_LEN = 7 * 1024 * 1024  # ~5 МБ файла в base64


def find_group(gid):
    return DB['groups'].get(gid)


@app.route('/groups/<me>')
def groups_list(me):
    if me not in DB['users']:
        return jsonify([])
    result = []
    for g in DB['groups'].values():
        if me == g['owner'] or me in g.get('members', []):
            result.append({'id': g['id'], 'title': g['title'], 'owner': g['owner'],
                           'members': len(g.get('members', []))})
    return jsonify(result)


@app.route('/create_group/<me>', methods=['POST'])
def create_group(me):
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'ok': False, 'error': 'Введите название'})
    if len(title) > 50:
        return jsonify({'ok': False, 'error': 'Название слишком длинное (до 50 символов)'})
    mine = [g for g in DB['groups'].values() if g['owner'] == me]
    if len(mine) >= GROUP_LIMIT:
        return jsonify({'ok': False, 'error': 'Достигнут лимит групп'})
    gid = uuid.uuid4().hex[:12]
    DB['groups'][gid] = {'id': gid, 'title': title, 'owner': me, 'members': [], 'messages': []}
    save_db()
    return jsonify({'ok': True, 'id': gid})


@app.route('/group/<gid>')
def group_info(gid):
    g = find_group(gid)
    if not g:
        return jsonify({'ok': False, 'error': 'Группа не найдена'})
    me = request.args.get('me', '')
    is_member = me == g['owner'] or me in g.get('members', [])
    return jsonify({
        'ok': True, 'id': gid, 'title': g['title'], 'owner': g['owner'],
        'members': g.get('members', []),
        'messages': g.get('messages', []),
        'is_owner': me == g['owner'],
        'is_member': is_member,
    })


def validate_file_payload(text):
    """Проверка вложения: JSON с name и data (data URL). Возвращает (ok, error)."""
    try:
        fdata = json.loads(text or '')
    except Exception:
        return False, 'Некорректный файл'
    if not isinstance(fdata, dict):
        return False, 'Некорректный файл'
    data_url = str(fdata.get('data', ''))
    if not data_url.startswith('data:'):
        return False, 'Некорректный файл'
    if len(data_url) > FILE_MAX_LEN:
        return False, 'Файл слишком большой (до 5 МБ)'
    return True, ''


@app.route('/group/<gid>/send', methods=['POST'])
def group_send(gid):
    g = find_group(gid)
    if not g:
        return jsonify({'ok': False, 'error': 'Группа не найдена'})
    data = request.get_json(silent=True) or {}
    sender = data.get('sender')
    text = data.get('text')
    mtype = data.get('type', 'text')
    if sender not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    if sender != g['owner'] and sender not in g.get('members', []):
        return jsonify({'ok': False, 'error': 'Ты не участник этой группы'})
    if mtype == 'file':
        ok, err = validate_file_payload(text)
        if not ok:
            return jsonify({'ok': False, 'error': err})
    elif not (text or '').strip():
        return jsonify({'ok': False, 'error': 'Пустое сообщение'})
    msg = {'sender': sender, 'text': text, 'type': mtype, 'time': now_str(),
           'id': uuid.uuid4().hex, 'reactions': {}}
    g.setdefault('messages', []).append(msg)
    save_db()
    socketio.emit('group_message', {'group_id': gid, 'msg': msg})
    return jsonify({'ok': True, 'msg': msg})


@app.route('/group/<gid>/invite', methods=['POST'])
def group_invite(gid):
    g = find_group(gid)
    if not g:
        return jsonify({'ok': False, 'error': 'Группа не найдена'})
    data = request.get_json(silent=True) or {}
    receiver = data.get('receiver')
    if receiver not in DB['users']:
        return jsonify({'ok': False, 'error': 'Пользователь не найден'})
    if receiver != g['owner'] and receiver not in g.get('members', []):
        g.setdefault('members', []).append(receiver)
        add_bot_message(receiver, f'👋 Вас добавили в группу «{g["title"]}»! Она уже в списке чатов.')
    save_db()
    return jsonify({'ok': True, 'members': len(g.get('members', [])) + 1})


@app.route('/group/<gid>/leave', methods=['POST'])
def group_leave(gid):
    g = find_group(gid)
    if not g:
        return jsonify({'ok': False, 'error': 'Группа не найдена'})
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    if username != g['owner']:
        g['members'] = [m for m in g.get('members', []) if m != username]
        save_db()
    return jsonify({'ok': True})


@app.route('/group/<gid>/delete', methods=['POST'])
def group_delete(gid):
    g = find_group(gid)
    if not g:
        return jsonify({'ok': False, 'error': 'Группа не найдена'})
    data = request.get_json(silent=True) or {}
    if data.get('username') != g['owner']:
        return jsonify({'ok': False, 'error': 'Удалять может только создатель'})
    DB['groups'].pop(gid, None)
    save_db()
    return jsonify({'ok': True})


# ================== СООБЩЕНИЯ ==================
@app.route('/messages/<me>/<other>')
def get_messages(me, other):
    if me not in DB['users']:
        return jsonify([])
    if other in DB['users'][me].get('blocked', []):
        return jsonify([])
    return jsonify(get_chat(me, other))


@app.route('/send', methods=['POST'])
def send_message():
    data = request.get_json(silent=True) or {}
    sender = data.get('sender')
    receiver = data.get('receiver')
    text = data.get('text')
    mtype = data.get('type', 'text')
    if sender not in DB['users'] or receiver not in DB['users']:
        return jsonify({'ok': False, 'error': 'Пользователь не найден'})
    if is_blocked(sender, receiver):
        return jsonify({'ok': False, 'error': 'Вы заблокированы этим пользователем'})
    if mtype == 'file':
        ok, err = validate_file_payload(text)
        if not ok:
            return jsonify({'ok': False, 'error': err})
    elif mtype in ('text', 'gift') and not (text or '').strip():
        return jsonify({'ok': False, 'error': 'Пустое сообщение'})
    get_chat(sender, receiver).append({
        'sender': sender,
        'text': text,
        'type': mtype,
        'time': now_str(),
        'id': uuid.uuid4().hex,
        'reactions': {},
    })
    # Бот отвечает всегда, как в Telegram
    if receiver == BOT_NAME and mtype == 'text':
        add_bot_message(sender, bot_reply_text(text or ''))
    save_db()
    socketio.emit('new_message', {'from': sender, 'to': receiver}, room=receiver)
    return jsonify({'ok': True})


@app.route('/bot/start/<me>', methods=['POST'])
def bot_start(me):
    """Кнопка бота в чате: добавляет бота в контакты и здоровается."""
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    contacts = DB['users'][me].setdefault('contacts', [])
    if BOT_NAME not in contacts:
        contacts.append(BOT_NAME)
        add_bot_message(me, 'Привет! Я SemBot 👋 Пиши мне что угодно, а ещё жми 💎 вверху чата — сыграем в «3 в ряд»! Призы: 🥇 100 · 🥈 50 · 🥉 10.')
        save_db()
    return jsonify({'ok': True})


@app.route('/delete_chat/<me>/<other>', methods=['POST'])
def delete_chat(me, other):
    DB['messages'][chat_key(me, other)] = []
    save_db()
    return jsonify({'ok': True})


# ================== РЕАКЦИИ ==================
@app.route('/react', methods=['POST'])
def react():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    msg_id = data.get('msg_id')
    emoji = data.get('emoji')
    if username not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    if emoji not in REACTION_EMOJI:
        return jsonify({'ok': False, 'error': 'Недопустимая реакция'})
    target = find_message(msg_id)
    if not target:
        return jsonify({'ok': False, 'error': 'Сообщение не найдено'})
    reactions = target.setdefault('reactions', {})
    users = reactions.setdefault(emoji, [])
    if username in users:
        users.remove(username)
        if not users:
            del reactions[emoji]
    else:
        users.append(username)
    save_db()
    socketio.emit('msg_reactions', {'msg_id': msg_id, 'reactions': target['reactions']})
    return jsonify({'ok': True, 'reactions': target['reactions']})


# ================== ЗВЁЗДЫ / ПРОМОКОД / PREMIUM / ПОДАРКИ ==================
@app.route('/buy_stars/<me>', methods=['POST'])
def buy_stars(me):
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    DB['users'][me]['stars'] = DB['users'][me].get('stars', 0) + GIFT_BUY_STARS
    save_db()
    return jsonify({'ok': True, 'stars': DB['users'][me]['stars']})


@app.route('/promo/<me>/<code>', methods=['POST'])
def promo(me, code):
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    code = (code or '').strip().lower()
    used = DB['promos_used'].setdefault(me, [])
    if code in used:
        return jsonify({'ok': False, 'error': 'Промокод уже использован на этом аккаунте'})
    if code not in PROMO_CODES:
        return jsonify({'ok': False, 'error': 'Промокод не найден'})
    used.append(code)
    DB['users'][me]['stars'] = DB['users'][me].get('stars', 0) + PROMO_CODES[code]
    save_db()
    return jsonify({'ok': True, 'stars': DB['users'][me]['stars']})


@app.route('/buy_premium/<me>', methods=['POST'])
def buy_premium(me):
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    u = DB['users'][me]
    if u.get('premium'):
        return jsonify({'ok': False, 'error': 'Premium уже куплен'})
    if u.get('stars', 0) < PREMIUM_PRICE:
        return jsonify({'ok': False, 'error': 'Недостаточно звезд'})
    u['stars'] -= PREMIUM_PRICE
    u['premium'] = True
    save_db()
    return jsonify({'ok': True, 'stars': u['stars']})


@app.route('/gift', methods=['POST'])
def gift():
    data = request.get_json(silent=True) or {}
    sender = data.get('sender')
    receiver = data.get('receiver')
    payload = data.get('payload')
    if sender not in DB['users'] or receiver not in DB['users']:
        return jsonify({'ok': False, 'error': 'Пользователь не найден'})
    if is_blocked(sender, receiver):
        return jsonify({'ok': False, 'error': 'Вы заблокированы этим пользователем'})
    try:
        price = int((payload or {}).get('price', 0))
        name = str((payload or {}).get('name', 'Подарок'))
    except Exception:
        return jsonify({'ok': False, 'error': 'Некорректная цена'})
    u = DB['users'][sender]
    if u.get('stars', 0) < price:
        return jsonify({'ok': False, 'error': 'Недостаточно звезд'})
    u['stars'] -= price
    get_chat(sender, receiver).append({
        'sender': sender,
        'text': json.dumps(payload, ensure_ascii=False) if payload else '{}',
        'type': 'gift',
        'time': now_str(),
        'id': uuid.uuid4().hex,
        'reactions': {},
    })
    if receiver == BOT_NAME:
        add_bot_message(sender, f'Спасибо за подарок «{name}»! 🎁 Очень приятно!')
    save_db()
    socketio.emit('new_message', {'from': sender, 'to': receiver}, room=receiver)
    return jsonify({'ok': True, 'stars': u['stars']})


@app.route('/sell_gift/<me>/<msg_id>', methods=['POST'])
def sell_gift(me, msg_id):
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    for msgs in DB['messages'].values():
        for m in msgs:
            if m.get('id') == msg_id and m.get('type') == 'gift' and m.get('sender') != me:
                try:
                    price = int(json.loads(m.get('text') or '{}').get('price', 0))
                except Exception:
                    price = 0
                gain = price // 2
                msgs.remove(m)
                DB['users'][me]['stars'] = DB['users'][me].get('stars', 0) + gain
                save_db()
                return jsonify({'ok': True, 'stars': DB['users'][me]['stars'], 'gain': gain})
    return jsonify({'ok': False, 'error': 'Подарок не найден или уже продан'})


# ================== ИГРА «3 В РЯД» (match-3) ==================
def find_matches(board):
    matches = set()
    for r in range(8):
        run = 1
        for c in range(1, 8):
            if board[r][c] == board[r][c - 1]:
                run += 1
                if run >= 3:
                    for k in range(c - run + 1, c + 1):
                        matches.add((r, k))
            else:
                run = 1
    for c in range(8):
        run = 1
        for r in range(1, 8):
            if board[r][c] == board[r - 1][c]:
                run += 1
                if run >= 3:
                    for k in range(r - run + 1, r + 1):
                        matches.add((k, c))
            else:
                run = 1
    return matches


def resolve_board(board):
    """Убирает линии, обрушивает поле, начисляет очки с комбо."""
    total = 0
    combo = 0
    while True:
        m = find_matches(board)
        if not m:
            break
        combo += 1
        total += len(m) * 10 * combo
        for (r, c) in m:
            board[r][c] = None
        for c in range(8):
            col = [board[r][c] for r in range(8) if board[r][c] is not None]
            col = [None] * (8 - len(col)) + col
            for r in range(8):
                board[r][c] = col[r]
        for r in range(8):
            for c in range(8):
                if board[r][c] is None:
                    board[r][c] = random.randrange(len(GEM_COLORS))
    return total


def has_possible_move(board):
    for r in range(8):
        for c in range(8):
            for dr, dc in ((0, 1), (1, 0)):
                r2, c2 = r + dr, c + dc
                if r2 >= 8 or c2 >= 8:
                    continue
                board[r][c], board[r2][c2] = board[r2][c2], board[r][c]
                ok = bool(find_matches(board))
                board[r][c], board[r2][c2] = board[r2][c2], board[r][c]
                if ok:
                    return True
    return False


def fresh_board():
    for _ in range(200):
        board = [[random.randrange(len(GEM_COLORS)) for _ in range(8)] for _ in range(8)]
        guard = 0
        m = find_matches(board)
        while m and guard < 50:
            for (r, c) in m:
                board[r][c] = random.randrange(len(GEM_COLORS))
            m = find_matches(board)
            guard += 1
        if not m and has_possible_move(board):
            return board
    return board


@app.route('/game/start/<me>', methods=['POST'])
def game_start(me):
    if me not in DB['users'] or me in RESERVED_NAMES:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    GAME_STATE[me] = {'board': fresh_board(), 'score': 0, 'started': time.time()}
    return jsonify({'ok': True, 'board': GAME_STATE[me]['board'],
                    'score': 0, 'time_limit': GAME_TIME_LIMIT, 'gems': GEM_COLORS})


@app.route('/game/move/<me>', methods=['POST'])
def game_move(me):
    st = GAME_STATE.get(me)
    if not st:
        return jsonify({'ok': False, 'error': 'Игра не начата'})
    if time.time() - st['started'] > GAME_TIME_LIMIT + 5:
        return jsonify({'ok': False, 'error': 'Время вышло', 'timeout': True})
    data = request.get_json(silent=True) or {}
    try:
        r1, c1, r2, c2 = int(data['r1']), int(data['c1']), int(data['r2']), int(data['c2'])
    except Exception:
        return jsonify({'ok': False, 'error': 'Некорректный ход'})
    if not (0 <= r1 < 8 and 0 <= c1 < 8 and 0 <= r2 < 8 and 0 <= c2 < 8):
        return jsonify({'ok': False, 'error': 'Вне поля'})
    if abs(r1 - r2) + abs(c1 - c2) != 1:
        return jsonify({'ok': False, 'error': 'Менять можно только соседние'})
    board = st['board']
    board[r1][c1], board[r2][c2] = board[r2][c2], board[r1][c1]
    if not find_matches(board):
        board[r1][c1], board[r2][c2] = board[r2][c2], board[r1][c1]
        return jsonify({'ok': False, 'error': 'Этот ход не создаёт линию'})
    gained = resolve_board(board)
    st['score'] += gained
    if not has_possible_move(board):
        st['board'] = fresh_board()
    return jsonify({'ok': True, 'board': st['board'], 'score': st['score'], 'gained': gained})


@app.route('/game/finish/<me>', methods=['POST'])
def game_finish(me):
    st = GAME_STATE.pop(me, None)
    if st is None:
        return jsonify({'ok': False, 'error': 'Нет активной игры'})
    score = st['score']
    tourney = DB.setdefault('tourney', {'scores': {}})
    tourney.setdefault('scores', {})
    best = max(tourney['scores'].get(me, 0), score)
    tourney['scores'][me] = best
    top = sorted(tourney['scores'].items(), key=lambda kv: -kv[1])
    place = next((i + 1 for i, (u, _) in enumerate(top) if u == me), None)
    save_db()
    return jsonify({'ok': True, 'score': score, 'best': best, 'place': place})


@app.route('/game/leaderboard')
def game_leaderboard():
    scores = DB['tourney'].get('scores', {})
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:10]
    return jsonify([{'user': u, 'score': s} for u, s in top])


# ================== ПАНЕЛЬ ВЛАДЕЛЬЦА ==================
def require_owner(data):
    return (data or {}).get('admin') == OWNER_NAME and OWNER_NAME in DB['users']


@app.route('/admin/give_stars', methods=['POST'])
def admin_give_stars():
    data = request.get_json(silent=True) or {}
    if not require_owner(data):
        return jsonify({'ok': False, 'error': 'Только владелец может выдавать звёзды'})
    username = data.get('username')
    try:
        amount = int(data.get('amount', 0))
    except Exception:
        amount = 0
    if username not in DB['users']:
        return jsonify({'ok': False, 'error': 'Пользователь не найден'})
    if amount <= 0 or amount > 100000:
        return jsonify({'ok': False, 'error': 'Количество от 1 до 100000'})
    DB['users'][username]['stars'] = DB['users'][username].get('stars', 0) + amount
    add_bot_message(username, f'💰 Владелец выдал вам {amount} ⭐!')
    save_db()
    return jsonify({'ok': True, 'stars': DB['users'][username]['stars']})


@app.route('/admin/award_prizes', methods=['POST'])
def admin_award_prizes():
    data = request.get_json(silent=True) or {}
    if not require_owner(data):
        return jsonify({'ok': False, 'error': 'Только владелец может разыгрывать призы'})
    scores = DB['tourney'].get('scores', {})
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
    if not top:
        return jsonify({'ok': False, 'error': 'Нет результатов турнира'})
    medals = ['🥇', '🥈', '🥉']
    winners = []
    for i, (u, s) in enumerate(top):
        prize = GAME_PRIZES[i]
        DB['users'][u]['stars'] = DB['users'][u].get('stars', 0) + prize
        winners.append({'user': u, 'score': s, 'prize': prize})
        add_bot_message(u, f'{medals[i]} Поздравляем! {i + 1} место в турнире «3 в ряд» — счёт {s}. Вам начислено {prize} ⭐!')
    DB['tourney']['scores'] = {}
    save_db()
    return jsonify({'ok': True, 'winners': winners})


# ================== КАНАЛЫ ==================
@app.route('/channels/<me>')
def channels(me):
    if me not in DB['users']:
        return jsonify({'my_channels': [], 'subscribed': [], 'limit': CHANNEL_LIMIT_FREE})
    limit = CHANNEL_LIMIT_PREMIUM if DB['users'][me].get('premium') else CHANNEL_LIMIT_FREE
    mine = [{'id': ch['id'], 'title': ch['title'], 'subscribers': len(ch.get('subscribers', []))}
            for ch in DB['channels'].get(me, [])]
    subscribed = []
    for owner, chs in DB['channels'].items():
        for ch in chs:
            if owner != me and me in ch.get('subscribers', []):
                subscribed.append({'id': ch['id'], 'title': ch['title'], 'owner': owner})
    return jsonify({'my_channels': mine, 'subscribed': subscribed, 'limit': limit})


@app.route('/create_channel/<me>', methods=['POST'])
def create_channel(me):
    if me not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'ok': False, 'error': 'Введите название'})
    if len(title) > 50:
        return jsonify({'ok': False, 'error': 'Название слишком длинное (до 50 символов)'})
    limit = CHANNEL_LIMIT_PREMIUM if DB['users'][me].get('premium') else CHANNEL_LIMIT_FREE
    mine = DB['channels'].setdefault(me, [])
    if len(mine) >= limit:
        return jsonify({'ok': False, 'error': 'Достигнут лимит каналов'})
    channel = {'id': uuid.uuid4().hex[:12], 'title': title, 'subscribers': []}
    mine.append(channel)
    save_db()
    return jsonify({'ok': True, 'id': channel['id']})


@app.route('/search_channels/<me>/<query>')
def search_channels(me, query):
    """Поиск каналов по названию."""
    q = (query or '').strip().lower()
    if not q:
        return jsonify([])
    results = []
    for owner, chs in DB['channels'].items():
        for ch in chs:
            if q in ch.get('title', '').lower():
                results.append({
                    'id': ch['id'],
                    'title': ch['title'],
                    'owner': owner,
                    'subscribers': len(ch.get('subscribers', [])),
                    'subscribed': me in ch.get('subscribers', []),
                })
    return jsonify(results[:20])


@app.route('/channel/<cid>')
def channel_info(cid):
    """Информация о канале: посты, владелец, статус подписки."""
    owner, ch = find_channel(cid)
    if not ch:
        return jsonify({'ok': False, 'error': 'Канал не найден'})
    me = request.args.get('me', '')
    posts = DB['posts'].get(cid, [])
    return jsonify({
        'ok': True,
        'id': cid,
        'title': ch['title'],
        'owner': owner,
        'subscribers': len(ch.get('subscribers', [])),
        'is_owner': me == owner,
        'subscribed': me in ch.get('subscribers', []),
        'posts': posts,
    })


@app.route('/channel/<cid>/post', methods=['POST'])
def channel_post(cid):
    """Публикация поста. Только владелец канала."""
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    text = (data.get('text') or '').strip()
    owner, ch = find_channel(cid)
    if not ch:
        return jsonify({'ok': False, 'error': 'Канал не найден'})
    if username != owner:
        return jsonify({'ok': False, 'error': 'Писать в канал может только владелец'})
    if not text:
        return jsonify({'ok': False, 'error': 'Пустой пост'})
    if len(text) > POST_MAX_LEN:
        return jsonify({'ok': False, 'error': f'Пост слишком длинный (до {POST_MAX_LEN} символов)'})
    post = {'id': uuid.uuid4().hex, 'author': owner, 'text': text, 'time': now_str()}
    DB['posts'].setdefault(cid, []).append(post)
    save_db()
    socketio.emit('channel_post', {'channel_id': cid, 'post': post})
    return jsonify({'ok': True, 'post': post})


@app.route('/channel/<cid>/invite', methods=['POST'])
def channel_invite(cid):
    """Приглашение: кладёт сообщение с кнопкой «Подписаться» в личный чат."""
    data = request.get_json(silent=True) or {}
    sender = data.get('sender')
    receiver = data.get('receiver')
    if sender not in DB['users'] or receiver not in DB['users']:
        return jsonify({'ok': False, 'error': 'Пользователь не найден'})
    owner, ch = find_channel(cid)
    if not ch:
        return jsonify({'ok': False, 'error': 'Канал не найден'})
    if is_blocked(sender, receiver):
        return jsonify({'ok': False, 'error': 'Вы заблокированы этим пользователем'})
    get_chat(sender, receiver).append({
        'sender': sender,
        'text': json.dumps({'channel_id': cid, 'title': ch.get('title', 'Канал'), 'owner': owner},
                           ensure_ascii=False),
        'type': 'channel_invite',
        'time': now_str(),
        'id': uuid.uuid4().hex,
        'reactions': {},
    })
    save_db()
    socketio.emit('new_message', {'from': sender, 'to': receiver}, room=receiver)
    return jsonify({'ok': True})


@app.route('/channel/<cid>/join', methods=['POST'])
def channel_join(cid):
    """Подписка на канал."""
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    if username not in DB['users']:
        return jsonify({'ok': False, 'error': 'Нет пользователя'})
    owner, ch = find_channel(cid)
    if not ch:
        return jsonify({'ok': False, 'error': 'Канал не найден'})
    subs = ch.setdefault('subscribers', [])
    if username != owner and username not in subs:
        subs.append(username)
        save_db()
    return jsonify({'ok': True, 'subscribers': len(subs)})


@app.route('/channel/<cid>/leave', methods=['POST'])
def channel_leave(cid):
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    owner, ch = find_channel(cid)
    if not ch:
        return jsonify({'ok': False, 'error': 'Канал не найден'})
    subs = ch.setdefault('subscribers', [])
    if username in subs:
        subs.remove(username)
        save_db()
    return jsonify({'ok': True, 'subscribers': len(subs)})


@app.route('/channel/<cid>/delete', methods=['POST'])
def channel_delete(cid):
    """Удаление канала. Только владелец."""
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    owner, ch = find_channel(cid)
    if not ch:
        return jsonify({'ok': False, 'error': 'Канал не найден'})
    if username != owner:
        return jsonify({'ok': False, 'error': 'Удалять может только владелец'})
    mine = DB['channels'].get(owner, [])
    DB['channels'][owner] = [c for c in mine if c.get('id') != cid]
    DB['posts'].pop(cid, None)
    save_db()
    return jsonify({'ok': True})


# ================== ЗВОНКИ И ПРИСУТСТВИЕ (SocketIO) ==================
@socketio.on('register_online')
def register_online(data):
    username = data.get('username')
    if username:
        online[request.sid] = username
        join_room(username)
        broadcast_presence(username, True)


@socketio.on('disconnect')
def on_disconnect():
    username = online.pop(request.sid, None)
    if username and not is_online(username):
        broadcast_presence(username, False)


def sid_of(username):
    for sid, u in online.items():
        if u == username:
            return sid
    return None


@socketio.on('call_user')
def call_user(data):
    target = sid_of(data.get('target'))
    if target:
        socketio.emit('incoming_call', {'caller': data.get('caller')}, room=target)
    else:
        emit('call_error', {'message': 'Пользователь не в сети'})


@socketio.on('accept_call')
def accept_call(data):
    target = sid_of(data.get('target'))
    if target:
        socketio.emit('call_accepted', {'by': data.get('by')}, room=target)


@socketio.on('decline_call')
def decline_call(data):
    target = sid_of(data.get('target'))
    if target:
        socketio.emit('call_declined', {}, room=target)


@socketio.on('webrtc_signal')
def webrtc_signal(data):
    target = sid_of(data.get('target'))
    if target:
        socketio.emit('webrtc_signal', data.get('signal'), room=target)


if __name__ == '__main__':
    load_db()
    print('SemChat server: http://127.0.0.1:5000')
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
