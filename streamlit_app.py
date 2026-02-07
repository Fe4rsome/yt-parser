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

# --- ПАМЯТЬ (ЧТОБЫ НЕ ПРОПАДАЛО НА ТЕЛЕФОНЕ) ---
if 'processed' not in st.session_state: st.session_state['processed'] = False
if 'excel_data' not in st.session_state: st.session_state['excel_data'] = None
if 'file_name' not in st.session_state: st.session_state['file_name'] = ""
if 'ai_text' not in st.session_state: st.session_state['ai_text'] = None

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
    
    # Берем первые 80 комментариев для анализа
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    prompt = f"""
    Проанализируй комментарии YouTube.
    Напиши отчет на русском языке. Markdown.
    1. 🎭 Настроение
    2. 🔥 Темы споров
    3. 👍 Позитив
    4. 👎 Негатив
    5. 🧠 Вывод
    
    Текст: {text_corpus}
    """
    
    models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash']
    
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
        except: continue
            
    return "⚠️ Не удалось подключить AI.", None

# --- ПАРСИНГ (ИСПРАВЛЕНО: ДОБАВЛЕНЫ ОТВЕТЫ) ---
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
            
            # ВАЖНО: Добавили 'replies' в запрос
            req = youtube.commentThreads().list(part="snippet,replies", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']: 
                    # 1. Главный комментарий
                    top = item['snippet']['topLevelComment']['snippet']
                    all_data.append({
                        'Автор': top['authorDisplayName'], 
                        'Текст': top['textDisplay'],
                        'Тип': 'Комментарий'
                    })
                    
                    # 2. Ответы (Replies) - ВОТ ЧТО МЫ ПРОПУСКАЛИ
                    if 'replies' in item:
                        for reply in item['replies']['comments']:
                            r = reply['snippet']
                            all_data.append({
                                'Автор': r['authorDisplayName'], 
                                'Текст': r['textDisplay'],
                                'Тип': 'Ответ'
                            })
                            
                req = youtube.commentThreads().list_next(req, resp)
        except: pass
    return all_data, file_name

# --- ИНТЕРФЕЙС ---

# Заголовок по центру, маленький
st.markdown("<h3 style='text-align: center; margin-bottom: 10px;'>YouTubeComm</h3>", unsafe_allow_html=True)

raw_urls = st.text_area("Ссылка:", height=100)
use_ai = st.toggle("AI-анализ", value=False)

# КНОПКА ЗАПУСКА
if st.button("Начать работу", type="primary", use_container_width=True):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        st.session_state['processed'] = False # Сброс
        
        with st.spinner('Сбор всех комментариев и ответов...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            # Готовим Excel
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # Сохраняем в память
            st.session_state['excel_data'] = buffer.getvalue()
            st.session_state['file_name'] = fname
            st.session_state['ai_text'] = None
            
            # AI (если нужно)
            if use_ai:
                with st.spinner('Анализ...'):
                    summary, mod = get_ai_summary(data)
                    st.session_state['ai_text'] = summary

            st.session_state['processed'] = True # Флаг готовности
            
            # Отправка в ТГ
            send_results_to_telegram(st.session_state['excel_data'], fname, st.session_state['ai_text'])

# --- БЛОК РЕЗУЛЬТАТОВ (ПОЯВЛЯЕТСЯ ПОСЛЕ ОБРАБОТКИ) ---
if st.session_state['processed']:
    st.divider()
    
    # Показываем AI текст
    if st.session_state['ai_text']:
        if "Ошибка" in st.session_state['ai_text']:
            st.error(st.session_state['ai_text'])
        else:
            st.markdown(st.session_state['ai_text'])
    
    # КНОПКА СКАЧИВАНИЯ (ОТДЕЛЬНАЯ И БОЛЬШАЯ)
    st.success(f"Готово! Собрано записей: {len(pd.read_excel(io.BytesIO(st.session_state['excel_data'])))}")
    
    st.download_button(
        label=f"📥 СКАЧАТЬ ТАБЛИЦУ ({st.session_state['file_name']})",
        data=st.session_state['excel_data'],
        file_name=st.session_state['file_name'],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", # Делает кнопку яркой
        use_container_width=True # Растягивает на всю ширину (удобно на телефоне)
    )
