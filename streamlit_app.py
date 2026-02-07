import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTubeComm", page_icon="📉", layout="centered")

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
        caption = f"📂 {file_name}"
        if ai_text: caption += "\n\n(Отчет AI ниже)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
        if ai_text:
            url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            if len(ai_text) > 4000:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[:4000], 'parse_mode': 'Markdown'})
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[4000:], 'parse_mode': 'Markdown'})
            else:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
    except: pass

# --- AI АНАЛИЗ ---
def get_ai_summary(comments_list):
    if not comments_list: return "Нет данных.", None
    
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
    
    # Приоритет на 2.5-flash, так как она сработала
    models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash']
    
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
                return response.json()['candidates'][0]['content']['parts'][0]['text'], model
            else:
                last_error = f"{model}: {response.status_code}"
                continue
        except Exception as e:
            last_error = str(e)
            continue
            
    return f"⚠️ Ошибка AI: {last_error}", None

# --- ПАРСИНГ ---
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

# 1. ЗАГОЛОВОК (HTML для центрирования и уменьшения размера)
st.markdown("<h3 style='text-align: center;'>YouTubeComm</h3>", unsafe_allow_html=True)

# Инициализация хранилища (чтобы файл не пропадал на телефоне)
if 'excel_data' not in st.session_state:
    st.session_state['excel_data'] = None
if 'file_name' not in st.session_state:
    st.session_state['file_name'] = None

raw_urls = st.text_area("Ссылка на видео:", height=100)
use_ai = st.toggle("Подключить AI-анализ", value=False)

# 2. КНОПКИ В ОДИН РЯД (Колонки)
col1, col2 = st.columns([1, 1])

with col1:
    # Кнопка НАЧАТЬ
    if st.button("Начать работу", type="primary", use_container_width=True):
        if not raw_urls:
            st.warning("Вставьте ссылку")
        else:
            with st.spinner('Парсинг...'):
                data, fname = process_videos(API_KEY, raw_urls.split('\n'))
            
            if data:
                # Сохраняем в память сессии
                df = pd.DataFrame(data)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)
                
                st.session_state['excel_data'] = buffer.getvalue()
                st.session_state['file_name'] = fname
                
                # AI Анализ
                summary = None
                if use_ai:
                    with st.spinner('Анализ...'):
                        summary, model_used = get_ai_summary(data)
                    if model_used:
                        st.success(f"Готово! ({model_used})")
                        st.markdown(summary)
                    else:
                        st.error(summary)
                else:
                    st.info("Готово (без AI).")

                # Отправка в ТГ
                send_results_to_telegram(st.session_state['excel_data'], fname, summary)
                # Перезагружаем страницу, чтобы кнопка скачивания обновилась
                st.rerun()

with col2:
    # Кнопка СКАЧАТЬ (Появляется только если файл есть в памяти)
    if st.session_state['excel_data']:
        st.download_button(
            label="Скачать таблицу",
            data=st.session_state['excel_data'],
            file_name=st.session_state['file_name'],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary",
            use_container_width=True
        )
    else:
        # Пустая кнопка для симметрии (неактивная)
        st.button("Скачать таблицу", disabled=True, use_container_width=True)
