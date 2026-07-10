"""
services/media.py — Shared media extraction helpers for whisper + reply creation.

Extracts (content, message_type, file_id, caption, location_lat, location_lon)
from a Telegram Message, storing Telegram file_ids directly (no downloads).
"""

import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_WHISPER_MEDIA = {
    "photo", "video", "voice", "audio", "document", "location", "animation",
}


def extract_media_from_message(msg) -> dict:
    """
    Extract media metadata from a Telegram Message.

    Returns a dict with keys:
        content       — text or caption (may be empty)
        message_type  — None for text-only, or one of SUPPORTED_WHISPER_MEDIA
        file_id       — Telegram file_id (or JSON for location)
        caption       — alias for content when media is present
        location_lat  — latitude for location type
        location_lon  — longitude for location type
    """
    result = {
        "content": "",
        "message_type": None,
        "file_id": None,
        "caption": None,
        "location_lat": None,
        "location_lon": None,
    }

    ct = msg.content_type

    if ct == "text":
        result["content"] = (msg.text or "").strip()
        return result

    if ct == "photo":
        result["message_type"] = "photo"
        result["file_id"] = msg.photo[-1].file_id
        result["content"] = (msg.caption or "").strip()
        result["caption"] = result["content"]
        return result

    if ct == "video":
        result["message_type"] = "video"
        result["file_id"] = msg.video.file_id
        result["content"] = (msg.caption or "").strip()
        result["caption"] = result["content"]
        return result

    if ct == "voice":
        result["message_type"] = "voice"
        result["file_id"] = msg.voice.file_id
        result["content"] = (msg.caption or "").strip()
        result["caption"] = result["content"]
        return result

    if ct == "audio":
        result["message_type"] = "audio"
        result["file_id"] = msg.audio.file_id
        result["content"] = (msg.caption or "").strip()
        result["caption"] = result["content"]
        return result

    if ct == "document":
        result["message_type"] = "document"
        result["file_id"] = msg.document.file_id
        result["content"] = (msg.caption or "").strip()
        result["caption"] = result["content"]
        return result

    if ct == "location":
        result["message_type"] = "location"
        loc = msg.location
        result["location_lat"] = loc.latitude
        result["location_lon"] = loc.longitude
        result["file_id"] = json.dumps({
            "latitude": loc.latitude,
            "longitude": loc.longitude,
        })
        return result

    if ct == "animation":
        result["message_type"] = "animation"
        result["file_id"] = msg.animation.file_id
        result["content"] = (msg.caption or "").strip()
        result["caption"] = result["content"]
        return result

    return result


def send_media_message(bot, chat_id: int, media_data: dict,
                       text: str = None, reply_markup=None,
                       parse_mode: str = None) -> bool:
    """
    Send a whisper's media content to a chat.

    Args:
        bot: TeleBot instance
        chat_id: target chat
        media_data: dict with message_type, file_id, caption, location_lat, location_lon
        text: optional text to include (used as caption for media, or standalone for text)
        reply_markup: optional inline keyboard
        parse_mode: optional parse mode

    Returns True if sent successfully.
    """
    mt = media_data.get("message_type")
    fid = media_data.get("file_id")
    caption = media_data.get("caption") or text or ""

    try:
        if mt == "photo":
            bot.send_photo(chat_id, fid, caption=caption[:1024],
                           parse_mode=parse_mode, reply_markup=reply_markup)
            return True

        if mt == "video":
            bot.send_video(chat_id, fid, caption=caption[:1024],
                           parse_mode=parse_mode, reply_markup=reply_markup)
            return True

        if mt == "voice":
            bot.send_voice(chat_id, fid, reply_markup=reply_markup)
            if caption:
                bot.send_message(chat_id, caption, parse_mode=parse_mode,
                                 reply_markup=None)
            return True

        if mt == "audio":
            bot.send_audio(chat_id, fid, caption=caption[:1024],
                           parse_mode=parse_mode, reply_markup=reply_markup)
            if not caption and text:
                bot.send_message(chat_id, text, parse_mode=parse_mode,
                                 reply_markup=reply_markup)
            return True

        if mt == "document":
            bot.send_document(chat_id, fid, caption=caption[:1024],
                              parse_mode=parse_mode, reply_markup=reply_markup)
            return True

        if mt == "location":
            lat = media_data.get("location_lat")
            lon = media_data.get("location_lon")
            if lat is not None and lon is not None:
                bot.send_location(chat_id, latitude=lat, longitude=lon,
                                  reply_markup=reply_markup)
            if caption:
                bot.send_message(chat_id, caption, parse_mode=parse_mode,
                                 reply_markup=None)
            return True

        if mt == "animation":
            bot.send_animation(chat_id, fid, caption=caption[:1024],
                               parse_mode=parse_mode, reply_markup=reply_markup)
            return True

        # Text-only whisper
        full_text = caption or text or ""
        if full_text:
            bot.send_message(chat_id, full_text[:4096],
                             parse_mode=parse_mode, reply_markup=reply_markup)
        return True

    except Exception as exc:
        logger.error(f"send_media_message failed: {exc}")
        return False
