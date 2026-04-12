"""プロンプト取得・パース・キャッシュサービス.

Google Drive からプロンプトを取得し、オプションで構造化ステップ定義をパースする。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TypedDict

from google_drive_docs_export import export_doc
from config import (
    INSTRUCTION_DOC_ID,
    COMPLIANCE_INSTRUCTION_DOC_ID,
    SA_CREDENTIALS_PATH,
    BASE_DIR,
)

logger = logging.getLogger(__name__)


class StepDef(TypedDict):
    name: str
    purpose: str
    instruction: str


class StructuredPrompt:
    """パース済みプロンプト. メインプロンプトとステップ定義を保持する."""

    def __init__(self, main_prompt: str, steps: dict[int, StepDef] | None = None):
        self.main_prompt = main_prompt
        self.steps = steps


# ── キャッシュ ──
_prompt_cache: dict[str, StructuredPrompt] = {}


def parse_structured_prompt(text: str) -> StructuredPrompt:
    """プロンプトテキストをメインプロンプトとステップ定義に分離する.

    フォーマット:
        (メインプロンプト本文)
        ---STEPS---
        [Step 0: greeting]
        purpose: ...
        instruction: ...

        [Step 1: why_joined]
        purpose: ...
        instruction: ...

    ``---STEPS---`` マーカーが無い場合はステップ定義なし (steps=None) を返す。
    """
    marker = "---STEPS---"
    if marker not in text:
        return StructuredPrompt(main_prompt=text.strip(), steps=None)

    main_part, steps_part = text.split(marker, 1)
    steps: dict[int, StepDef] = {}

    current_step_num: int | None = None
    current_name = ""
    current_purpose = ""
    current_instruction_lines: list[str] = []

    def _flush():
        nonlocal current_step_num, current_name, current_purpose, current_instruction_lines
        if current_step_num is not None:
            steps[current_step_num] = StepDef(
                name=current_name,
                purpose=current_purpose.strip(),
                instruction="\n".join(current_instruction_lines).strip(),
            )
        current_step_num = None
        current_name = ""
        current_purpose = ""
        current_instruction_lines = []

    for line in steps_part.strip().splitlines():
        stripped = line.strip()

        # [Step 0: greeting] ヘッダー
        if stripped.startswith("[Step ") and "]" in stripped:
            _flush()
            inner = stripped[1 : stripped.index("]")]  # "Step 0: greeting"
            parts = inner.split(":", 1)
            try:
                current_step_num = int(parts[0].replace("Step", "").strip())
            except ValueError:
                continue
            current_name = parts[1].strip() if len(parts) > 1 else ""
        elif stripped.startswith("purpose:") and current_step_num is not None:
            current_purpose = stripped[len("purpose:") :].strip()
        elif stripped.startswith("instruction:") and current_step_num is not None:
            current_instruction_lines = [stripped[len("instruction:") :].strip()]
        elif current_step_num is not None and current_instruction_lines:
            # instruction の継続行
            current_instruction_lines.append(line.rstrip())
        elif current_step_num is not None and stripped:
            # purpose の後、instruction の前にある行は instruction の開始
            current_instruction_lines.append(stripped)

    _flush()

    return StructuredPrompt(
        main_prompt=main_part.strip(),
        steps=steps if steps else None,
    )


def _load_sa_info() -> dict | None:
    """サービスアカウント認証情報を読み込む."""
    if not SA_CREDENTIALS_PATH.exists():
        return None
    try:
        return json.loads(SA_CREDENTIALS_PATH.read_text())
    except Exception:
        logger.exception("Failed to load service account credentials")
        return None


def _get_doc_id(scenario: str) -> str | None:
    """シナリオに対応する Google Drive ドキュメントIDを返す."""
    if scenario == "exit_interview":
        return INSTRUCTION_DOC_ID
    elif scenario == "compliance":
        return COMPLIANCE_INSTRUCTION_DOC_ID
    return None


def _get_local_fallback(scenario: str) -> str | None:
    """ローカルファイルからプロンプトを読み込む."""
    filenames = {
        "exit_interview": "prompt.txt",
        "compliance": "prompt_compliance.txt",
        "test": "prompt_test.txt",
    }
    filename = filenames.get(scenario)
    if not filename:
        return None
    path = BASE_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def get_prompt(scenario: str, use_cache: bool = True) -> StructuredPrompt:
    """プロンプトを取得してパースする.

    1. キャッシュがあればそれを返す
    2. Google Drive から取得を試みる
    3. ローカルファイルにフォールバック
    """
    if use_cache and scenario in _prompt_cache:
        logger.debug(f"Returning cached prompt for scenario={scenario}")
        return _prompt_cache[scenario]

    text: str | None = None

    # Google Drive から取得
    doc_id = _get_doc_id(scenario)
    if doc_id:
        sa_info = _load_sa_info()
        if sa_info:
            try:
                raw = export_doc(doc_id, sa_info, "text/plain")
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                logger.info(f"Fetched prompt from Google Drive for scenario={scenario}")
            except Exception:
                logger.exception(f"Failed to fetch from Google Drive for scenario={scenario}")

    # ローカルフォールバック
    if text is None:
        text = _get_local_fallback(scenario)
        if text:
            logger.info(f"Using local fallback prompt for scenario={scenario}")
        else:
            logger.warning(f"No prompt found for scenario={scenario}")
            text = ""

    result = parse_structured_prompt(text)
    logger.debug(f"Parsed prompt for scenario={scenario}: main_prompt length={len(result.main_prompt)}, steps={list(result.steps.keys()) if result.steps else 'None'}")
    _prompt_cache[scenario] = result
    return result


def clear_cache(scenario: str | None = None) -> None:
    """プロンプトキャッシュをクリアする."""
    if scenario:
        _prompt_cache.pop(scenario, None)
    else:
        _prompt_cache.clear()
