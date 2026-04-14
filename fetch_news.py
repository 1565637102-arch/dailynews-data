import os
import json
import httpx
import feedparser
import re
from datetime import datetime, timezone, timedelta

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Only keep articles published within this many days
MAX_AGE_DAYS = 3

RSS_FEEDS = [
    # 中文媒体
    {"source": "BBC中文",   "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"},
    {"source": "联合早报",  "url": "https://www.zaobao.com.sg/rss/realtime/world"},
    {"source": "联合早报",  "url": "https://www.zaobao.com.sg/rss/realtime/china"},
    {"source": "中央社",    "url": "https://www.cna.com.tw/rss/aall.aspx"},
    {"source": "自由亚洲",  "url": "https://www.rfa.org/mandarin/rss2.xml"},
    # 英文综合
    {"source": "Reuters",   "url": "https://feeds.reuters.com/reuters/topNews"},
    {"source": "Reuters",   "url": "https://feeds.reuters.com/reuters/worldNews"},
    {"source": "Reuters",   "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"source": "Reuters",   "url": "https://feeds.reuters.com/reuters/technologyNews"},
    {"source": "AP",        "url": "https://rsshub.app/ap/topics/apf-topnews"},
    {"source": "AP",        "url": "https://rsshub.app/ap/topics/apf-business"},
    {"source": "AP",        "url": "https://rsshub.app/ap/topics/apf-technology"},
    {"source": "CNN",       "url": "http://rss.cnn.com/rss/edition_world.rss"},
    {"source": "CNN",       "url": "http://rss.cnn.com/rss/edition_technology.rss"},
    {"source": "CNN",       "url": "http://rss.cnn.com/rss/money_latest.rss"},
    # 科技/AI
    {"source": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    {"source": "Ars Technica", "url": "http://feeds.arstechnica.com/arstechnica/technology-lab"},
    {"source": "Wired",     "url": "https://www.wired.com/feed/rss"},
    {"source": "TechCrunch","url": "https://techcrunch.com/feed/"},
    # 财经
    {"source": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss"},
    {"source": "FT",        "url": "https://www.ft.com/rss/home"},
    {"source": "WSJ",       "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml"},
    {"source": "WSJ",       "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    # 新能源/汽车
    {"source": "Electrek",  "url": "https://electrek.co/feed/"},
    {"source": "InsideEVs", "url": "https://insideevs.com/rss/"},
]

CATEGORIES = ["时政", "军事", "经济", "互联网", "AI", "新能源汽车", "3C数码", "中国特大新闻", "其他"]


def parse_entry_time(entry):
    """Return aware datetime or None."""
    for attr in ('published_parsed', 'updated_parsed'):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def extract_image(entry):
    # 1. media:content
    if hasattr(entry, 'media_content') and entry.media_content:
        for m in entry.media_content:
            if m.get('type', '').startswith('image') and m.get('url'):
                return m['url']
        # Some feeds omit type but still have url
        for m in entry.media_content:
            if m.get('url') and not m.get('type', '').startswith('video'):
                return m['url']

    # 2. media:thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        for m in entry.media_thumbnail:
            if m.get('url'):
                return m['url']

    # 3. enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for e in entry.enclosures:
            if e.get('type', '').startswith('image'):
                return e.get('href') or e.get('url')

    # 4. img in summary / content
    for field in ('summary', 'content'):
        text = ''
        val = getattr(entry, field, None)
        if isinstance(val, list):
            text = ' '.join(v.get('value', '') for v in val)
        elif isinstance(val, str):
            text = val
        if text:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', text)
            if m:
                return m.group(1)

    return None


def fetch_all_feeds():
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    seen_urls = set()
    articles = []

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:20]:
                url = entry.get('link', '').strip()
                if not url or url in seen_urls:
                    continue
                title = entry.get('title', '').strip()
                if not title:
                    continue

                pub_dt = parse_entry_time(entry)
                if pub_dt and pub_dt < cutoff:
                    # Article too old — skip
                    continue

                pub_time = pub_dt.isoformat() if pub_dt else datetime.now(timezone.utc).isoformat()
                seen_urls.add(url)

                summary_raw = ''
                if hasattr(entry, 'content') and entry.content:
                    summary_raw = entry.content[0].get('value', '')
                if not summary_raw:
                    summary_raw = entry.get('summary', '') or ''
                summary = re.sub(r'<[^>]+>', '', summary_raw)[:300]

                articles.append({
                    'source': feed_info['source'],
                    'title_original': title,
                    'title_zh': '',
                    'summary': summary,
                    'url': url,
                    'image_url': extract_image(entry),
                    'category': '',
                    'published_at': pub_time,
                })
        except Exception as e:
            print(f"Error fetching {feed_info['url']}: {e}")

    print(f"After date filter: {len(articles)} articles (cutoff: {cutoff.date()})")
    return articles


def classify_and_translate(articles):
    if not articles or not DEEPSEEK_API_KEY:
        for a in articles:
            if not a.get('title_zh'):
                a['title_zh'] = a['title_original']
            if not a.get('category'):
                a['category'] = '其他'
        return articles

    batch_size = 20
    results = []
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i+batch_size]
        items_text = "\n".join(
            f"{j+1}. [{a['source']}] {a['title_original']}"
            for j, a in enumerate(batch)
        )
        prompt = (
            "以下是一批新闻标题，请对每条新闻：\n"
            "1. 翻译成简洁准确的中文标题（如果已是中文则保持或优化）\n"
            "2. 从以下分类中选择最合适的一个：" + ", ".join(CATEGORIES) + "\n\n"
            "请严格按照以下JSON格式返回，不要有任何其他内容：\n"
            '[\n  {"title_zh": "中文标题", "category": "分类"},\n  ...\n]\n\n'
            "新闻列表：\n" + items_text
        )
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    DEEPSEEK_URL,
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2000,
                        "temperature": 0.3,
                    }
                )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                    for j, item in enumerate(parsed):
                        if j < len(batch):
                            batch[j]['title_zh'] = item.get('title_zh', batch[j]['title_original'])
                            batch[j]['category'] = item.get('category', '其他')
        except Exception as e:
            print(f"AI classification error: {e}")

        # Fallback for any items not filled
        for a in batch:
            if not a.get('title_zh'):
                a['title_zh'] = a['title_original']
            if not a.get('category'):
                a['category'] = '其他'

        results.extend(batch)
    return results


if __name__ == '__main__':
    print("Fetching RSS feeds...")
    articles = fetch_all_feeds()
    print(f"Fetched {len(articles)} recent articles")
    print("Classifying and translating...")
    articles = classify_and_translate(articles)
    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'articles': articles,
    }
    with open('news.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(articles)} articles to news.json")
