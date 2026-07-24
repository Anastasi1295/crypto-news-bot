import os
import json
import feedparser
import httpx
import asyncio
import re

# === НАСТРОЙКИ ===
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHANNEL_ID = os.environ['CHANNEL_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

RSS_FEEDS = [
    "https://www.theblock.co/rss.xml",
    "https://www.binance.com/en/blog/rss",
    "https://blog.kraken.com/feed/",
    "https://www.coinbase.com/blog/rss.xml",
    "https://cyprus-mail.com/feed/"
]

EXCHANGES = ["binance", "kraken", "okx", "bybit", "coinbase"]
STABLECOINS = ["usdt", "usdc", "tether", "circle", "stablecoin", "e-money token", "emt", "art"]
REGULATORY = ["mica", "caspr", "license", "authorized", "sepa", "sepainstant", "кипр", "cyprus", "налог", "tax", "aml", "travel rule", "bafin", "amf"]

def load_sent_news():
    try:
        with open('sent_news.json', 'r') as f:
            return json.load(f)
    except:
        return []

def save_sent_news(sent_news):
    with open('sent_news.json', 'w') as f:
        json.dump(sent_news, f, indent=2)

def fetch_and_filter_rss():
    all_news = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries[:15]: # Берём чуть больше для фильтрации
            title = entry.get('title', '').lower()
            summary = entry.get('summary', entry.get('description', '')).lower()
            link = entry.get('link', '')
            original_title = entry.get('title', '')
            original_summary = entry.get('summary', entry.get('description', ''))
            
            text = f"{title} {summary}"
            
            # Логика фильтрации: (Биржа ИЛИ Стейблкоин) И (Регуляторика)
            has_exchange = any(ex in text for ex in EXCHANGES)
            has_stable = any(st in text for st in STABLECOINS)
            has_reg = any(reg in text for reg in REGULATORY)
            
            if (has_exchange or has_stable) and has_reg:
                all_news.append({
                    'title': original_title,
                    'summary': original_summary,
                    'link': link
                })
    
    return all_news

async def analyze_with_groq(title, summary):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты — старший аналитик по крипто-регулированию в ЕС. Оцени новость по критериям: 1) Лицензирование бирж по MiCA, 2) Операции с USDT/USDC, 3) SEPA/банкинг, 4) Налоги Кипра. Верни СТРОГО валидный JSON: {\"relevance_score\": 1-10, \"tags\": [\"#MiCA\", \"#SEPA\", \"#CyprusTax\", \"#Stablecoin\", \"#ExchangeLicense\"], \"summary_ru\": \"Суть в 2 предложениях с акцентом на последствия\", \"action_required\": \"Да/Нет\"}"
                    },
                    {
                        "role": "user",
                        "content": f"Заголовок: {title}\nТекст: {summary[:800]}"
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 300
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data['choices'][0]['message']['content']
            try:
                json_match = re.search(r'\{.*?\}', content, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
            except:
                pass
            return {"relevance_score": 5, "tags": ["#General"], "summary_ru": content, "action_required": "Нет"}
        else:
            return {"relevance_score": 0, "tags": ["#Error"], "summary_ru": f"Ошибка API: {response.status_code}", "action_required": "Нет"}

async def send_to_telegram(analysis, title, link):
    tags_str = " ".join(analysis.get("tags", ["#Crypto"]))
    action_emoji = " ⚠️ ТРЕБУЕТ ВНИМАНИЯ" if analysis.get("action_required", "Нет").lower() == "да" else ""
    
    text = f"""{tags_str}{action_emoji}

<b>{title}</b>

{analysis.get('summary_ru', 'Нет описания')}

🔗 [Читать оригинал]({link})

Оценка важности: {analysis.get('relevance_score', 'N/A')}/10"""
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHANNEL_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
        )
        return response.status_code == 200

async def main():
    print("=== Запуск крипто-аналитика ===")
    sent_news = load_sent_news()
    all_news = fetch_and_filter_rss()
    
    sent_links = set(sent_news)
    new_news = [item for item in all_news if item['link'] not in sent_links]
    print(f"Найдено {len(new_news)} новых релевантных новостей")
    
    if not new_news:
        print("Новостей нет.")
        return
    
    for news in new_news[:5]: # Максимум 5 самых свежих
        print(f"Анализ: {news['title'][:50]}...")
        analysis = await analyze_with_groq(news['title'], news['summary'])
        
        # Публикуем только если оценка важности >= 6 (чтобы отсечь мусор)
        if analysis.get("relevance_score", 0) >= 6:
            success = await send_to_telegram(analysis, news['title'], news['link'])
            if success:
                print("✅ Отправлено")
                sent_news.append(news['link'])
        else:
            print(f"⏭️ Пропущено (низкая важность: {analysis.get('relevance_score')})")
    
    save_sent_news(sent_news)
    print("=== Готово ===")

asyncio.run(main())
