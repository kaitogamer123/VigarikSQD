"""Отрисовка картинки турнирной сетки (Pillow).

Старое оформление (арт-фон, «Участники / 1/4 финала / Полуфинал / Финал»,
предварительный раунд отдельным блоком) + адаптер к текущей модели матчей.
BYE-матчей нет: пустые слоты подписаны «Победитель пары N» / «ждёт».
"""
import io
import logging
import math
import os

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:  # noqa: BLE001
    PIL_AVAILABLE = False

from .bracket import _bracket_layout, _next_pow2, _total_rounds
from .tournament_db import _team_name

_FONT_FILES = {
    "sans": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/TTF/DejaVuSans.ttf",
             "C:/Windows/Fonts/arial.ttf",
             "C:/Windows/Fonts/segoeui.ttf"),
    "bold": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
             "C:/Windows/Fonts/arialbd.ttf",
             "C:/Windows/Fonts/segoeuib.ttf"),
    # серифный DejaVu есть в дефолтном пакете fonts-dejavu на Debian/Ubuntu
    "serif": ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
              "/usr/share/fonts/TTF/DejaVuSerif.ttf",
              "C:/Windows/Fonts/times.ttf",
              "C:/Windows/Fonts/georgia.ttf"),
    "serif_bold": ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
                   "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
                   "C:/Windows/Fonts/timesbd.ttf",
                   "C:/Windows/Fonts/georgiab.ttf"),
}


def _load_font(size: int, family: str = "sans"):
    """Шрифт с поддержкой кириллицы; при отсутствии — встроенный PIL-шрифт."""
    for path in _FONT_FILES.get(family, _FONT_FILES["sans"]):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    for path in _FONT_FILES["sans"]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _clean_team(text) -> str:
    """Убирает мусор из названия состава: 'TEST-03 [] TEST-05' → 'TEST-03 vs TEST-05'.

    Появлялось из-за пустых тегов («[]») и эмодзи-крестика, который шрифт не рисует.
    """
    raw = str(text or "")
    # убираем только эмодзи/пиктограммы (шрифт DejaVu их не рисует);
    # буквы, цифры, «№», тире и кавычки сохраняем
    cleaned = []
    for ch in raw:
        code = ord(ch)
        if ch in {"🆚", "⚔", "⚔️"}:
            cleaned.append(" vs ")
            continue
        if (
            0x1F000 <= code <= 0x1FAFF      # эмодзи
            or 0x2600 <= code <= 0x27BF     # символы/дингбаты
            or 0xFE00 <= code <= 0xFE0F     # variation selectors
            or 0x1F1E6 <= code <= 0x1F1FF   # флаги
            or code in (0x2192, 0x2190)     # стрелки
        ):
            continue
        cleaned.append(ch)
    out = "".join(cleaned).replace("[]", "")
    out = " ".join(out.split())
    return out.strip()


def _fit_text(draw, text, font, max_width: int) -> str:
    """Обрезает подпись по пикселям, чтобы она не вылезала из бокса."""
    text = _clean_team(text) or "—"
    try:
        if draw.textlength(text, font=font) <= max_width:
            return text
        while len(text) > 1 and draw.textlength(text + "…", font=font) > max_width:
            text = text[:-1]
        return text + "…"
    except Exception:
        return text[:28]


def _round_title(total: int, round_no: int) -> str:
    """1/8 финала / 1/4 финала / Полуфинал / Финал."""
    total = max(1, int(total))
    round_no = max(1, int(round_no))
    if round_no >= total:
        return "Финал"
    if round_no == total - 1:
        return "Полуфинал"
    return f"1/{2 ** (total - round_no)} финала"


