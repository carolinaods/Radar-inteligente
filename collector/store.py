"""Armazenamento persistente em JSON, deduplicação e histórico.

- Nunca apaga notícias automaticamente (a menos que retention_days > 0, e nesse
  caso apenas itens Média/Informativa; Crítica/Alta são preservadas).
- Escrita atômica: grava em arquivo temporário e renomeia; se algo falhar, o
  arquivo anterior permanece íntegro.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .util import canonical_url, iso, norm_title, parse_iso


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def content_hash(item: dict) -> str:
    base = f"{item['title']}|{item.get('summary', '')}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _is_official(item: dict) -> bool:
    return item.get("content_kind") == "Conteúdo oficial"


def merge(existing: list[dict], incoming: list[dict], now: datetime) -> tuple[list[dict], dict]:
    """Mescla itens novos na base. Retorna (base atualizada, estatísticas)."""
    by_id = {i["id"]: i for i in existing}
    by_title: dict[str, str] = {}
    for i in existing:
        by_title.setdefault(norm_title(i["title"]), i["id"])

    stats = {"new": 0, "duplicates": 0, "updated": 0}
    stamp = iso(now)

    for it in incoming:
        it["id"] = item_id(it["url"])
        it["canonical_url"] = canonical_url(it["url"])
        it["content_hash"] = content_hash(it)
        tkey = norm_title(it["title"])

        # 1) mesmo URL canônico
        cur = by_id.get(it["id"])
        if cur is not None:
            cur["last_seen"] = stamp
            if cur["content_hash"] != it["content_hash"]:
                # atualização substancial: preserva histórico da versão anterior
                cur.setdefault("history", []).append(
                    {"at": stamp, "change": "Conteúdo atualizado na fonte", "previous_title": cur["title"]}
                )
                for k in ("title", "summary", "content_hash", "relevance", "action_level",
                          "impact_ai", "action_ai", "nature", "deadlines", "rt_status",
                          "tags", "categories", "sap_modules", "confidence"):
                    cur[k] = it[k]
                cur["updated_at"] = stamp
                stats["updated"] += 1
            else:
                stats["duplicates"] += 1
            continue

        # 2) mesmo título em outra fonte/URL => fonte complementar
        other_id = by_title.get(tkey) if tkey else None
        if other_id and other_id in by_id:
            cur = by_id[other_id]
            cur.setdefault("also_in", [])
            if not any(a["url"] == it["url"] for a in cur["also_in"]):
                cur["also_in"].append({"source_name": it["source_name"], "url": it["url"]})
            cur["last_seen"] = stamp
            stats["duplicates"] += 1
            continue

        # 3) novo
        it["first_seen"] = stamp
        it["last_seen"] = stamp
        it["updated_at"] = None
        it["also_in"] = []
        it["history"] = []
        by_id[it["id"]] = it
        by_title.setdefault(tkey, it["id"])
        stats["new"] += 1

    return list(by_id.values()), stats


def apply_retention(items: list[dict], retention_days: int, now: datetime) -> list[dict]:
    if not retention_days or retention_days <= 0:
        return items
    limit = now - timedelta(days=retention_days)
    kept = []
    for i in items:
        ref = parse_iso(i.get("published")) or parse_iso(i.get("first_seen")) or now
        if ref < limit and i["relevance"]["level"] in ("Média", "Informativa"):
            continue
        kept.append(i)
    return kept
