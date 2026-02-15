import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import io
import re
import requests

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
        caption = f"📂 {file_name}\nℹ️ {quota_info}"
        if ai_text: caption += "\n\n(⬇️ Анализ ниже)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
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

# --- AI АНАЛИЗАТОР (СТРОГИЙ РЕЖИМ) ---
def get_ai_verdict(title, transcript, comments_list, is_deep_scan):
    if not comments_list: return "Нет комментариев."
    
    limit = 300 if is_deep_scan else 100
    transcript_limit = 20000 if is_deep_scan else 10000
    transcript_text = transcript[:transcript_limit] if transcript else "Субтитры недоступны."
    audience_voice = "\n".join([f"- {str(c['Текст'])[:300]}" for c in comments_list[:limit]])
    
    prompt = f"""
    Роль: Ты строгий, беспристрастный аналитик данных. Твоя задача — дать объективную оценку видео.
    
    1. ИНФОРМАЦИЯ О ВИДЕО:
    Название: {title}
    Слова автора: {transcript_text}...
    
    2. КОММЕНТАРИИ ЗРИТЕЛЕЙ (Выборка {limit} шт):
    {audience_voice}
    
    ИНСТРУКЦИЯ:
    Проанализируй тональность. Игнорируй единичные всплески эмоций, ищи общий тренд.
    
    ОТЧЕТ (Markdown):
    1. 🎯 ВЕРДИКТ (Оценка 0-10, где 0 - мусор/обман, 10 - шедевр/польза). Будь строг.
    2. ⚖️ ДЕТЕКТОР ПРАВДЫ (Ложь vs Истина).
    3. 🔥 ГЛАВНЫЕ СПОРЫ.
    4. 🧠 ВЫВОД.
    """
    
    # Сначала пробуем самую умную (Pro), потом быстрые
    models = ['gemini-1.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash']
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            # ДОБАВЛЕН КОНФИГ ТЕМПЕРАТУРЫ = 0
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "topP": 0.8,
                    "topK": 40
                }
            }
            
            response = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
            
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
        except: continue
    return "⚠️ AI не ответил."

# --- СБОР ОТВЕТОВ ---
def get_replies_recursive(youtube, parent_id, progress_callback):
    replies = []
    cost = 0
    try:
        req = youtube.comments().list(parentId=parent_id, part="snippet", maxResults=100)
        while req:
            resp = req.execute()
            cost += 1 
            for item in resp['items']:
                replies.append({
                    'Автор': item['snippet']['authorDisplayName'],
                    'Текст': item['snippet']['textDisplay'],
                    'Тип': 'Ответ',
                    'Лайки': item['snippet']['likeCount']
                })
            progress_callback(len(replies))
            if 'nextPageToken' in resp: req = youtube.comments().list_next(req, resp)
            else: break
    except: pass
    return replies, cost

# --- ПАРСЕР ---
def process_full_data(api_key, url, use_deep_scan):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "report.xlsx"
    total_cost = 0 
    
    status_text = st.empty()
    bar = st.progress(0)

    if "v=" in url: v_id = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: v_id = url.split("youtu.be/")[1].split("?")[0]
    else: return [], "Bad Link", "", None, 0

    try:
        vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
        total_cost += 1
        if vid_req['items']:
            title = vid_req['items'][0]['snippet']['title']
            file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
        else: return [], "Video not found", "", None, 1
        
        transcript = get_video_transcript(v_id)
        
        req = youtube.commentThreads().list(part="snippet,replies", videoId=v_id, maxResults=100)
        fetched_count = 0
        while req:
            resp = req.execute()
            total_cost += 1 
            for item in resp['items']:
                top = item['snippet']['topLevelComment']['snippet']
                all_data.append({'Автор': top['authorDisplayName'], 'Текст': top['textDisplay'], 'Тип': 'Комментарий', 'Лайки': top['likeCount']})
                fetched_count += 1
                
                if item['snippet']['totalReplyCount'] > 0:
                    if use_deep_scan:
                        status_text.text(f"🔥 Deep Scan... Квота: {total_cost}")
                        replies, r_cost = get_replies_recursive(youtube, item['id'], lambda x: None)
                        all_data.extend(replies)
                        total_cost += r_cost
                        fetched_count += len(replies)
                    elif 'replies' in item:
                        for r in item['replies']['comments']:
                            all_data.append({'Автор': r['snippet']['authorDisplayName'], 'Текст': r['snippet']['textDisplay'], 'Тип': 'Ответ', 'Лайки': r['snippet']['likeCount']})
                            fetched_count += 1
            
            bar.progress(min(fetched_count % 100, 100), text=f"Собрано: {fetched_count} | Квота: {total_cost}")
            if 'nextPageToken' in resp: req = youtube.commentThreads().list_next(req, resp)
            else: break
            
    except Exception as e: return [], str(e), "", None, total_cost
    
    bar.empty()
    status_text.empty()
    return all_data, file_name, title, transcript, total_cost

# --- ИНТЕРФЕЙС ---
st.markdown("<h3 style='text-align: center;'>YouTubeComm</h3>", unsafe_allow_html=True)

# ССЫЛКА НА КОНСОЛЬ (КНОПКА ВВЕРХУ)
st.link_button("📊 Проверить остаток квоты в Google Console", "https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas")

raw_url = st.text_input("", placeholder="Ссылка на видео...")

# НАСТРОЙКИ
with st.container(border=True):
    c1, c2 = st.columns(2)
    with c1: use_ai = st.toggle("🤖 Включить AI", value=False)
    with c2: deep_scan = st.toggle("☢️ Deep Scan", value=False, help="Качает все ответы.")

# КНОПКА И ИНФО
btn_col, info_col = st.columns([1.2, 0.8])
with btn_col:
    start_btn = st.button("Начать работу", type="primary", use_container_width=True)
with info_col:
    # ИНДИКАТОР ПОТРАЧЕННОГО ЗА СЕССИЮ
    if st.session_state['quota_used'] > 0:
        st.metric(label="Потрачено за раз", value=f"{st.session_state['quota_used']} ед.", delta=f"-{st.session_state['quota_used']}")
    else:
        st.caption("Лимит: 10 000 ед./день")

if start_btn:
    if not raw_url: st.warning("Нет ссылки!")
    else:
        st.session_state['ai_verdict'] = None
        st.session_state['processed'] = False
        
        with st.spinner('Парсинг...'):
            data, fname, title, transcript, cost = process_full_data(API_KEY, raw_url, deep_scan)
            st.session_state['quota_used'] = cost
        
        if data:
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False)
            
            ai_text = None
            if use_ai:
                with st.spinner('AI Анализ...'):
                    ai_text = get_ai_verdict(title, transcript, data, deep_scan)
                    st.session_state['ai_verdict'] = ai_text
            
            quota_msg = f"Потрачено квоты: {cost}"
            sent = send_to_telegram(buffer.getvalue(), fname, ai_text, quota_msg)
            
            if sent: st.success("✅ Файл в Telegram!")
            st.session_state['processed'] = True
            st.rerun()
        else: st.error(f"Ошибка: {fname}")

if st.session_state['processed'] and st.session_state['ai_verdict']:
    st.divider()
    st.markdown(st.session_state['ai_verdict'])