# ─── Оформление сетки: три направления, а не одна «геймерская» тема ──────────
# protocol — светлый печатный протокол: бумага, тушь, серифный заголовок, острые углы
# board    — тёмное арена-табло: графит, тонкие линии, только янтарный акцент
# ledger   — клубный журнал: тёплый серо-зелёный, плотная вёрстка, малый радиус
BR_THEME_LABELS = {
    "sunset": "Закат",
    "protocol": "Протокол",
    "board": "Табло",
    "ledger": "Журнал",
}
BR_THEME_DESCRIPTIONS = {
    "sunset": "закатное море и палуба, приглушённый фон под сеткой",
    "protocol": "светлый печатный бланк, серифные заголовки, острые углы",
    "board": "тёмное табло арены, линии без рамок, янтарный акцент",
    "ledger": "тёплый клубный журнал, плотные строки, малый радиус",
}
BR_THEME_ORDER = ("sunset", "protocol", "board", "ledger")
BR_THEMES = {
    "protocol": {
        "bg": (243, 243, 239), "head": (233, 234, 228), "surface": (255, 255, 253),
        "line": (196, 200, 191), "text": (23, 25, 28), "muted": (100, 106, 99),
        "dim": (151, 156, 147), "brass": (124, 96, 40), "silver": (111, 121, 130),
        "copper": (146, 86, 52), "bronze": (122, 106, 84),
        "live": (166, 58, 38), "live_fill": (251, 236, 231), "live_text": (110, 33, 20),
        "radius": 0, "title_family": "serif_bold", "name_family": "serif",
    },
    "board": {
        "bg": (13, 15, 17), "head": (18, 21, 24), "surface": (23, 27, 31),
        "line": (47, 54, 60), "text": (235, 238, 240), "muted": (138, 148, 156),
        "dim": (93, 102, 109), "brass": (201, 162, 90), "silver": (166, 176, 184),
        "copper": (183, 124, 78), "bronze": (134, 119, 101),
        "live": (224, 163, 62), "live_fill": (36, 30, 18), "live_text": (243, 205, 137),
        "radius": 4, "title_family": "bold", "name_family": "sans",
    },
    "ledger": {
        "bg": (228, 233, 224), "head": (216, 223, 212), "surface": (243, 246, 240),
        "line": (178, 189, 173), "text": (27, 38, 30), "muted": (89, 103, 92),
        "dim": (137, 150, 138), "brass": (116, 86, 34), "silver": (96, 111, 116),
        "copper": (139, 78, 46), "bronze": (110, 95, 74),
        "live": (139, 62, 40), "live_fill": (246, 231, 224), "live_text": (94, 36, 22),
        "radius": 2, "title_family": "serif_bold", "name_family": "sans",
    },
    # закатное море и палуба — настроение Brawl Stars, приглушённое ради читаемости
    "sunset": {
        "bg": (12, 20, 34), "head": (10, 16, 28), "surface": (26, 33, 46),
        "line": (62, 80, 102), "text": (238, 243, 250), "muted": (156, 172, 192),
        "dim": (112, 128, 148), "brass": (247, 201, 92), "silver": (203, 214, 226),
        "copper": (232, 150, 92), "bronze": (172, 141, 108),
        "live": (104, 222, 156), "live_fill": (20, 46, 36), "live_text": (176, 246, 206),
        "radius": 6, "title_family": "serif_bold", "name_family": "sans",
        "bg_style": "sunset",
    },
}

# можно положить свою картинку на сервер — она станет фоном «sunset»
_BRACKET_BG_FILES = (
    "assets/bracket_bg.png", "assets/bracket_bg.jpg", "assets/bracket_bg.webp",
    "bracket_bg.png", "bracket_bg.jpg",
    "league/assets/tournament_bg.png", "league/assets/tournament_bg.jpg",
    "assets/tournament_bg.png", "assets/tournament_bg.jpg",
    "tournament_bg.png", "tournament_bg.jpg",
)


