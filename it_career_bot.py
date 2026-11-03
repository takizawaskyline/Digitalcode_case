# -*- coding: utf-8 -*-
"""
ВК-бот «Профессии в IT: найди своё будущее»
============================================

ЧТО УМЕЕТ БОТ
  • «Начать» / «Старт» — тест из 10 вопросов, ИИ подбирает IT-профессию.
  • Во время теста кнопки «Назад» (к прошлому вопросу) и «Отмена».
  • «Профессии» — каталог: описание каждой профессии по нажатию.
  • Под результатом и в каталоге — ссылки на ресурсы и вузы России.
  • «Ресурсы» — полезные сайты и курсы для старта.
  • «Помощь» — что умеет бот.
  • После результата — проценты совпадения с профессиями.
  • /stats — статистика тестов (только для админов, см. ADMIN_IDS).

ФАЙЛЫ ПРОЕКТА (кладите в одну папку)
  it_career_bot.py — логика бота (этот файл)
  texts.py         — все тексты, вопросы и описания профессий
  stats.json       — создастся сам, хранит статистику

ИНСТРУКЦИЯ ПО ЗАПУСКУ
---------------------
1. Установи библиотеки (в терминале):
       pip install vk_api requests gigachat

2. Получи токен сообщества ВК:
   - Открой своё сообщество -> «Управление» -> «Работа с API».
   - Вкладка «Ключи доступа» -> «Создать ключ».
   - Отметь права: «Разрешить приложению доступ к сообщениям сообщества».
   - Скопируй ключ (это и есть токен).
   - Вкладка «Long Poll API» -> включи, версия API 5.131 или новее,
     в «Типах событий» отметь «Входящее сообщение».
   - «Управление» -> «Сообщения» -> включи сообщения сообщества.

3. (Необязательно) Бесплатный ИИ — GigaChat от Сбера:
   - Зайди на https://developers.sber.ru/studio и войди через СберID.
   - Создай проект «GigaChat API» и нажми «Получить новый ключ».
     Ключ авторизации показывается ОДИН раз — сразу скопируй его
     (это и есть GIGACHAT_AUTH_KEY).
   - Физлицам даётся бесплатный пакет токенов (Freemium).
   Без ключа бот всё равно работает: включится анализ по баллам.

   Другой вариант: любой сервис с OpenAI-совместимым API (например
   Groq или OpenRouter) — см. переменные LLM_API_KEY, LLM_BASE_URL,
   LLM_MODEL ниже. Доступность из России проверь заранее.

4. Задай ключи через переменные окружения (так безопаснее, чем в коде):
       Windows (cmd):   set VK_TOKEN=твой_токен
       Linux / macOS:   export VK_TOKEN=твой_токен
   Аналогично GIGACHAT_AUTH_KEY.
   Чтобы видеть статистику, задай свой ID ВК: ADMIN_IDS=123456789
   (несколько ID — через запятую).
   Переменные без кавычек! НЕ выкладывай токены на GitHub!

5. Запусти:   python it_career_bot.py
"""

import json
import os
import random
import requests
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor

# Все тексты берём из отдельного файла texts.py
import texts

# ======================= НАСТРОЙКИ =======================

VK_TOKEN = os.getenv("VK_TOKEN", "")

# Вариант А (основной): GigaChat. Пусто = не используем.
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
# Модель GigaChat. GigaChat-2 — бесплатная лёгкая (Lite) модель.
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2")

# Вариант Б (запасной): любой OpenAI-совместимый API (Groq, OpenRouter и т.п.)
# Пример для Groq: LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# Если ни один ключ не задан или ИИ не ответил — работает анализ по баллам.

# ID администраторов (кто видит /stats): "123456,789012"
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
             if x.isdigit()}

STATS_FILE = "stats.json"

QUESTIONS = texts.QUESTIONS
TOTAL = len(QUESTIONS)  # сколько всего вопросов (сейчас 10)

