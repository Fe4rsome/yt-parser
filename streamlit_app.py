import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import time

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube AI Analyst", page_icon="📊", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ФУНКЦИИ СВЯЗИ ---

def send_telegram_message(text):
    """Отправляет простое текстовое сообщение"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'})
    except: pass

def check_gemini_health():
    """Тихая проверка здоровья API при запуске"""
    # Список моделей по приоритету (Сначала новые и быстрые)
    models_to_check = ['gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash']
    
    for model in models_to_check:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            # Отправляем "Ping"
            response = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": "Ping"}]}]}, 
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                return True, model, None # Все супер
            elif response.status_code == 429:
                continue # Лимит исчерпан, пробуем следующую
            else:
                continue # Другая ошибка, пробуем следующую
                
        except Exception as e:
            continue
            
    # Если цикл закончился и ничего не нашли
    error_msg = "Все модели недоступны (возможно, проблемы с квотами или ключом)."
    return False, None, error_msg

# --- ЛОГИКА ПРИ ЗАПУСКЕ ---
# Проверяем статус один раз при загрузке страницы
if 'api_status' not in st.session_state:
    is_ok, model_name, error = check_gemini_health()
    st.session_state['api_status'] = is_ok
    st.session_state['active_model'] = model_name
    
    if not is_ok:
        # ОТПРАВЛЯЕМ ОТЧЕТ ОБ ОШИБКЕ В ТЕЛЕГРАМ
        send_telegram_message(f"🚨 **ALARM:** Ваш парсер сломался!\nПричина: {error}\nПроверьте Google AI Studio.")

# --- UI ЗАГОЛОВОК С ИНДИКАТОРОМ ---
col1, col2 = st.columns([0.8, 0.2])
with col1:
    st.title("YouTube Analyst")
with col2:
    if st.session_state['api_status']:
        st.markdown(f"### 🟢 API\n`{st.session_state['active_model']}`")
    else:
        st.markdown("### 🔴 Offline")

# --- ФУНКЦИЯ АНАЛИЗА ---
def get_ai_summary(comments_list):
    if not st.session_state['api_status']:
        return "⚠️ Анализ недоступен (нет связи с AI)."

    # Лимитируем текст, чтобы не перегружать модель
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    prompt = f"""
    Проанализируй комментарии YouTube. Кратко и четко:
    1. Эмоциональный фон.
    2. Главные темы.
    3. Хвалят.
    4. Ругают.
    
    Текст: {text_corpus}
    """
    
    model = st.session_state['active_model']
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
    
    try:
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"Ошибка при генерации: {response.status_code}"
    except Exception as e:
        return f"Сбой: {e}"

# --- ФУНКЦИЯ ОТПРАВКИ ФАЙЛА ---
def send_results_to_telegram(file_data, file_name, ai_text):
    # 1. Файл
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
        data={'chat_id': TG_CHAT_ID, 'caption': f"📂 {file_name}"}, 
        files={'document': (file_name, file_data)}
    )
    # 2. Текст
    if len(ai_text) > 4000: ai_text = ai_text[:4000]
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", 
        json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'}
    )

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
                    top = item['snippet']['topLevelComment']['snippet']
                    all_data.append({'Автор': top['authorDisplayName'], 'Текст': top['textDisplay']})
                req = youtube.commentThreads().list_next(req, resp)
        except: pass
    return all_data, file_name

# --- ОСНОВНОЙ ИНТЕРФЕЙС ---
raw_urls = st.text_area("Ссылка на видео:", height=100)

if st.button("Запуск", type="primary", disabled=(not st.session_state['api_status'])):
    if not raw_urls:
        st.warning("Нет ссылки")
    else:
        with st.spinner('Работаем...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            summary = get_ai_summary(data)
            
            # Показываем результат красиво
            st.success("Готово!")
            st.markdown(summary)
            
            # Excel
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            send_results_to_telegram(buffer.getvalue(), fname, summary)
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
