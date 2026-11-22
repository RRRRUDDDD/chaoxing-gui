"""Hybrid Tiku adapter integrating AI (Local SQLite + AI) with existing TikuOcs.

Supports user-selected dual-bank cooperation order:
- local_ocs_ai: Local SQLite -> External OCS -> Built-in AI
- local_ai_ocs: Local SQLite -> Built-in AI -> External OCS
- local_ai_only: Local SQLite -> Built-in AI
- ocs_only: External OCS only (still auto-saves new answers to local SQLite if enabled)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from api.answer import Tiku, _has_ocs_tiku_config
from api.answer_check import check_answer, match_answer
from api.logger import logger
from api.ocs_tiku import TikuOcs
from api.ai.config import VALID_PRIORITIES


class TikuHybrid(Tiku):
    """Dual-bank Tiku coordinating local SQLite bank, AI engine, and external OCS Tiku."""

    def __init__(self, *, ai_engine=None, config_store=None) -> None:
        super().__init__()
        self.name = "AI 协同题库"
        self._engine = ai_engine
        self._config_store = config_store
        self._ocs: Optional[TikuOcs] = None
        self._priority: str = "local_ocs_ai"
        self._ai_enabled: bool = False
        self._auto_save: bool = True
        self._default_folder_id: int = 0

    def _get_engine(self):
        if self._engine is None:
            from api.ai import get_ai_engine
            self._engine = get_ai_engine()
        return self._engine

    def _get_config_store(self):
        if self._config_store is None:
            from api.ai import get_ai_config
            self._config_store = get_ai_config()
        return self._config_store

    def _init_tiku(self):
        conf = self._conf or {}
        ai_cfg = self._get_config_store().load()
        ai_engine_cfg = ai_cfg.get("engine", {})

        # Allow task-level tiku_config overrides, fallback to AI global engine config
        if "ai_enabled" in conf:
            self._ai_enabled = str(conf.get("ai_enabled")).strip().lower() in ("true", "1", "yes")
        else:
            self._ai_enabled = bool(ai_engine_cfg.get("enabled", False))

        raw_priority = str(conf.get("ai_priority") or ai_engine_cfg.get("priority") or "local_ocs_ai").strip()
        self._priority = raw_priority if raw_priority in VALID_PRIORITIES else "local_ocs_ai"
        self._auto_save = bool(ai_engine_cfg.get("autoSaveToBank", True))
        self._default_folder_id = int(ai_engine_cfg.get("defaultFolderId", 0))

        has_ocs = _has_ocs_tiku_config(conf)
        if has_ocs and self._priority != "local_ai_only":
            ocs = TikuOcs()
            ocs.config_set(conf)
            ocs.init_tiku()
            if not ocs.DISABLE:
                self._ocs = ocs

        if not self._ai_enabled and self._ocs is None:
            self.DISABLE = True
            logger.info("AI 与外部 OCS 题库均未启用，已停用题库功能")

    def _validate_candidate(self, answer: str, q_info: Dict[str, Any]) -> Optional[str]:
        cleaned = str(answer or "").strip()
        if not cleaned:
            return None
        if q_info.get("options") or q_info.get("type") == "completion":
            matched = match_answer(cleaned, q_info, self.true_list, self.false_list)
            if matched.answer is not None:
                return cleaned
            return None
        if check_answer(cleaned, q_info.get("type", ""), self):
            return cleaned
        return None

    def _save_to_local_bank(self, q_info: Dict[str, Any], answer: str, *, is_ai: bool, model_name: str) -> None:
        if not self._auto_save:
            return
        try:
            self._get_engine().db.upsert_cached_answer(
                question=str(q_info.get("title") or ""),
                options=q_info.get("options") or "",
                question_type=str(q_info.get("type") or ""),
                answer=answer,
                model_name=model_name,
                folder_id=self._default_folder_id,
                is_ai=is_ai,
            )
        except Exception as exc:
            logger.debug("写入 AI 本地题库失败: {}", exc)

    def _try_ocs(self, q_info: Dict[str, Any]) -> Optional[str]:
        if self._ocs is None:
            return None
        ans = self._ocs._query(q_info)
        if self._ocs.query_diagnostics:
            self.query_diagnostics.extend(self._ocs.query_diagnostics)
        if not ans:
            return None
        valid = self._validate_candidate(ans, q_info)
        if valid:
            self._save_to_local_bank(q_info, valid, is_ai=False, model_name="外部 OCS 题库")
            return valid
        return None

    def _try_ai(self, q_info: Dict[str, Any]) -> Optional[str]:
        if not self._ai_enabled:
            return None
        res = self._get_engine().answer_question(
            q_info,
            allow_local=False,
            allow_ai=True,
            record_log=True,
            request_path="/chaoxing/study",
            request_method="INTERNAL",
        )
        self.query_diagnostics.append(
            {
                "source": "ai_engine",
                "status": "selected" if res.get("ok") else "failed",
                "model": res.get("modelName") or "",
                "error": res.get("error") or "",
            }
        )
        if res.get("ok") and res.get("answer"):
            return str(res["answer"]).strip()
        return None

    def _query(self, q_info: Dict[str, Any]) -> Optional[str]:
        engine = self._get_engine()

        # Step 1: Always check local SQLite bank first (unless ocs_only without ai)
        if self._ai_enabled or self._priority != "ocs_only":
            matched, local_status = engine.lookup_local(q_info, allow_same_check=self._ai_enabled)
            if matched and matched.get("answer"):
                valid = self._validate_candidate(matched["answer"], q_info)
                if valid:
                    self.query_diagnostics.append({"source": f"ai_{local_status}", "status": "selected"})
                    logger.info("AI 本地题库命中 ({}): {} -> {}", local_status, q_info.get("title"), valid)
                    return valid

        # Step 2: Execute configured cooperation order
        if self._priority == "local_ai_ocs":
            ans = self._try_ai(q_info)
            if ans:
                return ans
            return self._try_ocs(q_info)

        if self._priority == "local_ai_only":
            return self._try_ai(q_info)

        if self._priority == "ocs_only":
            return self._try_ocs(q_info)

        # Default: local_ocs_ai
        ans = self._try_ocs(q_info)
        if ans:
            return ans
        return self._try_ai(q_info)

    def close(self) -> bool:
        ocs_ok = True
        if self._ocs is not None:
            try:
                ocs_ok = self._ocs.close()
            except Exception as exc:
                logger.warning("关闭外部 OCS 题库失败: {}", exc)
                ocs_ok = False
        base_ok = super().close()
        return ocs_ok and base_ok