# Названия кнопок. Бот узнаёт команды по этим словам.
BTN_START = "Начать"
BTN_PROFESSIONS = "Профессии"
BTN_RESOURCES = "Ресурсы"
BTN_HELP = "Помощь"
BTN_MENU = "Меню"
BTN_BACK = "Назад"
BTN_CANCEL = "Отмена"

# «Название профессии» -> «ключ» (нужно для кнопок каталога)
TITLE_TO_KEY = {title: key for key, title in texts.PROFESSION_TITLES.items()}

# ======================= СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ =======================
# Словарь: ID пользователя -> {"step": номер вопроса, "answers": [ответы]}
# step = 0 — ждём ответ на 1-й вопрос, 1 — на 2-й и т.д.
# Если пользователя нет в словаре — тест не идёт.
user_states = {}


# ======================= СТАТИСТИКА =======================

def load_stats():
    """Читает статистику из файла (или создаёт пустую)."""
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"started": 0, "completed": 0, "by_profession": {}}


def record_stat(event, profession=None):
    """Записывает событие: 'started' (начал тест) или 'completed' (закончил)."""
    try:
        stats = load_stats()
        stats[event] = stats.get(event, 0) + 1
        if profession:
            by_prof = stats.setdefault("by_profession", {})
            by_prof[profession] = by_prof.get(profession, 0) + 1
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # Статистика — не главное: если не записалась, бот не должен падать
        print("Не удалось записать статистику:", e)


def stats_text():
    """Собирает текст статистики для админа."""
    stats = load_stats()
    lines = [
        "📈 Статистика бота",
        f"Тестов начато: {stats.get('started', 0)}",
        f"Тестов завершено: {stats.get('completed', 0)}",
        "",
        "Какие профессии получили школьники:",
    ]
    by_prof = stats.get("by_profession", {})
    for key, title in texts.PROFESSION_TITLES.items():
        lines.append(f"• {title}: {by_prof.get(key, 0)}")
    return "\n".join(lines)


# ======================= КЛАВИАТУРЫ =======================

