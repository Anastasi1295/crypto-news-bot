import os
import json
import re
import feedparser
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# === НАСТРОЙКИ ===
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHANNEL_ID = os.environ['CHANNEL_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

# Отправляем только новости за последние 24 часа
HOURS_AGO = 24

# Максимум 5 лучших новостей за запуск
MAX_NEWS_TO_SEND = 5

RSS_FEEDS = [
    "https://www.theblock.co/rss.xml",
    "https://www.binance.com/en/blog/rss",
    "https://blog.kraken.com/feed/",
    "https://www.coinbase.com/blog/rss.xml",
    "https://cyprus-mail.com/feed/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# Ключевые слова для фильтрации
EXCHANGES = ["binance", "kraken", "okx", "bybit", "coinbase"]
STABLECOINS = ["usdt", "usdc", "tether", "circle", "stablecoin", "e-money token", "emt", "art"]
REGULATORY = [
    "mica", "caspr", "license", "authorized", "sepa", "sepainstant",
    "кипр", "cyprus", "налог", "tax", "aml", "travel rule", "bafin", "amf",
    "esma", "eba", "ecb", "dlr", "crypto-asset", "casp"
]

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def load_sent_news():
    """Загружает список уже отправленных новостей из файла."""
    try:
        with open('sent_news.json', 'r') as f:
            return json.load(f)
    except:
        return []

def save_sent_news(sent_news):
    """Сохраняет список отправленных новостей."""
    with open('sent_news.json', 'w') as f:
        json.dump(sent_news, f, indent=2)

def parse_entry_date(entry):
    """Пытается извлечь дату публикации из RSS-записи."""
    for date_field in ['published', 'updated', 'created']:
        date_str = entry.get(date_field)
        if date_str:
            try:
                return parsedate_to_datetime(date_str)
            except Exception:
                pass
    
    if entry.get('published_parsed'):
        try:
            from calendar import timegm
            ts = timegm(entry['published_parsed'])
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    
    return None

def is_news_fresh(entry, hours_ago):
    """Проверяет, что новость опубликована не позже чем hours_ago часов назад."""
    pub_date = parse_entry_date(entry)
    if pub_date is None:
        return True
    
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return pub_date >= cutoff

def fetch_and_filter_rss():
    """Собирает RSS и фильтрует по ключевым словам + дате."""
    all_news = []
    skipped_old = 0
    skipped_irrelevant = 0
    skipped_already_sent = 0
    
    # Загружаем уже отправленные ссылки
    sent_links = set(load_sent_news())
    
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        source_name = feed.feed.get('title', url)
        
        for entry in feed.entries[:50]:
            title = entry.get('title', '')
            summary = entry.get('summary', entry.get('description', ''))
            link = entry.get('link', '')
            
            # 1. Проверяем, не отправляли ли уже
            if link in sent_links:
                skipped_already_sent += 1
                continue
            
            # 2. Фильтр по дате
            if not is_news_fresh(entry, HOURS_AGO):
                skipped_old += 1
                continue
            
            # 3. Фильтр по ключевым словам
            text = f"{title} {summary}".lower()
            
            has_exchange = any(ex in text for ex in EXCHANGES)
            has_stable = any(st in text for st in STABLECOINS)
            has_reg = any(reg in text for reg in REGULATORY)
            
            if not ((has_exchange or has_stable) and has_reg):
                skipped_irrelevant += 1
                continue
            
            all_news.append({
                'title': title,
                'summary': summary,
                'link': link,
                'source': source_name
            })
    
    print(f"Пропущено уже отправленных: {skipped_already_sent}")
    print(f"Пропущено устаревших: {skipped_old}")
    print(f"Пропущено нерелевантных: {skipped_irrelevant}")
    print(f"Найдено кандидатов: {len(all_news)}")
    
    return all_news

async def analyze_with_groq(title, summary):
    """Анализирует новость через Groq LLM и возвращает оценку важности."""
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
                        "content": (
                            "Ты — старший аналитик по крипто-регулированию в ЕС. "
                            "Оцени новость по критериям: "
                            "1) Лицензирование бирж (Binance, Kraken, OKX, Bybit, Coinbase) по MiCA, "
                            "2) Операции с USDT/USDC (ограничения, новые пары, изменения в депозитах/выводах), "
                            "3) SEPA/банкинг (блокировки счетов, новые коридоры), "
                            "4) Налоги Кипра для криптоактивов. "
                            "Верни СТРОГО валидный JSON: "
                            "{\"relevance_score\": 1-10, \"tags\": [\"#MiCA\", \"#SEPA\", \"#CyprusTax\", \"#Stablecoin\", \"#ExchangeLicense\", \"#AMLD6\"], "
                            "\"summary_ru\": \"Суть в 2 предложениях с акцентом на последствия для трейдеров/бизнеса\", "
                            "\"action_required\": \"Да/Нет\"}"
                        )
                    },
                    {
                        "role": "user",
                        "content": f"Заголовок: {title}\nТекст: {summary[:1000]}"
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
            except Exception as e:
                print(f"Ошибка парсинга JSON от LLM: {e}")
            return {
                "relevance_score": 5,
                "tags": ["#General"],
                "summary_ru": content,
                "action_required": "Нет"
            }
        else:
            print(f"Groq API error: {response.status_code}")
            return {
                "relevance_score": 0,
                "tags": ["#Error"],
                "summary_ru": f"Ошибка анализа: {response.status_code}",
                "action_required": "Нет"
            }

async def send_to_telegram(analysis, title, link, source):
    """Отправляет отформатированное сообщение в Telegram."""
    tags_str = " ".join(analysis.get("tags", ["#Crypto"]))
    action_emoji = " ⚠️ ТРЕБУЕТ ВНИМАНИЯ" if str(analysis.get("action_required", "Нет")).lower() == "да" else ""
    relevance = analysis.get("relevance_score", "N/A")
    
    text = (
        f"{tags_str}{action_emoji}\n\n"
        f"<b>{title}</b>\n\n"
        f"{analysis.get('summary_ru', 'Нет описания')}\n\n"
        f"📰 Источник: {source}\n"
        f"🔗 [Читать оригинал]({link})\n\n"
        f"Оценка важности: {relevance}/10"
    )
    
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
    print("=" * 60)
    print("=== Запуск крипто-аналитика ===")
    print(f"Фильтр: новости за последние {HOURS_AGO} часов")
    print(f"Максимум новостей: {MAX_NEWS_TO_SEND}")
    print("=" * 60)
    
    # Собираем и фильтруем новости
    all_news = fetch_and_filter_rss()
    
    if not all_news:
        print("\nНовых релевантных новостей нет. Завершаем работу.")
        return
    
    # Анализируем ВСЕ найденные новости через LLM
    print(f"\nАнализируем {len(all_news)} новостей через Groq...")
    analyzed_news = []
    
    for news in all_news:
        print(f"  Анализ: {news['title'][:50]}...")
        analysis = await analyze_with_groq(news['title'], news['summary'])
        news['analysis'] = analysis
        news['relevance_score'] = analysis.get("relevance_score", 0)
        analyzed_news.append(news)
    
    # Сортируем по оценке важности (от высокой к низкой)
    analyzed_news.sort(key=lambda x: x['relevance_score'], reverse=True)
    
    # Берём топ-5
    top_news = analyzed_news[:MAX_NEWS_TO_SEND]
    
    print(f"\n{'=' * 60}")
    print(f"Отправляем топ-{len(top_news)} новостей:")
    print("=" * 60)
    
    # Отправляем топ-5
    sent_count = 0
    sent_links = load_sent_news()
    
    for news in top_news:
        relevance = news['relevance_score']
        print(f"\n[{relevance}/10] {news['title'][:60]}...")
        
        success = await send_to_telegram(
            news['analysis'],
            news['title'],
            news['link'],
            news['source']
        )
        
        if success:
            print("✅ Отправлено")
            sent_links.append(news['link'])
            sent_count += 1
        else:
            print("❌ Ошибка отправки")
    
    # Сохраняем обновлённый список
    save_sent_news(sent_links)
    
    print("\n" + "=" * 60)
    print(f"=== Готово! Отправлено: {sent_count} новостей ===")
    print(f"Всего сохранено отправленных ссылок: {len(sent_links)}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
