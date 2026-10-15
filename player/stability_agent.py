"""Registro persistente de eventos de estabilidade (sem coletar credenciais)."""
from __future__ import annotations
import json
import os
import threading
import time
import uuid
from pathlib import Path

class StabilityAgent:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / "stability_events.json"
        self.lock = threading.RLock()
        self.data = {"boot_id": "", "player_session": "", "pending": []}
        self.session_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.browser_starts_session = 0
        try:
            old = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                self.data.update({key: old[key] for key in ("boot_id", "player_session", "pending") if key in old})
        except (OSError, ValueError, TypeError):
            pass
        if not isinstance(self.data.get("pending"), list):
            self.data["pending"] = []
        self.data["pending"] = [x for x in self.data["pending"] if isinstance(x, dict) and x.get("id")][-2000:]
        try:
            self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            # Não fingir que temos detecção de reboot se o SO não oferece boot_id.
            self.boot_id = ""

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    def _event(self, kind, reason="", content="", severity="info"):
        item = {"id": uuid.uuid4().hex, "at": time.time(), "type": kind,
                "reason": str(reason or "")[:500], "content": str(content or "")[:300],
                "severity": severity}
        self.data["pending"].append(item)
        # 2000 eventos (~um histórico muito extenso); nunca enviar logs ilimitados.
        self.data["pending"] = self.data["pending"][-2000:]
        self._save()
        return item

    def register_start(self):
        with self.lock:
            last_boot = str(self.data.get("boot_id") or "")
            last_session = str(self.data.get("player_session") or "")
            if self.boot_id and last_boot and self.boot_id != last_boot:
                self._event("system_reboot", "Nova inicialização detectada pelo boot_id", severity="warning")
            elif self.boot_id and not last_boot:
                self._event("monitor_started", "Início da coleta; histórico anterior indisponível")
            elif last_session:
                self._event("player_restart", "Processo do Player iniciado novamente", severity="warning")
            else:
                self._event("monitor_started", "Início da coleta; histórico anterior indisponível")
            self.data["boot_id"] = self.boot_id
            self.data["player_session"] = self.session_id
            self._save()

    def record_browser_start(self, reason, content=""):
        with self.lock:
            self.browser_starts_session += 1
            if self.browser_starts_session == 1:
                self._event("browser_start", reason, content)
            else:
                self._event("browser_restart", reason, content,
                            "info" if str(reason).lower().startswith("remote:") else "warning")

    def pending(self, limit=40):
        with self.lock:
            return [dict(x) for x in self.data["pending"][:limit]]

    def acknowledge(self, ids):
        if not isinstance(ids, list):
            return
        known = {str(x) for x in ids if isinstance(x, str)}
        with self.lock:
            old = len(self.data["pending"])
            self.data["pending"] = [e for e in self.data["pending"] if e["id"] not in known]
            if len(self.data["pending"]) != old:
                self._save()

    def stats(self):
        with self.lock:
            return {"stability_boot_id": self.boot_id,
                    "stability_player_started_at": self.started_at,
                    "stability_pending_events": len(self.data["pending"]),
                    "stability_events": self.pending()}