def menu_keyboard():
    """Главное меню: всегда на экране, пока человек не в тесте."""
    kb = VkKeyboard(one_time=False)
    kb.add_button(BTN_START, color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button(BTN_PROFESSIONS)
    kb.add_button(BTN_RESOURCES)
    kb.add_line()
    kb.add_button(BTN_HELP)
    return kb.get_keyboard()


def professions_keyboard():
    """Кнопки каталога: по одной на каждую профессию + «Меню»."""
    kb = VkKeyboard(one_time=False)
    for title in texts.PROFESSION_TITLES.values():
        kb.add_button(title, color=VkKeyboardColor.PRIMARY)
        kb.add_line()
    kb.add_button(BTN_MENU)
    return kb.get_keyboard()


def answers_keyboard(options, show_back):
    """Кнопки с вариантами ответа + «Назад» и «Отмена» внизу.
    options — список пар (текст кнопки, баллы), берём только текст."""
    kb = VkKeyboard(one_time=True)
    for label, _scores in options:
        kb.add_button(label, color=VkKeyboardColor.PRIMARY)
        kb.add_line()
    if show_back:  # на первом вопросе «Назад» не нужна
        kb.add_button(BTN_BACK)
    kb.add_button(BTN_CANCEL, color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


# ======================= ОТПРАВКА СООБЩЕНИЙ =======================

def send(vk, user_id, text, keyboard=None, no_preview=False):
    """Отправляет сообщение пользователю.
    no_preview=True — ВК не будет показывать большие карточки-превью
    для ссылок в тексте (удобно, когда ссылок много)."""
    params = {
        "user_id": user_id,
        "message": text,
        "random_id": random.getrandbits(31),  # ВК требует случайное число
    }
    if no_preview:
        params["dont_parse_links"] = 1
    if keyboard:
        params["keyboard"] = keyboard
    vk.messages.send(**params)


# ======================= ПОДСЧЁТ БАЛЛОВ =======================

def compute_scores(answers):
    """Считает, насколько ответы подходят каждой профессии (в процентах).
    Каждый ответ даёт баллы профессиям (см. texts.py). Баллы делим на то,
    сколько раз профессия упоминается в тесте, чтобы «популярные»
    профессии не побеждали слишком часто. Сумма процентов = 100."""
    raw = {name: 0.0 for name in texts.PROFESSIONS}

    # Сколько раз каждая профессия встречается в вариантах ответов
    mentions = {name: 0 for name in texts.PROFESSIONS}
    for q in QUESTIONS:
        for _label, points in q["options"]:
            for profession in points:
                mentions[profession] += 1

    for i, answer in enumerate(answers):
        # Ищем среди вариантов вопроса тот, что выбрал пользователь
        for label, points in QUESTIONS[i]["options"]:
            if label == answer:
                for profession, value in points.items():
                    raw[profession] += value / mentions[profession]

    total = sum(raw.values())
    if total == 0:
        return {name: 0.0 for name in raw}
    return {name: value / total * 100 for name, value in raw.items()}


def scores_text(scores):
    """Текст с тремя лучшими совпадениями: «🎨 UI/UX-дизайнер — 34%»."""
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
    lines = [texts.SCORES_HEADER]
    for key, percent in top:
        emoji = texts.PROFESSION_EMOJI.get(key, "•")
        lines.append(f"{emoji} {texts.PROFESSION_TITLES[key]} — {round(percent)}%")
    return "\n".join(lines)


# ======================= РЕСУРСЫ И ВУЗЫ =======================

# По каким словам в ответе ИИ понимаем, какую профессию он назвал
PROFESSION_KEYWORDS = {
    "web": ["веб-разработчик", "веб разработчик", "web-разработчик"],
    "tester": ["тестировщик"],
    "designer": ["ui/ux", "ux/ui", "дизайнер"],
    "mobile": ["мобильн"],
    "game": ["разработчик игр", "геймдев", "гейм-разработчик"],
}


def detect_profession(ai_text, default):
    """Определяет, какую профессию назвал ИИ в своём ответе.
    Берём то ключевое слово, которое встречается в тексте раньше всех
    (ИИ просим называть профессию в начале). Если ничего не нашли —
    возвращаем default (профессию с лучшим счётом по баллам)."""
    lowered = ai_text.lower()
    best_key, best_pos = default, None
    for key, words in PROFESSION_KEYWORDS.items():
        for word in words:
            pos = lowered.find(word)
            if pos != -1 and (best_pos is None or pos < best_pos):
                best_key, best_pos = key, pos
    return best_key


def extras_text(key):
    """Блок под результатом: ссылки на ресурсы и вузы России (из texts.py)."""
    return (
        f"{texts.RESOURCES_TITLE}\n{texts.PROFESSION_LINKS[key]}\n\n"
        f"{texts.UNIVERSITIES_TITLE}\n{texts.PROFESSION_UNIVERSITIES[key]}\n\n"
        f"{texts.UNIVERSITIES_NOTE}"
    )


# ======================= РАБОТА С ИИ =======================

def build_prompt(answers, scores):
    """Собирает текстовый запрос (промпт) для нейросети:
    все вопросы теста, ответы школьника и предварительный подсчёт баллов."""
    qa_lines = []
    for i, answer in enumerate(answers):
        qa_lines.append(f"{i + 1}) {QUESTIONS[i]['text']} — {answer}")
    qa_text = "\n".join(qa_lines)

    ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    ranking_text = ", ".join(
        f"{texts.PROFESSION_TITLES[key]} — {round(p)}%" for key, p in ranking
    )
    professions = ", ".join(texts.PROFESSION_TITLES.values())

    return (
        "Ты — школьный профориентолог в сфере IT. Школьник прошёл "
        "профориентационный тест. Вопросы и его ответы:\n"
        f"{qa_text}\n\n"
        f"Предварительный подсчёт баллов показал: {ranking_text}. "
        "Учти его, но опирайся и на ответы целиком.\n"
        f"Выбери ОДНУ наиболее подходящую профессию из списка: {professions}. "
        "Ответь дружелюбно и простым языком, по пунктам. "
        "Название профессии пиши в самом начале ответа, точно как в списке. "
        "Не придумывай ссылки и названия вузов — бот добавит их сам.\n"
        "1. Твоя профессия — название.\n"
        "2. В чём суть профессии (2-3 предложения).\n"
        "3. Ключевые навыки (3-5 штук).\n"
        "4. С чего начать обучение (2-3 конкретных шага для школьника).\n"
        "Не больше 1200 символов."
    )


def ask_gigachat(prompt):
    """Запрос к GigaChat (Сбер). Возвращает текст ответа или None при ошибке.
    Библиотека gigachat сама получает временный токен по ключу авторизации."""
    if not GIGACHAT_AUTH_KEY:
        return None
    try:
        # Импорт внутри функции: если библиотека не установлена,
        # бот не упадёт, а просто перейдёт к запасному варианту.
        from gigachat import GigaChat
        from gigachat.models import Chat, Messages, MessagesRole

        # verify_ssl_certs=False — упрощение для хакатона: у GigaChat
        # сертификат Минцифры, и на многих компьютерах он не установлен.
        with GigaChat(
            credentials=GIGACHAT_AUTH_KEY,
            scope="GIGACHAT_API_PERS",   # доступ для физлиц
            model=GIGACHAT_MODEL,
            verify_ssl_certs=False,
        ) as giga:
            response = giga.chat(Chat(
                model=GIGACHAT_MODEL,
                messages=[Messages(role=MessagesRole.USER, content=prompt)],
                temperature=0.6,
                max_tokens=800,
            ))
            return response.choices[0].message.content
    except Exception as e:
        print("Ошибка GigaChat:", e)
        return None


def ask_openai_compatible(prompt):
    """Запрос к любому OpenAI-совместимому API (Groq, OpenRouter и др.).
    Возвращает текст ответа или None при ошибке."""
    if not (LLM_API_KEY and LLM_BASE_URL and LLM_MODEL):
        return None
    try:
        response = requests.post(
            LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.6,
                "max_tokens": 800,
            },
            timeout=30,
        )
        response.raise_for_status()  # ошибка, если сервер ответил не 200
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка LLM API:", e)
        return None


def ask_ai(prompt):
    """Пробует ИИ по очереди: сначала GigaChat, потом OpenAI-совместимый API.
    Если не ответил никто — вернёт None."""
    return ask_gigachat(prompt) or ask_openai_compatible(prompt)


# ======================= ЛОГИКА ТЕСТА =======================

def ask_question(vk, user_id):
    """Задаёт пользователю вопрос, соответствующий его текущему шагу."""
    step = user_states[user_id]["step"]
    q = QUESTIONS[step]
    # Номер вопроса добавляем автоматически: «Вопрос 3 из 10»
    text = f"Вопрос {step + 1} из {TOTAL}.\n{q['text']}"
    send(vk, user_id, text, answers_keyboard(q["options"], show_back=step > 0))


def finish_test(vk, user_id):
    """Тест пройден: считаем баллы, спрашиваем ИИ и отправляем результат."""
    answers = user_states[user_id]["answers"]
    send(vk, user_id, texts.ANALYZING)

    scores = compute_scores(answers)
    best = max(scores, key=scores.get)  # профессия с наибольшим числом баллов

    # Сначала пробуем ИИ, если не вышло — берём готовое описание из texts.py
    ai_text = ask_ai(build_prompt(answers, scores))
    if ai_text:
        recommendation = ai_text
        shown = detect_profession(ai_text, default=best)  # что назвал ИИ
    else:
        recommendation = texts.PROFESSIONS[best]
        shown = best

    send(vk, user_id, texts.RESULT_INTRO)
    send(vk, user_id, recommendation)
    # Сразу под результатом — ссылки на ресурсы и вузы именно этой профессии
    send(vk, user_id, extras_text(shown), no_preview=True)
    send(vk, user_id, scores_text(scores))
    send(vk, user_id, texts.RESTART, menu_keyboard())

    record_stat("completed", shown)
    del user_states[user_id]  # очищаем состояние


def handle_test_message(vk, user_id, text):
    """Сообщение от человека, который сейчас проходит тест."""
    state = user_states[user_id]

    # Кнопка «Отмена» — выходим из теста
    if text.lower() == BTN_CANCEL.lower():
        del user_states[user_id]
        send(vk, user_id, texts.CANCELLED, menu_keyboard())
        return

    # Кнопка «Назад» — возвращаемся к прошлому вопросу
    if text.lower() == BTN_BACK.lower():
        if state["step"] == 0:
            send(vk, user_id, texts.CANT_GO_BACK)
        else:
            state["answers"].pop()   # забываем последний ответ
            state["step"] -= 1
        ask_question(vk, user_id)
        return

    # Иначе это должен быть один из вариантов ответа
    labels = [label for label, _ in QUESTIONS[state["step"]]["options"]]
    if text not in labels:
        send(vk, user_id, texts.USE_BUTTONS)
        ask_question(vk, user_id)
        return

    # Сохраняем ответ и переходим дальше
    state["answers"].append(text)
    state["step"] += 1

    if state["step"] < TOTAL:
        ask_question(vk, user_id)
    else:
        finish_test(vk, user_id)


def handle_message(vk, user_id, text):
    """Обрабатывает одно входящее сообщение."""
    text = text.strip()
    lowered = text.lower()

    # «Начать» / «Старт» работают всегда (даже посреди теста — начнём заново)
    if lowered in ("начать", "старт", "/start"):
        user_states[user_id] = {"step": 0, "answers": []}
        record_stat("started")
        send(vk, user_id, texts.WELCOME)
        ask_question(vk, user_id)
        return

    # Человек проходит тест — все остальные сообщения разбирает тест
    if user_id in user_states:
        handle_test_message(vk, user_id, text)
        return

    # ---- Дальше: человек вне теста, это команды меню ----

    if lowered in ("помощь", "/help"):
        send(vk, user_id, texts.HELP, menu_keyboard())

    elif lowered in ("меню", "/menu"):
        send(vk, user_id, texts.MENU_PROMPT, menu_keyboard())

    elif lowered == "профессии":
        send(vk, user_id, texts.CATALOG_INTRO, professions_keyboard())

    elif text in TITLE_TO_KEY:  # нажата кнопка с названием профессии
        key = TITLE_TO_KEY[text]
        send(vk, user_id, texts.PROFESSIONS[key])
        send(vk, user_id, extras_text(key), professions_keyboard(), no_preview=True)

    elif lowered == "ресурсы":
        send(vk, user_id, texts.RESOURCES, menu_keyboard())

    elif lowered in ("/stats", "статистика") and user_id in ADMIN_IDS:
        send(vk, user_id, stats_text())

    else:
        send(vk, user_id, texts.ASK_TO_START, menu_keyboard())


# ======================= ЗАПУСК БОТА =======================

def main():
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    print("Бот запущен! Нажми Ctrl+C для остановки.")

    # Бесконечно слушаем новые события
    while True:
        try:
            for event in longpoll.listen():
                # Нас интересуют только новые входящие текстовые сообщения
                if event.type == VkEventType.MESSAGE_NEW and event.to_me and event.text:
                    handle_message(vk, event.user_id, event.text)
        except Exception as e:
            # Если пропало соединение — не падаем, а продолжаем слушать
            print("Ошибка, перезапускаю прослушивание:", e)


if __name__ == "__main__":
    main()
