# llm/groq_client.py
# Groq API client — wraps groq SDK with retry, logging, and JSON parsing
# Free tier: https://console.groq.com — LLaMA 3.1 70B

from dotenv import load_dotenv
load_dotenv()

import json
import logging
import os
import re
from typing import Optional

from groq import Groq
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from llm.prompt_templates import SYSTEM_PROMPT, build_reasoning_prompt
from webhook.models import AlertContext

logger = logging.getLogger(__name__)


class GroqClient:
    """
    Wraps the Groq SDK for structured LLM reasoning over K8s alerts.
    Uses LLaMA 3.1 70B — free tier on Groq Cloud.
    """

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key or api_key.startswith("gsk_your"):
            logger.warning("GROQ_API_KEY not set — LLM calls will fail")

        self.client = Groq(api_key=api_key)
        self.model  = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
        self.max_tokens = 1024

        logger.info(f"GroqClient initialized with model: {self.model}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def reason(self, ctx: AlertContext) -> dict:
        """
        Send alert context to Groq LLM and get back a remediation plan.
        Returns parsed JSON dict or fallback investigate plan on failure.
        """
        prompt = build_reasoning_prompt(ctx)

        logger.info(
            f"Sending alert to Groq: {ctx.alert_name} | "
            f"severity={ctx.severity} | namespace={ctx.namespace}"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=self.max_tokens,
                temperature=0.1,       # Low temp = consistent, deterministic output
                response_format={"type": "json_object"},  # Force JSON output
            )

            raw = response.choices[0].message.content
            logger.debug(f"Raw LLM response: {raw}")

            plan = self._parse_response(raw)

            logger.info(
                f"LLM decision: action={plan.get('action')} | "
                f"target={plan.get('target')} | "
                f"confidence={plan.get('confidence')} | "
                f"impact={plan.get('estimated_impact')}"
            )

            return plan

        except Exception as e:
            logger.error(f"Groq API call failed: {e}")
            return self._fallback_plan(ctx, str(e))

    def _parse_response(self, raw: str) -> dict:
        """
        Parse LLM JSON response robustly.
        Handles cases where model wraps JSON in markdown code blocks.
        """
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
        cleaned = cleaned.rstrip("```").strip()

        try:
            plan = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e}\nRaw: {raw}")
            raise ValueError(f"LLM returned invalid JSON: {e}")

        # Validate required fields
        required = ["action", "target", "namespace", "reason", "confidence"]
        for field in required:
            if field not in plan:
                logger.warning(f"LLM response missing field: {field}")
                plan[field] = self._default_field(field)

        # Clamp confidence to 0.0-1.0
        plan["confidence"] = max(0.0, min(1.0, float(plan.get("confidence", 0.5))))

        return plan

    def _fallback_plan(self, ctx: AlertContext, error: str) -> dict:
        """Safe fallback when LLM call fails — always investigate, never act."""
        logger.warning(f"Using fallback plan for {ctx.alert_name}: {error}")
        return {
            "action":             "investigate",
            "target":             ctx.pod or ctx.deployment or ctx.node or "unknown",
            "namespace":          ctx.namespace,
            "reason":             f"LLM reasoning failed ({error}). Manual investigation required.",
            "confidence":         0.0,
            "additional_context": f"Original alert: {ctx.summary}",
            "estimated_impact":   "unknown",
            "rollback_plan":      "N/A — no automated action taken",
        }

    def _default_field(self, field: str) -> str | float:
        defaults = {
            "action":             "investigate",
            "target":             "unknown",
            "namespace":          "default",
            "reason":             "No reason provided by LLM",
            "confidence":         0.0,
            "estimated_impact":   "unknown",
            "rollback_plan":      "N/A",
            "additional_context": "",
        }
        return defaults.get(field, "")


# ── Singleton instance ────────────────────────────────────────────────────
groq_client = GroqClient()
