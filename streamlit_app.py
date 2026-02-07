import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube Parser", page_icon="🚀", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ТЕЛЕГРАМ ---
def send_results_to_telegram(file_data, file_name, ai_text=None):
    try:
        # 1. Документ
        caption = f"📂 {file_name}"
        if ai_text: caption += "\n\n(Отчет AI ниже)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
        # 2. Текст (если есть)
        if ai_text:
            url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            # Разбиваем, если длинный
            if len(ai_text) > 4000:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[:4000], 'parse_mode': 'Markdown'})
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[4000:], 'parse_mode': 'Markdown'})
            else:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
    except: pass

# --- AI АНАЛИЗ (ИСПРАВЛЕННЫЙ) ---
def get_ai_summary(comments_list):
    if not comments_list: return "Нет данных."
    
    # Берем первые 80 комментариев
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    prompt = f"""
    Проанализируй комментарии YouTube.
    Напиши отчет на русском:
    1. 🎭 Настроение.
    2. 🔥 Темы споров.
    3. 👍 Позитив.
    4. 👎 Негатив.
    5. 🧠 Вывод.
    
    Текст: {text_corpus}
    """
    
    # ПРИОРИТЕТНЫЙ СПИСОК (Сначала ставим ту, что сработала в тесте!)
    models = [
        'gemini-2.5-flash', # ПОБЕДИТЕЛЬ ТЕСТА
        'gemini-2.0-flash',
        'gemini-1.5-pro',
        'gemini-1.5-flash'
    ]
    
    last_error = ""
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            response = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": prompt}]}]}, 
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                # Успех! Возвращаем текст и имя модели
                return response.json()['candidates'][0]['content']['parts'][0]['text'], model
            else:
                last_error = f"{model}: {response.status_code}"
                continue # Пробуем следующую
        except Exception as e:
            last_error = str(e)
            continue
            
    return f"⚠️ Не удалось. Последняя ошибка: {last_error}", None

# --- ПАРСИНГ YOUTUBE ---
def process_videos(api_key, urls):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "comments.xlsx"
    for i, url in enumerate(urls):
        if "v=" in url: v_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url: v_id = url.split("youtu.be/")[1].split("?")[0]
        else: continue
        try:
            vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
            if vid_req['items']: 
                title = vid_req['items'][0]['snippet']['title']
                file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
            
            req = youtube.commentThreads().list(part="snippet", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']: 
                    all_data.append({
                        'Автор': item['snippet']['topLevelComment']['snippet']['authorDisplayName'], 
                        'Текст': item['snippet']['topLevelComment']['snippet']['textDisplay']
                    })
                req = youtube.commentThreads().list_next(req, resp)
        except: pass
    return all_data, file_name

# --- ИНТЕРФЕЙС ---
st.title("YouTube Parser 🚀")

# 1. Ссылка
raw_urls = st.text_area("Ссылка на видео:", height=100)

# 2. Переключатель AI (Рубильник)
use_ai = st.toggle("Подключить AI-анализ (Gemini)", value=False)

# 3. Кнопка
if st.button("Начать работу", type="primary"):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        # ЭТАП 1: Сбор
        with st.spinner('Парсинг...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            summary = None
            
            # ЭТАП 2: AI (только если включен)
            if use_ai:
                with st.spinner('Gemini думает...'):
                    summary, model_used = get_ai_summary(data)
                
                if model_used:
                    st.success(f"Готово! (Модель: {model_used})")
                    st.markdown(summary)
                else:
                    st.error(summary)
            
            # ЭТАП 3: Сохранение и отправка
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            send_results_to_telegram(buffer.getvalue(), fname, summary)
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
            
            if not use_ai:
                st.info("✅ Excel отправлен в Telegram (без анализа).")
