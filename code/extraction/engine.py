"""Motor de invocación al CLI de Claude. Un solo lugar que sabe llamar al modelo.

Usa `claude --print --restricted` — medido: baja la sobrecarga de 28.5k a 9.1k tokens
y la latencia de 26s a 3s frente al modo sin restringir. No hay ANTHROPIC_API_KEY y no
debe haberla: el motor va siempre por el CLI ya autenticado en esta Mac, nunca por la
API directa.

Nunca lanza por un fallo del modelo (timeout, CLI caído, JSON ilegible): el llamador
recibe {"ok": False, ...} y decide el respaldo. Un defecto del motor no debe tumbar
el pipeline completo de 231 llamadas.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

MODEL = "claude-haiku-4-5-20251001"
CLAUDE_BIN = "claude"
DISALLOWED_TOOLS = ("Bash", "Write", "Edit", "WebFetch", "WebSearch", "Task", "Agent")

HERE = Path(__file__).resolve().parent          # code/extraction
CODE_ROOT = HERE.parent                         # code/
REPO_ROOT = CODE_ROOT.parent                    # BUY-OR-WAIT/


class EngineError(RuntimeError):
    """El texto de respuesta no trae JSON aprovechable."""


def _extract_json(text: str) -> Any:
    """El modelo casi siempre envuelve el JSON en ```json ... ``` ; a veces le pone
    prosa alrededor. Busca el primer objeto/arreglo JSON balanceado y lo parsea.
    Nunca usa eval — sólo json.loads sobre el substring encontrado.
    """
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    raise EngineError(f"no se pudo extraer JSON de la respuesta: {text[:200]!r}")


def call(system_prompt_file: str, prompt: str, allowed_tools: list[str] | None = None,
         cwd: Path | None = None, retries: int = 2, timeout: int = 120) -> dict:
    """Llama al CLI una vez (con reintentos ante fallos transitorios).

    Devuelve siempre:
        {"ok": bool, "data": <json parseado o None>, "usage": dict,
         "cost_usd": float, "ms": int, "error": str|None}
    """
    cmd = [CLAUDE_BIN, "--print", "--model", MODEL, "--output-format", "json",
           "--system-prompt-file", str(system_prompt_file), "--restricted",
           "--disallowed-tools", *DISALLOWED_TOOLS]
    if allowed_tools:
        cmd += ["--allowed-tools", *allowed_tools]
    cmd += ["--", prompt]

    last_err = None
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd or REPO_ROOT), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, text=True,
            )
        except subprocess.TimeoutExpired:
            last_err = f"timeout tras {timeout}s (intento {attempt + 1})"
            time.sleep(1.5 * (attempt + 1))
            continue
        ms = int((time.monotonic() - t0) * 1000)

        if proc.returncode != 0:
            last_err = f"exit={proc.returncode} stderr={proc.stderr[:300]!r}"
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last_err = f"stdout no es JSON: {proc.stdout[:300]!r}"
            time.sleep(1.5 * (attempt + 1))
            continue

        usage = envelope.get("usage") or {}
        cost = envelope.get("total_cost_usd") or 0.0
        if envelope.get("is_error"):
            last_err = f"is_error subtype={envelope.get('subtype')}"
            time.sleep(1.5 * (attempt + 1))
            continue

        result_text = envelope.get("result", "")
        try:
            data = _extract_json(result_text)
        except EngineError as exc:
            last_err = str(exc)
            time.sleep(1.5 * (attempt + 1))
            continue

        return {"ok": True, "data": data, "usage": usage, "cost_usd": cost,
                "ms": ms, "error": None}

    return {"ok": False, "data": None, "usage": {}, "cost_usd": 0.0, "ms": 0,
            "error": last_err}
