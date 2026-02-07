import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import time

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube Analyst", page_icon="📉", layout="centered")

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
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'})
    except: pass

def send_results_to_telegram(file_data, file_name, ai_text):
    # 1. Файл
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': f"📂 {file_name}"}, 
            files={'document': (file_name, file_data)}
        )
    except: pass
    
    # 2. Текст (разбиваем, если длинный)
    url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        if len(ai_text) > 4000:
            requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[:4000], 'parse_mode': 'Markdown'})
            requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[4000:], 'parse_mode': 'Markdown'})
        else:
            requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
    except: pass

# --- ФУНКЦИЯ АНАЛИЗА (С ПЕРЕБОРОМ ВНУТРИ) ---
def get_ai_summary_lazy(comments_list):
    """
    Пробует модели по очереди ТОЛЬКО когда пользователь нажал кнопку.
    Это экономит квоту.
    """
    if not comments_list: return "Нет данных для анализа."

    # Берем первые 80 комментариев
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    prompt = f"""
    Проанализируй комментарии YouTube.
    Напиши отчет на русском языке:
    1. 🎭 **Настроение:** (Эмоции, сарказм).
    2. 🔥 **О чем спорят:** (Главные темы).
    3. 👍 **Позитив:** (За что хвалят).
    4. 👎 **Негатив:** (За что ругают).
    5. 🧠 **Вывод:** (Итог).
    
    Текст: {text_corpus}
    """
    
    # Список моделей: от новой к старой
    models_to_try = [
        'gemini-2.0-flash',      # Новейшая (быстрая)
        'gemini-1.5-pro',        # Умная
        'gemini-1.5-flash',      # Стандартная
        'gemini-pro'             # Старая (запасная)
    ]
    
    last_error = ""
    
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            # Пытаемся получить ответ
            response = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": prompt}]}]}, 
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                # УРА! Получилось. Возвращаем текст и имя модели для логов
                ai_text = response.json()['candidates'][0]['content']['parts'][0]['text']
                return ai_text, model
            
            elif response.status_code == 429:
                # Лимит исчерпан, молча идем к следующей
                last_error = "429 (Лимит)"
                continue
            else:
                last_error = f"{response.status_code}"
                continue
                
        except Exception as e:
            last_error = str(e)
            continue
            
    # Если цикл закончился и ничего не вышло
    error_report = f"⚠️ Не удалось подключиться ни к одной модели. Последняя ошибка: {last_error}"
    send_telegram_message(f"🚨 Ошибка AI: {error_report}") # Шлем алерт в телегу
    return error_report, None

# --- ПАРСИНГ ---
def get_video_id(url):
    if "v=" in url: return url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: return url.split("youtu.be/")[1].split("?")[0]
    return None

def process_videos(api_key, urls):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "comments.xlsx"
    
    for i, url in enumerate(urls):
        v_id = get_video_id(url)
        if not v_id: continue
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

# --- ИНТЕРФЕЙС ---
st.title("YouTube Analyst 🚀")
st.caption("Режим экономии квоты: AI подключается только при анализе.")

raw_urls = st.text_area("Ссылка на видео:", height=100)

if st.button("Запуск", type="primary"):
    if not raw_urls:
        st.warning("Нет ссылки")
    else:
        # 1. Сбор комментариев
        with st.spinner('Парсим YouTube...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            # 2. Анализ AI (только сейчас делаем запрос к Google)
            with st.spinner('Подключаем AI...'):
                summary, used_model = get_ai_summary_lazy(data)
            
            # 3. Вывод результата
            if used_model:
                st.success(f"Готово! Использована модель: `{used_model}`")
                st.markdown(summary)
            else:
                st.error(summary) # Вывод ошибки, если все модели отказали
            
            # 4. Excel
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # 5. Отправка
            send_results_to_telegram(buffer.getvalue(), fname, summary)
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
