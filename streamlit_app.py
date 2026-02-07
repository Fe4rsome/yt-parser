import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import io
import re
import requests
import time

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTubeComm", page_icon="📡", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- СЕССИЯ ---
if 'processed' not in st.session_state: st.session_state['processed'] = False
if 'ai_verdict' not in st.session_state: st.session_state['ai_verdict'] = None
if 'quota_used' not in st.session_state: st.session_state['quota_used'] = 0

# --- ТЕЛЕГРАМ ---
def send_to_telegram(file_data, file_name, ai_text=None, quota_info=""):
    try:
        # 1. Файл
        caption = f"📂 {file_name}\nℹ️ {quota_info}"
        if ai_text: caption += "\n\n(⬇️ Анализ ниже)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
        # 2. Текст
        if ai_text:
            url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            if len(ai_text) > 3000:
                chunks = [ai_text[i:i+3000] for i in range(0, len(ai_text), 3000)]
                for chunk in chunks:
                    requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': chunk})
            else:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
        return True
    except: return False

# --- ТРАНСКРИПЦИЯ ---
def get_video_transcript(video_id):
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ru', 'en'])
        return " ".join([t['text'] for t in transcript_list])
    except: return None

# --- AI АНАЛИЗАТОР (Сравнение видео и комментов) ---
def get_ai_verdict(title, transcript, comments_list, is_deep_scan):
    if not comments_list: return "Нет комментариев."
    
    # Если Deep Scan включен, даем AI больше данных (300 комментов вместо 100)
    limit = 300 if is_deep_scan else 100
    transcript_limit = 20000 if is_deep_scan else 10000
    
    # Формируем текст видео
    transcript_text = transcript[:transcript_limit] if transcript else "Субтитры недоступны."
    
    # Формируем мнение народа
    audience_voice = "\n".join([f"- {str(c['Текст'])[:300]}" for c in comments_list[:limit]])
    
    prompt = f"""
    Роль: Ты критический аналитик YouTube. 
    Задача: Сравни содержание видео (слова автора) с реакцией зрителей.
    
    1. ИНФОРМАЦИЯ О ВИДЕО:
    Название: {title}
    Слова автора (транскрипция): {transcript_text}...
    
    2. КОММЕНТАРИИ ЗРИТЕЛЕЙ (Анализируем топ-{limit}):
    {audience_voice}
    
    СОСТАВЬ ОТЧЕТ (Markdown):
    1. 🎯 **ВЕРДИКТ:** (Стоит смотреть? Оценка 0-10).
    2. ⚖️ **ДЕТЕКТОР ПРАВДЫ:** Подтверждают ли зрители слова автора? Есть ли опровержения в комментариях?
    3. 🔥 **ГЛАВНЫЕ СПОРЫ:** О чем самая жаркая дискуссия (особенно в ответах).
    4. 🧠 **ВЫВОД:** Краткое резюме.
    """
    
    models = ['gemini-2.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash', 'gemini-1.5-flash']
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            response = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": prompt}]}]}, 
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
        except: continue
    return "⚠️ AI не ответил."

# --- ФУНКЦИЯ СБОРА ОТВЕТОВ (РЕКУРСИВНАЯ) ---
def get_replies_recursive(youtube, parent_id, progress_callback):
    replies = []
    cost = 0
    try:
        req = youtube.comments().list(parentId=parent_id, part="snippet", maxResults=100)
        while req:
            resp = req.execute()
            cost += 1 # +1 квота за страницу ответов
            
            for item in resp['items']:
                replies.append({
                    'Автор': item['snippet']['authorDisplayName'],
                    'Текст': item['snippet']['textDisplay'],
                    'Тип': 'Ответ',
                    'Лайки': item['snippet']['likeCount']
                })
            
            progress_callback(len(replies))
            
            if 'nextPageToken' in resp:
                req = youtube.comments().list_next(req, resp)
            else: break
    except: pass
    return replies, cost

