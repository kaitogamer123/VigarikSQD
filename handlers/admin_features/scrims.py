"""Мониторинг незакрытых скримов."""
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd
from database import get_member, get_setting
from utils.permissions import is_any_admin, has_role
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
)
from utils.admin_logger import log_admin_action
from utils.notify_settings import is_muted
from league import league_db as db
from league.competitive import SCRIM_TYPE_LABEL, _normalise_scheduled
from .common import _cb_is_admin, _split_text

logger = logging.getLogger(__name__)
router = Router()


# ─── МОНИТОРИНГ НЕЗАКРЫТЫХ СКРИМОВ (Этап 1 ТЗ, п.3) ─────────────────────────
SCRIM_STATUS_LABEL = {
    "searching": "🔍 Поиск соперника (рандомный скрим)",
    "invited": "📨 Приглашение отправлено, ждём ответа",
    "scheduled": "📅 Игра назначена, счёт ещё не отправлен",
    "awaiting_scores": "⏳ Ожидаем подтверждения счёта",
}


def _get_all_active_scrims():
    """Все незакрытые скримы (status IN SCRIM_ACTIVE_STATUSES), свежие сверху."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM scrims WHERE status IN (?, ?, ?, ?) ORDER BY created_at DESC",
        tuple(db.SCRIM_ACTIVE_STATUSES),
    ).fetchall()
    conn.close()
    return rows


def _side_report_line(scrim, side: str) -> str:
    """Строка по стороне скрима: отправлен ли итоговый счёт и сколько скриншотов."""
    if side == "challenger":
        name = scrim["challenger_name"] or f"#{scrim['challenger_league_id']}"
        score_a, score_b = scrim["challenger_score_a"], scrim["challenger_score_b"]
    else:
        name = scrim["opponent_name"] or "соперник"
        score_a, score_b = scrim["opponent_score_a"], scrim["opponent_score_b"]
    if score_a is None or score_b is None:
        score_txt = "❌ итоговый счёт не отправлен"
    else:
        score_txt = f"✅ счёт {int(score_a)}:{int(score_b)}"
    shots = db.get_scrim_shots_count(int(scrim["id"]), side)
    return f"• {hd.quote(str(name))}: {score_txt}; 📸 скриншотов: {int(shots)}"


def _monitor_text(rows) -> str:
    """Текст мониторинга: по каждому незакрытому скриму видно, кто не оставил счёт."""
    if not rows:
        return (
            "👀 <b>Незакрытые скримы</b>\n\n"
            "✅ Сейчас нет открытых/незакрытых скримов — все игры завершены, отменены или сгорели."
        )
    lines = ["👀 <b>Незакрытые скримы</b>\n"]
    for s in rows:
        label = SCRIM_TYPE_LABEL.get(s["scrim_type"], s["scrim_type"])
        status = SCRIM_STATUS_LABEL.get(s["status"], s["status"])
        created = str(s["created_at"] or "")[:16]
        opp_name = s["opponent_name"] or "поиск..."
        opp_tag = s["opponent_tag"] or "?"
        lines.append(
            f"<b>#{s['id']}</b> {label} · {hd.quote(str(s['format']))} · {hd.quote(str(s['scheduled_text'] or '—'))}\n"
            f"👑 {hd.quote(str(s['challenger_name'] or '?'))} [{hd.quote(str(s['challenger_tag'] or '?'))}] 🆚 "
            f"{hd.quote(str(opp_name))} [{hd.quote(str(opp_tag))}]\n"
            f"Статус: {hd.quote(str(status))} (создан {hd.quote(created)})"
        )
        if s["status"] in ("scheduled", "awaiting_scores"):
            lines.append(_side_report_line(s, "challenger"))
            if s["opponent_league_id"]:
                lines.append(_side_report_line(s, "opponent"))
            else:
                lines.append("• соперник ещё не найден")
        elif s["status"] == "searching":
            lines.append("• ⏳ Соперник подбирается автоматически (рандомный скрим)")
        else:
            lines.append("• ⏳ Приглашение ожидает ответа соперника")
        lines.append("")
    lines.append(
        "Чтобы <b>принудительно закрыть</b> скрим (без зачёта результата и без смены MMR), "
        "нажми «🚫 Закрыть» под нужной карточкой."
    )
    return "\n".join(lines)


def _monitor_keyboard(rows) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in rows[:15]:
        builder.button(text=f"🚫 Закрыть #{s['id']}", callback_data=f"adm_scrim:close:{s['id']}")
    builder.button(text="🔄 Обновить", callback_data="adm_scrim:refresh")
    builder.button(text="◀️ Назад", callback_data="adm_scrim:back")
    builder.adjust(1)
    return builder.as_markup()


async def _refresh_monitor(call: CallbackQuery):
    """Обновляет сообщение мониторинга (edit_text, при неудаче — удалить и отправить заново)."""
    rows = _get_all_active_scrims()
    text = _monitor_text(rows)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_monitor_keyboard(rows))
        return
    except Exception:
        pass
    try:
        await call.message.delete()
    except Exception:
        pass
    parts = _split_text(text)
    for part in parts[:-1]:
        await call.message.answer(part, parse_mode="HTML")
    await call.message.answer(parts[-1], parse_mode="HTML", reply_markup=_monitor_keyboard(rows))


@router.message(F.text == "👀 Незакрытые скримы")
async def admin_open_scrims_monitor(message: Message):
    """Этап 1 ТЗ, п.3: отдельная кнопка в разделе «Скримы» со списком незакрытых скримов."""
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ только для администрации.")
        return
    rows = _get_all_active_scrims()
    parts = _split_text(_monitor_text(rows))
    for part in parts[:-1]:
        await message.answer(part, parse_mode="HTML")
    await message.answer(parts[-1], parse_mode="HTML", reply_markup=_monitor_keyboard(rows))


@router.callback_query(F.data.startswith("adm_scrim:close_yes:"))
async def admin_scrim_close_yes(call: CallbackQuery, bot: Bot):
    """Принудительное закрытие скрима администратором (с подтверждением)."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = db.get_scrim(scrim_id)
    if not scrim or scrim["status"] not in db.SCRIM_ACTIVE_STATUSES:
        await call.answer("Скрим уже закрыт", show_alert=True)
        return
    db.set_scrim_status(scrim_id, "cancelled")
    admin_name = call.from_user.username or call.from_user.first_name or str(call.from_user.id)
    opp_name = scrim["opponent_name"] or "соперник не найден"
    notify_text = (
        f"🚫 Администратор {hd.quote(str(admin_name))} принудительно закрыл скрим #{scrim_id} "
        f"({hd.quote(str(scrim['challenger_name'] or '?'))} 🆚 {hd.quote(str(opp_name))}).\n"
        f"Результат не засчитан, MMR не изменился."
    )
    for leader_id in {int(scrim["challenger_leader_id"] or 0), int(scrim["opponent_leader_id"] or 0)}:
        if leader_id and not await is_muted(leader_id, "scrims"):
            try:
                await bot.send_message(leader_id, notify_text, parse_mode="HTML")
            except Exception:
                pass
    await log_admin_action(
        bot=bot,
        admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=(
            f"🚫 Принудительно закрыл незакрытый скрим #{scrim_id} "
            f"({scrim['challenger_name'] or '?'} vs {opp_name}). Статус: cancelled, MMR не менялся."
        ),
    )
    await call.message.edit_text(
        f"🚫 Скрим #{scrim_id} принудительно закрыт администратором {hd.quote(str(admin_name))}.\n"
        f"Статус: <b>cancelled</b>. Результат не засчитан, MMR не изменился. Лидерам отправлены уведомления.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="adm_scrim:refresh"),
            InlineKeyboardButton(text="◀️ Назад", callback_data="adm_scrim:back"),
        ]]),
    )
    await call.answer("Скрим закрыт")


@router.callback_query(F.data == "adm_scrim:close_no")
async def admin_scrim_close_no(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await _refresh_monitor(call)
    await call.answer("Отменено")


@router.callback_query(F.data == "adm_scrim:refresh")
async def admin_scrim_refresh(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await _refresh_monitor(call)
    await call.answer("Обновлено")


@router.callback_query(F.data == "adm_scrim:back")
async def admin_scrim_back(call: CallbackQuery):
    """Возврат из мониторинга в папку «Скримы»."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        "📂 Скримы и турниры \n\n"
        "Выбери необходимое действие:",
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard()
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_scrim:close:"))
async def admin_scrim_close_ask(call: CallbackQuery):
    """Запрос подтверждения перед принудительным закрытием скрима."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = db.get_scrim(scrim_id)
    if not scrim or scrim["status"] not in db.SCRIM_ACTIVE_STATUSES:
        await call.answer("Скрим уже закрыт", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Да, закрыть #{scrim_id}", callback_data=f"adm_scrim:close_yes:{scrim_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm_scrim:close_no"),
    ]]))
    await call.answer("Подтвердите принудительное закрытие")
