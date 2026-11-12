"""Отрисовка картинки турнирной сетки (Pillow)."""
import logging
import os
from .bracket import _bracket_layout, _round_title
from .tournament_db import _team_name

logger = logging.getLogger(__name__)


def render_bracket_image(names: list[str], title: str, matches=None):
    """Безопасная обёртка: при любой ошибке отрисовки возвращает None (текстовая сетка)."""
    try:
        return _render_bracket_image(names, title, matches)
    except Exception:
        logger.exception("Не удалось отрисовать сетку турнира")
        return None


def _render_bracket_image(names: list[str], title: str, matches=None):
    """Рисует сетку турнира (PNG в BytesIO). None — если Pillow не установлен.

    Компактная схема без фейковых матчей с BYE: первый круг — плей-ин только из
    реальных пар, а команды без пары показаны сразу в следующем круге.
    Если передан matches — подмешиваем реальные продвинувшиеся команды и
    подсвечиваем победителей зелёным.
    """
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except Exception as e:
        logger.warning(f"Pillow недоступен, сетка будет текстовой: {e}")
        return None

    n = len(names)
    if n < 2:
        return None
    size = 1
    while size < n:
        size *= 2
    total_rounds = size.bit_length() - 1
    playin = (n != size)
    playin_matches = n - size // 2 if playin else 0
    autos = size - n

    # ── Данные раундов: [ (label_a, label_b, win_side|None, live) ] ──
    # live=True — матч играбельный ПРЯМО СЕЙЧАС: обе команды известны и итог не записан.
    rounds_data = []
    if matches:
        mains = [m for m in matches if str(m["bracket"] or "main") == "main"]
        by_round = {}
        for m in mains:
            by_round.setdefault(int(m["round_no"]), []).append(m)
        for r in sorted(by_round):
            row_pairs = []
            for m in sorted(by_round[r], key=lambda x: int(x["slot_no"])):
                la = _team_name(m["team_a_id"], "ждёт")
                lb = _team_name(m["team_b_id"], "ждёт")
                win = None
                if m["winner_team_id"]:
                    wid = int(m["winner_team_id"])
                    if m["team_a_id"] and int(m["team_a_id"]) == wid:
                        win = 0
                    elif m["team_b_id"] and int(m["team_b_id"]) == wid:
                        win = 1
                live = (m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]
                        and not m["winner_team_id"])
                row_pairs.append((la, lb, win, bool(live)))
            rounds_data.append(row_pairs)
        while len(rounds_data) < total_rounds:
            idx = len(rounds_data)
            rounds_data.append([("ждёт", "ждёт", None, False)]
                               * max(1, size >> (idx + 1)))
    else:
        layout, _ = _bracket_layout(names)
        for rnd in layout:
            rounds_data.append([(a or "ждёт", b or "ждёт", None, False)
                                for (a, b) in rnd])
    if not rounds_data:
        return None

    third = None
    if matches:
        for _m in matches:
            if str(_m["bracket"] or "main") == "third":
                third = _m
                break

    # ── Пьедестал: 1/2 место из финала, 3/4 — из матча за 3-е место ──
    places = []
    if matches:
        mains = [m for m in matches if str(m["bracket"] or "main") == "main"]
        if mains:
            max_round = max(int(m["round_no"]) for m in mains)
            final_ms = [m for m in mains if int(m["round_no"]) == max_round]
            # Считается, только если последний раунд один и у него есть победитель
            final_done = [m for m in final_ms if m["winner_team_id"]]
            if len(final_ms) == 1 and final_done:
                fm = final_ms[0]
                places.append((1, _team_name(fm["winner_team_id"])))
                if fm["loser_team_id"]:
                    places.append((2, _team_name(fm["loser_team_id"])))
        if third and third["winner_team_id"]:
            places.append((3, _team_name(third["winner_team_id"])))
            if third["loser_team_id"]:
                places.append((4, _team_name(third["loser_team_id"])))
    podium_h = 140 if places else 0   # полоса пьедестала горизонтальная, высота фиксированная

    def _font(size_pt: int):
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size_pt)
            except Exception:
                continue
        return ImageFont.load_default()

    def _img_text(text: str) -> str:
        """Убирает символы без глифов в DejaVu (все эмодзи), чтобы нет «квадратов»."""
        out = []
        for ch in str(text):
            o = ord(ch)
            if (o < 0x7F or 0x0400 <= o <= 0x04FF          # латиница + кириллица
                    or 0x2010 <= o <= 0x205E               # тире, кавычки, многоточие
                    or 0x2190 <= o <= 0x21FF               # стрелки
                    or 0x2500 <= o <= 0x257F               # box drawing
                    or o in (0x00B7, 0x2022, 0x2713)):     # ·, •, ✓
                out.append(ch)
        return " ".join("".join(out).split())

    # ── геометрия ──
    SS = 2                       # supersampling: рисуем в 2× и сжимаем — гладкие края
    box_w, box_h = 284, 48
    gap_in = 12
    pair_gap = 30
    col_gap = 76
    x0 = 48
    header_h = 100
    y_head = header_h + podium_h  # сетка ниже, если рисуется пьедестал
    y_top = y_head + 26
    match_h = 2 * box_h + gap_in

    p_step = match_h + pair_gap
    ys_per_round: list[list[int]] = []
    for r, pairs_r in enumerate(rounds_data):
        ys_per_round.append([
            int(y_top + ((j - 0.5) * 2 ** r - 0.5) * p_step)
            for j in range(1, len(pairs_r) + 1)])
    grid_bottom = max(ys[-1] + match_h for ys in ys_per_round if ys)
    third_strip = 172 if third else 0
    width = x0 + total_rounds * (box_w + col_gap) + 26
    height = int(grid_bottom + 92 + third_strip)

    # ── палитра тёмной темы VGS (рассчитана поверх фотофона: линии/рамки ярче) ──
    BG = (13, 17, 30)
    CARD = (30, 37, 58)            # активный слот
    PANEL = (19, 24, 39)           # карточки и ожидающие слоты
    WON_BG = (22, 48, 37)          # победитель
    BORDER = (128, 144, 188)       # обводка боксов — не сливается с фоном
    BORDER_P = (86, 98, 132)       # обводка ожидающих слотов
    CONN = (158, 176, 216)         # соединители — заметно светлее
    DIVIDER = (80, 92, 126)
    GREEN = (53, 211, 127)
    GOLD = (255, 214, 92)
    TXT = (255, 255, 255)          # названия команд — чисто белые
    DIM = (148, 158, 188)
    DIMM = (108, 118, 150)

    F_TITLE = _font(30 * SS)
    F_HEAD = _font(15 * SS)
    F_BOX = _font(19 * SS)
    F_SMALL = _font(13 * SS)

    # ── Фон: арт (assets/bracket_bg.*) cover-кропом, мягкий blur, затемнение ──
    img = None
    for bg_path in ("assets/bracket_bg.png", "assets/bracket_bg.jpg",
                    "assets/bracket_bg.webp", "bracket_bg.png", "bracket_bg.jpg"):
        try:
            if not os.path.exists(bg_path):
                continue
            bg = Image.open(bg_path).convert("RGB")
            bw, bh = bg.size
            sc = max(width * SS / max(bw, 1), height * SS / max(bh, 1))
            bg = bg.resize((int(bw * sc + 0.5), int(bh * sc + 0.5)), Image.LANCZOS)
            left = (bg.width - width * SS) // 2
            top = (bg.height - height * SS) // 2
            bg = bg.crop((left, top, left + width * SS, top + height * SS))
            # слегка сглаживаем арт (убирает шум апскейла)…
            bg = bg.filter(ImageFilter.GaussianBlur(radius=1.6 * SS))
            # …и затемняем сильнее (0.74): фон остаётся декорацией, контент читается
            bg = Image.blend(bg, Image.new("RGB", bg.size, (9, 12, 24)), 0.74)
            img = bg
            break
        except Exception as e:
            logger.warning(f"Фоновая картинка сетки {bg_path} не загружена: {e}")
    if img is None:
        img = Image.new("RGB", (width * SS, height * SS), BG)
    draw = ImageDraw.Draw(img)

    def T(px, py, s, f, fill):
        draw.text((int(px * SS), int(py * SS)), s, font=f, fill=fill)

    def RR(px, py, pw, ph, r, fill, outline=None, wdt=1):
        draw.rounded_rectangle(
            [px * SS, py * SS, (px + pw) * SS, (py + ph) * SS],
            radius=r * SS, fill=fill, outline=outline,
            width=(wdt * SS if outline else 1))

    def LN(x1, y1, x2, y2, fill, wdt=2):
        draw.line([(int(x1 * SS), int(y1 * SS)), (int(x2 * SS), int(y2 * SS))],
                  fill=fill, width=wdt * SS)

    def DISC(px, py, d, fill):
        draw.ellipse([px * SS, py * SS, (px + d) * SS, (py + d) * SS], fill=fill)

    def TW(s, f):
        try:
            return draw.textlength(s, font=f) / SS
        except Exception:
            return len(str(s)) * 7

    def _fit(text: str, limit: int = 21) -> str:
        text = str(text)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    # ── заголовок: «Турнир #N» акцентом, остаток приглушён (без жёлтой плашки) ──
    t_parts = _img_text(title[:70]).split("·", 1)
    T(x0, 20, t_parts[0].strip(), F_TITLE, GOLD)
    if len(t_parts) > 1:
        T(x0 + TW(t_parts[0].strip(), F_TITLE) + 12, 20,
          "· " + t_parts[1].strip(), F_TITLE, DIM)
    LN(x0, 66, width - x0, 66, DIVIDER, 1)

    # ── Полоса «ПОБЕДИТЕЛИ»: медальные диски, место полностью, имя белым ──
    if places:
        T(x0, 84, "ПОБЕДИТЕЛИ", F_HEAD, GOLD)
        card_y = 108
        card_h = 86
        RR(x0, card_y, width - 2 * x0, card_h, 14, PANEL, BORDER_P)
        medal_fill = {1: GOLD, 2: (201, 210, 222), 3: (206, 128, 56)}
        cols = max(len(places), 1)
        col_w = max(230, (width - 2 * x0 - 24) // cols)
        for i, (place, pname) in enumerate(places):
            cx = x0 + 22 + i * col_w
            color = medal_fill.get(place, (150, 160, 185))
            d = 30
            DISC(cx, card_y + 28, d, color)
            num = str(place)
            T(cx + d / 2 - TW(num, F_BOX) / 2, card_y + 28 + d / 2 - 11, num, F_BOX, BG)
            T(cx + d + 14, card_y + 20, f"{place} МЕСТО", F_SMALL, DIMM)
            lim = max(8, int((col_w - d - 40) / 9.2))
            T(cx + d + 14, card_y + 44, _img_text(_fit(pname, lim)), F_BOX, TXT)

    def _box(px: int, py: int, label: str, style: str, won: bool):
        nonlocal img, draw
        if style == "won":
            fill, outline, tc, wdt = WON_BG, GREEN, TXT, 2
        elif style == "live":
            # играется сейчас: мягкое неоновое свечение + тонкая зелёная рамка
            fill, outline, tc, wdt = (24, 46, 37), GREEN, TXT, 3
        elif style in ("lost", "pending"):
            fill, outline, tc, wdt = PANEL, BORDER_P, DIMM, 1
        else:
            fill, outline, tc, wdt = CARD, BORDER, TXT, 1
        if style == "live":
            # свечение рисуется на отдельном слое и размывается — получаем плавную ару,
            # а не жёсткое пятно за боксом
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            gd.rounded_rectangle(
                [(px - 8) * SS, (py - 8) * SS, (px + box_w + 8) * SS, (py + box_h + 8) * SS],
                radius=20 * SS, fill=(53, 211, 127, 96))
            glow = glow.filter(ImageFilter.GaussianBlur(radius=7 * SS))
            img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
            draw = ImageDraw.Draw(img)
        RR(px, py, box_w, box_h, 13, fill, outline, wdt)
        max_chars = int((box_w - (64 if won else 28)) / 9.2)
        T(px + 16, py + (box_h - 21) // 2,
          _img_text(_fit(label, max_chars)), F_BOX, tc)
        if won:
            d = 18
            DISC(px + box_w - d - 13, py + (box_h - d) // 2, d, GREEN)
            T(px + box_w - 13 - d / 2 - TW("✓", F_BOX) / 2,
              py + box_h / 2 - 11, "✓", F_BOX, BG)

    # Текущий раунд — самый ранний, где есть ИГРАБЕЛЬНЫЙ матч (обе команды есть)
    current_round = None
    if matches:
        pend = [int(m["round_no"]) for m in matches
                if str(m["bracket"] or "main") == "main" and m["status"] == "pending"
                and m["team_a_id"] and m["team_b_id"] and not m["winner_team_id"]]
        if pend:
            current_round = min(pend)

    # ── колонки раундов: заголовки по центру колонки, соединители-уголки ──
    for r, pairs_r in enumerate(rounds_data):
        x = x0 + r * (box_w + col_gap)
        cap = _round_title(len(pairs_r), r, total_rounds, playin)
        is_current = (current_round == r + 1)
        cap_color = GREEN if is_current else DIMM
        cap_x = x + max(0, (box_w - TW(cap, F_HEAD)) / 2)
        T(cap_x, y_head, cap, F_HEAD, cap_color)
        if is_current:
            # зелёная плашка-подчёркивание у играющегося сейчас раунда
            LN(cap_x, y_head + 21, cap_x + TW(cap, F_HEAD), y_head + 21, GREEN, 2)
        ys_r = ys_per_round[r]
        for j, (la, lb, win, live) in enumerate(pairs_r):
            ya, yb = ys_r[j], ys_r[j] + box_h + gap_in
            for k, (lab, yy) in enumerate(((la, ya), (lb, yb))):
                if lab == "ждёт":
                    style, won = "pending", False
                elif win is not None and win == k:
                    style, won = "won", True
                elif win is not None:
                    style, won = "lost", False
                elif live:
                    style, won = "live", False
                else:
                    style, won = "idle", False
                _box(x, yy, lab, style, won)
            if r < total_rounds - 1 and j // 2 < len(ys_per_round[r + 1]):
                xm = x + box_w + col_gap // 2
                yc = ys_r[j] + match_h / 2
                tgt = ys_per_round[r + 1][j // 2] + match_h / 2
                LN(x + box_w, yc, xm, yc, CONN)
                LN(xm, min(yc, tgt), xm, max(yc, tgt), CONN)
                LN(xm, tgt, x + box_w + col_gap, tgt, CONN)

    # ── матч за 3-е место: отдельная панель с золотой полосой (отступы шире) ──
    if third:
        strip_y = grid_bottom + 32
        RR(x0, strip_y, min(width - 2 * x0, 2 * box_w + 96), 148, 14, PANEL, BORDER_P)
        RR(x0, strip_y, 6, 148, 3, GOLD)
        T(x0 + 34, strip_y + 20, "МАТЧ ЗА 3-Е МЕСТО", F_HEAD, GOLD)
        done = bool(third["winner_team_id"])
        live3 = (not done) and third["team_a_id"] and third["team_b_id"]
        for k, key in enumerate(("team_a_id", "team_b_id")):
            team = third[key]
            lab = _team_name(team) if team else "ждёт"
            won = bool(done) and team and int(third["winner_team_id"]) == int(team)
            if won:
                style = "won"
            elif live3:
                style = "live"
            else:
                style = "pending" if not team else "idle"
            _box(x0 + 34 + k * (box_w + 16), strip_y + 60, lab, style, bool(won))

    footer = (f"Команд: {n} · плей-ин: {playin_matches} матч. · "
              f"автопроходов сразу: {autos}")
    T(x0, height - 30, footer, F_SMALL, DIMM)
    wm = "VGStats bracket"
    T(width - x0 - TW(wm, F_SMALL), height - 30, wm, F_SMALL, (74, 82, 108))

    # сглаживание: сжимаем с 2× до целевого размера
    img = img.resize((width, height), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
