import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube Truth Detector", page_icon="⚖️", layout="centered")

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
if 'status' not in st.session_state: st.session_state['status'] = ""
if 'ai_verdict' not in st.session_state: st.session_state['ai_verdict'] = None

# --- ТЕЛЕГРАМ ---
def send_to_telegram(file_data, file_name, ai_text=None):
    try:
        # Файл
        caption = f"📂 {file_name}"
        if ai_text: caption += "\n\n(⬇️ ВЕРДИКТ НИЖЕ)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
        # Текст (Разбиваем, если длинный)
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

# --- ФУНКЦИЯ: ЧИТАЕМ СУБТИТРЫ (ТРАНСКРИПЦИЯ) ---
def get_video_transcript(video_id):
    try:
        # Пытаемся получить русские или английские субтитры
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ru', 'en'])
        
        # Собираем текст в одну строку
        full_text = " ".join([t['text'] for t in transcript_list])
        return full_text
    except:
        return None # Если субтитров нет или они отключены

# --- AI СУДЬЯ (ТЕПЕРЬ СРАВНИВАЕТ СЛОВА АВТОРА И НАРОДА) ---
def get_ai_verdict(title, transcript, comments_list):
    if not comments_list: return "Нет комментариев."
    
    # Подготовка данных
    # Обрезаем транскрипцию, если она слишком огромная (до 15 000 символов), чтобы влезло в контекст
    transcript_text = transcript[:15000] if transcript else "Субтитры недоступны (анализируем только заголовок)."
    
    # Берем топ-100 комментариев
    audience_voice = "\n".join([f"- {str(c['Текст'])[:200]}" for c in comments_list[:100]])
    
    prompt = f"""
    Ты — безжалостный детектор лжи и кликбейта. Твоя цель — понять, стоит ли тратить время на это видео.
    
    1. ВОТ ЧТО ГОВОРИТ АВТОР ВИДЕО (ТРАНСКРИПЦИЯ):
    Заголовок: {title}
    Слова из видео: {transcript_text}...
    
    2. ВОТ ЧТО ГОВОРЯТ ЗРИТЕЛИ (КОММЕНТАРИИ):
    {audience_voice}
    
    ЗАДАЧА:
    Сравни слова автора и реакцию людей. Найди несостыковки.
    
    Напиши отчет (Markdown):
    1. 🎯 **ВЕРДИКТ:** (Смотреть / Не смотреть / Кликбейт). Оценка пользы 0-10.
    2. ⚖️ **ДЕТЕКТОР ЛЖИ:** - Автор утверждает: "..."
       - А люди говорят: "..." (есть ли обман?)
    3. 🔥 **СУТЬ (О чем видео на самом деле):** Краткий пересказ слов автора в 2 предложениях.
    4. 👎 **КРИТИКА:** Главные претензии толпы.
    """
    
    # Используем модели по очереди
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
            
    return "⚠️ AI не справился с анализом."

# --- ПАРСИНГ ---
def get_full_data(api_key, url):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "report.xlsx"
    title = ""
    transcript = None
    
    if "v=" in url: v_id = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: v_id = url.split("youtu.be/")[1].split("?")[0]
    else: return [], "", "", None

    try:
        # 1. Инфо о видео
        vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
        if vid_req['items']:
            title = vid_req['items'][0]['snippet']['title']
            file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
        
        # 2. Скачиваем СУБТИТРЫ (Слова автора)
        transcript = get_video_transcript(v_id)
        
        # 3. Скачиваем КОММЕНТАРИИ (Глас народа)
        # Берем 100 штук для быстрого анализа
        req = youtube.commentThreads().list(part="snippet", videoId=v_id, maxResults=100)
        while req:
            resp = req.execute()
            for item in resp['items']:
                top = item['snippet']['topLevelComment']['snippet']
                all_data.append({
                    'Автор': top['authorDisplayName'], 
                    'Текст': top['textDisplay'],
                    'Лайки': top['likeCount']
                })
            # Ограничимся 200 комментами для скорости в этом режиме
            if len(all_data) >= 200: break
            if 'nextPageToken' in resp:
                req = youtube.commentThreads().list_next(req, resp)
            else: break
                
    except Exception as e:
        return [], f"Ошибка: {e}", "", None
        
    return all_data, file_name, title, transcript

# --- ИНТЕРФЕЙС ---
st.markdown("<h3 style='text-align: center;'>YouTube Truth Detector ⚖️</h3>", unsafe_allow_html=True)

raw_url = st.text_input("", placeholder="Ссылка на видео...")

# КНОПКА ЗАПУСКА
col1, col2 = st.columns([1, 2])
with col1:
    btn = st.button("Проверить видео", type="primary", use_container_width=True)
with col2:
    status_box = st.empty()

if btn:
    if not raw_url:
        st.warning("Вставьте ссылку!")
    else:
        status_box.info("🕵️ Скачиваю слова автора и комментарии...")
        st.session_state['ai_verdict'] = None
        
        # Сбор данных
        comments, fname, title, transcript = get_full_data(API_KEY, raw_url)
        
        if comments:
            # Excel
            df = pd.DataFrame(comments)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # AI Анализ
            status_box.info("🧠 AI ищет ложь и несостыковки...")
            verdict = get_ai_verdict(title, transcript, comments)
            st.session_state['ai_verdict'] = verdict
            
            # Отправка
            sent = send_to_telegram(buffer.getvalue(), fname, verdict)
            if sent:
                status_box.markdown("✅ **Отчет в Telegram!**")
            else:
                status_box.error("Ошибка отправки в TG")
        else:
            status_box.error("Не удалось прочитать данные.")

# ВЫВОД РЕЗУЛЬТАТА
if st.session_state['ai_verdict']:
    st.divider()
    st.markdown(st.session_state['ai_verdict'])
