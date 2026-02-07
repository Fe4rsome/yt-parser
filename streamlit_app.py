import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import time

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTubeComm", page_icon="☢️", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ПАМЯТЬ ---
if 'processed' not in st.session_state: st.session_state['processed'] = False
if 'excel_data' not in st.session_state: st.session_state['excel_data'] = None
if 'file_name' not in st.session_state: st.session_state['file_name'] = ""
if 'ai_text' not in st.session_state: st.session_state['ai_text'] = None

# --- ТЕЛЕГРАМ ---
def send_results_to_telegram(file_data, file_name, ai_text=None):
    try:
        # 1. Файл
        caption = f"📂 {file_name}"
        if ai_text: caption += "\n\n(Полный отчет — следующим сообщением)"
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
    except: pass

# --- AI АНАЛИЗ ---
def get_ai_summary(comments_list):
    if not comments_list: return "Нет данных.", None
    
    # Берем больше контекста (первые 120 комментов)
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:120]])
    
    prompt = f"""
    Проанализируй комментарии YouTube.
    Составь детальный отчет на русском языке:
    1. Настроение (детально)
    2. Основные ветки споров
    3. Аргументы "ЗА"
    4. Аргументы "ПРОТИВ"
    5. Вывод аналитика
    
    Текст: {text_corpus}
    """
    
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
                last_error = f"{model} ({response.status_code})"
        except Exception as e:
            last_error = str(e)
            continue
            
    return f"⚠️ Ошибка AI: {last_error}", None

# --- ФУНКЦИЯ: ПОЛУЧИТЬ ВСЕ ОТВЕТЫ (ЯДЕРНЫЙ РЕЖИМ) ---
def get_all_replies(youtube, parent_id):
    replies = []
    try:
        req = youtube.comments().list(parentId=parent_id, part="snippet", maxResults=100)
        while req:
            resp = req.execute()
            for item in resp['items']:
                replies.append({
                    'Автор': item['snippet']['authorDisplayName'],
                    'Текст': item['snippet']['textDisplay'],
                    'Тип': 'Ответ',
                    'Лайки': item['snippet']['likeCount']
                })
            # Проверяем, есть ли следующая страница ответов
            if 'nextPageToken' in resp:
                req = youtube.comments().list_next(req, resp)
            else:
                break
    except: pass
    return replies

# --- ПАРСИНГ ---
def process_videos(api_key, urls, deep_scan=False):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "comments.xlsx"
    
    # Прогресс-бар для визуализации
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, url in enumerate(urls):
        if "v=" in url: v_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url: v_id = url.split("youtu.be/")[1].split("?")[0]
        else: continue
        
        try:
            vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
            if vid_req['items']: 
                title = vid_req['items'][0]['snippet']['title']
                file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
            
            # Запрашиваем треды
            req = youtube.commentThreads().list(part="snippet,replies", videoId=v_id, maxResults=100)
            
            total_fetched = 0
            
            while req:
                resp = req.execute()
                for item in resp['items']: 
                    # 1. Главный комментарий
                    top = item['snippet']['topLevelComment']['snippet']
                    all_data.append({
                        'Автор': top['authorDisplayName'], 
                        'Текст': top['textDisplay'],
                        'Тип': 'Комментарий',
                        'Лайки': top['likeCount']
                    })
                    total_fetched += 1
                    
                    # 2. Работа с ответами
                    reply_count = item['snippet']['totalReplyCount']
                    
                    if reply_count > 0:
                        if deep_scan:
                            # РЕЖИМ "ЯДЕРНЫЙ": Качаем всё отдельным запросом
                            # Это тратит квоту, но достает все ответы
                            replies = get_all_replies(youtube, item['id'])
                            all_data.extend(replies)
                            total_fetched += len(replies)
                        else:
                            # РЕЖИМ "ЭКОНОМ": Берем только то, что дали сразу (до 5 шт)
                            if 'replies' in item:
                                for reply in item['replies']['comments']:
                                    all_data.append({
                                        'Автор': reply['snippet']['authorDisplayName'], 
                                        'Текст': reply['snippet']['textDisplay'],
                                        'Тип': 'Ответ',
                                        'Лайки': reply['snippet']['likeCount']
                                    })
                                    total_fetched += 1

                # Обновляем статус
                status_text.text(f"Собрано: {total_fetched}...")
                
                if 'nextPageToken' in resp:
                    req = youtube.commentThreads().list_next(req, resp)
                else:
                    break
        except Exception as e:
            st.error(f"Ошибка API: {e}")
            
    progress_bar.empty()
    status_text.empty()
    return all_data, file_name

# --- ИНТЕРФЕЙС ---
st.markdown("<h3 style='text-align: center; margin-bottom: 10px;'>YouTubeComm</h3>", unsafe_allow_html=True)

raw_urls = st.text_area("Ссылка:", height=100)

# НАСТРОЙКИ (В ДВЕ КОЛОНКИ)
c1, c2 = st.columns(2)
with c1:
    use_ai = st.toggle("AI-анализ", value=False)
with c2:
    # ТОТ САМЫЙ ПОЛЗУНОК
    deep_scan = st.toggle("🔥 Все ответы (Медленно!)", value=False, help="Выгружает ВСЕ ветки ответов. Тратит много квоты.")

# КНОПКА ЗАПУСКА
if st.button("Начать работу", type="primary", use_container_width=True):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        st.session_state['processed'] = False
        
        with st.spinner('Сбор данных (это может занять время)...'):
            # Передаем параметр deep_scan
            data, fname = process_videos(API_KEY, raw_urls.split('\n'), deep_scan=deep_scan)
        
        if data:
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            st.session_state['excel_data'] = buffer.getvalue()
            st.session_state['file_name'] = fname
            st.session_state['ai_text'] = None
            
            if use_ai:
                with st.spinner('Анализ...'):
                    summary, mod = get_ai_summary(data)
                    st.session_state['ai_text'] = summary

            st.session_state['processed'] = True
            
            # Отправка
            send_results_to_telegram(st.session_state['excel_data'], fname, st.session_state['ai_text'])

# БЛОК РЕЗУЛЬТАТОВ
if st.session_state['processed']:
    st.divider()
    
    st.info(f"✅ Готово! Собрано записей: {len(pd.read_excel(io.BytesIO(st.session_state['excel_data'])))}")
    
    if st.session_state['ai_text']:
        if "Ошибка" in st.session_state['ai_text']:
            st.error(st.session_state['ai_text'])
        else:
            st.markdown(st.session_state['ai_text'])
    
    st.download_button(
        label=f"📥 Скачать таблицу",
        data=st.session_state['excel_data'],
        file_name=st.session_state['file_name'],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
