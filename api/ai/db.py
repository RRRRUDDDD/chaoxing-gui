"""SQLite storage for AI question bank, practice, study graph, and request logs."""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from api.paths import data_dir
from api.ai.forgetting import (
    clamp_forgetting_stage,
    clamp_mastery,
    effective_mastery,
    next_forgetting_stage,
    next_review_due_iso,
    parse_study_quality,
    retention_ratio,
    reviewed_at_for_quality,
    utc_now,
    utc_now_iso,
)


def normalize_question_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", str(text or ""))
    cleaned = re.sub(r"^\s*(?:【[^】]+】|\[\s*[^\]]+\s*\]|\d+\s*[.、．)）])\s*", "", cleaned)
    cleaned = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[)）]\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_for_match(text: str) -> str:
    base = normalize_question_text(text).lower()
    return re.sub(r"[\s\W_]+", "", base, flags=re.UNICODE)


def _tokenize_for_recall(text: str) -> List[str]:
    cleaned = normalize_question_text(text).lower()
    # Extract CJK bigrams and alphanumeric words for fast SQL LIKE candidate recall.
    words = re.findall(r"[a-z0-9]{2,}", cleaned)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", cleaned)
    tokens: List[str] = []
    for w in words:
        if w not in tokens:
            tokens.append(w)
    for run in cjk_runs:
        if len(run) <= 4:
            if run not in tokens:
                tokens.append(run)
        else:
            step = max(2, len(run) // 4)
            for idx in range(0, len(run) - 1, step):
                chunk = run[idx : idx + 3]
                if len(chunk) >= 2 and chunk not in tokens:
                    tokens.append(chunk)
    return tokens[:8]


def _normalize_options_str(options: Any) -> str:
    if options is None:
        return ""
    if isinstance(options, list):
        return "\n".join(str(x).strip() for x in options if str(x).strip())
    return str(options).strip()


class AIDB:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.path.join(data_dir(), "ai.db")
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self):
        with self._lock:
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS Folders (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  Name TEXT NOT NULL,
                  ParentId INTEGER DEFAULT 0,
                  CreateTime TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS AIResponses (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  Question TEXT NOT NULL,
                  Options TEXT DEFAULT '',
                  QuestionType TEXT DEFAULT '',
                  Answer TEXT NOT NULL,
                  Explanation TEXT DEFAULT '',
                  ModelName TEXT DEFAULT '',
                  CreateTime TEXT DEFAULT (datetime('now')),
                  FolderId INTEGER DEFAULT 0,
                  FolderName TEXT DEFAULT '默认文件夹',
                  IsAi INTEGER DEFAULT 1,
                  IsPendingCorrection INTEGER DEFAULT 0,
                  Importance INTEGER DEFAULT 0,
                  Mastery INTEGER DEFAULT 0,
                  Difficulty INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_ai_responses_folder ON AIResponses (FolderId, Id DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_responses_pending ON AIResponses (IsPendingCorrection, Id DESC);

                CREATE TABLE IF NOT EXISTS QuestionPracticeHistory (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  QuestionId INTEGER NOT NULL,
                  UserAnswer TEXT NOT NULL DEFAULT '',
                  IsCorrect INTEGER DEFAULT 0,
                  Note TEXT DEFAULT '',
                  Source TEXT DEFAULT 'practice',
                  CreateTime TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_practice_question ON QuestionPracticeHistory (QuestionId, Id DESC);

                CREATE TABLE IF NOT EXISTS StudySubjects (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  Name TEXT NOT NULL,
                  Description TEXT DEFAULT '',
                  CreateTime TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS StudyGraphNodes (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  SubjectId INTEGER NOT NULL,
                  NodeKey TEXT NOT NULL,
                  Name TEXT NOT NULL,
                  Summary TEXT DEFAULT '',
                  Mastery INTEGER DEFAULT 0,
                  ParentId INTEGER,
                  SortOrder INTEGER DEFAULT 0,
                  ForgettingStage INTEGER DEFAULT 0,
                  LastReviewedAt TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_study_nodes_subject ON StudyGraphNodes (SubjectId, SortOrder, Id);

                CREATE TABLE IF NOT EXISTS StudyGraphEdges (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  SubjectId INTEGER NOT NULL,
                  FromId INTEGER NOT NULL,
                  ToId INTEGER NOT NULL,
                  Relation TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_study_edges_subject ON StudyGraphEdges (SubjectId);

                CREATE TABLE IF NOT EXISTS QuestionKnowledgeNodes (
                  QuestionId INTEGER NOT NULL,
                  NodeId INTEGER NOT NULL,
                  PRIMARY KEY (QuestionId, NodeId)
                );
                CREATE INDEX IF NOT EXISTS idx_qkn_node ON QuestionKnowledgeNodes (NodeId);

                CREATE TABLE IF NOT EXISTS StudyActivity (
                  Id INTEGER PRIMARY KEY AUTOINCREMENT,
                  SubjectId INTEGER NOT NULL,
                  Kind TEXT NOT NULL,
                  Names TEXT DEFAULT '[]',
                  QuestionCount INTEGER DEFAULT 0,
                  CorrectCount INTEGER DEFAULT 0,
                  CreateTime TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_study_activity_subject ON StudyActivity (SubjectId, CreateTime DESC, Id DESC);

                CREATE TABLE IF NOT EXISTS RequestLogs (
                  LogId INTEGER PRIMARY KEY AUTOINCREMENT,
                  RequestId TEXT NOT NULL,
                  Timestamp TEXT NOT NULL,
                  Method TEXT NOT NULL,
                  Path TEXT NOT NULL,
                  Status INTEGER DEFAULT 200,
                  ResponseTime INTEGER DEFAULT 0,
                  RequestBody TEXT DEFAULT '',
                  ResponseBody TEXT DEFAULT '',
                  Stage TEXT NOT NULL DEFAULT 'completed',
                  LocalSearch TEXT DEFAULT '',
                  ModelName TEXT DEFAULT '',
                  Reasoning TEXT DEFAULT '',
                  SearchTraces TEXT DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_request_logs_req ON RequestLogs (RequestId);

                CREATE TABLE IF NOT EXISTS DailyRequestCounts (
                  Day TEXT PRIMARY KEY,
                  Count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS AgentSessions (
                  Id TEXT PRIMARY KEY,
                  Title TEXT NOT NULL,
                  MessagesJson TEXT NOT NULL DEFAULT '[]',
                  UpdatedAt TEXT NOT NULL,
                  CreateTime TEXT NOT NULL
                );
                """
            )
            exists = conn.execute("SELECT COUNT(1) FROM Folders WHERE Id = 0").fetchone()[0]
            if not exists:
                conn.execute("INSERT INTO Folders (Id, Name, ParentId) VALUES (0, '默认文件夹', 0)")
            else:
                conn.execute("UPDATE Folders SET ParentId = 0 WHERE Id = 0 AND ParentId != 0")

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------
    def list_folders(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.Id, f.Name, f.ParentId, f.CreateTime,
                       (SELECT COUNT(1) FROM AIResponses q WHERE q.FolderId = f.Id) AS QuestionCount
                FROM Folders f
                ORDER BY CASE WHEN f.Id = 0 THEN 0 ELSE 1 END, f.Id ASC
                """
            ).fetchall()
            return [
                {
                    "id": int(r["Id"]),
                    "name": r["Name"],
                    "parentId": int(r["ParentId"] or 0),
                    "createTime": r["CreateTime"],
                    "questionCount": int(r["QuestionCount"] or 0),
                }
                for r in rows
            ]

    def create_folder(self, name: str, parent_id: int = 0) -> Dict[str, Any]:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("文件夹名称不能为空")
        pid = max(0, int(parent_id or 0))
        with self._connect() as conn:
            if pid != 0:
                exists = conn.execute("SELECT 1 FROM Folders WHERE Id = ?", (pid,)).fetchone()
                if not exists:
                    pid = 0
            cur = conn.execute(
                "INSERT INTO Folders (Name, ParentId, CreateTime) VALUES (?, ?, ?)",
                (cleaned, pid, utc_now_iso()),
            )
            fid = int(cur.lastrowid)
            return {"id": fid, "name": cleaned, "parentId": pid, "questionCount": 0}

    def rename_folder(self, folder_id: int, name: str) -> Dict[str, Any]:
        fid = int(folder_id)
        if fid == 0:
            raise ValueError("默认文件夹不可重命名")
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("文件夹名称不能为空")
        with self._connect() as conn:
            row = conn.execute("SELECT ParentId FROM Folders WHERE Id = ?", (fid,)).fetchone()
            if not row:
                raise ValueError("文件夹不存在")
            conn.execute("UPDATE Folders SET Name = ? WHERE Id = ?", (cleaned, fid))
            conn.execute("UPDATE AIResponses SET FolderName = ? WHERE FolderId = ?", (cleaned, fid))
            return {"id": fid, "name": cleaned, "parentId": int(row["ParentId"] or 0)}

    def move_folder(self, folder_id: int, target_parent_id: int) -> None:
        fid = int(folder_id)
        target = max(0, int(target_parent_id or 0))
        if fid == 0:
            raise ValueError("默认文件夹不可移动")
        if fid == target:
            raise ValueError("不能移动到自身")
        with self._connect() as conn:
            all_rows = conn.execute("SELECT Id, ParentId FROM Folders").fetchall()
            parents = {int(r["Id"]): int(r["ParentId"] or 0) for r in all_rows}
            if fid not in parents:
                raise ValueError("文件夹不存在")
            if target != 0 and target not in parents:
                raise ValueError("目标父文件夹不存在")
            # Cycle detection
            cursor = target
            visited = {fid}
            while cursor != 0:
                if cursor in visited:
                    raise ValueError("不能将文件夹移动到其子文件夹下")
                visited.add(cursor)
                cursor = parents.get(cursor, 0)
            conn.execute("UPDATE Folders SET ParentId = ? WHERE Id = ?", (target, fid))

    def delete_folder(self, folder_id: int, *, delete_questions: bool = False) -> None:
        fid = int(folder_id)
        if fid == 0:
            raise ValueError("默认文件夹不可删除")
        with self._connect() as conn:
            all_rows = conn.execute("SELECT Id, ParentId FROM Folders").fetchall()
            children_map: Dict[int, List[int]] = {}
            for r in all_rows:
                children_map.setdefault(int(r["ParentId"] or 0), []).append(int(r["Id"]))
            to_delete: List[int] = []
            stack = [fid]
            seen = set()
            while stack:
                cur = stack.pop()
                if cur in seen or cur == 0:
                    continue
                seen.add(cur)
                to_delete.append(cur)
                for child in children_map.get(cur, []):
                    if child not in seen:
                        stack.append(child)
            if not to_delete:
                return
            placeholders = ",".join("?" for _ in to_delete)
            if delete_questions:
                conn.execute(f"DELETE FROM AIResponses WHERE FolderId IN ({placeholders})", to_delete)
            else:
                conn.execute(
                    f"UPDATE AIResponses SET FolderId = 0, FolderName = '默认文件夹' WHERE FolderId IN ({placeholders})",
                    to_delete,
                )
            conn.execute(f"DELETE FROM Folders WHERE Id IN ({placeholders})", to_delete)

    def clear_folder(self, folder_id: int) -> int:
        fid = int(folder_id)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM AIResponses WHERE FolderId = ?", (fid,))
            return int(cur.rowcount or 0)

    # ------------------------------------------------------------------
    # Questions (AIResponses)
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_question(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": int(row["Id"]),
            "question": row["Question"],
            "options": row["Options"] or "",
            "questionType": row["QuestionType"] or "",
            "answer": row["Answer"] or "",
            "explanation": row["Explanation"] or "",
            "modelName": row["ModelName"] or "",
            "createTime": row["CreateTime"] or "",
            "folderId": int(row["FolderId"] or 0),
            "folderName": row["FolderName"] or "默认文件夹",
            "isAi": bool(row["IsAi"]),
            "isPendingCorrection": bool(row["IsPendingCorrection"]),
            "importance": int(row["Importance"] or 0),
            "mastery": int(row["Mastery"] or 0),
            "difficulty": int(row["Difficulty"] or 0),
        }

    def _folder_name(self, conn: sqlite3.Connection, folder_id: int) -> Tuple[int, str]:
        fid = max(0, int(folder_id or 0))
        row = conn.execute("SELECT Id, Name FROM Folders WHERE Id = ?", (fid,)).fetchone()
        if row:
            return int(row["Id"]), str(row["Name"])
        return 0, "默认文件夹"

    def save_question(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        qid = payload.get("id")
        question = str(payload.get("question") or payload.get("title") or "").strip()
        answer = str(payload.get("answer") or "").strip()
        if not question:
            raise ValueError("题目内容不能为空")
        if not answer:
            raise ValueError("答案不能为空")
        options = _normalize_options_str(payload.get("options"))
        qtype = str(payload.get("questionType") or payload.get("type") or "").strip()
        explanation = str(payload.get("explanation") or "").strip()
        model_name = str(payload.get("modelName") or "").strip()
        is_ai = 1 if payload.get("isAi", False) else 0
        is_pending = 1 if payload.get("isPendingCorrection", False) else 0
        importance = max(0, min(5, int(payload.get("importance") or 0)))
        mastery = clamp_mastery(payload.get("mastery") or 0)
        difficulty = max(0, min(5, int(payload.get("difficulty") or 0)))

        with self._connect() as conn:
            fid, fname = self._folder_name(conn, int(payload.get("folderId") or 0))
            if qid is not None and str(qid).strip() != "":
                qid_int = int(qid)
                existing = conn.execute("SELECT Id FROM AIResponses WHERE Id = ?", (qid_int,)).fetchone()
                if not existing:
                    raise ValueError("题目不存在")
                conn.execute(
                    """
                    UPDATE AIResponses
                    SET Question = ?, Options = ?, QuestionType = ?, Answer = ?,
                        Explanation = ?, ModelName = ?, FolderId = ?, FolderName = ?,
                        IsAi = ?, IsPendingCorrection = ?, Importance = ?, Mastery = ?, Difficulty = ?
                    WHERE Id = ?
                    """,
                    (
                        question, options, qtype, answer, explanation, model_name,
                        fid, fname, is_ai, is_pending, importance, mastery, difficulty, qid_int,
                    ),
                )
                row = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (qid_int,)).fetchone()
                return self._row_to_question(row)

            cur = conn.execute(
                """
                INSERT INTO AIResponses (
                  Question, Options, QuestionType, Answer, Explanation, ModelName,
                  CreateTime, FolderId, FolderName, IsAi, IsPendingCorrection,
                  Importance, Mastery, Difficulty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question, options, qtype, answer, explanation, model_name,
                    utc_now_iso(), fid, fname, is_ai, is_pending, importance, mastery, difficulty,
                ),
            )
            row = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (int(cur.lastrowid),)).fetchone()
            return self._row_to_question(row)

    def upsert_cached_answer(
        self,
        *,
        question: str,
        options: Any = "",
        question_type: str = "",
        answer: str,
        explanation: str = "",
        model_name: str = "",
        folder_id: int = 0,
        is_ai: bool = True,
    ) -> Optional[Dict[str, Any]]:
        cleaned_q = normalize_question_text(question)
        cleaned_a = str(answer or "").strip()
        if not cleaned_q or not cleaned_a:
            return None
        opts_str = _normalize_options_str(options)
        norm_q = normalize_for_match(cleaned_q)
        norm_opts = normalize_for_match(opts_str)
        with self._connect() as conn:
            existing_rows = conn.execute(
                "SELECT * FROM AIResponses WHERE Question = ? ORDER BY Id DESC LIMIT 10",
                (cleaned_q,),
            ).fetchall()
            for r in existing_rows:
                if normalize_for_match(r["Question"]) == norm_q and normalize_for_match(r["Options"] or "") == norm_opts:
                    conn.execute(
                        """
                        UPDATE AIResponses
                        SET Answer = ?, Explanation = CASE WHEN ? != '' THEN ? ELSE Explanation END,
                            ModelName = CASE WHEN ? != '' THEN ? ELSE ModelName END,
                            IsPendingCorrection = 0
                        WHERE Id = ?
                        """,
                        (cleaned_a, explanation, explanation, model_name, model_name, int(r["Id"])),
                    )
                    updated = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (int(r["Id"]),)).fetchone()
                    return self._row_to_question(updated)

        return self.save_question(
            {
                "question": cleaned_q,
                "options": opts_str,
                "questionType": question_type,
                "answer": cleaned_a,
                "explanation": explanation,
                "modelName": model_name,
                "folderId": folder_id,
                "isAi": is_ai,
                "isPendingCorrection": False,
            }
        )

    def list_questions(
        self,
        *,
        folder_id: Optional[int] = None,
        keyword: str = "",
        question_type: str = "",
        source: str = "all",
        pending_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        clauses: List[str] = []
        params: List[Any] = []
        if folder_id is not None and folder_id >= 0:
            clauses.append("FolderId = ?")
            params.append(int(folder_id))
        kw = str(keyword or "").strip()
        if kw:
            clauses.append("(Question LIKE ? OR Answer LIKE ? OR Options LIKE ? OR Explanation LIKE ?)")
            like = f"%{kw}%"
            params.extend([like, like, like, like])
        qt = str(question_type or "").strip()
        if qt and qt != "all":
            clauses.append("QuestionType = ?")
            params.append(qt)
        if source == "ai":
            clauses.append("IsAi = 1")
        elif source == "manual":
            clauses.append("IsAi = 0")
        if pending_only:
            clauses.append("IsPendingCorrection = 1")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(200, int(page_size or 50)))
        offset = max(0, (max(1, int(page or 1)) - 1) * limit)

        with self._connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(1) FROM AIResponses {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM AIResponses {where} ORDER BY Id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return {
                "items": [self._row_to_question(r) for r in rows],
                "total": total,
                "page": max(1, int(page or 1)),
                "pageSize": limit,
            }

    def get_question(self, question_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (int(question_id),)).fetchone()
            return self._row_to_question(row) if row else None

    def delete_questions(self, ids: Iterable[int]) -> int:
        id_list = [int(i) for i in ids if str(i).strip().isdigit()]
        if not id_list:
            return 0
        placeholders = ",".join("?" for _ in id_list)
        with self._connect() as conn:
            conn.execute(f"DELETE FROM QuestionKnowledgeNodes WHERE QuestionId IN ({placeholders})", id_list)
            conn.execute(f"DELETE FROM QuestionPracticeHistory WHERE QuestionId IN ({placeholders})", id_list)
            cur = conn.execute(f"DELETE FROM AIResponses WHERE Id IN ({placeholders})", id_list)
            return int(cur.rowcount or 0)

    def move_questions(self, ids: Iterable[int], target_folder_id: int) -> int:
        id_list = [int(i) for i in ids if str(i).strip().isdigit()]
        if not id_list:
            return 0
        placeholders = ",".join("?" for _ in id_list)
        with self._connect() as conn:
            fid, fname = self._folder_name(conn, int(target_folder_id or 0))
            cur = conn.execute(
                f"UPDATE AIResponses SET FolderId = ?, FolderName = ? WHERE Id IN ({placeholders})",
                [fid, fname, *id_list],
            )
            return int(cur.rowcount or 0)

    def set_pending_correction(
        self,
        question_id: int,
        pending: bool,
        corrected_answer: Optional[str] = None,
        explanation: Optional[str] = None,
    ) -> Dict[str, Any]:
        qid = int(question_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (qid,)).fetchone()
            if not row:
                raise ValueError("题目不存在")
            if corrected_answer is not None and str(corrected_answer).strip():
                conn.execute(
                    """
                    UPDATE AIResponses
                    SET Answer = ?, Explanation = CASE WHEN ? IS NOT NULL THEN ? ELSE Explanation END,
                        IsPendingCorrection = ?, IsAi = 0
                    WHERE Id = ?
                    """,
                    (str(corrected_answer).strip(), explanation, explanation or "", 1 if pending else 0, qid),
                )
            else:
                conn.execute(
                    "UPDATE AIResponses SET IsPendingCorrection = ? WHERE Id = ?",
                    (1 if pending else 0, qid),
                )
            updated = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (qid,)).fetchone()
            return self._row_to_question(updated)

    def find_exact_local_match(
        self,
        question: str,
        options: Any = "",
        question_type: str = "",
    ) -> Optional[Dict[str, Any]]:
        cleaned_q = normalize_question_text(question)
        norm_q = normalize_for_match(cleaned_q)
        if not norm_q:
            return None
        opts_str = _normalize_options_str(options)
        norm_opts = normalize_for_match(opts_str)
        norm_type = str(question_type or "").strip().lower()

        with self._connect() as conn:
            # Fast exact text match first
            rows = conn.execute(
                "SELECT * FROM AIResponses WHERE IsPendingCorrection = 0 AND Question = ? ORDER BY Id DESC LIMIT 20",
                (cleaned_q,),
            ).fetchall()
            if not rows:
                # Fallback token-filtered scan for normalized match (punctuation/whitespace differences)
                tokens = _tokenize_for_recall(cleaned_q)
                if tokens:
                    clauses = " OR ".join("Question LIKE ?" for _ in tokens[:4])
                    params = [f"%{t}%" for t in tokens[:4]]
                    rows = conn.execute(
                        f"SELECT * FROM AIResponses WHERE IsPendingCorrection = 0 AND ({clauses}) ORDER BY Id DESC LIMIT 120",
                        params,
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM AIResponses WHERE IsPendingCorrection = 0 ORDER BY Id DESC LIMIT 200"
                    ).fetchall()

        best: Optional[Dict[str, Any]] = None
        for r in rows:
            if normalize_for_match(r["Question"]) != norm_q:
                continue
            r_opts = normalize_for_match(r["Options"] or "")
            r_type = str(r["QuestionType"] or "").strip().lower()
            if norm_type and r_type and r_type != norm_type:
                continue
            if norm_opts and r_opts and r_opts == norm_opts:
                return self._row_to_question(r)
            if not norm_opts or not r_opts:
                best = best or self._row_to_question(r)
        return best

    def find_similar_candidates(
        self,
        question: str,
        options: Any = "",
        question_type: str = "",
        *,
        limit: int = 5,
        min_ratio: float = 0.52,
    ) -> List[Dict[str, Any]]:
        cleaned_q = normalize_question_text(question)
        norm_q = normalize_for_match(cleaned_q)
        if len(norm_q) < 4:
            return []
        norm_type = str(question_type or "").strip().lower()
        tokens = _tokenize_for_recall(cleaned_q)
        with self._connect() as conn:
            if tokens:
                clauses = " OR ".join("Question LIKE ?" for _ in tokens[:6])
                params = [f"%{t}%" for t in tokens[:6]]
                rows = conn.execute(
                    f"SELECT * FROM AIResponses WHERE IsPendingCorrection = 0 AND ({clauses}) ORDER BY Id DESC LIMIT 150",
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM AIResponses WHERE IsPendingCorrection = 0 ORDER BY Id DESC LIMIT 150"
                ).fetchall()

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for r in rows:
            r_type = str(r["QuestionType"] or "").strip().lower()
            if norm_type and r_type and r_type != norm_type:
                continue
            r_norm = normalize_for_match(r["Question"])
            if not r_norm:
                continue
            ratio = SequenceMatcher(None, norm_q, r_norm).ratio()
            if ratio >= min_ratio:
                item = self._row_to_question(r)
                item["similarity"] = round(ratio, 3)
                scored.append((ratio, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def import_questions(
        self,
        *,
        fmt: str,
        content: str,
        folder_id: int = 0,
    ) -> Dict[str, Any]:
        fmt_l = (fmt or "json").strip().lower()
        items: List[Dict[str, Any]] = []
        if fmt_l == "json":
            parsed = json.loads(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
                parsed = parsed["items"]
            if not isinstance(parsed, list):
                raise ValueError("JSON 导入格式必须为数组或包含 items 数组的对象")
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                q = str(entry.get("question") or entry.get("title") or "").strip()
                a = str(entry.get("answer") or "").strip()
                if q and a:
                    items.append(
                        {
                            "question": q,
                            "options": _normalize_options_str(entry.get("options")),
                            "questionType": str(entry.get("questionType") or entry.get("type") or "").strip(),
                            "answer": a,
                            "explanation": str(entry.get("explanation") or "").strip(),
                            "isAi": bool(entry.get("isAi", False)),
                        }
                    )
        elif fmt_l == "csv":
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                q = str(row.get("question") or row.get("题目") or row.get("title") or "").strip()
                a = str(row.get("answer") or row.get("答案") or "").strip()
                if q and a:
                    items.append(
                        {
                            "question": q,
                            "options": str(row.get("options") or row.get("选项") or "").strip(),
                            "questionType": str(row.get("questionType") or row.get("题型") or row.get("type") or "").strip(),
                            "answer": a,
                            "explanation": str(row.get("explanation") or row.get("解析") or "").strip(),
                            "isAi": False,
                        }
                    )
        else:
            raise ValueError(f"不支持的导入格式: {fmt}")

        imported = 0
        skipped = 0
        for item in items:
            try:
                self.upsert_cached_answer(
                    question=item["question"],
                    options=item["options"],
                    question_type=item["questionType"],
                    answer=item["answer"],
                    explanation=item["explanation"],
                    folder_id=folder_id,
                    is_ai=item["isAi"],
                )
                imported += 1
            except Exception:
                skipped += 1
        return {"imported": imported, "skipped": skipped, "totalParsed": len(items)}

    def export_questions(self, *, fmt: str = "json", folder_id: Optional[int] = None) -> Dict[str, Any]:
        result = self.list_questions(folder_id=folder_id, page=1, page_size=200)
        # Fetch full set without 200 cap
        clauses = ["FolderId = ?"] if (folder_id is not None and folder_id >= 0) else []
        params = [int(folder_id)] if clauses else []
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM AIResponses {where} ORDER BY Id ASC", params).fetchall()
            items = [self._row_to_question(r) for r in rows]

        fmt_l = (fmt or "json").strip().lower()
        if fmt_l == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["question", "options", "questionType", "answer", "explanation", "folderName", "isAi"])
            for it in items:
                writer.writerow([
                    it["question"],
                    it["options"],
                    it["questionType"],
                    it["answer"],
                    it["explanation"],
                    it["folderName"],
                    "1" if it["isAi"] else "0",
                ])
            return {"format": "csv", "count": len(items), "content": buf.getvalue()}
        return {
            "format": "json",
            "count": len(items),
            "content": json.dumps(items, ensure_ascii=False, indent=2),
        }

    # ------------------------------------------------------------------
    # Practice History & Smart Queue
    # ------------------------------------------------------------------
    def list_practice_candidates(
        self,
        *,
        folder_id: Optional[int] = None,
        question_type: str = "",
        mode: str = "smart",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if folder_id is not None and folder_id >= 0:
            clauses.append("q.FolderId = ?")
            params.append(int(folder_id))
        qt = str(question_type or "").strip()
        if qt and qt != "all":
            clauses.append("q.QuestionType = ?")
            params.append(qt)
        if mode == "wrong":
            clauses.append(
                "(q.IsPendingCorrection = 1 OR EXISTS (SELECT 1 FROM QuestionPracticeHistory h WHERE h.QuestionId = q.Id AND h.IsCorrect = 0))"
            )
        elif mode == "unmastered":
            clauses.append("q.Mastery < 3")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = (
            "ORDER BY q.Mastery ASC, wrong_count DESC, practice_count ASC, q.Id DESC"
            if mode in ("smart", "wrong", "unmastered")
            else "ORDER BY RANDOM()"
        )
        cap = max(1, min(100, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT q.*,
                       (SELECT COUNT(1) FROM QuestionPracticeHistory h WHERE h.QuestionId = q.Id) AS practice_count,
                       (SELECT COUNT(1) FROM QuestionPracticeHistory h WHERE h.QuestionId = q.Id AND h.IsCorrect = 0) AS wrong_count
                FROM AIResponses q
                {where}
                {order}
                LIMIT ?
                """,
                [*params, cap],
            ).fetchall()
            items = []
            for r in rows:
                item = self._row_to_question(r)
                item["practiceCount"] = int(r["practice_count"] or 0)
                item["wrongCount"] = int(r["wrong_count"] or 0)
                items.append(item)
            return items

    def record_practice(
        self,
        *,
        question_id: int,
        user_answer: str,
        is_correct: bool,
        note: str = "",
        source: str = "practice",
    ) -> Dict[str, Any]:
        qid = int(question_id)
        now_iso = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM AIResponses WHERE Id = ?", (qid,)).fetchone()
            if not row:
                raise ValueError("题目不存在")
            cur = conn.execute(
                """
                INSERT INTO QuestionPracticeHistory (QuestionId, UserAnswer, IsCorrect, Note, Source, CreateTime)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (qid, str(user_answer or ""), 1 if is_correct else 0, str(note or ""), str(source or "practice"), now_iso),
            )
            current_mastery = int(row["Mastery"] or 0)
            next_mastery = clamp_mastery(current_mastery + 1 if is_correct else max(0, current_mastery - 1))
            conn.execute("UPDATE AIResponses SET Mastery = ? WHERE Id = ?", (next_mastery, qid))

            # Update linked knowledge graph nodes with forgetting curve progression!
            linked_nodes = conn.execute(
                """
                SELECT n.Id, n.SubjectId, n.Name, n.ForgettingStage, n.LastReviewedAt
                FROM QuestionKnowledgeNodes qkn
                JOIN StudyGraphNodes n ON n.Id = qkn.NodeId
                WHERE qkn.QuestionId = ?
                """,
                (qid,),
            ).fetchall()
            subject_nodes: Dict[int, List[str]] = {}
            for node in linked_nodes:
                nid = int(node["Id"])
                new_stage = next_forgetting_stage(
                    int(node["ForgettingStage"] or 0),
                    node["LastReviewedAt"],
                    bool(is_correct),
                    now_iso,
                )
                quality = "good" if is_correct else "poor"
                reviewed_at = reviewed_at_for_quality(new_stage, quality)
                eff = effective_mastery(1, new_stage, reviewed_at)
                conn.execute(
                    "UPDATE StudyGraphNodes SET ForgettingStage = ?, LastReviewedAt = ?, Mastery = ? WHERE Id = ?",
                    (new_stage, reviewed_at, eff, nid),
                )
                subject_nodes.setdefault(int(node["SubjectId"]), []).append(str(node["Name"]))

            for subject_id, names in subject_nodes.items():
                conn.execute(
                    """
                    INSERT INTO StudyActivity (SubjectId, Kind, Names, QuestionCount, CorrectCount, CreateTime)
                    VALUES (?, 'practice', ?, 1, ?, ?)
                    """,
                    (subject_id, json.dumps(names, ensure_ascii=False), 1 if is_correct else 0, now_iso),
                )

            return {
                "id": int(cur.lastrowid),
                "questionId": qid,
                "userAnswer": str(user_answer or ""),
                "isCorrect": bool(is_correct),
                "mastery": next_mastery,
                "createTime": now_iso,
            }

    def list_practice_history(self, limit: int = 50) -> Dict[str, Any]:
        cap = max(1, min(200, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT h.*, q.Question, q.Answer, q.QuestionType
                FROM QuestionPracticeHistory h
                LEFT JOIN AIResponses q ON q.Id = h.QuestionId
                ORDER BY h.Id DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
            stats = conn.execute(
                "SELECT COUNT(1) AS total, SUM(CASE WHEN IsCorrect = 1 THEN 1 ELSE 0 END) AS correct FROM QuestionPracticeHistory"
            ).fetchone()
            total = int(stats["total"] or 0)
            correct = int(stats["correct"] or 0)
            return {
                "total": total,
                "correct": correct,
                "accuracy": round((correct / total) * 100, 1) if total > 0 else 0.0,
                "items": [
                    {
                        "id": int(r["Id"]),
                        "questionId": int(r["QuestionId"]),
                        "question": r["Question"] or "(已删除)",
                        "correctAnswer": r["Answer"] or "",
                        "questionType": r["QuestionType"] or "",
                        "userAnswer": r["UserAnswer"] or "",
                        "isCorrect": bool(r["IsCorrect"]),
                        "note": r["Note"] or "",
                        "source": r["Source"] or "practice",
                        "createTime": r["CreateTime"] or "",
                    }
                    for r in rows
                ],
            }

    # ------------------------------------------------------------------
    # Study Knowledge Graph
    # ------------------------------------------------------------------
    def list_subjects(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(1) FROM StudyGraphNodes n WHERE n.SubjectId = s.Id) AS NodeCount,
                       (SELECT COUNT(1) FROM StudyGraphEdges e WHERE e.SubjectId = s.Id) AS EdgeCount
                FROM StudySubjects s
                ORDER BY s.Id DESC
                """
            ).fetchall()
            return [
                {
                    "id": int(r["Id"]),
                    "name": r["Name"],
                    "description": r["Description"] or "",
                    "createTime": r["CreateTime"] or "",
                    "nodeCount": int(r["NodeCount"] or 0),
                    "edgeCount": int(r["EdgeCount"] or 0),
                }
                for r in rows
            ]

    def save_subject(self, *, name: str, description: str = "", subject_id: Optional[int] = None) -> Dict[str, Any]:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("学科名称不能为空")
        desc = str(description or "").strip()
        with self._connect() as conn:
            if subject_id is not None:
                sid = int(subject_id)
                conn.execute("UPDATE StudySubjects SET Name = ?, Description = ? WHERE Id = ?", (cleaned, desc, sid))
            else:
                cur = conn.execute(
                    "INSERT INTO StudySubjects (Name, Description, CreateTime) VALUES (?, ?, ?)",
                    (cleaned, desc, utc_now_iso()),
                )
                sid = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM StudySubjects WHERE Id = ?", (sid,)).fetchone()
            return {
                "id": int(row["Id"]),
                "name": row["Name"],
                "description": row["Description"] or "",
                "createTime": row["CreateTime"] or "",
            }

    def delete_subject(self, subject_id: int) -> None:
        sid = int(subject_id)
        with self._connect() as conn:
            node_ids = [int(r["Id"]) for r in conn.execute("SELECT Id FROM StudyGraphNodes WHERE SubjectId = ?", (sid,)).fetchall()]
            if node_ids:
                ph = ",".join("?" for _ in node_ids)
                conn.execute(f"DELETE FROM QuestionKnowledgeNodes WHERE NodeId IN ({ph})", node_ids)
            conn.execute("DELETE FROM StudyGraphEdges WHERE SubjectId = ?", (sid,))
            conn.execute("DELETE FROM StudyGraphNodes WHERE SubjectId = ?", (sid,))
            conn.execute("DELETE FROM StudyActivity WHERE SubjectId = ?", (sid,))
            conn.execute("DELETE FROM StudySubjects WHERE Id = ?", (sid,))

    def get_study_graph(self, subject_id: int) -> Dict[str, Any]:
        sid = int(subject_id)
        now = utc_now()
        with self._connect() as conn:
            subj = conn.execute("SELECT * FROM StudySubjects WHERE Id = ?", (sid,)).fetchone()
            if not subj:
                raise ValueError("学科不存在")
            node_rows = conn.execute(
                "SELECT * FROM StudyGraphNodes WHERE SubjectId = ? ORDER BY SortOrder ASC, Id ASC",
                (sid,),
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT * FROM StudyGraphEdges WHERE SubjectId = ? ORDER BY Id ASC",
                (sid,),
            ).fetchall()
            link_rows = conn.execute(
                """
                SELECT qkn.NodeId, q.Id, q.Question, q.Answer, q.QuestionType, q.Mastery
                FROM QuestionKnowledgeNodes qkn
                JOIN AIResponses q ON q.Id = qkn.QuestionId
                JOIN StudyGraphNodes n ON n.Id = qkn.NodeId
                WHERE n.SubjectId = ?
                """,
                (sid,),
            ).fetchall()
            act_rows = conn.execute(
                "SELECT * FROM StudyActivity WHERE SubjectId = ? ORDER BY CreateTime DESC, Id DESC LIMIT 30",
                (sid,),
            ).fetchall()

        links_by_node: Dict[int, List[Dict[str, Any]]] = {}
        for lr in link_rows:
            links_by_node.setdefault(int(lr["NodeId"]), []).append(
                {
                    "id": int(lr["Id"]),
                    "question": lr["Question"],
                    "answer": lr["Answer"],
                    "questionType": lr["QuestionType"] or "",
                    "mastery": int(lr["Mastery"] or 0),
                }
            )

        nodes = []
        for r in node_rows:
            nid = int(r["Id"])
            stage = clamp_forgetting_stage(int(r["ForgettingStage"] or 0))
            last_rev = r["LastReviewedAt"]
            stored_m = int(r["Mastery"] or 0)
            eff_m = effective_mastery(stored_m, stage, last_rev, now=now)
            ret = retention_ratio(stage, last_rev, now=now)
            nodes.append(
                {
                    "id": nid,
                    "subjectId": sid,
                    "nodeKey": r["NodeKey"],
                    "name": r["Name"],
                    "summary": r["Summary"] or "",
                    "mastery": eff_m,
                    "storedMastery": stored_m,
                    "parentId": int(r["ParentId"]) if r["ParentId"] is not None else None,
                    "sortOrder": int(r["SortOrder"] or 0),
                    "forgettingStage": stage,
                    "lastReviewedAt": last_rev,
                    "retention": ret,
                    "nextReviewDue": next_review_due_iso(stage, last_rev),
                    "questions": links_by_node.get(nid, []),
                }
            )

        edges = [
            {
                "id": int(r["Id"]),
                "subjectId": sid,
                "fromId": int(r["FromId"]),
                "toId": int(r["ToId"]),
                "relation": r["Relation"] or "",
            }
            for r in edge_rows
        ]
        activities = []
        for r in act_rows:
            try:
                names = json.loads(r["Names"] or "[]")
            except Exception:
                names = []
            activities.append(
                {
                    "id": int(r["Id"]),
                    "kind": r["Kind"],
                    "names": names,
                    "questionCount": int(r["QuestionCount"] or 0),
                    "correctCount": int(r["CorrectCount"] or 0),
                    "createTime": r["CreateTime"],
                }
            )
        return {
            "subject": {
                "id": int(subj["Id"]),
                "name": subj["Name"],
                "description": subj["Description"] or "",
            },
            "nodes": nodes,
            "edges": edges,
            "activities": activities,
        }

    def save_graph_node(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sid = int(payload["subjectId"])
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("知识点名称不能为空")
        summary = str(payload.get("summary") or "").strip()
        node_key = str(payload.get("nodeKey") or f"node_{uuid.uuid4().hex[:8]}").strip()
        parent_id = payload.get("parentId")
        parent_id = int(parent_id) if parent_id is not None and str(parent_id).strip() != "" else None
        mastery = clamp_mastery(payload.get("mastery") or 0)
        sort_order = int(payload.get("sortOrder") or 0)
        node_id = payload.get("id")
        with self._connect() as conn:
            if node_id is not None:
                nid = int(node_id)
                conn.execute(
                    """
                    UPDATE StudyGraphNodes
                    SET Name = ?, Summary = ?, ParentId = ?, Mastery = ?, SortOrder = ?
                    WHERE Id = ? AND SubjectId = ?
                    """,
                    (name, summary, parent_id, mastery, sort_order, nid, sid),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO StudyGraphNodes (SubjectId, NodeKey, Name, Summary, Mastery, ParentId, SortOrder, ForgettingStage)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (sid, node_key, name, summary, mastery, parent_id, sort_order),
                )
                nid = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM StudyGraphNodes WHERE Id = ?", (nid,)).fetchone()
            return {
                "id": int(row["Id"]),
                "subjectId": int(row["SubjectId"]),
                "nodeKey": row["NodeKey"],
                "name": row["Name"],
                "summary": row["Summary"] or "",
                "mastery": int(row["Mastery"] or 0),
                "parentId": int(row["ParentId"]) if row["ParentId"] is not None else None,
                "forgettingStage": int(row["ForgettingStage"] or 0),
                "lastReviewedAt": row["LastReviewedAt"],
            }

    def delete_graph_node(self, node_id: int) -> None:
        nid = int(node_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM QuestionKnowledgeNodes WHERE NodeId = ?", (nid,))
            conn.execute("DELETE FROM StudyGraphEdges WHERE FromId = ? OR ToId = ?", (nid, nid))
            conn.execute("UPDATE StudyGraphNodes SET ParentId = NULL WHERE ParentId = ?", (nid,))
            conn.execute("DELETE FROM StudyGraphNodes WHERE Id = ?", (nid,))

    def save_graph_edge(self, subject_id: int, from_id: int, to_id: int, relation: str = "") -> Dict[str, Any]:
        sid, fid, tid = int(subject_id), int(from_id), int(to_id)
        if fid == tid:
            raise ValueError("连边起点和终点不能相同")
        rel = str(relation or "").strip()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT Id FROM StudyGraphEdges WHERE SubjectId = ? AND FromId = ? AND ToId = ?",
                (sid, fid, tid),
            ).fetchone()
            if existing:
                conn.execute("UPDATE StudyGraphEdges SET Relation = ? WHERE Id = ?", (rel, int(existing["Id"])))
                eid = int(existing["Id"])
            else:
                cur = conn.execute(
                    "INSERT INTO StudyGraphEdges (SubjectId, FromId, ToId, Relation) VALUES (?, ?, ?, ?)",
                    (sid, fid, tid, rel),
                )
                eid = int(cur.lastrowid)
            return {"id": eid, "subjectId": sid, "fromId": fid, "toId": tid, "relation": rel}

    def delete_graph_edge(self, edge_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM StudyGraphEdges WHERE Id = ?", (int(edge_id),))

    def link_question_node(self, question_id: int, node_id: int, *, unlink: bool = False) -> None:
        qid, nid = int(question_id), int(node_id)
        with self._connect() as conn:
            if unlink:
                conn.execute("DELETE FROM QuestionKnowledgeNodes WHERE QuestionId = ? AND NodeId = ?", (qid, nid))
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO QuestionKnowledgeNodes (QuestionId, NodeId) VALUES (?, ?)",
                    (qid, nid),
                )

    def review_graph_node(self, node_id: int, quality_raw: str) -> Dict[str, Any]:
        nid = int(node_id)
        quality = parse_study_quality(quality_raw)
        now_iso = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM StudyGraphNodes WHERE Id = ?", (nid,)).fetchone()
            if not row:
                raise ValueError("知识点不存在")
            remembered = quality != "poor"
            new_stage = next_forgetting_stage(
                int(row["ForgettingStage"] or 0),
                row["LastReviewedAt"],
                remembered,
                now_iso,
            )
            reviewed_at = reviewed_at_for_quality(new_stage, quality)
            eff = effective_mastery(1, new_stage, reviewed_at)
            conn.execute(
                "UPDATE StudyGraphNodes SET ForgettingStage = ?, LastReviewedAt = ?, Mastery = ? WHERE Id = ?",
                (new_stage, reviewed_at, eff, nid),
            )
            conn.execute(
                """
                INSERT INTO StudyActivity (SubjectId, Kind, Names, QuestionCount, CorrectCount, CreateTime)
                VALUES (?, 'review', ?, 1, ?, ?)
                """,
                (int(row["SubjectId"]), json.dumps([row["Name"]], ensure_ascii=False), 1 if remembered else 0, now_iso),
            )
            return {
                "id": nid,
                "forgettingStage": new_stage,
                "lastReviewedAt": reviewed_at,
                "mastery": eff,
                "retention": retention_ratio(new_stage, reviewed_at),
                "nextReviewDue": next_review_due_iso(new_stage, reviewed_at),
            }

    # ------------------------------------------------------------------
    # Request Logs & Daily Counts
    # ------------------------------------------------------------------
    def record_request_log(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status: int,
        response_time_ms: int,
        request_body: Any,
        response_body: Any,
        stage: str = "completed",
        local_search: str = "",
        model_name: str = "",
        reasoning: str = "",
        search_traces: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        now = utc_now()
        ts = now.isoformat().replace("+00:00", "Z")
        day = now.strftime("%Y-%m-%d")
        req_str = request_body if isinstance(request_body, str) else json.dumps(request_body, ensure_ascii=False)
        res_str = response_body if isinstance(response_body, str) else json.dumps(response_body, ensure_ascii=False)
        traces_str = json.dumps(search_traces or [], ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO RequestLogs (
                  RequestId, Timestamp, Method, Path, Status, ResponseTime,
                  RequestBody, ResponseBody, Stage, LocalSearch, ModelName, Reasoning, SearchTraces
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, ts, method, path, int(status), int(response_time_ms),
                    req_str[:16000], res_str[:16000], stage, local_search, model_name,
                    (reasoning or "")[:16000], traces_str[:16000],
                ),
            )
            conn.execute(
                """
                INSERT INTO DailyRequestCounts (Day, Count) VALUES (?, 1)
                ON CONFLICT(Day) DO UPDATE SET Count = Count + 1
                """,
                (day,),
            )
            # Keep last 300 logs bounded
            conn.execute(
                "DELETE FROM RequestLogs WHERE LogId NOT IN (SELECT LogId FROM RequestLogs ORDER BY LogId DESC LIMIT 300)"
            )
            return int(cur.lastrowid)

    def list_request_logs(self, limit: int = 80) -> List[Dict[str, Any]]:
        cap = max(1, min(300, int(limit or 80)))
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM RequestLogs ORDER BY LogId DESC LIMIT ?", (cap,)).fetchall()
            out = []
            for r in rows:
                try:
                    traces = json.loads(r["SearchTraces"] or "[]")
                except Exception:
                    traces = []
                out.append(
                    {
                        "logId": int(r["LogId"]),
                        "requestId": r["RequestId"],
                        "timestamp": r["Timestamp"],
                        "method": r["Method"],
                        "path": r["Path"],
                        "status": int(r["Status"] or 200),
                        "responseTime": int(r["ResponseTime"] or 0),
                        "requestBody": r["RequestBody"] or "",
                        "responseBody": r["ResponseBody"] or "",
                        "stage": r["Stage"] or "completed",
                        "localSearch": r["LocalSearch"] or "",
                        "modelName": r["ModelName"] or "",
                        "reasoning": r["Reasoning"] or "",
                        "searchTraces": traces,
                    }
                )
            return out

    def clear_request_logs(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM RequestLogs")
            return int(cur.rowcount or 0)

    def get_stats(self) -> Dict[str, Any]:
        today = utc_now()
        days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        with self._connect() as conn:
            total_q = int(conn.execute("SELECT COUNT(1) FROM AIResponses").fetchone()[0])
            ai_q = int(conn.execute("SELECT COUNT(1) FROM AIResponses WHERE IsAi = 1").fetchone()[0])
            pending_q = int(conn.execute("SELECT COUNT(1) FROM AIResponses WHERE IsPendingCorrection = 1").fetchone()[0])
            folder_cnt = int(conn.execute("SELECT COUNT(1) FROM Folders").fetchone()[0])
            subject_cnt = int(conn.execute("SELECT COUNT(1) FROM StudySubjects").fetchone()[0])
            node_cnt = int(conn.execute("SELECT COUNT(1) FROM StudyGraphNodes").fetchone()[0])
            practice_total = int(conn.execute("SELECT COUNT(1) FROM QuestionPracticeHistory").fetchone()[0])
            daily_rows = conn.execute(
                f"SELECT Day, Count FROM DailyRequestCounts WHERE Day IN ({','.join('?' for _ in days)})",
                days,
            ).fetchall()
            daily_map = {r["Day"]: int(r["Count"] or 0) for r in daily_rows}
        return {
            "totalQuestions": total_q,
            "aiQuestions": ai_q,
            "pendingCorrections": pending_q,
            "folderCount": folder_cnt,
            "subjectCount": subject_cnt,
            "knowledgeNodeCount": node_cnt,
            "practiceTotal": practice_total,
            "todayRequests": daily_map.get(days[-1], 0),
            "dailyCounts": [{"day": d, "count": daily_map.get(d, 0)} for d in days],
        }

    # ------------------------------------------------------------------
    # Agent Chat Sessions
    # ------------------------------------------------------------------
    def list_agent_sessions(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM AgentSessions ORDER BY UpdatedAt DESC LIMIT 50").fetchall()
            out = []
            for r in rows:
                try:
                    msgs = json.loads(r["MessagesJson"] or "[]")
                except Exception:
                    msgs = []
                out.append(
                    {
                        "id": r["Id"],
                        "title": r["Title"],
                        "messages": msgs,
                        "updatedAt": r["UpdatedAt"],
                        "createTime": r["CreateTime"],
                    }
                )
            return out

    def create_agent_session(self, title: str = "新对话") -> Dict[str, Any]:
        sid = uuid.uuid4().hex[:12]
        now = utc_now_iso()
        cleaned = str(title or "新对话").strip()[:80] or "新对话"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO AgentSessions (Id, Title, MessagesJson, UpdatedAt, CreateTime) VALUES (?, ?, '[]', ?, ?)",
                (sid, cleaned, now, now),
            )
        return {"id": sid, "title": cleaned, "messages": [], "updatedAt": now, "createTime": now}

    def get_agent_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM AgentSessions WHERE Id = ?", (str(session_id),)).fetchone()
            if not r:
                return None
            try:
                msgs = json.loads(r["MessagesJson"] or "[]")
            except Exception:
                msgs = []
            return {
                "id": r["Id"],
                "title": r["Title"],
                "messages": msgs,
                "updatedAt": r["UpdatedAt"],
                "createTime": r["CreateTime"],
            }

    def update_agent_session(self, session_id: str, messages: List[Dict[str, Any]], title: Optional[str] = None) -> Dict[str, Any]:
        now = utc_now_iso()
        trimmed = messages[-60:]
        with self._connect() as conn:
            if title is not None:
                conn.execute(
                    "UPDATE AgentSessions SET Title = ?, MessagesJson = ?, UpdatedAt = ? WHERE Id = ?",
                    (title.strip()[:80] or "新对话", json.dumps(trimmed, ensure_ascii=False), now, str(session_id)),
                )
            else:
                conn.execute(
                    "UPDATE AgentSessions SET MessagesJson = ?, UpdatedAt = ? WHERE Id = ?",
                    (json.dumps(trimmed, ensure_ascii=False), now, str(session_id)),
                )
        sess = self.get_agent_session(session_id)
        if not sess:
            raise ValueError("对话会话不存在")
        return sess

    def rename_agent_session(self, session_id: str, title: str) -> Dict[str, Any]:
        cleaned = str(title or "").strip()[:80]
        if not cleaned:
            raise ValueError("对话标题不能为空")
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE AgentSessions SET Title = ?, UpdatedAt = ? WHERE Id = ?",
                (cleaned, now, str(session_id)),
            )
            if cur.rowcount == 0:
                raise ValueError("对话会话不存在")
        sess = self.get_agent_session(session_id)
        if not sess:
            raise ValueError("对话会话不存在")
        return sess

    def clear_agent_session(self, session_id: str) -> Dict[str, Any]:
        return self.update_agent_session(session_id, [])

    def delete_agent_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM AgentSessions WHERE Id = ?", (str(session_id),))