# --- ОСНОВНОЙ ПАРСЕР С СЧЕТЧИКОМ ---
def process_full_data(api_key, url, use_deep_scan):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "report.xlsx"
    total_cost = 0 # СЧЕТЧИК КВОТЫ
    
    # Элементы UI
    status_text = st.empty()
    bar = st.progress(0)

    if "v=" in url: v_id = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: v_id = url.split("youtu.be/")[1].split("?")[0]
    else: return [], "Bad Link", "", None, 0

    try:
        # 1. Инфо о видео (+1 квота)
        vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
        total_cost += 1
        
        if vid_req['items']:
            title = vid_req['items'][0]['snippet']['title']
            file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
        else: return [], "Video not found", "", None, 1
        
        # 2. Транскрипция (0 квоты)
        transcript = get_video_transcript(v_id)
        
        # 3. Комментарии
        req = youtube.commentThreads().list(part="snippet,replies", videoId=v_id, maxResults=100)
        
        fetched_count = 0
        while req:
            resp = req.execute()
            total_cost += 1 # +1 квота за страницу тредов
            
            for item in resp['items']:
                # Главный коммент
                top = item['snippet']['topLevelComment']['snippet']
                all_data.append({
                    'Автор': top['authorDisplayName'], 
                    'Текст': top['textDisplay'],
                    'Тип': 'Комментарий',
                    'Лайки': top['likeCount']
                })
                fetched_count += 1
                
                # Ответы
                reply_count = item['snippet']['totalReplyCount']
                if reply_count > 0:
                    if use_deep_scan:
                        # РЕЖИМ ПЫЛЕСОСА (Отдельные запросы)
                        status_text.text(f"🔥 Deep Scan: Качаем ветку ({reply_count} ответов)... Квота: {total_cost}")
                        replies, r_cost = get_replies_recursive(youtube, item['id'], lambda x: None)
                        all_data.extend(replies)
                        total_cost += r_cost
                        fetched_count += len(replies)
                    else:
                        # ЭКОНОМ (Только то, что прилипло)
                        if 'replies' in item:
                            for r in item['replies']['comments']:
                                all_data.append({
                                    'Автор': r['snippet']['authorDisplayName'], 
                                    'Текст': r['snippet']['textDisplay'],
                                    'Тип': 'Ответ',
                                    'Лайки': r['snippet']['likeCount']
                                })
                                fetched_count += 1
            
            bar.progress(min(fetched_count % 100, 100), text=f"Собрано: {fetched_count} | Потрачено квоты: {total_cost}")
            
            if 'nextPageToken' in resp:
                req = youtube.commentThreads().list_next(req, resp)
            else: break
            
    except Exception as e:
        return [], str(e), "", None, total_cost
    
    bar.empty()
    status_text.empty()
    return all_data, file_name, title, transcript, total_cost

# --- ИНТЕРФЕЙС ---
st.markdown("<h3 style='text-align: center;'>YouTubeComm</h3>", unsafe_allow_html=True)

raw_url = st.text_input("", placeholder="Ссылка на видео...")

# ПАНЕЛЬ УПРАВЛЕНИЯ
with st.container(border=True):
    c1, c2 = st.columns(2)
    with c1:
        use_ai = st.toggle("🤖 Включить AI", value=False)
    with c2:
        deep_scan = st.toggle("☢️ Deep Scan (Все ответы)", value=False, help="Качает все ветки. Тратит много квоты!")

# КНОПКА И СЧЕТЧИК
btn_col, info_col = st.columns([1, 1])

with btn_col:
    start_btn = st.button("Начать работу", type="primary", use_container_width=True)

with info_col:
    # Отображение последней траты квоты
    if st.session_state['quota_used'] > 0:
        st.caption(f"📉 Потрачено квоты за раз: **{st.session_state['quota_used']}**")
        st.caption(f"Остаток (примерно): **{10000 - st.session_state['quota_used']}/10000**")

if start_btn:
    if not raw_url:
        st.warning("Нет ссылки!")
    else:
        st.session_state['ai_verdict'] = None
        st.session_state['processed'] = False
        
        # 1. ЗАПУСК ПАРСИНГА
        with st.spinner('Парсинг данных...'):
            data, fname, title, transcript, cost = process_full_data(API_KEY, raw_url, deep_scan)
            st.session_state['quota_used'] = cost # Сохраняем расход
        
        if data:
            # 2. EXCEL
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # 3. AI АНАЛИЗ (Если включен)
            ai_text = None
            if use_ai:
                with st.spinner('AI анализирует видео и комментарии...'):
                    ai_text = get_ai_verdict(title, transcript, data, deep_scan)
                    st.session_state['ai_verdict'] = ai_text
            
            # 4. ОТПРАВКА
            quota_msg = f"Потрачено квоты: {cost}"
            sent = send_to_telegram(buffer.getvalue(), fname, ai_text, quota_msg)
            
            if sent:
                st.success("✅ Файл в Telegram!")
            st.session_state['processed'] = True
            st.rerun() # Обновляем, чтобы показать результат
        else:
            st.error(f"Ошибка: {fname}")

# РЕЗУЛЬТАТ (Остается на экране)
if st.session_state['processed'] and st.session_state['ai_verdict']:
    st.divider()
    st.markdown(st.session_state['ai_verdict'])
