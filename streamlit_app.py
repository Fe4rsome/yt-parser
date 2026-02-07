import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import google.generativeai as genai

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="YouTube AI Parser", page_icon="🧠", layout="centered")

# --- ПОЛУЧЕНИЕ СЕКРЕТОВ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка в Secrets: {e}")
    st.stop()

# --- НАСТРОЙКА GEMINI (Исправленная версия v2) ---
genai.configure(api_key=GEMINI_KEY)

# Список моделей: добавим 'gemini-pro' как самый надежный вариант
model_names = ['gemini-1.5-flash', 'gemini-1.5-flash-latest', 'gemini-pro']
ai_model = None

for name in model_names:
    try:
        print(f"Пробую модель: {name}...")
        test_model = genai.GenerativeModel(name)
        # ГЛАВНОЕ ИЗМЕНЕНИЕ: Делаем реальный тестовый запрос
        test_model.generate_content("Hello")
        
        # Если ошибки не возникло — ура, модель работает!
        ai_model = test_model
        print(f"✅ Успешно выбрана модель: {name}")
        break
    except Exception as e:
        print(f"❌ Модель {name} не доступна: {e}")
        continue

if ai_model is None:
    st.error("Не удалось подключиться ни к одной модели Gemini. Проверьте API ключ или обновите библиотеку.")
    st.stop()
    
# --- ФУНКЦИИ ---

def get_ai_summary(comments_list):
    """Улучшенная генерация сводки с защитой от ошибок"""
    try:
        if not comments_list:
            return "Нет комментариев для анализа."
        
        # Берем только текст, чистим от лишних пробелов и берем первые 50 штук
        text_corpus = "\n".join([str(c['Текст'])[:300] for c in comments_list[:50]]) 
        
        prompt = f"""
        Проанализируй эти комментарии к видео и напиши кратко:
        1. Общее настроение аудитории.
        2. Основные темы (о чем говорят).
        3. Что хвалят, а что ругают.
        
        Текст комментариев:
        {text_corpus}
        """
        
        # Настройка: просим AI быть менее строгим к фильтрам безопасности
        response = ai_model.generate_content(
            prompt,
            safety_settings={
                "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
                "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
                "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
            }
        )
        
        if response.text:
            return response.text
        return "Gemini вернул пустой ответ (возможно, сработал фильтр безопасности Google)."
        
    except Exception as e:
        # Это поможет нам увидеть реальную причину в интерфейсе
        return f"Ошибка AI: {str(e)}"

def send_to_telegram(file_data, file_name, ai_text):
    """Отправка файла и сводки в Telegram"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    caption = f"📊 **AI Анализ:**\n{ai_text[:900]}" # Ограничение длины подписи
    files = {'document': (file_name, file_data)}
    try:
        requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': caption, 'parse_mode': 'Markdown'}, files=files)
        return True
    except:
        return False

def get_video_id(url):
    url = url.strip()
    if "v=" in url: return url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: return url.split("youtu.be/")[1].split("?")[0]
    return url if len(url) == 11 else None

def get_video_title(youtube, video_id):
    try:
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        return resp['items'][0]['snippet']['title']
    except:
        return f"Video_{video_id}"

def process_videos(api_key, urls):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    logs = []
    file_name = "comments.xlsx"
    
    for i, url in enumerate(urls):
        v_id = get_video_id(url)
        if not v_id: continue
        if i == 0:
            title = get_video_title(youtube, v_id)
            file_name = f"{re.sub(r'[\\/*? Glad:<>|]', '', title)[:50]}.xlsx"
        
        try:
            req = youtube.commentThreads().list(part="snippet,replies", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']:
                    top = item['snippet']['topLevelComment']['snippet']
                    all_data.append({'Автор': top['authorDisplayName'], 'Текст': top['textDisplay'], 'Дата': top['publishedAt']})
                req = youtube.commentThreads().list_next(req, resp)
        except Exception as e:
            logs.append(f"Ошибка: {e}")
    return all_data, logs, file_name

# --- ИНТЕРФЕЙС ---
st.title("YouTube AI Parser 🚀")
raw_urls = st.text_area("Вставьте ссылки (каждая с новой строки)", height=150)

if st.button("Начать сбор и AI-анализ", type="primary"):
    if not raw_urls.strip():
        st.warning("Введите ссылки!")
    else:
        with st.spinner('Анализирую...'):
            urls = raw_urls.split('\n')
            data, logs, fname = process_videos(API_KEY, urls)
        
        if data:
            # Вывод AI сводки
            st.subheader("🤖 Сводка от Gemini AI")
            ai_summary = get_ai_summary(data)
            st.info(ai_summary)
            
            # Подготовка файла
            df = pd.DataFrame(data)
            df['Текст'] = df['Текст'].astype(str).str.replace(r'<[^>]*>', ' ', regex=True)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # Отправка в ТГ и кнопка
            send_to_telegram(buffer.getvalue(), fname, ai_summary)
            st.success("Данные собраны и отправлены в Telegram!")
            st.download_button(f"📥 Скачать {fname}", buffer.getvalue(), fname)




