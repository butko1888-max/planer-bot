"""Parse user text into (clean_text, due_date, remind_at)."""
import re
from datetime import datetime, timedelta, date, time
import pytz

TZ = pytz.timezone("Europe/Kiev")


def parse_task(text: str, now: datetime | None = None):
    """
    Returns (clean_text, due_date, remind_at).
    Patterns:
      - 'завтра 15:00 текст' / 'завтра текст'
      - 'сегодня 15:00 текст'
      - 'в 15:00 текст' / '15:00 текст' (today if future, tomorrow if past)
      - 'через час/30 мин/2 часа текст'
      - 'послезавтра ...'
      - 'в понедельник ...' / 'в пн ...'
      - plain text → tomorrow, no reminder
    """
    now = now or datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    s = text.strip()
    lower = s.lower()

    due_date = None
    remind_at = None

    # "через N час|мин|минут|часа"
    m = re.match(r"через\s+(\d+)\s*(час|часа|часов|ч|мин|минут|минуты|м)\s+(.+)", lower, re.I)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        rest = s[m.start(3):].strip()
        delta = timedelta(hours=n) if unit.startswith(("час", "ч")) else timedelta(minutes=n)
        remind_at = now + delta
        due_date = remind_at.date()
        return rest, due_date, remind_at

    # "завтра [HH:MM] текст"
    m = re.match(r"завтра\s+(?:в\s+)?(?:(\d{1,2}):(\d{2}))?\s*(.+)", lower, re.I)
    if m:
        h, mn = m.group(1), m.group(2)
        rest = s[m.start(3):].strip()
        due_date = tomorrow
        if h is not None:
            remind_at = TZ.localize(datetime.combine(tomorrow, time(int(h), int(mn))))
        return rest, due_date, remind_at

    # "сегодня [HH:MM] текст"
    m = re.match(r"сегодня\s+(?:в\s+)?(?:(\d{1,2}):(\d{2}))?\s*(.+)", lower, re.I)
    if m:
        h, mn = m.group(1), m.group(2)
        rest = s[m.start(3):].strip()
        due_date = today
        if h is not None:
            remind_at = TZ.localize(datetime.combine(today, time(int(h), int(mn))))
        return rest, due_date, remind_at

    # "послезавтра [HH:MM] текст"
    m = re.match(r"послезавтра\s+(?:в\s+)?(?:(\d{1,2}):(\d{2}))?\s*(.+)", lower, re.I)
    if m:
        h, mn = m.group(1), m.group(2)
        rest = s[m.start(3):].strip()
        d = today + timedelta(days=2)
        due_date = d
        if h is not None:
            remind_at = TZ.localize(datetime.combine(d, time(int(h), int(mn))))
        return rest, due_date, remind_at

    # "в HH:MM текст" / "HH:MM текст"
    m = re.match(r"(?:в\s+)?(\d{1,2}):(\d{2})\s+(.+)", s, re.I)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        rest = m.group(3).strip()
        target = TZ.localize(datetime.combine(today, time(h, mn)))
        if target <= now:
            target += timedelta(days=1)
        remind_at = target
        due_date = target.date()
        return rest, due_date, remind_at

    # Default: no date (goes to inbox) OR tomorrow? Choice: no date → inbox.
    # Actually make default = tomorrow so user's "evening dump" works.
    return s, tomorrow, None


def parse_time_only(text: str, base_date: date | None = None, now: datetime | None = None):
    """Parse 'HH:MM' or 'через N мин/час' → datetime. For 'set reminder' flow."""
    now = now or datetime.now(TZ)
    s = text.strip().lower()
    base_date = base_date or now.date()

    m = re.match(r"через\s+(\d+)\s*(час|часа|часов|ч|мин|минут|минуты|м)\s*$", s, re.I)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(hours=n) if unit.startswith(("час", "ч")) else timedelta(minutes=n)
        return now + delta

    m = re.match(r"(?:в\s+)?(\d{1,2}):(\d{2})$", s, re.I)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        target = TZ.localize(datetime.combine(base_date, time(h, mn)))
        if target <= now:
            target += timedelta(days=1)
        return target

    return None