def _mix(a, b, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _cover(img, width: int, height: int):
    """Вписывает картинку по принципу cover и обрезает по центру."""
    ratio = max(width / img.width, height / img.height)
    size = (max(width, int(img.width * ratio) + 1), max(height, int(img.height * ratio) + 1))
    img = img.resize(size, Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def _custom_bracket_bg(width: int, height: int):
    """Своя картинка фона, если положить её в assets/ на сервере."""
    for path in _BRACKET_BG_FILES:
        if not os.path.exists(path):
            continue
        try:
            photo = _cover(Image.open(path).convert("RGB"), width, height)
            return Image.blend(photo, Image.new("RGB", (width, height), (7, 11, 20)), 0.45)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tournament bg load failed ({path}): {e}")
    return None


def _sunset_bracket_bg(width: int, height: int):
    """Закатное море с палубой: рисуем в 2× и уменьшаем — без «лесенок».

    Палитра и композиция намеренно тише референса: фон уходит под сетку,
    а не спорит с ней.
    """
    ss = 2
    w, h = width * ss, height * ss
    img = Image.new("RGB", (w, h), (10, 18, 32))
    d = ImageDraw.Draw(img)
    horizon = int(h * 0.56)

    top = (10, 20, 38)
    mid = (18, 46, 74)
    glow = (34, 96, 116)
    for y in range(horizon):
        t = y / max(1, horizon - 1)
        color = _mix(top, mid, t / 0.6) if t < 0.6 else _mix(mid, glow, (t - 0.6) / 0.4)
        d.line([(0, y), (w, y)], fill=color)

    cx, cy, r = int(w * 0.44), int(horizon - int(h * 0.02)), int(min(w, h) * 0.15)
    base = glow
    for i in range(16, 0, -1):
        rr = int(r * (1 + i * 0.22))
        color = _mix(base, (255, 238, 176), 0.055 * (16 - i) / 16 + 0.03)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 246, 198))
    d.ellipse([cx - int(r * 0.55), cy - int(r * 0.55), cx + int(r * 0.55), cy + int(r * 0.55)],
              fill=(255, 251, 222))

    for y in range(horizon, h):
        t = (y - horizon) / max(1, h - horizon)
        d.line([(0, y), (w, y)], fill=_mix((16, 52, 66), (8, 22, 34), t))
    d.line([(0, horizon), (w, horizon)], fill=(206, 226, 210), width=max(2, ss))

    # блики на воде
    for i in range(26):
        y = horizon + int((i + 1) * (h - horizon) / 30)
        half = int(r * (1.5 - i / 26) * 0.9)
        if y >= h:
            break
        d.line([(cx - half, y), (cx + half, y)],
               fill=_mix((16, 52, 66), (236, 236, 190), max(0.0, 0.5 - i * 0.02)),
               width=max(2, ss + (1 if i % 3 == 0 else 0)))

    # палуба: тёмный передний план, чтобы карточки читались
    d.polygon([(0, int(h * 0.93)), (int(w * 0.5), int(h * 0.80)),
               (w, int(h * 0.88)), (w, h), (0, h)], fill=(20, 18, 24))
    d.line([(0, int(h * 0.93)), (int(w * 0.5), int(h * 0.80)), (w, int(h * 0.88))],
           fill=(58, 46, 46), width=max(2, ss * 2))
    for i in range(7):
        x0 = int(w * 0.06 * i)
        d.line([(x0, h), (int(w * 0.5) + x0, int(h * 0.82))],
               fill=(30, 27, 33), width=max(1, ss))

    # мачта и ванты слева
    d.line([(int(w * 0.06), 0), (int(w * 0.10), int(h * 0.93))], fill=(16, 22, 30), width=max(4, ss * 5))
    for i in range(5):
        y = int(h * (0.05 + i * 0.16))
        d.line([(int(w * 0.06), y), (int(w * 0.24), int(h * 0.93))],
               fill=(30, 52, 62), width=max(1, ss))
    # пузыри на небе, как в референсе
    for i in range(14):
        bx = int(w * (0.08 + (i % 7) * 0.13))
        by = int(h * (0.05 + (i // 7) * 0.09))
        br = int(min(w, h) * 0.03)
        d.ellipse([bx - br, by - br, bx + br, by + br], outline=(46, 84, 116), width=ss)
        d.ellipse([bx - br + ss * 3, by - br + ss * 3, bx + br - ss * 3, by + br - ss * 3],
                  outline=(38, 70, 98), width=ss)

    # штурвал справа на горизонте
    wx, wy, wr = int(w * 0.90), horizon - int(h * 0.02), int(min(w, h) * 0.06)
    d.ellipse([wx - wr, wy - wr, wx + wr, wy + wr], outline=(94, 62, 52), width=max(3, ss * 3))
    d.ellipse([wx - int(wr * 0.35), wy - int(wr * 0.35), wx + int(wr * 0.35), wy + int(wr * 0.35)],
              outline=(94, 62, 52), width=max(2, ss * 2))
    for k in range(8):
        angle = k * math.pi / 4
        x1 = wx + int(math.cos(angle) * wr * 0.3)
        y1 = wy + int(math.sin(angle) * wr * 0.3)
        x2 = wx + int(math.cos(angle) * wr * 1.45)
        y2 = wy + int(math.sin(angle) * wr * 1.45)
        d.line([(x1, y1), (x2, y2)], fill=(94, 62, 52), width=max(2, ss * 2))

    img = img.resize((width, height), Image.LANCZOS)
    # «слегка темнее»: гасим фон, чтобы сетка оставалась главной
    return Image.blend(img, Image.new("RGB", (width, height), (6, 10, 18)), 0.38)


def _background_for_theme(width: int, height: int):
    style = BR_THEMES.get(BRACKET_THEME, {}).get("bg_style")
    if not style:
        return None
    custom = _custom_bracket_bg(width, height)
    if custom is not None:
        return custom
    return _sunset_bracket_bg(width, height)

# значения по умолчанию; переключаются командой /bracket_theme
BRACKET_THEME = "sunset"
BR_BG = BR_THEMES[BRACKET_THEME]["bg"]
BR_HEAD = BR_THEMES[BRACKET_THEME]["head"]
BR_SURFACE = BR_THEMES[BRACKET_THEME]["surface"]
BR_LINE = BR_THEMES[BRACKET_THEME]["line"]
BR_TEXT = BR_THEMES[BRACKET_THEME]["text"]
BR_MUTED = BR_THEMES[BRACKET_THEME]["muted"]
BR_DIM = BR_THEMES[BRACKET_THEME]["dim"]
BR_BRASS = BR_THEMES[BRACKET_THEME]["brass"]
BR_SILVER = BR_THEMES[BRACKET_THEME]["silver"]
BR_COPPER = BR_THEMES[BRACKET_THEME]["copper"]
BR_BRONZE = BR_THEMES[BRACKET_THEME]["bronze"]
BR_LIVE = BR_THEMES[BRACKET_THEME]["live"]
BR_LIVE_FILL = BR_THEMES[BRACKET_THEME]["live_fill"]
BR_LIVE_TEXT = BR_THEMES[BRACKET_THEME]["live_text"]
BR_RADIUS = BR_THEMES[BRACKET_THEME]["radius"]
BR_TITLE_FAMILY = BR_THEMES[BRACKET_THEME]["title_family"]
BR_NAME_FAMILY = BR_THEMES[BRACKET_THEME]["name_family"]
def _render_legacy(columns, title: str, subtitle: str = "",
                         third=None, solo: bool = False, labels=None,
                         playin=None, placements=None,
                         active_col: int | None = None,
                         active_playin: bool = False) -> bytes | None:
    """Сетка турнира в сдержанном редакционном стиле.

    columns[0] — участники, columns[r] — победители раунда r.
    Победитель отмечается тонкой линией и полосой слева, а не заливкой;
    призёры — типографикой (место + название), без «пилюль».
    """
    if not PIL_AVAILABLE:
        return None
    try:
        columns = [list(col) for col in (columns or [])]
        if not columns:
            columns = [["Свободный слот", "Свободный слот"]]
        solo = bool(solo) or len(columns) == 1
        placements = [(int(p), str(n)) for p, n in (placements or [])]
        total_rounds = max(0, len(columns) - 1)

        def stage_label(col_index: int) -> str:
            # последняя колонка — обладатель места; пока финал не закрыт, это «Финал»
            if not solo and len(columns) > 1 and col_index == len(columns) - 1:
                champ = _clean_team(columns[-1][0] if columns[-1] else "")
                decided = bool(champ) and champ.upper() != "BYE" and not any(
                    w in champ for w in ("Победитель", "Финал", "Полуфинал", "1/", "ждёт", "—")
                )
                return "Чемпион" if decided else "Финал"
            if labels and col_index < len(labels) and labels[col_index]:
                return labels[col_index]
            if col_index <= 0:
                return "Участники"
            from_end = len(columns) - col_index
            return {1: "Финал", 2: "Полуфинал", 3: "1/4 финала", 4: "1/8 финала"}.get(
                from_end, f"1/{2 ** (from_end - 1)} финала")

        # сетка отступов кратна 4 — визуальный ритм
        pad = 48
        box_w, box_h, v_gap, gap_x = 268, 56, 18, 64
        row_h = box_h + v_gap
        header_h = 96
        panel_h = 112 if placements else 0
        label_y = header_h + panel_h + 34
        top = label_y + 26

        rows = len(columns[0])
        bottom = top + rows * row_h - v_gap
        third_h = 96 if (third and (third.get("side_a") or third.get("side_b"))) else 0
        playin_rows = math.ceil(len(playin) / 2) if playin else 0
        # нижние блоки: больше воздуха перед карточкой за 3-4 места
        playin_block = (44 + 30 + 26 * playin_rows + 24) if playin else 0
        third_block = (52 + third_h + 56) if third_h else 0
        height = max(520, bottom + (56 if not (playin or third_h) else 0)
                     + playin_block + third_block)
        width = max(980, pad * 2 + len(columns) * box_w + (len(columns) - 1) * gap_x)

        themed_bg = _background_for_theme(width, height)
        if themed_bg is not None:
            img = themed_bg
            # затемняющие полосы под шапкой и призёрами — текст поверх рисунка читается
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle([0, 0, width, header_h], fill=(8, 12, 20, 190))
            if placements:
                od.rectangle([0, header_h, width, header_h + panel_h], fill=(8, 12, 20, 170))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        else:
            img = Image.new("RGB", (width, height), BR_BG)
        draw = ImageDraw.Draw(img)
        f_title = _load_font(31, BR_TITLE_FAMILY)
        f_meta = _load_font(13, BR_TITLE_FAMILY)
        f_rank = _load_font(13)
        f_name = _load_font(21, BR_NAME_FAMILY)
        f_stage = _load_font(12)
        f_slot = _load_font(18, BR_NAME_FAMILY)
        f_small = _load_font(13)

        # сколько матчей реально идёт прямо сейчас (обе команды известны, итога нет)
        live_cells = 0
        for _c, _col in enumerate(columns):
            if _c == 0:
                continue
            for _v in _col:
                _raw = _clean_team(_v)
                if " vs " in _raw and not _raw.endswith(("ждёт", "ждет")):
                    live_cells += 1
        word = ("матч" if live_cells % 10 == 1 and live_cells % 100 != 11
                else "матча" if 2 <= live_cells % 10 <= 4 and not 12 <= live_cells % 100 <= 14
                else "матчей")

        # шапка: заголовок слева, справа — короткий статус строчными буквами
        if themed_bg is None:
            draw.rectangle([0, 0, width, header_h], fill=BR_HEAD)
        draw.rectangle([pad, 30, pad + 3, 66], fill=BR_BRASS)
        draw.text((pad + 18, 26), _fit_text(draw, title, f_title, width - pad * 2 - 24),
                  font=f_title, fill=BR_TEXT)
        draw.line([(0, header_h), (width, header_h)], fill=BR_LINE, width=1)

        # призёры: место + название, разделены волосяными линиями
        if placements:
            y = header_h
            if themed_bg is None:
                draw.rectangle([0, y, width, y + panel_h], fill=BR_HEAD)
            rank_color = {1: BR_BRASS, 2: BR_SILVER, 3: BR_COPPER, 4: BR_BRONZE}
            cells = placements[:4]
            cell_w = (width - pad * 2) / max(1, len(cells))
            for i, (place, name) in enumerate(cells):
                x = pad + i * cell_w
                if i:
                    draw.line([(x, y + 20), (x, y + 92)], fill=BR_LINE, width=1)
                color = rank_color.get(place, BR_MUTED)
                draw.text((x + 24, y + 28), f"{place} место", font=f_rank, fill=color)
                draw.text((x + 24, y + 50), _fit_text(draw, name, f_name, cell_w - 48),
                          font=f_name, fill=BR_TEXT)
            draw.line([(0, y + panel_h), (width, y + panel_h)], fill=BR_LINE, width=1)

        # заголовки стадий
        col_positions: list[list[float]] = []
        for c, col in enumerate(columns):
            if c == 0:
                centers = [top + i * row_h + box_h / 2 for i in range(len(col))]
            else:
                prev = col_positions[c - 1]
                centers = []
                for i in range(len(col)):
                    a = prev[2 * i] if 2 * i < len(prev) else prev[-1]
                    b = prev[2 * i + 1] if 2 * i + 1 < len(prev) else a
                    centers.append((a + b) / 2)
            col_positions.append(centers)
            x = pad + c * (box_w + gap_x)
            is_active = active_col is not None and c == active_col
            label_text = stage_label(c)
            draw.text((x, label_y), _fit_text(draw, label_text, f_stage, box_w),
                      font=f_stage, fill=BR_LIVE if is_active else BR_MUTED)
            if is_active:
                bar_w = min(box_w, draw.textlength(label_text, font=f_stage) + 4)
                draw.rectangle([x, label_y + 17, x + bar_w, label_y + 18], fill=BR_LIVE)

        # коннекторы рисуются первыми и уходят под карточки
        for c in range(1, len(columns)):
            x_prev = pad + (c - 1) * (box_w + gap_x) + box_w
            x_cur = pad + c * (box_w + gap_x)
            mid_x = x_prev + gap_x / 2
            for i, y_cur in enumerate(col_positions[c]):
                y_a = col_positions[c - 1][2 * i] if 2 * i < len(col_positions[c - 1]) else y_cur
                y_b = col_positions[c - 1][2 * i + 1] if 2 * i + 1 < len(col_positions[c - 1]) else y_a
                draw.line([(x_prev, y_a), (mid_x, y_a)], fill=BR_LINE, width=2)
                draw.line([(x_prev, y_b), (mid_x, y_b)], fill=BR_LINE, width=2)
                if y_a != y_b:
                    draw.line([(mid_x, y_a), (mid_x, y_b)], fill=BR_LINE, width=2)
                draw.line([(mid_x, y_cur), (x_cur, y_cur)], fill=BR_LINE, width=2)

        placeholder_words = ("Победитель", "Свободный слот", "Слот ", "ждёт", "?")
        stage_words = ("1/2 финала", "1/4 финала", "1/8 финала", "1/16 финала",
                       "Полуфинал", "Финал", "Предварительный раунд")
        live_count = 0
        for c, col in enumerate(columns):
            x = pad + c * (box_w + gap_x)
            for i, name in enumerate(col):
                cy = col_positions[c][i]
                raw = _clean_team(name)
                is_bye = raw.strip().upper() == "BYE"
                in_stage_name = raw.strip() in stage_words
                is_placeholder = in_stage_name or any(w in raw for w in placeholder_words) \
                    or raw.strip() in ("—", "")
                is_waiting = raw.endswith("ждёт") or raw.endswith("ждет")
                # матч в игре: обе команды известны, победитель ещё не записан
                is_live = (not is_placeholder) and (not is_waiting) and (" vs " in raw) and c > 0
                # прошедший дальше: имя команды оказалось в следующем раунде
                is_advanced = (not is_placeholder) and (not is_waiting) \
                    and (not is_live) and (not is_bye) and c > 0

                if is_live:
                    outline, fill, color = BR_LIVE, BR_LIVE_FILL, BR_LIVE_TEXT
                    live_count += 1
                elif is_advanced:
                    outline, fill, color = BR_LINE, BR_SURFACE, BR_TEXT
                elif is_waiting:
                    outline, fill, color = BR_LINE, BR_HEAD, BR_MUTED
                elif is_placeholder or is_bye:
                    outline, fill, color = BR_LINE, BR_HEAD, BR_DIM
                else:
                    outline, fill, color = BR_MUTED, BR_SURFACE, BR_TEXT

                draw.rounded_rectangle(
                    [x, cy - box_h / 2, x + box_w, cy + box_h / 2],
                    radius=BR_RADIUS, fill=fill,
                    outline=outline, width=2 if is_live else 1,
                )
                if is_live or is_advanced:
                    strip = BR_LIVE if is_live else BR_MUTED
                    draw.rectangle([x, cy - box_h / 2 + 5, x + 3, cy + box_h / 2 - 5],
                                   fill=strip)

                label = "BYE" if is_bye else raw
                if is_live:
                    # подпись «идёт» — без капса и без легенды над сеткой
                    tag = "идёт"
                    tag_w = draw.textlength(tag, font=f_small) + 14
                    tx0 = x + box_w - tag_w - 8
                    draw.rounded_rectangle([tx0, cy - 9, tx0 + tag_w, cy + 9],
                                           radius=max(2, BR_RADIUS),
                                           fill=fill, outline=BR_LIVE, width=1)
                    draw.text((tx0 + 7, cy - 6), tag, font=f_small, fill=BR_LIVE_TEXT)
                    draw.text((x + 14, cy - 11),
                              _fit_text(draw, label, f_slot, box_w - 28 - tag_w),
                              font=f_slot, fill=color)
                else:
                    draw.text((x + 14, cy - 11),
                              _fit_text(draw, label, f_slot, box_w - 28),
                              font=f_slot, fill=color)

        # предварительный раунд
        if playin:
            y0 = bottom + 48
            draw.text((pad, y0), "Предварительный раунд", font=f_stage,
                      fill=BR_LIVE if active_playin else BR_MUTED)
            if active_playin:
                draw.rectangle([pad, y0 + 17, pad + 128, y0 + 18], fill=BR_LIVE)
            for i, (a, b) in enumerate(playin):
                px = pad + (i % 2) * (box_w + gap_x)
                py = y0 + 30 + (i // 2) * 26
                pair_text = f"{_clean_team(a)} vs {_clean_team(b)}"
                if active_playin:
                    draw.ellipse([px, py + 7, px + 7, py + 14], fill=BR_LIVE)
                    draw.text((px + 14, py),
                              _fit_text(draw, pair_text, f_slot, box_w + gap_x - 38),
                              font=f_slot, fill=BR_LIVE_TEXT)
                else:
                    draw.text((px, py),
                              _fit_text(draw, pair_text, f_slot, box_w + gap_x - 24),
                              font=f_slot, fill=BR_TEXT)

        # матч за 3-4 места: обычная карточка с акцентной полосой
        if third_h:
            y0 = bottom + 52
            if playin:
                y0 = bottom + playin_block + 40
            box3_w = min(width - pad * 2, box_w * 2 + gap_x)
            draw.rounded_rectangle([pad, y0, pad + box3_w, y0 + 92],
                                   radius=BR_RADIUS + 2,
                                   fill=BR_SURFACE, outline=BR_LINE, width=1)
            draw.rectangle([pad, y0 + 1, pad + 3, y0 + 91], fill=BR_COPPER)
            draw.text((pad + 20, y0 + 14), "Матч за 3–4 места",
                      font=f_stage, fill=BR_COPPER)
            a = _clean_team(third.get("side_a") or "ждёт полуфинала")
            b = _clean_team(third.get("side_b") or "ждёт полуфинала")
            winner = _clean_team(third.get("winner") or "")
            draw.text((pad + 20, y0 + 36),
                      _fit_text(draw, f"{a}   ·   {b}", f_slot, box3_w - 40),
                      font=f_slot, fill=BR_TEXT)
            if winner:
                draw.text((pad + 20, y0 + 64),
                          _fit_text(draw, f"Победитель: {winner}", f_small, box3_w - 40),
                          font=f_small, fill=(158, 214, 178))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.error(f"render_bracket_image error: {e}")
        return None




# ─── Адаптер: текущие матчи → колонки старого рендера ────────────────────────

def _stage_title(round_no: int, last_round: int) -> str:
    """«1/4 финала» / «Полуфинал» / «Финал» по номеру раунда."""
    diff = max(0, int(last_round) - int(round_no))
    return {
        0: "Финал", 1: "Полуфинал", 2: "1/4 финала", 3: "1/8 финала",
        4: "1/16 финала", 5: "1/32 финала", 6: "1/64 финала",
    }.get(diff, f"1/{2 ** diff} финала")


def _name_of(team_id) -> str:
    if not team_id:
        return ""
    try:
        return str(_team_name(team_id, "") or "")
    except Exception:
        return ""


def _by_round(matches):
    rounds = {}
    for m in matches or []:
        if str(m["bracket"] or "main") != "main":
            continue
        rounds.setdefault(int(m["round_no"]), []).append(m)
    for ms in rounds.values():
        ms.sort(key=lambda x: int(x["slot_no"]))
    return rounds


def _preview_data(names):
    """Сетка-предпросмотр из одних имён (без матчей)."""
    layout, autos = _bracket_layout(names)
    if not layout:
        return [], [], []
    last_round = len(layout)
    start_round = 2 if autos else 1
    playin = [(a, b) for (a, b) in layout[0]] if autos else []

    first = []
    for pair in layout[start_round - 1]:
        for name in pair:
            if name:
                first.append(name)
            elif autos and len(first) < autos:
                first.append(f"Победитель пары {len(first) + 1}")
            else:
                first.append("ждёт")

    columns = [first]
    for r in range(start_round, last_round + 1):
        columns.append([_stage_title(r, last_round)] * len(layout[r - 1]))
    labels = ["Участники"] + [_stage_title(r, last_round)
                              for r in range(start_round, last_round + 1)]
    return columns, labels, playin


def _live_data(names, matches):
    """Колонки из реальных матчей: участники → матчи каждого раунда."""
    rounds = _by_round(matches)
    if not rounds:
        return [], [], []
    n = len(names)
    size = _next_pow2(n)
    last_round = max(_total_rounds(n), max(rounds))
    playin_mode = n != size
    start_round = 2 if playin_mode else 1

    playin = []
    playin_index = {}
    if playin_mode:
        for i, m in enumerate(rounds.get(1, []), 1):
            playin_index[int(m["slot_no"])] = i
            playin.append((_name_of(m["team_a_id"]) or "ждёт",
                           _name_of(m["team_b_id"]) or "ждёт"))

    first = []
    if playin_mode:
        for m in rounds.get(2, []):
            slot = int(m["slot_no"])
            for key, fed_by in (("team_a_id", 2 * slot - 1), ("team_b_id", 2 * slot)):
                name = _name_of(m[key])
                if name:
                    first.append(name)
                else:
                    idx = playin_index.get(fed_by)
                    first.append(f"Победитель пары {idx}" if idx else "ждёт")
    else:
        for m in rounds.get(1, []):
            first.append(_name_of(m["team_a_id"]) or "ждёт")
            first.append(_name_of(m["team_b_id"]) or "ждёт")
    if not first:
        return [], [], []

    columns = [first]
    for r in range(start_round, last_round + 1):
        col = []
        for m in rounds.get(r, []):
            a, b = _name_of(m["team_a_id"]), _name_of(m["team_b_id"])
            winner = _name_of(m["winner_team_id"])
            if winner:
                col.append(winner)
            elif a and b:
                col.append(f"{a} vs {b}")
            elif a or b:
                col.append(f"{a or b} · ждёт")
            else:
                col.append(_stage_title(r, last_round))
        # раунд ещё не создан в БД — рисуем полную сетку с заглушками
        expected = max(1, size >> r)
        while len(col) < expected:
            col.append(_stage_title(r, last_round))
        columns.append(col)

    labels = ["Участники"] + [_stage_title(r, last_round)
                              for r in range(start_round, last_round + 1)]
    return columns, labels, playin


def _third_and_placements(matches):
    thirds = [m for m in (matches or []) if str(m["bracket"] or "main") == "third"]
    third = None
    if thirds:
        m = thirds[0]
        third = {
            "side_a": _name_of(m["team_a_id"]) or "ждёт полуфинала",
            "side_b": _name_of(m["team_b_id"]) or "ждёт полуфинала",
            "winner": _name_of(m["winner_team_id"]),
        }

    placements = []
    mains = [m for m in (matches or []) if str(m["bracket"] or "main") == "main"]
    if mains:
        last_round = max(int(m["round_no"]) for m in mains)
        final = [m for m in mains if int(m["round_no"]) == last_round]
        if len(final) == 1 and final[0]["winner_team_id"]:
            fm = final[0]
            placements.append((1, _name_of(fm["winner_team_id"]) or "—"))
            if fm["loser_team_id"]:
                placements.append((2, _name_of(fm["loser_team_id"]) or "—"))
    if thirds and thirds[0]["winner_team_id"]:
        placements.append((3, _name_of(thirds[0]["winner_team_id"]) or "—"))
        if thirds[0]["loser_team_id"]:
            placements.append((4, _name_of(thirds[0]["loser_team_id"]) or "—"))
    return third, placements


def _active_state(names, matches):
    rounds = _by_round(matches)
    pending = [(r, m) for r, ms in rounds.items() for m in ms
               if m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]]
    n = len(names)
    size = _next_pow2(n)
    playin_mode = n != size
    start_round = 2 if playin_mode else 1
    active_playin = any(r == 1 for r, _ in pending) and playin_mode
    main_pending = sorted(r for r, _ in pending if r >= start_round)
    active_col = (main_pending[0] - start_round + 1) if main_pending else None
    return active_col, active_playin


def render_bracket_image(names: list[str], title: str, matches=None):
    """Сетка в старом оформлении. None — если Pillow нет или отрисовка упала."""
    try:
        if matches:
            columns, labels, playin = _live_data(names, matches)
        else:
            columns, labels, playin = _preview_data(names)
        if not columns:
            return None
        third, placements = _third_and_placements(matches)
        active_col, active_playin = _active_state(names, matches)
        data = _render_legacy(
            columns, title, "",
            third=third, labels=labels, playin=playin,
            placements=placements,
            active_col=active_col, active_playin=active_playin,
        )
        if not data:
            return None
        buf = io.BytesIO(data)
        buf.seek(0)
        return buf
    except Exception:
        logger.exception("Не удалось отрисовать сетку турнира")
        return None
